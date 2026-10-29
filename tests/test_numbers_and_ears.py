"""Spoken numbers, lexicon-before-reconcile, and answering the agent's question (mock mode, no API)."""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("KAHIE_DB", str(Path(tempfile.mkdtemp()) / "test.db"))
os.environ["KAHIE_BACKEND"] = "mock"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pytest  # noqa: E402

from app import db, lexicon, models_client  # noqa: E402
from app.agent import pipeline, reconciler  # noqa: E402
from app.numbers_mg import numbers_in, to_digits  # noqa: E402


# ---------------------------------------------------------------- spoken numbers
@pytest.mark.parametrize("spoken, digits", [
    ("roa arivo", "2000"),
    ("roa amby telopolo", "32"),                 # units first, "amby"
    ("iraika ambin'ny folo", "11"),
    ("dimy amby roapolo sy zato", "125"),        # "sy" between parts
    ("roa arivo sy dimanjato", "2500"),          # big first
    ("dimanjato sy roa arivo", "2500"),          # small first
    ("roa arivo dimanjato", "2500"),
    ("roa amby telopolo arivo", "32000"),        # group × arivo
    ("telo alina", "30000"),
    ("deux mille cinq cents", "2500"),
    ("quatre-vingt-dix", "90"),
    ("vingt et un", "21"),
    ("2 arivo", "2000"),
    ("2 000", "2000"),                           # thousands separator
])
def test_spoken_numbers(spoken, digits):
    assert to_digits(spoken) == digits


def test_numbers_inside_a_note():
    assert to_digits("Rabe naka telo kapoaka vary, nandoa roa arivo Ariary") == \
        "Rabe naka 3 kapoaka vary, nandoa 2000 Ariary"


@pytest.mark.parametrize("text", ["vary sy siramamy", "riz et sucre", "naka 3 2000 Ar", "roa telo"])
def test_separate_numbers_are_not_merged(text):
    out = to_digits(text)
    assert "32000" not in out and "5" not in out.split()


def test_numbers_in_keeps_separate_numbers_apart():
    assert numbers_in("naka 3 2000 Ar") == {3, 2000}
    assert numbers_in("nandoa 2 000 Ar") == {2000}


# ---------------------------------------------------------------- reconciler
def test_words_and_digits_agree_after_normalisation():
    conn = _fresh()
    a = pipeline.normalize_ear(conn, "Rabe naka telo kapoaka vary nandoa roa arivo Ariary")
    b = pipeline.normalize_ear(conn, "Rabe naka 3 kapoaka vary nandoa 2000 Ar")
    assert reconciler.reconcile(a, b, lexicon.keywords(conn))["agree"]


def test_question_names_only_the_disputed_numbers():
    rec = reconciler.reconcile("Rabe 3 vary 2000 Ar", "Rabe 3 vary 3000 Ar", ["vary", "Rabe"])
    assert not rec["agree"] and rec["question"] == "Firy marina: 2000 sa 3000?"
    assert [o["id"] for o in rec["options"]] == ["ear1", "ear2"]


def test_single_ear_is_used_when_the_other_is_silent():
    assert reconciler.reconcile("", "Rabe 3 vary", ["vary"]) ["agree"]
    assert reconciler.reconcile("Rabe 3 vary", "", ["vary"]) ["agree"]
    assert not reconciler.reconcile("", "", ["vary"])["agree"]


def test_lexicon_is_applied_before_the_ears_are_compared():
    conn = _fresh()
    heard, right = "Rabe naka 3 kapwaka vary", "Rabe naka 3 kapoaka vary"
    vocab = lexicon.keywords(conn) + ["kapoaka"]
    # before learning: the ears disagree on a name
    assert not reconciler.reconcile(pipeline.normalize_ear(conn, heard), pipeline.normalize_ear(conn, right), vocab)["agree"]
    # the merchant corrects it once
    lexicon.learn(conn, "kapwaka", "kapoaka", "unit")
    # after: the same two readings agree, no question asked
    assert reconciler.reconcile(pipeline.normalize_ear(conn, heard), pipeline.normalize_ear(conn, right), vocab)["agree"]


def test_lexicon_normalize_is_punctuation_safe():
    conn = _fresh()
    lexicon.learn(conn, "kapwaka", "kapoaka", "unit")
    assert lexicon.normalize(conn, "Rabe naka 3 kapwaka, vary") == "Rabe naka 3 kapoaka, vary"


# ---------------------------------------------------------------- answering the question
def test_choosing_an_ear_replans_and_still_needs_confirmation(monkeypatch):
    conn = _fresh()
    ears = {"ear1": "Rabe naka 3 kapoaka vary nandoa roa arivo Ar", "ear2": "Rabe naka 3 kapoaka vary nandoa 3000 Ar"}
    monkeypatch.setattr(models_client, "transcribe", lambda *a, **k: {"text": ears["ear1"]})
    monkeypatch.setattr(models_client, "listen_with_spark", lambda *a, **k: {"text": ears["ear2"]})

    first = pipeline.handle_voice(conn, b"fake-audio", "audio/webm")
    assert first["verdict"]["level"] == "clarify" and "2000 sa 3000" in first["verdict"]["question"]
    assert first["ears"]["ear1"] == ears["ear1"]  # raw words shown on the card

    second = pipeline.handle_clarification(conn, first["pending_id"], choice="ear1")
    kinds = [l["kind"] for l in second["verdict"]["lines"]]
    assert kinds == ["sale", "debt"] and second["verdict"]["lines"][1]["amount"] == 1000
    assert conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0] == 0          # nothing written yet
    assert conn.execute("SELECT 1 FROM pending_plans WHERE id=?", (first["pending_id"],)).fetchone() is None


def test_rewriting_the_note_answers_any_question():
    conn = _fresh()
    first = pipeline.handle_voice(conn, b"", "audio/webm", hint_text="Bodo naka zavatra")
    assert first["verdict"]["level"] == "clarify"
    second = pipeline.handle_clarification(conn, first["pending_id"], text="Bodo naka roa savony")
    assert second["verdict"]["lines"][0]["product"] == "savony" and second["verdict"]["lines"][0]["qty"] == 2


def test_clarification_needs_a_choice_or_text():
    conn = _fresh()
    first = pipeline.handle_voice(conn, b"", "audio/webm", hint_text="Bodo naka zavatra")
    with pytest.raises(ValueError):
        pipeline.handle_clarification(conn, first["pending_id"])
    with pytest.raises(LookupError):
        pipeline.handle_clarification(conn, "nope", text="x")


def _fresh():
    conn = db.connect(Path(tempfile.mkdtemp()) / "t.db")
    db.init(conn)
    db.seed_demo(conn)
    return conn
