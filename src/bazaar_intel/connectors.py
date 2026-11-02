"""تلاش مستقیم به مبدأ، و اگر میزبان جواب نداد مسیر جایگزینی که واقعاً داده برگرداند."""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote, urlparse

from bazaar_intel.db import log_fetch, source_id
from bazaar_intel.http_client import decode_body, fetch
from bazaar_intel.ingest import _insert_news, _insert_quote, _market_of, _upsert_instrument, parse_shakhesban_board
from bazaar_intel.textnorm import parse_decimal, parse_scaled
from bazaar_intel.timeutil import iso_utc, jalali_date_to_tehran_midnight_utc, jalali_datetime_to_utc
from bazaar_intel.universe import parse_shakhesban_sitemap, parse_symbol_snapshot

DIRECT_TIMEOUT = 8
DIRECT_TARGETS = (
    ("tsetmc", "https://www.tsetmc.com", "صفحه اصلی دیده‌بان"),
    (
        "tsetmc",
        "https://cdn.tsetmc.com/api/ClosingPrice/GetMarketWatch?market=0&paperTypes[0]=1&withBestLimits=false&hEven=0&RefID=0",
        "API دیده‌بان cdn.tsetmc.com",
    ),
    (
        "tsetmc",
        "https://old.tsetmc.com/tsev2/data/MarketWatchPlus.aspx",
        "خروجی قدیمی MarketWatchPlus",
    ),
    ("codal", "https://www.codal.ir", "صفحه اصلی کدال"),
    (
        "codal",
        "https://search.codal.ir/api/search/v2/q?PageNumber=1&search=true&Audited=true&AuditorRef=-1&Category=-1&Childs=true&CompanyState=-1&CompanyType=-1&Consolidatable=true&Length=-1&LetterType=-1&Mains=true&NotAudited=true&NotConsolidatable=true&Publisher=false&ReportingType=-1&TracingNo=-1",
        "جستجوی اطلاعیه‌های کدال",
    ),
    ("tse", "https://www.tse.ir", "سایت بورس تهران"),
    ("tse", "https://webgw.tse.ir", "درگاه webgw.tse.ir"),
    ("ifb", "https://www.ifb.ir", "سایت فرابورس"),
    ("ime", "https://www.ime.co.ir", "سایت بورس کالا"),
    ("irenex", "https://www.irenex.ir", "سایت بورس انرژی"),
    ("irenex", "https://irenex.ir", "میزبان بدون www بورس انرژی"),
    ("fipiran", "https://www.fipiran.com", "سایت فایپیران"),
    ("fipiran", "https://fipiran.ir", "میزبان fipiran.ir"),
)

ALLOWED_HOSTS = {
    "www.tsetmc.com",
    "cdn.tsetmc.com",
    "old.tsetmc.com",
    "tsetmc.com",
    "www.codal.ir",
    "codal.ir",
    "search.codal.ir",
    "www.tse.ir",
    "tse.ir",
    "webgw.tse.ir",
    "www.ifb.ir",
    "ifb.ir",
    "www.ime.co.ir",
    "ime.co.ir",
    "www.irenex.ir",
    "irenex.ir",
    "www.fipiran.com",
    "fipiran.com",
    "fipiran.ir",
    "www.fipiran.ir",
    "www.shakhesban.com",
    "shakhesban.com",
    "www.sahamyab.com",
    "sahamyab.com",
}

CODAL_PAGES = 4
SNAPSHOT_LIMIT = 8


def allowed_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in ALLOWED_HOSTS


