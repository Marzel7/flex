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
import time
import sqlite3
import asyncio
import websockets
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path
from threading import Thread

# Helius RPC configuration
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "") or "0ae07551-32df-4d9d-af2a-1925fb7f561f"
HELIUS_RPC = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"

# Alternative API key for WebSocket (separates WebSocket from HTTP load)
# Using different key reduces rate limiting by distributing across endpoints
HELIUS_WEBSOCKET_API_KEY = os.getenv("HELIUS_WEBSOCKET_API_KEY", "") or "f084fae8-d111-4337-9960-2d9c5e02a726"
HELIUS_RPC_WS = f"wss://mainnet.helius-rpc.com/?api-key={HELIUS_WEBSOCKET_API_KEY}"

SOL_DECIMALS = 9
SOL_USD_PRICE = 125  # Current SOL price (updated every 30 mins)

# PumpSwap program ID
PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"

def fetch_sol_price():
    """Fetch current SOL price from CoinGecko API

    Returns:
        float: Current SOL price in USD, or 125 as fallback
    """
    try:
        response = requests.get(
            "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd",
            timeout=5
        )
        if response.status_code == 200:
            data = response.json()
            price = data.get('solana', {}).get('usd', 125)
            return float(price)
    except Exception as e:
        pass
    return 125  # Fallback to default


