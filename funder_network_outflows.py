#!/usr/bin/env python3
"""
Funder Network Analysis - Where do funded creators send their SOL?

For a creator's funders:
1. Get all creators funded by each funder
2. For each of those creators, check where they send SOL
3. Identify if funders are coordinated (send to same places)
"""

import sqlite3
import sys
sys.path.insert(0, '/Users/kevinkeaveney/Dev/claude/flex')

from infra_mapping import get_cex_info, get_account_info

DB_PATH = "pumpswap_tokens.db"


def get_creator_funders(creator_address: str) -> list:
    """Get all funders for a creator"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT funder_address, COUNT(DISTINCT creator_address) as creator_count, SUM(amount_sol) as total_sol
            FROM creator_funders
            WHERE creator_address = ?
            GROUP BY funder_address
            ORDER BY creator_count DESC, total_sol DESC
        """,
            (creator_address,),
        )

        funders = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return funders
    except Exception as e:
        print(f"[DB] Error: {e}")
        return []


def get_repeat_funders(creator_address: str) -> list:
    """Get funders that fund multiple creators (repeat funders)"""
    funders = get_creator_funders(creator_address)
    return [f for f in funders if f["creator_count"] > 1]


def get_creators_funded_by(funder_address: str, limit: int = 100) -> list:
    """Get all creators funded by a specific funder"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT creator_address, SUM(amount_sol) as total_sol, COUNT(*) as transaction_count
            FROM creator_funders
            WHERE funder_address = ?
            GROUP BY creator_address
            ORDER BY total_sol DESC
            LIMIT ?
        """,
            (funder_address, limit),
        )

        creators = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return creators
    except Exception as e:
        return []


def get_creator_outflows(creator_address: str, limit: int = 10) -> list:
    """Get where a creator sends SOL"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT recipient_address, SUM(amount_sol) as total_sol, COUNT(*) as transaction_count, recipient_type, is_cex, cex_exchange
            FROM creator_outgoing_transfers
            WHERE creator_address = ?
            GROUP BY recipient_address
            ORDER BY total_sol DESC
            LIMIT ?
        """,
            (creator_address, limit),
        )

        outflows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return outflows
    except Exception as e:
        return []


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Analyze funder network - where do funded creators send their SOL?"
    )
    parser.add_argument("creator", type=str, help="Creator address to analyze")
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Limit repeat funders to analyze (default 5)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Show ALL repeat funders",
    )
    args = parser.parse_args()

    creator = args.creator

    print(f"[NETWORK] Funder Network Outflow Analysis")
    print(f"[NETWORK] Creator: {creator}\n")

    # Get repeat funders
    repeat_funders = get_repeat_funders(creator)
    if not repeat_funders:
        print(f"[DB] ℹ️  No repeat funders found for this creator")
        return

    show_count = len(repeat_funders) if args.all else min(args.limit, len(repeat_funders))

    print(f"[DB] ✅ Found {len(repeat_funders)} repeat funders\n")
    print(f"[NETWORK] Analyzing top {show_count} repeat funders:\n")

    # Track shared destinations
    shared_destinations = {}

    for i, funder in enumerate(repeat_funders[:show_count], 1):
        funder_addr = funder["funder_address"]
        creator_count = funder["creator_count"]

        # Classify funder
        funder_type = ""
        cex_info = get_cex_info(funder_addr)
        if cex_info:
            funder_type = f"✅ CEX: {cex_info.get('name')}"
        else:
            infra_info = get_account_info(funder_addr)
            if infra_info:
                funder_type = f"✅ INFRA: {infra_info.get('name')}"

        print(f"[{i}] Funder: {funder_addr[:12]}... | Funds {creator_count} creators")
        if funder_type:
            print(f"    Type: {funder_type}")

        # Get creators funded by this funder
        funded_creators = get_creators_funded_by(funder_addr, limit=20)

        # Collect where these creators send SOL
        destinations = {}
        for fc in funded_creators:
            outflows = get_creator_outflows(fc["creator_address"], limit=10)
            for outflow in outflows:
                dest = outflow["recipient_address"]
                if dest not in destinations:
                    destinations[dest] = {"sol": 0, "txs": 0, "type": outflow.get("recipient_type")}
                destinations[dest]["sol"] += outflow["total_sol"]
                destinations[dest]["txs"] += outflow["transaction_count"]

                # Track in shared_destinations
                if dest not in shared_destinations:
                    shared_destinations[dest] = {"funders": set(), "total_sol": 0}
                shared_destinations[dest]["funders"].add(funder_addr)
                shared_destinations[dest]["total_sol"] += outflow["total_sol"]

        # Show top destinations
        if destinations:
            sorted_dests = sorted(destinations.items(), key=lambda x: x[1]["sol"], reverse=True)
            print(f"    Recipients (from funded creators):")
            for j, (dest_addr, data) in enumerate(sorted_dests[:5], 1):
                dest_type_label = ""
                if data.get("type") == "cex":
                    cex_info = get_cex_info(dest_addr)
                    if cex_info:
                        dest_type_label = f"[✅ CEX: {cex_info.get('name')}]"
                elif data.get("type") == "infra":
                    dest_type_label = f"[✅ INFRA]"

                print(
                    f"      {j}. {dest_addr[:12]}... ← {data['sol']:.2f} SOL | {data['txs']} txs {dest_type_label}"
                )

            if len(sorted_dests) > 5:
                remaining = sum(d["sol"] for _, d in sorted_dests[5:])
                print(f"      ... and {len(sorted_dests) - 5} more ({remaining:.2f} SOL)")
        else:
            print(f"    No outflows detected from funded creators")

        print()

    # Summary - shared destinations
    print(f"\n{'='*100}")
    print(f"NETWORK COORDINATION ANALYSIS")
    print(f"{'='*100}")

    # Find destinations shared by multiple funders
    shared = {d: info for d, info in shared_destinations.items() if len(info["funders"]) > 1}

    if shared:
        print(f"\n🔗 SHARED DESTINATIONS (Multiple funders send here):\n")
        sorted_shared = sorted(shared.items(), key=lambda x: x[1]["total_sol"], reverse=True)
        for i, (dest, info) in enumerate(sorted_shared[:10], 1):
            funder_count = len(info["funders"])
            print(f"[{i}] {dest[:12]}... ← {info['total_sol']:.2f} SOL from {funder_count} funders")

            # Show which funders use this destination
            for funder in list(info["funders"])[:3]:
                print(f"      • {funder[:12]}...")
            if funder_count > 3:
                print(f"      ... and {funder_count - 3} more")

        print(f"\n⚠️ VERDICT: Coordination detected!")
        print(f"   {len(shared)} destinations are used by multiple funders")
        print(f"   This indicates potential network coordination")
    else:
        print(f"\n✅ No coordination detected")
        print(f"   Each funder routes to different destinations")


if __name__ == "__main__":
    main()