def parse_codal_search(payload: dict) -> list[dict]:
    letters = payload.get("Letters")
    if not isinstance(letters, list):
        return []
    items = []
    for letter in letters:
        if not isinstance(letter, dict):
            continue
        title = str(letter.get("Title") or "").strip()
        url = str(letter.get("Url") or letter.get("UrlHtml") or "").strip()
        symbol = str(letter.get("Symbol") or "").strip()
        if not title or "codal.ir" not in url:
            continue
        items.append(
            {
                "title": title,
                "url": url,
                "symbol": symbol,
                "company": str(letter.get("CompanyName") or "").strip(),
                "published_raw": str(letter.get("PublishDateTime") or letter.get("SentDateTime") or ""),
                "external_id": str(letter.get("TracingNo") or url),
            }
        )
    return items


def parse_sahamyab_codal(payload: dict) -> list[dict]:
    rows = payload.get("result")
    if not isinstance(rows, list):
        return []
    items = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        url = str(row.get("url") or "").strip()
        symbol = str(row.get("namad") or "").strip()
        if not title or "codal.ir" not in url or not symbol:
            continue
        items.append(
            {
                "title": title,
                "url": url,
                "symbol": symbol,
                "company": str(row.get("corp") or "").strip(),
                "published_raw": str(row.get("publishDate") or ""),
                "external_id": url,
                "instrument_id": str(row.get("instrumentId") or ""),
            }
        )
    return items


def ingest_market_connections(conn: sqlite3.Connection, report) -> None:
    direct_ok = _probe_direct(conn, report)
    _ingest_sitemap(conn, report)
    symbols = _ingest_codal(conn, report, direct_ok)
    _ingest_symbol_context(conn, report, symbols)


def _probe_direct(conn: sqlite3.Connection, report) -> set[str]:
    opened = set()
    jobs = []
    for source_key, url, label in DIRECT_TARGETS:
        if not allowed_url(url):
            report.errors.append(f"{source_key}: نشانی خارج از فهرست مجاز رد شد")
            continue
        jobs.append((source_key, url, label, iso_utc()))

    def pull(job):
        source_key, url, label, started = job
        return job, fetch(url, timeout=DIRECT_TIMEOUT)

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(pull, job) for job in jobs]
        for future in as_completed(futures):
            (source_key, url, label, started), result = future.result()
            records = 0
            notes = label
            parsed = []
            if result.ok and source_key == "codal" and b"Letters" in result.body:
                try:
                    parsed = parse_codal_search(json.loads(decode_body(result.body)))
                    records = len(parsed)
                except json.JSONDecodeError:
                    parsed = []
                notes = f"{label}. پاسخ JSON کدال شناسایی شد."
            elif result.ok:
                notes = f"{label}. پاسخ {len(result.body)} بایت بود و به‌عنوان فهرست نماد یا متن اطلاعیه تفسیر نشد."
            log_fetch(
                conn,
                source_key=source_key,
                channel="direct",
                url=url,
                started_at=started,
                ok=result.ok,
                status_code=result.status_code,
                error=result.error,
                records=records,
                notes=notes,
            )
            if result.ok:
                opened.add(source_key)
                if parsed:
                    _store_codal_items(conn, report, parsed, relay=False)
            else:
                report.errors.append(f"{source_key} direct {url}: {result.error or result.status_code}")
    if opened:
        report.notes.append("اتصال مستقیم موفق: " + "، ".join(sorted(opened)))
    else:
        report.notes.append("هیچ‌کدام از میزبان‌های مستقیم TSETMC، کدال، بورس تهران، فرابورس، کالا، انرژی و فایپیران در این تلاش پاسخ قابل استفاده ندادند.")
    return opened


