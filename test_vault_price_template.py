#!/usr/bin/env python3
"""
Fetch LIVE token prices from PumpSwap pool vaults via blockchain RPC.

This shows how to:
1. Get pool addresses from database OR search blockchain for any token
2. Fetch LIVE vault balances from RPC
3. Calculate CURRENT prices from vault ratio (SOL Balance / Token Balance)
4. Display price in USD with market data

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
    python test_vault_price_template.py                          # All tokens in DB
    python test_vault_price_template.py <TOKEN_MINT>             # Single token from DB
    python test_vault_price_template.py -s <POOL_SIGNATURE>      # Any token via signature
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
            # RPC returns {'context': {...}, 'value': lamports}
            if isinstance(result, dict) and 'value' in result:
                lamports = result['value']
            else:
                lamports = result

            return {
                'lamports': lamports,
                'sol': lamports / (10 ** SOL_DECIMALS)
            }
    except:
        pass
    return None

def get_token_accounts_by_owner(owner_address, mint_address):
    """Find token accounts owned by a specific owner for a specific mint

    This RPC method returns all token accounts (held by owner) for a given mint.
    Much faster and more reliable than getProgramAccounts with filters.
    """
    try:
        result = rpc_call("getTokenAccountsByOwner", [
            owner_address,
            {"mint": mint_address},
            {"encoding": "jsonParsed"}
        ])
        if result and result.get('value'):
            return result['value']
        return []
    except Exception as e:
        return []

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

def find_pool_creation_tx(token_mint):
    """Find pool creation transaction for a token mint by searching blockchain.

    Uses getSignaturesForAddress on the token mint account to find its creation
    transaction, then filters for PumpSwap pool creation signature patterns.

    Note: This searches token account history which may contain many transactions.
    """
    try:
        print(f"  [SEARCHING] Finding pool creation transaction for {token_mint[:8]}...")

        # Get signatures for the token mint account (limited to recent ones)
        result = rpc_call("getSignaturesForAddress", [
            token_mint,
            {"limit": 100}  # Search recent 100 transactions
        ])

        if not result:
            print(f"  [✗] No transactions found for token")
            return None

        # The first signature should be the creation (oldest is usually first in some implementations)
        # For PumpSwap, we want to find the signature that created the token
        # Typically this is among the first few transactions

        print(f"  [FOUND] {len(result)} transactions, checking first few...")

        for sig_info in result[:10]:  # Check first 10 transactions
            signature = sig_info.get('signature')
            if not signature:
                continue

            print(f"    [CHECKING] {signature[:16]}...", end=" ", flush=True)
            tx_data = get_transaction(signature)

            if not tx_data:
                print("✗ (fetch failed)")
                continue

            # Check if this transaction has token balance changes (pool creation signature)
            meta = tx_data.get('meta', {})
            post_balances = meta.get('postTokenBalances', [])

            # Pool creation will have the token mint with balance changes
            has_token = any(b.get('mint') == token_mint for b in post_balances)
            has_sol = any(b.get('mint') == 'So11111111111111111111111111111111111111112' for b in post_balances)

            if has_token and has_sol:
                print("✓ [POOL CREATION FOUND]")
                return signature
            else:
                print("✗")

        print(f"  [✗] Could not find pool creation transaction in recent history")
        return None

    except Exception as e:
        print(f"  [✗] Error searching transactions: {str(e)[:50]}")
        return None

def extract_vault_account_addresses(tx_data, base_mint):
    """Extract actual token and SOL vault account addresses from transaction

    The postTokenBalances contain accountIndex values that point to the
    message.accountKeys array. This function maps those indices to actual
    account addresses which are the REAL token accounts we need to query
    for real-time balances.

    Note: Account keys may have either 'pubkey' field (dict) or be strings directly.
    """
    try:
        if not tx_data:
            return None, None

        # Transaction structure: tx_data['transaction']['message']['accountKeys']
        message = tx_data.get('transaction', {}).get('message', {})
        account_keys = message.get('accountKeys', [])
        meta = tx_data.get('meta', {})
        post_balances = meta.get('postTokenBalances', [])

        SOL_MINT = "So11111111111111111111111111111111111111112"
        token_account = None
        sol_account = None
        max_token_balance = 0

        for balance_info in post_balances:
            mint = balance_info.get('mint')
            account_index = balance_info.get('accountIndex', -1)
            ui_amount = balance_info.get('uiTokenAmount', {}).get('uiAmount', 0)

            # Map accountIndex to actual account address
            if account_index >= 0 and account_index < len(account_keys):
                account_key = account_keys[account_index]
                # Extract pubkey - could be dict or string
                if isinstance(account_key, dict):
                    account_address = account_key.get('pubkey')
                else:
                    account_address = account_key

                if mint == base_mint and ui_amount > 0:
                    # Store the token account with the largest balance
                    if ui_amount > max_token_balance:
                        token_account = account_address
                        max_token_balance = ui_amount

                elif mint == SOL_MINT and ui_amount > 0:
                    sol_account = account_address

        return token_account, sol_account
    except:
        pass

    return None, None

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
        token_vault_account = None  # Actual token account address
        sol_vault_account = None    # Actual SOL account address

        for balance_info in post_balances:
            mint = balance_info.get("mint")
            ui_amount = balance_info.get("uiTokenAmount", {}).get("uiAmount", 0)
            owner = balance_info.get("owner", "")
            account_index = balance_info.get("accountIndex", -1)

            if mint == base_mint and ui_amount > 0:
                # Collect all token balances (might be in multiple vault accounts)
                # Also store the actual account address (from accountIndex)
                token_balances.append({
                    'amount': ui_amount,
                    'owner': owner,
                    'index': account_index
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
            SELECT symbol, base_mint, amm_id, total_supply, signature, token_vault_account, sol_vault_account
            FROM pools
            WHERE is_pumpswap = 1
            ORDER BY base_mint DESC
        """)
        return [dict(row) for row in cursor.fetchall()]
    except:
        return []

