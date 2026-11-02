import sqlite3
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from bazaar_intel.clusters import copy_clusters, earliest
from bazaar_intel.db import init_db
from bazaar_intel.hypotheses import refresh_hypotheses
from bazaar_intel.ingest import parse_ime_board, parse_rss, parse_shakhesban_board, parse_telegram_channel, parse_tgju_payload
from bazaar_intel.ledger import LedgerError, evaluate_due, register_prediction
from bazaar_intel.metrics import brier_score
from bazaar_intel.ranking import rank_sources
from bazaar_intel.schedule import next_slot
from bazaar_intel.scoring import score_opportunity
from bazaar_intel.textnorm import jaccard, parse_scaled, tokens
from bazaar_intel.timeutil import TEHRAN, gregorian_to_jalali, iso_utc, jalali_to_gregorian, utc_now


class CalendarTests(unittest.TestCase):
    def test_market_date_seen_on_2_november_2026(self):
        self.assertEqual(gregorian_to_jalali(2026, 11, 2), (1405, 8, 11))
        self.assertEqual(jalali_to_gregorian(1405, 8, 11), (2026, 11, 2))

    def test_nowruz_1405(self):
        self.assertEqual(gregorian_to_jalali(2026, 3, 21), (1405, 1, 1))

    def test_preopen_is_next_on_a_monday_morning(self):
        moment = __import__("datetime").datetime(2026, 11, 2, 8, 0, tzinfo=TEHRAN)
        when, session = next_slot(moment)
        self.assertEqual(session, "preopen")
        self.assertEqual((when.hour, when.minute), (8, 50))

    def test_thursday_evening_report_comes_before_saturday_preopen(self):
        moment = __import__("datetime").datetime(2026, 11, 5, 10, 0, tzinfo=TEHRAN)
        self.assertEqual(moment.weekday(), 3)
        _when, session = next_slot(moment)
        self.assertEqual(session, "close")


class ScoreTests(unittest.TestCase):
    def test_weights_and_low_confidence(self):
        scored = score_opportunity(
            evidence=8, forward=6, earnings=5, price_gap=2, risk_reward=2, timing=3,
            rationale="داده شرکت موجود نیست", missing_critical=True,
        )
        self.assertEqual(scored.total, 26)
        self.assertEqual(scored.band, "فعلاً فاقد جذابیت کافی")
        self.assertEqual(scored.confidence, "low")

    def test_component_cannot_exceed_its_cap(self):
        with self.assertRaises(ValueError):
            score_opportunity(
                evidence=21, forward=0, earnings=0, price_gap=0, risk_reward=0, timing=0,
                rationale="x", missing_critical=True,
            )

    def test_brier(self):
        self.assertAlmostEqual(brier_score([(0.7, 1), (0.7, 0)]), 0.29)


