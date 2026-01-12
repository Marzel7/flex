#!/usr/bin/env python3
"""
PumpFun Pre-Migration Analyzer - Version 1

Detects early manipulation and rug probability for pump.fun tokens
BEFORE they migrate to PumpSwap (bonding curve phase analysis).

Metrics:
- Mint concentration in top wallets
- Unique minter ratio (decentralization)
- Sell suppression (red flag for rugs)
- Mint velocity (rate of buying activity)
- Buy size entropy (variance in purchase amounts)
"""

import time
import math
import requests
import asyncio
import aiohttp
from collections import defaultdict, Counter
from statistics import variance
import sys
from typing import Optional, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env


class PumpFunPreMigrationAnalyzer:
    """
    Analyzer for pump.fun tokens BEFORE migration (bonding curve phase).
    Detects early manipulation and rug probability.
    """

    def __init__(self, token_mint: str, rpc_url="https://api.mainnet-beta.solana.com"):
        self.token_mint = token_mint
        self.rpc_url = rpc_url
        self.events = []  # {wallet, type, amount, ts}
        self.token_name = None
        self.token_symbol = None
        self.signatures_requested = 0  # Number of signatures we requested
        self.transactions_fetched = 0  # Successfully fetched transactions

    # -----------------------------
    # Fetch curve transactions
    # -----------------------------
    def fetch_curve_activity(self, limit=200):
        """Fetch and parse bonding curve buy/sell activity"""
        print(f"[PRE-MIGRATION] Fetching curve activity for {self.token_mint[:16]}... (limit={limit})", flush=True)
        sys.stdout.flush()

        # Try Helius API first (much faster), fall back to standard RPC
        sigs = self._get_signatures_helius(limit)
        if not sigs:
            print(f"[FETCH-METHOD] ⏱️  Helius unavailable/timed out, falling back to standard RPC (slower)", flush=True)
            sigs = self._get_signatures(limit)
        else:
            print(f"[FETCH-METHOD] ✅ Helius API succeeded", flush=True)
        if not sigs:
            print(f"[PRE-MIGRATION] ⚠ No signatures found", flush=True)
            return

        self.signatures_requested = len(sigs)
        print(f"[PRE-MIGRATION] Found {len(sigs)} signatures, parsing transactions...", flush=True)
        sys.stdout.flush()

        # Fetch transactions in parallel using ThreadPoolExecutor
        # Balance speed vs rate limiting on public RPC endpoints
        max_workers = 10  # Moderate: 10 concurrent requests (balance between speed and stability)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(self._get_tx, sig): sig for sig in sigs}

            for i, future in enumerate(as_completed(futures), 1):
                try:
                    tx = future.result()
                    if tx:
                        self.transactions_fetched += 1
                        self._parse_curve_tx(tx)
                except Exception:
                    pass  # RPC fetch failed, covered in coverage metric

                # Progress update every 50 transactions
                if i % 50 == 0:
                    print(f"[PRE-MIGRATION] Processed {i}/{len(sigs)} transactions...", flush=True)
                    sys.stdout.flush()

        coverage = round((self.transactions_fetched / self.signatures_requested * 100) if self.signatures_requested > 0 else 0, 1)
        print(f"[PRE-MIGRATION] {self.token_mint} | ✅ Parsed {len(self.events)} events from {self.transactions_fetched}/{self.signatures_requested} transactions ({coverage}% coverage)", flush=True)
        sys.stdout.flush()

        # Fetch token metadata in background (fast, ~100-200ms)
        self._fetch_token_metadata()

    def _fetch_token_metadata(self):
        """Fetch token name and symbol from Jupiter API (fast, non-blocking)"""
        try:
            url = f"https://api.jup.ag/tokens/v1?search={self.token_mint}"
            res = requests.get(url, timeout=5)
            if res.status_code == 200:
                data = res.json()
                if data:
                    self.token_name = data[0].get("name", None)
                    self.token_symbol = data[0].get("symbol", None)
        except Exception:
            # Silently fail - metadata is optional
            pass

    def _get_signatures_helius(self, limit):
        """Fetch ALL transaction signatures using Helius API (much faster with pagination)"""
        helius_api_key = os.getenv('HELIUS_API_KEY')
        if not helius_api_key:
            return []

        print(f"[FETCH-METHOD] 🚀 Using Helius API (fast indexed transactions)", flush=True)
        all_sigs = []
        page_token = None
        page = 0

        while True:
            page += 1
            url = f"https://api.helius.xyz/v0/addresses/{self.token_mint}/transactions"
            params = {
                "api-key": helius_api_key,
                "limit": 1000,  # Helius supports up to 1000 per request
                "type": "all"
            }
            if page_token:
                params["page-token"] = page_token

            try:
                res = requests.get(url, params=params, timeout=30).json()  # Helius can be slow
                txs = res.get("transactions", [])

                if not txs:
                    break

                # Extract signatures
                sigs = [tx.get("signature") for tx in txs if tx.get("signature")]
                all_sigs.extend(sigs)

                print(f"[PRE-MIGRATION] Helius: Fetched {len(all_sigs)} total signatures (page {page})...", flush=True)

                # Check for next page
                page_token = res.get("page-token")
                if not page_token:
                    break

                # Respect the limit parameter
                if len(all_sigs) >= limit:
                    all_sigs = all_sigs[:limit]
                    break

            except Exception as e:
                print(f"[PRE-MIGRATION] ❌ Helius API failed (page {page}): {e}", flush=True)
                break

        return all_sigs

    def _get_signatures(self, limit):
        """Fetch ALL transaction signatures for token using pagination"""
        print(f"[FETCH-METHOD] 📡 Using Standard RPC with pagination", flush=True)
        all_sigs = []
        before = None
        page = 0

        while True:
            page += 1
            params = {"limit": min(limit, 1000)}  # Max 1000 per RPC call
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

                sig_list = [x["signature"] for x in sigs]
                all_sigs.extend(sig_list)

                print(f"[PRE-MIGRATION] Fetched {len(all_sigs)} total signatures (page {page})...", flush=True)

                # Stop if we got fewer than requested (reached end)
                if len(sig_list) < 1000:
                    break

                # Set 'before' to last signature for next page
                before = sig_list[-1]

                # Respect the limit parameter (total max signatures to fetch)
                if len(all_sigs) >= limit:
                    all_sigs = all_sigs[:limit]
                    break

            except Exception as e:
                print(f"[PRE-MIGRATION] ❌ Failed to fetch signatures (page {page}): {e}", flush=True)
                break

        return all_sigs

    def _get_tx(self, sig, retry=0, max_retries=2):
        """Fetch full transaction data with retry logic"""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
        }
        try:
            res = requests.post(self.rpc_url, json=payload, timeout=15).json()
            result = res.get("result")
            if result is None:
                # RPC returned error or null
                error = res.get("error", {})
                if error and retry < max_retries:
                    wait_time = 0.5 * (2 ** retry)
                    time.sleep(wait_time)
                    return self._get_tx(sig, retry=retry + 1, max_retries=max_retries)
            return result
        except Exception as e:
            # Retry up to max_retries times with exponential backoff
            if retry < max_retries:
                wait_time = 0.5 * (2 ** retry)  # 0.5s, 1s, 2s
                time.sleep(wait_time)
                return self._get_tx(sig, retry=retry + 1, max_retries=max_retries)
            return None

    def _parse_curve_tx(self, tx):
        """Parse bonding curve buy/sell transactions"""
        try:
            meta = tx.get("meta")
            if not meta:
                return

            ts = tx.get("blockTime", int(time.time()))
            pre = meta.get("preTokenBalances", [])
            post = meta.get("postTokenBalances", [])

            for a, b in zip(pre, post):
                if a.get("mint") != self.token_mint:
                    continue

                pre_amount = int(a.get("uiTokenAmount", {}).get("amount", 0))
                post_amount = int(b.get("uiTokenAmount", {}).get("amount", 0))
                delta = post_amount - pre_amount

                if delta == 0:
                    continue

                wallet = b.get("owner")
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

    # -----------------------------
    # Metrics for Rug Detection
    # -----------------------------
    def mint_concentration(self):
        """Concentration of buys in top 5 wallets (high = red flag)"""
        buys = [e for e in self.events if e["type"] == "buy"]
        if not buys:
            return 0.0

        wallet_amounts = Counter(e["wallet"] for e in buys)
        total = sum(wallet_amounts.values())
        top5_sum = sum(v for _, v in wallet_amounts.most_common(5))
        return top5_sum / total if total else 0.0

    def unique_minters_ratio(self):
        """Ratio of unique wallets to total buy events (low = red flag)"""
        buys = [e for e in self.events if e["type"] == "buy"]
        if not buys:
            return 0.0

        unique = len({e["wallet"] for e in buys})
        return unique / len(buys)

    def sell_suppression_ratio(self):
        """Ratio of sells to total events (low = red flag, indicates no exit)"""
        if not self.events:
            return 0.0

        sells = sum(1 for e in self.events if e["type"] == "sell")
        return sells / len(self.events)

    def mint_velocity(self):
        """Average time between buy events in seconds"""
        buys = [e for e in self.events if e["type"] == "buy"]
        if len(buys) < 2:
            return 0.0

        timestamps = sorted(e["ts"] for e in buys)
        intervals = [timestamps[i+1] - timestamps[i] for i in range(len(timestamps)-1)]
        return sum(intervals) / len(intervals) if intervals else 0.0

    def buy_size_variance(self):
        """Variance in buy amounts (low = artificial, suspicious)"""
        buys = [e for e in self.events if e["type"] == "buy"]
        if len(buys) < 3:
            return 0.0

        amounts = [e["amount"] for e in buys]
        try:
            return variance(amounts)
        except Exception:
            return 0.0

    def sell_volume_concentration(self):
        """Concentration of sell volume in top 3 sellers"""
        sells = [e for e in self.events if e["type"] == "sell"]
        if not sells:
            return 0.0

        wallet_volumes = defaultdict(int)
        for e in sells:
            wallet_volumes[e["wallet"]] += e["amount"]

        total = sum(wallet_volumes.values())
        top3_sum = sum(v for _, v in sorted(wallet_volumes.items(), key=lambda x: x[1], reverse=True)[:3])
        return top3_sum / total if total else 0.0

    # -----------------------------
    # Rug Probability Score
    # -----------------------------
    def compute_rug_score(self):
        """
        Compute rug probability (0.0 = safe, 1.0 = likely rug)

        Factors:
        - High mint concentration (>70%) = +0.25
        - Low unique minters (<15% ratio) = +0.20
        - High sell suppression (<5% sells) = +0.20
        - Fast mint velocity (<5 sec avg) = +0.15
        - Low buy variance (<1M units) = +0.15
        - High sell concentration (>50% in top 3) = +0.05
        """
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

        variance = self.buy_size_variance()
        if variance < 1e6:
            score += 0.15
        elif variance < 1e7:
            score += 0.08

        sell_conc = self.sell_volume_concentration()
        if sell_conc > 0.5:
            score += 0.05

        return round(min(score, 1.0), 3)

    def risk_level(self):
        """Convert rug score to risk assessment"""
        score = self.compute_rug_score()
        if score < 0.3:
            return "🟢 LOW RISK"
        elif score < 0.6:
            return "🟡 MEDIUM RISK"
        else:
            return "🔴 HIGH RISK (LIKELY RUG)"

    # =====================================
    # AMM Gap Window Rug Risk Scoring
    # =====================================
    def creator_activity_ratio(self) -> float:
        """
        Ratio of creator wallet transactions to total transactions.
        High = prepping exit.
        """
        if not self.events:
            return 0.0

        # Creator would be one of the top minters (typically first or among top)
        minters = Counter(e["wallet"] for e in self.events if e["type"] == "buy")
        if not minters:
            return 0.0

        # Top minter assumed to be creator
        creator_wallet = minters.most_common(1)[0][0]
        creator_txs = sum(1 for e in self.events if e["wallet"] == creator_wallet)
        return creator_txs / len(self.events)

    def compute_amm_rug_score(self, liquidity_locked: bool = False) -> float:
        """
        Compute AMM gap window rug probability.

        Factors:
        - 0.30 * Wallet Concentration (top 5)
        - 0.25 * Exit Asymmetry (sell concentration)
        - 0.20 * Wash Trading Ratio
        - 0.15 * Creator Activity
        - 0.10 * Liquidity Absence

        Args:
            liquidity_locked: If True, reduces liquidity absence component

        Returns:
            Score 0.0-1.0 where 1.0 = certain rug
        """
        score = 0.0

        # Wallet concentration: 0.30 weight
        mint_conc = self.mint_concentration()
        score += 0.30 * min(mint_conc / 0.7, 1.0)

        # Exit asymmetry: 0.25 weight
        exit_asymmetry = self.sell_volume_concentration()
        score += 0.25 * min(exit_asymmetry / 0.5, 1.0)

        # Wash trading: 0.20 weight
        wash_ratio = self._compute_wash_trading_ratio()
        score += 0.20 * wash_ratio

        # Creator activity: 0.15 weight
        creator_activity = self.creator_activity_ratio()
        score += 0.15 * min(creator_activity / 0.5, 1.0)

        # Liquidity absence: 0.10 weight (before AMM exists)
        liquidity_component = 0.0 if liquidity_locked else 0.10
        score += liquidity_component

        return round(min(score, 1.0), 3)

    def _compute_wash_trading_ratio(self) -> float:
        """Compute wash trading ratio (repeated buy/sell cycles)"""
        if not self.events:
            return 0.0

        wallet_sequences = defaultdict(list)
        for e in self.events:
            wallet_sequences[e["wallet"]].append(e["type"])

        # Count repeated buy/sell cycles per wallet
        total_cycles = 0
        for wallet, sequence in wallet_sequences.items():
            # Count alternating buy/sell pairs
            for i in range(len(sequence) - 1):
                if sequence[i] != sequence[i + 1]:
                    total_cycles += 1

        return min(total_cycles / len(self.events) if self.events else 0.0, 1.0)

    def amm_risk_level(self, amm_score: Optional[float] = None) -> str:
        """Convert AMM rug score to risk assessment"""
        if amm_score is None:
            amm_score = self.compute_amm_rug_score()

        if amm_score < 0.3:
            return "🟢 LOW RISK"
        elif amm_score < 0.6:
            return "🟡 MEDIUM RISK"
        elif amm_score < 0.85:
            return "🔴 HIGH RISK"
        else:
            return "☠️ ALMOST CERTAIN RUG"

    def print_summary(self):
        """Print detailed analysis report"""
        print("\n" + "=" * 70)
        print("PUMP.FUN PRE-MIGRATION ANALYSIS (BONDING CURVE PHASE)")
        print("=" * 70)
        print(f"Token Mint:                 {self.token_mint}")
        print(f"Total Events:               {len(self.events)}")
        print(f"\nMetrics:")
        print(f"  Mint Concentration:       {self.mint_concentration():.1%} (top 5 wallets)")
        print(f"  Unique Minters Ratio:     {self.unique_minters_ratio():.1%}")
        print(f"  Sell Suppression Ratio:   {self.sell_suppression_ratio():.1%}")
        print(f"  Mint Velocity (avg):      {self.mint_velocity():.2f} sec between buys")
        print(f"  Buy Size Variance:        {self.buy_size_variance():.0f}")
        print(f"  Sell Volume Concentration:{self.sell_volume_concentration():.1%} (top 3 sellers)")
        print(f"\nRug Probability Score:      {self.compute_rug_score()}")
        print(f"Risk Assessment:            {self.risk_level()}")
        print(f"\nAMM Gap Window Risk:        {self.compute_amm_rug_score()}")
        print(f"AMM Risk Assessment:        {self.amm_risk_level()}")
        print("=" * 70 + "\n")

    def summary(self):
        """Return analysis as dictionary"""
        coverage = round((self.transactions_fetched / self.signatures_requested * 100) if self.signatures_requested > 0 else 0, 1)
        return {
            "token_mint": self.token_mint,
            "token_name": self.token_name,
            "token_symbol": self.token_symbol,
            "events_parsed": len(self.events),
            "mint_concentration": round(self.mint_concentration(), 3),
            "unique_minters_ratio": round(self.unique_minters_ratio(), 3),
            "sell_suppression_ratio": round(self.sell_suppression_ratio(), 3),
            "mint_velocity_sec": round(self.mint_velocity(), 2),
            "buy_size_variance": round(self.buy_size_variance(), 0),
            "sell_volume_concentration": round(self.sell_volume_concentration(), 3),
            "rug_probability": self.compute_rug_score(),
            "risk_level": self.risk_level(),
            "creator_activity_ratio": round(self.creator_activity_ratio(), 3),
            "amm_rug_probability": self.compute_amm_rug_score(),
            "amm_risk_level": self.amm_risk_level(),
            "pre_migration_coverage": coverage
        }
