#!/usr/bin/env python3
"""
Batch analyze networks starting from smallest (fewest funders) to largest.
This populates senders data for all networks systematically.

Usage:
    python3 batch_analyze_networks.py                    # Analyze all networks
    python3 batch_analyze_networks.py --max-funders 10   # Only networks with ≤10 funders
    python3 batch_analyze_networks.py --networks 204,205,206  # Specific networks
"""

import sys
import os
import sqlite3
from dotenv import load_dotenv
import asyncio

sys.path.insert(0, '/Users/kevinkeaveney/Dev/claude/flex')

load_dotenv()

from funder_helius_extractor import extract_transfers_for_funder

DB_PATH = "pumpswap_tokens.db"
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "").strip()


def get_networks_by_size(max_funders=None, specific_networks=None):
    """Get networks sorted by funder count (smallest first)"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        if specific_networks:
            # Query specific networks
            placeholders = ','.join('?' * len(specific_networks))
            cursor.execute(f"""
                SELECT network_id, network_name, total_members
                FROM funding_networks
                WHERE network_id IN ({placeholders})
                ORDER BY total_members ASC
            """, specific_networks)
        elif max_funders:
            cursor.execute("""
                SELECT network_id, network_name, total_members
                FROM funding_networks
                WHERE total_members <= ?
                ORDER BY total_members ASC
            """, (max_funders,))
        else:
            cursor.execute("""
                SELECT network_id, network_name, total_members
                FROM funding_networks
                ORDER BY total_members ASC
            """)

        networks = [(row['network_id'], row['network_name'], row['total_members']) for row in cursor.fetchall()]
        conn.close()
        return networks
    except Exception as e:
        print(f"[DB] Error getting networks: {e}")
        return []


def get_network_funders(network_id):
    """Get all funders in a specific network"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT DISTINCT funder_address
            FROM funding_network_members
            WHERE network_id = ?
        """, (network_id,))

        funders = [row['funder_address'] for row in cursor.fetchall()]
        conn.close()
        return funders
    except Exception as e:
        print(f"[DB] Error getting network funders: {e}")
        return []


def get_network_senders(network_id):
    """Count unique senders for a network"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT COUNT(DISTINCT sender_address) as senders
            FROM funder_incoming_transfers
            WHERE funder_address IN (
                SELECT funder_address
                FROM funding_network_members
                WHERE network_id = ?
            )
        """, (network_id,))

        result = cursor.fetchone()
        conn.close()
        return result[0] if result else 0
    except Exception as e:
        return 0


async def analyze_network(network_id, network_name, funder_count):
    """Analyze a single network"""
    print(f"\n{'='*80}")
    print(f"[NETWORK] {network_name} (ID: {network_id})")
    print(f"{'='*80}")
    print(f"Funders to analyze: {funder_count}")

    # Get all funders
    funders = get_network_funders(network_id)
    if not funders:
        print(f"[ERROR] No funders found for network {network_id}")
        return None

    # Analyze each funder
    total_incoming = 0
    for i, funder_addr in enumerate(funders, 1):
        print(f"\n[{i}/{len(funders)}] {funder_addr[:16]}...")
        result = extract_transfers_for_funder(funder_addr)
        incoming = result.get('incoming_count', 0)
        total_incoming += incoming
        print(f"     ✓ {incoming} incoming transfers")

    # Get final sender count
    senders = get_network_senders(network_id)

    print(f"\n{'='*80}")
    print(f"[COMPLETE] {network_name}")
    print(f"  Total incoming transfers found: {total_incoming}")
    print(f"  Total unique senders: {senders}")
    print(f"{'='*80}")

    return {
        'network_id': network_id,
        'network_name': network_name,
        'funders': funder_count,
        'senders': senders,
        'total_incoming': total_incoming
    }


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Batch analyze networks for senders")
    parser.add_argument(
        '--max-funders',
        type=int,
        default=None,
        help='Only analyze networks with this many funders or fewer'
    )
    parser.add_argument(
        '--networks',
        type=str,
        default=None,
        help='Comma-separated list of network IDs to analyze'
    )
    parser.add_argument(
        '--skip',
        type=str,
        default=None,
        help='Comma-separated list of network IDs to skip'
    )
    args = parser.parse_args()

    if not HELIUS_API_KEY:
        print("[ERROR] HELIUS_API_KEY not set in environment")
        sys.exit(1)

    # Parse arguments
    specific_networks = None
    skip_networks = set()

    if args.networks:
        specific_networks = [int(x.strip()) for x in args.networks.split(',')]

    if args.skip:
        skip_networks = set(int(x.strip()) for x in args.skip.split(','))

    # Get networks to analyze
    networks = get_networks_by_size(
        max_funders=args.max_funders,
        specific_networks=specific_networks
    )

    # Filter out skipped networks
    if skip_networks:
        networks = [(n, name, f) for n, name, f in networks if n not in skip_networks]

    print(f"\n{'='*80}")
    print(f"[START] Batch Network Analysis")
    print(f"{'='*80}")
    print(f"Networks to analyze: {len(networks)}")
    if args.max_funders:
        print(f"Max funders per network: {args.max_funders}")
    print()

    # Analyze each network
    results = []
    for i, (network_id, network_name, funder_count) in enumerate(networks, 1):
        try:
            result = await analyze_network(network_id, network_name, funder_count)
            if result:
                results.append(result)
        except Exception as e:
            print(f"[ERROR] Failed to analyze network {network_id}: {e}")
            continue

    # Summary
    print(f"\n\n{'='*80}")
    print(f"BATCH ANALYSIS COMPLETE")
    print(f"{'='*80}")
    print(f"Networks analyzed: {len(results)}")
    print(f"Total senders found: {sum(r['senders'] for r in results)}")
    print(f"Total incoming transfers: {sum(r['total_incoming'] for r in results)}")

    if results:
        print(f"\nTop networks by senders:")
        sorted_results = sorted(results, key=lambda x: x['senders'], reverse=True)
        for i, r in enumerate(sorted_results[:10], 1):
            print(f"  {i}. {r['network_name']} (ID: {r['network_id']}) - {r['senders']} senders, {r['funders']} funders")


if __name__ == "__main__":
    asyncio.run(main())
