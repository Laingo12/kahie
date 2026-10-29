"""Builds eval/cases.jsonl from the scripted notes below.

Each note gives the text to read aloud and the IDEAL tool calls. The expected lines are not written
by hand: they are produced by running the real verifier on a fresh demo shop, so prices, totals and
debts always match what Kahie would propose. A note whose right answer is "ask a question" has
calls = ask_clarification → expected = [].

Usage:  python eval/make_cases.py            (writes eval/cases.jsonl)
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("KAHIE_DB", str(Path(tempfile.mkdtemp()) / "cases.db"))

from app import db  # noqa: E402
from app.agent import verifier  # noqa: E402


def sale(customer, items, paid=None):
    """items: (product, qty) in the product's usual unit, or (product, qty, unit) when the note says another unit."""
    its = [{"product": it[0], "qty": it[1], **({"unit": it[2]} if len(it) > 2 else {})} for it in items]
    return {"tool": "record_sale", "args": {"customer": customer, "items": its, "paid": paid}}


def pay(customer, amount):
    return {"tool": "record_payment", "args": {"customer": customer, "amount": amount}}


def debt(customer, amount):
    return {"tool": "record_debt", "args": {"customer": customer, "amount": amount}}


def stock(product, delta, unit=None):
    return {"tool": "update_stock", "args": {"product": product, "delta": delta, "reason": "restock" if delta > 0 else "loss",
                                             **({"unit": unit} if unit else {})}}


ASK = {"tool": "ask_clarification", "args": {"question": "?"}}

