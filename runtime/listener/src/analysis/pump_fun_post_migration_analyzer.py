#!/usr/bin/env python3
"""
Post-Migration Token Analyzer (PumpSwap)

Analyzes tokens AFTER migration to PumpSwap.
Fetches transaction history and calculates risk metrics.

CREATOR / CREATE-TX FIXES INCLUDED:

✅ Fix 1: Normalize Helius /v0/transactions schema vs Solana RPC getTransaction schema
✅ Fix 2: Harden inner-instruction expansion across provider schema variants
✅ Fix 3: Normalize Helius accountKeys objects to pubkey strings
✅ Fix 4: Create validation is now STRICT:
        - Must create the bonding curve PDA (System.createAccount owner == Pump.fun bonding curve program)
        - AND must create/initialize the *mint* in the same transaction:
             (a) System.createAccount creates mint owned by Token Program / Token-2022
             OR (b) Token initializeMint / initializeMint2 for mint
        - Pumpfun program id presence is "nice-to-have" and no longer required
✅ Fix 5: Parsed-info account extraction is generic: collects pubkey-like strings from parsed.info

✅ NEW: FAST CREATE SEARCH (Helius Enhanced Transactions)
        - Scans parsed txs for the mint via:
          GET https://api.helius.xyz/v0/addresses/<address>/transactions
        - This avoids 1000x getTransaction calls per page.
"""

import asyncio
import aiohttp
import requests
import time
from typing import Dict, List, Optional, Tuple, Any, AsyncGenerator
from collections import Counter, defaultdict
from statistics import variance
import os
from dotenv import load_dotenv
import base58
import struct

load_dotenv(os.path.join(os.path.dirname(__file__), '../../config/.env'))

# Import RPC metrics recorder for monitoring
try:
    from src.metrics.rpc_metrics_recorder import record_request, initialize_recorder
    initialize_recorder(plan_monthly_credits=50_000_000)
except ImportError:
    def record_request(*args, **kwargs):
        pass  # No-op if metrics recorder not available

# -----------------------------
# Config (tune to your RPC tier)
# -----------------------------
BATCH_SIZE = 15
MAX_SIGNATURES = 1_000_000
RPC_TIMEOUT = 60
MAX_RETRIES = 10
RETRY_DELAYS = [0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 15.0, 20.0, 30.0, 60.0]
BATCH_DELAY = 0.2

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
HELIUS_MONITORING_API_KEY = os.getenv("HELIUS_MONITORING_API_KEY", "")

# Use monitoring key if available, fall back to regular key
_RPC_KEY = HELIUS_MONITORING_API_KEY or HELIUS_API_KEY

RPC_URLS: List[str] = []
if _RPC_KEY:
    RPC_URLS.append(f"https://mainnet.helius-rpc.com/?api-key={_RPC_KEY}")
RPC_URLS.append("https://api.mainnet-beta.solana.com")

HISTORY_RPC_URLS: List[str] = []
if _RPC_KEY:
    HISTORY_RPC_URLS.append(f"https://mainnet.helius-rpc.com/?api-key={_RPC_KEY}")
HISTORY_RPC_URLS.append("https://api.mainnet-beta.solana.com")


# -----------------------------
# Helper: cache-limited detection
# -----------------------------
def _looks_cache_limited(sigs: List[Dict]) -> bool:
    if not sigs:
        return False
    if len(sigs) >= 1000:
        return False
    slots = [s.get("slot") for s in sigs if isinstance(s.get("slot"), int)]
    if not slots:
        return False
    slot_span = max(slots) - min(slots)
    null_bt = sum(1 for s in sigs if s.get("blockTime") is None)
    if slot_span < 2000 and (null_bt / max(1, len(sigs))) > 0.7:
        return True
    return False


async def _rpc_post(session: aiohttp.ClientSession, url: str, payload: dict, timeout_s: int = 30) -> Optional[dict]:
    retry_delay = 2.0
    max_retries = 5
    for attempt in range(max_retries):
        try:
            start_time = time.time()
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=timeout_s)) as resp:
                latency_ms = (time.time() - start_time) * 1000
                rpc_method = payload.get("method", "unknown")

                if resp.status == 429:
                    record_request(
                        section="ui_api",
                        provider="helius_rpc" if "helius" in url else "solana_rpc",
                        method=rpc_method,
                        status_code=429,
                        latency_ms=latency_ms,
                        mode="background",
                        retries=attempt,
                    )
                    if attempt < max_retries - 1:
                        print(f"⚠️  RPC 429, retrying in {retry_delay}s... ({attempt+1}/{max_retries})", flush=True)
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 2
                        continue
                    print(f"❌ RPC 429 after {max_retries} retries", flush=True)
                    return None

                if resp.status == 200:
                    data = await resp.json()
                    record_request(
                        section="ui_api",
                        provider="helius_rpc" if "helius" in url else "solana_rpc",
                        method=rpc_method,
                        status_code=200,
                        latency_ms=latency_ms,
                        mode="background",
                        retries=attempt,
                    )
                    # allow caller to inspect RPC errors
                    return data

                record_request(
                    section="ui_api",
                    provider="helius_rpc" if "helius" in url else "solana_rpc",
                    method=rpc_method,
                    status_code=resp.status,
                    latency_ms=latency_ms,
                    mode="background",
                    retries=attempt,
                )
                return None
        except asyncio.TimeoutError:
            record_request(
                section="ui_api",
                provider="helius_rpc" if "helius" in url else "solana_rpc",
                method=payload.get("method", "unknown"),
                status_code=0,
                latency_ms=(time.time() - start_time) * 1000,
                mode="background",
                retries=attempt,
                source_file="pump_fun_post_migration_analyzer",

                error="timeout",
            )
            if attempt < max_retries - 1:
                print(f"⚠️  RPC timeout, retrying in {retry_delay}s... ({attempt+1}/{max_retries})", flush=True)
                await asyncio.sleep(retry_delay)
                retry_delay *= 2
                continue
            print(f"❌ RPC timeout after {max_retries} retries", flush=True)
            return None
        except Exception as e:
            record_request(
                section="ui_api",
                provider="helius_rpc" if "helius" in url else "solana_rpc",
                method=payload.get("method", "unknown"),
                status_code=0,
                latency_ms=(time.time() - start_time) * 1000,
                mode="background",
                retries=attempt,
                source_file="pump_fun_post_migration_analyzer",

                error=str(e),
            )
            return None
    return None


# -----------------------------
# Program IDs / constants
# -----------------------------
PUMPFUN_AMM_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
PUMPFUN_BONDING_CURVE_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

PUMPFUN_PROGRAM_IDS = {
    PUMPFUN_AMM_PROGRAM,
    PUMPFUN_BONDING_CURVE_PROGRAM,
}

SYSTEM_PROGRAM = "11111111111111111111111111111111"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJsyFbPtrKbVs73Cw6Xj2Yg5MNg"
TOKEN_2022 = "TokenzQdBbjFD8aff5ZZUwWWwG6Go5rm5KWQEypdCU8"
TOKEN_METADATA_PROGRAM = "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s"  # Metaplex Metadata

SYSTEM_PROGRAMS = {SYSTEM_PROGRAM, TOKEN_PROGRAM, TOKEN_2022}


