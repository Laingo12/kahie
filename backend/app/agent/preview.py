"""Automatic calculation shown on the confirmation card, BEFORE anything is written.

For the lines the verifier accepted, compute what the ledger will look like after the ✓:
  - each customer's debt balance: before → after
  - each product's stock:         before → after
  - today's sales total:          before → after
and a short sentence the app can read aloud. Read-only.
"""
from datetime import date

from ..numbers_mg import speakify
from .answers import ar


def _qty(x) -> str:
    return f"{x:g}"


def compute(conn, lines: list[dict]) -> dict:
    customers, products = {}, {}
    sales_add = 0
    for ln in lines:
        k = ln["kind"]
        if k == "sale":
            p = products.setdefault(ln["product"], {"delta": 0})
            p["delta"] -= ln["qty"]
            sales_add += ln["total"]
        elif k == "stock":
            p = products.setdefault(ln["product"], {"delta": 0})
            p["delta"] += ln["delta"]
        elif k in ("debt", "payment"):
            c = customers.setdefault(ln["customer"], {"delta": 0})
            c["delta"] += ln["amount"] if k == "debt" else -ln["amount"]

    out_c = []
    for name, c in customers.items():
        before = conn.execute(
            "SELECT COALESCE(SUM(d.amount), 0) FROM debts d JOIN customers cu ON cu.id = d.customer_id "
            "WHERE lower(cu.name) = lower(?) AND d.voided = 0", (name,)).fetchone()[0]
        out_c.append({"name": name, "before": before, "after": before + c["delta"]})

    out_p = []
    for name, p in products.items():
        r = conn.execute("SELECT name, unit, stock FROM products WHERE lower(name) = lower(?)", (name,)).fetchone()
        if r:
            out_p.append({"name": r["name"], "unit": r["unit"], "before": r["stock"], "after": r["stock"] + p["delta"]})

    today = date.today().isoformat()
    s_before = conn.execute("SELECT COALESCE(SUM(total), 0) FROM sales WHERE voided = 0 AND date(created_at) = ?",
                            (today,)).fetchone()[0]
    sales = {"before": s_before, "after": s_before + sales_add} if sales_add else None
    return {"customers": out_c, "products": out_p, "sales_today": sales}


def summary(lines: list[dict], preview: dict) -> str:
    """One sentence for the card, read aloud: what will be written and the new balances."""
    parts = []
    for ln in lines:
        if ln["kind"] == "sale":
            who = f"{ln['customer']}: " if ln.get("customer") else ""
            parts.append(f"{who}{ln['product']} {_qty(ln['qty'])} {ln['unit']}, {ar(ln['total'])}")
        elif ln["kind"] == "debt":
            parts.append(f"trosa vaovao {ar(ln['amount'])}")
        elif ln["kind"] == "payment":
            parts.append(f"{ln['customer']} nandoa {ar(ln['amount'])}")
        elif ln["kind"] == "stock":
            parts.append(f"{ln['product']} {'miditra' if ln['delta'] > 0 else 'mivoaka'} {_qty(abs(ln['delta']))}")
    for c in preview["customers"]:
        parts.append(f"ny trosan'i {c['name']} lasa {ar(max(c['after'], 0))}" if c["after"] > 0
                     else f"tsy manana trosa intsony i {c['name']}")
    for p in preview["products"]:
        if p["after"] < 10:
            parts.append(f"{p['name']} {_qty(p['after'])} {p['unit']} sisa")
    return ". ".join(x[0].upper() + x[1:] for x in parts) + ". Marina ve?" if parts else ""


def attach(conn, verdict: dict) -> dict:
    """Add `preview`, `summary` and `speak` to a verdict that has lines to write."""
    if verdict.get("level") == "clarify" or not verdict.get("lines"):
        if verdict.get("question"):
            verdict["speak"] = speakify(verdict["question"])
        return verdict
    verdict["preview"] = compute(conn, verdict["lines"])
    # A repayment larger than the debt is probably a mishearing: ask for a reinforced confirmation.
    for c in verdict["preview"]["customers"]:
        if c["after"] < 0 <= c["before"]:
            verdict.setdefault("issues", []).append(
                {"level": "warn", "msg": f"Mihoatra ny trosan'i {c['name']} ({ar(c['before'])}) ny vola naloa: {ar(-c['after'])} be loatra"})
            verdict["level"] = "warn"
    verdict["summary"] = summary(verdict["lines"], verdict["preview"])
    verdict["speak"] = speakify(verdict["summary"])
    return verdict
