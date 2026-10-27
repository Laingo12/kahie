-- Kahie — SQLite schema
-- Every write goes through audit_log; each audit row stores the hash of the previous row (hash chain).

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS products (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,      -- canonical name, e.g. "vary"
    unit        TEXT NOT NULL DEFAULT 'unité', -- kapoaka, kg, zinga, pièce...
    unit_price  INTEGER NOT NULL DEFAULT 0, -- Ariary
    stock       REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS customers (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE,
    phone TEXT
);

CREATE TABLE IF NOT EXISTS sales (
    id          INTEGER PRIMARY KEY,
    customer_id INTEGER REFERENCES customers(id),
    product_id  INTEGER NOT NULL REFERENCES products(id),
    qty         REAL NOT NULL,
    unit_price  INTEGER NOT NULL,
    total       INTEGER NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    audit_id    INTEGER REFERENCES audit_log(id),
    voided      INTEGER NOT NULL DEFAULT 0
);

-- Positive amount = customer owes more; negative = repayment.
CREATE TABLE IF NOT EXISTS debts (
    id          INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    amount      INTEGER NOT NULL,
    reason      TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    audit_id    INTEGER REFERENCES audit_log(id),
    voided      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS stock_moves (
    id          INTEGER PRIMARY KEY,
    product_id  INTEGER NOT NULL REFERENCES products(id),
    delta       REAL NOT NULL,
    reason      TEXT NOT NULL,             -- sale | restock | loss | undo
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    audit_id    INTEGER REFERENCES audit_log(id)
);

-- Merchant vocabulary learned from corrections; fed back into ASR biasing and the planner prompt.
CREATE TABLE IF NOT EXISTS lexicon (
    id          INTEGER PRIMARY KEY,
    heard       TEXT NOT NULL,             -- what the ear produced, e.g. "kapwaka"
    canonical   TEXT NOT NULL,             -- what the merchant meant, e.g. "kapoaka"
    kind        TEXT NOT NULL,             -- product | customer | unit | word
    hits        INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (heard, canonical)
);

-- Pending plans waiting for the one-tap confirmation.
CREATE TABLE IF NOT EXISTS pending_plans (
    id          TEXT PRIMARY KEY,          -- uuid
    transcript  TEXT NOT NULL,
    ear1        TEXT,
    ear2        TEXT,
    plan_json   TEXT NOT NULL,
    verdict_json TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY,
    created_at  TEXT NOT NULL,
    kind        TEXT NOT NULL,             -- execute | undo | import
    transcript  TEXT,
    plan_json   TEXT,
    verdict_json TEXT,
    result_json TEXT,
    undoes_id   INTEGER REFERENCES audit_log(id),
    prev_hash   TEXT NOT NULL,
    hash        TEXT NOT NULL
);
