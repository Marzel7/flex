#!/usr/bin/env python3
"""
Find connections between creators through shared SOL destination addresses.

Shows:
- All creators that send SOL to the same addresses
- Network relationships and shared treasury accounts
- Transaction patterns between creators

Usage:
  python find_creator_connections.py <creator_address>     # Show all connections for a creator
  python find_creator_connections.py --network              # Show entire creator network
  python find_creator_connections.py --treasury             # Show treasury address sharing
  python find_creator_connections.py --all-connections      # Show all creator pairs with shared destinations
"""

import sqlite3
from pathlib import Path
import sys

try:
    from tabulate import tabulate
    HAS_TABULATE = True
except ImportError:
    HAS_TABULATE = False


def get_creator_sol_destinations(creator_address):
    """Get all SOL destination addresses for a creator"""
    db_path = Path(__file__).parent / 'pumpswap_tokens.db'

    if not db_path.exists():
        print(f"❌ Database not found: {db_path}")
        return {}

    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        cursor.execute('''
            SELECT 
                counterparty_address,
                total_amount,
                transfer_count,
                is_treasury,
                first_transfer_timestamp,
                last_transfer_timestamp
            FROM creator_sol_transfers
            WHERE creator_address = ?
            AND transfer_type = 'outgoing'
            ORDER BY total_amount DESC
        ''', (creator_address,))

        destinations = {}
        for addr, amount, count, is_treasury, first_ts, last_ts in cursor.fetchall():
            destinations[addr] = {
                'amount': amount,
                'count': count,
                'is_treasury': is_treasury,
                'first_ts': first_ts,
                'last_ts': last_ts
            }

        conn.close()
        return destinations

    except Exception as e:
        print(f"❌ Error: {e}")
        return {}


def find_creators_sharing_destinations(creator_address):
    """Find all other creators that send to the same addresses"""
    db_path = Path(__file__).parent / 'pumpswap_tokens.db'

    if not db_path.exists():
        print(f"❌ Database not found: {db_path}")
        return {}

    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # Get destinations for the input creator
        cursor.execute('''
            SELECT DISTINCT counterparty_address
            FROM creator_sol_transfers
            WHERE creator_address = ?
            AND transfer_type = 'outgoing'
        ''', (creator_address,))

        destinations = [row[0] for row in cursor.fetchall()]

        if not destinations:
            conn.close()
            return {}

        # Find all other creators that share these destinations
        placeholders = ','.join('?' * len(destinations))
        query = f'''
            SELECT DISTINCT creator_address
            FROM creator_sol_transfers
            WHERE counterparty_address IN ({placeholders})
            AND transfer_type = 'outgoing'
            AND creator_address != ?
        '''

        cursor.execute(query, destinations + [creator_address])
        connected_creators = [row[0] for row in cursor.fetchall()]

        # Build detailed relationship data
        connections = {}
        for connected_creator in connected_creators:
            cursor.execute('''
                SELECT 
                    cst1.counterparty_address,
                    cst1.total_amount,
                    cst1.transfer_count,
                    cst2.total_amount,
                    cst2.transfer_count
                FROM creator_sol_transfers cst1
                JOIN creator_sol_transfers cst2
                ON cst1.counterparty_address = cst2.counterparty_address
                WHERE cst1.creator_address = ?
                AND cst2.creator_address = ?
                AND cst1.transfer_type = 'outgoing'
                AND cst2.transfer_type = 'outgoing'
                ORDER BY cst1.total_amount DESC
            ''', (creator_address, connected_creator))

            shared_destinations = []
            for addr, amt1, cnt1, amt2, cnt2 in cursor.fetchall():
                shared_destinations.append({
                    'address': addr,
                    'creator_amount': amt1,
                    'creator_count': cnt1,
                    'other_amount': amt2,
                    'other_count': cnt2
                })

            if shared_destinations:
                connections[connected_creator] = shared_destinations

        conn.close()
        return connections

    except Exception as e:
        print(f"❌ Error: {e}")
        return {}


