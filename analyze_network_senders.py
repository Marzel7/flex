#!/usr/bin/env python3
"""
Analyze and populate senders for a specific network.
Usage: python3 analyze_network_senders.py <network_id>
"""

import sys
import os
from dotenv import load_dotenv

sys.path.insert(0, '/Users/kevinkeaveney/Dev/claude/flex')

load_dotenv()

# Import the funder extractor
from funder_helius_extractor import extract_transfers_for_funder, get_creator_funders
import sqlite3

DB_PATH = "pumpswap_tokens.db"
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "").strip()


def get_network_funders(network_id: int) -> list:
    """Get all funders in a specific network"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT DISTINCT funder_address
            FROM funding_network_members
            WHERE network_id = ?
        """,
            (network_id,),
        )

        funders = [row["funder_address"] for row in cursor.fetchall()]
        conn.close()
        return funders
    except Exception as e:
        print(f"[DB] Error getting network funders: {e}")
        return []


async def main():
    if not HELIUS_API_KEY:
        print("[ERROR] HELIUS_API_KEY not set in environment")
        sys.exit(1)

    if len(sys.argv) < 2:
        print("Usage: python3 analyze_network_senders.py <network_id>")
        sys.exit(1)

    network_id = int(sys.argv[1])

    print(f"\n{'='*80}")
    print(f"[NETWORK] Analyzing senders for network {network_id}")
    print(f"{'='*80}\n")

    # Get all funders in this network
    funders = get_network_funders(network_id)
    print(f"[NETWORK] Found {len(funders)} funders in network {network_id}")

    if not funders:
        print("[NETWORK] No funders found!")
        return

    # Extract transfers for each funder
    total_senders = set()
    for i, funder_addr in enumerate(funders, 1):
        print(f"\n[{i}/{len(funders)}] Extracting transfers for funder: {funder_addr[:12]}...")
        result = extract_transfers_for_funder(funder_addr)

        # The result includes incoming_count which tells us how many incoming transfers were found
        incoming = result.get('incoming_count', 0)
        print(f"     Found {incoming} incoming transfers (senders)")

    # Now check how many unique senders we found
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT COUNT(DISTINCT sender_address) as unique_senders
            FROM funder_incoming_transfers
            WHERE funder_address IN (
                SELECT funder_address
                FROM funding_network_members
                WHERE network_id = ?
            )
        """,
            (network_id,),
        )

        result = cursor.fetchone()
        unique_senders = result["unique_senders"] if result else 0

        conn.close()

        print(f"\n{'='*80}")
        print(f"[COMPLETE] Network {network_id} analysis complete")
        print(f"  Total unique senders found: {unique_senders}")
        print(f"{'='*80}\n")

    except Exception as e:
        print(f"[DB] Error counting senders: {e}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
