"""Thin wrapper around the Meta Model API (OpenAI-compatible) with an offline mock.

Ear 1 modes (config.EAR1_MODE, default "spark_literal"):
  - "spark_literal": Spark audio with a LITERAL prompt that keeps numbers as words.
    Cheap, works now. The reconciler catches the difference between this and ear 2's
    digit output (the whole point of the dual-ear test).
  - "whisper": Whisper large-v3 via Fireworks (needs FIREWORKS_API_KEY).
    True independent ear, counts for the Open Weights bonus (config D).
  - "voice_transcribe": real Muse Voice Transcribe WebSocket (J1 task, not wired yet).
  - "off": no ear 1; reconciler falls back to single-ear.
"""
import base64
import io
import json
import re
import subprocess
from pathlib import Path

from . import config
from .numbers_mg import to_digits

AUDIO_MIN_TOKENS = 120  # system prompt alone ~60-80 tokens; calibrate on J1 with a real note

try:
    TOOLS = json.loads((Path(__file__).parent / "agent" / "tools.json").read_text(encoding="utf-8"))
except FileNotFoundError:
    TOOLS = []  # server stays up; planner will fall back to ask_clarification


def to_wav(audio: bytes, rate: int = 16000) -> bytes:
    """Browser MediaRecorder gives webm/opus; Spark accepts only WAV or MP3."""
    out = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-ac", "1", "-ar", str(rate), "-f", "wav", "pipe:1"],
        input=audio, capture_output=True, check=True)
    return out.stdout


def to_pcm16(audio: bytes, rate: int = 24000) -> bytes:
    """Raw mono PCM16 for the Voice Transcribe WebSocket."""
    out = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-ac", "1", "-ar", str(rate), "-f", "s16le", "pipe:1"],
        input=audio, capture_output=True, check=True)
    return out.stdout


def _client(open_weights: bool = False):
    from openai import OpenAI
    if open_weights:
        return OpenAI(base_url=config.OPEN_WEIGHTS_BASE, api_key=config.OPEN_WEIGHTS_KEY)
    return OpenAI(base_url=config.MODEL_API_BASE, api_key=config.MODEL_API_KEY)


# ---------------------------------------------------------------- ear 1
EAR1_LITERAL_SYSTEM = (
    "Transcris cette note vocale d'un commerçant malgache MOT À MOT (malgache, français ou mélange). "
    "IMPORTANT: garde les nombres TELS QUELS, en mots. Écris 'roa arivo' et non '2000', "
    "écris 'telo kapoaka' et non '3 kapoaka'. Écris uniquement la transcription, rien d'autre."
)


def _spark_ear1_literal(audio: bytes, mime: str, keywords: list[str], hint_text: str | None = None) -> dict:
    """Ear 1 fallback: Spark audio, literal prompt (keeps numbers as words)."""
    if hint_text:
        return {"text": hint_text, "confidence": None, "source": "ear1-hint"}
    try:
        wav = to_wav(audio)
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        return {"text": "", "confidence": None, "source": "ear1-spark", "error": f"to_wav: {type(e).__name__}"}
    client = _client(config.BACKEND == "open")
    b64 = base64.b64encode(wav).decode()
    system = EAR1_LITERAL_SYSTEM
    if keywords:
        system += "\nVocabulaire possible : " + ", ".join(keywords[:30])
    resp = client.chat.completions.create(
        max_tokens=4000,
        model=config.OPEN_WEIGHTS_MODEL if config.BACKEND == "open" else getattr(config, "MODEL_EAR1", config.MODEL_EAR2),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": b64, "format": "wav"}}]},
        ],
    )
    text = (resp.choices[0].message.content or "").strip()
    return {"text": text, "confidence": None, "source": "ear1-spark-literal"}


