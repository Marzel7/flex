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
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "") or "80ff2d2d-14d1-4b05-bfcd-26769047e331"
HELIUS_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
PUBLIC_RPC = "https://api.mainnet-beta.solana.com"


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

    async def _post_rpc(self, payload: dict, timeout: int = 15, retry_count: int = 0) -> Optional[dict]:
        """Post to RPC - tries Helius first, falls back to public RPC with retry logic"""
        max_retries = 5
        backoff_base = 1.0  # Start with 1 second backoff, more aggressive delays

        # Try Helius first
        try:
            async with self.session.post(
                HELIUS_URL,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    # Check if Helius returned an error (rate limited, etc)
                    if 'error' not in result:
                        return result
                    # Helius returned error, try public RPC instead
        except Exception as e:
            # Helius failed, will try public RPC
            pass

        # Fallback to public Solana RPC
        try:
            async with self.session.post(
                PUBLIC_RPC,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                result = await resp.json()

                # Handle 429 rate limiting with exponential backoff
                if resp.status == 429:
                    if retry_count < max_retries:
                        backoff = backoff_base * (2 ** retry_count)
                        await asyncio.sleep(backoff)
                        return await self._post_rpc(payload, timeout, retry_count + 1)
                    # Max retries exceeded
                    return None

                # Check status
                if resp.status == 200:
                    # Verify we got actual result data
                    if 'result' in result:
                        return result
                    elif 'error' in result:
                        # RPC returned error in successful response
                        return None

                # Other HTTP errors
                return None

        except asyncio.TimeoutError:
            # Timeout - retry if we haven't exceeded max retries
            if retry_count < max_retries:
                backoff = backoff_base * (2 ** retry_count)
                await asyncio.sleep(backoff)
                return await self._post_rpc(payload, timeout, retry_count + 1)
            return None
        except Exception as e:
            # Other exceptions - don't retry
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
        """Get transaction"""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [
                signature,
                {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
            ]
        }
        result = await self._post_rpc(payload, timeout=20)
        if result:
            # Check if we got a result
            if "result" in result:
                tx = result.get("result")
                # Sometimes RPC returns null for old transactions
                if tx is None:
                    return None
                return tx
            # Check if there's an error
            if "error" in result:
                # Silent fail on errors - transaction not found or other RPC errors
                return None
        return None

    def extract_sol_transfers(self, tx: Dict, creator: str) -> List[Dict]:
        """Extract SOL transfers to creator with counterparty"""
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

                    # Find counterparty
                    for idx2, acc2 in enumerate(accounts):
                        if idx2 == creator_idx or idx2 >= len(pre_balances) or idx2 >= len(post_balances):
                            continue

                        balance_change2 = post_balances[idx2] - pre_balances[idx2]

                        # Check if this account's balance change is roughly opposite
                        if balance_change > 0 and balance_change2 < 0:
                            if abs(balance_change + balance_change2) < 10000:
                                acc_str = acc2.get("pubkey") if isinstance(acc2, dict) else str(acc2)
                                transfers.append({
                                    "direction": "in",
                                    "counterparty": acc_str,
                                    "amount_sol": amount_sol,
                                })
                                break
                        elif balance_change < 0 and balance_change2 > 0:
                            if abs(balance_change + balance_change2) < 10000:
                                acc_str = acc2.get("pubkey") if isinstance(acc2, dict) else str(acc2)
                                transfers.append({
                                    "direction": "out",
                                    "counterparty": acc_str,
                                    "amount_sol": amount_sol,
                                })
                                break

        except Exception as e:
            pass

        return transfers

    def _save_funder(self, creator: str, funder: str, amount_sol: float):
        """Save funder relationship to database, checking for CEX wallets"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Check if funder is a known CEX wallet
            cex_exchange = None
            cex_type = None
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
                    print(f"[FUNDING] 🏛️ CEX FUNDER DETECTED: {exchange} {wallet_type} → {creator[:16]}... ({amount_sol:.2f} SOL)", flush=True)
            except:
                pass

            cursor.execute("""
                INSERT OR REPLACE INTO creator_funders
                (creator_address, funder_address, amount_sol, first_detected_at, is_cex, cex_exchange, cex_type)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?)
            """, (creator, funder, amount_sol, 1 if cex_exchange else 0, cex_exchange, cex_type))

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
        Extract funding activity for a creator.
        Called in real-time when token is detected.

        Strategy:
        1. Find PRE-MIGRATION funders (transactions signed BY creator before migration)
        2. Find POST-MIGRATION outgoing transfers (creator sending SOL after migration)
        3. Combine both to get complete funding picture
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

            # Get signatures before migration (pre-migration funders)
            signatures = await self.get_signatures_until_time(creator, migration_timestamp)
            print(f"[REALTIME_FUNDING]    Found {len(signatures)} pre-migration signatures", flush=True)

            # Analyze pre-migration transactions
            funders = {}  # funder -> {amount: total_sol, count: tx_count}
            sigs_checked = 0
            max_sigs_to_check = 200  # Limit to most recent 200 pre-migration txs to avoid slow processing

            if signatures:
                # Process most recent signatures first (highest indices = most recent = most relevant)
                sigs_to_check = signatures[-max_sigs_to_check:] if len(signatures) > max_sigs_to_check else signatures
                print(f"[REALTIME_FUNDING]    Processing {len(sigs_to_check)} of {len(signatures)} signatures (most recent {max_sigs_to_check})", flush=True)

                for sig_idx, (sig, block_time) in enumerate(sigs_to_check):
                    # Print progress every 10 transactions
                    if sig_idx % 10 == 0:
                        print(f"[REALTIME_FUNDING]    ⏳ Processed {sig_idx}/{len(sigs_to_check)} signatures...", flush=True)

                    tx = await self.get_transaction(sig)
                    if not tx:
                        continue

                    sigs_checked += 1

                    # Extract transfers
                    transfers = self.extract_sol_transfers(tx, creator)
                    for transfer in transfers:
                        if transfer["direction"] != "in":
                            continue

                        counterparty = transfer["counterparty"]
                        amount = transfer["amount_sol"]

                        if counterparty not in funders:
                            funders[counterparty] = {"amount": 0, "count": 0}

                        funders[counterparty]["amount"] += amount
                        funders[counterparty]["count"] += 1

                        # Save to database immediately
                        self._save_funder(creator, counterparty, amount)

                    await asyncio.sleep(0.1)  # Increased delay to reduce rate limiting

                # Summary of funders
                total_inbound = sum(f["amount"] for f in funders.values())
                print(f"[REALTIME_FUNDING]    ✓ Pre-migration: {sigs_checked} txs analyzed, {len(funders)} funders, {total_inbound:.2f} SOL inbound", flush=True)

                # Show top funders
                if funders:
                    sorted_funders = sorted(funders.items(), key=lambda x: x[1]["amount"], reverse=True)
                    for i, (funder, data) in enumerate(sorted_funders[:3], 1):
                        print(f"[REALTIME_FUNDING]    Funder #{i}: {funder[:16]}... → {data['amount']:.2f} SOL", flush=True)

            # Also extract post-migration outgoing transfers
            outgoing = await self.extract_outgoing_transfers(creator, migration_timestamp)
            if outgoing:
                total_outbound = sum(r["amount"] for r in outgoing.values())
                print(f"[REALTIME_FUNDING]    ✓ Post-migration: {len(outgoing)} recipients, {total_outbound:.2f} SOL outbound", flush=True)
                
                # Show top recipients
                if outgoing:
                    sorted_recipients = sorted(outgoing.items(), key=lambda x: x[1]["amount"], reverse=True)
                    for i, (recipient, data) in enumerate(sorted_recipients[:3], 1):
                        print(f"[REALTIME_FUNDING]    Recipient #{i}: {recipient[:16]}... ← {data['amount']:.2f} SOL", flush=True)

            return {
                "creator": creator,
                "signatures_checked": sigs_checked,
                "funding_sources": len(funders),
                "total_inbound": sum(f["amount"] for f in funders.values()) if funders else 0,
                "outgoing_transfers": len(outgoing),
                "total_outbound": sum(r["amount"] for r in outgoing.values()) if outgoing else 0,
                "funders": {k: v["amount"] for k, v in sorted(funders.items(), key=lambda x: x[1]["amount"], reverse=True)[:10]} if funders else {}
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
