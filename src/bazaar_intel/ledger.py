"""دفتر پیش‌بینی.

ردیف اولیه هرگز به‌روزرسانی یا حذف نمی‌شود.
اگر فرضیه عوض شود، شناسه جدید با ارجاع به شناسه قبلی ثبت می‌شود.
نتیجه در جدول جدا می‌نشیند. اصلاح نتیجه هم ردیف جدید است، نه بازنویسی.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from decimal import Decimal

from bazaar_intel.db import next_public_id
from bazaar_intel.timeutil import iso_utc, parse_iso, tehran_label

UNUSABLE_QUOTE_QUALITIES = {"conflicting", "stale"}


class LedgerError(ValueError):
    pass


def _hash(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _decimal_text(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    rendered = format(Decimal(value), "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def register_prediction(
    conn: sqlite3.Connection,
    *,
    instrument_id: int,
    quote_id: int,
    prediction_type: str,
    statement: str,
    direction: str,
    expected_outcome: str,
    horizon_end: str,
    success_probability: float,
    evidence: str,
    invalidators: str,
    confidence: str,
    source_ids: list[int],
    target_low: str | None = None,
    target_high: str | None = None,
    horizon_trading_days: int | None = None,
    opportunity_id: int | None = None,
    opportunity_score: float | None = None,
    supersedes_id: int | None = None,
    created_by: str = "system",
) -> str:
    if prediction_type not in {"price", "event", "fundamental", "performance"}:
        raise LedgerError("نوع پیش‌بینی نامعتبر است")
    if direction not in {"up", "down", "neutral"}:
        raise LedgerError("جهت پیش‌بینی نامعتبر است")
    if confidence not in {"low", "medium", "high"}:
        raise LedgerError("میزان اطمینان نامعتبر است")
    if not statement.strip() or not expected_outcome.strip() or not evidence.strip() or not invalidators.strip():
        raise LedgerError("شرح، نتیجه مورد انتظار، شواهد و عامل ابطال الزامی است")
    if not horizon_end:
        raise LedgerError("پیش‌بینی بدون افق زمانی معتبر نیست")
    horizon = parse_iso(horizon_end)
    if horizon <= parse_iso(iso_utc()):
        raise LedgerError("افق زمانی گذشته است؛ ثبت پس از معلوم شدن نتیجه مجاز نیست")
    if not 0 <= float(success_probability) <= 1:
        raise LedgerError("احتمال موفقیت باید بین صفر و یک باشد")
    if horizon_trading_days is not None and horizon_trading_days <= 0:
        raise LedgerError("افق روزهای معاملاتی باید مثبت باشد")

    quote = conn.execute("SELECT * FROM quotes WHERE id = ?", (quote_id,)).fetchone()
    if quote is None:
        raise LedgerError("قیمت مبنا پیدا نشد")
    if int(quote["instrument_id"]) != int(instrument_id):
        raise LedgerError("قیمت مبنا متعلق به همین دارایی نیست")
    if quote["quality"] in UNUSABLE_QUOTE_QUALITIES:
        raise LedgerError("قیمت مبنا متعارض یا کهنه است و قابل اتکا نیست")
    instrument = conn.execute("SELECT * FROM instruments WHERE id = ?", (instrument_id,)).fetchone()
    if instrument is None:
        raise LedgerError("دارایی پیدا نشد")
    if supersedes_id is not None:
        previous = conn.execute("SELECT id FROM predictions WHERE id = ?", (supersedes_id,)).fetchone()
        if previous is None:
            raise LedgerError("پیش‌بینی قبلی برای نسخه جدید پیدا نشد")

    payload = {
        "instrument_id": instrument_id,
        "quote_id": quote_id,
        "prediction_type": prediction_type,
        "statement": statement.strip(),
        "direction": direction,
        "target_low": _decimal_text(target_low),
        "target_high": _decimal_text(target_high),
        "expected_outcome": expected_outcome.strip(),
        "horizon_end": horizon_end,
        "horizon_trading_days": horizon_trading_days,
        "success_probability": float(success_probability),
        "evidence": evidence.strip(),
        "invalidators": invalidators.strip(),
        "confidence": confidence,
        "supersedes_id": supersedes_id,
    }
    digest = _hash(payload)
    public_id = next_public_id(conn, "prediction", "P")
    registered = iso_utc()
    conn.execute(
        """
        INSERT INTO predictions (
          public_id, registered_at, registered_at_tehran, instrument_id, symbol, asset_name,
          quote_id, base_price, base_price_as_of, base_price_source, prediction_type, statement,
          direction, target_low, target_high, expected_outcome, horizon_end, horizon_trading_days,
          success_probability, evidence, opportunity_id, opportunity_score, invalidators,
          confidence, content_hash, supersedes_id, created_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            public_id,
            registered,
            tehran_label(),
            instrument_id,
            instrument["symbol"],
            instrument["name"] or instrument["symbol"],
            quote_id,
            quote["price"],
            quote["observed_at_raw"],
            quote["source_label"],
            prediction_type,
            payload["statement"],
            direction,
            payload["target_low"],
            payload["target_high"],
            payload["expected_outcome"],
            horizon_end,
            horizon_trading_days,
            float(success_probability),
            payload["evidence"],
            opportunity_id,
            opportunity_score,
            payload["invalidators"],
            confidence,
            digest,
            supersedes_id,
            created_by,
        ),
    )
    prediction_row_id = conn.execute(
        "SELECT id FROM predictions WHERE public_id = ?",
        (public_id,),
    ).fetchone()["id"]
    for source in source_ids:
        conn.execute(
            """
            INSERT INTO prediction_attributions(prediction_id, source_id, role)
            VALUES (?, ?, 'evidence')
            """,
            (prediction_row_id, source),
        )
    return public_id


