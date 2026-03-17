#!/usr/bin/env python3
"""
Monitor vault revalidation status for the 40 pools marked for fixing.

This script tracks:
1. How many pools have been revalidated (status changed from 'pending' to 'validated')
2. How many revalidation attempts have been made
3. Any pools stuck in revalidation (too many attempts)
4. Current coverage by validation status
"""

import sqlite3
import sys
import argparse
from datetime import datetime, timedelta

DB_PATH = "database/flex_complete_database.db"
WSOL_MINT = "So11111111111111111111111111111111111111112"


def get_vault_status():
    """Get current vault validation status breakdown."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    status = cursor.execute("""
        SELECT
          vault_validation_status,
          COUNT(*) as count,
          SUM(CASE WHEN quote_account = ? THEN 1 ELSE 0 END) as still_broken
        FROM token_pool_accounts
        WHERE is_active = 1
        GROUP BY vault_validation_status
    """, (WSOL_MINT,)).fetchall()

    conn.close()
    return status


def show_revalidation_progress():
    """Show progress of revalidation efforts."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Pools marked for revalidation
    pending = cursor.execute("""
        SELECT COUNT(*) FROM token_pool_accounts
        WHERE is_active = 1
          AND vault_validation_status = 'pending'
          AND quote_account = ?
    """, (WSOL_MINT,)).fetchone()[0]

    # Pools that were fixed
    fixed = cursor.execute("""
        SELECT COUNT(*) FROM token_pool_accounts
        WHERE is_active = 1
          AND vault_validation_status = 'validated'
          AND quote_account != ?
    """, (WSOL_MINT,)).fetchone()[0]

    # Still broken (not yet fixed)
    still_broken = cursor.execute("""
        SELECT COUNT(*) FROM token_pool_accounts
        WHERE is_active = 1
          AND quote_account = ?
    """, (WSOL_MINT,)).fetchone()[0]

    print("\n" + "="*70)
    print("VAULT REVALIDATION PROGRESS")
    print("="*70)
    print(f"Still broken (MINT as account): {still_broken}")
    print(f"Marked for revalidation:        {pending}")
    print(f"Successfully revalidated:       {fixed}")
    print(f"Total in database:              {still_broken + pending + fixed}")

    if still_broken == 0:
        print("\n✅ ALL POOLS REVALIDATED!")
    else:
        progress_pct = (pending + fixed) * 100 // (still_broken + pending + fixed)
        print(f"\n📊 Progress: {progress_pct}% ({pending + fixed}/{still_broken + pending + fixed} fixed)")

    print("="*70)

    # Sample pools by status
    print("\nSample pending pools (first 5):")
    pending_pools = cursor.execute("""
        SELECT mint, base_account, vault_validation_attempts, last_vault_validation_at
        FROM token_pool_accounts
        WHERE is_active = 1
          AND vault_validation_status = 'pending'
          AND quote_account = ?
        LIMIT 5
    """, (WSOL_MINT,)).fetchall()

    for mint, base_account, attempts, last_at in pending_pools:
        last_str = ""
        if last_at:
            last_dt = datetime.fromtimestamp(last_at)
            ago = datetime.now() - last_dt
            if ago.total_seconds() < 60:
                last_str = f"(last attempt {ago.total_seconds():.0f}s ago)"
            elif ago.total_seconds() < 3600:
                last_str = f"(last attempt {ago.total_seconds()/60:.0f}m ago)"
            else:
                last_str = f"(last attempt {ago.total_seconds()/3600:.0f}h ago)"
        print(f"  • {mint[:16]}... attempts={attempts} {last_str}")

    conn.close()


def show_status_breakdown():
    """Show breakdown by validation status."""
    status = get_vault_status()

    print("\n" + "="*70)
    print("VAULT STATUS BREAKDOWN")
    print("="*70)

    for stat_type, count, broken in status:
        broken_str = f" ({broken} still have MINT)" if broken else ""
        print(f"{stat_type:12} {count:4} pools{broken_str}")

    print("="*70)


def main():
    parser = argparse.ArgumentParser(
        description="Monitor vault revalidation progress"
    )
    parser.add_argument(
        "--progress", action="store_true",
        help="Show revalidation progress"
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Show status breakdown"
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Show all information (default)"
    )

    args = parser.parse_args()

    # Default to showing all if no args
    if not args.progress and not args.status:
        args.all = True

    if args.progress or args.all:
        show_revalidation_progress()

    if args.status or args.all:
        show_status_breakdown()


if __name__ == "__main__":
    main()
