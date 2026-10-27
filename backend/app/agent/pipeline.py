"""The agent loop: listen (2 ears) → normalise → reconcile → plan → verify → [confirm] → execute → audit.

Nothing is written to the database before the merchant taps "confirm".
When the agent asks a question, the merchant answers it on the card (handle_clarification)
and the note is planned again; still nothing is written before the tap.
"""
import json
import time
import uuid

from .. import config, lexicon, models_client
from ..numbers_mg import to_digits
from . import reconciler, verifier


def _context(conn) -> dict:
    products = [dict(r) for r in conn.execute("SELECT name, unit, unit_price, stock FROM products")]
    customers = [r["name"] for r in conn.execute("SELECT name FROM customers")]
    return {
        "lexicon": lexicon.as_prompt(conn),
        "products": ", ".join(f"{p['name']} ({p['unit']}, {p['unit_price']} Ar)" for p in products),
        "customers": ", ".join(customers) or "(aucun)",
        "product_list": [p["name"] for p in products],
        "customer_list": customers,
    }


def normalize_ear(conn, text: str) -> str:
    """Same treatment for both ears before they are compared:
    spoken numbers → digits (always), then learned mishearings → canonical words (if the lexicon is on)."""
    text = to_digits(text or "")
    return lexicon.normalize(conn, text) if config.LEXICON_ON else text


def handle_voice(conn, audio: bytes, mime: str, hint_text: str | None = None) -> dict:
    t0 = time.perf_counter()
    ctx = _context(conn)
    kw = lexicon.keywords(conn) if config.LEXICON_ON else []
    if not config.LEXICON_ON:
        ctx["lexicon"] = ""

    ear1 = models_client.transcribe(audio, mime, kw, hint_text)
    ear2 = models_client.listen_with_spark(audio, mime, ctx["lexicon"], hint_text) if config.EAR2_ON else {"text": ""}
    raw = {"ear1": ear1["text"], "ear2": ear2["text"]}
    n1, n2 = normalize_ear(conn, raw["ear1"]), normalize_ear(conn, raw["ear2"])
    rec = reconciler.reconcile(n1, n2, kw)  # respects LEXICON_ON (was lexicon.keywords(conn))
    if config.BACKEND == "mock" and audio and not hint_text and not n1 and not n2:
        rec["question"] = "Mode mock (tsy misy API key): tsy henoina ny feo. Soraty eto ambany ny fanamarihana."

    if not rec["agree"]:
        verdict = verifier.verify(conn, [], rec)
        verdict["options"] = rec["options"]
        return _store_pending(conn, rec["text"], n1, n2, [], verdict, t0, raw)
    return _plan_and_store(conn, rec["text"], n1, n2, ctx, t0, raw)


def handle_clarification(conn, pending_id: str, choice: str | None = None, text: str | None = None) -> dict:
    """The merchant answered the agent's question on the card.

    choice = "ear1" | "ear2": pick one of the two readings when the ears disagreed.
    text   = the note rewritten by the merchant (any other question: unknown product, missing amount…).
    The old pending plan is replaced by a new one, which still needs the confirmation tap.
    """
    t0 = time.perf_counter()
    row = conn.execute("SELECT * FROM pending_plans WHERE id=?", (pending_id,)).fetchone()
    if not row:
        raise LookupError("pending plan not found")
    if choice in ("ear1", "ear2") and row[choice]:
        note = row[choice]
    elif text and text.strip():
        note = normalize_ear(conn, text)
    else:
        raise ValueError("choice (ear1|ear2) or text required")
    ctx = _context(conn)
    if not config.LEXICON_ON:
        ctx["lexicon"] = ""
    # Plan FIRST, then delete the old pending. If planning fails, the old pending survives
    # (the merchant can retry instead of ending up with nothing to confirm).
    result = _plan_and_store(conn, note, row["ear1"], row["ear2"], ctx, t0)
    conn.execute("DELETE FROM pending_plans WHERE id=?", (pending_id,))
    return result


def _plan_and_store(conn, text, ear1, ear2, ctx, t0, raw=None) -> dict:
    calls = models_client.plan(text, ctx)
    verdict = verifier.verify(conn, calls)
    return _store_pending(conn, text, ear1, ear2, calls, verdict, t0, raw)


def handle_plan(conn, calls: list[dict], transcript: str) -> dict:
    """Used by the notebook photo import: calls come from Spark vision."""
    verdict = verifier.verify(conn, calls)
    return _store_pending(conn, transcript, None, None, calls, verdict, time.perf_counter())


def _store_pending(conn, text, ear1, ear2, calls, verdict, t0, raw=None) -> dict:
    """ear1/ear2 are stored normalised (they are the choices offered on the card);
    `raw` keeps what each ear really heard, for display and evaluation."""
    pid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO pending_plans(id, transcript, ear1, ear2, plan_json, verdict_json) VALUES (?,?,?,?,?,?)",
        (pid, text, ear1, ear2, json.dumps(calls, ensure_ascii=False), json.dumps(verdict, ensure_ascii=False)),
    )
    raw = raw or {"ear1": ear1, "ear2": ear2}
    return {
        "pending_id": pid,
        "transcript": text,
        "ears": {"ear1": raw["ear1"], "ear2": raw["ear2"]},
        "plan": calls,
        "verdict": verdict,
        "latency_ms": int((time.perf_counter() - t0) * 1000),
    }