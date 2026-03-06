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
import tempfile
from datetime import datetime
from typing import Optional, Dict
import sqlite3
from pathlib import Path

# Load .env file if it exists
try:
    from dotenv import load_dotenv
    env_file = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_file):
        load_dotenv(env_file)
except ImportError:
    pass

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


def _get_or_create_keypair_file() -> str:
    """
    Get keypair file path. If HELIUS_WALLET_KEYPAIR env var exists, create a temp file.
    Otherwise use SOLANA_KEYPAIR or default path.
    """
    # Check if we have the keypair from .env
    keypair_env = os.getenv("HELIUS_WALLET_KEYPAIR")
    if keypair_env:
        try:
            # Parse the JSON array from env
            keypair_array = json.loads(keypair_env)
            
            # Create temp file with restricted permissions
            temp_file = tempfile.NamedTemporaryFile(
                mode='w',
                suffix='.json',
                delete=False,
                prefix='helius_keypair_'
            )
            
            # Write keypair as JSON
            json.dump(keypair_array, temp_file)
            temp_file.close()
            
            # Restrict permissions to 600 (owner read/write only)
            os.chmod(temp_file.name, 0o600)
            
            print(f"[HELIUS] 🔐 Using keypair from HELIUS_WALLET_KEYPAIR env var", flush=True)
            return temp_file.name
        except (json.JSONDecodeError, Exception) as e:
            print(f"[HELIUS] ⚠️ Failed to parse HELIUS_WALLET_KEYPAIR: {str(e)[:100]}", flush=True)
    
    # Fall back to SOLANA_KEYPAIR or default
    return KEYPAIR_PATH


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
          prepaid_credits_used INTEGER,
          overage_credits_used INTEGER,
          overage_cost REAL,
          webhook_usage INTEGER,
          api_usage INTEGER,
          rpc_usage INTEGER,
          rpc_gpa_usage INTEGER,
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

        # Migrate existing table if needed (add new columns if they don't exist)
        cur.execute("PRAGMA table_info(helius_usage_snapshots)")
        columns = {row[1] for row in cur.fetchall()}

        new_columns = ['prepaid_credits_used', 'overage_credits_used', 'overage_cost',
                       'webhook_usage', 'api_usage', 'rpc_usage', 'rpc_gpa_usage']
        for col in new_columns:
            if col not in columns:
                if col == 'overage_cost':
                    cur.execute(f"ALTER TABLE helius_usage_snapshots ADD COLUMN {col} REAL DEFAULT 0")
                else:
                    cur.execute(f"ALTER TABLE helius_usage_snapshots ADD COLUMN {col} INTEGER DEFAULT 0")
                print(f"[HELIUS] ✅ Added column {col}", flush=True)

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
    temp_keypair = None
    try:
        print("[HELIUS] 📊 Capturing usage via CLI...", flush=True)

        # Get or create keypair file
        keypair_path = _get_or_create_keypair_file()
        if keypair_path != KEYPAIR_PATH:
            temp_keypair = keypair_path

        # Authenticate with keypair if not already done
        auth_result = subprocess.run(
            ["helius", "login", "--keypair", keypair_path, "--json"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if auth_result.returncode != 0:
            print(f"[HELIUS] ⚠️ Auth warning (may be already authenticated): {auth_result.stderr[:100]}", flush=True)

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

        # Extract credits usage details
        credits_usage = usage_data.get("creditsUsage", {})

        # Debug: log available fields
        print(f"[HELIUS] Available fields in creditsUsage: {list(credits_usage.keys())}", flush=True)

        # Extract fields (structure may vary, handle gracefully)
        # Try to find monthly credits - Helius may call it different things
        credits_used_month = (
            credits_usage.get("monthlyCreditsUsed", 0) or
            credits_usage.get("creditsUsedThisMonth", 0) or
            credits_usage.get("totalCreditsUsed", 0)
        )

        return {
            "credits_remaining": credits_usage.get("remainingCredits", 0),
            "credits_used": credits_usage.get("totalCreditsUsed", 0),
            "credits_used_month": credits_used_month,
            "prepaid_credits_used": credits_usage.get("prepaidCreditsUsed", 0),
            "overage_credits_used": credits_usage.get("overageCreditsUsed", 0),
            "overage_cost": credits_usage.get("overageCost", 0.0),
            "webhook_usage": credits_usage.get("webhookUsage", 0),
            "api_usage": credits_usage.get("apiUsage", 0),
            "rpc_usage": credits_usage.get("rpcUsage", 0),
            "rpc_gpa_usage": credits_usage.get("rpcGPAUsage", 0),
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
    finally:
        # Clean up temp keypair if created
        if temp_keypair and os.path.exists(temp_keypair):
            try:
                os.unlink(temp_keypair)
            except Exception:
                pass


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
          prepaid_credits_used,
          overage_credits_used,
          overage_cost,
          webhook_usage,
          api_usage,
          rpc_usage,
          rpc_gpa_usage,
          project_id,
          raw_json,
          captured_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                usage.get("credits_remaining", 0),
                usage.get("credits_used", 0),
                usage.get("credits_used_month", 0),
                usage.get("prepaid_credits_used", 0),
                usage.get("overage_credits_used", 0),
                usage.get("overage_cost", 0.0),
                usage.get("webhook_usage", 0),
                usage.get("api_usage", 0),
                usage.get("rpc_usage", 0),
                usage.get("rpc_gpa_usage", 0),
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


def sync_helius_to_config(usage: Dict):
    """Sync Helius usage data to rpc_metrics_config.py CURRENT_USAGE"""
    try:
        import re
        config_path = os.path.join(os.path.dirname(__file__), "rpc_metrics_config.py")
        
        if not os.path.exists(config_path):
            return
        
        # Read the config file
        with open(config_path, 'r') as f:
            config_content = f.read()
        
        # Extract values from Helius usage
        credits_used_today = usage.get("credits_used", 0)
        credits_remaining = usage.get("credits_remaining", 0)
        
        # Update the CURRENT_USAGE dict in config
        # Replace credits_used_today value
        config_content = re.sub(
            r'("credits_used_today":\s+)\d+',
            rf'\g<1>{credits_used_today}',
            config_content
        )
        
        # Replace credits_remaining value
        config_content = re.sub(
            r'("credits_remaining":\s+)\d+(_?)',
            rf'\g<1>{credits_remaining}\g<2>',
            config_content
        )
        
        # Update budget_start_date
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        config_content = re.sub(
            r'("budget_start_date":\s+")([\d-]+)(")',
            rf'\g<1>{today}\g<3>',
            config_content
        )
        
        # Write back
        with open(config_path, 'w') as f:
            f.write(config_content)
        
        print(f"[HELIUS] 🔄 Synced config: {credits_used_today} used, {credits_remaining} remaining", flush=True)
    except Exception as e:
        print(f"[HELIUS] ⚠️ Config sync error: {str(e)[:100]}", flush=True)


def print_usage(usage: Dict):
    """Pretty print usage data"""
    print("\n" + "=" * 80, flush=True)
    print("[HELIUS] 📊 ACCOUNT USAGE (from CLI)", flush=True)
    print("=" * 80, flush=True)
    print(f"Credits Remaining:    {usage.get('credits_remaining', 0):>12,}", flush=True)
    print(f"Credits Used:         {usage.get('credits_used', 0):>12,}", flush=True)
    print(f"Prepaid Used:         {usage.get('prepaid_credits_used', 0):>12,}", flush=True)
    print(f"Overage Used:         {usage.get('overage_credits_used', 0):>12,}", flush=True)
    print(f"Overage Cost:         ${usage.get('overage_cost', 0.0):>11.2f}", flush=True)
    print("-" * 80, flush=True)
    print(f"RPC Usage:            {usage.get('rpc_usage', 0):>12,}", flush=True)
    print(f"RPC GPA Usage:        {usage.get('rpc_gpa_usage', 0):>12,}", flush=True)
    print(f"API Usage:            {usage.get('api_usage', 0):>12,}", flush=True)
    print(f"Webhook Usage:        {usage.get('webhook_usage', 0):>12,}", flush=True)
    print("=" * 80, flush=True)
    print(f"Project ID:         {usage.get('project_id', 'unknown')}", flush=True)
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
          prepaid_credits_used,
          overage_credits_used,
          overage_cost,
          webhook_usage,
          api_usage,
          rpc_usage,
          rpc_gpa_usage,
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
          credits_used_month,
          prepaid_credits_used,
          overage_credits_used,
          overage_cost,
          webhook_usage,
          api_usage,
          rpc_usage,
          rpc_gpa_usage
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
    continuous_mode = "--continuous" in sys.argv or len(sys.argv) == 1  # Default to continuous

    # Capture usage
    print("[HELIUS] 🌐 Helius CLI Usage Monitor", flush=True)

    if continuous_mode:
        # Run continuously, capturing every 30 seconds
        import time
        print("[HELIUS] 🔄 Running in continuous mode (capture every 30s)", flush=True)
        try:
            while True:
                if test_mode:
                    # Mock mode for testing without CLI (for dev environments)
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
                    usage = get_helius_usage_cli()

                if usage:
                    print_usage(usage)
                    record_usage_snapshot(usage)
                    sync_helius_to_config(usage)
                    print("[HELIUS] ✅ Snapshot captured", flush=True)
                else:
                    print("[HELIUS] ⚠️ Failed to retrieve usage, retrying in 30s", flush=True)

                time.sleep(30)  # Wait 30 seconds before next capture
        except KeyboardInterrupt:
            print("[HELIUS] ⛔ Continuous monitor stopped", flush=True)
            sys.exit(0)
    else:
        # One-time capture
        if test_mode:
            # Mock mode for testing without CLI (for dev environments)
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
            usage = get_helius_usage_cli()

        if usage:
            print_usage(usage)
            record_usage_snapshot(usage)
            sync_helius_to_config(usage)
            print("[HELIUS] ✅ Done", flush=True)
        else:
            print("[HELIUS] ❌ Failed to retrieve usage", flush=True)
            print("[HELIUS] 💡 Hint: Use --test flag for mock data", flush=True)
            sys.exit(1)
