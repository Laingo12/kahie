"""Thin wrapper around the Meta Model API (OpenAI-compatible) with an offline mock.

Ear 1 modes (config.EAR1_MODE, default "spark_literal"):
  - "spark_literal": Spark audio, literal prompt (keeps numbers as words)
  - "whisper": Whisper large-v3 via Fireworks (needs FIREWORKS_API_KEY)
  - "voice_transcribe": real Muse Voice Transcribe WebSocket (post-hackathon, stub)
  - "off": no ear 1; reconciler falls back to single-ear

Safety: the ear rejects (a) silent WAV payloads locally, (b) Meta's "no audio received" prose,
(c) "(silence)" tokens. The planner uses tool_choice="auto" so ambiguous notes become a
clarification question, never a silent write.
"""
import base64
import io
import json
import re
import struct
import subprocess
from pathlib import Path

from . import config
from .numbers_mg import to_digits

AUDIO_MIN_TOKENS = 120

try:
    TOOLS = json.loads((Path(__file__).parent / "agent" / "tools.json").read_text(encoding="utf-8"))
except FileNotFoundError:
    TOOLS = []

# Responses that mean "no usable audio": reject them instead of storing the prose
_META_PATTERNS = (
    "aucun audio",
    "n'a été reçu",
    "n'est exploitable",
    "je ne peux pas effectuer",
    "je ne peux donc pas",
    "je ne peux effectuer",
    "merci de renvoyer",
    "pas reçu",
    "no audio",
    "i cannot",
    "unable to transcribe",
    "cannot transcribe",
)

_SILENCE_TOKENS = ("(silence)", "silence", "(vide)", "...", "[silence]")


def to_wav(audio: bytes, rate: int = 16000) -> bytes:
    out = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-ac", "1", "-ar", str(rate), "-f", "wav", "pipe:1"],
        input=audio, capture_output=True, check=True)
    return out.stdout


def to_pcm16(audio: bytes, rate: int = 24000) -> bytes:
    out = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-ac", "1", "-ar", str(rate), "-f", "s16le", "pipe:1"],
        input=audio, capture_output=True, check=True)
    return out.stdout


def _client(open_weights: bool = False):
    from openai import OpenAI
    if open_weights:
        return OpenAI(base_url=config.OPEN_WEIGHTS_BASE, api_key=config.OPEN_WEIGHTS_KEY)
    return OpenAI(base_url=config.MODEL_API_BASE, api_key=config.MODEL_API_KEY)


