#!/usr/bin/env python3
"""
Pump.Fun Bonding Curve Listener (HTTP Polling Version)

Alternative implementation using HTTP polling instead of WebSocket.
More reliable when WebSocket subscription doesn't return messages.
"""

import asyncio
import time
import json
import sqlite3
import os
import base64
import struct
from typing import Set, Dict, Optional
from solders.pubkey import Pubkey
from solana.rpc.async_api import AsyncClient
from dotenv import load_dotenv
from pump_fun_pre_migration_analyzer import PumpFunPreMigrationAnalyzer
import requests

load_dotenv()

# Pump.Fun Program ID
PUMPFUN_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

# Helius RPC
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
RPC_HTTP = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}" if HELIUS_API_KEY else "https://api.mainnet-beta.solana.com"

# Database
DB_PATH = "pumpswap_tokens.db"

# Market cap threshold (USD)
MARKET_CAP_THRESHOLD_USD = 50000

# Helius gRPC webhook for real-time events (if using Helius account)
HELIUS_WEBHOOK_URL = os.getenv("HELIUS_WEBHOOK_URL", None)


class PumpFunCurveListener:
    def __init__(self, rpc_url=RPC_HTTP, db_path=DB_PATH):
        self.rpc_url = rpc_url
        self.db_path = db_path
        self.seen_mints: Set[str] = set()
        self.completed_curves = {}
        self.analyzed_tokens = {}
        self.filtered_mints: Set[str] = set()
        self.last_block_checked = 0
        print(f"[LISTENER] Initialized with RPC: {rpc_url[:50]}...", flush=True)

    async def get_token_market_cap(self, mint: str) -> float:
        """Fetch token market cap in USD from DexScreener"""
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
            response = requests.get(url, timeout=5)
            response.raise_for_status()
            data = response.json()

            if data.get("pairs"):
                pair = data["pairs"][0]
                market_cap = pair.get("marketCap", 0)
                if market_cap:
                    return float(market_cap)
            return 0.0
        except Exception as e:
            print(f"[MARKET_CAP] Error fetching market cap for {mint[:16]}: {e}", flush=True)
            return 0.0

    async def is_curve_complete(self, client: AsyncClient, mint: str) -> bool:
        """Check bonding curve PDA sold >= max supply"""
        try:
            mint_pubkey = Pubkey.from_string(mint)
            seeds = [b"bonding_curve", bytes(mint_pubkey)]
            curve_pda, _ = Pubkey.find_program_address(
                seeds, Pubkey.from_string(PUMPFUN_PROGRAM_ID)
            )

            resp = await client.get_account_info(curve_pda, encoding="base64")
            if resp.value is None:
                return False

            data_b64 = resp.value.data[0]
            raw = base64.b64decode(data_b64)
            sold = struct.unpack_from("<Q", raw, 8)[0]
            max_supply = struct.unpack_from("<Q", raw, 16)[0]

            return sold >= max_supply
        except Exception:
            return False

    async def analyze_curve(self, mint: str):
        """Run pre-migration analyzer on a completed token"""
        try:
            if mint in self.analyzed_tokens:
                return

            print(f"[ANALYZER] 🔍 Analyzing {mint[:16]}...", flush=True)
            analyzer = PumpFunPreMigrationAnalyzer(mint, rpc_url=self.rpc_url)
            analyzer.fetch_curve_activity(limit=200)
            summary = analyzer.summary()
            self.analyzed_tokens[mint] = summary

            risk_level = summary.get("amm_risk_level", "🟢 Low")
            score = summary.get("amm_rug_probability", 0.0)
            print(f"[ANALYZER] {risk_level} | Score: {score:.2%} | {mint[:16]}...", flush=True)
        except Exception as e:
            print(f"[ANALYZER] ❌ Analysis failed for {mint}: {e}", flush=True)

    async def handle_mint(self, client: AsyncClient, mint: str):
        """Process a detected mint - check market cap and poll for completion"""
        try:
            if mint in self.seen_mints:
                return

            self.seen_mints.add(mint)
            print(f"[EVENT] 📍 Detected bonding activity for {mint[:16]}...", flush=True)

            # Check market cap filter
            market_cap = await self.get_token_market_cap(mint)
            if market_cap < MARKET_CAP_THRESHOLD_USD:
                print(f"[FILTER] ❌ Market cap ${market_cap:,.0f} < ${MARKET_CAP_THRESHOLD_USD:,.0f} - SKIPPED", flush=True)
                return

            print(f"[FILTER] ✅ Market cap ${market_cap:,.0f} >= ${MARKET_CAP_THRESHOLD_USD:,.0f} - PROCEEDING", flush=True)
            self.filtered_mints.add(mint)

            # Poll PDA until curve completes
            max_polls = 120  # 20 minutes max (10s intervals)
            for poll_count in range(max_polls):
                complete = await self.is_curve_complete(client, mint)
                if complete:
                    print(f"[EVENT] 🔴 Bonding curve COMPLETE: {mint[:16]}...", flush=True)
                    self.completed_curves[mint] = time.time()
                    await self.analyze_curve(mint)
                    break
                if poll_count % 6 == 0 and poll_count > 0:
                    print(f"[EVENT] ⏳ Still waiting for curve completion ({poll_count*10}s elapsed)...", flush=True)
                await asyncio.sleep(10)
        except Exception as e:
            print(f"[ERROR] Error handling mint {mint}: {e}", flush=True)

    async def fetch_recent_transactions(self, client: AsyncClient) -> list:
        """Fetch recent transactions from Pump.Fun program"""
        try:
            # Get recent block
            block_resp = await client.get_block_height()
            current_block = block_resp.value

            # Limit lookback to avoid rate limits
            if self.last_block_checked == 0:
                start_block = current_block - 100
                self.last_block_checked = current_block
            else:
                start_block = self.last_block_checked
                self.last_block_checked = current_block

            # This is a simplified approach - in production you'd use Helius gRPC or webhook
            print(f"[POLL] Checking blocks {start_block} to {current_block}...", flush=True)

            # For now, return empty list - the actual implementation would fetch TXs
            return []
        except Exception as e:
            print(f"[POLL] Error fetching recent transactions: {e}", flush=True)
            return []

    async def listen_with_helius_webhook(self):
        """Listen via Helius webhook (requires account configuration)"""
        if not HELIUS_WEBHOOK_URL:
            print("[WEBHOOK] No HELIUS_WEBHOOK_URL set in .env", flush=True)
            return False

        print(f"[WEBHOOK] Using Helius webhook: {HELIUS_WEBHOOK_URL[:40]}...", flush=True)
        # Webhook setup would be done in your server code
        # This is just a stub
        return True

    async def listen(self):
        """Main listener loop using HTTP polling"""
        print(f"\n[LISTENER] Starting pump.fun curve completion listener...", flush=True)
        print(f"[LISTENER] Using HTTP polling method (more reliable)...", flush=True)

        async with AsyncClient(self.rpc_url) as client:
            try:
                # Try Helius webhook first
                if HELIUS_WEBHOOK_URL:
                    if await self.listen_with_helius_webhook():
                        # Webhook listening is set up in your server
                        print("[LISTENER] Waiting for webhook events...", flush=True)
                        while True:
                            await asyncio.sleep(60)

                # Fallback to polling
                print("[LISTENER] Polling for transactions every 10 seconds...", flush=True)
                last_status = time.time()

                while True:
                    try:
                        txs = await self.fetch_recent_transactions(client)

                        # Log status every 30 seconds
                        current_time = time.time()
                        if current_time - last_status >= 30:
                            print(f"[POLL] ✓ Polling... Detected {len(self.seen_mints)} mints, {len(self.filtered_mints)} filtered, {len(self.completed_curves)} completed", flush=True)
                            last_status = current_time

                        # Process transactions
                        for tx in txs:
                            # Extract mints from transaction
                            # This would parse transaction logs for "Mint:" strings
                            pass

                        await asyncio.sleep(10)

                    except KeyboardInterrupt:
                        raise
                    except Exception as e:
                        print(f"[POLL] Error: {e}", flush=True)
                        await asyncio.sleep(5)

            except KeyboardInterrupt:
                print(f"\n[LISTENER] Interrupted by user", flush=True)
                print(f"[LISTENER] Summary:")
                print(f"  Total detected: {len(self.seen_mints)}")
                print(f"  Passed market cap filter: {len(self.filtered_mints)}")
                print(f"  Curves completed: {len(self.completed_curves)}")
                print(f"  Curves analyzed: {len(self.analyzed_tokens)}")
            except Exception as e:
                print(f"[LISTENER] ❌ Error: {e}", flush=True)
                raise


async def main():
    listener = PumpFunCurveListener()
    await listener.listen()


if __name__ == "__main__":
    asyncio.run(main())
