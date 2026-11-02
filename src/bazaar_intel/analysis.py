"""وصل کردن خبر ذخیره‌شده به قیمت و حجم ذخیره‌شده. رقم سود ساخته نمی‌شود."""

from __future__ import annotations

import sqlite3
from decimal import Decimal

from bazaar_intel.db import next_public_id
from bazaar_intel.scoring import score_opportunity
from bazaar_intel.timeutil import iso_utc

MATERIAL = ("افزایش سرمایه", "تقسیم سود", "سود", "صورت", "شفاف", "تعدیل", "مجوز")


def refresh_impacts(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """
        SELECT n.id AS news_id, n.title, n.body, n.published_at, n.url, n.verification_class,
               m.instrument_id, i.symbol, i.asset_class
        FROM news_items n
        JOIN news_mentions m ON m.news_id = n.id AND m.instrument_id IS NOT NULL
        JOIN instruments i ON i.id = m.instrument_id
        WHERE n.published_at IS NOT NULL
        ORDER BY n.id DESC
        LIMIT 80
        """
    ).fetchall()
    written = 0
    created = 0
    for row in rows:
        impact = _compare(conn, row)
        if impact is None:
            continue
        conn.execute(
            """
            INSERT INTO news_impacts(
              news_id, instrument_id, computed_at, published_at, price_before, price_after,
              price_change_pct, volume_before, volume_after, quote_before_id, quote_after_id,
              earnings_effect, price_reflection, missing_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(news_id, instrument_id) DO UPDATE SET
              computed_at = excluded.computed_at,
              price_before = excluded.price_before,
              price_after = excluded.price_after,
              price_change_pct = excluded.price_change_pct,
              volume_before = excluded.volume_before,
              volume_after = excluded.volume_after,
              quote_before_id = excluded.quote_before_id,
              quote_after_id = excluded.quote_after_id,
              earnings_effect = excluded.earnings_effect,
              price_reflection = excluded.price_reflection,
              missing_data = excluded.missing_data
            """,
            impact,
        )
        written += 1
        if created < 5 and _unpriced(row, impact):
            if _ensure_opportunity(conn, row, impact):
                created += 1
    return written


def _compare(conn, row) -> tuple | None:
    before = conn.execute(
        """
        SELECT id, price, volume, observed_at FROM quotes
        WHERE instrument_id = ? AND observed_at IS NOT NULL AND observed_at <= ?
        ORDER BY observed_at DESC, id DESC
        LIMIT 1
        """,
        (row["instrument_id"], row["published_at"]),
    ).fetchone()
    after = conn.execute(
        """
        SELECT id, price, volume, observed_at FROM quotes
        WHERE instrument_id = ? AND observed_at IS NOT NULL AND observed_at > ?
        ORDER BY observed_at ASC, id ASC
        LIMIT 1
        """,
        (row["instrument_id"], row["published_at"]),
    ).fetchone()
    if before is None and after is None:
        return None
    change = None
    if before is not None and after is not None:
        change = _pct(before["price"], after["price"])
    profit = conn.execute(
        """
        SELECT value_raw FROM fundamental_observations
        WHERE instrument_id = ? AND metric = 'realized_profit'
        ORDER BY id DESC LIMIT 1
        """,
        (row["instrument_id"],),
    ).fetchone()
    missing = []
    if before is None or after is None:
        missing.append("یکی از دو قیمت قبل یا بعد از زمان خبر در پایگاه نیست")
    if change is None:
        missing.append("درصد تغییر قیمت قابل محاسبه نیست")
    if before is None or after is None or not before["volume"] or not after["volume"]:
        missing.append("حجم هر دو سوی خبر موجود نیست")
    if row["verification_class"] == "relayed_official":
        earnings = "اثر عددی این عنوان بر سود شرکت محاسبه نشد چون متن نامه و صورت مالی از کدال خوانده نشد."
    else:
        earnings = "اثر عددی این متن بر سود شرکت محاسبه نشد. این ردیف صورت مالی نیست."
    if profit is not None:
        earnings += f" در صفحه نماد، رقم تحقق سود {profit['value_raw']} دیده شد و به این خبر نسبت داده نشد."
    else:
        missing.append("رقم سود دوره در داده موجود نیست")
    if change is None:
        reflection = "انعکاس خبر در قیمت مشخص نیست."
    elif abs(change) < 1:
        reflection = f"فاصله قیمت بعد از خبر نسبت به قبل {change:.2f} درصد است و حرکت بزرگ‌تر از یک درصد ثبت نشده."
    else:
        reflection = f"قیمت بعد از خبر نسبت به قبل {change:.2f} درصد فاصله دارد."
    return (
        row["news_id"],
        row["instrument_id"],
        iso_utc(),
        row["published_at"],
        None if before is None else before["price"],
        None if after is None else after["price"],
        None if change is None else f"{change:.4f}",
        None if before is None else before["volume"],
        None if after is None else after["volume"],
        None if before is None else before["id"],
        None if after is None else after["id"],
        earnings,
        reflection,
        "؛ ".join(missing) if missing else "پنجره قیمت قبل و بعد موجود است",
    )