# -----------------------------
# Main Analyzer
# -----------------------------
class PostMigrationAnalyzer:
    """Analyzes token activity on PumpSwap (post-migration). Includes robust creator extraction."""

    def __init__(self, token_mint: str, rpc_url: str = "https://api.mainnet-beta.solana.com"):
        self.token_mint = token_mint
        self.rpc_url = rpc_url

        self.events: List[dict] = []
        self.transactions_fetched = 0
        self.signatures_requested = 0

        self.token_name = None
        self.token_symbol = None
        self.market_cap_current = None
        self.market_cap_highest = None

        # CREATE provenance
        self._create_tx_validation: Optional[dict] = None
        self._create_tx_signature: Optional[str] = None
        self._create_tx_creator: Optional[str] = None

        # Fallback creator inference from earliest scanned candidate
        self._fallback_creator_sig: Optional[str] = None
        self._fallback_creator_tx: Optional[dict] = None
        self._oldest_scanned_sig: Optional[str] = None
        self._oldest_scanned_tx: Optional[dict] = None

        print(f"[ANALYZER_INIT] Token: {token_mint}", flush=True)
        print(f"[ANALYZER_INIT] RPC: {rpc_url[:80]}{'...' if len(rpc_url) > 80 else ''}", flush=True)

    # -----------------------------
    # NEW: Helius bulk parsed tx iterator (FAST)
    # -----------------------------
    async def helius_iter_parsed_txs_for_address(
        self,
        address: str,
        limit: int = 100,
        max_pages: int = 200,
        oldest_first: bool = True,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Iterate parsed txs for an address using Helius Enhanced Transactions API:

          GET https://api.helius.xyz/v0/addresses/<ADDRESS>/transactions?api-key=...

        Returns newest->oldest by default; we reverse for oldest_first.
        """
        if not HELIUS_API_KEY:
            return

        base = f"https://api.helius.xyz/v0/addresses/{address}/transactions?api-key={HELIUS_API_KEY}"
        before = None
        timeout = aiohttp.ClientTimeout(total=20)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            for page in range(1, max_pages + 1):
                url = f"{base}&limit={limit}"
                if before:
                    url += f"&before={before}"

                try:
                    async with session.get(url) as resp:
                        if resp.status != 200:
                            print(f"[HELIUS_ADDR_TX] ⚠ status={resp.status} page={page}", flush=True)
                            return
                        txs = await resp.json()

                    if not isinstance(txs, list) or not txs:
                        # natural end
                        print(f"[HELIUS_ADDR_TX] ✅ End of history page={page}", flush=True)
                        return

                    if oldest_first:
                        txs = list(reversed(txs))

                    for tx in txs:
                        yield tx

                    # Cursor for next page must be the OLDEST signature in the *original* (newest->oldest) stream.
                    # With oldest_first=True, we reversed, so txs[0] is oldest in this batch.
                    cursor_tx = txs[0] if oldest_first else txs[-1]
                    before = cursor_tx.get("signature")
                    if not before:
                        # try find any signature
                        for t in reversed(txs):
                            if t.get("signature"):
                                before = t["signature"]
                                break
                    if not before:
                        print("[HELIUS_ADDR_TX] ⚠ Missing signature cursor; stopping", flush=True)
                        return

                except Exception as e:
                    print(f"[HELIUS_ADDR_TX] ⚠ error page={page}: {e}", flush=True)
                    return

    # -----------------------------
    # RPC helpers
    # -----------------------------
    async def _post_rpc_with_fallback(self, payload: dict, timeout: int = 10) -> Optional[dict]:
        try:
            async with aiohttp.ClientSession() as session:
                for i, rpc_url in enumerate(RPC_URLS):
                    try:
                        async with session.post(
                            rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=timeout)
                        ) as resp:
                            if resp.status == 200:
                                return await resp.json()
                            if resp.status == 429 and i < len(RPC_URLS) - 1:
                                continue
                            if i < len(RPC_URLS) - 1:
                                continue
                    except asyncio.TimeoutError:
                        if i < len(RPC_URLS) - 1:
                            continue
                    except Exception:
                        if i < len(RPC_URLS) - 1:
                            continue
                return None
        except Exception as e:
            print(f"[RPC_ERROR] {e}", flush=True)
            return None

    async def fetch_signatures(self, limit=MAX_SIGNATURES) -> List[str]:
        all_sigs: List[str] = []
        before = None
        pages_fetched = 0

        while len(all_sigs) < limit:
            params = {"limit": min(limit - len(all_sigs), 1000)}
            if before:
                params["before"] = before

            payload = {"jsonrpc": "2.0", "id": 1, "method": "getSignaturesForAddress", "params": [self.token_mint, params]}

            try:
                res = await self._post_rpc_with_fallback(payload, timeout=10)
                if res is None:
                    print(f"[SIG_FETCH] ✗ All RPC endpoints failed after {pages_fetched} pages", flush=True)
                    break

                sigs = res.get("result", [])
                if not sigs:
                    print(f"[SIG_FETCH] ✓ Reached end of transaction history after {pages_fetched} pages", flush=True)
                    break

                sig_list = [x["signature"] for x in sigs if x.get("err") is None and x.get("signature")]
                all_sigs.extend(sig_list)
                pages_fetched += 1
                print(f"[SIG_FETCH] Page {pages_fetched}: +{len(sig_list)} (total: {len(all_sigs)})", flush=True)

                if len(sig_list) < 1000:
                    print(f"[SIG_FETCH] ✓ Final page retrieved (<1000)", flush=True)
                    break

                before = sig_list[-1]
            except Exception as e:
                print(f"[SIG_FETCH] ⚠ RPC error: {type(e).__name__}: {str(e)}", flush=True)
                break

        print(f"[SIG_FETCH] ✅ Total signatures fetched: {len(all_sigs)}", flush=True)
        return all_sigs[:limit]

    # -----------------------------
    # Async transaction fetching
    # -----------------------------
    async def fetch_transactions_async(self, sigs: List[str]):
        sem = asyncio.Semaphore(BATCH_SIZE)

        async with aiohttp.ClientSession() as session:
            successful = 0
            failed = 0
            total_processed = 0

            chunk_size = 5000
            for chunk_start in range(0, len(sigs), chunk_size):
                chunk_end = min(chunk_start + chunk_size, len(sigs))
                chunk = sigs[chunk_start:chunk_end]

                tasks = [self._fetch_tx_semaphore(session, sig, sem) for sig in chunk]

                for idx, future in enumerate(asyncio.as_completed(tasks), 1):
                    try:
                        tx = await future
                        if tx:
                            self._parse_curve_tx(tx)
                            self.transactions_fetched += 1
                            successful += 1
                        else:
                            failed += 1
                    except Exception:
                        failed += 1

                    total_processed += 1
                    if idx % BATCH_SIZE == 0 or idx == len(chunk):
                        sr = (successful / total_processed * 100) if total_processed else 0
                        print(
                            f"[ASYNC] {total_processed}/{len(sigs)} | ok={successful} ({sr:.1f}%) | fail={failed}",
                            flush=True,
                        )

                if chunk_end < len(sigs):
                    await asyncio.sleep(BATCH_DELAY)

    async def _fetch_tx_semaphore(self, session: aiohttp.ClientSession, sig: str, sem: asyncio.Semaphore):
        async with sem:
            return await self.fetch_tx_with_retry(session, sig)

    async def fetch_tx_with_retry(self, session: aiohttp.ClientSession, sig: str):
        for attempt in range(MAX_RETRIES):
            for rpc_url in RPC_URLS:
                try:
                    payload = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getTransaction",
                        "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
                    }

                    async with session.post(rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=RPC_TIMEOUT)) as resp:
                        if resp.status != 200:
                            if resp.status in (429,) or resp.status >= 500:
                                continue
                            return None

                        data = await resp.json()
                        if "error" in data:
                            code = (data["error"] or {}).get("code", -1)
                            retryable = code in {-32008, -32000, -32003, -32009}
                            if retryable:
                                continue
                            return None

                        result = data.get("result")
                        if result:
                            return result
                        continue
                except asyncio.TimeoutError:
                    continue
                except Exception:
                    continue

            if attempt < MAX_RETRIES - 1:
                if (attempt + 1) % 5 == 0:
                    print(f"[FETCH_TX] Retrying {sig[:12]}... ({attempt+1}/{MAX_RETRIES})", flush=True)
                delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                await asyncio.sleep(delay)

        return None

    # -----------------------------
    # Event parsing
    # -----------------------------
    def _parse_curve_tx(self, tx: dict):
        try:
            meta = tx.get("meta")
            if not meta:
                return

            ts = tx.get("blockTime", int(time.time()))
            pre_balances = meta.get("preTokenBalances", []) or []
            post_balances = meta.get("postTokenBalances", []) or []

            pre_by_key = {}
            for pre in pre_balances:
                if pre.get("mint") != self.token_mint:
                    continue
                key = (pre.get("accountIndex"), pre.get("mint"), pre.get("owner"))
                pre_by_key[key] = int((pre.get("uiTokenAmount") or {}).get("amount", 0) or 0)

            post_by_key = {}
            for post in post_balances:
                if post.get("mint") != self.token_mint:
                    continue
                key = (post.get("accountIndex"), post.get("mint"), post.get("owner"))
                post_by_key[key] = int((post.get("uiTokenAmount") or {}).get("amount", 0) or 0)

            all_keys = set(pre_by_key.keys()) | set(post_by_key.keys())
            for key in all_keys:
                _, _, wallet = key
                pre_amount = pre_by_key.get(key, 0)
                post_amount = post_by_key.get(key, 0)
                delta = post_amount - pre_amount
                if delta == 0 or not wallet:
                    continue

                self.events.append(
                    {
                        "wallet": wallet,
                        "type": "buy" if delta > 0 else "sell",
                        "amount": abs(delta),
                        "ts": ts,
                    }
                )
        except Exception:
            pass

    async def fetch_curve_activity_async(self):
        print(f"[STREAM] Starting post-migration analysis for {self.token_mint}", flush=True)

        sigs = await self.fetch_signatures(limit=MAX_SIGNATURES)
        print(f"[STREAM] Fetched {len(sigs)} signatures, starting async fetch...", flush=True)

        if not sigs:
            print(f"[STREAM] ⚠ No signatures found", flush=True)
            return

        self.signatures_requested = len(sigs)
        await self.fetch_transactions_async(sigs)

        print(f"[STREAM] ✅ Done: {len(self.events)} events from {self.transactions_fetched} txs", flush=True)

    # -----------------------------
    # Risk metrics
    # -----------------------------
    def mint_concentration(self) -> float:
        buys = [e for e in self.events if e["type"] == "buy"]
        if not buys:
            return 0.0
        wallet_counts = Counter(e["wallet"] for e in buys)
        total = sum(wallet_counts.values())
        top5_sum = sum(v for _, v in wallet_counts.most_common(5))
        return top5_sum / total if total else 0.0

    def unique_minters_ratio(self) -> float:
        buys = [e for e in self.events if e["type"] == "buy"]
        if not buys:
            return 0.0
        unique = len(set(e["wallet"] for e in buys))
        return unique / len(buys)

    def sell_suppression_ratio(self) -> float:
        if not self.events:
            return 0.0
        sells = sum(1 for e in self.events if e["type"] == "sell")
        return sells / len(self.events)

    def mint_velocity(self) -> float:
        buys = [e for e in self.events if e["type"] == "buy"]
        if len(buys) < 2:
            return 0.0
        timestamps = sorted(e["ts"] for e in buys)
        intervals = [timestamps[i + 1] - timestamps[i] for i in range(len(timestamps) - 1)]
        return sum(intervals) / len(intervals) if intervals else 0.0

    def buy_size_variance(self) -> float:
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
        except Exception:
            return 0.0

    def sell_volume_concentration(self) -> float:
        sells = [e for e in self.events if e["type"] == "sell"]
        if not sells:
            return 0.0
        wallet_volumes = defaultdict(int)
        for e in sells:
            wallet_volumes[e["wallet"]] += e["amount"]
        total = sum(wallet_volumes.values())
        top3_sum = sum(v for _, v in sorted(wallet_volumes.items(), key=lambda x: x[1], reverse=True)[:3])
        return top3_sum / total if total else 0.0

    def creator_activity_ratio(self) -> float:
        if not self.events:
            return 0.0
        minters = Counter(e["wallet"] for e in self.events if e["type"] == "buy")
        if not minters:
            return 0.0
        creator_wallet = minters.most_common(1)[0][0]
        creator_txs = sum(1 for e in self.events if e["wallet"] == creator_wallet)
        return creator_txs / len(self.events)

    def fetch_market_cap_dexscreener(self) -> Optional[float]:
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{self.token_mint}"
            res = requests.get(url, timeout=10)
            if res.status_code != 200:
                return None
            data = res.json()
            pairs = data.get("pairs", []) or []
            if not pairs:
                return None
            pair = pairs[0]
            market_cap = pair.get("marketCap")
            if market_cap is not None:
                self.market_cap_current = market_cap
                if self.market_cap_highest is None or market_cap > self.market_cap_highest:
                    self.market_cap_highest = market_cap
                return market_cap
            return None
        except Exception as e:
            print(f"[MARKET_CAP] ⚠ {e}", flush=True)
            return None

    def compute_rug_score(self) -> float:
        score = 0.0

        mint_conc = self.mint_concentration()
        if mint_conc > 0.5:
            score += min(0.25, ((mint_conc - 0.5) / 0.3) * 0.25)

        unique_ratio = self.unique_minters_ratio()
        if unique_ratio < 0.25:
            score += min(0.20, ((0.25 - unique_ratio) / 0.15) * 0.20)

        sell_ratio = self.sell_suppression_ratio()
        if sell_ratio < 0.10:
            score += min(0.20, ((0.10 - sell_ratio) / 0.10) * 0.20)

        velocity = self.mint_velocity()
        if velocity < 10:
            score += min(0.15, ((10 - velocity) / 8) * 0.15)

        var = self.buy_size_variance()
        if var < 0.01:
            score += min(0.15, ((0.01 - var) / 0.01) * 0.15)

        sell_conc = self.sell_volume_concentration()
        if sell_conc > 0.3:
            score += min(0.05, ((sell_conc - 0.3) / 0.4) * 0.05)

        return round(min(score, 1.0), 3)

    def get_risk_level(self, score: float) -> str:
        if score >= 0.7:
            return "HIGH"
        if score >= 0.4:
            return "MEDIUM"
        return "LOW"

    # -----------------------------
    # Creator extraction helpers
    # -----------------------------
    async def get_token_creator_from_das(self) -> Optional[str]:
        """Helius DAS getAsset creators array (often unreliable for Pump.fun but useful fallback)."""
        if not HELIUS_API_KEY:
            return None
        try:
            das_url = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
            payload = {"jsonrpc": "2.0", "id": 1, "method": "getAsset", "params": {"id": self.token_mint}}
            async with aiohttp.ClientSession() as session:
                async with session.post(das_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    result = data.get("result")
                    if not result:
                        return None
                    creators = result.get("creators") or []
                    if creators:
                        c0 = creators[0] or {}
                        return c0.get("address")
            return None
        except Exception:
            return None

    def _normalize_account_keys(self, keys: list) -> list:
        out = []
        for k in keys or []:
            if isinstance(k, str):
                out.append(k)
            elif isinstance(k, dict):
                pubkey = k.get("pubkey") or k.get("account") or k.get("address")
                if pubkey:
                    out.append(pubkey)
        return [x for x in out if x]

    def _get_message_and_instructions(self, tx: dict) -> Tuple[dict, list]:
        # Standard Solana RPC schema
        if "transaction" in tx:
            msg = (tx.get("transaction") or {}).get("message") or {}
            return msg, (msg.get("instructions") or [])
        # Helius /v0/transactions schema
        if "instructions" in tx:
            account_keys = tx.get("accountKeys") or tx.get("accounts") or []
            account_keys = self._normalize_account_keys(account_keys)
            msg = {"accountKeys": account_keys, "instructions": tx.get("instructions") or []}
            return msg, msg["instructions"]
        return {}, []

    def _resolve_account_key(self, message: dict, idx: int) -> Optional[str]:
        keys = message.get("accountKeys") or []
        if not (0 <= idx < len(keys)):
            return None
        k = keys[idx]
        return k if isinstance(k, str) else (k.get("pubkey") if isinstance(k, dict) else None)

    # -------- System.createAccount decode helpers --------
    def _is_system_create_compiled(self, ix: dict) -> bool:
        data = ix.get("data")
        if not data or not isinstance(data, str):
            return False
        try:
            raw = base58.b58decode(data)
            if len(raw) < 4:
                return False
            (tag,) = struct.unpack("<I", raw[:4])
            return tag in (0, 3)  # CreateAccount, CreateAccountWithSeed
        except Exception:
            return False

    def _decode_system_create_owner_program(self, ix: dict) -> Optional[str]:
        data = ix.get("data")
        if not data or not isinstance(data, str):
            return None
        try:
            raw = base58.b58decode(data)
            if len(raw) < 4:
                return None
            (tag,) = struct.unpack("<I", raw[:4])

            # tag 0: createAccount => owner at bytes 20..51
            if tag == 0:
                if len(raw) < 4 + 8 + 8 + 32:
                    return None
                owner_bytes = raw[4 + 8 + 8 : 4 + 8 + 8 + 32]
                return base58.b58encode(owner_bytes).decode("ascii")

            # tag 3: createAccountWithSeed => owner after base(32)+seed_len(4)+seed+lamports(8)+space(8)
            if tag == 3:
                if len(raw) < 4 + 32 + 4:
                    return None
                offset = 4 + 32
                (seed_len,) = struct.unpack("<I", raw[offset : offset + 4])
                offset += 4
                if len(raw) < offset + seed_len + 8 + 8 + 32:
                    return None
                offset += seed_len
                offset += 8
                offset += 8
                owner_bytes = raw[offset : offset + 32]
                return base58.b58encode(owner_bytes).decode("ascii")

            return None
        except Exception:
            return None

    def _system_create_new_account_pubkey(self, message: dict, instr: dict) -> Optional[str]:
        # Parsed (jsonParsed)
        parsed = instr.get("parsed")
        if isinstance(parsed, dict):
            info = parsed.get("info") or {}
            payer = info.get("source") or info.get("from")

            for key in ("newAccount", "newAccountPubkey"):
                v = info.get(key)
                if isinstance(v, str) and v:
                    return v

            for key in ("account", "to"):
                v = info.get(key)
                if isinstance(v, str) and v and v != payer:
                    return v

        # Compiled
        accs = instr.get("accounts")
        if isinstance(accs, list) and len(accs) >= 2:
            new_acc = accs[1]
            if isinstance(new_acc, int):
                return self._resolve_account_key(message, new_acc)
            if isinstance(new_acc, str) and new_acc:
                return new_acc
        return None

    # -------- Instruction iteration & scoping --------
    def _iter_relevant_instructions_for_create(self, tx: dict, create_outer_index: Optional[int] = None):
        message, top = self._get_message_and_instructions(tx)

        for ix in top:
            yield ix, False

        if create_outer_index is not None:
            inner_sets = (tx.get("meta") or {}).get("innerInstructions")
            if inner_sets is None:
                inner_sets = tx.get("innerInstructions")
            inner_sets = inner_sets or []

            for inner in inner_sets:
                parent_idx = None
                if isinstance(inner, dict):
                    parent_idx = inner.get("index")
                    if parent_idx is None:
                        parent_idx = inner.get("parentIndex")
                    if parent_idx is None:
                        parent_idx = inner.get("outerInstructionIndex")
                if parent_idx != create_outer_index:
                    continue

                if isinstance(inner, dict):
                    for ix in inner.get("instructions") or []:
                        yield ix, True
                elif isinstance(inner, list):
                    for ix in inner:
                        yield ix, True

    def _extract_accounts_from_parsed_info(self, parsed_info: dict) -> Optional[list]:
        """Generic extraction of pubkey-like strings from parsed.info."""
        try:
            accounts = []
            for v in (parsed_info or {}).values():
                if isinstance(v, str) and 32 <= len(v) <= 60:
                    accounts.append(v)
                elif isinstance(v, dict):
                    for kk in ("pubkey", "address", "account"):
                        vv = v.get(kk)
                        if isinstance(vv, str):
                            accounts.append(vv)
            return accounts if accounts else None
        except Exception:
            return None

    def _find_pumpfun_create_outer_index(self, tx: dict) -> Optional[int]:
        """Find outer instruction index that looks like Pump.fun CREATE (contains mint in its accounts)."""
        try:
            message, instructions = self._get_message_and_instructions(tx)

            for ix_idx, ix in enumerate(instructions):
                program_id = ix.get("programId")
                if not program_id and "programIdIndex" in ix:
                    program_id = self._resolve_account_key(message, ix.get("programIdIndex"))

                if program_id not in PUMPFUN_PROGRAM_IDS and program_id not in (PUMPFUN_AMM_PROGRAM, PUMPFUN_BONDING_CURVE_PROGRAM):
                    continue

                accounts = ix.get("accounts")
                if accounts is None and "parsed" in ix:
                    accounts = self._extract_accounts_from_parsed_info((ix.get("parsed") or {}).get("info") or {})

                if not accounts:
                    continue

                pubkeys = []
                for a in accounts:
                    if isinstance(a, int):
                        k = self._resolve_account_key(message, a)
                        if k:
                            pubkeys.append(k)
                    elif isinstance(a, str):
                        pubkeys.append(a)
                    elif isinstance(a, dict) and "pubkey" in a:
                        pubkeys.append(a["pubkey"])

                if self.token_mint in pubkeys:
                    return ix_idx
            return None
        except Exception:
            return None

    # -------- Strict CREATE validation: bonding curve + mint creation --------
    def _find_system_create_accounts(self, tx: dict, create_outer_index: Optional[int]) -> List[dict]:
        """
        Return list of dicts:
          {
            "created": <pubkey>,
            "owner": <program_id or None>,
            "is_inner": bool,
            "parsed": bool
          }
        for SystemProgram createAccount variants under the scoped parent + top-level.
        """
        out = []
        message, _ = self._get_message_and_instructions(tx)

        system_program = SYSTEM_PROGRAM
        create_types = {"createaccount", "createaccountwithseed", "create"}

        for instr, is_inner in self._iter_relevant_instructions_for_create(tx, create_outer_index):
            program_id = instr.get("programId")
            if not program_id and "programIdIndex" in instr:
                program_id = self._resolve_account_key(message, instr.get("programIdIndex"))

            if program_id != system_program:
                continue

            owner_program = None
            parsed = False

            if "parsed" in instr and isinstance(instr.get("parsed"), dict):
                parsed = True
                p = instr.get("parsed") or {}
                ptype = (p.get("type") or "").lower()
                if ptype in create_types:
                    owner_program = ((p.get("info") or {}).get("owner"))
                else:
                    continue
            else:
                if not self._is_system_create_compiled(instr):
                    continue
                owner_program = self._decode_system_create_owner_program(instr)

            created = self._system_create_new_account_pubkey(message, instr)
            if created:
                out.append({"created": created, "owner": owner_program, "is_inner": is_inner, "parsed": parsed})

        return out

    def _flatten_all_instructions(self, tx: dict) -> list:
        """
        Return a flat list of all instructions in tx:
        - top-level instructions
        - all inner instructions (any schema variant)
        """
        message, top = self._get_message_and_instructions(tx)

        inner = (tx.get("meta") or {}).get("innerInstructions")
        if inner is None:
            inner = tx.get("innerInstructions")  # Helius fallback
        inner = inner or []

        out = list(top)

        for item in inner:
            if isinstance(item, dict) and "instructions" in item:
                out.extend(item.get("instructions") or [])
            elif isinstance(item, dict):
                out.append(item)
            elif isinstance(item, list):
                out.extend(item)

        return out

    def _find_token_initialize_mint(self, tx: dict) -> bool:
        """
        Detect initializeMint / initializeMint2 for self.token_mint across ALL instructions.
        Works with Solana RPC jsonParsed and Helius parsed tx schemas.
        """
        message, _ = self._get_message_and_instructions(tx)
        all_ix = self._flatten_all_instructions(tx)

        for instr in all_ix:
            parsed = instr.get("parsed")
            if not isinstance(parsed, dict):
                continue

            ptype = (parsed.get("type") or "").lower()
            if ptype not in ("initializemint", "initializemint2"):
                continue

            program_id = instr.get("programId")
            if not program_id and "programIdIndex" in instr:
                idx = instr.get("programIdIndex")
                if isinstance(idx, int):
                    program_id = self._resolve_account_key(message, idx)

            program_name = (instr.get("program") or "").lower()

            is_token_program = (
                program_id in (TOKEN_PROGRAM, TOKEN_2022)
                or program_name in ("spl-token", "spl-token-2022")
            )
            if not is_token_program:
                continue

            info = parsed.get("info") or {}
            mint = info.get("mint")
            if mint == self.token_mint:
                print(
                    f"[CREATOR] ✓ Found {ptype} for mint={mint} via program_id={program_id} program={program_name}",
                    flush=True,
                )
                return True

        return False

    def _validate_pumpfun_create_tx(self, tx: dict) -> dict:
        """
        STRICT validation:
          A) bonding curve PDA created by System.createAccount where owner == PUMPFUN_BONDING_CURVE_PROGRAM
        AND
          B) mint creation evidence in SAME tx:
               - System.createAccount creates mint where created == token_mint AND owner == TOKEN_PROGRAM or TOKEN_2022
                 OR
               - token initializeMint/initializeMint2 for token_mint
        """
        result = {
            "is_pumpfun_create": False,
            "mint_in_accounts": False,
            "pumpfun_program_found": False,
            "program_ids": [],
            "slot": tx.get("slot"),
            "blockTime": tx.get("blockTime"),
            "bonding_curve": None,
            "mint_create_found": False,
            "mint_init_found": False,
            "validation_notes": [],
        }

        message, instructions = self._get_message_and_instructions(tx)
        account_keys = message.get("accountKeys") or []
        if not isinstance(account_keys, list):
            account_keys = []
        account_pubkeys = account_keys if all(isinstance(k, str) for k in account_keys) else self._normalize_account_keys(account_keys)

        if self.token_mint in account_pubkeys:
            result["mint_in_accounts"] = True

        inner_instructions = (tx.get("meta") or {}).get("innerInstructions")
        if inner_instructions is None:
            inner_instructions = tx.get("innerInstructions")
        inner_instructions = inner_instructions or []

        all_instructions = list(instructions)
        for inner in inner_instructions:
            if isinstance(inner, dict) and "instructions" in inner:
                all_instructions.extend(inner.get("instructions") or [])
            elif isinstance(inner, dict):
                all_instructions.append(inner)
            elif isinstance(inner, list):
                all_instructions.extend(inner)

        for instr in all_instructions:
            program_id = instr.get("programId")
            if not program_id and "programIdIndex" in instr:
                idx = instr.get("programIdIndex")
                if isinstance(idx, int):
                    program_id = self._resolve_account_key(message, idx)

            if program_id:
                result["program_ids"].append(program_id)
                if program_id in PUMPFUN_PROGRAM_IDS:
                    result["pumpfun_program_found"] = True

        create_outer_index = self._find_pumpfun_create_outer_index(tx)
        if create_outer_index is None:
            result["validation_notes"].append("could not scope create_outer_index; scanning top-level only")

        creates = self._find_system_create_accounts(tx, create_outer_index)

        bonding_curve_candidates = [
            c for c in creates if c.get("owner") == PUMPFUN_BONDING_CURVE_PROGRAM and isinstance(c.get("created"), str)
        ]
        if len(bonding_curve_candidates) == 1:
            result["bonding_curve"] = bonding_curve_candidates[0]["created"]
        elif len(bonding_curve_candidates) > 1:
            result["validation_notes"].append(f"multiple bonding curve creates (ambiguous): {len(bonding_curve_candidates)}")

        bonding_curve_ok = result["bonding_curve"] is not None

        for c in creates:
            if c.get("created") == self.token_mint and c.get("owner") in (TOKEN_PROGRAM, TOKEN_2022):
                result["mint_create_found"] = True
                break

        result["mint_init_found"] = self._find_token_initialize_mint(tx)

        mint_ok = result["mint_create_found"] or result["mint_init_found"]
        if not mint_ok:
            result["validation_notes"].append("mint not created/initialized in this tx (likely pool-init or swap)")

        result["is_pumpfun_create"] = bool(bonding_curve_ok and mint_ok)

        if not result["pumpfun_program_found"]:
            result["validation_notes"].append("pumpfun program id not found; relying on strict system/mint evidence")

        return result

    def _infer_creator_from_tx(self, tx_data: dict) -> Optional[str]:
        """
        Fallback creator inference when strict CREATE validation fails.

        Tier 2: STRONG INFERENCE
        - Extract signer accounts from tx message
        - Remove known program/system accounts
        - Prefer a single writable signer if present
        - Otherwise accept a single remaining signer
        """
        try:
            message = tx_data.get("transaction", {}).get("message", {})
            account_keys = message.get("accountKeys", []) or []

            excluded = {
                SYSTEM_PROGRAM,
                TOKEN_PROGRAM,
                TOKEN_2022,
                "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
                "So11111111111111111111111111111111111111112",
                PUMPFUN_AMM_PROGRAM,
                PUMPFUN_BONDING_CURVE_PROGRAM,
                TOKEN_METADATA_PROGRAM,
            }

            candidates = []

            for acc in account_keys:
                if isinstance(acc, dict):
                    pubkey = acc.get("pubkey")
                    signer = acc.get("signer", False)
                    writable = acc.get("writable", False)
                else:
                    pubkey = str(acc)
                    signer = False
                    writable = False

                if signer and pubkey and pubkey not in excluded:
                    candidates.append((pubkey, writable))

            # Prefer a single writable signer
            writable_candidates = [pubkey for pubkey, writable in candidates if writable]
            if len(writable_candidates) == 1:
                return writable_candidates[0]

            # Otherwise accept a single signer (deduped)
            unique_candidates = list(dict.fromkeys(pubkey for pubkey, _ in candidates))
            if len(unique_candidates) == 1:
                return unique_candidates[0]

            return None
        except Exception as e:
            print(f"[CREATOR] ⚠ Error in creator inference fallback: {e}", flush=True)
            return None

    # -----------------------------
    # Creator extraction flow
    # -----------------------------
    async def extract_bonding_curve_via_helius_parse(self, create_tx_sig: str) -> Optional[str]:
        """If you already have CREATE sig, parse it via Helius /v0/transactions (1 call)."""
        if not HELIUS_API_KEY or not create_tx_sig:
            return None

        try:
            url = f"https://api-mainnet.helius-rpc.com/v0/transactions?api-key={HELIUS_API_KEY}"
            payload = {"transactions": [create_tx_sig]}

            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status != 200:
                        print(f"[CREATOR] ⚠ Helius parse returned {resp.status}", flush=True)
                        return None

                    data = await resp.json()
                    if not isinstance(data, list) or not data:
                        print(f"[CREATOR] ⚠ Helius parse empty", flush=True)
                        return None

                    tx = data[0]
                    validation = self._validate_pumpfun_create_tx(tx)
                    if not validation["is_pumpfun_create"]:
                        print(f"[CREATOR] ❌ Helius-parsed tx FAILED strict CREATE validation", flush=True)
                        print(f"[CREATOR] notes={validation.get('validation_notes')}", flush=True)
                        return None

                    bonding_curve = validation.get("bonding_curve")
                    if bonding_curve:
                        self._create_tx_signature = create_tx_sig
                        self._create_tx_validation = validation
                        return bonding_curve
                    return None
        except Exception as e:
            print(f"[CREATOR] ⚠ Helius parse error: {e}", flush=True)
            return None
    
    def _is_fast_strict_create_candidate(self, sig_item: dict) -> bool:
        """
        Fast pre-filter before getTransaction():
        - Skip errored txs
        - Skip missing blockTime (often cache-limited / partial)
        - Skip txs after migration (if migration_blocktime is set)
        """
        if not isinstance(sig_item, dict):
            return False

        # 1) Skip failed txs
        if sig_item.get("err") is not None:
            return False

        # 2) Skip missing blockTime
        bt = sig_item.get("blockTime")
        if bt is None:
            return False

        # 3) Optional: skip anything after migration time
        mig_bt = getattr(self, "migration_blocktime", None)
        if isinstance(mig_bt, int) and bt > mig_bt:
            return False

        return True


    async def _get_blocktime_for_signature(self, sig: str) -> Optional[int]:
        """
        Fetch blockTime for a signature (used to set migration_blocktime reliably).
        """
        if not sig:
            return None
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
        }
        tx_data = await self._post_rpc_with_fallback(payload, timeout=15)
        tx = (tx_data or {}).get("result") or {}
        bt = tx.get("blockTime")
        return bt if isinstance(bt, int) else None

    async def extract_bonding_curve_from_creation_tx(
        self,
        migration_blocktime: Optional[int] = None,
        max_pages_override: Optional[int] = None,
        deadline_monotonic: Optional[float] = None,
    ) -> Optional[str]:
        """
        Find the true Pump.fun CREATE tx by scanning mint signature history from oldest to newest
        until STRICT CREATE validation passes.

        NEW:
          - migration_blocktime filter: skip signatures with blockTime > migration_blocktime
          - uses fast pre-filter before getTransaction()
          - prints extra debug when bonding_curve == no
        """
        print(
            f"[CREATOR] Extracting bonding curve from *strict* CREATE tx for {self.token_mint[:20]}..."
            + (f" (filter<=migration_bt={migration_blocktime})" if migration_blocktime else ""),
            flush=True,
        )

        # Fast path
        if self._create_tx_signature and HELIUS_API_KEY:
            bc = await self.extract_bonding_curve_via_helius_parse(self._create_tx_signature)
            if bc:
                return bc

        import time as _t_budget

        # Budget tracking — read by _resolve_creator_rpc after asyncio.run() returns
        self._budget_exceeded = False
        self._pages_checked   = 0
        self._sigs_examined   = 0

        earliest_create_sig = None
        earliest_create_tx = None
        earliest_create_validation = None
        fallback_sig = None
        fallback_tx = None
        pages_checked = 0
        proven_end = False
        oldest_logged = 0

        # Candidate budget per page to avoid 1000 getTransaction calls/page
        CANDIDATE_BUDGET_PER_PAGE = 80

        def _fast_candidate(sig_item: dict) -> bool:
            if not isinstance(sig_item, dict):
                return False
            if sig_item.get("err") is not None:
                return False

            bt = sig_item.get("blockTime")
            # If blockTime exists and migration_blocktime exists: enforce <= migration
            if migration_blocktime is not None and isinstance(bt, int):
                if bt > migration_blocktime:
                    return False

            # If bt is None, keep it (some providers omit blockTime). We'll filter later if we can.
            return True

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40)) as session:
            rpc_url = HISTORY_RPC_URLS[0] if HISTORY_RPC_URLS else "https://api.mainnet-beta.solana.com"
            before = None
            max_pages = max_pages_override if max_pages_override is not None else 5000

            while pages_checked < max_pages:
                # Deadline check before each page fetch (primary enforcement point)
                if deadline_monotonic is not None and _t_budget.monotonic() > deadline_monotonic:
                    self._budget_exceeded = True
                    print(f"[CREATOR] ⏱ Deadline exceeded before page {pages_checked + 1}", flush=True)
                    break

                pages_checked += 1
                self._pages_checked = pages_checked
                if pages_checked > 1:
                    await asyncio.sleep(0.1)

                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getSignaturesForAddress",
                    "params": [self.token_mint, {"limit": 1000, **({"before": before} if before else {})}],
                }

                data = await _rpc_post(session, rpc_url, payload, timeout_s=30)
                if not data or "result" not in data:
                    break

                sigs = data.get("result") or []
                if not sigs:
                    print(f"[CREATOR] ✅ Reached true end of mint history after {pages_checked} pages", flush=True)
                    proven_end = True
                    break

                self._sigs_examined += len(sigs)

                # Walk oldest->newest within the page
                candidates = []
                skipped_post_migration = 0
                skipped_errored = 0

                for sig_item in reversed(sigs):
                    if not isinstance(sig_item, dict):
                        continue

                    if sig_item.get("err") is not None:
                        skipped_errored += 1
                        continue

                    bt = sig_item.get("blockTime")
                    if migration_blocktime is not None and isinstance(bt, int) and bt > migration_blocktime:
                        skipped_post_migration += 1
                        continue

                    if _fast_candidate(sig_item):
                        candidates.append(sig_item)

                if not candidates:
                    before = sigs[-1].get("signature")
                    print(
                        f"[CREATOR] Page {pages_checked}: candidates=0 "
                        f"(skipped_post_migration={skipped_post_migration}, skipped_errored={skipped_errored})",
                        flush=True,
                    )
                    continue

                candidates_to_check = candidates[:CANDIDATE_BUDGET_PER_PAGE]
                if len(candidates) > len(candidates_to_check):
                    print(
                        f"[CREATOR] Page {pages_checked}: prefilter candidates={len(candidates)} "
                        f"(checking first {len(candidates_to_check)}), "
                        f"skipped_post_migration={skipped_post_migration}, skipped_errored={skipped_errored}",
                        flush=True,
                    )

                for sig_item in candidates_to_check:
                    # Deadline check before each getTransaction call
                    if deadline_monotonic is not None and _t_budget.monotonic() > deadline_monotonic:
                        self._budget_exceeded = True
                        print(f"[CREATOR] ⏱ Deadline exceeded mid-page {pages_checked}", flush=True)
                        break

                    sig = sig_item.get("signature")
                    if not sig:
                        continue

                    # If blockTime is missing on the signature item but we have migration_blocktime,
                    # do a cheap guard: skip if tx.blockTime ends up post-migration.
                    tx_payload = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getTransaction",
                        "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
                    }

                    tx_data = await self._post_rpc_with_fallback(tx_payload, timeout=10)
                    tx = (tx_data or {}).get("result")
                    if not tx:
                        continue

                    tx_bt = tx.get("blockTime")
                    if migration_blocktime is not None and isinstance(tx_bt, int) and tx_bt > migration_blocktime:
                        # hard skip: post-migration
                        continue

                    # Track the oldest scanned transaction
                    if self._oldest_scanned_sig is None:
                        self._oldest_scanned_sig = sig
                        self._oldest_scanned_tx = tx

                    # Capture earliest fallback candidate for inference if strict validation fails
                    if fallback_sig is None and fallback_tx is None:
                        fallback_sig = sig
                        fallback_tx = tx

                    validation = self._validate_pumpfun_create_tx(tx)


                    if oldest_logged < 5:
                        oldest_logged += 1
                        print(
                            f"[CREATOR] Oldest#{oldest_logged} {sig[:16]}... strict_create={validation['is_pumpfun_create']} "
                            f"mint_create={validation['mint_create_found']} mint_init={validation['mint_init_found']} "
                            f"bonding_curve={'yes' if validation.get('bonding_curve') else 'no'}",
                            flush=True,
                        )

                    if validation["is_pumpfun_create"]:
                        print(f"[CREATOR] ✅ Found STRICT Pump.fun CREATE tx: {sig}", flush=True)
                        earliest_create_sig = sig
                        earliest_create_tx = tx
                        earliest_create_validation = validation
                        break

                if earliest_create_sig:
                    break

                if getattr(self, "_budget_exceeded", False):
                    break

                before = sigs[-1].get("signature")
                print(
                    f"[CREATOR] Page {pages_checked}: checked={len(candidates_to_check)}/{len(candidates)} candidates "
                    f"(budget={CANDIDATE_BUDGET_PER_PAGE}), no strict CREATE yet",
                    flush=True,
                )

            if pages_checked >= max_pages and not proven_end:
                self._budget_exceeded = True
                print(f"[CREATOR] ⚠ Hit max_pages={max_pages} (proven_end=False)", flush=True)

        if not earliest_create_sig or not earliest_create_tx or not earliest_create_validation:
            print(f"[CREATOR] ❌ No STRICT CREATE transaction found for mint", flush=True)

            self._fallback_creator_sig = fallback_sig
            self._fallback_creator_tx = fallback_tx

            if fallback_sig:
                print(f"[CREATOR] ⚠ Stored fallback candidate tx: {fallback_sig}", flush=True)
            else:
                print(f"[CREATOR] ⚠ No fallback candidate tx was captured during strict scan", flush=True)

            return None

        # store
        self._create_tx_signature = earliest_create_sig
        self._create_tx_validation = earliest_create_validation

        # fee payer (creator heuristic) from CREATE tx
        msg = ((earliest_create_tx.get("transaction") or {}).get("message") or {})
        keys = msg.get("accountKeys") or []
        fee_payer = None
        if keys:
            k0 = keys[0]
            fee_payer = k0.get("pubkey") if isinstance(k0, dict) else str(k0)
        if fee_payer:
            self._create_tx_creator = fee_payer
            print(f"[CREATOR] ✓ CREATE fee payer (creator): {fee_payer}", flush=True)

        bc = earliest_create_validation.get("bonding_curve")
        print(f"[CREATOR] ✓ Bonding curve: {bc} (proven_end={proven_end})", flush=True)
        return bc



    async def get_true_earliest_signature(self, bonding_curve_pda: Optional[str] = None, max_pages: int = 5000, page_limit: int = 1000) -> tuple:
        """
        Find earliest signature for a given account.
        Used for *bonding curve activity provenance only*.
        """
        if bonding_curve_pda is None and self._create_tx_signature:
            return self._create_tx_signature, False, "cached"

        if not HISTORY_RPC_URLS:
            return None, False, "none"

        query_account = bonding_curve_pda or self.token_mint

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40)) as session:
            for rpc_url in HISTORY_RPC_URLS:
                before = None
                last_sig = None
                pages = 0
                first_page_sigs = []

                try:
                    while pages < max_pages:
                        pages += 1
                        if pages > 1:
                            await asyncio.sleep(0.1)

                        cfg = {"limit": page_limit, **({"before": before} if before else {})}
                        payload = {"jsonrpc": "2.0", "id": 1, "method": "getSignaturesForAddress", "params": [query_account, cfg]}

                        data = await _rpc_post(session, rpc_url, payload, timeout_s=30)
                        if data is None:
                            break
                        sigs = data.get("result") or []
                        if pages == 1:
                            first_page_sigs = sigs

                        if not sigs:
                            if _looks_cache_limited(first_page_sigs):
                                return last_sig, False, rpc_url
                            return last_sig, True, rpc_url

                        last_sig = sigs[-1]["signature"]
                        before = last_sig
                    return last_sig, False, rpc_url
                except Exception:
                    continue

        return None, False, "none"

    async def get_creator_from_earliest_tx(
        self,
        migration_signature: Optional[str] = None,
        migration_blocktime: Optional[int] = None,
        max_pages_override: Optional[int] = None,
        deadline_monotonic: Optional[float] = None,
    ) -> dict:
        """
        Creator = fee payer of the STRICT CREATE tx.
        Also tracks earliest bonding curve activity signature separately.

        NEW:
          - You can pass migration_signature OR migration_blocktime
          - If migration_signature is provided, we fetch its blockTime once and use it to filter out post-migration txs.
        """
        provenance = {
            "creator": None,
            "create_sig": None,
            "earliest_curve_sig": None,
            "reached_end": False,
            "rpc_used": None,
            "is_pumpfun_create": False,
            "slot": None,
            "blockTime": None,
            "fee_payer": None,
            "bonding_curve_pda": None,
            "status": "unproven",
            "validation_notes": [],
            "migration_blocktime": None,
            "migration_signature": migration_signature,
        }

        # Resolve migration_blocktime if only signature is provided
        if migration_blocktime is None and migration_signature:
            migration_blocktime = await self._get_blocktime_for_signature(migration_signature)
            if migration_blocktime:
                print(f"[CREATOR] 🕐 Using migration blockTime={migration_blocktime} from sig={migration_signature[:16]}...", flush=True)

        provenance["migration_blocktime"] = migration_blocktime

        bonding_curve_pda = await self.extract_bonding_curve_from_creation_tx(
            migration_blocktime=migration_blocktime,
            max_pages_override=max_pages_override,
            deadline_monotonic=deadline_monotonic,
        )
        if not bonding_curve_pda:
            provenance["validation_notes"].append("Could not extract bonding curve from strict CREATE tx")
            print(f"[CREATOR] ⚠ Fallback inference triggered", flush=True)

            # Tier 2A: Prefer fallback candidate captured during strict CREATE scan
            fallback_sig = self._fallback_creator_sig
            fallback_tx = self._fallback_creator_tx

            if fallback_tx:
                try:
                    inferred_creator = self._infer_creator_from_tx(fallback_tx)
                    if inferred_creator:
                        print(f"[CREATOR] ⚠ Using inferred creator fallback from scanned candidate: {inferred_creator}", flush=True)
                        provenance["creator"] = inferred_creator
                        provenance["create_sig"] = fallback_sig
                        provenance["status"] = "inferred_create"
                        provenance["blockTime"] = fallback_tx.get("blockTime")
                        provenance["slot"] = fallback_tx.get("slot")
                        provenance["validation_notes"].append("Creator inferred from earliest scanned candidate transaction (Tier 2)")
                        return provenance
                except Exception as e:
                    print(f"[CREATOR] ⚠ Error during scanned-candidate inference: {e}", flush=True)

            # Tier 2B: Fallback to earliest mint transaction lookup
            # Skip fallback if the budget deadline is already exceeded
            import time as _t_check
            if deadline_monotonic is not None and _t_check.monotonic() > deadline_monotonic:
                self._budget_exceeded = True
                print("[CREATOR] ⏱ Deadline exceeded before Tier 2B fallback — skipping", flush=True)
            elif not getattr(self, "_budget_exceeded", False):
                pass  # proceed to call below
            earliest_sig, reached_end, rpc_used = (
                (None, False, "deadline") if getattr(self, "_budget_exceeded", False)
                else await self.get_true_earliest_signature()
            )
            if earliest_sig:
                try:
                    async with aiohttp.ClientSession() as session:
                        earliest_tx = await self.fetch_tx_with_retry(session, earliest_sig)

                    if earliest_tx:
                        inferred_creator = self._infer_creator_from_tx(earliest_tx)
                        if inferred_creator:
                            print(f"[CREATOR] ⚠ Using inferred creator fallback from earliest mint tx: {inferred_creator}", flush=True)
                            provenance["creator"] = inferred_creator
                            provenance["create_sig"] = earliest_sig
                            provenance["status"] = "inferred_create"
                            provenance["blockTime"] = earliest_tx.get("blockTime")
                            provenance["slot"] = earliest_tx.get("slot")
                            provenance["validation_notes"].append("Creator inferred from earliest mint transaction (Tier 2)")
                            return provenance
                except Exception as e:
                    print(f"[CREATOR] ⚠ Error during earliest-tx fallback inference: {e}", flush=True)

            print(f"[CREATOR] ❌ Fallback inference failed - no creator found", flush=True)
            provenance["status"] = "no_create_found"
            provenance["validation_notes"].append("No creator found - neither strict CREATE nor inference succeeded")
            return provenance

        provenance["bonding_curve_pda"] = bonding_curve_pda

        if not self._create_tx_signature or not self._create_tx_validation:
            provenance["validation_notes"].append("No CREATE signature/validation stored")
            return provenance

        provenance["create_sig"] = self._create_tx_signature
        provenance["is_pumpfun_create"] = bool(self._create_tx_validation.get("is_pumpfun_create"))
        provenance["slot"] = self._create_tx_validation.get("slot")
        provenance["blockTime"] = self._create_tx_validation.get("blockTime")
        provenance["validation_notes"].extend(self._create_tx_validation.get("validation_notes") or [])

        # Provenance: earliest curve activity (NOT used for creator)
        earliest_curve_sig, reached_end, rpc_used = await self.get_true_earliest_signature(bonding_curve_pda=bonding_curve_pda)
        provenance["earliest_curve_sig"] = earliest_curve_sig
        provenance["reached_end"] = reached_end
        provenance["rpc_used"] = rpc_used

        if self._create_tx_creator and provenance["is_pumpfun_create"]:
            provenance["fee_payer"] = self._create_tx_creator
            provenance["creator"] = self._create_tx_creator
        else:
            provenance["validation_notes"].append("CREATE fee payer missing or CREATE validation false")
            return provenance

        if provenance["is_pumpfun_create"]:
            provenance["status"] = "confirmed" if reached_end else "unproven"
            if not reached_end:
                provenance["validation_notes"].append("pagination incomplete for curve account (creator still valid from CREATE)")
        else:
            provenance["status"] = "unproven"

        return provenance

    async def get_summary_async(self) -> Dict:
        score = self.compute_rug_score()

        provenance = await self.get_creator_from_earliest_tx()
        pumpfun_creator = provenance.get("creator")
        metadata_creator = None
        if not pumpfun_creator:
            metadata_creator = await self.get_token_creator_from_das()

        final_creator = pumpfun_creator or metadata_creator

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
            "creator_activity_ratio": self.creator_activity_ratio(),
            "market_cap_current": self.market_cap_current,
            "market_cap_highest": self.market_cap_highest,
            "coverage": (self.transactions_fetched / self.signatures_requested * 100) if self.signatures_requested > 0 else 0,
            "creator": final_creator,
            "creator_provenance": {
                "pumpfun_creator": pumpfun_creator,
                "pumpfun_status": provenance.get("status"),
                "metadata_creator": metadata_creator,
                "bonding_curve_pda": provenance.get("bonding_curve_pda"),
                "create_sig": provenance.get("create_sig"),
                "earliest_curve_sig": provenance.get("earliest_curve_sig"),
                "validation_notes": provenance.get("validation_notes", []),
                "reached_end": provenance.get("reached_end"),
                "is_pumpfun_create": provenance.get("is_pumpfun_create"),
            },
        }

    def summary(self) -> Dict:
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
            "creator_activity_ratio": self.creator_activity_ratio(),
            "market_cap_current": self.market_cap_current,
            "market_cap_highest": self.market_cap_highest,
            "coverage": (self.transactions_fetched / self.signatures_requested * 100) if self.signatures_requested > 0 else 0,
        }


# -----------------------------
# Test runner
# -----------------------------
async def main():
    mint = "62eNTADfQDdDygSAHeqqipaHHKvcWc4Cob1xqaYjpump"
    a = PostMigrationAnalyzer(mint)

    # (Optional) post-migration activity parsing:
    # await a.fetch_curve_activity_async()

    prov = await a.get_creator_from_earliest_tx()
    print("\nCREATOR RESULT\n", prov)

    # full summary (creator + risk metrics)
    # summary = await a.get_summary_async()
    # print("\nSUMMARY\n", summary)``

    return prov


if __name__ == "__main__":
    asyncio.run(main())
