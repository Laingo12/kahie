"""Grounding: every number and customer name the planner writes must come from what was said.

The planner (a language model) may "complete" a note: invent a quantity for "Rabe naka vary",
a customer, an amount. Before anything reaches the confirmation card, each value is checked
against the normalised transcript:
  - quantities, amounts, prices, stock changes → must appear as numbers in the text
    (0 is allowed: "crédit daholo" / "mbola tsy nandoa" means paid 0)
  - customer names → must appear in the text
  - product names → should appear (or be the canonical word of a lexicon entry heard in the text);
    if not, the card asks for a reinforced confirmation instead of blocking
An ungrounded number or name turns the plan into a question. Nothing is written.
"""
import re

_NUM = re.compile(r"(?<!\d)(?<!\d\.)\d+(?:\.\d+)?(?!\d)(?!\.\d)")   # 6000, 0.5, 2.5 — and "6000." at a sentence end


def _numbers(text: str) -> set[float]:
    return {float(x) for x in _NUM.findall((text or "").replace(" ", "").replace(" ", ""))}


def _said(word: str, text: str) -> bool:
    return bool(word) and re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", text or "", re.I) is not None


def check(text: str, calls: list[dict], heard_words: dict[str, list[str]] | None = None) -> dict:
    """→ {"missing": [...values not said...], "unsure_products": [...]}.
    heard_words: canonical product → words that map to it in the lexicon (e.g. vary → [riz])."""
    nums = _numbers(text)
    heard_words = heard_words or {}
    missing, unsure = [], []

    def need_number(v):
        if v is None:
            return
        try:
            x = abs(float(v))
        except (TypeError, ValueError):
            missing.append(str(v))
            return
        if x != 0 and x not in nums:
            missing.append(f"{x:g}")

    def need_customer(name):
        if name and not _said(name, text):
            missing.append(name)

    def check_product(name):
        if name and not _said(name, text) and not any(_said(w, text) for w in heard_words.get(name.lower(), [])):
            unsure.append(name)

    for c in calls:
        a = c.get("args") or {}
        t = c.get("tool")
        if t == "record_sale":
            need_customer(a.get("customer"))
            for it in a.get("items") or []:
                need_number(it.get("qty"))
                need_number(it.get("unit_price"))
                check_product(it.get("product"))
            need_number(a.get("paid"))
        elif t in ("record_debt", "record_payment"):
            need_customer(a.get("customer"))
            need_number(a.get("amount"))
        elif t == "update_stock":
            need_number(a.get("delta"))
            check_product(a.get("product"))
    return {"missing": list(dict.fromkeys(missing)), "unsure_products": list(dict.fromkeys(unsure))}
