"""Kahie API — FastAPI. Serves the PWA from /frontend and the agent endpoints under /api."""
import json
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import audit, config, db, lexicon, models_client
from .agent import executor, pipeline

app = FastAPI(title="Kahie", version="0.1.0")
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
    return {"ok": True, "backend": config.BACKEND}


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


@app.post("/api/confirm")
def confirm(body: Confirm):
    row = conn.execute("SELECT * FROM pending_plans WHERE id=?", (body.pending_id,)).fetchone()
    if not row:
        raise HTTPException(404, "pending plan not found")
    verdict = json.loads(row["verdict_json"])
    if verdict["level"] == "clarify":
        raise HTTPException(409, "clarification needed before execution")
    for c in body.corrections:
        lexicon.learn(conn, c.heard, c.canonical, c.kind)
    result = executor.execute(conn, row["transcript"], json.loads(row["plan_json"]), verdict)
    conn.execute("DELETE FROM pending_plans WHERE id=?", (body.pending_id,))
    return result


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