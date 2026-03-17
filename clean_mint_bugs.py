#!/usr/bin/env python3
"""
Clean up pools with MINT bug by:
1. Finding all pools where quote_account = wSOL MINT
2. Deleting them so vault discovery can re-discover with correct accounts
3. This forces fresh discovery on next listener run

After this, the listener will re-discover the pools with REAL accounts.
"""

import sqlite3
import sys

DB_PATH = "database/flex_complete_database.db"
WSOL_MINT = "So11111111111111111111111111111111111111112"

def main():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Find all pools with MINT bug
    cursor.execute("""
        SELECT COUNT(*) FROM token_pool_accounts
        WHERE quote_account = ? AND is_active = 1
    """, (WSOL_MINT,))
    count = cursor.fetchone()[0]

    if count == 0:
        print("✅ No pools with MINT bug found")
        return

    print(f"🔍 Found {count} pools with MINT bug")
    print(f"Sample tokens:")

    cursor.execute("""
        SELECT mint FROM token_pool_accounts
        WHERE quote_account = ? AND is_active = 1
        LIMIT 5
    """, (WSOL_MINT,))
    samples = cursor.fetchall()
    for (mint,) in samples:
        print(f"  • {mint[:20]}...")

    response = input(f"\n🗑️  DELETE these {count} pools? (they will be re-discovered) [y/N]: ")

    if response.lower() != 'y':
        print("❌ Cancelled")
        return

    # Delete the broken pools
    cursor.execute("""
        DELETE FROM token_pool_accounts
        WHERE quote_account = ? AND is_active = 1
    """, (WSOL_MINT,))

    deleted = cursor.rowcount
    conn.commit()

    print(f"\n✅ Deleted {deleted} broken pools")
    print(f"\n📋 Next steps:")
    print(f"1. Listener will re-discover these tokens")
    print(f"2. Vault discovery will find REAL account addresses")
    print(f"3. WebSocket will subscribe and prices will flow")
    print(f"\nEstimated time: 30-60 seconds to see WebSocket prices in UI")

    conn.close()

if __name__ == "__main__":
    main()
