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
SOL_DECIMALS = 9
SOL_USD_PRICE = 125  # Current SOL price

# PumpSwap program ID
PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"

# WebSocket configuration for live event listening
HELIUS_RPC_WS = HELIUS_RPC.replace('https://', 'wss://').replace('http://', 'ws://')


class VaultPriceFetcher:
    """Fetch live prices from vault balances - Independent from main.py"""

    def __init__(self):
        self.helius_api_key = HELIUS_API_KEY
        self.helius_rpc = HELIUS_RPC

    def rpc_call(self, method, params, retries=2):
        """Make JSON-RPC call to Solana with retry logic"""
        try:
            if not self.helius_api_key:
                return None

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": method,
                "params": params
            }

            # Retry logic with exponential backoff
            for attempt in range(retries + 1):
                try:
                    response = requests.post(self.helius_rpc, json=payload, timeout=10)

                    # Handle rate limiting (429)
                    if response.status_code == 429:
                        if attempt < retries:
                            wait_time = 0.5 * (2 ** attempt)  # Exponential backoff
                            time.sleep(wait_time)
                            continue
                        else:
                            return None

                    if response.status_code == 200:
                        data = response.json()
                        if "error" in data:
                            return None
                        return data.get("result")

                    return None
                except requests.exceptions.Timeout:
                    if attempt < retries:
                        wait_time = 0.5 * (2 ** attempt)
                        time.sleep(wait_time)
                        continue
                    else:
                        return None

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

    def fetch_live_price_for_token(self, token_mint, signature, symbol="", debug=False):
        """Fetch live vault-based price for a token"""
        try:
            # Get pool creation transaction
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


