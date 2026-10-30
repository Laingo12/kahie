"""Kahie API — FastAPI. Serves the PWA from /frontend and the agent endpoints under /api."""
import json
import logging
import shutil
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import audit, config, db, lexicon, models_client, phrases, tts
from .agent import commands, dialogue, executor, pipeline
from .numbers_mg import speakify

app = FastAPI(title="Kahie", version="0.1.0")
app.include_router(tts.router)   # /api/speak — optional server voice

_log = logging.getLogger("uvicorn.error")
_log.info("[kahie] backend=%s  ear1=%s  ear2=%s  tts=%s  ffmpeg=%s", config.BACKEND, config.EAR1_MODE,
          "on" if config.EAR2_ON else "off", "gemini" if tts.enabled() else "phone", bool(shutil.which("ffmpeg")))
if config.BACKEND != "mock" and config.EAR1_MODE != "voice_transcribe":
    _log.warning("[kahie] EAR1_MODE=%s — set EAR1_MODE=voice_transcribe in .env (dedicated ASR, better on short answers)",
                 config.EAR1_MODE)
conn = db.connect()
db.init(conn)
db.seed_demo(conn)


class Correction(BaseModel):
    heard: str
    canonical: str
    kind: str = "word"  # product | customer | unit | word


class Confirm(BaseModel):
    pending_id: str
    corrections: list[Correction] = []


@app.get("/api/health")
def health():
    return {"ok": True, "backend": config.BACKEND, "tts": "server" if tts.enabled() else "phone",
            "ffmpeg": shutil.which("ffmpeg") is not None,
            "ear1": config.EAR1_MODE, "ear2": "spark-audio" if config.EAR2_ON else "off"}   # without ffmpeg the ears get no audio


@app.post("/api/voice")
async def voice(audio: UploadFile | None = File(None), text: str | None = Form(None)):
    """A voice note (multipart 'audio'). 'text' lets tests and the mock mode skip ASR."""
    data = await audio.read() if audio else b""
    mime = audio.content_type if audio else "audio/webm"
    if not data and not text:
        raise HTTPException(400, "audio or text required")
    return pipeline.handle_voice(conn, data, mime, hint_text=text)


@app.post("/api/image")
async def image(photo: UploadFile = File(...)):
    """Photo of the paper notebook → debts to confirm."""
    data = await photo.read()
    ctx = pipeline._context(conn)
    calls = models_client.read_notebook(data, photo.content_type or "image/jpeg", ctx)
    return pipeline.handle_plan(conn, calls, transcript="[photo du cahier]")


def _execute_pending(pending_id: str, corrections=()) -> dict:
    row = conn.execute("SELECT * FROM pending_plans WHERE id=?", (pending_id,)).fetchone()
    if not row:
        raise HTTPException(404, "pending plan not found")
    verdict = json.loads(row["verdict_json"])
    if verdict["level"] == "clarify":
        raise HTTPException(409, "clarification needed before execution")
    for c in corrections:
        lexicon.learn(conn, c.heard, c.canonical, c.kind)
    result = executor.execute(conn, row["transcript"], json.loads(row["plan_json"]), verdict)
    conn.execute("DELETE FROM pending_plans WHERE id=?", (pending_id,))
    result["message"] = phrases.success(verdict["lines"])
    result["speak"] = speakify(result["message"])
    return result


@app.post("/api/confirm")
def confirm(body: Confirm):
    return _execute_pending(body.pending_id, body.corrections)


