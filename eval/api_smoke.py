"""Quick test of the Meta Model API — independent of Kahie's code.

    python eval/api_smoke.py                      # key, models, text chat, tool call
    python eval/api_smoke.py eval/audio/mg-001.m4a  # + Voice Transcribe and Spark audio on one recording

Reads MODEL_API_KEY / MODEL_API_BASE / model IDs from .env. Prints OK / FAIL for each step.
Cost: a few cents at most (4 small calls, +2 with audio).
"""
import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
KEY = os.getenv("MODEL_API_KEY", "")
BASE = os.getenv("MODEL_API_BASE", "https://api.meta.ai/v1").rstrip("/")
PLANNER = os.getenv("MODEL_PLANNER", "muse-spark-1.3")
EAR2 = os.getenv("MODEL_EAR2", "muse-spark-1.2")
TRANSCRIBE = os.getenv("MODEL_TRANSCRIBE", "muse-voice-transcribe-1.0")

results = []


def step(name):
    def deco(fn):
        def run(*a):
            t = time.perf_counter()
            try:
                info = fn(*a)
                results.append((name, True))
                print(f"OK    {name}  ({(time.perf_counter() - t) * 1000:.0f} ms)  {info or ''}")
                return True
            except Exception as e:
                results.append((name, False))
                print(f"FAIL  {name}  →  {type(e).__name__}: {str(e)[:300]}")
                return False
        return run
    return deco


def client():
    from openai import OpenAI
    return OpenAI(base_url=BASE, api_key=KEY)


@step("1. API key in .env")
def check_key():
    if not KEY:
        raise RuntimeError("MODEL_API_KEY is empty in .env")
    return f"{KEY[:4]}…{KEY[-4:]} ({len(KEY)} chars) · base {BASE}"


@step("2. models.list")
def check_models():
    ids = sorted(m.id for m in client().models.list())
    print("      models:", ", ".join(ids))
    missing = [m for m in (PLANNER, EAR2, TRANSCRIBE) if m not in ids]
    return f"{len(ids)} models" + (f" · ⚠ not listed: {missing} — fix the IDs in .env" if missing else " · all .env IDs listed")


@step("3. text chat (planner model)")
def check_chat():
    r = client().chat.completions.create(model=PLANNER, max_tokens=1000, messages=[
        {"role": "user", "content": "Réponds en un mot : quelle est la capitale de Madagascar ?"}])
    return f"“{(r.choices[0].message.content or '').strip()}” · tokens {r.usage.total_tokens if r.usage else '?'}"


@step("4. tool call (record_sale)")
def check_tools():
    tools = json.loads((ROOT / "backend" / "app" / "agent" / "tools.json").read_text(encoding="utf-8"))
    r = client().chat.completions.create(model=PLANNER, tools=tools, tool_choice="auto", max_tokens=4000, messages=[
        {"role": "system", "content": "Tu es l'agent comptable d'une boutique à Madagascar. Produits : vary (kapoaka, 1000 Ar). "
                                      "Clients : Rabe. Appelle les outils nécessaires."},
        {"role": "user", "content": "Rabe naka 3 kapoaka vary, nandoa 2000 Ar"}])
    calls = r.choices[0].message.tool_calls or []
    if not calls:
        raise RuntimeError(f"no tool call returned; text reply: {(r.choices[0].message.content or '')[:200]!r}")
    return " ".join(f"{c.function.name}({c.function.arguments})" for c in calls)


def to_wav(path: Path) -> bytes:
    if path.suffix.lower() == ".wav":
        return path.read_bytes()
    out = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-ac", "1", "-ar", "16000", "-f", "wav", "pipe:1"],
                         capture_output=True, check=True)
    return out.stdout


@step("5. Voice Transcribe (batch, auto-detect)")
def check_vt(wav: bytes):
    import httpx
    req = {"model": TRANSCRIBE, "audioEncoding": "WAV", "mode": "PUSH_TO_TALK", "keywords": ["vary", "kapoaka", "Rabe", "arivo"]}
    r = httpx.post(BASE + "/asr/transcribe", timeout=60,
                   headers={"Authorization": f"Bearer {KEY}", "Accept": "application/json"},
                   files={"request": (None, json.dumps(req), "application/json"), "audio": ("note.wav", wav, "audio/wav")})
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    b = r.json()
    return f"“{b.get('transcript')}” · {b.get('audioDurationMs')} ms of audio"


@step("6. Spark audio (ear 2)")
def check_spark_audio(wav: bytes):
    r = client().chat.completions.create(model=EAR2, max_tokens=4000, messages=[
        {"role": "system", "content": "Transcris mot à mot cette note vocale (malgache, français ou mélange). Écris les nombres en chiffres."},
        {"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": base64.b64encode(wav).decode(), "format": "wav"}}]}])
    pt = r.usage.prompt_tokens if r.usage else None
    warn = " · ⚠ very few prompt tokens: audio may have been dropped" if pt is not None and pt < 120 else ""
    return f"“{(r.choices[0].message.content or '').strip()}” · prompt_tokens {pt}{warn}"


if __name__ == "__main__":
    if check_key() and check_models():
        check_chat()
        check_tools()
        if len(sys.argv) > 1:
            p = Path(sys.argv[1])
            try:
                wav = to_wav(p)
                print(f"      audio: {p.name} → {len(wav) // 1024} KB WAV")
                check_vt(wav)
                check_spark_audio(wav)
            except FileNotFoundError:
                print("FAIL  audio  →  file not found, or ffmpeg not installed (ffmpeg -version)")
            except subprocess.CalledProcessError as e:
                print(f"FAIL  audio  →  ffmpeg could not read {p.name}: {e.stderr.decode(errors='ignore')[:200]}")
    ok = sum(r for _, r in results)
    print(f"\n{ok}/{len(results)} steps OK")
