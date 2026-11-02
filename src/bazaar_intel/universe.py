"""فهرست ابزارها از پاسخ واقعی نقشه سایت. ردیفی ساخته نمی‌شود."""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import unquote, urlparse

from bazaar_intel.textnorm import parse_decimal

SITEMAP_CLASS = {
    "stock": "equity",
    "option": "option",
    "fund": "fund",
    "bond": "bond",
    "future": "futures",
    "warrant": "warrant",
    "mortgageloan": "facility",
    "index": "index",
    "closedassets": "delisted",
}

TAG_RE = re.compile(r"<[^>]+>")
SNAPSHOT_RE = re.compile(
    r"بازگشایی\s+([\d,]+)\s+پایانی\s+([\d,]+)\s+(-?[\d,.]+)\s+بیشترین\s+([\d,]+)\s+کمترین\s+([\d,]+)\s+حجم\s+([\d,.]+)\s+(\S+)"
)
PROFIT_RE = re.compile(r"تحقق سود\s+([\d,]+)")
PE_RE = re.compile(r"P/E TTM\s+([\d,.]+)")


def parse_shakhesban_sitemap(xml: str) -> tuple[str | None, list[dict]]:
    lastmods = re.findall(r"<lastmod>([^<]+)</lastmod>", xml)
    fresh = max(lastmods) if lastmods else None
    items = []
    for loc in re.findall(r"<loc>([^<]+)</loc>", xml):
        parts = [unquote(part) for part in urlparse(loc).path.strip("/").split("/") if part]
        if len(parts) < 3 or parts[0] != "markets":
            continue
        asset_class = SITEMAP_CLASS.get(parts[1])
        if asset_class is None:
            continue
        symbol = parts[2].strip()
        if not symbol:
            continue
        items.append(
            {
                "symbol": symbol,
                "name": symbol,
                "asset_class": asset_class,
                "board": parts[1],
                "option_type": _option_type(symbol, asset_class),
                "url": loc,
            }
        )
    return fresh, items


def _option_type(symbol: str, asset_class: str) -> str | None:
    if asset_class != "option":
        return None
    if symbol.startswith("ط"):
        return "call"
    if symbol.startswith("ض"):
        return "put"
    return None


def parse_symbol_snapshot(html: str) -> dict:
    """جدول صفحه نماد. اگر چیدمان عوض شده باشد قیمت برنمی‌گردد."""
    plain = re.sub(r"\s+", " ", unescape(TAG_RE.sub(" ", html))).strip()
    found = SNAPSHOT_RE.search(plain)
    item = {"parsed": False, "profit_raw": None, "pe_ttm_raw": None}
    profit = PROFIT_RE.search(plain)
    pe = PE_RE.search(plain)
    if profit:
        item["profit_raw"] = profit.group(1)
    if pe:
        item["pe_ttm_raw"] = pe.group(1)
    if not found:
        return item
    close = parse_decimal(found.group(2))
    if close is None:
        return item
    item.update(
        {
            "parsed": True,
            "open_price": parse_decimal(found.group(1)),
            "close": close,
            "change_abs": parse_decimal(found.group(3)),
            "high": parse_decimal(found.group(4)),
            "low": parse_decimal(found.group(5)),
            "volume_raw": f"{found.group(6)} {found.group(7)}",
        }
    )
    return item
