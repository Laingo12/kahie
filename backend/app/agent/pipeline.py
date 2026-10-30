"""The agent loop: listen (2 ears) → normalise → reconcile → plan → verify → [confirm] → execute → audit.

Nothing is written to the database before the merchant taps "confirm".
When the agent asks a question, the merchant answers it on the card (handle_clarification)
and the note is planned again; still nothing is written before the tap.

Every step returns a `trace` entry: which model was used, its latency, the source
(mock / spark-audio / voice_transcribe), and the reason it asked a question.
The UI shows this trace so the agent is *verifiable*, not a black box.
"""
import json
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from .. import config, lexicon, models_client, units
from .. import phrases
from ..numbers_mg import speakify, to_digits
from . import answers, commands, grounding, preview, reconciler, verifier


def _context(conn) -> dict:
    products = [dict(r) for r in conn.execute("SELECT name, unit, unit_price, stock FROM products")]
    customers = [r["name"] for r in conn.execute("SELECT name FROM customers")]
    return {
        "lexicon": lexicon.as_prompt(conn),
        "products": ", ".join(f"{p['name']} ({p['unit_price']:g} Ar/{p['unit']}; unités: "
                              f"{', '.join(units.accepted(p['name'], p['unit']))})" for p in products),
        "customers": ", ".join(customers) or "(aucun)",
        "product_list": [p["name"] for p in products],
        "customer_list": customers,
    }


def normalize_ear(conn, text: str) -> str:
    """Same treatment for both ears before they are compared:
    spoken numbers → digits and halves → decimals ("antsasaky ny kilao" → "0.5 kg") always,
    then learned mishearings → canonical words (if the lexicon is on)."""
    text = units.normalize_text(text or "")
    return lexicon.normalize(conn, text) if config.LEXICON_ON else text


log = logging.getLogger("uvicorn.error")   # shows in the uvicorn terminal


def _keep_audio(audio: bytes, mime: str, kind: str) -> str | None:
    """KAHIE_DEBUG_AUDIO=on: save the recording as received, to replay it or test it with eval/api_smoke.py."""
    if not config.DEBUG_AUDIO or not audio:
        return None
    folder = config.ROOT / "data" / "debug"
    folder.mkdir(parents=True, exist_ok=True)
    ext = next((e for e in ("webm", "mp4", "ogg", "wav", "mpeg") if e in (mime or "")), "bin")
    path = folder / f"{time.strftime('%Y%m%d-%H%M%S')}-{kind}.{ext}"
    path.write_bytes(audio)
    return str(path)


def _log_ear(step: str, result: dict, audio: bytes, ms: int) -> None:
    log.info("[kahie] %s %-16s %5d ms  %6d bytes  %r%s", step, result.get("source"), ms, len(audio or b""),
             (result.get("text") or "")[:120], f"  ✗ {result['error']}" if result.get("error") else "")


def _ear_trace(step: str, result: dict, ms: int) -> dict:
    """One trace entry for an ear. Shows model, source, latency — visible in the UI."""
    if config.BACKEND == "mock":
        model = "mock"
    elif step == "ear1":
        model = {"voice_transcribe": config.MODEL_TRANSCRIBE, "whisper": config.WHISPER_MODEL,
                 "off": None}.get(config.EAR1_MODE, config.MODEL_EAR1)
    elif step == "ear2":
        model = config.MODEL_EAR2
    else:
        model = None
    return {
        "step": step,
        "model": model,
        "source": result.get("source"),
        "ms": ms,
        "text": (result.get("text") or "")[:160],
        "dropped": result.get("dropped", False),
        "error": result.get("error"),
        "rejected": result.get("rejected"),       # text refused as a transcript (kept for the trace only)
        "seconds": result.get("seconds"),
    }