def _whisper_transcribe(audio: bytes, mime: str, keywords: list[str], hint_text: str | None = None) -> dict:
    """Ear 1 real: Whisper large-v3 via Fireworks (OpenAI-compatible)."""
    if hint_text:
        return {"text": hint_text, "confidence": None, "source": "ear1-hint"}
    key = getattr(config, "FIREWORKS_API_KEY", "")
    if not key:
        return {"text": "", "confidence": None, "source": "ear1-whisper", "error": "FIREWORKS_API_KEY missing"}
    from openai import OpenAI
    base = getattr(config, "WHISPER_BASE_URL", "https://audio-prod.us-virginia-1.direct.fireworks.ai/v1")
    client = OpenAI(base_url=base, api_key=key)
    try:
        wav = to_wav(audio)
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        return {"text": "", "confidence": None, "source": "ear1-whisper", "error": f"to_wav: {type(e).__name__}"}
    file_obj = io.BytesIO(wav)
    file_obj.name = "audio.wav"
    kwargs = {"model": getattr(config, "WHISPER_MODEL", "whisper-v3"), "file": file_obj}
    if keywords:
        kwargs["prompt"] = ", ".join(keywords[:30])
    try:
        resp = client.audio.transcriptions.create(**kwargs)
        return {"text": (getattr(resp, "text", "") or "").strip(), "confidence": None, "source": "ear1-whisper"}
    except Exception as e:
        return {"text": "", "confidence": None, "source": "ear1-whisper", "error": type(e).__name__}


def transcribe(audio: bytes, mime: str, keywords: list[str], hint_text: str | None = None) -> dict:
    """Ear 1. Mode controlled by config.EAR1_MODE (see module docstring)."""
    if config.BACKEND == "mock" or not audio:
        return {"text": hint_text or "", "confidence": 1.0, "source": "mock"}
    mode = getattr(config, "EAR1_MODE", "spark_literal")
    if mode == "off":
        return {"text": "", "confidence": None, "source": "ear1-off"}
    if mode == "whisper":
        return _whisper_transcribe(audio, mime, keywords, hint_text)
    if mode == "voice_transcribe":
        # TODO(J1): WebSocket wss://api.meta.ai/v1/asr/realtime — see docs/meta-api-notes.md
        return {"text": "", "confidence": None, "source": "ear1-not-wired"}
    # default: spark_literal — cheap ear, literal prompt (keeps numbers as words)
    return _spark_ear1_literal(audio, mime, keywords, hint_text)


# ---------------------------------------------------------------- ear 2
def listen_with_spark(audio: bytes, mime: str, lexicon_prompt: str, hint_text: str | None = None) -> dict:
    """Ear 2 — Muse Spark listens to the raw audio with the merchant lexicon as context."""
    if config.BACKEND == "mock" or not audio:
        return {"text": hint_text or "", "confidence": 1.0, "source": "mock"}
    try:
        wav = to_wav(audio)
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        return {"text": "", "confidence": None, "source": "spark-audio", "error": f"to_wav: {type(e).__name__}"}
    client = _client(config.BACKEND == "open")
    b64 = base64.b64encode(wav).decode()
    resp = client.chat.completions.create(
        max_tokens=4000,
        model=config.OPEN_WEIGHTS_MODEL if config.BACKEND == "open" else config.MODEL_EAR2,
        messages=[
            {"role": "system", "content": "Transcris mot à mot cette note vocale d'un commerçant malgache "
                                          "(malgache, français ou mélange). Écris les nombres en chiffres.\n" + lexicon_prompt},
            {"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": b64, "format": "wav"}}]},
        ],
    )
    prompt_tokens = getattr(resp.usage, "prompt_tokens", None) if resp.usage else None
    dropped = prompt_tokens is not None and prompt_tokens < AUDIO_MIN_TOKENS + len(lexicon_prompt) // 4
    text = "" if dropped else (resp.choices[0].message.content or "").strip()
    return {"text": text, "confidence": None, "source": "spark-audio", "dropped": dropped, "prompt_tokens": prompt_tokens}


