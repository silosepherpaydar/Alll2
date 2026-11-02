from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed

from bazaar_intel.http_client import fetch
from bazaar_intel.timeutil import iso_utc


def probe_sources(conn: sqlite3.Connection, timeout: int = 10) -> dict[str, int]:
    sources = [
        (row["id"], row["url"])
        for row in conn.execute(
            "SELECT id, url FROM sources WHERE url IS NOT NULL AND active = 1"
        ).fetchall()
    ]
    counts = {"ok": 0, "failed": 0}

    def check(item: tuple[int, str]):
        source_id, url = item
        return source_id, fetch(url, timeout=timeout)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(check, item) for item in sources]
        for future in as_completed(futures):
            source_id, result = future.result()
            conn.execute(
                """
                INSERT INTO source_access(
                  source_id, checked_at, method, status_code, ok, error, final_url, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    iso_utc(),
                    result.method,
                    result.status_code,
                    1 if result.ok else 0,
                    result.error,
                    result.final_url,
                    "بررسی دسترسی عمومی. موفق بودن به‌معنای پوشش کامل محتوا نیست.",
                ),
            )
            counts["ok" if result.ok else "failed"] += 1
    conn.commit()
    return counts
