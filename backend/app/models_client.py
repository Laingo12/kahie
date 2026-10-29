"""Thin wrapper around the Meta Model API (OpenAI-compatible) with an offline mock.

Ear 1 modes (config.EAR1_MODE, default "voice_transcribe"):
  - "voice_transcribe": Muse Voice Transcribe, batch endpoint POST /v1/asr/transcribe (independent ASR model).
  - "spark_literal": Spark audio with a LITERAL prompt that keeps numbers as words (same model as ear 2).
  - "whisper": Whisper large-v3 via Fireworks (not a Meta model; optional comparison only).
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


VT_MAX_KEYWORDS = 50  # docs give no limit; keep the list short and most-used first


def voice_transcribe(audio: bytes, keywords: list[str] | None = None, language_bias: list[str] | None = None,
                     timeout: float = 60.0) -> dict:
    """Ear 1 — Muse Voice Transcribe, batch endpoint (one request per finished note).

    POST {MODEL_API_BASE}/asr/transcribe, multipart/form-data:
      part "request" (application/json): {"model", "audioEncoding": "WAV", "mode": "PUSH_TO_TALK",
                                          "keywords"?, "languageBias"?}
      part "audio"   (audio/wav): mono 16-bit PCM WAV, 16 kHz
    Response: {"sessionId", "transcript", "audioDurationMs", "turns": []}
    Ref: dev.meta.ai/docs/api-reference/voice/transcribe and …/voice/schemas.

    Malagasy is not a bias language: language_bias=None lets the model detect the language;
    ["French"] biases toward French. day1_test.py compares both.
    Never raises: on failure returns text "" + "error", and the pipeline falls back to the other ear.
    """
    import httpx

    try:
        wav = to_wav(audio, rate=16000)
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        return {"text": "", "confidence": None, "source": "voice-transcribe", "error": f"to_wav: {type(e).__name__}"}
    req = {"model": config.MODEL_TRANSCRIBE, "audioEncoding": "WAV", "mode": "PUSH_TO_TALK"}
    if keywords:
        req["keywords"] = list(dict.fromkeys(k for k in keywords if k))[:VT_MAX_KEYWORDS]
    if language_bias:
        req["languageBias"] = language_bias
    try:
        r = httpx.post(
            config.MODEL_API_BASE.rstrip("/") + "/asr/transcribe",
            headers={"Authorization": f"Bearer {config.MODEL_API_KEY}", "Accept": "application/json"},
            files={"request": (None, json.dumps(req), "application/json"),
                   "audio": ("note.wav", wav, "audio/wav")},
            timeout=timeout,
        )
    except httpx.HTTPError as e:
        return {"text": "", "confidence": None, "source": "voice-transcribe", "error": type(e).__name__}
    if r.status_code != 200:
        # 400 bad WAV / too long · 413 too big · 429 rate limit · 500 server — message is client-safe
        try:
            msg = r.json().get("message") or r.json().get("error", {}).get("message", "")
        except ValueError:
            msg = r.text[:200]
        return {"text": "", "confidence": None, "source": "voice-transcribe", "error": f"HTTP {r.status_code}: {msg}"}
    body = r.json()
    return {"text": (body.get("transcript") or "").strip(), "confidence": None, "source": "voice-transcribe",
            "session_id": body.get("sessionId"), "audio_ms": body.get("audioDurationMs")}


def transcribe(audio: bytes, mime: str, keywords: list[str], hint_text: str | None = None) -> dict:
    """Ear 1. Mode controlled by config.EAR1_MODE (default "voice_transcribe")."""
    if config.BACKEND == "mock" or not audio:
        return {"text": hint_text or "", "confidence": 1.0, "source": "mock"}
    mode = getattr(config, "EAR1_MODE", "voice_transcribe")
    if mode == "off":
        return {"text": "", "confidence": None, "source": "ear1-off"}
    if mode == "whisper":
        return _whisper_transcribe(audio, mime, keywords, hint_text)
    if mode == "voice_transcribe":
        return voice_transcribe(audio, keywords, config.VT_LANGUAGE_BIAS or None)
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
    except Exception as e:  # API error, rate limit, network: this ear stays silent, the other one can still work
        return {"text": "", "confidence": None, "source": "spark-audio", "error": f"{type(e).__name__}: {str(e)[:160]}"}
    prompt_tokens = getattr(resp.usage, "prompt_tokens", None) if resp.usage else None
    dropped = prompt_tokens is not None and prompt_tokens < AUDIO_MIN_TOKENS + len(lexicon_prompt) // 4
    text = "" if dropped else (resp.choices[0].message.content or "").strip()
    return {"text": text, "confidence": None, "source": "spark-audio", "dropped": dropped, "prompt_tokens": prompt_tokens}


# ---------------------------------------------------------------- planner
PLANNER_SYSTEM = """Tu es l'agent comptable d'une petite boutique à Madagascar.
À partir de la note (malgache, français ou mélange), appelle les outils nécessaires.
Montants en Ariary (Ar). N'invente jamais un produit, un client ou un montant :
si une information manque, appelle ask_clarification.
Si le commerçant POSE UNE QUESTION sur ses données (« iza no manana trosa ? », « firy ny trosan'i Rabe ? »,
« ohatrinona ny varotra androany ? », « firy ny vary sisa ? »), appelle ask_ledger et rien d'autre :
la réponse sera calculée depuis la base.
{lexicon}
Produits connus : {products}
Clients connus : {customers}"""


PLANNER_MAX_TOKENS = 4000  # Spark reasons before answering: a small limit leaves an empty reply


def _tool_calls(resp) -> list[dict]:
    """Tool calls from a chat response. The Meta API only accepts tool_choice="auto", so the model may
    answer in plain text instead of calling a tool: that text becomes a question to the merchant,
    never a guessed write. Arguments that are not valid JSON are dropped the same way."""
    msg = resp.choices[0].message
    calls = []
    for c in msg.tool_calls or []:
        try:
            calls.append({"tool": c.function.name, "args": json.loads(c.function.arguments or "{}")})
        except json.JSONDecodeError:
            continue
    if not calls:
        text = (msg.content or "").strip()
        calls = [{"tool": "ask_clarification",
                  "args": {"question": text[:200] or "Tsy azoko tsara. Avereno azafady ?"}}]
    return calls


def plan(text: str, context: dict) -> list[dict]:
    """Returns a list of tool calls: [{"tool": name, "args": {...}}]."""
    if config.BACKEND == "mock":
        return mock_plan(text, context)
    client = _client(config.BACKEND == "open")
    try:
        resp = _planner_call(client, text, context)
    except Exception as e:  # network, rate limit, API error: ask to retry instead of a 500
        return [{"tool": "ask_clarification",
                 "args": {"question": f"Tsy afaka niantso ny modely ({type(e).__name__}). Andramo indray."}}]
    return _tool_calls(resp)


def _planner_call(client, text: str, context: dict):
    return client.chat.completions.create(
        model=config.OPEN_WEIGHTS_MODEL if config.BACKEND == "open" else config.MODEL_PLANNER,
        max_tokens=PLANNER_MAX_TOKENS,
        messages=[
            {"role": "system", "content": PLANNER_SYSTEM.format(**context)
             + "\nRéponds UNIQUEMENT par des appels d'outils, jamais par du texte."},
            {"role": "user", "content": text},
        ],
        tools=TOOLS,
        tool_choice="auto",  # "required" is rejected by the Meta Model API (HTTP 400)
    )


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
        tool_choice="auto",
        max_tokens=PLANNER_MAX_TOKENS,
    )
    return _tool_calls(resp)


# ---------------------------------------------------------------- offline mock
_UNIT = r"(?:kapoaka|litatra|litre|litres|pièces?|pieces?|kg|kilos?)"

# Payment verbs (sale payment OR debt repayment). "avance" = deposit.
_PAY_VERBS_SALE = r"(?:nandoa|payé|paye|donné|donne|avance)"

# Classification regexes (case-insensitive, word-boundary)
_LOSS_RE    = re.compile(r"(?<!\w)(perte|perdu|perdue|perdues|simba|very|abîmée?s?|gâtée?s?|jetée?s?)(?!\w)|retirer du stock", re.I)
_RESTOCK_RE = re.compile(r"(?<!\w)(tonga|réception|reception|réçu|recu|livraison|nampiditra|arrivage)(?!\w)", re.I)
_BORROW_RE  = re.compile(r"(?<!\w)(nindrana|emprunte|emprunté|empruntée?)(?!\w)", re.I)
_PAY_RE     = re.compile(r"(?<!\w)(nanefa|remboursé|rembourse)(?!\w)|payé\s+sa\s+dette|nandoa\s+(?:ny\s+)?trosa(?:ny)?", re.I)
_CREDIT_RE  = re.compile(r"(?<!\w)(crédit|credit)(?!\s+daholo)(?!\w)", re.I)
_NOTPAID_RE = re.compile(r"tsy\s+nandoa|mbola\s+trosa|crédit\s+daholo|à\s+crédit|a\s+crédit|à\s+credit", re.I)


# Questions: the note STARTS with a question word ("Hery naka riz, combien déjà ?" is an incomplete sale, not a question).
_QUESTION_RE = re.compile(r"^\s*(iza|firy|ohatrinona|inona|aiza|mbola\s+misy|qui|combien|quel(?:le)?s?|est-ce|reste-t-il)(?!\w)", re.I)
_DEBT_WORD_RE = re.compile(r"trosa|dette|crédit|credit|tsy\s+(?:mbola\s+)?nandoa", re.I)
_AGED_RE = re.compile(r"(?<!\w)(ela|taloha|ancien(?:ne)?s?|depuis longtemps|7 andro|herinandro)(?!\w)", re.I)
_SALES_RE = re.compile(r"varotra|vente|lafo|nivarotra|vola\s+niditra|recette", re.I)
_LOW_RE = re.compile(r"ho\s+lany|lany|ambany|manquer|bientôt|épuis", re.I)
_STOCK_RE = re.compile(r"sisa|tahiry|stock|reste", re.I)


def mock_question(t: str, ctx: dict):
    """Offline stand-in for the planner's ask_ledger choice. None if the note is not a question."""
    if not _QUESTION_RE.search(t):
        return None
    customer = next((c for c in ctx["customer_list"] if re.search(r"(?<!\w)" + re.escape(c.lower()) + r"(?!\w)", t)), None)
    product = next((p for p in ctx["product_list"] if re.search(r"(?<!\w)" + re.escape(p.lower()) + r"(?!\w)", t)), None)
    period = ("week" if re.search(r"herinandro|semaine", t) else "month" if re.search(r"volana|mois", t) else "today")
    if _DEBT_WORD_RE.search(t):
        if customer:
            args = {"topic": "customer_balance", "customer": customer}
        elif _AGED_RE.search(t):
            args = {"topic": "aged_debts"}
        else:
            args = {"topic": "debts"}
    elif _SALES_RE.search(t):
        args = {"topic": "sales", "period": period}
    elif product and (_STOCK_RE.search(t) or t.startswith("firy")):
        args = {"topic": "stock", "product": product}
    elif _LOW_RE.search(t) or _STOCK_RE.search(t):
        args = {"topic": "low_stock"}
    else:
        args = {"topic": "unknown"}
    return [{"tool": "ask_ledger", "args": args}]


