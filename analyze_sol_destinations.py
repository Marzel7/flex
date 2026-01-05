#!/usr/bin/env python3
"""
Analyze SOL destination addresses and find which creators use them.

Shows:
- Destination addresses and how many creators use them
- Frequency of each destination (how many creators send to it)
- Treasury account detection (addresses receiving from multiple creators)
- Potential money laundering or fund aggregation patterns

Usage:
  python analyze_sol_destinations.py                    # Show all destinations and their usage
  python analyze_sol_destinations.py --frequent         # Show most frequently used destinations
  python analyze_sol_destinations.py --suspicious       # Show potential treasury/aggregation addresses
  python analyze_sol_destinations.py <destination_addr> # Show all creators sending to address
"""

import sqlite3
from pathlib import Path
import sys

try:
    from tabulate import tabulate
    HAS_TABULATE = True
except ImportError:
    HAS_TABULATE = False


def get_destination_stats():
    """Get statistics about all SOL destination addresses"""
    db_path = Path(__file__).parent / 'pumpswap_tokens.db'

    if not db_path.exists():
        print(f"❌ Database not found: {db_path}")
        return {}

    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # Get all destinations and count how many creators use them
        cursor.execute('''
            SELECT 
                counterparty_address,
                COUNT(DISTINCT creator_address) as creator_count,
                SUM(total_amount) as total_sol,
                SUM(transfer_count) as total_transfers,
                MAX(is_treasury) as flagged_as_treasury
            FROM creator_sol_transfers
            WHERE transfer_type = 'outgoing'
            GROUP BY counterparty_address
            ORDER BY creator_count DESC, total_sol DESC
        ''')

        destinations = {}
        for addr, creator_count, total_sol, total_txs, is_treasury in cursor.fetchall():
            destinations[addr] = {
                'creator_count': creator_count,
                'total_sol': total_sol,
                'total_transfers': total_txs,
                'is_treasury': is_treasury
            }

        conn.close()
        return destinations

    except Exception as e:
        print(f"❌ Error: {e}")
        return {}


def find_creators_for_destination(destination_addr):
    """Find all creators that send SOL to a specific address"""
    db_path = Path(__file__).parent / 'pumpswap_tokens.db'

    if not db_path.exists():
        print(f"❌ Database not found: {db_path}")
        return []

    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        cursor.execute('''
            SELECT 
                creator_address,
                total_amount,
                transfer_count,
                is_treasury,
                first_transfer_timestamp,
                last_transfer_timestamp
            FROM creator_sol_transfers
            WHERE counterparty_address = ?
            AND transfer_type = 'outgoing'
            ORDER BY total_amount DESC
        ''', (destination_addr,))

        creators = []
        for creator, amount, count, is_treasury, first_ts, last_ts in cursor.fetchall():
            creators.append({
                'creator': creator,
                'amount': amount,
                'count': count,
                'is_treasury': is_treasury,
                'first_ts': first_ts,
                'last_ts': last_ts
            })

        conn.close()
        return creators

    except Exception as e:
        print(f"❌ Error: {e}")
        return []


def show_all_destinations():
    """Display all SOL destinations with creator usage stats"""
    destinations = get_destination_stats()

    if not destinations:
        print("No destinations found")
        return

    print(f"\n{'='*160}")
    print(f"ALL SOL DESTINATIONS ({len(destinations)} total)")
    print(f"{'='*160}\n")

    table_data = []
    for addr, stats in destinations.items():
        treasury = "🏦" if stats['is_treasury'] else ""
        table_data.append([
            addr,
            stats['creator_count'],
            f"{stats['total_sol']:.4f}",
            stats['total_transfers'],
            treasury
        ])

    if HAS_TABULATE:
        headers = ['Destination Address', 'Used by Creators', 'Total SOL', 'Total Transfers', 'Treasury']
        print(tabulate(table_data, headers=headers, tablefmt='grid'))
    else:
        print(f"{'Destination':<45} | {'Creators':<10} | {'Total SOL':<15} | {'Transfers':<12} | Treasury")
        print("-" * 90)
        for row in table_data:
            print(f"{row[0]:<45} | {row[1]:<10} | {row[2]:<15} | {row[3]:<12} | {row[4]}")

    print()


def show_frequent_destinations():
    """Show destinations used by multiple creators (potential aggregation points)"""
    destinations = get_destination_stats()
    
    if not destinations:
        print("No destinations found")
        return

    # Filter to only those used by 2+ creators
    frequent = {k: v for k, v in destinations.items() if v['creator_count'] > 1}

    if not frequent:
        print(f"\n{'='*160}")
        print("NO SHARED DESTINATIONS FOUND")
        print("(No destination addresses are used by multiple creators yet)")
        print(f"{'='*160}\n")
        return

    print(f"\n{'='*160}")
    print(f"SHARED DESTINATIONS ({len(frequent)} used by multiple creators)")
    print(f"{'='*160}\n")

    table_data = []
    for addr, stats in sorted(frequent.items(), key=lambda x: x[1]['creator_count'], reverse=True):
        treasury = "🏦 TREASURY" if stats['is_treasury'] else ""
        table_data.append([
            addr,
            stats['creator_count'],
            f"{stats['total_sol']:.4f}",
            stats['total_transfers'],
            treasury
        ])

    if HAS_TABULATE:
        headers = ['Destination Address', 'Creator Count', 'Total SOL', 'Total Transfers', 'Flag']
        print(tabulate(table_data, headers=headers, tablefmt='grid'))
    else:
        print(f"{'Destination':<45} | {'Creators':<8} | {'Total SOL':<12} | {'Transfers':<10} | Flag")
        print("-" * 85)
        for row in table_data:
            print(f"{row[0]:<45} | {row[1]:<8} | {row[2]:<12} | {row[3]:<10} | {row[4]}")

    print()


def show_creators_for_destination(destination_addr):
    """Show all creators sending to a specific address"""
    creators = find_creators_for_destination(destination_addr)

    if not creators:
        print(f"❌ No creators found sending to: {destination_addr}")
        return

    print(f"\n{'='*160}")
    print(f"CREATORS SENDING TO: {destination_addr}")
    print(f"{'='*160}\n")

    print(f"Total creators: {len(creators)}\n")

    table_data = []
    for creator_info in creators:
        creator_short = f"{creator_info['creator'][:8]}...{creator_info['creator'][-4:]}"
        treasury = "🏦" if creator_info['is_treasury'] else ""
        table_data.append([
            creator_short,
            creator_info['creator'],
            f"{creator_info['amount']:.4f}",
            creator_info['count'],
            treasury
        ])

    if HAS_TABULATE:
        headers = ['Creator (Short)', 'Full Address', 'SOL Amount', 'Transfers', 'Flag']
        print(tabulate(table_data, headers=headers, tablefmt='grid'))
    else:
        print(f"{'Creator':<15} | {'Full Address':<45} | {'SOL':<12} | {'Count':<8} | Flag")
        print("-" * 90)
        for row in table_data:
            print(f"{row[0]:<15} | {row[1]:<45} | {row[2]:<12} | {row[3]:<8} | {row[4]}")

    print()


if __name__ == '__main__':
    if len(sys.argv) < 2:
        show_all_destinations()
    elif sys.argv[1] == '--frequent':
        show_frequent_destinations()
    elif sys.argv[1] == '--suspicious':
        show_frequent_destinations()
    elif sys.argv[1] in ['-h', '--help']:
        print(__doc__)
    else:
        show_creators_for_destination(sys.argv[1])
