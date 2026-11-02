"""رتبه عملکرد فقط از نتیجه ثبت‌شده می‌آید. دنبال‌کننده و شهرت در محاسبه نیستند."""

from __future__ import annotations

import sqlite3

from bazaar_intel.clusters import copy_clusters, earliest
from bazaar_intel.metrics import brier_score, outcome_value
from bazaar_intel.timeutil import iso_utc, parse_iso


def rank_sources(conn: sqlite3.Connection) -> int:
    now = iso_utc()
    leads = _lead_hours(conn)
    written = 0
    sources = conn.execute("SELECT * FROM sources WHERE active = 1").fetchall()
    for source in sources:
        rows = conn.execute(
            """
            SELECT p.id, p.success_probability, r.status, r.error_pct
            FROM prediction_attributions a
            JOIN predictions p ON p.id = a.prediction_id
            LEFT JOIN prediction_current_result r ON r.prediction_id = p.id
            WHERE a.source_id = ?
            """,
            (source["id"],),
        ).fetchall()
        resolved = [row for row in rows if row["status"] in {"hit", "miss", "partial", "invalidated"}]
        hits = sum(1 for row in resolved if row["status"] == "hit")
        misses = sum(1 for row in resolved if row["status"] in {"miss", "invalidated"})
        partials = sum(1 for row in resolved if row["status"] == "partial")
        hit_rate = None if not resolved else hits / len(resolved)
        errors = []
        for row in resolved:
            if row["error_pct"] is not None:
                errors.append(abs(float(row["error_pct"])))
        mean_error = None if not errors else sum(errors) / len(errors)
        pairs = []
        for row in resolved:
            outcome = outcome_value(row["status"])
            if outcome is None or row["success_probability"] is None:
                continue
            pairs.append((float(row["success_probability"]), outcome))
        brier = brier_score(pairs)
        news = conn.execute(
            """
            SELECT
              SUM(CASE WHEN verdict = 'confirmed' THEN 1 ELSE 0 END) AS confirmed,
              SUM(CASE WHEN verdict = 'refuted' THEN 1 ELSE 0 END) AS refuted
            FROM news_assessments a
            JOIN news_items n ON n.id = a.news_id
            WHERE n.source_id = ?
            """,
            (source["id"],),
        ).fetchone()
        level, basis, notes = _level(source["prior_level"], len(resolved), hit_rate, brier)
        conn.execute(
            """
            INSERT INTO source_score_snapshots (
              source_id, computed_at, level, level_basis, predictions_total, predictions_resolved,
              hits, misses, partials, hit_rate, mean_abs_pct_error, brier_score, mean_lead_hours,
              confirmed_news, refuted_news, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source["id"],
                now,
                level,
                basis,
                len(rows),
                len(resolved),
                hits,
                misses,
                partials,
                hit_rate,
                mean_error,
                brier,
                leads.get(source["id"]),
                int(news["confirmed"] or 0),
                int(news["refuted"] or 0),
                notes,
            ),
        )
        written += 1
    return written


def _level(prior: str, resolved: int, hit_rate: float | None, brier: float | None) -> tuple[str, str, str]:
    if resolved < 8 or hit_rate is None:
        return prior, "prior", "تعداد پیش‌بینی تعیین‌تکلیف‌شده برای تغییر سطح کافی نیست. سطح فعلی هنوز پیشین است."
    if hit_rate >= 0.6 and (brier is None or brier <= 0.22):
        return "A", "performance", "سطح بر اساس نرخ موفقیت و بریر اسکور پیش‌بینی‌های بسته‌شده تغییر کرد."
    if hit_rate >= 0.45:
        return "B", "performance", "عملکرد ثبت‌شده متوسط است."
    return "C", "performance", "نرخ موفقیت ثبت‌شده پایین است."


def _lead_hours(conn: sqlite3.Connection) -> dict[int, float]:
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT n.id, n.source_id, n.title, n.body, n.published_at
            FROM news_items n
            WHERE n.relevance = 'market'
            ORDER BY n.id DESC
            LIMIT 400
            """
        ).fetchall()
    ]
    totals: dict[int, list[float]] = {}
    for cluster in copy_clusters(rows):
        if len(cluster) < 2:
            continue
        first = earliest(cluster)
        if not first.get("published_at"):
            continue
        start = parse_iso(first["published_at"])
        delays = []
        for other in cluster:
            if other["id"] == first["id"] or not other.get("published_at"):
                continue
            delays.append((parse_iso(other["published_at"]) - start).total_seconds() / 3600)
        if delays:
            totals.setdefault(int(first["source_id"]), []).extend(delays)
    return {source: sum(values) / len(values) for source, values in totals.items()}


def performance_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT s.name, s.handle, s.group_name, snap.level, snap.level_basis,
               snap.predictions_resolved, snap.hit_rate, snap.mean_abs_pct_error,
               snap.brier_score, snap.mean_lead_hours, snap.confirmed_news, snap.refuted_news,
               snap.notes
        FROM source_score_snapshots snap
        JOIN sources s ON s.id = snap.source_id
        WHERE snap.id IN (SELECT MAX(id) FROM source_score_snapshots GROUP BY source_id)
          AND snap.level_basis = 'performance'
        ORDER BY COALESCE(snap.hit_rate, -1) DESC, s.name
        """
    ).fetchall()
