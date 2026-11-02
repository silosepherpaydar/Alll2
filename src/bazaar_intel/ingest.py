"""جمع‌آوری فقط از پاسخ واقعی منابع. عدد یا خبری ساخته نمی‌شود."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from xml.etree import ElementTree

from bazaar_intel.db import source_id
from bazaar_intel.http_client import decode_body, fetch
from bazaar_intel.sources_seed import IME_PAGES, RSS_FEEDS, SHAKHESBAN_BOARDS, TELEGRAM_HANDLES
from bazaar_intel.textnorm import (
    content_hash,
    fingerprint,
    hashtags,
    industry_tags,
    jaccard,
    looks_market_relevant,
    parse_decimal,
    parse_scaled,
    tokens,
)
from bazaar_intel.timeutil import (
    TEHRAN,
    iso_utc,
    jalali_date_to_tehran_midnight_utc,
    utc_now,
)

TAG_RE = re.compile(r"<[^>]+>")
TGJU_EXACT = {
    "bourse": ("شاخص کل", "index", "نماگر TGJU با کلید bourse؛ این چاپ رسمی TSETMC نیست"),
    "oil_brent": ("نفت برنت", "commodity", "کلید oil_brent"),
    "oil_opec": ("نفت اوپک", "commodity", "کلید oil_opec"),
    "ons": ("انس طلا", "commodity", "کلید ons"),
    "price_dollar_rl": ("دلار", "fx", "نرخ آزاد TGJU، نرخ رسمی بانک مرکزی نیست"),
    "price_eur": ("یورو", "fx", "نرخ TGJU"),
    "geram18": ("گرم طلای ۱۸ عیار", "commodity", "کلید geram18"),
    "mesghal": ("مثقال طلا", "commodity", "کلید mesghal"),
    "sekee": ("سکه امامی", "commodity", "کلید sekee"),
    "basemetal-london-copper": ("مس لندن", "commodity", "کلید basemetal-london-copper"),
    "basemetal-copper": ("مس", "commodity", "کلید basemetal-copper"),
    "basemetal-aluminum": ("آلومینیوم", "commodity", "کلید basemetal-aluminum"),
    "basemetal-steel": ("فولاد", "commodity", "کلید basemetal-steel"),
    "ime_certificate_gold_bars": ("گواهی شمش طلا", "certificate", "کلید ime_certificate_gold_bars"),
    "ime_certificate_coin": ("گواهی سکه", "certificate", "کلید ime_certificate_coin"),
    "ime_certificate_silver_bars": ("گواهی شمش نقره", "certificate", "کلید ime_certificate_silver_bars"),
}
COPY_THRESHOLD = 0.72


@dataclass
class IngestReport:
    instruments: int = 0
    quotes: int = 0
    news: int = 0
    skipped_irrelevant: int = 0
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def plain(fragment: str) -> str:
    text = TAG_RE.sub(" ", fragment)
    text = unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def parse_shakhesban_board(html: str) -> list[dict]:
    """تابلوی شاخص‌بان. اگر تعداد ستون عوض شده باشد قیمت نسبت داده نمی‌شود."""
    found = []
    for symbol, inner in re.findall(r'<tr data-symbol="([^"]+)">(.*?)</tr>', html, flags=re.S):
        cells = [plain(td) for td in re.findall(r"<td[^>]*>(.*?)</td>", inner, flags=re.S)]
        item = {"symbol": plain(symbol), "cells": cells, "parsed": False}
        if len(cells) >= 19 and cells[0] == item["symbol"] and parse_decimal(cells[4]) is not None:
            item.update(
                {
                    "parsed": True,
                    "name": cells[1],
                    "asset_type": cells[2],
                    "venue": cells[3],
                    "last": parse_decimal(cells[4]),
                    "last_change": parse_decimal(cells[5]),
                    "last_pct": parse_decimal(cells[6]),
                    "close": parse_decimal(cells[7]),
                    "previous_close": parse_decimal(cells[15]),
                    "open_price": parse_decimal(cells[16]),
                    "low": parse_decimal(cells[17]),
                    "high": parse_decimal(cells[18]),
                    "trade_date": cells[10],
                    "volume_raw": cells[12],
                    "value_raw": cells[13],
                }
            )
        found.append(item)
    return found


def parse_ime_board(html: str) -> list[dict]:
    found = []
    for inner in re.findall(r'<tr class="ime-row">(.*?)</tr>', html, flags=re.S):
        cells = [plain(td) for td in re.findall(r"<td[^>]*>(.*?)</td>", inner, flags=re.S)]
        if len(cells) < 6:
            continue
        change_abs, change_pct = _split_change(cells[3] if len(cells) > 3 else "")
        found.append(
            {
                "symbol": cells[0],
                "name": cells[1] if len(cells) > 1 else cells[0],
                "last": parse_decimal(cells[2]) if len(cells) > 2 else None,
                "change_abs": change_abs,
                "change_pct": change_pct,
                "clock": cells[4] if len(cells) > 4 else "",
                "close": parse_decimal(cells[5]) if len(cells) > 5 else None,
                "open_price": parse_decimal(cells[6]) if len(cells) > 6 else None,
                "low": parse_decimal(cells[7]) if len(cells) > 7 else None,
                "high": parse_decimal(cells[8]) if len(cells) > 8 else None,
                "volume_raw": cells[10] if len(cells) > 10 else None,
                "value_raw": cells[11] if len(cells) > 11 else None,
                "parsed": parse_decimal(cells[2]) is not None if len(cells) > 2 else False,
            }
        )
    return found


def _split_change(text: str) -> tuple[str | None, str | None]:
    match = re.search(r"([-+0-9,\.]+)\s*\(([-+0-9,\.]+)%\)", text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")))
    if not match:
        return parse_decimal(text), None
    return parse_decimal(match.group(1)), parse_decimal(match.group(2))


def parse_tgju_payload(payload: dict) -> list[dict]:
    current = payload.get("current") if isinstance(payload, dict) else None
    if not isinstance(current, dict):
        return []
    cutoff = utc_now() - timedelta(days=21)
    rows = []
    for key, raw in current.items():
        if not isinstance(raw, dict) or "p" not in raw:
            continue
        keep = key in TGJU_EXACT or key.startswith("ime_")
        if not keep:
            continue
        observed, zone_note, fresh = _tgju_time(str(raw.get("ts") or ""), cutoff)
        if key.startswith("ime_") and not fresh:
            continue
        price = parse_decimal(str(raw.get("p")))
        if price is None:
            continue
        meta = TGJU_EXACT.get(key)
        rows.append(
            {
                "key": key,
                "symbol": meta[0] if meta else key,
                "asset_class": meta[1] if meta else _tgju_class(key),
                "note": meta[2] if meta else f"کلید {key}",
                "price": price,
                "change_pct": parse_decimal(str(raw.get("dp"))) if raw.get("dp") is not None else None,
                "high": parse_decimal(str(raw.get("h"))) if raw.get("h") is not None else None,
                "low": parse_decimal(str(raw.get("l"))) if raw.get("l") is not None else None,
                "observed_at": observed,
                "observed_at_raw": str(raw.get("ts") or ""),
                "timezone_note": zone_note,
                "raw": raw,
            }
        )
    return rows


def _ime_class(path: str) -> str:
    if path.endswith("option"):
        return "option"
    if path.endswith("future"):
        return "futures"
    if path.endswith("sandoq"):
        return "fund"
    if path.endswith("gavahi"):
        return "certificate"
    if path.endswith("salaf"):
        return "salaf"
    return "other"


def _tgju_class(key: str) -> str:
    if "option" in key:
        return "option"
    if "futures" in key:
        return "futures"
    if "fund" in key:
        return "fund"
    if "certificate" in key:
        return "certificate"
    if "indices" in key:
        return "index"
    return "other"


def _tgju_time(ts: str, cutoff: datetime) -> tuple[str | None, str, bool]:
    text = ts.strip()
    try:
        naive = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None, "زمان خام TGJU قابل خواندن نیست", False
    local = naive.replace(tzinfo=TEHRAN)
    moment = local.astimezone(timezone.utc).replace(microsecond=0)
    note = "زمان خام TGJU منطقه نداشت؛ با ساعت تهران تفسیر شد"
    if naive.hour == 0 and naive.minute == 0 and naive.second == 0:
        note = "TGJU فقط تاریخ داده است؛ به نیمه‌شب تهران همان روز وصل شد"
    return moment.isoformat(), note, moment >= cutoff


def parse_rss(xml_text: str) -> list[dict]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []
    items = []
    nodes = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    for node in nodes:
        title = _xml_text(node, "title")
        link = _xml_text(node, "link") or _atom_link(node)
        description = _xml_text(node, "description") or _xml_text(node, "{http://purl.org/rss/1.0/modules/content/}encoded")
        if not description:
            description = _xml_text(node, "{http://www.w3.org/2005/Atom}summary") or _xml_text(node, "{http://www.w3.org/2005/Atom}content")
        published = _xml_text(node, "pubDate") or _xml_text(node, "{http://www.w3.org/2005/Atom}updated")
        if not title:
            continue
        items.append(
            {
                "title": plain(title),
                "url": link,
                "body": plain(description or ""),
                "published_at": _parse_http_date(published),
                "published_at_raw": published,
            }
        )
    return items


def parse_telegram_channel(html: str, handle: str) -> list[dict]:
    messages = []
    chunks = re.findall(
        r'data-post="([^"]+)"[\s\S]*?<time[^>]*datetime="([^"]+)"[\s\S]*?tgme_widget_message_text[^>]*>([\s\S]*?)</div>',
        html,
    )
    if not chunks:
        chunks = []
        for post, block in re.findall(r'data-post="([^"]+)"([\s\S]*?)(?=data-post="|$)', html):
            time_match = re.search(r'<time[^>]*datetime="([^"]+)"', block)
            text_match = re.search(r'tgme_widget_message_text[^>]*>([\s\S]*?)</div>', block)
            if time_match and text_match:
                chunks.append((post, time_match.group(1), text_match.group(1)))
    for post, stamp, text in chunks:
        message_id = post.split("/")[-1]
        messages.append(
            {
                "external_id": post,
                "url": f"https://t.me/{handle}/{message_id}",
                "published_at": stamp,
                "published_at_raw": stamp,
                "title": _title_from_body(plain(text)),
                "body": plain(text),
            }
        )
    return messages


def _title_from_body(body: str) -> str:
    compact = body.strip()
    return compact[:180] if compact else "(بدون متن)"


def _xml_text(node: ElementTree.Element, name: str) -> str:
    child = node.find(name)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _atom_link(node: ElementTree.Element) -> str:
    for child in node.findall("{http://www.w3.org/2005/Atom}link"):
        href = child.attrib.get("href")
        if href:
            return href
    return ""


def _parse_http_date(value: str) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def ingest_all(conn: sqlite3.Connection) -> IngestReport:
    report = IngestReport()
    from bazaar_intel.connectors import ingest_market_connections
    from bazaar_intel.telegram_watch import ingest_telegram_history

    ingest_market_connections(conn, report)
    _ingest_tgju(conn, report)
    _ingest_shakhesban(conn, report)
    _ingest_rss(conn, report)
    ingest_telegram_history(conn, report)
    conn.commit()
    return report


def _ingest_tgju(conn: sqlite3.Connection, report: IngestReport) -> None:
    result = fetch("https://call1.tgju.org/ajax.json", timeout=30)
    if not result.ok:
        result = fetch("https://call1.tgju.org/ajax.json", timeout=30)
    if not result.ok:
        report.errors.append(f"tgju: {result.error or result.status_code}")
        return
    try:
        payload = json.loads(decode_body(result.body))
    except json.JSONDecodeError as exc:
        report.errors.append(f"tgju json: {exc}")
        return
    rows = parse_tgju_payload(payload)
    sid = source_id(conn, "tgju")
    for row in rows:
        instrument = _upsert_instrument(
            conn,
            symbol=row["symbol"],
            name=row["symbol"],
            market="tgju",
            asset_class=row["asset_class"],
            provider_key=row["key"],
            source_note=row["note"],
        )
        if _insert_quote(
            conn,
            instrument_id=instrument,
            observed_at=row["observed_at"],
            observed_at_raw=row["observed_at_raw"] or row["key"],
            timezone_note=row["timezone_note"],
            price=row["price"],
            change_pct=row["change_pct"],
            high_price=row["high"],
            low_price=row["low"],
            source_id=sid,
            source_label=f"tgju:{row['key']}",
            quality="as_provided",
            raw=row["raw"],
        ):
            report.quotes += 1
    report.instruments += len(rows)
    report.notes.append(f"TGJU: {len(rows)} کلید تازه یا منتخب ذخیره شد.")
    _ingest_tgju_profile_conflict(conn, report, sid)


def _ingest_tgju_profile_conflict(conn: sqlite3.Connection, report: IngestReport, sid: int | None) -> None:
    page = fetch("https://www.tgju.org/profile/bourse")
    if not page.ok:
        report.errors.append(f"tgju profile/bourse: {page.error or page.status_code}")
        return
    html = decode_body(page.body)
    price_match = re.search(r'data-col="info\.last_trade\.PDrCotVal">([^<]+)', html)
    date_match = re.search(r"زمان ثبت آخرین نرخ.{0,80}?(\d{1,2}\s+[^\s<]+)", html, flags=re.S)
    if not price_match:
        report.notes.append("صفحه پروفایل شاخص TGJU قیمت قابل استخراج نداشت.")
        return
    price = parse_decimal(plain(price_match.group(1)))
    if price is None:
        return
    ajax = conn.execute(
        """
        SELECT q.price FROM quotes q
        JOIN instruments i ON i.id = q.instrument_id
        WHERE i.provider_key = 'bourse' AND q.source_label = 'tgju:bourse'
        ORDER BY q.id DESC LIMIT 1
        """
    ).fetchone()
    quality = "as_provided"
    if ajax is not None:
        ajax_price = float(ajax["price"])
        profile_price = float(price)
        if ajax_price and abs(profile_price - ajax_price) / ajax_price > 0.005:
            quality = "conflicting"
    instrument = _upsert_instrument(
        conn,
        symbol="شاخص کل",
        name="شاخص بورس تهران در صفحه پروفایل TGJU",
        market="tgju",
        asset_class="index",
        provider_key="bourse",
        source_note="صفحه profile/bourse. اگر با خوراک ajax فاصله داشته باشد متعارض علامت می‌خورد.",
    )
    raw_date = plain(date_match.group(1)) if date_match else ""
    if _insert_quote(
        conn,
        instrument_id=instrument,
        observed_at=None,
        observed_at_raw=raw_date or "profile/bourse",
        timezone_note="تاریخ صفحه پروفایل منطقه دقیق ندارد و به ساعت تبدیل نشد",
        price=price,
        source_id=sid,
        source_label="tgju:profile/bourse",
        quality=quality,
        raw={"price": price, "page_date": raw_date},
    ):
        report.quotes += 1
    if quality == "conflicting":
        report.notes.append(
            f"تعارض شاخص: خوراک ajax و صفحه profile/bourse بیش از ۰٫۵ درصد فاصله دارند. هیچ‌کدام به‌تنهایی قیمت رسمی اعلام نشد."
        )


def _ingest_shakhesban(conn: sqlite3.Connection, report: IngestReport) -> None:
    sid = source_id(conn, "shakhesban")
    for path, board in SHAKHESBAN_BOARDS:
        result = fetch(f"https://www.shakhesban.com{path}")
        if not result.ok:
            report.errors.append(f"shakhesban {path}: {result.error or result.status_code}")
            continue
        rows = parse_shakhesban_board(decode_body(result.body))
        parsed = 0
        for row in rows:
            market, asset_class = _market_of(row.get("venue"), row.get("asset_type"), board)
            instrument = _upsert_instrument(
                conn,
                symbol=row["symbol"],
                name=row.get("name"),
                market=market,
                asset_class=asset_class,
                venue=row.get("venue"),
                source_note=f"دیده شده در نمای عمومی {path}؛ این فهرست کامل بازار نیست",
            )
            if not row.get("parsed") or not row.get("last"):
                continue
            observed = jalali_date_to_tehran_midnight_utc(row["trade_date"]) if row.get("trade_date") else None
            volume, _ = parse_scaled(row.get("volume_raw"))
            value, _ = parse_scaled(row.get("value_raw"))
            if _insert_quote(
                conn,
                instrument_id=instrument,
                observed_at=observed,
                observed_at_raw=row.get("trade_date") or path,
                timezone_note="تاریخ معامله شمسی است؛ ساعت معامله در جدول نبود و به نیمه‌شب تهران همان روز وصل شد",
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
                source_label=f"shakhesban:{path}",
                quality="as_provided",
                raw={k: v for k, v in row.items() if k != "cells"},
            ):
                report.quotes += 1
                parsed += 1
        report.notes.append(f"شاخص‌بان {path}: {len(rows)} ردیف، {parsed} قیمت قابل‌خواندن.")
    for path in IME_PAGES:
        result = fetch(f"https://www.shakhesban.com{path}")
        if not result.ok:
            report.errors.append(f"shakhesban {path}: {result.error or result.status_code}")
            continue
        rows = parse_ime_board(decode_body(result.body))
        saved = 0
        for row in rows:
            instrument = _upsert_instrument(
                conn,
                symbol=row["symbol"],
                name=row["name"],
                market="ime",
                asset_class=_ime_class(path),
                venue="بورس کالا",
                source_note=f"دیده شده در {path}",
            )
            if not row["parsed"] or not row["last"]:
                continue
            volume, _ = parse_scaled(row.get("volume_raw"))
            value, _ = parse_scaled(row.get("value_raw"))
            if _insert_quote(
                conn,
                instrument_id=instrument,
                observed_at=None,
                observed_at_raw=row.get("clock") or path,
                timezone_note="جدول بورس کالا فقط ساعت آخرین معامله را داشت، نه تاریخ؛ این ساعت به روز مشخصی وصل نشد",
                price=row["last"],
                change_abs=row.get("change_abs"),
                change_pct=row.get("change_pct"),
                high_price=row.get("high"),
                low_price=row.get("low"),
                open_price=row.get("open_price"),
                volume_raw=row.get("volume_raw"),
                value_raw=row.get("value_raw"),
                volume=volume,
                trade_value=value,
                source_id=sid,
                source_label=f"shakhesban:{path}",
                quality="as_provided",
                raw=row,
            ):
                saved += 1
        report.quotes += saved
        report.notes.append(f"شاخص‌بان {path}: {len(rows)} ردیف کالا، {saved} قیمت.")


def _market_of(venue: str | None, asset_type: str | None, board: str) -> tuple[str, str]:
    venue = venue or ""
    asset = asset_type or ""
    if "فرابورس" in venue:
        market = "ifb"
    elif "بورس" in venue:
        market = "tse"
    elif board == "bond_board":
        market = "debt"
    elif board == "future_board":
        market = "derivatives"
    else:
        market = "unknown"
    mapping = {
        "سهام": "equity",
        "صندوق": "fund",
        "اوراق": "bond",
        "آتی": "futures",
        "اختیار": "option",
        "حق تقدم": "warrant",
    }
    asset_class = "other"
    for needle, label in mapping.items():
        if needle in asset:
            asset_class = label
            break
    if asset_class == "other":
        asset_class = {
            "stock_board": "equity",
            "fund_board": "fund",
            "future_board": "futures",
            "bond_board": "bond",
            "option_board": "option",
        }.get(board, "other")
    return market, asset_class


def _ingest_rss(conn: sqlite3.Connection, report: IngestReport) -> None:
    for key, url, market_only in RSS_FEEDS:
        sid = source_id(conn, key)
        if sid is None:
            report.errors.append(f"rss source missing: {key}")
            continue
        result = fetch(url)
        if not result.ok:
            report.errors.append(f"rss {key}: {result.error or result.status_code}")
            continue
        source = conn.execute("SELECT * FROM sources WHERE id = ?", (sid,)).fetchone()
        count = 0
        for item in parse_rss(decode_body(result.body)):
            text = f"{item['title']} {item['body']}"
            if market_only and not looks_market_relevant(text):
                report.skipped_irrelevant += 1
                continue
            if _insert_news(conn, source, item, verification_for(source)):
                count += 1
        report.news += count
        report.notes.append(f"خبر {key}: {count} مورد مرتبط ذخیره شد.")


def _ingest_telegram(conn: sqlite3.Connection, report: IngestReport) -> None:
    for handle in TELEGRAM_HANDLES:
        source = conn.execute("SELECT * FROM sources WHERE handle = ?", (handle,)).fetchone()
        if source is None:
            report.errors.append(f"telegram source missing: {handle}")
            continue
        result = fetch(f"https://t.me/s/{handle}")
        if not result.ok:
            report.errors.append(f"telegram {handle}: {result.error or result.status_code}")
            continue
        messages = parse_telegram_channel(decode_body(result.body), handle)
        if not messages and "tgme_widget_message" not in decode_body(result.body):
            report.notes.append(f"تلگرام @{handle}: پیش‌نمایش عمومی پیامی برنگرداند.")
        count = 0
        for item in messages:
            if not looks_market_relevant(f"{item['title']} {item['body']}"):
                report.skipped_irrelevant += 1
                continue
            if _insert_news(conn, source, item, verification_for(source)):
                count += 1
        report.news += count


def verification_for(source: sqlite3.Row) -> str:
    if source["group_name"] == "fringe":
        return "rumor"
    if source["group_name"] == "media":
        return "reputable_media"
    if source["group_name"] == "official":
        return "official_confirmed"
    return "single_specialist"


def _insert_news(
    conn: sqlite3.Connection,
    source: sqlite3.Row,
    item: dict,
    verification: str,
    extra_tokens: list[str] | None = None,
) -> bool:
    body = item.get("body") or ""
    title = item["title"]
    digest = content_hash(f"{source['id']}|{title}|{body}")
    exists = conn.execute(
        "SELECT id FROM news_items WHERE source_id = ? AND content_hash = ?",
        (source["id"], digest),
    ).fetchone()
    if exists:
        return False
    primary = _matching_primary(conn, title, body)
    relevance = "market"
    confidence = "low" if verification in {"rumor", "single_specialist"} else "medium"
    opposing = None
    if verification == "rumor":
        opposing = "این متن از منبع سطح C است و به‌تنهایی واقعیت محسوب نمی‌شود."
    elif source["source_key"] in {"tg-codalscan", "tg-codal360"}:
        opposing = "متن از کانال بازنشر کدال است. خود کدال در این اجرا باز نشد، پس تأیید رسمی مستقیم نیست."
        verification = "single_specialist"
    cur = conn.execute(
        """
        INSERT INTO news_items (
          source_id, primary_news_id, url, external_id, title, body, published_at,
          published_at_raw, fetched_at, content_hash, fingerprint, verification_class,
          confidence, supporting_evidence, opposing_evidence, is_republication,
          industry_tags, relevance
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source["id"],
            primary,
            item.get("url"),
            item.get("external_id"),
            title,
            body,
            item.get("published_at"),
            item.get("published_at_raw"),
            iso_utc(),
            digest,
            fingerprint(f"{title} {body}"),
            verification,
            confidence,
            f"منبع: {source['name']}",
            opposing,
            1 if primary else 0,
            "، ".join(industry_tags(f"{title} {body}")) or None,
            relevance,
        ),
    )
    news_id = int(cur.lastrowid)
    seen = set()
    for token in [*hashtags(f"{title} {body}"), *(extra_tokens or [])]:
        if token in seen:
            continue
        seen.add(token)
        instrument = conn.execute(
            "SELECT id FROM instruments WHERE symbol = ? ORDER BY id LIMIT 1",
            (token,),
        ).fetchone()
        conn.execute(
            "INSERT INTO news_mentions(news_id, token, instrument_id, created_at) VALUES (?, ?, ?, ?)",
            (news_id, token, None if instrument is None else instrument["id"], iso_utc()),
        )
    return True