def handle_voice(conn, audio: bytes, mime: str, hint_text: str | None = None) -> dict:
    t0 = time.perf_counter()
    trace = []
    ctx = _context(conn)
    kw = lexicon.keywords(conn) if config.LEXICON_ON else []
    if not config.LEXICON_ON:
        ctx["lexicon"] = ""

    saved = _keep_audio(audio, mime, "note")
    if saved:
        log.info("[kahie] note audio saved: %s (%s)", saved, mime)

    # The two ears listen AT THE SAME TIME (independent API calls): latency = the slower ear, not the sum.
    def ear_one():
        t = time.perf_counter()
        r = models_client.transcribe(audio, mime, kw, hint_text)
        return r, round((time.perf_counter() - t) * 1000)

    def ear_two():
        t = time.perf_counter()
        r = (models_client.listen_with_spark(audio, mime, ctx["lexicon"], hint_text) if config.EAR2_ON
             else {"text": "", "source": "off"})
        return r, round((time.perf_counter() - t) * 1000)

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1, f2 = pool.submit(ear_one), pool.submit(ear_two)
        (ear1, ms1), (ear2, ms2) = f1.result(), f2.result()
    trace.append(_ear_trace("ear1", ear1, ms1))
    trace.append(_ear_trace("ear2", ear2, ms2))
    _log_ear("ear1", ear1, audio, ms1)
    _log_ear("ear2", ear2, audio, ms2)

    raw = {"ear1": ear1["text"], "ear2": ear2["text"]}
    n1, n2 = normalize_ear(conn, raw["ear1"]), normalize_ear(conn, raw["ear2"])
    trace.append({
        "step": "normalize",
        "ear1": n1[:160],
        "ear2": n2[:160],
        "lexicon_on": config.LEXICON_ON,
        "changed": (n1 != raw["ear1"]) or (n2 != raw["ear2"]),
    })

    # Hands-free voice command ("asehoy ny tabilao", "foano ilay farany"…): answered before any planning.
    for heard in (n1, n2):
        cmd = commands.detect(heard)
        if cmd:
            trace.append({"step": "command", "command": cmd, "text": heard})
            out = commands.respond(conn, cmd, heard)
            out.update({"ears": raw, "trace": trace, "latency_ms": int((time.perf_counter() - t0) * 1000)})
            return out

    rec = reconciler.reconcile(n1, n2, kw)
    trace.append({
        "step": "reconcile",
        "agree": rec["agree"],
        "conflicts": rec.get("conflicts", []),
        "question": rec.get("question"),
    })

    if config.BACKEND == "mock" and audio and not hint_text and not n1 and not n2:
        rec["question"] = "Tsy misy API key (mode mock): tsy afaka mihaino feo aho. Soraty eto ambany ilay izy."

    if not rec["agree"]:
        verdict = verifier.verify(conn, [], rec)
        verdict["options"] = rec["options"]
        preview.attach(conn, verdict)  # adds the spoken question
        return _store_pending(conn, rec["text"], n1, n2, [], verdict, t0, raw, trace)

    # Plan
    t = time.perf_counter()
    calls = models_client.plan(rec["text"], ctx)
    trace.append({
        "step": "plan",
        "model": config.MODEL_PLANNER if config.BACKEND != "mock" else "mock",
        "ms": round((time.perf_counter() - t) * 1000),
        "calls": [{"tool": c["tool"], "args": c.get("args")} for c in calls],
    })

    return _finish(conn, rec["text"], n1, n2, calls, t0, raw, trace)


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
    clarify_trace = [{"step": "clarify", "choice": choice, "text": (text or "")[:160]}]
    result = _plan_and_store(conn, note, row["ear1"], row["ear2"], ctx, t0, raw=None, trace=clarify_trace)
    conn.execute("DELETE FROM pending_plans WHERE id=?", (pending_id,))
    return result


def _plan_and_store(conn, text, ear1, ear2, ctx, t0, raw=None, trace=None) -> dict:
    calls = models_client.plan(text, ctx)
    trace = list(trace or []) + [
        {"step": "plan", "model": config.MODEL_PLANNER if config.BACKEND != "mock" else "mock",
         "calls": [{"tool": c["tool"], "args": c.get("args")} for c in calls]}]
    return _finish(conn, text, ear1, ear2, calls, t0, raw, trace)


