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

    # --- Database ---
    def _ensure_db(self):
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        cursor = conn.cursor()

        # Post-migration token analysis (simplified)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS token_analysis (
                mint TEXT PRIMARY KEY,
                analyzed_at REAL,
                total_txs INTEGER,
                total_events INTEGER,
                mint_concentration REAL,
                unique_minters_ratio REAL,
                sell_suppression_ratio REAL,
                mint_velocity_sec REAL,
                buy_size_variance REAL,
                sell_volume_concentration REAL,
                rug_probability REAL,
                risk_level TEXT,
                coverage REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()
        conn.close()

    async def _store_analysis(self, mint: str, analysis: dict):
        """Store post-migration analysis results"""
        async with self.db_lock:
            try:
                conn = sqlite3.connect(DB_PATH, timeout=60)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=60000")
                cursor = conn.cursor()

                # Try new schema first
                try:
                    cursor.execute("""
                        INSERT OR REPLACE INTO token_analysis (
                            mint, analyzed_at, total_txs, total_events,
                            mint_concentration, unique_minters_ratio, sell_suppression_ratio,
                            mint_velocity_sec, buy_size_variance, sell_volume_concentration,
                            rug_probability, risk_level, coverage
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        mint,
                        time.time(),
                        analysis.get("total_txs", 0),
                        analysis.get("total_events", 0),
                        analysis.get("mint_concentration", 0),
                        analysis.get("unique_minters_ratio", 0),
                        analysis.get("sell_suppression_ratio", 0),
                        analysis.get("mint_velocity_sec", 0),
                        analysis.get("buy_size_variance", 0),
                        analysis.get("sell_volume_concentration", 0),
                        analysis.get("rug_probability", 0),
                        analysis.get("risk_level", ""),
                        analysis.get("coverage", 0)
                    ))
                except sqlite3.OperationalError:
                    # Fall back to old schema
                    cursor.execute("""
                        INSERT OR REPLACE INTO token_analysis (
                            mint, analyzed_at, events_parsed,
                            mint_concentration, unique_minters_ratio, sell_suppression_ratio,
                            mint_velocity_sec, buy_size_variance, sell_volume_concentration,
                            rug_probability, risk_level, pre_migration_coverage
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        analysis.get("rug_probability", 0),
                        analysis.get("risk_level", ""),
                        analysis.get("coverage", 0)
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
    async def analyze_post_migration(self, mint: str):
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

            # Store analysis results
            await self._store_analysis(mint, summary)
        except Exception as e:
            print(f"[ANALYZER] ⚠ Analysis failed for {mint}: {e}", flush=True)

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

            # Analyze post-migration token asynchronously
            asyncio.create_task(self.analyze_post_migration(mint))

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

        while True:
            try:
                endpoint, name = endpoints[current_endpoint_idx]
                async with websockets.connect(endpoint, ping_interval=30, ping_timeout=10) as ws:
                    self.websocket_connected = True
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
                            msg = await asyncio.wait_for(ws.recv(), timeout=30)
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
                        except Exception as e:
                            # Suppress keepalive ping timeout spam
                            if "keepalive" not in str(e).lower():
                                print(f"[WEBSOCKET] ⚠ Error processing message: {e}", flush=True)
                            continue

            except Exception as e:
                self.websocket_connected = False
                error_str = str(e).lower()

                # Check for specific auth issues
                if "401" in str(e) or "unauthorized" in error_str:
                    print(f"[WEBSOCKET] ⚠ Auth error (401) - falling back to public RPC", flush=True)
                    current_endpoint_idx = 1  # Switch to public Solana
                elif "connection" in error_str or "refused" in error_str:
                    print(f"[WEBSOCKET] ⚠ Connection refused, retrying in 5s...", flush=True)
                else:
                    print(f"[WEBSOCKET] ⚠ {name} connection error: {e}", flush=True)
                    print(f"[WEBSOCKET] Retrying in 5s...", flush=True)

                await asyncio.sleep(5)

    # --- Main listener ---
    

    async def listen(self):
        """Main entry point - start WebSocket listener"""
        await self.listen_websocket()


async def main():
    listener = PumpFunCurveListener()
    await listener.listen()


if __name__ == "__main__":
    asyncio.run(main())