def _matching_primary(conn: sqlite3.Connection, title: str, body: str) -> int | None:
    fresh = tokens(f"{title} {body}")
    if len(fresh) < 4:
        return None
    rows = conn.execute(
        """
        SELECT id, title, body, primary_news_id, published_at
        FROM news_items
        ORDER BY id DESC
        LIMIT 300
        """
    ).fetchall()
    best_id = None
    best_key = None
    for row in rows:
        score = jaccard(fresh, tokens(f"{row['title']} {row['body'] or ''}"))
        if score < COPY_THRESHOLD:
            continue
        root_id = row["primary_news_id"] or row["id"]
        published = row["published_at"] or "9999"
        key = (published, root_id)
        if best_key is None or key < best_key:
            best_key = key
            best_id = root_id
    return best_id


def _upsert_instrument(conn: sqlite3.Connection, **fields) -> int:
    now = iso_utc()
    row = conn.execute(
        """
        SELECT id FROM instruments
        WHERE symbol = ? AND market = ? AND asset_class = ?
        """,
        (fields["symbol"], fields["market"], fields["asset_class"]),
    ).fetchone()
    if row is None and fields["market"] == "unspecified":
        row = conn.execute(
            """
            SELECT id FROM instruments
            WHERE symbol = ? AND asset_class = ?
            ORDER BY id
            LIMIT 1
            """,
            (fields["symbol"], fields["asset_class"]),
        ).fetchone()
    if row is None and fields["market"] != "unspecified":
        row = conn.execute(
            """
            SELECT id FROM instruments
            WHERE symbol = ? AND asset_class = ? AND market = 'unspecified'
            """,
            (fields["symbol"], fields["asset_class"]),
        ).fetchone()
        if row is not None:
            conn.execute(
                "UPDATE instruments SET market = ?, venue = COALESCE(?, venue) WHERE id = ?",
                (fields["market"], fields.get("venue"), row["id"]),
            )
    if row:
        conn.execute(
            """
            UPDATE instruments
            SET last_seen_at = ?,
                name = COALESCE(?, name),
                venue = COALESCE(?, venue),
                provider_key = COALESCE(?, provider_key),
                option_type = COALESCE(option_type, ?)
            WHERE id = ?
            """,
            (
                now,
                fields.get("name"),
                fields.get("venue"),
                fields.get("provider_key"),
                fields.get("option_type"),
                row["id"],
            ),
        )
        return int(row["id"])
    cur = conn.execute(
        """
        INSERT INTO instruments (
          symbol, name, market, asset_class, industry, venue, underlying_symbol,
          option_type, strike, expiry_label, provider_key, listing_complete,
          source_note, first_seen_at, last_seen_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
        """,
        (
            fields["symbol"],
            fields.get("name"),
            fields["market"],
            fields["asset_class"],
            fields.get("industry"),
            fields.get("venue"),
            fields.get("underlying_symbol"),
            fields.get("option_type"),
            fields.get("strike"),
            fields.get("expiry_label"),
            fields.get("provider_key"),
            fields["source_note"],
            now,
            now,
        ),
    )
    return int(cur.lastrowid)


def _insert_quote(conn: sqlite3.Connection, **fields) -> bool:
    raw = fields.get("raw")
    raw_json = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, default=str)
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO quotes (
          instrument_id, observed_at, observed_at_raw, timezone_note, price, change_abs,
          change_pct, high_price, low_price, open_price, previous_close, volume_raw,
          value_raw, volume, trade_value, source_id, source_label, quality, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fields["instrument_id"],
            fields.get("observed_at"),
            fields["observed_at_raw"],
            fields["timezone_note"],
            fields["price"],
            fields.get("change_abs"),
            fields.get("change_pct"),
            fields.get("high_price"),
            fields.get("low_price"),
            fields.get("open_price"),
            fields.get("previous_close"),
            fields.get("volume_raw"),
            fields.get("value_raw"),
            fields.get("volume"),
            fields.get("trade_value"),
            fields.get("source_id"),
            fields["source_label"],
            fields["quality"],
            raw_json,
        ),
    )
    return cur.rowcount > 0
