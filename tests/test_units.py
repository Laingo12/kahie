"""Units: sugar and flour are sold by weight (kg, g, half a kilo); conversions per product (mock mode)."""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("KAHIE_DB", str(Path(tempfile.mkdtemp()) / "test.db"))
os.environ["KAHIE_BACKEND"] = "mock"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pytest  # noqa: E402

from app import db, units  # noqa: E402
from app.agent import executor, pipeline  # noqa: E402
from app.numbers_mg import speakify  # noqa: E402


def _fresh():
    conn = db.connect(Path(tempfile.mkdtemp()) / "t.db")
    db.init(conn)
    db.seed_demo(conn)
    return conn


def _sale_line(conn, text):
    r = pipeline.handle_voice(conn, b"", "audio/webm", hint_text=text)
    return r, next(line for line in r["verdict"]["lines"] if line["kind"] == "sale")


@pytest.mark.parametrize("product, base, unit, f", [
    ("siramamy", "kg", "grama", 0.001), ("siramamy", "kg", "kilao", 1.0), ("siramamy", "kg", "kapoaka", 0.25),
    ("lafarinina", "kg", "kapoaka", 0.2), ("vary", "kapoaka", "kg", 3.5), ("menaka", "litatra", "litre", 1.0),
    ("mofo", "isa", "pièce", 1.0), ("siramamy", "kg", "litatra", None), ("menaka", "litatra", "kg", None),
])
def test_factor(product, base, unit, f):
    assert units.factor(product, base, unit) == f


@pytest.mark.parametrize("spoken, normalised", [
    ("antsasaky ny kilao siramamy", "0.5 kg siramamy"),
    ("lafarinina roa kilao sy sasany", "lafarinina 2.5 kilao"),
    ("kilao sy sasany vary", "1.5 kilao vary"),
    ("un demi-kilo de sucre", "0.5 kg de sucre"),
    ("siramamy dimanjato grama", "siramamy 500 grama"),
])
def test_halves_and_grams_in_words(spoken, normalised):
    assert units.normalize_text(spoken) == normalised


def test_grams_of_sugar_priced_per_kg():
    r, ln = _sale_line(_fresh(), "Rabe naka siramamy dimanjato grama, nandoa arivo")
    assert (ln["qty"], ln["unit"], ln["base_qty"], ln["total"]) == (500, "g", 0.5, 1600)   # 3 200 Ar/kg
    assert [x["amount"] for x in r["verdict"]["lines"] if x["kind"] == "debt"] == [600]


def test_half_a_kilo_and_two_and_a_half_kilos():
    _, ln = _sale_line(_fresh(), "Soa naka antsasaky ny kilao siramamy")
    assert (ln["base_qty"], ln["total"]) == (0.5, 1600)
    _, ln = _sale_line(_fresh(), "Naivo naka lafarinina roa kilao sy sasany")
    assert (ln["base_qty"], ln["total"]) == (2.5, 7500)                                     # 3 000 Ar/kg


def test_kapoaka_of_sugar_still_works_and_keeps_its_price():
    _, ln = _sale_line(_fresh(), "Bodo naka siramamy roa kapoaka")
    assert (ln["qty"], ln["unit"], ln["base_qty"], ln["total"]) == (2, "kapoaka", 0.5, 1600)  # 800 Ar/kapoaka


def test_rice_by_the_kilo():
    _, ln = _sale_line(_fresh(), "Hery naka vary iray kilao")
    assert (ln["base_qty"], ln["total"]) == (3.5, 3500)


def test_impossible_unit_is_a_question():
    v = pipeline.handle_voice(_fresh(), b"", "audio/webm", hint_text="Lalao naka siramamy roa litatra")["verdict"]
    assert v["level"] == "clarify" and "kg" in v["question"] and "litatra" in v["question"]


def test_stock_kept_in_base_unit_after_sale_restock_and_loss():
    conn = _fresh()
    for text in ("Rabe naka siramamy dimanjato grama", "Tonga ny siramamy, kilao folo", "Simba ny siramamy iray kilao"):
        r = pipeline.handle_voice(conn, b"", "audio/webm", hint_text=text)
        executor.execute(conn, r["transcript"], r["plan"], r["verdict"])
    assert conn.execute("SELECT stock FROM products WHERE name='siramamy'").fetchone()[0] == pytest.approx(15 - 0.5 + 10 - 1)
    ans = pipeline.handle_voice(conn, b"", "audio/webm", hint_text="Firy kilao ny siramamy sisa?")["answer"]
    assert ans["text"] == "23,5 kg siramamy sisa."


def test_loss_always_removes_stock_even_with_a_positive_delta():
    from app.agent import verifier
    v = verifier.verify(_fresh(), [{"tool": "update_stock", "args": {"product": "mofo", "delta": 3, "reason": "loss"}}])
    assert v["lines"][0]["delta"] == -3


def test_display_and_speech():
    assert units.fmt_qty(0.5, "kg") == "500 g" and units.fmt_qty(14.5, "kg") == "14,5 kg"
    assert speakify("siramamy 500 g, 14,5 kg") == "siramamy dimanjato grama, efatra ambin'ny folo faingo dimy kilao"
