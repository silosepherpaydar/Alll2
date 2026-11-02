"""فرضیه فقط وقتی ساخته می‌شود که عدد مشاهده‌شده و داده مفقود هر دو Explicit باشند."""

from __future__ import annotations

import sqlite3

from bazaar_intel.db import next_public_id
from bazaar_intel.scoring import score_opportunity
from bazaar_intel.timeutil import iso_utc, jalali_label

CLASS_INFERENCE = "system_inference"


def refresh_hypotheses(conn: sqlite3.Connection) -> list[str]:
    created: list[str] = []
    brent = conn.execute(
        """
        SELECT q.price, q.change_pct, q.observed_at_raw, q.source_label
        FROM quotes q
        JOIN instruments i ON i.id = q.instrument_id
        WHERE i.provider_key = 'oil_brent' AND q.quality = 'as_provided' AND q.change_pct IS NOT NULL
        ORDER BY q.id DESC LIMIT 1
        """
    ).fetchone()
    if brent is None:
        return created
    try:
        change = abs(float(brent["change_pct"]))
    except ValueError:
        return created
    if change < 2:
        return created
    direction = "افزایش" if float(brent["change_pct"]) > 0 else "کاهش"
    statement = (
        f"حرکت {direction} {brent['change_pct']} درصدی نفت برنت در خوراک {brent['source_label']} "
        f"با قیمت {brent['price']} و زمان خام {brent['observed_at_raw']} ممکن است بعداً بر درآمد شرکت‌های "
        "وابسته به قیمت جهانی نفت اثر بگذارد، اما اندازه اثر هنوز قابل محاسبه نیست."
    )
    if conn.execute("SELECT id FROM hypotheses WHERE statement = ?", (statement,)).fetchone():
        return created
    hypothesis_id = next_public_id(conn, "hypothesis", "H")
    now = iso_utc()
    conn.execute(
        """
        INSERT INTO hypotheses (
          public_id, created_at, statement, rationale, supporting_evidence, opposing_evidence,
          beneficiaries, harmed, visibility_window, invalidator, confidence, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'low', 'open')
        """,
        (
            hypothesis_id,
            now,
            statement,
            "فقط تغییر قیمت جهانی ثبت‌شده است. فرمول نرخ فروش شرکت‌های ایرانی، حجم فروش و موجودی در این چرخه موجود نیست.",
            f"خوراک TGJU کلید oil_brent، قیمت {brent['price']}، تغییر {brent['change_pct']} درصد، زمان خام {brent['observed_at_raw']}.",
            "نرخ رسمی فروش نفت و فرآورده ایران لزوماً برابر برنت همان ساعت نیست. تابلوی کامل سهام هم برای سنجش انعکاس قیمت در دسترس نبود.",
            "شرکت‌های بالادستی و برخی پالایشی‌ها، فقط در صورت انتقال این قیمت به نرخ فروش واقعی.",
            "مصرف‌کنندگان خوراک نفتی، اگر افزایش قیمت جهانی به بهای تمام‌شده منتقل شود و نرخ فروششان ثابت بماند.",
            "نامعلوم. داده فروش شرکت‌ها برای تعیین زمان اثر موجود نیست.",
            "بازگشت قیمت برنت، یا اعلام رسمی که نرخ فروش شرکت‌های ایرانی به این حرکت وصل نیست.",
        ),
    )
    row_id = conn.execute("SELECT id FROM hypotheses WHERE public_id = ?", (hypothesis_id,)).fetchone()["id"]
    score = score_opportunity(
        evidence=8,
        forward=6,
        earnings=5,
        price_gap=2,
        risk_reward=2,
        timing=3,
        missing_critical=True,
        rationale=(
            "شواهد از یک تجمیع‌کننده است نه از اطلاعیه شرکت. حرکت جهانی همین حالا علنی است، "
            "پس بخش پیش‌نگر آن ضعیف است. اثر سود بدون صورت فروش قابل عددگذاری نیست. "
            "فاصله تا قیمت سهام سنجیده نشد چون نماگر رسمی نمادها باز نشد. "
            f"تاریخ شمسی ثبت فرضیه: {jalali_label()}."
        ),
    )
    public_opportunity = next_public_id(conn, "opportunity", "O")
    conn.execute(
        """
        INSERT INTO opportunities (
          public_id, hypothesis_id, created_at, title, asset_class, summary, first_source_key,
          first_published_at, verification_class, supporting_evidence, opposing_evidence,
          expected_event, fundamental_effect, price_status, reflected_in_price, horizon,
          scenario_up, scenario_base, scenario_down, invalidator, score_evidence, score_forward,
          score_earnings, score_price_gap, score_risk_reward, score_timing, score_total,
          score_band, score_rationale, confidence, estimated_success_probability, source_links,
          missing_data
        ) VALUES (?, ?, ?, ?, 'commodity', ?, 'tgju', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            public_opportunity,
            row_id,
            now,
            "سرنخ قیمت برنت",
            statement,
            brent["observed_at_raw"],
            CLASS_INFERENCE,
            f"قیمت مشاهده‌شده برنت {brent['price']}.",
            "ارتباط با سود شرکت ایرانی هنوز اندازه‌گیری نشده است.",
            f"{direction} قیمت جهانی نفت ممکن است به نرخ فروش یا بهای تمام‌شده منتقل شود.",
            "اثر درآمد و سود خالص عددگذاری نشد. داده فروش، ترکیب محصول و نرخ رسمی مفقود است.",
            "قیمت سهام شرکت‌های مرتبط در تابلوی رسمی این چرخه خوانده نشد.",
            "مشخص نیست. انعکاس در قیمت سهم قابل داوری نیست.",
            "افق نامعلوم؛ به همین دلیل پیش‌بینی قیمت ثبت نشد.",
            "انتقال سریع قیمت جهانی به نرخ فروش شرکت‌های منتفع، اگر بعدها در داده فروش دیده شود.",
            "انتقال ناقص یا با تأخیر، بدون تغییر قابل مشاهده در سود دوره جاری.",
            "نبود انتقال به نرخ‌های داخلی، یا حرکت معکوس برنت.",
            "بازگشت برنت یا تکذیب ارتباط نرخ فروش.",
            score.evidence,
            score.forward,
            score.earnings,
            score.price_gap,
            score.risk_reward,
            score.timing,
            score.total,
            score.band,
            score.rationale,
            score.confidence,
            None,
            "https://www.tgju.org",
            "حجم فروش شرکت‌ها، نرخ رسمی، موجودی، قیمت نمادهای مرتبط روی TSETMC، و زمان تصمیم نرخ‌گذاری.",
        ),
    )
    created.append(public_opportunity)
    return created
