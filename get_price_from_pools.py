#!/usr/bin/env python3
"""
Get token price from local pool database in USD.

This script queries the pumpswap_tokens.db for price data
extracted from on-chain pool vaults, displayed in USD.

Usage:
    python get_price_from_pools.py <TOKEN_MINT>
    python get_price_from_pools.py  # Uses default reference token
"""

import sqlite3
from datetime import datetime
from pathlib import Path
import sys

# Default token to analyze
TOKEN_MINT = "fdry5i5kuadz1ik8gps26qjj9rw9mpufxmeggc2hnsp7"
DB_PATH = Path(__file__).parent / "pumpswap_tokens.db"
SOL_USD_PRICE = 200  # Current SOL price in USD (can be updated)

def connect_db():
    """Connect to database"""
    try:
        if not DB_PATH.exists():
            return None
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        print(f"[✗] Database error: {e}")
        return None

def find_token(conn, token_mint):
    """Find token by mint address"""
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                symbol,
                name,
                base_mint,
                current_price,
                pumpswap_initial_price,
                price,
                total_supply,
                market_cap,
                liquidity,
                volume_24h,
                creator,
                amm_id,
                is_pumpswap,
                last_updated,
                first_seen,
                sol_usd_price,
                dexscreener_price_native,
                dexscreener_price_usd,
                last_price_update
            FROM pools
            WHERE base_mint = ?
        """, (token_mint,))

        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception as e:
        print(f"[✗] Query error: {e}")
        return None

def format_price_usd(price):
    """Format price for display in USD"""
    if price is None or price == 0:
        return "N/A"
    if isinstance(price, str):
        return price
    if price < 0.000001:
        return f"${price:.18f}"
    elif price < 0.0001:
        return f"${price:.12f}"
    elif price < 0.01:
        return f"${price:.8f}"
    elif price < 1:
        return f"${price:.6f}"
    elif price < 1000:
        return f"${price:,.2f}"
    else:
        return f"${price:,.2f}"

def format_number(num):
    """Format large numbers"""
    if num is None or num == 0:
        return "N/A"
    if num > 1_000_000_000:
        return f"{num/1_000_000_000:.2f}B"
    elif num > 1_000_000:
        return f"{num/1_000_000:.2f}M"
    elif num > 1_000:
        return f"{num/1_000:.2f}K"
    else:
        return f"{num:.2f}"

def get_all_pumpswap_tokens(conn):
    """Get all PumpSwap tokens"""
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT symbol, base_mint, current_price, market_cap, total_supply, sol_usd_price,
                   dexscreener_price_usd, dexscreener_price_native, liquidity, amm_id
            FROM pools
            WHERE is_pumpswap = 1
            ORDER BY dexscreener_price_usd DESC NULLS LAST
        """)
        return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        return []

def calculate_usd_price(sol_price, sol_usd=None):
    """Convert SOL price to USD"""
    if sol_price is None or sol_price == 0:
        return None
    usd_rate = sol_usd if sol_usd else SOL_USD_PRICE
    return sol_price * usd_rate

def get_price_age_str(last_price_update):
    """Calculate and format how long ago price was updated"""
    if not last_price_update:
        return "Never updated (initial price)"

    try:
        from datetime import datetime
        last_update_dt = datetime.fromisoformat(last_price_update)
        now = datetime.now()
        age_seconds = (now - last_update_dt).total_seconds()

        if age_seconds < 60:
            return f"Updated {int(age_seconds)}s ago ✓ (fresh)"
        elif age_seconds < 300:
            return f"Updated {int(age_seconds/60)}m ago ✓ (fresh)"
        elif age_seconds < 1800:
            return f"Updated {int(age_seconds/60)}m ago ~ (ok)"
        elif age_seconds < 3600:
            return f"Updated {int(age_seconds/60)}m ago ~ (moderate)"
        else:
            return f"Updated {int(age_seconds/3600)}h ago ⚠ (stale)"
    except:
        return "Unknown"

