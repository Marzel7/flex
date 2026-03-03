#!/usr/bin/env python3
"""
Real-time RPC Metrics Monitor
Tracks live RPC activity from the database and saves snapshots to files.
Run alongside listener to monitor RPC call patterns in real-time.
"""

import sqlite3
import time
import json
from datetime import datetime, timedelta
from pathlib import Path
import sys

DB_PATH = "flex_complete_database.db"
MONITOR_DIR = Path("rpc_monitor_logs")
MONITOR_DIR.mkdir(exist_ok=True)

def get_db_connection():
    """Create database connection"""
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn

def get_metrics_snapshot(conn, since_timestamp=None):
    """Get current RPC metrics from database"""
    cursor = conn.cursor()

    if since_timestamp:
        cursor.execute("""
            SELECT
                method,
                source_file,
                COUNT(*) as calls,
                SUM(credits) as total_credits,
                MIN(recorded_at) as first_seen,
                MAX(recorded_at) as last_seen
            FROM rpc_metrics
            WHERE recorded_at > ?
            GROUP BY method, source_file
            ORDER BY total_credits DESC
        """, (since_timestamp,))
    else:
        cursor.execute("""
            SELECT
                method,
                source_file,
                COUNT(*) as calls,
                SUM(credits) as total_credits,
                MIN(recorded_at) as first_seen,
                MAX(recorded_at) as last_seen
            FROM rpc_metrics
            GROUP BY method, source_file
            ORDER BY total_credits DESC
        """)

    return cursor.fetchall()

def get_summary(conn):
    """Get overall summary"""
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            COUNT(*) as total_calls,
            SUM(credits) as total_credits,
            COUNT(DISTINCT source_file) as source_files,
            COUNT(DISTINCT method) as methods
        FROM rpc_metrics
    """)
    return cursor.fetchone()

def format_metrics(rows):
    """Format metrics for display"""
    lines = []
    lines.append("=" * 120)
    lines.append(f"{'METHOD':<40} {'SOURCE':<30} {'CALLS':<8} {'CREDITS':<10}")
    lines.append("-" * 120)

    for row in rows:
        method = row['method'][:38]
        source = (row['source_file'] or 'unknown')[:28]
        calls = str(row['calls'])
        credits = str(row['total_credits'])
        lines.append(f"{method:<40} {source:<30} {calls:<8} {credits:<10}")

    return "\n".join(lines)

def save_snapshot(snapshot_data, filename_prefix="snapshot"):
    """Save snapshot to file"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # JSON snapshot
    json_file = MONITOR_DIR / f"{filename_prefix}_{timestamp}.json"
    with open(json_file, 'w') as f:
        json.dump(snapshot_data, f, indent=2, default=str)

    # Text snapshot
    text_file = MONITOR_DIR / f"{filename_prefix}_{timestamp}.txt"
    with open(text_file, 'w') as f:
        f.write(snapshot_data['formatted'])

    return json_file, text_file

def monitor_loop(interval=10, duration=None):
    """
    Monitor RPC activity continuously

    Args:
        interval: How often to check database (seconds)
        duration: How long to run (minutes), None = forever
    """
    try:
        start_time = datetime.now()
        last_check = datetime.now() - timedelta(seconds=interval)

        print(f"🚀 RPC Metrics Monitor started")
        print(f"📁 Logs saved to: {MONITOR_DIR.absolute()}")
        print(f"⏱️  Checking every {interval} seconds")
        if duration:
            print(f"⏳ Running for {duration} minutes")
        print()

        iteration = 0
        while True:
            iteration += 1
            conn = get_db_connection()

            # Get snapshots
            all_metrics = get_metrics_snapshot(conn)
            summary = get_summary(conn)

            # Format output
            formatted = format_metrics(all_metrics)
            summary_text = f"""
Total Calls: {summary['total_calls']}
Total Credits: {summary['total_credits']}
Source Files: {summary['source_files']}
Unique Methods: {summary['methods']}

{formatted}
"""

            # Display
            print(f"\n[ITERATION {iteration}] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(summary_text)

            # Save snapshot every 5 iterations
            if iteration % 5 == 0:
                snapshot_data = {
                    'timestamp': datetime.now().isoformat(),
                    'iteration': iteration,
                    'summary': {
                        'total_calls': summary['total_calls'],
                        'total_credits': summary['total_credits'],
                        'source_files': summary['source_files'],
                        'unique_methods': summary['methods']
                    },
                    'metrics': [
                        {
                            'method': row['method'],
                            'source_file': row['source_file'],
                            'calls': row['calls'],
                            'total_credits': row['total_credits'],
                            'first_seen': row['first_seen'],
                            'last_seen': row['last_seen']
                        }
                        for row in all_metrics
                    ],
                    'formatted': summary_text
                }
                json_file, text_file = save_snapshot(snapshot_data, "rpc_snapshot")
                print(f"💾 Snapshot saved: {json_file.name}")

            conn.close()

            # Check duration
            if duration and (datetime.now() - start_time).total_seconds() > (duration * 60):
                print(f"\n⏹️  Monitor duration ({duration}m) reached. Saving final snapshot...")
                break

            time.sleep(interval)

    except KeyboardInterrupt:
        print("\n⏹️  Monitor stopped by user")
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Save final snapshot
        try:
            conn = get_db_connection()
            all_metrics = get_metrics_snapshot(conn)
            summary = get_summary(conn)
            formatted = format_metrics(all_metrics)

            snapshot_data = {
                'timestamp': datetime.now().isoformat(),
                'final': True,
                'summary': {
                    'total_calls': summary['total_calls'],
                    'total_credits': summary['total_credits'],
                    'source_files': summary['source_files'],
                    'unique_methods': summary['methods']
                },
                'metrics': [
                    {
                        'method': row['method'],
                        'source_file': row['source_file'],
                        'calls': row['calls'],
                        'total_credits': row['total_credits'],
                        'first_seen': row['first_seen'],
                        'last_seen': row['last_seen']
                    }
                    for row in all_metrics
                ],
                'formatted': formatted
            }
            save_snapshot(snapshot_data, "rpc_final")
            print("✅ Final snapshot saved")
            conn.close()
        except Exception as e:
            print(f"⚠️  Error saving final snapshot: {e}")

if __name__ == "__main__":
    duration = None
    interval = 10

    if len(sys.argv) > 1:
        try:
            duration = int(sys.argv[1])
            print(f"Using duration: {duration} minutes")
        except:
            pass

    if len(sys.argv) > 2:
        try:
            interval = int(sys.argv[2])
            print(f"Using interval: {interval} seconds")
        except:
            pass

    monitor_loop(interval=interval, duration=duration)
