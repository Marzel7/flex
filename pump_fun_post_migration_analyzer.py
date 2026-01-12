#!/usr/bin/env python3
"""
Post-Migration Token Analyzer (PumpSwap)

Analyzes tokens AFTER migration to PumpSwap.
Fetches complete transaction history and calculates risk metrics.
"""

import asyncio
import aiohttp
import requests
import time
from typing import Dict, List
from collections import Counter, defaultdict
from statistics import variance
import os
from dotenv import load_dotenv

load_dotenv()

# Configuration
BATCH_SIZE = 3  # Conservative batch size for QuickNode free tier (15 req/sec = ~1 req/67ms)
MAX_SIGNATURES = 1000000  # Fetch entire transaction history
RPC_TIMEOUT = 60  # Increased timeout to handle slow RPC responses
MAX_RETRIES = 10  # More retries for rate limit recovery
RETRY_DELAYS = [1.0, 2.0, 3.0, 5.0, 8.0, 13.0, 20.0, 30.0, 45.0, 60.0]  # Exponential backoff up to 60s
BATCH_DELAY = 0.5  # Delay between batches to respect rate limits


class PostMigrationAnalyzer:
    """Analyzes token activity on PumpSwap (post-migration)"""

    def __init__(self, token_mint: str, rpc_url: str = "https://api.mainnet-beta.solana.com"):
        self.token_mint = token_mint
        self.rpc_url = rpc_url

        self.events = []
        self.transactions_fetched = 0
        self.signatures_requested = 0

        self.token_name = None
        self.token_symbol = None

    # --- Signature Fetching ---
    def fetch_signatures(self, limit=MAX_SIGNATURES) -> List[str]:
        """Fetch signatures for token mint"""
        all_sigs = []
        before = None
        pages_fetched = 0

        while len(all_sigs) < limit:
            params = {"limit": min(limit - len(all_sigs), 1000)}
            if before:
                params["before"] = before

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [self.token_mint, params]
            }

            try:
                res = requests.post(self.rpc_url, json=payload, timeout=10).json()
                sigs = res.get("result", [])
                
                if not sigs:
                    print(f"[SIG_FETCH] ✓ Reached end of transaction history after {pages_fetched} pages", flush=True)
                    break

                sig_list = [x["signature"] for x in sigs if x.get("err") is None]
                all_sigs.extend(sig_list)
                pages_fetched += 1
                print(f"[SIG_FETCH] Page {pages_fetched}: Fetched {len(sig_list)} signatures (total: {len(all_sigs)})", flush=True)

                if len(sig_list) < 1000:
                    print(f"[SIG_FETCH] ✓ Final page retrieved (less than 1000 sigs)", flush=True)
                    break

                before = sig_list[-1]
            except Exception as e:
                print(f"[SIG_FETCH] ⚠ RPC error: {type(e).__name__}: {str(e)}", flush=True)
                break

        print(f"[SIG_FETCH] ✅ Total signatures fetched: {len(all_sigs)}", flush=True)
        return all_sigs[:limit]

    # --- Async Transaction Fetching ---
    async def fetch_transactions_async(self, sigs: List[str], batch_size: int = BATCH_SIZE):
        """Fetch transactions asynchronously in batches"""
        async with aiohttp.ClientSession() as session:
            successful = 0
            failed = 0
            
            for i in range(0, len(sigs), batch_size):
                batch = sigs[i:i+batch_size]
                tasks = [self.fetch_tx_with_retry(session, sig) for sig in batch]

                results = await asyncio.gather(*tasks, return_exceptions=True)

                for tx in results:
                    if isinstance(tx, Exception) or not tx:
                        failed += 1
                        continue
                    self._parse_curve_tx(tx)
                    self.transactions_fetched += 1
                    successful += 1

                progress = min(i + batch_size, len(sigs))
                success_rate = (successful / progress * 100) if progress > 0 else 0
                print(f"[ASYNC] Progress: {progress}/{len(sigs)} txs | Success: {successful}/{progress} ({success_rate:.1f}%) | Failed: {failed}", flush=True)
                
                # Delay between batches to respect rate limits
                if i + batch_size < len(sigs):
                    await asyncio.sleep(BATCH_DELAY)

    async def fetch_tx_with_retry(self, session: aiohttp.ClientSession, sig: str):
        """Fetch transaction with exponential backoff retry"""
        for attempt in range(MAX_RETRIES):
            try:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransaction",
                    "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
                }

                async with session.post(self.rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=RPC_TIMEOUT)) as resp:
                    # Check HTTP status first
                    if resp.status != 200:
                        if resp.status == 429 or resp.status >= 500:
                            if attempt < MAX_RETRIES - 1:
                                print(f"[FETCH_TX] 📝 HTTP {resp.status} for {sig[:12]}..., retrying (attempt {attempt + 1}/{MAX_RETRIES})", flush=True)
                                await asyncio.sleep(RETRY_DELAYS[attempt])
                                continue
                        print(f"[FETCH_TX] ⚠ HTTP {resp.status} for {sig[:12]}...", flush=True)
                        return None
                    
                    data = await resp.json()

                    if "error" in data:
                        error_code = data["error"].get("code", -1)
                        error_msg = data["error"].get("message", "unknown error")
                        
                        # Retry on rate limit or server errors
                        if error_code in [-32008, -32000, -32003, -32009]:  # Various server/rate limit errors
                            if attempt < MAX_RETRIES - 1:
                                delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS)-1)]
                                print(f"[FETCH_TX] 📝 Retrying {sig[:12]}... (RPC error {error_code}: {error_msg}, attempt {attempt + 1}/{MAX_RETRIES}, waiting {delay}s)", flush=True)
                                await asyncio.sleep(delay)
                                continue
                        
                        # Transaction not indexed yet
                        if error_code == -32602:  # Invalid params / not found
                            if "not found" in error_msg.lower() and attempt < MAX_RETRIES - 1:
                                delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS)-1)]
                                print(f"[FETCH_TX] 📝 Transaction not indexed yet for {sig[:12]}... (attempt {attempt + 1}/{MAX_RETRIES}, waiting {delay}s)", flush=True)
                                await asyncio.sleep(delay)
                                continue
                        
                        print(f"[FETCH_TX] ⚠ RPC error for {sig[:12]}... (code {error_code}: {error_msg})", flush=True)
                        return None

                    result = data.get("result")
                    if not result:
                        # Transaction not indexed yet, retry with backoff
                        if attempt < MAX_RETRIES - 1:
                            delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS)-1)]
                            print(f"[FETCH_TX] 📝 No result for {sig[:12]}..., retrying (attempt {attempt + 1}/{MAX_RETRIES}, waiting {delay}s)", flush=True)
                            await asyncio.sleep(delay)
                            continue
                        print(f"[FETCH_TX] ⚠ Transaction not found after {MAX_RETRIES} retries: {sig[:12]}...", flush=True)
                        return None
                    
                    return result
                    
            except asyncio.TimeoutError:
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS)-1)]
                    print(f"[FETCH_TX] ⏱ Timeout for {sig[:12]}..., retrying (attempt {attempt + 1}/{MAX_RETRIES}, waiting {delay}s)", flush=True)
                    await asyncio.sleep(delay)
                    continue
                print(f"[FETCH_TX] ⚠ Timeout after {MAX_RETRIES} retries: {sig[:12]}...", flush=True)
                return None
            except Exception as e:
                print(f"[FETCH_TX] ⚠ Exception for {sig[:12]}...: {type(e).__name__}: {str(e)}", flush=True)
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS)-1)]
                    await asyncio.sleep(delay)
                    continue
                return None

        print(f"[FETCH_TX] ⚠ Max retries exceeded for {sig[:12]}...", flush=True)
        return None

    def _parse_curve_tx(self, tx):
        """Parse transaction to detect buy/sell events"""
        try:
            meta = tx.get("meta")
            if not meta:
                return

            ts = tx.get("blockTime", int(time.time()))

            pre_balances = meta.get("preTokenBalances", [])
            post_balances = meta.get("postTokenBalances", [])

            for pre, post in zip(pre_balances, post_balances):
                if pre.get("mint") != self.token_mint:
                    continue

                pre_amount = int(pre.get("uiTokenAmount", {}).get("amount", 0))
                post_amount = int(post.get("uiTokenAmount", {}).get("amount", 0))
                delta = post_amount - pre_amount

                if delta == 0:
                    continue

                wallet = post.get("owner")
                if not wallet:
                    continue

                self.events.append({
                    "wallet": wallet,
                    "type": "buy" if delta > 0 else "sell",
                    "amount": abs(delta),
                    "ts": ts
                })
        except Exception:
            pass

    async def fetch_curve_activity_async(self):
        """Main async entry point"""
        print(f"[STREAM] Starting post-migration analysis for {self.token_mint[:16]}...", flush=True)

        sigs = self.fetch_signatures(limit=MAX_SIGNATURES)
        print(f"[STREAM] Fetched {len(sigs)} signatures, starting async fetch...", flush=True)

        if not sigs:
            print(f"[STREAM] ⚠ No signatures found", flush=True)
            return

        self.signatures_requested = len(sigs)
        await self.fetch_transactions_async(sigs, batch_size=BATCH_SIZE)

        print(f"[STREAM] ✅ Analysis complete: {len(self.events)} events from {self.transactions_fetched} txs", flush=True)

    # --- Risk Metrics ---

    def mint_concentration(self):
        """% of transactions from top 5 wallets"""
        buys = [e for e in self.events if e["type"] == "buy"]
        if not buys:
            return 0.0

        wallet_counts = Counter(e["wallet"] for e in buys)
        total = sum(wallet_counts.values())
        top5_sum = sum(v for _, v in wallet_counts.most_common(5))
        return top5_sum / total if total else 0.0

    def unique_minters_ratio(self):
        """Ratio of unique wallets to total buys"""
        buys = [e for e in self.events if e["type"] == "buy"]
        if not buys:
            return 0.0

        unique = len(set(e["wallet"] for e in buys))
        return unique / len(buys)

    def sell_suppression_ratio(self):
        """% of sells vs total events"""
        if not self.events:
            return 0.0

        sells = sum(1 for e in self.events if e["type"] == "sell")
        return sells / len(self.events)

    def mint_velocity(self):
        """Average time between buys (seconds)"""
        buys = [e for e in self.events if e["type"] == "buy"]
        if len(buys) < 2:
            return 0.0

        timestamps = sorted(e["ts"] for e in buys)
        intervals = [timestamps[i + 1] - timestamps[i] for i in range(len(timestamps) - 1)]
        return sum(intervals) / len(intervals) if intervals else 0.0

    def buy_size_variance(self):
        """Variance in buy amounts (normalized)"""
        buys = [e for e in self.events if e["type"] == "buy"]
        if len(buys) < 3:
            return 0.0

        try:
            amounts = [e["amount"] for e in buys]
            mean_amount = sum(amounts) / len(amounts)
            if mean_amount == 0:
                return 0.0
            normalized = [a / mean_amount for a in amounts]
            return variance(normalized)
        except:
            return 0.0

    def sell_volume_concentration(self):
        """% of sell volume from top 3 sellers"""
        sells = [e for e in self.events if e["type"] == "sell"]
        if not sells:
            return 0.0

        wallet_volumes = defaultdict(int)
        for e in sells:
            wallet_volumes[e["wallet"]] += e["amount"]

        total = sum(wallet_volumes.values())
        top3_sum = sum(v for _, v in sorted(wallet_volumes.items(), key=lambda x: x[1], reverse=True)[:3])
        return top3_sum / total if total else 0.0

    def compute_rug_score(self):
        """Calculate rug probability (0-1)"""
        score = 0.0

        mint_conc = self.mint_concentration()
        if mint_conc > 0.7:
            score += 0.25
        elif mint_conc > 0.5:
            score += 0.15

        unique_ratio = self.unique_minters_ratio()
        if unique_ratio < 0.15:
            score += 0.20
        elif unique_ratio < 0.25:
            score += 0.10

        sell_ratio = self.sell_suppression_ratio()
        if sell_ratio < 0.05:
            score += 0.20
        elif sell_ratio < 0.10:
            score += 0.10

        velocity = self.mint_velocity()
        if velocity < 5:
            score += 0.15
        elif velocity < 10:
            score += 0.08

        var = self.buy_size_variance()
        if var < 1e6:
            score += 0.15
        elif var < 1e7:
            score += 0.08

        sell_conc = self.sell_volume_concentration()
        if sell_conc > 0.5:
            score += 0.05

        return round(min(score, 1.0), 3)

    def get_risk_level(self, score: float) -> str:
        """Determine risk level from score"""
        if score >= 0.7:
            return "🔴 HIGH RISK"
        elif score >= 0.4:
            return "🟡 MEDIUM RISK"
        else:
            return "🟢 LOW RISK"

    def summary(self) -> Dict:
        """Get complete analysis summary"""
        score = self.compute_rug_score()

        return {
            "mint": self.token_mint,
            "total_txs": self.transactions_fetched,
            "total_events": len(self.events),
            "rug_probability": score,
            "risk_level": self.get_risk_level(score),
            "mint_concentration": self.mint_concentration(),
            "unique_minters_ratio": self.unique_minters_ratio(),
            "sell_suppression_ratio": self.sell_suppression_ratio(),
            "mint_velocity_sec": self.mint_velocity(),
            "buy_size_variance": self.buy_size_variance(),
            "sell_volume_concentration": self.sell_volume_concentration(),
            "coverage": (self.transactions_fetched / self.signatures_requested * 100) if self.signatures_requested > 0 else 0
        }
