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

def get_initial_state() -> Tuple[int, int]:
    """Get initial credits state from database"""
    conn = _connect()
    cur = conn.cursor()

    try:
        # Get last local metrics
        cur.execute("""
            SELECT credits_total FROM rpc_requests_summary
            WHERE timestamp = (SELECT MAX(timestamp) FROM rpc_requests_summary)
            LIMIT 1
        """)
        row = cur.fetchone()
        local_start = row[0] if row else 0
    except:
        local_start = 0

    try:
        # Get last Helius snapshot
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

┌─ COMPARISON ─────────────────────────────────────────────────────────┐
│ Absolute Difference:          {diff:,} credits                        │
│ Relative Difference:          {diff_pct:.1f}%                          │
│ Status:                        {status}                         │
│ Expected Range:               ±1-2% (clean)                           │
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

            local_total = summary.get('credits_total', 0)
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

    # Final summary
    summary, helius_snapshot = get_current_metrics()
    local_total = summary.get('credits_total', 0)
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

    print(f"\nDifference: {diff:,} credits ({diff_pct:.1f}%)")

    if diff_pct <= 2:
        print("Result: ✅ CLEAN (within acceptable range)")
    elif diff_pct <= 5:
        print("Result: ⚠️ MINOR DRIFT (acceptable but investigate)")
    else:
        print("Result: ❌ SIGNIFICANT DRIFT")
        print("        (Large diff expected if Helius reports account-wide usage)")

    print("\nDIAGNOSTICS:")
    print(f"  • Helius includes ALL account activity (listener, all extractors, etc.)")
    print(f"  • Local metrics only show ACTIVE processes during test")
    print(f"  • If you only ran getSignaturesForAddress, large diff is normal")
    print("\nIf credits are 10x off on getSignaturesForAddress (10 cr each):")
    print("  → Check if batch transaction calls are being double-recorded")
    print("\nIf credits match for simple RPC methods:")
    print("  → Batch transaction fix is working correctly ✅")
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
