"""Hands-free voice commands: detected before planning, read-only, undo only after a clear "eny"."""
import tempfile
from pathlib import Path

import pytest

from app import db
from app.agent import commands, executor, pipeline


def _fresh():
    conn = db.connect(Path(tempfile.mkdtemp()) / "t.db"); db.init(conn); db.seed_demo(conn)
    return conn


def _say(conn, text):
    return pipeline.handle_voice(conn, b"", "audio/webm", hint_text=text)


@pytest.mark.parametrize("text, cmd", [
    ("Asehoy ny tabilao", "dash"), ("tabilao", "dash"), ("Sokafy ny boky", "dash"),
    ("Tatitra", "statement"), ("asehoy ny tatitra herinandro", "statement"),
    ("Foano ilay farany", "undo"), ("fafao ilay teo", "undo"), ("diso ilay teo", "undo"),
    ("Ajanony", "stop"), ("ajanony azafady", "stop"), ("Arrête", "stop"),
    ("Avereno", "repeat"), ("avereno azafady", "repeat"), ("tsy re", "repeat"),
    ("Miverena", "home"), ("Fanampiana", "help"), ("Inona no azoko lazaina?", "help"),
])
def test_commands_are_recognised(text, cmd):
    assert commands.detect(text) == cmd


@pytest.mark.parametrize("text", [
    "Rabe naka telo kapoaka vary, nandoa roa arivo Ariary",
    "Iza no manana trosa?", "Ohatrinona ny varotra androany?", "Eny", "Tsia",
    "Soa nandoa ny trosany", "Tsy nandoa izy",
    "ajanony aloha fa Rabe naka vary telo kapoaka sy siramamy",      # long: a note, not a command
    "foano ilay 2000",                                            # digits: never a command
])
def test_notes_and_answers_are_not_commands(text):
    assert commands.detect(text) is None


def test_dash_command_speaks_the_totals_and_writes_nothing():
    conn = _fresh()
    before = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    r = _say(conn, "Asehoy ny tabilao")
    assert r["kind"] == "command" and r["command"] == "dash"
    assert "Varotra androany" in r["message"] and "Ariary" in r["speak"]
    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == before
    assert not conn.execute("SELECT COUNT(*) FROM pending_plans").fetchone()[0]


def test_statement_command_reads_the_seal():
    r = _say(_fresh(), "Tatitra")
    assert r["command"] == "statement" and "diary" in r["message"]


def test_undo_command_only_asks():
    conn = _fresh()
    assert _say(conn, "Foano ilay farany")["message"] == "Tsy misy azo foanana."
    w = _say(conn, "Rabe naka telo kapoaka vary, nandoa roa arivo Ariary")
    executor.execute(conn, w["transcript"], w["plan"], w["verdict"])
    r = _say(conn, "Foano ilay farany")
    assert r["command"] == "undo" and r["undo"]["audit_id"]
    assert r["message"].startswith("Hofoanana ve ilay farany: « Rabe naka 3 kapoaka vary")
    assert "telo kapoaka" in r["speak"]
    # asking wrote nothing: the sale is still there
    assert conn.execute("SELECT COUNT(*) FROM sales WHERE voided=0").fetchone()[0] == 1
    executor.undo(conn, r["undo"]["audit_id"])
    assert commands.last_undoable(conn) is None or commands.last_undoable(conn)["id"] != r["undo"]["audit_id"]


def test_only_exact_cancel_words_drop_a_question():
    assert commands.is_cancel("Foano") and commands.is_cancel("tsia")
    assert not commands.is_cancel("tsy nandoa") and not commands.is_cancel("Rabe")
