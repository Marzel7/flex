#!/usr/bin/env python3
"""
Real-time RPC Comparison Monitor
Captures local instrumented credits vs Helius billing credits over a specified duration.
Displays live comparison metrics every 5 seconds.
"""

import time
import json
import requests
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
import sqlite3
import os

DB_PATH = "flex_complete_database.db"
API_URL = "http://localhost:8001/metrics/rpc"
HELIUS_API_URL = "http://localhost:8001/metrics/helius"

def _connect():
    """Create database connection"""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn

def trigger_helius_snapshot():
    """Trigger a fresh Helius CLI snapshot capture"""
    try:
        import subprocess
        subprocess.run(
            ["python", "helius_cli_monitor.py"],
            capture_output=True,
            timeout=15
        )
        time.sleep(2)  # Wait for snapshot to be recorded
    except Exception:
        pass  # Fail silently

def get_initial_state() -> Tuple[int, int]:
    """Get initial credits state from database"""
    # Capture fresh Helius snapshot before test
    print("📸 Capturing initial Helius snapshot...")
    trigger_helius_snapshot()

    conn = _connect()
    cur = conn.cursor()

    try:
        # Get last local metrics (from API which tracks real-time)
        resp = requests.get(API_URL, timeout=5)
        metrics = resp.json()
        local_start = metrics.get('summary', {}).get('credits_instrumented_today', 0)
    except:
        local_start = 0

    try:
        # Get latest Helius snapshot (just captured)
        cur.execute("""
            SELECT credits_used FROM helius_usage_snapshots
            WHERE timestamp IS NOT NULL
            ORDER BY timestamp DESC LIMIT 1
        """)
        row = cur.fetchone()
        helius_start = row[0] if row else 0
    except:
        helius_start = 0

    conn.close()
    return local_start, helius_start

def get_current_metrics() -> Tuple[Dict, Dict]:
    """Fetch current metrics from API"""
    try:
        resp = requests.get(API_URL, timeout=5)
        metrics = resp.json()
        summary = metrics.get('summary', {})
    except:
        summary = {}

    try:
        resp = requests.get(HELIUS_API_URL, timeout=5)
        helius_data = resp.json()
        # Get the latest snapshot from the database instead of from API
        # because the API returns a single snapshot, but we need to track deltas
        conn = _connect()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT credits_used FROM helius_usage_snapshots
                WHERE timestamp IS NOT NULL
                ORDER BY timestamp DESC LIMIT 1
            """)
            row = cur.fetchone()
            helius_snapshot = helius_data.get('helius_snapshot', {})
            if row:
                helius_snapshot['credits_used'] = row[0]
        except:
            helius_snapshot = helius_data.get('helius_snapshot', {})
        finally:
            conn.close()
    except:
        helius_snapshot = {}

    return summary, helius_snapshot

def format_comparison(
    elapsed_seconds: int,
    local_delta: int,
    helius_delta: int,
    local_total: int,
    helius_total: int
) -> str:
    """Format comparison display"""

    diff = abs(local_delta - helius_delta)
    diff_pct = (diff / max(helius_delta, 1) * 100) if helius_delta > 0 else 0

    # Status determination
    if diff_pct <= 2:
        status = "✅ CLEAN"
        status_color = "#10b981"
    elif diff_pct <= 5:
        status = "⚠️ MINOR"
        status_color = "#fbbf24"
    else:
        status = "❌ DRIFT"
        status_color = "#ef4444"

    output = f"""
╔════════════════════════════════════════════════════════════════════════╗
║              RPC COMPARISON MONITOR - LIVE METRICS                     ║
╚════════════════════════════════════════════════════════════════════════╝

⏱️  Elapsed Time: {elapsed_seconds:3d} seconds

┌─ LOCAL INSTRUMENTATION ──────────────────────────────────────────────┐
│ Credits Used (this session):  {local_delta:,} credits                │
│ Total Credits:                 {local_total:,} credits                │
│ Method: Per-request attribution                                       │
└──────────────────────────────────────────────────────────────────────┘

┌─ HELIUS BILLING ─────────────────────────────────────────────────────┐
│ Credits Used (this session):  {helius_delta:,} credits                │
│ Total Credits:                 {helius_total:,} credits                │
│ Method: Account-level billing                                         │
└──────────────────────────────────────────────────────────────────────┘

┌─ NOTE ───────────────────────────────────────────────────────────────┐
│ Local metrics ≠ Helius billing (different measurement methods)       │
│ Local: What we recorded in code (may miss some calls)               │
│ Helius: What Helius actually charged (authoritative)                │
│ Use Helius as source of truth for cost tracking.                    │
└──────────────────────────────────────────────────────────────────────┘