# ---------------------------------------------------------------- guards
def _is_silent(wav: bytes) -> bool:
    """True if the WAV payload (after a 44-byte header) is all near-zero samples."""
    if len(wav) < 100:
        return True
    try:
        pcm = wav[44:]
        if len(pcm) < 2:
            return True
        pcm = pcm[: len(pcm) // 2 * 2]
        samples = struct.unpack(f"<{len(pcm) // 2}h", pcm)
        if not samples:
            return True
        return max(abs(s) for s in samples) < 50
    except Exception:
        return False


def _looks_like_meta(text: str) -> bool:
    t = (text or "").lower()
    return any(p in t for p in _META_PATTERNS)


def _is_silence_token(text: str) -> bool:
    return (text or "").strip().lower() in _SILENCE_TOKENS


def _tool_calls(resp) -> list[dict]:
    """Parse OpenAI-style tool calls. If the model replied in prose (tool_choice="auto"),
    turn it into an ask_clarification — never a silent write."""
    msg = resp.choices[0].message
    calls = msg.tool_calls or []
    if calls:
        out = []
        for c in calls:
            try:
                args = json.loads(c.function.arguments or "{}")
                if not isinstance(args, dict):
                    raise ValueError
            except (json.JSONDecodeError, ValueError):
                # Malformed tool call → never trust it, ask instead
                return [{"tool": "ask_clarification",
                         "args": {"question": "Inona marina no tianao holazaina?"}}]
            out.append({"tool": c.function.name, "args": args})
        return out
    content = (msg.content or "").strip()
    if content and len(content) <= 200:
        return [{"tool": "ask_clarification", "args": {"question": content}}]
    if content and len(content) > 200:
        return [{"tool": "ask_clarification", "args": {"question": "Tsy re tsara. Avereno azafady."}}]
    return []


# ---------------------------------------------------------------- ear 1
EAR1_LITERAL_SYSTEM = (
    "Transcris cette note vocale d'un commerçant malgache MOT À MOT (malgache, français ou mélange). "
    "IMPORTANT: garde les nombres TELS QUELS, en mots. Écris 'roa arivo' et non '2000', "
    "écris 'telo kapoaka' et non '3 kapoaka'. Écris uniquement la transcription, rien d'autre."
)


def _spark_ear1_literal(audio: bytes, mime: str, keywords: list[str], hint_text: str | None = None) -> dict:
    if hint_text:
        return {"text": hint_text, "confidence": None, "source": "ear1-hint"}
    try:
        wav = to_wav(audio)
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        return {"text": "", "confidence": None, "source": "ear1-spark", "error": f"to_wav: {type(e).__name__}"}
    if _is_silent(wav):
        return {"text": "", "confidence": None, "source": "ear1-spark", "error": "mangina (silent audio)"}
    client = _client(config.BACKEND == "open")
    b64 = base64.b64encode(wav).decode()
    system = EAR1_LITERAL_SYSTEM
    if keywords:
        system += "\nVocabulaire possible : " + ", ".join(keywords[:30])
    try:
        resp = client.chat.completions.create(
            max_tokens=4000,
            model=config.OPEN_WEIGHTS_MODEL if config.BACKEND == "open" else getattr(config, "MODEL_EAR1", config.MODEL_EAR2),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": b64, "format": "wav"}}]},
            ],
        )
    except Exception as e:
        return {"text": "", "confidence": None, "source": "ear1-spark", "error": type(e).__name__}
    text = (resp.choices[0].message.content or "").strip()
    if _looks_like_meta(text) or _is_silence_token(text):
        return {"text": "", "confidence": None, "source": "ear1-spark", "rejected": text or "empty"}
    return {"text": text, "confidence": None, "source": "ear1-spark-literal"}


def _whisper_transcribe(audio: bytes, mime: str, keywords: list[str], hint_text: str | None = None) -> dict:
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
    if _is_silent(wav):
        return {"text": "", "confidence": None, "source": "ear1-whisper", "error": "mangina (silent audio)"}
    file_obj = io.BytesIO(wav)
    file_obj.name = "audio.wav"
    kwargs = {"model": getattr(config, "WHISPER_MODEL", "whisper-v3"), "file": file_obj}
    if keywords:
        kwargs["prompt"] = ", ".join(keywords[:30])
    try:
        resp = client.audio.transcriptions.create(**kwargs)
        text = (getattr(resp, "text", "") or "").strip()
        if _looks_like_meta(text) or _is_silence_token(text):
            return {"text": "", "confidence": None, "source": "ear1-whisper", "rejected": text or "empty"}
        return {"text": text, "confidence": None, "source": "ear1-whisper"}
    except Exception as e:
        return {"text": "", "confidence": None, "source": "ear1-whisper", "error": type(e).__name__}


def voice_transcribe(audio: bytes, keywords: list[str] | None = None, language_bias: list[str] | None = None) -> dict:
    """Ear 1 real: Muse Voice Transcribe WebSocket (post-hackathon). Never raises."""
    return {"text": "", "confidence": None, "source": "voice-transcribe-not-wired"}


def transcribe(audio: bytes, mime: str, keywords: list[str], hint_text: str | None = None) -> dict:
    """Ear 1. Mode controlled by config.EAR1_MODE."""
    if config.BACKEND == "mock" or not audio:
        return {"text": hint_text or "", "confidence": 1.0, "source": "mock"}
    mode = getattr(config, "EAR1_MODE", "spark_literal")
    if mode == "off":
        return {"text": "", "confidence": None, "source": "ear1-off"}
    if mode == "whisper":
        return _whisper_transcribe(audio, mime, keywords, hint_text)
    if mode == "voice_transcribe":
        return voice_transcribe(audio, keywords, getattr(config, "VT_LANGUAGE_BIAS", []))
    return _spark_ear1_literal(audio, mime, keywords, hint_text)


