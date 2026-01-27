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
from typing import Dict, List, Optional
from collections import Counter, defaultdict
from statistics import variance
import os
from dotenv import load_dotenv

load_dotenv()

# Configuration - Optimized for QuickNode free tier (balanced approach)
BATCH_SIZE = 15
# Balanced concurrency (10 concurrent requests is safe + fast)
MAX_SIGNATURES = 1000000  # Fetch entire transaction history
RPC_TIMEOUT = 60  # Increased timeout to handle slow RPC responses
MAX_RETRIES = 10  # More retries to handle rate limit recovery
RETRY_DELAYS = [0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 15.0, 20.0, 30.0, 60.0]  # Extended backoff
BATCH_DELAY = 0.2  # Small delay between batches to prevent burst overload

# RPC failover chain - same as pumpfun_curve_listener
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
RPC_URL = os.getenv("RPC_URL", "")
RPC_URL_2 = os.getenv("RPC_URL_2", "")
FLUX_RPC_URL = "https://eu.fluxrpc.com?key=65c5a3de-6232-4300-a9c6-198646d467c4"

# HTTP: Use QuickNode if available, then Helius, then public
# NOTE: FluxRPC excluded - it only returns recent ~5min of data, unsuitable for signature history
RPC_URLS = [url for url in [RPC_URL, RPC_URL_2] if url]  # QuickNodes
RPC_URLS.append(f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}" if HELIUS_API_KEY else "https://api.mainnet-beta.solana.com")  # Helius fallback
RPC_URLS.append("https://api.mainnet-beta.solana.com")  # Public fallback

# History RPC: Full history only (no FluxRPC which caches recent only, no QuickNode which can be unreliable)
# Use Helius (if available) and public Solana for reliable full-history pagination
HISTORY_RPC_URLS = []
if HELIUS_API_KEY:
    HISTORY_RPC_URLS.append(f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}")
HISTORY_RPC_URLS.append("https://api.mainnet-beta.solana.com")  # Public Solana (full history)


# Helper functions for cache-limited RPC detection
def _looks_cache_limited(sigs: List[Dict]) -> bool:
    """
    Detect providers that only return a tiny recent cache (FluxRPC-like).
    Heuristics:
      - small window
      - missing blockTime
      - narrow slot range
    """
    if not sigs:
        return False

    if len(sigs) >= 1000:
        return False

    slots = [s.get("slot") for s in sigs if isinstance(s.get("slot"), int)]
    if not slots:
        return False

    slot_span = max(slots) - min(slots)
    null_bt = sum(1 for s in sigs if s.get("blockTime") is None)

    # If slot span is tiny (< 2000 slots ~ few seconds) and most are missing blockTime,
    # this looks like a cache-limited RPC
    if slot_span < 2000 and (null_bt / max(1, len(sigs))) > 0.7:
        return True

    return False


async def _rpc_post(session: aiohttp.ClientSession, url: str, payload: dict, timeout_s: int = 30) -> Optional[dict]:
    """Post to single RPC URL and return response"""
    try:
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=timeout_s)) as resp:
            if resp.status == 200:
                data = await resp.json()
                if "error" not in data:
                    return data
    except Exception:
        pass
    return None


# Pump.fun program IDs
PUMPFUN_PROGRAM_IDS = {
    "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg",  # Pump.fun processor
}

