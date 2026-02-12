#!/usr/bin/env python3
"""
Query funder outgoing transfers from database.

Fast lookups for where funders send their SOL.
"""

import sqlite3
import sys
sys.path.insert(0, '/Users/kevinkeaveney/Dev/claude/flex')

from infra_mapping import get_cex_info, get_account_info

DB_PATH = "pumpswap_tokens.db"


def get_funder_outflows(funder_address: str, limit: int = 20) -> list:
    """Get where a funder sends SOL - from database"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT
                recipient_address,
                SUM(amount_sol) as total_sol,
                COUNT(*) as transaction_count,
                recipient_type,
                is_cex,
                cex_exchange,
                cex_type
            FROM funder_outgoing_transfers
            WHERE funder_address = ?
            GROUP BY recipient_address
            ORDER BY total_sol DESC
            LIMIT ?
        """,
            (funder_address, limit),
        )

        outflows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return outflows
    except Exception as e:
        print(f"[DB] Error: {e}")
        return []


def get_all_funder_outflows(funder_address: str) -> list:
    """Get ALL outflows for a funder"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT
                recipient_address,
                SUM(amount_sol) as total_sol,
                COUNT(*) as transaction_count,
                recipient_type,
                is_cex,
                cex_exchange,
                cex_type
            FROM funder_outgoing_transfers
            WHERE funder_address = ?
            GROUP BY recipient_address
            ORDER BY total_sol DESC
        """,
            (funder_address,),
        )

        outflows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return outflows
    except Exception as e:
        return []


def get_creator_funder_outflows(creator_address: str, limit: int = 20) -> dict:
    """Get outflows for all funders of a creator"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Get all funders for this creator
        cursor.execute(
            """
            SELECT DISTINCT funder_address
            FROM creator_funders
            WHERE creator_address = ?
            ORDER BY amount_sol DESC
            LIMIT ?
        """,
            (creator_address, limit),
        )

        funders = [row["funder_address"] for row in cursor.fetchall()]
        conn.close()

        # Get outflows for each funder
        result = {}
        for funder in funders:
            outflows = get_funder_outflows(funder, limit=10)
            if outflows:
                result[funder] = outflows

        return result
    except Exception as e:
        return {}


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Query funder outgoing transfers from database")
    parser.add_argument("funder", type=str, help="Funder address to query")
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Limit recipients to show (default 20)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Show ALL recipients (no limit)",
    )
    args = parser.parse_args()

    funder = args.funder

    print(f"[QUERY] Funder Outgoing Transfers")
    print(f"[QUERY] Funder: {funder}\n")

    # Get outflows
    if args.all:
        outflows = get_all_funder_outflows(funder)
        print(f"[DB] ✅ Found {len(outflows)} recipient addresses\n")
        print(f"[QUERY] ALL recipient addresses:\n")
    else:
        outflows = get_funder_outflows(funder, limit=args.limit)
        print(f"[DB] ✅ Found {len(outflows)} recipient addresses (from database)\n")
        print(f"[QUERY] Top {min(args.limit, len(outflows))} recipients:\n")

    if not outflows:
        print(f"[DB] ℹ️  No outgoing transfers found in database")
        print(f"     Run: python3 funder_outgoing_extractor.py <creator> --limit 50")
        print(f"     to extract and save funder outflows via RPC")
        return

    total_sol_out = 0
    cex_count = 0

    for i, transfer in enumerate(outflows, 1):
        total_sol_out += transfer["total_sol"]
        recipient_type = transfer["recipient_type"] or "unknown"

        # Build type label
        type_label = ""
        if transfer["is_cex"]:
            cex_count += 1
            type_label = f"✅ CEX: {transfer['cex_exchange']}"
        elif recipient_type == "infra":
            type_label = f"✅ INFRA"
        elif recipient_type == "pumpfun":
            type_label = f"🎯 PUMPFUN"
        elif recipient_type == "suspicious":
            type_label = f"⚠️ SUSPICIOUS"
        else:
            type_label = f"❓ UNKNOWN"

        print(f"[{i:3}] {transfer['recipient_address'][:16]}... | {transfer['total_sol']:8.2f} SOL | {transfer['transaction_count']:3} txs | {type_label}")

    # Summary
    print(f"\n{'='*100}")
    print(f"SUMMARY - FUNDER OUTFLOWS")
    print(f"{'='*100}")
    print(f"Total recipients: {len(outflows)}")
    print(f"Total SOL sent out: {total_sol_out:.2f} SOL")
    print(f"CEX recipients: {cex_count}")
    print(f"Average per recipient: {total_sol_out / len(outflows):.2f} SOL" if outflows else "")

    # Identify patterns
    if cex_count == len(outflows):
        print(f"\n✅ VERDICT: All outflows go to known CEX accounts (legitimate)")
    elif cex_count > 0:
        unknown_count = len(outflows) - cex_count
        print(f"\n⚠️ MIXED: {cex_count} CEX accounts + {unknown_count} unknown recipients")
    else:
        print(f"\n❌ WARNING: All outflows go to unknown addresses (investigate further)")


if __name__ == "__main__":
    main()
