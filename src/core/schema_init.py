"""
Schema migration for flex_complete_database.db.

Extracted from src/core/main.py so that standalone tools and scripts can
run schema checks without importing Flask or starting background threads.

main.py imports _ensure_schema from here. scripts/ensure_schema.py does too.
"""
import os

from src.utils.db_locking import db_connect

_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "../.."))
_DEFAULT_DB_PATH = os.path.join(_REPO_ROOT, "database", "flex_complete_database.db")

DB_PATH = os.path.abspath(os.environ.get("DB_PATH", _DEFAULT_DB_PATH))

_MIGRATIONS = [
    "ALTER TABLE token_analysis ADD COLUMN market_cap_highest_at_ts INTEGER",
    "ALTER TABLE token_analysis ADD COLUMN is_about_to_migrate BOOLEAN DEFAULT 0",
    "ALTER TABLE token_analysis ADD COLUMN migration_progress_pct REAL",
    "ALTER TABLE token_analysis ADD COLUMN migration_band TEXT",
    "ALTER TABLE token_analysis ADD COLUMN migration_signal_updated_at INTEGER",
    "ALTER TABLE token_analysis ADD COLUMN first_pre_migration_signal_at INTEGER",
    "ALTER TABLE token_analysis ADD COLUMN migration_signal_source TEXT",
    "ALTER TABLE token_analysis ADD COLUMN lifecycle_stage TEXT DEFAULT 'migration_pending'",
    "ALTER TABLE token_analysis ADD COLUMN migrated_at INTEGER",
    "ALTER TABLE token_analysis ADD COLUMN dex TEXT",
    "ALTER TABLE token_analysis ADD COLUMN pumpswap_pool_address TEXT",
    "ALTER TABLE token_analysis ADD COLUMN source_platform TEXT",
    "ALTER TABLE token_analysis ADD COLUMN is_new INTEGER DEFAULT 0",
    "ALTER TABLE token_analysis ADD COLUMN pf_ws_creator TEXT",
    "ALTER TABLE token_analysis ADD COLUMN creator_mismatch INTEGER DEFAULT 0",
    "ALTER TABLE token_market_cap_peaks ADD COLUMN raw_peak_mc_at INTEGER",
    """UPDATE token_analysis
       SET market_cap_highest_at_ts = CAST(strftime('%s', market_cap_highest_at) AS INTEGER)
       WHERE market_cap_highest_at IS NOT NULL
         AND market_cap_highest_at_ts IS NULL
         AND CAST(strftime('%s', market_cap_highest_at) AS INTEGER) > 1577836800""",
    """UPDATE token_market_cap_peaks
       SET raw_peak_mc_at = peak_market_cap_at
       WHERE raw_peak_mc_at IS NULL AND peak_market_cap_at IS NOT NULL""",
    """UPDATE token_market_cap_peaks
       SET peak_market_cap_at = COALESCE(
           peak_market_cap_at,
           (
               SELECT MIN(tps.captured_at)
               FROM token_price_snapshots tps
               WHERE tps.mint = token_market_cap_peaks.mint
                 AND tps.market_cap >= token_market_cap_peaks.peak_market_cap
                 AND tps.market_cap > 0
           ),
           (
               SELECT CASE
                   WHEN CAST(ta.created_at AS REAL) > 1000000000 THEN CAST(ta.created_at AS INTEGER)
                   ELSE CAST(strftime('%s', ta.created_at) AS INTEGER)
               END
               FROM token_analysis ta
               WHERE ta.mint = token_market_cap_peaks.mint
           ),
           raw_peak_mc_at
       )
       WHERE peak_market_cap > 0
         AND (peak_market_cap_at IS NULL OR peak_market_cap_at = 0)""",
    """UPDATE token_analysis
       SET market_cap_highest_at_ts = COALESCE(
           market_cap_highest_at_ts,
           (
               SELECT tmp.peak_market_cap_at
               FROM token_market_cap_peaks tmp
               WHERE tmp.mint = token_analysis.mint
           ),
           CASE
               WHEN CAST(created_at AS REAL) > 1000000000 THEN CAST(created_at AS INTEGER)
               ELSE CAST(strftime('%s', created_at) AS INTEGER)
           END
       )
       WHERE market_cap_highest IS NOT NULL
         AND market_cap_highest > 0
         AND (market_cap_highest_at_ts IS NULL OR market_cap_highest_at_ts = 0)""",
    """UPDATE token_analysis
       SET market_cap_highest_at = datetime(market_cap_highest_at_ts, 'unixepoch') || 'Z'
       WHERE market_cap_highest IS NOT NULL
         AND market_cap_highest > 0
         AND market_cap_highest_at_ts IS NOT NULL
         AND market_cap_highest_at_ts > 0
         AND (market_cap_highest_at IS NULL OR market_cap_highest_at = '')""",
    """CREATE TABLE IF NOT EXISTS pumpfun_migration_verification (
           mint TEXT PRIMARY KEY,
           migrated_at INTEGER,
           migration_tx TEXT,
           dex TEXT,
           pumpswap_pool_address TEXT,
           pre_is_about_to_migrate INTEGER DEFAULT 0,
           pre_migration_band TEXT,
           pre_migration_progress_pct REAL,
           pre_migration_signal_updated_at INTEGER,
           pre_market_cap_current REAL,
           pre_market_cap_updated_at INTEGER,
           pre_buys_10s INTEGER DEFAULT 0,
           pre_unique_30s INTEGER DEFAULT 0,
           pre_sol_15s REAL DEFAULT 0,
           pre_inflow_accel REAL DEFAULT 0,
           pre_signal_score INTEGER DEFAULT 0,
           pre_migration_signal_source TEXT,
           predicted_by_flow INTEGER DEFAULT 0,
           predicted_by_market_cap INTEGER DEFAULT 0,
           predicted_by_explicit_signal INTEGER DEFAULT 0,
           was_about_to_migrate_at_migration INTEGER DEFAULT 0,
           was_hot_or_warm_before_migration INTEGER DEFAULT 0,
           signal_age_seconds INTEGER,
           signal_was_fresh INTEGER DEFAULT 0,
           final_verdict TEXT,
           created_at INTEGER
       )""",
    "ALTER TABLE wt_sub_provisioners ADD COLUMN token_mint TEXT",
    "ALTER TABLE wt_sub_provisioners ADD COLUMN token_symbol TEXT",
    "ALTER TABLE wt_sub_provisioners ADD COLUMN traded_amount REAL",
    "ALTER TABLE wt_sub_provisioners ADD COLUMN last_trade_tx TEXT",
    "ALTER TABLE wt_sub_provisioners ADD COLUMN last_trade_at INTEGER",
    "ALTER TABLE watchtower_infra_events ADD COLUMN token_mint TEXT",
    "ALTER TABLE watchtower_infra_events ADD COLUMN token_symbol TEXT",
    "ALTER TABLE watchtower_infra_events ADD COLUMN traded_amount REAL",
]

# Fast-exit sentinel: if these two columns already exist, all migrations have run.
_SENTINEL_COLS = {"pf_ws_creator", "creator_mismatch"}


def ensure_schema(db_path: str = None) -> None:
    """Apply pending schema migrations to flex_complete_database.db.

    Idempotent. Fast-exits if all migrations already applied.
    Silently skips duplicate-column errors (ALTER TABLE on already-present columns).
    """
    path = db_path or DB_PATH

    try:
        check = db_connect(path, timeout=5)
        cols = {row[1] for row in check.execute("PRAGMA table_info(token_analysis)")}
        check.close()
        if _SENTINEL_COLS.issubset(cols):
            print("[SCHEMA] All migrations already applied — skipping", flush=True)
            return
    except Exception as e:
        print(f"[SCHEMA] Could not check columns, will attempt migrations: {e}", flush=True)

    conn = db_connect(path, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    for sql in _MIGRATIONS:
        try:
            conn.execute(sql)
            conn.commit()
        except Exception as e:
            if "duplicate column" not in str(e).lower():
                print(f"[SCHEMA] note: {e}", flush=True)
    conn.close()
