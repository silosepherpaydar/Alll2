from __future__ import annotations

import sqlite3
from html import escape
from urllib.parse import parse_qs, urlparse


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, params).fetchall()


def render_dashboard(conn: sqlite3.Connection, query: str) -> str:
    params = parse_qs(urlparse("http://local/?" + query).query)
    q = (params.get("q") or [""])[0].strip()
    kind = (params.get("kind") or [""])[0].strip()
    like = f"%{q}%"
    type_counts = _rows(
        conn,
        "SELECT instrument_type, COUNT(*) AS n FROM instruments GROUP BY instrument_type ORDER BY n DESC",
    )
    probes = _rows(
        conn,
        """
        SELECT source_key, status, http_status, items_seen, items_new, message, finished_at
        FROM collector_runs
        WHERE id IN (SELECT MAX(id) FROM collector_runs GROUP BY source_key)
        ORDER BY source_key
        """,
    )
    news = _rows(
        conn,
        """
        SELECT items.title, items.url, items.published_at, items.verification, sources.title AS source,
               clusters.verification AS cluster_verification, clusters.corroboration_count, clusters.first_published_at
        FROM items
        JOIN sources ON sources.id = items.source_id
        LEFT JOIN clusters ON clusters.id = items.cluster_id
        WHERE items.is_primary = 1 AND (? = '' OR items.title LIKE ? OR items.body LIKE ?)
        ORDER BY items.published_at DESC
        LIMIT 40
        """,
        (q, like, like),
    )
    rumors = [row for row in news if (row["cluster_verification"] or row["verification"]) == "unconfirmed"]
    opportunities = _rows(
        conn,
        """
        SELECT opportunities.kind, opportunities.evidence_level, opportunities.price_reflection, opportunities.thesis,
               instruments.symbol, instruments.instrument_type, instruments.company_name
        FROM opportunities
        JOIN instruments ON instruments.id = opportunities.instrument_id
        WHERE opportunities.status = 'open'
          AND (? = '' OR opportunities.kind = ?)
          AND (? = '' OR instruments.symbol LIKE ? OR opportunities.thesis LIKE ?)
        ORDER BY opportunities.created_at DESC
        LIMIT 60
        """,
        (kind, kind, q, like, like),
    )
    predictions = _rows(
        conn,
        """
        SELECT predictions.created_at, predictions.direction, predictions.probability, predictions.horizon_end,
               predictions.base_price, predictions.evidence_level, predictions.rationale, predictions.invalidators,
               instruments.symbol, sources.title AS source,
               prediction_evaluations.outcome, prediction_evaluations.return_pct
        FROM predictions
        JOIN instruments ON instruments.id = predictions.instrument_id
        LEFT JOIN sources ON sources.id = predictions.source_id
        LEFT JOIN prediction_evaluations ON prediction_evaluations.prediction_id = predictions.id
        ORDER BY predictions.created_at DESC
        LIMIT 40
        """,
    )
    sources = _rows(
        conn,
        """
        SELECT sources.title, sources.kind, sources.method, sources.credibility_prior, sources.verification_default,
               sources.url,
               SUM(CASE WHEN prediction_evaluations.outcome = 'hit' THEN 1 ELSE 0 END) AS hits,
               SUM(CASE WHEN prediction_evaluations.outcome = 'miss' THEN 1 ELSE 0 END) AS misses,
               SUM(CASE WHEN prediction_evaluations.outcome = 'insufficient' THEN 1 ELSE 0 END) AS insufficient
        FROM sources
        LEFT JOIN predictions ON predictions.source_id = sources.id
        LEFT JOIN prediction_evaluations ON prediction_evaluations.prediction_id = predictions.id
        GROUP BY sources.id
        ORDER BY sources.credibility_prior DESC, sources.title
        """,
    )
    reports = _rows(conn, "SELECT slot, tehran_date, generated_at, body FROM reports ORDER BY generated_at DESC LIMIT 6")
    watch = _rows(
        conn,
        """
        SELECT instruments.symbol, instruments.company_name, instruments.instrument_type, watchlist.note
        FROM watchlist JOIN instruments ON instruments.id = watchlist.instrument_id
        ORDER BY instruments.symbol
        """,
    )
    parts = [
        "<!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'>",
        "<title>هوش بازار سرمایه</title>",
        "<style>body{font-family:Tahoma,sans-serif;margin:24px;line-height:1.5}table{border-collapse:collapse;width:100%;margin:8px 0 24px}td,th{border:1px solid #ccc;padding:6px;vertical-align:top}nav a{margin-left:12px} .blocked{color:#8a4b08}</style>",
        "</head><body>",
        "<h1>هوش بازار سرمایه</h1>",
        "<p>فقط ردیف‌هایی نشان داده می‌شود که جمع‌آوری واقعی در پایگاه نوشته است. عدد ساختگی در این صفحه نیست.</p>",
        "<nav>",
        "<a href='#market'>بازار</a><a href='#news'>اخبار</a><a href='#forward'>پیش‌نگر</a>",
        "<a href='#rumor'>تأییدنشده</a><a href='#opp'>فرصت‌ها</a><a href='#watch'>تحت نظر</a>",
        "<a href='#sources'>منابع</a><a href='#ledger'>پیش‌بینی</a><a href='#results'>نتایج</a><a href='#reports'>گزارش</a>",
        "</nav>",
        "<form><input name='q' value='%s' placeholder='جست‌وجو'> " % escape(q),
        "<select name='kind'>",
        "<option value=''>همه فرصت‌ها</option>",
    ]
    for value, label in (
        ("equity", "سهام"),
        ("option", "آپشن"),
        ("futures", "آتی"),
        ("commodity", "کالا و گواهی"),
        ("fund", "صندوق"),
        ("bond", "اوراق"),
    ):
        selected = " selected" if kind == value else ""
        parts.append(f"<option value='{value}'{selected}>{label}</option>")
    parts.append("<button>فیلتر</button></form>")
    parts.append("<h2 id='market'>نمای بازار</h2>")
    if not type_counts:
        parts.append("<p>هنوز ابزاری ذخیره نشده. چرخه جمع‌آوری را اجرا کنید.</p>")
    else:
        parts.append("<table><tr><th>نوع</th><th>تعداد ذخیره‌شده</th></tr>")
        for row in type_counts:
            parts.append(f"<tr><td>{escape(row['instrument_type'])}</td><td>{row['n']}</td></tr>")
        parts.append("</table>")
    parts.append("<h3>سلامت اتصال‌ها</h3><table><tr><th>منبع</th><th>وضعیت</th><th>HTTP</th><th>دیده</th><th>تازه</th><th>پیام</th></tr>")
    for row in probes:
        klass = " class='blocked'" if row["status"] in {"blocked", "error"} else ""
        parts.append(
            "<tr%s><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (
                klass,
                escape(row["source_key"]),
                escape(row["status"]),
                escape(str(row["http_status"] or "")),
                row["items_seen"],
                row["items_new"],
                escape(row["message"] or ""),
            )
        )
    parts.append("</table>")
    parts.append(_news_table("اخبار مهم", news, "news"))
    parts.append(_news_table("شایعه و اطلاعات تأییدنشده", rumors, "rumor"))
    parts.append("<h2 id='forward'>فرصت‌های پیش‌نگر</h2>")
    parts.append(_opportunity_table(opportunities))
    parts.append("<h2 id='opp'>فرصت به تفکیک ابزار</h2>")
    for group, title in (
        ("equity", "سهام"),
        ("option", "آپشن و مشتقه"),
        ("commodity", "گواهی، کالا و صندوق کالایی"),
        ("fund", "صندوق"),
        ("futures", "آتی"),
    ):
        subset = [row for row in opportunities if row["kind"] == group]
        parts.append(f"<h3>{title}</h3>")
        parts.append(_opportunity_table(subset))
    parts.append("<h2 id='watch'>نمادهای تحت نظر</h2>")
    if not watch:
        parts.append("<p>فهرست خالی است. با دستور watch-symbol نماد واقعی را اضافه کنید.</p>")
    else:
        parts.append("<table><tr><th>نماد</th><th>شرکت</th><th>نوع</th><th>یادداشت</th></tr>")
        for row in watch:
            parts.append(
                "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                % (escape(row["symbol"]), escape(row["company_name"]), escape(row["instrument_type"]), escape(row["note"]))
            )
        parts.append("</table>")
    parts.append("<h2 id='sources'>بانک منابع</h2>")
    parts.append(
        "<p>اعتبار سردبیری یک پیشین ثابت است. مهارت پیش‌بینی فقط از ارزیابی دفتر محاسبه می‌شود. سرعت انتشار وقتی خوشه چندمنبعی ساخته شود جداگانه دیده می‌شود.</p>"
    )
    parts.append("<table><tr><th>منبع</th><th>جنس</th><th>روش</th><th>اعتبار پیشین</th><th>برچسب پیش‌فرض</th><th>برد</th><th>باخت</th><th>ناکافی</th></tr>")
    for row in sources:
        parts.append(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (
                escape(row["title"]),
                escape(row["kind"]),
                escape(row["method"]),
                row["credibility_prior"],
                escape(row["verification_default"]),
                row["hits"] or 0,
                row["misses"] or 0,
                row["insufficient"] or 0,
            )
        )
    parts.append("</table>")
    parts.append("<h2 id='ledger'>دفتر پیش‌بینی</h2>")
    parts.append(_prediction_table(predictions, evaluated_only=False))
    parts.append("<h2 id='results'>نتایج پیش‌بینی‌های قبلی</h2>")
    evaluated = [row for row in predictions if row["outcome"]]
    if not evaluated:
        parts.append("<p>هنوز افق هیچ پیش‌بینی‌ای تمام نشده یا قیمت بعد از افق ذخیره نشده است. ردیف‌های باز حذف نمی‌شوند.</p>")
    else:
        parts.append(_prediction_table(evaluated, evaluated_only=True))
    parts.append("<h2 id='reports'>گزارش‌های روزانه</h2>")
    if not reports:
        parts.append("<p>هنوز گزارشی تولید نشده است.</p>")
    for row in reports:
        parts.append(f"<h3>{escape(row['slot'])} — {escape(row['tehran_date'])}</h3>")
        parts.append("<pre>%s</pre>" % escape(row["body"]))
    parts.append("</body></html>")
    return "".join(parts)


