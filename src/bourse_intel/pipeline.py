from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone

from bourse_intel.textutil import jaccard, normalize, tokens

POSITIVE = (
    "افزایش نرخ",
    "رشد فروش",
    "قرارداد جدید",
    "تعدیل مثبت",
    "افزایش سرمایه",
    "سود خالص",
    "مجوز صادرات",
    "بهره برداری",
    "بهره‌برداری",
    "رکورد تولید",
    "افزایش تولید",
)
NEGATIVE = (
    "کاهش نرخ",
    "افت فروش",
    "تعدیل منفی",
    "زیان",
    "آتش سوزی",
    "آتش‌سوزی",
    "توقف نماد",
    "تعلیق",
    "تحریم",
    "ورشکست",
    "کاهش تولید",
    "قطعی گاز",
    "قطعی برق",
    "حادثه",
)

HORIZON_DAYS = 7
MOVE_THRESHOLD = 0.005
# نمادهایی که عین کلمه رایج فارسی‌اند. بدون علامت # یا گیومه به خبر وصل نمی‌شوند.
AMBIGUOUS_SYMBOLS = {
    "ارزش",
    "آینده",
    "انرژی",
    "بزرگ",
    "بورس",
    "تداوم",
    "تصمیم",
    "جهش",
    "دی",
    "رشد",
    "شهر",
    "مدیریت",
    "مهر",
    "نماد",
    "نشان",
    "هدف",
    "هرمز",
    "همراه",
    "آگاه",
    "انتخاب",
    "پرداخت",
    "پایدار",
    "توان",
    "خلیج",
    "کالا",
    "کیان",
    "ناوگان",
    "همسنگ",
}


def content_hash(title: str, body: str) -> str:
    return hashlib.sha256(normalize(f"{title}\n{body[:400]}").encode("utf-8")).hexdigest()


def effect_direction(text: str) -> tuple[str, str, str, str]:
    """جهت کیفی. عدد سود ساخته نمی‌شود."""
    import re

    norm = normalize(text)
    exited_loss = re.search(r"خروج\s+\S+\s+از\s+زیان", norm) or "خروج از زیان" in norm or "کاهش زیان" in norm
    if exited_loss:
        norm = norm.replace("زیان", " ")
    pos = [phrase for phrase in POSITIVE if normalize(phrase) in norm]
    if exited_loss:
        pos = ["خروج از زیان", *pos]
    neg = [phrase for phrase in NEGATIVE if normalize(phrase) in norm]
    if pos and not neg:
        return "up", "up", "up", "عبارت‌های مثبت: " + "، ".join(pos)
    if neg and not pos:
        return "down", "down", "down", "عبارت‌های منفی: " + "، ".join(neg)
    if pos and neg:
        return "unclear", "unclear", "unclear", "عبارت مثبت و منفی با هم دیده شد"
    return "unclear", "unclear", "unclear", "کلیدواژه سود و زیان پیدا نشد"


def link_clusters(conn: sqlite3.Connection, fresh_ids: list[int]) -> None:
    if not fresh_ids:
        return
    window_start = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    recent = conn.execute(
        """
        SELECT id, title, body, published_at, cluster_id
        FROM items
        WHERE published_at >= ? OR id IN (%s)
        """
        % ",".join("?" * len(fresh_ids)),
        (window_start, *fresh_ids),
    ).fetchall()
    token_map = {row["id"]: tokens(f"{row['title']} {row['body'][:500]}") for row in recent}
    for item_id in fresh_ids:
        item = next(row for row in recent if row["id"] == item_id)
        if item["cluster_id"]:
            continue
        best_id = None
        best_score = 0.0
        for other in recent:
            if other["id"] == item_id or not other["cluster_id"]:
                continue
            score = jaccard(token_map[item_id], token_map[other["id"]])
            if score > best_score:
                best_score = score
                best_id = other["cluster_id"]
        if best_id is not None and best_score >= 0.62:
            cluster_id = best_id
        else:
            cur = conn.execute(
                """
                INSERT INTO clusters(primary_item_id, first_published_at, title_norm, corroboration_count, verification)
                VALUES(?, ?, ?, 1, 'unconfirmed')
                """,
                (item_id, item["published_at"], normalize(item["title"])[:240]),
            )
            cluster_id = int(cur.lastrowid)
        conn.execute("UPDATE items SET cluster_id = ? WHERE id = ?", (cluster_id, item_id))
        for row in recent:
            if row["id"] == item_id:
                row_dict = dict(row)
                row_dict["cluster_id"] = cluster_id
                recent[recent.index(row)] = row_dict
                break
    _refresh_clusters(conn)