class VaultPriceFetcher:
    """Fetch live prices from vault balances - Independent from main.py"""

    def __init__(self):
        self.helius_api_key = HELIUS_API_KEY
        self.helius_rpc = HELIUS_RPC

    def rpc_call(self, method, params, retries=2):
        """Make JSON-RPC call to Solana with retry logic and jitter"""
        import random
        try:
            if not self.helius_api_key:
                print(f"[RPC] ✗ No API key configured")
                return None

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": method,
                "params": params
            }

            # Retry logic with exponential backoff + jitter
            for attempt in range(retries + 1):
                try:
                    response = requests.post(self.helius_rpc, json=payload, timeout=10)

                    # Handle rate limiting (429)
                    if response.status_code == 429:
                        print(f"[RPC] ⚠ Rate limited (429) on attempt {attempt + 1}/{retries + 1}: {method}")
                        if attempt < retries:
                            base_wait = 0.5 * (2 ** attempt)  # Exponential backoff
                            jitter = random.uniform(0, 0.5)    # Add jitter to spread retries
                            wait_time = base_wait + jitter
                            time.sleep(wait_time)
                            continue
                        else:
                            print(f"[RPC] ✗ Failed after {retries + 1} attempts due to rate limiting")
                            return None

                    if response.status_code == 200:
                        data = response.json()
                        if "error" in data:
                            error_msg = data.get("error", {}).get("message", "Unknown error")
                            print(f"[RPC] ✗ RPC error from {method}: {error_msg}")
                            return None
                        return data.get("result")

                    print(f"[RPC] ✗ Unexpected status {response.status_code} on {method}")
                    return None
                except requests.exceptions.Timeout:
                    print(f"[RPC] ⚠ Timeout on attempt {attempt + 1}/{retries + 1}: {method}")
                    if attempt < retries:
                        base_wait = 0.5 * (2 ** attempt)
                        jitter = random.uniform(0, 0.5)
                        wait_time = base_wait + jitter
                        time.sleep(wait_time)
                        continue
                    else:
                        print(f"[RPC] ✗ Failed after {retries + 1} attempts due to timeouts")
                        return None

            print(f"[RPC] ✗ All retries exhausted for {method}")
            return None
        except Exception as e:
            print(f"[RPC] ✗ Exception in RPC call: {e}")
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

    def get_transaction(self, signature, retries=12, retry_interval=10, max_duration=120):
        """Get full transaction details with consistent retry interval over extended period

        Some transactions take longer to index on RPC (up to 1-2 minutes).
        Strategy: Retry every 10 seconds for up to 2 minutes instead of exponential backoff.

        Args:
            signature: Transaction signature
            retries: Maximum number of retry attempts (default 12 = 2 minutes with 10s interval)
            retry_interval: Seconds between retries (default 10)
            max_duration: Maximum total time to retry in seconds (default 120 = 2 minutes)
        """
        import random
        start_time = time.time()

        for attempt in range(retries):
            try:
                result = self.rpc_call("getTransaction", [
                    signature,
                    {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
                ], retries=1)  # Single attempt per call, we handle retries here
                if result:
                    elapsed = time.time() - start_time
                    print(f"[RPC] ✓ getTransaction succeeded on attempt {attempt + 1}/{retries} after {elapsed:.1f}s: {signature[:20]}...")
                    return result

                # Check if we've exceeded max duration
                elapsed = time.time() - start_time
                if elapsed >= max_duration:
                    print(f"[RPC] ✗ getTransaction timeout: exceeded {max_duration}s max duration after {attempt + 1} attempts: {signature[:20]}...")
                    break

                # On failure, wait before retry with small jitter
                if attempt < retries - 1:
                    jitter = random.uniform(0, 1)  # Add 0-1s random jitter
                    wait_time = retry_interval + jitter
                    elapsed = time.time() - start_time
                    remaining = max_duration - elapsed
                    print(f"[RPC] ⚠ getTransaction attempt {attempt + 1}/{retries} failed for {signature[:20]}... Waiting {wait_time:.1f}s before retry (elapsed: {elapsed:.1f}s, remaining: {remaining:.1f}s)")
                    time.sleep(wait_time)
            except Exception as e:
                print(f"[RPC] ✗ Exception in getTransaction attempt {attempt + 1}/{retries}: {e}")
                elapsed = time.time() - start_time
                if elapsed < max_duration and attempt < retries - 1:
                    jitter = random.uniform(0, 1)
                    wait_time = retry_interval + jitter
                    time.sleep(wait_time)

        print(f"[RPC] ✗ getTransaction failed after {retries} retries over {time.time() - start_time:.1f}s: {signature[:20]}...")
        return None

    def get_recent_signatures(self, limit=10):
        """Get recent transaction signatures for PumpSwap program"""
        try:
            result = self.rpc_call("getSignaturesForAddress", [
                PUMPSWAP_PROGRAM,
                {"limit": limit}
            ])
            return result if result else []
        except:
            return []

    def get_dexscreener_price(self, token_mint):
        """Fetch price data from DexScreener API"""
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{token_mint}"
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                data = response.json()
                if data.get('pairs') and len(data['pairs']) > 0:
                    pair = data['pairs'][0]
                    return {
                        'price_usd': float(pair.get('priceUsd', 0)),
                        'price_native': float(pair.get('priceNative', 0))
                    }
            return None
        except:
            return None

    def is_pool_creation_transaction(self, tx_data):
        """Check if transaction is a Pump.fun → PumpSwap migration (NOT a new pool creation)

        MIGRATION ONLY: Detects tokens migrating from Pump.fun to PumpSwap
        Does NOT detect brand new pools created directly on PumpSwap.

        Identifies migrations by:
        - Requiring "Instruction: Migrate" (must be present)
        - Excluding transactions with Buy/Sell instructions (those are swaps)
        - Excluding transactions with Swap instructions from other DEXes (Raydium, etc)
        - Excluding failed transactions

        Pump.fun → PumpSwap migration patterns:
        - Contains "Instruction: Migrate" (Pump.fun migration marker)
        - Contains pool initialization (Initialize account, CreatePool, etc)
        - Does NOT contain Buy/Sell/Swap instructions
        - Transaction status is SUCCESS
        """
        try:
            # First check: Exclude failed transactions
            tx_err = tx_data.get('meta', {}).get('err')
            if tx_err:
                return False

            logs = tx_data.get('meta', {}).get('logMessages', [])
            logs_text = ' '.join(logs)

            # CRITICAL: Must have Migrate instruction (Pump.fun → PumpSwap migration marker)
            if 'Instruction: Migrate' not in logs_text:
                return False

            # Exclude swaps (they have Buy/Sell instructions from PumpSwap)
            if 'Instruction: Buy' in logs_text or 'Instruction: Sell' in logs_text:
                return False

            # Exclude other DEX swaps (Raydium, etc have "Instruction: Swap")
            if 'Instruction: Swap' in logs_text:
                return False

            # Check if this looks like pool initialization
            # Migrations should have initialization patterns
            pool_creation_patterns = [
                'initialize',
                'create_pool',
                'InitializePool',
            ]
            has_init_pattern = any(pattern.lower() in logs_text.lower() for pattern in pool_creation_patterns)

            # Must have both: Migrate instruction AND initialization pattern
            is_migration = ('Instruction: Migrate' in logs_text) and has_init_pattern and not ('Instruction: Buy' in logs_text or 'Instruction: Sell' in logs_text)

            return is_migration
        except:
            return False

    def extract_token_from_signature(self, signature):
        """Extract token mint from a transaction signature (migrations only)

        Filters to ONLY detect Pump.fun → PumpSwap migrations.
        Does NOT detect brand new pools created directly on PumpSwap.
        Requires "Instruction: Migrate" in transaction logs.
        """
        try:
            tx_data = self.get_transaction(signature)
            if not tx_data:
                return None
            return self.extract_token_from_tx_data(tx_data)
        except:
            return None

    def extract_token_from_tx_data(self, tx_data):
        """Extract token mint from transaction data (pool creation only)

        Accepts pre-fetched tx_data to avoid redundant RPC calls.
        """
        try:
            if not tx_data:
                return None

            # First verify this is a pool creation, not a swap
            if not self.is_pool_creation_transaction(tx_data):
                return None

            # Look for token mint in transaction logs
            logs = tx_data.get('meta', {}).get('logMessages', [])
            post_balances = tx_data.get('meta', {}).get('postTokenBalances', [])

            # Extract mints from postTokenBalances
            for balance_info in post_balances:
                mint = balance_info.get('mint', '')
                # Skip SOL and wrapped SOL
                if mint and mint != "So11111111111111111111111111111111111111112" and len(mint) == 44:
                    return mint

            return None
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

    def fetch_live_price_for_token(self, token_mint, signature, symbol="", debug=False, tx_data=None):
        """Fetch live vault-based price for a token

        Args:
            token_mint: Token mint address
            signature: Transaction signature
            symbol: Token symbol (for display)
            debug: Enable debug logging
            tx_data: Optional pre-fetched transaction data to avoid redundant RPC calls
        """
        try:
            # Get pool creation transaction (use provided data if available)
            if not tx_data:
                tx_data = self.get_transaction(signature)
            if not tx_data:
                if debug:
                    print(f"    [DEBUG] {symbol} - Could not fetch transaction {signature[:20]}...")
                return None

            # Extract vault accounts from transaction
            token_account, sol_account = self.extract_vault_account_addresses(tx_data, token_mint, debug=debug)
            if not token_account:
                if debug:
                    print(f"    [DEBUG] {symbol} - Could not extract token vault account")
                return None

            if debug:
                print(f"    [DEBUG] {symbol} - Extracted accounts: Token={token_account[:16]}..., SOL={sol_account[:16] if sol_account else 'None'}...")

            # Get CURRENT token balance from blockchain RPC
            token_balance_result = self.get_token_account_balance(token_account)
            if not token_balance_result:
                if debug:
                    print(f"    [DEBUG] {symbol} - Could not fetch token balance")
                return None

            # Get SOL balance - try sol_account first, fallback to searching transaction
            sol_balance_result = None
            if sol_account:
                sol_balance_result = self.get_sol_balance(sol_account)

            if not sol_balance_result:
                if debug:
                    print(f"    [DEBUG] {symbol} - Could not fetch SOL balance from vault account, searching transaction...")
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
                        print(f"    [DEBUG] {symbol} - Found SOL account fallback: {sol_account_fallback[:16]}...")

            if not sol_balance_result:
                if debug:
                    print(f"    [DEBUG] {symbol} - Could not fetch SOL balance from any source")
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

    def calculate_migration_initial_price(self, token_mint, symbol="", debug=False):
        """Calculate initial price for migrating token based on 85 SOL initial liquidity

        PumpFun tokens that migrate to PumpSwap always start with 85 SOL in liquidity.
        If we know the total supply, we can calculate the exact initial price:

        Price = 85 SOL / Total Supply
        Price USD = Price SOL × SOL USD Price

        This is more reliable than fetching from vault for newly migrated tokens
        because the vault data might not reflect the exact migration values.
        """
        try:
            # Fetch total supply
            total_supply = self.get_token_supply(token_mint)
            if not total_supply or total_supply <= 0:
                if debug:
                    print(f"[MIGRATION] ⚠ Could not fetch total supply for {symbol}: {token_mint[:16]}...")
                return None

            # PumpFun migrations always start with 85 SOL
            initial_sol_liquidity = 85.0

            # Calculate initial price
            price_sol = initial_sol_liquidity / total_supply
            price_usd = price_sol * SOL_USD_PRICE

            market_cap = price_usd * total_supply
            fdv = price_usd * total_supply  # At migration, all tokens are in circulation

            if debug:
                print(f"\n[MIGRATION] 🎯 Initial Price Calculation (85 SOL migration):")
                print(f"  Total Supply: {total_supply:,.0f}")
                print(f"  Initial SOL: {initial_sol_liquidity:.2f} SOL")
                print(f"  Price: ${price_usd:.8f} USD (${price_sol:.10f} SOL)")
                print(f"  Market Cap: ${market_cap:,.0f}")
                print(f"  FDV: ${fdv:,.0f}")

            return {
                'price_usd': price_usd,
                'price_sol': price_sol,
                'sol_balance': initial_sol_liquidity,
                'token_balance': total_supply,
                'total_supply': total_supply,
                'market_cap': market_cap,
                'fdv': fdv,
                'is_migration_initial': True
            }
        except Exception as e:
            if debug:
                print(f"[MIGRATION] ✗ Error calculating initial price: {e}")
            return None


class TradingBot:
    """Automated trading bot for PumpSwap tokens with profit tracking"""

    def __init__(self, use_trading=False):
        """Initialize trading bot

        Args:
            use_trading: Enable actual trading (default False for safety)
        """
        self.use_trading = use_trading
        self.trader = None
        self.keypair = None
        self.bought_tokens = set()  # Track tokens we've already bought to prevent duplicates

        if use_trading:
            try:
                # Import trading utilities only if needed
                sys.path.insert(0, str(Path(__file__).parent.parent))
                from utils.load_env import load_env
                from trading_executor import TokenTrader
                from solders.keypair import Keypair

                load_env()

                helius_key = os.environ.get("HELIUS_API_KEY")
                jupiter_key = os.environ.get("JUPITER_API_KEY")
                trading_keypair_env = os.environ.get("TRADING_KEYPAIR")

                print(f"[TRADING BOT] DEBUG: TRADING_KEYPAIR from environ: {trading_keypair_env[:50] if trading_keypair_env else 'NOT SET'}...")

                if helius_key and trading_keypair_env:
                    keypair_array = json.loads(trading_keypair_env)
                    keypair_bytes = bytes(keypair_array)
                    self.keypair = Keypair.from_bytes(keypair_bytes)
                    wallet_addr = str(self.keypair.pubkey())
                    self.trader = TokenTrader(
                        rpc_endpoint=f"https://mainnet.helius-rpc.com/?api-key={helius_key}",
                        network="mainnet",
                        default_slippage_bps=500,
                        default_tip_amount=50000,
                        jupiter_api_key=jupiter_key,
                    )
                    print("[TRADING BOT] ✓ Initialized with trading enabled")
                    print(f"[TRADING BOT] ═══════════════════════════════════════")
                    print(f"[TRADING BOT] Trading Wallet Address:")
                    print(f"[TRADING BOT] {wallet_addr}")
                    print(f"[TRADING BOT] ═══════════════════════════════════════")
            except Exception as e:
                print(f"[TRADING BOT] ⚠ Failed to initialize trading: {e}")
                self.trader = None
                self.keypair = None

    async def execute_buy(self, token_mint: str, symbol: str = "UNKNOWN", sol_amount: float = 0.001) -> Dict:
        """Execute buy transaction for newly detected token"""
        if not self.use_trading or not self.trader:
            return {
                'status': 'skipped',
                'signature': None,
                'output_amount': 0,
                'price_executed': 0,
                'error': 'Trading disabled'
            }

        # Prevent duplicate buys of same token
        if token_mint in self.bought_tokens:
            print(f"[TRADING BOT] ⚠ Token already bought: {token_mint}")
            return {
                'status': 'skipped',
                'signature': None,
                'output_amount': 0,
                'price_executed': 0,
                'error': 'Token already bought'
            }

        # Mark token as bought IMMEDIATELY to prevent race conditions
        # where multiple WebSocket messages try to buy the same token
        self.bought_tokens.add(token_mint)

        try:
            # Try with legacy transaction (direct routes) first to avoid ATA creation issues
            result = await self.trader.buy_token(
                token_mint=token_mint,
                sol_amount=sol_amount,
                user_keypair=self.keypair,
                slippage_bps=500,
                use_legacy_transaction=True  # Use direct routes to avoid ATA issues
            )

            # If first attempt failed, retry once with higher slippage
            if result.status != 'confirmed' or result.output_amount == 0:
                print(f"[TRADING BOT] ⚠ First attempt failed: {result.error}")
                print(f"[TRADING BOT] Retrying with higher slippage (1000 bps)...")

                result = await self.trader.buy_token(
                    token_mint=token_mint,
                    sol_amount=sol_amount,
                    user_keypair=self.keypair,
                    slippage_bps=1000,  # Higher slippage for retry
                    use_legacy_transaction=False  # Try without legacy mode on retry
                )

            if result.status == 'confirmed' or result.output_amount > 0:
                print(f"[TRADING BOT] ✓ Buy executed for {symbol}: {result.output_amount} tokens")
                print(f"[TRADING BOT]   Signature: {result.signature}")
            else:
                print(f"[TRADING BOT] ⚠ Buy failed after retry: {result.error}")

            return {
                'status': result.status,
                'signature': result.signature,
                'output_amount': result.output_amount,
                'price_executed': result.price_executed,
                'error': result.error
            }
        except Exception as e:
            print(f"[TRADING BOT] ✗ Buy failed for {symbol}: {e}")
            return {
                'status': 'failed',
                'signature': None,
                'output_amount': 0,
                'price_executed': 0,
                'error': str(e)
            }

    async def execute_sell(self, token_mint: str, symbol: str, quantity: float) -> Dict:
        """Execute sell transaction when profit target reached"""
        if not self.use_trading or not self.trader:
            return {
                'status': 'skipped',
                'signature': None,
                'output_sol': 0,
                'error': 'Trading disabled'
            }

        try:
            result = await self.trader.sell_token(
                token_mint=token_mint,
                token_amount=quantity,
                user_keypair=self.keypair,
                slippage_bps=500
            )

            print(f"[TRADING BOT] ✓ Sell executed for {symbol}: {result.output_amount} SOL")
            print(f"[TRADING BOT]   Signature: {result.signature}")
            return {
                'status': result.status,
                'signature': result.signature,
                'output_sol': result.output_amount,
                'error': result.error
            }
        except Exception as e:
            print(f"[TRADING BOT] ✗ Sell failed for {symbol}: {e}")
            return {
                'status': 'failed',
                'signature': None,
                'output_sol': 0,
                'error': str(e)
            }

    def update_trade_in_db(self, token_mint: str, buy_price_usd: float,
                          quantity_bought: float, buy_signature: str) -> bool:
        """Update database with buy information"""
        db_path = Path(__file__).parent.parent / 'pumpswap_tokens.db'

        try:
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            cursor = conn.cursor()

            cursor.execute('''
                UPDATE pools
                SET trade_status = 'bought', buy_price_usd = ?, buy_time = ?,
                    buy_signature = ?, quantity_bought = ?
                WHERE base_mint = ?
            ''', (buy_price_usd, datetime.now(), buy_signature, quantity_bought, token_mint))

            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"[DB] ✗ Error updating buy trade: {e}")
            return False

    def update_sell_in_db(self, token_mint: str, sell_price_usd: float,
                         sell_signature: str) -> bool:
        """Update database with sell information and calculate P&L"""
        db_path = Path(__file__).parent.parent / 'pumpswap_tokens.db'

        try:
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            cursor = conn.cursor()

            # Get buy information to calculate P&L
            cursor.execute('''
                SELECT buy_price_usd, quantity_bought FROM pools WHERE base_mint = ?
            ''', (token_mint,))
            result = cursor.fetchone()

            if result:
                buy_price_usd, quantity_bought = result
                if buy_price_usd and quantity_bought:
                    # Calculate profit/loss
                    total_cost = buy_price_usd * quantity_bought
                    total_revenue = sell_price_usd * quantity_bought
                    profit_loss_usd = total_revenue - total_cost
                    profit_loss_percent = (profit_loss_usd / total_cost * 100) if total_cost > 0 else 0

                    cursor.execute('''
                        UPDATE pools
                        SET trade_status = 'sold', sell_price_usd = ?, sell_time = ?,
                            sell_signature = ?, profit_loss_usd = ?, profit_loss_percent = ?
                        WHERE base_mint = ?
                    ''', (sell_price_usd, datetime.now(), sell_signature, profit_loss_usd,
                          profit_loss_percent, token_mint))

                    conn.commit()
                    conn.close()
                    return True

            conn.close()
            return False
        except Exception as e:
            print(f"[DB] ✗ Error updating sell trade: {e}")
            return False


