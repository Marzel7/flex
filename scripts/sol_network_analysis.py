#!/usr/bin/env python3
"""
Comprehensive SOL transfer network analysis.

Analyzes:
- Creator → Destination address flows
- Destination → Creator reverse relationships
- Network clustering (which creators/addresses are interconnected)
- Potential Treasury addresses (receive from multiple sources)
- Fund aggregation patterns

Usage:
  python sol_network_analysis.py                      # Show network summary
  python sol_network_analysis.py <destination>        # Show all paths to a destination
  python sol_network_analysis.py --aggregation        # Find aggregation addresses
  python sol_network_analysis.py --export             # Export network graph data
"""

import sqlite3
from pathlib import Path
import sys
from collections import defaultdict

try:
    from tabulate import tabulate
    HAS_TABULATE = True
except ImportError:
    HAS_TABULATE = False


def get_network_summary():
    """Get overall network statistics"""
    db_path = Path(__file__).parent / 'pumpswap_tokens.db'

    if not db_path.exists():
        return None

    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # Count creators
        cursor.execute('SELECT COUNT(DISTINCT creator_address) FROM creator_sol_transfers WHERE transfer_type = "outgoing"')
        creator_count = cursor.fetchone()[0]

        # Count destinations
        cursor.execute('SELECT COUNT(DISTINCT counterparty_address) FROM creator_sol_transfers WHERE transfer_type = "outgoing"')
        destination_count = cursor.fetchone()[0]

        # Total SOL
        cursor.execute('SELECT SUM(total_amount) FROM creator_sol_transfers WHERE transfer_type = "outgoing"')
        total_sol = cursor.fetchone()[0] or 0

        # Count connections
        cursor.execute('SELECT COUNT(*) FROM creator_sol_transfers WHERE transfer_type = "outgoing"')
        edge_count = cursor.fetchone()[0]

        # Find aggregation hubs (destinations with multiple sources)
        cursor.execute('''
            SELECT COUNT(*) FROM (
                SELECT counterparty_address, COUNT(DISTINCT creator_address) as sources
                FROM creator_sol_transfers
                WHERE transfer_type = "outgoing"
                GROUP BY counterparty_address
                HAVING sources > 1
            )
        ''')
        aggregation_hubs = cursor.fetchone()[0]

        conn.close()

        return {
            'creators': creator_count,
            'destinations': destination_count,
            'total_sol': total_sol,
            'edges': edge_count,
            'aggregation_hubs': aggregation_hubs
        }

    except Exception as e:
        print(f"❌ Error: {e}")
        return None


def show_network_summary():
    """Display network statistics"""
    stats = get_network_summary()

    if not stats:
        return

    print(f"\n{'='*160}")
    print("SOL TRANSFER NETWORK SUMMARY")
    print(f"{'='*160}\n")

    print(f"Network Size:")
    print(f"  Creators (sources): {stats['creators']}")
    print(f"  Destinations (sinks): {stats['destinations']}")
    print(f"  Total connections: {stats['edges']}")
    print()
    print(f"Fund Flow:")
    print(f"  Total SOL transferred: {stats['total_sol']:.4f}")
    print(f"  Average per creator: {stats['total_sol']/max(1, stats['creators']):.4f}")
    print()
    print(f"Network Topology:")
    print(f"  Aggregation hubs (destinations with multiple sources): {stats['aggregation_hubs']}")
    print()


def get_path_to_destination(destination_addr):
    """Find all creators sending to a destination"""
    db_path = Path(__file__).parent / 'pumpswap_tokens.db'

    if not db_path.exists():
        return []

    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        cursor.execute('''
            SELECT 
                creator_address,
                total_amount,
                transfer_count,
                is_treasury
            FROM creator_sol_transfers
            WHERE counterparty_address = ?
            AND transfer_type = 'outgoing'
            ORDER BY total_amount DESC
        ''', (destination_addr,))

        paths = []
        for creator, amount, count, is_treasury in cursor.fetchall():
            paths.append({
                'creator': creator,
                'amount': amount,
                'count': count,
                'is_treasury': is_treasury
            })

        conn.close()
        return paths

    except Exception as e:
        print(f"❌ Error: {e}")
        return []


def show_destination_paths(destination_addr):
    """Show all creators sending to a destination"""
    paths = get_path_to_destination(destination_addr)

    if not paths:
        print(f"❌ No creators found sending to: {destination_addr}")
        return

    print(f"\n{'='*160}")
    print(f"INCOMING PATHS TO: {destination_addr}")
    print(f"{'='*160}\n")

    print(f"Total sources: {len(paths)}\n")

    table_data = []
    for path in paths:
        creator_short = f"{path['creator'][:8]}...{path['creator'][-4:]}"
        treasury = "🏦" if path['is_treasury'] else ""
        table_data.append([
            creator_short,
            path['creator'],
            f"{path['amount']:.4f}",
            path['count'],
            treasury
        ])

    if HAS_TABULATE:
        headers = ['Creator', 'Full Address', 'SOL Sent', 'Transfers', 'Flag']
        print(tabulate(table_data, headers=headers, tablefmt='grid'))
    else:
        print(f"{'Creator':<15} | {'Full Address':<45} | {'SOL':<12} | {'Count':<8} | Flag")
        print("-" * 90)
        for row in table_data:
            print(f"{row[0]:<15} | {row[1]:<45} | {row[2]:<12} | {row[3]:<8} | {row[4]}")

    print()


def find_aggregation_hubs():
    """Find destinations receiving from multiple creators"""
    db_path = Path(__file__).parent / 'pumpswap_tokens.db'

    if not db_path.exists():
        print(f"❌ Database not found")
        return

    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        cursor.execute('''
            SELECT 
                counterparty_address,
                COUNT(DISTINCT creator_address) as sources,
                SUM(total_amount) as total_sol,
                SUM(transfer_count) as total_transfers
            FROM creator_sol_transfers
            WHERE transfer_type = "outgoing"
            GROUP BY counterparty_address
            HAVING sources > 1
            ORDER BY sources DESC, total_sol DESC
        ''')

        hubs = cursor.fetchall()
        conn.close()

        if not hubs:
            print(f"\n{'='*160}")
            print("NO AGGREGATION HUBS FOUND")
            print("(No destinations receive from multiple creators yet)")
            print(f"{'='*160}\n")
            return

        print(f"\n{'='*160}")
        print(f"AGGREGATION HUBS ({len(hubs)} destinations with multiple sources)")
        print(f"{'='*160}\n")

        table_data = []
        for addr, sources, total_sol, total_txs in hubs:
            table_data.append([
                addr,
                sources,
                f"{total_sol:.4f}",
                total_txs
            ])

        if HAS_TABULATE:
            headers = ['Address', 'Source Creators', 'Total SOL', 'Total Transfers']
            print(tabulate(table_data, headers=headers, tablefmt='grid'))
        else:
            print(f"{'Address':<45} | {'Sources':<8} | {'Total SOL':<12} | {'Transfers':<10}")
            print("-" * 80)
            for row in table_data:
                print(f"{row[0]:<45} | {row[1]:<8} | {row[2]:<12} | {row[3]:<10}")

        print()

    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        show_network_summary()
    elif sys.argv[1] == '--aggregation':
        find_aggregation_hubs()
    elif sys.argv[1] in ['-h', '--help']:
        print(__doc__)
    else:
        show_destination_paths(sys.argv[1])
