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
BACKEND = os.getenv("KAHIE_BACKEND", "api" if MODEL_API_KEY else "mock")

# Evaluation switches (configs A–D): turn ear 2 or the lexicon off to measure what each one adds.
EAR2_ON = os.getenv("KAHIE_EAR2", "on") == "on"
LEXICON_ON = os.getenv("KAHIE_LEXICON", "on") == "on"

DB_PATH =Path(os.getenv("KAHIE_DB", ROOT / "data" / "kahie.db"))

# Verifier thresholds
PRICE_DEVIATION = float(os.getenv("PRICE_DEVIATION", "0.5"))   # ±50 % from usual price → reinforced confirmation

# Ear 1 mode: "spark_literal" | "whisper" | "voice_transcribe" | "off"
EAR1_MODE = os.getenv("EAR1_MODE", "spark_literal")

# Optional: separate model for ear 1 (defaults to ear 2's model)
MODEL_EAR1 = os.getenv("MODEL_EAR1", os.getenv("MODEL_EAR2", "muse-spark-1.2"))

# Optional: Whisper via Fireworks (for EAR1_MODE=whisper)
FIREWORKS_API_KEY = os.getenv("FIREWORKS_API_KEY", "")
WHISPER_BASE_URL = os.getenv("WHISPER_BASE_URL", "https://audio-prod.us-virginia-1.direct.fireworks.ai/v1")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "whisper-v3")