def _pct(start: str, end: str) -> str | None:
    base = Decimal(start)
    if base == 0:
        return None
    value = (Decimal(end) - base) / base * Decimal(100)
    rendered = format(value.quantize(Decimal("0.0001")), "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _latest_result(conn: sqlite3.Connection, prediction_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT * FROM prediction_current_result
        WHERE prediction_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (prediction_id,),
    ).fetchone()


def evaluate_due(conn: sqlite3.Connection, *, now: str | None = None) -> list[str]:
    """پیش‌بینی‌هایی را که افق‌شان تمام شده تعیین تکلیف می‌کند."""
    moment = now or iso_utc()
    rows = conn.execute(
        "SELECT * FROM predictions WHERE horizon_end <= ? ORDER BY id",
        (moment,),
    ).fetchall()
    done: list[str] = []
    for prediction in rows:
        current = _latest_result(conn, prediction["id"])
        if current is not None and current["status"] != "insufficient_data":
            continue
        if current is not None and current["end_quote_id"] is not None:
            continue
        result_id = _evaluate_one(conn, prediction, moment, current["id"] if current else None)
        if result_id is not None:
            done.append(prediction["public_id"])
    return done


def _evaluate_one(
    conn: sqlite3.Connection,
    prediction: sqlite3.Row,
    moment: str,
    supersedes_result_id: int | None,
) -> int | None:
    later = conn.execute(
        """
        SELECT * FROM quotes
        WHERE instrument_id = ?
          AND id != ?
          AND observed_at IS NOT NULL
          AND observed_at > ?
          AND observed_at <= ?
          AND quality NOT IN ('conflicting', 'stale')
        ORDER BY observed_at
        """,
        (
            prediction["instrument_id"],
            prediction["quote_id"],
            prediction["registered_at"],
            prediction["horizon_end"],
        ),
    ).fetchall()
    if not later:
        if supersedes_result_id is not None:
            return None
        note = (
            "پس از ثبت، قیمت قابل اتکایی داخل افق زمانی ذخیره نشده است."
            if prediction["prediction_type"] == "price"
            else "تحقق رویداد یا اثر بنیادی از روی داده موجود قابل داوری نیست."
        )
        return _insert_result(
            conn,
            prediction,
            moment,
            status="insufficient_data",
            deviation_note=note,
            brier_outcome=None,
        )

    end = later[-1]
    prices = [Decimal(row["price"]) for row in later]
    base = Decimal(prediction["base_price"])
    max_price = max(prices)
    min_price = min(prices)
    max_gain = (max_price - base) / base * Decimal(100) if base else None
    max_drawdown = (min_price - base) / base * Decimal(100) if base else None
    status, achievement = _judge_price(prediction, end["price"], max_price, min_price)
    brier = {"hit": 1.0, "partial": 0.5, "miss": 0.0}.get(status)
    index_return = _benchmark_return(conn, prediction, ("شاخص کل",), ("bourse",))
    equal_return = _benchmark_return(conn, prediction, ("شاخص کل هم‌وزن",), ("equal_weight",))
    industry_return = None
    note = "نتیجه فقط با قیمت‌های ذخیره‌شده داخل افق مقایسه شد."
    if index_return is None:
        note += " بازده شاخص کل در همین بازه موجود نیست."
    if equal_return is None:
        note += " بازده شاخص هم‌وزن موجود نیست."
    if industry_return is None:
        note += " بازده صنعت مربوط موجود نیست."
    result_id = _insert_result(
        conn,
        prediction,
        moment,
        status=status,
        end_price=end["price"],
        end_quote_id=end["id"],
        asset_return_pct=_pct(prediction["base_price"], end["price"]),
        index_return_pct=index_return,
        equal_weight_return_pct=equal_return,
        industry_return_pct=industry_return,
        target_achievement=achievement,
        max_gain_pct=_fmt(max_gain),
        max_drawdown_pct=_fmt(max_drawdown),
        error_abs=_error_abs(prediction, end["price"]),
        error_pct=_error_pct(prediction, end["price"]),
        deviation_note=note,
        brier_outcome=brier,
        supersedes_result_id=supersedes_result_id,
    )
    if supersedes_result_id is not None:
        conn.execute(
            """
            INSERT INTO analysis_corrections(
              created_at, target_kind, target_id, reason, replacement_kind, replacement_id
            ) VALUES (?, 'prediction_result', ?, ?, 'prediction_result', ?)
            """,
            (
                moment,
                supersedes_result_id,
                "نتیجه قبلی به‌دلیل کمبود داده ناکافی بود. ردیف تازه افزوده شد و ردیف قبلی دست نخورده ماند.",
                result_id,
            ),
        )
    return result_id


