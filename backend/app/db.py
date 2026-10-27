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
    """Small fictional shop used for the demo and tests. No real customer data."""
    if conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]:
        return
    conn.executemany(
        "INSERT INTO products(name, unit, unit_price, stock) VALUES (?,?,?,?)",
        [
            ("vary", "kapoaka", 1000, 120),
            ("siramamy", "kapoaka", 800, 60),
            ("menaka", "litatra", 9000, 15),
            ("savony", "pièce", 1500, 40),
            ("mofo", "pièce", 300, 50),
        ],
    )
    conn.executemany(
        "INSERT INTO customers(name) VALUES (?)",
        [("Rabe",), ("Soa",), ("Naivo",), ("Bodo",)],
    )
    conn.executemany(
        "INSERT INTO lexicon(heard, canonical, kind) VALUES (?,?,?)",
        [("riz", "vary", "product"), ("sucre", "siramamy", "product"),
         ("huile", "menaka", "product"), ("savon", "savony", "product"),
         ("pain", "mofo", "product")],
    )
    conn.commit()