def _ingest_sitemap(conn: sqlite3.Connection, report) -> None:
    url = "https://www.shakhesban.com/sitemap.xml"
    started = iso_utc()
    result = fetch(url, timeout=40)
    if not result.ok:
        log_fetch(
            conn,
            source_key="shakhesban",
            channel="relay",
            url=url,
            started_at=started,
            ok=False,
            status_code=result.status_code,
            error=result.error,
            records=0,
            notes="نقشه سایت برای فهرست نمادها باز نشد.",
        )
        report.errors.append(f"sitemap: {result.error or result.status_code}")
        return
    fresh, items = parse_shakhesban_sitemap(decode_body(result.body))
    counts: dict[str, int] = {}
    for item in items:
        counts[item["asset_class"]] = counts.get(item["asset_class"], 0) + 1
        _catalog_instrument(
            conn,
            symbol=item["symbol"],
            name=item["name"],
            asset_class=item["asset_class"],
            option_type=item["option_type"],
            provider_key=item["url"],
            source_note=(
                "نشانی در نقشه سایت شاخص‌بان. این فهرست رسمی TSETMC نیست و تا تطبیق با فایل مبدأ، "
                f"کامل علامت نخورده است. آخرین lastmod دیده‌شده: {fresh or 'نبود'}"
            ),
        )
    note = "نقشه سایت شاخص‌بان: " + "، ".join(f"{key} {value}" for key, value in sorted(counts.items()))
    if fresh:
        note = f"{note}. تازه‌ترین lastmod: {fresh}"
    log_fetch(
        conn,
        source_key="shakhesban",
        channel="relay",
        url=url,
        started_at=started,
        ok=True,
        status_code=result.status_code,
        error=None,
        records=len(items),
        notes=note,
    )
    report.instruments += len(items)
    report.notes.append(note + ". بازار بورس یا فرابورس فقط وقتی نوشته می‌شود که خود ردیف تابلو آن را گفته باشد.")


def _ingest_codal(conn: sqlite3.Connection, report, direct_ok: set[str]) -> list[str]:
    if "codal" in direct_ok:
        report.notes.append("کدال مستقیم پاسخ داد؛ مسیر سهامیاب در این چرخه برای نامه تکراری لازم نبود.")
        return []
    symbols: list[str] = []
    saved = 0
    for page in range(CODAL_PAGES):
        url = f"https://www.sahamyab.com/api/proxy/symbol/getCodal?v=0.1&page={page}"
        started = iso_utc()
        result = fetch(url, timeout=25)
        if not result.ok:
            log_fetch(
                conn,
                source_key="codal",
                channel="relay",
                url=url,
                started_at=started,
                ok=False,
                status_code=result.status_code,
                error=result.error,
                records=0,
                notes=f"صفحه {page} مسیر جایگزین سهامیاب باز نشد. این پاسخ خود codal.ir نیست.",
            )
            report.errors.append(f"codal relay page {page}: {result.error or result.status_code}")
            continue
        try:
            payload = json.loads(decode_body(result.body))
        except json.JSONDecodeError as exc:
            log_fetch(
                conn,
                source_key="codal",
                channel="relay",
                url=url,
                started_at=started,
                ok=False,
                status_code=result.status_code,
                error=str(exc),
                records=0,
                notes="بدنه JSON نبود.",
            )
            continue
        items = parse_sahamyab_codal(payload)
        stored = _store_codal_items(conn, report, items, relay=True)
        saved += stored
        for item in items:
            if item["symbol"] not in symbols:
                symbols.append(item["symbol"])
        log_fetch(
            conn,
            source_key="codal",
            channel="relay",
            url=url,
            started_at=started,
            ok=bool(items),
            status_code=result.status_code,
            error=None if items else "فهرست نامه خالی بود",
            records=len(items),
            notes=(
                f"صفحه {page} از API سهامیاب. پیوندها به codal.ir هستند اما متن نامه از آن میزبان دانلود نشده است. "
                f"ذخیره تازه: {stored}."
            ),
        )
    report.notes.append(f"کدال از مسیر سهامیاب: {saved} عنوان تازه. متن نامه از خود کدال خوانده نشد.")
    return symbols