# ---------------------------------------------------------------- planner
PLANNER_SYSTEM = """Tu es l'agent comptable d'une petite boutique à Madagascar.
À partir de la note (malgache, français ou mélange), appelle les outils nécessaires.
Montants en Ariary (Ar). N'invente jamais un produit, un client ou un montant :
si une information manque, appelle ask_clarification.
{lexicon}
Produits connus : {products}
Clients connus : {customers}"""


def plan(text: str, context: dict) -> list[dict]:
    """Returns a list of tool calls: [{"tool": name, "args": {...}}]."""
    if config.BACKEND == "mock":
        return mock_plan(text, context)
    client = _client(config.BACKEND == "open")
    resp = client.chat.completions.create(
        model=config.OPEN_WEIGHTS_MODEL if config.BACKEND == "open" else config.MODEL_PLANNER,
        messages=[
            {"role": "system", "content": PLANNER_SYSTEM.format(**context)},
            {"role": "user", "content": text},
        ],
        tools=TOOLS,
        tool_choice="required",
    )
    calls = resp.choices[0].message.tool_calls or []
    return [{"tool": c.function.name, "args": json.loads(c.function.arguments or "{}")} for c in calls]


# ---------------------------------------------------------------- vision
def read_notebook(image: bytes, mime: str, context: dict) -> list[dict]:
    """Spark vision reads a photo of the paper notebook → record_debt calls to confirm."""
    if config.BACKEND == "mock":
        return []
    client = _client()
    b64 = base64.b64encode(image).decode()
    resp = client.chat.completions.create(
        model=config.MODEL_VISION,
        messages=[
            {"role": "system", "content": "Lis cette page de cahier de dettes. Pour chaque client et montant dû, "
                                          "appelle record_debt. Si une ligne est illisible, appelle ask_clarification.\n"
                                          + PLANNER_SYSTEM.format(**context)},
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]},
        ],
        tools=TOOLS,
        tool_choice="required",
    )
    calls = resp.choices[0].message.tool_calls or []
    return [{"tool": c.function.name, "args": json.loads(c.function.arguments or "{}")} for c in calls]


# ---------------------------------------------------------------- offline mock
# Payment verbs: "nandoa 2000", "payé 2000", "a payé 2000", "donné 2000"
PAID_VERBS = r"(?:nandoa|nanome|namatsy|nanaloa|nandoavana|payé|paye|donné|donne)"


def mock_plan(text: str, ctx: dict) -> list[dict]:
    """Rule-based planner so the app, tests and demo run without an API key.
    Expects normalised text (numbers already in digits, see pipeline.normalize_ear)."""
    t = to_digits(text).lower()
    customer = next((c for c in ctx["customer_list"]
                     if re.search(r"(?<!\w)" + re.escape(c.lower()) + r"(?!\w)", t)), None)
    items = []
    for p in ctx["product_list"]:
        m = re.search(r"(?<![\d\w])(\d+)\s+(?:kapoaka|litatra|pièces?|kg)?\s*(?:de\s+)?" + re.escape(p.lower()), t)
        if m and int(m.group(1)):
            items.append({"product": p, "qty": int(m.group(1))})
    paid = None
    # (a) explicit "2000 ar" / "2000 ariary"
    m = re.search(r"(?<![\d\w])(\d+)\s*(?:ar|ariary)(?!\w)", t)
    if m:
        paid = int(m.group(1))
    else:
        # (b) "nandoa 2000", "payé 2000", "donné 2000"
        m = re.search(PAID_VERBS + r"\s+(\d+)", t)
        if m:
            paid = int(m.group(1))
    if not items:
        return [{"tool": "ask_clarification", "args": {"question": "Inona no entana nolafosina ? (Quel produit ?)"}}]
    calls = [{"tool": "record_sale", "args": {"customer": customer, "items": items, "paid": paid}}]
    return calls