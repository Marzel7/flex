#!/usr/bin/env python3
"""
Get funder outgoing transfers from database - historical approach.

Instead of querying RPC (limited history), query where funders appear
as RECIPIENTS in creator_outgoing_transfers (complete transaction history).
"""

import sqlite3
import sys
sys.path.insert(0, '/Users/kevinkeaveney/Dev/claude/flex')

from infra_mapping import get_cex_info, get_account_info, get_pumpfun_creator_info

DB_PATH = "pumpswap_tokens.db"


def get_creator_funders(creator_address: str) -> list:
    """Get all funders for a creator"""
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


def get_funder_outflows_from_history(funder_address: str) -> list:
    """
    Get where a funder receives SOL from creators.

    This shows: Creator → [Funder as recipient]
    Which tells us: Funders are receiving SOL from creators (profit taking?)
    """
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT
                creator_address,
                SUM(amount_sol) as total_sol,
                COUNT(*) as transaction_count,
                recipient_type
            FROM creator_outgoing_transfers
            WHERE recipient_address = ?
            GROUP BY creator_address
            ORDER BY total_sol DESC
        """,
            (funder_address,),
        )

        outflows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return outflows
    except Exception as e:
        return []


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Get funder outflows from historical database (creator_outgoing_transfers)"
    )
    parser.add_argument("funder", type=str, help="Funder address to analyze")
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Limit creators to show (default 20)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Show ALL creators (no limit)",
    )
    args = parser.parse_args()

    funder = args.funder

    print(f"[HISTORY] Funder Outgoing Transfers (Historical)")
    print(f"[HISTORY] Funder: {funder}\n")

    # Get outflows from database
    outflows = get_funder_outflows_from_history(funder)

    if not outflows:
        print(f"[DB] ℹ️  No historical outflows found for this funder")
        print(f"     (Funder may not have received SOL from creators)")
        return

    # Determine how many to show
    show_count = len(outflows) if args.all else min(args.limit, len(outflows))

    print(f"[DB] ✅ Found {len(outflows)} creators that sent SOL to this funder\n")

    if args.all:
        print(f"[HISTORY] Showing ALL {len(outflows)} creators:\n")
    else:
        print(f"[HISTORY] Top {show_count} creators by amount:\n")

    total_received = 0
    funder_type = ""

    # Classify funder once
    cex_info = get_cex_info(funder)
    if cex_info:
        funder_type = f"✅ CEX: {cex_info.get('name', 'CEX')}"
    else:
        infra_info = get_account_info(funder)
        if infra_info:
            funder_type = f"✅ INFRA: {infra_info.get('name', 'Infrastructure')}"
        else:
            pumpfun_info = get_pumpfun_creator_info(funder)
            if pumpfun_info:
                funder_type = f"🎯 PUMPFUN: {pumpfun_info.get('name', 'Creator')}"

    if funder_type:
        print(f"Funder Type: {funder_type}\n")

    for i, row in enumerate(outflows[:show_count], 1):
        total_received += row["total_sol"]
        creator = row["creator_address"]

        print(f"[{i:3}] Creator: {creator[:16]}... | {row['total_sol']:8.2f} SOL | {row['transaction_count']:3} txs")

    # Summary
    print(f"\n{'='*100}")
    print(f"SUMMARY - FUNDER RECEIVES FROM CREATORS")
    print(f"{'='*100}")
    print(f"Creators that sent to this funder: {show_count} / {len(outflows)}")
    print(f"Total SOL received: {total_received:.2f} SOL")

    if len(outflows) > 0:
        avg_per_creator = total_received / len(outflows)
        print(f"Average per creator: {avg_per_creator:.2f} SOL")

    # Interpretation
    print(f"\n📊 INTERPRETATION:")
    if total_received > 100:
        print(f"   This funder is receiving significant SOL from creators")
        print(f"   Type: Profit-taking / Exit strategy")
    elif total_received > 10:
        print(f"   This funder receives moderate SOL from creators")
        print(f"   Type: Selective profit-taking")
    else:
        print(f"   This funder receives minimal SOL from creators")
        print(f"   Type: Likely one-time funding (no active trading)")


if __name__ == "__main__":
    main()
