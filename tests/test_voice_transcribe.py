"""Ear 1 = Muse Voice Transcribe (batch endpoint). No network: httpx.post is replaced by a fake."""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("KAHIE_DB", str(Path(tempfile.mkdtemp()) / "test.db"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import httpx  # noqa: E402

from app import config, models_client  # noqa: E402


def _tone_wav(seconds=1.0, rate=16000):
    """A real mono 16-bit WAV with a 440 Hz tone (passes the silence check)."""
    import io, math, wave
    from array import array
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(array("h", (int(8000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(int(seconds * rate)))).tobytes())
    return buf.getvalue()


WAV = _tone_wav(2.0)


class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.text = json.dumps(body)

    def json(self):
        return self._body


def _fake_post(calls, status=200, body=None):
    def post(url, headers=None, files=None, timeout=None):
        calls.append({"url": url, "headers": headers, "files": files})
        return FakeResponse(status, body if body is not None else
                            {"sessionId": "s1", "transcript": " Rabe naka telo kapoaka vary ", "audioDurationMs": 2100, "turns": []})
    return post


def _setup(monkeypatch, calls, **kw):
    monkeypatch.setattr(models_client, "to_wav", lambda audio, rate=16000: WAV)
    monkeypatch.setattr(httpx, "post", _fake_post(calls, **kw))
    monkeypatch.setattr(config, "MODEL_API_KEY", "test-key")
    monkeypatch.setattr(config, "MODEL_API_BASE", "https://api.meta.ai/v1/")


def test_request_shape_matches_the_docs(monkeypatch):
    calls = []
    _setup(monkeypatch, calls)
    out = models_client.voice_transcribe(b"webm", keywords=["vary", "Rabe", "vary", ""], language_bias=["French"])
    assert out["text"] == "Rabe naka telo kapoaka vary" and out["source"] == "voice-transcribe"
    assert out["session_id"] == "s1" and out["audio_ms"] == 2100
    c = calls[0]
    assert c["url"] == "https://api.meta.ai/v1/asr/transcribe"
    assert c["headers"]["Authorization"] == "Bearer test-key"
    name, payload, ctype = c["files"]["request"]
    req = json.loads(payload)
    assert name is None and ctype == "application/json"
    assert req == {"model": config.MODEL_TRANSCRIBE, "audioEncoding": "WAV", "mode": "PUSH_TO_TALK",
                   "keywords": ["vary", "Rabe"], "languageBias": ["French"]}
    assert c["files"]["audio"] == ("note.wav", WAV, "audio/wav")


def test_no_bias_means_auto_detect(monkeypatch):
    calls = []
    _setup(monkeypatch, calls)
    models_client.voice_transcribe(b"webm", keywords=[], language_bias=None)
    req = json.loads(calls[0]["files"]["request"][1])
    assert "languageBias" not in req and "keywords" not in req


def test_http_error_never_raises(monkeypatch):
    calls = []
    _setup(monkeypatch, calls, status=429, body={"type": "rate_limit", "message": "Too many sessions"})
    out = models_client.voice_transcribe(b"webm")
    assert out["text"] == "" and out["error"] == "HTTP 429: Too many sessions"


def test_network_error_never_raises(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("no network")
    monkeypatch.setattr(models_client, "to_wav", lambda audio, rate=16000: WAV)
    monkeypatch.setattr(httpx, "post", boom)
    out = models_client.voice_transcribe(b"webm")
    assert out["text"] == "" and out["error"] == "ConnectError"


def test_transcribe_routes_to_voice_transcribe(monkeypatch):
    calls = []
    _setup(monkeypatch, calls)
    monkeypatch.setattr(config, "BACKEND", "api")
    monkeypatch.setattr(config, "EAR1_MODE", "voice_transcribe")
    monkeypatch.setattr(config, "VT_LANGUAGE_BIAS", [])
    out = models_client.transcribe(b"webm", "audio/webm", ["vary"])
    assert out["source"] == "voice-transcribe" and len(calls) == 1


def test_words_only_in_turns_are_kept(monkeypatch):
    calls = []
    _setup(monkeypatch, calls, body={"sessionId": "s2", "transcript": "", "audioDurationMs": 2000,
                                     "turns": [{"transcript": "Rabe naka"}, {"text": "telo kapoaka vary"}]})
    assert models_client.voice_transcribe(b"webm")["text"] == "Rabe naka telo kapoaka vary"


def test_empty_transcript_stays_empty(monkeypatch):
    calls = []
    _setup(monkeypatch, calls, body={"sessionId": "s3", "transcript": "", "audioDurationMs": 2000, "turns": []})
    assert models_client.voice_transcribe(b"webm")["text"] == ""
