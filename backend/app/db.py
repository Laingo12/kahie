"""SQLite helpers and demo seed."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import config

SCHEMA = Path(__file__).with_name("schema.sql")


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = Path(path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None: autocommit; multi-step writes use the explicit transaction() below.
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    conn.commit()


@contextmanager
def transaction(conn: sqlite3.Connection):
    try:
        conn.execute("BEGIN")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def seed_demo(conn: sqlite3.Connection) -> None:
    """Small fictional shop used for the demo and tests. No real customer data.

    Hery and Lalao are included so the eval cases that mention them
    (mg-015, mg-021, mg-028, mx-006, mx-010, mx-016, fr-007, fr-009)
    resolve to a known customer instead of a spurious clarification.
    """
    if conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]:
        return
    conn.executemany(
        "INSERT INTO products(name, unit, unit_price, stock) VALUES (?,?,?,?)",
        [
            # Price and stock are in each product's base unit (see units.py):
            # siramamy and lafarinina are sold by weight (kg); 1 kapoaka of sugar ≈ 250 g → 800 Ar.
            ("vary", "kapoaka", 1000, 120),
            ("siramamy", "kg", 3200, 15),
            ("lafarinina", "kg", 3000, 25),
            ("menaka", "litatra", 9000, 15),
            ("savony", "isa", 1500, 40),
            ("mofo", "isa", 300, 50),
        ],
    )
    conn.executemany(
        "INSERT INTO customers(name) VALUES (?)",
        [("Rabe",), ("Soa",), ("Naivo",), ("Bodo",), ("Hery",), ("Lalao",)],
    )
    # lexicon.normalize() matches whole words only, so French plurals need their own entries.
    # Keep singulier + pluriel + French units — the eval (cases.jsonl) uses all three forms.
    conn.executemany(
        "INSERT INTO lexicon(heard, canonical, kind) VALUES (?,?,?)",
        [
            ("riz", "vary", "product"),
            ("sucre", "siramamy", "product"), ("sucres", "siramamy", "product"),
            ("huile", "menaka", "product"), ("huiles", "menaka", "product"),
            ("savon", "savony", "product"), ("savons", "savony", "product"),
            ("pain", "mofo", "product"), ("pains", "mofo", "product"),
            ("litre", "litatra", "unit"), ("litres", "litatra", "unit"),
            ("farine", "lafarinina", "product"), ("lafarine", "lafarinina", "product"),
        ],
    )
    conn.commit()