def _store_codal_items(conn, report, items: list[dict], *, relay: bool) -> int:
    source_key = "sahamyab" if relay else "codal"
    source = conn.execute("SELECT * FROM sources WHERE source_key = ?", (source_key,)).fetchone()
    if source is None:
        report.errors.append(f"codal source missing: {source_key}")
        return 0
    saved = 0
    for item in items:
        published = jalali_datetime_to_utc(item["published_raw"]) if item.get("published_raw") else None
        body = (
            f"نماد: {item['symbol']}. شرکت: {item.get('company') or 'در پاسخ منبع نیامده'}. "
            "این ردیف عنوان اطلاعیه است. واژه کدال در پیوند آمده و متن نامه در این دریافت خوانده نشد."
        )
        news = {
            "title": item["title"],
            "body": body,
            "url": item["url"],
            "external_id": item["external_id"],
            "published_at": published,
            "published_at_raw": item.get("published_raw") or "",
        }
        if item["symbol"]:
            _catalog_instrument(
                conn,
                symbol=item["symbol"],
                name=item.get("company") or item["symbol"],
                asset_class="equity",
                provider_key=item.get("instrument_id") or None,
                source_note="نماد همراه عنوان اطلاعیه. بازار بورس یا فرابورس در همین پاسخ نیامده است.",
            )
        verification = "relayed_official" if relay else "official_confirmed"
        if _insert_news(conn, source, news, verification, extra_tokens=[item["symbol"]] if item["symbol"] else None):
            saved += 1
            report.news += 1
    return saved


