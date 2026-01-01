#!/usr/bin/env python3
"""
Standalone PumpSwap Listener with Real-Time Vault Price Updates

This test is INDEPENDENT from main.py - it demonstrates fetching live vault-based
prices for PumpSwap tokens without requiring the full application.

Features:
- Simulates pool detection by querying recent transactions
- Fetches LIVE prices from blockchain vaults for detected pools
- Calculates prices from SOL/Token ratio
- Identifies liquidity status (ACTIVE, LOW, DRAINED)
- No external dependencies beyond RPC

REQUIREMENTS:
- Helius API key for RPC access: https://www.helius.dev/
- Set environment variable: export HELIUS_API_KEY="your-key-here"

Usage:
  export HELIUS_API_KEY="your-api-key"
  python tests/test_pumpswap_listener.py

  The script will:
  - Query for recent PumpSwap transactions
  - Extract pool addresses and token mints
  - Fetch LIVE vault-based prices for each token
  - Display prices with liquidity status

Press Ctrl+C to stop.
"""

import sys
import json
import signal
import requests
import os
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path

# Helius RPC configuration
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "") or "0ae07551-32df-4d9d-af2a-1925fb7f561f"
HELIUS_RPC = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
SOL_DECIMALS = 9
SOL_USD_PRICE = 125  # Current SOL price

# PumpSwap program ID
PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"


