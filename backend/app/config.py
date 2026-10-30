"""Settings, read from the environment (.env). The API key never lives in the repo."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

MODEL_API_KEY = os.getenv("MODEL_API_KEY", "")
MODEL_API_BASE = os.getenv("MODEL_API_BASE", "https://api.meta.ai/v1")

# Model names — check the exact IDs with client.models.list() on day 1.
MODEL_PLANNER = os.getenv("MODEL_PLANNER", "muse-spark-1.3")
# Ear 2 uses 1.2: Meta docs say audio understanding on 1.3 "is currently not fully supported".
# Avoid the -contributor tiers for audio: input_audio is silently dropped there (meta-model-cookbook issue #59).
MODEL_EAR2 = os.getenv("MODEL_EAR2", "muse-spark-1.2")
MODEL_VISION = os.getenv("MODEL_VISION", "muse-spark-1.3")
MODEL_TRANSCRIBE = os.getenv("MODEL_TRANSCRIBE", "muse-voice-transcribe-1.0")

# Config D: same agent on open weights through a partner (Baseten / Modal), OpenAI-compatible.
OPEN_WEIGHTS_BASE = os.getenv("OPEN_WEIGHTS_BASE", "")
OPEN_WEIGHTS_KEY = os.getenv("OPEN_WEIGHTS_KEY", "")
OPEN_WEIGHTS_MODEL = os.getenv("OPEN_WEIGHTS_MODEL", "meta-models/muse-spark-open-1026")


# "api" = Meta Model API, "open" = open weights, "mock" = offline rules (tests and demo without key)
# Empty KAHIE_BACKEND (as shipped in .env.example) → auto-detect from MODEL_API_KEY.
BACKEND = os.getenv("KAHIE_BACKEND") or ("api" if MODEL_API_KEY else "mock")

# Evaluation switches (configs A–D): turn ear 2 or the lexicon off to measure what each one adds.
EAR2_ON = os.getenv("KAHIE_EAR2", "on") == "on"
LEXICON_ON = os.getenv("KAHIE_LEXICON", "on") == "on"

DB_PATH =Path(os.getenv("KAHIE_DB", ROOT / "data" / "kahie.db"))

# Verifier thresholds
# Debug: keep every received recording in data/debug/ (never committed) to replay it or run
# eval/api_smoke.py on it. Off by default — recordings are personal data.
DEBUG_AUDIO = os.getenv("KAHIE_DEBUG_AUDIO", "off") == "on"

PRICE_DEVIATION = float(os.getenv("PRICE_DEVIATION", "0.5"))   # ±50 % from usual price → reinforced confirmation

# Ear 1 mode: "voice_transcribe" (default) | "spark_literal" | "whisper" | "off"
EAR1_MODE = os.getenv("EAR1_MODE", "voice_transcribe")

# Voice Transcribe language bias: empty = auto-detect (Malagasy is not a bias language);
# "French" biases toward French. Comma-separated language names, e.g. "French,English".
VT_LANGUAGE_BIAS = [x.strip() for x in os.getenv("VT_LANGUAGE_BIAS", "").split(",") if x.strip()]

# Optional: separate model for ear 1 (defaults to ear 2's model)
MODEL_EAR1 = os.getenv("MODEL_EAR1", os.getenv("MODEL_EAR2", "muse-spark-1.2"))

# Optional: Whisper via Fireworks (for EAR1_MODE=whisper)
FIREWORKS_API_KEY = os.getenv("FIREWORKS_API_KEY", "")
WHISPER_BASE_URL = os.getenv("WHISPER_BASE_URL", "https://audio-prod.us-virginia-1.direct.fireworks.ai/v1")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "whisper-v3")

# Optional: server voice for Kahie's spoken replies (Google Gemini TTS, not a Meta model).
# Empty key = off → the phone's own voice speaks (speechSynthesis). Never writes to the ledger.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_TTS_MODEL = os.getenv("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts")
GEMINI_TTS_VOICE = os.getenv("GEMINI_TTS_VOICE", "Aoede")

# Planned, default off: SAM 3.1 notebook line segmentation before Spark vision (see README roadmap).
SAM_ENABLED = os.getenv("KAHIE_SAM", "off") == "on"
SAM_API_KEY = os.getenv("SAM_API_KEY", "") or MODEL_API_KEY
SAM_MODEL = os.getenv("SAM_MODEL", "sam-3.1")
