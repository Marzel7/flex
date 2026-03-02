#!/usr/bin/env python3
"""
Helius Usage CLI - API Key-based Alternative to helius-cli

Since the official helius-cli requires the project owner's keypair,
this provides a CLI-like interface using your API key.

Usage:
    python helius_usage_cli.py usage              # Get usage (manual/cached)
    python helius_usage_cli.py check              # Validate API key
    python helius_usage_cli.py update REMAINING USED MONTH  # Record usage
    python helius_usage_cli.py history            # Show snapshots
    python helius_usage_cli.py dashboard          # Open dashboard

Examples:
    python helius_usage_cli.py check
    python helius_usage_cli.py update 975318 24682 24682
    python helius_usage_cli.py usage
    python helius_usage_cli.py history --limit 5
    python helius_usage_cli.py dashboard
"""

import os
import sys
import json
import requests
import sqlite3
import webbrowser
from datetime import datetime
from typing import Optional, Dict
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DB_PATH = os.getenv("DB_PATH", "flex_complete_database.db")
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "").strip()
HELIUS_PROJECT_ID = os.getenv("HELIUS_PROJECT_ID", "").strip()


def _connect():
    """Create database connection"""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def ensure_table():
    """Create helius_usage_snapshots table"""
    conn = _connect()
    cur = conn.cursor()
    try:
        cur.execute("""
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
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_helius_usage_ts ON helius_usage_snapshots(timestamp)")
        conn.commit()
    finally:
        conn.close()


def validate_api_key() -> bool:
    """Check if API key works by making a test RPC call"""
    try:
        rpc_url = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": ["11111111111111111111111111111111"]
        }
        resp = requests.post(rpc_url, json=payload, timeout=10)
        if resp.status_code == 200 and "error" not in resp.json():
            return True
    except:
        pass
    return False


def cmd_check():
    """Check API key validity"""
    if not HELIUS_API_KEY or not HELIUS_PROJECT_ID:
        print("❌ Missing HELIUS_API_KEY or HELIUS_PROJECT_ID in .env")
        return False

    print("[helius-usage] Checking API credentials...")
    if validate_api_key():
        print("✅ API key is valid")
        print(f"✅ Project ID: {HELIUS_PROJECT_ID}")
        return True
    else:
        print("❌ API key validation failed")
        return False


def cmd_usage():
    """Show latest usage"""
    ensure_table()
    conn = _connect()
    cur = conn.cursor()
    try:
        cur.execute("""
        SELECT timestamp, credits_remaining, credits_used, credits_used_month
        FROM helius_usage_snapshots
        ORDER BY timestamp DESC
        LIMIT 1
        """)
        row = cur.fetchone()
        if row:
            print(json.dumps({
                "timestamp": row["timestamp"],
                "creditsRemaining": row["credits_remaining"],
                "creditsUsed": row["credits_used"],
                "creditsUsedMonth": row["credits_used_month"],
                "projectId": HELIUS_PROJECT_ID
            }, indent=2))
        else:
            print("{}")
    finally:
        conn.close()


def cmd_update(remaining: int, used: int, used_month: int):
    """Record usage snapshot"""
    ensure_table()
    conn = _connect()
    cur = conn.cursor()
    try:
        cur.execute("""
        INSERT INTO helius_usage_snapshots(
          credits_remaining, credits_used, credits_used_month,
          project_id, raw_json, captured_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """, (remaining, used, used_month, HELIUS_PROJECT_ID,
              json.dumps({"remaining": remaining, "used": used, "used_month": used_month}),
              datetime.now().isoformat()))
        conn.commit()
        print(json.dumps({
            "status": "recorded",
            "creditsRemaining": remaining,
            "creditsUsed": used,
            "creditsUsedMonth": used_month
        }))
    finally:
        conn.close()


def cmd_history(limit: int = 20):
    """Show usage history"""
    ensure_table()
    conn = _connect()
    cur = conn.cursor()
    try:
        cur.execute("""
        SELECT timestamp, credits_remaining, credits_used, credits_used_month
        FROM helius_usage_snapshots
        ORDER BY timestamp DESC
        LIMIT ?
        """, (limit,))
        snapshots = []
        for row in cur.fetchall():
            snapshots.append({
                "timestamp": row["timestamp"],
                "creditsRemaining": row["credits_remaining"],
                "creditsUsed": row["credits_used"],
                "creditsUsedMonth": row["credits_used_month"]
            })
        print(json.dumps(snapshots, indent=2))
    finally:
        conn.close()


def cmd_dashboard():
    """Open dashboard in browser"""
    url = f"https://dashboard.helius.dev/rpcs?projectId={HELIUS_PROJECT_ID}"
    print(f"Opening: {url}")
    webbrowser.open(url)


def print_help():
    """Show help"""
    print(__doc__)


if __name__ == "__main__":
    ensure_table()

    if len(sys.argv) < 2:
        print_help()
        sys.exit(0)

    cmd = sys.argv[1].lower()

    if cmd == "check":
        sys.exit(0 if cmd_check() else 1)

    elif cmd == "usage":
        cmd_usage()

    elif cmd == "update":
        if len(sys.argv) < 5:
            print("Usage: helius_usage_cli.py update REMAINING USED MONTH")
            sys.exit(1)
        try:
            cmd_update(int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]))
        except ValueError:
            print("Error: arguments must be integers")
            sys.exit(1)

    elif cmd == "history":
        limit = 20
        if "--limit" in sys.argv:
            idx = sys.argv.index("--limit")
            if idx + 1 < len(sys.argv):
                limit = int(sys.argv[idx + 1])
        cmd_history(limit)

    elif cmd == "dashboard":
        cmd_dashboard()

    else:
        print(f"Unknown command: {cmd}")
        print_help()
        sys.exit(1)
