import sqlite3
import tempfile
import unittest
from pathlib import Path

from bourse_intel.db import connect
from bourse_intel.pipeline import (
    content_hash,
    effect_direction,
    evaluate_due_predictions,
    link_clusters,
    map_entities,
    register_predictions,
)
from bourse_intel.textutil import jaccard, normalize, tokens


class TextTests(unittest.TestCase):
    def test_normalize_arabic_yeh(self):
        self.assertEqual(normalize("شركت ملي"), normalize("شرکت ملی"))

    def test_jaccard_overlap(self):
        score = jaccard(tokens("افزایش نرخ سیمان در بورس"), tokens("افزایش نرخ سیمان امروز در بورس"))
        self.assertGreater(score, 0.62)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "test.db")
        self.conn.execute(
            """
            INSERT INTO sources(key, title, kind, method, url, verification_default, credibility_prior)
            VALUES('demo_source', '[DEMO] منبع', 'demo', 'demo', 'demo://', 'reported', 0)
            """
        )
        self.conn.execute(
            """
            INSERT INTO instruments(source_key, external_id, symbol, name, company_name, market, instrument_type, industry, slug, updated_at)
            VALUES
            ('demo', '1', 'فولاد', '[DEMO] فولاد', '[DEMO] فولاد مبارکه', 'demo', 'equity', 'فلزات', 'foulad', '2026-01-01T00:00:00+00:00'),
            ('demo', '2', 'فملی', '[DEMO] فملی', '[DEMO] ملی مس', 'demo', 'equity', 'فلزات', 'meli', '2026-01-01T00:00:00+00:00'),
            ('demo', '3', 'دی', '[DEMO] دی', '', 'demo', 'equity', '', 'day', '2026-01-01T00:00:00+00:00'),
            ('demo', '4', 'رشد', '[DEMO] رشد', '', 'demo', 'equity_etf', '', 'roshd', '2026-01-01T00:00:00+00:00')
            """
        )
        self.source_id = self.conn.execute("SELECT id FROM sources").fetchone()["id"]

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _item(self, external_id: str, title: str, body: str, published: str) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO items(source_id, external_id, url, title, body, published_at, fetched_at, content_hash, verification)
            VALUES(?, ?, 'demo://', ?, ?, ?, ?, ?, 'reported')
            """,
            (self.source_id, external_id, title, body, published, published, content_hash(external_id, title + body)),
        )
        return int(cur.lastrowid)

    def test_short_symbol_needs_explicit_mark(self):
        item_id = self._item("a", "بازار در دی ماه", "دی ماه شلوغ بود", "2026-11-01T08:00:00+00:00")
        explicit = self._item("b", "نماد #دی", "بررسی #دی", "2026-11-01T09:00:00+00:00")
        steel = self._item("c", "فولاد رشد فروش داشت", "افزایش نرخ فولاد", "2026-11-01T10:00:00+00:00")
        self.assertEqual(map_entities(self.conn, [item_id]), 0)
        self.assertEqual(map_entities(self.conn, [explicit]), 1)
        self.assertGreaterEqual(map_entities(self.conn, [steel]), 1)
        linked = self.conn.execute(
            "SELECT instruments.symbol FROM item_entities JOIN instruments ON instruments.id = instrument_id WHERE item_id = ?",
            (steel,),
        ).fetchall()
        self.assertIn("فولاد", [row["symbol"] for row in linked])
        self.assertNotIn("رشد", [row["symbol"] for row in linked])

    def test_cluster_joins_near_duplicates(self):
        first = self._item("c1", "افزایش نرخ سیمان در بورس", "جزئیات افزایش نرخ سیمان", "2026-11-01T08:00:00+00:00")
        second = self._item("c2", "افزایش نرخ سیمان در بورس امروز", "جزئیات افزایش نرخ سیمان امروز", "2026-11-01T09:00:00+00:00")
        link_clusters(self.conn, [first, second])
        clusters = self.conn.execute("SELECT cluster_id FROM items ORDER BY id").fetchall()
        self.assertEqual(clusters[0]["cluster_id"], clusters[1]["cluster_id"])
        primary = self.conn.execute("SELECT is_primary FROM items WHERE id = ?", (first,)).fetchone()
        self.assertEqual(primary["is_primary"], 1)

    def test_direction_is_qualitative(self):
        direction, revenue, profit, evidence = effect_direction("شرکت از افزایش نرخ و رشد فروش خبر داد")
        self.assertEqual((direction, revenue, profit), ("up", "up", "up"))
        self.assertIn("افزایش نرخ", evidence)
        unclear = effect_direction("جلسه هیئت مدیره برگزار شد")
        self.assertEqual(unclear[0], "unclear")
        exited = effect_direction("خروج «حفارس» از زیان ده ساله")
        self.assertEqual(exited[0], "up")

    def test_predictions_are_immutable_and_evaluated(self):
        instrument = self.conn.execute("SELECT id FROM instruments WHERE symbol = 'فولاد'").fetchone()["id"]
        self.conn.execute(
            """
            INSERT INTO predictions(created_at, instrument_id, source_id, cluster_id, direction, probability, horizon_end, base_price, base_price_time, rationale, invalidators, evidence_level, method)
            VALUES('2026-01-01T00:00:00+00:00', ?, ?, NULL, 'up', 0.5, '2026-01-02T00:00:00+00:00', 100, '2026-01-01T00:00:00+00:00', '[DEMO] دلیل', '[DEMO] ابطال', 'reported', 'rule_v1')
            """,
            (instrument, self.source_id),
        )
        self.conn.commit()
        prediction_id = self.conn.execute("SELECT id FROM predictions").fetchone()["id"]
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("DELETE FROM predictions WHERE id = ?", (prediction_id,))
        self.conn.rollback()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE predictions SET rationale = 'changed' WHERE id = ?", (prediction_id,))
        self.conn.rollback()
        self.conn.execute(
            """
            INSERT INTO quotes(instrument_id, observed_at, trade_time, last_price, change_percent, volume, value, source_key)
            VALUES(?, '2026-01-03T00:00:00+00:00', '', 110, 0.1, 1, 1, 'demo')
            """,
            (instrument,),
        )
        made = evaluate_due_predictions(self.conn, "2026-01-04T00:00:00+00:00")
        self.assertEqual(made, 1)
        outcome = self.conn.execute("SELECT outcome, return_pct FROM prediction_evaluations").fetchone()
        self.assertEqual(outcome["outcome"], "hit")
        self.assertAlmostEqual(outcome["return_pct"], 0.1)
        still = self.conn.execute("SELECT rationale FROM predictions").fetchone()["rationale"]
        self.assertEqual(still, "[DEMO] دلیل")

    def test_register_skips_unclear(self):
        made = register_predictions(self.conn, "2026-11-02T00:00:00+00:00")
        self.assertEqual(made, 0)


if __name__ == "__main__":
    unittest.main()
