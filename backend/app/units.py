"""Units of sale: kg, g, kapoaka, litatra, isa — and conversions per product.

Each product has a BASE unit (products.unit), the one its stock and price are kept in:
  siramamy, lafarinina → kg (sold by weight: "dimanjato grama", "antsasaky ny kilao", "roa kilao")
  vary                 → kapoaka (but "iray kilao vary" also works)
  menaka               → litatra;  savony, mofo → isa
A note may use any unit the product accepts; the verifier converts it to the base unit, so the
stock, the price and the total are always computed the same way. An impossible unit
("siramamy 2 litatra") is a question, never a guess.

The kapoaka weights below are typical market values (a kapoaka is a condensed-milk tin).
They are per product because a tin of flour weighs less than a tin of sugar. Adjust them here.
"""
import re

# spoken / written form → canonical unit
UNIT_ALIASES = {
    "kg": "kg", "kilao": "kg", "kilo": "kg", "kilos": "kg", "kilogramme": "kg", "kilogrammes": "kg",
    "g": "g", "gr": "g", "grama": "g", "gramme": "g", "grammes": "g",
    "livatra": "livatra",
    "kapoaka": "kapoaka",
    "litatra": "litatra", "l": "litatra", "litre": "litatra", "litres": "litatra",
    "isa": "isa", "pièce": "isa", "pièces": "isa", "piece": "isa", "pieces": "isa",
}
MASS_KG = {"kg": 1.0, "g": 0.001, "livatra": 0.5}  # livatra ≈ one pound, 500 g

# Per product: extra units → how many BASE units one of them is.
PRODUCT_UNITS = {
    "siramamy": {"kapoaka": 0.25},     # base kg: 1 kapoaka ≈ 250 g
    "lafarinina": {"kapoaka": 0.2},    # base kg: 1 kapoaka ≈ 200 g
    "vary": {"kg": 3.5},               # base kapoaka: 1 kg ≈ 3.5 kapoaka
}

UNIT_WORDS = "|".join(sorted((re.escape(u) for u in UNIT_ALIASES), key=len, reverse=True))


def normalize(unit: str | None) -> str | None:
    if not unit:
        return None
    u = unit.strip().lower()
    return UNIT_ALIASES.get(u, u)


def factor(product: str, base_unit: str, unit: str | None) -> float | None:
    """How many BASE units one `unit` of `product` is. None = this unit makes no sense for this product."""
    base, u = normalize(base_unit), normalize(unit) or normalize(base_unit)
    if u == base:
        return 1.0
    extra = PRODUCT_UNITS.get((product or "").lower(), {})
    if u in extra:
        return extra[u]
    if u in MASS_KG:
        if base == "kg":
            return MASS_KG[u]
        if "kg" in extra:                       # e.g. vary: grams → kg → kapoaka
            return MASS_KG[u] * extra["kg"]
    return None


def accepted(product: str, base_unit: str) -> list[str]:
    """Units a product can be sold in, for the planner prompt and error messages."""
    out = [normalize(base_unit)]
    extra = PRODUCT_UNITS.get((product or "").lower(), {})
    if normalize(base_unit) == "kg" or "kg" in extra:
        out += [u for u in ("kg", "g", "livatra") if u not in out]
    out += [u for u in extra if u not in out]
    return out


def fmt_qty(qty: float, unit: str) -> str:
    """15 kg → "15 kg"; 0.5 kg → "500 g"; 14.5 kg → "14,5 kg" (Malagasy/French decimal comma)."""
    unit = normalize(unit) or unit
    if unit == "kg" and 0 < abs(qty) < 1:
        return f"{qty * 1000:g} g"
    return f"{round(qty, 3):g}".replace(".", ",") + f" {unit}"


# Halves said in words → digits, after numbers_mg.to_digits():
#   "antsasaky ny kilao" → "0.5 kg", "2 kilao sy sasany" → "2.5 kg", "demi-kilo" → "0.5 kg"
_HALF_KG = re.compile(r"(?<!\w)(?:(?:an)?tsasaky\s+ny\s+kilao|antsasa[-\s]?kilao|antsasany\s+kilao|(?:un\s+)?demi[-\s]?kilo)(?!\w)", re.I)
_N_AND_HALF = re.compile(r"(?<![\d.])(\d+)\s+(kilao|kilo|kg|kapoaka|litatra)\s+sy\s+(?:ts)?sasany(?!\w)", re.I)
_ONE_AND_HALF = re.compile(r"(?<![\d\w])(kilao|kilo|kapoaka|litatra)\s+sy\s+(?:ts)?sasany(?!\w)", re.I)


def before_digits(text: str) -> str:
    """Run BEFORE numbers_mg.to_digits (so "un demi-kilo" is not read as "1 …")."""
    return _HALF_KG.sub("0.5 kg", text or "")


def after_digits(text: str) -> str:
    """Run AFTER numbers_mg.to_digits: "2 kilao sy sasany" → "2.5 kilao"."""
    text = _N_AND_HALF.sub(lambda m: f"{m.group(1)}.5 {m.group(2)}", text or "")
    return _ONE_AND_HALF.sub(lambda m: f"1.5 {m.group(1)}", text)


def normalize_text(text: str) -> str:
    """Halves in words → decimal quantities. Applies to_digits in between."""
    from .numbers_mg import to_digits
    return after_digits(to_digits(before_digits(text)))