def show_creator_connections(creator_address):
    """Display all creators connected through shared SOL destinations"""
    creator_short = f"{creator_address[:8]}...{creator_address[-4:]}"

    print(f"\n{'='*160}")
    print(f"CREATOR CONNECTIONS: {creator_short}")
    print(f"Full Address: {creator_address}")
    print(f"{'='*160}\n")

    connections = find_creators_sharing_destinations(creator_address)

    if not connections:
        print(f"❌ No connected creators found (no shared SOL destinations)")
        return

    print(f"Found {len(connections)} creator(s) sharing SOL destinations:\n")

    for connected_creator, shared_dests in connections.items():
        connected_short = f"{connected_creator[:8]}...{connected_creator[-4:]}"
        print(f"\n{'─'*160}")
        print(f"Connected Creator: {connected_short}")
        print(f"Full Address: {connected_creator}")
        print(f"Shared destinations: {len(shared_dests)}")
        print(f"{'─'*160}\n")

        # Display shared destinations table
        table_data = []
        for dest in shared_dests:
            table_data.append([
                dest['address'],
                f"{dest['creator_amount']:.4f}",
                dest['creator_count'],
                f"{dest['other_amount']:.4f}",
                dest['other_count']
            ])

        if HAS_TABULATE:
            headers = ['Shared Address', f'{creator_short} SOL', f'{creator_short} Count', 
                      f'{connected_short} SOL', f'{connected_short} Count']
            print(tabulate(table_data, headers=headers, tablefmt='grid'))
        else:
            print(f"{'Shared Address':<45} | {creator_short:<15} SOL | Count | {connected_short:<15} SOL | Count")
            print("-" * 140)
            for row in table_data:
                print(f"{row[0]:<45} | {row[1]:<15} | {row[2]:<5} | {row[3]:<15} | {row[4]:<5}")

        print()


def show_all_connections():
    """Show all creator connections in the database"""
    db_path = Path(__file__).parent / 'pumpswap_tokens.db'

    if not db_path.exists():
        print(f"❌ Database not found: {db_path}")
        return

    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # Find all pairs of creators sharing SOL destinations
        cursor.execute('''
            SELECT 
                cst1.creator_address,
                cst2.creator_address,
                COUNT(DISTINCT cst1.counterparty_address) as shared_count,
                SUM(cst1.total_amount) as creator1_total,
                SUM(cst2.total_amount) as creator2_total
            FROM creator_sol_transfers cst1
            JOIN creator_sol_transfers cst2
            ON cst1.counterparty_address = cst2.counterparty_address
            WHERE cst1.creator_address < cst2.creator_address
            AND cst1.transfer_type = 'outgoing'
            AND cst2.transfer_type = 'outgoing'
            GROUP BY cst1.creator_address, cst2.creator_address
            ORDER BY shared_count DESC
        ''')

        rows = cursor.fetchall()
        conn.close()

        if not rows:
            print("No creator connections found")
            return

        print(f"\n{'='*160}")
        print(f"ALL CREATOR CONNECTIONS ({len(rows)} pairs)")
        print(f"{'='*160}\n")

        table_data = []
        for creator1, creator2, shared, total1, total2 in rows:
            creator1_short = f"{creator1[:8]}...{creator1[-4:]}"
            creator2_short = f"{creator2[:8]}...{creator2[-4:]}"
            table_data.append([
                creator1_short,
                creator2_short,
                shared,
                f"{total1:.4f}",
                f"{total2:.4f}"
            ])

        if HAS_TABULATE:
            headers = ['Creator 1', 'Creator 2', 'Shared Destinations', 'Creator 1 Total SOL', 'Creator 2 Total SOL']
            print(tabulate(table_data, headers=headers, tablefmt='grid'))
        else:
            print(f"{'Creator 1':<15} | {'Creator 2':<15} | {'Shared':<8} | {'Creator 1 SOL':<15} | {'Creator 2 SOL':<15}")
            print("-" * 75)
            for row in table_data:
                print(f"{row[0]:<15} | {row[1]:<15} | {row[2]:<8} | {row[3]:<15} | {row[4]:<15}")

        print()

    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
    elif sys.argv[1] == '--all-connections':
        show_all_connections()
    elif sys.argv[1] == '--network':
        show_all_connections()
    else:
        show_creator_connections(sys.argv[1])
