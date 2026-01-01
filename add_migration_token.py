#!/usr/bin/env python3
"""
Utility script to manually add a migration token to the database for tracking.

This is useful when:
- You have a transaction signature that is a valid migration
- The token didn't get picked up by the automatic listener
- You want to add it to the database manually

Usage:
    python add_migration_token.py <TOKEN_MINT> <SIGNATURE>

Example:
    python add_migration_token.py 223qgQtUny3WQXwwG2Wfx57tHsb7gHAuyHrH9BqNpump \
        45wUS6jtk8PhxFngetFxMXma5WnqEjDBn1rDQYiSgDtc2sNtUw7WCH8qn7ZjLNUz532d59wGjHMzSVsGd4rihFJ2
"""

import sys
import sqlite3
import os
from datetime import datetime
from pathlib import Path

def add_migration_to_db(token_mint: str, signature: str):
    """Add a migration token to the database"""
    db_path = Path(__file__).parent / 'pumpswap_tokens.db'

    if not db_path.exists():
        print(f"[ERROR] Database not found at {db_path}")
        return False

    try:
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        cursor = conn.cursor()

        # Check if token already exists
        cursor.execute('SELECT id FROM pools WHERE base_mint = ?', (token_mint,))
        existing = cursor.fetchone()

        if existing:
            print(f"[INFO] Token already in database (ID: {existing[0]})")
            # Ensure it's marked as PumpSwap
            cursor.execute(
                'UPDATE pools SET is_pumpswap = 1, signature = ? WHERE base_mint = ?',
                (signature, token_mint)
            )
            print(f"[✓] Updated PumpSwap flag and signature")
        else:
            # Insert new token
            cursor.execute('''
                INSERT INTO pools (
                    base_mint, signature, is_pumpswap, first_seen, last_updated, amm_id
                ) VALUES (?, ?, 1, ?, ?, ?)
            ''', (token_mint, signature, datetime.now(), datetime.now(), token_mint))
            print(f"[✓] Token added to database")

        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] Could not add token to database: {e}")
        return False

if __name__ == '__main__':
    if len(sys.argv) != 3:
        print("Usage: python add_migration_token.py <TOKEN_MINT> <SIGNATURE>")
        print("\nExample:")
        print("  python add_migration_token.py 223qgQtUny3WQXwwG2Wfx57tHsb7gHAuyHrH9BqNpump \\")
        print("    45wUS6jtk8PhxFngetFxMXma5WnqEjDBn1rDQYiSgDtc2sNtUw7WCH8qn7ZjLNUz532d59wGjHMzSVsGd4rihFJ2")
        sys.exit(1)

    token_mint = sys.argv[1]
    signature = sys.argv[2]

    print(f"Adding token: {token_mint}")
    print(f"Signature: {signature}")

    if add_migration_to_db(token_mint, signature):
        print(f"\n[SUCCESS] Token added and ready for price tracking")
        print(f"\nTo view the token's price, run:")
        print(f"  python get_price_from_pools.py {token_mint}")
    else:
        print(f"\n[FAILED] Could not add token to database")
        sys.exit(1)
