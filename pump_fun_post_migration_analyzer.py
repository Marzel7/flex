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
import base58
import struct

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
# RPC Configuration: Use Helius for primary, Public Solana as fallback
# QuickNode removed - causes rate limiting issues
RPC_URLS = []
if HELIUS_API_KEY:
    RPC_URLS.append(f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}")
RPC_URLS.append("https://api.mainnet-beta.solana.com")  # Public Solana fallback

# History RPC: Use Helius (if available) and public Solana for reliable full-history pagination
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
    """Post to single RPC URL and return response, with 429 retry logic"""
    retry_delay = 2.0  # Start with 2 second delay
    max_retries = 5
    
    for attempt in range(max_retries):
        try:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=timeout_s)) as resp:
                if resp.status == 429:
                    # Rate limited - retry with exponential backoff
                    if attempt < max_retries - 1:
                        print(f"⚠️  RPC rate limited (429), retrying in {retry_delay}s... (attempt {attempt+1}/{max_retries})", flush=True)
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 2  # Double the delay for next retry
                        continue
                    else:
                        # Max retries exceeded
                        print(f"❌ RPC rate limited (429) after {max_retries} retries", flush=True)
                        return None
                
                if resp.status == 200:
                    data = await resp.json()
                    if "error" not in data:
                        return data
                
                # Other error status codes
                return None
                
        except asyncio.TimeoutError:
            if attempt < max_retries - 1:
                print(f"⚠️  RPC timeout, retrying in {retry_delay}s... (attempt {attempt+1}/{max_retries})", flush=True)
                await asyncio.sleep(retry_delay)
                retry_delay *= 2
                continue
            else:
                print(f"❌ RPC timeout after {max_retries} retries", flush=True)
                return None
        except Exception:
            # Other exceptions - don't retry, just fail
            return None
    
    return None


# Pump.fun Program IDs (instruction.programId / resolved programIdIndex)
# Source: Solscan Pump.fun documentation
PUMPFUN_AMM_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"  # Swap/AMM program
PUMPFUN_BONDING_CURVE_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"  # Bonding curve program

PUMPFUN_PROGRAM_IDS = {
    PUMPFUN_AMM_PROGRAM,
    PUMPFUN_BONDING_CURVE_PROGRAM,
}

# Pump.fun Accounts (addresses in accountKeys, not instruction programIds)
# Source: Solscan - this is the migration/graduation account, not a program
PUMPFUN_MIGRATION_ACCOUNT = "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg"

SYSTEM_PROGRAM = "11111111111111111111111111111111"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJsyFbPtrKbVs73Cw6Xj2Yg5MNg"
TOKEN_2022 = "TokenzQdBbjFD8aff5ZZUwWWwG6Go5rm5KWQEypdCU8"
SYSTEM_PROGRAMS = {SYSTEM_PROGRAM, TOKEN_PROGRAM, TOKEN_2022}