class StandalonePumpSwapListener:
    """Standalone PumpSwap listener - Independent from main.py"""

    def __init__(self):
        self.price_fetcher = VaultPriceFetcher()
        self.detected_tokens: List[Dict] = []
        self.pumpswap_tokens: List[Dict] = []
        self.start_time = datetime.now()
        self.is_running = True
        self.seen_mints = set()  # Track unique token mints to avoid duplicates
        self.websocket_running = False  # Flag for WebSocket listener

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
            print(f"[ERROR] Database not found: {db_path}\n")
            return tokens

        try:
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Query all PumpSwap tokens
            cursor.execute('''
                SELECT symbol, name, base_mint, signature, total_supply, dexscreener_price_usd
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
                    'dexscreener_price_usd': row['dexscreener_price_usd']
                })

            conn.close()
        except Exception as e:
            print(f"[ERROR] Could not load from database: {e}\n")

        return tokens

    def add_token_to_db(self, token_mint, signature):
        """Add newly detected token to database for future price tracking"""
        db_path = Path(__file__).parent.parent / 'pumpswap_tokens.db'

        if not db_path.exists():
            return False

        try:
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            cursor = conn.cursor()

            # Check if token already exists
            cursor.execute('SELECT id FROM pools WHERE base_mint = ?', (token_mint,))
            existing = cursor.fetchone()

            if existing:
                # Token already in DB, just ensure it's marked as PumpSwap
                cursor.execute(
                    'UPDATE pools SET is_pumpswap = 1, signature = ? WHERE base_mint = ?',
                    (signature, token_mint)
                )
            else:
                # Insert new token
                cursor.execute('''
                    INSERT INTO pools (
                        base_mint, signature, is_pumpswap, first_seen, last_updated, amm_id
                    ) VALUES (?, ?, 1, ?, ?, ?)
                ''', (token_mint, signature, datetime.now(), datetime.now(), token_mint))

            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"[ERROR] Could not add token to database: {e}")
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
            print(f"\n{'-'*250}")
            print(f"{'Name':<32} {'Price (USD)':<20} {'SOL Balance':<15} {'Market Cap':<20} {'FDV':<20} {'Source':<12} {'Match':<12} {'Token Address':<35}")
            print(f"{'-'*250}")

            for token, price_result, source in active_tokens:
                base_mint = token.get('base_mint', '')
                name = token.get('name')
                symbol = token.get('symbol')

                # Display name: use token name if available, otherwise use symbol or mint prefix
                if name and name != 'Unknown':
                    # Use full token name, limited to 30 chars for display
                    display_name = name[:30]
                elif symbol and symbol != 'Unknown':
                    # Fallback to symbol
                    display_name = symbol[:30]
                else:
                    # Unnamed tokens: show first 30 chars of mint as identifier
                    display_name = base_mint[:30]

                # Get price and balances from result (now includes fallback data)
                price_usd = price_result.get('price_usd', 0)
                sol_balance = price_result.get('sol_balance', 0)
                token_balance = price_result.get('token_balance', 0)

                # Format price
                price_str = f"${price_usd:.8f}" if price_usd > 0 else "$0.00"

                # Format SOL balance - show actual balance or N/A if couldn't fetch
                if sol_balance > 0:
                    sol_str = f"{sol_balance:.2f} SOL"
                else:
                    sol_str = "N/A"

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

                # Source indicator
                source_str = "🔗 OnChain" if source == 'onchain' else "📊 DexScreen"

                # Calculate match ratio vs DexScreener (only if on-chain)
                if source == 'onchain':
                    dexscreener_price = token.get('dexscreener_price_usd', 0)

                    # Fetch DexScreener price on-demand if not in database
                    if not dexscreener_price or dexscreener_price == 0:
                        dex_data = self.price_fetcher.get_dexscreener_price(base_mint)
                        if dex_data:
                            dexscreener_price = dex_data['price_usd']
                            # Also update database for next time
                            self.update_dexscreener_price(
                                base_mint,
                                dex_data['price_usd'],
                                dex_data['price_native']
                            )

                    if dexscreener_price and dexscreener_price > 0 and price_usd > 0:
                        match_ratio = price_usd / dexscreener_price
                        if 0.95 <= match_ratio <= 1.05:
                            match_str = f"✓ {match_ratio:.2f}x"
                        elif 0.90 <= match_ratio <= 1.10:
                            match_str = f"~ {match_ratio:.2f}x"
                        else:
                            match_str = f"⚠ {match_ratio:.2f}x"
                    elif dexscreener_price and dexscreener_price > 0:
                        match_str = "—"
                    else:
                        match_str = "—"
                else:
                    match_str = "—"

                print(f"{display_name:<32} {price_str:<20} {sol_str:<15} {market_cap_str:<20} {fdv_str:<20} {source_str:<12} {match_str:<12} {base_mint:<35}")

            print(f"{'-'*250}")
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

                                # Check if this is a pool creation transaction (migration)
                                if signature and self.is_pool_creation_transaction({
                                    'meta': {'logMessages': logs, 'err': err},
                                    'blockTime': int(time.time())
                                }):
                                    print(f"[WEBSOCKET] 🚨 Migration detected: {signature}")

                                    # Add to database if not already seen
                                    if signature not in self.seen_mints:
                                        self.seen_mints.add(signature)
                                        print(f"[WEBSOCKET] Processing migration: {signature}")
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

        # Start WebSocket listener in background for live migration detection
        self.start_websocket_listener()
        print("[LISTENER] WebSocket listener running in background for LIVE migration detection\n")

        try:
            refresh_interval = 60  # Refresh every 60 seconds
            last_refresh = 0
            active_mints = set()  # Track which tokens we're monitoring
            seen_signatures = set()  # Track signatures we've already seen
            cycle_count = 0

            while self.is_running:
                current_time = time.time()

                # Scan for new launches on-chain
                new_launches = self.scan_for_new_launches(seen_signatures)

                if new_launches:
                    print(f"\n[🆕 NEW LAUNCHES] Detected {len(new_launches)} new token(s) on-chain:")
                    for launch in new_launches[:5]:  # Show first 5
                        print(f"   Token: {launch['token_mint']}")
                        print(f"   Sig:   {launch['signature']}")
                    if len(new_launches) > 5:
                        print(f"   ... and {len(new_launches) - 5} more")

                # Load tokens from database
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

                        price_result = self.price_fetcher.fetch_live_price_for_token(
                            token_mint, signature, symbol
                        )

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
