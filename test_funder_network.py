#!/usr/bin/env python3
"""
Test Funder Network - Identify Coordinated Funding

For a given creator:
1. Get all funders (from creator_funders table)
2. For EACH funder, check if they also fund OTHER creators
3. Show which funders are funding multiple creators
4. This reveals coordination patterns

Uses database queries (instant) instead of RPC (slow/limited).
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


def get_funder_activities(funder_address: str) -> dict:
    """Get all creators this funder supports"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT creator_address, amount_sol
            FROM creator_funders
            WHERE funder_address = ?
            ORDER BY amount_sol DESC
        """,
            (funder_address,),
        )

        creators = {}
        for row in cursor.fetchall():
            creators[row["creator_address"]] = row["amount_sol"]

        conn.close()
        return creators
    except Exception as e:
        print(f"[DB] Error: {e}")
        return {}


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Test funder network coordination")
    parser.add_argument("creator", type=str, help="Creator address to analyze")
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Limit funders to analyze (default 20)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Analyze ALL funders (overrides --limit)",
    )
    args = parser.parse_args()

    creator = args.creator

    print(f"[TEST] Analyzing funder network for creator:")
    print(f"[TEST] {creator}\n")

    # Get all funders for this creator
    funders = get_creator_funders(creator)
    if not funders:
        print(f"[DB] ❌ No funders found for this creator")
        return

    print(f"[DB] ✅ Found {len(funders)} total funders\n")

    # Determine how many funders to analyze
    analyze_count = len(funders) if args.all else min(args.limit, len(funders))
    if args.all:
        print(f"[ANALYSIS] Analyzing ALL {len(funders)} funders:\n")
    else:
        print(f"[ANALYSIS] Top {analyze_count} funders by amount:\n")

    repeat_funder_count = 0
    total_other_creators_funded = 0

    for i, (funder_address, amount_to_creator) in enumerate(funders[:analyze_count], 1):
        # Get all creators this funder supports
        all_creators = get_funder_activities(funder_address)

        # Check for known accounts
        account_info = None
        account_type = ""

        cex_info = get_cex_info(funder_address)
        if cex_info:
            account_info = cex_info
            account_type = f"✅ CEX: {cex_info.get('name', 'CEX')}"
        else:
            infra_info = get_account_info(funder_address)
            if infra_info:
                account_info = infra_info
                account_type = f"✅ INFRA: {infra_info.get('name', 'Infrastructure')}"
            else:
                pumpfun_info = get_pumpfun_creator_info(funder_address)
                if pumpfun_info:
                    account_info = pumpfun_info
                    account_type = f"🎯 PUMPFUN: {pumpfun_info.get('name', 'Creator')}"
                else:
                    suspicious_info = get_suspicious_wallet_info(funder_address)
                    if suspicious_info:
                        account_info = suspicious_info
                        account_type = f"⚠️  SUSPICIOUS: {suspicious_info.get('name', 'Suspicious')}"

        # Check if funds multiple creators
        is_repeat_funder = len(all_creators) > 1
        other_creators_count = len(all_creators) - 1

        status = "🔗" if is_repeat_funder else "  "
        if is_repeat_funder:
            repeat_funder_count += 1
            total_other_creators_funded += other_creators_count

        if account_type:
            print(
                f"[{i:2}] {status} {funder_address[:12]}... | {amount_to_creator:8.3f} SOL | "
                f"Funds {len(all_creators)} creators | {account_type}"
            )
        else:
            print(
                f"[{i:2}] {status} {funder_address[:12]}... | {amount_to_creator:8.3f} SOL | "
                f"Funds {len(all_creators)} creators"
            )

        # Show other creators if this is a repeat funder
        if is_repeat_funder and other_creators_count > 0:
            # Find which other creators (exclude current creator)
            other_creators = [
                (addr, amt)
                for addr, amt in sorted(all_creators.items(), key=lambda x: x[1], reverse=True)
                if addr != creator
            ]

            print(f"           └─ ALSO FUNDS:")
            for other_creator, other_amount in other_creators[:3]:
                print(
                    f"              • {other_creator[:12]}... ({other_amount:.3f} SOL)"
                )
            if other_creators_count > 3:
                print(f"              ... and {other_creators_count - 3} more creators")
            print()

    # Summary
    print(f"\n{'='*80}")
    print(f"SUMMARY")
    print(f"{'='*80}")
    print(f"Total funders analyzed: {analyze_count} / {len(funders)}")
    print(f"Repeat funders (fund 2+ creators): {repeat_funder_count}")
    print(f"Other creators funded by these repeats: {total_other_creators_funded}")

    if repeat_funder_count > 0:
        percentage = (repeat_funder_count / analyze_count) * 100
        print(f"\n🔗 NETWORK DETECTED: {repeat_funder_count} funders ({percentage:.1f}%) are part of larger network!")
        print(f"    This indicates coordinated funding or operational wallets.")

    if args.all:
        print(f"\n✅ Analysis complete: ALL {len(funders)} funders analyzed")


if __name__ == "__main__":
    main()