class StandalonePumpSwapListener:
    """Standalone PumpSwap listener - Independent from main.py"""

    def __init__(self, use_trading=False):
        self.price_fetcher = VaultPriceFetcher()
        self.trading_bot = TradingBot(use_trading=use_trading)  # Initialize trading bot
        self.detected_tokens: List[Dict] = []
        self.pumpswap_tokens: List[Dict] = []
        self.start_time = datetime.now()
        self.is_running = True
        self.seen_mints = set()  # Track unique token mints to avoid duplicates
        self.websocket_running = False  # Flag for WebSocket listener
        self.last_sol_price_update = 0  # Track last SOL price update time

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

    def load_tokens_from_db(self) -> List[Dict]:
        """Load all PumpSwap tokens from database"""
        db_path = Path(__file__).parent.parent / 'pumpswap_tokens.db'
        tokens = []

        if not db_path.exists():
            print(f"[INFO] Database is empty - no tokens tracked yet\n")
            return tokens

        try:
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Query all PumpSwap tokens
            cursor.execute('''
                SELECT symbol, name, base_mint, signature, total_supply, dexscreener_price_usd, initial_price_usd
                FROM pools
                WHERE is_pumpswap = 1 AND signature IS NOT NULL
                ORDER BY first_seen DESC
            ''')

            for row in cursor.fetchall():
                tokens.append({
                    'base_mint': row['base_mint'],
                    'symbol': row['symbol'],
                    'name': row['name'],
                    'signature': row['signature'],
                    'total_supply': row['total_supply'],
                    'dexscreener_price_usd': row['dexscreener_price_usd'],
                    'initial_price_usd': row['initial_price_usd']
                })

            conn.close()
        except Exception as e:
            print(f"[ERROR] Could not load from database: {e}\n")

        return tokens

    def add_token_to_db(self, token_mint, signature):
        """Add newly detected token to database for future price tracking"""
        db_path = Path(__file__).parent.parent / 'pumpswap_tokens.db'

        try:
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            cursor = conn.cursor()

            # Create table if it doesn't exist
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS pools (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    base_mint TEXT UNIQUE,
                    signature TEXT,
                    is_pumpswap BOOLEAN DEFAULT 1,
                    first_seen TIMESTAMP,
                    last_updated TIMESTAMP,
                    amm_id TEXT,
                    symbol TEXT,
                    name TEXT,
                    total_supply REAL,
                    dexscreener_price_usd REAL,
                    dexscreener_price_native REAL,
                    last_price_update TIMESTAMP,
                    initial_price_usd REAL DEFAULT 0,
                    trade_status TEXT DEFAULT 'waiting',
                    buy_price_usd REAL,
                    buy_time TIMESTAMP,
                    buy_signature TEXT,
                    sell_price_usd REAL,
                    sell_time TIMESTAMP,
                    sell_signature TEXT,
                    quantity_bought REAL,
                    profit_loss_usd REAL,
                    profit_loss_percent REAL
                )
            ''')

            # Check if token already exists
            cursor.execute('SELECT id FROM pools WHERE base_mint = ?', (token_mint,))
            existing = cursor.fetchone()

            if existing:
                # Token already in DB, just ensure it's marked as PumpSwap
                cursor.execute(
                    'UPDATE pools SET is_pumpswap = 1, signature = ? WHERE base_mint = ?',
                    (signature, token_mint)
                )
                print(f"[DB] Updated existing token in database: {token_mint}")
            else:
                # Insert new token
                cursor.execute('''
                    INSERT INTO pools (
                        base_mint, signature, is_pumpswap, first_seen, last_updated, amm_id
                    ) VALUES (?, ?, 1, ?, ?, ?)
                ''', (token_mint, signature, datetime.now(), datetime.now(), token_mint))
                print(f"[DB] Inserted new token into database: {token_mint}")

            conn.commit()
            conn.close()
            return True
        except sqlite3.IntegrityError as e:
            print(f"[ERROR] Integrity error adding token (likely duplicate): {e}")
            return False
        except Exception as e:
            print(f"[ERROR] Could not add token to database: {e}")
            return False

    def migrate_database_schema(self):
        """Migrate database to add trading columns if they don't exist"""
        db_path = Path(__file__).parent.parent / 'pumpswap_tokens.db'

        if not db_path.exists():
            return True

        try:
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            cursor = conn.cursor()

            # Get current columns
            cursor.execute("PRAGMA table_info(pools)")
            existing_columns = {row[1] for row in cursor.fetchall()}

            # Define trading columns to add
            trading_columns = {
                'trade_status': "TEXT DEFAULT 'waiting'",
                'buy_price_usd': 'REAL',
                'buy_time': 'TIMESTAMP',
                'buy_signature': 'TEXT',
                'sell_price_usd': 'REAL',
                'sell_time': 'TIMESTAMP',
                'sell_signature': 'TEXT',
                'quantity_bought': 'REAL',
                'profit_loss_usd': 'REAL',
                'profit_loss_percent': 'REAL',
                'peak_price_usd': 'REAL',
                'peak_percent_change': 'REAL'
            }

            # Add missing columns
            for col_name, col_type in trading_columns.items():
                if col_name not in existing_columns:
                    cursor.execute(f'ALTER TABLE pools ADD COLUMN {col_name} {col_type}')
                    print(f"[DB] ✓ Added column: {col_name}")

            conn.commit()
            conn.close()
            print(f"[DB] ✓ Database schema migrated successfully")
            return True
        except Exception as e:
            print(f"[DB] ⚠ Migration error (may be non-fatal): {e}")
            return False

    def update_initial_price(self, token_mint, price_result):
        """Update initial migration price and total supply in database"""
        db_path = Path(__file__).parent.parent / 'pumpswap_tokens.db'

        if not db_path.exists():
            return False

        try:
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            cursor = conn.cursor()

            price_usd = price_result.get('price_usd', 0)
            total_supply = price_result.get('total_supply', 0)

            cursor.execute('''
                UPDATE pools
                SET initial_price_usd = ?, total_supply = ?, last_price_update = ?, peak_percent_change = 0
                WHERE base_mint = ?
            ''', (price_usd, total_supply, datetime.now(), token_mint))

            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"[ERROR] Could not update initial price: {e}")
            return False

    def update_dexscreener_price(self, token_mint, price_usd, price_native):
        """Update DexScreener price in database"""
        db_path = Path(__file__).parent.parent / 'pumpswap_tokens.db'

        if not db_path.exists():
            return False

        try:
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            cursor = conn.cursor()

            cursor.execute('''
                UPDATE pools
                SET dexscreener_price_usd = ?, dexscreener_price_native = ?, last_dexscreener_update = ?
                WHERE base_mint = ?
            ''', (price_usd, price_native, datetime.now(), token_mint))

            conn.commit()
            conn.close()
            return True
        except Exception as e:
            return False

    def update_token_supply(self, token_mint, total_supply):
        """Update token total supply in database"""
        db_path = Path(__file__).parent.parent / 'pumpswap_tokens.db'

        if not db_path.exists():
            return False

        try:
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            cursor = conn.cursor()

            cursor.execute('''
                UPDATE pools
                SET total_supply = ?, last_price_update = ?
                WHERE base_mint = ?
            ''', (total_supply, datetime.now(), token_mint))

            conn.commit()
            conn.close()
            return True
        except Exception as e:
            return False

    def update_sol_price_periodically(self) -> None:
        """Background thread to fetch SOL price every 30 minutes"""
        global SOL_USD_PRICE

        while self.is_running:
            try:
                current_time = time.time()

                # Update every 30 minutes (1800 seconds)
                if current_time - self.last_sol_price_update >= 1800:
                    new_price = fetch_sol_price()
                    old_price = SOL_USD_PRICE
                    SOL_USD_PRICE = new_price
                    self.last_sol_price_update = current_time

                    price_change = ((new_price - old_price) / old_price) * 100
                    print(f"[SOL PRICE] Updated: ${old_price:.2f} → ${new_price:.2f} ({price_change:+.2f}%)")

                # Sleep for 1 minute before checking again
                time.sleep(60)
            except Exception as e:
                pass

    def start_sol_price_updater(self) -> None:
        """Start SOL price updater in background thread"""
        price_thread = Thread(target=self.update_sol_price_periodically, daemon=True)
        price_thread.start()
        print("[SOL PRICE] Background price updater thread started (updates every 30 minutes)\n")

    def monitor_profit_targets(self) -> None:
        """Monitor bought tokens and sell at 20% profit (background thread)"""
        while self.is_running:
            try:
                if not self.trading_bot.use_trading or not self.trading_bot.trader:
                    time.sleep(10)
                    continue

                db_path = Path(__file__).parent.parent / 'pumpswap_tokens.db'
                if not db_path.exists():
                    time.sleep(10)
                    continue

                conn = sqlite3.connect(str(db_path), check_same_thread=False)
                cursor = conn.cursor()

                # Get all bought tokens
                cursor.execute('''
                    SELECT base_mint, symbol, buy_price_usd, quantity_bought, dexscreener_price_usd, buy_signature
                    FROM pools WHERE trade_status = 'bought'
                ''')

                bought_tokens = cursor.fetchall()
                conn.close()

                for token_mint, symbol, buy_price, quantity, current_price, buy_signature in bought_tokens:
                    if not buy_price or not quantity:
                        continue

                    # Use current market price
                    if not current_price or current_price <= 0:
                        # Fetch live price if not cached
                        price_result = self.price_fetcher.fetch_live_price_for_token(token_mint, buy_signature or "", symbol)
                        current_price = price_result.get('price_usd', 0) if price_result and isinstance(price_result, dict) else 0

                    if not current_price or current_price <= 0:
                        continue

                    # Check profit percentage
                    profit_pct = ((current_price - buy_price) / buy_price) * 100

                    if profit_pct >= 20.0:
                        print(f"[PROFIT MONITOR] ✓ {symbol[:8]} reached {profit_pct:.1f}% profit! Selling...")
                        try:
                            # Execute sell
                            sell_result = asyncio.run(self.trading_bot.execute_sell(
                                token_mint=token_mint,
                                symbol=symbol,
                                quantity=quantity
                            ))

                            if sell_result['status'] == 'confirmed':
                                # Update DB with sell and P&L
                                self.trading_bot.update_sell_in_db(
                                    token_mint=token_mint,
                                    sell_price_usd=current_price,
                                    sell_signature=sell_result['signature']
                                )
                                print(f"[PROFIT MONITOR] ✓ Sold {symbol[:8]}: Profit {profit_pct:.1f}%")
                            else:
                                print(f"[PROFIT MONITOR] ⚠ Sell failed for {symbol[:8]}: {sell_result['error']}")
                        except Exception as e:
                            print(f"[PROFIT MONITOR] ✗ Error selling {symbol[:8]}: {e}")

                time.sleep(30)  # Check every 30 seconds
            except Exception as e:
                print(f"[PROFIT MONITOR] Error in profit monitoring: {e}")
                time.sleep(30)

    def start_profit_monitor(self) -> None:
        """Start profit monitoring in background thread"""
        if self.trading_bot.use_trading:
            profit_thread = Thread(target=self.monitor_profit_targets, daemon=True)
            profit_thread.start()
            print("[PROFIT MONITOR] Background profit monitor started (checks every 30 seconds)\n")

    def scan_for_new_launches(self, seen_signatures):
        """Scan recent PumpSwap transactions for Pump.fun → PumpSwap migrations

        Filters for pool creation transactions only (not swaps or other operations).
        Also deduplicates by token mint to avoid reporting the same token multiple times.
        Only detects launches from the last 10 minutes (600 seconds).
        Automatically adds new tokens to database for future price tracking.
        """
        import time
        new_launches = []
        current_time = time.time()
        max_age_seconds = 600  # Only detect launches from last 10 minutes

        # Get recent signatures from PumpSwap program
        signatures = self.price_fetcher.get_recent_signatures(limit=50)

        for sig_info in signatures:
            signature = sig_info.get('signature', '')

            if signature in seen_signatures:
                continue

            # Fetch transaction to get accurate blockTime
            tx_data = self.price_fetcher.get_transaction(signature)
            if not tx_data:
                continue

            # Filter by transaction age - only process recent transactions
            block_time = tx_data.get('blockTime', 0)
            if block_time == 0:
                # If blockTime is 0 or missing, skip this transaction
                continue

            tx_age_seconds = current_time - block_time

            if tx_age_seconds > max_age_seconds:
                # Transaction is older than 10 minutes, skip it
                continue

            # Extract token mint from transaction (only pool creations)
            # This filters out swaps, deposits, and other non-migration transactions
            # Note: We're using tx_data we already fetched above to avoid redundant RPC call
            token_mint = self.price_fetcher.extract_token_from_tx_data(tx_data)

            if token_mint:
                # Skip if we've already reported this token mint
                if token_mint not in self.seen_mints:
                    new_launches.append({
                        'token_mint': token_mint,
                        'signature': signature,
                        'timestamp': sig_info.get('blockTime', 0)
                    })
                    self.seen_mints.add(token_mint)

                    # Add to database for future price tracking
                    if self.add_token_to_db(token_mint, signature):
                        print(f"   [✓ ADDED] Token added to database for tracking")

                        # Fetch DexScreener price for the new token
                        dex_price = self.price_fetcher.get_dexscreener_price(token_mint)
                        if dex_price:
                            self.update_dexscreener_price(
                                token_mint,
                                dex_price['price_usd'],
                                dex_price['price_native']
                            )
                            print(f"   [✓ DEXSCREENER] Price: ${dex_price['price_usd']:.8f}")
                        else:
                            print(f"   [⚠] Could not fetch DexScreener price (will use fallback)")
                    else:
                        print(f"   [⚠] Could not add token to database")

                seen_signatures.add(signature)

        return new_launches

    def print_live_table(self):
        """Print live price table for active tokens"""
        if not self.pumpswap_tokens:
            return

        active_tokens = []
        low_count = 0
        fetch_failed_count = 0

        for token_entry in self.pumpswap_tokens:
            # Extract token data and price result (already fetched in run_listener)
            token = token_entry.get('token_data', {})
            price_result = token_entry.get('price_result')

            # Use on-chain price if available
            if price_result:
                sol_balance = price_result.get('sol_balance', 0)
                if sol_balance >= 1:
                    active_tokens.append((token, price_result, 'onchain'))
                else:
                    low_count += 1
            else:
                # On-chain fetch failed - use DexScreener price as fallback
                dexscreener_price = token.get('dexscreener_price_usd', 0)
                if dexscreener_price and dexscreener_price > 0:
                    # Create a fallback result with DexScreener price but attempt to fetch on-chain data
                    fallback_result = {
                        'price_usd': dexscreener_price,
                        'sol_balance': 0,
                        'token_balance': 0,
                        'total_supply': token.get('total_supply')
                    }

                    # Try to fetch actual SOL balance and token balance from vault
                    signature = token.get('signature', '')
                    if signature:
                        try:
                            tx_data = self.price_fetcher.get_transaction(signature)
                            if tx_data:
                                base_mint = token.get('base_mint', '')
                                token_acct, sol_acct = self.price_fetcher.extract_vault_account_addresses(tx_data, base_mint)

                                if token_acct and sol_acct:
                                    token_bal = self.price_fetcher.get_token_account_balance(token_acct)
                                    sol_bal = self.price_fetcher.get_sol_balance(sol_acct)

                                    if token_bal and sol_bal:
                                        fallback_result['token_balance'] = token_bal.get('ui_amount', 0)
                                        fallback_result['sol_balance'] = sol_bal.get('sol', 0)
                        except:
                            pass  # Keep fallback values if fetch fails

                    active_tokens.append((token, fallback_result, 'dexscreener'))
                else:
                    fetch_failed_count += 1

        if active_tokens:
            print(f"\n{'-'*500}")
            print(f"{'Name':<6} {'Current Price':<18} {'Buy Price':<18} {'SOL Balance':<15} {'% Change':<15} {'Peak %':<8} {'Market Cap':<16} {'FDV':<12} {'Src':<3} {'Match':<12} {'Unrealized %':<20} {'P&L':<10} {'Token Address':<31}")
            print(f"{'-'*500}")

            for token, price_result, source in active_tokens:
                base_mint = token.get('base_mint', '')
                name = token.get('name')
                symbol = token.get('symbol')

                # Display name: use token name if available, otherwise use symbol or mint prefix
                if name and name != 'Unknown' and name.strip():
                    # Use first 6 chars of token name for compact display
                    display_name = name[:6]
                elif symbol and symbol != 'Unknown' and symbol.strip():
                    # Fallback to symbol, first 6 chars
                    display_name = symbol[:6]
                else:
                    # Unnamed tokens: show first 6 chars of mint as identifier
                    display_name = base_mint[:6]

                # Get price and balances from result (now includes fallback data)
                price_usd = price_result.get('price_usd', 0)
                sol_balance = price_result.get('sol_balance', 0)
                token_balance = price_result.get('token_balance', 0)

                # Format current price
                price_str = f"${price_usd:.8f}" if price_usd > 0 else "$0.00"

                # Format SOL balance - show actual balance or N/A if couldn't fetch
                if sol_balance > 0:
                    sol_str = f"{sol_balance:.2f} SOL"
                else:
                    sol_str = "N/A"

                # Calculate percentage change: current SOL balance vs 85 SOL at migration
                # Positive = gained SOL (pool value increased)
                # Negative = lost SOL (pool value decreased/drained)
                if sol_balance > 0:
                    sol_change_pct = ((sol_balance - 85) / 85) * 100
                    if sol_change_pct >= 0:
                        price_change_str = f"+{sol_change_pct:.1f}%"
                    else:
                        price_change_str = f"{sol_change_pct:.1f}%"
                else:
                    price_change_str = "N/A"

                # Calculate market cap
                market_cap = price_usd * token_balance if token_balance > 0 else 0
                if market_cap > 1000000:
                    market_cap_str = f"${market_cap/1000000:.2f}M"
                elif market_cap > 1000:
                    market_cap_str = f"${market_cap/1000:.2f}K"
                else:
                    market_cap_str = f"${market_cap:.2f}" if market_cap > 0 else "N/A"

                # Calculate FDV
                total_supply = (price_result.get('total_supply') if price_result else None) or token.get('total_supply')
                if total_supply and total_supply > 0:
                    fdv = price_usd * total_supply
                else:
                    fdv = market_cap if market_cap > 0 else 0

                if fdv > 1000000:
                    fdv_str = f"${fdv/1000000:.2f}M"
                elif fdv > 1000:
                    fdv_str = f"${fdv/1000:.2f}K"
                else:
                    fdv_str = f"${fdv:.2f}" if fdv > 0 else "N/A"

                # Source indicator (icon only to save space)
                source_str = "🔗" if source == 'onchain' else "📊"

                # Calculate match ratio vs DexScreener (only if on-chain)
                if source == 'onchain':
                    # Use cached DexScreener price from database, fallback to fetching fresh
                    dexscreener_price = token.get('dexscreener_price_usd', 0)

                    if not dexscreener_price or dexscreener_price == 0:
                        dex_data = self.price_fetcher.get_dexscreener_price(base_mint)
                        dexscreener_price = dex_data['price_usd'] if dex_data else 0

                        # Cache the fetched price
                        if dexscreener_price > 0:
                            self.update_dexscreener_price(
                                base_mint,
                                dex_data['price_usd'],
                                dex_data.get('price_native', 0)
                            )

                    if dexscreener_price and dexscreener_price > 0 and price_usd > 0:
                        match_ratio = price_usd / dexscreener_price
                        if 0.95 <= match_ratio <= 1.05:
                            match_str = f"✓ {match_ratio:.2f}x"
                        elif 0.90 <= match_ratio <= 1.10:
                            match_str = f"~ {match_ratio:.2f}x"
                        else:
                            match_str = f"⚠ {match_ratio:.2f}x"
                    else:
                        match_str = "—"
                else:
                    match_str = "—"

                # Update peak % change for ALL tokens - track highest % change ever reached
                if price_usd and price_usd > 0:
                    try:
                        db_peak = Path(__file__).parent.parent / 'pumpswap_tokens.db'
                        if db_peak.exists():
                            conn_peak = sqlite3.connect(str(db_peak), check_same_thread=False)
                            cursor_peak = conn_peak.cursor()
                            # Get initial price and current peak % change
                            cursor_peak.execute('SELECT initial_price_usd, peak_percent_change FROM pools WHERE base_mint = ?', (base_mint,))
                            peak_result = cursor_peak.fetchone()

                            if peak_result and peak_result[0]:
                                initial_price = peak_result[0]
                                current_peak_pct = peak_result[1] if peak_result[1] is not None else 0

                                # Calculate current % change from initial price
                                current_pct_change = ((price_usd - initial_price) / initial_price) * 100

                                # Update peak if current % is higher than recorded peak
                                if current_pct_change > current_peak_pct:
                                    cursor_peak.execute('UPDATE pools SET peak_percent_change = ? WHERE base_mint = ?', (current_pct_change, base_mint))
                                    conn_peak.commit()

                            conn_peak.close()
                    except:
                        pass

                # Calculate unrealized gains (current price vs buy price)
                unrealized_str = "—"
                try:
                    db_path = Path(__file__).parent.parent / 'pumpswap_tokens.db'
                    if db_path.exists():
                        conn = sqlite3.connect(str(db_path), check_same_thread=False)
                        cursor = conn.cursor()
                        cursor.execute('''
                            SELECT trade_status, buy_price_usd, profit_loss_percent, profit_loss_usd FROM pools WHERE base_mint = ?
                        ''', (base_mint,))
                        trade_result = cursor.fetchone()
                        conn.close()

                        if trade_result:
                            trade_status, buy_price, pnl_percent, pnl_usd = trade_result

                            # Show unrealized gain if bought but not yet sold
                            if trade_status == 'bought' and buy_price and buy_price > 0:
                                if price_usd and price_usd > 0:
                                    # Have both current price and buy price - calculate gain
                                    unrealized_gain_pct = ((price_usd - buy_price) / buy_price) * 100
                                    # Calculate USD gain (need quantity from database)
                                    db_path_qty = Path(__file__).parent.parent / 'pumpswap_tokens.db'
                                    if db_path_qty.exists():
                                        try:
                                            conn_qty = sqlite3.connect(str(db_path_qty), check_same_thread=False)
                                            cursor_qty = conn_qty.cursor()
                                            cursor_qty.execute('SELECT quantity_bought FROM pools WHERE base_mint = ?', (base_mint,))
                                            qty_result = cursor_qty.fetchone()
                                            conn_qty.close()
                                            if qty_result and qty_result[0]:
                                                qty = qty_result[0]
                                                unrealized_gain_usd = (price_usd - buy_price) * qty
                                                if unrealized_gain_pct >= 0:
                                                    unrealized_str = f"📈 +{unrealized_gain_pct:.1f}% (+${unrealized_gain_usd:.2f})"
                                                else:
                                                    unrealized_str = f"📉 {unrealized_gain_pct:.1f}% (${unrealized_gain_usd:.2f})"
                                            else:
                                                if unrealized_gain_pct >= 0:
                                                    unrealized_str = f"📈 +{unrealized_gain_pct:.1f}%"
                                                else:
                                                    unrealized_str = f"📉 {unrealized_gain_pct:.1f}%"
                                        except:
                                            if unrealized_gain_pct >= 0:
                                                unrealized_str = f"📈 +{unrealized_gain_pct:.1f}%"
                                            else:
                                                unrealized_str = f"📉 {unrealized_gain_pct:.1f}%"
                                    else:
                                        if unrealized_gain_pct >= 0:
                                            unrealized_str = f"📈 +{unrealized_gain_pct:.1f}%"
                                        else:
                                            unrealized_str = f"📉 {unrealized_gain_pct:.1f}%"
                                else:
                                    # Have buy price but no current price - show bought status
                                    unrealized_str = f"💰 Holding (bought @ ${buy_price:.8f})"
                except:
                    pass

                # Extract buy price for display column
                buy_price_str = "—"
                try:
                    db_path = Path(__file__).parent.parent / 'pumpswap_tokens.db'
                    if db_path.exists():
                        conn = sqlite3.connect(str(db_path), check_same_thread=False)
                        cursor = conn.cursor()
                        cursor.execute('SELECT buy_price_usd FROM pools WHERE base_mint = ?', (base_mint,))
                        buy_result = cursor.fetchone()
                        conn.close()

                        if buy_result and buy_result[0]:
                            buy_price_val = buy_result[0]
                            buy_price_str = f"${buy_price_val:.8f}"
                except:
                    pass

                # Display peak % change (highest % gain from initial price)
                peak_change_str = "—"
                try:
                    db_path = Path(__file__).parent.parent / 'pumpswap_tokens.db'
                    if db_path.exists():
                        conn = sqlite3.connect(str(db_path), check_same_thread=False)
                        cursor = conn.cursor()
                        cursor.execute('SELECT peak_percent_change FROM pools WHERE base_mint = ?', (base_mint,))
                        peak_result = cursor.fetchone()
                        conn.close()

                        if peak_result and peak_result[0] is not None:
                            peak_pct = peak_result[0]
                            if peak_pct >= 0:
                                peak_change_str = f"\033[92m+{peak_pct:.1f}%\033[0m"
                            else:
                                peak_change_str = f"\033[91m{peak_pct:.1f}%\033[0m"
                except:
                    pass

                # Fetch P&L from database
                pnl_str = "—"
                try:
                    db_path = Path(__file__).parent.parent / 'pumpswap_tokens.db'
                    if db_path.exists():
                        conn = sqlite3.connect(str(db_path), check_same_thread=False)
                        cursor = conn.cursor()
                        cursor.execute('''
                            SELECT trade_status, profit_loss_percent, profit_loss_usd FROM pools WHERE base_mint = ?
                        ''', (base_mint,))
                        pnl_result = cursor.fetchone()
                        conn.close()

                        if pnl_result:
                            trade_status, pnl_percent, pnl_usd = pnl_result
                            if trade_status == 'bought' and pnl_percent is None:
                                pnl_str = "💰"
                            elif trade_status == 'sold' and pnl_percent is not None:
                                if pnl_percent >= 0:
                                    pnl_str = f"✓ +{pnl_percent:.1f}% (+${pnl_usd:.2f})"
                                else:
                                    pnl_str = f"✗ {pnl_percent:.1f}% (${pnl_usd:.2f})"
                except:
                    pass

                print(f"{display_name:<6} {price_str:<18} {buy_price_str:<18} {sol_str:<15} {price_change_str:<15} {peak_change_str:<8} {market_cap_str:<16} {fdv_str:<12} {source_str:<3} {match_str:<12} {unrealized_str:<20} {pnl_str:<10} {base_mint:<31}")

            print(f"{'-'*500}")
            on_chain_count = sum(1 for _, _, src in active_tokens if src == 'onchain')
            dex_fallback_count = sum(1 for _, _, src in active_tokens if src == 'dexscreener')
            print(f"\n[RESULT] ✓ OnChain: {on_chain_count} | DexScreen Fallback: {dex_fallback_count} | Low liquidity: {low_count} | No price: {fetch_failed_count}")

    async def listen_websocket(self) -> None:
        """Listen to PumpSwap program via WebSocket for live migration events

        This listens to the PumpSwap program (pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA)
        and receives live transaction notifications for Pump.fun → PumpSwap migrations.
        """
        print(f"[WEBSOCKET] Connecting to: {HELIUS_RPC_WS}")

        while self.is_running:
            try:
                async with websockets.connect(HELIUS_RPC_WS) as ws:
                    self.websocket_running = True
                    print(f"[WEBSOCKET] ✓ Connected to {PUMPSWAP_PROGRAM}")

                    # Subscribe to PumpSwap program for pool creation transactions
                    subscribe_msg = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "logsSubscribe",
                        "params": [
                            {
                                "mentions": [PUMPSWAP_PROGRAM]
                            },
                            {
                                "commitment": "confirmed"
                            }
                        ]
                    }
                    await ws.send(json.dumps(subscribe_msg))
                    print(f"[WEBSOCKET] Subscribed to PumpSwap program transactions")

                    while self.is_running:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=30)
                            data = json.loads(msg)

                            # Process subscription response
                            if 'params' in data and 'result' in data['params']:
                                result = data['params']['result']
                                value = result.get('value', {})
                                logs = value.get('logs', [])
                                signature = value.get('signature', '')
                                err = value.get('err')

                                # Skip failed transactions
                                if err:
                                    continue

                                # Validate signature length (Helius WebSocket sends 87-88 chars)
                                # 87 chars appears to be normal for Helius WebSocket (slightly truncated format)
                                # 88 chars is the standard Solana format from HTTP RPC
                                if not signature or len(signature) < 87 or len(signature) > 88:
                                    continue  # Skip invalid signatures silently (normal behavior)

                                # Check if this is a pool creation transaction (migration)
                                if self.price_fetcher.is_pool_creation_transaction({
                                    'meta': {'logMessages': logs, 'err': err},
                                    'blockTime': int(time.time())
                                }):
                                    print(f"[WEBSOCKET] 🚨 Migration detected: {signature[:60]}...")

                                    # Fetch full transaction to extract token (with retry logic)
                                    print(f"[WEBSOCKET] Starting transaction fetch for {signature[:20]}... (3 retries, will show detailed logging)")
                                    full_tx = self.price_fetcher.get_transaction(signature, retries=3)
                                    if not full_tx:
                                        print(f"[WEBSOCKET] ✗ Failed to fetch transaction after 3 retries: {signature[:60]}...")
                                        continue

                                    print(f"[WEBSOCKET] ✓ Successfully fetched transaction, extracting token...")

                                    token_mint = self.price_fetcher.extract_token_from_tx_data(full_tx)
                                    if not token_mint:
                                        print(f"[WEBSOCKET] ✗ Could not extract token from transaction data: {signature}")
                                        continue

                                    if token_mint not in self.seen_mints:
                                        self.seen_mints.add(token_mint)
                                        print(f"[WEBSOCKET] ✓ Added token: {token_mint}")
                                        # Add to database for tracking
                                        success = self.add_token_to_db(token_mint, signature)
                                        if success:
                                            print(f"[WEBSOCKET] ✓ Token persisted to database: {token_mint}")

                                            # Auto-buy if trading is enabled
                                            if self.trading_bot.use_trading and self.trading_bot.trader:
                                                # Double-check database to prevent duplicate buys from race conditions
                                                db_path = Path(__file__).parent.parent / 'pumpswap_tokens.db'
                                                check_conn = sqlite3.connect(str(db_path), check_same_thread=False)
                                                check_cursor = check_conn.cursor()
                                                check_cursor.execute('SELECT trade_status FROM pools WHERE base_mint = ?', (token_mint,))
                                                existing = check_cursor.fetchone()
                                                check_conn.close()

                                                if existing and existing[0] == 'bought':
                                                    print(f"[TRADING BOT] ⚠ Token already bought (in database): {token_mint}")
                                                else:
                                                    print(f"[TRADING BOT] Attempting auto-buy for new token: {token_mint}")
                                                    try:
                                                        buy_result = await self.trading_bot.execute_buy(
                                                            token_mint=token_mint,
                                                            symbol=token_mint[:8],
                                                            sol_amount=0.001
                                                        )
                                                        # Record buy if we have a signature (tx was sent)
                                                        # Don't just check status as confirmation might timeout
                                                        print(f"[TRADING BOT] Buy result: sig={buy_result['signature']}, amount={buy_result['output_amount']}, price={buy_result['price_executed']}")
                                                        if buy_result['signature'] and buy_result['output_amount'] and buy_result['output_amount'] > 0:
                                                            # Record buy in database
                                                            success = self.trading_bot.update_trade_in_db(
                                                                token_mint=token_mint,
                                                                buy_price_usd=buy_result['price_executed'],
                                                                quantity_bought=buy_result['output_amount'],
                                                                buy_signature=buy_result['signature']
                                                            )
                                                            if success:
                                                                print(f"[TRADING BOT] ✓ Auto-buy recorded: {buy_result['output_amount']} tokens @ ${buy_result['price_executed']:.8f}")
                                                            else:
                                                                print(f"[TRADING BOT] ✗ Failed to record buy in database")
                                                        else:
                                                            print(f"[TRADING BOT] ⚠ Auto-buy failed: sig={buy_result['signature']}, amount={buy_result['output_amount']}, error={buy_result['error']}")
                                                    except Exception as e:
                                                        print(f"[TRADING BOT] ✗ Auto-buy error: {e}")
                                        else:
                                            print(f"[WEBSOCKET] ✗ FAILED to persist token to database: {token_mint}")

                                        # Immediately calculate and display initial price for this token
                                        print(f"\n[WEBSOCKET] Calculating initial price from 85 SOL migration...")
                                        price_result = self.price_fetcher.calculate_migration_initial_price(
                                            token_mint, symbol="?", debug=True
                                        )

                                        # If calculation fails, fall back to live price fetch
                                        if not price_result:
                                            print(f"[WEBSOCKET] Initial price calculation failed, fetching live price from vault...")
                                            price_result = self.price_fetcher.fetch_live_price_for_token(
                                                token_mint, signature, "?", debug=True, tx_data=full_tx
                                            )

                                        # Display the new token's price immediately (any amount of SOL)
                                        if price_result:
                                            print(f"\n{'-'*150}")
                                            print(f"{'Token':<35} {'Price (USD)':<20} {'SOL Balance':<15} {'Market Cap':<20} {'FDV':<20} {'Source':<12}")
                                            print(f"{'-'*150}")

                                            base_mint = token_mint
                                            price_usd = price_result.get('price_usd', 0)
                                            sol_balance = price_result.get('sol_balance', 0)
                                            token_balance = price_result.get('token_balance', 0)

                                            # Format output
                                            price_str = f"${price_usd:.8f}" if price_usd > 0 else "$0.00"
                                            sol_str = f"{sol_balance:.2f} SOL"

                                            # Calculate market cap
                                            market_cap = price_usd * token_balance if token_balance > 0 else 0
                                            if market_cap > 1000000:
                                                market_cap_str = f"${market_cap/1000000:.2f}M"
                                            elif market_cap > 1000:
                                                market_cap_str = f"${market_cap/1000:.2f}K"
                                            else:
                                                market_cap_str = f"${market_cap:.2f}" if market_cap > 0 else "N/A"

                                            # Calculate FDV
                                            total_supply = price_result.get('total_supply', 0)
                                            if total_supply and total_supply > 0:
                                                fdv = price_usd * total_supply
                                            else:
                                                fdv = market_cap if market_cap > 0 else 0

                                            if fdv > 1000000:
                                                fdv_str = f"${fdv/1000000:.2f}M"
                                            elif fdv > 1000:
                                                fdv_str = f"${fdv/1000:.2f}K"
                                            else:
                                                fdv_str = f"${fdv:.2f}" if fdv > 0 else "N/A"

                                            print(f"{base_mint:<35} {price_str:<20} {sol_str:<15} {market_cap_str:<20} {fdv_str:<20} 🔗 OnChain")
                                            print(f"{'-'*150}\n")

                                            # Store initial price in database
                                            if self.update_initial_price(token_mint, price_result):
                                                print(f"[WEBSOCKET] ✓ Stored initial price in database")
                                            else:
                                                print(f"[WEBSOCKET] ⚠ Could not store initial price in database")
                                        else:
                                            print(f"[WEBSOCKET] ⚠ Could not fetch price for newly detected token")
                        except asyncio.TimeoutError:
                            continue
                        except Exception as e:
                            print(f"[WEBSOCKET] Error processing message: {e}")
                            break

            except Exception as e:
                print(f"[WEBSOCKET] Connection error: {e}")
                self.websocket_running = False
                if self.is_running:
                    await asyncio.sleep(5)  # Wait before reconnecting

    def run_websocket(self) -> None:
        """Run WebSocket listener in async event loop"""
        try:
            asyncio.run(self.listen_websocket())
        except Exception as e:
            print(f"[WEBSOCKET] Error in async loop: {e}")

    def start_websocket_listener(self) -> None:
        """Start WebSocket listener in background thread"""
        if not self.websocket_running:
            ws_thread = Thread(target=self.run_websocket, daemon=True)
            ws_thread.start()
            print("[WEBSOCKET] Background listener thread started")

    def run_listener(self) -> None:
        """Run the standalone listener - continuously monitors and updates prices"""
        print("[LISTENER] Starting continuous PumpSwap listener (independent from main.py)\n")
        print("[LISTENER] Scanning for new launches and printing price table every 60 seconds\n")

        # Migrate database schema to add trading columns if needed
        print("[LISTENER] Checking database schema...")
        self.migrate_database_schema()

        # Start WebSocket listener in background for live migration detection
        self.start_websocket_listener()
        print("[LISTENER] WebSocket listener running in background for LIVE migration detection\n")

        # Start SOL price updater in background
        self.start_sol_price_updater()

        # Start profit monitor in background (if trading enabled)
        self.start_profit_monitor()

        try:
            refresh_interval = 60  # Refresh price table every 60 seconds
            last_refresh = 0
            active_mints = set()  # Track which tokens we're monitoring
            cycle_count = 0

            while self.is_running:
                current_time = time.time()

                # Load tokens from database
                # (WebSocket listener populates this in real-time)
                all_tokens = self.load_tokens_from_db()

                if not all_tokens:
                    print("[WARNING] No tokens found in database")
                    time.sleep(5)
                    continue

                # Detect new launches from database
                current_mints = {t['base_mint'] for t in all_tokens}
                db_new_tokens = current_mints - active_mints
                active_mints = current_mints

                # Update prices and print table every 60 seconds
                if current_time - last_refresh >= refresh_interval:
                    cycle_count += 1
                    print(f"\n{'='*200}")
                    print(f"[CYCLE {cycle_count}] Price Update at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                    print(f"{'='*200}")

                    self.pumpswap_tokens = []  # Clear old tokens

                    # Fetch prices for all tokens
                    for token_data in all_tokens:
                        if not self.is_running:
                            break

                        token_mint = token_data.get('base_mint', '')
                        symbol = token_data.get('symbol', '?')
                        signature = token_data.get('signature', '')

                        if not signature:
                            continue

                        # Always fetch live prices from vaults for current state
                        price_result = self.price_fetcher.fetch_live_price_for_token(
                            token_mint, signature, symbol
                        )

                        # Update total supply if we got a price result
                        if price_result and price_result.get('total_supply'):
                            self.update_token_supply(token_mint, price_result['total_supply'])

                        # Always add token, even if price fetch failed (price_result is None)
                        self.pumpswap_tokens.append({
                            'token_data': token_data,
                            'price_result': price_result
                        })

                    # Print live table
                    self.print_live_table()
                    last_refresh = current_time

                # Sleep briefly to avoid busy waiting
                time.sleep(1)

        except KeyboardInterrupt:
            print("\n\n[LISTENER] Stopping listener...")
            self.is_running = False

        except Exception as e:
            print(f"\n[LISTENER] Error: {e}")
            import traceback
            traceback.print_exc()
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
                print(f"\n{'-'*200}")
                print(f"{'Symbol':<15} {'Price (USD)':<20} {'SOL Balance':<15} {'Market Cap':<20} {'FDV':<20} {'Match':<12} {'Token Address':<38}")
                print(f"{'-'*200}")

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

                    # Calculate match ratio vs DexScreener price
                    dexscreener_price = token.get('dexscreener_price_usd', 0)
                    if dexscreener_price and dexscreener_price > 0 and price_usd > 0:
                        match_ratio = price_usd / dexscreener_price
                        if 0.95 <= match_ratio <= 1.05:
                            match_str = f"✓ {match_ratio:.2f}x"
                        elif 0.90 <= match_ratio <= 1.10:
                            match_str = f"~ {match_ratio:.2f}x"
                        else:
                            match_str = f"⚠ {match_ratio:.2f}x"
                    elif dexscreener_price and dexscreener_price > 0:
                        match_str = "N/A"
                    else:
                        match_str = "—"

                    print(f"{symbol:<15} {price_str:<20} {sol_str:<15} {market_cap_str:<20} {fdv_str:<20} {match_str:<12} {base_mint:<38}")

                print(f"{'-'*200}")

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
        print("[LISTENER] ✅ Goodbye!")
        sys.exit(0)


def main():
    """Run standalone PumpSwap listener"""
    # Check if trading should be enabled
    use_trading = os.environ.get("ENABLE_TRADING", "").lower() == "true"

    listener = StandalonePumpSwapListener(use_trading=use_trading)

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

    # Print trading status
    if use_trading:
        print("[TRADING] ⚠️  AUTO-TRADING ENABLED - Bot will buy new tokens and sell at 20% profit\n")
    else:
        print("[TRADING] ℹ️  Auto-trading disabled. To enable, set: export ENABLE_TRADING=true\n")

    # Run listener
    try:
        listener.run_listener()
    finally:
        listener.print_summary()


if __name__ == "__main__":
    main()