def _pct(before: str, after: str) -> float | None:
    try:
        base = Decimal(before)
        nxt = Decimal(after)
    except Exception:
        return None
    if base == 0:
        return None
    return float((nxt - base) / base * 100)


def _unpriced(row, impact: tuple) -> bool:
    text = f"{row['title']} {row['body'] or ''}"
    if not any(word in text for word in MATERIAL):
        return False
    change = impact[6]
    if change is None:
        return False
    return abs(float(change)) < 1


def _ensure_opportunity(conn, row, impact: tuple) -> bool:
    title = f"{row['symbol']}: {row['title'][:90]}"
    exists = conn.execute(
        "SELECT id FROM opportunities WHERE title = ? AND first_published_at = ?",
        (title, row["published_at"]),
    ).fetchone()
    if exists:
        return False
    change = float(impact[6])
    score = score_opportunity(
        evidence=6,
        forward=4,
        earnings=0,
        price_gap=8,
        risk_reward=0,
        timing=4,
        rationale="عنوان اطلاعیه ذخیره شده و حرکت قیمت بعد از آن کمتر از یک درصد است. متن نامه خوانده نشده، پس این سرنخ است نه نتیجه.",
        missing_critical=True,
    )
    public_id = next_public_id(conn, "opportunity", "O")
    conn.execute(
        """
        INSERT INTO opportunities (
          public_id, hypothesis_id, instrument_id, created_at, title, asset_class, summary,
          first_source_key, first_published_at, verification_class, supporting_evidence,
          opposing_evidence, expected_event, fundamental_effect, price_status, reflected_in_price,
          horizon, scenario_up, scenario_base, scenario_down, invalidator, score_evidence,
          score_forward, score_earnings, score_price_gap, score_risk_reward, score_timing,
          score_total, score_band, score_rationale, confidence, estimated_success_probability,
          source_links, missing_data
        ) VALUES (
          ?, NULL, ?, ?, ?, ?, ?, 'sahamyab', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?
        )
        """,
        (
            public_id,
            row["instrument_id"],
            iso_utc(),
            title,
            row["asset_class"],
            "سرنخ عنوان اطلاعیه در برابر قیمت ذخیره‌شده. پیش‌بینی ساخته نشده است.",
            row["published_at"],
            row["verification_class"],
            f"عنوان: {row['title']}. تغییر قیمت محاسبه‌شده: {change:.2f} درصد.",
            "متن نامه کدال و اثر سود خوانده نشده است.",
            "اگر متن نامه رویداد بااهمیتی باشد، ممکن است هنوز در قیمت دیده نشود.",
            impact[11],
            f"قبل {impact[4]} و بعد {impact[5]}",
            impact[12],
            "تا خواندن متن نامه افقی تعیین نشد.",
            "رویداد نامه مهم باشد و قیمت هنوز حرکت نکرده باشد.",
            "رویداد تکراری یا کم‌اثر باشد.",
            "متن نامه اثر را رد کند یا قیمت پیش از این حرکت را نشان داده باشد.",
            "خوانده شدن متن نامه، یا حرکت بعدی قیمت بیش از این پنجره.",
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
            row["url"] or "",
            impact[13],
        ),
    )
    return True