class ParseTests(unittest.TestCase):
    def test_shakhesban_row_keeps_source_numbers(self):
        cells = ["توسن", "توسعه سامانه", "سهام", "فرابورس", "17,690", "10", "0.06", "17,440", "-240", "-1.36", "1405/08/11", "377", "6.625 میلیون", "115.558 میلیارد", "17,574", "17,574", "17,680", "17,160", "17,980"]
        html = "<table><tr data-symbol=\"توسن\">" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr></table>"
        row = parse_shakhesban_board(html)[0]
        self.assertTrue(row["parsed"])
        self.assertEqual(row["last"], "17690")
        self.assertEqual(row["trade_date"], "1405/08/11")
        self.assertEqual(parse_scaled(row["volume_raw"])[0], "6625000")

    def test_shifted_board_does_not_invent_a_price(self):
        html = '<tr data-symbol="توسن"><td>توسن</td><td>فقط نام</td></tr>'
        row = parse_shakhesban_board(html)[0]
        self.assertFalse(row["parsed"])
        self.assertNotIn("last", row)

    def test_ime_change_split(self):
        html = """
        <tr class="ime-row"><td>عيار</td><td>صندوق طلا</td><td>691,430</td>
        <td>1,212 (0.18%)</td><td>18:00:00</td><td>691,977</td><td>-</td>
        <td>688,901</td><td>696,480</td><td>10</td><td>52,697,900</td><td>36.466 هزار میلیارد</td></tr>
        """
        row = parse_ime_board(html)[0]
        self.assertEqual(row["symbol"], "عيار")
        self.assertEqual(row["last"], "691430")
        self.assertEqual(row["change_pct"], "0.18")
        self.assertEqual(parse_scaled(row["value_raw"])[0], "36466000000000")

    def test_tgju_drops_stale_contracts(self):
        payload = {"current": {
            "oil_brent": {"p": "109.809", "dp": 3.3, "h": "110", "l": "100", "ts": "2026-11-02 09:19:43"},
            "ime_option_old": {"p": "10", "dp": 0, "ts": "2020-01-01 00:00:00"},
        }}
        rows = parse_tgju_payload(payload)
        self.assertEqual([row["key"] for row in rows], ["oil_brent"])

    def test_rss_and_telegram(self):
        rss = """<?xml version="1.0"?><rss><channel><item>
          <title>افزایش نرخ خوراک پتروشیمی</title>
          <link>https://example.com/a</link>
          <description>جزئیات</description>
          <pubDate>Mon, 02 Nov 2026 05:50:00 GMT</pubDate>
        </item></channel></rss>"""
        item = parse_rss(rss)[0]
        self.assertEqual(item["title"], "افزایش نرخ خوراک پتروشیمی")
        self.assertTrue(item["published_at"].startswith("2026-11-02"))
        html = """
        <div data-post="codalscan/10">
          <time datetime="2026-11-02T03:30:24+00:00"></time>
          <div class="tgme_widget_message_text">گزارش ماهانه #فولاد منتشر شد</div>
        </div>
        """
        message = parse_telegram_channel(html, "codalscan")[0]
        self.assertEqual(message["url"], "https://t.me/codalscan/10")
        self.assertIn("فولاد", message["body"])


class RelevanceTests(unittest.TestCase):
    def test_traffic_crash_is_not_market_news(self):
        from bazaar_intel.textnorm import effective_verification, looks_market_relevant

        self.assertFalse(looks_market_relevant("تصادف سه خودرو در بلوار میامی مشهد ۳ کشته بر جای گذاشت"))
        self.assertTrue(looks_market_relevant("اطلاعیه کدال #فولاد درباره نرخ فروش"))
        self.assertEqual(effective_verification("single_specialist", "هسته بانک‌ها هک شدن؟"), "rumor")
        self.assertEqual(effective_verification("single_specialist", "اطلاعیه کدال منتشر شد"), "single_specialist")
        self.assertEqual(
            effective_verification("reputable_media", "قیمت نفت برنت دوباره زیر ۱۱۰ دلار؟"),
            "reputable_media",
        )


