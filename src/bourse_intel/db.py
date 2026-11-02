from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    kind TEXT NOT NULL,
    method TEXT NOT NULL,
    url TEXT NOT NULL,
    verification_default TEXT NOT NULL,
    credibility_prior INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    requires_credential TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS collector_runs (
    id INTEGER PRIMARY KEY,
    source_key TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    status TEXT NOT NULL,
    http_status INTEGER,
    items_seen INTEGER NOT NULL DEFAULT 0,
    items_new INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS instruments (
    id INTEGER PRIMARY KEY,
    source_key TEXT NOT NULL,
    external_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    company_name TEXT NOT NULL DEFAULT '',
    market TEXT NOT NULL DEFAULT '',
    instrument_type TEXT NOT NULL,
    industry TEXT NOT NULL DEFAULT '',
    slug TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    UNIQUE(source_key, external_id)
);

CREATE TABLE IF NOT EXISTS quotes (
    id INTEGER PRIMARY KEY,
    instrument_id INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    trade_time TEXT NOT NULL DEFAULT '',
    last_price REAL,
    change_percent REAL,
    volume REAL,
    value REAL,
    source_key TEXT NOT NULL,
    FOREIGN KEY(instrument_id) REFERENCES instruments(id)
);

CREATE INDEX IF NOT EXISTS idx_quotes_instrument ON quotes(instrument_id, observed_at);

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL,
    external_id TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    published_at TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    content_hash TEXT NOT NULL UNIQUE,
    verification TEXT NOT NULL,
    cluster_id INTEGER,
    is_primary INTEGER NOT NULL DEFAULT 0,
    UNIQUE(source_id, external_id),
    FOREIGN KEY(source_id) REFERENCES sources(id)
);

CREATE TABLE IF NOT EXISTS clusters (
    id INTEGER PRIMARY KEY,
    primary_item_id INTEGER,
    first_published_at TEXT NOT NULL,
    title_norm TEXT NOT NULL,
    corroboration_count INTEGER NOT NULL DEFAULT 1,
    verification TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS item_entities (
    item_id INTEGER NOT NULL,
    instrument_id INTEGER NOT NULL,
    match_on TEXT NOT NULL,
    PRIMARY KEY(item_id, instrument_id)
);

CREATE TABLE IF NOT EXISTS impacts (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL,
    instrument_id INTEGER NOT NULL,
    direction TEXT NOT NULL,
    revenue_effect TEXT NOT NULL,
    profit_effect TEXT NOT NULL,
    price_reflection TEXT NOT NULL,
    evidence TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(item_id, instrument_id)
);

CREATE TABLE IF NOT EXISTS opportunities (
    id INTEGER PRIMARY KEY,
    cluster_id INTEGER NOT NULL,
    instrument_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    thesis TEXT NOT NULL,
    evidence_level TEXT NOT NULL,
    price_reflection TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    UNIQUE(cluster_id, instrument_id)
);

CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    instrument_id INTEGER NOT NULL,
    source_id INTEGER,
    cluster_id INTEGER,
    direction TEXT NOT NULL,
    probability REAL NOT NULL,
    horizon_end TEXT NOT NULL,
    base_price REAL NOT NULL,
    base_price_time TEXT NOT NULL,
    rationale TEXT NOT NULL,
    invalidators TEXT NOT NULL,
    evidence_level TEXT NOT NULL,
    method TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prediction_evaluations (
    id INTEGER PRIMARY KEY,
    prediction_id INTEGER NOT NULL,
    evaluated_at TEXT NOT NULL,
    price REAL,
    return_pct REAL,
    outcome TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(prediction_id) REFERENCES predictions(id)
);

CREATE TABLE IF NOT EXISTS watchlist (
    instrument_id INTEGER PRIMARY KEY,
    note TEXT NOT NULL DEFAULT '',
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY,
    slot_key TEXT NOT NULL UNIQUE,
    slot TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    tehran_date TEXT NOT NULL,
    body TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS predictions_no_delete
BEFORE DELETE ON predictions
BEGIN
    SELECT RAISE(ABORT, 'predictions are immutable');
END;

CREATE TRIGGER IF NOT EXISTS predictions_no_mutate
BEFORE UPDATE ON predictions
BEGIN
    SELECT RAISE(ABORT, 'predictions are immutable');
END;
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def upsert_source(conn: sqlite3.Connection, row: dict) -> int:
    conn.execute(
        """
        INSERT INTO sources(key, title, kind, method, url, verification_default, credibility_prior, requires_credential, notes)
        VALUES(:key, :title, :kind, :method, :url, :verification_default, :credibility_prior, :requires_credential, :notes)
        ON CONFLICT(key) DO UPDATE SET
            title=excluded.title,
            kind=excluded.kind,
            method=excluded.method,
            url=excluded.url,
            verification_default=excluded.verification_default,
            credibility_prior=excluded.credibility_prior,
            requires_credential=excluded.requires_credential,
            notes=excluded.notes
        """,
        {
            "requires_credential": row.get("requires_credential", ""),
            "notes": row.get("notes", ""),
            **row,
        },
    )
    found = conn.execute("SELECT id FROM sources WHERE key = ?", (row["key"],)).fetchone()
    return int(found["id"])