def _news_table(title: str, rows: list[sqlite3.Row], anchor: str) -> str:
    html = [f"<h2 id='{anchor}'>{title}</h2>"]
    if not rows:
        html.append("<p>موردی با این فیلتر در پایگاه نیست.</p>")
        return "".join(html)
    html.append("<table><tr><th>زمان</th><th>منبع</th><th>سطح</th><th>تأییدکننده‌ها</th><th>عنوان</th></tr>")
    for row in rows:
        level = row["cluster_verification"] or row["verification"]
        html.append(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td><a href='%s'>%s</a></td></tr>"
            % (
                escape(row["published_at"] or ""),
                escape(row["source"]),
                escape(level or ""),
                row["corroboration_count"] or 1,
                escape(row["url"] or "#"),
                escape(row["title"]),
            )
        )
    html.append("</table>")
    return "".join(html)


def _opportunity_table(rows: list[sqlite3.Row]) -> str:
    if not rows:
        return "<p>فرصتی با این فیلتر ثبت نشده است.</p>"
    html = ["<table><tr><th>نماد</th><th>نوع</th><th>سطح شاهد</th><th>انعکاس قیمت</th><th>شرح</th></tr>"]
    for row in rows:
        html.append(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (
                escape(row["symbol"]),
                escape(row["instrument_type"]),
                escape(row["evidence_level"]),
                escape(row["price_reflection"]),
                escape(row["thesis"]),
            )
        )
    html.append("</table>")
    return "".join(html)


def _prediction_table(rows: list[sqlite3.Row], evaluated_only: bool) -> str:
    html = ["<table><tr><th>ثبت</th><th>نماد</th><th>جهت</th><th>احتمال</th><th>افق</th><th>قیمت مبنا</th><th>منبع</th><th>نتیجه</th><th>دلیل</th></tr>"]
    for row in rows:
        if evaluated_only and not row["outcome"]:
            continue
        ret = ""
        if row["return_pct"] is not None:
            ret = f" ({row['return_pct'] * 100:.2f}%)"
        html.append(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s%s</td><td>%s</td></tr>"
            % (
                escape(row["created_at"]),
                escape(row["symbol"]),
                escape(row["direction"]),
                row["probability"],
                escape(row["horizon_end"]),
                row["base_price"],
                escape(row["source"] or "موتور قاعده"),
                escape(row["outcome"] or "باز"),
                ret,
                escape((row["rationale"] or "")[:240]),
            )
        )
    html.append("</table>")
    return "".join(html)
