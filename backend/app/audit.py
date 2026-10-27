"""Append-only audit log as a hash chain: any later edit of a row breaks every hash after it."""
import hashlib
import json
from datetime import datetime, timezone

GENESIS = "0" * 64
FIELDS = ("created_at", "kind", "transcript", "plan_json", "verdict_json", "result_json", "undoes_id", "prev_hash")


def _digest(row: dict) -> str:
    payload = json.dumps({k: row.get(k) for k in FIELDS}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def head(conn) -> str:
    r = conn.execute("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    return r["hash"] if r else GENESIS


def append(conn, kind: str, transcript=None, plan=None, verdict=None, result=None, undoes_id=None) -> int:
    row = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kind": kind,
        "transcript": transcript,
        "plan_json": json.dumps(plan, ensure_ascii=False) if plan is not None else None,
        "verdict_json": json.dumps(verdict, ensure_ascii=False) if verdict is not None else None,
        "result_json": json.dumps(result, ensure_ascii=False) if result is not None else None,
        "undoes_id": undoes_id,
        "prev_hash": head(conn),
    }
    row["hash"] = _digest(row)
    cur = conn.execute(
        "INSERT INTO audit_log(created_at, kind, transcript, plan_json, verdict_json, result_json, undoes_id, prev_hash, hash)"
        " VALUES (:created_at,:kind,:transcript,:plan_json,:verdict_json,:result_json,:undoes_id,:prev_hash,:hash)",
        row,
    )
    return cur.lastrowid


def set_result(conn, audit_id: int, result: dict) -> None:
    """Result is known only after execution; written before the hash is computed by re-hashing this last row."""
    row = dict(conn.execute("SELECT * FROM audit_log WHERE id=?", (audit_id,)).fetchone())
    if row["hash"] != head(conn):
        raise RuntimeError("only the last audit row can be completed")
    row["result_json"] = json.dumps(result, ensure_ascii=False)
    row["hash"] = _digest(row)
    conn.execute("UPDATE audit_log SET result_json=?, hash=? WHERE id=?", (row["result_json"], row["hash"], audit_id))


def verify(conn) -> dict:
    prev = GENESIS
    rows = conn.execute("SELECT * FROM audit_log ORDER BY id").fetchall()
    for r in rows:
        r = dict(r)
        if r["prev_hash"] != prev or _digest(r) != r["hash"]:
            return {"ok": False, "broken_at": r["id"], "rows": len(rows)}
        prev = r["hash"]
    return {"ok": True, "head": prev, "rows": len(rows)}
