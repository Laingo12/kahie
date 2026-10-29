"""End-to-end tests in mock mode (no API key needed)."""
import os
import sys
import tempfile
from pathlib import Path

os.environ["KAHIE_DB"] = str(Path(tempfile.mkdtemp()) / "test.db")
os.environ["KAHIE_BACKEND"] = "mock"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

from app import audit  # noqa: E402
from app.agent import reconciler  # noqa: E402
from app.main import app, conn  # noqa: E402

client = TestClient(app)


def test_sale_with_partial_payment_creates_debt_and_needs_confirmation():
    r = client.post("/api/voice", data={"text": "Rabe naka 3 kapoaka vary, nandoa 2000 Ar"}).json()
    kinds = [l["kind"] for l in r["verdict"]["lines"]]
    assert kinds == ["sale", "debt"]
    assert r["verdict"]["lines"][1]["amount"] == 1000
    # nothing written before confirmation
    assert conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0] == 0
    res = client.post("/api/confirm", json={"pending_id": r["pending_id"]}).json()
    assert conn.execute("SELECT COUNT(*) FROM sales WHERE voided=0").fetchone()[0] == 1
    rep = client.get("/api/report").json()
    assert any(d["name"] == "Rabe" and d["owed"] == 1000 for d in rep["debts"])
    # undo restores stock and voids the debt
    stock_before = conn.execute("SELECT stock FROM products WHERE name='vary'").fetchone()[0]
    client.post(f"/api/undo/{res['audit_id']}")
    assert conn.execute("SELECT stock FROM products WHERE name='vary'").fetchone()[0] == stock_before + 3
    assert client.get("/api/verify").json()["ok"]


def test_unknown_product_asks_instead_of_writing():
    r = client.post("/api/voice", data={"text": "Bodo naka zavatra"}).json()
    assert r["verdict"]["level"] == "clarify"
    assert client.post("/api/confirm", json={"pending_id": r["pending_id"]}).status_code == 409


def test_ears_disagreeing_on_amount_trigger_question():
    rec = reconciler.reconcile("nandoa 2000 Ar", "nandoa 3000 Ar", ["vary"])
    assert not rec["agree"] and "2000" in rec["question"] and "3000" in rec["question"]


def test_tampering_breaks_the_chain():
    r = client.post("/api/voice", data={"text": "Soa naka 2 kapoaka siramamy"}).json()
    client.post("/api/confirm", json={"pending_id": r["pending_id"]})
    assert audit.verify(conn)["ok"]
    conn.execute("UPDATE audit_log SET transcript='changed' WHERE id=1")
    assert not audit.verify(conn)["ok"]


def test_answering_the_question_on_the_card():
    r = client.post("/api/voice", data={"text": "Bodo naka zavatra"}).json()
    assert r["verdict"]["level"] == "clarify"
    r2 = client.post("/api/clarify", json={"pending_id": r["pending_id"], "text": "Bodo naka roa savony"}).json()
    assert r2["verdict"]["level"] in ("ok", "warn") and r2["verdict"]["lines"][0]["qty"] == 2
    assert client.post("/api/confirm", json={"pending_id": r2["pending_id"]}).status_code == 200
    assert client.post("/api/clarify", json={"pending_id": "nope", "text": "x"}).status_code == 404


def test_question_is_answered_and_writes_nothing():
    before = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    r = client.post("/api/voice", data={"text": "Iza avy no mbola tsy nandoa trosa?"}).json()
    assert r["kind"] == "answer" and r["answer"]["text"] and r["answer"]["speak"]
    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == before


def test_card_carries_the_automatic_calculation():
    r = client.post("/api/voice", data={"text": "Bodo naka roa kapoaka vary"}).json()
    pv = r["verdict"]["preview"]
    assert pv["products"][0]["after"] == pv["products"][0]["before"] - 2 and r["verdict"]["summary"]
