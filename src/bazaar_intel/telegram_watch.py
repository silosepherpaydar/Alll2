"""پیش‌نمایش عمومی تلگرام. نشانی خارج از t.me/s ساخته نمی‌شود."""

from __future__ import annotations

import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed

from bazaar_intel.db import log_fetch, source_id
from bazaar_intel.http_client import decode_body, fetch
from bazaar_intel.ingest import _insert_news, parse_telegram_channel, verification_for
from bazaar_intel.sources_seed import TELEGRAM_HANDLES
from bazaar_intel.textnorm import looks_market_relevant
from bazaar_intel.timeutil import iso_utc

HANDLE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
LINK_RE = re.compile(r"https?://t\.me/(?:s/)?([A-Za-z][A-Za-z0-9_]{3,31})")
RESERVED = {"share", "joinchat", "addstickers", "proxy", "socks", "iv", "login"}
HISTORY_PAGES = 2
MAX_DISCOVERED_PER_CYCLE = 12


def safe_handle(value: str) -> str | None:
    handle = value.strip().lstrip("@")
    if handle.lower() in RESERVED or not HANDLE_RE.fullmatch(handle):
        return None
    return handle


def preview_url(handle: str, before: str | None = None) -> str:
    checked = safe_handle(handle)
    if checked is None:
        raise ValueError("telegram handle is not a public channel name")
    if before is None:
        return f"https://t.me/s/{checked}"
    if not before.isdigit():
        raise ValueError("telegram before must be a message id")
    return f"https://t.me/s/{checked}?before={before}"


def mentions_in(html: str) -> list[str]:
    found = []
    seen = set()
    for handle in LINK_RE.findall(html):
        checked = safe_handle(handle)
        if checked is None or checked in seen:
            continue
        seen.add(checked)
        found.append(checked)
    return found


def ingest_telegram_history(conn: sqlite3.Connection, report) -> None:
    known = {safe_handle(handle) for handle in TELEGRAM_HANDLES}
    known.discard(None)
    discovered: list[tuple[str, str]] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(_read_channel, handle): handle for handle in TELEGRAM_HANDLES}
        for future in as_completed(futures):
            handle = futures[future]
            posts, error, html_pages = future.result()
            _store_channel(conn, report, handle, posts, error, html_pages, seeded=True)
            for page in html_pages:
                for mention in mentions_in(page):
                    if mention not in known:
                        known.add(mention)
                        discovered.append((mention, handle))
    for mention, origin in discovered[:MAX_DISCOVERED_PER_CYCLE]:
        _remember_channel(conn, mention, origin)
        posts, error, html_pages = _read_channel(mention, pages=1)
        _store_channel(conn, report, mention, posts, error, html_pages, seeded=False)
    report.notes.append(
        "تلگرام: هر صفحه عمومی t.me/s حدود ۲۰ پیام تازه را برمی‌گرداند. "
        f"برای کانال‌های معرفی‌شده {HISTORY_PAGES} صفحه با پارامتر before خوانده می‌شود. "
        "گروه خصوصی، کانال بدون پیش‌نمایش، و تاریخ کامل بدون نشست کاربر در دسترس نیست."
    )


def _read_channel(handle: str, pages: int = HISTORY_PAGES) -> tuple[list[dict], str | None, list[str]]:
    if safe_handle(handle) is None:
        return [], "نام کانال برای پیش‌نمایش عمومی مجاز نیست", []
    posts: list[dict] = []
    html_pages: list[str] = []
    before = None
    error = None
    for _ in range(pages):
        url = preview_url(handle, before)
        result = fetch(url, timeout=20)
        if not result.ok:
            error = result.error or str(result.status_code)
            break
        html = decode_body(result.body)
        html_pages.append(html)
        batch = parse_telegram_channel(html, safe_handle(handle) or handle)
        if not batch:
            break
        for item in batch:
            item["page_url"] = url
        posts.extend(batch)
        ids = [item["external_id"].rsplit("/", 1)[-1] for item in batch]
        numeric = [value for value in ids if value.isdigit()]
        if not numeric:
            break
        oldest = str(min(int(value) for value in numeric))
        if before == oldest:
            break
        before = oldest
    return posts, error, html_pages


