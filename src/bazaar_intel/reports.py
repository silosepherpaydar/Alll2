"""گزارش فارسی از داده‌های ذخیره‌شده. جای خالی را با عدد ساختگی پر نمی‌کند."""

from __future__ import annotations

import hashlib
import sqlite3

from datetime import datetime

from bazaar_intel.clusters import copy_clusters, earliest
from bazaar_intel.metrics import brier_score, calibration_table, outcome_value
from bazaar_intel.textnorm import effective_verification, looks_market_relevant
from bazaar_intel.timeutil import TEHRAN, is_trading_weekday, jalali_label, tehran_label, tehran_now

CLASS_FA = {
    "official_confirmed": "تأییدشده رسمی",
    "relayed_official": "عنوان رسمی از مسیر جایگزین؛ متن نامه خوانده نشده",
    "reputable_media": "گزارش رسانه معتبر",
    "multi_independent": "دارای چند منبع مستقل",
    "single_specialist": "دارای یک منبع تخصصی",
    "rumor": "زمزمه یا شایعه",
    "system_inference": "تحلیل و استنباط سامانه",
    "contradicted": "اطلاعات متناقض یا ردشده",
}

STATUS_FA = {
    "hit": "محقق‌شده",
    "miss": "ناموفق",
    "partial": "تحقق ناقص",
    "invalidated": "باطل‌شده",
    "insufficient_data": "داده برای داوری کافی نیست",
}


def build_report(conn: sqlite3.Connection, session_name: str) -> str:
    trading = is_trading_weekday()
    market_note = (
        "امروز از نظر تقویم هفتگی روز معاملاتی است. تعطیلات رسمی در سامانه ثبت نشده و جداگانه تأیید نشده است."
        if trading
        else "امروز تعطیل هفتگی بازار سهام است. بخش معاملات با برچسب تعطیلی آمده و رصد خبر ادامه دارد."
    )
    lines = [
        f"# گزارش { _session_title(session_name) }",
        "",
        f"زمان تولید: {tehran_label()}",
        f"تاریخ شمسی: {jalali_label()}",
        "",
        "این گزارش فقط از ردیف‌های پایگاه همین اجرا ساخته شده است. اگر منبعی باز نشده باشد، نبودن خبر آن به‌معنای نبودن رویداد نیست.",
        "",
        f"وضعیت تقویم: {market_note}",
        "",
        "## واقعیت، استنباط، شایعه و پیش‌بینی",
        "",
        "واقعیت در این گزارش یعنی عددی که از یک منبع نام‌برده خوانده شده است. استنباط، جمع‌بندی خود سامانه است و پیش‌بینی فقط ردیفی است که پیش از نتیجه در دفتر ثبت شده باشد. شایعه واقعیت نیست.",
        "",
    ]
    if session_name == "pilot":
        lines.extend(
            [
                "این فایل آزمایشی است و جایگزین گزارش زمان‌بندی‌شده ساعت ۸:۵۰ یا ۱۹:۰۰ نیست. هر دو قالب با داده همین لحظه پر شده‌اند.",
                "",
            ]
        )
    if session_name == "preopen":
        lines.extend(_preopen(conn, trading))
    elif session_name == "close":
        lines.extend(_close(conn, trading))
    else:
        lines.extend(_preopen(conn, trading))
        lines.append("")
        lines.extend(_close(conn, trading))
    lines.extend(["", "## پوشش بازار و آخرین دریافت", ""])
    lines.extend(_coverage(conn))
    lines.extend(["", "## قابلیت‌های فعال و مسدود", ""])
    lines.extend(_capabilities(conn))
    lines.extend(["", "## دسترسی منابع", ""])
    lines.extend(_access(conn))
    body = "\n".join(lines).rstrip() + "\n"
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    conn.execute(
        """
        INSERT INTO daily_reports(session_name, created_at, tehran_label, market_session_note, body, content_hash)
        VALUES (?, datetime('now'), ?, ?, ?, ?)
        """,
        (session_name, tehran_label(), market_note, body, digest),
    )
    return body


