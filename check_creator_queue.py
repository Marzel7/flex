#!/usr/bin/env python3
"""
Creator Queue Status Monitor

Shows:
- How many creators are in the queue
- Which ones need processing
- When they were last checked
- Their priority levels
"""

import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = "flex_complete_database.db"


def format_time_ago(unix_timestamp):
    """Convert unix timestamp to 'X ago' format"""
    if not unix_timestamp:
        return "Never"

    now = int(time.time())
    diff = now - unix_timestamp

    if diff < 60:
        return f"{diff}s ago"
    elif diff < 3600:
        return f"{diff // 60}m ago"
    elif diff < 86400:
        return f"{diff // 3600}h ago"
    else:
        return f"{diff // 86400}d ago"


def check_queue():
    """Display creator queue status"""

    if not Path(DB_PATH).exists():
        print("❌ Database not found. Run Flask app first.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    print("\n" + "="*80)
    print("CREATOR QUEUE STATUS")
    print("="*80 + "\n")

    # Overall statistics
    cursor.execute("""
        SELECT
            COUNT(*) as total,
            COUNT(CASE WHEN priority >= 80 THEN 1 END) as critical,
            COUNT(CASE WHEN priority >= 60 AND priority < 80 THEN 1 END) as elevated,
            COUNT(CASE WHEN priority >= 40 AND priority < 60 THEN 1 END) as moderate,
            COUNT(CASE WHEN priority < 40 THEN 1 END) as low,
            COUNT(CASE WHEN locked_until > strftime('%s', 'now') THEN 1 END) as processing,
            COUNT(CASE WHEN attempts = 0 THEN 1 END) as never_checked
        FROM work_queue
    """)

    row = cursor.fetchone()
    if row:
        total, critical, elevated, moderate, low, processing, never_checked = row

        print(f"📊 QUEUE OVERVIEW")
        print(f"  Total Creators:        {total:>6}")
        print(f"  Critical Priority:     {critical:>6}  (priority >= 80)")
        print(f"  Elevated Priority:     {elevated:>6}  (priority 60-79)")
        print(f"  Moderate Priority:     {moderate:>6}  (priority 40-59)")
        print(f"  Low Priority:          {low:>6}  (priority < 40)")
        print(f"  Currently Processing:  {processing:>6}")
        print(f"  Never Checked:         {never_checked:>6}")
        print()

    # Top priority creators ready for processing
    print("🔴 TOP 10 CRITICAL PRIORITY (Ready to Process)")
    print("-" * 80)

    cursor.execute("""
        SELECT
            SUBSTR(address, 1, 8) || '...' as addr,
            ROUND(priority, 1) as priority,
            reason,
            attempts,
            CASE WHEN locked_until > strftime('%s', 'now') THEN '🔒 LOCKED' ELSE '✅ READY' END as status
        FROM work_queue
        WHERE priority >= 80
        ORDER BY priority DESC
        LIMIT 10
    """)

    rows = cursor.fetchall()
    if rows:
        for addr, priority, reason, attempts, status in rows:
            print(f"  {addr:12} | Priority: {priority:>6} | {status:>9} | Checked: {attempts:>2}x | {reason}")
    else:
        print("  (no critical priority creators in queue)")

    print()

    # Recently processed creators
    print("✅ RECENTLY PROCESSED CREATORS (Last Check)")
    print("-" * 80)

    cursor.execute("""
        SELECT
            SUBSTR(work_queue.address, 1, 8) || '...' as addr,
            ROUND(work_queue.priority, 1) as priority,
            work_queue.attempts,
            address_activity.tx_1h,
            ROUND(address_activity.sol_in_1h + address_activity.sol_out_1h, 4) as volume,
            address_activity.last_processed_at
        FROM work_queue
        JOIN address_activity ON work_queue.address = address_activity.address
        WHERE work_queue.attempts > 0 AND address_activity.last_processed_at IS NOT NULL
        ORDER BY address_activity.last_processed_at DESC
        LIMIT 10
    """)

    rows = cursor.fetchall()
    if rows:
        for addr, priority, attempts, tx_1h, volume, last_processed in rows:
            time_ago = format_time_ago(last_processed)
            print(f"  {addr:12} | Priority: {priority:>6} | Checked: {attempts:>2}x | Tx/1h: {tx_1h:>3} | Volume: {volume:>8.4f} SOL | {time_ago:>10}")
    else:
        print("  (no creators have been processed yet)")

    print()

    # Currently locked (being processed)
    print("🔒 CURRENTLY PROCESSING")
    print("-" * 80)

    cursor.execute("""
        SELECT
            SUBSTR(address, 1, 8) || '...' as addr,
            ROUND(priority, 1) as priority,
            reason,
            CAST((locked_until - strftime('%s', 'now')) AS INTEGER) as seconds_left
        FROM work_queue
        WHERE locked_until > strftime('%s', 'now')
        ORDER BY locked_until DESC
    """)

    rows = cursor.fetchall()
    if rows:
        for addr, priority, reason, seconds_left in rows:
            print(f"  {addr:12} | Priority: {priority:>6} | {seconds_left:>3}s remaining | {reason}")
    else:
        print("  (no creators currently being processed)")

    print()

    # Queue statistics
    cursor.execute("""
        SELECT
            COUNT(*) as total,
            ROUND(AVG(priority), 1) as avg_priority,
            MIN(priority) as min_priority,
            MAX(priority) as max_priority,
            ROUND(AVG(attempts), 1) as avg_attempts
        FROM work_queue
    """)

    total, avg_priority, min_priority, max_priority, avg_attempts = cursor.fetchone()

    print("📈 QUEUE STATISTICS")
    print("-" * 80)
    if total > 0:
        print(f"  Total Creators:       {total}")
        print(f"  Average Priority:     {avg_priority:.1f} (range: {min_priority:.1f} - {max_priority:.1f})")
        print(f"  Average Times Checked: {avg_attempts:.1f}")
    else:
        print(f"  Queue is empty (no creators queued yet)")
        print(f"  Send webhooks or manually enqueue creators to get started")

    print()
    print("="*80)
    print(f"Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*80 + "\n")

    conn.close()


if __name__ == "__main__":
    check_queue()
