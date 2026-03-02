#!/usr/bin/env python3
"""
Creator Outgoing Scan State Management

Per-creator scan state with tiered cadence and change detection.
Scales cost with new activity, not total creators.

Schema:
  creator_scan_state(creator_address, before_signature, last_head_signature,
                     last_scan_time, next_scan_time, tier, updated_at)

Tiered cadence:
  hot (<=24h):    every 2 hours,   MAX_PAGES_PER_CYCLE=2
  warm (1-7d):    every 12 hours,  MAX_PAGES_PER_CYCLE=1
  cold (>7d):     every 7 days,    MAX_PAGES_PER_CYCLE=1

Change detection:
  - fetch 1 page
  - if first signature == last_head_signature -> stop (no new work)
  - else proceed up to page cap

Budget gate:
  - OUTGOING_DAILY_BUDGET_CREDITS (nullable)
  - each RPC call costs 10 credits
  - stop when daily budget exhausted
"""

import os
import sqlite3
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from db_global_lock import db_write_lock_global

DB_PATH = os.getenv("DB_PATH", "flex_complete_database.db")

# Budget configuration (nullable; None = unlimited)
OUTGOING_DAILY_BUDGET_CREDITS = os.getenv("OUTGOING_DAILY_BUDGET_CREDITS", None)
if OUTGOING_DAILY_BUDGET_CREDITS:
    OUTGOING_DAILY_BUDGET_CREDITS = int(OUTGOING_DAILY_BUDGET_CREDITS)


def _connect():
    """Create connection with optimal PRAGMA settings"""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def migrate_creator_scan_state():
    """Create creator_scan_state table and migrate existing data"""
    with db_write_lock_global():
        conn = _connect()
        cur = conn.cursor()
        try:
            # Create new table
            cur.execute("""
            CREATE TABLE IF NOT EXISTS creator_scan_state (
              creator_address TEXT PRIMARY KEY,
              before_signature TEXT,
              last_head_signature TEXT,
              last_scan_time TIMESTAMP,
              next_scan_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
              tier TEXT DEFAULT 'warm',
              updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """)

            # Create indexes
            cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_css_tier ON creator_scan_state(tier)
            """)
            cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_css_next_scan ON creator_scan_state(next_scan_time)
            """)

            # Migrate data from creator_sig_cursors if it exists
            try:
                cur.execute("""
                INSERT OR IGNORE INTO creator_scan_state(
                  creator_address,
                  last_head_signature,
                  last_scan_time,
                  updated_at
                )
                SELECT
                  creator_address,
                  last_signature,
                  updated_at,
                  CURRENT_TIMESTAMP
                FROM creator_sig_cursors
                WHERE creator_address NOT IN (
                  SELECT creator_address FROM creator_scan_state
                )
                """)
            except sqlite3.OperationalError:
                # creator_sig_cursors may not exist yet
                pass

            conn.commit()
        finally:
            conn.close()


def get_tier(last_scan_time: Optional[datetime]) -> str:
    """Determine tier based on last scan time"""
    if last_scan_time is None:
        return "hot"  # New creators are hot priority

    now = datetime.utcnow()
    age = now - last_scan_time

    if age <= timedelta(hours=24):
        return "hot"
    elif age <= timedelta(days=7):
        return "warm"
    else:
        return "cold"


def update_creator_tier(creator_address: str):
    """Update tier for creator based on last_scan_time"""
    with db_write_lock_global():
        conn = _connect()
        cur = conn.cursor()
        try:
            # Get current last_scan_time
            cur.execute(
                "SELECT last_scan_time FROM creator_scan_state WHERE creator_address = ?",
                (creator_address,),
            )
            row = cur.fetchone()
            if not row:
                return

            last_scan_time = None
            if row["last_scan_time"]:
                last_scan_time = datetime.fromisoformat(row["last_scan_time"])

            new_tier = get_tier(last_scan_time)

            # Update tier
            cur.execute(
                """
            UPDATE creator_scan_state
            SET tier = ?, updated_at = CURRENT_TIMESTAMP
            WHERE creator_address = ?
            """,
                (new_tier, creator_address),
            )
            conn.commit()
        finally:
            conn.close()


