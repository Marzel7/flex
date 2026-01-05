#!/usr/bin/env python3
"""
Query and display PumpFun creator info for all tokens in database.

Usage:
  python get_token_creators.py          # Show all with creators
  python get_token_creators.py all      # Show all tokens
  python get_token_creators.py missing  # Show only missing creators
"""

import sys
import sqlite3
from pathlib import Path
from tabulate import tabulate


def get_token_creators(filter_type='with_creator'):
    """Get creator info for tokens"""
    db_path = Path(__file__).parent / 'pumpswap_tokens.db'

    if not db_path.exists():
        print(f"❌ Database not found: {db_path}")
        return

    try:
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        cursor = conn.cursor()

        if filter_type == 'with_creator':
            cursor.execute('''
                SELECT base_mint, symbol, name, pumpfun_creator, pumpfun_symbol, trade_status
                FROM pools
                WHERE pumpfun_creator IS NOT NULL AND pumpfun_creator != ''
                ORDER BY first_seen DESC
            ''')
            title = "Tokens WITH Creator Info"
        elif filter_type == 'missing':
            cursor.execute('''
                SELECT base_mint, symbol, name, trade_status
                FROM pools
                WHERE pumpfun_creator IS NULL OR pumpfun_creator = ''
                ORDER BY first_seen DESC
            ''')
            title = "Tokens MISSING Creator Info"
        else:  # all
            cursor.execute('''
                SELECT base_mint, symbol, name, pumpfun_creator, pumpfun_symbol, trade_status
                FROM pools
                ORDER BY first_seen DESC
            ''')
            title = "All Tokens"

        rows = cursor.fetchall()
        conn.close()

        if not rows:
            print(f"No tokens found matching filter: {filter_type}")
            return

        print(f"\n{'='*120}")
        print(f"{title} ({len(rows)} total)")
        print(f"{'='*120}\n")

        if filter_type == 'missing':
            headers = ['Token Mint', 'Symbol', 'Name', 'Status']
            data = []
            for mint, symbol, name, status in rows:
                data.append([
                    mint[:16] + '...' if len(mint) > 16 else mint,
                    symbol or '—',
                    name or '—',
                    status or '—'
                ])
        else:
            headers = ['Token Mint', 'Symbol', 'Name', 'Creator Address', 'PumpFun Symbol', 'Status']
            data = []
            for mint, symbol, name, creator, pf_symbol, status in rows:
                creator_display = f"{creator[:8]}...{creator[-4:]}" if creator and len(creator) > 12 else (creator or '—')
                data.append([
                    mint[:16] + '...' if len(mint) > 16 else mint,
                    symbol or '—',
                    name or '—',
                    creator_display,
                    pf_symbol or '—',
                    status or '—'
                ])

        print(tabulate(data, headers=headers, tablefmt='grid'))
        print(f"\n{'='*120}")

    except ImportError:
        # Fallback if tabulate not installed
        print("Note: Install 'tabulate' for better formatting: pip install tabulate")
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        cursor = conn.cursor()

        if filter_type == 'with_creator':
            cursor.execute('''
                SELECT base_mint, symbol, name, pumpfun_creator, pumpfun_symbol, trade_status
                FROM pools
                WHERE pumpfun_creator IS NOT NULL AND pumpfun_creator != ''
                ORDER BY first_seen DESC
            ''')
        elif filter_type == 'missing':
            cursor.execute('''
                SELECT base_mint, symbol, name, trade_status
                FROM pools
                WHERE pumpfun_creator IS NULL OR pumpfun_creator = ''
                ORDER BY first_seen DESC
            ''')
        else:
            cursor.execute('''
                SELECT base_mint, symbol, name, pumpfun_creator, pumpfun_symbol, trade_status
                FROM pools
                ORDER BY first_seen DESC
            ''')

        rows = cursor.fetchall()
        conn.close()

        print(f"\nTotal: {len(rows)} tokens\n")
        for row in rows:
            if filter_type == 'missing':
                mint, symbol, name, status = row
                print(f"{mint[:16]}... | {symbol or '—':<12} | {name or '—':<20} | {status}")
            else:
                mint, symbol, name, creator, pf_symbol, status = row
                creator_short = f"{creator[:8]}...{creator[-4:]}" if creator and len(creator) > 12 else (creator or '—')
                print(f"{mint[:16]}... | {symbol or '—':<12} | {name or '—':<20} | {creator_short:<20} | {pf_symbol or '—':<12} | {status}")

    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == '__main__':
    filter_type = 'with_creator'  # default
    if len(sys.argv) > 1:
        filter_type = sys.argv[1].lower()
        if filter_type not in ['all', 'missing', 'with_creator']:
            print("Usage: python get_token_creators.py [all|missing|with_creator]")
            sys.exit(1)

    get_token_creators(filter_type)