def _session_title(session_name: str) -> str:
    return {
        "preopen": "پیش از بازگشایی",
        "close": "پایان روز",
        "pilot": "آزمایشی؛ هر دو قالب در یک نوبت",
        "phase2": "مرحله دوم؛ اتصال منابع و پوشش بازار",
    }.get(session_name, session_name)


def _preopen(conn: sqlite3.Connection, trading: bool) -> list[str]:
    lines = ["## گزارش پیش از بازگشایی", ""]
    lines += ["### بخش ۱. مهم‌ترین اخبار", ""]
    lines += _news_block(conn, limit=8)
    lines += ["", "### بخش ۲. فرصت‌های پیش‌گشایش", ""]
    if not trading:
        lines.append("بازار سهام در تعطیلی هفتگی است. واکنشی برای پیش‌گشایش امروز ثبت نمی‌شود.")
    else:
        lines.append("نمادهایی که فقط به‌دلیل بودن در نمای عمومی یک سایت دیده شده‌اند، فرصت پیش‌گشایش حساب نمی‌شوند.")
        lines += _movers(conn)
    lines += ["", "### بخش ۳. اخبار پیش‌نگر و شایعات", ""]
    lines += _rumors_and_hypotheses(conn)
    lines += ["", "### بخش ۴. آپشن و گواهی سپرده", ""]
    lines += _derivatives(conn)
    lines += ["", "### بخش ۵. تقویم رویدادها", ""]
    lines += _event_calendar(conn)
    lines += ["", "### بخش ۶. فهرست مراقبت", ""]
    lines += _watchlist(conn)
    return lines


def _close(conn: sqlite3.Connection, trading: bool) -> list[str]:
    lines = ["## گزارش پایان روز", ""]
    lines += ["### بخش ۱. اتفاقات بازار", ""]
    if not trading:
        lines.append("تعطیلی بازار: معامله سهام برای این روز در تقویم هفتگی تعریف نشده است.")
    lines += _market_facts(conn)
    lines += ["", "### بخش ۲. اخبار جدید", ""]
    lines += _news_block(conn, limit=6)
    lines += ["", "### بخش ۳. فرصت‌های کشف‌شده", ""]
    lines += _opportunities(conn)
    lines += ["", "### بخش ۴. قیمت و حجم", ""]
    lines.append("حجم و ارزش فقط برای ردیف‌هایی آمده که خود جدول منبع آن را داشته است. ورود پول حقیقی و صف خرید از TSETMC خوانده نشد.")
    lines += _impacts(conn)
    lines += _movers(conn)
    lines += ["", "### بخش ۵. آپشن و ابزار کالایی", ""]
    lines += _derivatives(conn)
    lines += ["", "### بخش ۶. دفتر پیش‌بینی‌ها", ""]
    lines += _ledger_section(conn)
    lines += ["", "### بخش ۷. جمع‌بندی", ""]
    lines += _summary(conn)
    return lines


def _tehran_time(value: str | None, raw: str | None) -> str:
    if not value:
        return raw or "زمان انتشار در منبع نیامده"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    if parsed.tzinfo is None:
        return f"{value} (منطقه زمانی در منبع نبود)"
    return parsed.astimezone(TEHRAN).strftime("%Y-%m-%d %H:%M") + " تهران"


def _news_rows(conn: sqlite3.Connection) -> list[dict]:
    rows = []
    for row in conn.execute(
        """
        SELECT n.id, n.title, n.body, n.url, n.published_at, n.published_at_raw,
               n.verification_class, n.confidence, s.name AS source_name
        FROM news_items n
        LEFT JOIN sources s ON s.id = n.source_id
        WHERE n.is_republication = 0
        ORDER BY COALESCE(n.published_at, n.fetched_at) DESC
        LIMIT 250
        """
    ).fetchall():
        item = dict(row)
        text = f"{item['title']} {item.get('body') or ''}"
        if not looks_market_relevant(text):
            continue
        item["verification_class"] = effective_verification(item["verification_class"], text)
        rows.append(item)
    return rows


