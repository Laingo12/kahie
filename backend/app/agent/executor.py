"""Executor: applies a confirmed verdict in one transaction, writes the audit row, supports undo."""
import json

from .. import audit
from ..db import transaction


def _customer_id(conn, name):
    if not name:
        return None
    r = conn.execute("SELECT id FROM customers WHERE lower(name)=lower(?)", (name,)).fetchone()
    if r:
        return r["id"]
    return conn.execute("INSERT INTO customers(name) VALUES (?)", (name,)).lastrowid


def _product_id(conn, name):
    return conn.execute("SELECT id FROM products WHERE lower(name)=lower(?)", (name,)).fetchone()["id"]


def execute(conn, transcript: str, plan: list, verdict: dict, kind: str = "execute") -> dict:
    with transaction(conn):
        aid = audit.append(conn, kind, transcript=transcript, plan=plan, verdict=verdict)
        written = []
        for ln in verdict["lines"]:
            if ln["kind"] == "sale":
                pid, cid = _product_id(conn, ln["product"]), _customer_id(conn, ln["customer"])
                sid = conn.execute(
                    "INSERT INTO sales(customer_id, product_id, qty, unit_price, total, audit_id) VALUES (?,?,?,?,?,?)",
                    (cid, pid, ln["qty"], ln["unit_price"], ln["total"], aid)).lastrowid
                conn.execute("UPDATE products SET stock = stock - ? WHERE id=?", (ln["qty"], pid))
                conn.execute("INSERT INTO stock_moves(product_id, delta, reason, audit_id) VALUES (?,?,?,?)",
                             (pid, -ln["qty"], "sale", aid))
                written.append({"table": "sales", "id": sid})
            elif ln["kind"] in ("debt", "payment"):
                amount = ln["amount"] if ln["kind"] == "debt" else -ln["amount"]
                did = conn.execute("INSERT INTO debts(customer_id, amount, reason, audit_id) VALUES (?,?,?,?)",
                                   (_customer_id(conn, ln["customer"]), amount, ln["kind"], aid)).lastrowid
                written.append({"table": "debts", "id": did})
            elif ln["kind"] == "stock":
                pid = _product_id(conn, ln["product"])
                conn.execute("UPDATE products SET stock = stock + ? WHERE id=?", (ln["delta"], pid))
                conn.execute("INSERT INTO stock_moves(product_id, delta, reason, audit_id) VALUES (?,?,?,?)",
                             (pid, ln["delta"], ln["reason"], aid))
        audit.set_result(conn, aid, {"written": written})
    return {"audit_id": aid, "written": written}


def undo(conn, audit_id: int) -> dict:
    row = conn.execute("SELECT * FROM audit_log WHERE id=? AND kind IN ('execute','import')", (audit_id,)).fetchone()
    if not row:
        raise ValueError("audit row not found")
    if conn.execute("SELECT 1 FROM audit_log WHERE undoes_id=?", (audit_id,)).fetchone():
        raise ValueError("already undone")
    with transaction(conn):
        uid = audit.append(conn, "undo", transcript=f"undo #{audit_id}", undoes_id=audit_id)
        for s in conn.execute("SELECT * FROM sales WHERE audit_id=? AND voided=0", (audit_id,)).fetchall():
            conn.execute("UPDATE sales SET voided=1 WHERE id=?", (s["id"],))
            conn.execute("UPDATE products SET stock = stock + ? WHERE id=?", (s["qty"], s["product_id"]))
            conn.execute("INSERT INTO stock_moves(product_id, delta, reason, audit_id) VALUES (?,?,?,?)",
                         (s["product_id"], s["qty"], "undo", uid))
        conn.execute("UPDATE debts SET voided=1 WHERE audit_id=?", (audit_id,))
        for m in conn.execute("SELECT * FROM stock_moves WHERE audit_id=? AND reason IN ('restock','loss')", (audit_id,)).fetchall():
            conn.execute("UPDATE products SET stock = stock - ? WHERE id=?", (m["delta"], m["product_id"]))
            conn.execute("INSERT INTO stock_moves(product_id, delta, reason, audit_id) VALUES (?,?,?,?)",
                         (m["product_id"], -m["delta"], "undo", uid))
        audit.set_result(conn, uid, {"undone": audit_id})
    return {"audit_id": uid, "undone": audit_id}