# ---------------------------------------------------------------- ear 2
def listen_with_spark(audio: bytes, mime: str, lexicon_prompt: str, hint_text: str | None = None) -> dict:
    if config.BACKEND == "mock" or not audio:
        return {"text": hint_text or "", "confidence": 1.0, "source": "mock"}
    try:
        wav = to_wav(audio)
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        return {"text": "", "confidence": None, "source": "spark-audio", "error": f"to_wav: {type(e).__name__}"}
    if _is_silent(wav):
        return {"text": "", "confidence": None, "source": "spark-audio", "error": "mangina (silent audio)"}
    client = _client(config.BACKEND == "open")
    b64 = base64.b64encode(wav).decode()
    try:
        resp = client.chat.completions.create(
            max_tokens=4000,
            model=config.OPEN_WEIGHTS_MODEL if config.BACKEND == "open" else config.MODEL_EAR2,
            messages=[
                {"role": "system", "content": "Transcris mot à mot cette note vocale d'un commerçant malgache "
                                              "(malgache, français ou mélange). Écris les nombres en chiffres.\n" + lexicon_prompt},
                {"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": b64, "format": "wav"}}]},
            ],
        )
    except Exception as e:
        return {"text": "", "confidence": None, "source": "spark-audio", "error": type(e).__name__}
    prompt_tokens = getattr(resp.usage, "prompt_tokens", None) if resp.usage else None
    dropped = prompt_tokens is not None and prompt_tokens < AUDIO_MIN_TOKENS + len(lexicon_prompt) // 4
    text = (resp.choices[0].message.content or "").strip()
    if _looks_like_meta(text) or _is_silence_token(text):
        return {"text": "", "confidence": None, "source": "spark-audio", "rejected": text or "empty",
                "prompt_tokens": prompt_tokens}
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
    """Returns tool calls. tool_choice="auto": prose reply becomes a clarification."""
    if config.BACKEND == "mock":
        return mock_plan(text, context)
    client = _client(config.BACKEND == "open")
    try:
        resp = client.chat.completions.create(
            model=config.OPEN_WEIGHTS_MODEL if config.BACKEND == "open" else config.MODEL_PLANNER,
            messages=[
                {"role": "system", "content": PLANNER_SYSTEM.format(**context)},
                {"role": "user", "content": text},
            ],
            tools=TOOLS,
            tool_choice="auto",
            max_tokens=4000,
        )
    except Exception as e:
        return [{"tool": "ask_clarification",
                 "args": {"question": f"Tsy afaka nifandray tamin'ny API ({type(e).__name__}). Andramo indray azafady."}}]
    return _tool_calls(resp)


# ---------------------------------------------------------------- vision
def read_notebook(image: bytes, mime: str, context: dict) -> list[dict]:
    if config.BACKEND == "mock":
        return []
    client = _client()
    b64 = base64.b64encode(image).decode()
    try:
        resp = client.chat.completions.create(
            model=config.MODEL_VISION,
            messages=[
                {"role": "system", "content": "Lis cette page de cahier de dettes. Pour chaque client et montant dû, "
                                              "appelle record_debt. Si une ligne est illisible, appelle ask_clarification.\n"
                                              + PLANNER_SYSTEM.format(**context)},
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]},
            ],
            tools=TOOLS,
            tool_choice="auto",
            max_tokens=4000,
        )
    except Exception:
        return []
    return _tool_calls(resp)


# ---------------------------------------------------------------- offline mock planner
_UNIT = r"(?:kapoaka|litatra|litre|litres|pièces?|pieces?|kg|kilos?)"
_PAY_VERBS_SALE = r"(?:nandoa|payé|paye|donné|donne|avance)"

_LOSS_RE    = re.compile(r"(?<!\w)(perte|perdu|perdue|perdues|simba|very|abîmée?s?|gâtée?s?|jetée?s?)(?!\w)|retirer du stock", re.I)
_RESTOCK_RE = re.compile(r"(?<!\w)(tonga|réception|reception|réçu|recu|livraison|nampiditra|arrivage)(?!\w)", re.I)
_BORROW_RE  = re.compile(r"(?<!\w)(nindrana|emprunte|emprunté|empruntée?)(?!\w)", re.I)
_PAY_RE     = re.compile(r"(?<!\w)(nanefa|remboursé|rembourse)(?!\w)|payé\s+sa\s+dette|nandoa\s+(?:ny\s+)?trosa(?:ny)?", re.I)
_CREDIT_RE  = re.compile(r"(?<!\w)(crédit|credit)(?!\s+daholo)(?!\w)", re.I)
_NOTPAID_RE = re.compile(r"tsy\s+nandoa|mbola\s+trosa|crédit\s+daholo|à\s+crédit|a\s+crédit|à\s+credit", re.I)


