#!/usr/bin/env python3
"""
Manage CEX wallet mappings for funding detection.

Usage:
    python scripts/manage_cex_wallets.py --list                                    # List all CEX wallets
    python scripts/manage_cex_wallets.py --add <address> <exchange> <type>         # Add a new CEX wallet
    python scripts/manage_cex_wallets.py --delete <address>                        # Remove a CEX wallet
"""

import sqlite3
import argparse
import sys
from datetime import datetime

DB_PATH = "pumpswap_tokens.db"

def get_db_connection():
    """Create database connection"""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn

def list_wallets():
    """List all active CEX wallets"""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT cex_address, exchange_name, wallet_type, confidence_level, discovered_date
            FROM cex_wallets
            WHERE is_active = 1
            ORDER BY exchange_name, wallet_type
        """)

        rows = cursor.fetchall()

        if not rows:
            print("No CEX wallets found")
            return

        print(f"\n{'Exchange':<12} {'Type':<20} {'Confidence':<12} {'Address':<48}")
        print("─" * 95)

        for row in rows:
            print(f"{row['exchange_name']:<12} {row['wallet_type']:<20} {row['confidence_level']:>3}%        {row['cex_address']}")

        print(f"\nTotal: {len(rows)} CEX wallets\n")

    finally:
        conn.close()

def add_wallet(address, exchange, wallet_type, confidence=95, source="Manual", notes=""):
    """Add a new CEX wallet"""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT OR REPLACE INTO cex_wallets
            (cex_address, exchange_name, wallet_type, confidence_level, discovered_date, discovery_source, notes, is_active)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?, 1)
        """, (address, exchange, wallet_type, confidence, source, notes))

        conn.commit()
        print(f"✅ Added {exchange} {wallet_type}: {address}")

    finally:
        conn.close()

def delete_wallet(address):
    """Remove a CEX wallet (soft delete)"""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("UPDATE cex_wallets SET is_active = 0 WHERE cex_address = ?", (address,))
        conn.commit()

        if cursor.rowcount > 0:
            print(f"✅ Deactivated wallet: {address}")
        else:
            print(f"❌ Wallet not found: {address}")

    finally:
        conn.close()

def main():
    parser = argparse.ArgumentParser(description="Manage CEX wallet mappings")
    parser.add_argument("--list", action="store_true", help="List all CEX wallets")
    parser.add_argument("--add", nargs="+", help="Add CEX wallet: ADDRESS EXCHANGE TYPE [CONFIDENCE] [SOURCE] [NOTES]")
    parser.add_argument("--delete", type=str, help="Delete CEX wallet by address")

    args = parser.parse_args()

    if args.list:
        list_wallets()
    elif args.add:
        if len(args.add) < 3:
            print("Error: --add requires at least ADDRESS, EXCHANGE, TYPE")
            sys.exit(1)

        address = args.add[0]
        exchange = args.add[1]
        wallet_type = args.add[2]
        confidence = int(args.add[3]) if len(args.add) > 3 else 95
        source = args.add[4] if len(args.add) > 4 else "Manual"
        notes = " ".join(args.add[5:]) if len(args.add) > 5 else ""

        add_wallet(address, exchange, wallet_type, confidence, source, notes)
    elif args.delete:
        delete_wallet(args.delete)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
