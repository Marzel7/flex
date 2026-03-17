#!/usr/bin/env python3
"""
Manual pool registration: Register pools directly when you have vault addresses.

Usage:
    python3 manual_pool_registration.py <mint> <base_vault> <quote_vault> [quote_mint]

Example:
    python3 manual_pool_registration.py \
      BWGFePEdaTBSEqRzZ27fsFSrdLo7uE1AzAnXbYqGpump \
      8Z3tpQW8LP5CakR1t7ppMA3Mog47ep3snbzjcHDK7MV2 \
      6ybRixNGw412p9U14q3pCo5BBmqyAZC4Tc6xAZcDD773 \
      So11111111111111111111111111111111111111112
"""

import sqlite3
import sys
import time

DB_PATH = "database/flex_complete_database.db"

WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112"

def register_pool(mint: str, base_vault: str, quote_vault: str, quote_mint: str = None):
    """Register a pool manually."""

    if not quote_mint:
        quote_mint = WRAPPED_SOL_MINT

    print(f"\n{'='*80}")
    print(f"MANUAL POOL REGISTRATION")
    print(f"{'='*80}")
    print(f"\nToken: {mint}")
    print(f"Base Vault: {base_vault}")
    print(f"Quote Vault: {quote_vault}")
    print(f"Quote Mint: {quote_mint}")

    conn = sqlite3.connect(DB_PATH, timeout=10)
    cursor = conn.cursor()
    now = int(time.time())

    try:
        # Check if already exists
        cursor.execute(
            "SELECT COUNT(*) FROM token_pool_accounts WHERE mint = ? AND base_account = ?",
            (mint, base_vault)
        )
        exists = cursor.fetchone()[0]

        if exists:
            print(f"\n⚠️  Pool already registered")
            conn.close()
            return False

        # Insert pool
        cursor.execute(
            """
            INSERT INTO token_pool_accounts
            (mint, base_account, quote_account, pool_program, base_token,
             base_decimals, quote_decimals, quote_token,
             vault_validation_status, discovery_method,
             created_at, updated_at, is_primary, pool_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mint,
                base_vault,
                quote_vault,
                "unknown",  # pool program (would need RPC to verify)
                mint,  # base token
                6,  # base decimals (assume 6)
                9 if quote_mint == WRAPPED_SOL_MINT else 6,  # quote decimals
                quote_mint,  # quote token
                "validated",  # status
                "manual_registration",  # discovery method
                now,
                now,
                1 if quote_mint == WRAPPED_SOL_MINT else 0,  # mark wSOL as primary
                100.0 if quote_mint == WRAPPED_SOL_MINT else 50.0  # score
            )
        )

        # Update price source to 'pool'
        cursor.execute(
            "UPDATE token_analysis SET price_source = 'pool' WHERE mint = ?",
            (mint,)
        )

        conn.commit()
        print(f"\n✅ Pool registered successfully")
        print(f"✅ Price source updated to 'pool'")
        print(f"\nNext: WebSocket will subscribe to this pool")
        print(f"Run: python3 test_top20_update_times.py")

        return True

    except Exception as e:
        print(f"\n❌ Registration failed: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)

    mint = sys.argv[1]
    base_vault = sys.argv[2]
    quote_vault = sys.argv[3]
    quote_mint = sys.argv[4] if len(sys.argv) > 4 else WRAPPED_SOL_MINT

    register_pool(mint, base_vault, quote_vault, quote_mint)