def _first_number(t: str):
    m = re.search(r"(?<![\d\w])(\d+)(?![\d\w])", t)
    return int(m.group(1)) if m else None


def _find_amount_sale(t: str):
    m = re.search(r"(?<![\d\w])(\d+)\s*(?:ar|ariary)(?!\w)", t)
    if m:
        return int(m.group(1))
    m = re.search(_PAY_VERBS_SALE + r"\s+(\d+)", t)
    if m:
        return int(m.group(1))
    return None


def _find_qty(t: str, product: str):
    p = re.escape(product)
    for pat in (
        rf"(?<![\d\w])(\d+)\s*{_UNIT}?\s*(?:de\s+|d')?{p}(?!\w)",
        rf"(?<!\w){p}(?!\w)[,\s:]+{_UNIT}\s*(\d+)",
        rf"(?<!\w){p}(?!\w)[,\s:]*(\d+)",
    ):
        m = re.search(pat, t)
        if m:
            return int(m.group(1))
    return None


def mock_plan(text: str, ctx: dict) -> list[dict]:
    t = to_digits(text or "").lower()
    customer = next((c for c in ctx["customer_list"]
                     if re.search(r"(?<!\w)" + re.escape(c.lower()) + r"(?!\w)", t)), None)
    items = []
    for p in ctx["product_list"]:
        q = _find_qty(t, p.lower())
        if q and q > 0:
            items.append({"product": p, "qty": q})

    if _LOSS_RE.search(t) and items:
        return [{"tool": "update_stock", "args": {"product": items[0]["product"], "delta": -items[0]["qty"], "reason": "loss"}}]
    if _RESTOCK_RE.search(t) and items:
        return [{"tool": "update_stock", "args": {"product": items[0]["product"], "delta": items[0]["qty"], "reason": "restock"}}]
    if not items and customer and _PAY_RE.search(t):
        amount = _first_number(t)
        if amount:
            return [{"tool": "record_payment", "args": {"customer": customer, "amount": amount}}]
    if not items and customer and _BORROW_RE.search(t):
        amount = _first_number(t)
        if amount:
            return [{"tool": "record_debt", "args": {"customer": customer, "amount": amount}}]
    if not items and customer and _CREDIT_RE.search(t) and not _NOTPAID_RE.search(t):
        amount = _first_number(t)
        if amount:
            return [{"tool": "record_debt", "args": {"customer": customer, "amount": amount}}]
    if items:
        paid = 0 if _NOTPAID_RE.search(t) else _find_amount_sale(t)
        return [{"tool": "record_sale", "args": {"customer": customer, "items": items, "paid": paid}}]
    return [{"tool": "ask_clarification", "args": {"question": "Inona no entana nolafosina ? (Quel produit ?)"}}]


# ---------------------------------------------------------------- spoken confirmation
def spoken_confirmation(verdict: dict) -> str:
    """Natural Malagasy sentence for TTS. Empty when there is nothing to say."""
    if verdict.get("level") == "clarify":
        return verdict.get("question", "")
    lines = verdict.get("lines", [])
    if not lines:
        return ""
    parts = []
    for ln in lines:
        if ln["kind"] == "sale":
            qty = int(ln["qty"]) if float(ln["qty"]).is_integer() else ln["qty"]
            cust = f"ho an'i {ln.get('customer')}" if ln.get("customer") else "ho an'ny client"
            parts.append(f"nividy {qty} {ln['unit']} {ln['product']} {cust}")
        elif ln["kind"] == "debt":
            parts.append(f"manana trosa {ln['amount']} Ar")
        elif ln["kind"] == "payment":
            parts.append(f"nandoa {ln['amount']} Ar")
        elif ln["kind"] == "stock":
            direction = "nitombo" if ln["delta"] > 0 else "nihena"
            parts.append(f"{direction} {abs(ln['delta'])} ny tahirin-tsakafo {ln['product']}")
    resumé = " ary ".join(parts)
    if verdict.get("total"):
        return f"Soratana ve fa {resumé}? Totaly {verdict['total']} Ar."
    return f"Soratana ve fa {resumé}?"