def _refresh_clusters(conn: sqlite3.Connection) -> None:
    clusters = conn.execute("SELECT id FROM clusters").fetchall()
    for cluster in clusters:
        members = conn.execute(
            """
            SELECT items.id, items.published_at, items.verification, sources.key AS source_key
            FROM items JOIN sources ON sources.id = items.source_id
            WHERE items.cluster_id = ?
            ORDER BY items.published_at, items.id
            """,
            (cluster["id"],),
        ).fetchall()
        if not members:
            continue
        primary = members[0]
        sources = {row["source_key"] for row in members}
        verifications = {row["verification"] for row in members}
        if "confirmed" in verifications:
            verification = "confirmed"
        elif len(sources) >= 2:
            verification = "corroborated"
        elif "reported" in verifications and len(sources) == 1:
            verification = "reported"
        else:
            verification = "unconfirmed"
        conn.execute("UPDATE items SET is_primary = 0 WHERE cluster_id = ?", (cluster["id"],))
        conn.execute("UPDATE items SET is_primary = 1 WHERE id = ?", (primary["id"],))
        conn.execute(
            """
            UPDATE clusters
            SET primary_item_id = ?, first_published_at = ?, corroboration_count = ?, verification = ?
            WHERE id = ?
            """,
            (primary["id"], primary["published_at"], len(sources), verification, cluster["id"]),
        )


def map_entities(conn: sqlite3.Connection, item_ids: list[int]) -> int:
    instruments = conn.execute(
        """
        SELECT id, symbol, company_name FROM instruments
        WHERE instrument_type != 'index' AND length(symbol) >= 2
        """
    ).fetchall()
    symbols = sorted(
        ((row["symbol"].strip(), row["id"], "symbol") for row in instruments if row["symbol"].strip()),
        key=lambda row: len(row[0]),
        reverse=True,
    )
    companies = sorted(
        (
            (row["company_name"].strip(), row["id"], "company")
            for row in instruments
            if row["company_name"] and len(row["company_name"].strip()) >= 6
        ),
        key=lambda row: len(row[0]),
        reverse=True,
    )
    linked = 0
    for item_id in item_ids:
        item = conn.execute("SELECT title, body FROM items WHERE id = ?", (item_id,)).fetchone()
        text = f"{item['title']}\n{item['body']}"
        seen: set[int] = set()
        for label, instrument_id, kind in (*symbols, *companies):
            if instrument_id in seen:
                continue
            explicit = _explicit_symbol(text, label)
            if kind == "symbol" and label in AMBIGUOUS_SYMBOLS and not explicit:
                continue
            if kind == "symbol" and len(label) < 3 and not explicit:
                continue
            if _contains_term(text, label):
                conn.execute(
                    """
                    INSERT OR IGNORE INTO item_entities(item_id, instrument_id, match_on)
                    VALUES(?, ?, ?)
                    """,
                    (item_id, instrument_id, kind),
                )
                seen.add(instrument_id)
                linked += 1
    return linked


def _explicit_symbol(text: str, symbol: str) -> bool:
    return any(token in text for token in (f"#{symbol}", f"«{symbol}»", f"({symbol})"))


def _contains_term(text: str, term: str) -> bool:
    if term.startswith("بانک ") or term.endswith("(سهامی عام)"):
        term = term.replace("(سهامی عام)", "").strip()
    if len(term) < 2:
        return False
    pattern = r"(?<![\w\u0600-\u06FF])" + re_escape(term) + r"(?![\w\u0600-\u06FF])"
    return re_search(pattern, text)


def re_escape(term: str) -> str:
    import re

    return re.escape(term)


def re_search(pattern: str, text: str) -> bool:
    import re

    return re.search(pattern, text) is not None


