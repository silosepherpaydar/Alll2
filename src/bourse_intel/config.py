from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
VAR_DIR = ROOT / "var"
DB_PATH = DATA_DIR / "intel.db"
DEMO_DB_PATH = DATA_DIR / "demo.db"

TEHRAN = "Asia/Tehran"
USER_AGENT = "BourseIntel/0.1 (local market monitor)"

# تابلوی عمومی ره‌آورد. TSETMC از این محیط تایم‌اوت می‌شود و اینجا جایگزین ثابت نیست.
RAHAVARD_BOARDS = [
    ("stocks", "equity", "https://rahavard365.com/api/v2/market-data/stocks"),
    ("etf-funds", "etf", "https://rahavard365.com/api/v2/market-data/etf-funds"),
    ("options", "option", "https://rahavard365.com/api/v2/market-data/options"),
    ("futures", "futures", "https://rahavard365.com/api/v2/market-data/futures"),
    ("funds", "mutual_fund", "https://rahavard365.com/api/v2/market-data/funds"),
    ("bonds", "bond", "https://rahavard365.com/api/v2/market-data/bonds"),
    ("commodities", "commodity_spot", "https://rahavard365.com/api/v2/market-data/commodities"),
    ("indexes", "index", "https://rahavard365.com/api/v2/market-data/indexes"),
]

BLOCKED_PROBES = [
    ("tsetmc_market", "https://cdn.tsetmc.com/api/ClosingPrice/GetMarketWatch?market=0"),
    ("codal_home", "https://codal.ir/"),
    ("fipiran_home", "https://www.fipiran.com/"),
]

RSS_FEEDS = [
    {
        "key": "donya_rss",
        "title": "دنیای اقتصاد",
        "kind": "media",
        "url": "https://donya-e-eqtesad.com/rss",
        "verification": "reported",
        "credibility_prior": 74,
    },
    {
        "key": "farda_rss",
        "title": "فردای اقتصاد",
        "kind": "media",
        "url": "https://www.fardayeeghtesad.com/rss",
        "verification": "reported",
        "credibility_prior": 72,
    },
]

HTML_PAGES = [
    {
        "key": "bourse24_home",
        "title": "بورس۲۴",
        "kind": "media",
        "url": "https://www.bourse24.ir/",
        "link_prefix": "https://www.bourse24.ir/articles/",
        "verification": "reported",
        "credibility_prior": 62,
    }
]


def load_channels() -> list[dict]:
    path = ROOT / "config" / "channels.json"
    return json.loads(path.read_text(encoding="utf-8"))
