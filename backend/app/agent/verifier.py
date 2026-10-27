"""Rule-based verifier: checks every planned call against the database before anything is written.

Verdict levels: ok | warn (reinforced confirmation) | clarify (no write, ask a question)
"""
from .. import config


def _product(conn, name):
    return conn.execute("SELECT * FROM products WHERE lower(name)=lower(?)", (name or "",)).fetchone()


def _customer(conn, name):
    return conn.execute("SELECT * FROM customers WHERE lower(name)=lower(?)", (name or "",)).fetchone()


def verify(conn, calls: list[dict], reconciliation: dict | None = None) -> dict:
    issues, lines, total_due = [], [], 0

    if reconciliation and not reconciliation["agree"]:
        return {"level": "clarify", "question": reconciliation["question"], "issues": reconciliation["conflicts"], "lines": []}

    for call in calls:
        tool, a = call["tool"], call.get("args", {})
        if tool == "ask_clarification":
            return {"level": "clarify", "question": a.get("question"), "issues": [], "lines": []}

        if tool == "record_sale":
            if a.get("customer") and not _customer(conn, a["customer"]):
                issues.append({"level": "warn", "msg": f"Client nouveau : {a['customer']}"})
            sale_total = 0
            for it in a.get("items", []):
                p = _product(conn, it.get("product"))
                if not p:
                    return {"level": "clarify", "question": f"Tsy fantatra ny entana « {it.get('product')} ». Inona marina ?",
                            "issues": [], "lines": []}
                qty = float(it.get("qty") or 0)
                if qty <= 0:
                    return {"level": "clarify", "question": f"Firy ny {p['name']} ?", "issues": [], "lines": []}
                price = it.get("unit_price") or p["unit_price"]
                if p["unit_price"] and abs(price - p["unit_price"]) / p["unit_price"] > config.PRICE_DEVIATION:
                    issues.append({"level": "warn", "msg": f"Prix inhabituel pour {p['name']} : {price} Ar (habituel {p['unit_price']} Ar)"})
                if qty > p["stock"]:
                    issues.append({"level": "warn", "msg": f"Stock insuffisant : {p['name']} {p['stock']:g} restant(s)"})
                line_total = int(round(qty * price))
                sale_total += line_total
                lines.append({"kind": "sale", "customer": a.get("customer"), "product": p["name"], "unit": p["unit"],
                              "qty": qty, "unit_price": price, "total": line_total})
            paid = a.get("paid")
            if paid is not None and paid < sale_total:
                if not a.get("customer"):
                    return {"level": "clarify", "question": "Iza no nindrana ? (Qui doit le reste ?)", "issues": [], "lines": []}
                lines.append({"kind": "debt", "customer": a["customer"], "amount": sale_total - paid})
            if paid is not None and paid > sale_total:
                issues.append({"level": "warn", "msg": f"Payé {paid} Ar > total {sale_total} Ar"})
            total_due += sale_total

        elif tool == "update_stock":
            p = _product(conn, a.get("product"))
            if not p:
                return {"level": "clarify", "question": f"Inona ilay entana « {a.get('product')} » ?", "issues": [], "lines": []}
            lines.append({"kind": "stock", "product": p["name"], "delta": a["delta"], "reason": a.get("reason")})

        elif tool in ("record_debt", "record_payment"):
            if not a.get("customer") or not a.get("amount"):
                return {"level": "clarify", "question": "Iza ary ohatrinona ?", "issues": [], "lines": []}
            if not _customer(conn, a["customer"]):
                issues.append({"level": "warn", "msg": f"Client nouveau : {a['customer']}"})
            kind = "debt" if tool == "record_debt" else "payment"
            lines.append({"kind": kind, "customer": a["customer"], "amount": int(a["amount"])})

    level = "warn" if any(i["level"] == "warn" for i in issues) else "ok"
    return {"level": level, "question": None, "issues": issues, "lines": lines, "total": total_due}