def _news_block(conn: sqlite3.Connection, limit: int) -> list[str]:
    rows = [row for row in _news_rows(conn) if row["verification_class"] != "rumor"]
    if not rows:
        return ["خبر مرتبطی ذخیره نشده است."]
    clusters = copy_clusters(rows)
    clusters.sort(
        key=lambda group: (
            _headline_rank(earliest(group)),
            earliest(group).get("published_at") or "",
        ),
        reverse=True,
    )
    lines = []
    for cluster in clusters[:limit]:
        first = earliest(cluster)
        others = [row for row in cluster if row["id"] != first["id"]]
        klass = CLASS_FA.get(first["verification_class"], first["verification_class"])
        lines.append(f"- [{klass}] {first['title']}")
        lines.append(f"  نخستین منبع قابل شناسایی در داده‌های ذخیره‌شده: {first.get('source_name') or 'نامشخص'}")
        lines.append(f"  زمان: {_tehran_time(first.get('published_at'), first.get('published_at_raw'))}")
        if first.get("url"):
            lines.append(f"  پیوند: {first['url']}")
        if others:
            names = "، ".join(sorted({row.get("source_name") or "" for row in others}))
            lines.append(f"  بازنشرهای شبیه، تأیید مستقل حساب نشدند: {names}")
        lines.append("  تعداد تأیید مستقل: ۱")
        lines.append(f"  اطمینان: {first.get('confidence')}")
    return lines


def _headline_rank(row: dict) -> int:
    text = f"{row.get('title') or ''} {row.get('body') or ''}"
    anchors = ("کدال", "اطلاعیه", "شفاف", "مجمع", "افزایش سرمایه", "بورس کالا", "نماد")
    return sum(1 for anchor in anchors if anchor in text)


def _rumors_and_hypotheses(conn: sqlite3.Connection) -> list[str]:
    lines = []
    rumors = [row for row in _news_rows(conn) if row["verification_class"] == "rumor"][:8]
    if rumors:
        lines.append("شایعات و منابع سطح C، فقط به‌عنوان سرنخ:")
        for row in rumors:
            lines.append(f"- {row['title']} | منبع: {row['source_name']} | این مورد واقعیت تأییدشده نیست.")
            if row["url"]:
                lines.append(f"  پیوند: {row['url']}")
    else:
        lines.append("شایعه قابل نمایشی در این چرخه ذخیره نشده است.")
    lines.append("")
    lines.append("استنباط‌های سامانه:")
    hypotheses = conn.execute(
        "SELECT public_id, statement, confidence, invalidator FROM hypotheses ORDER BY id DESC LIMIT 5"
    ).fetchall()
    if not hypotheses:
        lines.append("فرضیه تازه‌ای ثبت نشد.")
    for row in hypotheses:
        lines.append(f"- {row['public_id']} [{row['confidence']}] {row['statement']}")
        lines.append(f"  عامل ابطال: {row['invalidator']}")
    brent_reports = [row for row in _news_rows(conn) if "برنت" in (row["title"] or "")][:3]
    if brent_reports:
        lines.append("گزارش‌های جدا درباره برنت که نباید با استنباط بالا یکی گرفته شوند:")
        for row in brent_reports:
            klass = CLASS_FA.get(row["verification_class"], row["verification_class"])
            lines.append(f"- [{klass}] {row['title']} | {row.get('source_name')}")
            if row.get("url"):
                lines.append(f"  پیوند: {row['url']}")
    return lines