def _first_number(t: str):
    m = re.search(r"(?<![\d\w])(\d+)(?![\d\w])", t)
    return int(m.group(1)) if m else None


def _find_amount_sale(t: str):
    """For a sale: explicit '2000 ar/ariary' wins, then a payment verb + number. None = all paid."""
    m = re.search(r"(?<![\d\w])(\d+)\s*(?:ar|ariary)(?!\w)", t)
    if m:
        return int(m.group(1))
    m = re.search(_PAY_VERBS_SALE + r"\s+(\d+)", t)
    if m:
        return int(m.group(1))
    return None


def _find_qty(t: str, product: str):
    """qty of `product` in normalised text; handles both word orders and optional unit."""
    p = re.escape(product)
    for pat in (
        rf"(?<![\d\w])(\d+)\s*{_UNIT}?\s*(?:de\s+|d')?{p}(?!\w)",  # 3 kapoaka vary / 1 litre d'menaka
        rf"(?<!\w){p}(?!\w)[,\s:]+{_UNIT}\s*(\d+)",                  # vary, kapoaka 3
        rf"(?<!\w){p}(?!\w)[,\s:]*(\d+)",                            # vary 3 / mofo : 20
    ):
        m = re.search(pat, t)
        if m:
            return int(m.group(1))
    return None


