#!/usr/bin/env python3
"""
Cleanup: Remove stale DexScreener pool references.

DexScreener API returns Pump.Fun bonding curve addresses before token migration.
These are NOT tradeable pools — they're outdated references that block real pool discovery.

After cleanup, RPC-authoritative vault discovery (which finds Token2022 pools) takes over.
"""

import sqlite3
import os

DB_PATH = "database/flex_complete_database.db"


def main():
    """Remove all DexScreener-discovered pools."""

    conn = sqlite3.connect(DB_PATH, timeout=10)
    cursor = conn.cursor()

    print("\n" + "=" * 100)
    print("CLEANUP: Remove Stale DexScreener Pool References")
    print("=" * 100)

    # Find all DexScreener pools
    cursor.execute("""
        SELECT COUNT(*) FROM token_pool_accounts
        WHERE discovery_method = 'dexscreener_authoritative'
    """)

    dex_count, = cursor.fetchone()

    print(f"\nFound {dex_count} DexScreener-discovered pools")
    print("These are Pump.Fun bonding curve references (not tradeable).\n")

    # Show what we're about to delete
    cursor.execute("""
        SELECT mint, base_account, created_at
        FROM token_pool_accounts
        WHERE discovery_method = 'dexscreener_authoritative'
        ORDER BY created_at DESC
    """)

    pools_to_delete = cursor.fetchall()

    print("Pools to remove:")
    for mint, base_account, created in pools_to_delete:
        print(f"  {mint[:14]}: {base_account[:30]}...")

    # Confirm deletion
    print(f"\nRemoving {len(pools_to_delete)} DexScreener pools...")

    cursor.execute("""
        DELETE FROM token_pool_accounts
        WHERE discovery_method = 'dexscreener_authoritative'
    """)

    conn.commit()

    print(f"✅ Deleted {len(pools_to_delete)} pools\n")

    # Show summary of what remains
    cursor.execute("""
        SELECT discovery_method, COUNT(*) as count
        FROM token_pool_accounts
        GROUP BY discovery_method
        ORDER BY count DESC
    """)

    print("Remaining pools by discovery method:")
    for method, count in cursor.fetchall():
        print(f"  {method}: {count}")

    # Show how many tokens now have NO pools
    cursor.execute("""
        SELECT COUNT(DISTINCT mint) FROM token_analysis
        WHERE mint NOT IN (SELECT DISTINCT mint FROM token_pool_accounts)
    """)

    no_pool_count, = cursor.fetchone()

    print(f"\nTokens with no pools: {no_pool_count}")
    print("These will use DexScreener API as fallback (correct behavior)")

    conn.close()

    print("\n" + "=" * 100)
    print("NEXT STEPS:")
    print("  1. Restart listener to trigger vault discovery for affected tokens")
    print("  2. Real RPC-discovered pools (Token2022) will be registered")
    print("  3. WebSocket will subscribe to valid pools only")
    print("=" * 100 + "\n")


if __name__ == "__main__":
    main()