def build_impacts(conn: sqlite3.Connection, item_ids: list[int], now: str) -> int:
    made = 0
    for item_id in item_ids:
        item = conn.execute("SELECT title, body, published_at FROM items WHERE id = ?", (item_id,)).fetchone()
        entities = conn.execute(
            "SELECT instrument_id FROM item_entities WHERE item_id = ?",
            (item_id,),
        ).fetchall()
        direction, revenue, profit, evidence = effect_direction(f"{item['title']} {item['body']}")
        for entity in entities:
            reflection = price_reflection(conn, entity["instrument_id"], item["published_at"])
            conn.execute(
                """
                INSERT INTO impacts(item_id, instrument_id, direction, revenue_effect, profit_effect, price_reflection, evidence, created_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_id, instrument_id) DO UPDATE SET
                    direction=excluded.direction,
                    revenue_effect=excluded.revenue_effect,
                    profit_effect=excluded.profit_effect,
                    price_reflection=excluded.price_reflection,
                    evidence=excluded.evidence
                """,
                (item_id, entity["instrument_id"], direction, revenue, profit, reflection, evidence, now),
            )
            made += 1
    return made


def price_reflection(conn: sqlite3.Connection, instrument_id: int, published_at: str) -> str:
    before = conn.execute(
        """
        SELECT last_price, observed_at FROM quotes
        WHERE instrument_id = ? AND observed_at <= ? AND last_price IS NOT NULL
        ORDER BY observed_at DESC LIMIT 1
        """,
        (instrument_id, published_at),
    ).fetchone()
    after = conn.execute(
        """
        SELECT last_price, observed_at FROM quotes
        WHERE instrument_id = ? AND observed_at > ? AND last_price IS NOT NULL
        ORDER BY observed_at DESC LIMIT 1
        """,
        (instrument_id, published_at),
    ).fetchone()
    if before is None or after is None or not before["last_price"]:
        return "insufficient_history"
    change = (after["last_price"] - before["last_price"]) / before["last_price"]
    if abs(change) < MOVE_THRESHOLD:
        return "not_reflected"
    return "reflected"


def build_opportunities(conn: sqlite3.Connection, now: str) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=36)).isoformat()
    rows = conn.execute(
        """
        SELECT clusters.id AS cluster_id, clusters.verification, items.id AS item_id,
               items.title, item_entities.instrument_id, instruments.instrument_type,
               impacts.direction, impacts.price_reflection, impacts.evidence
        FROM clusters
        JOIN items ON items.id = clusters.primary_item_id
        JOIN item_entities ON item_entities.item_id = items.id
        JOIN instruments ON instruments.id = item_entities.instrument_id
        LEFT JOIN impacts ON impacts.item_id = items.id AND impacts.instrument_id = instruments.id
        WHERE items.published_at >= ?
          AND instruments.instrument_type != 'index'
        """,
        (cutoff,),
    ).fetchall()
    made = 0
    for row in rows:
        kind = _opportunity_kind(row["instrument_type"])
        thesis = f"{row['title']} | اثر کیفی: {row['direction'] or 'unclear'} | {row['evidence'] or ''}"
        conn.execute(
            """
            INSERT INTO opportunities(cluster_id, instrument_id, kind, thesis, evidence_level, price_reflection, created_at, status)
            VALUES(?, ?, ?, ?, ?, ?, ?, 'open')
            ON CONFLICT(cluster_id, instrument_id) DO UPDATE SET
                thesis=excluded.thesis,
                evidence_level=excluded.evidence_level,
                price_reflection=excluded.price_reflection
            """,
            (
                row["cluster_id"],
                row["instrument_id"],
                kind,
                thesis[:1000],
                row["verification"],
                row["price_reflection"] or "insufficient_history",
                now,
            ),
        )
        made += 1
    return made


def _opportunity_kind(instrument_type: str) -> str:
    if instrument_type in {"call_option", "put_option", "option"}:
        return "option"
    if instrument_type == "futures":
        return "futures"
    if instrument_type in {"commodity_etf", "commodity_spot"}:
        return "commodity"
    if instrument_type in {"equity_etf", "fixed_income_etf", "mutual_fund"}:
        return "fund"
    if instrument_type == "bond":
        return "bond"
    return "equity"


