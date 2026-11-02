-- دفتر اطلاعات بازار سرمایه ایران
-- ردیف‌های تاریخی مهم حذف یا بازنویسی نمی‌شوند.
-- پیش‌بینی، نتیجه، انتساب و اصلاحیه فقط افزودنی هستند.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS id_counters (
  name TEXT PRIMARY KEY,
  value INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY,
  source_key TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  handle TEXT,
  url TEXT,
  group_name TEXT NOT NULL,
  specialty TEXT NOT NULL,
  activity_started_on TEXT,
  conflict_of_interest TEXT NOT NULL DEFAULT 'unknown',
  prior_level TEXT NOT NULL CHECK (prior_level IN ('A', 'B', 'C')),
  prior_basis TEXT NOT NULL,
  notes TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_access (
  id INTEGER PRIMARY KEY,
  source_id INTEGER NOT NULL REFERENCES sources(id),
  checked_at TEXT NOT NULL,
  method TEXT NOT NULL,
  status_code INTEGER,
  ok INTEGER NOT NULL,
  error TEXT,
  final_url TEXT,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS source_score_snapshots (
  id INTEGER PRIMARY KEY,
  source_id INTEGER NOT NULL REFERENCES sources(id),
  computed_at TEXT NOT NULL,
  level TEXT NOT NULL CHECK (level IN ('A', 'B', 'C')),
  level_basis TEXT NOT NULL CHECK (level_basis IN ('prior', 'performance')),
  predictions_total INTEGER NOT NULL DEFAULT 0,
  predictions_resolved INTEGER NOT NULL DEFAULT 0,
  hits INTEGER NOT NULL DEFAULT 0,
  misses INTEGER NOT NULL DEFAULT 0,
  partials INTEGER NOT NULL DEFAULT 0,
  hit_rate REAL,
  mean_abs_pct_error REAL,
  brier_score REAL,
  mean_lead_hours REAL,
  confirmed_news INTEGER NOT NULL DEFAULT 0,
  refuted_news INTEGER NOT NULL DEFAULT 0,
  notes TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS instruments (
  id INTEGER PRIMARY KEY,
  symbol TEXT NOT NULL,
  name TEXT,
  market TEXT NOT NULL,
  asset_class TEXT NOT NULL,
  industry TEXT,
  venue TEXT,
  underlying_symbol TEXT,
  option_type TEXT,
  strike TEXT,
  expiry_label TEXT,
  provider_key TEXT,
  listing_complete INTEGER NOT NULL DEFAULT 0,
  source_note TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  UNIQUE (symbol, market, asset_class)
);

CREATE TABLE IF NOT EXISTS quotes (
  id INTEGER PRIMARY KEY,
  instrument_id INTEGER NOT NULL REFERENCES instruments(id),
  observed_at TEXT,
  observed_at_raw TEXT NOT NULL,
  timezone_note TEXT NOT NULL,
  price TEXT NOT NULL,
  change_abs TEXT,
  change_pct TEXT,
  high_price TEXT,
  low_price TEXT,
  open_price TEXT,
  previous_close TEXT,
  volume_raw TEXT,
  value_raw TEXT,
  volume TEXT,
  trade_value TEXT,
  source_id INTEGER REFERENCES sources(id),
  source_label TEXT NOT NULL,
  quality TEXT NOT NULL,
  raw_json TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS quotes_dedupe
  ON quotes (instrument_id, observed_at_raw, source_label, price);

CREATE TRIGGER IF NOT EXISTS quotes_no_update
BEFORE UPDATE ON quotes
BEGIN
  SELECT RAISE(ABORT, 'quote history is immutable');
END;

CREATE TRIGGER IF NOT EXISTS quotes_no_delete
BEFORE DELETE ON quotes
BEGIN
  SELECT RAISE(ABORT, 'quote history cannot be deleted');
END;

CREATE TABLE IF NOT EXISTS news_items (
  id INTEGER PRIMARY KEY,
  source_id INTEGER REFERENCES sources(id),
  primary_news_id INTEGER,
  url TEXT,
  external_id TEXT,
  title TEXT NOT NULL,
  body TEXT,
  published_at TEXT,
  published_at_raw TEXT,
  fetched_at TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  verification_class TEXT NOT NULL,
  confidence TEXT NOT NULL,
  supporting_evidence TEXT,
  opposing_evidence TEXT,
  is_republication INTEGER NOT NULL DEFAULT 0,
  industry_tags TEXT,
  relevance TEXT NOT NULL,
  UNIQUE (source_id, content_hash)
);

CREATE TRIGGER IF NOT EXISTS news_items_no_update
BEFORE UPDATE ON news_items
BEGIN
  SELECT RAISE(ABORT, 'news rows are immutable');
END;

CREATE TRIGGER IF NOT EXISTS news_items_no_delete
BEFORE DELETE ON news_items
BEGIN
  SELECT RAISE(ABORT, 'news rows cannot be deleted');
END;

CREATE TABLE IF NOT EXISTS news_mentions (
  id INTEGER PRIMARY KEY,
  news_id INTEGER NOT NULL REFERENCES news_items(id),
  token TEXT NOT NULL,
  instrument_id INTEGER REFERENCES instruments(id),
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS news_assessments (
  id INTEGER PRIMARY KEY,
  news_id INTEGER NOT NULL REFERENCES news_items(id),
  assessed_at TEXT NOT NULL,
  verdict TEXT NOT NULL CHECK (verdict IN ('confirmed', 'refuted', 'unresolved')),
  basis TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS news_assessments_no_update
BEFORE UPDATE ON news_assessments
BEGIN
  SELECT RAISE(ABORT, 'news assessments are append-only');
END;

CREATE TRIGGER IF NOT EXISTS news_assessments_no_delete
BEFORE DELETE ON news_assessments
BEGIN
  SELECT RAISE(ABORT, 'news assessments cannot be deleted');
END;

CREATE TABLE IF NOT EXISTS hypotheses (
  id INTEGER PRIMARY KEY,
  public_id TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  statement TEXT NOT NULL,
  rationale TEXT NOT NULL,
  supporting_evidence TEXT NOT NULL,
  opposing_evidence TEXT NOT NULL,
  beneficiaries TEXT NOT NULL,
  harmed TEXT NOT NULL,
  visibility_window TEXT NOT NULL,
  invalidator TEXT NOT NULL,
  related_news_id INTEGER,
  confidence TEXT NOT NULL,
  status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS opportunities (
  id INTEGER PRIMARY KEY,
  public_id TEXT NOT NULL UNIQUE,
  hypothesis_id INTEGER REFERENCES hypotheses(id),
  instrument_id INTEGER REFERENCES instruments(id),
  created_at TEXT NOT NULL,
  title TEXT NOT NULL,
  asset_class TEXT,
  summary TEXT NOT NULL,
  first_source_key TEXT,
  first_published_at TEXT,
  verification_class TEXT NOT NULL,
  supporting_evidence TEXT NOT NULL,
  opposing_evidence TEXT NOT NULL,
  expected_event TEXT NOT NULL,
  fundamental_effect TEXT NOT NULL,
  price_status TEXT NOT NULL,
  reflected_in_price TEXT NOT NULL,
  horizon TEXT NOT NULL,
  scenario_up TEXT NOT NULL,
  scenario_base TEXT NOT NULL,
  scenario_down TEXT NOT NULL,
  invalidator TEXT NOT NULL,
  score_evidence REAL NOT NULL,
  score_forward REAL NOT NULL,
  score_earnings REAL NOT NULL,
  score_price_gap REAL NOT NULL,
  score_risk_reward REAL NOT NULL,
  score_timing REAL NOT NULL,
  score_total REAL NOT NULL,
  score_band TEXT NOT NULL,
  score_rationale TEXT NOT NULL,
  confidence TEXT NOT NULL,
  estimated_success_probability REAL,
  prediction_public_id TEXT,
  source_links TEXT NOT NULL,
  missing_data TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS predictions (
  id INTEGER PRIMARY KEY,
  public_id TEXT NOT NULL UNIQUE,
  registered_at TEXT NOT NULL,
  registered_at_tehran TEXT NOT NULL,
  instrument_id INTEGER NOT NULL REFERENCES instruments(id),
  symbol TEXT NOT NULL,
  asset_name TEXT NOT NULL,
  quote_id INTEGER NOT NULL REFERENCES quotes(id),
  base_price TEXT NOT NULL,
  base_price_as_of TEXT NOT NULL,
  base_price_source TEXT NOT NULL,
  prediction_type TEXT NOT NULL CHECK (prediction_type IN ('price', 'event', 'fundamental', 'performance')),
  statement TEXT NOT NULL,
  direction TEXT NOT NULL CHECK (direction IN ('up', 'down', 'neutral')),
  target_low TEXT,
  target_high TEXT,
  expected_outcome TEXT NOT NULL,
  horizon_end TEXT NOT NULL,
  horizon_trading_days INTEGER,
  success_probability REAL NOT NULL CHECK (success_probability >= 0 AND success_probability <= 1),
  evidence TEXT NOT NULL,
  opportunity_id INTEGER,
  opportunity_score REAL,
  invalidators TEXT NOT NULL,
  confidence TEXT NOT NULL CHECK (confidence IN ('low', 'medium', 'high')),
  content_hash TEXT NOT NULL UNIQUE,
  supersedes_id INTEGER REFERENCES predictions(id),
  created_by TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS predictions_no_update
BEFORE UPDATE ON predictions
BEGIN
  SELECT RAISE(ABORT, 'predictions are immutable and must be superseded by a new id');
END;

CREATE TRIGGER IF NOT EXISTS predictions_no_delete
BEFORE DELETE ON predictions
BEGIN
  SELECT RAISE(ABORT, 'predictions cannot be deleted');
END;

CREATE TABLE IF NOT EXISTS prediction_attributions (
  id INTEGER PRIMARY KEY,
  prediction_id INTEGER NOT NULL REFERENCES predictions(id),
  source_id INTEGER NOT NULL REFERENCES sources(id),
  role TEXT NOT NULL,
  UNIQUE (prediction_id, source_id, role)
);

CREATE TRIGGER IF NOT EXISTS prediction_attributions_no_update
BEFORE UPDATE ON prediction_attributions
BEGIN
  SELECT RAISE(ABORT, 'prediction attributions are immutable');
END;

CREATE TRIGGER IF NOT EXISTS prediction_attributions_no_delete
BEFORE DELETE ON prediction_attributions
BEGIN
  SELECT RAISE(ABORT, 'prediction attributions cannot be deleted');
END;

CREATE TABLE IF NOT EXISTS prediction_results (
  id INTEGER PRIMARY KEY,
  prediction_id INTEGER NOT NULL REFERENCES predictions(id),
  evaluated_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('hit', 'miss', 'partial', 'invalidated', 'insufficient_data')),
  end_price TEXT,
  end_quote_id INTEGER,
  asset_return_pct TEXT,
  index_return_pct TEXT,
  equal_weight_return_pct TEXT,
  industry_return_pct TEXT,
  target_achievement TEXT,
  max_gain_pct TEXT,
  max_drawdown_pct TEXT,
  error_abs TEXT,
  error_pct TEXT,
  event_happened TEXT,
  fundamental_effect_realized TEXT,
  deviation_note TEXT NOT NULL,
  brier_outcome REAL,
  supersedes_result_id INTEGER REFERENCES prediction_results(id),
  content_hash TEXT NOT NULL UNIQUE
);

CREATE TRIGGER IF NOT EXISTS prediction_results_no_update
BEFORE UPDATE ON prediction_results
BEGIN
  SELECT RAISE(ABORT, 'prediction results are immutable');
END;

CREATE TRIGGER IF NOT EXISTS prediction_results_no_delete
BEFORE DELETE ON prediction_results
BEGIN
  SELECT RAISE(ABORT, 'prediction results cannot be deleted');
END;

CREATE VIEW IF NOT EXISTS prediction_current_result AS
SELECT r.*
FROM prediction_results r
WHERE NOT EXISTS (
  SELECT 1 FROM prediction_results newer
  WHERE newer.supersedes_result_id = r.id
);

CREATE TABLE IF NOT EXISTS daily_reports (
  id INTEGER PRIMARY KEY,
  session_name TEXT NOT NULL,
  created_at TEXT NOT NULL,
  tehran_label TEXT NOT NULL,
  market_session_note TEXT NOT NULL,
  body TEXT NOT NULL,
  content_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analysis_corrections (
  id INTEGER PRIMARY KEY,
  created_at TEXT NOT NULL,
  target_kind TEXT NOT NULL,
  target_id INTEGER NOT NULL,
  reason TEXT NOT NULL,
  replacement_kind TEXT,
  replacement_id INTEGER
);

CREATE TRIGGER IF NOT EXISTS analysis_corrections_no_update
BEFORE UPDATE ON analysis_corrections
BEGIN
  SELECT RAISE(ABORT, 'corrections are append-only');
END;

CREATE TRIGGER IF NOT EXISTS analysis_corrections_no_delete
BEFORE DELETE ON analysis_corrections
BEGIN
  SELECT RAISE(ABORT, 'corrections cannot be deleted');
END;

CREATE TABLE IF NOT EXISTS capability_status (
  capability TEXT PRIMARY KEY,
  state TEXT NOT NULL,
  detail TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_news_fingerprint ON news_items (fingerprint);
CREATE INDEX IF NOT EXISTS idx_quotes_instrument ON quotes (instrument_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_predictions_horizon ON predictions (horizon_end);

CREATE TABLE IF NOT EXISTS fetch_log (
  id INTEGER PRIMARY KEY,
  source_key TEXT NOT NULL,
  channel TEXT NOT NULL CHECK (channel IN ('direct', 'relay')),
  url TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT NOT NULL,
  ok INTEGER NOT NULL,
  status_code INTEGER,
  error TEXT,
  records INTEGER NOT NULL DEFAULT 0,
  notes TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fetch_log_source ON fetch_log (source_key, id);

CREATE TABLE IF NOT EXISTS telegram_posts (
  id INTEGER PRIMARY KEY,
  handle TEXT NOT NULL,
  external_id TEXT NOT NULL,
  url TEXT,
  published_at TEXT,
  published_at_raw TEXT,
  text TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  page_url TEXT NOT NULL,
  UNIQUE (handle, external_id)
);

CREATE TABLE IF NOT EXISTS discovered_channels (
  handle TEXT PRIMARY KEY,
  discovered_from TEXT NOT NULL,
  discovered_at TEXT NOT NULL,
  preview_ok INTEGER,
  preview_error TEXT,
  post_count INTEGER NOT NULL DEFAULT 0,
  notes TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fundamental_observations (
  id INTEGER PRIMARY KEY,
  instrument_id INTEGER NOT NULL REFERENCES instruments(id),
  observed_at_raw TEXT NOT NULL,
  metric TEXT NOT NULL,
  value_raw TEXT NOT NULL,
  source_label TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  UNIQUE (instrument_id, metric, value_raw, source_label, observed_at_raw)
);

CREATE TABLE IF NOT EXISTS news_impacts (
  id INTEGER PRIMARY KEY,
  news_id INTEGER NOT NULL REFERENCES news_items(id),
  instrument_id INTEGER NOT NULL REFERENCES instruments(id),
  computed_at TEXT NOT NULL,
  published_at TEXT,
  price_before TEXT,
  price_after TEXT,
  price_change_pct TEXT,
  volume_before TEXT,
  volume_after TEXT,
  quote_before_id INTEGER,
  quote_after_id INTEGER,
  earnings_effect TEXT NOT NULL,
  price_reflection TEXT NOT NULL,
  missing_data TEXT NOT NULL,
  UNIQUE (news_id, instrument_id)
);
