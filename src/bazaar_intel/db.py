from __future__ import annotations

import sqlite3
from pathlib import Path

from bazaar_intel.config import DEFAULT_DB, SCHEMA_PATH
from bazaar_intel.sources_seed import SEED_SOURCES
from bazaar_intel.timeutil import iso_utc


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = Path(path) if path else DEFAULT_DB
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path: Path | None = None) -> sqlite3.Connection:
    conn = connect(path)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES('schema_version', '1') "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
    )
    seed_sources(conn)
    conn.commit()
    return conn


def seed_sources(conn: sqlite3.Connection) -> int:
    now = iso_utc()
    inserted = 0
    for source in SEED_SOURCES:
        cur = conn.execute(
            """
            INSERT INTO sources (
              source_key, name, handle, url, group_name, specialty,
              activity_started_on, conflict_of_interest, prior_level,
              prior_basis, notes, active, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'unknown', ?, ?, ?, 1, ?)
            ON CONFLICT(source_key) DO NOTHING
            """,
            (
                source["source_key"],
                source["name"],
                source.get("handle"),
                source.get("url"),
                source["group_name"],
                source["specialty"],
                source.get("activity_started_on"),
                source["prior_level"],
                source["prior_basis"],
                source.get("notes"),
                now,
            ),
        )
        if cur.rowcount:
            inserted += 1
            source_id = conn.execute(
                "SELECT id FROM sources WHERE source_key = ?",
                (source["source_key"],),
            ).fetchone()["id"]
            conn.execute(
                """
                INSERT INTO source_score_snapshots (
                  source_id, computed_at, level, level_basis, notes
                ) VALUES (?, ?, ?, 'prior', ?)
                """,
                (
                    source_id,
                    now,
                    source["prior_level"],
                    "سطح اولیه بر اساس نوع منبع است، نه عملکرد ثبت‌شده.",
                ),
            )
    return inserted


def next_public_id(conn: sqlite3.Connection, name: str, prefix: str) -> str:
    row = conn.execute("SELECT value FROM id_counters WHERE name = ?", (name,)).fetchone()
    value = 1 if row is None else int(row["value"]) + 1
    if row is None:
        conn.execute("INSERT INTO id_counters(name, value) VALUES(?, ?)", (name, value))
    else:
        conn.execute("UPDATE id_counters SET value = ? WHERE name = ?", (value, name))
    return f"{prefix}-{value:06d}"


def source_id(conn: sqlite3.Connection, source_key: str) -> int | None:
    row = conn.execute("SELECT id FROM sources WHERE source_key = ?", (source_key,)).fetchone()
    return None if row is None else int(row["id"])


def set_capability(conn: sqlite3.Connection, capability: str, state: str, detail: str) -> None:
    conn.execute(
        """
        INSERT INTO capability_status(capability, state, detail, updated_at)
        VALUES(?, ?, ?, ?)
        ON CONFLICT(capability) DO UPDATE SET
          state = excluded.state,
          detail = excluded.detail,
          updated_at = excluded.updated_at
        """,
        (capability, state, detail, iso_utc()),
    )