def register_predictions(conn: sqlite3.Connection, now: str) -> int:
    rows = conn.execute(
        """
        SELECT opportunities.id AS opportunity_id, opportunities.cluster_id, opportunities.instrument_id,
               opportunities.evidence_level, impacts.direction, impacts.evidence, items.published_at,
               items.title, items.body, items.source_id, instruments.symbol,
               quotes.last_price, quotes.trade_time
        FROM opportunities
        JOIN instruments ON instruments.id = opportunities.instrument_id
        JOIN impacts ON impacts.instrument_id = opportunities.instrument_id
        JOIN items ON items.id = impacts.item_id AND items.cluster_id = opportunities.cluster_id AND items.is_primary = 1
        JOIN quotes ON quotes.id = (
            SELECT id FROM quotes WHERE instrument_id = opportunities.instrument_id AND last_price IS NOT NULL
            ORDER BY observed_at DESC LIMIT 1
        )
        WHERE impacts.direction IN ('up', 'down')
          AND opportunities.evidence_level IN ('confirmed', 'corroborated', 'reported')
          AND (
              SELECT COUNT(*) FROM item_entities WHERE item_id = items.id
          ) <= 4
        """
    ).fetchall()
    made = 0
    for row in rows:
        exists = conn.execute(
            "SELECT 1 FROM predictions WHERE cluster_id = ? AND instrument_id = ? AND method = 'rule_v1'",
            (row["cluster_id"], row["instrument_id"]),
        ).fetchone()
        if exists:
            continue
        if not _explicit_symbol(f"{row['title']}\n{row['body']}", row["symbol"]):
            continue
        probability = {"confirmed": 0.62, "corroborated": 0.55, "reported": 0.48}.get(row["evidence_level"], 0.4)
        horizon = _plus_days(row["published_at"], HORIZON_DAYS)
        conn.execute(
            """
            INSERT INTO predictions(
                created_at, instrument_id, source_id, cluster_id, direction, probability,
                horizon_end, base_price, base_price_time, rationale, invalidators, evidence_level, method
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'rule_v1')
            """,
            (
                now,
                row["instrument_id"],
                row["source_id"],
                row["cluster_id"],
                row["direction"],
                probability,
                horizon,
                row["last_price"],
                row["trade_time"] or now,
                (row["evidence"] or "")[:500],
                "تکذیب خبر، یا حرکت قیمت خلاف جهت تا پایان افق، این فرض را باطل می‌کند.",
                row["evidence_level"],
            ),
        )
        made += 1
    return made


def _plus_days(published_at: str, days: int) -> str:
    try:
        base = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        base = datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return (base + timedelta(days=days)).isoformat()


def evaluate_due_predictions(conn: sqlite3.Connection, now: str) -> int:
    due = conn.execute(
        """
        SELECT predictions.*
        FROM predictions
        WHERE horizon_end <= ?
          AND NOT EXISTS (
              SELECT 1 FROM prediction_evaluations WHERE prediction_id = predictions.id
          )
        """,
        (now,),
    ).fetchall()
    made = 0
    for row in due:
        quote = conn.execute(
            """
            SELECT last_price, observed_at FROM quotes
            WHERE instrument_id = ? AND observed_at >= ? AND last_price IS NOT NULL
            ORDER BY observed_at ASC LIMIT 1
            """,
            (row["instrument_id"], row["horizon_end"]),
        ).fetchone()
        if quote is None or not row["base_price"]:
            conn.execute(
                """
                INSERT INTO prediction_evaluations(prediction_id, evaluated_at, price, return_pct, outcome, note)
                VALUES(?, ?, NULL, NULL, 'insufficient', 'بعد از افق، قیمت ذخیره‌شده‌ای وجود ندارد')
                """,
                (row["id"], now),
            )
            made += 1
            continue
        return_pct = (quote["last_price"] - row["base_price"]) / row["base_price"]
        moved = abs(return_pct) >= MOVE_THRESHOLD
        hit = (row["direction"] == "up" and return_pct >= MOVE_THRESHOLD) or (
            row["direction"] == "down" and return_pct <= -MOVE_THRESHOLD
        )
        outcome = "hit" if hit else "miss"
        conn.execute(
            """
            INSERT INTO prediction_evaluations(prediction_id, evaluated_at, price, return_pct, outcome, note)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                now,
                quote["last_price"],
                return_pct,
                outcome,
                f"قیمت بعد از افق در {quote['observed_at']}",
            ),
        )
        made += 1
    return made
