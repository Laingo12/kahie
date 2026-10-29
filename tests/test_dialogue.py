"""Two-way voice: what a short spoken answer means, and what Kahie says (no network)."""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("KAHIE_DB", str(Path(tempfile.mkdtemp()) / "test.db"))
os.environ["KAHIE_BACKEND"] = "mock"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pytest  # noqa: E402

from app import db, phrases  # noqa: E402
from app.agent import dialogue, pipeline  # noqa: E402
from app.numbers_mg import speakify  # noqa: E402


@pytest.mark.parametrize("said, meaning", [
    ("Eny", True), ("eny marina", True), ("Oui", True), ("ok", True), ("Ekena", True), ("izay izy", True),
    ("Tsia", False), ("Tsy marina", False), ("Diso", False), ("non", False), ("aza soratana", False),
    ("siramamy", None), ("", None),
])
def test_yes_no(said, meaning):
    assert dialogue.yes_no(said) is meaning


@pytest.mark.parametrize("echo", ["Marina ve?", "Rabe naka vary 3 kapoaka, trosa 1000 Ar. Marina ve?", "Ekenao ve?",
                                  "Rabe naka vary telo kapoaka telo arivo trosa vaovao arivo marina tokoa"])
def test_kahie_own_question_or_a_long_sentence_is_never_a_yes(echo):
    assert dialogue.yes_no(echo) is None


OPTS = [{"id": "ear1", "text": "Rabe naka 3 kapoaka vary nandoa 2000 Ar"},
        {"id": "ear2", "text": "Rabe naka 3 kapoaka vary nandoa 3000 Ar"}]


@pytest.mark.parametrize("said, choice", [("roa arivo", "ear1"), ("2000", "ear1"), ("telo arivo", "ear2"),
                                          ("telo", None), ("vary", None), ("", None)])
def test_pick_option_by_the_disputed_number(said, choice):
    assert dialogue.pick_option(OPTS, said) == choice


def test_pick_option_by_name():
    opts = [{"id": "ear1", "text": "Rabe naka vary"}, {"id": "ear2", "text": "Soa naka vary"}]
    assert dialogue.pick_option(opts, "Soa") == "ear2" and dialogue.pick_option(opts, "Rabe izy") == "ear1"


def test_spoken_answer_completes_the_note():
    conn = db.connect(Path(tempfile.mkdtemp()) / "t.db"); db.init(conn); db.seed_demo(conn)
    first = pipeline.handle_voice(conn, b"", "audio/webm", hint_text="Bodo naka zavatra")
    assert first["verdict"]["level"] == "clarify"
    note = dialogue.merge_answer(first["transcript"], pipeline.hear_short(conn, b"", "x", "siramamy roa kilao")["text"])
    second = pipeline.handle_clarification(conn, first["pending_id"], text=note)
    line = second["verdict"]["lines"][0]
    assert (line["product"], line["qty"], line["unit"], line["total"]) == ("siramamy", 2, "kg", 6400)


# ---------------------------------------------------------------- what Kahie says
def test_two_variants_for_every_state():
    for state, variants in phrases.PHRASES.items():
        assert len(variants) == 2 and all(v.strip() for v in variants), state


def test_amount_question_direct_and_softer():
    assert phrases.say("clarify_amount", 0, a=2000, b=3000) == "Firy marina: 2000 sa 3000?"
    assert speakify(phrases.say("clarify_amount", 1, a=2000, b=3000)) == "Azafady, roa arivo ve sa telo arivo no marina?"


def test_success_reads_the_numbers():
    lines = [{"kind": "sale", "total": 3000}, {"kind": "debt", "customer": "Rabe", "amount": 1000}]
    assert phrases.success(lines) == "Voasoratra: varotra 3 000 Ar, trosa 1 000 Ar."
    assert speakify(phrases.success(lines)) == "Voasoratra: varotra telo arivo Ariary, trosa arivo Ariary."
    assert "no mbola trosa" in phrases.success(lines, 1)
    assert phrases.success([{"kind": "payment", "customer": "Soa", "amount": 1500}]) == "Voasoratra: nandoa 1 500 Ar i Soa."


# ---------------------------------------------------------------- the spoken yes/no question
def _confirmation(text, conn=None):
    conn = conn or _db()
    return pipeline.handle_voice(conn, b"", "audio/webm", hint_text=text)


def _db():
    conn = db.connect(Path(tempfile.mkdtemp()) / "t.db"); db.init(conn); db.seed_demo(conn)
    return conn


@pytest.mark.parametrize("note, spoken", [
    ("Rabe naka telo kapoaka vary, nandoa roa arivo Ariary",
     "Soratana ve fa naka vary 3 kapoaka i Rabe, nandoa 2 000 Ar, ary mbola manana trosa 1 000 Ar?"),
    ("Soa naka siramamy dimanjato grama", "Soratana ve fa naka siramamy 500 g i Soa, ary nandoa 1 600 Ar?"),
    ("Mofo folo no lafo tamin'ny olona tsy fantatra",
     "Soratana ve fa nisy olona tsy fantatra naka mofo 10, ary nandoa 3 000 Ar?"),
    ("Bodo nindrana roa arivo Ariary", "Soratana ve fa nindrana 2 000 Ar i Bodo?"),
    ("Soa nandoa ny trosany, telo arivo sy dimanjato",
     "Tandremo: Tsy manana trosa i Soa, nefa misy vola naloa 3 500 Ar. Soratana ve fa nandoa trosa 3 500 Ar i Soa?"),
    ("Tonga ny vary, kapoaka dimampolo", "Soratana ve fa niditra vary 50 kapoaka?"),
    ("Simba ny menaka valo litatra", "Soratana ve fa simba na very menaka 8 litatra?"),
    ("Bodo naka zavatra", "Inona no entana?"),
])
def test_spoken_confirmation(note, spoken):
    r = _confirmation(note)
    assert r["spoken_confirmation"] == spoken and r["verdict"]["speak"]


def test_spoken_confirmation_is_never_a_yes_when_heard_back():
    r = _confirmation("Rabe naka telo kapoaka vary, nandoa roa arivo Ariary")
    assert dialogue.yes_no(r["verdict"]["speak"]) is None
