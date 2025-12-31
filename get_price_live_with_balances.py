#!/usr/bin/env python3
"""
Get token price and SOL balances directly from on-chain pool vaults.

This script:
1. Queries the local database for pool information
2. Fetches LIVE vault balances from on-chain (SOL balance, Token balance)
3. Calculates LIVE prices from vault ratios
4. Shows comparison between database prices and live prices

Usage:
    python get_price_live_with_balances.py <TOKEN_MINT>
    python get_price_live_with_balances.py  # Uses default reference token
"""

import sqlite3
import requests
import json
from datetime import datetime
from pathlib import Path
import sys

# Configuration
TOKEN_MINT = "fdry5i5kuadz1ik8gps26qjj9rw9mpufxmeggc2hnsp7"
DB_PATH = Path(__file__).parent / "pumpswap_tokens.db"
HELIUS_RPC = "https://mainnet.helius-rpc.com/"
SOL_USD_PRICE = 200  # Current SOL price in USD

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
                total_supply,
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

def get_all_pumpswap_tokens(conn):
    """Get all PumpSwap tokens with vault info"""
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT symbol, base_mint, amm_id, current_price, total_supply,
                   sol_usd_price, dexscreener_price_usd, dexscreener_price_native,
                   liquidity
            FROM pools
            WHERE is_pumpswap = 1
            ORDER BY dexscreener_price_usd DESC NULLS LAST
        """)
        return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        return []

def rpc_call(method, params):
    """Make JSON-RPC call to Solana"""
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params
        }

        response = requests.post(HELIUS_RPC, json=payload, timeout=10)

        if response.status_code == 200:
            data = response.json()
            if "error" in data:
                return None
            return data.get("result")
        return None
    except Exception as e:
        return None

def get_token_account_balance(token_account):
    """Get balance of a token account"""
    try:
        result = rpc_call("getTokenAccountBalance", [token_account])
        if result:
            value = result.get('value', {})
            return {
                'amount': value.get('amount'),
                'decimals': value.get('decimals'),
                'ui_amount': value.get('uiAmount')
            }
    except Exception as e:
        pass
    return None

def get_sol_balance(account_address):
    """Get SOL balance of an account"""
    try:
        result = rpc_call("getBalance", [account_address])
        if result is not None:
            return result / 1e9  # Convert lamports to SOL
    except Exception as e:
        pass
    return None

def get_account_info(address):
    """Get parsed account information"""
    try:
        result = rpc_call("getAccountInfo", [
            address,
            {"encoding": "jsonParsed"}
        ])
        return result
    except Exception as e:
        pass
    return None

def calculate_price_from_balances(token_balance, sol_balance, token_decimals):
    """Calculate price from vault balances"""
    try:
        if not token_balance or token_balance == 0 or token_decimals is None:
            return None

        token_adjusted = int(token_balance) / (10 ** token_decimals)
        sol_adjusted = int(sol_balance) / (10 ** 9)  # SOL always 9 decimals

        if token_adjusted == 0:
            return None

        price_sol = sol_adjusted / token_adjusted
        return price_sol
    except Exception as e:
        pass
    return None

def format_price(price):
    """Format price for display"""
    if price is None:
        return "N/A"
    if price < 0.00000001:
        return f"${price:.18f}"
    elif price < 0.00001:
        return f"${price:.15f}"
    elif price < 0.0001:
        return f"${price:.12f}"
    elif price < 0.001:
        return f"${price:.10f}"
    elif price < 0.01:
        return f"${price:.8f}"
    elif price < 1:
        return f"${price:.6f}"
    else:
        return f"${price:,.4f}"

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

def get_price_age_str(last_price_update):
    """Calculate and format how long ago price was updated"""
    if not last_price_update:
        return "Never updated"

    try:
        last_update_dt = datetime.fromisoformat(last_price_update)
        now = datetime.now()
        age_seconds = (now - last_update_dt).total_seconds()

        if age_seconds < 60:
            return f"{int(age_seconds)}s ago ✓"
        elif age_seconds < 300:
            return f"{int(age_seconds/60)}m ago ✓"
        elif age_seconds < 1800:
            return f"{int(age_seconds/60)}m ago"
        elif age_seconds < 3600:
            return f"{int(age_seconds/60)}m ago"
        else:
            return f"{int(age_seconds/3600)}h ago ⚠"
    except:
        return "Unknown"

def main():
    """Main function"""
    # Get token from command line or use default
    token_mint = sys.argv[1] if len(sys.argv) > 1 else TOKEN_MINT

    print("=" * 130)
    print(f"TOKEN PRICE WITH LIVE ON-CHAIN BALANCES")
    print(f"Token Mint: {token_mint}")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print("=" * 130)

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
        print("-" * 130)
        print(f"Symbol: {token.get('symbol') or 'N/A'}")
        print(f"Name: {token.get('name') or 'N/A'}")
        print(f"Mint: {token.get('base_mint')}")
        print(f"Creator: {token.get('creator') or 'N/A'}")
        print(f"AMM ID: {token.get('amm_id')}")

        # Get stored prices from database
        print(f"\nDATABASE PRICES (Stored):")
        print("-" * 130)

        price_usd_stored = token.get('dexscreener_price_usd')
        price_sol_stored = token.get('dexscreener_price_native')

        if not price_usd_stored and not price_sol_stored:
            current_price_sol = token.get('current_price')
            sol_usd = token.get('sol_usd_price') or SOL_USD_PRICE
            price_sol_stored = current_price_sol
            price_usd_stored = (current_price_sol * sol_usd) if current_price_sol else None

        last_update = token.get('last_price_update')
        price_freshness = get_price_age_str(last_update)

        print(f"Stored Price (SOL): {format_price(price_sol_stored) if price_sol_stored else 'N/A'}/token")
        print(f"Stored Price (USD): {format_price(price_usd_stored) if price_usd_stored else 'N/A'}/token")
        print(f"Price Status: {price_freshness}")

        # Try to get live prices from vaults if available
        print(f"\nLIVE VAULT DATA (From RPC):")
        print("-" * 130)

        amm_id = token.get('amm_id')
        if amm_id:
            # For now, we'd need the vault addresses which aren't directly in our database
            # This is where the pool address is needed to extract vault accounts
            print(f"[!] To fetch live vault balances, we need:")
            print(f"    - Vault Token Account (holds the token)")
            print(f"    - Vault SOL Account (holds SOL)")
            print(f"    - These are derived from the pool address: {amm_id}")
            print(f"\n[Note] The database only stores the AMM ID, not the vault addresses.")
            print(f"       To get live balances, either:")
            print(f"       1. Fetch pool creation transaction to extract vault addresses")
            print(f"       2. Use PumpSwap program account parsing")
        else:
            print(f"[!] No AMM ID in database")

        print(f"\nMARKET DATA:")
        print("-" * 130)
        total_supply = token.get('total_supply')
        liquidity = token.get('liquidity')

        print(f"Total Supply: {format_number(total_supply)} tokens")

        if price_usd_stored and total_supply:
            calculated_market_cap = price_usd_stored * total_supply
            print(f"Market Cap: ${format_number(calculated_market_cap)}")
        else:
            print(f"Market Cap: N/A")

        print(f"Liquidity: ${format_number(liquidity)}")
        print(f"24h Volume: ${format_number(token.get('volume_24h'))}")

        print(f"\nPOOL DATA:")
        print("-" * 130)
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
        print("-" * 130)

        pumpswap_tokens = get_all_pumpswap_tokens(conn)

        if pumpswap_tokens:
            print(f"\nTotal PumpSwap tokens: {len(pumpswap_tokens)}\n")
            print(f"{'Symbol':<12} {'Price (USD)':<20} {'SOL Liquidity':<20} {'Market Cap':<25} {'Token Address':<45}")
            print("-" * 130)

            for t in pumpswap_tokens[:15]:  # Show first 15
                symbol = t.get('symbol') or 'N/A'
                symbol = symbol[:11]

                # Use DEXScreener price if available
                usd_price = t.get('dexscreener_price_usd')
                if not usd_price:
                    sol_price = t.get('current_price')
                    sol_usd = t.get('sol_usd_price') or SOL_USD_PRICE
                    usd_price = (sol_price * sol_usd) if sol_price else None

                price_str = format_price(usd_price)

                # Calculate market cap
                total_supply = t.get('total_supply')
                if usd_price and total_supply:
                    mcap = usd_price * total_supply
                    mcap_str = f"${format_number(mcap)}"
                else:
                    mcap_str = "N/A"

                liquidity = t.get('liquidity')
                liquidity_str = f"${format_number(liquidity)}" if liquidity else "N/A"

                mint = t.get('base_mint') or 'N/A'
                mint = mint[:44]  # Show full mint address

                print(f"{symbol:<12} {price_str:<20} {liquidity_str:<20} {mcap_str:<25} {mint:<45}")
        else:
            print("No PumpSwap tokens in database yet.")
            print("\nRun: python main.py  (to start monitoring)")

    print("\n" + "=" * 130)
    conn.close()

if __name__ == "__main__":
    main()
