#!/usr/bin/env python3
"""
Market Cap Updater for PumpSwap Tokens

Periodically fetches market cap from DexScreener and updates the database.
Stops tracking tokens once market cap falls below 30k.
"""

import sqlite3
import requests
import time
import asyncio
from typing import Optional

DB_PATH = "pumpswap_tokens.db"
MARKET_CAP_THRESHOLD = 30000  # Stop tracking below this value


def fetch_market_cap_dexscreener(token_mint: str) -> Optional[float]:
    """Fetch current market cap from DexScreener"""
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_mint}"
        res = requests.get(url, timeout=10)

        if res.status_code != 200:
            return None

        data = res.json()
        pairs = data.get("pairs", [])

        if not pairs:
            return None

        # Find the pair with highest liquidity (primary pair)
        pair = pairs[0]
        market_cap = pair.get("marketCap")

        return market_cap

    except Exception as e:
        print(f"[MARKET_CAP_ERROR] {token_mint}: {e}")
        return None


def update_market_cap_for_token(token_mint: str) -> tuple:
    """Update market cap for a single token. Returns (current_cap, highest_cap, should_stop)"""
    current_cap = fetch_market_cap_dexscreener(token_mint)

    if current_cap is None:
        return None, None, False

    try:
        conn = sqlite3.connect(DB_PATH, timeout=60)
        cursor = conn.cursor()

        # Get existing highest cap
        cursor.execute(
            "SELECT market_cap_highest FROM token_analysis WHERE mint = ?",
            (token_mint,)
        )
        row = cursor.fetchone()
        highest_cap = row[0] if row and row[0] else current_cap

        # Update highest if current is higher
        if current_cap > highest_cap:
            highest_cap = current_cap

        # Determine if we should stop tracking
        should_stop = current_cap < MARKET_CAP_THRESHOLD

        # Update database
        cursor.execute("""
            UPDATE token_analysis
            SET market_cap_current = ?, market_cap_highest = ?, market_cap_stopped_tracking = ?
            WHERE mint = ?
        """, (current_cap, highest_cap, 1 if should_stop else 0, token_mint))

        conn.commit()
        conn.close()

        status = "🛑 STOPPED" if should_stop else "✓"
        print(f"[UPDATE] {token_mint[:16]}... | Current: ${current_cap:>10,.0f} | Highest: ${highest_cap:>10,.0f} | {status}")

        return current_cap, highest_cap, should_stop

    except Exception as e:
        print(f"[DB_ERROR] {token_mint}: {e}")
        return current_cap, None, False


def get_tokens_to_update() -> list:
    """Get list of tokens that need market cap updates (not stopped)"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=60)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT mint FROM token_analysis
            WHERE market_cap_stopped_tracking = 0 OR market_cap_stopped_tracking IS NULL
            ORDER BY analyzed_at DESC
            LIMIT 100
        """)

        tokens = [row[0] for row in cursor.fetchall()]
        conn.close()

        return tokens

    except Exception as e:
        print(f"[DB_ERROR] Failed to fetch tokens: {e}")
        return []


def update_all_market_caps():
    """Update market caps for all active tokens"""
    tokens = get_tokens_to_update()

    if not tokens:
        print("[UPDATE] No tokens to update")
        return

    print(f"\n[UPDATE] Updating {len(tokens)} tokens...")

    stopped_count = 0
    updated_count = 0

    for token_mint in tokens:
        current, highest, stopped = update_market_cap_for_token(token_mint)

        if current is not None:
            updated_count += 1
            if stopped:
                stopped_count += 1

        # Rate limit to avoid overwhelming DexScreener
        time.sleep(0.5)

    print(f"[UPDATE] Complete: {updated_count} updated, {stopped_count} stopped")


def continuous_update(interval=60):
    """Continuously update market caps at specified interval (seconds)"""
    print(f"[UPDATE] Starting continuous updates every {interval}s...")

    try:
        while True:
            update_all_market_caps()
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[UPDATE] Stopped")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "continuous":
        # Run continuous updates
        interval = int(sys.argv[2]) if len(sys.argv) > 2 else 300  # Default 5 minutes
        continuous_update(interval)
    else:
        # Run once
        update_all_market_caps()