def _judge_price(prediction: sqlite3.Row, end_price: str, max_price: Decimal, min_price: Decimal):
    end = Decimal(end_price)
    low = Decimal(prediction["target_low"]) if prediction["target_low"] else None
    high = Decimal(prediction["target_high"]) if prediction["target_high"] else None
    direction = prediction["direction"]
    if direction == "up":
        threshold = low if low is not None else high
        if threshold is None:
            return "insufficient_data", "هدف عددی ثبت نشده است"
        if end >= threshold:
            return "hit", "قیمت پایان افق به محدوده هدف رسیده است"
        if max_price >= threshold:
            return "partial", "قیمت داخل دوره به هدف رسیده اما در پایان افق آنجا نمانده است"
        return "miss", "قیمت به کف هدف نرسیده است"
    if direction == "down":
        threshold = high if high is not None else low
        if threshold is None:
            return "insufficient_data", "هدف عددی ثبت نشده است"
        if end <= threshold:
            return "hit", "قیمت پایان افق به محدوده هدف رسیده است"
        if min_price <= threshold:
            return "partial", "قیمت داخل دوره به هدف رسیده اما در پایان افق آنجا نمانده است"
        return "miss", "قیمت به سقف هدف نرسیده است"
    if low is None or high is None:
        return "insufficient_data", "برای جهت خنثی هر دو مرز هدف لازم است"
    if low <= end <= high:
        return "hit", "قیمت پایان افق داخل محدوده خنثی مانده است"
    if low <= max_price and min_price <= high:
        return "partial", "قیمت در بخشی از دوره داخل محدوده بوده است"
    return "miss", "قیمت از محدوده خنثی خارج شده است"


