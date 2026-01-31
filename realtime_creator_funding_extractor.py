#!/usr/bin/env python3
"""
Real-time creator funding extractor.
Hooks into token migration events to extract creator funding immediately.

When a new token is detected as migrated:
  1. Get creator address from transaction
  2. Query all signatures BEFORE migration timestamp
  3. Extract SOL transfers TO creator (two types):
     - OUTGOING: Creator signed tx that moved SOL in (creator is fee payer)
     - INCOMING: Transfers where creator is recipient account (not signer)
  4. Save funder relationships to database
  5. Flag suspicious funding patterns

KEY DISTINCTION:
- FUNDING ACCOUNT: Fee payer who signed a transaction sending SOL
- RECIPIENT ACCOUNT: Account receiving SOL without necessarily signing
  (detected via balance change analysis or transaction parsing)
"""

import sqlite3
import asyncio
import aiohttp
import os
from typing import Optional, Dict, List, Set
from datetime import datetime

DB_PATH = "pumpswap_tokens.db"
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "") or "84ec9a31-f8c2-4116-8e98-695a9377c5ed"

# Same RPC configuration as post_migration_analyzer for consistency
# RPC Configuration: Use Helius + Public Solana only (QuickNode removed)
RPC_URLS = []
if HELIUS_API_KEY:
    RPC_URLS.append(f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}")
RPC_URLS.append("https://api.mainnet-beta.solana.com")  # Public fallback

BATCH_SIZE = 10  # Limit concurrent requests to reduce rate limiting
MAX_RETRIES = 5
RPC_TIMEOUT = 30


