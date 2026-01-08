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
from pump_fun_pre_migration_analyzer import PumpFunPreMigrationAnalyzer
from solana.rpc.async_api import AsyncClient
from dotenv import load_dotenv

load_dotenv()

# === Config ===
PUMPFUN_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
RPC_HTTP = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}" if HELIUS_API_KEY else "https://api.mainnet-beta.solana.com"
DB_PATH = "pumpswap_tokens.db"
MARKET_CAP_THRESHOLD_USD = 50000
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
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS curve_completions (
                mint TEXT PRIMARY KEY,
                detected_at REAL,
                market_cap_usd REAL,
                signature TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()

    async def _store_completion(self, mint: str, market_cap: float, signature: str):
        async with self.db_lock:
            try:
                conn = sqlite3.connect(DB_PATH, timeout=30)
                # Ensure WAL mode is set for this connection too
                conn.execute("PRAGMA journal_mode=WAL")
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

    def _is_already_processed(self, mint: str) -> bool:
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM curve_completions WHERE mint = ?", (mint,))
            result = cursor.fetchone()
            conn.close()
            return bool(result)
        except Exception as e:
            print(f"[DB] ⚠ Could not check {mint}: {e}", flush=True)
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
    async def analyze_curve(self, mint: str):
        if mint in self.analyzed_tokens:
            return
        try:
            print(f"[ANALYZER] 🔍 Analyzing {mint}", flush=True)
            analyzer = PumpFunPreMigrationAnalyzer(mint, rpc_url=RPC_HTTP)
            analyzer.fetch_curve_activity(limit=200)
            summary = analyzer.summary()
            self.analyzed_tokens[mint] = summary
            risk_level = summary.get("amm_risk_level", "🟢 Low")
            score = summary.get("amm_rug_probability", 0.0)
            print(f"[ANALYZER] {risk_level} | Score: {score:.2%} | {mint}", flush=True)
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
            print(f"[FILTER] ❌ Market cap ${market_cap:,.0f} < ${MARKET_CAP_THRESHOLD_USD} USD - SKIPPED", flush=True)
            return

        # Skip if market cap is already above migration threshold
        if market_cap >= MIGRATION_MARKET_CAP_USD:
            print(f"[FILTER] ⚠ Market cap ${market_cap:,.0f} >= ${MIGRATION_MARKET_CAP_USD} USD - Already migrated or too high, SKIPPED", flush=True)
            return

        # Only now mark it as seen
        self.seen_mints.add(mint)
        self.filtered_mints.add(mint)

        print(f"[EVENT] 📍 Detected token: {mint}", flush=True)
        print(f"[FILTER] ✅ Market cap ${market_cap:,.0f} within target range (${MARKET_CAP_THRESHOLD_USD:,} - ${MIGRATION_MARKET_CAP_USD:,}) - PROCEEDING", flush=True)

        # Store in DB
        await self._store_completion(mint, market_cap, signature)

        # Analyze token asynchronously
        asyncio.create_task(self.analyze_curve(mint))

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
                    for tx_info in txs:
                        sig = tx_info.get("signature")
                        if not sig or sig == self.last_signature:
                            continue
                        self.last_signature = sig
                        full_tx = await self.fetch_transaction(client, sig)
                        if not full_tx:
                            continue
                        mints = await self.extract_mints_from_tx(full_tx)
                        for mint in mints:
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