def _movers(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT i.symbol, i.name, i.market, q.price, q.change_pct, q.volume_raw, q.value_raw,
               q.observed_at_raw, q.source_label
        FROM quotes q
        JOIN instruments i ON i.id = q.instrument_id
        WHERE q.quality = 'as_provided' AND i.asset_class = 'equity' AND q.change_pct IS NOT NULL
        ORDER BY ABS(CAST(q.change_pct AS REAL)) DESC, q.id DESC
        LIMIT 8
        """
    ).fetchall()
    if not rows:
        return ["تغییر قیمت سهمی که بشود آن را از جدول خواند ذخیره نشده است."]
    lines = ["بزرگ‌ترین تغییرهای دیده‌شده در نمای ناقص تابلو. این فهرست فرصت نیست:"]
    for row in rows:
        lines.append(
            f"- {row['symbol']} ({row['market']}) قیمت {row['price']} تغییر {row['change_pct']} درصد، "
            f"حجم خام «{row['volume_raw'] or 'نیامده'}»، ارزش خام «{row['value_raw'] or 'نیامده'}»، "
            f"تاریخ خام {row['observed_at_raw']}، منبع {row['source_label']}"
        )
    return lines


def _market_facts(conn: sqlite3.Connection) -> list[str]:
    lines = []
    index_rows = conn.execute(
        """
        SELECT q.price, q.change_pct, q.observed_at_raw, q.source_label, q.quality, q.timezone_note
        FROM quotes q
        JOIN instruments i ON i.id = q.instrument_id
        WHERE i.provider_key = 'bourse'
        ORDER BY q.id DESC LIMIT 4
        """
    ).fetchall()
    if not index_rows:
        lines.append("عددی برای شاخص کل ذخیره نشده است.")
    else:
        lines.append("چاپ‌های ذخیره‌شده شاخص. این‌ها جایگزین آمار رسمی TSETMC نیستند:")
        parsed_prices = []
        for row in index_rows:
            lines.append(
                f"- {row['source_label']}: {row['price']}، تغییر {row['change_pct'] or 'نیامده'}، "
                f"زمان خام {row['observed_at_raw']}، کیفیت {row['quality']}. {row['timezone_note']}"
            )
            try:
                parsed_prices.append(float(row["price"]))
            except (TypeError, ValueError):
                continue
        if len(parsed_prices) >= 2 and max(parsed_prices):
            gap = abs(parsed_prices[0] - parsed_prices[1]) / max(parsed_prices) * 100
            lines.append(f"فاصله دو چاپ اخیر حدود {gap:.3f} درصد است. هیچ‌کدام آمار رسمی بورس نیست.")
    counts = conn.execute(
        "SELECT market, asset_class, COUNT(*) AS n FROM instruments GROUP BY market, asset_class ORDER BY n DESC"
    ).fetchall()
    if counts:
        lines.append("")
        lines.append("ابزارهای دیده‌شده، نه فهرست کامل بازار:")
        for row in counts:
            lines.append(f"- {row['market']} / {row['asset_class']}: {row['n']}")
    lines.append("")
    lines.append("شاخص هم‌وزن، ورود و خروج پول حقیقی، و صف خرید از منبع رسمی در این چرخه موجود نیست.")
    return lines


def _derivatives(conn: sqlite3.Connection) -> list[str]:
    rows = []
    jalali_prefix = jalali_label()[:4] + "/%"
    month_prefix = tehran_now().strftime("%Y-%m") + "%"
    for asset_class in ("option", "futures", "certificate", "fund"):
        rows.extend(
            conn.execute(
                """
                SELECT i.symbol, i.asset_class, q.price, q.change_pct, q.observed_at_raw, q.source_label
                FROM quotes q
                JOIN instruments i ON i.id = q.instrument_id
                WHERE i.asset_class = ?
                  AND q.quality = 'as_provided'
                  AND NOT (q.price = '1' AND CAST(q.change_pct AS REAL) <= -90)
                ORDER BY CASE
                    WHEN q.observed_at_raw LIKE ? OR q.observed_at_raw LIKE ? THEN 0
                    ELSE 1
                END, q.observed_at_raw DESC, q.id DESC
                LIMIT 3
                """,
                (asset_class, jalali_prefix, month_prefix),
            ).fetchall()
        )
    lines = [
        "قیمت قرارداد به‌تنهایی فرصت نیست. ارزش ذاتی، ارزش زمانی، موقعیت باز، فاصله خرید و فروش، نوسان ضمنی و نقدشوندگی در داده این چرخه نبود.",
        "احتمال از دست رفتن کل مبلغ پرداختی برای اختیار قابل محاسبه نیست چون این ورودی‌ها موجود نیستند.",
        "اگر در زمان خام فقط ساعت آمده باشد، تاریخ معامله مشخص نیست.",
    ]
    if not rows:
        lines.append("قرارداد یا گواهی تازه‌ای ذخیره نشده است.")
        return lines
    for row in rows:
        note = ""
        raw_date = row["observed_at_raw"] or ""
        today = jalali_label()
        if raw_date.startswith("140") and not raw_date.startswith(today):
            note = " این تاریخ، تاریخ امروز نیست."
        if row["price"] == "1" and row["change_pct"] is not None and float(row["change_pct"]) <= -90:
            note += " این چاپ با قیمت‌های متعارف همان گروه هم‌خوان نیست و فرصت حساب نمی‌شود."
        lines.append(
            f"- {row['asset_class']} {row['symbol']}: قیمت {row['price']}، "
            f"تغییر {row['change_pct'] or 'نیامده'}، زمان خام {row['observed_at_raw']}، منبع {row['source_label']}.{note}"
        )
    return lines


def _watchlist(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT * FROM opportunities
        WHERE score_total >= 40
        ORDER BY score_total DESC
        LIMIT 10
        """
    ).fetchall()
    if not rows:
        return ["فرصت معناداری شناسایی نشده است. موردی به آستانه ۴۰ نرسید و فهرست مراقبت عمداً خالی مانده است."]
    return [_opportunity_card(row) for row in rows]


