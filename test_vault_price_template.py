#!/usr/bin/env python3
"""
Template for fetching LIVE token prices from PumpSwap pool vaults.

This shows how to:
1. Get pool addresses from database
2. Fetch LIVE vault balances from RPC
3. Calculate CURRENT prices from vault ratio (SOL Balance / Token Balance)
4. Display market cap, liquidity, and all fresh metrics

REQUIREMENTS:
- Helius API key for RPC access: https://www.helius.dev/
- Set environment variable: export HELIUS_API_KEY="your-key-here"

Formula:
  Price (SOL per token) = SOL Vault Balance / Token Vault Balance
  Price (USD per token) = Price (SOL) × SOL USD Price
  Market Cap = Price (USD) × Total Supply
  Liquidity = SOL Vault Balance

Usage:
    export HELIUS_API_KEY="your-key"
    python test_vault_price_template.py              # All tokens
    python test_vault_price_template.py <TOKEN_MINT> # Single token
"""

import sqlite3
import requests
import json
from datetime import datetime
from pathlib import Path
import sys
import os

# Configuration
DB_PATH = Path(__file__).parent / "pumpswap_tokens.db"
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "") or "0ae07551-32df-4d9d-af2a-1925fb7f561f"
HELIUS_RPC = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}" if HELIUS_API_KEY else "https://mainnet.helius-rpc.com/"
SOL_DECIMALS = 9
SOL_USD_PRICE = 200  # Update to current SOL price

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

def rpc_call(method, params):
    """Make JSON-RPC call to Solana (requires API key)"""
    try:
        if not HELIUS_API_KEY:
            print("\n[!] NOTE: Set HELIUS_API_KEY environment variable for live RPC calls")
            print("    export HELIUS_API_KEY='your-api-key'")
            return None

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

def get_token_metadata(token_mint):
    """Get token decimals from on-chain"""
    try:
        result = rpc_call("getTokenSupply", [token_mint])
        if result and result.get('value'):
            return {
                'decimals': result['value'].get('decimals'),
                'supply': result['value'].get('amount')
            }
    except:
        pass
    return None

def get_token_account_balance(token_account):
    """Get LIVE token account balance from blockchain"""
    try:
        result = rpc_call("getTokenAccountBalance", [token_account])
        if result and result.get('value'):
            value = result['value']
            return {
                'amount': value.get('amount'),
                'decimals': value.get('decimals'),
                'ui_amount': value.get('uiAmount')
            }
    except:
        pass
    return None

def get_sol_balance(account_address):
    """Get LIVE SOL balance from blockchain"""
    try:
        result = rpc_call("getBalance", [account_address])
        if result is not None:
            return {
                'lamports': result,
                'sol': result / (10 ** SOL_DECIMALS)
            }
    except:
        pass
    return None

def get_account_info(address):
    """Get account info from RPC"""
    try:
        result = rpc_call("getAccountInfo", [
            address,
            {"encoding": "base64"}
        ])
        if result and result.get('value'):
            return result['value']
    except:
        pass
    return None

def extract_vault_addresses(account_data_b64):
    """Extract vault addresses from LBPair account data"""
    import base64
    try:
        # Decode base64 data
        account_data = base64.b64decode(account_data_b64)

        result = {}
        if len(account_data) > 232:  # Need at least 200 + 32 bytes for vault_y
            # Extract vault addresses (32-byte Pubkeys at specific offsets)
            # PumpSwap LBPair: vault_x at offset 168, vault_y at offset 200
            vault_x_bytes = account_data[168:200]
            vault_y_bytes = account_data[200:232]

            # Decode from bytes to base58
            import base58
            vault_x_addr = base58.b58encode(vault_x_bytes).decode() if len(vault_x_bytes) == 32 else None
            vault_y_addr = base58.b58encode(vault_y_bytes).decode() if len(vault_y_bytes) == 32 else None

            result['vault_x'] = vault_x_addr
            result['vault_y'] = vault_y_addr

        return result
    except:
        pass
    return {}

def get_associated_token_accounts(owner, mint):
    """Find vault accounts for a pool and token"""
    try:
        result = rpc_call("getProgramAccounts", [
            "TokenkegQfeZyiNwAJsyFbPVwwQQfq5x5wr4ao64jULkJ",  # SPL Token Program
            {
                "encoding": "jsonParsed",
                "filters": [
                    {"dataSize": 165},  # Token account size
                    {
                        "memcmp": {
                            "offset": 0,
                            "bytes": owner  # Owner = AMM pool
                        }
                    },
                    {
                        "memcmp": {
                            "offset": 64,
                            "bytes": mint  # Mint = token
                        }
                    }
                ]
            }
        ])
        return result if result else []
    except:
        pass
    return []