@app.post("/api/reply")
async def reply(pending_id: str = Form(...), audio: UploadFile | None = File(None), text: str | None = Form(None),
                attempt: int = Form(0)):
    """Two-way voice: the shopkeeper's spoken answer to the card or to Kahie's question.

    Card to confirm  → "eny" writes (same as ✓), "tsia" cancels, anything else → "Eny sa tsia?"
    Question         → picks one of the two readings, or adds the answer to the note and plans again.
    'text' lets tests and the mock mode skip ASR. 'attempt' > 0 uses the softer phrasing.
    """
    row = conn.execute("SELECT * FROM pending_plans WHERE id=?", (pending_id,)).fetchone()
    if not row:
        raise HTTPException(404, "pending plan not found")
    data = await audio.read() if audio else b""
    if not data and not text:
        raise HTTPException(400, "audio or text required")
    heard = pipeline.hear_short(conn, data, audio.content_type if audio else "audio/webm", hint_text=text)
    said = heard["text"]
    verdict = json.loads(row["verdict_json"])

    def ask(state):
        msg = phrases.say(state, min(attempt + 1, 1))
        return {"action": "repeat", "heard": said, "message": msg, "speak": speakify(msg), "error": heard["error"]}

    if not said.strip():
        return ask("not_heard")
    cmd = commands.detect(said)
    if cmd == "repeat":   # "avereno" / "tsy re": say the card or the question again (never a "no")
        msg = verdict.get("spoken_confirmation") or verdict.get("question") or ""
        return {"action": "repeat", "heard": said, "message": msg, "speak": verdict.get("speak") or speakify(msg)}
    if verdict["level"] == "clarify" and (cmd == "stop" or commands.is_cancel(said)):
        conn.execute("DELETE FROM pending_plans WHERE id=?", (pending_id,))
        msg = phrases.say("cancelled", min(attempt, 1))
        return {"action": "cancelled", "heard": said, "message": msg, "speak": speakify(msg)}
    if verdict["level"] != "clarify":
        yn = dialogue.yes_no(said)
        if yn is True:
            return {"action": "confirmed", "heard": said, **_execute_pending(pending_id)}
        if yn is False:
            conn.execute("DELETE FROM pending_plans WHERE id=?", (pending_id,))
            msg = phrases.say("cancelled", min(attempt, 1))
            return {"action": "cancelled", "heard": said, "message": msg, "speak": speakify(msg)}
        return ask("yes_or_no")
    choice = dialogue.pick_option(verdict.get("options") or [], said)
    if choice:
        new = pipeline.handle_clarification(conn, pending_id, choice=choice)
    else:
        new = pipeline.handle_clarification(conn, pending_id, text=dialogue.merge_answer(row["transcript"], said))
    return {"action": "plan", "heard": said, "result": new}


@app.post("/api/undo_reply")
async def undo_reply(audit_id: int = Form(...), audio: UploadFile | None = File(None), text: str | None = Form(None),
                     attempt: int = Form(0)):
    """Hands-free undo: Kahie asked "Hofoanana ve ilay farany: « … »?"; only a clear "eny" undoes."""
    data = await audio.read() if audio else b""
    if not data and not text:
        raise HTTPException(400, "audio or text required")
    said = pipeline.hear_short(conn, data, audio.content_type if audio else "audio/webm", hint_text=text)["text"]
    yn = dialogue.yes_no(said) if said.strip() else None
    if yn is True:
        try:
            result = executor.undo(conn, audit_id)
        except ValueError as e:
            raise HTTPException(400, str(e))
        msg = phrases.say("undone", min(attempt, 1))
        return {"action": "undone", "heard": said, "message": msg, "speak": speakify(msg), **result}
    if yn is False:
        msg = phrases.say("not_undone", min(attempt, 1))
        return {"action": "kept", "heard": said, "message": msg, "speak": speakify(msg)}
    msg = phrases.say("yes_or_no", min(attempt + 1, 1))
    return {"action": "repeat", "heard": said, "message": msg, "speak": speakify(msg)}


class Clarify(BaseModel):
    pending_id: str
    choice: str | None = None  # "ear1" | "ear2" when the two ears disagreed
    text: str | None = None    # the note rewritten by the merchant, for any other question


@app.post("/api/clarify")
def clarify(body: Clarify):
    """Answer the agent's question; returns a NEW pending plan (still needs the confirmation tap)."""
    try:
        return pipeline.handle_clarification(conn, body.pending_id, body.choice, body.text)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/undo/{audit_id}")
