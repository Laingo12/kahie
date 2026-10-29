"""Two-way voice: the shopkeeper ANSWERS Kahie out loud.

After the card is read aloud ("… Marina ve?") or a question is asked ("Firy marina: 2000 sa 3000?"),
the app listens once more. This module decides what that short answer means:
  - on a card to confirm: yes → write, no → cancel, anything else → ask "Eny sa tsia?"
  - on a question with two readings: the answer that names one reading's number or word picks it
  - on any other question: the answer is added to the original note and the note is planned again
Nothing is written without a clear "yes" (or the ✓ tap). Pure functions, no model call.
"""
import re

from ..numbers_mg import numbers_in, to_digits

YES = {"eny", "ie", "ia", "marina", "tsara", "ekena", "ekeko", "ok", "okey", "okay", "oui", "ouais", "voui",
       "d'accord", "dacord", "daccord", "exact", "izay", "izay izy", "izay ihany", "soraty", "ataovy", "vita"}
NO = {"tsia", "tsy", "aza", "diso", "non", "nan", "tsy marina", "tsy izy", "fafao", "foano", "foanana",
      "ajanony", "annule", "annuler", "faux", "aza soratana"}


def _words(text: str) -> list[str]:
    return re.findall(r"[^\W\d_]+(?:'[^\W\d_]+)?", (text or "").lower())


def yes_no(text: str) -> bool | None:
    """True = yes, False = no, None = not a clear yes/no. "Tsy marina" is no, even though it contains "marina"."""
    t = " ".join(_words(text))
    if not t:
        return None
    # "… Marina ve?" is Kahie's OWN question (e.g. the speaker's echo picked up by the mic), not an answer.
    if re.search(r"(?<!\w)ve(?!\w)", t):
        return None
    if len(t.split()) > 8:   # a yes/no is short; a long sentence here is noise or the card read back
        return None
    for phrase in sorted(NO | YES, key=len, reverse=True):   # longest first: "tsy marina" before "marina"
        if re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", t):
            return phrase in YES
    return None


def pick_option(options: list[dict], answer: str) -> str | None:
    """Which reading did the shopkeeper confirm? Match the numbers first, then the words that differ."""
    if not options:
        return None
    said_nums = numbers_in(to_digits(answer or ""))
    if said_nums:
        hits = [o["id"] for o in options if said_nums & numbers_in(o["text"])
                and not all(said_nums & numbers_in(x["text"]) for x in options)]
        if len(hits) == 1:
            return hits[0]
    said = set(_words(answer))
    words = {o["id"]: set(_words(o["text"])) for o in options}
    common = set.intersection(*words.values()) if words else set()
    hits = [oid for oid, ws in words.items() if (ws - common) & said]
    return hits[0] if len(hits) == 1 else None


def merge_answer(transcript: str, answer: str) -> str:
    """"Rabe naka siramamy" + "roa kilao" → "Rabe naka siramamy. roa kilao" (planned again as one note)."""
    transcript, answer = (transcript or "").strip(), (answer or "").strip()
    if not transcript:
        return answer
    return f"{transcript.rstrip('.')}. {answer}" if answer else transcript
