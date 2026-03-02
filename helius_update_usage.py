#!/usr/bin/env python3
"""
Helius Usage Manual Update Helper

Since Helius doesn't expose usage via API, you can periodically check your dashboard
and run this script to record the latest usage snapshot.

Usage:
    python helius_update_usage.py --remaining 975318 --used 24682 --month 24682

    or for interactive mode:
    python helius_update_usage.py
"""

import os
import sys
import json
import sqlite3
from datetime import datetime
from typing import Optional, Dict

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DB_PATH = os.getenv("DB_PATH", "flex_complete_database.db")
HELIUS_PROJECT_ID = os.getenv("HELIUS_PROJECT_ID", "").strip()


def _connect():
    """Create connection with optimal PRAGMA settings"""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def ensure_helius_usage_table():
    """Create helius_usage_snapshots table for tracking"""
    conn = _connect()
    cur = conn.cursor()
    try:
        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS helius_usage_snapshots (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          credits_remaining INTEGER,
          credits_used INTEGER,
          credits_used_month INTEGER,
          project_id TEXT,
          raw_json TEXT,
          captured_at TIMESTAMP
        )
        """
        )
        conn.commit()
    except Exception as e:
        print(f"⚠️ Table error: {str(e)[:100]}", flush=True)
    finally:
        conn.close()


def record_usage(remaining: int, used: int, used_month: int):
    """Store usage snapshot in database"""
    conn = _connect()
    cur = conn.cursor()
    try:
        cur.execute(
            """
        INSERT INTO helius_usage_snapshots(
          credits_remaining,
          credits_used,
          credits_used_month,
          project_id,
          raw_json,
          captured_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
            (
                remaining,
                used,
                used_month,
                HELIUS_PROJECT_ID,
                json.dumps({"remaining": remaining, "used": used, "used_month": used_month}),
                datetime.now().isoformat(),
            ),
        )
        conn.commit()
        print(f"✅ Recorded: Remaining={remaining:,} | Used={used:,} | Monthly={used_month:,}", flush=True)
        return True
    except Exception as e:
        print(f"❌ Recording error: {str(e)[:100]}", flush=True)
        return False
    finally:
        conn.close()


def interactive_mode():
    """Interactive mode to enter usage data"""
    print("\n" + "=" * 80)
    print("HELIUS USAGE MANUAL UPDATE")
    print("=" * 80)
    print("\nVisit: https://dashboard.helius.dev/rpcs?projectId=" + HELIUS_PROJECT_ID)
    print("\nEnter the values from your Helius dashboard:\n")

    try:
        remaining = int(input("Credits Remaining: ").strip())
        used = int(input("Credits Used (today/period): ").strip())
        used_month = int(input("Credits Used (this month): ").strip())

        record_usage(remaining, used, used_month)
    except ValueError:
        print("❌ Invalid input - numbers only")
        sys.exit(1)


def get_latest_snapshot() -> Optional[Dict]:
    """Get most recent usage snapshot"""
    conn = _connect()
    cur = conn.cursor()
    try:
        cur.execute(
            """
        SELECT
          credits_remaining,
          credits_used,
          credits_used_month,
          timestamp
        FROM helius_usage_snapshots
        ORDER BY timestamp DESC
        LIMIT 1
        """
        )
        row = cur.fetchone()
        if row:
            return dict(row)
        return None
    finally:
        conn.close()


def show_latest():
    """Show latest usage snapshot"""
    snapshot = get_latest_snapshot()
    if snapshot:
        print("\n" + "=" * 80)
        print("LATEST HELIUS USAGE SNAPSHOT")
        print("=" * 80)
        print(f"Recorded:           {snapshot['timestamp']}")
        print(f"Credits Remaining:  {snapshot['credits_remaining']:>12,}")
        print(f"Credits Used:       {snapshot['credits_used']:>12,}")
        print(f"Credits Monthly:    {snapshot['credits_used_month']:>12,}")
        print("=" * 80 + "\n")
    else:
        print("❌ No snapshots recorded yet")


if __name__ == "__main__":
    ensure_helius_usage_table()

    if len(sys.argv) > 1:
        # Command line mode
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--remaining", type=int, help="Credits remaining")
        parser.add_argument("--used", type=int, help="Credits used")
        parser.add_argument("--month", type=int, help="Credits used this month")
        parser.add_argument("--show", action="store_true", help="Show latest snapshot")

        args = parser.parse_args()

        if args.show:
            show_latest()
        elif args.remaining is not None and args.used is not None and args.month is not None:
            record_usage(args.remaining, args.used, args.month)
        else:
            parser.print_help()
    else:
        # Interactive mode
        interactive_mode()
        show_latest()