class PostMigrationAnalyzer:
    """Analyzes token activity on PumpSwap (post-migration)"""

    def __init__(self, token_mint: str, rpc_url: str = "https://api.mainnet-beta.solana.com"):
        # NOTE: Do NOT strip "pump" suffix blindly - "pump" are valid base58 characters
        # Only strip if it was clearly appended (e.g., "tokenaddresspump" but address is 44 chars)
        # Pump.fun tokens often have "pump" in their natural base58 encoding (e.g., ...Lpump)
        self.token_mint = token_mint
        self.rpc_url = rpc_url

        self.events = []
        self.transactions_fetched = 0
        self.signatures_requested = 0

        self.token_name = None
        self.token_symbol = None
        self.market_cap_current = None
        self.market_cap_highest = None

        # Store CREATE transaction validation for use in provenance determination
        self._create_tx_validation = None
        # Store CREATE transaction signature for persistence to database
        self._create_tx_signature = None
        # Store CREATE transaction's fee payer (true creator) for accurate provenance
        self._create_tx_creator = None

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
        """Fetch transactions asynchronously with chunked task creation to avoid memory overhead"""
        # Use semaphore for concurrency control
        sem = asyncio.Semaphore(BATCH_SIZE)
        
        async with aiohttp.ClientSession() as session:
            successful = 0
            failed = 0
            total_processed = 0
            
            # Process signatures in chunks to avoid creating millions of coroutine objects at once
            chunk_size = 5000
            
            for chunk_start in range(0, len(sigs), chunk_size):
                chunk_end = min(chunk_start + chunk_size, len(sigs))
                chunk = sigs[chunk_start:chunk_end]
                
                # Create tasks only for this chunk
                tasks = []
                for sig in chunk:
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
                    
                    total_processed += 1
                    
                    # Progress update every batch
                    if idx % BATCH_SIZE == 0 or idx == len(chunk):
                        success_rate = (successful / total_processed * 100) if total_processed > 0 else 0
                        print(f"[ASYNC] Progress: {total_processed}/{len(sigs)} txs | Success: {successful}/{total_processed} ({success_rate:.1f}%) | Failed: {failed}", flush=True)

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

            # FIXED: Index by (accountIndex, mint, owner) tuple instead of relying on zip()
            # zip() silently truncates if arrays differ in length and doesn't guarantee ordering
            pre_by_key = {}
            for pre in pre_balances:
                if pre.get("mint") != self.token_mint:
                    continue
                key = (pre.get("accountIndex"), pre.get("mint"), pre.get("owner"))
                pre_by_key[key] = int(pre.get("uiTokenAmount", {}).get("amount", 0))

            post_by_key = {}
            for post in post_balances:
                if post.get("mint") != self.token_mint:
                    continue
                key = (post.get("accountIndex"), post.get("mint"), post.get("owner"))
                post_by_key[key] = int(post.get("uiTokenAmount", {}).get("amount", 0))

            # Find all accounts where balance changed
            all_keys = set(pre_by_key.keys()) | set(post_by_key.keys())
            for key in all_keys:
                account_idx, mint, wallet = key
                pre_amount = pre_by_key.get(key, 0)
                post_amount = post_by_key.get(key, 0)
                delta = post_amount - pre_amount

                if delta == 0:
                    continue

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
        # FIXED: Changed threshold from 1e7 to 0.01
        # Normalized variance values typically range 0.01-2, not millions
        # Suspicious = very low variance (everyone buying similar amounts)
        var = self.buy_size_variance()
        if var < 0.01:
            # lower variance = more suspicious (uniform buy sizes)
            score += min(0.15, ((0.01 - var) / 0.01) * 0.15)

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

    def _is_system_create_compiled(self, ix: dict) -> bool:
        """
        Detect SystemProgram CreateAccount / CreateAccountWithSeed from compiled instruction.

        Compiled instruction has "data" as base58 string.
        First 4 bytes (little-endian u32) is the instruction discriminator:
        - 0 = CreateAccount
        - 3 = CreateAccountWithSeed
        """
        data = ix.get("data")
        if not data or not isinstance(data, str):
            return False

        try:
            raw = base58.b58decode(data)
            if len(raw) < 4:
                return False
            (tag,) = struct.unpack("<I", raw[:4])
            # 0=createAccount, 3=createAccountWithSeed
            return tag in (0, 3)
        except Exception:
            return False

    def _decode_system_create_owner_program(self, ix: dict) -> Optional[str]:
        """
        Extract the owner program ID from a compiled System.createAccount or createAccountWithSeed instruction.

        CRITICAL: Different layouts for different discriminators!

        System.createAccount (discriminator 0):
        - Bytes 0-3:   Discriminator (u32 = 0)
        - Bytes 4-11:  Lamports (u64)
        - Bytes 12-19: Space (u64)
        - Bytes 20-51: Owner program ID (32-byte Pubkey)

        System.createAccountWithSeed (discriminator 3):
        - Bytes 0-3:   Discriminator (u32 = 3)
        - Bytes 4-35:  Base pubkey (32 bytes)
        - Bytes 36-39: Seed length (u32)
        - Bytes 40+:   Seed string (variable length)
        - After seed:  Lamports (u64)
        - After lamps: Space (u64)
        - After space: Owner program ID (32-byte Pubkey)

        Must handle both correctly or risk false positives/negatives.
        """
        data = ix.get("data")
        if not data or not isinstance(data, str):
            return None

        try:
            raw = base58.b58decode(data)
            if len(raw) < 4:
                return None

            (tag,) = struct.unpack("<I", raw[:4])

            # CreateAccount (tag 0): Owner at bytes 20-51
            if tag == 0:
                if len(raw) < 4 + 8 + 8 + 32:
                    return None
                owner_bytes = raw[4 + 8 + 8 : 4 + 8 + 8 + 32]
                owner_program = base58.b58encode(owner_bytes).decode('ascii')
                return owner_program

            # CreateAccountWithSeed (tag 3): Owner after base + seed_len + seed + lamports + space
            if tag == 3:
                if len(raw) < 4 + 32 + 4:
                    return None

                offset = 4 + 32  # Skip tag and base pubkey

                # Extract seed length
                (seed_len,) = struct.unpack("<I", raw[offset:offset + 4])
                offset += 4

                # Check we have enough bytes for seed + lamports + space + owner
                if len(raw) < offset + seed_len + 8 + 8 + 32:
                    return None

                offset += seed_len  # Skip seed bytes
                offset += 8  # Skip lamports
                offset += 8  # Skip space

                # Owner is the final 32 bytes
                owner_bytes = raw[offset : offset + 32]
                owner_program = base58.b58encode(owner_bytes).decode('ascii')
                return owner_program

            # Other discriminators - not createAccount variants
            return None

        except Exception as e:
            print(f"[CREATOR] ⚠ Error decoding owner program from System instruction: {e}", flush=True)
            return None

    def _resolve_account_key(self, message: dict, idx: int) -> Optional[str]:
        """Resolve account key from accountKeys list using index."""
        keys = message.get("accountKeys") or []
        if not (0 <= idx < len(keys)):
            return None
        k = keys[idx]
        return k if isinstance(k, str) else k.get("pubkey")

    def _system_create_new_account_pubkey(self, message: dict, instr: dict) -> Optional[str]:
        """
        Extract which account was CREATED by this System.createAccount instruction.

        CRITICAL FIX: Support both parsed and compiled instruction formats!

        Parsed format (encoding=jsonParsed):
        - instr["parsed"]["info"]["newAccount"] or similar key
        - Different RPC versions use different key names (newAccount, newAccountPubkey, account, etc.)

        Compiled format:
        - instr["accounts"] is list of indices into message.accountKeys
        - accounts[1] = new account being created (index or sometimes already a pubkey string)

        Returns the pubkey of the newly created account, or None if not found.
        """
        # TRY 1: Parsed system instruction format (most common with jsonParsed encoding)
        parsed = instr.get("parsed")
        if isinstance(parsed, dict):
            info = parsed.get("info") or {}
            # Try common account key names seen across different RPC versions
            for key in ("newAccount", "newAccountPubkey", "account", "to"):
                value = info.get(key)
                if isinstance(value, str) and value:
                    return value
        
        # TRY 2: Compiled format - accounts are indices into accountKeys
        accs = instr.get("accounts")
        if isinstance(accs, list) and len(accs) >= 2:
            new_account_idx = accs[1]
            
            # Case 2a: accounts[1] is an index (typical compiled format)
            if isinstance(new_account_idx, int):
                return self._resolve_account_key(message, new_account_idx)
            
            # Case 2b: accounts[1] is already a pubkey string (some RPC versions)
            if isinstance(new_account_idx, str) and new_account_idx:
                return new_account_idx
        
        return None

    def _has_system_create_account_instruction(self, tx: dict, expected_bonding_curve: Optional[str] = None) -> bool:
        """
        Check if transaction contains System Program account creation instruction AT TOP LEVEL
        with PUMPFUN_BONDING_CURVE_PROGRAM as the owner.

        ULTRA-ROBUST CHECK:
        1. Find top-level System.createAccount instruction
        2. Verify owner program = PUMPFUN_BONDING_CURVE_PROGRAM
        3. If bonding curve is known, verify created account matches it

        Without the bonding curve check, false positives are possible if Pump.Fun creates
        other PDAs owned by the bonding curve program during later operations.

        Args:
            tx: Transaction to validate
            expected_bonding_curve: If provided, verify the created account IS this bonding curve

        Returns: True only if all conditions met
        """
        try:
            message = (tx.get("transaction") or {}).get("message") or {}
            instructions = message.get("instructions") or []

            system_program = "11111111111111111111111111111111"
            create_types = {"createaccount", "createaccountwithseed", "create"}

            for instr in instructions:  # Only check top-level instructions
                # Resolve program ID
                program_id = instr.get("programId")
                if not program_id and "programIdIndex" in instr:
                    program_id = self._resolve_account_key(message, instr.get("programIdIndex"))

                if program_id != system_program:
                    continue

                owner_program = None

                # TRY 1: Check parsed format (sometimes available)
                if "parsed" in instr:
                    parsed_type = instr.get("parsed", {}).get("type", "").lower()
                    if parsed_type in create_types:
                        # Extract owner from parsed info
                        owner_program = instr.get("parsed", {}).get("info", {}).get("owner")

                        if owner_program == PUMPFUN_BONDING_CURVE_PROGRAM:
                            # If bonding curve is known, verify this creates it
                            if expected_bonding_curve:
                                created = self._system_create_new_account_pubkey(message, instr)
                                if created != expected_bonding_curve:
                                    print(f"[CREATOR] ✗ Owner is bonding curve program, but created account {created} != expected {expected_bonding_curve}", flush=True)
                                    continue

                            print(f"[CREATOR] ✓ Found TOP-LEVEL System createAccount with Pump.Fun bonding curve owner (parsed)", flush=True)
                            return True
                        elif owner_program:
                            print(f"[CREATOR] ✗ Found System createAccount but owner is {owner_program[:16]}..., not Pump.Fun bonding curve", flush=True)
                        else:
                            # Parsed type is createaccount but owner not in parsed.info
                            # FIXED: Fall back to compiled format decoder
                            owner_program = self._decode_system_create_owner_program(instr)
                            if owner_program == PUMPFUN_BONDING_CURVE_PROGRAM:
                                if expected_bonding_curve:
                                    created = self._system_create_new_account_pubkey(message, instr)
                                    if created != expected_bonding_curve:
                                        print(f"[CREATOR] ✗ Owner is bonding curve program, but created account {created} != expected {expected_bonding_curve}", flush=True)
                                        continue

                                print(f"[CREATOR] ✓ Found TOP-LEVEL System createAccount with Pump.Fun bonding curve owner (parsed+compiled fallback)", flush=True)
                                return True
                            elif owner_program:
                                print(f"[CREATOR] ✗ Found System createAccount but owner is {owner_program[:16]}..., not Pump.Fun bonding curve", flush=True)

                # TRY 2: Decode compiled format (common)
                if self._is_system_create_compiled(instr):
                    owner_program = self._decode_system_create_owner_program(instr)

                    if owner_program == PUMPFUN_BONDING_CURVE_PROGRAM:
                        # If bonding curve is known, verify this creates it
                        if expected_bonding_curve:
                            created = self._system_create_new_account_pubkey(message, instr)
                            if created != expected_bonding_curve:
                                print(f"[CREATOR] ✗ Owner is bonding curve program, but created account {created} != expected {expected_bonding_curve}", flush=True)
                                continue

                        print(f"[CREATOR] ✓ Found TOP-LEVEL System createAccount with Pump.Fun bonding curve owner (compiled)", flush=True)
                        return True
                    elif owner_program:
                        print(f"[CREATOR] ✗ Found System createAccount but owner is {owner_program[:16]}..., not Pump.Fun bonding curve", flush=True)

            return False

        except Exception as e:
            print(f"[CREATOR] ⚠ Error checking for account creation instruction: {e}", flush=True)
            return False

    def _validate_pumpfun_create_tx(self, tx: dict) -> dict:
        """
        Validate that a transaction is actually a Pump.fun CREATE event.

        ULTRA-ROBUST VALIDATION:
        1. Mint must appear in transaction accounts
        2. Pump.fun program must be referenced
        3. System.createAccount must create the bonding curve (verified by:
           a. Owner = PUMPFUN_BONDING_CURVE_PROGRAM
           b. Created account = extracted bonding curve PDA

        Returns dict:
        {
            'is_pumpfun_create': True/False,
            'mint_in_accounts': True/False,
            'pumpfun_program_found': True/False,
            'program_ids': [list of found program IDs],
            'slot': transaction slot (for on-chain timestamp),
            'blockTime': UNIX timestamp from block,
            'bonding_curve': extracted bonding curve PDA or None
        }
        """
        result = {
            'is_pumpfun_create': False,
            'mint_in_accounts': False,
            'pumpfun_program_found': False,
            'program_ids': [],
            'slot': None,
            'blockTime': None,
            'bonding_curve': None
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

                # FIXED: Use _resolve_account_key() for programIdIndex resolution
                # This is safer and consistent with how other code handles account resolution
                if not program_id and "programIdIndex" in instr:
                    idx = instr.get("programIdIndex")
                    if isinstance(idx, int):
                        program_id = self._resolve_account_key(message, idx)

                if program_id:
                    result['program_ids'].append(program_id)
                    if program_id in PUMPFUN_PROGRAM_IDS:
                        result['pumpfun_program_found'] = True

            # Log programs found for debugging
            if result['program_ids']:
                print(f"[CREATOR] 📋 Programs found in transaction: {result['program_ids']}", flush=True)
            else:
                print(f"[CREATOR] 📋 No instructions/programs found in transaction", flush=True)

            # STEP 1: Extract bonding curve from the Pump.Fun instruction
            # This gives us the "expected" bonding curve to verify against System.createAccount
            expected_bonding_curve = self._extract_bonding_curve_from_tx(tx)
            if expected_bonding_curve:
                result['bonding_curve'] = expected_bonding_curve
                print(f"[CREATOR] ✓ Extracted expected bonding curve: {expected_bonding_curve}", flush=True)

            # STEP 2: Verify System.createAccount creates this exact bonding curve
            # This is the ultra-robust check that eliminates false positives
            has_system_create = self._has_system_create_account_instruction(tx, expected_bonding_curve)

            # FINAL VALIDATION: All three conditions must be TRUE
            # 1. Mint in accounts (ensures this is the mint's creation)
            # 2. Pump.fun program found (ensures it's a Pump.Fun tx)
            # 3. System.createAccount creates bonding curve (ensures it's CREATE, not SELL/SWAP/other)
            result['is_pumpfun_create'] = (
                result['mint_in_accounts'] and
                result['pumpfun_program_found'] and
                has_system_create
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

    async def get_true_earliest_signature(self, bonding_curve_pda: Optional[str] = None, max_pages: int = 5000, page_limit: int = 1000) -> tuple:
        """
        Find the true earliest signature using full-history RPC chain.
        
        OPTIMIZATION: If we already found the CREATE tx signature earlier,
        skip pagination and return it immediately!
        
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
        # FAST PATH: If we already found the CREATE tx AND we're NOT querying bonding_curve_pda,
        # return it immediately. Otherwise we need to query the actual account.
        if bonding_curve_pda is None and self._create_tx_signature:
            print(f"[CREATOR] 🚀 Fast path: Already have CREATE tx signature, skipping pagination", flush=True)
            return self._create_tx_signature, True, "cached"
        
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

                        # Rate limiting: Small delay between pages to avoid public RPC rate limits
                        if pages > 1:
                            await asyncio.sleep(0.1)

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

    async def extract_bonding_curve_via_helius_parse(self, create_tx_sig: str) -> Optional[str]:
        """
        FAST: Use Helius to parse the CREATE tx directly instead of paginating.
        
        IMPORTANT: Validates that parsed tx is actually a Pump.fun CREATE before extracting.
        
        If we already know the CREATE tx signature, use:
        Helius: POST /v0/transactions
        Body: {"transactions": ["sig"]}
        Response: Pre-parsed transaction with all data
        
        This is 1 API call instead of 5000 pagination requests!
        
        Args:
            create_tx_sig: The CREATE transaction signature we already found
            
        Returns: bonding curve address or None
        """
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
                    
                    # Helius returns a list of parsed transactions
                    if not isinstance(data, list) or len(data) == 0:
                        print(f"[CREATOR] ⚠ Helius parse returned empty", flush=True)
                        return None
                    
                    tx = data[0]
                    print(f"[CREATOR] ✅ Parsed CREATE tx via Helius in 1 API call", flush=True)
                    
                    # CRITICAL: Validate this is actually a Pump.fun CREATE transaction
                    validation = self._validate_pumpfun_create_tx(tx)
                    
                    if not validation['is_pumpfun_create']:
                        print(f"[CREATOR] ❌ Parsed tx failed Pump.fun validation:", flush=True)
                        print(f"    mint_in_accounts={validation['mint_in_accounts']}", flush=True)
                        print(f"    pumpfun_program_found={validation['pumpfun_program_found']}", flush=True)
                        return None
                    
                    print(f"[CREATOR] ✓ Validated: Pump.fun CREATE transaction", flush=True)
                    
                    # Extract bonding curve from validated CREATE tx
                    bonding_curve = self._extract_bonding_curve_from_tx(tx)
                    if bonding_curve:
                        print(f"[CREATOR] ✓ Extracted Bonding Curve: {bonding_curve}", flush=True)
                        self._create_tx_signature = create_tx_sig
                        if validation:
                            self._create_tx_validation = validation
                        return bonding_curve
                    else:
                        print(f"[CREATOR] ❌ Could not extract bonding curve from validated CREATE tx", flush=True)
                        return None
        
        except Exception as e:
            print(f"[CREATOR] ⚠ Helius parse error: {e}", flush=True)
            return None

    async def extract_bonding_curve_from_creation_tx(self) -> Optional[str]:
        """
        Extract the bonding curve PDA from the token's creation transaction.

        OPTIMIZATION: If we already have the CREATE tx signature, use Helius direct parse (1 API call)
        FALLBACK: Otherwise paginate through mint signatures to find it (slow but reliable)

        Returns: bonding curve PDA address or None
        """
        print(f"[CREATOR] Extracting bonding curve from creation transaction for {self.token_mint[:20]}...", flush=True)
        
        # FAST PATH: If we already found the CREATE tx signature, use Helius to parse it directly
        # This is 1 API call instead of 5000 pagination requests!
        if self._create_tx_signature and HELIUS_API_KEY:
            print(f"[CREATOR] 🚀 Fast path: Using Helius to parse CREATE tx directly", flush=True)
            bonding_curve = await self.extract_bonding_curve_via_helius_parse(self._create_tx_signature)
            if bonding_curve:
                return bonding_curve
            else:
                print(f"[CREATOR] ⚠ Helius parse failed, falling back to pagination...", flush=True)
        
        # SLOW PATH: Paginate through mint signatures looking for Pump.fun CREATE
        # This is the fallback when we don't have the CREATE tx signature yet
        print(f"[CREATOR] Paginating through mint signatures to find CREATE tx...", flush=True)
        
        earliest_create_sig = None
        earliest_create_tx = None
        earliest_create_validation = None
        pages_checked = 0
        proven_end = False
        oldest_txs_checked = 0

        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40)) as session:
                # Use best history RPC (Helius if available, else public)
                rpc_url = HISTORY_RPC_URLS[0] if HISTORY_RPC_URLS else "https://api.mainnet-beta.solana.com"
                before = None
                max_pages = 5000

                while pages_checked < max_pages:
                    pages_checked += 1

                    # Rate limiting: Small delay between pages to avoid public RPC rate limits
                    if pages_checked > 1:
                        await asyncio.sleep(0.1)

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
                            # Empty result = reached true end of history
                            print(f"[CREATOR] ✅ Reached true end of mint history after {pages_checked} pages", flush=True)
                            proven_end = True
                            break

                        # Check each signature in this page (oldest to newest, reverse order)
                        for sig_item in reversed(sigs):
                            sig = sig_item.get("signature")
                            if not sig:
                                continue

                            # Fetch and validate this transaction
                            tx_payload = {
                                "jsonrpc": "2.0",
                                "id": 1,
                                "method": "getTransaction",
                                "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
                            }

                            tx_data = await self._post_rpc_with_fallback(tx_payload, timeout=10)
                            if not tx_data or "result" not in tx_data or not tx_data["result"]:
                                continue

                            tx = tx_data["result"]

                            # Validate this is a Pump.fun CREATE transaction
                            validation = self._validate_pumpfun_create_tx(tx)

                            # Log the oldest few transactions' program IDs for debugging
                            if oldest_txs_checked < 5:
                                oldest_txs_checked += 1
                                prog_ids = validation.get("program_ids", [])
                                prog_ids_str = ", ".join(prog_ids)
                                print(f"[CREATOR] Oldest tx #{oldest_txs_checked}: {sig[:16]}... | Programs: [{prog_ids_str}]", flush=True)

                            # Debug: log validation result for this transaction
                            print(f"[CREATOR] TX Validation: mint_in_accounts={validation['mint_in_accounts']}, pumpfun_program_found={validation['pumpfun_program_found']}, is_pumpfun_create={validation['is_pumpfun_create']}", flush=True)

                            if validation['is_pumpfun_create']:
                                # Found a valid Pump.fun create!
                                print(f"[CREATOR] ✅ Found Pump.fun CREATE tx: {sig}", flush=True)
                                earliest_create_sig = sig
                                earliest_create_tx = tx
                                earliest_create_validation = validation
                                break
                        
                        # If we found a create, stop pagination
                        if earliest_create_sig:
                            break
                        
                        # Move to next page
                        if sigs:
                            before = sigs[-1]["signature"]
                            print(f"[CREATOR] Page {pages_checked}: checked {len(sigs)} sigs, no CREATE found yet", flush=True)
                        
                    except Exception as e:
                        print(f"[CREATOR] RPC error during pagination: {e}", flush=True)
                        break
                
                if pages_checked >= max_pages:
                    print(f"[CREATOR] ⚠ Hit max_pages limit ({max_pages})", flush=True)
                    proven_end = False
        
        except Exception as e:
            print(f"[CREATOR] ❌ Failed pagination: {e}", flush=True)
            return None
        
        if not earliest_create_sig or not earliest_create_tx:
            print(f"[CREATOR] ❌ No Pump.fun CREATE transaction found for mint", flush=True)
            return None

        # CRITICAL: Validate that this signature actually passed Pump.Fun CREATE validation
        # This prevents invalid signatures (FLASHX, Maestro, etc.) from being stored
        if not earliest_create_validation or not earliest_create_validation.get('is_pumpfun_create', False):
            print(f"[CREATOR] ❌ Earliest CREATE tx failed validation check - rejecting signature", flush=True)
            return None

        print(f"[CREATOR] ✓ Using creation tx (proven_end={proven_end}): {earliest_create_sig[:20]}...", flush=True)

        # Store the CREATE transaction signature and validation for persistence and provenance
        self._create_tx_signature = earliest_create_sig
        if earliest_create_validation:
            self._create_tx_validation = earliest_create_validation
            print(f"[CREATOR] ✓ Stored CREATE tx signature and validation for persistence", flush=True)

        # Extract and store the CREATE transaction's fee payer (true creator)
        message = earliest_create_tx.get("transaction", {}).get("message", {})
        account_keys = message.get("accountKeys", [])

        if account_keys:
            # Fee payer is always the first signer in the transaction
            first_key = account_keys[0]
            if isinstance(first_key, dict):
                # jsonParsed format
                fee_payer = first_key.get("pubkey")
            else:
                # Plain string format
                fee_payer = str(first_key)

            if fee_payer:
                self._create_tx_creator = fee_payer
                print(f"[CREATOR] ✓ Extracted CREATE tx fee payer (creator): {fee_payer}", flush=True)

        # Step 2: Extract bonding curve from the validated Pump.fun CREATE transaction
        tx = earliest_create_tx
        bonding_curve = self._extract_bonding_curve_from_tx(tx)
        
        if bonding_curve:
            print(f"[CREATOR] ✓ Extracted Bonding Curve: {bonding_curve}", flush=True)
            return bonding_curve
        else:
            print(f"[CREATOR] ❌ Could not extract bonding curve from CREATE tx", flush=True)
            return None

    def _extract_bonding_curve_from_tx(self, tx: dict) -> Optional[str]:
        """
        Extract bonding curve PDA from a Pump.fun CREATE transaction.
        
        CRITICAL FIX: Only extract from Pump.fun instructions that include the MINT in their accounts.
        This ensures we're looking at the CREATE instruction, not some other Pump.Fun operation.
        
        For a CREATE tx:
        1. There's a Pump.Fun instruction whose accounts include self.token_mint
        2. There's a System.createAccount that creates an account (accounts[1])
           - This can be top-level OR nested in inner instructions!
        3. That created account's OWNER PROGRAM must be PUMPFUN_BONDING_CURVE_PROGRAM
        
        Returns: bonding curve address or None
        """
        try:
            message = (tx.get("transaction") or {}).get("message") or {}
            account_keys = message.get("accountKeys") or []
            instructions = message.get("instructions") or []
            inner_instructions = tx.get("meta", {}).get("innerInstructions") or []
            
            print(f"[CREATOR] Transaction has {len(instructions)} top-level instructions", flush=True)
            
            # Step 1: Find Pump.fun CREATE instruction (must include mint in accounts)
            for ix_idx, ix in enumerate(instructions):  # Only top-level!
                # Resolve program ID
                program_id = ix.get("programId")
                if not program_id and "programIdIndex" in ix:
                    program_id_idx = ix.get("programIdIndex")
                    if isinstance(program_id_idx, int) and 0 <= program_id_idx < len(account_keys):
                        acct = account_keys[program_id_idx]
                        program_id = acct if isinstance(acct, str) else acct.get("pubkey")
                
                if program_id not in PUMPFUN_PROGRAM_IDS:
                    continue
                
                print(f"[CREATOR] Found Pump.Fun instruction (#{ix_idx}): {program_id}", flush=True)
                
                # Step 2: Extract accounts from this instruction
                accounts = ix.get("accounts")
                
                if accounts is None and "parsed" in ix:
                    # jsonParsed format
                    parsed_info = ix.get("parsed", {}).get("info", {})
                    accounts = self._extract_accounts_from_parsed_info(parsed_info)
                
                if not accounts:
                    print(f"[CREATOR] ⚠ This Pump.Fun instruction has no accounts", flush=True)
                    continue
                
                # Step 3: CRITICAL: Check if mint is in this instruction's accounts
                # Only the CREATE instruction will have the mint
                instruction_account_pubkeys = []
                for acc in accounts:
                    if isinstance(acc, int):
                        # Account index - resolve it
                        if 0 <= acc < len(account_keys):
                            acct = account_keys[acc]
                            pubkey = acct if isinstance(acct, str) else acct.get("pubkey")
                            if pubkey:
                                instruction_account_pubkeys.append(pubkey)
                    elif isinstance(acc, str):
                        instruction_account_pubkeys.append(acc)
                    elif isinstance(acc, dict) and "pubkey" in acc:
                        instruction_account_pubkeys.append(acc["pubkey"])
                
                # If mint is NOT in this instruction's accounts, this is not the CREATE
                if self.token_mint not in instruction_account_pubkeys:
                    print(f"[CREATOR] ✗ Mint not in this Pump.Fun instruction's accounts - not CREATE", flush=True)
                    continue
                
                print(f"[CREATOR] ✓ Mint found in Pump.Fun instruction - this is the CREATE!", flush=True)
                
                # Step 4: Now find which System.createAccount created the bonding curve
                # The created account should be the bonding curve PDA
                # It's created by: System.createAccount (can be top-level OR nested in inner instructions)
                # AND the owner program must be PUMPFUN_BONDING_CURVE_PROGRAM
                
                # Collect all System.createAccount instructions (both top-level and nested)
                all_system_creates = []
                
                # Check top-level instructions
                for sys_ix in instructions:
                    all_system_creates.append((sys_ix, "top-level"))
                
                # Check nested inner instructions for this instruction
                for inner_group in inner_instructions:
                    if inner_group.get("index") == ix_idx:
                        # These are inner instructions from this Pump.Fun instruction
                        for inner_ix in inner_group.get("instructions", []):
                            all_system_creates.append((inner_ix, "nested"))
                
                print(f"[CREATOR] Checking {len(all_system_creates)} System instructions (top-level + nested)", flush=True)
                
                # Now check each System instruction
                for sys_ix, location in all_system_creates:
                    # Resolve program ID for system instruction
                    sys_program_id = sys_ix.get("programId")
                    if not sys_program_id and "programIdIndex" in sys_ix:
                        sys_program_id_idx = sys_ix.get("programIdIndex")
                        if isinstance(sys_program_id_idx, int) and 0 <= sys_program_id_idx < len(account_keys):
                            acct = account_keys[sys_program_id_idx]
                            sys_program_id = acct if isinstance(acct, str) else acct.get("pubkey")
                    
                    if sys_program_id != "11111111111111111111111111111111":
                        continue
                    
                    # Check if this is a createAccount instruction
                    if not self._is_system_create_compiled(sys_ix) and "parsed" not in sys_ix:
                        continue
                    
                    parsed_type = None
                    if "parsed" in sys_ix:
                        parsed_type = sys_ix.get("parsed", {}).get("type", "").lower()
                        if parsed_type not in {"createaccount", "createaccountwithseed"}:
                            continue
                    
                    if not self._is_system_create_compiled(sys_ix) and not parsed_type:
                        continue
                    
                    # Extract the created account (accounts[1])
                    created_account = self._system_create_new_account_pubkey(message, sys_ix)
                    
                    if created_account:
                        # CRITICAL FIX: Verify the owner program is PUMPFUN_BONDING_CURVE_PROGRAM
                        owner_program = None
                        
                        # Try parsed format first
                        if "parsed" in sys_ix:
                            owner_program = sys_ix.get("parsed", {}).get("info", {}).get("owner")
                        
                        # Fall back to decoding compiled format
                        if not owner_program:
                            owner_program = self._decode_system_create_owner_program(sys_ix)
                        
                        if owner_program:
                            print(f"[CREATOR] Found System.createAccount ({location}) creating: {created_account} (owner={owner_program})", flush=True)
                            
                            # Verify owner is the bonding curve program
                            if owner_program == PUMPFUN_BONDING_CURVE_PROGRAM:
                                print(f"[CREATOR] ✓ Owner program matches PUMPFUN_BONDING_CURVE_PROGRAM!", flush=True)
                                return created_account
                            else:
                                print(f"[CREATOR] ✗ Owner program {owner_program} != {PUMPFUN_BONDING_CURVE_PROGRAM}", flush=True)
                                continue
                        else:
                            print(f"[CREATOR] ⚠ Could not extract owner program for {created_account}", flush=True)
                            continue
                
                # If no System.createAccount found, fall back to heuristic selection
                print(f"[CREATOR] ⚠ No System.createAccount with bonding curve owner found, falling back to heuristic", flush=True)
                
                known_programs = SYSTEM_PROGRAMS | PUMPFUN_PROGRAM_IDS | {
                    "So11111111111111111111111111111111111111112",  # Wrapped SOL
                    "EPjFWaLb3odcccccccccccccccccccccccccccccc",     # USDC
                    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",  # Token-2022
                    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",  # Token Program
                    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",  # Jupiter
                    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",  # ATA Program
                }
                
                candidates = []
                for i, pubkey in enumerate(instruction_account_pubkeys):
                    if pubkey == self.token_mint:
                        continue
                    if pubkey in known_programs:
                        continue
                    if pubkey.startswith("ATA"):
                        continue
                    if 0 < i < len(instruction_account_pubkeys) - 1:
                        candidates.append(pubkey)
                
                if candidates:
                    print(f"[CREATOR] → Selected bonding curve (heuristic): {candidates[0]}", flush=True)
                    return candidates[0]
            
            print(f"[CREATOR] ❌ No Pump.Fun instruction with mint found", flush=True)
            return None
            
        except Exception as e:
            print(f"[CREATOR] ⚠ Error extracting bonding curve: {e}", flush=True)
            return None

    def _extract_accounts_from_parsed_info(self, parsed_info: dict) -> Optional[list]:
        """
        Extract account list from jsonParsed instruction info.
        
        Pump.fun parsed instructions may have account info in various formats:
        - Create: has "owner", "mint", "bondingCurve", etc.
        - Other: varies by operation
        
        Returns: list of account pubkeys or None
        """
        try:
            accounts = []
            
            # Common account fields in Pump.fun parsed instructions
            account_fields = [
                "mint", "bondingCurve", "owner", "user", "creator",
                "associatedTokenProgram", "tokenProgram", "systemProgram",
                "solReceiver", "feeReceiver"
            ]
            
            for field in account_fields:
                if field in parsed_info:
                    val = parsed_info[field]
                    if isinstance(val, str):
                        accounts.append(val)
                    elif isinstance(val, dict) and "address" in val:
                        accounts.append(val["address"])
            
            return accounts if accounts else None
            
        except Exception as e:
            print(f"[CREATOR] ⚠ Error parsing instruction info: {e}", flush=True)
            return None

    async def get_creator_from_earliest_tx(self) -> Optional[dict]:
        """
        Extract CREATOR = fee payer of the Pump.fun CREATE transaction.
        Returns full provenance object with clear distinction between:
        - create_sig: The actual CREATE transaction (from mint history)
        - earliest_curve_sig: The earliest tx touching bonding curve (may be a trade)

        CRITICAL GUARDRAIL:
        ✅ Creator = fee payer of CREATE tx ONLY
        ❌ Never use earliest_curve_sig fee payer for creator (that's "who paid for earliest activity")

        Provenance tracks both separately to avoid confusion.
        """

        # Initialize provenance tracking object with explicit sig fields
        provenance = {
            'creator': None,
            'create_sig': None,  # The actual CREATE transaction signature
            'earliest_curve_sig': None,  # Earliest tx on bonding curve (may differ)
            'reached_end': False,
            'rpc_used': None,
            'mint_in_accounts': False,
            'pumpfun_program_found': False,
            'is_pumpfun_create': False,
            'slot': None,
            'blockTime': None,
            'fee_payer': None,
            'bonding_curve_pda': None,
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

            # Step 2: Get the CREATE signature (should be stored from extraction)
            if not self._create_tx_signature:
                print(f"[CREATOR] ❌ No CREATE signature stored from extraction", flush=True)
                provenance['validation_notes'].append("No CREATE signature available")
                return provenance
            
            provenance['create_sig'] = self._create_tx_signature
            print(f"[CREATOR] ✓ CREATE signature: {self._create_tx_signature[:20]}...", flush=True)

            # Step 3: Query bonding curve account for earliest signature (may be different!)
            print(f"[CREATOR] Querying bonding curve account for earliest signature...", flush=True)
            earliest_curve_sig, reached_end, rpc_used = await self.get_true_earliest_signature(
                bonding_curve_pda=bonding_curve_pda
            )

            if not earliest_curve_sig:
                print(f"[CREATOR] ❌ No signatures found on bonding curve account", flush=True)
                provenance['validation_notes'].append("No signatures on bonding curve account")
                return provenance

            provenance['earliest_curve_sig'] = earliest_curve_sig
            provenance['reached_end'] = reached_end
            provenance['rpc_used'] = rpc_used

            # Log both signatures for debugging clarity
            print(f"[CREATOR] create_sig={self._create_tx_signature[:20]}...", flush=True)
            print(f"[CREATOR] earliest_curve_sig={earliest_curve_sig[:20]}...", flush=True)
            
            if earliest_curve_sig != self._create_tx_signature:
                print(f"[CREATOR] ℹ️  Signatures differ: CREATE is one tx, earliest curve activity is another", flush=True)
            else:
                print(f"[CREATOR] ✓ Both signatures match: CREATE is the earliest curve activity", flush=True)

            if not reached_end:
                provenance['validation_notes'].append("Pagination stopped (cache-limited RPC or max_pages hit)")
                print(f"[CREATOR] ⚠ Pagination did not reach end", flush=True)

            # Step 4: Use stored CREATE tx validation (definitive proof of CREATE)
            # CRITICAL: Never use the earliest_curve_sig tx for validation
            # The CREATE is already validated when extracted
            if not self._create_tx_validation:
                print(f"[CREATOR] ❌ No CREATE tx validation stored", flush=True)
                provenance['validation_notes'].append("No CREATE tx validation available")
                return provenance

            validation = self._create_tx_validation
            print(f"[CREATOR] ✓ Using stored CREATE tx validation (definitive)", flush=True)

            # Populate validation fields
            provenance['mint_in_accounts'] = validation['mint_in_accounts']
            provenance['pumpfun_program_found'] = validation['pumpfun_program_found']
            provenance['is_pumpfun_create'] = validation['is_pumpfun_create']
            provenance['slot'] = validation['slot']
            provenance['blockTime'] = validation['blockTime']

            # Step 5: Assign creator ONLY if CREATE is confirmed
            if self._create_tx_creator and validation['is_pumpfun_create']:
                creator = self._create_tx_creator
                provenance['fee_payer'] = creator
                provenance['creator'] = creator
                print(f"[CREATOR] ✓ Creator assigned from CREATE tx fee payer: {creator}", flush=True)
            else:
                print(f"[CREATOR] ❌ Creator not assigned: CREATE not validated or no fee payer found", flush=True)
                provenance['validation_notes'].append("CREATE validation failed or fee payer missing")
                return provenance

            # Step 6: Determine status
            if provenance['reached_end'] and provenance['is_pumpfun_create']:
                provenance['status'] = 'confirmed'
                print(f"[CREATOR] ✅ CONFIRMED CREATOR: {creator}", flush=True)
                print(f"[CREATOR] ━━ VALIDATION ━━", flush=True)
                print(f"[CREATOR]   ✅ status = 'confirmed'", flush=True)
                print(f"[CREATOR]   ✅ reached_end = {provenance['reached_end']}", flush=True)
                print(f"[CREATOR]   ✅ is_pumpfun_create = {provenance['is_pumpfun_create']}", flush=True)
                print(f"[CREATOR] ━━ PROVENANCE ━━", flush=True)
                print(f"[CREATOR]   CREATE signature: {provenance['create_sig'][:20]}...", flush=True)
                print(f"[CREATOR]   Earliest curve sig: {provenance['earliest_curve_sig'][:20]}...", flush=True)
                print(f"[CREATOR]   Bonding Curve PDA: {provenance['bonding_curve_pda']}", flush=True)
                print(f"[CREATOR]   Creator: {creator}", flush=True)
            else:
                provenance['status'] = 'unproven'
                if not provenance['reached_end']:
                    provenance['validation_notes'].append("pagination incomplete")
                if not provenance['is_pumpfun_create']:
                    provenance['validation_notes'].append("transaction not a valid Pump.fun create")
                print(f"[CREATOR] ⚠ UNPROVEN: {creator} ({', '.join(provenance['validation_notes'])})", flush=True)

            return provenance

        except Exception as e:
            print(f"[CREATOR] Error extracting creator: {type(e).__name__}: {str(e)}", flush=True)
            provenance['validation_notes'].append(f"Exception: {str(e)}")
            return provenance

    async def get_summary_async(self) -> Dict:
        """
        Get complete analysis summary with dual creator lookup methods.
        
        Uses two approaches to identify creator:
        1. get_token_creator_from_das() - Metaplex metadata (quick but unreliable for Pump.fun)
        2. get_creator_from_earliest_tx() - Pump.fun CREATE signer (strong but slower)
        
        Returns provenance data showing which creator method was successful and confidence level.
        """
        score = self.compute_rug_score()
        
        # Try the strong method first (Pump.fun creation signer heuristic)
        provenance = await self.get_creator_from_earliest_tx()
        pumpfun_creator = provenance.get('creator')
        pumpfun_creator_status = provenance.get('status')
        bonding_curve_pda = provenance.get('bonding_curve_pda')
        create_sig = provenance.get('create_sig')
        earliest_curve_sig = provenance.get('earliest_curve_sig')
        
        # Fallback to metadata if strong method didn't work
        metadata_creator = None
        if not pumpfun_creator:
            metadata_creator = await self.get_token_creator_from_das()
        
        # Final creator: prefer Pump.fun (stronger) over metadata
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
            # Creator data with full provenance
            "creator": final_creator,
            "creator_provenance": {
                "pumpfun_creator": pumpfun_creator,
                "pumpfun_status": pumpfun_creator_status,
                "metadata_creator": metadata_creator,
                "bonding_curve_pda": bonding_curve_pda,
                "create_sig": create_sig,
                "earliest_curve_sig": earliest_curve_sig,
                "validation_notes": provenance.get('validation_notes', []),
                "reached_end": provenance.get('reached_end'),
                "mint_in_accounts": provenance.get('mint_in_accounts'),
                "pumpfun_program_found": provenance.get('pumpfun_program_found'),
                "is_pumpfun_create": provenance.get('is_pumpfun_create')
            }
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


# Test runner for creator extraction
async def main():
    """Test creator extraction for specific token"""
    mint = "62eNTADfQDdDygSAHeqqipaHHKvcWc4Cob1xqaYjpump"
    a = PostMigrationAnalyzer(mint)
    prov = await a.get_creator_from_earliest_tx()
    print("\nCREATOR RESULT\n", prov)
    return prov

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