def undo(audit_id: int):
    try:
        return executor.undo(conn, audit_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/lexicon")
def add_word(c: Correction):
    lexicon.learn(conn, c.heard, c.canonical, c.kind)
    return {"ok": True, "lexicon": lexicon.all_entries(conn)}


@app.get("/api/report")
def report():
    today = date.today().isoformat()
    sales = conn.execute(
        "SELECT COALESCE(SUM(total),0) t, COUNT(*) n FROM sales WHERE voided=0 AND date(created_at)=?", (today,)
    ).fetchone()
    debts = [dict(r) for r in conn.execute(
        "SELECT c.name, SUM(d.amount) owed FROM debts d JOIN customers c ON c.id=d.customer_id "
        "WHERE d.voided=0 GROUP BY c.id HAVING owed > 0 ORDER BY owed DESC")]
    low = [dict(r) for r in conn.execute("SELECT name, unit, stock FROM products WHERE stock < 10 ORDER BY stock")]
    journal = [dict(r) for r in conn.execute(
        "SELECT id, created_at, kind, transcript, undoes_id FROM audit_log ORDER BY id DESC LIMIT 20")]
    return {"date": today, "sales_total": sales["t"], "sales_count": sales["n"], "debts": debts,
            "low_stock": low, "journal": journal, "stock": [dict(r) for r in conn.execute("SELECT * FROM products")]}


# ---------------------------------------------------------------- aged debts
def _oldest_unpaid_since(conn, customer_id: int):
    """Walk the debt rows in order and return the created_at of the earliest row
    after which the running balance stayed > 0 until now. None if the balance <= 0.

    This is what a shopkeeper means by "since when does this person owe me?":
    a repayment that clears the balance resets the clock.
    """
    rows = conn.execute(
        "SELECT amount, created_at FROM debts WHERE customer_id=? AND voided=0 ORDER BY created_at, id",
        (customer_id,),
    ).fetchall()
    balance, since = 0, None
    for r in rows:
        prev = balance
        balance += r["amount"]
        if prev <= 0 and balance > 0:
            since = r["created_at"]
        elif prev > 0 and balance <= 0:
            since = None
    return since if balance > 0 else None


@app.get("/api/alerts")
def alerts(days: int = 7):
    """Aged debts: every customer who still owes, with the age of their oldest unpaid debt.

    `urgent` = customers whose unpaid balance has been > 0 for at least `days` days.
    """
    customers = conn.execute("""
        SELECT c.id, c.name, SUM(d.amount) AS owed
        FROM debts d JOIN customers c ON c.id = d.customer_id
        WHERE d.voided = 0
        GROUP BY c.id
        HAVING owed > 0
    """).fetchall()
    out = []
    today = date.today()
    for c in customers:
        since = _oldest_unpaid_since(conn, c["id"])
        if not since:
            continue
        age = (today - date.fromisoformat(since[:10])).days
        out.append({"name": c["name"], "owed": c["owed"], "oldest_since": since, "oldest_days": age})
    out.sort(key=lambda x: (-x["oldest_days"], -x["owed"]))
    return {"threshold_days": days, "alerts": out, "urgent": [a for a in out if a["oldest_days"] >= days]}


@app.get("/api/debt/{customer}")
def debt_report(customer: str):
    """Per-customer debt report: every line, its age in days, the net balance."""
    rows = conn.execute("""
        SELECT d.id, d.amount, d.reason, d.created_at,
               CAST(julianday('now') - julianday(d.created_at) AS INTEGER) AS age_days
        FROM debts d JOIN customers c ON c.id = d.customer_id
        WHERE d.voided = 0 AND lower(c.name) = lower(?)
        ORDER BY d.created_at, d.id
    """, (customer,)).fetchall()
    lines = [dict(r) for r in rows]
    total = sum(r["amount"] for r in lines)
    first_debt = next((r for r in lines if r["amount"] > 0), None)
    return {
        "customer": customer,
        "total": total,
        "oldest": first_debt["created_at"] if first_debt else None,
        "oldest_days": first_debt["age_days"] if first_debt else 0,
        "lines": lines,
    }


@app.get("/api/statement")
def statement(days: int = 7):
    """Weekly statement sealed by the head of the audit hash chain."""
    since = (date.today() - timedelta(days=days)).isoformat()
    sales = conn.execute("SELECT COALESCE(SUM(total),0) t, COUNT(*) n FROM sales WHERE voided=0 AND date(created_at)>=?",
                         (since,)).fetchone()
    repaid = conn.execute("SELECT COALESCE(-SUM(amount),0) FROM debts WHERE voided=0 AND amount<0 AND date(created_at)>=?",
                          (since,)).fetchone()[0]
    check = audit.verify(conn)
    return {"from": since, "to": date.today().isoformat(), "sales_total": sales["t"], "sales_count": sales["n"],
            "repaid": repaid, "chain": check}


@app.get("/api/verify")
def verify_chain():
    return audit.verify(conn)


FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")