#!/usr/bin/env python3
"""
Helius CLI-based Account Monitor

Periodically captures Helius usage snapshots using the CLI tool.
Stores credits_remaining and credits_used in SQLite for historical tracking.

Usage:
    # One-time setup
    helius login --keypair ~/.config/solana/id.json --json

    # Capture once
    python helius_cli_monitor.py

    # Schedule (every 5 minutes)
    */5 * * * * cd /path/to/flex && python helius_cli_monitor.py
"""

import os
import sys
import json
import subprocess
from datetime import datetime
from typing import Optional, Dict
import sqlite3

DB_PATH = os.getenv("DB_PATH", "flex_complete_database.db")
KEYPAIR_PATH = os.getenv("SOLANA_KEYPAIR", os.path.expanduser("~/.config/solana/id.json"))


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
        # Create index for faster queries
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_helius_usage_ts ON helius_usage_snapshots(timestamp)"
        )
        conn.commit()
        print("[HELIUS] ✅ Usage table ready", flush=True)
    except Exception as e:
        print(f"[HELIUS] ⚠️ Table creation error: {str(e)[:100]}", flush=True)
    finally:
        conn.close()


def get_helius_usage_cli() -> Optional[Dict]:
    """
    Get usage from Helius CLI command: helius usage --json

    Returns:
    {
        "credits_remaining": int,
        "credits_used": int,
        "credits_used_month": int,
        "project_id": str,
        "raw_json": str
    }
    """
    try:
        print("[HELIUS] 📊 Capturing usage via CLI...", flush=True)

        # Run helius usage command
        result = subprocess.run(
            ["helius", "usage", "--json"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode != 0:
            print(f"[HELIUS] ❌ CLI error: {result.stderr[:200]}", flush=True)
            return None

        # Parse JSON output
        usage_data = json.loads(result.stdout)
        print(f"[HELIUS] ✅ Got usage data from CLI", flush=True)

        # Extract fields (structure may vary, handle gracefully)
        return {
            "credits_remaining": usage_data.get("creditsRemaining", 0),
            "credits_used": usage_data.get("creditsUsed", 0),
            "credits_used_month": usage_data.get("creditsUsedMonth", 0),
            "project_id": usage_data.get("projectId", ""),
            "raw_json": json.dumps(usage_data),
            "timestamp": datetime.now().isoformat(),
        }

    except subprocess.TimeoutExpired:
        print("[HELIUS] ⏱️ CLI command timed out", flush=True)
        return None
    except json.JSONDecodeError as e:
        print(f"[HELIUS] ⚠️ JSON parse error: {str(e)[:100]}", flush=True)
        return None
    except FileNotFoundError:
        print(
            "[HELIUS] ❌ helius-cli not found. Install with: npm install -g helius-cli",
            flush=True,
        )
        return None
    except Exception as e:
        print(f"[HELIUS] ❌ Error: {str(e)[:200]}", flush=True)
        return None


def record_usage_snapshot(usage: Dict):
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
                usage.get("credits_remaining", 0),
                usage.get("credits_used", 0),
                usage.get("credits_used_month", 0),
                usage.get("project_id", ""),
                usage.get("raw_json", ""),
                usage.get("timestamp"),
            ),
        )
        conn.commit()
        print("[HELIUS] 💾 Recorded snapshot in database", flush=True)
    except Exception as e:
        print(f"[HELIUS] ⚠️ Recording error: {str(e)[:100]}", flush=True)
    finally:
        conn.close()


def print_usage(usage: Dict):
    """Pretty print usage data"""
    print("\n" + "=" * 80, flush=True)
    print("[HELIUS] 📊 ACCOUNT USAGE (from CLI)", flush=True)
    print("=" * 80, flush=True)
    print(f"Credits Remaining:  {usage.get('credits_remaining', 0):>12,}", flush=True)
    print(f"Credits Used:       {usage.get('credits_used', 0):>12,}", flush=True)
    print(f"Credits Used Month: {usage.get('credits_used_month', 0):>12,}", flush=True)
    print(f"Project ID:         {usage.get('project_id', 'unknown'):>12}", flush=True)
    print(f"Captured At:        {usage.get('timestamp', 'unknown')}", flush=True)
    print("=" * 80 + "\n", flush=True)


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
          project_id,
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


def get_snapshot_history(limit: int = 20) -> list:
    """Get recent usage snapshots"""
    conn = _connect()
    cur = conn.cursor()
    try:
        cur.execute(
            """
        SELECT
          timestamp,
          credits_remaining,
          credits_used,
          credits_used_month
        FROM helius_usage_snapshots
        ORDER BY timestamp DESC
        LIMIT ?
        """,
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


if __name__ == "__main__":
    # Initialize
    ensure_helius_usage_table()

    # Capture usage
    print("[HELIUS] 🌐 Helius CLI Usage Monitor", flush=True)
    usage = get_helius_usage_cli()

    if usage:
        print_usage(usage)
        record_usage_snapshot(usage)
        print("[HELIUS] ✅ Done", flush=True)
    else:
        print("[HELIUS] ❌ Failed to retrieve usage", flush=True)
        sys.exit(1)
