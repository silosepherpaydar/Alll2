from __future__ import annotations

import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from bourse_intel.config import TEHRAN


def build_report(conn: sqlite3.Connection, slot: str) -> str:
    tehran = datetime.now(ZoneInfo(TEHRAN))
    counts = {
        "instruments": conn.execute("SELECT COUNT(*) AS n FROM instruments").fetchone()["n"],
        "quotes": conn.execute("SELECT COUNT(*) AS n FROM quotes").fetchone()["n"],
        "items": conn.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"],
        "clusters": conn.execute("SELECT COUNT(*) AS n FROM clusters").fetchone()["n"],
        "opportunities": conn.execute("SELECT COUNT(*) AS n FROM opportunities WHERE status = 'open'").fetchone()["n"],
        "predictions": conn.execute("SELECT COUNT(*) AS n FROM predictions").fetchone()["n"],
        "evaluations": conn.execute("SELECT COUNT(*) AS n FROM prediction_evaluations").fetchone()["n"],
    }
    types = conn.execute(
        "SELECT instrument_type, COUNT(*) AS n FROM instruments GROUP BY instrument_type ORDER BY n DESC"
    ).fetchall()
    probes = conn.execute(
        """
        SELECT source_key, status, message, items_seen, items_new, finished_at
        FROM collector_runs
        WHERE id IN (SELECT MAX(id) FROM collector_runs GROUP BY source_key)
        ORDER BY status, source_key
        """
    ).fetchall()
    news = conn.execute(
        """
        SELECT items.title, items.published_at, items.verification, sources.title AS source, clusters.verification AS cluster_verification
        FROM items
        JOIN sources ON sources.id = items.source_id
        LEFT JOIN clusters ON clusters.id = items.cluster_id
        WHERE items.is_primary = 1
        ORDER BY items.published_at DESC
        LIMIT 15
        """
    ).fetchall()
    lines = [
        f"# گزارش {slot}",
        f"زمان تهران: {tehran.strftime('%Y-%m-%d %H:%M')}",
        "",
        "این گزارش فقط از ردیف‌های ذخیره‌شده در پایگاه ساخته شده است.",
        "",
        f"- نماد و ابزار: {counts['instruments']}",
        f"- قیمت ذخیره‌شده: {counts['quotes']}",
        f"- خبر و پست: {counts['items']}",
        f"- خوشه خبر: {counts['clusters']}",
        f"- فرصت باز: {counts['opportunities']}",
        f"- پیش‌بینی ثبت‌شده: {counts['predictions']}",
        f"- ارزیابی انجام‌شده: {counts['evaluations']}",
        "",
        "## پوشش ابزار",
    ]
    if types:
        lines.extend(f"- {row['instrument_type']}: {row['n']}" for row in types)
    else:
        lines.append("- هنوز ابزاری ذخیره نشده است.")
    lines.append("")
    lines.append("## آخرین وضعیت جمع‌آوری")
    for row in probes:
        lines.append(
            f"- {row['source_key']}: {row['status']} | دیده‌شده {row['items_seen']} | تازه {row['items_new']} | {row['message']}"
        )
    lines.append("")
    lines.append("## تازه‌ترین موارد اولیه")
    if not news:
        lines.append("- خبر اولیه‌ای ذخیره نشده است.")
    for row in news:
        lines.append(
            f"- [{row['verification']}/{row['cluster_verification'] or '-'}] {row['source']}: {row['title']}"
        )
    body = "\n".join(lines) + "\n"
    slot_key = f"{tehran.date().isoformat()}-{slot}"
    conn.execute(
        """
        INSERT INTO reports(slot_key, slot, generated_at, tehran_date, body)
        VALUES(?, ?, ?, ?, ?)
        ON CONFLICT(slot_key) DO UPDATE SET body = excluded.body, generated_at = excluded.generated_at
        """,
        (slot_key, slot, tehran.isoformat(), tehran.date().isoformat(), body),
    )
    conn.commit()
    return body
