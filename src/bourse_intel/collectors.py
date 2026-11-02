from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import unquote, urljoin
from xml.etree import ElementTree

from bourse_intel.config import BLOCKED_PROBES, HTML_PAGES, RAHAVARD_BOARDS, RSS_FEEDS, load_channels
from bourse_intel.http_client import HttpResult, fetch, fetch_json
from bourse_intel.textutil import strip_html


@dataclass
class Probe:
    source_key: str
    status: str
    http_status: int | None
    seen: int
    message: str


@dataclass
class ItemDraft:
    source_key: str
    external_id: str
    url: str
    title: str
    body: str
    published_at: str
    verification: str


@dataclass
class InstrumentDraft:
    source_key: str
    external_id: str
    symbol: str
    name: str
    market: str
    instrument_type: str
    slug: str
    last_price: float | None
    change_percent: float | None
    volume: float | None
    value: float | None
    trade_time: str


@dataclass
class CollectBatch:
    probes: list[Probe] = field(default_factory=list)
    items: list[ItemDraft] = field(default_factory=list)
    instruments: list[InstrumentDraft] = field(default_factory=list)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _probe_from(result: HttpResult, source_key: str, seen: int = 0) -> Probe:
    if result.blocked:
        status = "blocked"
    elif result.ok:
        status = "ok" if seen else "empty"
    else:
        status = "error"
    return Probe(source_key, status, result.status, seen, result.error or "ok")


def _num(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _option_type(symbol: str) -> str:
    if symbol.startswith("ض"):
        return "call_option"
    if symbol.startswith("ط"):
        return "put_option"
    return "option"


def _etf_type(name: str) -> str:
    gold_words = ("طلا", "سکه", "نقره", "زعفران", "پسته", "مثقال", "کهربا", "شمش")
    fixed_words = ("ثابت", "درآمد", "گنجینه", "گنجین", "سپرده")
    if any(word in name for word in gold_words):
        return "commodity_etf"
    if any(word in name for word in fixed_words):
        return "fixed_income_etf"
    return "equity_etf"


def _market(exchange_id) -> str:
    return {1: "بورس تهران", "1": "بورس تهران", 2: "فرابورس", "2": "فرابورس"}.get(exchange_id, "")


def collect_boards() -> tuple[list[InstrumentDraft], list[Probe]]:
    drafts: list[InstrumentDraft] = []
    probes: list[Probe] = []
    for key, default_type, url in RAHAVARD_BOARDS:
        result, payload = fetch_json(url, timeout=40)
        rows = []
        if isinstance(payload, dict):
            rows = payload.get("data") or []
        if not isinstance(rows, list):
            rows = []
        probe = _probe_from(result, f"rahavard_{key}", len(rows))
        if result.ok and not rows:
            probe.status = "empty"
            probe.message = "پاسخ سالم بود و فهرست خالی برگشت"
        probes.append(probe)
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = (row.get("name") or row.get("short_title") or "").strip()
            external = str(row.get("asset_id") or row.get("index_id") or symbol)
            if not symbol or not external:
                continue
            instrument_type = default_type
            if default_type == "option":
                instrument_type = _option_type(symbol)
            elif default_type == "etf":
                instrument_type = _etf_type(symbol)
            price = _num(row.get("real_close_price", row.get("bid_price", row.get("close_value"))))
            change = _num(
                row.get(
                    "real_close_price_change_percent",
                    row.get("bid_price_change_percent", row.get("close_value_change_percent")),
                )
            )
            drafts.append(
                InstrumentDraft(
                    source_key="rahavard365",
                    external_id=external,
                    symbol=symbol,
                    name=row.get("title") or symbol,
                    market=_market(row.get("exchange_id")),
                    instrument_type=instrument_type,
                    slug=row.get("slug") or "",
                    last_price=price,
                    change_percent=change,
                    volume=_num(row.get("volume")),
                    value=_num(row.get("value")),
                    trade_time=row.get("trade_date_time") or row.get("value_data_time") or "",
                )
            )
    return drafts, probes


def enrich_identity(external_id: str) -> dict | None:
    result, payload = fetch_json(f"https://rahavard365.com/api/v2/asset/{external_id}", timeout=20)
    if not result.ok or not isinstance(payload, dict):
        return None
    asset = (payload.get("data") or {}).get("asset") or {}
    if not asset:
        return None
    category = asset.get("category") or {}
    parent = (category.get("parent") or {}) if isinstance(category, dict) else {}
    industry = ""
    if isinstance(parent, dict) and parent.get("name"):
        industry = parent.get("name") or ""
    elif isinstance(category, dict):
        industry = category.get("name") or ""
    exchange = asset.get("exchange") or {}
    return {
        "symbol": asset.get("trade_symbol") or "",
        "company_name": asset.get("name") or asset.get("short_name") or "",
        "industry": industry,
        "market": (exchange.get("title") if isinstance(exchange, dict) else "") or "",
        "type_title": asset.get("type_title") or "",
    }


def probe_blocked() -> list[Probe]:
    probes = []
    for key, url in BLOCKED_PROBES:
        result = fetch(url, timeout=8)
        probe = _probe_from(result, key, 0)
        if result.blocked:
            probe.message = "اتصال از این محیط تایم‌اوت شد؛ داده این منبع نوشته نشد"
        elif not result.ok:
            probe.message = result.error or "ناموفق"
        else:
            probe.status = "ok"
            probe.message = "پاسخ HTTP گرفت؛ جمع‌آوری تخصصی این منبع هنوز به نقطه ورود پایدار نرسیده"
        probes.append(probe)
    return probes


def _parse_rss_date(value: str | None) -> str:
    if not value:
        return _now()
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, IndexError):
        return _now()


