"""Hands-free voice commands: "asehoy ny tabilao", "foano ilay farany", "ajanony"…

Checked on what the ears heard BEFORE the planner, so a command never becomes a ledger line and
costs no planner call. Only short utterances count (a note like "Rabe naka vary…" never matches).
Read-only, except "undo", which only ASKS ("Hofoanana ve …?"): the write happens after a clear "eny".
"""
import re
import unicodedata
from datetime import date, timedelta

from .. import audit, phrases
from ..numbers_mg import speakify
from .answers import ar

MAX_WORDS = 7

# (command, pattern) — first match wins; patterns run on lower-case text without accents.
PATTERNS = [
    ("stop",      r"^(ajanony|ajanona|mijanona|mangina|stop|arrete|arrete toi)( (ianao|azafady|kahie|aloha|izay))*$"),
    ("undo",      r"\b(foano|foanana|fafao|esory|annule|annuler|diso)\b.*\b(farany|teo|iny|dernier|derniere)\b"
                  r"|\b(farany|teo)\b.*\b(foano|foanana|fafao|esory)\b"),
    ("dash",      r"\btabilao\b|\b(asehoy|sokafy|jereo)\b.*\b(boky|kahie)\b|\btableau\b"),
    ("statement", r"\btatitra\b|\b(bilan|releve)\b"),
    ("home",      r"\b(miverena|fandraisana|accueil)\b"),
    ("repeat",    r"^(avereno|averino|lazao indray|repete|repeter|inona hoy ianao|tsy re)( (azafady|indray|ilay izy))*$"),
    ("help",      r"\b(fanampiana|aide)\b|inona no (azoko|azo) (lazaina|atao)"),
]


def _plain(text: str) -> str:
    t = unicodedata.normalize("NFD", (text or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return " ".join(re.findall(r"[a-z]+", t))


def detect(text: str) -> str | None:
    t = _plain(text)
    if not t or len(t.split()) > MAX_WORDS or re.search(r"\d", text or ""):
        return None
    for name, pat in PATTERNS:
        if re.search(pat, t):
            return name
    return None


CANCEL = {"foano", "foanana", "fafao", "tsia", "aza soratana", "aza atao", "annule", "annuler", "tsy soratana"}


def is_cancel(text: str) -> bool:
    """While Kahie asks a question, only an EXACT cancel word drops the note
    ("tsy nandoa" is an answer, not a cancel)."""
    return _plain(text) in CANCEL


def last_undoable(conn):
    return conn.execute(
        "SELECT id, transcript FROM audit_log a WHERE kind IN ('execute','import') "
        "AND NOT EXISTS (SELECT 1 FROM audit_log u WHERE u.undoes_id = a.id) ORDER BY id DESC LIMIT 1"
    ).fetchone()


def respond(conn, command: str, transcript: str, variant: int = 0) -> dict:
    """The reply for a detected command: what to show and what Kahie says."""
    out = {"kind": "command", "command": command, "transcript": transcript}
    if command == "dash":
        today = date.today().isoformat()
        sales = conn.execute("SELECT COALESCE(SUM(total),0) FROM sales WHERE voided=0 AND date(created_at)=?",
                             (today,)).fetchone()[0]
        debt = conn.execute("SELECT COALESCE(SUM(amount),0) FROM debts WHERE voided=0").fetchone()[0]
        msg = phrases.say("cmd_dash", variant, sales=ar(sales), debt=ar(max(debt, 0)))
    elif command == "statement":
        since = (date.today() - timedelta(days=7)).isoformat()
        sales = conn.execute("SELECT COALESCE(SUM(total),0) FROM sales WHERE voided=0 AND date(created_at)>=?",
                             (since,)).fetchone()[0]
        repaid = conn.execute("SELECT COALESCE(-SUM(amount),0) FROM debts WHERE voided=0 AND amount<0 "
                              "AND date(created_at)>=?", (since,)).fetchone()[0]
        seal = phrases.say("seal_ok" if audit.verify(conn).get("ok") else "seal_bad", variant)
        msg = phrases.say("cmd_statement", variant, sales=ar(sales), repaid=ar(repaid), seal=seal)
    elif command == "undo":
        row = last_undoable(conn)
        if row:
            what = (row["transcript"] or "").strip().rstrip(".")[:80]
            msg = phrases.say("cmd_undo_ask", variant, what=what)
            out["undo"] = {"audit_id": row["id"], "what": what}
        else:
            msg = phrases.say("cmd_undo_none", variant)
    elif command in ("home", "help", "stop"):
        msg = phrases.say("cmd_" + command, variant)
    else:  # repeat: the app replays what it said last
        msg = ""
    out["message"] = msg
    out["speak"] = speakify(msg) if msg else ""
    return out
