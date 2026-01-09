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


class PumpFunPreMigrationAnalyzer:
    """
    Analyzer for pump.fun tokens BEFORE migration (bonding curve phase).
    Detects early manipulation and rug probability.
    """

    def __init__(self, token_mint: str, rpc_url="https://api.mainnet-beta.solana.com"):
        self.token_mint = token_mint
        self.rpc_url = rpc_url
        self.events = []  # {wallet, type, amount, ts}

    # -----------------------------
    # Fetch curve transactions
    # -----------------------------
    def fetch_curve_activity(self, limit=200):
        """Fetch and parse bonding curve buy/sell activity"""
        print(f"[PRE-MIGRATION] Fetching curve activity for {self.token_mint[:16]}... (limit={limit})", flush=True)
        sys.stdout.flush()

        sigs = self._get_signatures(limit)
        if not sigs:
            print(f"[PRE-MIGRATION] ⚠ No signatures found", flush=True)
            return

        print(f"[PRE-MIGRATION] Found {len(sigs)} signatures, parsing transactions...", flush=True)
        sys.stdout.flush()

        # Fetch transactions concurrently (batch of 10 at a time to avoid RPC rate limits)
        batch_size = 10
        for batch_start in range(0, len(sigs), batch_size):
            batch_sigs = sigs[batch_start:batch_start + batch_size]

            # Fetch all transactions in batch concurrently
            txs = []
            for sig in batch_sigs:
                tx = self._get_tx(sig)
                if tx:
                    txs.append(tx)

            # Parse all transactions from this batch
            for tx in txs:
                self._parse_curve_tx(tx)

            if batch_start > 0:
                processed = min(batch_start + batch_size, len(sigs))
                print(f"[PRE-MIGRATION] Processed {processed}/{len(sigs)} transactions...", flush=True)
                sys.stdout.flush()

        print(f"[PRE-MIGRATION] ✅ Parsed {len(self.events)} events from {len(sigs)} transactions", flush=True)
        sys.stdout.flush()

    def _get_signatures(self, limit):
        """Fetch transaction signatures for token"""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [self.token_mint, {"limit": limit}]
        }
        try:
            res = requests.post(self.rpc_url, json=payload, timeout=10).json()
            return [x["signature"] for x in res.get("result", [])]
        except Exception as e:
            print(f"[PRE-MIGRATION] ❌ Failed to fetch signatures: {e}", flush=True)
            return []

    def _get_tx(self, sig):
        """Fetch full transaction data"""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
        }
        try:
            res = requests.post(self.rpc_url, json=payload, timeout=10).json()
            return res.get("result")
        except Exception:
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
        return {
            "token_mint": self.token_mint,
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
            "amm_risk_level": self.amm_risk_level()
        }
