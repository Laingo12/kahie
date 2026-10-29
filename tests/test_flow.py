import pytest
from fastapi.testclient import TestClient
from pathlib import Path
import tempfile

from backend.app.main import app
from backend.app import db

client = TestClient(app)


# ---------------------------------------------------------------- helpers
def _fresh():
    """Creates a fresh isolated database for a test."""
    conn = db.connect(Path(tempfile.mkdtemp()) / "t.db")
    db.init(conn)
    db.seed_demo(conn)
    return conn


def _pending(text: str):
    """Sends a note and returns the pending plan (not confirmed)."""
    r = client.post("/api/voice", data={"text": text})
    assert r.status_code == 200
    return r.json()


# ---------------------------------------------------------------- CORE TESTS (PASSING)
def test_sale_with_partial_payment_creates_debt_and_needs_confirmation():
    r = client.post("/api/voice", data={"text": "Rabe naka 3 kapoaka vary, nandoa 2000 Ar"}).json()
    assert r["verdict"]["level"] == "ok"
    assert r["kind"] == "plan"
    assert len(r["verdict"]["lines"]) == 2
    assert r["verdict"]["lines"][0]["kind"] == "sale"
    assert r["verdict"]["lines"][0]["total"] == 3000
    assert r["verdict"]["lines"][1]["kind"] == "debt"
    assert r["verdict"]["lines"][1]["amount"] == 1000


def test_unknown_product_asks_instead_of_writing():
    r = client.post("/api/voice", data={"text": "Bodo naka zavatra"}).json()
    assert r["verdict"]["level"] == "clarify"
    assert r["kind"] == "plan"
    assert "Inona" in r["verdict"]["question"]


def test_tampering_breaks_the_chain():
    r = client.post("/api/voice", data={"text": "Soa naka 2 kapoaka siramamy"}).json()
    assert r["verdict"]["level"] == "ok"

    # Confirm the pending plan
    r2 = client.post("/api/confirm", json={"pending_id": r["pending_id"]})
    assert r2.status_code == 200

    # Tamper with the DB directly
    conn = db.connect()  # Connects to the default DB used by the app
    conn.execute("UPDATE audit_log SET transcript='hacked' WHERE id=1")
    conn.commit()

    # Verify the chain is broken
    v = client.get("/api/verify").json()
    assert v["ok"] is False


def test_answering_the_question_on_the_card():
    r = client.post("/api/voice", data={"text": "Bodo naka zavatra"}).json()
    assert r["verdict"]["level"] == "clarify"

    # Answer the question via /api/clarify
    r2 = client.post("/api/clarify", json={
        "pending_id": r["pending_id"],
        "text": "siramamy roa kapoaka"
    }).json()

    assert r2["verdict"]["level"] == "ok"
    assert r2["verdict"]["lines"][0]["product"] == "siramamy"
    assert r2["verdict"]["lines"][0]["qty"] == 2


def test_card_carries_the_automatic_calculation():
    r = client.post("/api/voice", data={"text": "Bodo naka roa kapoaka vary"}).json()
    assert r["verdict"]["level"] == "ok"
    assert r["verdict"]["lines"][0]["total"] == 2000
    assert r["verdict"]["total"] == 2000


# ---------------------------------------------------------------- POST-HACKATHON TESTS (SKIPPED)
# These tests require the `/api/reply` endpoint and the `answer` mode in `/api/voice`
# which are planned for post-hackathon development.

@pytest.mark.skip(reason="post-hackathon: Q&A 'answer' mode not wired yet")
def test_question_is_answered_and_writes_nothing():
    before = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    r = client.post("/api/voice", data={"text": "Iza avy no mbola tsy nandoa trosa?"}).json()
    assert r["kind"] == "answer" and r["answer"]["text"] and r["answer"]["speak"]
    after = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    assert before == after


@pytest.mark.skip(reason="post-hackathon: /api/reply not wired yet")
def test_spoken_yes_writes_and_reads_back_the_numbers():
    p = _pending("Soa naka roa kapoaka vary, nandoa arivo")
    r = client.post("/api/reply", data={"pending_id": p["pending_id"], "text": "Eny"}).json()
    assert r["action"] == "confirmed" and r["audit_id"] and "arivo" in r["speak"]


@pytest.mark.skip(reason="post-hackathon: /api/reply not wired yet")
def test_spoken_no_cancels_and_writes_nothing():
    before = client.get("/api/verify").json()["rows"]
    p = _pending("Soa naka roa kapoaka vary")
    r = client.post("/api/reply", data={"pending_id": p["pending_id"], "text": "Tsia"}).json()
    assert r["action"] == "cancelled"
    after = client.get("/api/verify").json()["rows"]
    assert before == after


@pytest.mark.skip(reason="post-hackathon: /api/reply not wired yet")
def test_echo_of_the_card_is_not_a_yes():
    p = _pending("Soa naka roa kapoaka vary")
    r = client.post("/api/reply",
                    data={"pending_id": p["pending_id"], "text": "Soa naka vary 2 kapoaka. Marina ve?"}).json()
    assert r["action"] == "repeat" and r["speak"]


@pytest.mark.skip(reason="post-hackathon: /api/reply not wired yet")
def test_spoken_answer_to_a_question_replans():
    p = _pending("Bodo naka zavatra")
    r = client.post("/api/reply", data={"pending_id": p["pending_id"], "text": "siramamy roa kilao"}).json()
    assert r["action"] == "plan" and r["result"]["verdict"]["lines"][0]["product"] == "siramamy"