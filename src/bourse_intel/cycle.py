from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from bourse_intel import collectors
from bourse_intel.config import HTML_PAGES, RSS_FEEDS, load_channels
from bourse_intel.db import upsert_source
from bourse_intel.pipeline import (
    build_impacts,
    build_opportunities,
    content_hash,
    evaluate_due_predictions,
    link_clusters,
    map_entities,
    register_predictions,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_sources(conn: sqlite3.Connection) -> None:
    rows = [
        {
            "key": "rahavard365",
            "title": "ره‌آورد ۳۶۵، تابلوی عمومی",
            "kind": "mirror",
            "method": "rahavard_board",
            "url": "https://rahavard365.com/api/v2/market-data/stocks",
            "verification_default": "confirmed",
            "credibility_prior": 70,
            "notes": "آینه زنده قیمت. مرجع رسمی TSETMC از این محیط در دسترس نیست.",
        },
        {
            "key": "tsetmc_market",
            "title": "TSETMC",
            "kind": "official",
            "method": "tsetmc",
            "url": "https://cdn.tsetmc.com/",
            "verification_default": "confirmed",
            "credibility_prior": 95,
            "notes": "probe فقط. تا وقتی اتصال برقرار شود داده‌ای از این منبع ذخیره نمی‌شود.",
        },
        {
            "key": "codal_home",
            "title": "کدال",
            "kind": "official",
            "method": "codal",
            "url": "https://codal.ir/",
            "verification_default": "confirmed",
            "credibility_prior": 95,
            "notes": "probe فقط. افشای رسمی تا رفع تایم‌اوت وارد پایگاه نمی‌شود.",
        },
        {
            "key": "fipiran_home",
            "title": "فیپیران",
            "kind": "official",
            "method": "fipiran",
            "url": "https://www.fipiran.com/",
            "verification_default": "confirmed",
            "credibility_prior": 90,
            "notes": "probe فقط.",
        },
    ]
    for feed in RSS_FEEDS:
        rows.append(
            {
                "key": feed["key"],
                "title": feed["title"],
                "kind": feed["kind"],
                "method": "rss",
                "url": feed["url"],
                "verification_default": feed["verification"],
                "credibility_prior": feed["credibility_prior"],
                "notes": "",
            }
        )
    for page in HTML_PAGES:
        rows.append(
            {
                "key": page["key"],
                "title": page["title"],
                "kind": page["kind"],
                "method": "html",
                "url": page["url"],
                "verification_default": page["verification"],
                "credibility_prior": page["credibility_prior"],
                "notes": "",
            }
        )
    for channel in load_channels():
        rows.append(
            {
                "key": f"tg_{channel['key']}",
                "title": channel["title"],
                "kind": channel["kind"],
                "method": "telegram_public",
                "url": f"https://t.me/s/{channel['username']}",
                "verification_default": channel["verification"],
                "credibility_prior": channel["credibility_prior"],
                "notes": "پیش‌نمایش عمومی تلگرام. کانال خصوصی یا API رسمی نیست.",
            }
        )
    for row in rows:
        upsert_source(conn, row)
    conn.commit()


def _record_run(conn: sqlite3.Connection, probe: collectors.Probe, started: str, new_count: int) -> None:
    conn.execute(
        """
        INSERT INTO collector_runs(source_key, started_at, finished_at, status, http_status, items_seen, items_new, message)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (probe.source_key, started, utc_now(), probe.status, probe.http_status, probe.seen, new_count, probe.message[:500]),
    )


def _store_instruments(conn: sqlite3.Connection, drafts: list[collectors.InstrumentDraft]) -> int:
    now = utc_now()
    stored = 0
    for draft in drafts:
        conn.execute(
            """
            INSERT INTO instruments(source_key, external_id, symbol, name, company_name, market, instrument_type, industry, slug, updated_at)
            VALUES(?, ?, ?, ?, '', ?, ?, '', ?, ?)
            ON CONFLICT(source_key, external_id) DO UPDATE SET
                symbol=excluded.symbol,
                name=CASE WHEN instruments.company_name != '' THEN instruments.name ELSE excluded.name END,
                market=CASE WHEN excluded.market != '' THEN excluded.market ELSE instruments.market END,
                instrument_type=excluded.instrument_type,
                slug=excluded.slug,
                updated_at=excluded.updated_at
            """,
            (
                draft.source_key,
                draft.external_id,
                draft.symbol,
                draft.name,
                draft.market,
                draft.instrument_type,
                draft.slug,
                now,
            ),
        )
        instrument = conn.execute(
            "SELECT id FROM instruments WHERE source_key = ? AND external_id = ?",
            (draft.source_key, draft.external_id),
        ).fetchone()
        if draft.last_price is None and draft.volume is None:
            stored += 1
            continue
        conn.execute(
            """
            INSERT INTO quotes(instrument_id, observed_at, trade_time, last_price, change_percent, volume, value, source_key)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                instrument["id"],
                now,
                draft.trade_time,
                draft.last_price,
                draft.change_percent,
                draft.volume,
                draft.value,
                draft.source_key,
            ),
        )
        stored += 1
    return stored


