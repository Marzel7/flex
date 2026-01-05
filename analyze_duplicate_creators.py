#!/usr/bin/env python3
"""
Analyze duplicate creators and their token performance.

Shows:
- Peak % reached for each token
- Time to reach peak (from first_seen to peak_time)
- Comparison between tokens from same creator
"""

import sqlite3
from pathlib import Path
from datetime import datetime
import sys
import os

# Load environment variables from .env file
env_path = Path(__file__).parent / '.env'
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key.strip()] = value.strip()

try:
    from tabulate import tabulate
    HAS_TABULATE = True
except ImportError:
    HAS_TABULATE = False


def migrate_database():
    """Add peak_time column if it doesn't exist"""
    db_path = Path(__file__).parent / 'pumpswap_tokens.db'

    if not db_path.exists():
        return

    try:
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        cursor = conn.cursor()

        # Check if peak_time column exists
        cursor.execute("PRAGMA table_info(pools)")
        columns = {col[1] for col in cursor.fetchall()}

        if 'peak_time' not in columns:
            print("[DB] Adding peak_time column...")
            cursor.execute('ALTER TABLE pools ADD COLUMN peak_time TIMESTAMP')
            conn.commit()
            print("[DB] ✓ Column added")

        conn.close()
    except Exception as e:
        print(f"[DB] Note: {e}")


def get_duplicate_creators():
    """Get creators with multiple tokens"""
    db_path = Path(__file__).parent / 'pumpswap_tokens.db'

    if not db_path.exists():
        print(f"❌ Database not found: {db_path}")
        return {}

    try:
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        cursor = conn.cursor()

        # Get creators with multiple tokens
        cursor.execute('''
            SELECT pumpfun_creator, COUNT(*) as count
            FROM pools
            WHERE pumpfun_creator IS NOT NULL AND pumpfun_creator != ''
            GROUP BY pumpfun_creator
            HAVING count > 1
            ORDER BY count DESC
        ''')

        duplicate_creators = {}
        for creator, count in cursor.fetchall():
            # Get all tokens for this creator
            cursor.execute('''
                SELECT
                    base_mint,
                    symbol,
                    pumpfun_symbol,
                    first_seen,
                    peak_percent_change,
                    peak_time,
                    buy_price_usd,
                    buy_time,
                    current_price_usd,
                    trade_status,
                    peak_price_usd
                FROM pools
                WHERE pumpfun_creator = ?
                ORDER BY first_seen DESC
            ''', (creator,))

            tokens = cursor.fetchall()
            duplicate_creators[creator] = tokens

        conn.close()
        return duplicate_creators

    except Exception as e:
        print(f"❌ Error: {e}")
        return {}


def parse_timestamp(ts_str):
    """Parse timestamp string to datetime"""
    if not ts_str:
        return None

    try:
        # Try parsing ISO format
        if 'T' in str(ts_str):
            return datetime.fromisoformat(str(ts_str).replace('Z', '+00:00'))
        else:
            # Try parsing as float (unix timestamp)
            return datetime.fromtimestamp(float(ts_str) / 1000)
    except:
        return None


def calculate_time_to_peak(first_seen, peak_time):
    """Calculate time difference between first_seen and peak_time"""
    t1 = parse_timestamp(first_seen)
    t2 = parse_timestamp(peak_time)

    if not t1 or not t2:
        return None

    diff = t2 - t1
    total_seconds = diff.total_seconds()

    if total_seconds < 0:
        return None

    minutes = total_seconds / 60
    if minutes < 60:
        return f"{int(minutes)}m"
    else:
        hours = minutes / 60
        if hours < 24:
            return f"{hours:.1f}h"
        else:
            days = hours / 24
            return f"{days:.1f}d"


def analyze_duplicate_creators():
    """Analyze and display duplicate creators and their tokens"""
    migrate_database()

    duplicate_creators = get_duplicate_creators()

    if not duplicate_creators:
        print("\nNo duplicate creators found!")
        return

    print(f"\n{'='*140}")
    print(f"DUPLICATE CREATORS ANALYSIS ({len(duplicate_creators)} creators with multiple tokens)")
    print(f"{'='*140}\n")

    total_multi_token_creators = len(duplicate_creators)
    total_tokens_from_dup_creators = sum(len(tokens) for tokens in duplicate_creators.values())

    print(f"Summary:")
    print(f"  Multi-token creators: {total_multi_token_creators}")
    print(f"  Total tokens from duplicates: {total_tokens_from_dup_creators}")
    print()

    for creator, tokens in duplicate_creators.items():
        creator_short = f"{creator[:8]}...{creator[-4:]}"
        print(f"\n{'─'*140}")
        print(f"Creator: {creator_short} ({creator})")
        print(f"Tokens: {len(tokens)}")
        print(f"{'─'*140}\n")

        data = []
        traded_count = 0
        peaked_count = 0

        for token_info in tokens:
            (mint, symbol, pf_symbol, first_seen, peak_pct, peak_time,
             buy_price, buy_time, curr_price, status, peak_price) = token_info

            # Get display name
            display_name = symbol or pf_symbol or mint[:6]

            # Calculate time to peak
            time_to_peak = calculate_time_to_peak(first_seen, peak_time)

            # Format peak percent
            peak_pct_str = f"{peak_pct:.2f}%" if peak_pct and peak_pct > 0 else "—"
            if peak_pct and peak_pct > 0:
                peaked_count += 1

            # Track if traded
            if buy_price:
                traded_count += 1

            data.append([
                display_name,
                mint,  # Show full mint address
                peak_pct_str,
                time_to_peak or "—",
                status or "—",
                f"${peak_price:.2f}" if peak_price else "—"
            ])

        if HAS_TABULATE:
            headers = ['Token', 'Mint', 'Peak %', 'Time to Peak', 'Status', 'Peak Price']
            print(tabulate(data, headers=headers, tablefmt='grid'))
        else:
            headers = ['Token', 'Mint', 'Peak %', 'Time to Peak', 'Status', 'Peak Price']
            print(f"{'Token':<12} | {'Mint':<44} | {'Peak %':<10} | {'Time to Peak':<15} | {'Status':<12} | {'Peak Price':<12}")
            print("-" * 130)
            for row in data:
                print(f"{row[0]:<12} | {row[1]:<44} | {row[2]:<10} | {row[3]:<15} | {row[4]:<12} | {row[5]:<12}")

        print(f"\n  Tokens traded: {traded_count}/{len(tokens)}")
        print(f"  Tokens with peak data: {peaked_count}/{len(tokens)}")

    print(f"\n{'='*140}")


if __name__ == '__main__':
    analyze_duplicate_creators()