def ensure_creator_in_state(creator_address: str):
    """Insert creator into scan state if not exists (with 'warm' tier)"""
    with db_write_lock_global():
        conn = _connect()
        cur = conn.cursor()
        try:
            cur.execute(
                """
            INSERT OR IGNORE INTO creator_scan_state(
              creator_address, tier, next_scan_time, updated_at
            )
            VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
                (creator_address, "warm"),
            )
            conn.commit()
        finally:
            conn.close()


def get_due_creators(limit: int = 100) -> List[Tuple[str, str, int]]:
    """
    Get creators due for scanning (next_scan_time <= now), ordered by:
    1. hot tier first
    2. oldest next_scan_time
    3. limit results

    Returns list of (creator_address, tier, max_pages_per_cycle)
    """
    with db_write_lock_global():
        conn = _connect()
        cur = conn.cursor()
        try:
            cur.execute(
                """
            SELECT
              creator_address,
              tier,
              CASE
                WHEN tier = 'hot' THEN 2
                WHEN tier = 'warm' THEN 1
                WHEN tier = 'cold' THEN 1
                ELSE 1
              END AS max_pages
            FROM creator_scan_state
            WHERE next_scan_time <= datetime('now')
            ORDER BY
              CASE WHEN tier = 'hot' THEN 0 ELSE 1 END,
              next_scan_time ASC
            LIMIT ?
            """,
                (limit,),
            )
            results = [
                (row["creator_address"], row["tier"], row["max_pages"]) for row in cur.fetchall()
            ]
            return results
        finally:
            conn.close()


def load_creator_state(creator_address: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Load before_signature and last_head_signature for creator

    Returns (before_signature, last_head_signature) or (None, None)
    """
    with db_write_lock_global():
        conn = _connect()
        cur = conn.cursor()
        try:
            cur.execute(
                """
            SELECT before_signature, last_head_signature
            FROM creator_scan_state
            WHERE creator_address = ?
            """,
                (creator_address,),
            )
            row = cur.fetchone()
            if not row:
                return None, None
            return row["before_signature"], row["last_head_signature"]
        finally:
            conn.close()


def update_creator_state_after_scan(
    creator_address: str,
    before_signature: Optional[str],
    head_signature: Optional[str],
    credits_used: int,
):
    """
    Update creator state after successful scan

    Parameters:
    - creator_address: The creator
    - before_signature: Pagination cursor for next scan
    - head_signature: Newest signature found in this scan
    - credits_used: Credits consumed (for budget tracking)
    """
    with db_write_lock_global():
        conn = _connect()
        cur = conn.cursor()
        try:
            # Calculate next scan time based on tier
            cur.execute(
                "SELECT tier FROM creator_scan_state WHERE creator_address = ?",
                (creator_address,),
            )
            row = cur.fetchone()
            tier = row["tier"] if row else "warm"

            now = datetime.utcnow()
            if tier == "hot":
                next_scan = now + timedelta(hours=2)
            elif tier == "warm":
                next_scan = now + timedelta(hours=12)
            else:  # cold
                next_scan = now + timedelta(days=7)

            # Update state
            cur.execute(
                """
            UPDATE creator_scan_state
            SET
              before_signature = ?,
              last_head_signature = ?,
              last_scan_time = CURRENT_TIMESTAMP,
              next_scan_time = ?,
              updated_at = CURRENT_TIMESTAMP
            WHERE creator_address = ?
            """,
                (before_signature, head_signature, next_scan.isoformat(), creator_address),
            )

            # Update tier based on new last_scan_time
            new_tier = get_tier(now)
            cur.execute(
                """
            UPDATE creator_scan_state
            SET tier = ?
            WHERE creator_address = ?
            """,
                (new_tier, creator_address),
            )

            conn.commit()
        finally:
            conn.close()


def get_daily_credits_used() -> int:
    """Get total credits used today (from RPC metrics)"""
    try:
        from rpc_metrics_recorder import get_recorder

        recorder = get_recorder()
        summary = recorder.get_summary()
        return summary.get("credits_instrumented_today", 0)
    except (ImportError, Exception):
        return 0


def check_daily_budget() -> bool:
    """
    Check if daily budget allows more scanning

    Returns True if budget allows (or unlimited), False if exhausted
    """
    if OUTGOING_DAILY_BUDGET_CREDITS is None:
        return True  # Unlimited

    used = get_daily_credits_used()
    remaining = OUTGOING_DAILY_BUDGET_CREDITS - used

    return remaining > 0


def get_daily_budget_status() -> dict:
    """Get daily budget status"""
    if OUTGOING_DAILY_BUDGET_CREDITS is None:
        return {"budget": None, "used": 0, "remaining": None, "status": "unlimited"}

    used = get_daily_credits_used()
    remaining = max(0, OUTGOING_DAILY_BUDGET_CREDITS - used)
    percent_used = (used / OUTGOING_DAILY_BUDGET_CREDITS * 100) if OUTGOING_DAILY_BUDGET_CREDITS > 0 else 0

    return {
        "budget": OUTGOING_DAILY_BUDGET_CREDITS,
        "used": used,
        "remaining": remaining,
        "percent_used": round(percent_used, 1),
        "status": "ok" if remaining > 0 else "exhausted",
    }
