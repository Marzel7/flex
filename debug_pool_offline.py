#!/usr/bin/env python3
"""
Debug script to understand what pool accounts we're detecting.
Uses cached RPC data from listener's logging.
"""

import sqlite3

# Get a detected pool address
try:
    conn = sqlite3.connect('database/flex_complete_database.db')
    cursor = conn.cursor()

    # Get token with pool
    cursor.execute("""
        SELECT mint, pool_address
        FROM token_analysis
        WHERE pool_address IS NOT NULL
        LIMIT 1
    """)
    token_mint, pool_address = cursor.fetchone()

    # Get the vault accounts that were extracted
    cursor.execute("""
        SELECT base_account, quote_account, pool_program
        FROM token_pool_accounts
        WHERE mint = ?
    """, (token_mint,))
    base_account, quote_account, pool_program = cursor.fetchone()

    conn.close()

    print("="*80)
    print("DETECTED POOL ANALYSIS")
    print("="*80)
    print(f"\nToken:           {token_mint[:20]}...")
    print(f"Pool Address:    {pool_address}")
    print(f"Pool Program:    {pool_program}")
    print(f"\nExtracted Vaults:")
    print(f"  Base:  {base_account}")
    print(f"  Quote: {quote_account}")

    # Check if vault accounts are suspicious
    print(f"\n🔍 Analysis:")

    # These vault addresses are repeated across all tokens!
    KNOWN_REPEATED_VAULT = "EZGLemQL2H2oCUDkAsuGoVpAD4LrWmfySFKV9y7Vq8d9"
    KNOWN_REPEATED_QUOTE = "9AQ5oouQjPDAaPn5v5wNHRg4kXPxNxFS7kVUty9NK91z"

    if base_account == KNOWN_REPEATED_VAULT:
        print(f"  ⚠️  Base account is the KNOWN REPEATED vault")
        print(f"      (same across all tokens - indicates wrong offset)")

    if quote_account == KNOWN_REPEATED_QUOTE:
        print(f"  ⚠️  Quote account is the KNOWN REPEATED vault")
        print(f"      (same across all tokens - indicates wrong offset)")

    # Check program owner
    if pool_program == "raydium_amm":
        print(f"  ℹ️  Program says 'raydium_amm'")
        print(f"      But might be PumpSwap pool state (uses Raydium layout)")
    elif pool_program == "pumpswap":
        print(f"  ℹ️  Program says 'pumpswap'")
        print(f"      Extracting from PumpSwap bonding curve or pool account?")

    print(f"\n💡 Hypothesis:")
    print(f"  The pool addresses being detected might be:")
    print(f"  1. PumpSwap bonding curve accounts (not actual pools)")
    print(f"  2. Raydium pool state PDAs (not the full state)")
    print(f"  3. Some other account type with offsets 232-296 containing")
    print(f"     fixed/constant data (like program addresses or config)")

    print(f"\n🔧 Next debugging steps:")
    print(f"  1. Enable debug logging in pool_detector")
    print(f"  2. Inspect actual account data from RPC")
    print(f"  3. Compare with Raydium/PumpSwap documentation")
    print(f"  4. Check if offsets are correct for account type")

except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