def _store_channel(conn, report, handle: str, posts: list[dict], error: str | None, html_pages: list[str], *, seeded: bool) -> None:
    started = iso_utc()
    source = conn.execute("SELECT * FROM sources WHERE handle = ?", (handle,)).fetchone()
    saved_posts = 0
    news = 0
    for item in posts:
        message_id = item["external_id"].rsplit("/", 1)[-1]
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO telegram_posts(
              handle, external_id, url, published_at, published_at_raw, text, fetched_at, page_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                handle,
                message_id,
                item.get("url"),
                item.get("published_at"),
                item.get("published_at_raw"),
                item.get("body") or item.get("title") or "",
                iso_utc(),
                item.get("page_url") or preview_url(handle),
            ),
        )
        saved_posts += cur.rowcount
        if source is not None and looks_market_relevant(f"{item['title']} {item['body']}"):
            if _insert_news(conn, source, item, verification_for(source)):
                news += 1
        elif source is None:
            report.skipped_irrelevant += 0
        else:
            report.skipped_irrelevant += 1
    report.news += news
    note = (
        f"@{handle}: {len(posts)} پیام در {len(html_pages)} صفحه، {saved_posts} پیام تازه در تاریخچه، {news} خبر مرتبط."
    )
    if error:
        note = f"{note} خطا: {error}"
        report.errors.append(f"telegram {handle}: {error}")
    elif not seeded:
        note = f"کانال تازه‌دیده در پیش‌نمایش عمومی. {note}"
    log_fetch(
        conn,
        source_key="telegram",
        channel="relay",
        url=preview_url(handle),
        started_at=started,
        ok=error is None and bool(html_pages),
        status_code=200 if html_pages else None,
        error=error,
        records=len(posts),
        notes=note,
    )
    if not seeded:
        conn.execute(
            """
            UPDATE discovered_channels
            SET preview_ok = ?, preview_error = ?, post_count = ?, notes = ?
            WHERE handle = ?
            """,
            (1 if html_pages else 0, error, len(posts), note, handle),
        )


def _remember_channel(conn: sqlite3.Connection, handle: str, origin: str) -> None:
    conn.execute(
        """
        INSERT INTO discovered_channels(handle, discovered_from, discovered_at, preview_ok, post_count, notes)
        VALUES (?, ?, ?, NULL, 0, ?)
        ON CONFLICT(handle) DO NOTHING
        """,
        (
            handle,
            origin,
            iso_utc(),
            "فقط از پیوند t.me داخل پیش‌نمایش عمومی پیدا شد. عضویت یا تاریخ خصوصی خوانده نشده است.",
        ),
    )
    exists = source_id(conn, f"tg-discovered-{handle.lower()}")
    if exists is not None:
        return
    conn.execute(
        """
        INSERT INTO sources(
          source_key, name, handle, url, group_name, specialty, conflict_of_interest,
          prior_level, prior_basis, notes, active, created_at
        ) VALUES (?, ?, ?, ?, 'fringe', 'کانال تازه‌دیده در پیش‌نمایش عمومی', 'unknown', 'C', ?, ?, 1, ?)
        """,
        (
            f"tg-discovered-{handle.lower()}",
            f"کانال کشف‌شده @{handle}",
            handle,
            f"https://t.me/s/{handle}",
            "پیشین سطح C چون این کانال در فهرست اولیه نبود و فقط پیوند عمومی‌اش دیده شده است.",
            "کشف خودکار از پیوند داخل پیش‌نمایش. رتبه عملکرد نیست.",
            iso_utc(),
        ),
    )