def _benchmark_return(conn: sqlite3.Connection, prediction: sqlite3.Row, symbols: tuple[str, ...], keys: tuple[str, ...]) -> str | None:
    placeholders = ",".join("?" for _ in symbols)
    key_placeholders = ",".join("?" for _ in keys)
    instrument = conn.execute(
        f"""
        SELECT id FROM instruments
        WHERE symbol IN ({placeholders}) OR provider_key IN ({key_placeholders})
        ORDER BY id LIMIT 1
        """,
        (*symbols, *keys),
    ).fetchone()
    if instrument is None:
        return None
    start = conn.execute(
        """
        SELECT price FROM quotes
        WHERE instrument_id = ? AND observed_at IS NOT NULL AND observed_at <= ?
          AND quality NOT IN ('conflicting', 'stale')
        ORDER BY observed_at DESC LIMIT 1
        """,
        (instrument["id"], prediction["registered_at"]),
    ).fetchone()
    end = conn.execute(
        """
        SELECT price FROM quotes
        WHERE instrument_id = ? AND observed_at IS NOT NULL AND observed_at <= ?
          AND quality NOT IN ('conflicting', 'stale')
        ORDER BY observed_at DESC LIMIT 1
        """,
        (instrument["id"], prediction["horizon_end"]),
    ).fetchone()
    if start is None or end is None or start["price"] == end["price"] and start is end:
        if start is None or end is None:
            return None
    if start is None or end is None:
        return None
    return _pct(start["price"], end["price"])


def _fmt(value: Decimal | None) -> str | None:
    if value is None:
        return None
    rendered = format(value.quantize(Decimal("0.0001")), "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _target_mid(prediction: sqlite3.Row) -> Decimal | None:
    low = Decimal(prediction["target_low"]) if prediction["target_low"] else None
    high = Decimal(prediction["target_high"]) if prediction["target_high"] else None
    if low is not None and high is not None:
        return (low + high) / 2
    return low if low is not None else high


def _error_abs(prediction: sqlite3.Row, end_price: str) -> str | None:
    mid = _target_mid(prediction)
    if mid is None:
        return None
    return _fmt(Decimal(end_price) - mid)


def _error_pct(prediction: sqlite3.Row, end_price: str) -> str | None:
    mid = _target_mid(prediction)
    if mid is None or mid == 0:
        return None
    return _fmt((Decimal(end_price) - mid) / mid * Decimal(100))


def _insert_result(conn: sqlite3.Connection, prediction: sqlite3.Row, moment: str, **fields) -> int:
    payload = {
        "prediction_id": prediction["id"],
        "status": fields["status"],
        "end_price": fields.get("end_price"),
        "end_quote_id": fields.get("end_quote_id"),
        "asset_return_pct": fields.get("asset_return_pct"),
        "target_achievement": fields.get("target_achievement"),
        "supersedes_result_id": fields.get("supersedes_result_id"),
        "brier_outcome": fields.get("brier_outcome"),
    }
    digest = _hash(payload)
    if fields.get("supersedes_result_id") is not None:
        payload["supersedes_result_id"] = fields["supersedes_result_id"]
        digest = _hash(payload)
    cur = conn.execute(
        """
        INSERT INTO prediction_results (
          prediction_id, evaluated_at, status, end_price, end_quote_id, asset_return_pct,
          index_return_pct, equal_weight_return_pct, industry_return_pct, target_achievement,
          max_gain_pct, max_drawdown_pct, error_abs, error_pct, event_happened,
          fundamental_effect_realized, deviation_note, brier_outcome, supersedes_result_id,
          content_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            prediction["id"],
            moment,
            fields["status"],
            fields.get("end_price"),
            fields.get("end_quote_id"),
            fields.get("asset_return_pct"),
            fields.get("index_return_pct"),
            fields.get("equal_weight_return_pct"),
            fields.get("industry_return_pct"),
            fields.get("target_achievement"),
            fields.get("max_gain_pct"),
            fields.get("max_drawdown_pct"),
            fields.get("error_abs"),
            fields.get("error_pct"),
            fields.get("event_happened"),
            fields.get("fundamental_effect_realized"),
            fields["deviation_note"],
            fields.get("brier_outcome"),
            fields.get("supersedes_result_id"),
            digest,
        ),
    )
    return int(cur.lastrowid)