def _ingest_symbol_context(conn, report, symbols: list[str]) -> None:
    sid = source_id(conn, "shakhesban")
    seen = 0
    for symbol in symbols:
        if seen >= SNAPSHOT_LIMIT:
            break
        seen += 1
        search_url = "https://www.shakhesban.com/markets/all?search=" + quote(symbol)
        started = iso_utc()
        result = fetch(search_url, timeout=25)
        if not result.ok:
            log_fetch(
                conn,
                source_key="shakhesban",
                channel="relay",
                url=search_url,
                started_at=started,
                ok=False,
                status_code=result.status_code,
                error=result.error,
                records=0,
                notes=f"جستجوی تابلو برای {symbol} باز نشد.",
            )
            report.errors.append(f"symbol search {symbol}: {result.error or result.status_code}")
            continue
        rows = [row for row in parse_shakhesban_board(decode_body(result.body)) if row["symbol"] == symbol and row.get("parsed")]
        log_fetch(
            conn,
            source_key="shakhesban",
            channel="relay",
            url=search_url,
            started_at=started,
            ok=bool(rows),
            status_code=result.status_code,
            error=None if rows else "ردیف همین نماد در نتیجه جستجو نبود",
            records=len(rows),
            notes=f"جستجوی تابلو برای {symbol}. قیمت فقط از سلولی آمده که تجزیه‌گر جدول قبول کرده است.",
        )
        for row in rows[:3]:
            market, asset_class = _market_of(row.get("venue"), row.get("asset_type"), "stock_board")
            instrument = _upsert_instrument(
                conn,
                symbol=row["symbol"],
                name=row.get("name"),
                market=market,
                asset_class=asset_class,
                venue=row.get("venue"),
                source_note="قیمت از نتیجه جستجوی تابلوی شاخص‌بان، نه از TSETMC.",
            )
            observed = jalali_date_to_tehran_midnight_utc(row["trade_date"]) if row.get("trade_date") else None
            volume, _ = parse_scaled(row.get("volume_raw"))
            value, _ = parse_scaled(row.get("value_raw"))
            if _insert_quote(
                conn,
                instrument_id=instrument,
                observed_at=observed,
                observed_at_raw=row.get("trade_date") or search_url,
                timezone_note="تاریخ معامله شمسی جدول است و به نیمه‌شب تهران همان روز وصل شد",
                price=row["last"],
                change_abs=row.get("last_change"),
                change_pct=row.get("last_pct"),
                high_price=row.get("high"),
                low_price=row.get("low"),
                open_price=row.get("open_price"),
                previous_close=row.get("previous_close"),
                volume_raw=row.get("volume_raw"),
                value_raw=row.get("value_raw"),
                volume=volume,
                trade_value=value,
                source_id=sid,
                source_label=f"shakhesban:search:{symbol}",
                quality="as_provided",
                raw={key: value for key, value in row.items() if key != "cells"},
            ):
                report.quotes += 1
        page_url = "https://www.shakhesban.com/markets/stock/" + quote(symbol)
        page_started = iso_utc()
        page = fetch(page_url, timeout=25)
        if not page.ok:
            log_fetch(
                conn,
                source_key="shakhesban",
                channel="relay",
                url=page_url,
                started_at=page_started,
                ok=False,
                status_code=page.status_code,
                error=page.error,
                records=0,
                notes=f"صفحه نماد {symbol} باز نشد؛ رقم سود ساخته نشد.",
            )
            continue
        snapshot = parse_symbol_snapshot(decode_body(page.body))
        metrics = []
        instrument_id = _instrument_for_fundamental(conn, symbol)
        if instrument_id is not None and snapshot.get("profit_raw"):
            if _insert_fundamental(conn, instrument_id, "realized_profit", snapshot["profit_raw"], page_url):
                metrics.append("realized_profit")
        if instrument_id is not None and snapshot.get("pe_ttm_raw"):
            if _insert_fundamental(conn, instrument_id, "pe_ttm", snapshot["pe_ttm_raw"], page_url):
                metrics.append("pe_ttm")
        price = snapshot.get("close") if snapshot.get("parsed") else None
        if instrument_id is not None and price and parse_decimal(str(price)):
            if _insert_quote(
                conn,
                instrument_id=instrument_id,
                observed_at=None,
                observed_at_raw=f"symbol-page:{symbol}",
                timezone_note="صفحه نماد تاریخ معامله جداگانه نداشت؛ این قیمت برای ارزیابی زمانی استفاده نمی‌شود",
                price=price,
                change_abs=snapshot.get("change_abs"),
                high_price=snapshot.get("high"),
                low_price=snapshot.get("low"),
                open_price=snapshot.get("open_price"),
                volume_raw=snapshot.get("volume_raw"),
                source_id=sid,
                source_label=f"shakhesban:symbol:{symbol}",
                quality="as_provided",
                raw=snapshot,
            ):
                report.quotes += 1
        log_fetch(
            conn,
            source_key="shakhesban",
            channel="relay",
            url=page_url,
            started_at=page_started,
            ok=bool(snapshot.get("parsed") or metrics),
            status_code=page.status_code,
            error=None if snapshot.get("parsed") or metrics else "جدول قیمت یا سود در صفحه پیدا نشد",
            records=1 if snapshot.get("parsed") else 0,
            notes=f"صفحه نماد {symbol}. سنجه‌های ذخیره‌شده: {', '.join(metrics) or 'هیچ'}.",
        )


def _catalog_instrument(conn, **fields) -> int:
    return _upsert_instrument(
        conn,
        symbol=fields["symbol"],
        name=fields.get("name"),
        market="unspecified",
        asset_class=fields["asset_class"],
        option_type=fields.get("option_type"),
        provider_key=fields.get("provider_key"),
        source_note=fields["source_note"],
    )


def _instrument_for_fundamental(conn, symbol: str) -> int | None:
    row = conn.execute(
        """
        SELECT id FROM instruments
        WHERE symbol = ?
        ORDER BY CASE market WHEN 'unspecified' THEN 1 ELSE 0 END, id
        LIMIT 1
        """,
        (symbol,),
    ).fetchone()
    return None if row is None else int(row["id"])


def _insert_fundamental(conn, instrument_id: int, metric: str, value_raw: str, source_label: str) -> bool:
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO fundamental_observations(
          instrument_id, observed_at_raw, metric, value_raw, source_label, fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (instrument_id, "symbol-page", metric, value_raw, source_label, iso_utc()),
    )
    return cur.rowcount > 0
