#!/usr/bin/env python3
"""
Pump.Fun → PumpSwap Migration Listener

Detects token migrations from Pump.Fun bonding curve to PumpSwap AMM via WebSocket.
When a migration is detected, runs post-migration analyzer to assess risk.
"""

import asyncio
import json
import os
import re
import sqlite3
import time
import websockets
import aiohttp
import requests
from datetime import datetime
from typing import Set, Optional, List
from pump_fun_post_migration_analyzer import PostMigrationAnalyzer
from realtime_creator_funding_extractor import extract_funding_for_new_token
from realtime_wallet_clustering_extractor import trigger_wallet_clustering
from dotenv import load_dotenv

# Import settings checker (will be imported dynamically when needed)
def get_migration_setting(key: str, default=True) -> bool:
    """Get a migration setting from file (default implementation)"""
    try:
        import json
        import os
        settings_file = "migration_settings.json"
        if os.path.exists(settings_file):
            with open(settings_file) as f:
                settings = json.load(f)
                return settings.get(key, default)
    except:
        pass
    return default

load_dotenv()

# === Config ===
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
RPC_URL = os.getenv("RPC_URL", "")
RPC_URL_2 = os.getenv("RPC_URL_2", "")

# WebSocket: Try Helius first, fall back to public Solana
HELIUS_RPC_WS = f"wss://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}" if HELIUS_API_KEY else "wss://api.mainnet-beta.solana.com/"

# HTTP: Use QuickNode if available, otherwise Helius, then public
RPC_HTTP = RPC_URL if RPC_URL else (f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}" if HELIUS_API_KEY else "https://api.mainnet-beta.solana.com")

# RPC failover chain: Primary QuickNode -> Secondary QuickNode -> Helius -> Public
RPC_URLS = [url for url in [RPC_URL, RPC_URL_2] if url]  # QuickNodes
RPC_URLS.append(f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}" if HELIUS_API_KEY else "https://api.mainnet-beta.solana.com")  # Helius fallback
RPC_URLS.append("https://api.mainnet-beta.solana.com")  # Public fallback

PUMPFUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"

DB_PATH = "pumpswap_tokens.db"


