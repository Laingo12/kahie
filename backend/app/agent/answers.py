"""Answers to the shopkeeper's questions — read-only, computed from the database.

The model only chooses WHICH question was asked (tool ask_ledger: topic + customer/product/period).
Every name and number in the answer comes from SQL here, never from the model, so the agent
cannot invent a debt. Nothing is written.
"""
from datetime import date, timedelta

from .. import units
from ..numbers_mg import speakify

TOPICS = ("debts", "customer_balance", "aged_debts", "sales", "stock", "low_stock")
LOW_STOCK = 10
AGED_DAYS = 7


def ar(n) -> str:
    """1800 → "1 800 Ar" (thin grouping like the UI)."""
    return f"{int(round(n)):,}".replace(",", "\u202f") + "\u00a0Ar"   # no line break inside an amount


def _qty(x) -> str:
    return f"{x:g}"


def _since(period: str | None) -> tuple[str, str]:
    today = date.today()
    if period == "week":
        return (today - timedelta(days=6)).isoformat(), "tamin'ity herinandro ity"
    if period == "month":
        return today.replace(day=1).isoformat(), "tamin'ity volana ity"
    return today.isoformat(), "androany"


def _balances(conn):
    return [dict(r) for r in conn.execute(
        "SELECT c.name, SUM(d.amount) AS owed FROM debts d JOIN customers c ON c.id = d.customer_id "
        "WHERE d.voided = 0 GROUP BY c.id HAVING owed > 0 ORDER BY owed DESC, c.name")]


def _customer(conn, name):
    return conn.execute("SELECT * FROM customers WHERE lower(name) = lower(?)", (name or "",)).fetchone()


def _product(conn, name):
    return conn.execute("SELECT * FROM products WHERE lower(name) = lower(?)", (name or "",)).fetchone()


def answer_one(conn, args: dict) -> dict:
    """One ask_ledger call → {"topic", "text", "rows"}; rows feed a small table in the UI."""
    topic = args.get("topic")

    if topic == "debts":
        rows = _balances(conn)
        if not rows:
            return {"topic": topic, "text": "Tsy misy olona manana trosa aminao.", "rows": []}
        total = sum(r["owed"] for r in rows)
        names = ", ".join(f"{r['name']} {ar(r['owed'])}" for r in rows)
        who = "Olona iray" if len(rows) == 1 else f"Olona {len(rows)}"
        return {"topic": topic, "rows": [{"label": r["name"], "value": ar(r["owed"])} for r in rows],
                "text": f"{who} no mbola manana trosa, {ar(total)} fitambarany: {names}."}

    if topic == "customer_balance":
        c = _customer(conn, args.get("customer"))
        if not c:
            return {"topic": topic, "rows": [], "text": f"Tsy fantatro ny mpanjifa « {args.get('customer') or '?'} »."}
        owed = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM debts WHERE customer_id = ? AND voided = 0",
                            (c["id"],)).fetchone()[0]
        if owed > 0:
            text = f"{ar(owed)} ny trosan'i {c['name']}."
        elif owed < 0:
            text = f"Tsy manana trosa i {c['name']}. Efa nandoa mialoha {ar(-owed)} aza izy."
        else:
            text = f"Tsy manana trosa i {c['name']}."
        return {"topic": topic, "rows": [{"label": c["name"], "value": ar(max(owed, 0))}], "text": text}

    if topic == "aged_debts":
        rows = []
        for b in _balances(conn):
            first = conn.execute(
                "SELECT MIN(d.created_at) FROM debts d JOIN customers c ON c.id = d.customer_id "
                "WHERE lower(c.name) = lower(?) AND d.voided = 0 AND d.amount > 0", (b["name"],)).fetchone()[0]
            days = (date.today() - date.fromisoformat(first[:10])).days if first else 0
            if days >= AGED_DAYS:
                rows.append({"label": b["name"], "value": f"{ar(b['owed'])} · {days} andro", "days": days, "owed": b["owed"]})
        if not rows:
            return {"topic": topic, "rows": [], "text": f"Tsy misy trosa mihoatra ny {AGED_DAYS} andro."}
        rows.sort(key=lambda r: -r["days"])
        names = ", ".join(f"{r['label']} {ar(r['owed'])} ({r['days']} andro)" for r in rows)
        return {"topic": topic, "rows": rows, "text": f"Trosa efa ela: {names}."}

    if topic == "sales":
        since, label = _since(args.get("period"))
        s = conn.execute("SELECT COALESCE(SUM(total), 0) t, COUNT(*) n FROM sales WHERE voided = 0 AND date(created_at) >= ?",
                         (since,)).fetchone()
        top = [dict(r) for r in conn.execute(
            "SELECT p.name, p.unit, SUM(s.qty) q, SUM(s.total) t FROM sales s JOIN products p ON p.id = s.product_id "
            "WHERE s.voided = 0 AND date(s.created_at) >= ? GROUP BY p.id ORDER BY t DESC LIMIT 3", (since,))]
        if not s["n"]:
            return {"topic": topic, "rows": [], "text": f"Mbola tsy nisy varotra {label}."}
        best = ", ".join(f"{r['name']} {ar(r['t'])}" for r in top)
        return {"topic": topic, "rows": [{"label": r["name"], "value": f"{units.fmt_qty(r['q'], r['unit'])} · {ar(r['t'])}"} for r in top],
                "text": f"Varotra {label}: {ar(s['t'])}, varotra {s['n']}. Ny nahazoana vola be indrindra: {best}."}

    if topic == "stock":
        p = _product(conn, args.get("product"))
        if not p:
            return {"topic": topic, "rows": [], "text": f"Tsy fantatro ny entana « {args.get('product') or '?'} »."}
        warn = " Efa ho lany!" if p["stock"] < LOW_STOCK else ""
        return {"topic": topic, "rows": [{"label": p["name"], "value": units.fmt_qty(p["stock"], p["unit"])}],
                "text": f"{units.fmt_qty(p['stock'], p['unit'])} {p['name']} sisa.{warn}"}

    if topic == "low_stock":
        rows = [dict(r) for r in conn.execute("SELECT name, unit, stock FROM products WHERE stock < ? ORDER BY stock", (LOW_STOCK,))]
        if not rows:
            return {"topic": topic, "rows": [], "text": "Tsara daholo ny tahiry, tsy misy efa ho lany."}
        names = ", ".join(f"{r['name']} {units.fmt_qty(r['stock'], r['unit'])}" for r in rows)
        return {"topic": topic, "rows": [{"label": r["name"], "value": units.fmt_qty(r["stock"], r["unit"])} for r in rows],
                "text": f"Efa ho lany: {names}."}

    return {"topic": "unknown", "rows": [],
            "text": "Tsy azoko tsara ny fanontanianao. Azonao atao ny manontany hoe: iza no manana trosa, ohatrinona ny varotra, firy ny entana sisa."}


def answer(conn, calls: list[dict]) -> dict:
    """All ask_ledger calls of one note → one answer (text + spoken text + table rows)."""
    parts = [answer_one(conn, c.get("args", {})) for c in calls]
    text = " ".join(p["text"] for p in parts)
    return {"text": text, "speak": speakify(text), "rows": [r for p in parts for r in p["rows"]],
            "topics": [p["topic"] for p in parts]}
