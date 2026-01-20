#!/usr/bin/env python3
"""
Store known funding accounts that fund token creators.

This script accepts funding account addresses and stores them with the creators they fund.
"""

import sqlite3
import sys
import os

# Change to project directory
os.chdir('/Users/kevinkeaveney/Dev/claude/flex')

DB_PATH = "pumpswap_tokens.db"


def store_funding_account(creator_address: str, funder_address: str, amount_sol: float = None):
    """Store a funding relationship"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=60)
        cursor = conn.cursor()

        # Ensure table exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS creator_funders_manual (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_address TEXT NOT NULL,
                funder_address TEXT NOT NULL,
                amount_sol REAL,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(creator_address, funder_address)
            )
        """)

        # Store the funding relationship
        cursor.execute(
            """INSERT OR REPLACE INTO creator_funders_manual
               (creator_address, funder_address, amount_sol)
               VALUES (?, ?, ?)""",
            (creator_address, funder_address, amount_sol)
        )

        conn.commit()
        conn.close()

        print(f"✅ Stored: {funder_address[:20]}... → {creator_address[:20]}... ({amount_sol} SOL if known)")
        return True

    except Exception as e:
        print(f"❌ Error storing funding account: {e}")
        return False


def list_all_funders():
    """List all stored funding accounts"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=60)
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM creator_funders_manual ORDER BY added_at DESC")
        records = cursor.fetchall()
        conn.close()

        if not records:
            print("No funding accounts stored yet")
            return

        print(f"\n{'='*100}")
        print(f"STORED FUNDING ACCOUNTS ({len(records)} total)")
        print(f"{'='*100}\n")

        for record in records:
            idx, creator, funder, amount, added = record
            print(f"Funder:  {funder}")
            print(f"Creator: {creator}")
            print(f"Amount:  {amount} SOL" if amount else "Amount:  Unknown")
            print(f"Added:   {added}")
            print()

    except Exception as e:
        print(f"Error reading funders: {e}")


if __name__ == "__main__":
    # Add the example funding account we found
    print("[STORING] Funding account we discovered...\n")

    creator = "CQ3k9qYCUjNjyBzxpi3ttiTxZvpaU8QpV9ErfyzVkkqi"
    funder = "8hfTZP4hzPh2bBwMKounGnTzpiYMK7wiyEtrgqVKHhBM"
    amount = 0.50202428

    store_funding_account(creator, funder, amount)

    # Show what we stored
    print("\n")
    list_all_funders()
