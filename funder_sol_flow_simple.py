#!/usr/bin/env python3
"""
Simple Funder SOL Flow Analysis

For a given creator:
1. Get all funders from database
2. Show account type for each funder (CEX, INFRA, etc.)
3. Show total SOL sent to creator
4. Show any repeat funding patterns
"""

import sqlite3
import sys
sys.path.insert(0, '/Users/kevinkeaveney/Dev/claude/flex')

from infra_mapping import get_account_info, get_cex_info, get_pumpfun_creator_info, get_suspicious_wallet_info

DB_PATH = "pumpswap_tokens.db"


def get_creator_funders(creator_address: str) -> list:
    """Get all funders for a creator from database"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT funder_address, amount_sol
            FROM creator_funders
            WHERE creator_address = ?
            ORDER BY amount_sol DESC
        """,
            (creator_address,),
        )

        funders = [(row["funder_address"], row["amount_sol"]) for row in cursor.fetchall()]
        conn.close()
        return funders
    except Exception as e:
        print(f"[DB] Error: {e}")
        return []


def get_funder_creator_count(funder_address: str) -> int:
    """Get how many creators this funder supports"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT COUNT(DISTINCT creator_address) as count
            FROM creator_funders
            WHERE funder_address = ?
        """,
            (funder_address,),
        )

        result = cursor.fetchone()
        conn.close()
        return result[0] if result else 0
    except Exception as e:
        return 0


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Show funder SOL flows for a creator")
    parser.add_argument("creator", type=str, help="Creator address to analyze")
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Limit funders to show (default 20)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Show ALL funders (overrides --limit)",
    )
    args = parser.parse_args()

    creator = args.creator

    print(f"[ANALYSIS] Funder SOL Flow Analysis")
    print(f"[ANALYSIS] Creator: {creator}\n")

    # Get all funders
    funders = get_creator_funders(creator)
    if not funders:
        print(f"[DB] ❌ No funders found for this creator")
        return

    print(f"[DB] ✅ Found {len(funders)} total funders\n")

    # Determine how many to show
    show_count = len(funders) if args.all else min(args.limit, len(funders))

    if args.all:
        print(f"[ANALYSIS] Showing ALL {len(funders)} funders:\n")
    else:
        print(f"[ANALYSIS] Top {show_count} funders by amount:\n")

    total_sol_in = 0
    repeat_funder_count = 0

    for i, (funder_address, amount_sol) in enumerate(funders[:show_count], 1):
        total_sol_in += amount_sol

        # Get account type
        account_type = ""
        cex_info = get_cex_info(funder_address)
        if cex_info:
            account_type = f"✅ CEX: {cex_info.get('name', 'CEX')}"
        else:
            infra_info = get_account_info(funder_address)
            if infra_info:
                account_type = f"✅ INFRA: {infra_info.get('name', 'Infrastructure')}"
            else:
                pumpfun_info = get_pumpfun_creator_info(funder_address)
                if pumpfun_info:
                    account_type = f"🎯 PUMPFUN: {pumpfun_info.get('name', 'Creator')}"
                else:
                    suspicious_info = get_suspicious_wallet_info(funder_address)
                    if suspicious_info:
                        account_type = f"⚠️ SUSPICIOUS: {suspicious_info.get('name', 'Suspicious')}"

        # Check if repeat funder
        creator_count = get_funder_creator_count(funder_address)
        is_repeat = creator_count > 1

        if is_repeat:
            repeat_funder_count += 1
            marker = "🔗"
        else:
            marker = "  "

        # Format output
        print(f"[{i:3}] {marker} {funder_address[:16]}... | {amount_sol:8.2f} SOL IN", end="")

        if account_type:
            print(f" | {account_type}", end="")

        if is_repeat:
            print(f" | Funds {creator_count} creators")
        else:
            print()

    # Summary
    print(f"\n{'='*100}")
    print(f"SUMMARY")
    print(f"{'='*100}")
    print(f"Total funders shown: {show_count} / {len(funders)}")
    print(f"Total SOL received from these funders: {total_sol_in:.2f} SOL")
    print(f"Repeat funders (fund 2+ creators): {repeat_funder_count}")
    print(f"Repeat funder percentage: {(repeat_funder_count/show_count)*100:.1f}%")

    if repeat_funder_count > 0:
        print(f"\n🔗 NETWORK DETECTED: {repeat_funder_count} funders fund this creator AND other creators")
        print(f"   This indicates potential coordinated funding patterns")


if __name__ == "__main__":
    main()