def _finish(conn, text, ear1, ear2, calls, t0, raw=None, trace=None) -> dict:
    """After planning: a question is answered from the database (nothing to confirm, nothing written);
    actions are verified, previewed (balances before → after) and stored for the ✓ tap."""
    trace = list(trace or [])
    questions = [c for c in calls if c["tool"] == "ask_ledger"]
    actions = [c for c in calls if c["tool"] != "ask_ledger"]
    ans = answers.answer(conn, questions) if questions else None
    if ans:
        trace.append({"step": "answer", "source": "database", "text": ans["text"][:160]})
    if questions and not actions:
        raw = raw or {"ear1": ear1, "ear2": ear2}
        return {"kind": "answer", "answer": ans, "transcript": text,
                "ears": {"ear1": raw["ear1"], "ear2": raw["ear2"]}, "plan": calls,
                "verdict": {"level": "answer", "lines": [], "issues": []},
                "latency_ms": int((time.perf_counter() - t0) * 1000), "trace": trace}
    # Grounding: numbers and customer names in the plan must have been said (no invented values).
    heard = {}
    for r in conn.execute("SELECT heard, canonical FROM lexicon WHERE kind = 'product'"):
        heard.setdefault(r["canonical"].lower(), []).append(r["heard"])
    g = grounding.check(text, actions, heard)
    trace.append({"step": "grounding", "source": "transcript", "missing": g["missing"],
                  "unsure_products": g["unsure_products"]})
    if g["missing"] and not any(c["tool"] == "ask_clarification" for c in actions):
        verdict = {"level": "clarify", "lines": [], "issues": [],
                   "question": phrases.say("not_said", missing=", ".join(g["missing"]))}
        preview.attach(conn, verdict)
        trace.append({"step": "verify", "level": "clarify", "issues": []})
        return _store_pending(conn, text, ear1, ear2, actions, verdict, t0, raw, trace)
    verdict = verifier.verify(conn, actions)
    for name in g["unsure_products"]:
        if verdict.get("level") != "clarify":
            verdict.setdefault("issues", []).append(
                {"level": "warn", "msg": f"Tsy re mazava ny entana « {name} »: hamarino"})
            verdict["level"] = "warn"
    preview.attach(conn, verdict)
    if ans:
        verdict["answer"] = ans
    trace.append({"step": "verify", "level": verdict["level"], "issues": verdict.get("issues", [])})
    return _store_pending(conn, text, ear1, ear2, actions, verdict, t0, raw, trace)


def handle_plan(conn, calls: list[dict], transcript: str) -> dict:
    """Used by the notebook photo import: calls come from Spark vision."""
    verdict = verifier.verify(conn, calls)
    preview.attach(conn, verdict)
    trace = [
        {"step": "vision", "source": "spark-vision",
         "model": config.MODEL_VISION if config.BACKEND != "mock" else "mock",
         "calls": [{"tool": c["tool"], "args": c.get("args")} for c in calls]},
        {"step": "verify", "level": verdict["level"], "issues": verdict.get("issues", [])},
    ]
    return _store_pending(conn, transcript, None, None, calls, verdict, time.perf_counter(), trace=trace)


def _said_qty(qty, unit) -> str:
    """How a quantity is said: "mofo 10" (pieces need no unit), "siramamy 500 g", "vary 3 kapoaka"."""
    return f"{qty:g}" if units.normalize(unit) == "isa" else units.fmt_qty(qty, unit)


