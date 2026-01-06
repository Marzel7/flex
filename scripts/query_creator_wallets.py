#!/usr/bin/env python3
"""
Query stored creator wallet data and SOL transfer accounts.

Usage:
  python query_creator_wallets.py <creator_address>     # Show wallet summary + transfers
  python query_creator_wallets.py --list                # List all analyzed creators
  python query_creator_wallets.py --recent              # Show recently analyzed creators
  python query_creator_wallets.py --treasury            # Show creators with treasury accounts
"""

import sqlite3
from pathlib import Path
import sys
from datetime import datetime

try:
    from tabulate import tabulate
    HAS_TABULATE = True
except ImportError:
    HAS_TABULATE = False


def list_analyzed_creators():
    """List all creators in the database"""
    db_path = Path(__file__).parent / 'pumpswap_tokens.db'

    if not db_path.exists():
        print("❌ Database not found")
        return

    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        cursor.execute('''
            SELECT 
                creator_address,
                account_age_days,
                total_transactions,
                total_sol_out,
                last_analyzed
            FROM creator_wallets
            ORDER BY last_analyzed DESC
        ''')

        rows = cursor.fetchall()
        conn.close()

        if not rows:
            print("No creators analyzed yet")
            return

        print(f"\n{'='*140}")
        print(f"ANALYZED CREATORS ({len(rows)} total)")
        print(f"{'='*140}\n")

        data = []
        for creator, age, txs, sol_out, analyzed in rows:
            analyzed_date = datetime.fromisoformat(analyzed).strftime('%Y-%m-%d %H:%M:%S') if analyzed else 'unknown'
            data.append([creator, age, txs, f"{sol_out:.4f} SOL", analyzed_date])

        if HAS_TABULATE:
            headers = ['Creator Address', 'Age (days)', 'Transactions', 'SOL Out', 'Last Analyzed']
            print(tabulate(data, headers=headers, tablefmt='grid'))
        else:
            print(f"{'Creator Address':<45} | {'Age':<10} | {'Txs':<8} | {'SOL Out':<12} | {'Last Analyzed':<20}")
            print("-" * 140)
            for row in data:
                print(f"{row[0]:<45} | {row[1]:<10} | {row[2]:<8} | {row[3]:<12} | {row[4]:<20}")

    except Exception as e:
        print(f"❌ Error: {e}")


