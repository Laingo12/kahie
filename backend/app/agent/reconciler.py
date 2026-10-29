"""Double listening: compare ear 1 (Voice Transcribe) and ear 2 (Spark audio).

Both ears must already be normalised (spoken numbers → digits, learned lexicon applied):
see pipeline.normalize_ear(). Then:
Agreement on the facts that matter (numbers, products, customers) → continue.
Disagreement → a targeted clarification question with the two readings as choices.
"""
import re

from ..numbers_mg import numbers_in
from .. import phrases


def _names(text: str, vocab: list[str]) -> set[str]:
    t = text or ""
    return {w for w in vocab if re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", t, flags=re.IGNORECASE)}


def _options(ear1: str, ear2: str) -> list[dict]:
    return [{"id": "ear1", "text": ear1}, {"id": "ear2", "text": ear2}]


def reconcile(ear1: str, ear2: str, vocab: list[str]) -> dict:
    """Returns {"text", "agree", "conflicts": [...], "question", "options"}."""
    ear1, ear2 = (ear1 or "").strip(), (ear2 or "").strip()
    if not ear1 and not ear2:
        return {"text": "", "agree": False, "conflicts": [{"kind": "empty"}],
                "question": phrases.say("not_heard"), "options": []}
    if not ear1 or not ear2:  # one ear not wired, failed or switched off: single ear
        return {"text": ear1 or ear2, "agree": True, "conflicts": [], "question": None, "options": []}
    if ear1.lower() == ear2.lower():
        return {"text": ear1, "agree": True, "conflicts": [], "question": None, "options": []}

    conflicts = []
    n1, n2 = numbers_in(ear1), numbers_in(ear2)
    if n1 != n2:
        conflicts.append({"kind": "number", "ear1": sorted(n1 - n2), "ear2": sorted(n2 - n1)})
    w1, w2 = _names(ear1, vocab), _names(ear2, vocab)
    if w1 != w2:
        conflicts.append({"kind": "name", "ear1": sorted(w1 - w2), "ear2": sorted(w2 - w1)})

    if not conflicts:
        # Same facts, different wording: keep the version that names more known words.
        text = ear2 if len(w2) >= len(w1) else ear1
        return {"text": text, "agree": True, "conflicts": [], "question": None, "options": []}

    c = conflicts[0]
    a = ", ".join(map(str, c["ear1"])) or "?"
    b = ", ".join(map(str, c["ear2"])) or "?"
    q = phrases.say("clarify_amount" if c["kind"] == "number" else "clarify_name", a=a, b=b)
    return {"text": ear1, "agree": False, "conflicts": conflicts, "question": q, "options": _options(ear1, ear2)}