def find_token_account_for_owner(owner, base_mint):
    """Find the token account owned by a specific account that holds a specific mint

    The vault_owner is an account that owns token accounts. We need to find the
    actual token account (ATA or other) that this owner controls.
    """
    try:
        # Strategy: Query all token accounts, then filter in Python
        # This avoids complex memcmp filter issues
        print(f"    [LIVE] Searching for token accounts owned by {owner[:8]}...", end=" ", flush=True)

        result = rpc_call("getProgramAccounts", [
            "TokenkegQfeZyiNwAJsyFbPVwwQQfq5x5wr4ao64jULkJ",  # SPL Token Program
            {
                "encoding": "jsonParsed",
                "filters": [
                    {"dataSize": 165}  # Standard token account size
                ]
            }
        ])

        if not result:
            print(f"✗ (no results)", flush=True)
            return None

        # Filter for accounts owned by our vault_owner with matching mint
        for acct in result:
            try:
                pubkey = acct.get('pubkey')
                data = acct.get('account', {}).get('data', {})

                # Check if this is a jsonParsed token account
                if isinstance(data, dict):
                    mint = data.get('parsed', {}).get('info', {}).get('mint')
                    owner_field = data.get('parsed', {}).get('info', {}).get('owner')

                    if mint == base_mint and owner_field == owner:
                        print(f"✓ {pubkey[:8]}...", flush=True)
                        return pubkey
            except:
                continue

        print(f"✗ (not found)", flush=True)
        return None

    except Exception as e:
        print(f"✗ ({str(e)[:40]})", flush=True)
        return None

