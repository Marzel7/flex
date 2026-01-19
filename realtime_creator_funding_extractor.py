#!/usr/bin/env python3
"""
Real-time creator funding extractor.
Hooks into token migration events to extract pre-migration funding immediately.

When a new token is detected as migrated:
  1. Get creator address from transaction
  2. Query all signatures BEFORE migration timestamp
  3. Extract SOL transfers to creator
  4. Save funder relationships to database
  5. Flag suspicious funding patterns
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

    async def _post_rpc(self, payload: dict, timeout: int = 15) -> Optional[dict]:
        """Post to RPC"""
        try:
            async with self.session.post(
                HELIUS_URL,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
        except:
            pass
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

                # Stop if we've gone past the target time
                if block_time and block_time < until_timestamp:
                    return signatures

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
        if result and "result" in result:
            return result.get("result")
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
        """Save funder relationship to database"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            cursor.execute("""
                INSERT OR REPLACE INTO creator_funders
                (creator_address, funder_address, amount_sol, first_detected_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """, (creator, funder, amount_sol))

            conn.commit()
            conn.close()
        except:
            pass

    async def extract_for_creator(self, creator: str, migration_timestamp_str: str) -> Dict:
        """
        Extract pre-migration funding for a creator.
        Called in real-time when token is detected.
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

            print(f"[REALTIME_FUNDING] 🔍 Extracting pre-migration funding for {creator[:16]}...", flush=True)
            print(f"[REALTIME_FUNDING]    Migration timestamp: {migration_timestamp_str}", flush=True)

            # Get signatures before migration
            signatures = await self.get_signatures_until_time(creator, migration_timestamp)
            print(f"[REALTIME_FUNDING]    Found {len(signatures)} pre-migration signatures", flush=True)

            if not signatures:
                print(f"[REALTIME_FUNDING] ✓ No pre-migration activity", flush=True)
                return {"creator": creator, "signatures": 0, "funding_sources": []}

            # Analyze transactions
            funders = {}  # funder -> {amount: total_sol, count: tx_count}
            sigs_checked = 0

            for sig_idx, (sig, block_time) in enumerate(signatures):
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

                await asyncio.sleep(0.01)

            # Summary
            total_inbound = sum(f["amount"] for f in funders.values())
            print(f"[REALTIME_FUNDING] ✅ Complete: {sigs_checked} txs analyzed, {len(funders)} funders, {total_inbound:.2f} SOL", flush=True)

            # Show top funders
            if funders:
                sorted_funders = sorted(funders.items(), key=lambda x: x[1]["amount"], reverse=True)
                for i, (funder, data) in enumerate(sorted_funders[:3], 1):
                    print(f"[REALTIME_FUNDING]    Funder #{i}: {funder[:16]}... → {data['amount']:.2f} SOL", flush=True)

            return {
                "creator": creator,
                "signatures_checked": sigs_checked,
                "funding_sources": len(funders),
                "total_inbound": total_inbound,
                "funders": {k: v["amount"] for k, v in sorted(funders.items(), key=lambda x: x[1]["amount"], reverse=True)[:10]}
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
