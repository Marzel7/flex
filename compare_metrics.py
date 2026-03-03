#!/usr/bin/env python3
"""
Compare Local RPC Metrics vs Helius Actual Charges
Shows discrepancies and helps identify unattributed calls.
"""

import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = "flex_complete_database.db"

def get_db_connection():
    """Create database connection"""
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn

def get_local_metrics(conn, hours=24):
    """Get local RPC metrics from database"""
    cursor = conn.cursor()
    cutoff = datetime.now() - timedelta(hours=hours)

    cursor.execute("""
        SELECT
            source_file,
            method,
            COUNT(*) as calls,
            SUM(CASE WHEN status_code = 200 THEN credits ELSE 0 END) as successful_credits,
            SUM(CASE WHEN status_code != 200 THEN credits ELSE 0 END) as failed_credits,
            SUM(credits) as total_credits,
            COUNT(CASE WHEN status_code = 200 THEN 1 END) as successful_calls,
            COUNT(CASE WHEN status_code != 200 THEN 1 END) as failed_calls
        FROM rpc_metrics
        WHERE recorded_at > ?
        GROUP BY source_file, method
        ORDER BY total_credits DESC
    """, (cutoff.isoformat(),))

    return cursor.fetchall()

def print_comparison(rows):
    """Print comparison in readable format"""
    print("=" * 140)
    print(f"{'SOURCE':<30} {'METHOD':<40} {'CALLS':<8} {'SUCCESS':<10} {'FAILED':<10} {'TOTAL $':<10}")
    print("-" * 140)

    total_successful = 0
    total_failed = 0
    total_credits = 0

    for row in rows:
        source = (row['source_file'] or 'unknown')[:28]
        method = row['method'][:38]
        calls = str(row['calls'])
        success = f"{row['successful_calls']} ({row['successful_credits']}c)"
        failed = f"{row['failed_calls']} ({row['failed_credits']}c)"
        total = str(row['total_credits'])

        print(f"{source:<30} {method:<40} {calls:<8} {success:<10} {failed:<10} {total:<10}")

        total_successful += row['successful_credits']
        total_failed += row['failed_credits']
        total_credits += row['total_credits']

    print("-" * 140)
    print(f"{'TOTAL':<30} {'':<40} {'':<8} {'successful: ' + str(total_successful) + 'c':<10} {'failed: ' + str(total_failed) + 'c':<10} {str(total_credits):<10}")
    print()

    return total_successful, total_failed, total_credits

def get_by_source(conn, hours=24):
    """Get metrics by source file only"""
    cursor = conn.cursor()
    cutoff = datetime.now() - timedelta(hours=hours)

    cursor.execute("""
        SELECT
            source_file,
            COUNT(*) as calls,
            SUM(CASE WHEN status_code = 200 THEN 1 ELSE 0 END) as successful_calls,
            SUM(CASE WHEN status_code = 200 THEN credits ELSE 0 END) as successful_credits,
            SUM(credits) as total_credits
        FROM rpc_metrics
        WHERE recorded_at > ?
        GROUP BY source_file
        ORDER BY total_credits DESC
    """, (cutoff.isoformat(),))

    return cursor.fetchall()

def print_by_source(rows):
    """Print by source file"""
    print("\n" + "=" * 100)
    print("RPC CREDITS BY SOURCE FILE")
    print("=" * 100)
    print(f"{'SOURCE':<30} {'CALLS':<10} {'SUCCESSFUL':<15} {'TOTAL CREDITS':<15}")
    print("-" * 100)

    for row in rows:
        source = (row['source_file'] or 'unknown')[:28]
        calls = str(row['calls'])
        success = f"{row['successful_calls']} calls ({row['successful_credits']}c)"
        total = str(row['total_credits'])

        print(f"{source:<30} {calls:<10} {success:<15} {total:<15}")

    print()

def get_unknown_sources(conn, hours=24):
    """Find calls with unknown source_file"""
    cursor = conn.cursor()
    cutoff = datetime.now() - timedelta(hours=hours)

    cursor.execute("""
        SELECT
            method,
            COUNT(*) as calls,
            SUM(credits) as total_credits,
            MIN(recorded_at) as first_seen,
            MAX(recorded_at) as last_seen
        FROM rpc_metrics
        WHERE (source_file IS NULL OR source_file = 'unknown')
          AND recorded_at > ?
        GROUP BY method
        ORDER BY total_credits DESC
    """, (cutoff.isoformat(),))

    return cursor.fetchall()

def print_unknown_sources(rows):
    """Print unknown sources"""
    if not rows:
        print("\n✅ No unknown sources found!\n")
        return

    print("\n" + "=" * 120)
    print("⚠️  UNKNOWN SOURCE DETECTION")
    print("=" * 120)
    print(f"{'METHOD':<40} {'CALLS':<10} {'CREDITS':<12} {'FIRST SEEN':<20} {'LAST SEEN':<20}")
    print("-" * 120)

    for row in rows:
        method = row['method'][:38]
        calls = str(row['calls'])
        credits = str(row['total_credits'])
        first = row['first_seen'][-8:] if row['first_seen'] else 'N/A'
        last = row['last_seen'][-8:] if row['last_seen'] else 'N/A'

        print(f"{method:<40} {calls:<10} {credits:<12} {first:<20} {last:<20}")

    print()
    print("⚠️  These calls are not properly attributed to a source file.")
    print("    Check: creator_outgoing_extractor.py, main.py, or other modules making RPC calls.")
    print()

def main():
    try:
        conn = get_db_connection()

        # Prompt for hours
        print("\n" + "=" * 100)
        print("RPC METRICS COMPARISON TOOL")
        print("=" * 100)

        hours_str = input("How many hours to analyze? (default 24): ").strip()
        try:
            hours = int(hours_str) if hours_str else 24
        except ValueError:
            hours = 24

        print(f"\n📊 Analyzing last {hours} hours of RPC activity...\n")

        # Get and display detailed metrics
        rows = get_local_metrics(conn, hours=hours)
        total_success, total_failed, total_credits = print_comparison(rows)

        # By source
        source_rows = get_by_source(conn, hours=hours)
        print_by_source(source_rows)

        # Check for unknown sources
        unknown_rows = get_unknown_sources(conn, hours=hours)
        print_unknown_sources(unknown_rows)

        # Summary
        print("=" * 100)
        print("SUMMARY")
        print("=" * 100)
        print(f"Total RPC Calls: {len(rows)}")
        print(f"Successful Credits: {total_success}")
        print(f"Failed Credits: {total_failed}")
        print(f"Total Credits (all): {total_credits}")
        print()
        print("💡 Tips:")
        print("  - 'successful_credits' should match Helius actual billing")
        print("  - 'failed_credits' are from non-200 responses (retries, errors)")
        print("  - Unknown sources indicate code not properly attributing RPC calls")
        print("=" * 100 + "\n")

        conn.close()

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