def query_creator_wallet(creator_address):
    """Query wallet data for a specific creator"""
    db_path = Path(__file__).parent / 'pumpswap_tokens.db'

    if not db_path.exists():
        print("❌ Database not found")
        return

    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # Get wallet summary
        cursor.execute('''
            SELECT 
                creator_address,
                account_age_days,
                first_transaction_timestamp,
                total_transactions,
                swap_count,
                transfer_count,
                total_sol_in,
                total_sol_out,
                net_sol_position,
                unique_wallet_interactions,
                last_analyzed
            FROM creator_wallets
            WHERE creator_address = ?
        ''', (creator_address,))

        wallet = cursor.fetchone()

        if not wallet:
            print(f"❌ No data found for creator: {creator_address}")
            conn.close()
            return

        creator_short = f"{wallet[0][:8]}...{wallet[0][-4:]}"

        print(f"\n{'='*160}")
        print(f"CREATOR WALLET SUMMARY: {creator_short}")
        print(f"{'='*160}\n")

        print(f"Address: {wallet[0]}")
        print(f"Account age: {wallet[1]} days")
        print(f"First transaction: {wallet[2]}")
        print(f"Total transactions: {wallet[3]}")
        print(f"  - Swaps: {wallet[4]} ({wallet[4]/wallet[3]*100:.1f}%)" if wallet[3] > 0 else "  - Swaps: 0")
        print(f"  - Transfers: {wallet[5]}")
        print(f"Unique wallet interactions: {wallet[9]}")
        print()
        print(f"SOL Flow:")
        print(f"  - Received: {wallet[6]:.4f} SOL")
        print(f"  - Sent: {wallet[7]:.4f} SOL")
        print(f"  - Net position: {wallet[8]:+.4f} SOL")
        print(f"Last analyzed: {wallet[10]}")
        print()

        # Get outgoing transfers
        print("OUTGOING SOL TRANSFERS")
        print("-" * 160)

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

        transfers = cursor.fetchall()

        if transfers:
            print(f"Total destinations: {len(transfers)}\n")

            data = []
            for addr, amount, count, is_treasury, first_ts, last_ts in transfers:
                treasury = "🏦 Treasury" if is_treasury else ""
                data.append([
                    addr,
                    f"{amount:.4f}",
                    count,
                    treasury,
                    first_ts,
                    last_ts
                ])

            if HAS_TABULATE:
                headers = ['Address', 'SOL Amount', 'Transfers', 'Type', 'First TX', 'Last TX']
                print(tabulate(data, headers=headers, tablefmt='grid'))
            else:
                print(f"{'Address':<45} | {'SOL':<12} | {'Count':<8} | {'Type':<12} | {'First':<15} | {'Last':<15}")
                print("-" * 160)
                for row in data:
                    print(f"{row[0]:<45} | {row[1]:<12} | {row[2]:<8} | {row[3]:<12} | {row[4]:<15} | {row[5]:<15}")
        else:
            print("No outgoing transfers found")

        print()

        # Get incoming transfers
        print("INCOMING SOL TRANSFERS")
        print("-" * 160)

        cursor.execute('''
            SELECT 
                counterparty_address,
                total_amount,
                transfer_count,
                first_transfer_timestamp
            FROM creator_sol_transfers
            WHERE creator_address = ?
            AND transfer_type = 'incoming'
            ORDER BY total_amount DESC
        ''', (creator_address,))

        transfers = cursor.fetchall()

        if transfers:
            print(f"Total sources: {len(transfers)}\n")

            data = []
            for addr, amount, count, first_ts in transfers:
                data.append([
                    addr,
                    f"{amount:.4f}",
                    count,
                    first_ts
                ])

            if HAS_TABULATE:
                headers = ['Address', 'SOL Amount', 'Transfers', 'First TX']
                print(tabulate(data, headers=headers, tablefmt='grid'))
            else:
                print(f"{'Address':<45} | {'SOL':<12} | {'Count':<8} | {'First TX':<15}")
                print("-" * 160)
                for row in data:
                    print(f"{row[0]:<45} | {row[1]:<12} | {row[2]:<8} | {row[3]:<15}")
        else:
            print("No incoming transfers found (⚠️  Suspicious - no funding source)")

        print()

        conn.close()

    except Exception as e:
        print(f"❌ Error: {e}")


def show_treasury_creators():
    """Show creators with treasury accounts (multiple transfers to same address)"""
    db_path = Path(__file__).parent / 'pumpswap_tokens.db'

    if not db_path.exists():
        print("❌ Database not found")
        return

    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        cursor.execute('''
            SELECT DISTINCT
                creator_address,
                (SELECT SUM(total_sol_out) FROM creator_wallets WHERE creator_address = cst.creator_address),
                COUNT(DISTINCT counterparty_address)
            FROM creator_sol_transfers cst
            WHERE transfer_type = 'outgoing'
            AND is_treasury = 1
            GROUP BY creator_address
        ''')

        rows = cursor.fetchall()
        conn.close()

        if not rows:
            print("No creators with treasury accounts found")
            return

        print(f"\n{'='*140}")
        print(f"CREATORS WITH TREASURY ACCOUNTS ({len(rows)} detected)")
        print(f"{'='*140}\n")

        data = []
        for creator, total_out, num_treasury in rows:
            data.append([creator, f"{total_out:.4f} SOL", num_treasury])

        if HAS_TABULATE:
            headers = ['Creator Address', 'Total SOL Out', 'Treasury Accounts']
            print(tabulate(data, headers=headers, tablefmt='grid'))
        else:
            print(f"{'Creator Address':<45} | {'SOL Out':<15} | {'Treasury Accounts':<20}")
            print("-" * 140)
            for row in data:
                print(f"{row[0]:<45} | {row[1]:<15} | {row[2]:<20}")

    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
    elif sys.argv[1] == '--list':
        list_analyzed_creators()
    elif sys.argv[1] == '--recent':
        list_analyzed_creators()
    elif sys.argv[1] == '--treasury':
        show_treasury_creators()
    else:
        query_creator_wallet(sys.argv[1])