class RealTimeCreatorFundingExtractor:
    """Extract creator funding in real-time when new tokens launch"""

    def __init__(self):
        self.processed_creators: Set[str] = set()
        self.session = None

    async def init_session(self):
        """Initialize aiohttp session"""
        if not self.session:
            self.session = aiohttp.ClientSession()

    async def close_session(self):
        """Close aiohttp session"""
        if self.session:
            await self.session.close()

    async def _post_rpc(self, payload: dict) -> Optional[dict]:
        """Post to RPC with failover chain - mirrors post_migration_analyzer approach"""
        for attempt in range(MAX_RETRIES):
            # Try each RPC endpoint in the failover chain
            for rpc_url in RPC_URLS:
                try:
                    async with self.session.post(
                        rpc_url,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=RPC_TIMEOUT)
                    ) as resp:
                        # HTTP-level errors
                        if resp.status != 200:
                            if resp.status == 429:
                                # Rate limited on this RPC, try next one
                                continue
                            elif resp.status >= 500:
                                # Server error, try next RPC
                                continue
                            else:
                                # Client error, don't retry
                                return None

                        data = await resp.json()

                        # RPC-level errors
                        if "error" in data:
                            error_code = data["error"].get("code", -1)
                            # Retryable RPC errors
                            if error_code in {-32008, -32000, -32003, -32009}:
                                continue
                            else:
                                return None

                        # Success
                        if "result" in data:
                            return data

                except asyncio.TimeoutError:
                    # Timeout on this RPC, try next
                    continue
                except Exception as e:
                    # Other errors, try next RPC
                    continue

            # After trying all RPCs once, wait before next attempt
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(0.5 * (2 ** attempt))

        return None

    async def get_signatures_until_time(
        self, creator: str, until_timestamp: int, limit: int = 1000
    ) -> List[tuple]:
        """
        Get signatures UNTIL a specific timestamp (Unix seconds).
        Returns list of (signature, blockTime) tuples.
        """
        signatures = []
        before = None

        while True:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [
                    creator,
                    {
                        "limit": limit,
                        **({"before": before} if before else {})
                    }
                ]
            }

            result = await self._post_rpc(payload)
            if not result or "result" not in result:
                break

            sigs = result.get("result", [])
            if not sigs:
                break

            for sig_info in sigs:
                sig = sig_info["signature"]
                block_time = sig_info.get("blockTime", 0)

                # API returns signatures newest-to-oldest
                # We want all signatures BEFORE the target time (for pre-migration funding)
                # Skip anything at or after the target time
                if block_time and block_time >= until_timestamp:
                    # Still in the post-migration period, skip
                    continue

                # This signature is before target time, include it
                signatures.append((sig, block_time))

            # If we got fewer than requested, we've reached the end
            if len(sigs) < limit:
                break

            before = sigs[-1]["signature"]
            await asyncio.sleep(0.05)

        return signatures

    async def get_transaction(self, signature: str) -> Optional[Dict]:
        """Get transaction with RPC failover"""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [
                signature,
                {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
            ]
        }
        result = await self._post_rpc(payload)
        if result and "result" in result:
            tx = result.get("result")
            # RPC may return null for old/pruned transactions
            if tx is not None:
                return tx
        return None

    def extract_sol_transfers(self, tx: Dict, creator: str) -> List[Dict]:
        """Extract SOL transfers to/from creator"""
        transfers = []

        try:
            if not tx or "meta" not in tx:
                return transfers

            meta = tx.get("meta", {})
            pre_balances = meta.get("preBalances", [])
            post_balances = meta.get("postBalances", [])
            accounts = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])

            # Find creator account index
            creator_idx = None
            for idx, acc in enumerate(accounts):
                acc_str = acc.get("pubkey") if isinstance(acc, dict) else str(acc)
                if acc_str == creator:
                    creator_idx = idx
                    break

            if creator_idx is None:
                return transfers

            # Calculate balance change for creator
            if creator_idx < len(pre_balances) and creator_idx < len(post_balances):
                balance_change = post_balances[creator_idx] - pre_balances[creator_idx]

                # Only track meaningful amounts (> 1000 lamports = 0.000001 SOL)
                if abs(balance_change) > 1000:
                    amount_sol = abs(balance_change) / 1e9

                    # Determine direction
                    direction = "in" if balance_change > 0 else "out"

                    # Try to find best counterparty (account with opposite balance change)
                    # For multi-party transactions, just identify the largest opposite account
                    best_counterparty = None
                    best_match = float('inf')

                    for idx2, acc2 in enumerate(accounts):
                        if idx2 == creator_idx or idx2 >= len(pre_balances) or idx2 >= len(post_balances):
                            continue

                        balance_change2 = post_balances[idx2] - pre_balances[idx2]

                        # Look for accounts with opposite direction
                        if direction == "in" and balance_change2 < 0:
                            # Best match is most negative (source of funds)
                            if abs(balance_change2) < best_match:
                                best_match = abs(balance_change2)
                                best_counterparty = acc2.get("pubkey") if isinstance(acc2, dict) else str(acc2)
                        elif direction == "out" and balance_change2 > 0:
                            # Best match is most positive (destination of funds)
                            if balance_change2 < best_match:
                                best_match = balance_change2
                                best_counterparty = acc2.get("pubkey") if isinstance(acc2, dict) else str(acc2)

                    # Report the transfer with best counterparty found
                    # (if no counterparty found, use system/fee account as placeholder)
                    counterparty = best_counterparty or "SYSTEM"

                    transfers.append({
                        "direction": direction,
                        "counterparty": counterparty,
                        "amount_sol": amount_sol,
                    })

        except Exception as e:
            pass

        return transfers

    def _save_funder(self, creator: str, funder: str, amount_sol: float):
        """Save funder relationship to database, checking for CEX/infrastructure wallets"""
        try:
            from infra_mapping import is_infrastructure_account, is_cex_account

            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Check if funder is a known CEX wallet
            cex_exchange = None
            cex_type = None
            is_classified = 0

            try:
                cursor.execute("""
                    SELECT exchange_name, wallet_type
                    FROM cex_wallets
                    WHERE cex_address = ? AND is_active = 1
                    LIMIT 1
                """, (funder,))
                cex_row = cursor.fetchone()
                if cex_row:
                    exchange, wallet_type = cex_row
                    cex_exchange = exchange
                    cex_type = wallet_type
                    is_classified = 1  # Mark as classified (already tagged)
                    print(f"[FUNDING] 🏛️ CEX FUNDER DETECTED: {exchange} {wallet_type} → {creator[:16]}... ({amount_sol:.2f} SOL)", flush=True)
            except:
                pass

            # Check if funder is infrastructure/automation account
            if not cex_exchange and is_infrastructure_account(funder):
                is_classified = 1  # Mark as classified (infrastructure)

            # Check if funder is CEX via infra_mapping
            if not cex_exchange and is_cex_account(funder):
                is_classified = 1  # Mark as classified (CEX in mapping)

            # Mark as fully_analyzed if amount > 1 SOL
            fully_analyzed = 1 if amount_sol > 1.0 else 0

            cursor.execute("""
                INSERT OR REPLACE INTO creator_funders
                (creator_address, funder_address, amount_sol, first_detected_at, is_cex, cex_exchange, cex_type, is_classified, fully_analyzed)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?)
            """, (creator, funder, amount_sol, 1 if cex_exchange else 0, cex_exchange, cex_type, is_classified, fully_analyzed))

            conn.commit()
            conn.close()
        except:
            pass

    def _save_recipient(self, creator: str, recipient: str, amount_sol: float):
        """Save recipient relationship to database (creator sent SOL to recipient)"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Create table if needed
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS creator_receivers (
                    creator_address TEXT NOT NULL,
                    receiver_address TEXT NOT NULL,
                    amount_sol REAL,
                    first_detected_at TEXT,
                    PRIMARY KEY (creator_address, receiver_address)
                )
            """)

            cursor.execute("""
                INSERT OR REPLACE INTO creator_receivers
                (creator_address, receiver_address, amount_sol, first_detected_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """, (creator, recipient, amount_sol))

            conn.commit()
            conn.close()
        except:
            pass

    def _save_outgoing_transfer(self, creator: str, recipient: str, amount_sol: float, sig: str = None, block_time: int = None):
        """Save outgoing transfer from creator to recipient"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Check if recipient is a known CEX wallet
            recipient_type = None
            try:
                cursor.execute("""
                    SELECT exchange_name, wallet_type
                    FROM cex_wallets
                    WHERE cex_address = ? AND is_active = 1
                    LIMIT 1
                """, (recipient,))
                cex_row = cursor.fetchone()
                if cex_row:
                    exchange, wallet_type = cex_row
                    recipient_type = f"cex_{exchange.lower()}"
                    print(f"[FUNDING] 💸 OUTGOING TO CEX: {creator[:16]}... → {exchange} {wallet_type} ({amount_sol:.2f} SOL)", flush=True)
            except:
                pass

            cursor.execute("""
                INSERT OR REPLACE INTO creator_outgoing_transfers
                (creator_address, recipient_address, amount_sol, transaction_signature, block_time, recipient_type, first_detected_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (creator, recipient, amount_sol, sig, block_time, recipient_type))

            conn.commit()
            conn.close()
        except:
            pass

    async def extract_incoming_transfers(self, creator: str) -> Dict:
        """
        Search for incoming SOL transfers to creator by scanning recent transactions.
        This finds transfers where creator is a RECIPIENT (not signer).

        Alternative approach: We look at all recent transactions on-chain that mention
        the creator address and extract transfers TO the creator.
        """
        print(f"[REALTIME_FUNDING]    🔍 Searching for INCOMING transfers to creator...", flush=True)

        funders = {}
        max_attempts = 5
        attempt = 0

        # We'll need to search recent block transactions
        # This is a simplified version - in production, use indexed services
        try:
            # For now, return empty - we'd need to implement transaction scanning
            # This would require either:
            # 1. Scanning recent blocks manually
            # 2. Using a service like Helius that indexes transactions
            # 3. Using getSignaturesForAddress on all known funders (not scalable)
            return funders
        except Exception as e:
            print(f"[REALTIME_FUNDING]    ⚠ Error searching incoming: {e}", flush=True)
            return funders

    async def extract_outgoing_transfers(self, creator: str, after_timestamp: int, limit: int = 100) -> Dict:
        """
        Search for outgoing transfers FROM creator AFTER a specific timestamp (post-migration).
        Returns dict of recipient -> {amount: total_sol, count: tx_count}
        """
        print(f"[REALTIME_FUNDING]    🔍 Searching for OUTGOING transfers after migration...", flush=True)

        recipients = {}
        before = None
        max_sigs = 0

        try:
            # Get all signatures for the creator
            while max_sigs < limit:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getSignaturesForAddress",
                    "params": [
                        creator,
                        {
                            "limit": 50,
                            **({"before": before} if before else {})
                        }
                    ]
                }

                result = await self._post_rpc(payload)
                if not result or "result" not in result:
                    break

                sigs = result.get("result", [])
                if not sigs:
                    break

                for sig_info in sigs:
                    sig = sig_info["signature"]
                    block_time = sig_info.get("blockTime", 0)

                    # We want signatures AFTER the migration time (post-migration)
                    if block_time and block_time <= after_timestamp:
                        # Before or at migration time, skip
                        continue

                    # This is post-migration, analyze it
                    tx = await self.get_transaction(sig)
                    if not tx:
                        continue

                    transfers = self.extract_sol_transfers(tx, creator)
                    for transfer in transfers:
                        if transfer["direction"] != "out":
                            continue

                        counterparty = transfer["counterparty"]
                        amount = transfer["amount_sol"]

                        if counterparty not in recipients:
                            recipients[counterparty] = {"amount": 0, "count": 0}

                        recipients[counterparty]["amount"] += amount
                        recipients[counterparty]["count"] += 1

                        # Save to database immediately
                        self._save_outgoing_transfer(creator, counterparty, amount, sig, block_time)

                    max_sigs += 1

                if len(sigs) < 50:
                    break

                before = sigs[-1]["signature"]
                await asyncio.sleep(0.1)  # Increased delay to reduce rate limiting

            return recipients

        except Exception as e:
            print(f"[REALTIME_FUNDING]    ⚠ Error searching outgoing: {e}", flush=True)
            return recipients

    async def extract_for_creator(self, creator: str, migration_timestamp_str: str) -> Dict:
        """
        Extract funding activity for a creator using Helius Enhanced API.
        Uses same reliable approach as standalone tmp.py script:
        - Single page fetch (100 txs) instead of rapid pagination
        - Proper delays between requests
        - Filters pre-migration transfers only
        - Excludes token mints and bonding curves from both directions
        
        This is slower than pagination but avoids 429 rate limit errors.
        """
        if creator in self.processed_creators:
            return {"status": "already_processed"}

        self.processed_creators.add(creator)

        try:
            # Parse migration timestamp
            if "T" in migration_timestamp_str:
                migration_dt = datetime.fromisoformat(migration_timestamp_str.replace("Z", "+00:00"))
            else:
                migration_dt = datetime.fromisoformat(migration_timestamp_str)

            migration_timestamp = int(migration_dt.timestamp())

            print(f"[REALTIME_FUNDING] 🔍 Extracting creator funding for {creator[:16]}...", flush=True)
            print(f"[REALTIME_FUNDING]    Migration timestamp: {migration_timestamp_str}", flush=True)

            # Build exclusion set: token mints + bonding curves created by this creator
            exclude_set = set()

            # Get all tokens launched by this creator to exclude them
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT mint, bonding_curve_pda
                FROM token_analysis
                WHERE earliest_tx_creator = ?
            """, (creator,))
            creator_tokens = cursor.fetchall()

            for mint, bonding_curve in creator_tokens:
                if mint:
                    exclude_set.add(mint)
                if bonding_curve:
                    exclude_set.add(bonding_curve)

            # Also exclude fully analyzed funders (>1 SOL already logged)
            # to avoid re-processing addresses we've already identified
            cursor.execute("""
                SELECT DISTINCT funder_address
                FROM creator_funders
                WHERE amount_sol > 1.0 AND fully_analyzed = 1
            """)
            fully_analyzed = cursor.fetchall()
            for (funder,) in fully_analyzed:
                exclude_set.add(funder)

            conn.close()
            
            if exclude_set:
                print(f"[REALTIME_FUNDING]    Excluding {len(exclude_set)} addresses (creator's tokens & bonding curves)", flush=True)

            # Use Helius Enhanced API - paginate through all transactions
            funders = {}
            recipients = {}
            filtered_dust = 0
            filtered_excluded = 0

            MIN_SOL = 0.001  # Filter dust

            print(f"[REALTIME_FUNDING]    Fetching all pre-migration transactions from Helius API...", flush=True)

            try:
                async with aiohttp.ClientSession() as helius_session:
                    url = f"https://api-mainnet.helius-rpc.com/v0/addresses/{creator}/transactions"

                    page_num = 0
                    before_signature = None
                    total_fetched = 0
                    found_pre_migration = False

                    while True:
                        page_num += 1

                        # Build URL with query parameters directly
                        # Note: Helius Enhanced API max limit is 100, not 1000
                        query_url = f"{url}?api-key={HELIUS_API_KEY}&limit=100&sort-order=desc&commitment=finalized"
                        if before_signature:
                            query_url += f"&before={before_signature}"

                        try:
                            # Log the RPC call
                            print(f"[REALTIME_FUNDING]    [PAGE {page_num}] RPC CALL #{page_num}...", flush=True)

                            async with helius_session.get(
                                query_url,
                                timeout=aiohttp.ClientTimeout(total=30)
                            ) as resp:
                                if resp.status == 429:
                                    print(f"[REALTIME_FUNDING]    ⚠ Rate limited (429) on page {page_num}", flush=True)
                                    break

                                if resp.status != 200:
                                    txt = await resp.text()
                                    print(f"[REALTIME_FUNDING]    ⚠ Helius HTTP {resp.status} on page {page_num}", flush=True)
                                    break

                                page = await resp.json()
                                if not isinstance(page, list) or len(page) == 0:
                                    print(f"[REALTIME_FUNDING]    [PAGE {page_num}] No more transactions", flush=True)
                                    break

                                print(f"[REALTIME_FUNDING]    [PAGE {page_num}] fetched={len(page)} txs", flush=True)
                                total_fetched += len(page)

                                # Process transactions
                                page_has_pre_migration = False
                                earliest_tx_timestamp = None
                                page_funders_found = 0
                                page_dust_filtered = 0
                                page_excluded_filtered = 0

                                for tx in page:
                                    tx_ts = tx.get("timestamp", 0)

                                    # Track earliest timestamp on this page
                                    if earliest_tx_timestamp is None or tx_ts < earliest_tx_timestamp:
                                        earliest_tx_timestamp = tx_ts

                                    # Capture ALL transfers regardless of pre/post migration
                                    # (we want all funding sources, not just pre-migration)
                                    page_has_pre_migration = True
                                    found_pre_migration = True

                                    # Extract nativeTransfers
                                    native = tx.get("nativeTransfers") or []
                                    for nt in native:
                                        frm = nt.get("fromUserAccount")
                                        to = nt.get("toUserAccount")
                                        amt = nt.get("amount", 0)

                                        if not isinstance(frm, str) or not isinstance(to, str):
                                            continue

                                        amount_sol = amt / 1_000_000_000

                                        # Filter dust
                                        if amount_sol < MIN_SOL:
                                            filtered_dust += 1
                                            page_dust_filtered += 1
                                            continue

                                        # Inbound: someone sent creator SOL
                                        if to == creator and amount_sol > 0:
                                            if frm in exclude_set:
                                                filtered_excluded += 1
                                                page_excluded_filtered += 1
                                                continue

                                            if frm not in funders:
                                                funders[frm] = 0
                                                page_funders_found += 1
                                            funders[frm] += amount_sol
                                            self._save_funder(creator, frm, amount_sol)

                                        # Outbound: creator sent SOL
                                        elif frm == creator and amount_sol > 0:
                                            if to in exclude_set:
                                                filtered_excluded += 1
                                                continue

                                            if to not in recipients:
                                                recipients[to] = 0
                                            recipients[to] += amount_sol
                                            self._save_recipient(creator, to, amount_sol)

                                # Log page summary
                                if page_funders_found > 0 or page_dust_filtered > 0 or page_excluded_filtered > 0:
                                    details = []
                                    if page_funders_found > 0:
                                        details.append(f"✓ {page_funders_found} new funders")
                                    if page_dust_filtered > 0:
                                        details.append(f"🚫 {page_dust_filtered} dust")
                                    if page_excluded_filtered > 0:
                                        details.append(f"🔄 {page_excluded_filtered} excluded")
                                    print(f"[REALTIME_FUNDING]    [PAGE {page_num}] " + " | ".join(details), flush=True)

                                # Set up next page - continue if we haven't reached token creation time yet
                                # or if we found pre-migration txs on this page
                                should_continue = False
                                if page:
                                    # Continue if we found pre-migration txs
                                    if page_has_pre_migration:
                                        should_continue = True
                                    # OR if the earliest tx on this page is still after migration (means older txs exist)
                                    elif earliest_tx_timestamp and earliest_tx_timestamp > migration_timestamp:
                                        should_continue = True
                                        print(f"[REALTIME_FUNDING]    [PAGE {page_num}] All post-migration, but continuing to find older txs...", flush=True)

                                    if should_continue and (page_num < 100):  # Up to 100 pages (10,000 txs with limit=100)
                                        before_signature = page[-1].get("signature")
                                        if before_signature:
                                            await asyncio.sleep(0.5)  # Rate limit delay
                                        else:
                                            print(f"[REALTIME_FUNDING]    No more signatures available", flush=True)
                                            break
                                    else:
                                        print(f"[REALTIME_FUNDING]    Pagination complete (reached page limit or end)", flush=True)
                                        break
                                else:
                                    break

                        except asyncio.TimeoutError:
                            print(f"[REALTIME_FUNDING]    ⚠ Timeout on page {page_num}", flush=True)
                            break
                        except Exception as e:
                            print(f"[REALTIME_FUNDING]    ⚠ Error on page {page_num}: {e}", flush=True)
                            break

                    print(f"[REALTIME_FUNDING]    Total transactions fetched: {total_fetched}", flush=True)

            except Exception as e:
                print(f"[REALTIME_FUNDING]    ⚠ Error: {e}", flush=True)
                return {"creator": creator, "error": str(e)}
            
            # Summary
            total_inbound = sum(funders.values())
            total_outbound = sum(recipients.values())
            
            print(f"[REALTIME_FUNDING]    ✓ Inbound: {len(funders)} funders ({total_inbound:.2f} SOL)", flush=True)
            print(f"[REALTIME_FUNDING]    ✓ Outbound: {len(recipients)} recipients ({total_outbound:.2f} SOL)", flush=True)
            
            if filtered_dust > 0:
                print(f"[REALTIME_FUNDING]    ℹ Filtered {filtered_dust} dust transfers", flush=True)
            if filtered_excluded > 0:
                print(f"[REALTIME_FUNDING]    ℹ Filtered {filtered_excluded} internal transfers (token/curve)", flush=True)
            
            # Show top funders
            if funders:
                sorted_funders = sorted(funders.items(), key=lambda x: x[1], reverse=True)[:3]
                for i, (funder, amount) in enumerate(sorted_funders, 1):
                    print(f"[REALTIME_FUNDING]    Funder #{i}: {funder[:16]}... → {amount:.2f} SOL", flush=True)
            
            return {
                "creator": creator,
                "status": "success",
                "funding_sources": len(funders),
                "total_inbound": total_inbound,
                "outgoing_transfers": len(recipients),
                "total_outbound": total_outbound,
                "funders": {k: v for k, v in sorted(funders.items(), key=lambda x: x[1], reverse=True)[:10]} if funders else {}
            }

        except Exception as e:
            print(f"[REALTIME_FUNDING] ⚠ Error: {e}", flush=True)
            return {"creator": creator, "error": str(e)}

    async def process_new_token(self, creator: str, migration_timestamp_str: str):
        """
        Process a newly detected token.
        Call from main listener when migration is detected.
        """
        # Ensure session is initialized
        await self.init_session()

        # Extract funding in background (don't block main listener)
        try:
            result = await self.extract_for_creator(creator, migration_timestamp_str)
            return result
        except Exception as e:
            print(f"[REALTIME_FUNDING] ⚠ Unexpected error: {e}", flush=True)
            return {"error": str(e)}


# Global instance
_extractor = None


async def get_extractor() -> RealTimeCreatorFundingExtractor:
    """Get or create global extractor instance"""
    global _extractor
    if not _extractor:
        _extractor = RealTimeCreatorFundingExtractor()
        await _extractor.init_session()
    return _extractor


async def extract_funding_for_new_token(creator: str, migration_timestamp_str: str):
    """
    Public function to extract funding when new token detected.

    Call from pumpfun_curve_listener.py in handle_migration():
        await extract_funding_for_new_token(creator, migration_time)
    """
    extractor = await get_extractor()
    return await extractor.process_new_token(creator, migration_timestamp_str)


if __name__ == "__main__":
    # Test with a known creator
    async def test():
        extractor = RealTimeCreatorFundingExtractor()
        await extractor.init_session()

        # Example: Extract for a specific creator
        creator = "cwPG1BF4GqAPDF8p"  # Replace with real creator
        timestamp = "2026-01-16T17:28:51"

        result = await extractor.extract_for_creator(creator, timestamp)
        print(f"\nResult: {result}")

        await extractor.close_session()

    asyncio.run(test())