def fetch_live_vault_balances(pool):
    """Fetch LIVE current vault balances from blockchain

    Uses stored vault account owners to find and query actual token accounts.
    Returns: {'token_balance': float, 'sol_balance': float} or None
    """
    vault_owner = pool.get('token_vault_account')
    sol_vault_owner = pool.get('sol_vault_account')
    base_mint = pool['base_mint']

    # If vaults not stored, fall back to transaction-based extraction
    if not vault_owner or not sol_vault_owner:
        return None

    try:
        # Find the actual token account owned by the vault_owner
        token_account = find_token_account_for_owner(vault_owner, base_mint)
        if not token_account:
            return None

        print(f"    [LIVE] Fetching current token balance...", end=" ", flush=True)
        token_balance_result = get_token_account_balance(token_account)
        if not token_balance_result:
            print("✗ (account error)")
            return None
        token_balance = token_balance_result.get('ui_amount', 0)
        print(f"✓ ({token_balance:.2f})", flush=True)

        print(f"    [LIVE] Fetching current SOL balance...", end=" ", flush=True)
        sol_balance_result = get_sol_balance(sol_vault_owner)
        if not sol_balance_result:
            print("✗ (no SOL)")
            return None
        sol_balance = sol_balance_result.get('sol', 0)
        print(f"✓ ({sol_balance:.6f})", flush=True)

        if token_balance > 0 and sol_balance > 0:
            return {
                'token_balance': token_balance,
                'sol_balance': sol_balance,
                'vault_owner': vault_owner,
                'sol_vault_owner': sol_vault_owner,
                'is_live': True
            }
        else:
            print(f"    [!] Invalid balances: token={token_balance}, sol={sol_balance}")
            return None

    except Exception as e:
        print(f"✗ ({str(e)[:50]})")
        return None

def fetch_realtime_vault_prices(pool):
    """Fetch REAL-TIME current vault balances using RPC

    Strategy:
    1. Fetch the pool creation transaction
    2. Extract the actual token and SOL account addresses from the transaction
    3. Query the CURRENT balances of those actual accounts
    4. Calculate the real-time price

    This gives us the REAL CURRENT state of the vault, not the snapshot at creation.
    """
    signature = pool.get('signature', '')
    base_mint = pool['base_mint']

    if not signature:
        return None

    try:
        # Fetch the transaction to get actual vault account addresses
        tx_data = get_transaction(signature)
        if not tx_data:
            return None

        # Extract the actual token and SOL account addresses from transaction
        token_account, sol_account = extract_vault_account_addresses(tx_data, base_mint)
        if not token_account or not sol_account:
            return None

        # Fetch CURRENT balances of these accounts
        print(f"    [RT] Token acct: {token_account[:8]}...", end=" ", flush=True)
        token_balance_result = get_token_account_balance(token_account)
        if not token_balance_result:
            print("✗")
            return None
        token_balance = token_balance_result.get('ui_amount', 0)
        print(f"✓", flush=True)

        print(f"    [RT] SOL acct:   {sol_account[:8]}...", end=" ", flush=True)
        sol_balance_result = get_sol_balance(sol_account)
        if not sol_balance_result:
            print("✗")
            return None
        sol_balance = sol_balance_result.get('sol', 0)
        print(f"✓", flush=True)

        # Only return valid prices
        if token_balance > 0 and sol_balance > 0:
            return {
                'token_balance': token_balance,
                'sol_balance': sol_balance,
                'token_account': token_account,
                'sol_account': sol_account,
                'is_realtime': True,
                'data_source': 'CURRENT VAULT BALANCES (Real-time RPC)'
            }
        else:
            # Vault is empty or drained - still return as valid realtime data
            print(f"    [RT] Vault drained: token={token_balance:.6f}, sol={sol_balance:.6f}")
            return {
                'token_balance': token_balance,
                'sol_balance': sol_balance,
                'token_account': token_account,
                'sol_account': sol_account,
                'is_realtime': True,
                'data_source': 'CURRENT VAULT BALANCES (Real-time RPC - DRAINED)'
            }

    except Exception as e:
        return None

