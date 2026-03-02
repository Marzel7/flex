"""
Reconciliation schema setup for Helius CLI vs FLEX internal metrics.
Handles SQLite table creation and migrations.
"""

import sqlite3
from pathlib import Path
from typing import Optional

DB_PATH = "flex_complete_database.db"


def _connect(timeout: int = 30) -> sqlite3.Connection:
    """Create database connection with optimal settings."""
    conn = sqlite3.connect(DB_PATH, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_reconciliation_schema():
    """Initialize or migrate reconciliation tables."""
    conn = _connect()
    cursor = conn.cursor()

    # Check if helius_usage_snapshots needs migration
    cursor.execute("PRAGMA table_info(helius_usage_snapshots)")
    helius_cols = {row[1] for row in cursor.fetchall()}

    # Add missing columns to existing helius_usage_snapshots if needed
    if "ts_utc" not in helius_cols:
        try:
            cursor.execute(
                "ALTER TABLE helius_usage_snapshots ADD COLUMN ts_utc TEXT"
            )
            print("[RECONCILIATION] Added ts_utc column to helius_usage_snapshots")
        except:
            pass

    if "billing_cycle_start_utc" not in helius_cols:
        try:
            cursor.execute(
                "ALTER TABLE helius_usage_snapshots ADD COLUMN billing_cycle_start_utc TEXT"
            )
            cursor.execute(
                "ALTER TABLE helius_usage_snapshots ADD COLUMN billing_cycle_end_utc TEXT"
            )
            print("[RECONCILIATION] Added billing_cycle columns to helius_usage_snapshots")
        except:
            pass

    if "total_credits_used" not in helius_cols:
        try:
            cursor.execute(
                "ALTER TABLE helius_usage_snapshots ADD COLUMN total_credits_used INTEGER"
            )
            print("[RECONCILIATION] Added total_credits_used column to helius_usage_snapshots")
        except:
            pass

    conn.commit()

    # FLEX internal metrics snapshots
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS internal_usage_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc TEXT NOT NULL,
            credits_all_attempts INTEGER,
            credits_success_only INTEGER,
            requests_total INTEGER,
            requests_429 INTEGER,
            errors_total INTEGER,
            burn_rate_per_minute REAL,
            raw_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(ts_utc)
        )
    """)

    # Reconciliation results (deltas and analysis)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usage_reconciliation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc TEXT NOT NULL,
            window_seconds INTEGER,
            cli_delta INTEGER,
            internal_delta INTEGER,
            delta_diff INTEGER,
            diff_pct REAL,
            is_break INTEGER DEFAULT 0,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(ts_utc)
        )
    """)

    conn.commit()

    # Index for performance
    try:
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_helius_snapshots_ts ON helius_usage_snapshots(ts_utc)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_internal_snapshots_ts ON internal_usage_snapshots(ts_utc)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_reconciliation_ts ON usage_reconciliation(ts_utc)"
        )
        conn.commit()
    except Exception as e:
        print(f"[RECONCILIATION] ⚠️ Index creation warning: {e}", flush=True)

    conn.close()
    print("[RECONCILIATION] ✅ Schema initialized", flush=True)


if __name__ == "__main__":
    init_reconciliation_schema()