def _opportunities(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT * FROM opportunities ORDER BY id DESC LIMIT 10").fetchall()
    if not rows:
        return ["فرصت یا سرنخ امتیازدهی‌شده‌ای ثبت نشده است."]
    lines = []
    for row in rows:
        lines.append(_opportunity_card(row))
        lines.append("")
    return lines


def _opportunity_card(row: sqlite3.Row) -> str:
    return "\n".join(
        [
            f"نام شرکت / نماد: {row['title']}",
            f"نوع دارایی: {row['asset_class'] or 'نامشخص'}",
            f"شرح فرصت: {row['summary']}",
            f"نخستین منبع: {row['first_source_key'] or 'نامشخص'}",
            f"زمان اولین انتشار: {row['first_published_at'] or 'نامشخص'}",
            f"سطح اعتبار اطلاعات: {CLASS_FA.get(row['verification_class'], row['verification_class'])}",
            f"شواهد موافق: {row['supporting_evidence']}",
            f"شواهد مخالف: {row['opposing_evidence']}",
            f"اتفاق احتمالی آینده: {row['expected_event']}",
            f"اثر بر درآمد و سودآوری: {row['fundamental_effect']}",
            f"وضعیت فعلی قیمت: {row['price_status']}",
            f"آیا خبر در قیمت منعکس شده است؟ {row['reflected_in_price']}",
            f"افق زمانی: {row['horizon']}",
            f"سناریوی خوش‌بینانه: {row['scenario_up']}",
            f"سناریوی پایه: {row['scenario_base']}",
            f"سناریوی بدبینانه: {row['scenario_down']}",
            f"عامل ابطال فرضیه: {row['invalidator']}",
            f"امتیاز فرصت از ۱۰۰: {row['score_total']} ({row['score_band']}) با اطمینان {row['confidence']}",
            f"احتمال موفقیت برآوردی: {'ثبت نشده' if row['estimated_success_probability'] is None else row['estimated_success_probability']}",
            f"شناسه پیش‌بینی: {row['prediction_public_id'] or 'پیش‌بینی قیمت ثبت نشد'}",
            f"لینک منابع: {row['source_links']}",
            f"داده مفقود: {row['missing_data']}",
            f"دلیل امتیاز: {row['score_rationale']}",
        ]
    )


def _ledger_section(conn: sqlite3.Connection) -> list[str]:
    predictions = conn.execute(
        """
        SELECT p.*, r.status AS result_status, r.asset_return_pct, r.deviation_note
        FROM predictions p
        LEFT JOIN prediction_current_result r ON r.prediction_id = p.id
        ORDER BY p.id
        """
    ).fetchall()
    if not predictions:
        return [
            "پیش‌بینی جدیدی ثبت نشد.",
            "پیش‌بینی در آستانه تحقق، محقق‌شده یا ناموفق در دفتر نیست.",
            "دلیل: برای هیچ دارایی، هم قیمت مبنای قابل اتکا و هم فرضیه عددی با افق مشخص و عامل ابطال وجود نداشت. سامانه به‌جای ساختن پیش‌بینی، دفتر را خالی گذاشت.",
        ]
    lines = []
    buckets = {"open": [], "hit": [], "miss": [], "partial": [], "insufficient_data": [], "invalidated": []}
    for row in predictions:
        key = row["result_status"] or "open"
        buckets.setdefault(key, []).append(row)
    lines.append(f"تعداد کل پیش‌بینی‌های ثبت‌شده: {len(predictions)}")
    lines.append("پیش‌بینی‌های هنوز باز: " + _ids(buckets["open"]))
    lines.append("محقق‌شده: " + _ids(buckets["hit"]))
    lines.append("ناموفق: " + _ids(buckets["miss"]))
    lines.append("تحقق ناقص: " + _ids(buckets["partial"]))
    lines.append("باطل‌شده: " + _ids(buckets["invalidated"]))
    lines.append("نیازمند داده: " + _ids(buckets["insufficient_data"]))
    for row in predictions:
        status = STATUS_FA.get(row["result_status"], "باز")
        lines.append(
            f"- {row['public_id']} {row['symbol']} قیمت مبنا {row['base_price']} از {row['base_price_source']} "
            f"در {row['registered_at_tehran']}؛ افق تا {row['horizon_end']}؛ وضعیت: {status}"
        )
    lines.extend(_scorecard(conn))
    return lines


def _ids(rows: list[sqlite3.Row]) -> str:
    if not rows:
        return "هیچ"
    return "، ".join(row["public_id"] for row in rows)


def _scorecard(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT p.success_probability, r.status, r.asset_return_pct, r.error_pct, p.horizon_trading_days
        FROM predictions p
        JOIN prediction_current_result r ON r.prediction_id = p.id
        WHERE r.status IN ('hit', 'miss', 'partial', 'invalidated')
        """
    ).fetchall()
    lines = ["", "سنجش کیفیت پیش‌بینی‌های تعیین‌تکلیف‌شده:"]
    if not rows:
        lines.append("هنوز پیش‌بینی بسته‌شده‌ای نیست. نرخ موفقیت، بریر اسکور و مقایسه با شاخص قابل محاسبه نیست.")
        return lines
    hits = sum(1 for row in rows if row["status"] == "hit")
    lines.append(f"تعداد بسته‌شده: {len(rows)}")
    lines.append(f"موفق: {hits}")
    lines.append(f"ناموفق یا باطل: {sum(1 for row in rows if row['status'] in {'miss', 'invalidated'})}")
    lines.append(f"نرخ موفقیت، اگر تحقق ناقص را موفق حساب نکنیم: {hits / len(rows):.2f}")
    returns = [float(row["asset_return_pct"]) for row in rows if row["asset_return_pct"] is not None]
    errors = [abs(float(row["error_pct"])) for row in rows if row["error_pct"] is not None]
    if returns:
        lines.append(f"میانگین بازده دارایی در پیش‌بینی‌های قیمتی بسته‌شده: {sum(returns) / len(returns):.4f} درصد")
    if errors:
        lines.append(f"میانگین قدر مطلق خطای قیمت نسبت به میانه هدف: {sum(errors) / len(errors):.4f} درصد")
    pairs = []
    for row in rows:
        outcome = outcome_value(row["status"])
        if outcome is not None:
            pairs.append((float(row["success_probability"]), outcome))
    brier = brier_score(pairs)
    lines.append(f"Brier Score: {brier if brier is not None else 'قابل محاسبه نیست'}")
    for bucket in calibration_table(pairs):
        if bucket["count"]:
            lines.append(
                f"کالیبراسیون بازه {bucket['bin_low']:.1f} تا {bucket['bin_high']:.1f}: "
                f"{bucket['count']} مورد، میانگین احتمال {bucket['mean_probability']:.2f}، "
                f"میانگین نتیجه {bucket['mean_outcome']:.2f}"
            )
    lines.append("مقایسه با شاخص فقط در نتیجه هر پیش‌بینی نوشته می‌شود و اگر شاخص همان بازه موجود نباشد خالی می‌ماند.")
    return lines


def _summary(conn: sqlite3.Connection) -> list[str]:
    serious = conn.execute("SELECT COUNT(*) AS n FROM opportunities WHERE score_total >= 55").fetchone()["n"]
    if serious == 0:
        head = "فرصت معناداری که به آستانه بررسی جدی رسیده باشد شناسایی نشده است."
    else:
        head = f"{serious} مورد به آستانه ۵۵ یا بالاتر رسیده است و جزئیاتش در بخش فرصت‌هاست."
    return [
        head,
        "ریسک اصلی این چرخه، کمبود داده رسمی است نه یک رویداد شرکتی مشخص.",
        "هیچ معامله‌ای پیشنهاد اجرایی نشده و تصمیمی برای خرید یا فروش گرفته نشده است.",
    ]


def _event_calendar(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT n.title, n.published_at_raw, n.url, n.verification_class, i.symbol
        FROM news_items n
        LEFT JOIN news_mentions m ON m.news_id = n.id
        LEFT JOIN instruments i ON i.id = m.instrument_id
        WHERE n.verification_class IN ('relayed_official', 'official_confirmed')
        ORDER BY COALESCE(n.published_at, n.fetched_at) DESC
        LIMIT 8
        """
    ).fetchall()
    if not rows:
        return [
            "مجمع، سررسید و عرضه اولیه از میزبان مستقیم کدال، بورس تهران، فرابورس و بورس کالا خوانده نشد و عنوان جایگزینی هم ذخیره نشده است."
        ]
    lines = [
        "عنوان‌های زیر از پاسخ واقعی مسیر کدال آمده‌اند. اگر سطح «مسیر جایگزین» باشد، متن نامه از خود codal.ir دانلود نشده و تاریخ قطعی رویداد حساب نمی‌شود."
    ]
    seen = set()
    for row in rows:
        key = (row["title"], row["url"])
        if key in seen:
            continue
        seen.add(key)
        symbol = row["symbol"] or "نماد در عنوان جدا نیامده"
        lines.append(f"- {symbol}: {row['title']} | {row['published_at_raw'] or 'زمان خام نیست'} | {row['url']}")
    return lines


def _coverage(conn: sqlite3.Connection) -> list[str]:
    total = conn.execute("SELECT COUNT(*) AS n FROM instruments").fetchone()["n"]
    lines = [f"ابزارهای ذخیره‌شده: {total}"]
    for row in conn.execute(
        "SELECT asset_class, COUNT(*) AS n FROM instruments GROUP BY asset_class ORDER BY n DESC"
    ):
        lines.append(f"- {row['asset_class']}: {row['n']}")
    calls = conn.execute("SELECT COUNT(*) AS n FROM instruments WHERE option_type = 'call'").fetchone()["n"]
    puts = conn.execute("SELECT COUNT(*) AS n FROM instruments WHERE option_type = 'put'").fetchone()["n"]
    lines.append(f"اختیار خرید با پیشوند ط: {calls}. اختیار فروش با پیشوند ض: {puts}. بقیه اختیارها در پاسخ منبع پیشوند استاندارد نداشتند.")
    lines.append("listing_complete برای این ردیف‌ها صفر است چون فهرست با فایل رسمی TSETMC تطبیق نشده است.")
    lines.append("")
    lines.append("پرحجم‌ترین دریافت موفق هر منبع:")
    keys = [
        row["source_key"]
        for row in conn.execute("SELECT DISTINCT source_key FROM fetch_log WHERE ok = 1 ORDER BY source_key")
    ]
    if not keys:
        lines.append("هنوز دریافت موفقی در fetch_log نیست.")
    for key in keys:
        row = conn.execute(
            """
            SELECT source_key, channel, finished_at, records, notes
            FROM fetch_log
            WHERE source_key = ? AND ok = 1
            ORDER BY records DESC, id DESC
            LIMIT 1
            """,
            (key,),
        ).fetchone()
        lines.append(
            f"- {row['source_key']} / {row['channel']} / {row['records']} رکورد / {_tehran_time(row['finished_at'], None)}"
        )
        lines.append(f"  {row['notes']}")
    lines.append("")
    lines.append("آخرین خطای هر منبع:")
    failures = conn.execute(
        """
        SELECT source_key, channel, finished_at, error, url
        FROM fetch_log
        WHERE ok = 0 AND id IN (SELECT MAX(id) FROM fetch_log WHERE ok = 0 GROUP BY source_key)
        ORDER BY source_key
        """
    ).fetchall()
    if not failures:
        lines.append("خطای تازه‌ای ثبت نشده است.")
    for row in failures:
        lines.append(
            f"- {row['source_key']} / {row['channel']} / {_tehran_time(row['finished_at'], None)} / {row['error'] or 'بدون متن خطا'}"
        )
        lines.append(f"  {row['url']}")
    return lines


def _impacts(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT i.symbol, n.title, p.price_before, p.price_after, p.price_change_pct,
               p.volume_before, p.volume_after, p.earnings_effect, p.price_reflection, p.missing_data
        FROM news_impacts p
        JOIN news_items n ON n.id = p.news_id
        JOIN instruments i ON i.id = p.instrument_id
        ORDER BY p.id DESC
        LIMIT 6
        """
    ).fetchall()
    if not rows:
        return ["مقایسه خبر با قیمت: هنوز پنجره قیمت قبل و بعد برای یک خبر نماددار ذخیره نشده است."]
    lines = ["مقایسه خبر ذخیره‌شده با قیمت ذخیره‌شده:"]
    for row in rows:
        lines.append(f"- {row['symbol']}: {row['title']}")
        lines.append(
            f"  قیمت قبل {row['price_before'] or 'نیست'}، بعد {row['price_after'] or 'نیست'}، "
            f"تغییر {row['price_change_pct'] or 'محاسبه نشد'} درصد"
        )
        lines.append(f"  حجم قبل {row['volume_before'] or 'نیست'}، بعد {row['volume_after'] or 'نیست'}")
        lines.append(f"  {row['price_reflection']}")
        lines.append(f"  {row['earnings_effect']}")
        lines.append(f"  کمبود داده: {row['missing_data']}")
    return lines


def _capabilities(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT capability, state, detail FROM capability_status ORDER BY capability").fetchall()
    if not rows:
        return ["وضعیت قابلیت‌ها هنوز ثبت نشده است."]
    labels = {"active": "فعال", "partial": "ناقص", "blocked": "مسدود"}
    return [f"- {row['capability']}: {labels.get(row['state'], row['state'])}. {row['detail']}" for row in rows]


def _access(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT s.name, s.url, a.ok, a.status_code, a.error
        FROM sources s
        LEFT JOIN source_access a ON a.id = (
          SELECT id FROM source_access WHERE source_id = s.id ORDER BY id DESC LIMIT 1
        )
        ORDER BY s.group_name, s.name
        """
    ).fetchall()
    open_rows = [row for row in rows if row["ok"] == 1]
    closed = [row for row in rows if row["ok"] != 1]
    lines = [f"منابعی که در آخرین بررسی پاسخ قابل قبول دادند: {len(open_rows)}", ""]
    for row in open_rows:
        lines.append(f"- {row['name']} ({row['status_code']}) {row['url']}")
    lines.append("")
    lines.append("منابعی که در این محیط باز نشدند یا پاسخ خطا دادند:")
    if not closed:
        lines.append("- هیچ")
    for row in closed:
        reason = row["error"] or row["status_code"] or "هنوز بررسی نشده"
        lines.append(f"- {row['name']}: {reason} | {row['url']}")
    return lines
