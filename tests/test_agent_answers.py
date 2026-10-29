"""The agent answers questions from the database, speaks numbers in Malagasy, and previews balances (mock mode)."""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("KAHIE_DB", str(Path(tempfile.mkdtemp()) / "test.db"))
os.environ["KAHIE_BACKEND"] = "mock"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pytest  # noqa: E402

from app import db  # noqa: E402
from app.agent import executor, pipeline  # noqa: E402
from app.numbers_mg import speakify, to_digits, to_words  # noqa: E402


def _fresh():
    conn = db.connect(Path(tempfile.mkdtemp()) / "t.db")
    db.init(conn)
    db.seed_demo(conn)
    return conn


def _say(conn, text):
    return pipeline.handle_voice(conn, b"", "audio/webm", hint_text=text)


def _write(conn, text):
    r = _say(conn, text)
    assert r["kind"] == "plan" and r["verdict"]["level"] != "clarify", r["verdict"]
    executor.execute(conn, r["transcript"], r["plan"], r["verdict"])
    return r


# ---------------------------------------------------------------- spoken numbers
@pytest.mark.parametrize("n, words", [
    (11, "iraika ambin'ny folo"), (32, "roa amby telopolo"), (1800, "arivo sy valonjato"),
    (2500, "roa arivo sy dimanjato"), (125000, "zato dimy amby roapolo arivo"), (1_000_000, "iray tapitrisa"),
])
def test_to_words(n, words):
    assert to_words(n) == words


def test_to_words_round_trip():
    for n in list(range(1, 3000)) + list(range(3000, 2_000_000, 991)):
        assert to_digits(to_words(n)) == str(n), n


def test_speakify_reads_amounts_in_malagasy():
    assert speakify("Rabe 1 400 Ar.") == "Rabe arivo sy efajato Ariary."


# ---------------------------------------------------------------- questions
def test_who_owes_is_answered_from_the_database():
    conn = _fresh()
    assert "Tsy misy" in _say(conn, "Iza avy no mbola tsy nandoa trosa?")["answer"]["text"]
    _write(conn, "Rabe naka telo kapoaka vary, nandoa roa arivo Ariary")      # debt 1000
    _write(conn, "Naivo naka mofo valo, nandoa arivo")                         # debt 1400
    pending_before = conn.execute("SELECT COUNT(*) FROM pending_plans").fetchone()[0]
    r = _say(conn, "Iza avy no mbola tsy nandoa trosa?")
    assert r["kind"] == "answer" and "pending_id" not in r
    assert [x["label"] for x in r["answer"]["rows"]] == ["Naivo", "Rabe"]     # biggest debt first
    assert "2 400 Ar" in r["answer"]["text"]                          # total
    assert "roa arivo sy efajato Ariary" in r["answer"]["speak"]
    assert conn.execute("SELECT COUNT(*) FROM pending_plans").fetchone()[0] == pending_before  # nothing to confirm


def test_customer_balance_sales_stock_low_stock():
    conn = _fresh()
    _write(conn, "Rabe naka telo kapoaka vary, nandoa roa arivo Ariary")
    assert _say(conn, "Firy ny trosan'i Rabe?")["answer"]["text"] == "1 000 Ar ny trosan'i Rabe."
    assert "3 000 Ar" in _say(conn, "Ohatrinona ny varotra androany?")["answer"]["text"]
    assert _say(conn, "Firy kapoaka ny vary sisa?")["answer"]["text"] == "Vary: 117 kapoaka sisa."
    assert "Tsara daholo" in _say(conn, "Inona no efa ho lany?")["answer"]["text"]
    _write(conn, "Simba ny menaka valo litatra")
    assert _say(conn, "Inona no efa ho lany?")["answer"]["text"] == "Efa ho lany: menaka 7 litatra."


def test_a_question_never_writes():
    conn = _fresh()
    before = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    _say(conn, "Iza no manana trosa efa ela?")
    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == before


def test_incomplete_sale_with_combien_is_not_a_question():
    r = _say(_fresh(), "Hery naka riz, combien déjà?")
    assert r["kind"] == "plan" and r["verdict"]["level"] == "clarify"


# ---------------------------------------------------------------- automatic calculation on the card
def test_preview_shows_balances_before_and_after():
    conn = _fresh()
    _write(conn, "Rabe naka telo kapoaka vary, nandoa roa arivo Ariary")
    r = _say(conn, "Rabe naka siramamy efatra kapoaka, nandoa roa arivo")
    pv = r["verdict"]["preview"]
    assert pv["customers"] == [{"name": "Rabe", "before": 1000, "after": 2200}]
    assert pv["products"] == [{"name": "siramamy", "unit": "kapoaka", "before": 60, "after": 56}]
    assert pv["sales_today"] == {"before": 3000, "after": 6200}
    assert r["verdict"]["summary"].endswith("Marina ve?") and "2 200 Ar" in r["verdict"]["summary"]
    assert "roa arivo sy roanjato" in r["verdict"]["speak"]


def test_overpayment_asks_for_a_reinforced_confirmation():
    conn = _fresh()
    _write(conn, "Rabe naka telo kapoaka vary, nandoa roa arivo Ariary")      # owes 1000
    v = _say(conn, "Rabe nanefa trosa telo arivo")["verdict"]
    assert v["level"] == "warn" and any("be loatra" in i["msg"] for i in v["issues"])


def test_clarification_question_is_spoken():
    v = _say(_fresh(), "Bodo naka zavatra")["verdict"]
    assert v["level"] == "clarify" and v["speak"]
