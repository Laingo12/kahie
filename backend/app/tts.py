"""Optional server voice for Kahie's spoken replies (Google Gemini TTS).

Off unless GEMINI_API_KEY is set. When it is off, or when a call fails, the app falls back to the
phone's own voice (speechSynthesis + the phonetic respelling in app.js) — so Kahie always speaks.

Not a Meta model: it only reads aloud text Kahie already wrote (questions, confirmations, answers).
Nothing it returns is ever written to the ledger. The text sent is the spoken sentence only
(fictional names in the demo); say so in the consent form if real shopkeepers use it.

Gemini TTS returns raw PCM (16-bit, mono, 24 kHz); we wrap it in a WAV header so <audio> can play it.
"""
import base64
import io
import wave
from collections import OrderedDict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import config

router = APIRouter()
MAX_CHARS = 600
CACHE_MAX = 300                 # Kahie repeats the same short sentences ("Eny sa tsia?"): synthesize once
_cache: "OrderedDict[str, bytes]" = OrderedDict()

# How Gemini should read: Malagasy as spoken in Antananarivo, calm, numbers clear (they are already words).
STYLE = ("Read aloud in Malagasy, as spoken in Antananarivo, Madagascar. Warm, clear and calm, like a "
         "shopkeeper's helper; pronounce every number slowly and distinctly. Say only the sentence: ")


class SpeakRequest(BaseModel):
    text: str


def enabled() -> bool:
    return bool(config.GEMINI_API_KEY)


def pcm_to_wav(pcm: bytes, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def synthesize(text: str) -> bytes:
    """Text → WAV bytes (cached). Raises on any failure (the caller falls back to the phone's voice)."""
    if text in _cache:
        _cache.move_to_end(text)
        return _cache[text]
    wav = _synthesize(text)
    _cache[text] = wav
    if len(_cache) > CACHE_MAX:
        _cache.popitem(last=False)
    return wav


def _synthesize(text: str) -> bytes:
    from google import genai                      # imported here: the package is only needed when TTS is on
    from google.genai import types

    client = genai.Client(api_key=config.GEMINI_API_KEY)
    resp = client.models.generate_content(
        model=config.GEMINI_TTS_MODEL,
        contents=STYLE + text,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=config.GEMINI_TTS_VOICE)
                )
            ),
        ),
    )
    data = resp.candidates[0].content.parts[0].inline_data.data
    pcm = base64.b64decode(data) if isinstance(data, str) else data
    if not pcm:
        raise RuntimeError("empty audio")
    return pcm_to_wav(pcm)


@router.post("/api/speak")
def speak(req: SpeakRequest):
    if not enabled():
        raise HTTPException(404, "server TTS off (no GEMINI_API_KEY)")
    text = (req.text or "").strip()[:MAX_CHARS]
    if not text:
        raise HTTPException(400, "text required")
    try:
        wav = synthesize(text)
    except Exception as e:  # noqa: BLE001 — any failure → the phone speaks instead
        raise HTTPException(502, f"TTS: {type(e).__name__}")
    return {"audio": base64.b64encode(wav).decode("ascii"), "mime": "audio/wav"}
