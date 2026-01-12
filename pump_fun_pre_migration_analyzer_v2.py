#!/usr/bin/env python3
"""
Pump.fun Pre-Migration Analyzer V2 (ASYNC STREAMING)

- Mint-anchored queries
- Async aiohttp fetching
- Global concurrency limiter (NO RPC hammering)
- Batched streaming parser
"""

import asyncio
import aiohttp
import time
import os
import requests
from collections import defaultdict, Counter
from statistics import variance
from typing import List
from dotenv import load_dotenv

load_dotenv()

# =============================
# CONFIG
# =============================
BATCH_SIZE = 100
MAX_SIGNATURES = 40000
RPC_TIMEOUT = 30

MAX_RETRIES = 5
RETRY_DELAYS = [0.5, 1.0, 2.0, 3.0, 5.0]

MAX_CONCURRENT_RPC = 15  # 🔒 HARD SAFETY CAP


class PumpFunPreMigrationAnalyzerV2:

    def __init__(self, token_mint: str, rpc_url="https://api.mainnet-beta.solana.com"):
        self.token_mint = token_mint
        self.rpc_url = rpc_url

        self.events = []
        self.transactions_fetched = 0
        self.signatures_requested = 0

        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT_RPC)

    # ==========================================================
    # SIGNATURE FETCHING
    # ==========================================================
    def fetch_signatures(self, limit=40000) -> List[str]:
        sigs = self._get_signatures_helius(limit)
        if not sigs:
            sigs = self._get_signatures_rpc(limit)

        self.signatures_requested = len(sigs)
        return sigs

    def _get_signatures_helius(self, limit) -> List[str]:
        helius_api_key = os.getenv("HELIUS_API_KEY")
        if not helius_api_key:
            return []

        all_sigs = []
        page_token = None

        while len(all_sigs) < limit:
            url = f"https://api.helius.xyz/v0/addresses/{self.token_mint}/transactions"
            params = {
                "api-key": helius_api_key,
                "limit": 1000,
                "type": "all",
            }
            if page_token:
                params["page-token"] = page_token

            try:
                res = requests.get(url, params=params, timeout=30).json()
                txs = res.get("transactions", [])
                if not txs:
                    break

                sigs = [tx["signature"] for tx in txs if tx.get("signature")]
                all_sigs.extend(sigs)

                page_token = res.get("page-token")
                if not page_token:
                    break

            except Exception:
                break

        return all_sigs[:limit]

    def _get_signatures_rpc(self, limit) -> List[str]:
        all_sigs = []
        before = None

        while len(all_sigs) < limit:
            params = {"limit": min(1000, limit - len(all_sigs))}
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

                valid = [x["signature"] for x in sigs if x.get("err") is None]
                all_sigs.extend(valid)

                if len(valid) < 1000:
                    break

                before = valid[-1]

            except Exception:
                break

        return all_sigs[:limit]

    # ==========================================================
    # ASYNC TRANSACTION FETCHING (RATE LIMITED)
    # ==========================================================
    async def fetch_transactions_async(self, signatures: List[str]):

        async def fetch_tx(session, sig, retry=0):
            async with self.semaphore:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransaction",
                    "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
                }

                try:
                    async with session.post(self.rpc_url, json=payload, timeout=RPC_TIMEOUT) as resp:
                        if resp.status == 429:
                            raise aiohttp.ClientResponseError(
                                resp.request_info, resp.history, status=429
                            )

                        data = await resp.json()
                        return data.get("result")

                except Exception:
                    if retry < MAX_RETRIES:
                        await asyncio.sleep(RETRY_DELAYS[retry])
                        return await fetch_tx(session, sig, retry + 1)
                    return None

        async with aiohttp.ClientSession() as session:
            for i in range(0, len(signatures), BATCH_SIZE):
                batch = signatures[i:i + BATCH_SIZE]
                tasks = [fetch_tx(session, sig) for sig in batch]
                results = await asyncio.gather(*tasks)

                for tx in results:
                    if tx and tx.get("meta"):
                        self.transactions_fetched += 1
                        self._parse_tx(tx)

                print(
                    f"[ASYNC] {min(i+BATCH_SIZE,len(signatures))}/{len(signatures)} "
                    f"| txs {self.transactions_fetched}",
                    flush=True
                )

    async def fetch_curve_activity_async(self):
        sigs = self.fetch_signatures(MAX_SIGNATURES)
        if not sigs:
            return
        await self.fetch_transactions_async(sigs)

    def fetch_curve_activity(self):
        asyncio.run(self.fetch_curve_activity_async())

    # ==========================================================
    # TX PARSER
    # ==========================================================
    def _parse_tx(self, tx):
        meta = tx.get("meta")
        if not meta:
            return

        ts = tx.get("blockTime", int(time.time()))
        pre = meta.get("preTokenBalances", [])
        post = meta.get("postTokenBalances", [])

        for a, b in zip(pre, post):
            if a.get("mint") != self.token_mint:
                continue

            delta = int(b["uiTokenAmount"]["amount"]) - int(a["uiTokenAmount"]["amount"])
            if delta == 0:
                continue

            self.events.append({
                "wallet": b.get("owner"),
                "type": "buy" if delta > 0 else "sell",
                "amount": abs(delta),
                "ts": ts
            })

    # ==========================================================
    # METRICS
    # ==========================================================
    def mint_concentration(self):
        buys = [e for e in self.events if e["type"] == "buy"]
        if not buys:
            return 0.0
        c = Counter(e["wallet"] for e in buys)
        top5 = sum(v for _, v in c.most_common(5))
        return top5 / len(buys)

    def unique_minters_ratio(self):
        buys = [e for e in self.events if e["type"] == "buy"]
        return len(set(e["wallet"] for e in buys)) / len(buys) if buys else 0.0

    def mint_velocity(self):
        buys = sorted(e["ts"] for e in self.events if e["type"] == "buy")
        if len(buys) < 2:
            return 0.0
        intervals = [buys[i+1] - buys[i] for i in range(len(buys)-1)]
        return sum(intervals) / len(intervals)

    def buy_size_variance(self):
        buys = [e["amount"] for e in self.events if e["type"] == "buy"]
        if len(buys) < 3:
            return 0.0
        mean = sum(buys) / len(buys)
        return variance([b / mean for b in buys])

    def sell_volume_concentration(self):
        sells = defaultdict(int)
        for e in self.events:
            if e["type"] == "sell":
                sells[e["wallet"]] += e["amount"]
        if not sells:
            return 0.0
        total = sum(sells.values())
        top3 = sum(v for _, v in sorted(sells.items(), key=lambda x: x[1], reverse=True)[:3])
        return top3 / total
