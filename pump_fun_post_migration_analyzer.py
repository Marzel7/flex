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

# HTTP: Use QuickNode if available, then FluxRPC, otherwise Helius, then public
RPC_URLS = [url for url in [RPC_URL, RPC_URL_2] if url]  # QuickNodes
RPC_URLS.append(FLUX_RPC_URL)  # FluxRPC fallback (reliable for pool fetching)
RPC_URLS.append(f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}" if HELIUS_API_KEY else "https://api.mainnet-beta.solana.com")  # Helius fallback
RPC_URLS.append("https://api.mainnet-beta.solana.com")  # Public fallback


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

    async def _get_earliest_signature(self, session: aiohttp.ClientSession, rpc_url: str) -> Optional[str]:
        """
        Fetch the earliest SUCCESSFUL signature for this token mint via pagination.

        Solana RPC returns signatures newest → oldest by default.
        Must paginate (using 'before' parameter) to reach the oldest signature.

        Filters out failed signatures (where err != None) to avoid decoding errors.
        Stops early when less than 1000 results are returned (end of history).
        """
        before = None
        last_sig = None
        pages = 0
        max_pages = 50  # Bump limit for busy mints

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

                async with session.post(rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    data = await resp.json()

                # Check for RPC error response
                if "error" in data:
                    raise RuntimeError(f"RPC error: {data['error']}")

                sigs = data.get("result") or []
                if not sigs:
                    # Empty page = reached the end
                    return last_sig

                # Filter to only successful signatures (err == None)
                ok_sigs = [s for s in sigs if s.get("err") is None]
                if ok_sigs:
                    last_sig = ok_sigs[-1]["signature"]

                # Paginate using the last returned sig (even if errored) to continue forward
                before = sigs[-1]["signature"]

                # Early exit: if we got fewer than 1000 results, we've reached the end
                if len(sigs) < 1000:
                    return last_sig

            return last_sig

        except Exception as e:
            print(f"[CREATOR] ⚠ Error fetching earliest signature: {e}", flush=True)
            return None

    async def get_creator_from_earliest_tx(self) -> Optional[str]:
        """
        Extract creator (fee payer) from earliest transaction on the token mint.

        Strategy:
        1. Try FluxRPC: Paginate to find truly earliest signature, extract fee payer
        2. Fallback: Use stored signers extraction from cached transaction

        Note: Fee payer is a strong heuristic but not guaranteed creator.
        Relayers and launch services may be the fee payer instead.
        """
        import os

        try:
            # Get FluxRPC URL from environment or use default
            flux_rpc = os.getenv("FLUX_RPC_URL", "").strip()
            if not flux_rpc:
                flux_rpc = "https://eu.fluxrpc.com?key=ca1a8797-c505-4c44-9918-5f832c89e91d"

            async with aiohttp.ClientSession() as session:
                # Tier 1: Paginate to find truly earliest signature
                earliest_sig = await self._get_earliest_signature(session, flux_rpc)

                if earliest_sig:
                    # Fetch the transaction with jsonParsed encoding
                    tx_payload = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getTransaction",
                        "params": [earliest_sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
                    }

                    try:
                        async with session.post(flux_rpc, json=tx_payload, timeout=aiohttp.ClientTimeout(total=30)) as tx_resp:
                            tx_data = await tx_resp.json()

                            # Check for RPC error
                            if "error" not in tx_data:
                                tx = tx_data.get("result")
                                if tx:
                                    creator = self._extract_fee_payer_from_tx(tx)
                                    if creator:
                                        print(f"[CREATOR] ✅ Earliest signature: {earliest_sig}", flush=True)
                                        print(f"[CREATOR] ✅ Creator: {creator}", flush=True)
                                        return creator
                    except Exception:
                        pass

        except Exception as flux_err:
            print(f"[CREATOR] FluxRPC error: {flux_err}", flush=True)

        # Tier 2: Fallback - use the existing signers extraction method
        try:
            # Fetch all signatures - fetch_signatures already handles RPC failover
            sigs = await self.fetch_signatures(limit=1000)

            if not sigs:
                print(f"[CREATOR] No signatures found for {self.token_mint}", flush=True)
                return None

            # Get the EARLIEST signature (oldest = last in list after pagination)
            # Note: getSignaturesForAddress returns newest first, so we take the last
            earliest_sig = sigs[-1] if sigs else None

            if not earliest_sig:
                print(f"[CREATOR] Could not determine earliest signature", flush=True)
                return None

            print(f"[CREATOR] Fallback: Fetching earliest transaction: {earliest_sig}", flush=True)

            # Fetch that transaction
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [earliest_sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
            }

            tx_data = await self._post_rpc_with_fallback(payload, timeout=10)

            if not tx_data or "result" not in tx_data or not tx_data["result"]:
                print(f"[CREATOR] Transaction not found or failed to parse", flush=True)
                return None

            tx = tx_data["result"]

            # Extract signers from transaction
            message = tx.get("transaction", {}).get("message", {})
            account_keys = message.get("accountKeys", [])

            if not account_keys:
                print(f"[CREATOR] No accountKeys found in transaction", flush=True)
                return None

            # When using jsonParsed encoding, accountKeys is a list of objects with 'pubkey' and 'signer' fields
            # Extract signers from the accounts marked with signer=True
            KNOWN_PROGRAMS = {
                "11111111111111111111111111111111",  # System Program
                "TokenkegQfeZyiNwAJsyFbPtrKbVs73Cw6Xj2Yg5MNg",  # Token Program
                "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg",  # Pump.Fun (migration processor)
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
                print(f"[CREATOR] No signers found in transaction (0 accounts marked as signer)", flush=True)
                return None

            print(f"[CREATOR] Found {len(signers)} signers in transaction", flush=True)

            # First signer is the fee payer (creator)
            # Skip if it's a known program
            creator = None
            for signer in signers:
                if signer not in KNOWN_PROGRAMS:
                    creator = signer
                    print(f"[CREATOR] ✓ Found creator: {creator}", flush=True)
                    return creator

            # If all signers are known programs, use the first one
            if signers:
                creator = signers[0]
                print(f"[CREATOR] ⚠ All signers are known programs, using first: {creator}", flush=True)
                return creator

            print(f"[CREATOR] No valid signers found", flush=True)
            return None

        except Exception as e:
            print(f"[CREATOR] Error extracting creator: {type(e).__name__}: {str(e)}", flush=True)
            return None

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
