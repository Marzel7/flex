#!/usr/bin/env python3
"""
Analyze a Repeat Funder

For a given funder address:
1. Get all creators it funds
2. For each creator, get their OTHER funders
3. Show if those OTHER funders also are repeat funders
4. Reveal the full coordination network
"""

import sqlite3

DB_PATH = "pumpswap_tokens.db"


def get_creators_funded_by(funder_address: str) -> list:
    """Get all creators funded by this address"""
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

        creators = [(row["creator_address"], row["amount_sol"]) for row in cursor.fetchall()]
        conn.close()
        return creators
    except Exception as e:
        print(f"[DB] Error: {e}")
        return []


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


def get_funder_creator_count(funder_address: str) -> int:
    """Get how many creators a funder supports"""
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
        print(f"[DB] Error: {e}")
        return 0


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Analyze a repeat funder's network")
    parser.add_argument("funder", type=str, help="Funder address to analyze")
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Limit creators to analyze (default 10)",
    )
    args = parser.parse_args()

    funder = args.funder

    print(f"[TEST] Analyzing funder network for repeat funder:")
    print(f"[TEST] {funder}\n")

    # Get all creators this funder supports
    creators = get_creators_funded_by(funder)
    if not creators:
        print(f"[DB] ❌ No creators funded by this address")
        return

    print(f"[DB] ✅ This funder supports {len(creators)} creators\n")
    print(f"[ANALYSIS] Top {min(args.limit, len(creators))} creators by funding amount:\n")

    repeat_funders_in_network = set()

    for i, (creator_address, amount) in enumerate(creators[: args.limit], 1):
        print(f"[{i:2}] Creator: {creator_address[:12]}... | {amount:.3f} SOL")

        # Get all funders for this creator
        creator_funders = get_creator_funders(creator_address)
        print(f"      └─ This creator has {len(creator_funders)} funders")

        # Check if any of these funders are also repeat funders
        for funder_addr, funder_amount in creator_funders[:5]:
            creator_count = get_funder_creator_count(funder_addr)

            if creator_count > 1:
                print(
                    f"         🔗 {funder_addr[:12]}... ({funder_amount:.3f} SOL) - funds {creator_count} creators!"
                )
                repeat_funders_in_network.add(funder_addr)
            else:
                print(f"            {funder_addr[:12]}... ({funder_amount:.3f} SOL) - single creator")

        if len(creator_funders) > 5:
            print(f"            ... and {len(creator_funders) - 5} more funders")
        print()

    # Summary
    print(f"\n{'='*80}")
    print(f"NETWORK ANALYSIS SUMMARY")
    print(f"{'='*80}")
    print(f"Primary funder: {funder[:12]}... (funds {len(creators)} creators)")
    print(f"Repeat funders in this network: {len(repeat_funders_in_network)}")

    if repeat_funders_in_network:
        print(f"\n🔗 COORDINATION PATTERN DETECTED!")
        print(f"   The following {len(repeat_funders_in_network)} repeat funders also appear across creators funded by {funder[:12]}:")
        for addr in sorted(repeat_funders_in_network)[:5]:
            count = get_funder_creator_count(addr)
            print(f"   • {addr} (funds {count} creators)")


if __name__ == "__main__":
    main()