class VaultPriceFetcher:
    """Fetch live prices from vault balances - Independent from main.py"""

    def __init__(self):
        self.helius_api_key = HELIUS_API_KEY
        self.helius_rpc = HELIUS_RPC

    def rpc_call(self, method, params):
        """Make JSON-RPC call to Solana"""
        try:
            if not self.helius_api_key:
                return None

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": method,
                "params": params
            }
            response = requests.post(self.helius_rpc, json=payload, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if "error" in data:
                    return None
                return data.get("result")
            return None
        except Exception as e:
            return None

    def get_token_supply(self, token_mint):
        """Get token total supply from blockchain"""
        try:
            result = self.rpc_call("getTokenSupply", [token_mint])
            if result and result.get('value'):
                supply = int(result['value'].get('amount', 0))
                decimals = result['value'].get('decimals', 0)
                ui_supply = supply / (10 ** decimals) if decimals else supply
                return ui_supply
            return None
        except:
            return None

    def get_token_account_balance(self, token_account):
        """Get token balance from blockchain"""
        try:
            result = self.rpc_call("getTokenAccountBalance", [token_account])
            if result and result.get('value'):
                value = result['value']
                amount = int(value.get('amount', 0))
                decimals = value.get('decimals', 0)
                ui_amount = amount / (10 ** decimals) if decimals else amount
                return {'amount': amount, 'decimals': decimals, 'ui_amount': ui_amount}
            return None
        except:
            return None

    def get_sol_balance(self, sol_account):
        """Get SOL balance from blockchain"""
        try:
            result = self.rpc_call("getBalance", [sol_account])
            if result:
                lamports = result.get('value', 0)
                sol = lamports / (10 ** SOL_DECIMALS)
                return {'lamports': lamports, 'sol': sol}
            return None
        except:
            return None

    def get_transaction(self, signature):
        """Get full transaction details"""
        try:
            result = self.rpc_call("getTransaction", [
                signature,
                {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
            ])
            return result
        except:
            return None

    def extract_vault_account_addresses(self, tx_data, base_mint, debug=False):
        """Extract token and SOL vault accounts from transaction

        For PumpSwap: Identifies token/SOL accounts owned by the same pool account
        Uses owner field to group accounts by pool, then selects smallest balances
        (smallest = active bonding curve, largest = liquidity reserves)
        """
        try:
            if not tx_data:
                return None, None

            message = tx_data.get('transaction', {}).get('message', {})
            account_keys = message.get('accountKeys', [])
            meta = tx_data.get('meta', {})
            post_balances = meta.get('postTokenBalances', [])

            SOL_MINT = "So11111111111111111111111111111111111111112"

            # Group accounts by owner (pool account)
            pools = {}

            # Extract account addresses and group by owner
            for balance_info in post_balances:
                mint = balance_info.get('mint')
                account_index = balance_info.get('accountIndex', -1)
                ui_amount = balance_info.get('uiTokenAmount', {}).get('uiAmount', 0)
                owner = balance_info.get('owner', '')  # The pool/vault account

                if account_index >= 0 and account_index < len(account_keys):
                    account_key = account_keys[account_index]
                    if isinstance(account_key, dict):
                        account_address = account_key.get('pubkey')
                    else:
                        account_address = account_key

                    if not owner:
                        continue

                    if owner not in pools:
                        pools[owner] = {
                            'token_accounts': [],
                            'sol_accounts': [],
                            'pool_account': owner
                        }

                    if mint == base_mint and ui_amount > 0:
                        pools[owner]['token_accounts'].append((account_address, ui_amount, account_index))
                    elif mint == SOL_MINT and ui_amount > 0:
                        pools[owner]['sol_accounts'].append((account_address, ui_amount, account_index))

            # Find the pool with both token and SOL accounts
            token_account = None
            sol_account = None

            for pool_owner, accounts in pools.items():
                if accounts['token_accounts'] and accounts['sol_accounts']:
                    # Found a pool with both token and SOL
                    # Pick smallest token account (active bonding curve, not reserve)
                    token_acct, token_amt, token_idx = min(accounts['token_accounts'], key=lambda x: x[1])
                    sol_acct, sol_amt, sol_idx = min(accounts['sol_accounts'], key=lambda x: x[1])

                    if debug:
                        print(f"\n    [DEBUG] Pool analysis:")
                        print(f"      Pool owner: {pool_owner[:16]}...")
                        print(f"      Token accounts: {len(accounts['token_accounts'])}")
                        for addr, amt, idx in sorted(accounts['token_accounts'], key=lambda x: x[1]):
                            marker = " ← SELECTED (smallest)" if addr == token_acct else ""
                            print(f"        [{idx}] {amt:,.2f} @ {addr[:16]}...{marker}")
                        print(f"      SOL accounts: {len(accounts['sol_accounts'])}")
                        for addr, amt, idx in sorted(accounts['sol_accounts'], key=lambda x: x[1]):
                            marker = " ← SELECTED (smallest)" if addr == sol_acct else ""
                            print(f"        [{idx}] {amt:,.2f} @ {addr[:16]}...{marker}")

                    token_account = token_acct
                    sol_account = sol_acct
                    break

            return token_account, sol_account
        except Exception as e:
            if debug:
                print(f"    [DEBUG] Error in extract_vault_account_addresses: {e}")
            return None, None

    def fetch_live_price_for_token(self, token_mint, signature, symbol="", debug=False):
        """Fetch live vault-based price for a token"""
        try:
            # Get pool creation transaction
            tx_data = self.get_transaction(signature)
            if not tx_data:
                if debug:
                    print(f"    [DEBUG] Could not fetch transaction {signature}")
                return None

            # Extract vault accounts from transaction
            token_account, sol_account = self.extract_vault_account_addresses(tx_data, token_mint, debug=debug)
            if not token_account:
                if debug:
                    print(f"    [DEBUG] Could not extract token vault account")
                return None

            if debug:
                print(f"    [DEBUG] Extracted accounts:")
                print(f"      Token Vault: {token_account}")
                print(f"      SOL Vault: {sol_account}")

            # Get CURRENT token balance from blockchain RPC
            token_balance_result = self.get_token_account_balance(token_account)
            if not token_balance_result:
                if debug:
                    print(f"    [DEBUG] Could not fetch token balance")
                return None

            # Get SOL balance - try sol_account first, fallback to searching transaction
            sol_balance_result = None
            if sol_account:
                sol_balance_result = self.get_sol_balance(sol_account)

            if not sol_balance_result:
                if debug:
                    print(f"    [DEBUG] Could not fetch SOL balance from vault account, searching transaction...")
                # Try to find SOL balance from postBalances in transaction
                meta = tx_data.get('meta', {})
                post_balances = meta.get('postBalances', [])
                message = tx_data.get('transaction', {}).get('message', {})
                account_keys = message.get('accountKeys', [])

                max_sol_amount = 0
                sol_account_fallback = None

                for i, balance in enumerate(post_balances):
                    if i < len(account_keys) and balance > max_sol_amount:
                        # Check if this account has a reasonable SOL amount (skip fee payers/system accounts)
                        account_key = account_keys[i]
                        if isinstance(account_key, dict):
                            addr = account_key.get('pubkey')
                        else:
                            addr = account_key

                        if balance / (10 ** SOL_DECIMALS) >= 1:  # At least 1 SOL
                            max_sol_amount = balance
                            sol_account_fallback = addr

                if sol_account_fallback:
                    sol_balance_result = self.get_sol_balance(sol_account_fallback)
                    if debug:
                        print(f"    [DEBUG] Found SOL account fallback: {sol_account_fallback}")

            if not sol_balance_result:
                if debug:
                    print(f"    [DEBUG] Could not fetch SOL balance from any source")
                return None

            if not token_balance_result or not sol_balance_result:
                if debug:
                    print(f"    [DEBUG] Could not fetch balances (token={token_balance_result}, sol={sol_balance_result})")
                return None

            token_balance = token_balance_result.get('ui_amount', 0)
            sol_balance = sol_balance_result.get('sol', 0)

            if debug:
                decimals = token_balance_result.get('decimals', 0)
                raw_amount = token_balance_result.get('amount', 0)
                print(f"    [DEBUG] Balance details:")
                print(f"      Token: {token_balance:.8f} (decimals={decimals}, raw={raw_amount})")
                print(f"      SOL: {sol_balance:.8f}")

            # Determine liquidity status
            drain_status = None
            if token_balance > 0 and sol_balance > 0:
                drain_status = None
            elif sol_balance == 0 and token_balance > 0:
                drain_status = 'LIQUIDITY_DRAINED'
            elif token_balance == 0 and sol_balance > 0:
                drain_status = 'TOKENS_SWEPT'
            else:
                drain_status = 'FULLY_DRAINED'

            # Calculate price - minimum 1 SOL required for reliable pricing
            if token_balance > 0 and sol_balance >= 1:
                price_sol = sol_balance / token_balance
                price_usd = price_sol * SOL_USD_PRICE
            else:
                price_sol = 0
                price_usd = 0

            # Fetch total supply from blockchain
            total_supply = self.get_token_supply(token_mint)

            return {
                'price_usd': price_usd,
                'price_sol': price_sol,
                'sol_balance': sol_balance,
                'token_balance': token_balance,
                'token_account': token_account,
                'sol_account': sol_account,
                'drain_status': drain_status,
                'total_supply': total_supply,
                'is_realtime': True
            }
        except Exception as e:
            return None


class StandalonePumpSwapListener:
    """Standalone PumpSwap listener - Independent from main.py"""

    def __init__(self):
        self.price_fetcher = VaultPriceFetcher()
        self.detected_tokens: List[Dict] = []
        self.pumpswap_tokens: List[Dict] = []
        self.start_time = datetime.now()
        self.is_running = True

    def print_header(self) -> None:
        """Print startup header"""
        print("\n" + "="*100)
        print("  STANDALONE PUMPSWAP LISTENER - Independent Price Fetcher")
        print("="*100)
        print(f"\nStarted: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print("\nCapabilities:")
        print("  • Queries PumpSwap transactions from blockchain")
        print("  • Extracts vault account addresses")
        print("  • Fetches LIVE vault-based prices")
        print("  • Calculates prices from SOL/Token ratio")
        print("  • Identifies liquidity status")
        print("\nNote: This test is INDEPENDENT from main.py")
        print("\nPress Ctrl+C to stop\n")
        print("="*100 + "\n")

    def fetch_and_log_price(self, token_mint, signature, symbol, debug=False):
        """Fetch and display live price for a token"""
        price_result = self.price_fetcher.fetch_live_price_for_token(token_mint, signature, symbol, debug=debug)

        if price_result:
            price_usd = price_result['price_usd']
            price_sol = price_result['price_sol']
            sol_balance = price_result['sol_balance']
            token_balance = price_result['token_balance']
            drain_status = price_result.get('drain_status')

            # Determine liquidity status
            if sol_balance >= 1:
                status = "✓ ACTIVE"
            elif drain_status == 'LIQUIDITY_DRAINED':
                status = "⚠ LIQUIDITY_DRAINED"
            elif drain_status == 'TOKENS_SWEPT':
                status = "⚠ TOKENS_SWEPT"
            elif drain_status == 'FULLY_DRAINED':
                status = "⚠ FULLY_DRAINED"
            else:
                status = "⚠ LOW LIQUIDITY"

            price_display = f"${price_usd:.8f}" if price_usd > 0 else "$0.00"
            print(f"[LIVE PRICE] {symbol:<12} {price_display}/token ({price_sol:.8f} SOL)")
            print(f"[VAULT DATA] {symbol:<12} SOL: ${sol_balance:.2f} | Tokens: {token_balance:.0f} | {status}")

            return price_result
        else:
            print(f"[LIVE PRICE] {symbol:<12} ⚠ Could not fetch live price from vaults")
            return None

    def run_listener(self) -> None:
        """Run the standalone listener - fetches prices for known PumpSwap tokens"""
        print("[LISTENER] Starting standalone PumpSwap listener (independent from main.py)\n")

        try:
            import sqlite3

            # Fetch all PumpSwap tokens from database (matching price_template)
            db_path = Path(__file__).parent.parent / 'pumpswap_tokens.db'
            test_tokens = []

            if db_path.exists():
                print(f"[SETUP] Loading tokens from database: {db_path.name}\n")
                try:
                    conn = sqlite3.connect(str(db_path), check_same_thread=False)
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()

                    # Query all PumpSwap tokens (matching price_template.py)
                    cursor.execute('''
                        SELECT symbol, base_mint, signature, total_supply, dexscreener_price_usd
                        FROM pools
                        WHERE is_pumpswap = 1 AND signature IS NOT NULL
                        ORDER BY base_mint DESC
                    ''')

                    for row in cursor.fetchall():
                        test_tokens.append({
                            'base_mint': row['base_mint'],
                            'symbol': row['symbol'] or row['base_mint'][:8],
                            'signature': row['signature'],
                            'total_supply': row['total_supply'],
                            'dexscreener_price_usd': row['dexscreener_price_usd']
                        })

                    conn.close()
                except Exception as e:
                    print(f"[ERROR] Could not load from database: {e}\n")
            else:
                print(f"[ERROR] Database not found: {db_path}\n")

            print("="*100)

            for token_data in test_tokens:
                if not self.is_running:
                    break

                token_mint = token_data.get('base_mint', '')
                symbol = token_data.get('symbol', '?')
                signature = token_data.get('signature', '')

                if not signature:
                    print(f"\n⚠ Skipping {symbol} - no signature available\n")
                    continue

                # Debug mode disabled - vault extraction now correct
                debug_enabled = False

                print(f"\n🚀 Fetching LIVE price for {symbol}")
                print(f"   Token: {token_mint}")
                print(f"   Signature: {signature[:32]}...\n")

                price_result = self.fetch_and_log_price(token_mint, signature, symbol, debug=debug_enabled)

                if price_result:
                    self.pumpswap_tokens.append(token_data)
                    print(f"   ✓ Successfully fetched live price\n")
                else:
                    print(f"   ⚠ Could not fetch price (pool may be drained or signature invalid)\n")

                print("="*100)

        except KeyboardInterrupt:
            print("\n\n[LISTENER] Stopping listener...")
            self.is_running = False

        except Exception as e:
            print(f"\n[LISTENER] Error: {e}")
            self.is_running = False

    def print_summary(self) -> None:
        """Print final summary with table"""
        end_time = datetime.now()
        duration = (end_time - self.start_time).total_seconds()

        print("\n" + "="*120)
        print("  LISTENER SUMMARY - LIVE VAULT PRICES")
        print("="*120)

        print(f"\nSession Duration: {duration:.1f} seconds")
        print(f"Started: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Stopped: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")

        # Print price table (active tokens only)
        if self.pumpswap_tokens:
            active_tokens = []
            low_count = 0

            for token in self.pumpswap_tokens:
                # Fetch live price from blockchain vaults (on-chain calculation only)
                price_result = self.price_fetcher.fetch_live_price_for_token(
                    token.get('base_mint', ''),
                    token.get('signature', ''),
                    token.get('symbol', '?')
                )

                if price_result:
                    sol_balance = price_result.get('sol_balance', 0)
                    if sol_balance >= 1:
                        active_tokens.append((token, price_result))
                    else:
                        low_count += 1

            if active_tokens:
                print(f"\n{'-'*165}")
                print(f"{'Symbol':<15} {'Price (USD)':<20} {'SOL Balance':<15} {'Market Cap':<20} {'FDV':<20} {'Token Address':<50}")
                print(f"{'-'*165}")

                for token, price_result in active_tokens:
                    symbol = token.get('symbol', '?')[:15]
                    base_mint = token.get('base_mint', '')
                    price_usd = price_result.get('price_usd', 0)
                    sol_balance = price_result.get('sol_balance', 0)
                    token_balance = price_result.get('token_balance', 0)

                    # Format price
                    price_str = f"${price_usd:.8f}" if price_usd > 0 else "$0.00"
                    sol_str = f"{sol_balance:.2f} SOL"

                    # Calculate market cap (current vault balance)
                    market_cap = price_usd * token_balance if token_balance > 0 else 0
                    if market_cap > 1000000:
                        market_cap_str = f"${market_cap/1000000:.2f}M"
                    elif market_cap > 1000:
                        market_cap_str = f"${market_cap/1000:.2f}K"
                    else:
                        market_cap_str = f"${market_cap:.2f}"

                    # Calculate FDV (Fully Diluted Valuation)
                    # FDV = Price × Total Supply (from price_result if fetched from blockchain, otherwise from database)
                    total_supply = price_result.get('total_supply') or token.get('total_supply')
                    if total_supply and total_supply > 0:
                        fdv = price_usd * total_supply
                    else:
                        fdv = market_cap  # Use market cap as FDV if total supply not available

                    if fdv > 1000000:
                        fdv_str = f"${fdv/1000000:.2f}M"
                    elif fdv > 1000:
                        fdv_str = f"${fdv/1000:.2f}K"
                    else:
                        fdv_str = f"${fdv:.2f}"

                    print(f"{symbol:<15} {price_str:<20} {sol_str:<15} {market_cap_str:<20} {fdv_str:<20} {base_mint:<50}")

                print(f"{'-'*165}")

            print(f"\n[RESULT] ✓ Fetched {len(self.pumpswap_tokens)}/10 prices | {len(active_tokens)} active | {low_count} low/drained")
        else:
            print(f"\nNo prices fetched")

        print("\n" + "="*120)
        print("This listener is INDEPENDENT and does not require main.py")
        print("="*120 + "\n")

    def signal_handler(self, sig, frame):
        """Handle Ctrl+C gracefully"""
        print("\n\n[LISTENER] Caught interrupt signal, shutting down...")
        self.is_running = False


def main():
    """Run standalone PumpSwap listener"""
    listener = StandalonePumpSwapListener()

    # Set up signal handler for Ctrl+C
    signal.signal(signal.SIGINT, listener.signal_handler)

    # Print startup info
    listener.print_header()

    # Check API key
    if not HELIUS_API_KEY:
        print("[!] WARNING: HELIUS_API_KEY not set - price fetching will be disabled")
        print("    Set environment variable to enable: export HELIUS_API_KEY='your-api-key'\n")
    else:
        print("[SETUP] Helius API configured\n")

    # Run listener
    try:
        listener.run_listener()
    finally:
        listener.print_summary()


if __name__ == "__main__":
    main()