SYSTEM_PROGRAM = "11111111111111111111111111111111"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJsyFbPtrKbVs73Cw6Xj2Yg5MNg"
TOKEN_2022 = "TokenzQdBbjFD8aff5ZZUwWWwG6Go5rm5KWQEypdCU8"
SYSTEM_PROGRAMS = {SYSTEM_PROGRAM, TOKEN_PROGRAM, TOKEN_2022}


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
        self.market_cap_current = None
        self.market_cap_highest = None

        print(f"[ANALYZER_INIT] Token: {token_mint}", flush=True)
        print(f"[ANALYZER_INIT] RPC: {rpc_url[:80]}{'...' if len(rpc_url) > 80 else ''}", flush=True)

    # --- Signature Fetching with RPC Fallback ---
    async def _post_rpc_with_fallback(self, payload: dict, timeout: int = 10) -> Optional[dict]:
        """
        Post to RPC with automatic failover chain.

        Tries: Primary QuickNode -> Secondary QuickNode -> Helius -> Public Solana
        Returns: JSON response data or None if all fail
        """
        try:
            async with aiohttp.ClientSession() as session:
                for i, rpc_url in enumerate(RPC_URLS):
                    try:
                        async with session.post(rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                            if resp.status == 200:
                                return await resp.json()
                            elif resp.status == 429:
                                # Rate limited, try next in chain (silently)
                                if i < len(RPC_URLS) - 1:
                                    continue
                            else:
                                # Other error, try next
                                if i < len(RPC_URLS) - 1:
                                    continue
                    except asyncio.TimeoutError:
                        if i < len(RPC_URLS) - 1:
                            continue
                    except Exception as e:
                        if i < len(RPC_URLS) - 1:
                            continue

                # All RPC endpoints failed
                return None
        except Exception as e:
            print(f"[RPC_ERROR] {e}", flush=True)
            return None

    async def fetch_signatures(self, limit=MAX_SIGNATURES) -> List[str]:
        """Fetch signatures for token mint with RPC fallback chain"""
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
                res = await self._post_rpc_with_fallback(payload, timeout=10)
                if res is None:
                    print(f"[SIG_FETCH] ✗ All RPC endpoints failed after {pages_fetched} pages", flush=True)
                    break

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
    async def fetch_transactions_async(self, sigs: List[str]):
        """Fetch transactions asynchronously with semaphore-based concurrency (proven working method)"""
        # Use semaphore like the pre-migration analyzer (this approach was working)
        sem = asyncio.Semaphore(BATCH_SIZE)
        
        async with aiohttp.ClientSession() as session:
            successful = 0
            failed = 0
            
            tasks = []
            for sig in sigs:
                task = self._fetch_tx_semaphore(session, sig, sem)
                tasks.append(task)
            
            # Process results as they complete
            for idx, future in enumerate(asyncio.as_completed(tasks), 1):
                try:
                    tx = await future
                    if tx:
                        self._parse_curve_tx(tx)
                        self.transactions_fetched += 1
                        successful += 1
                    else:
                        failed += 1
                except Exception as e:
                    failed += 1
                
                # Progress update every batch
                if idx % BATCH_SIZE == 0 or idx == len(sigs):
                    success_rate = (successful / idx * 100) if idx > 0 else 0
                    print(f"[ASYNC] Progress: {idx}/{len(sigs)} txs | Success: {successful}/{idx} ({success_rate:.1f}%) | Failed: {failed}", flush=True)

    async def _fetch_tx_semaphore(self, session: aiohttp.ClientSession, sig: str, sem: asyncio.Semaphore):
        """Fetch a single transaction with semaphore to limit concurrency"""
        async with sem:
            return await self.fetch_tx_with_retry(session, sig)

    async def fetch_tx_with_retry(self, session: aiohttp.ClientSession, sig: str):
        """Fetch transaction with RPC failover chain and exponential backoff"""
        for attempt in range(MAX_RETRIES):
            # Try each RPC endpoint in the failover chain
            for rpc_url in RPC_URLS:
                try:
                    payload = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getTransaction",
                        "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
                    }

                    async with session.post(
                        rpc_url,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=RPC_TIMEOUT)
                    ) as resp:

                        # --- HTTP-level errors ---
                        if resp.status != 200:
                            if resp.status == 429:
                                # Rate limited, try next RPC endpoint
                                continue
                            elif resp.status >= 500:
                                # Server error, try next RPC endpoint
                                continue
                            else:
                                # Other error (4xx), don't retry this endpoint
                                return None

                        data = await resp.json()

                        # --- RPC-level errors ---
                        if "error" in data:
                            error_code = data["error"].get("code", -1)
                            error_msg = data["error"].get("message", "unknown")

                            retryable = error_code in {-32008, -32000, -32003, -32009}

                            if retryable:
                                # Try next RPC endpoint
                                continue
                            # Non-retryable error
                            return None

                        result = data.get("result")
                        if result:
                            return result

                        # No result, try next RPC endpoint
                        continue

                except asyncio.TimeoutError:
                    # Timeout on this RPC, try next
                    continue
                except Exception:
                    # Error on this RPC, try next
                    continue

            # All RPC endpoints failed, retry with backoff
            if attempt < MAX_RETRIES - 1:
                # Only log every 5 retries to reduce noise
                if (attempt + 1) % 5 == 0:
                    print(
                        f"[FETCH_TX] Retrying transaction {sig[:12]}... "
                        f"(attempt {attempt + 1}/{MAX_RETRIES})",
                        flush=True
                    )
                delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                await asyncio.sleep(delay)

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
        print(f"[STREAM] Starting post-migration analysis for {self.token_mint}", flush=True)

        sigs = await self.fetch_signatures(limit=MAX_SIGNATURES)
        print(f"[STREAM] Fetched {len(sigs)} signatures, starting async fetch...", flush=True)

        if not sigs:
            print(f"[STREAM] ⚠ No signatures found", flush=True)
            return

        self.signatures_requested = len(sigs)
        await self.fetch_transactions_async(sigs)

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

    def creator_activity_ratio(self):
        """Ratio of transactions from top minter (creator proxy)"""
        if not self.events:
            return 0.0
        
        minters = Counter(e["wallet"] for e in self.events if e["type"] == "buy")
        if not minters:
            return 0.0
        
        creator_wallet = minters.most_common(1)[0][0]
        creator_txs = sum(1 for e in self.events if e["wallet"] == creator_wallet)
        return creator_txs / len(self.events)

    def fetch_market_cap_dexscreener(self) -> float:
        """Fetch current market cap from DexScreener"""
        try:
            # DexScreener API endpoint for Solana tokens
            url = f"https://api.dexscreener.com/latest/dex/tokens/{self.token_mint}"
            res = requests.get(url, timeout=10)
            
            if res.status_code != 200:
                return None
            
            data = res.json()
            pairs = data.get("pairs", [])
            
            if not pairs:
                return None
            
            # Find the pair with highest liquidity (primary pair)
            pair = pairs[0]
            market_cap = pair.get("marketCap")
            
            if market_cap is not None:
                print(f"[MARKET_CAP] Current: ${market_cap:,.0f}", flush=True)
                
                # Update current market cap
                self.market_cap_current = market_cap
                
                # Update highest market cap
                if self.market_cap_highest is None or market_cap > self.market_cap_highest:
                    self.market_cap_highest = market_cap
                    print(f"[MARKET_CAP] New highest: ${self.market_cap_highest:,.0f}", flush=True)
                
                return market_cap
            
            return None
            
        except Exception as e:
            print(f"[MARKET_CAP] ⚠ Error fetching from DexScreener: {e}", flush=True)
            return None
    
    def should_stop_tracking_market_cap(self, market_cap: float) -> bool:
        """Check if market cap has fallen below 30k threshold"""
        if market_cap is None:
            return False
        return market_cap < 30000

    def compute_rug_score(self):
        """
        Continuous rug probability score (0–1)
        Preserves original max weights but scales by severity
        """
        score = 0.0

        # 1. Mint concentration (max 0.25)
        mint_conc = self.mint_concentration()
        if mint_conc > 0.5:
            # scale from 50% → 80%
            score += min(0.25, ((mint_conc - 0.5) / 0.3) * 0.25)

        # 2. Unique minters ratio (max 0.20)
        unique_ratio = self.unique_minters_ratio()
        if unique_ratio < 0.25:
            # scale from 25% → 10%
            score += min(0.20, ((0.25 - unique_ratio) / 0.15) * 0.20)

        # 3. Sell suppression (max 0.20)
        sell_ratio = self.sell_suppression_ratio()
        if sell_ratio < 0.10:
            # scale from 10% → 0%
            score += min(0.20, ((0.10 - sell_ratio) / 0.10) * 0.20)

        # 4. Mint velocity (max 0.15)
        velocity = self.mint_velocity()
        if velocity < 10:
            # scale from 10s → 2s
            score += min(0.15, ((10 - velocity) / 8) * 0.15)

        # 5. Buy size variance (max 0.15)
        var = self.buy_size_variance()
        if var < 1e7:
            # lower variance = more suspicious
            score += min(0.15, ((1e7 - var) / 1e7) * 0.15)

        # 6. Sell volume concentration (max 0.05)
        sell_conc = self.sell_volume_concentration()
        if sell_conc > 0.3:
            # scale from 30% → 70%
            score += min(0.05, ((sell_conc - 0.3) / 0.4) * 0.05)

        return round(min(score, 1.0), 3)


    def get_risk_level(self, score: float) -> str:
        """Determine risk level from score"""
        if score >= 0.7:
            return "HIGH"
        elif score >= 0.4:
            return "MEDIUM"
        else:
            return "LOW"
    async def get_token_creator_from_das(self) -> Optional[str]:
        """
        Fetch token creator from Helius DAS API.
        
        The Metaplex metadata stores creator information in the creators array.
        This method fetches it from the DAS API which indexes all SPL token metadata.
        
        Returns:
            Creator wallet address, or None if not found
        """
        if not HELIUS_API_KEY:
            return None
        
        try:
            das_url = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
            
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAsset",
                "params": {"id": self.token_mint}
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(das_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if "result" in data and data["result"]:
                            result = data["result"]
                            # Get first creator from creators array
                            if "creators" in result and result["creators"]:
                                creator = result["creators"][0]
                                creator_address = creator.get("address")
                                if creator_address:
                                    return creator_address
                    return None
        except Exception as e:
            # Silently fail - DAS API not critical for analysis
            return None

    def _extract_fee_payer_from_tx(self, tx: dict) -> Optional[str]:
        """
        Extract fee payer (first account / accounts[0]) from transaction.

        Handles both string and dict accountKeys formats.
        Safely handles missing "message" field (can be None in some responses).
        Note: Fee payer is a heuristic for "creator", not guaranteed truth.
        """
        try:
            # Safely handle missing or None "message" field
            msg = ((tx.get("transaction") or {}).get("message") or {})
            keys = msg.get("accountKeys") or []
            if not keys:
                return None

            first = keys[0]
            if isinstance(first, str):
                return first
            if isinstance(first, dict):
                return first.get("pubkey")

            return None
        except Exception:
            return None

    def _validate_pumpfun_create_tx(self, tx: dict) -> dict:
        """
        Validate that a transaction is actually a Pump.fun CREATE event.

        Returns dict:
        {
            'is_pumpfun_create': True/False,
            'mint_in_accounts': True/False,
            'pumpfun_program_found': True/False,
            'program_ids': [list of found program IDs],
            'slot': transaction slot (for on-chain timestamp),
            'blockTime': UNIX timestamp from block
        }
        """
        result = {
            'is_pumpfun_create': False,
            'mint_in_accounts': False,
            'pumpfun_program_found': False,
            'program_ids': [],
            'slot': None,
            'blockTime': None
        }

        try:
            # Get transaction metadata
            result['slot'] = tx.get('slot')
            result['blockTime'] = tx.get('blockTime')

            # Check if mint appears in account keys
            message = (tx.get("transaction") or {}).get("message") or {}
            account_keys = message.get("accountKeys") or []

            # account_keys can be list of strings or list of dicts
            account_pubkeys = []
            for acct in account_keys:
                if isinstance(acct, str):
                    account_pubkeys.append(acct)
                elif isinstance(acct, dict):
                    pubkey = acct.get("pubkey")
                    if pubkey:
                        account_pubkeys.append(pubkey)

            # Check if our mint is in the accounts
            if self.token_mint in account_pubkeys:
                result['mint_in_accounts'] = True

            # Check instructions for Pump.fun programs
            instructions = message.get("instructions") or []
            inner_instructions = tx.get("meta", {}).get("innerInstructions") or []

            all_instructions = list(instructions)
            for inner in inner_instructions:
                all_instructions.extend(inner.get("instructions") or [])

            for instr in all_instructions:
                program_id = instr.get("programId")
                if program_id:
                    result['program_ids'].append(program_id)
                    if program_id in PUMPFUN_PROGRAM_IDS:
                        result['pumpfun_program_found'] = True

            # Log programs found for debugging
            if result['program_ids']:
                print(f"[CREATOR] 📋 Programs found in transaction: {result['program_ids']}", flush=True)
            else:
                print(f"[CREATOR] 📋 No instructions/programs found in transaction", flush=True)

            # CRITICAL FIX: A valid Pump.fun create MUST have BOTH:
            # 1. Mint in accounts (ensures this is the mint's creation)
            # 2. Pump.fun program found in instructions (ensures it's a Pump.fun tx)
            result['is_pumpfun_create'] = (
                result['mint_in_accounts'] and 
                result['pumpfun_program_found']
            )

            return result

        except Exception as e:
            print(f"[CREATOR] ⚠ Error validating Pump.fun create: {e}", flush=True)
            return result

    async def _get_earliest_signature(self, session: aiohttp.ClientSession, rpc_url: str) -> dict:
        """
        Paginate through ALL signatures to find the truly earliest one.

        Returns ONLY when pagination naturally ends (empty page), not when hitting max_pages.
        This ensures we can prove the signature is the TRUE oldest, not just "oldest we found so far".

        Returns dict:
        {
            'signature': earliest_sig or None,
            'reached_end': True only if pagination naturally completed (got empty page),
            'pages_traversed': number of pages fetched,
            'total_sigs_seen': total signatures across all pages
        }

        CRITICAL: reached_end must be True to claim "this is the first transaction ever"
        """
        import os

        before = None
        last_sig = None
        pages = 0
        total_sigs_seen = 0
        reached_end = False
        max_pages = int(os.getenv("CREATOR_MAX_PAGES", "200"))  # Can be overridden via env

        try:
            while pages < max_pages:
                pages += 1
                config = {"limit": 1000}
                if before:
                    config["before"] = before

                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getSignaturesForAddress",
                    "params": [self.token_mint, config],
                }

                try:
                    async with session.post(rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                        data = await resp.json()

                    # Check for RPC error response
                    if "error" in data:
                        raise RuntimeError(f"RPC error: {data['error']}")

                    sigs = data.get("result") or []

                    # CRITICAL CHECK: Empty result = we've reached the actual end of history
                    if not sigs:
                        reached_end = True
                        return {
                            "signature": last_sig,
                            "reached_end": reached_end,
                            "pages_traversed": pages,
                            "total_sigs_seen": total_sigs_seen
                        }

                    total_sigs_seen += len(sigs)

                    # Get the last signature (oldest in this page)
                    last_sig = sigs[-1]["signature"]

                    # Paginate using the last returned sig
                    before = last_sig

                    # If we got fewer than 1000, we've reached the actual end
                    if len(sigs) < 1000:
                        reached_end = True
                        return {
                            "signature": last_sig,
                            "reached_end": reached_end,
                            "pages_traversed": pages,
                            "total_sigs_seen": total_sigs_seen
                        }

                except asyncio.TimeoutError:
                    print(f"[CREATOR] ⚠ RPC timeout on page {pages}", flush=True)
                    raise
                except Exception as e:
                    print(f"[CREATOR] ⚠ Error on page {pages}: {e}", flush=True)
                    raise

            # If we exit the loop, we hit max_pages without reaching the end
            print(f"[CREATOR] ⚠ Hit max_pages={max_pages} without reaching end of history", flush=True)
            return {
                "signature": last_sig,
                "reached_end": False,  # CRITICAL: False because we didn't naturally reach the end
                "pages_traversed": pages,
                "total_sigs_seen": total_sigs_seen
            }

        except Exception as e:
            print(f"[CREATOR] ⚠ Error fetching earliest signature: {e}", flush=True)
            return {
                "signature": None,
                "reached_end": False,
                "pages_traversed": pages,
                "total_sigs_seen": total_sigs_seen
            }

    async def get_true_earliest_signature(self, bonding_curve_pda: Optional[str] = None, max_pages: int = 1000, page_limit: int = 1000) -> tuple:
        """
        Find the true earliest signature using full-history RPC chain.
        
        For Pump.fun tokens, queries the bonding curve PDA (if provided) to find the
        creation transaction. Otherwise falls back to token mint.
        
        Args:
            bonding_curve_pda: Optional bonding curve PDA address. If provided, queries this
                             instead of token_mint for more accurate creator extraction.
            max_pages: Maximum pagination pages to traverse
            page_limit: Signatures per page (usually 1000 from RPC)
        
        Returns: (earliest_sig, proven, rpc_used)
          - earliest_sig: The signature (None if not found)
          - proven: True if we reached end-of-history or legitimately paginated through real data
          - rpc_used: Which RPC endpoint succeeded
        """
        if not HISTORY_RPC_URLS:
            return None, False, "none"

        # Use bonding curve PDA if available (more accurate for creator extraction)
        # Otherwise fall back to token mint
        query_account = bonding_curve_pda or self.token_mint
        query_type = "bonding_curve_pda" if bonding_curve_pda else "token_mint"

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40)) as session:
            for rpc_url in HISTORY_RPC_URLS:
                before = None
                last_sig = None
                pages = 0
                first_page_sigs = []

                try:
                    while pages < max_pages:
                        pages += 1
                        cfg = {"limit": page_limit}
                        if before:
                            cfg["before"] = before

                        payload = {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "getSignaturesForAddress",
                            "params": [query_account, cfg],
                        }

                        data = await _rpc_post(session, rpc_url, payload, timeout_s=30)
                        if data is None:
                            break

                        sigs = data.get("result") or []

                        if pages == 1:
                            first_page_sigs = sigs

                        if not sigs:
                            # Check if this looks like cache-limited RPC
                            if _looks_cache_limited(first_page_sigs):
                                print(f"[CREATOR] ⚠ Cache-limited RPC {rpc_url[:40]}... (not proven)", flush=True)
                                return last_sig, False, rpc_url
                            # True end of history
                            print(f"[CREATOR] ✅ Reached true end of history ({query_type}) from {rpc_url[:40]}...", flush=True)
                            return last_sig, True, rpc_url

                        last_sig = sigs[-1]["signature"]
                        before = last_sig

                        print(f"[CREATOR] Page {pages}: {len(sigs)} sigs from {query_type} ({rpc_url[:40]}...)", flush=True)

                    # Hit max_pages safety limit
                    # If we got consistent full pages (1000 sigs each), this is real pagination, not cache-limited
                    # Mark as proven if we're genuinely paginating through history
                    is_real_pagination = pages > 1  # We made multiple pagination requests
                    print(f"[CREATOR] ⚠ Hit max_pages limit ({max_pages}) on {query_type} ({rpc_url[:40]}...) (proven={is_real_pagination})", flush=True)
                    return last_sig, is_real_pagination, rpc_url

                except Exception as e:
                    print(f"[CREATOR] RPC error on {query_type} ({rpc_url[:40]}...): {e}", flush=True)
                    continue

        return None, False, "none"

    async def extract_bonding_curve_from_creation_tx(self) -> Optional[str]:
        """
        Extract the bonding curve PDA from the token's creation transaction.
        
        This is more reliable than mathematical derivation because:
        - It gets the actual bonding curve account created in the transaction
        - Works even if Pump.fun changes their seed format
        - Proven approach: fetch earliest tx, extract account from Pump.fun instruction
        
        Critical Process:
        1. Paginate through token mint's signatures to PROVEN end (not just first 1000)
        2. Fetch the earliest (creation) transaction
        3. Find Pump.fun program instruction in transaction
        4. Extract bonding curve account using heuristics:
           - Writable account
           - Non-signer
           - Not in SYSTEM_PROGRAMS
        
        Returns: bonding curve PDA address or None if not found
        """
        print(f"[CREATOR] Extracting bonding curve from creation transaction for {self.token_mint[:20]}...", flush=True)
        
        # Step 1: Get TRUE earliest signature (must paginate to proven end)
        # Use public Solana RPC for signature fetching (more reliable than QuickNode)
        earliest_sig = None
        proven = False
        
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40)) as session:
                # Use public Solana RPC specifically for signature history
                rpc_url = "https://api.mainnet-beta.solana.com"
                before = None
                pages = 0
                max_pages = 1000
                
                while pages < max_pages:
                    pages += 1
                    payload = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getSignaturesForAddress",
                        "params": [self.token_mint, {"limit": 1000, **({"before": before} if before else {})}]
                    }
                    
                    try:
                        data = await _rpc_post(session, rpc_url, payload, timeout_s=30)
                        if data is None:
                            break
                        
                        sigs = data.get("result") or []
                        
                        if not sigs:
                            # Empty result = we reached the true end
                            print(f"[CREATOR] ✅ Reached true end of mint history after {pages} pages", flush=True)
                            proven = True
                            break
                        
                        earliest_sig = sigs[-1]["signature"]  # Oldest sig in this batch
                        before = earliest_sig
                        print(f"[CREATOR] Page {pages}: {len(sigs)} signatures fetched, earliest so far: {earliest_sig[:20]}...", flush=True)
                        
                    except Exception as e:
                        print(f"[CREATOR] RPC error fetching signatures: {e}", flush=True)
                        break
                
                if pages >= max_pages:
                    print(f"[CREATOR] ⚠ Hit max_pages limit ({max_pages}) - result may not be true earliest", flush=True)
                    # If we got multiple pages, we did paginate through real data (not cache-limited)
                    # Mark as proven if pages > 1
                    proven = (pages > 1)
        
        except Exception as e:
            print(f"[CREATOR] ❌ Failed to get earliest signature: {e}", flush=True)
            return None
        
        if not earliest_sig:
            print(f"[CREATOR] ❌ No signatures found for mint", flush=True)
            return None
        
        print(f"[CREATOR] Earliest signature found (proven={proven}): {earliest_sig[:20]}...", flush=True)
        
        # Step 2: Fetch the creation transaction
        print(f"[CREATOR] Fetching creation transaction...", flush=True)
        
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [earliest_sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
        }
        
        tx_data = await self._post_rpc_with_fallback(payload, timeout=20)
        
        if not tx_data or "result" not in tx_data or not tx_data["result"]:
            print(f"[CREATOR] ❌ Transaction fetch failed", flush=True)
            return None
        
        tx = tx_data["result"]
        
        # Step 3: Normalize account keys into dicts with pubkey/signer/writable info
        message = (tx.get("transaction") or {}).get("message") or {}
        account_keys_raw = message.get("accountKeys") or []
        
        # Build normalized account list: [{"pubkey": ..., "signer": ..., "writable": ...}, ...]
        normalized_accounts = []
        for i, acct in enumerate(account_keys_raw):
            if isinstance(acct, str):
                # Plain string: account is signer and writable by default (standard format)
                normalized_accounts.append({
                    "index": i,
                    "pubkey": acct,
                    "signer": i < len([k for k in account_keys_raw if isinstance(k, dict) and k.get("signer")]) if isinstance(account_keys_raw[0], dict) else i == 0,
                    "writable": True
                })
            elif isinstance(acct, dict):
                # Dict format (jsonParsed): has pubkey, signer, writable fields
                normalized_accounts.append({
                    "index": i,
                    "pubkey": acct.get("pubkey"),
                    "signer": acct.get("signer", False),
                    "writable": acct.get("writable", False)
                })
        
        print(f"[CREATOR] Transaction has {len(normalized_accounts)} accounts", flush=True)
        
        # Step 4: Find Pump.fun instruction and extract bonding curve candidate
        instructions = message.get("instructions") or []
        inner_instructions = tx.get("meta", {}).get("innerInstructions") or []
        
        # Collect all instructions (top-level + inner)
        all_ix = list(instructions)
        for inner in inner_instructions:
            all_ix.extend(inner.get("instructions") or [])
        
        print(f"[CREATOR] Transaction has {len(all_ix)} total instructions (including inner)", flush=True)
        
        # Step 5: Search for Pump.fun instruction and extract bonding curve
        for ix_idx, ix in enumerate(all_ix):
            program_id = ix.get("programId")
            
            if program_id not in PUMPFUN_PROGRAM_IDS:
                continue
            
            print(f"[CREATOR] Found Pump.fun instruction (#{ix_idx}): {program_id}", flush=True)
            
            # Get account indexes for this instruction
            accounts = ix.get("accounts") or []
            
            if not accounts:
                print(f"[CREATOR] ⚠ Pump.fun instruction has no accounts", flush=True)
                continue
            
            print(f"[CREATOR] Instruction accounts: {accounts}", flush=True)
            
            # Resolve account indexes to actual pubkeys
            instruction_accounts = []
            for acc_idx in accounts:
                if isinstance(acc_idx, int) and 0 <= acc_idx < len(normalized_accounts):
                    acc_info = normalized_accounts[acc_idx]
                    instruction_accounts.append(acc_info)
                elif isinstance(acc_idx, str):
                    # Already a pubkey string
                    instruction_accounts.append({"pubkey": acc_idx, "signer": False, "writable": False})
            
            print(f"[CREATOR] Resolved {len(instruction_accounts)} instruction accounts", flush=True)
            
            # Find bonding curve candidate using heuristics:
            # - Writable account (state changes)
            # - Non-signer (not a transaction signer)
            # - Not in SYSTEM_PROGRAMS
            # - Typically early in the accounts list
            
            bonding_curve_candidates = []
            for acc_info in instruction_accounts:
                pubkey = acc_info.get("pubkey")
                if not pubkey:
                    continue
                
                is_writable = acc_info.get("writable", False)
                is_signer = acc_info.get("signer", False)
                is_system = pubkey in SYSTEM_PROGRAMS
                
                if is_writable and not is_signer and not is_system:
                    bonding_curve_candidates.append(pubkey)
                    print(f"[CREATOR] ✓ Bonding curve candidate: {pubkey}", flush=True)
            
            if bonding_curve_candidates:
                bonding_curve = bonding_curve_candidates[0]
                print(f"[CREATOR] ✓ Extracted Bonding Curve: {bonding_curve}", flush=True)
                return bonding_curve
        
        print(f"[CREATOR] ❌ No bonding curve found in Pump.fun instruction", flush=True)
        return None

    async def get_creator_from_earliest_tx(self) -> Optional[dict]:
        """
        Extract creator (fee payer) from earliest transaction on the bonding curve.
        Returns full provenance object proving this is the first Pump.fun create.

        CRITICAL: Extracts the bonding curve account from the creation transaction
        instead of deriving it mathematically. This is more reliable because:
        - Avoids guessing at seed parameters
        - Gets the actual account used in the creation
        - Works even if Pump.fun changes their seed format

        Process:
        1. Extract bonding curve account from token's creation transaction
        2. Query bonding curve account's transaction history (ONLY source)
        3. Get earliest transaction and extract fee payer (creator)
        4. Validate that it's a Pump.fun create event

        Provenance object includes:
        {
            'creator': 'address or None',
            'earliest_sig': signature hash,
            'reached_end': True ONLY if pagination completed to actual end,
            'rpc_used': which RPC endpoint found the signature,
            'mint_in_accounts': mint appears in tx account keys,
            'pumpfun_program_found': at least one Pump.fun program in tx,
            'is_pumpfun_create': both above are True,
            'slot': Solana slot number (for on-chain time),
            'blockTime': UNIX timestamp from block,
            'fee_payer': extracted fee payer account,
            'bonding_curve_pda': the actual bonding curve account extracted from tx,
            'query_source': 'bonding_curve' (extracted from creation tx),
            'status': 'confirmed' (all checks pass) or 'unproven' (some checks failed),
            'validation_notes': human-readable explanation
        }
        """

        # Initialize provenance tracking object
        provenance = {
            'creator': None,
            'earliest_sig': None,
            'reached_end': False,
            'rpc_used': None,
            'mint_in_accounts': False,
            'pumpfun_program_found': False,
            'is_pumpfun_create': False,
            'slot': None,
            'blockTime': None,
            'fee_payer': None,
            'bonding_curve_pda': None,
            'query_source': 'bonding_curve',
            'status': 'unproven',
            'validation_notes': []
        }

        try:
            # Step 1: Extract bonding curve account from creation transaction
            bonding_curve_pda = await self.extract_bonding_curve_from_creation_tx()
            
            if not bonding_curve_pda:
                print(f"[CREATOR] ❌ Failed to extract bonding curve from creation tx", flush=True)
                provenance['validation_notes'].append("Could not extract bonding curve from creation tx")
                return provenance
            
            provenance['bonding_curve_pda'] = bonding_curve_pda
            print(f"[CREATOR] ✓ Extracted Bonding Curve: {bonding_curve_pda}", flush=True)

            # Step 2: Query bonding curve account for earliest signature
            print(f"[CREATOR] Querying bonding curve account for earliest signature...", flush=True)
            earliest_sig, reached_end, rpc_used = await self.get_true_earliest_signature(
                bonding_curve_pda=bonding_curve_pda
            )

            if not earliest_sig:
                print(f"[CREATOR] ❌ No signatures found on bonding curve account", flush=True)
                provenance['validation_notes'].append("No signatures on bonding curve account")
                return provenance

            provenance['earliest_sig'] = earliest_sig
            provenance['reached_end'] = reached_end
            provenance['rpc_used'] = rpc_used

            if not reached_end:
                provenance['validation_notes'].append("Pagination stopped (cache-limited RPC or max_pages hit)")
                print(f"[CREATOR] ⚠ Pagination did not reach end", flush=True)

            # Step 3: Fetch the earliest transaction
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [earliest_sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
            }

            tx_data = await self._post_rpc_with_fallback(payload, timeout=10)

            if not tx_data or "result" not in tx_data or not tx_data["result"]:
                print(f"[CREATOR] ❌ Transaction not found or failed to parse", flush=True)
                provenance['validation_notes'].append("Transaction fetch failed")
                return provenance

            tx = tx_data["result"]

            # Step 4: Validate that this is a Pump.fun create event
            validation = self._validate_pumpfun_create_tx(tx)
            provenance['mint_in_accounts'] = validation['mint_in_accounts']
            provenance['pumpfun_program_found'] = validation['pumpfun_program_found']
            provenance['is_pumpfun_create'] = validation['is_pumpfun_create']
            provenance['slot'] = validation['slot']
            provenance['blockTime'] = validation['blockTime']

            # Extract signers from transaction
            message = tx.get("transaction", {}).get("message", {})
            account_keys = message.get("accountKeys", [])

            if not account_keys:
                print(f"[CREATOR] ❌ No accountKeys found in transaction", flush=True)
                provenance['validation_notes'].append("No account keys")
                return provenance

            # When using jsonParsed encoding, accountKeys is a list of objects with 'pubkey' and 'signer' fields
            KNOWN_PROGRAMS = {
                "11111111111111111111111111111111",  # System Program
                "TokenkegQfeZyiNwAJsyFbPtrKbVs73Cw6Xj2Yg5MNg",  # Token Program
                "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg",  # Pump.fun (migration processor)
                "6EF8rrecthR5DkNCG6aB2SUHbBmXoxopY6kfMDBM4mA",  # PumpSwap
            }

            # Extract actual signers from accounts
            signers = []
            for acct in account_keys:
                if isinstance(acct, dict):
                    # jsonParsed format: {"pubkey": "...", "signer": true, ...}
                    if acct.get("signer", False):
                        signers.append(acct.get("pubkey"))
                else:
                    # Fallback: assume first account is signer if not in dict format
                    signers.append(str(acct))

            if not signers:
                print(f"[CREATOR] ❌ No signers found in transaction", flush=True)
                provenance['validation_notes'].append("No signers found")
                return provenance

            print(f"[CREATOR] Found {len(signers)} signers in transaction", flush=True)

            # First signer is the fee payer (creator)
            # Skip if it's a known program
            creator = None
            for signer in signers:
                if signer not in KNOWN_PROGRAMS:
                    creator = signer
                    provenance['fee_payer'] = creator
                    print(f"[CREATOR] ✓ Found creator: {creator}", flush=True)
                    break

            # If all signers are known programs, use the first one
            if not creator and signers:
                creator = signers[0]
                provenance['fee_payer'] = creator
                print(f"[CREATOR] ⚠ All signers are known programs, using first: {creator}", flush=True)

            if creator:
                provenance['creator'] = creator
                # Determine status
                # When extracted from bonding curve: reached_end + valid tx = confirmed
                # Since we extracted bonding curve from this tx, is_pumpfun_create is our proof
                if (provenance['reached_end'] and
                    provenance['is_pumpfun_create']):
                    provenance['status'] = 'confirmed'
                    print(f"[CREATOR] ✅ CONFIRMED EARLIEST: {creator}", flush=True)
                    print(f"[CREATOR]   Source: bonding_curve (extracted from creation tx)", flush=True)
                    print(f"[CREATOR]   Reached end: {provenance['reached_end']}", flush=True)
                    print(f"[CREATOR]   Pump.fun program found: {provenance['pumpfun_program_found']}", flush=True)
                    print(f"[CREATOR]   Slot: {provenance['slot']}, BlockTime: {provenance['blockTime']}", flush=True)
                    print(f"[CREATOR]   Bonding Curve: {bonding_curve_pda}", flush=True)
                    print(f"[CREATOR]   Earliest Sig: {earliest_sig}", flush=True)
                else:
                    provenance['status'] = 'unproven'
                    if not provenance['reached_end']:
                        provenance['validation_notes'].append("pagination incomplete")
                    if not provenance['is_pumpfun_create']:
                        provenance['validation_notes'].append("transaction not a valid Pump.fun create")
                    print(f"[CREATOR] ⚠ UNPROVEN: {creator} ({', '.join(provenance['validation_notes'])})", flush=True)
            else:
                print(f"[CREATOR] ❌ No valid signers found", flush=True)
                provenance['validation_notes'].append("No valid signers")

            return provenance

        except Exception as e:
            print(f"[CREATOR] Error extracting creator: {type(e).__name__}: {str(e)}", flush=True)
            provenance['validation_notes'].append(f"Exception: {str(e)}")
            return provenance

    async def get_summary_async(self) -> Dict:
        """Get complete analysis summary with async creator lookup"""
        score = self.compute_rug_score()
        creator = await self.get_token_creator_from_das()

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
            "token_creator": creator  # NEW: Token creator from Metaplex metadata
        }
    
    def summary(self) -> Dict:
        """Get complete analysis summary (sync version - doesn't include creator)
        
        Note: Use get_summary_async() to include token creator from DAS API
        """
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
            "coverage": (self.transactions_fetched / self.signatures_requested * 100) if self.signatures_requested > 0 else 0
        }
