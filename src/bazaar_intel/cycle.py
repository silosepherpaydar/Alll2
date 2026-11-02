from __future__ import annotations

import fcntl
import sqlite3
from pathlib import Path

from bazaar_intel.analysis import refresh_impacts
from bazaar_intel.config import REPORTS_DIR
from bazaar_intel.db import init_db, set_capability
from bazaar_intel.hypotheses import refresh_hypotheses
from bazaar_intel.ingest import ingest_all
from bazaar_intel.ledger import evaluate_due
from bazaar_intel.probe import probe_sources
from bazaar_intel.ranking import rank_sources
from bazaar_intel.reports import build_report
from bazaar_intel.service import service_status
from bazaar_intel.timeutil import jalali_label


class CycleBusy(RuntimeError):
    pass


def run_cycle(
    db_path: Path | None = None,
    *,
    session: str = "pilot",
    do_probe: bool = True,
) -> tuple[sqlite3.Connection, Path]:
    conn = init_db(db_path)
    lock_path = conn.execute("PRAGMA database_list").fetchone()
    db_file = Path(lock_path[2]) if lock_path is not None else Path("bazaar.sqlite")
    lock_file = db_file.with_suffix(".lock")
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_handle = lock_file.open("a")
    try:
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock_handle.close()
        raise CycleBusy("چرخه دیگری در حال اجراست") from exc
    probe = {"ok": 0, "failed": 0}
    if do_probe:
        probe = probe_sources(conn)
    ingested = ingest_all(conn)
    impacts = refresh_impacts(conn)
    created = refresh_hypotheses(conn)
    evaluated = evaluate_due(conn)
    rank_sources(conn)
    _record_capabilities(conn, probe, ingested, created, evaluated, impacts)
    conn.commit()
    body = build_report(conn, session)
    conn.commit()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"{jalali_label().replace('/', '-')}-{session}.md"
    path.write_text(body, encoding="utf-8")
    (REPORTS_DIR / "latest.md").write_text(body, encoding="utf-8")
    lock_handle.close()
    return conn, path


def _record_capabilities(conn, probe: dict[str, int], ingested, created: list[str], evaluated: list[str], impacts: int) -> None:
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
    catalog = conn.execute(
        """
        SELECT asset_class, COUNT(*) AS n FROM instruments GROUP BY asset_class
        """
    ).fetchall()
    catalog_text = "، ".join(f"{row['asset_class']} {row['n']}" for row in catalog) or "خالی"
    direct_fail = conn.execute(
        """
        SELECT source_key, url, error FROM fetch_log
        WHERE channel = 'direct' AND ok = 0
          AND id IN (SELECT MAX(id) FROM fetch_log WHERE channel = 'direct' GROUP BY url)
        """
    ).fetchall()
    relay_ok = conn.execute(
        "SELECT source_key, records, notes, finished_at FROM fetch_log WHERE channel = 'relay' AND ok = 1 ORDER BY id DESC LIMIT 6"
    ).fetchall()
    set_capability(
        conn,
        "official_market_data",
        "partial" if relay_ok else "blocked",
        (
            f"میزبان‌های مستقیم که در آخرین تلاش باز نشدند: {official_down}. "
            f"خطاهای ثبت‌شده روی نشانی مستقیم: {len(direct_fail)}. "
            "مسیر جایگزین فقط وقتی ذخیره شده که پاسخ واقعی داشته باشد و در گزارش با برچسب relay آمده است."
        ),
    )
    set_capability(
        conn,
        "full_symbol_universe",
        "partial",
        "فهرست از نقشه سایت شاخص‌بان تازه می‌شود و تا تطبیق با TSETMC کامل علامت نخورده است. شمارش فعلی: " + catalog_text,
    )
    set_capability(
        conn,
        "tgju_and_shakhesban",
        "partial",
        " ".join(ingested.notes[:8]) or "خوراک تجمیعی در این چرخه نتیجه‌ای برنگرداند.",
    )
    set_capability(conn, "news_rss", "partial", f"خبرهای مرتبط ذخیره‌شده در این چرخه: {ingested.news}. موارد نامرتبط کنار گذاشته شدند: {ingested.skipped_irrelevant}.")
    posts = conn.execute("SELECT COUNT(*) AS n FROM telegram_posts").fetchone()["n"]
    discovered = conn.execute("SELECT COUNT(*) AS n FROM discovered_channels").fetchone()["n"]
    set_capability(
        conn,
        "telegram_public_preview",
        "partial",
        f"تاریخچه ذخیره‌شده از پیش‌نمایش عمومی: {posts} پیام. کانال تازه‌دیده: {discovered}. "
        "هر صفحه t.me/s حدود ۲۰ پیام است و با before فقط همان پیش‌نمایش عمومی عقب می‌رود. گروه خصوصی و تاریخ کامل بدون نشست مسدود است.",
    )
    set_capability(conn, "telegram_api", "blocked", "توکن یا نشست تلگرام در محیط نیست. کشف کانال فقط از پیوند داخل پیش‌نمایش عمومی است.")
    set_capability(conn, "option_greeks", "blocked", "نوسان ضمنی، موقعیت باز، ارزش ذاتی و فاصله خرید و فروش در داده موجود نیست.")
    set_capability(conn, "scheduler", "partial", service_status())
    set_capability(
        conn,
        "news_price_link",
        "partial" if impacts else "blocked",
        f"مقایسه خبر با قیمت ذخیره‌شده: {impacts} ردیف. اگر قیمت قبل و بعد یا متن نامه نباشد، اثر سود خالی می‌ماند.",
    )
    set_capability(conn, "trade_execution", "blocked", "اجرای معامله عمداً وجود ندارد.")
    set_capability(conn, "hypotheses", "active" if created else "partial", f"فرضیه‌های تازه‌ای که به فرصت کم‌امتیاز تبدیل شدند: {len(created)}.")
    if ingested.errors:
        set_capability(conn, "ingest_errors", "partial", " | ".join(ingested.errors[:12]))
    set_capability(conn, "source_probe", "partial", f"آخرین بررسی دسترسی: {probe.get('ok', 0)} پاسخ قابل قبول، {probe.get('failed', 0)} ناموفق.")
