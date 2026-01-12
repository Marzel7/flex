#!/usr/bin/env python3
"""
PumpFun Pre-Migration Analyzer - Batched Parallel Streaming

Optimized for:
- Fast parallel transaction fetching (ThreadPoolExecutor)
- Streaming parse: transactions discarded after processing
- Batched RPC calls for stability
- Retry logic with exponential backoff
- Memory efficient (no transaction caching)
"""

import os
import time
import requests
from collections import defaultdict, Counter
from statistics import variance
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Dict
from dotenv import load_dotenv

load_dotenv()

# Configuration
BATCH_SIZE = 50  # Signatures per batch
MAX_WORKERS = 20  # Parallel workers per batch
MAX_SIGNATURES = 40000
RPC_TIMEOUT = 15
MAX_RETRIES = 5
RETRY_DELAYS = [0.2, 0.5, 1.0, 2.0, 3.0]


class PumpFunPreMigrationAnalyzerParallel:
    """
    Batched parallel analyzer using ThreadPoolExecutor.
    Fetches transactions in parallel batches, parses immediately, discards.
    """

    def __init__(self, token_mint: str, rpc_url: str = "https://api.mainnet-beta.solana.com"):
        self.token_mint = token_mint
        self.rpc_url = rpc_url

        # Metrics tracking
        self.events = []
        self.transactions_fetched = 0
        self.signatures_requested = 0
        self.failed_fetches = 0

        # Token metadata
        self.token_name = None
        self.token_symbol = None

    # ==========================================================
    # SIGNATURE FETCHING
    # ==========================================================
    def fetch_signatures(self, limit=MAX_SIGNATURES) -> List[str]:
        """Fetch signatures via Helius first, fallback to RPC"""
        sigs = self._get_signatures_helius(limit)
        if not sigs:
            print(f"[FETCH] Helius unavailable, falling back to RPC", flush=True)
            sigs = self._get_signatures_rpc(limit)
        else:
            print(f"[FETCH] ✅ Helius API succeeded", flush=True)

        self.signatures_requested = len(sigs)
        return sigs

    def _get_signatures_helius(self, limit) -> List[str]:
        """Fetch from Helius API"""
        helius_api_key = os.getenv("HELIUS_API_KEY")
        if not helius_api_key:
            return []

        print(f"[FETCH] 🚀 Using Helius API", flush=True)
        all_sigs = []
        page_token = None

        while len(all_sigs) < limit:
            url = f"https://api.helius.xyz/v0/addresses/{self.token_mint}/transactions"
            params = {"api-key": helius_api_key, "limit": 1000, "type": "all"}
            if page_token:
                params["page-token"] = page_token

            try:
                res = requests.get(url, params=params, timeout=30).json()
                txs = res.get("transactions", [])
                if not txs:
                    break

                sigs = [tx.get("signature") for tx in txs if tx.get("signature")]
                all_sigs.extend(sigs)

                page_token = res.get("page-token")
                if not page_token:
                    break

            except Exception as e:
                print(f"[FETCH] Helius error: {e}", flush=True)
                break

        return all_sigs[:limit]

    def _get_signatures_rpc(self, limit) -> List[str]:
        """Fetch from RPC via getSignaturesForAddress"""
        all_sigs = []
        before = None

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
                    break

                sig_list = [x["signature"] for x in sigs if x.get("err") is None]
                all_sigs.extend(sig_list)

                if len(sig_list) < 1000:
                    break

                before = sig_list[-1]

            except Exception as e:
                print(f"[FETCH] RPC error: {e}", flush=True)
                break

        return all_sigs[:limit]

    # ==========================================================
    # PARALLEL TRANSACTION FETCHING
    # ==========================================================
    def fetch_transactions_parallel(self, signatures: List[str], batch_size: int = BATCH_SIZE, max_workers: int = MAX_WORKERS):
        """Fetch transactions in parallel batches using ThreadPoolExecutor"""

        def fetch_tx_with_retry(sig, retry_count=0):
            """Fetch single transaction with exponential backoff"""
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
            }
            try:
                resp = requests.post(self.rpc_url, json=payload, timeout=RPC_TIMEOUT)
                result = resp.json().get("result")
                if result:
                    return result
            except Exception as e:
                pass

            # Retry with exponential backoff
            if retry_count < MAX_RETRIES:
                delay = RETRY_DELAYS[retry_count]
                time.sleep(delay)
                return fetch_tx_with_retry(sig, retry_count + 1)

            self.failed_fetches += 1
            return None

        # Process signatures in batches
        for batch_idx in range(0, len(signatures), batch_size):
            batch = signatures[batch_idx:batch_idx + batch_size]

            # Fetch batch in parallel
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(fetch_tx_with_retry, sig): sig for sig in batch}

                for future in as_completed(futures):
                    try:
                        tx = future.result()
                        if tx and tx.get("meta"):
                            self.transactions_fetched += 1
                            self._parse_curve_tx(tx)
                    except Exception as e:
                        pass

            # Progress update
            progress = min(batch_idx + batch_size, len(signatures))
            print(f"[PARALLEL] Processed {progress}/{len(signatures)} txs", flush=True)

    # ==========================================================
    # MAIN ANALYSIS
    # ==========================================================
    def fetch_curve_activity(self):
        """Main entry point: fetch and analyze"""
        print(f"[STREAM] Starting pre-migration analysis for {self.token_mint[:16]}...", flush=True)

        # Fetch signatures
        sigs = self.fetch_signatures(limit=MAX_SIGNATURES)
        print(f"[STREAM] Fetched {len(sigs)} signatures, starting parallel fetch...", flush=True)

        if not sigs:
            print(f"[STREAM] ⚠ No signatures found", flush=True)
            return

        # Fetch transactions in parallel batches
        self.fetch_transactions_parallel(sigs, batch_size=BATCH_SIZE, max_workers=MAX_WORKERS)

        print(f"[STREAM] ✅ Analysis complete: {len(self.events)} events from {self.transactions_fetched} txs", flush=True)

    # ==========================================================
    # TRANSACTION PARSER
    # ==========================================================
    def _parse_curve_tx(self, tx):
        """Parse token balance changes to detect buys/sells (streaming)"""
        try:
            meta = tx.get("meta")
            if not meta:
                return

            ts = tx.get("blockTime", int(time.time()))

            pre_balances = meta.get("preTokenBalances", [])
            post_balances = meta.get("postTokenBalances", [])

            # Parse balance deltas
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

    # ==========================================================
    # METRICS
    # ==========================================================
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
            # Normalize by dividing by mean to handle large token amounts
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

    def creator_activity_ratio(self):
        """Placeholder"""
        return 0.0

    # ==========================================================
    # SCORING
    # ==========================================================
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

    def summary(self):
        """Return analysis as dictionary"""
        coverage = round((self.transactions_fetched / self.signatures_requested * 100) if self.signatures_requested > 0 else 0, 1)

        score = self.compute_rug_score()
        if score >= 0.7:
            risk_level = "🔴 HIGH RISK"
        elif score >= 0.4:
            risk_level = "🟡 MEDIUM RISK"
        else:
            risk_level = "🟢 LOW RISK"

        return {
            "token_mint": self.token_mint,
            "token_name": self.token_name,
            "token_symbol": self.token_symbol,
            "events_parsed": len(self.events),
            "signatures_requested": self.signatures_requested,
            "transactions_fetched": self.transactions_fetched,
            "pre_migration_coverage": coverage,
            "mint_concentration": round(self.mint_concentration(), 3),
            "unique_minters_ratio": round(self.unique_minters_ratio(), 3),
            "sell_suppression_ratio": round(self.sell_suppression_ratio(), 3),
            "mint_velocity_sec": round(self.mint_velocity(), 2),
            "buy_size_variance": round(self.buy_size_variance(), 0),
            "sell_volume_concentration": round(self.sell_volume_concentration(), 3),
            "rug_probability": self.compute_rug_score(),
            "risk_level": risk_level,
            "creator_activity_ratio": round(self.creator_activity_ratio(), 3),
            "failed_fetches": self.failed_fetches,
        }
