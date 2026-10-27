"""Merchant lexicon: learned from corrections on the confirmation card.

Used twice: as keyword/context biasing for Voice Transcribe (ear 1)
and as context in the Spark prompts (ear 2 + planner).
"""
import re


def all_entries(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT heard, canonical, kind, hits FROM lexicon ORDER BY hits DESC")]


def keywords(conn, limit: int = 100) -> list[str]:
    """Words to bias recognition toward: product names, customer names, learned words."""
    words = [r["name"] for r in conn.execute("SELECT name FROM products")]
    words += [r["name"] for r in conn.execute("SELECT name FROM customers")]
    words += [r["canonical"] for r in conn.execute("SELECT DISTINCT canonical FROM lexicon")]
    seen, out = set(), []
    for w in words:
        if w.lower() not in seen:
            seen.add(w.lower())
            out.append(w)
    return out[:limit]


def as_prompt(conn) -> str:
    lines = [f'- "{e["heard"]}" = {e["canonical"]} ({e["kind"]})' for e in all_entries(conn)]
    return "Vocabulaire du commerçant :\n" + ("\n".join(lines) if lines else "(vide)")


def learn(conn, heard: str, canonical: str, kind: str) -> None:
    heard, canonical = heard.strip().lower(), canonical.strip()
    if not heard or heard == canonical.lower():
        return
    conn.execute(
        "INSERT INTO lexicon(heard, canonical, kind) VALUES (?,?,?) "
        "ON CONFLICT(heard, canonical) DO UPDATE SET hits = hits + 1",
        (heard, canonical, kind),
    )


def normalize(conn, text: str) -> str:
    """Replace learned mishearings with canonical words.

    Runs on each ear BEFORE the two ears are compared, so a word the merchant already
    corrected once no longer causes a disagreement. Whole words only, punctuation-safe
    ("kapwaka," → "kapoaka,"), case of the rest of the text kept.
    """
    out = text or ""
    for e in all_entries(conn):
        out = re.sub(r"(?<!\w)" + re.escape(e["heard"]) + r"(?!\w)", lambda _m, c=e["canonical"]: c, out,
                     flags=re.IGNORECASE)
    return out.strip()