📊 Last Updated: {datetime.now().strftime('%H:%M:%S')}
"""

    return output.replace(f"{status}", f"\033[92m{status}\033[0m" if "CLEAN" in status
                         else f"\033[93m{status}\033[0m" if "MINOR" in status
                         else f"\033[91m{status}\033[0m")

def run_comparison_monitor(duration_seconds: int = 120, update_interval: int = 5):
    """
    Run real-time comparison monitor

    Args:
        duration_seconds: How long to run (default 120 = 2 minutes)
        update_interval: Update frequency in seconds (default 5)

    NOTE: This monitor compares LOCAL instrumented credits vs HELIUS billing.
    Local credits = only current active processes (e.g., creator_outgoing_scan)
    Helius credits = all API usage on your account (cumulative since billing period)
    """

    print("\n🚀 Starting RPC Comparison Monitor...")
    print(f"   Duration: {duration_seconds} seconds")
    print(f"   Update Interval: {update_interval} seconds")
    print(f"   Start Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # Get initial state
    local_start, helius_start = get_initial_state()
    print(f"📊 Initial State:")
    print(f"   Local Credits: {local_start:,}")
    print(f"   Helius Credits (cumulative): {helius_start:,}")
    print(f"\n⚠️  NOTE: Helius shows TOTAL account usage since billing period start.")
    print(f"   Only local processes currently active will be compared.\n")

    start_time = time.time()
    update_count = 0

    try:
        while True:
            elapsed = int(time.time() - start_time)

            if elapsed > duration_seconds:
                print("\n✅ Monitoring complete!")
                break

            # Fetch current metrics
            summary, helius_snapshot = get_current_metrics()

            # Use instrumented_today (what we actually recorded) not total (cumulative)
            local_total = summary.get('credits_instrumented_today', summary.get('credits_total', 0))
            helius_total = helius_snapshot.get('credits_used', 0)

            local_delta = max(0, local_total - local_start)
            helius_delta = max(0, helius_total - helius_start)

            # Display comparison
            output = format_comparison(
                elapsed,
                local_delta,
                helius_delta,
                local_total,
                helius_total
            )

            # Clear screen and print (works on Unix/Linux/Mac)
            os.system('clear' if os.name != 'nt' else 'cls')
            print(output)

            # Show remaining time
            remaining = duration_seconds - elapsed
            print(f"\n⏳ Time Remaining: {remaining} seconds")
            print("(Ctrl+C to stop early)")

            update_count += 1
            time.sleep(update_interval)

    except KeyboardInterrupt:
        print("\n\n⛔ Monitoring stopped by user")

    # Final summary - capture fresh Helius snapshot at end
    print("\n📸 Capturing final Helius snapshot...")
    trigger_helius_snapshot()

    summary, helius_snapshot = get_current_metrics()
    local_total = summary.get('credits_instrumented_today', 0)
    helius_total = helius_snapshot.get('credits_used', 0)
    local_delta = max(0, local_total - local_start)
    helius_delta = max(0, helius_total - helius_start)

    print("\n" + "="*75)
    print("FINAL COMPARISON SUMMARY")
    print("="*75)
    print(f"Duration: {elapsed} seconds ({elapsed/60:.1f} minutes)")
    print(f"Updates: {update_count}")
    print(f"\nLocal Instrumented Credits:  {local_delta:,} ({local_total:,} total)")
    print(f"Helius Account Credits:      {helius_delta:,} ({helius_total:,} total since period start)")

    diff = abs(local_delta - helius_delta)
    diff_pct = (diff / max(helius_delta, 1) * 100) if helius_delta > 0 else 0

    print(f"\n📊 HELIUS IS THE SOURCE OF TRUTH: {helius_delta:,} credits charged")
    print(f"Local metrics showed: {local_delta:,} credits (discrepancy: {diff_pct:.1f}%)")

    print("\n⚠️  IMPORTANT:")
    print("  Local metrics and Helius charges are fundamentally different:")
    print("  • Helius: Actual charges from their account-level billing")
    print("  • Local: Recorded RPC calls in instrumentation (may be incomplete)")
    print("")
    print("  Expected differences:")
    print("    - Retries and failed calls (Helius charges, local may not record)")
    print("    - WebSocket streaming (Helius charges by data, local charges by request)")
    print("    - API network overhead (Helius includes, local doesn't)")
    print("    - Background activity (Helius includes, local may miss)")
    print("")
    print("  Use HELIUS charges for cost tracking. Use LOCAL metrics for")
    print("  debugging which RPC methods are most expensive.")
    print("="*75 + "\n")

if __name__ == "__main__":
    import sys

    # Parse command line arguments
    duration = 120  # Default 2 minutes
    if len(sys.argv) > 1:
        try:
            duration = int(sys.argv[1])
        except ValueError:
            print(f"Usage: python rpc_comparison_monitor.py [duration_seconds]")
            print(f"Example: python rpc_comparison_monitor.py 120  (2 minutes)")
            sys.exit(1)

    run_comparison_monitor(duration_seconds=duration)
