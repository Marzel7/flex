#!/usr/bin/env python3
"""
Remove token from database
Usage: python3 utils/remove_token.py <TOKEN_MINT> [--all]

Examples:
  # Remove specific token
  python3 utils/remove_token.py DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263

  # Remove all tokens from database
  python3 utils/remove_token.py --all
"""

import sys
import sqlite3
from pathlib import Path

def remove_token(token_mint: str):
    """Remove a specific token from database"""
    db_path = Path(__file__).parent.parent / 'pumpswap_tokens.db'

    if not db_path.exists():
        print(f"❌ Database not found at {db_path}")
        return False

    try:
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        cursor = conn.cursor()

        # Check if token exists
        cursor.execute('SELECT symbol, name FROM pools WHERE base_mint = ?', (token_mint,))
        result = cursor.fetchone()

        if not result:
            print(f"❌ Token not found: {token_mint}")
            conn.close()
            return False

        symbol, name = result
        display_name = name or symbol or token_mint[:8]

        # Delete token
        cursor.execute('DELETE FROM pools WHERE base_mint = ?', (token_mint,))
        conn.commit()
        conn.close()

        print(f"✓ Removed token: {display_name}")
        print(f"  Mint: {token_mint}")
        return True

    except Exception as e:
        print(f"❌ Error removing token: {e}")
        return False

def remove_all_tokens():
    """Remove all tokens from database"""
    db_path = Path(__file__).parent.parent / 'pumpswap_tokens.db'

    if not db_path.exists():
        print(f"❌ Database not found at {db_path}")
        return False

    try:
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        cursor = conn.cursor()

        # Count tokens before deletion
        cursor.execute('SELECT COUNT(*) FROM pools')
        count = cursor.fetchone()[0]

        if count == 0:
            print("ℹ️  Database is already empty")
            conn.close()
            return True

        # Confirm deletion
        response = input(f"\n⚠️  This will delete {count} tokens from the database. Continue? (y/N): ")
        if response.lower() != 'y':
            print("Cancelled.")
            conn.close()
            return False

        # Delete all tokens
        cursor.execute('DELETE FROM pools')
        conn.commit()
        conn.close()

        print(f"✓ Removed all {count} tokens from database")
        return True

    except Exception as e:
        print(f"❌ Error clearing database: {e}")
        return False

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    arg = sys.argv[1]

    if arg in ['--help', '-h', '--all']:
        if arg in ['--help', '-h']:
            print(__doc__)
        elif arg == '--all':
            remove_all_tokens()
    else:
        remove_token(arg)

if __name__ == "__main__":
    main()