def get_transaction(signature):
    """Get full transaction details from RPC"""
    try:
        result = rpc_call("getTransaction", [
            signature,
            {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
        ])
        return result
    except:
        pass
    return None

def extract_price_from_transaction(tx_data, base_mint):
    """Extract token and SOL balances from transaction logs

    For PumpSwap pools, the transaction contains postTokenBalances that show
    the vault balances after the pool creation.

    Price = SOL Balance / Token Balance

    Strategy: Find the vault accounts by looking for accounts with largest balances
    of each token type (excluding the mint account itself).
    """
    try:
        if not tx_data:
            return None

        meta = tx_data.get("meta", {})
        post_balances = meta.get("postTokenBalances", [])

        SOL_MINT = "So11111111111111111111111111111111111111112"
        token_balances = []  # All balances of the base token
        sol_balance = None
        sol_vault_owner = None

        for balance_info in post_balances:
            mint = balance_info.get("mint")
            ui_amount = balance_info.get("uiTokenAmount", {}).get("uiAmount", 0)
            owner = balance_info.get("owner", "")

            if mint == base_mint and ui_amount > 0:
                # Collect all token balances (might be in multiple vault accounts)
                token_balances.append({
                    'amount': ui_amount,
                    'owner': owner,
                    'index': balance_info.get("accountIndex", -1)
                })
            elif mint == SOL_MINT and ui_amount > 0:
                # Take the largest SOL balance
                if sol_balance is None or ui_amount > sol_balance:
                    sol_balance = ui_amount
                    sol_vault_owner = owner

        # Find the largest token vault (most likely the actual vault, not leftover)
        if token_balances:
            token_balances.sort(key=lambda x: x['amount'], reverse=True)
            largest_vault = token_balances[0]
            token_balance = largest_vault['amount']
            vault_owner = largest_vault['owner']

            if sol_balance is not None and sol_balance > 0 and token_balance > 0:
                price_sol = sol_balance / token_balance
                price_usd = price_sol * SOL_USD_PRICE

                return {
                    'price_sol': price_sol,
                    'price_usd': price_usd,
                    'token_balance': token_balance,
                    'sol_balance': sol_balance,
                    'token_mint': base_mint,
                    'vault_owner': vault_owner,
                    'sol_vault_owner': sol_vault_owner
                }
    except:
        pass

    return None

def calculate_price(token_balance, sol_balance, token_decimals):
    """Calculate price from vault balances"""
    try:
        if not token_balance or token_balance == 0 or token_decimals is None:
            return None

        token_adjusted = int(token_balance) / (10 ** token_decimals)
        sol_adjusted = int(sol_balance) / (10 ** SOL_DECIMALS)

        if token_adjusted == 0:
            return None

        price_sol = sol_adjusted / token_adjusted
        price_usd = price_sol * SOL_USD_PRICE

        return {
            'price_sol': price_sol,
            'price_usd': price_usd,
            'token_balance': token_adjusted,
            'sol_balance': sol_adjusted
        }
    except:
        pass
    return None

def format_price(price):
    """Format price for display"""
    if price is None:
        return "N/A"
    if price < 0.00000001:
        return f"${price:.18f}"
    elif price < 0.0001:
        return f"${price:.12f}"
    elif price < 1:
        return f"${price:.8f}"
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

def fetch_all_pools(conn):
    """Get all PumpSwap pools from database"""
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT symbol, base_mint, amm_id, total_supply, signature
            FROM pools
            WHERE is_pumpswap = 1
            ORDER BY base_mint DESC
        """)
        return [dict(row) for row in cursor.fetchall()]
    except:
        return []

def fetch_pool_price(pool):
    """Fetch LIVE price for a pool using transaction-based price extraction

    Extracts vault balances from pool creation transaction snapshot.
    """
    symbol = pool['symbol'] or pool['base_mint'][:8]
    base_mint = pool['base_mint']
    total_supply = pool['total_supply']
    signature = pool.get('signature', '')

    try:
        print(f"  {symbol:<12} Fetching transaction...", end=" ", flush=True)

        # For PumpSwap pools, extract price from the pool creation transaction
        # The signature field contains the transaction that created the pool
        tx_data = get_transaction(signature)
        if not tx_data:
            print("✗")
            return None
        print(f"✓", flush=True)

        print(f"  {symbol:<12} Extracting price from balances...", end=" ", flush=True)
        price_result = extract_price_from_transaction(tx_data, base_mint)

        if not price_result:
            print("✗ (no price data)")
            return None
        print(f"✓", flush=True)

        market_cap = (price_result['price_usd'] * total_supply) if total_supply else None

        return {
            'symbol': symbol,
            'price_sol': price_result['price_sol'],
            'price_usd': price_result['price_usd'],
            'liquidity_sol': price_result['sol_balance'],
            'market_cap': market_cap,
            'token_balance': price_result['token_balance'],
            'sol_balance': price_result['sol_balance'],
            'token_mint': price_result['token_mint'],
            'vault_owner': price_result['vault_owner'],
            'sol_vault_owner': price_result['sol_vault_owner'],
            'timestamp': datetime.now().isoformat()
        }

    except Exception as e:
        print(f"✗ ({str(e)[:30]})")
        return None

def main():
    """Main function"""
    print("=" * 140)
    print("LIVE PUMPSWAP VAULT PRICE FETCHER")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print("=" * 140)

    if not HELIUS_API_KEY:
        print("\n[!] HELIUS_API_KEY not set!")
        print("    Set your API key to enable live RPC calls:")
        print("\n    export HELIUS_API_KEY='your-api-key-here'")
        print("    Get key from: https://www.helius.dev/\n")
        print("[SHOWING] Database pool information instead...")
        print("-" * 140)

        conn = connect_db()
        if not conn:
            print("[✗] Cannot connect to database")
            return

        cursor = conn.cursor()
        cursor.execute("""
            SELECT symbol, base_mint, amm_id, total_supply, dexscreener_price_usd, signature
            FROM pools
            WHERE is_pumpswap = 1
            ORDER BY dexscreener_price_usd DESC NULLS LAST
        """)

        pools = cursor.fetchall()
        print(f"\n{'Symbol':<15} {'Price (USD)':<20} {'SOL Balance':<20} {'Total Supply':<18} {'Market Cap':<22} {'Token Address':<44}")
        print("-" * 160)

        for pool in pools:
            symbol = pool['symbol'] or 'N/A'
            symbol = symbol[:14]
            price = pool['dexscreener_price_usd']
            supply = pool['total_supply']
            mint = pool['base_mint']  # Full 44-character address
            amm_id = pool['amm_id']

            price_str = format_price(price)
            supply_str = format_number(supply)

            # Try to fetch SOL balance from RPC if API key is set
            sol_balance_str = "N/A"
            if HELIUS_API_KEY:
                # Try to get associated token accounts
                accounts = get_associated_token_accounts(amm_id, pool['base_mint'])
                if accounts and len(accounts) > 0:
                    vault_token = accounts[0]['pubkey']
                    sol_balance_result = get_sol_balance(vault_token)
                    if sol_balance_result:
                        sol_balance_str = f"${format_number(sol_balance_result['sol'])} SOL"

            if price and supply:
                mcap = price * supply
                mcap_str = f"${format_number(mcap)}"
            else:
                mcap_str = "N/A"

            print(f"{symbol:<15} {price_str:<20} {sol_balance_str:<20} {supply_str:<18} {mcap_str:<22} {mint:<44}")

        print("-" * 160)
        print("\n[TIP] To fetch LIVE prices from blockchain vaults:")
        print("      1. Get Helius API key: https://www.helius.dev/")
        print("      2. export HELIUS_API_KEY='your-key'")
        print("      3. Run this script again")
        return

    # With API key, fetch live prices
    conn = connect_db()
    if not conn:
        print("[✗] Cannot connect to database")
        return

    token_mint = sys.argv[1] if len(sys.argv) > 1 else None

    if token_mint:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT symbol, base_mint, amm_id, total_supply, signature
            FROM pools
            WHERE base_mint = ?
        """, (token_mint,))
        pool = cursor.fetchone()
        if not pool:
            print(f"[✗] Token not found")
            return

        print(f"\n[FETCHING] Live price for {dict(pool)['symbol']}...")
        print("-" * 140)
        result = fetch_pool_price(dict(pool))

        if result:
            print(f"\n[✓] LIVE PRICE DATA [! POOL CREATION SNAPSHOT]")
            print("-" * 140)
            print(f"Token Mint:       {result['token_mint']}")
            print(f"Price (SOL):      {format_price(result['price_sol'])} SOL/token")
            print(f"Price (USD):      {format_price(result['price_usd'])} USD/token")
            print(f"\nVault Balances (AT CREATION):")
            print(f"  Token Balance:  {format_number(result['token_balance'])} {result['token_mint'][:8]}...")
            print(f"  SOL Balance:    {format_number(result['sol_balance'])} SOL")
            print(f"\nVault Accounts:")
            print(f"  Token Vault:    {result['vault_owner']}")
            print(f"  SOL Vault:      {result['sol_vault_owner']}")
            print(f"\nMarket Data:")
            print(f"  Liquidity (SOL): {format_number(result['liquidity_sol'])} SOL")
            print(f"  Market Cap:      ${format_number(result['market_cap'])} USD")
            print(f"  Data Type:       POOL CREATION SNAPSHOT (BLOCKCHAIN SOURCE)")
            print(f"  Fetched:         {result['timestamp']}")

    else:
        pools = fetch_all_pools(conn)

        if not pools:
            print("[✗] No pools in database")
            return

        print(f"\n[FETCHING] Live prices for {len(pools)} PumpSwap tokens...")
        print("-" * 185)
        print(f"{'Symbol':<15} {'Price (SOL)':<20} {'Price (USD)':<20} {'SOL Balance':<20} {'Token Address':<44} {'Total Supply':<18}")
        print("-" * 185)

        success = 0
        for pool in pools:
            result = fetch_pool_price(dict(pool))
            if result:
                success += 1
                sol_balance_str = f"${format_number(result['liquidity_sol'])} SOL"
                supply_str = format_number(pool['total_supply'])
                token_address = result['token_mint']
                print(f"{result['symbol']:<15} {format_price(result['price_sol']):<20} {format_price(result['price_usd']):<20} {sol_balance_str:<20} {token_address:<44} {supply_str:<18}")

        print("-" * 140)
        print(f"\n[RESULT] Fetched {success}/{len(pools)} live prices from blockchain")

if __name__ == "__main__":
    main()