def main():
    """Main function"""
    # Get token from command line or use default
    token_mint = sys.argv[1] if len(sys.argv) > 1 else TOKEN_MINT

    print("=" * 110)
    print(f"TOKEN PRICE - From Local Pool Database (USD)")
    print(f"Token Mint: {token_mint}")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print("=" * 110)

    conn = connect_db()
    if not conn:
        print("[✗] Cannot connect to database")
        print("[!] Run: python main.py  (to start collecting pool data)")
        return

    print(f"\n[SEARCH] Looking for token in pool database...")
    token = find_token(conn, token_mint)

    if token:
        print(f"\n[✓] TOKEN FOUND\n")

        print("TOKEN DATA:")
        print("-" * 110)
        print(f"Symbol: {token.get('symbol') or 'N/A'}")
        print(f"Name: {token.get('name') or 'N/A'}")
        print(f"Mint: {token.get('base_mint')}")
        print(f"Creator: {token.get('creator') or 'N/A'}")

        print(f"\nPRICE INFORMATION (in USD):")
        print("-" * 110)

        # Use DEXScreener prices if available (more accurate), otherwise calculate from stored data
        price_usd = token.get('dexscreener_price_usd')
        price_sol = token.get('dexscreener_price_native')

        # Fallback to calculating from current_price if DEXScreener data not available
        if not price_usd and not price_sol:
            current_price_sol = token.get('current_price')
            sol_usd = token.get('sol_usd_price') or SOL_USD_PRICE
            price_usd = calculate_usd_price(current_price_sol, sol_usd) if current_price_sol else None
            price_sol = current_price_sol

        initial_price_sol = token.get('pumpswap_initial_price')
        sol_usd = token.get('sol_usd_price') or SOL_USD_PRICE
        initial_price_usd = calculate_usd_price(initial_price_sol, sol_usd) if initial_price_sol else None

        # Show price freshness
        last_update = token.get('last_price_update')
        price_freshness = get_price_age_str(last_update)

        print(f"Current Price (SOL): {format_price_usd(price_sol) if price_sol else 'N/A'} SOL/token")
        print(f"Current Price (USD): {format_price_usd(price_usd)}/token")
        print(f"Price Status: {price_freshness}")

        print(f"\nInitial Price (SOL): {format_price_usd(initial_price_sol) if initial_price_sol else 'N/A'} SOL/token")
        print(f"Initial Price (USD): {format_price_usd(initial_price_usd)}/token")

        if price_usd and initial_price_usd and initial_price_usd != 0:
            change_pct = ((price_usd - initial_price_usd) / initial_price_usd) * 100
            direction = "📈" if change_pct > 0 else "📉"
            print(f"Price Change: {direction} {change_pct:+.2f}%")

        print(f"\nMARKET DATA:")
        print("-" * 110)
        total_supply = token.get('total_supply')
        liquidity = token.get('liquidity')

        # Calculate market cap from price and supply (more accurate than stored value)
        if price_usd and total_supply:
            calculated_market_cap = price_usd * total_supply
        else:
            calculated_market_cap = token.get('market_cap')

        print(f"Total Supply: {format_number(total_supply)} tokens")
        print(f"Market Cap: ${format_number(calculated_market_cap)}")
        print(f"Liquidity: ${format_number(liquidity)}")
        print(f"24h Volume: ${format_number(token.get('volume_24h'))}")

        print(f"\nPOOL DATA:")
        print("-" * 110)
        print(f"AMM Address: {token.get('amm_id')}")
        print(f"Is PumpSwap: {'Yes' if token.get('is_pumpswap') else 'No'}")
        print(f"First Seen: {token.get('first_seen')}")
        print(f"Last Updated: {token.get('last_updated')}")

    else:
        print(f"\n[✗] TOKEN NOT IN DATABASE\n")
        print(f"The token {token_mint} hasn't been detected yet.")
        print(f"\nFor this token to appear in the database:")
        print(f"  1. It must migrate from Pump.fun bonding curve → PumpSwap")
        print(f"  2. The monitoring system (python main.py) must be running")
        print(f"  3. WebSocket must detect the pool creation event")
        print(f"  4. Vault balances are extracted and price calculated")

        # Show available tokens
        print(f"\n[AVAILABLE PUMPSWAP TOKENS IN DATABASE]")
        print("-" * 110)

        pumpswap_tokens = get_all_pumpswap_tokens(conn)

        if pumpswap_tokens:
            print(f"\nTotal PumpSwap tokens: {len(pumpswap_tokens)}\n")
            print(f"{'Symbol':<12} {'Price (USD)':<20} {'SOL Balance':<18} {'Market Cap':<20} {'Token Address':<44}")
            print("-" * 130)

            for t in pumpswap_tokens[:15]:  # Show first 15
                symbol = t.get('symbol') or 'N/A'
                symbol = symbol[:11]

                # Use DEXScreener price if available, otherwise calculate
                usd_price = t.get('dexscreener_price_usd')
                if not usd_price:
                    sol_price = t.get('current_price')
                    sol_usd = t.get('sol_usd_price') or SOL_USD_PRICE
                    usd_price = calculate_usd_price(sol_price, sol_usd) if sol_price else None

                price_str = format_price_usd(usd_price)

                # Calculate market cap from price and supply (more accurate)
                total_supply = t.get('total_supply')
                if usd_price and total_supply:
                    mcap = usd_price * total_supply
                    mcap_str = f"${format_number(mcap)}"
                else:
                    mcap_str = "N/A"

                # SOL balance (liquidity) from vault
                liquidity = t.get('liquidity')
                sol_balance_str = f"${format_number(liquidity)}" if liquidity and liquidity > 0 else "N/A"

                mint = t.get('base_mint') or 'N/A'
                mint = mint[:44]  # Show full mint address

                print(f"{symbol:<12} {price_str:<20} {sol_balance_str:<18} {mcap_str:<20} {mint:<44}")
        else:
            print("No PumpSwap tokens in database yet.")
            print("\nRun: python main.py  (to start monitoring)")

    print("\n" + "=" * 110)
    conn.close()

if __name__ == "__main__":
    main()
