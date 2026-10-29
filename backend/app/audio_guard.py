"""Guards against hallucinated transcripts.

An "ear" must return what was SAID, nothing else. Three checks:
  1. Before any API call: the WAV must be long enough and not silent (saves credits, avoids
     models inventing words for an empty recording).
  2. After the call: a reply that talks ABOUT the audio ("Aucun audio reçu…", "Send the file…")
     or repeats our own prompt ("vocabulaire : vary, siramamy…") is not a transcript.
  3. A transcript with far more words than the recording could hold is not plausible.
A rejected reading becomes an empty ear with a reason in the trace; if both ears are empty the
agent asks the shopkeeper to repeat. A rejected text is never offered as a choice on the card.
"""
import io
import re
import wave
from array import array

MIN_SECONDS = 0.4
SILENCE_RMS = 60       # int16 RMS; a phone mic in a quiet room is usually well above this
SILENCE_PEAK = 600
WORDS_PER_SECOND = 4.5  # fast speech is ~3 words/s; generous margin
EXTRA_WORDS = 4


def inspect(wav: bytes) -> dict:
    """{"ok", "seconds", "rms", "reason"} for a mono 16-bit WAV."""
    try:
        with wave.open(io.BytesIO(wav)) as w:
            frames, rate, width = w.readframes(w.getnframes()), w.getframerate(), w.getsampwidth()
    except (wave.Error, EOFError):
        return {"ok": False, "seconds": 0.0, "rms": 0, "reason": "feo tsy voavaky (WAV invalide)"}
    seconds = len(frames) / (rate * width) if rate and width else 0.0
    if seconds < MIN_SECONDS:
        return {"ok": False, "seconds": seconds, "rms": 0, "reason": f"feo fohy loatra ({seconds:.1f} s)"}
    if width != 2:
        return {"ok": True, "seconds": seconds, "rms": None, "reason": None}
    samples = array("h")
    samples.frombytes(frames[: len(frames) // 2 * 2])
    step = max(1, len(samples) // 50_000)  # sample at most ~50k points: fast on long notes
    pts = samples[::step]
    rms = int((sum(s * s for s in pts) / max(1, len(pts))) ** 0.5)
    peak = max((abs(s) for s in pts), default=0)
    if rms < SILENCE_RMS and peak < SILENCE_PEAK:
        return {"ok": False, "seconds": seconds, "rms": rms, "reason": "tsy nisy feo re (mangina ilay firaketana)"}
    return {"ok": True, "seconds": seconds, "rms": rms, "reason": None}


# Replies that describe the audio instead of transcribing it (French, English, Malagasy).
_META = re.compile(
    r"aucun(e)?\s+(audio|note|fichier|son)|pas\s+(d'|de\s+)(audio|note vocale|fichier)|je\s+ne\s+peux\s+pas|"
    r"envoie[sz]?\s+(le|la|moi)|renvoy|merci\s+de|transcri(re|ption|s)\b|note\s+vocale|fichier\s+audio|"
    r"no\s+audio|cannot\s+(hear|transcribe)|please\s+(send|provide|upload)|i\s+(can't|cannot|don't)|"
    r"vocabulaire|vocabulary|tsy\s+misy\s+feo|alefaso\s+ny\s+feo",
    re.I)


def is_meta_reply(text: str) -> bool:
    return bool(_META.search(text or ""))


def plausible(text: str, seconds: float) -> bool:
    words = len(re.findall(r"[^\W_]+", text or ""))
    return words <= WORDS_PER_SECOND * max(seconds, 0) + EXTRA_WORDS


def clean(result: dict, seconds: float | None) -> dict:
    """Empty the text of an ear result that is not a plausible transcript; keep the reason."""
    text = (result.get("text") or "").strip()
    if not text:
        return result
    reason = None
    if is_meta_reply(text):
        reason = "valiny tsy transcription (nolavina)"
    elif seconds is not None and not plausible(text, seconds):
        reason = f"teny be loatra ho an'ny feo {seconds:.1f} s (nolavina)"
    if reason:
        return {**result, "text": "", "rejected": text[:200], "error": reason}
    return result