def enrich_missing(conn: sqlite3.Connection, limit: int) -> int:
    rows = conn.execute(
        """
        SELECT id, external_id FROM instruments
        WHERE company_name = '' AND source_key = 'rahavard365' AND instrument_type != 'index'
          AND external_id GLOB '[0-9]*'
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    if not rows:
        return 0
    updated = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(collectors.enrich_identity, row["external_id"]): row["id"] for row in rows}
        for future in as_completed(futures):
            instrument_id = futures[future]
            try:
                identity = future.result()
            except Exception:
                identity = None
            if not identity:
                continue
            conn.execute(
                """
                UPDATE instruments
                SET company_name = ?, industry = ?, market = CASE WHEN ? != '' THEN ? ELSE market END,
                    symbol = CASE WHEN ? != '' THEN ? ELSE symbol END
                WHERE id = ?
                """,
                (
                    identity.get("company_name") or "",
                    identity.get("industry") or "",
                    identity.get("market") or "",
                    identity.get("market") or "",
                    identity.get("symbol") or "",
                    identity.get("symbol") or "",
                    instrument_id,
                ),
            )
            updated += 1
    return updated


def _store_items(conn: sqlite3.Connection, drafts: list[collectors.ItemDraft]) -> tuple[list[int], int]:
    now = utc_now()
    new_ids: list[int] = []
    for draft in drafts:
        source = conn.execute("SELECT id FROM sources WHERE key = ?", (draft.source_key,)).fetchone()
        if source is None:
            continue
        digest = content_hash(draft.source_key + draft.external_id, draft.title)
        try:
            cur = conn.execute(
                """
                INSERT INTO items(source_id, external_id, url, title, body, published_at, fetched_at, content_hash, verification)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source["id"],
                    draft.external_id[:400],
                    draft.url,
                    draft.title,
                    draft.body,
                    draft.published_at,
                    now,
                    digest,
                    draft.verification,
                ),
            )
        except sqlite3.IntegrityError:
            continue
        new_ids.append(int(cur.lastrowid))
    return new_ids, len(drafts)


def run_cycle(conn: sqlite3.Connection, enrich_limit: int = 120) -> dict:
    ensure_sources(conn)
    started = utc_now()
    summary = {"started_at": started, "probes": [], "new_items": 0, "instruments": 0, "enriched": 0}
    instruments, board_probes = collectors.collect_boards()
    summary["instruments"] = _store_instruments(conn, instruments)
    for probe in board_probes:
        _record_run(conn, probe, started, summary["instruments"] if probe.status == "ok" else 0)
        summary["probes"].append(probe.__dict__)
    summary["enriched"] = enrich_missing(conn, enrich_limit)
    for probe in collectors.probe_blocked():
        _record_run(conn, probe, started, 0)
        summary["probes"].append(probe.__dict__)
    item_drafts: list[collectors.ItemDraft] = []
    for batch_items, probes in (collectors.collect_rss(), collectors.collect_html_pages(), collectors.collect_telegram()):
        item_drafts.extend(batch_items)
        for probe in probes:
            before = summary["new_items"]
            ids, _seen = _store_items(conn, [item for item in batch_items if item.source_key == probe.source_key])
            summary["new_items"] += len(ids)
            _record_run(conn, probe, started, len(ids))
            summary["probes"].append({**probe.__dict__, "items_new": len(ids)})
            summary.setdefault("_new_ids", []).extend(ids)
            _ = before
    new_ids = summary.pop("_new_ids", [])
    link_clusters(conn, new_ids)
    summary["entity_links"] = map_entities(conn, new_ids)
    now = utc_now()
    summary["impacts"] = build_impacts(conn, new_ids, now)
    summary["opportunities"] = build_opportunities(conn, now)
    summary["predictions"] = register_predictions(conn, now)
    summary["evaluations"] = evaluate_due_predictions(conn, now)
    summary["finished_at"] = utc_now()
    conn.commit()
    return summary
