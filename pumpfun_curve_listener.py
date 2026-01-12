#!/usr/bin/env python3
"""
Pump.Fun Bonding Curve Listener (Pre-Migration Only)
Monitors Pump.Fun program for new token mints before migration.
"""

import asyncio
import time
import os
import sqlite3
from typing import Set, Optional, List
import aiohttp
from solders.pubkey import Pubkey
from solders.signature import Signature
from pump_fun_pre_migration_analyzer_v2 import PumpFunPreMigrationAnalyzerV2
from solana.rpc.async_api import AsyncClient
from dotenv import load_dotenv

load_dotenv()

# === Config ===
PUMPFUN_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
RPC_HTTP = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}" if HELIUS_API_KEY else "https://api.mainnet-beta.solana.com"
DB_PATH = "pumpswap_tokens.db"
MARKET_CAP_THRESHOLD_USD = 30000
MIGRATION_MARKET_CAP_USD = 80000
POLL_INTERVAL = 5
FETCH_LIMIT = 20  # number of recent transactions per poll

class PumpFunCurveListener:
    def __init__(self):
        self.seen_mints: Set[str] = set()
        self.filtered_mints: Set[str] = set()
        self.analyzed_tokens = {}
        self.last_signature = None
        self.poll_count = 0
        self.db_lock = asyncio.Lock()  # lock for DB writes
        self._ensure_db()
        print(f"[INIT] Pump.Fun Bonding Curve Listener ready", flush=True)
        print(f"[INIT] Monitoring Pump.Fun program: {PUMPFUN_PROGRAM_ID}", flush=True)
        print(f"[INIT] Market cap threshold: ${MARKET_CAP_THRESHOLD_USD}", flush=True)

    # --- Database ---
    def _ensure_db(self):
        conn = sqlite3.connect(DB_PATH)
        # Enable WAL mode for concurrent write support
        conn.execute("PRAGMA journal_mode=WAL")
        cursor = conn.cursor()

        # Table 1: Detected tokens with basic market data
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS curve_completions (
                mint TEXT PRIMARY KEY,
                detected_at REAL,
                market_cap_usd REAL,
                signature TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Table 2: Pre-migration analysis results for purchase strategy
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS token_analysis (
                mint TEXT PRIMARY KEY,
                analyzed_at REAL,
                events_parsed INTEGER,
                mint_concentration REAL,
                unique_minters_ratio REAL,
                sell_suppression_ratio REAL,
                mint_velocity_sec REAL,
                buy_size_variance REAL,
                sell_volume_concentration REAL,
                rug_probability REAL,
                risk_level TEXT,
                creator_activity_ratio REAL,
                amm_rug_probability REAL,
                amm_risk_level TEXT,
                pre_migration_coverage REAL,
                post_migration_mint_concentration REAL,
                post_migration_unique_minters_ratio REAL,
                post_migration_sell_suppression_ratio REAL,
                post_migration_mint_velocity_sec REAL,
                post_migration_buy_size_variance REAL,
                post_migration_sell_volume_concentration REAL,
                post_migration_creator_activity_ratio REAL,
                post_migration_coverage REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Add coverage columns if they don't exist (for existing databases)
        try:
            cursor.execute("ALTER TABLE token_analysis ADD COLUMN pre_migration_coverage REAL")
        except sqlite3.OperationalError:
            pass  # Column already exists

        try:
            cursor.execute("ALTER TABLE token_analysis ADD COLUMN post_migration_coverage REAL")
        except sqlite3.OperationalError:
            pass  # Column already exists

        conn.commit()
        conn.close()

    async def _store_completion(self, mint: str, market_cap: float, signature: str):
        async with self.db_lock:
            try:
                conn = sqlite3.connect(DB_PATH, timeout=60)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=60000")
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO curve_completions (mint, detected_at, market_cap_usd, signature)
                    VALUES (?, ?, ?, ?)
                """, (mint, time.time(), market_cap, signature))
                conn.commit()
                conn.close()
                print(f"[DB] ✅ Stored {mint} (${market_cap:,.0f})", flush=True)
            except Exception as e:
                print(f"[DB] ❌ Failed to store {mint}: {e}", flush=True)

    async def _store_analysis(self, mint: str, analysis: dict):
        """Store analysis results for purchase strategy decision"""
        async with self.db_lock:
            try:
                conn = sqlite3.connect(DB_PATH, timeout=60)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=60000")
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO token_analysis (
                        mint, analyzed_at, events_parsed, mint_concentration,
                        unique_minters_ratio, sell_suppression_ratio, mint_velocity_sec,
                        buy_size_variance, sell_volume_concentration, rug_probability,
                        risk_level, creator_activity_ratio, amm_rug_probability, amm_risk_level,
                        pre_migration_coverage
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    mint,
                    time.time(),
                    analysis.get("events_parsed", 0),
                    analysis.get("mint_concentration", 0),
                    analysis.get("unique_minters_ratio", 0),
                    analysis.get("sell_suppression_ratio", 0),
                    analysis.get("mint_velocity_sec", 0),
                    analysis.get("buy_size_variance", 0),
                    analysis.get("sell_volume_concentration", 0),
                    analysis.get("rug_probability", 0),
                    analysis.get("risk_level", ""),
                    analysis.get("creator_activity_ratio", 0),
                    analysis.get("rug_probability", 0),  # V2 uses rug_probability, store in amm_rug_probability
                    analysis.get("risk_level", ""),      # V2 uses risk_level, store in amm_risk_level
                    analysis.get("pre_migration_coverage", 0)
                ))
                conn.commit()
                conn.close()
                print(f"[DB] ✅ Stored analysis for {mint}", flush=True)
            except Exception as e:
                print(f"[DB] ❌ Failed to store analysis for {mint}: {e}", flush=True)

    def _is_already_processed(self, mint: str) -> bool:
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM curve_completions WHERE mint = ?", (mint,))
            result = cursor.fetchone()
            conn.close()
            return bool(result)
        except Exception as e:
            print(f"[DB] ⚠ Could not check {mint}: {e}", flush=True)
            return False

    def _token_exists_in_db(self, mint: str) -> bool:
        """Check if token exists in analysis table (previously migrated or analyzed)"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()
            # Check if token is in token_analysis (either migrated or previously analyzed)
            cursor.execute("SELECT 1 FROM token_analysis WHERE mint = ?", (mint,))
            result = cursor.fetchone()
            conn.close()
            return bool(result)
        except Exception as e:
            print(f"[DB] ⚠ Could not check if token exists: {e}", flush=True)
            return False

    # --- Market Cap ---
    async def get_token_market_cap(self, mint: str) -> float:
        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("pairs"):
                            pair = data["pairs"][0]
                            market_cap = pair.get("marketCap", 0)
                            if market_cap:
                                return float(market_cap)
            return 0.0
        except Exception as e:
            print(f"[MARKET_CAP] ⚠ Error for {mint}: {e}", flush=True)
            return 0.0

    # --- Fetch transactions ---
    async def fetch_recent_signatures(self, client: AsyncClient) -> List[dict]:
        try:
            pubkey = Pubkey.from_string(PUMPFUN_PROGRAM_ID)
            resp = await client.get_signatures_for_address(pubkey, limit=FETCH_LIMIT)
            txs = []
            for sig_info in resp.value:
                if sig_info.signature:
                    txs.append({
                        "signature": str(sig_info.signature),
                        "slot": sig_info.slot if sig_info.slot else 0,
                        "err": sig_info.err
                    })
            print(f"[FETCH] 📡 Found {len(txs)} recent Pump.Fun transactions", flush=True)
            return txs
        except Exception as e:
            print(f"[FETCH] ⚠ Error: {e}", flush=True)
            return []

    async def fetch_transaction(self, client: AsyncClient, signature: str) -> Optional[dict]:
        try:
            sig_obj = Signature.from_string(signature)
            resp = await client.get_transaction(
                sig_obj,
                encoding="json",
                max_supported_transaction_version=0
            )
            if resp.value:
                return resp.value
            return None
        except Exception as e:
            print(f"[TX] ⚠ Error fetching {signature[:8]}: {e}", flush=True)
            return None

    async def extract_mints_from_tx(self, tx) -> Set[str]:
        try:
            if not hasattr(tx, 'transaction') or not hasattr(tx.transaction, 'meta'):
                return set()
            meta = tx.transaction.meta
            if not meta or not hasattr(meta, 'post_token_balances'):
                return set()
            mints = set()
            for balance in meta.post_token_balances or []:
                mint = balance.mint
                if mint and str(mint) not in self.seen_mints:
                    mints.add(str(mint))
            return mints
        except Exception as e:
            print(f"[MINT] ⚠ Error extracting mints: {e}", flush=True)
            return set()

    # --- Analyzer ---
    async def analyze_curve(self, mint: str, force_reanalyze: bool = False):
        """Analyze token's pre-migration curve activity

        Args:
            mint: Token mint address
            force_reanalyze: If True, re-analyze even if already analyzed (for better data at higher price)
        """
        if mint in self.analyzed_tokens and not force_reanalyze:
            return
        try:
            action = "Re-analyzing" if (mint in self.analyzed_tokens and force_reanalyze) else "Analyzing"
            print(f"[ANALYZER] 🔍 {action} {mint}", flush=True)
            analyzer = PumpFunPreMigrationAnalyzerV2(mint, rpc_url=RPC_HTTP)
            await analyzer.fetch_curve_activity_async()  # Async fetch with batching
            summary = analyzer.summary()
            self.analyzed_tokens[mint] = summary
            risk_level = summary.get("risk_level", "🟢 LOW RISK")
            score = summary.get("rug_probability", 0.0)
            print(f"[ANALYZER] {risk_level} | Score: {score:.2%} | {mint}", flush=True)

            # Store analysis results for purchase strategy (overwrites if re-analyzing)
            await self._store_analysis(mint, summary)
        except Exception as e:
            print(f"[ANALYZER] ⚠ Analysis failed for {mint}: {e}", flush=True)

    async def handle_mint(self, mint: str, signature: str):
        """Process detected mint safely"""
        # Skip already seen mints immediately
        if mint in self.seen_mints:
            return

        # Fetch market cap first
        market_cap = await self.get_token_market_cap(mint)

        # Skip low market cap tokens
        if market_cap < MARKET_CAP_THRESHOLD_USD:
            return

        # Skip if market cap is already above migration threshold
        if market_cap >= MIGRATION_MARKET_CAP_USD:
            return

        # Check if token already exists in database
        token_exists = self._token_exists_in_db(mint)

        if token_exists:
            # Re-analyze if token is above $50k (closer to migration, better data)
            if market_cap < 50000:
                self.seen_mints.add(mint)
                print(f"[FILTER] ⏭️  Token {mint} already analyzed at lower price - SKIPPED", flush=True)
                return
            else:
                # Token above $50k - re-analyze for better pre-migration data
                print(f"[FILTER] 🔄 Token {mint} already analyzed, but now at ${market_cap:,.0f} - RE-ANALYZING for better data", flush=True)

        # Only now mark it as seen
        self.seen_mints.add(mint)
        self.filtered_mints.add(mint)

        print(f"[EVENT] 📍 Detected token: {mint}", flush=True)
        print(f"[FILTER] ✅ Market cap ${market_cap:,.0f} within target range (${MARKET_CAP_THRESHOLD_USD:,} - ${MIGRATION_MARKET_CAP_USD:,}) - PROCEEDING", flush=True)

        # Store in DB
        await self._store_completion(mint, market_cap, signature)

        # Analyze token asynchronously
        # Force re-analyze if token was previously analyzed but is now above $50k (better data at higher price)
        force_reanalyze = token_exists and market_cap >= 50000
        asyncio.create_task(self.analyze_curve(mint, force_reanalyze=force_reanalyze))

    # --- Main listener ---
    async def listen(self):
        print(f"\n[LISTENER] Starting Pump.Fun monitoring...", flush=True)
        async with AsyncClient(RPC_HTTP) as client:
            last_status = time.time()
            while True:
                try:
                    self.poll_count += 1
                    current_time = time.time()
                    if current_time - last_status >= 30:
                        print(f"[STATUS] 📊 Poll #{self.poll_count} - Detected: {len(self.seen_mints)}, Filtered: {len(self.filtered_mints)}, Analyzed: {len(self.analyzed_tokens)}", flush=True)
                        last_status = current_time

                    txs = await self.fetch_recent_signatures(client)

                    # Filter to only new signatures
                    new_txs_with_sigs = []
                    for tx_info in txs:
                        sig = tx_info.get("signature")
                        if sig and sig != self.last_signature:
                            new_txs_with_sigs.append(sig)
                            self.last_signature = sig

                    # Fetch all new transactions concurrently (non-blocking)
                    if new_txs_with_sigs:
                        fetch_tasks = [self.fetch_transaction(client, sig) for sig in new_txs_with_sigs]
                        full_txs = await asyncio.gather(*fetch_tasks, return_exceptions=True)

                        # Process results with their signatures
                        for sig, full_tx in zip(new_txs_with_sigs, full_txs):
                            if isinstance(full_tx, Exception) or not full_tx:
                                continue
                            mints = await self.extract_mints_from_tx(full_tx)
                            for mint in mints:
                                # Spawn handle_mint as background task (non-blocking)
                                asyncio.create_task(self.handle_mint(mint, sig))

                    await asyncio.sleep(POLL_INTERVAL)
                except KeyboardInterrupt:
                    print(f"\n[LISTENER] Interrupted by user", flush=True)
                    break
                except Exception as e:
                    print(f"[LISTENER] ⚠ Error: {e}", flush=True)
                    await asyncio.sleep(POLL_INTERVAL)


async def main():
    listener = PumpFunCurveListener()
    await listener.listen()


if __name__ == "__main__":
    asyncio.run(main())