class PumpFunCurveListener:
    """Detects Pump.Fun → PumpSwap migrations via WebSocket and analyzes them"""

    def __init__(self):
        self.seen_mints: Set[str] = set()
        self.detected_migrations: Set[str] = set()
        self.analyzed_tokens = {}
        self.db_lock = asyncio.Lock()
        self.websocket_connected = False
        self.websocket_msg_count = 0  # Track message receipt
        self.websocket_migration_count = 0  # Track migrations detected
        self._ensure_db()
        print(f"[INIT] Pump.Fun → PumpSwap Migration Listener ready", flush=True)
        print(f"[INIT] Monitoring PumpSwap program: {PUMPSWAP_PROGRAM}", flush=True)
        print(f"[INIT] WebSocket: {HELIUS_RPC_WS[:60]}...", flush=True)
        print(f"[INIT] HTTP RPC: {RPC_HTTP[:60]}...", flush=True)

    async def _post_rpc_with_fallback(self, payload: dict, timeout: int = 10) -> Optional[dict]:
        """
        Post to RPC with automatic failover chain.
        
        Tries: Primary QuickNode -> Secondary QuickNode -> Helius -> Public Solana
        Returns: JSON response data or None if all fail
        """
        try:
            async with aiohttp.ClientSession() as session:
                for i, rpc_url in enumerate(RPC_URLS):
                    try:
                        async with session.post(rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                            if resp.status == 200:
                                return await resp.json()
                            elif resp.status == 429:
                                # Rate limited, try next in chain (silently)
                                if i < len(RPC_URLS) - 1:
                                    continue
                            else:
                                # Other error, try next
                                if i < len(RPC_URLS) - 1:
                                    continue
                    except asyncio.TimeoutError:
                        if i < len(RPC_URLS) - 1:
                            continue
                    except Exception as e:
                        if i < len(RPC_URLS) - 1:
                            continue
                
                # All RPC endpoints failed
                return None
        except Exception as e:
            print(f"[RPC_ERROR] {e}", flush=True)
            return None

    # --- Database ---
    def _ensure_db(self):
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        cursor = conn.cursor()

        # Post-migration token analysis with live on-chain price and market cap tracking
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS token_analysis (
                mint TEXT PRIMARY KEY,
                analyzed_at REAL,
                total_txs INTEGER,
                total_events INTEGER,
                events_parsed INTEGER,
                mint_concentration REAL,
                unique_minters_ratio REAL,
                sell_suppression_ratio REAL,
                mint_velocity_sec REAL,
                buy_size_variance REAL,
                sell_volume_concentration REAL,
                creator_activity_ratio REAL,
                post_migration_mint_concentration REAL,
                post_migration_unique_minters_ratio REAL,
                post_migration_sell_suppression_ratio REAL,
                post_migration_mint_velocity_sec REAL,
                post_migration_buy_size_variance REAL,
                post_migration_sell_volume_concentration REAL,
                post_migration_creator_activity_ratio REAL,
                post_migration_coverage REAL,
                rug_probability REAL,
                risk_level TEXT,
                migration_tx TEXT,
                price_current REAL,
                price_highest REAL,
                market_cap_current REAL,
                market_cap_highest REAL,
                market_cap_highest_at TIMESTAMP,
                price_updated_at TIMESTAMP,
                price_source TEXT,
                pool_address TEXT,
                creator_address TEXT,
                creator_reputation TEXT,
                earliest_tx_creator TEXT,
                creator_is_blocked INTEGER DEFAULT 0,
                network_risk INTEGER DEFAULT 0,
                connected_malicious_count INTEGER,
                rug_indicator TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Creator network tracking - stores SOL transfer destinations
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS creator_sol_transfers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_address TEXT NOT NULL,
                destination_address TEXT NOT NULL,
                total_amount REAL DEFAULT 0,
                transfer_count INTEGER DEFAULT 0,
                first_detected_at TIMESTAMP,
                last_detected_at TIMESTAMP,
                is_pool_address INTEGER DEFAULT 0,
                UNIQUE(creator_address, destination_address)
            )
        """)

        # Creator networks - identifies groups of creators sharing destinations
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS creator_networks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_address TEXT NOT NULL,
                connected_creators TEXT NOT NULL,  -- JSON array of connected creator addresses
                shared_destinations TEXT NOT NULL,  -- JSON array of shared destination addresses
                network_size INTEGER,  -- Number of creators in network
                network_risk_level TEXT,  -- CRITICAL, HIGH, MEDIUM, LOW based on connected ruggers
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(creator_address)
            )
        """)

        # Add columns if they don't exist (for backward compatibility)
        try:
            cursor.execute("PRAGMA table_info(token_analysis)")
            columns = [col[1] for col in cursor.fetchall()]
            
            if "creator_is_blocked" not in columns:
                cursor.execute("ALTER TABLE token_analysis ADD COLUMN creator_is_blocked INTEGER DEFAULT 0")
                print("[DB] ✅ Added creator_is_blocked column to token_analysis", flush=True)
            
            if "network_risk" not in columns:
                cursor.execute("ALTER TABLE token_analysis ADD COLUMN network_risk INTEGER DEFAULT 0")
                print("[DB] ✅ Added network_risk column to token_analysis", flush=True)
            
            if "connected_malicious_count" not in columns:
                cursor.execute("ALTER TABLE token_analysis ADD COLUMN connected_malicious_count INTEGER")
                print("[DB] ✅ Added connected_malicious_count column to token_analysis", flush=True)
        except Exception as e:
            pass  # Columns likely already exist

        # Add network columns to creator_blocklist if they don't exist
        try:
            cursor.execute("PRAGMA table_info(creator_blocklist)")
            columns = [col[1] for col in cursor.fetchall()]
            if "connected_to_malicious" not in columns:
                cursor.execute("ALTER TABLE creator_blocklist ADD COLUMN connected_to_malicious INTEGER DEFAULT 0")
                print("[DB] ✅ Added connected_to_malicious column to creator_blocklist", flush=True)
            if "network_members" not in columns:
                cursor.execute("ALTER TABLE creator_blocklist ADD COLUMN network_members TEXT")
                print("[DB] ✅ Added network_members column to creator_blocklist", flush=True)
        except Exception as e:
            pass  # Columns likely already exist

        conn.commit()
        conn.close()

    async def _store_analysis(self, mint: str, analysis: dict, signature: str = None, pool_address: str = None):
        """Store post-migration analysis results"""
        async with self.db_lock:
            try:
                conn = sqlite3.connect(DB_PATH, timeout=60)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=60000")
                cursor = conn.cursor()

                # Store post-migration analysis with live price tracking
                cursor.execute("""
                    INSERT OR REPLACE INTO token_analysis (
                        mint, analyzed_at, events_parsed,
                        post_migration_mint_concentration, post_migration_unique_minters_ratio,
                        post_migration_sell_suppression_ratio, post_migration_mint_velocity_sec,
                        post_migration_buy_size_variance, post_migration_sell_volume_concentration,
                        post_migration_creator_activity_ratio,
                        rug_probability, risk_level, post_migration_coverage,
                        migration_tx, price_current, price_highest, pool_address, earliest_tx_creator, creator_is_blocked, network_risk, connected_malicious_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    mint,
                    time.time(),
                    analysis.get("total_events", 0),
                    analysis.get("mint_concentration", 0),
                    analysis.get("unique_minters_ratio", 0),
                    analysis.get("sell_suppression_ratio", 0),
                    analysis.get("mint_velocity_sec", 0),
                    analysis.get("buy_size_variance", 0),
                    analysis.get("sell_volume_concentration", 0),
                    analysis.get("creator_activity_ratio", 0),
                    analysis.get("rug_probability", 0),
                    analysis.get("risk_level", ""),
                    analysis.get("coverage", 0),
                    signature,
                    None,  # price_current will be updated by background task
                    None,  # price_highest will be updated by background task
                    pool_address,  # Extracted pool address from migration transaction
                    analysis.get("earliest_tx_creator"),  # Creator from earliest transaction
                    analysis.get("creator_is_blocked", 0),  # Is creator in blocklist?
                    analysis.get("network_risk", 0),  # Is creator connected to malicious creators?
                    analysis.get("connected_malicious_count")  # Count of connected malicious creators
                ))

                conn.commit()
                conn.close()
                pool_info = f"Pool: {pool_address[:16]}" if pool_address else "Pool: will discover at price-time"
                print(f"[DB] ✅ Stored analysis {mint} | {pool_info}", flush=True)
            except Exception as e:
                print(f"[DB] ❌ Failed to store analysis for {mint}: {e}", flush=True)

    def _token_exists_in_db(self, mint: str) -> bool:
        """Check if token exists in analysis table (previously analyzed)"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM token_analysis WHERE mint = ?", (mint,))
            result = cursor.fetchone()
            conn.close()
            return bool(result)
        except Exception as e:
            print(f"[DB] ⚠ Could not check if token exists: {e}", flush=True)
            return False

    # --- Migration Detection ---
    def _is_migration_transaction(self, logs: list) -> bool:
        """
        Check if transaction logs indicate a Pump.Fun → PumpSwap migration.

        Looks for:
        - "Instruction: Migrate" (Pump.Fun migration marker)
        - Pool initialization patterns
        - Excludes swaps (Buy/Sell instructions)
        - Excludes MigrateBondingCurveCreator (NOT a pool creation)
        """
        logs_text = ' '.join(logs)

        # Exclude swaps (Buy/Sell instructions)
        if 'Instruction: Buy' in logs_text or 'Instruction: Sell' in logs_text:
            return False

        # Filter out MigrateBondingCurveCreator - that's NOT a pool creation
        if 'MigrateBondingCurveCreator' in logs_text:
            return False

        # Must have Migrate instruction (Pump.Fun migration marker)
        if 'Instruction: Migrate' not in logs_text:
            return False

        # Check for pool initialization patterns
        if not any(pattern.lower() in logs_text.lower() for pattern in ['initialize', 'create_pool', 'InitializePool']):
            return False

        return True

    async def _fetch_mint_from_transaction(self, signature: str) -> Optional[str]:
        """
        Fetch full transaction and extract token mint.

        Strategy:
        1. Try postTokenBalances first (most reliable)
        2. Fall back to accountKeys if postTokenBalances missing
        3. Filter out system programs
        4. Accept 43 or 44 char addresses (Pump.Fun token length variance)

        Includes retry logic for newly-confirmed transactions that may have indexing delays.
        Uses RPC failover chain: Primary QuickNode -> Secondary QuickNode -> Helius -> Public.
        """
        max_retries = 12
        retry_delays = [0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 10.0, 15.0, 20.0, 30.0, 45.0, 60.0]  # Extended backoff for slow indexing

        for attempt in range(max_retries):
            try:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransaction",
                    "params": [signature, {"encoding": "json", "maxSupportedTransactionVersion": 0}]
                }

                data = await self._post_rpc_with_fallback(payload)

                if not data or "result" not in data or not data["result"]:
                    # Transaction not indexed yet, retry with backoff
                    if attempt < max_retries - 1:
                        print(f"[MINT] 📝 Transaction indexing delay, retry {attempt + 1}/{max_retries}...", flush=True)
                        await asyncio.sleep(retry_delays[attempt])
                        continue
                    print(f"[MINT] ⚠ Transaction not found after retries: {signature}", flush=True)
                    return None

                tx_data = data["result"]
                meta = tx_data.get("meta", {})

                # Strategy 1: Try postTokenBalances first
                post_balances = meta.get("postTokenBalances", [])
                for balance in post_balances:
                    mint = balance.get("mint", "")
                    # Accept valid token mints (43-44 chars), exclude SOL
                    if mint and len(mint) in (43, 44) and mint != "So11111111111111111111111111111111111111112":
                        return mint

                # Strategy 2: Fall back to accountKeys
                message = tx_data.get("transaction", {}).get("message", {})
                accounts = message.get("accountKeys", [])

                system_programs = {
                    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",  # Pump.Fun
                    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",  # PumpSwap
                    "11111111111111111111111111111111",               # System program
                    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",  # ATA program
                    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",  # Token program
                    "So11111111111111111111111111111111111111112",   # Wrapped SOL
                }

                for account in accounts[:10]:
                    if len(account) in (43, 44) and account not in system_programs:
                        return account

                print(f"[MINT] ⚠ No valid mint found in {signature}", flush=True)
                return None

            except asyncio.TimeoutError:
                if attempt < max_retries - 1:
                    print(f"[MINT] ⏱️  Timeout, retrying {attempt + 1}/{max_retries}...", flush=True)
                    await asyncio.sleep(retry_delays[attempt])
                    continue
                print(f"[MINT] ⚠ Timeout after retries: {signature}", flush=True)
                return None
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"[MINT] ⚠ Error on attempt {attempt + 1}, retrying: {e}", flush=True)
                    await asyncio.sleep(retry_delays[attempt])
                    continue
                print(f"[MINT] ⚠ Error fetching {signature}: {e}", flush=True)
                return None
        
        return None

    async def _extract_pool_from_migration_tx(self, signature: str) -> Optional[str]:
        """
        Extract the PumpSwap pool address from a migration transaction.

        The pool is the account that is OWNED BY the PumpSwap program.

        Strategy:
        1. Fetch the transaction
        2. Look through all accounts in innerInstructions
        3. Find accounts that are used by the PumpSwap program
        4. Return the first writable PDA (index 0 of PumpSwap instruction accounts)

        Returns: The pool address (string) or None if extraction fails
        Uses RPC failover chain: Primary QuickNode -> Secondary QuickNode -> Helius -> Public.
        """
        max_retries = 3
        retry_delays = [1.0, 3.0, 5.0]

        for attempt in range(max_retries):
            try:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransaction",
                    "params": [signature, {"encoding": "json", "maxSupportedTransactionVersion": 0}]
                }

                data = await self._post_rpc_with_fallback(payload)

                if not data or "result" not in data or not data["result"]:
                    return None

                tx_data = data["result"]
                message = tx_data.get("transaction", {}).get("message", {})
                account_keys = message.get("accountKeys", [])
                
                if not account_keys:
                    return None
                
                meta = tx_data.get("meta", {})
                inner_instructions = meta.get("innerInstructions", [])
                
                PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
                
                # Find PumpSwap program index in accountKeys
                pumpswap_idx = -1
                for i, acc in enumerate(account_keys):
                    if acc == PUMPSWAP_PROGRAM:
                        pumpswap_idx = i
                        break
                
                if pumpswap_idx < 0:
                    return None
                
                # Search innerInstructions for PumpSwap calls using programIdIndex
                for ix_group in inner_instructions:
                    instructions = ix_group.get("instructions", [])
                    for ix in instructions:
                        program_id_idx = ix.get("programIdIndex")
                        
                        # Check if this instruction is calling PumpSwap
                        if program_id_idx == pumpswap_idx:
                            # This is a PumpSwap instruction
                            accounts = ix.get("accounts", [])
                            if accounts and len(accounts) > 0:
                                # The first account in a PumpSwap instruction is typically the pool
                                pool_idx = accounts[0]
                                if isinstance(pool_idx, int) and pool_idx < len(account_keys):
                                    pool_address = account_keys[pool_idx]
                                    print(f"[POOL] ✅ Extracted pool from PumpSwap instruction: {pool_address}", flush=True)
                                    return pool_address


                return None

            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"[POOL] ⚠ Error extracting pool (attempt {attempt + 1}/{max_retries}): {e}", flush=True)
                    await asyncio.sleep(retry_delays[attempt])
                else:
                    print(f"[POOL_ERROR] Failed to extract pool address after {max_retries} attempts: {e}", flush=True)
                    return None

        return None

    async def _get_pool_address(self, token_mint: str, signature: str) -> Optional[str]:
        """Get pool address from database or extract from blockchain"""
        try:
            # Try to get from database first
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()
            cursor.execute("SELECT pool_address FROM token_analysis WHERE mint = ?", (token_mint,))
            row = cursor.fetchone()
            conn.close()
            
            if row and row[0]:
                return row[0]
            
            # If not in database, find pool by querying largest token accounts
            pool_address = await self._find_pool_account(token_mint)
            if pool_address:
                # Update database with pool address
                try:
                    conn = sqlite3.connect(DB_PATH, timeout=60)
                    cursor = conn.cursor()
                    cursor.execute("UPDATE token_analysis SET pool_address = ? WHERE mint = ?", (pool_address, token_mint))
                    conn.commit()
                    conn.close()
                except:
                    pass
            
            return pool_address
        except Exception as e:
            return None

    async def _extract_price_from_transaction(self, signature: str, token_mint: str) -> Optional[tuple]:
        """
        Extract on-chain price from pool vault balances.
        
        Strategy:
        1. Get pool address (from DB or extract from transaction)
        2. Try to query pool account balances (token and SOL)
        3. If fails, use DexScreener (more reliable)
        
        Returns: (price_usd, market_cap_usd, source) or None
        """
        try:
            # Get or extract pool address
            pool_address = await self._get_pool_address(token_mint, signature)
            
            if pool_address:
                # Try to get price from pool balances
                result = await self._get_price_from_pool_account(pool_address, token_mint)
                if result is not None:
                    price, market_cap = result
                    return (price, market_cap, "onchain")
            
            # Fall back to DexScreener (more reliable and always available)
            result = await self._fetch_dexscreener_price(token_mint)
            if result is not None:
                price, market_cap = result
                return (price, market_cap, "dexscreener")
            
            return None
                    
        except Exception as e:
            print(f"[PRICE_ERROR] Failed to extract price for {token_mint}: {e}", flush=True)
            return None

    async def _find_pool_account(self, token_mint: str) -> Optional[str]:
        """
        Find the pool account that holds this token.
        The smallest token account is typically the active pool/bonding curve.
        """
        try:
            # Query all token accounts for this mint
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTokenAccountsByMint",
                "params": [token_mint]
            }

            async with aiohttp.ClientSession() as session:
                # First try to find accounts via getTokenAccountsByMint (if available)
                # Fallback to getTokenLargestAccounts
                accounts = None
                
                async with session.post(RPC_HTTP, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if "result" in data and "value" in data["result"]:
                            accounts = data["result"]["value"]
                            print(f"[POOL] Found {len(accounts)} accounts via getTokenAccountsByMint", flush=True)
                
                # Fallback to getTokenLargestAccounts
                if not accounts:
                    payload = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getTokenLargestAccounts",
                        "params": [token_mint]
                    }
                    
                    async with session.post(RPC_HTTP, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status != 200:
                            return None

                        data = await resp.json()
                        if "result" not in data or "value" not in data["result"]:
                            return None

                        accounts = data["result"]["value"]
                
                if not accounts:
                    return None
                
                print(f"[POOL] Checking {len(accounts)} token accounts to find pool...", flush=True)
                
                # Sort by balance - smallest account is usually the active pool
                sorted_accounts = sorted(accounts, key=lambda x: float(x.get("uiAmount", 0)))
                
                # Check the smallest few accounts (they're most likely to be pools)
                for account_info in sorted_accounts[:5]:
                    token_account_addr = account_info.get("address")
                    balance = float(account_info.get("uiAmount", 0))
                    
                    if not token_account_addr:
                        continue
                    
                    print(f"[POOL]   Checking {token_account_addr} (balance: {balance:.0f})", flush=True)
                    
                    # Get account info with jsonParsed to extract the owner
                    acct_payload = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getAccountInfo",
                        "params": [token_account_addr, {"encoding": "jsonParsed"}]
                    }
                    
                    try:
                        async with session.post(RPC_HTTP, json=acct_payload, timeout=aiohttp.ClientTimeout(total=5)) as acct_resp:
                            if acct_resp.status == 200:
                                acct_data = await acct_resp.json()
                                if "result" in acct_data and acct_data["result"]:
                                    account = acct_data["result"]
                                    value = account.get("value", {})
                                    
                                    # For jsonParsed encoding, owner is in value.data.parsed.info.owner
                                    if "data" in value and isinstance(value["data"], dict):
                                        parsed = value["data"].get("parsed", {})
                                        info = parsed.get("info", {})
                                        owner = info.get("owner")
                                        
                                        if owner:
                                            print(f"[POOL]     Owner: {owner}", flush=True)
                                            return owner
                    except:
                        pass
                
                # If we can't get owner via parsing, use the smallest account address as pool
                # (This is a fallback - smallest account is usually the pool)
                smallest_account = sorted_accounts[0].get("address")
                if smallest_account:
                    print(f"[POOL] Using smallest account as pool: {smallest_account}", flush=True)
                    return smallest_account
                
                return None
        except Exception as e:
            print(f"[POOL_ERROR] Failed to find pool: {e}", flush=True)
            return None

    async def _get_price_from_pool_account(self, pool_address: str, token_mint: str) -> Optional[tuple]:
        """
        Get price by querying pool account's token and SOL balances.

        PumpSwap pools store liquidity in WSOL (wrapped SOL) token accounts, not native lamports.
        We query for both WSOL and the token mint, then calculate price from the balance ratio.

        Returns: (price_usd, market_cap_usd) or None
        """
        try:
            # Query WSOL (wrapped SOL) token accounts owned by this pool
            # WSOL mint: So11111111111111111111111111111111111111112
            wsol_mint = "So11111111111111111111111111111111111111112"

            payload_wsol = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTokenAccountsByOwner",
                "params": [pool_address, {"mint": wsol_mint}, {"encoding": "jsonParsed"}]
            }

            data = await self._post_rpc_with_fallback(payload_wsol)
            
            sol_balance = 0
            if data and "result" in data and "value" in data["result"]:
                accounts = data["result"]["value"]
                if accounts:
                    # Get WSOL balance from first (should only be one)
                    wsol_info = accounts[0].get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
                    sol_balance = float(wsol_info.get("tokenAmount", {}).get("uiAmount", 0))

            # If no WSOL, fall back to pool account lamports
            if sol_balance == 0:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getAccountInfo",
                    "params": [pool_address, {"encoding": "jsonParsed"}]
                }

                data = await self._post_rpc_with_fallback(payload)
                if not data or "result" not in data or not data["result"]:
                    return None

                account_value = data["result"].get("value", {})
                if not account_value:
                    return None

                lamports = account_value.get("lamports", 0)
                sol_balance = lamports / 1e9

            if sol_balance == 0:
                return None

            # Query token accounts owned by this pool
            payload2 = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTokenAccountsByOwner",
                "params": [pool_address, {"mint": token_mint}, {"encoding": "jsonParsed"}]
            }

            data2 = await self._post_rpc_with_fallback(payload2)
            if not data2 or "result" not in data2 or "value" not in data2["result"]:
                return None

            accounts = data2["result"]["value"]
            if not accounts:
                # No token accounts for this mint in this pool - wrong pool address
                return None

            try:
                # Find the account with the LARGEST token balance
                # (PumpSwap pools may have multiple token accounts)
                max_balance_account = None
                max_balance = 0

                for token_account in accounts:
                    account_data = token_account.get("account", {})
                    parsed = account_data.get("data", {}).get("parsed", {})
                    token_info = parsed.get("info", {})
                    token_amount_info = token_info.get("tokenAmount", {})
                    balance = float(token_amount_info.get("uiAmount", 0))

                    if balance > max_balance:
                        max_balance = balance
                        max_balance_account = token_account

                if not max_balance_account:
                    return None

                account_data = max_balance_account.get("account", {})
                parsed = account_data.get("data", {}).get("parsed", {})
                token_info = parsed.get("info", {})
                token_amount_info = token_info.get("tokenAmount", {})
                token_balance = float(token_amount_info.get("uiAmount", 0))

            except (KeyError, ValueError, TypeError):
                return None

            if token_balance == 0 or sol_balance == 0:
                return None

            # Calculate price
            price_sol = sol_balance / token_balance
            sol_usd = await self._get_sol_price_usd()
            price_usd = price_sol * sol_usd
            total_supply = 1_000_000_000  # Pump.Fun tokens have 1B supply
            market_cap_usd = price_usd * total_supply

            return (price_usd, market_cap_usd)

        except Exception as e:
            print(f"[PRICE_ERROR] Exception in on-chain extraction: {e}", flush=True)
            return None

    async def _extract_onchain_pool_price(self, token_mint: str) -> Optional[tuple]:
        """
        Extract price from on-chain pool account balances.
        
        NOTE: The proper implementation would require:
        1. Finding the actual pool account address for this token
        2. Querying that specific account's vault balances
        3. Calculating price from token/SOL ratio
        
        Since we don't have the pool address readily available during price updates,
        and extracting it reliably from transactions is complex, we rely on DexScreener
        which has verified, real-time pricing. The fallback mechanism ensures pricing
        always works.
        
        Returns: None (signals to use DexScreener fallback)
        """
        return None

    async def _get_pool_price_from_vault(self, token_mint: str) -> Optional[tuple]:
        """
        Extract on-chain price by querying token pool account balances.
        Uses Jupiter API to find pool and fetch live vault balances.
        
        Returns: (price_usd, market_cap_usd) or None
        """
        try:
            # Query Jupiter API for token info which includes pool address
            url = f"https://api.jup.ag/tokens/v1?searchQuery={token_mint}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        return None
                    
                    data = await resp.json()
                    tokens = data.get("tokens", [])
                    
                    if not tokens:
                        return None
                    
                    # Get SOL price for USD conversion
                    sol_price_usd = await self._get_sol_price_usd()
                    
                    # For now, fallback to DexScreener if we can't determine pool
                    # TODO: Implement proper pool vault balance fetching
                    return None
                    
        except Exception as e:
            return None

    async def _get_sol_price_usd(self) -> float:
        """Get current SOL price in USD"""
        try:
            SOL_MINT = "So11111111111111111111111111111111111111112"
            url = f"https://api.dexscreener.com/latest/dex/tokens/{SOL_MINT}"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        pairs = data.get("pairs", [])
                        if pairs and "priceUsd" in pairs[0]:
                            try:
                                return float(pairs[0]["priceUsd"])
                            except (ValueError, TypeError):
                                pass
            return 200.0  # Fallback
        except:
            return 200.0

    async def _fetch_dexscreener_price(self, token_mint: str) -> Optional[tuple]:
        """
        Fetch price and market cap from DexScreener API.
        
        Returns: (price_usd, market_cap_usd) or None
        All values are in USD for consistency with database storage.
        """
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{token_mint}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        return None
                    
                    data = await resp.json()
                    pairs = data.get("pairs", [])
                    
                    if not pairs:
                        return None
                    
                    pair = pairs[0]
                    
                    # Get USD price and market cap from DexScreener
                    price_usd = pair.get("priceUsd")
                    market_cap_usd = pair.get("marketCap")
                    
                    if not price_usd or not market_cap_usd:
                        return None
                    
                    try:
                        price_usd = float(price_usd)
                        market_cap_usd = float(market_cap_usd)
                    except (ValueError, TypeError):
                        return None
                    
                    return (price_usd, market_cap_usd)
                    
        except Exception as e:
            print(f"[PRICE_ERROR] DexScreener fetch failed {token_mint}: {e}", flush=True)
            return None

    def _extract_mint_from_logs(self, logs: list) -> Optional[str]:
        """
        Fallback: Extract token mint address from transaction logs.
        Looks for patterns like "mint: EPjFWdd5Au..." in the logs.
        """
        try:
            logs_text = ' '.join(logs)
            # Look for "mint:" patterns followed by base58 addresses
            matches = re.findall(r'mint[:\s]+([1-9A-HJ-NP-Z]{32,})', logs_text, re.IGNORECASE)
            if matches:
                # Return the first valid match (don't filter by "pump" - not all pump.fun tokens contain "pump")
                return matches[0] if matches else None
            return None
        except Exception as e:
            print(f"[MINT] ⚠ Error extracting mint from logs: {e}", flush=True)
            return None

    # --- Analyzer ---
    async def analyze_post_migration(self, mint: str, signature: str = None, pool_address: str = None):
        """Analyze token's post-migration activity on PumpSwap"""
        if mint in self.analyzed_tokens:
            return
        try:
            print(f"[ANALYZER] 🔍 Analyzing post-migration {mint}", flush=True)
            analyzer = PostMigrationAnalyzer(mint, rpc_url=RPC_HTTP)
            await analyzer.fetch_curve_activity_async()

            summary = await analyzer.get_summary_async()

            # Extract creator from earliest transaction
            earliest_creator = await analyzer.get_creator_from_earliest_tx()
            creator_is_blocked = 0
            network_risk = None

            if earliest_creator:
                summary["earliest_tx_creator"] = earliest_creator
                print(f"[CREATOR] ✅ Extracted from earliest tx: {earliest_creator}", flush=True)

                # Extract pre-migration funding in real-time (non-blocking)
                try:
                    # Get migration timestamp from the signature (block time)
                    migration_timestamp = None
                    if signature:
                        try:
                            # Fetch migration transaction to get block time
                            import aiohttp
                            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                                payload = {
                                    "jsonrpc": "2.0",
                                    "id": 1,
                                    "method": "getTransaction",
                                    "params": [signature, {"encoding": "json", "maxSupportedTransactionVersion": 0}]
                                }
                                async with session.post(RPC_HTTP, json=payload) as resp:
                                    tx_data = await resp.json()
                                    if tx_data.get("result"):
                                        block_time = tx_data["result"].get("blockTime")
                                        if block_time:
                                            migration_timestamp = datetime.utcfromtimestamp(block_time).isoformat() + "Z"
                                            print(f"[FUNDING] Migration timestamp from tx: {migration_timestamp}", flush=True)
                        except Exception as ts_err:
                            print(f"[FUNDING] ⚠ Could not get migration timestamp: {ts_err}", flush=True)

                    # Fall back to current time if we couldn't get actual migration time
                    created_at = migration_timestamp or datetime.utcnow().isoformat() + "Z"

                    # Run funding extraction asynchronously (if enabled)
                    if get_migration_setting('token_history_check', True):
                        print(f"[SETTINGS] Token history check ✅ ON - extracting pre-migration funding", flush=True)
                        asyncio.create_task(extract_funding_for_new_token(earliest_creator, created_at))
                    else:
                        print(f"[SETTINGS] Token history check ❌ OFF - skipping funding extraction", flush=True)

                    # Trigger wallet clustering analysis asynchronously (if enabled)
                    if get_migration_setting('creator_clustering', True):
                        print(f"[SETTINGS] Creator clustering ✅ ON - analyzing wallet network", flush=True)
                        asyncio.create_task(trigger_wallet_clustering(earliest_creator))
                    else:
                        print(f"[SETTINGS] Creator clustering ❌ OFF - skipping wallet analysis", flush=True)
                except Exception as e:
                    print(f"[FUNDING] ⚠ Could not extract funding data: {e}", flush=True)

                # Check if creator is in blocklist
                try:
                    conn = sqlite3.connect(DB_PATH, timeout=60)
                    cursor = conn.cursor()
                    try:
                        cursor.execute("SELECT rug_count, reputation, connected_to_malicious, network_members FROM creator_blocklist WHERE creator_address = ?", (earliest_creator,))
                        blocklist_row = cursor.fetchone()
                    except sqlite3.OperationalError:
                        # creator_blocklist table doesn't exist yet
                        blocklist_row = None
                    conn.close()

                    if blocklist_row:
                        rug_count, reputation, connected_to_malicious, network_members_json = blocklist_row
                        creator_is_blocked = 1
                        summary["creator_is_blocked"] = 1
                        summary["creator_reputation"] = reputation

                        if rug_count >= 2:
                            print(f"[BLOCKLIST] 🚨 MALICIOUS CREATOR DETECTED: {earliest_creator} | {rug_count} rugs", flush=True)
                        else:
                            print(f"[BLOCKLIST] 📝 SUSPICIOUS CREATOR: {earliest_creator} | on watch list", flush=True)

                        # Check if connected to other malicious creators
                        if connected_to_malicious:
                            try:
                                network_members = json.loads(network_members_json) if network_members_json else []
                                network_risk = len(network_members)
                                summary["network_risk"] = 1
                                summary["connected_malicious_count"] = len(network_members)
                                print(f"[NETWORK] 🔗 NETWORK RISK: Creator is connected to {len(network_members)} malicious creator(s)", flush=True)
                            except:
                                pass

                except Exception as e:
                    print(f"[BLOCKLIST_CHECK] Error checking creator: {e}", flush=True)

            self.analyzed_tokens[mint] = summary
            risk_level = summary.get("risk_level", "🟢 LOW RISK")
            score = summary.get("rug_probability", 0.0)

            # Add creator risk indicator if blocked
            if creator_is_blocked:
                if network_risk:
                    risk_indicator = f"🔗 NETWORK RISK ({network_risk} connected)"
                elif summary.get("creator_reputation") == "MALICIOUS":
                    risk_indicator = "🚨 MALICIOUS CREATOR"
                else:
                    risk_indicator = "📝 SUSPICIOUS CREATOR"
                print(f"[ANALYZER] {risk_indicator} | {risk_level} | Score: {score:.2%} | {mint}", flush=True)
            else:
                print(f"[ANALYZER] {risk_level} | Score: {score:.2%} | {mint}", flush=True)

            # Store analysis results (will be updated with live price in background)
            # Pass pool_address if available
            await self._store_analysis(mint, summary, signature, pool_address)
        except Exception as e:
            print(f"[ANALYZER] ⚠ Analysis failed for {mint}: {e}", flush=True)

    async def update_live_prices_background(self):
        """Background task: Update live prices and market caps continuously"""
        await asyncio.sleep(2)  # Wait 2s before starting
        
        while True:
            try:
                tokens = self._get_tokens_needing_price_update()
                
                if not tokens:
                    await asyncio.sleep(5)
                    continue
                
                updated_count = 0
                failed_count = 0

                for token_mint in tokens:
                    try:
                        # Get the migration transaction for this token to extract price
                        tx_signature = await self._get_migration_tx_for_token(token_mint)

                        if not tx_signature:
                            failed_count += 1
                            continue

                        # Extract price from DexScreener or on-chain
                        result = await self._extract_price_from_transaction(tx_signature, token_mint)

                        if result is not None:
                            price, market_cap, source = result  # Unpack the source
                            await self._update_price_in_db(token_mint, price, market_cap, source)  # Pass source
                            updated_count += 1
                        else:
                            failed_count += 1

                        # Rate limit
                        await asyncio.sleep(0.1)
                    except Exception as e:
                        failed_count += 1

                # Loop back after 10 seconds for live updates
                await asyncio.sleep(10)
                        
            except Exception as e:
                print(f"[PRICE_BG] Error in background task: {e}", flush=True)
                await asyncio.sleep(5)

    def _get_tokens_needing_price_update(self) -> List[str]:
        """Get tokens that need live price updates (prioritize newer)"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Get top 50 newest tokens (all active tokens)
            cursor.execute("""
                SELECT mint FROM token_analysis
                ORDER BY analyzed_at DESC
                LIMIT 50
            """)
            
            tokens = [row[0] for row in cursor.fetchall()]
            conn.close()
            return tokens
        except Exception as e:
            print(f"[DB_ERROR] Failed to fetch tokens: {e}", flush=True)
            return []

    async def _get_migration_tx_for_token(self, token_mint: str) -> Optional[str]:
        """Get the migration transaction signature for a token"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute(
                "SELECT migration_tx FROM token_analysis WHERE mint = ?",
                (token_mint,)
            )
            row = cursor.fetchone()
            conn.close()
            
            return row[0] if row and row[0] else None
        except Exception as e:
            print(f"[DB_ERROR] Failed to get tx for {token_mint}: {e}", flush=True)
            return None

    async def _add_rug_creator_to_blocklist(self, token_mint: str, earliest_tx_creator: str = None):
        """
        When a rug is detected, add the creator to the block list in the database.
        This allows future tokens from the same creator to be skipped.
        """
        if not earliest_tx_creator:
            return

        async with self.db_lock:
            try:
                conn = sqlite3.connect(DB_PATH, timeout=60)
                cursor = conn.cursor()

                # Check if creator already in blocklist
                cursor.execute("SELECT rug_count, rugged_tokens FROM creator_blocklist WHERE creator_address = ?", (earliest_tx_creator,))
                row = cursor.fetchone()

                if row:
                    # Update existing entry
                    rug_count, rugged_tokens_json = row
                    rug_count += 1

                    # Parse existing tokens and add new one
                    try:
                        rugged_tokens = json.loads(rugged_tokens_json) if rugged_tokens_json else []
                    except:
                        rugged_tokens = []

                    if token_mint not in rugged_tokens:
                        rugged_tokens.append(token_mint)

                    # Determine reputation
                    reputation = "MALICIOUS" if rug_count >= 2 else "SUSPICIOUS"

                    cursor.execute(
                        """UPDATE creator_blocklist
                           SET rug_count = ?, rugged_tokens = ?, reputation = ?, last_rug_detected_at = datetime('now'), updated_at = datetime('now')
                           WHERE creator_address = ?""",
                        (rug_count, json.dumps(rugged_tokens), reputation, earliest_tx_creator)
                    )
                else:
                    # Insert new entry
                    cursor.execute(
                        """INSERT INTO creator_blocklist (creator_address, rug_count, rugged_tokens, reputation, first_rug_detected_at, last_rug_detected_at)
                           VALUES (?, 1, ?, 'SUSPICIOUS', datetime('now'), datetime('now'))""",
                        (earliest_tx_creator, json.dumps([token_mint]))
                    )
                    rug_count = 1

                conn.commit()
                conn.close()

                # Log
                if rug_count >= 2:
                    print(f"[BLOCKLIST] 🚨 SERIAL RUGGER: {earliest_tx_creator} | {rug_count} rugs detected", flush=True)
                else:
                    print(f"[BLOCKLIST] 📝 Added to watch list: {earliest_tx_creator} | {rug_count} rug", flush=True)

            except Exception as e:
                print(f"[BLOCKLIST_ERROR] Failed to update rug creator block list: {e}", flush=True)

    async def _update_price_in_db(self, token_mint: str, current_price: float, current_market_cap: float, source: str = "onchain"):
        """
        Update live price, market cap, and price source in database.
        
        Also automatically detects and flags rug pulls:
        - If time to peak < 30 minutes AND peak market cap < $100k → flag as 'quick_peak_low_mc'
        
        Note: Prices and market caps are stored in USD for consistency with DexScreener.
        """
        async with self.db_lock:
            try:
                conn = sqlite3.connect(DB_PATH, timeout=60)
                cursor = conn.cursor()
                
                # Get previous values and creation time
                cursor.execute(
                    "SELECT price_current, price_highest, market_cap_current, market_cap_highest, market_cap_highest_at, price_source, created_at, rug_indicator FROM token_analysis WHERE mint = ?",
                    (token_mint,)
                )
                row = cursor.fetchone()

                price_highest = row[1] if row and row[1] else current_price
                market_cap_highest = row[3] if row and row[3] else current_market_cap
                market_cap_highest_at = row[4] if row else None
                created_at = row[6] if row else None
                current_rug_indicator = row[7] if row else None

                # Track if this is a new peak
                is_new_peak = False
                
                # Update highest if this is higher
                if current_price > price_highest:
                    price_highest = current_price
                if current_market_cap > market_cap_highest:
                    market_cap_highest = current_market_cap
                    market_cap_highest_at = datetime.now().isoformat(sep=' ')  # Store timestamp when peak is reached
                    is_new_peak = True

                # Auto-detect rug pulls based on timing
                rug_indicator = current_rug_indicator
                if is_new_peak and created_at and market_cap_highest is not None:
                    try:
                        # Parse created_at timestamp
                        if isinstance(created_at, str):
                            created_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                        else:
                            created_dt = created_at
                        
                        # Parse peak timestamp
                        if isinstance(market_cap_highest_at, str):
                            peak_dt = datetime.fromisoformat(market_cap_highest_at.replace('Z', '+00:00'))
                        else:
                            peak_dt = market_cap_highest_at
                        
                        # Calculate time to peak in minutes
                        time_to_peak_minutes = (peak_dt - created_dt).total_seconds() / 60
                        
                        # RUG DETECTION LOGIC:
                        # Peak in < 30 minutes AND peak market cap < $100k = classic rug pattern
                        if time_to_peak_minutes < 30 and market_cap_highest < 100000:
                            rug_indicator = 'quick_peak_low_mc'
                            print(f"[RUG] 🚨 DETECTED: {token_mint} | Time to peak: {time_to_peak_minutes:.1f} min | Peak MC: ${market_cap_highest:,.0f}", flush=True)

                            # Get creator and add to block list
                            cursor.execute("SELECT earliest_tx_creator FROM token_analysis WHERE mint = ?", (token_mint,))
                            creator_row = cursor.fetchone()
                            if creator_row and creator_row[0]:
                                # Call async method to add to blocklist (fire and forget)
                                asyncio.create_task(self._add_rug_creator_to_blocklist(token_mint, creator_row[0]))
                        elif time_to_peak_minutes < 30:
                            # Peaked fast but market cap was substantial - not a rug, just volatile
                            rug_indicator = None
                            print(f"[PEAK] ⚡ Fast peak but legit size: {token_mint} | Time: {time_to_peak_minutes:.1f} min | MC: ${market_cap_highest:,.0f}", flush=True)
                        else:
                            # Normal progression
                            rug_indicator = None
                            
                    except Exception as e:
                        print(f"[RUG_CHECK] ⚠ Could not analyze rug pattern for {token_mint}: {e}", flush=True)

                cursor.execute("""
                    UPDATE token_analysis
                    SET price_current = ?, price_highest = ?,
                        market_cap_current = ?, market_cap_highest = ?,
                        market_cap_highest_at = ?,
                        rug_indicator = ?,
                        price_source = ?, price_updated_at = datetime('now')
                    WHERE mint = ?
                """, (current_price, price_highest, current_market_cap, market_cap_highest, market_cap_highest_at, rug_indicator, source, token_mint))
                
                conn.commit()
                conn.close()
                
            except Exception as e:
                print(f"[DB_ERROR] Failed to update price for {token_mint}: {e}", flush=True)

    async def handle_migration(self, signature: str, logs: list):
        """Process detected migration"""
        try:
            # Skip if already processing this signature
            if signature in self.detected_migrations:
                return

            self.detected_migrations.add(signature)

            # Extract mint from transaction (more reliable than logs)
            mint = await self._fetch_mint_from_transaction(signature)
            
            if not mint:
                print(f"[MIGRATION] ⚠ Failed to extract mint from postTokenBalances, trying logs fallback", flush=True)
                mint = self._extract_mint_from_logs(logs)
            
            if not mint:
                print(f"[MIGRATION] ⚠ Could not extract mint from {signature} - SKIPPED", flush=True)
                return  # Silent skip - not a pump.fun token migration

            # Skip if already analyzed
            if self._token_exists_in_db(mint):
                print(f"[MIGRATION] ⏭️  Token {mint} already analyzed - SKIPPED", flush=True)
                return

            self.seen_mints.add(mint)
            print(f"[EVENT] 🚀 MIGRATION DETECTED: {mint}", flush=True)
            print(f"[EVENT] Migration signature: {signature}", flush=True)

            # Extract pool address from migration transaction for on-chain price queries
            pool_address = await self._extract_pool_from_migration_tx(signature)
            if pool_address:
                print(f"[EVENT] ✅ Pool extracted from transaction: {pool_address}", flush=True)

            # Analyze post-migration token asynchronously
            # Pass pool_address if available so it can be saved immediately
            asyncio.create_task(self.analyze_post_migration(mint, signature, pool_address))

        except Exception as e:
            print(f"[MIGRATION] ⚠ Error handling migration: {e}", flush=True)
            import traceback
            traceback.print_exc()

    # --- WebSocket Listener ---
    async def listen_websocket(self):
        """Listen to PumpSwap program via WebSocket for live migration events"""
        print(f"\n[WEBSOCKET] Connecting to PumpSwap program...", flush=True)

        # Try Helius first, fall back to public Solana
        endpoints = [
            (HELIUS_RPC_WS, "Helius"),
            ("wss://api.mainnet-beta.solana.com/", "Public Solana")
        ]

        current_endpoint_idx = 0
        reconnect_delay = 5

        while True:
            try:
                endpoint, name = endpoints[current_endpoint_idx]
                # Improved WebSocket settings for stability
                async with websockets.connect(
                    endpoint,
                    ping_interval=20,      # Send ping every 20s
                    ping_timeout=5,        # Wait 5s for pong
                    close_timeout=10,      # Wait 10s for close frame
                    max_size=10 * 1024 * 1024  # 10MB max message size
                ) as ws:
                    self.websocket_connected = True
                    reconnect_delay = 5  # Reset delay on successful connection
                    print(f"[WEBSOCKET] ✓ Connected to PumpSwap program via {name}", flush=True)

                    # Subscribe to PumpSwap program logs
                    subscribe_msg = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "logsSubscribe",
                        "params": [
                            {"mentions": [PUMPSWAP_PROGRAM]},
                            {"commitment": "confirmed"}
                        ]
                    }
                    await ws.send(json.dumps(subscribe_msg))
                    print(f"[WEBSOCKET] Subscribed to PumpSwap migrations", flush=True)

                    # Wait for subscription confirmation before processing events
                    subscription_id = None
                    while subscription_id is None:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=10)
                            data = json.loads(msg)
                            
                            # Check for subscription response
                            if "result" in data:
                                subscription_id = data.get("result")
                                print(f"[WEBSOCKET] ✓ Subscription confirmed (ID: {subscription_id})\n", flush=True)
                                break
                        except asyncio.TimeoutError:
                            print(f"[WEBSOCKET] ⚠ No subscription confirmation after 10s", flush=True)
                            break
                    
                    # Now listen for actual migration events
                    while True:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=60)
                            data = json.loads(msg)

                            # Process only subscription result (actual events, not responses)
                            if 'params' in data and 'result' in data['params']:
                                self.websocket_msg_count += 1
                                result = data['params']['result']
                                value = result.get('value', {})
                                logs = value.get('logs', [])
                                signature = value.get('signature', '')
                                err = value.get('err')

                                # Skip failed transactions
                                if err or not signature:
                                    continue

                                # Check if this is a migration
                                if self._is_migration_transaction(logs):
                                    self.websocket_migration_count += 1
                                    print(f"[WEBSOCKET] 🚨 Migration #{self.websocket_migration_count} detected: {signature}", flush=True)
                                    asyncio.create_task(self.handle_migration(signature, logs))

                        except asyncio.TimeoutError:
                            # Keepalive timeout - continue listening
                            continue
                        except json.JSONDecodeError:
                            # Invalid JSON, skip
                            continue
                        except Exception as e:
                            # Suppress keepalive ping timeout spam and close frame warnings
                            error_msg = str(e).lower()
                            if "keepalive" not in error_msg and "close frame" not in error_msg:
                                print(f"[WEBSOCKET] ⚠ Error processing message: {e}", flush=True)
                            # Reconnect on serious errors
                            if "close frame" in error_msg or "connection closed" in error_msg:
                                break
                            continue

            except Exception as e:
                self.websocket_connected = False
                error_str = str(e).lower()

                # Check for specific auth issues
                if "401" in str(e) or "unauthorized" in error_str:
                    print(f"[WEBSOCKET] ⚠ Auth error (401) - falling back to public RPC", flush=True)
                    current_endpoint_idx = 1  # Switch to public Solana
                    reconnect_delay = 5
                elif "connection" in error_str or "refused" in error_str:
                    print(f"[WEBSOCKET] ⚠ Connection refused, retrying in {reconnect_delay}s...", flush=True)
                elif "close frame" not in error_str:
                    # Don't log close frame messages as errors
                    print(f"[WEBSOCKET] ⚠ {name} connection error: {e}", flush=True)
                    print(f"[WEBSOCKET] Retrying in {reconnect_delay}s...", flush=True)

                await asyncio.sleep(reconnect_delay)
                # Exponential backoff with cap at 30s
                reconnect_delay = min(reconnect_delay * 1.5, 30)

    # --- Main listener ---
    

    async def listen(self):
        """Main entry point - start WebSocket listener with live price updater"""
        # Start live price updater in background
        asyncio.create_task(self.update_live_prices_background())
        # Start WebSocket listener
        await self.listen_websocket()


async def main():
    listener = PumpFunCurveListener()
    await listener.listen()


if __name__ == "__main__":
    asyncio.run(main())
