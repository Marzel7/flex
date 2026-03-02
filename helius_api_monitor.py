#!/usr/bin/env python3
"""
Helius Account Monitor using API Key Authentication

Captures Helius usage snapshots using your API key + Project ID.
Stores credits_remaining and credits_used in SQLite for historical tracking.

Setup:
    Set environment variables:
    - HELIUS_API_KEY (already in .env)
    - HELIUS_PROJECT_ID (already in .env)

Usage:
    # Capture once
    python helius_api_monitor.py

    # Test mode
    python helius_api_monitor.py --test

    # Schedule (every 5 minutes)
    */5 * * * * cd /path/to/flex && python helius_api_monitor.py
"""

import os
import sys
import json
import requests
from datetime import datetime
from typing import Optional, Dict
import sqlite3

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DB_PATH = os.getenv("DB_PATH", "flex_complete_database.db")
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "").strip()
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


def validate_credentials():
    """Validate that API credentials are configured"""
    if not HELIUS_API_KEY:
        print("[HELIUS] ❌ HELIUS_API_KEY environment variable not set", flush=True)
        return False
    if not HELIUS_PROJECT_ID:
        print("[HELIUS] ❌ HELIUS_PROJECT_ID environment variable not set", flush=True)
        return False
    return True


def get_helius_usage_api() -> Optional[Dict]:
    """
    Get usage from Helius using direct RPC query to get account info.

    Since Helius doesn't expose a usage endpoint, we make an RPC call
    to validate the API key is working, then return placeholder data.

    In production, you would call the Helius dashboard API or check
    their documentation for the usage endpoint.

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
        if not validate_credentials():
            return None

        print("[HELIUS] 📊 Validating API credentials...", flush=True)

        # Test that the API key works by making a simple RPC call
        rpc_url = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"

        # Simple getBalance call to validate API key
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": ["11111111111111111111111111111111"]  # System program
        }

        resp = requests.post(rpc_url, json=payload, timeout=10)

        if resp.status_code != 200:
            print(
                f"[HELIUS] ❌ API error {resp.status_code}: {resp.text[:200]}",
                flush=True,
            )
            return None

        result = resp.json()

        # Check for RPC errors
        if "error" in result:
            print(
                f"[HELIUS] ❌ RPC error: {result['error'].get('message', 'Unknown')}",
                flush=True,
            )
            return None

        print(f"[HELIUS] ✅ API key validated", flush=True)

        # NOTE: Helius doesn't expose usage data via a public API.
        # You would need to:
        # 1. Check their dashboard: https://dashboard.helius.dev/rpcs?projectId={PROJECT_ID}
        # 2. Use their GraphQL API if available
        # 3. Scrape the dashboard (not recommended)
        #
        # For now, we return a marker that says "manual update needed"
        return {
            "credits_remaining": 0,  # Manual entry needed
            "credits_used": 0,       # Manual entry needed
            "credits_used_month": 0, # Manual entry needed
            "project_id": HELIUS_PROJECT_ID,
            "raw_json": json.dumps({"note": "API credentials valid, usage requires manual check"}),
            "timestamp": datetime.now().isoformat(),
            "is_placeholder": True
        }

    except requests.exceptions.Timeout:
        print("[HELIUS] ⏱️ API request timed out", flush=True)
        return None
    except requests.exceptions.ConnectionError:
        print("[HELIUS] ❌ Connection error - check internet", flush=True)
        return None
    except json.JSONDecodeError as e:
        print(f"[HELIUS] ⚠️ JSON parse error: {str(e)[:100]}", flush=True)
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
    print("[HELIUS] 📊 ACCOUNT STATUS", flush=True)
    print("=" * 80, flush=True)

    if usage.get("is_placeholder"):
        print("[HELIUS] ⚠️ API credentials validated, but usage data requires manual update", flush=True)
        print(f"Project ID:         {usage.get('project_id', 'unknown')}", flush=True)
        print(f"Dashboard URL:      https://dashboard.helius.dev/rpcs?projectId={usage.get('project_id')}", flush=True)
        print(f"Checked At:         {usage.get('timestamp', 'unknown')}", flush=True)
    else:
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

    # Check for test/mock mode
    test_mode = "--test" in sys.argv or "--mock" in sys.argv

    # Capture usage
    print("[HELIUS] 🌐 Helius Account Monitor", flush=True)

    if test_mode:
        # Mock mode for testing
        print("[HELIUS] 🧪 TEST MODE - Using mock data", flush=True)
        usage = {
            "credits_remaining": 975318,
            "credits_used": 24682,
            "credits_used_month": 24682,
            "project_id": "test-project",
            "raw_json": '{"creditsRemaining": 975318, "creditsUsed": 24682}',
            "timestamp": datetime.now().isoformat(),
        }
    else:
        usage = get_helius_usage_api()

    if usage:
        print_usage(usage)
        record_usage_snapshot(usage)
        print("[HELIUS] ✅ Done", flush=True)
    else:
        print("[HELIUS] ❌ Failed to validate credentials", flush=True)
        print("[HELIUS] 💡 Hint: Use --test flag for mock data", flush=True)
        sys.exit(1)