def mock_plan(text: str, ctx: dict) -> list[dict]:
    """Rule-based planner so the app, tests and demo run without an API key.
    Expects normalised text (numbers already in digits, see pipeline.normalize_ear).
    Handles all 6 tools: record_sale, record_payment, record_debt, update_stock, ask_clarification, ask_ledger."""
    t = to_digits(text or "").lower()

    q = mock_question(t, ctx)
    if q:
        return q

    customer = next((c for c in ctx["customer_list"]
                     if re.search(r"(?<!\w)" + re.escape(c.lower()) + r"(?!\w)", t)), None)

    items = []
    for p in ctx["product_list"]:
        q = _find_qty(t, p.lower())
        if q and q > 0:
            items.append({"product": p, "qty": q})

    # 1. Loss (damaged, lost, removed from stock)
    if _LOSS_RE.search(t) and items:
        return [{"tool": "update_stock",
                 "args": {"product": items[0]["product"], "delta": -items[0]["qty"], "reason": "loss"}}]

    # 2. Restock (received, delivered)
    if _RESTOCK_RE.search(t) and items:
        return [{"tool": "update_stock",
                 "args": {"product": items[0]["product"], "delta": items[0]["qty"], "reason": "restock"}}]

    # 3. Debt repayment (customer + amount, no product line)
    if not items and customer and _PAY_RE.search(t):
        amount = _first_number(t)
        if amount:
            return [{"tool": "record_payment", "args": {"customer": customer, "amount": amount}}]

    # 4. Borrow (customer + amount, no product line)
    if not items and customer and _BORROW_RE.search(t):
        amount = _first_number(t)
        if amount:
            return [{"tool": "record_debt", "args": {"customer": customer, "amount": amount}}]

    # 5. "crédit X" (customer + amount, no product line, not "crédit daholo")
    if not items and customer and _CREDIT_RE.search(t) and not _NOTPAID_RE.search(t):
        amount = _first_number(t)
        if amount:
            return [{"tool": "record_debt", "args": {"customer": customer, "amount": amount}}]

    # 6. Sale (product line detected)
    if items:
        if _NOTPAID_RE.search(t):
            paid = 0
        else:
            paid = _find_amount_sale(t)   # None = all paid
        return [{"tool": "record_sale",
                 "args": {"customer": customer, "items": items, "paid": paid}}]

    # 7. Ask
    return [{"tool": "ask_clarification",
             "args": {"question": "Inona no entana nolafosina ? (Quel produit ?)"}}]