class CopyTests(unittest.TestCase):
    def test_reposts_are_one_cluster(self):
        text = "شرکت فولاد نرخ فروش محصول را افزایش داد و جزئیات را منتشر کرد"
        rows = [
            {"id": 1, "title": text, "body": text, "published_at": "2026-11-02T06:00:00+00:00", "source_id": 1},
            {"id": 2, "title": text, "body": text, "published_at": "2026-11-02T07:00:00+00:00", "source_id": 2},
            {"id": 3, "title": text, "body": text, "published_at": "2026-11-02T08:00:00+00:00", "source_id": 3},
        ]
        clusters = copy_clusters(rows)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(earliest(clusters[0])["source_id"], 1)
        self.assertGreater(jaccard(tokens(text), tokens(text)), 0.9)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = init_db(Path(self.tmp.name) / "test.sqlite")
        self.instrument = self.conn.execute(
            """
            INSERT INTO instruments(
              symbol, name, market, asset_class, listing_complete, source_note, first_seen_at, last_seen_at
            ) VALUES('نمونه', 'نمونه', 'tse', 'equity', 0, 'fixture', ?, ?)
            """,
            (iso_utc(), iso_utc()),
        ).lastrowid
        self.quote = self._quote("1000", utc_now(), "as_provided")
        self.source = self.conn.execute("SELECT id FROM sources LIMIT 1").fetchone()["id"]
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _quote(self, price: str, moment, quality: str) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO quotes(
              instrument_id, observed_at, observed_at_raw, timezone_note, price, source_label, quality, raw_json
            ) VALUES (?, ?, ?, 'fixture', ?, 'fixture', ?, '{}')
            """,
            (self.instrument, moment.isoformat(), moment.isoformat(), price, quality),
        )
        return int(cur.lastrowid)

    def test_prediction_cannot_be_rewritten_or_deleted(self):
        public_id = self._register()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE predictions SET statement = 'عوض شد' WHERE public_id = ?", (public_id,))
        self.conn.rollback()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("DELETE FROM predictions WHERE public_id = ?", (public_id,))
        self.conn.rollback()
        kept = self.conn.execute("SELECT statement FROM predictions WHERE public_id = ?", (public_id,)).fetchone()
        self.assertIn("۱۲۰۰", kept["statement"])

    def test_revision_is_a_new_id(self):
        first = self._register()
        first_id = self.conn.execute("SELECT id FROM predictions WHERE public_id = ?", (first,)).fetchone()["id"]
        second = register_prediction(
            self.conn,
            instrument_id=self.instrument,
            quote_id=self.quote,
            prediction_type="price",
            statement="نسخه تازه: هدف ۱۴۰۰",
            direction="up",
            expected_outcome="۱۴۰۰",
            horizon_end=(utc_now() + timedelta(days=10)).isoformat(),
            success_probability=0.4,
            evidence="شاهد تازه",
            invalidators="ابطال تازه",
            confidence="low",
            source_ids=[self.source],
            target_low="1400",
            supersedes_id=first_id,
        )
        self.assertNotEqual(first, second)
        old = self.conn.execute("SELECT statement FROM predictions WHERE public_id = ?", (first,)).fetchone()
        self.assertIn("۱۲۰۰", old["statement"])

    def test_rejects_missing_horizon_past_horizon_and_bad_quote(self):
        with self.assertRaises(LedgerError):
            register_prediction(
                self.conn, instrument_id=self.instrument, quote_id=self.quote, prediction_type="price",
                statement="بی افق", direction="up", expected_outcome="بالا", horizon_end="",
                success_probability=0.5, evidence="ش", invalidators="ا", confidence="low", source_ids=[],
            )
        with self.assertRaises(LedgerError):
            register_prediction(
                self.conn, instrument_id=self.instrument, quote_id=self.quote, prediction_type="price",
                statement="گذشته", direction="up", expected_outcome="بالا",
                horizon_end=(utc_now() - timedelta(days=1)).isoformat(),
                success_probability=0.5, evidence="ش", invalidators="ا", confidence="low", source_ids=[],
                target_low="1100",
            )
        stale = self._quote("1000", utc_now() + timedelta(seconds=1), "stale")
        with self.assertRaises(LedgerError):
            register_prediction(
                self.conn, instrument_id=self.instrument, quote_id=stale, prediction_type="price",
                statement="قیمت کهنه", direction="up", expected_outcome="بالا",
                horizon_end=(utc_now() + timedelta(days=3)).isoformat(),
                success_probability=0.5, evidence="ش", invalidators="ا", confidence="low", source_ids=[],
                target_low="1100",
            )

    def test_horizon_is_immutable_so_evaluation_uses_recorded_end(self):
        public_id = self._register(target="1200", probability=0.65)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE predictions SET horizon_end = '2000-01-01T00:00:00+00:00' WHERE public_id = ?", (public_id,))
        self.conn.rollback()
        later = utc_now() + timedelta(days=2)
        self._quote("1300", later, "as_provided")
        prediction = self.conn.execute("SELECT horizon_end FROM predictions WHERE public_id = ?", (public_id,)).fetchone()
        evaluate_due(self.conn, now=prediction["horizon_end"])
        status = self.conn.execute(
            """
            SELECT r.status, r.asset_return_pct, r.brier_outcome
            FROM prediction_current_result r
            JOIN predictions p ON p.id = r.prediction_id
            WHERE p.public_id = ?
            """,
            (public_id,),
        ).fetchone()
        self.assertEqual(status["status"], "hit")
        self.assertEqual(status["asset_return_pct"], "30")
        self.assertEqual(status["brier_outcome"], 1.0)
        before = self.conn.execute("SELECT COUNT(*) AS n FROM prediction_results").fetchone()["n"]
        evaluate_due(self.conn, now=prediction["horizon_end"])
        after = self.conn.execute("SELECT COUNT(*) AS n FROM prediction_results").fetchone()["n"]
        self.assertEqual(before, after)

    def test_insufficient_result_is_kept_when_price_arrives(self):
        public_id = self._register(target="1200", probability=0.5)
        horizon = self.conn.execute("SELECT horizon_end, id FROM predictions WHERE public_id = ?", (public_id,)).fetchone()
        evaluate_due(self.conn, now=horizon["horizon_end"])
        first = self.conn.execute("SELECT status FROM prediction_results").fetchone()
        self.assertEqual(first["status"], "insufficient_data")
        self._quote("900", utc_now() + timedelta(days=1), "as_provided")
        evaluate_due(self.conn, now=horizon["horizon_end"])
        rows = self.conn.execute("SELECT status FROM prediction_results ORDER BY id").fetchall()
        self.assertEqual([row["status"] for row in rows], ["insufficient_data", "miss"])
        current = self.conn.execute("SELECT status FROM prediction_current_result").fetchone()
        self.assertEqual(current["status"], "miss")
        correction = self.conn.execute("SELECT reason FROM analysis_corrections").fetchone()
        self.assertIn("دست نخورده", correction["reason"])

    def test_rank_uses_results_not_popularity(self):
        self.assertNotIn("followers", {row["name"] for row in self.conn.execute("PRAGMA table_info(sources)")})
        public_id = self._register(target="1200", probability=0.8)
        horizon = self.conn.execute("SELECT horizon_end FROM predictions WHERE public_id = ?", (public_id,)).fetchone()
        self._quote("1300", utc_now() + timedelta(days=1), "as_provided")
        evaluate_due(self.conn, now=horizon["horizon_end"])
        rank_sources(self.conn)
        snap = self.conn.execute(
            """
            SELECT level_basis, hits FROM source_score_snapshots
            WHERE source_id = ? ORDER BY id DESC LIMIT 1
            """,
            (self.source,),
        ).fetchone()
        self.assertEqual(snap["hits"], 1)
        self.assertEqual(snap["level_basis"], "prior")

    def test_brent_hypothesis_does_not_become_a_prediction(self):
        self.conn.execute(
            """
            INSERT INTO instruments(
              symbol, name, market, asset_class, provider_key, listing_complete, source_note, first_seen_at, last_seen_at
            ) VALUES('نفت برنت', 'نفت برنت', 'tgju', 'commodity', 'oil_brent', 0, 'fixture', ?, ?)
            """,
            (iso_utc(), iso_utc()),
        )
        instrument = self.conn.execute("SELECT id FROM instruments WHERE provider_key = 'oil_brent'").fetchone()["id"]
        self.conn.execute(
            """
            INSERT INTO quotes(
              instrument_id, observed_at, observed_at_raw, timezone_note, price, change_pct,
              source_label, quality, raw_json
            ) VALUES (?, ?, '2026-11-02 09:19:43', 'fixture', '109.809', '3.3', 'tgju:oil_brent', 'as_provided', '{}')
            """,
            (instrument, iso_utc()),
        )
        created = refresh_hypotheses(self.conn)
        self.assertEqual(len(created), 1)
        opportunity = self.conn.execute("SELECT score_total, prediction_public_id FROM opportunities").fetchone()
        self.assertLess(opportunity["score_total"], 40)
        self.assertIsNone(opportunity["prediction_public_id"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0], 0)
        self.assertEqual(refresh_hypotheses(self.conn), [])

    def _register(self, target: str = "1200", probability: float = 0.65, statement: str | None = None) -> str:
        public_id = register_prediction(
            self.conn,
            instrument_id=self.instrument,
            quote_id=self.quote,
            prediction_type="price",
            statement=statement or "افزایش قیمت به محدوده ۱۲۰۰ تا ۱۳۰۰",
            direction="up",
            expected_outcome=f"محدوده {target}",
            horizon_end=(utc_now() + timedelta(days=5)).isoformat(),
            horizon_trading_days=5,
            success_probability=probability,
            evidence="شاهد آزمایشی",
            invalidators="نرسیدن قیمت به هدف",
            confidence="low",
            source_ids=[self.source],
            target_low=target,
            target_high=str(int(target) + 100),
        )
        self.conn.commit()
        return public_id


class Phase2Tests(unittest.TestCase):
    def test_jalali_clock_is_tehran(self):
        from bazaar_intel.timeutil import jalali_datetime_to_utc

        self.assertEqual(jalali_datetime_to_utc("1405/08/11 10:09"), "2026-11-02T06:39:00+00:00")

    def test_sitemap_classifies_calls_puts_and_funds(self):
        from bazaar_intel.universe import parse_shakhesban_sitemap

        xml = """
        <urlset>
          <url><loc>https://www.shakhesban.com/markets/stock/%D9%88%D8%A8%D9%85%D9%84%D8%AA</loc><lastmod>2026-11-02T04:49:56Z</lastmod></url>
          <url><loc>https://www.shakhesban.com/markets/option/%D8%B7%D9%85%D9%84%D8%AA</loc><lastmod>2026-11-02T01:00:00Z</lastmod></url>
          <url><loc>https://www.shakhesban.com/markets/option/%D8%B6%D9%85%D9%84%D8%AA</loc></url>
          <url><loc>https://www.shakhesban.com/markets/fund/%D8%B5%D8%A7%DB%8C%D9%86%D8%AF</loc></url>
          <url><loc>https://www.shakhesban.com/markets/future/SILOR02</loc></url>
          <url><loc>https://www.shakhesban.com/markets/all</loc></url>
        </urlset>
        """
        fresh, items = parse_shakhesban_sitemap(xml)
        self.assertEqual(fresh, "2026-11-02T04:49:56Z")
        by_symbol = {item["symbol"]: item for item in items}
        self.assertEqual(set(by_symbol), {"وبملت", "طملت", "ضملت", "صایند", "SILOR02"})
        self.assertEqual(by_symbol["طملت"]["option_type"], "call")
        self.assertEqual(by_symbol["ضملت"]["option_type"], "put")
        self.assertEqual(by_symbol["صایند"]["asset_class"], "fund")
        self.assertEqual(by_symbol["SILOR02"]["asset_class"], "futures")

    def test_symbol_snapshot_refuses_a_shifted_table(self):
        from bazaar_intel.universe import parse_symbol_snapshot

        html = "<table><tr><td>بازگشایی</td><td>26,890</td><td>پایانی</td><td>27,420</td><td>530</td><td>بیشترین</td><td>27,690</td><td>کمترین</td><td>26,500</td><td>حجم</td><td>202.305</td><td>میلیون</td></tr><tr><td>تحقق سود</td><td>504</td><td>P/E TTM</td><td>13.200</td></tr></table>"
        parsed = parse_symbol_snapshot(html)
        self.assertTrue(parsed["parsed"])
        self.assertEqual(parsed["close"], "27420")
        self.assertEqual(parsed["profit_raw"], "504")
        self.assertFalse(parse_symbol_snapshot("<table><td>فقط نام</td></table>")["parsed"])

    def test_codal_relay_keeps_only_real_links(self):
        from bazaar_intel.connectors import parse_sahamyab_codal

        items = parse_sahamyab_codal(
            {
                "result": [
                    {
                        "title": "صورت‌های مالی",
                        "url": "https://www.codal.ir/Reports/Decision.aspx?LetterSerial=abc",
                        "namad": "وبملت",
                        "publishDate": "1405/08/11 10:09",
                        "corp": "بانک ملت",
                    },
                    {"title": "بدون پیوند", "url": "", "namad": "فملی"},
                ]
            }
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["symbol"], "وبملت")

    def test_telegram_handle_stays_on_public_preview(self):
        from bazaar_intel.telegram_watch import preview_url, safe_handle

        self.assertIsNone(safe_handle("joinchat"))
        self.assertIsNone(safe_handle("../etc"))
        self.assertEqual(preview_url("feghtesad", "38121"), "https://t.me/s/feghtesad?before=38121")
        with self.assertRaises(ValueError):
            preview_url("feghtesad", "http://evil")

    def test_unspecified_listing_merges_when_venue_arrives(self):
        from bazaar_intel.ingest import _upsert_instrument

        with tempfile.TemporaryDirectory() as folder:
            conn = init_db(Path(folder) / "t.sqlite")
            first = _upsert_instrument(
                conn,
                symbol="وبملت",
                market="unspecified",
                asset_class="equity",
                option_type=None,
                source_note="sitemap",
            )
            second = _upsert_instrument(
                conn,
                symbol="وبملت",
                market="tse",
                asset_class="equity",
                venue="بورس",
                source_note="board",
            )
            self.assertEqual(first, second)
            row = conn.execute("SELECT market, venue FROM instruments WHERE id = ?", (first,)).fetchone()
            self.assertEqual(row["market"], "tse")
            self.assertEqual(row["venue"], "بورس")

    def test_news_impact_uses_only_stored_prices(self):
        from bazaar_intel.analysis import refresh_impacts

        with tempfile.TemporaryDirectory() as folder:
            conn = init_db(Path(folder) / "t.sqlite")
            now = utc_now()
            conn.execute(
                """
                INSERT INTO instruments(
                  symbol, name, market, asset_class, listing_complete, source_note, first_seen_at, last_seen_at
                ) VALUES('وبملت', 'بانک ملت', 'tse', 'equity', 0, 'fixture', ?, ?)
                """,
                (iso_utc(now), iso_utc(now)),
            )
            instrument = conn.execute("SELECT id FROM instruments").fetchone()["id"]
            source = conn.execute("SELECT id FROM sources WHERE source_key = 'sahamyab'").fetchone()["id"]
            published = iso_utc(now)
            conn.execute(
                """
                INSERT INTO news_items(
                  source_id, title, body, published_at, published_at_raw, fetched_at, content_hash,
                  fingerprint, verification_class, confidence, relevance
                ) VALUES (?, 'افزایش سرمایه', 'نماد کدال', ?, '1405/08/11 10:09', ?, 'hash', 'fp',
                         'relayed_official', 'low', 'market')
                """,
                (source, published, iso_utc(now)),
            )
            news_id = conn.execute("SELECT id FROM news_items").fetchone()["id"]
            conn.execute(
                "INSERT INTO news_mentions(news_id, token, instrument_id, created_at) VALUES (?, 'وبملت', ?, ?)",
                (news_id, instrument, iso_utc(now)),
            )
            before = iso_utc(now - timedelta(hours=2))
            after = iso_utc(now + timedelta(hours=2))
            for stamp, price, volume in ((before, "1000", "10"), (after, "1004", "12")):
                conn.execute(
                    """
                    INSERT INTO quotes(
                      instrument_id, observed_at, observed_at_raw, timezone_note, price, volume,
                      source_label, quality, raw_json
                    ) VALUES (?, ?, ?, 'fixture', ?, ?, 'fixture', 'as_provided', '{}')
                    """,
                    (instrument, stamp, stamp, price, volume),
                )
            self.assertEqual(refresh_impacts(conn), 1)
            impact = conn.execute("SELECT price_change_pct, volume_before, volume_after, earnings_effect FROM news_impacts").fetchone()
            self.assertEqual(impact["price_change_pct"], "0.4000")
            self.assertEqual(impact["volume_before"], "10")
            self.assertEqual(impact["volume_after"], "12")
            self.assertIn("محاسبه نشد", impact["earnings_effect"])
            opportunity = conn.execute("SELECT score_total, prediction_public_id FROM opportunities").fetchone()
            self.assertLess(opportunity["score_total"], 40)
            self.assertIsNone(opportunity["prediction_public_id"])


if __name__ == "__main__":
    unittest.main()
