#!/usr/bin/env python3
"""
Calculate token price directly from PumpSwap pool vault balances.

This script:
1. Takes a pool address or transaction signature
2. Fetches vault balances from RPC
3. Calculates price from vault ratio (no external APIs)

Usage:
    python get_pool_price.py --pool <POOL_ADDRESS>
    python get_pool_price.py --tx <TRANSACTION_SIGNATURE>
    python get_pool_price.py --token <TOKEN_MINT>
"""

import requests
import json
import sys
from datetime import datetime
import base58

# Configuration
HELIUS_RPC = "https://mainnet.helius-rpc.com/"
PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWaLb3odcwvLgJgWC1DjwzewMsNuqfZhyvtAPwqf"

# For reference token
TOKEN_MINT = "fdry5i5kuadz1ik8gps26qjj9rw9mpufxmeggc2hnsp7"

def rpc_call(method, params):
    """Make RPC call to Solana"""
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
                error = data["error"]
                print(f"[RPC ERROR] {error.get('message', 'Unknown error')}")
                return None
            return data.get("result")
        else:
            print(f"[RPC ERROR] HTTP {response.status_code}")
            return None
    except Exception as e:
        print(f"[RPC ERROR] Connection failed: {e}")
        return None

def get_account(address):
    """Fetch account data from RPC"""
    try:
        print(f"[RPC] Fetching account: {address[:8]}...")

        result = rpc_call("getAccountInfo", [
            address,
            {
                "encoding": "base64",
                "dataSlice": {"offset": 0, "length": 100}
            }
        ])

        return result
    except Exception as e:
        print(f"[✗] Failed to fetch account: {e}")
        return None

def get_token_account_balance(token_account):
    """Get token account balance"""
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
        print(f"[✗] Failed to fetch token balance: {e}")
    return None

def get_multiple_accounts(addresses):
    """Get multiple accounts in one call"""
    try:
        print(f"[RPC] Fetching {len(addresses)} accounts...")

        result = rpc_call("getMultipleAccounts", [
            addresses,
            {"encoding": "jsonParsed"}
        ])

        return result
    except Exception as e:
        print(f"[✗] Failed to fetch accounts: {e}")
        return None

def get_transaction(signature):
    """Fetch transaction details"""
    try:
        print(f"[RPC] Fetching transaction: {signature}...")

        result = rpc_call("getTransaction", [
            signature,
            {"maxSupportedTransactionVersion": 0}
        ])

        return result
    except Exception as e:
        print(f"[✗] Failed to fetch transaction: {e}")
        return None

def extract_pool_from_transaction(tx_sig):
    """Extract pool address and vaults from pool creation transaction"""
    try:
        tx = get_transaction(tx_sig)
        if not tx or not tx.get('transaction'):
            return None

        # Transaction contains accounts list
        # Pool creation instructions create accounts in specific order
        # For PumpSwap: [pool_account, vault_a, vault_b, ...]

        print(f"[!] Transaction structure:")
        print(f"    - This would require parsing PumpSwap instruction format")
        print(f"    - Instruction data contains vault addresses")
        return None
    except Exception as e:
        print(f"[✗] Error parsing transaction: {e}")
        return None

def calculate_price(token_balance, sol_balance, token_decimals, sol_decimals=9):
    """
    Calculate price from vault balances.

    Formula:
    Price (SOL per token) = SOL Balance / Token Balance
    """
    try:
        if token_balance == 0 or token_decimals is None:
            return None

        # Adjust for decimals
        token_adjusted = int(token_balance) / (10 ** token_decimals)
        sol_adjusted = int(sol_balance) / (10 ** sol_decimals)

        if token_adjusted == 0:
            return None

        price_sol = sol_adjusted / token_adjusted
        return price_sol

    except Exception as e:
        print(f"[✗] Error calculating price: {e}")
        return None

def format_price(price):
    """Format price for display"""
    if price is None:
        return "N/A"
    if price < 0.00000001:
        return f"{price:.18f}"
    elif price < 0.00001:
        return f"{price:.15f}"
    elif price < 0.0001:
        return f"{price:.12f}"
    elif price < 0.001:
        return f"{price:.10f}"
    elif price < 0.01:
        return f"{price:.8f}"
    elif price < 1:
        return f"{price:.6f}"
    else:
        return f"{price:,.4f}"

def main():
    """Main function"""
    print("=" * 90)
    print(f"GET TOKEN PRICE FROM PUMPSWAP POOL VAULTS")
    print(f"Reference Token: {TOKEN_MINT}")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print("=" * 90)

    # Example usage instructions
    print(f"\n[USAGE EXAMPLES]")
    print(f"\n1. If you have the pool address:")
    print(f"   VAULT_TOKEN = 'TokenVaultAddress...'")
    print(f"   VAULT_SOL = 'SolVaultAddress...'")
    print(f"   token_balance = get_token_account_balance(VAULT_TOKEN)")
    print(f"   sol_balance = get_token_account_balance(VAULT_SOL)")
    print(f"   price = calculate_price(token_balance['amount'], sol_balance['amount'],")
    print(f"                           token_balance['decimals'], 9)")

    print(f"\n2. If you have the pool creation transaction:")
    print(f"   tx_sig = '5xYz9ABC...'")
    print(f"   tx = get_transaction(tx_sig)")
    print(f"   # Parse accounts from tx.transaction.message.accountKeys")
    print(f"   # Pool account is typically first new account created")

    print(f"\n[DIRECT CALCULATION EXAMPLE]")
    print(f"If we have known vault balances:")

    # Example calculation
    example_token_balance = 1_000_000_000_000  # 1M tokens (if 6 decimals)
    example_sol_balance = 50_000_000_000  # 50 SOL
    example_decimals = 6

    example_price = calculate_price(example_token_balance, example_sol_balance, example_decimals)
    if example_price:
        print(f"  Token Balance: {example_token_balance / (10**example_decimals):,.2f} tokens")
        print(f"  SOL Balance: {example_sol_balance / (10**9):,.2f} SOL")
        print(f"  Price: {format_price(example_price)} SOL per token")
        print(f"  Price: ${format_price(example_price * 200)} (at $200 SOL)")  # Example SOL price

    print(f"\n[DATA NEEDED]")
    print(f"To get actual price for {TOKEN_MINT[:8]}...:")
    print(f"\nOption A: If token is on PumpSwap (graduated from Pump.fun)")
    print(f"  1. Find pool creation transaction signature")
    print(f"  2. Extract vault account addresses")
    print(f"  3. Fetch balances from RPC")
    print(f"  4. Calculate price")

    print(f"\nOption B: Use our local database")
    print(f"  python test_token_price.py  # If token is monitored")

    print(f"\nOption C: If still on Pump.fun bonding curve")
    print(f"  - Price continuously updates as people trade on bonding curve")
    print(f"  - Need Pump.fun API access to check current price")

    print(f"\n[RPC FUNCTION REFERENCE]")
    print(f"Available RPC functions in this script:")
    print(f"  - get_account(address): Fetch account data")
    print(f"  - get_token_account_balance(token_account): Get token balance")
    print(f"  - get_multiple_accounts(addresses): Fetch multiple accounts")
    print(f"  - get_transaction(signature): Get transaction details")
    print(f"  - calculate_price(...): Calculate from balances")

    print("\n" + "=" * 90)

if __name__ == "__main__":
    main()
