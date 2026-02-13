#!/usr/bin/env python3
"""
Analyze cross-funder coordinators - senders that fund multiple funders to reach multiple creators.
This detects sophisticated coordination patterns beyond simple direct funding.
"""

import sqlite3
import json
from typing import Dict, List, Set, Tuple
from collections import defaultdict

DB_PATH = "pumpswap_tokens.db"

def get_db_connection():
    """Get database connection with row factory."""
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn

def analyze_cross_funder_coordinators():
    """
    Analyze senders that fund multiple funders (cross-funder coordinators).

    These represent multi-layer coordination patterns:
    - Single sender → Multiple intermediary funders → Multiple creators
    - Can indicate sophisticated pump & dump operations
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Find all senders that fund 2+ funders
    cursor.execute("""
        SELECT
            sender_address,
            COUNT(DISTINCT funder_address) as funder_count,
            GROUP_CONCAT(DISTINCT funder_address) as funders_list,
            SUM(amount_sol) as total_sol_sent
        FROM funder_incoming_transfers
        GROUP BY sender_address
        HAVING funder_count >= 2
        ORDER BY funder_count DESC, total_sol_sent DESC
    """)

    cross_funder_senders = cursor.fetchall()
    print(f"\n{'='*80}")
    print(f"CROSS-FUNDER COORDINATORS ANALYSIS")
    print(f"{'='*80}")
    print(f"\nFound {len(cross_funder_senders)} senders funding 2+ funders\n")

    coordinators_to_insert = []

    for sender in cross_funder_senders:
        sender_addr = sender['sender_address']
        funder_count = sender['funder_count']
        funders = sender['funders_list'].split(',')
        total_sol = sender['total_sol_sent'] or 0

        # Get all creators reached through these funders
        creators_reached = set()
        creator_details = []

        for funder in funders:
            cursor.execute("""
                SELECT DISTINCT creator_address, SUM(amount_sol) as total_to_creator
                FROM creator_funders
                WHERE funder_address = ?
                GROUP BY creator_address
            """, (funder,))

            funder_creators = cursor.fetchall()
            for fc in funder_creators:
                creators_reached.add(fc['creator_address'])
                creator_details.append({
                    'creator': fc['creator_address'],
                    'via_funder': funder,
                    'amount': fc['total_to_creator']
                })

        creator_count = len(creators_reached)

        # Get sender type info
        cursor.execute("""
            SELECT DISTINCT sender_type FROM funder_incoming_transfers
            WHERE sender_address = ?
            LIMIT 1
        """, (sender_addr,))
        sender_type_row = cursor.fetchone()
        sender_type = sender_type_row['sender_type'] if sender_type_row else 'unknown'

        # Check if sender is CEX/INFRA
        from infra_mapping import get_cex_info
        cex_info = get_cex_info(sender_addr)
        is_cex = cex_info is not None
        cex_exchange = cex_info.get('name') if cex_info else None

        # Calculate suspicious flags
        suspicious_flags = []

        # Flag: Dust transfers (very small amounts)
        if total_sol < 0.001 and total_sol > 0:
            suspicious_flags.append("dust_transfers")

        # Flag: Multiple funders to same creators
        creators_via_multiple = defaultdict(int)
        for detail in creator_details:
            creators_via_multiple[detail['creator']] += 1

        if any(count > 1 for count in creators_via_multiple.values()):
            suspicious_flags.append("reaches_creators_via_multiple_funders")

        # Flag: High number of connections
        if funder_count >= 3:
            suspicious_flags.append("high_funder_fanout")

        if creator_count >= 3:
            suspicious_flags.append("high_creator_reach")

        # Determine network confidence
        confidence = "low"
        if is_cex:
            confidence = "low"  # CEX wallets less likely to be coordinators
        elif funder_count >= 3 and creator_count >= 3:
            confidence = "high"
        elif funder_count >= 2 and creator_count >= 2:
            confidence = "medium"

        # Store for insertion
        coordinator_info = {
            'address': sender_addr,
            'creator_count': creator_count,
            'funder_count': funder_count,
            'total_sol': total_sol,
            'creators': sorted(list(creators_reached)),
            'confidence': confidence,
            'is_cex': is_cex,
            'cex_exchange': cex_exchange,
            'suspicious_flags': suspicious_flags,
            'sender_type': sender_type
        }
        coordinators_to_insert.append(coordinator_info)

        # Print details
        print(f"\nCoordinator: {sender_addr}")
        print(f"  Type: {sender_type} {'(CEX)' if is_cex else ''}")
        print(f"  Funders: {funder_count} ({', '.join(funders[:3])}{'...' if len(funders) > 3 else ''})")
        print(f"  Creators Reached: {creator_count}")
        print(f"  Total SOL: {total_sol:.12f}")
        print(f"  Confidence: {confidence}")
        print(f"  Flags: {', '.join(suspicious_flags) if suspicious_flags else 'none'}")

        # Show creator network
        if creator_count <= 5:
            print(f"  Creator Network:")
            for creator in sorted(creators_reached):
                print(f"    - {creator}")

    print(f"\n{'='*80}")
    print(f"SUMMARY: {len(coordinators_to_insert)} cross-funder coordinators identified")
    print(f"{'='*80}\n")

    return coordinators_to_insert

def insert_coordinators_to_db(coordinators: List[Dict]):
    """Insert analyzed coordinators into network_coordinators table."""
    conn = sqlite3.connect(DB_PATH, timeout=5)
    cursor = conn.cursor()

    # Enable writing
    conn.execute("PRAGMA query_only = OFF")

    for coord in coordinators:
        cursor.execute("""
            INSERT OR REPLACE INTO network_coordinators
            (coordinator_address, creator_count, creators_linked, total_sol_moved,
             network_confidence, is_cex, cex_exchange, suspicious_flags)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            coord['address'],
            coord['creator_count'],
            json.dumps(coord['creators']),
            coord['total_sol'],
            coord['confidence'],
            1 if coord['is_cex'] else 0,
            coord['cex_exchange'],
            json.dumps(coord['suspicious_flags'])
        ))

    conn.commit()
    conn.close()
    print(f"✓ Inserted {len(coordinators)} coordinators into network_coordinators table")

def main():
    print("\nAnalyzing cross-funder coordination patterns...")
    coordinators = analyze_cross_funder_coordinators()

    if coordinators:
        print("\nInserting into database...")
        insert_coordinators_to_db(coordinators)

        # Verify insertion
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM network_coordinators WHERE network_confidence IN ('high', 'medium')")
        count = cursor.fetchone()[0]
        conn.close()
        print(f"✓ Database now contains {count} high/medium confidence coordinators")

if __name__ == "__main__":
    main()
