"""Optional server voice: off without a key, WAV wrapping correct, failures fall back (never crash)."""
import io
import wave

import pytest
from fastapi import HTTPException

from app import config, tts


def test_off_without_key():
    assert not tts.enabled()
    with pytest.raises(HTTPException) as e:
        tts.speak(tts.SpeakRequest(text="Eny"))
    assert e.value.status_code == 404


def test_pcm_is_wrapped_as_playable_wav():
    wav = tts.pcm_to_wav(b"\x00\x00" * 2400)
    with wave.open(io.BytesIO(wav)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, 24000, 2400)


def test_failure_is_a_clean_error_so_the_phone_speaks(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "x")
    def boom(text):
        raise RuntimeError("no network")
    monkeypatch.setattr(tts, "synthesize", boom)
    with pytest.raises(HTTPException) as e:
        tts.speak(tts.SpeakRequest(text="Soratana ve?"))
    assert e.value.status_code == 502


def test_text_is_capped(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "x")
    seen = {}
    monkeypatch.setattr(tts, "synthesize", lambda t: seen.setdefault("t", t) and tts.pcm_to_wav(b"\x00\x00"))
    r = tts.speak(tts.SpeakRequest(text="a" * 5000))
    assert len(seen["t"]) == tts.MAX_CHARS and r["mime"] == "audio/wav"


@pytest.mark.parametrize("text, said", [
    ("Mbola manana trosa i Rabe", "Mbola manana tchosa i Rabe"),
    ("Andry nitrosa", "Andjy nitchosa"),
    ("Tratra ny fandrosoana", "Tchatcha ny fandjosoana"),
    ("Eny sa tsia?", "Eny sa tsia?"),
])
def test_tr_and_dr_are_respelled_for_the_voice(text, said):
    assert tts.pronounce_mg(text) == said
