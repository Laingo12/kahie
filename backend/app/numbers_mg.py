"""Spoken numbers → digits, for Malagasy (and basic French), before the two ears are compared.

Why: one ear may write "roa arivo" and the other "2000". Without this step the reconciler
sees a disagreement on every note and asks a question for nothing.

Malagasy reading rules handled here:
  - units first, joined by "amby" / "ambin'ny":  roa amby telopolo = 32, iraika ambin'ny folo = 11
  - "sy" between larger parts:                     dimy amby roapolo sy zato = 125
  - big-first order also works:                    roa arivo sy dimanjato = 2500
  - a group before arivo/alina/hetsy/tapitrisa multiplies it: roa amby telopolo arivo = 32000
French: un … seize, vingt … soixante (+ quatre-vingt, soixante-dix), cent(s), mille, "et".

Known limit: "X sy Y arivo" multiplies only Y (dimy amby roapolo sy zato arivo → 100025, not 125000).
Shop notes rarely say this; the confirmation card shows the amount before anything is written.
"""
import re

UNITS = {
    # Malagasy
    "iray": 1, "iraika": 1, "roa": 2, "telo": 3, "efatra": 4, "dimy": 5, "enina": 6,
    "fito": 7, "valo": 8, "sivy": 9, "folo": 10,
    "roapolo": 20, "telopolo": 30, "efapolo": 40, "dimampolo": 50, "enimpolo": 60,
    "fitopolo": 70, "valopolo": 80, "sivifolo": 90,
    "zato": 100, "roanjato": 200, "telonjato": 300, "efajato": 400, "dimanjato": 500,
    "eninjato": 600, "fitonjato": 700, "valonjato": 800, "sivinjato": 900,
    # French
    "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7,
    "huit": 8, "neuf": 9, "dix": 10, "onze": 11, "douze": 12, "treize": 13, "quatorze": 14,
    "quinze": 15, "seize": 16, "vingt": 20, "vingts": 20, "trente": 30, "quarante": 40,
    "cinquante": 50, "soixante": 60,
}
BIG = {"arivo": 1_000, "alina": 10_000, "hetsy": 100_000, "tapitrisa": 1_000_000,
       "mille": 1_000, "million": 1_000_000, "millions": 1_000_000}
HUNDRED_FR = {"cent", "cents"}               # multiplies the current group (cinq cents = 500)
JOIN = {"amby", "ambin'ny", "ambiny", "et"}  # stay inside the group
FLUSH = {"sy"}                               # closes the group (Malagasy "and" between parts)

# Words, numbers with optional thousands separators, and apostrophes inside words (ambin'ny).
TOKEN = re.compile(r"\d{1,3}(?:[   .]\d{3}(?!\d))+|\d+|[^\W\d_]+(?:['’][^\W\d_]+)?|\S", re.UNICODE)
GROUPED = re.compile(r"^\d{1,3}(?:[   .]\d{3})+$")


def _num_value(tok: str):
    t = tok.lower().replace("’", "'")
    if tok.isdigit():
        return int(tok)
    if GROUPED.match(tok):
        return int(re.sub(r"\D", "", tok))
    return UNITS.get(t)


def _is_num(tok: str) -> bool:
    t = tok.lower()
    return _num_value(tok) is not None or t in BIG or t in HUNDRED_FR


def _can_follow(prev: str, cur: str) -> bool:
    """May `cur` continue the number started by `prev` with no connector in between?"""
    p, c = prev.lower(), cur.lower()
    if c in BIG or c in HUNDRED_FR:
        return True                       # roa arivo, 2 arivo, cinq cents
    if prev[0].isdigit() or cur[0].isdigit():
        return False                      # 3 2000 → two numbers
    if p in BIG or p in HUNDRED_FR:
        return True                       # roa arivo dimanjato, mille cinq cents
    pv, cv = _num_value(prev), _num_value(cur)
    if c in ("vingt", "vingts") and pv and 1 < pv < 10:
        return True                       # quatre vingt
    return bool(pv and cv and pv > cv and pv >= 20)  # zato dimampolo, vingt deux; not "roa telo"


def _value(run: list[str]) -> int:
    total, group = 0, 0
    for tok in run:
        t = tok.lower().replace("’", "'")
        if t in FLUSH:
            total, group = total + group, 0
        elif t in JOIN:
            continue
        elif t in BIG:
            total, group = total + (group or 1) * BIG[t], 0
        elif t in HUNDRED_FR:
            group = (group or 1) * 100
        else:
            v = _num_value(tok)
            # French "quatre vingt" = 80: a unit right before vingt multiplies it
            if t in ("vingt", "vingts") and 1 < group < 10:
                group = group * 20
            else:
                group += v
    return total + group


def to_digits(text: str) -> str:
    """Rewrite every spoken number in the text as digits; other words are kept as they are."""
    if not text:
        return text or ""
    text = text.replace("-", " ")  # soixante-dix, quatre-vingt
    spans = [(m.start(), m.end(), m.group()) for m in TOKEN.finditer(text)]
    out, last, i = [], 0, 0
    while i < len(spans):
        if not _is_num(spans[i][2]):
            i += 1
            continue
        j = i
        k = i + 1
        # extend over numbers and connectors, but end the run on a number
        while k < len(spans):
            t = spans[k][2].lower().replace("’", "'")
            if _is_num(spans[k][2]):
                if k == j + 1 and not _can_follow(spans[j][2], spans[k][2]):
                    break  # "3 2000", "roa telo": two separate numbers
                j, k = k, k + 1
            elif t in JOIN or t in FLUSH:
                k += 1
            else:
                break
        run = [s[2] for s in spans[i:j + 1]]
        out.append(text[last:spans[i][0]])
        out.append(str(_value(run)))
        last = spans[j][1]
        i = j + 1
    out.append(text[last:])
    return "".join(out)


def numbers_in(text: str) -> set[int]:
    """All numbers in an already-normalised text. '2 000' is one number; '3 2000' is two."""
    return {_num_value(m.group()) for m in TOKEN.finditer(text or "")
            if m.group()[0].isdigit() and _num_value(m.group()) is not None}