def fetch_pool_price(pool):
    """Fetch REAL-TIME or LIVE price for a pool

    Strategy:
    1. Try to fetch REAL-TIME current vault balances
    2. Fall back to pool creation snapshot from transaction
    """
    symbol = pool['symbol'] or pool['base_mint'][:8]
    base_mint = pool['base_mint']
    total_supply = pool['total_supply']
    signature = pool.get('signature', '')

    try:
        # First, try real-time current prices
        print(f"  {symbol:<12} [Attempting REAL-TIME current balances]")
        realtime_result = fetch_realtime_vault_prices(pool)

        if realtime_result:
            token_balance = realtime_result['token_balance']
            sol_balance = realtime_result['sol_balance']

            # Calculate price if we have non-zero balances
            if token_balance > 0 and sol_balance > 0:
                price_sol = sol_balance / token_balance
                price_usd = price_sol * SOL_USD_PRICE
            else:
                # Vault is drained - price is 0
                price_sol = 0
                price_usd = 0

            market_cap = (price_usd * total_supply) if total_supply and price_usd > 0 else None

            return {
                'symbol': symbol,
                'price_sol': price_sol,
                'price_usd': price_usd,
                'liquidity_sol': sol_balance,
                'market_cap': market_cap,
                'token_balance': token_balance,
                'sol_balance': sol_balance,
                'token_mint': base_mint,
                'timestamp': datetime.now().isoformat(),
                'is_realtime': True,
                'data_source': realtime_result.get('data_source', 'REAL-TIME RPC')
            }

        # Fall back to pool creation snapshot
        print(f"  {symbol:<12} [Falling back to pool creation snapshot]")
        print(f"  {symbol:<12} Fetching transaction...", end=" ", flush=True)

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
            'timestamp': datetime.now().isoformat(),
            'is_realtime': False,
            'data_source': 'POOL CREATION SNAPSHOT'
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

    # Parse command-line arguments
    token_mint = None
    pool_signature = None
    use_signature = False

    if len(sys.argv) > 1:
        if sys.argv[1] == "-s" and len(sys.argv) > 2:
            # Signature-based lookup: python script.py -s <SIGNATURE>
            pool_signature = sys.argv[2]
            use_signature = True
            print(f"\n[MODE] Direct signature lookup: {pool_signature[:16]}...")
        else:
            # Token mint lookup: python script.py <TOKEN_MINT>
            token_mint = sys.argv[1]

    if use_signature and pool_signature:
        # Direct signature lookup - no database needed
        print(f"[FETCHING] Fetching pool info from signature...")
        print("-" * 140)

        # Create a minimal pool dict with just the signature
        pool = {
            'symbol': pool_signature[:8],
            'base_mint': None,  # Will be extracted from tx
            'total_supply': None,
            'signature': pool_signature
        }

        # We need to fetch the tx first to get the token mint
        print(f"[EXTRACTING] Getting token mint from transaction...")
        tx_data = get_transaction(pool_signature)
        if not tx_data:
            print(f"[✗] Could not fetch transaction")
            return

        # Extract token mint from transaction (first non-SOL balance)
        SOL_MINT = "So11111111111111111111111111111111111111112"
        for balance_info in tx_data.get('meta', {}).get('postTokenBalances', []):
            mint = balance_info.get('mint')
            if mint and mint != SOL_MINT:
                pool['base_mint'] = mint
                print(f"[FOUND] Token mint: {mint}")
                break

        if not pool['base_mint']:
            print(f"[✗] Could not extract token mint from transaction")
            return

        result = fetch_pool_price(pool)

    elif token_mint:
        # Token mint lookup from database
        if not conn:
            print("[✗] Cannot connect to database")
            return

        cursor = conn.cursor()
        cursor.execute("""
            SELECT symbol, base_mint, amm_id, total_supply, signature
            FROM pools
            WHERE base_mint = ?
        """, (token_mint,))
        pool = cursor.fetchone()

        if not pool:
            # Token not in database - try to find it on blockchain
            print(f"\n[NOT IN DB] Token not found in database, searching blockchain...")
            print(f"[SEARCHING] This may take a moment...")
            found_signature = find_pool_creation_tx(token_mint)

            if not found_signature:
                print(f"[✗] Could not find pool creation transaction on blockchain")
                return

            # Found signature - use it
            pool = {
                'symbol': token_mint[:8],
                'base_mint': token_mint,
                'total_supply': None,
                'signature': found_signature
            }
            print(f"\n[FETCHING] Live price for {token_mint[:8]}...")
        else:
            print(f"\n[FETCHING] Live price for {dict(pool)['symbol']}...")

        print("-" * 140)
        result = fetch_pool_price(dict(pool) if hasattr(pool, '__getitem__') else pool)

    # Show single token result if we fetched one
    if token_mint or use_signature:
        if result:
            is_realtime = result.get('is_realtime', False)
            data_source = result.get('data_source', 'UNKNOWN')
            status_indicator = "[✓ REAL-TIME]" if is_realtime else "[! SNAPSHOT]"

            print(f"\n[✓] BLOCKCHAIN-SOURCED PRICE DATA {status_indicator}")
            print("-" * 140)
            print(f"Token Mint:       {result['token_mint']}")
            print(f"Price (SOL):      {format_price(result['price_sol'])} SOL/token")
            print(f"Price (USD):      {format_price(result['price_usd'])} USD/token")

            balance_label = "Vault Balances (NOW - REAL-TIME):" if is_realtime else "Vault Balances (At Pool Creation):"
            print(f"\n{balance_label}")
            print(f"  Token Balance:  {format_number(result['token_balance'])} {result['token_mint'][:8]}...")
            print(f"  SOL Balance:    {format_number(result['sol_balance'])} SOL")

            # Show vault accounts if available
            if result.get('vault_owner') or result.get('token_account'):
                print(f"\nVault Accounts:")
                if result.get('vault_owner'):
                    print(f"  Token Vault:    {result['vault_owner']}")
                if result.get('token_account'):
                    print(f"  Token Account:  {result['token_account']}")
                if result.get('sol_vault_owner'):
                    print(f"  SOL Vault:      {result['sol_vault_owner']}")
                if result.get('sol_account'):
                    print(f"  SOL Account:    {result['sol_account']}")

            print(f"\nMarket Data:")
            print(f"  Liquidity (SOL): {format_number(result['liquidity_sol'])} SOL")
            print(f"  Market Cap:      ${format_number(result['market_cap'])} USD")
            print(f"  Source:          {data_source}")
            print(f"  Timestamp:       {result['timestamp']}")

    else:
        # No arguments - fetch all tokens from database
        if not conn:
            print("[✗] Cannot connect to database")
            return

        pools = fetch_all_pools(conn)

        if not pools:
            print("[✗] No pools in database")
            return

        print(f"\n[FETCHING] Fetching prices for {len(pools)} PumpSwap tokens via blockchain RPC...")
        print("-" * 235)
        print(f"{'Symbol':<15} {'Price (SOL)':<20} {'Price (USD)':<20} {'SOL Balance':<20} {'Token Address':<44} {'Supply':<12} {'Status':<16}")
        print("-" * 235)

        success = 0
        realtime_count = 0
        for pool in pools:
            result = fetch_pool_price(dict(pool))
            if result:
                success += 1
                sol_balance_str = f"${format_number(result['liquidity_sol'])} SOL"
                supply_str = format_number(pool['total_supply'])
                token_address = result['token_mint']
                is_realtime = result.get('is_realtime', False)
                status = "✓ REAL-TIME" if is_realtime else "! SNAPSHOT"
                if is_realtime:
                    realtime_count += 1
                print(f"{result['symbol']:<15} {format_price(result['price_sol']):<20} {format_price(result['price_usd']):<20} {sol_balance_str:<20} {token_address:<44} {supply_str:<12} {status:<16}")

        print("-" * 235)
        print(f"\n[RESULT] ✓ Fetched {success}/{len(pools)} prices | {realtime_count} real-time | {success - realtime_count} snapshot")

if __name__ == "__main__":
    main()
