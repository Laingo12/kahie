"""No invented transcripts, no invented numbers or names (no network: fake audio and fake model)."""
import io
import math
import os
import sys
import tempfile
import wave
from array import array
from pathlib import Path
from types import SimpleNamespace as NS

os.environ.setdefault("KAHIE_DB", str(Path(tempfile.mkdtemp()) / "test.db"))
os.environ["KAHIE_BACKEND"] = "mock"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import audio_guard, config, db, models_client, phrases  # noqa: E402
from app.agent import grounding, pipeline  # noqa: E402

META = ("Aucun audio n'a été reçu ou n'est exploitable, je ne peux donc pas effectuer de transcription mot à mot. "
        "Merci de renvoyer la note vocale.")
ECHO = "Aucune note vocale détectée. Envoie le fichier audio, avec le vocabulaire : vary, siramamy, menaka, savony."


def _wav(seconds=2.0, amplitude=8000, rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(array("h", (int(amplitude * math.sin(2 * math.pi * 300 * i / rate))
                                  for i in range(int(seconds * rate)))).tobytes())
    return buf.getvalue()


# ---------------------------------------------------------------- 1. audio checked before any call
def test_silent_short_and_broken_audio_are_rejected():
    assert not audio_guard.inspect(_wav(amplitude=0))["ok"]
    assert not audio_guard.inspect(_wav(seconds=0.2))["ok"]
    assert not audio_guard.inspect(b"not a wav")["ok"]
    assert audio_guard.inspect(_wav())["ok"]


def test_silent_note_never_reaches_the_model(monkeypatch):
    called = []
    monkeypatch.setattr(config, "BACKEND", "api")
    monkeypatch.setattr(models_client, "to_wav", lambda audio, rate=16000: _wav(amplitude=0))
    monkeypatch.setattr(models_client, "_client", lambda *a, **k: called.append(1))
    out = models_client.listen_with_spark(b"webm", "audio/webm", "")
    assert out["text"] == "" and "mangina" in out["error"] and not called


# ---------------------------------------------------------------- 2. replies about the audio are not transcripts
def test_meta_replies_and_prompt_echo_are_rejected():
    for text in (META, ECHO, "I cannot hear any audio, please upload the file."):
        out = audio_guard.clean({"text": text}, 3.0)
        assert out["text"] == "" and out["rejected"]


def test_real_transcripts_pass():
    for text in ("Rabe naka telo kapoaka vary, nandoa roa arivo Ariary", "Soa a payé sa dette, dimy arivo."):
        assert audio_guard.clean({"text": text}, 3.0)["text"] == text


def test_too_many_words_for_the_audio_length():
    out = audio_guard.clean({"text": " ".join(["vary"] * 30)}, 1.0)
    assert out["text"] == "" and "be loatra" in out["error"]


def _fake_spark(content, prompt_tokens=900):
    resp = NS(choices=[NS(message=NS(content=content, tool_calls=None))], usage=NS(prompt_tokens=prompt_tokens))
    return NS(chat=NS(completions=NS(create=lambda **k: resp)))


def test_spark_meta_reply_becomes_an_empty_ear(monkeypatch):
    monkeypatch.setattr(config, "BACKEND", "api")
    monkeypatch.setattr(models_client, "to_wav", lambda audio, rate=16000: _wav())
    monkeypatch.setattr(models_client, "_client", lambda *a, **k: _fake_spark(META))
    out = models_client.listen_with_spark(b"webm", "audio/webm", "")
    assert out["text"] == "" and out["rejected"].startswith("Aucun audio")


def test_spark_silence_token(monkeypatch):
    monkeypatch.setattr(config, "BACKEND", "api")
    monkeypatch.setattr(models_client, "to_wav", lambda audio, rate=16000: _wav())
    monkeypatch.setattr(models_client, "_client", lambda *a, **k: _fake_spark("(silence)"))
    assert models_client.listen_with_spark(b"webm", "audio/webm", "")["text"] == ""


def test_two_rejected_ears_ask_to_repeat_and_offer_no_junk(monkeypatch):
    conn = db.connect(Path(tempfile.mkdtemp()) / "t.db"); db.init(conn); db.seed_demo(conn)
    monkeypatch.setattr(config, "BACKEND", "api")
    monkeypatch.setattr(models_client, "transcribe", lambda *a, **k: audio_guard.clean({"text": META}, 3.0))
    monkeypatch.setattr(models_client, "listen_with_spark", lambda *a, **k: audio_guard.clean({"text": ECHO}, 3.0))
    v = pipeline.handle_voice(conn, b"webm", "audio/webm")["verdict"]
    assert v["level"] == "clarify" and "Avereno" in v["question"] and not v.get("options")


# ---------------------------------------------------------------- 3. the planner cannot invent values
def test_invented_quantity_or_customer_is_caught():
    g = grounding.check("Rabe naka vary", [{"tool": "record_sale", "args": {"customer": "Rabe", "items": [{"product": "vary", "qty": 1}]}}])
    assert g["missing"] == ["1"]
    g = grounding.check("naka 3 kapoaka vary", [{"tool": "record_sale", "args": {"customer": "Soa", "items": [{"product": "vary", "qty": 3}]}}])
    assert g["missing"] == ["Soa"]


def test_grounded_plan_passes_including_zero_and_halves():
    calls = [{"tool": "record_sale", "args": {"customer": "Rabe", "items": [{"product": "siramamy", "qty": 0.5, "unit": "kg"}], "paid": 0}}]
    assert grounding.check("Rabe naka 0.5 kg siramamy, crédit daholo.", calls) == {"missing": [], "unsure_products": []}


def test_ungrounded_plan_becomes_a_question_not_a_card(monkeypatch):
    conn = db.connect(Path(tempfile.mkdtemp()) / "t.db"); db.init(conn); db.seed_demo(conn)
    monkeypatch.setattr(models_client, "plan", lambda text, ctx: [
        {"tool": "record_sale", "args": {"customer": "Rabe", "items": [{"product": "vary", "qty": 2}], "paid": 1500}}])
    v = pipeline.handle_voice(conn, b"", "audio/webm", hint_text="Rabe naka vary")["verdict"]
    assert v["level"] == "clarify" and "2" in v["question"] and "1500" in v["question"] and not v["lines"]


def test_long_planner_prose_is_not_shown_as_the_question():
    resp = NS(choices=[NS(message=NS(tool_calls=None, content=META + " " + META))])
    assert models_client._tool_calls(resp)[0]["args"]["question"] == phrases.say("not_heard")
