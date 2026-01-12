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
from typing import Set, Optional, List
from pump_fun_post_migration_analyzer import PostMigrationAnalyzer
from dotenv import load_dotenv

load_dotenv()

# === Config ===
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
RPC_URL = os.getenv("RPC_URL", "")

# WebSocket: Try Helius first, fall back to public Solana
HELIUS_RPC_WS = f"wss://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}" if HELIUS_API_KEY else "wss://api.mainnet-beta.solana.com/"

# HTTP: Use QuickNode if available, otherwise Helius, then public
RPC_HTTP = RPC_URL if RPC_URL else (f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}" if HELIUS_API_KEY else "https://api.mainnet-beta.solana.com")

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
                price_updated_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()
        conn.close()

    async def _store_analysis(self, mint: str, analysis: dict, signature: str = None):
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
                        migration_tx, price_current, price_highest
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    None   # price_highest will be updated by background task
                ))

                conn.commit()
                conn.close()
                print(f"[DB] ✅ Stored post-migration analysis for {mint}", flush=True)
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
        """
        max_retries = 5
        retry_delays = [0.5, 1.0, 2.0, 3.0, 5.0]  # Exponential backoff for indexing delay
        
        for attempt in range(max_retries):
            try:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransaction",
                    "params": [signature, {"encoding": "json", "maxSupportedTransactionVersion": 0}]
                }

                async with aiohttp.ClientSession() as session:
                    async with session.post(RPC_HTTP, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status != 200:
                            if attempt < max_retries - 1:
                                await asyncio.sleep(retry_delays[attempt])
                                continue
                            print(f"[MINT] ⚠ RPC error {resp.status} fetching {signature[:16]}...", flush=True)
                            return None

                        data = await resp.json()
                        if "result" not in data or not data["result"]:
                            # Transaction not indexed yet, retry with backoff
                            if attempt < max_retries - 1:
                                print(f"[MINT] 📝 Transaction indexing delay, retry {attempt + 1}/{max_retries}...", flush=True)
                                await asyncio.sleep(retry_delays[attempt])
                                continue
                            print(f"[MINT] ⚠ Transaction not found after retries: {signature[:16]}...", flush=True)
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

                        print(f"[MINT] ⚠ No valid mint found in {signature[:16]}...", flush=True)
                        return None

            except asyncio.TimeoutError:
                if attempt < max_retries - 1:
                    print(f"[MINT] ⏱️  Timeout, retrying {attempt + 1}/{max_retries}...", flush=True)
                    await asyncio.sleep(retry_delays[attempt])
                    continue
                print(f"[MINT] ⚠ Timeout after retries: {signature[:16]}...", flush=True)
                return None
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"[MINT] ⚠ Error on attempt {attempt + 1}, retrying: {e}", flush=True)
                    await asyncio.sleep(retry_delays[attempt])
                    continue
                print(f"[MINT] ⚠ Error fetching {signature[:16]}...: {e}", flush=True)
                return None
        
        return None

    async def _get_sol_usd_price(self) -> float:
        """Get current SOL/USD price from DexScreener"""
        try:
            # SOL token address on Solana
            SOL_MINT = "So11111111111111111111111111111111111111112"
            url = f"https://api.dexscreener.com/latest/dex/tokens/{SOL_MINT}"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status != 200:
                        return 200  # Fallback to 200 if API fails
                    
                    data = await resp.json()
                    pairs = data.get("pairs", [])
                    
                    if pairs:
                        price_usd = pairs[0].get("priceUsd")
                        if price_usd:
                            try:
                                return float(price_usd)
                            except (ValueError, TypeError):
                                return 200
                    
                    return 200  # Fallback
        except Exception as e:
            print(f"[PRICE_WARN] Failed to get SOL price: {e}, using fallback 200", flush=True)
            return 200

    async def _extract_price_from_transaction(self, signature: str, token_mint: str) -> Optional[tuple]:
        """
        Extract on-chain price from migration transaction (primary source).
        Falls back to DexScreener if blockchain extraction fails.
        
        Returns: (price, market_cap, source) or None
        Where source is "onchain" or "dexscreener"
        """
        try:
            # Try blockchain first
            price, market_cap = await self._extract_onchain_price(signature, token_mint)
            if price is not None:
                return (price, market_cap, "onchain")
            
            # Fall back to DexScreener
            print(f"[PRICE] ⚠ Onchain extraction failed, falling back to DexScreener for {token_mint[:16]}...", flush=True)
            result = await self._fetch_dexscreener_price(token_mint)
            if result is not None:
                price, market_cap = result
                return (price, market_cap, "dexscreener")
            
            return None
                    
        except Exception as e:
            print(f"[PRICE_ERROR] Failed to extract price for {token_mint[:16]}...: {e}", flush=True)
            return None

    async def _extract_onchain_price(self, signature: str, token_mint: str) -> Optional[tuple]:
        """
        Extract price directly from migration transaction post-token-balances.
        
        Returns: (price_usd, market_cap_usd) - both converted to USD for consistency with DexScreener
        """
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [signature, {"encoding": "json", "maxSupportedTransactionVersion": 0}]
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(RPC_HTTP, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        return None

                    data = await resp.json()
                    if "result" not in data or not data["result"]:
                        return None

                    tx_data = data["result"]
                    meta = tx_data.get("meta", {})
                    
                    # Get post balances
                    post_balances = meta.get("postTokenBalances", [])
                    
                    token_amount = 0
                    sol_amount = 0
                    
                    # Calculate token and SOL amounts
                    for balance in post_balances:
                        if balance.get("mint") == token_mint:
                            token_amount = float(balance.get("uiTokenAmount", {}).get("amount", 0) or 0)
                        elif balance.get("mint") == "So11111111111111111111111111111111111111112":
                            sol_amount = float(balance.get("uiTokenAmount", {}).get("amount", 0) or 0)
                    
                    # If no wrapped SOL, try native SOL
                    if sol_amount == 0:
                        post_native_balance = meta.get("postBalances", [0])[0]
                        pre_native_balance = meta.get("preBalances", [0])[0]
                        sol_amount = (post_native_balance - pre_native_balance) / 1e9
                    
                    # Calculate price
                    if token_amount > 0 and sol_amount > 0:
                        price_sol = sol_amount / token_amount
                        
                        # Get current SOL/USD price
                        sol_usd_price = await self._get_sol_usd_price()
                        
                        # Convert to USD
                        price_usd = price_sol * sol_usd_price
                        total_supply = 1_000_000
                        market_cap_usd = price_usd * total_supply
                        
                        print(f"[PRICE] ✅ Onchain price for {token_mint[:16]}...: ${price_usd:.10f}/token | MC: ${market_cap_usd:,.2f}", flush=True)
                        return (price_usd, market_cap_usd)
                    
                    return None
                    
        except Exception as e:
            print(f"[PRICE_ERROR] Onchain extraction failed for {token_mint[:16]}...: {e}", flush=True)
            return None

    async def _fetch_dexscreener_price(self, token_mint: str) -> Optional[tuple]:
        """
        Fallback: Fetch price and market cap from DexScreener API.
        
        Returns: (price_in_sol, market_cap_in_sol) or None
        DexScreener provides USD values, we convert to SOL for consistency.
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
                    
                    # Get USD prices and market cap from DexScreener
                    price_usd = pair.get("priceUsd")
                    market_cap_usd = pair.get("marketCap")
                    
                    if not price_usd or not market_cap_usd:
                        return None
                    
                    try:
                        price_usd = float(price_usd)
                        market_cap_usd = float(market_cap_usd)
                    except (ValueError, TypeError):
                        return None
                    
                    # Convert USD to SOL using current SOL price
                    # For now, use a reasonable SOL/USD rate (can be made dynamic)
                    SOL_USD_PRICE = 200  # Current SOL price in USD
                    
                    price_in_sol = price_usd / SOL_USD_PRICE
                    market_cap_in_sol = market_cap_usd / SOL_USD_PRICE
                    
                    print(f"[PRICE] 📊 DexScreener for {token_mint[:16]}...: ${price_usd:.10f}/token | MC: ${market_cap_usd:,.0f}", flush=True)
                    
                    return (price_in_sol, market_cap_in_sol)
                    
        except Exception as e:
            print(f"[PRICE_ERROR] DexScreener fetch failed for {token_mint[:16]}...: {e}", flush=True)
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
    async def analyze_post_migration(self, mint: str, signature: str = None):
        """Analyze token's post-migration activity on PumpSwap"""
        if mint in self.analyzed_tokens:
            return
        try:
            print(f"[ANALYZER] 🔍 Analyzing post-migration {mint}", flush=True)
            analyzer = PostMigrationAnalyzer(mint, rpc_url=RPC_HTTP)
            await analyzer.fetch_curve_activity_async()

            summary = analyzer.summary()
            self.analyzed_tokens[mint] = summary
            risk_level = summary.get("risk_level", "🟢 LOW RISK")
            score = summary.get("rug_probability", 0.0)
            print(f"[ANALYZER] {risk_level} | Score: {score:.2%} | {mint}", flush=True)

            # Store analysis results (will be updated with live price in background)
            await self._store_analysis(mint, summary, signature)
        except Exception as e:
            print(f"[ANALYZER] ⚠ Analysis failed for {mint}: {e}", flush=True)

    async def update_live_prices_background(self):
        """Background task: Update live on-chain prices and market caps continuously"""
        await asyncio.sleep(2)  # Wait 2s before starting
        
        while True:
            try:
                tokens = self._get_tokens_needing_price_update()
                
                if not tokens:
                    await asyncio.sleep(5)
                    continue
                
                print(f"\n[PRICE_UPDATE] Starting price cycle for {len(tokens)} tokens...", flush=True)
                updated_count = 0
                failed_count = 0
                
                for i, token_mint in enumerate(tokens, 1):
                    try:
                        # Get the migration transaction for this token to extract price
                        tx_signature = await self._get_migration_tx_for_token(token_mint)
                        
                        if not tx_signature:
                            print(f"[PRICE_UPDATE] [{i}/{len(tokens)}] {token_mint[:16]}... - No migration tx found", flush=True)
                            failed_count += 1
                            continue
                        
                        # Extract on-chain price and market cap from transaction
                        result = await self._extract_price_from_transaction(tx_signature, token_mint)
                        
                        if result is not None:
                            price, market_cap, source = result  # Unpack the source
                            await self._update_price_in_db(token_mint, price, market_cap, source)  # Pass source
                            updated_count += 1
                            source_icon = "✅" if source == "onchain" else "📊"
                            print(f"[PRICE_UPDATE] [{i}/{len(tokens)}] {token_mint[:16]}... {source_icon} ${price:.10f}/token | MC: ${market_cap:,.0f}", flush=True)
                        else:
                            failed_count += 1
                            print(f"[PRICE_UPDATE] [{i}/{len(tokens)}] {token_mint[:16]}... - Failed to extract price", flush=True)
                        
                        # Rate limit
                        await asyncio.sleep(0.1)
                    except Exception as e:
                        failed_count += 1
                        print(f"[PRICE_ERROR] [{i}/{len(tokens)}] {token_mint[:16]}...: {e}", flush=True)
                
                print(f"[PRICE_UPDATE] ✓ Cycle complete: {updated_count} updated, {failed_count} failed\n", flush=True)
                
                # Loop back immediately for continuous live updates
                await asyncio.sleep(1)
                        
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

    async def _update_price_in_db(self, token_mint: str, current_price: float, current_market_cap: float, source: str = "onchain"):
        """
        Update live price, market cap, and price source in database.
        
        Note: Prices and market caps are stored in USD for consistency with DexScreener.
        """
        async with self.db_lock:
            try:
                conn = sqlite3.connect(DB_PATH, timeout=60)
                cursor = conn.cursor()
                
                # Get previous values
                cursor.execute(
                    "SELECT price_current, price_highest, market_cap_current, market_cap_highest, price_source FROM token_analysis WHERE mint = ?",
                    (token_mint,)
                )
                row = cursor.fetchone()
                
                price_highest = row[1] if row and row[1] else current_price
                market_cap_highest = row[3] if row and row[3] else current_market_cap
                old_price = row[0] if row else None
                old_market_cap = row[2] if row else None
                old_source = row[4] if row else None
                
                # Update highest if this is higher
                if current_price > price_highest:
                    price_highest = current_price
                if current_market_cap > market_cap_highest:
                    market_cap_highest = current_market_cap
                
                cursor.execute("""
                    UPDATE token_analysis
                    SET price_current = ?, price_highest = ?, 
                        market_cap_current = ?, market_cap_highest = ?,
                        price_source = ?, price_updated_at = datetime('now')
                    WHERE mint = ?
                """, (current_price, price_highest, current_market_cap, market_cap_highest, source, token_mint))
                
                conn.commit()
                conn.close()
                
                # Log with source indicator
                source_icon = "✅" if source == "onchain" else "📊"
                if old_price is None:
                    print(f"[PRICE_DB] {source_icon} [{source.upper()}] Initial: {token_mint[:16]}... = ${current_price:.10f}/token | MC: ${current_market_cap:,.0f}", flush=True)
                else:
                    price_change = ((current_price - old_price) / old_price * 100) if old_price > 0 else 0
                    mc_change = ((current_market_cap - old_market_cap) / old_market_cap * 100) if old_market_cap > 0 else 0
                    arrow = "📈" if price_change > 0 else "📉" if price_change < 0 else "→"
                    source_change = "" if source == old_source else f" (switched from {old_source})"
                    print(f"[PRICE_DB] {source_icon} {arrow} [{source.upper()}] {token_mint[:16]}... | Price: {price_change:+.1f}% | MC: {mc_change:+.1f}%{source_change}", flush=True)
                
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
                print(f"[MIGRATION] ⚠ Could not extract mint from {signature[:16]}... - SKIPPED", flush=True)
                return  # Silent skip - not a pump.fun token migration

            # Skip if already analyzed
            if self._token_exists_in_db(mint):
                print(f"[MIGRATION] ⏭️  Token {mint} already analyzed - SKIPPED", flush=True)
                return

            self.seen_mints.add(mint)
            print(f"[EVENT] 🚀 MIGRATION DETECTED: {mint}", flush=True)
            print(f"[EVENT] Migration signature: {signature[:16]}...", flush=True)

            # Analyze post-migration token asynchronously with signature for live price tracking
            asyncio.create_task(self.analyze_post_migration(mint, signature))

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
                                    print(f"[WEBSOCKET] 🚨 Migration #{self.websocket_migration_count} detected: {signature[:16]}...", flush=True)
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