def collect_rss() -> tuple[list[ItemDraft], list[Probe]]:
    items: list[ItemDraft] = []
    probes: list[Probe] = []
    for feed in RSS_FEEDS:
        result = fetch(feed["url"], timeout=25, accept="application/rss+xml, application/xml, text/xml")
        if not result.ok:
            probes.append(_probe_from(result, feed["key"]))
            continue
        try:
            root = ElementTree.fromstring(result.body)
        except ElementTree.ParseError as exc:
            probes.append(Probe(feed["key"], "error", result.status, 0, f"RSS نامعتبر: {exc}"))
            continue
        nodes = root.findall(".//item")
        for node in nodes:
            title = (node.findtext("title") or "").strip()
            link = (node.findtext("link") or "").strip()
            description = strip_html(node.findtext("description") or "")
            guid = (node.findtext("guid") or link or title).strip()
            if not title:
                continue
            items.append(
                ItemDraft(
                    source_key=feed["key"],
                    external_id=guid[:300],
                    url=link,
                    title=title,
                    body=description[:4000],
                    published_at=_parse_rss_date(node.findtext("pubDate")),
                    verification=feed["verification"],
                )
            )
        probes.append(Probe(feed["key"], "ok" if nodes else "empty", result.status, len(nodes), "ok"))
    return items, probes


def collect_html_pages() -> tuple[list[ItemDraft], list[Probe]]:
    items: list[ItemDraft] = []
    probes: list[Probe] = []
    for page in HTML_PAGES:
        result = fetch(page["url"], timeout=25, accept="text/html")
        if not result.ok:
            probes.append(_probe_from(result, page["key"]))
            continue
        html = result.body.decode("utf-8", "replace")
        prefix = page["link_prefix"]
        found = sorted(set(re.findall(r'href="(' + re.escape(prefix) + r'\d+/[^"]+)"', html)))
        for href in found:
            slug = unquote(href.rstrip("/").split("/")[-1])
            title = slug.replace("-", " ").replace("«", "").replace("»", "").strip()
            if not title:
                continue
            items.append(
                ItemDraft(
                    source_key=page["key"],
                    external_id=href,
                    url=urljoin(page["url"], href),
                    title=title,
                    body="",
                    published_at=_now(),
                    verification=page["verification"],
                )
            )
        probes.append(Probe(page["key"], "ok" if found else "empty", result.status, len(found), "ok"))
    return items, probes


def collect_telegram() -> tuple[list[ItemDraft], list[Probe]]:
    items: list[ItemDraft] = []
    probes: list[Probe] = []
    for channel in load_channels():
        url = f"https://t.me/s/{channel['username']}"
        result = fetch(url, timeout=25, accept="text/html")
        if not result.ok:
            probes.append(_probe_from(result, f"tg_{channel['key']}"))
            continue
        html = result.body.decode("utf-8", "replace")
        chunks = html.split('data-post="')[1:]
        seen = 0
        for chunk in chunks:
            external_id = chunk.split('"', 1)[0]
            if "/" not in external_id:
                continue
            time_match = re.search(r'datetime="([^"]+)"', chunk)
            text_match = re.search(
                r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
                chunk,
                flags=re.S,
            )
            title_body = strip_html(text_match.group(1)) if text_match else ""
            if not title_body:
                continue
            title = title_body.split("\n", 1)[0][:240]
            published = time_match.group(1) if time_match else _now()
            items.append(
                ItemDraft(
                    source_key=f"tg_{channel['key']}",
                    external_id=external_id,
                    url=f"https://t.me/{external_id}",
                    title=title,
                    body=title_body[:4000],
                    published_at=published,
                    verification=channel["verification"],
                )
            )
            seen += 1
        probes.append(Probe(f"tg_{channel['key']}", "ok" if seen else "empty", result.status, seen, "ok"))
    return items, probes
