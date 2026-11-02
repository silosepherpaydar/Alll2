from __future__ import annotations

import sqlite3
from pathlib import Path

from bazaar_intel.config import REPORTS_DIR
from bazaar_intel.db import init_db, set_capability
from bazaar_intel.hypotheses import refresh_hypotheses
from bazaar_intel.ingest import ingest_all
from bazaar_intel.ledger import evaluate_due
from bazaar_intel.probe import probe_sources
from bazaar_intel.ranking import rank_sources
from bazaar_intel.reports import build_report
from bazaar_intel.timeutil import jalali_label


def run_cycle(
    db_path: Path | None = None,
    *,
    session: str = "pilot",
    do_probe: bool = True,
) -> tuple[sqlite3.Connection, Path]:
    conn = init_db(db_path)
    probe = {"ok": 0, "failed": 0}
    if do_probe:
        probe = probe_sources(conn)
    ingested = ingest_all(conn)
    created = refresh_hypotheses(conn)
    evaluated = evaluate_due(conn)
    rank_sources(conn)
    _record_capabilities(conn, probe, ingested, created, evaluated)
    conn.commit()
    body = build_report(conn, session)
    conn.commit()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"{jalali_label().replace('/', '-')}-{session}.md"
    path.write_text(body, encoding="utf-8")
    (REPORTS_DIR / "latest.md").write_text(body, encoding="utf-8")
    return conn, path


def _record_capabilities(conn, probe: dict[str, int], ingested, created: list[str], evaluated: list[str]) -> None:
    blocked = conn.execute(
        """
        SELECT s.source_key FROM sources s
        JOIN source_access a ON a.source_id = s.id
        WHERE a.id = (SELECT MAX(id) FROM source_access WHERE source_id = s.id)
          AND a.ok = 0
          AND s.source_key IN ('tsetmc', 'codal', 'fipiran', 'tse', 'ifb', 'ime', 'irenex')
        """
    ).fetchall()
    official_down = "، ".join(row["source_key"] for row in blocked) or "هنوز بررسی نشده"
    set_capability(conn, "prediction_ledger", "active", "ثبت پیش‌بینی فقط افزودنی است و تریگر پایگاه جلوی تغییر و حذف را می‌گیرد.")
    set_capability(conn, "auto_evaluation", "active", f"ارزیابی پس از افق فعال است. در این چرخه {len(evaluated)} مورد تعیین تکلیف شد.")
    set_capability(conn, "source_ranking", "partial", "محاسبه رتبه فعال است، اما تا وقتی پیش‌بینی بسته نشده باشد سطح منابع همان پیشین می‌ماند.")
    set_capability(conn, "sqlite_store", "active", "پایگاه فعلی SQLite است. PostgreSQL وصل نشده است.")
    set_capability(conn, "official_market_data", "blocked", f"منابع رسمی بازار که در این محیط باز نشدند: {official_down}.")
    set_capability(conn, "full_symbol_universe", "blocked", "فهرست کامل نمادهای بورس و فرابورس از TSETMC یا فایپیران گرفته نشد. فقط ابزارهای دیده‌شده در نماهای عمومی ذخیره شده‌اند.")
    set_capability(
        conn,
        "tgju_and_shakhesban",
        "partial",
        " ".join(ingested.notes[:8]) or "خوراک تجمیعی در این چرخه نتیجه‌ای برنگرداند.",
    )
    set_capability(conn, "news_rss", "partial", f"خبرهای مرتبط ذخیره‌شده در این چرخه: {ingested.news}. موارد نامرتبط کنار گذاشته شدند: {ingested.skipped_irrelevant}.")
    set_capability(conn, "telegram_public_preview", "partial", "فقط صفحه عمومی t.me/s خوانده می‌شود. تاریخ کامل، گروه خصوصی و حساب کاربری تلگرام وصل نیست.")
    set_capability(conn, "telegram_api", "blocked", "توکن یا نشست تلگرام در محیط نیست.")
    set_capability(conn, "option_greeks", "blocked", "نوسان ضمنی، موقعیت باز، ارزش ذاتی و فاصله خرید و فروش در داده موجود نیست.")
    set_capability(conn, "scheduler", "partial", "زمان‌بندی ۸:۵۰ و ۱۹:۰۰ تهران در برنامه هست، اما این محیط میزبان دائمی نیست و خودش سرویس را زنده نگه نمی‌دارد.")
    set_capability(conn, "trade_execution", "blocked", "اجرای معامله عمداً وجود ندارد.")
    set_capability(conn, "hypotheses", "active" if created else "partial", f"فرضیه‌های تازه‌ای که به فرصت کم‌امتیاز تبدیل شدند: {len(created)}.")
    if ingested.errors:
        set_capability(conn, "ingest_errors", "partial", " | ".join(ingested.errors[:12]))
    set_capability(conn, "source_probe", "partial", f"آخرین بررسی دسترسی: {probe.get('ok', 0)} پاسخ قابل قبول، {probe.get('failed', 0)} ناموفق.")