# (id, text read aloud, ideal calls). Customers are fictional; Lalao and Hery are new customers (verifier warns).
NOTES = [
    # ---------------------------------------------------------------- Malagasy (30)
    ("mg-001", "Rabe naka telo kapoaka vary, nandoa roa arivo Ariary.", [sale("Rabe", [("vary", 3)], 2000)]),
    ("mg-002", "Soa nividy siramamy kapoaka roa, voaloa avokoa.", [sale("Soa", [("siramamy", 2, "kapoaka")])]),
    ("mg-003", "Naivo naka menaka iray litatra, mbola tsy nandoa.", [sale("Naivo", [("menaka", 1)], 0)]),
    ("mg-004", "Bodo nividy savony efatra, nandoa enina arivo.", [sale("Bodo", [("savony", 4)], 6000)]),
    ("mg-005", "Mofo folo no lafo tamin'ny olona tsy fantatra.", [sale(None, [("mofo", 10)])]),
    ("mg-006", "Rabe nanefa trosa arivo Ariary.", [pay("Rabe", 1000)]),
    ("mg-007", "Soa nandoa ny trosany, telo arivo sy dimanjato.", [pay("Soa", 3500)]),
    ("mg-008", "Tonga ny vary, kapoaka dimampolo.", [stock("vary", 50)]),
    ("mg-009", "Simba ny mofo dimy.", [stock("mofo", -5)]),
    ("mg-010", "Naivo naka vary roa kapoaka sy siramamy iray kapoaka, nandoa arivo.",
     [sale("Naivo", [("vary", 2), ("siramamy", 1, "kapoaka")], 1000)]),
    ("mg-011", "Bodo nindrana roa arivo Ariary.", [debt("Bodo", 2000)]),
    ("mg-012", "Rabe naka mofo enina, nandoa arivo sy valonjato.", [sale("Rabe", [("mofo", 6)], 1800)]),
    ("mg-013", "Soa naka siramamy dimy kapoaka, nandoa telo arivo.", [sale("Soa", [("siramamy", 5, "kapoaka")], 3000)]),
    ("mg-014", "Nampiditra savony roapolo.", [stock("savony", 20)]),
    ("mg-015", "Lalao naka vary efatra kapoaka, mbola tsy nandoa.", [sale("Lalao", [("vary", 4)], 0)]),
    ("mg-016", "Naivo nanefa dimy arivo.", [pay("Naivo", 5000)]),
    ("mg-017", "Menaka roa litatra no lafo, voaloa.", [sale(None, [("menaka", 2)])]),
    ("mg-018", "Bodo naka zavatra.", [ASK]),
    ("mg-019", "Rabe naka vary.", [ASK]),
    ("mg-020", "Nisy olona nandoa trosa.", [ASK]),
    ("mg-021", "Hery nividy mofo roa amby folo, nandoa telo arivo sy eninjato.", [sale("Hery", [("mofo", 12)], 3600)]),
    ("mg-022", "Soa naka savony iray sy mofo telo.", [sale("Soa", [("savony", 1), ("mofo", 3)])]),
    ("mg-023", "Tonga ny siramamy, telopolo kapoaka.", [stock("siramamy", 30, "kapoaka")]),
    ("mg-024", "Very ny menaka iray litatra.", [stock("menaka", -1)]),
    ("mg-025", "Rabe naka siramamy efatra kapoaka, nandoa roa arivo.", [sale("Rabe", [("siramamy", 4, "kapoaka")], 2000)]),
    ("mg-026", "Naivo naka mofo valo, nandoa arivo.", [sale("Naivo", [("mofo", 8)], 1000)]),
    ("mg-027", "Bodo nanefa ny ampahany amin'ny trosany: arivo sy dimanjato.", [pay("Bodo", 1500)]),
    ("mg-028", "Lalao nindrana dimy arivo Ariary.", [debt("Lalao", 5000)]),
    ("mg-029", "Vary roapolo kapoaka no lafo androany maraina.", [sale(None, [("vary", 20)])]),
    ("mg-030", "Soa naka vary dimy kapoaka sy menaka iray litatra, nandoa folo arivo.",
     [sale("Soa", [("vary", 5), ("menaka", 1)], 10000)]),
    # ---------------------------------------------------------------- Malagasy + French (20)
    ("mx-001", "Soa a pris roa kapoaka siramamy, nandoa 1600 Ariary.", [sale("Soa", [("siramamy", 2, "kapoaka")], 1600)]),
    ("mx-002", "Rabe naka trois kapoaka vary, crédit daholo.", [sale("Rabe", [("vary", 3)], 0)]),
    ("mx-003", "Naivo nividy savon roa, payé.", [sale("Naivo", [("savony", 2)])]),
    ("mx-004", "Bodo a remboursé arivo.", [pay("Bodo", 1000)]),
    ("mx-005", "Tonga ny huile, dix litatra.", [stock("menaka", 10)]),
    ("mx-006", "Lalao naka pain efatra, nandoa mille deux cents.", [sale("Lalao", [("mofo", 4)], 1200)]),
    ("mx-007", "Rabe, riz deux kapoaka, mbola trosa.", [sale("Rabe", [("vary", 2)], 0)]),
    ("mx-008", "Soa a payé sa dette, dimy arivo.", [pay("Soa", 5000)]),
    ("mx-009", "Vente: mofo dimy, client de passage.", [sale(None, [("mofo", 5)])]),
    ("mx-010", "Hery nindrana trois mille.", [debt("Hery", 3000)]),
    ("mx-011", "Naivo naka sucre telo kapoaka sy riz roa kapoaka, avance arivo.",
     [sale("Naivo", [("siramamy", 3, "kapoaka"), ("vary", 2)], 1000)]),
    ("mx-012", "Perte: savony roa simba.", [stock("savony", -2)]),
    ("mx-013", "Bodo naka menaka un litre, payé cash.", [sale("Bodo", [("menaka", 1)])]),
    ("mx-014", "Rabe a acheté zavatra, tsy haiko intsony.", [ASK]),
    ("mx-015", "Réception: vary cent kapoaka.", [stock("vary", 100)]),
    ("mx-016", "Lalao nanefa deux mille cinq cents.", [pay("Lalao", 2500)]),
    ("mx-017", "Soa naka mofo vingt, nandoa dimy arivo.", [sale("Soa", [("mofo", 20)], 5000)]),
    ("mx-018", "Client tsy fantatra naka savon telo.", [sale(None, [("savony", 3)])]),
    ("mx-019", "Naivo, crédit arivo dimanjato.", [debt("Naivo", 1500)]),
    ("mx-020", "Hery naka riz, combien déjà?", [ASK]),
    # ---------------------------------------------------------------- French (10)
    ("fr-001", "Naivo a acheté quatre savons.", [sale("Naivo", [("savony", 4)])]),
    ("fr-002", "Rabe a pris trois kapoaka de riz et a payé deux mille ariary.", [sale("Rabe", [("vary", 3)], 2000)]),
    ("fr-003", "Soa a remboursé mille cinq cents ariary.", [pay("Soa", 1500)]),
    ("fr-004", "Livraison de sucre : quarante kapoaka.", [stock("siramamy", 40, "kapoaka")]),
    ("fr-005", "Bodo emprunte deux mille ariary.", [debt("Bodo", 2000)]),
    ("fr-006", "Vente au comptant : six pains.", [sale(None, [("mofo", 6)])]),
    ("fr-007", "Lalao a pris un litre d'huile, à crédit.", [sale("Lalao", [("menaka", 1)], 0)]),
    ("fr-008", "Trois pains abîmés, à retirer du stock.", [stock("mofo", -3)]),
    ("fr-009", "Hery a pris deux savons et cinq pains, il a donné trois mille.",
     [sale("Hery", [("savony", 2), ("mofo", 5)], 3000)]),
    ("fr-010", "Quelqu'un a pris quelque chose à crédit.", [ASK]),
]

LANG = {"mg": "mg", "mx": "mix", "fr": "fr"}


def build() -> list[dict]:
    out = []
    for cid, text, calls in NOTES:
        conn = db.connect(Path(tempfile.mkdtemp()) / "c.db")
        db.init(conn)
        db.seed_demo(conn)
        v = verifier.verify(conn, calls)
        if v["level"] == "clarify":
            expected = []
        else:
            expected = [{k: val for k, val in line.items()
                         if k not in ("unit", "unit_price", "reason", "base_qty", "base_unit", "base_price", "delta_spoken")}
                        for line in v["lines"]]
        out.append({"id": cid, "lang": LANG[cid[:2]], "reference": text, "expected": expected})
    return out


if __name__ == "__main__":
    cases = build()
    assert len(cases) == 60 and len({c["id"] for c in cases}) == 60
    path = ROOT / "eval" / "cases.jsonl"
    path.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), encoding="utf-8")
    n = {lg: sum(c["lang"] == lg for c in cases) for lg in ("mg", "mix", "fr")}
    print(f"{len(cases)} cases → {path}  {n}  clarify: {sum(not c['expected'] for c in cases)}")
