#!/usr/bin/env python3
"""
Visualize cross-funder coordinator network structure.
Shows the connections between coordinators, funders, and creators.
"""

import sqlite3
import json
from collections import defaultdict

DB_PATH = "pumpswap_tokens.db"

def visualize_coordinator_network():
    """Print ASCII visualization of coordinator network."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    print("\n" + "="*100)
    print("CROSS-FUNDER COORDINATOR NETWORK VISUALIZATION")
    print("="*100)

    # Get all coordinators
    cursor.execute("""
        SELECT
            coordinator_address,
            creator_count,
            creators_linked,
            network_confidence
        FROM network_coordinators
        ORDER BY network_confidence DESC, creator_count DESC
    """)

    coordinators = cursor.fetchall()

    # Build network graph
    coordinator_funders = defaultdict(list)
    funder_creators = defaultdict(list)

    for coord in coordinators:
        coord_addr = coord['coordinator_address']

        # Get funders for this coordinator
        cursor.execute("""
            SELECT DISTINCT funder_address
            FROM funder_incoming_transfers
            WHERE sender_address = ?
        """, (coord_addr,))

        funders = [row['funder_address'] for row in cursor.fetchall()]
        coordinator_funders[coord_addr] = funders

        # Get creators for each funder
        for funder in funders:
            cursor.execute("""
                SELECT DISTINCT creator_address
                FROM creator_funders
                WHERE funder_address = ?
            """, (funder,))

            creators = [row['creator_address'] for row in cursor.fetchall()]
            funder_creators[funder] = creators

    # Print network visualization
    for coord_data in coordinators:
        coord_addr = coord_data['coordinator_address']
        confidence = coord_data['network_confidence']
        creator_count = coord_data['creator_count']

        confidence_icon = "🔴" if confidence == "high" else "🟠"

        print(f"\n{confidence_icon} {coord_addr}")
        print(f"   Confidence: {confidence.upper()} | Creators: {creator_count}")
        print("   │")

        funders = coordinator_funders[coord_addr]
        for i, funder in enumerate(funders):
            is_last_funder = (i == len(funders) - 1)
            funder_prefix = "   └─→ " if is_last_funder else "   ├─→ "
            print(f"{funder_prefix}{funder[:20]}...")

            creators = funder_creators[funder]
            for j, creator in enumerate(creators):
                is_last_creator = (j == len(creators) - 1)

                # Get creator's risk info
                cursor.execute("""
                    SELECT risk_level, market_cap_highest
                    FROM token_analysis
                    WHERE earliest_tx_creator = ?
                    LIMIT 1
                """, (creator,))

                token_info = cursor.fetchone()
                risk = token_info['risk_level'] if token_info else "UNKNOWN"
                mc = int(token_info['market_cap_highest']) if token_info and token_info['market_cap_highest'] else 0

                risk_icon = "⚠️ " if risk in ["HIGH", "CRITICAL"] else "✓ "

                creator_line_prefix = "   " if is_last_funder else "   │"
                creator_prefix = creator_line_prefix + ("└─→ " if is_last_creator else "├─→ ")

                print(f"{creator_prefix}{risk_icon}{creator[:16]}... ({risk}, MC: ${mc:,.0f})")

    # Print summary statistics
    print("\n" + "="*100)
    print("NETWORK STATISTICS")
    print("="*100)

    # Count shared funders
    all_funders = set()
    for funders_list in coordinator_funders.values():
        all_funders.update(funders_list)

    funder_usage = defaultdict(int)
    for funders_list in coordinator_funders.values():
        for funder in funders_list:
            funder_usage[funder] += 1

    shared_funders = {f: count for f, count in funder_usage.items() if count > 1}

    print(f"\nTotal Coordinators: {len(coordinators)}")
    print(f"Total Funders: {len(all_funders)}")
    print(f"Shared Funders (used by 2+ coordinators): {len(shared_funders)}")

    if shared_funders:
        print("\nShared Funders (Infrastructure Reuse):")
        for funder, count in sorted(shared_funders.items(), key=lambda x: x[1], reverse=True):
            print(f"  {funder[:20]}... used by {count} coordinators")

    # Count shared creators
    all_creators = set()
    for creators_list in funder_creators.values():
        all_creators.update(creators_list)

    creator_funders_count = defaultdict(int)
    for creators_list in funder_creators.values():
        for creator in creators_list:
            creator_funders_count[creator] += 1

    shared_creators = {c: count for c, count in creator_funders_count.items() if count > 1}

    print(f"\nTotal Unique Creators: {len(all_creators)}")
    print(f"Shared Creators (funded by 2+ funders): {len(shared_creators)}")

    if shared_creators:
        print("\nMost Funded Creators:")
        for creator, count in sorted(shared_creators.items(), key=lambda x: x[1], reverse=True)[:5]:
            print(f"  {creator[:16]}... funded by {count} different funders")

    print("\n" + "="*100 + "\n")

    conn.close()

if __name__ == "__main__":
    visualize_coordinator_network()