def _spoken_confirmation(verdict: dict) -> str:
    """The yes/no question Kahie reads aloud before anything is written.

    clarify    → the question itself ("Firy marina: 2000 sa 3000?")
    ok / warn  → "Soratana ve fa naka vary 3 kapoaka i Rabe, ary mbola manana trosa 1 000 Ar?"
                 (a warning is read first: "Tandremo: … ." so the shopkeeper hears why to check)
    Quantities are said in the unit the shopkeeper used; amounts come from the verifier, never the model.
    """
    if verdict.get("level") == "clarify":
        return verdict.get("question") or ""
    lines = verdict.get("lines") or []
    if verdict.get("level") not in ("ok", "warn") or not lines:
        return ""
    sales, debts, parts = {}, {}, []
    for ln in lines:
        if ln["kind"] == "sale":
            sales.setdefault(ln.get("customer"), []).append(ln)
        elif ln["kind"] == "debt":
            debts[ln["customer"]] = debts.get(ln["customer"], 0) + ln["amount"]
    for who, items in sales.items():
        goods = " sy ".join(f"{ln['product']} {_said_qty(ln['qty'], ln['unit'])}" for ln in items)
        total = sum(ln["total"] for ln in items)
        part = f"naka {goods} i {who}" if who else f"nisy olona tsy fantatra naka {goods}"
        owed = debts.pop(who, 0) if who else 0
        if owed and owed < total:
            part += f", nandoa {answers.ar(total - owed)}, ary mbola manana trosa {answers.ar(owed)}"
        elif owed:
            part += f", ary mbola manana trosa {answers.ar(owed)}"
        else:
            part += f", ary nandoa {answers.ar(total)}"
        prior = next((c for c in (verdict.get("preview") or {}).get("customers", []) if c["name"] == who and c["before"] > 0), None)
        if owed and prior:   # the shopkeeper hears the new total, not only today's part
            part += f" (lasa {answers.ar(prior['after'])} ny trosany rehetra)"
        parts.append(part)
    for ln in lines:
        if ln["kind"] == "debt" and ln["customer"] in debts:            # a loan without a sale
            parts.append(f"nindrana {answers.ar(debts.pop(ln['customer']))} i {ln['customer']}")
        elif ln["kind"] == "payment":
            parts.append(f"nandoa trosa {answers.ar(ln['amount'])} i {ln['customer']}")
        elif ln["kind"] == "stock":
            q = _said_qty(abs(ln.get("delta_spoken", ln["delta"])), ln.get("unit") or ln.get("base_unit", ""))
            parts.append(f"niditra {ln['product']} {q}" if ln["delta"] > 0 else f"simba na very {ln['product']} {q}")
    question = "Soratana ve fa " + " ary ".join(parts) + "?"
    warnings = [i["msg"] for i in verdict.get("issues", []) if i.get("level") == "warn"]
    return (f"Tandremo: {'; '.join(w.rstrip('. ') for w in warnings)}. " if warnings else "") + question


def _store_pending(conn, text, ear1, ear2, calls, verdict, t0, raw=None, trace=None) -> dict:
    """ear1/ear2 are stored normalised (they are the choices offered on the card);
    `raw` keeps what each ear really heard, for display and evaluation.
    `trace` is the reasoning trace shown in the UI — which model, how long, why it asked."""
    spoken = _spoken_confirmation(verdict)
    if spoken:
        verdict["spoken_confirmation"] = spoken
        verdict["speak"] = speakify(spoken)       # what the phone reads aloud (numbers in Malagasy words)
    pid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO pending_plans(id, transcript, ear1, ear2, plan_json, verdict_json) VALUES (?,?,?,?,?,?)",
        (pid, text, ear1, ear2, json.dumps(calls, ensure_ascii=False), json.dumps(verdict, ensure_ascii=False)),
    )
    raw = raw or {"ear1": ear1, "ear2": ear2}
    return {
        "kind": "plan",
        "pending_id": pid,
        "transcript": text,
        "ears": {"ear1": raw["ear1"], "ear2": raw["ear2"]},
        "plan": calls,
        "verdict": verdict,
        "latency_ms": int((time.perf_counter() - t0) * 1000),
        "trace": trace or [],
        "spoken_confirmation": spoken,
    }

def hear_short(conn, audio: bytes, mime: str, hint_text: str | None = None) -> dict:
    """A short spoken answer ("eny", "roa arivo", "Rabe"): one ear is enough.
    Voice Transcribe first (fast, dedicated ASR), Spark audio if it heard nothing. Guards apply as for notes."""
    kw = lexicon.keywords(conn) if config.LEXICON_ON else []
    saved = _keep_audio(audio, mime, "reply")
    if saved:
        log.info("[kahie] reply audio saved: %s (%s)", saved, mime)
    t = time.perf_counter()
    out = models_client.transcribe(audio, mime, kw, hint_text)
    _log_ear("reply-ear1", out, audio, round((time.perf_counter() - t) * 1000))
    if not (out.get("text") or "").strip() and audio and config.BACKEND != "mock":
        t = time.perf_counter()
        out = models_client.listen_with_spark(audio, mime, lexicon.as_prompt(conn) if config.LEXICON_ON else "", hint_text)
        _log_ear("reply-ear2", out, audio, round((time.perf_counter() - t) * 1000))
    return {"text": normalize_ear(conn, out.get("text") or ""), "raw": out.get("text") or "",
            "source": out.get("source"), "error": out.get("error")}
