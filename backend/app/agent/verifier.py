"""Rule-based verifier: checks every planned call against the database before anything is written.

Verdict levels: ok | warn (reinforced confirmation) | clarify (no write, ask a question)
"""
from .. import config, units
from .. import phrases


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
                issues.append({"level": "warn", "msg": f"Mpanjifa vaovao: {a['customer']}"})
            sale_total = 0
            for it in a.get("items", []):
                p = _product(conn, it.get("product"))
                if not p:
                    return {"level": "clarify", "question": phrases.say("clarify_unknown_product", product=it.get("product")),
                            "issues": [], "lines": []}
                qty = float(it.get("qty") or 0)
                if qty <= 0:
                    return {"level": "clarify", "question": phrases.say("clarify_quantity", product=p["name"]), "issues": [], "lines": []}
                # Units: the note may say kg, g, kapoaka…; stock and price are kept in the product's base unit.
                unit = units.normalize(it.get("unit")) or p["unit"]
                f = units.factor(p["name"], p["unit"], unit)
                if f is None:
                    ok = ", ".join(units.accepted(p["name"], p["unit"]))
                    return {"level": "clarify", "issues": [], "lines": [],
                            "question": f"Tsy mety ny « {it.get('unit')} » ho an'ny {p['name']}. Amin'ny inona: {ok}?"}
                base_qty = qty * f
                base_price = (it["unit_price"] / f) if it.get("unit_price") else p["unit_price"]
                if p["unit_price"] and abs(base_price - p["unit_price"]) / p["unit_price"] > config.PRICE_DEVIATION:
                    issues.append({"level": "warn", "msg": f"Vidiny hafahafa ho an'ny {p['name']}: {base_price * f:g} Ar/{unit} "
                                                           f"(matetika {p['unit_price'] * f:g} Ar/{unit})"})
                if base_qty > p["stock"]:
                    issues.append({"level": "warn", "msg": f"Tsy ampy ny tahiry: {p['name']} {units.fmt_qty(p['stock'], p['unit'])} sisa"})
                line_total = int(round(base_qty * base_price))
                sale_total += line_total
                lines.append({"kind": "sale", "customer": a.get("customer"), "product": p["name"], "unit": unit,
                              "qty": qty, "unit_price": round(base_price * f, 2), "total": line_total,
                              "base_qty": base_qty, "base_unit": p["unit"], "base_price": base_price})
            paid = a.get("paid")
            if paid is not None and paid < sale_total:
                if not a.get("customer"):
                    return {"level": "clarify", "question": phrases.say("clarify_customer"), "issues": [], "lines": []}
                lines.append({"kind": "debt", "customer": a["customer"], "amount": sale_total - paid})
            if paid is not None and paid > sale_total:
                issues.append({"level": "warn", "msg": f"Mihoatra ny vidiny ({sale_total} Ar) ny vola naloa ({paid} Ar)"})
            total_due += sale_total

        elif tool == "update_stock":
            p = _product(conn, a.get("product"))
            if not p:
                return {"level": "clarify", "question": phrases.say("clarify_unknown_product", product=a.get("product")), "issues": [], "lines": []}
            unit = units.normalize(a.get("unit")) or p["unit"]
            f = units.factor(p["name"], p["unit"], unit)
            if f is None:
                return {"level": "clarify", "issues": [], "lines": [],
                        "question": f"Tsy mety ny « {a.get('unit')} » ho an'ny {p['name']}. Amin'ny inona: "
                                    f"{', '.join(units.accepted(p['name'], p['unit']))}?"}
            delta = float(a.get("delta") or 0)
            if delta == 0:
                return {"level": "clarify", "question": phrases.say("clarify_quantity", product=p["name"]), "issues": [], "lines": []}
            if a.get("reason") == "loss" and delta > 0:
                delta = -delta  # "simba ny mofo 5": a loss always removes stock, whatever sign the model chose
            lines.append({"kind": "stock", "product": p["name"], "delta": delta * f, "reason": a.get("reason"),
                          "delta_spoken": delta, "unit": unit, "base_unit": p["unit"]})

        elif tool in ("record_debt", "record_payment"):
            if not a.get("customer") or not a.get("amount"):
                return {"level": "clarify", "question": phrases.say("clarify_who_amount"), "issues": [], "lines": []}
            if not _customer(conn, a["customer"]):
                issues.append({"level": "warn", "msg": f"Mpanjifa vaovao: {a['customer']}"})
            kind = "debt" if tool == "record_debt" else "payment"
            lines.append({"kind": kind, "customer": a["customer"], "amount": int(a["amount"])})

    level = "warn" if any(i["level"] == "warn" for i in issues) else "ok"
    return {"level": level, "question": None, "issues": issues, "lines": lines, "total": total_due}
