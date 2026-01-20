#!/usr/bin/env python3
"""
FIXED: Extract creator funding sources correctly.

The issue with previous scripts:
1. getSignaturesForAddress returns only transactions the account signed
2. Direct SOL transfers TO an account might not show if account is just a recipient
3. Need to check ALL account changes, not just transactions the account signed

Solution:
- Get creator's transaction history
- For EACH transaction, check if creator received SOL
- Even if creator didn't sign it, if their balance increased, someone funded them
"""

import sqlite3
import asyncio
import aiohttp
import os
from typing import Optional, Dict, List
from datetime import datetime

DB_PATH = "pumpswap_tokens.db"

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
RPC_URL = os.getenv("RPC_URL", "")
RPC_URL_2 = os.getenv("RPC_URL_2", "")

RPC_URLS = [url for url in [RPC_URL, RPC_URL_2] if url]
if HELIUS_API_KEY:
    RPC_URLS.append(f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}")
RPC_URLS.append("https://api.mainnet-beta.solana.com")


class CreatorFundingFixedExtractor:
    """Extract creator funding with correct balance tracking"""

    def __init__(self):
        self.rpc_index = 0

    async def _post_rpc(self, payload: dict, timeout: int = 10) -> Optional[dict]:
        """Post to RPC with failover"""
        try:
            async with aiohttp.ClientSession() as session:
                for rpc_url in RPC_URLS:
                    try:
                        async with session.post(
                            rpc_url,
                            json=payload,
                            timeout=aiohttp.ClientTimeout(total=timeout)
                        ) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                if "result" in data and data["result"] is not None:
                                    return data
                    except Exception:
                        continue
                return None
        except Exception as e:
            print(f"[RPC_ERROR] {e}")
            return None

    async def get_creator_transactions(self, creator: str, limit: int = 100) -> List[str]:
        """Get all transactions for creator"""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [creator, {"limit": limit}]
            }

            result = await self._post_rpc(payload)
            if not result or "result" not in result:
                return []

            sigs_data = result.get("result")
            if not sigs_data:
                return []

            signatures = [tx["signature"] for tx in sigs_data if "signature" in tx]
            return signatures

        except Exception as e:
            print(f"[ERROR] Getting transactions for {creator[:8]}...: {e}")
            return []

    async def check_inbound_sol(self, creator: str, signature: str) -> Dict[str, float]:
        """Check if this transaction sent SOL TO the creator"""
        funders = {}

        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [signature, {"encoding": "jsonParsed"}]
            }

            result = await self._post_rpc(payload)
            if not result or "result" not in result:
                return funders

            tx = result["result"]
            if not tx or not tx.get("transaction"):
                return funders

            meta = tx.get("meta")
            if not meta:
                return funders

            # Get all accounts and their balance changes
            pre_balances = meta.get("preBalances", [])
            post_balances = meta.get("postBalances", [])
            accounts = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])

            if not accounts or len(accounts) == 0:
                return funders

            # Find creator's index
            creator_idx = None
            for idx, acc in enumerate(accounts):
                acc_addr = acc if isinstance(acc, str) else acc.get("pubkey", "")
                if acc_addr == creator:
                    creator_idx = idx
                    break

            if creator_idx is None:
                return funders

            if creator_idx >= len(pre_balances) or creator_idx >= len(post_balances):
                return funders

            # Check creator's balance change
            creator_pre = pre_balances[creator_idx]
            creator_post = post_balances[creator_idx]
            creator_change = creator_post - creator_pre

            # If creator received SOL (balance increased)
            if creator_change > 0:
                # Find who sent it
                # Look for accounts with matching negative balance change
                for idx, acc in enumerate(accounts):
                    if idx >= len(pre_balances) or idx >= len(post_balances):
                        continue

                    acc_addr = acc if isinstance(acc, str) else acc.get("pubkey", "")
                    if acc_addr == creator:
                        continue

                    pre_bal = pre_balances[idx]
                    post_bal = post_balances[idx]
                    acc_change = post_bal - pre_bal

                    # Account sent SOL (negative change)
                    if acc_change < 0:
                        # Check if this matches the creator's increase (accounting for fee)
                        abs_change = abs(acc_change)
                        if abs_change >= creator_change - 10000:  # Allow for fee variance
                            amount_sol = abs_change / 1e9
                            if amount_sol > 0:
                                if acc_addr not in funders:
                                    funders[acc_addr] = 0
                                funders[acc_addr] += amount_sol

        except Exception as e:
            print(f"[TX_ERROR] Processing {signature[:16]}...: {e}")

        return funders

    async def process_creator(self, creator: str, idx: int, total: int) -> Dict:
        """Process one creator"""
        creator_short = f"{creator[:8]}...{creator[-4:]}"
        print(f"\n[{idx}/{total}] {creator_short}", end=" ")

        # Get all transactions
        signatures = await self.get_creator_transactions(creator, limit=100)
        print(f"({len(signatures)} txs)", end=" ")

        all_funders = {}
        funding_txs = 0

        # Check each transaction for inbound SOL
        for sig in signatures:
            funders = await self.check_inbound_sol(creator, sig)
            if funders:
                funding_txs += 1
                for addr, amount in funders.items():
                    if addr not in all_funders:
                        all_funders[addr] = 0
                    all_funders[addr] += amount

        if all_funders:
            print(f"→ Found {len(all_funders)} funder(s) in {funding_txs} tx(s)")
            for addr, amount in sorted(all_funders.items(), key=lambda x: x[1], reverse=True)[:3]:
                addr_short = f"{addr[:8]}...{addr[-4:]}"
                print(f"  ← {addr_short}: {amount:.6f} SOL")
        else:
            print(f"→ No funders found")

        return {
            "creator": creator,
            "funders": all_funders
        }

    async def run(self):
        """Main execution"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Get all unique creators
            cursor.execute("SELECT DISTINCT earliest_tx_creator FROM token_analysis WHERE earliest_tx_creator IS NOT NULL")
            creators = [row[0] for row in cursor.fetchall()]
            conn.close()

            print(f"\n[START] FIXED extraction for {len(creators)} creators...\n")

            all_creator_funders = {}
            total_unique_funders = set()

            # Process each creator
            for idx, creator in enumerate(creators, 1):
                result = await self.process_creator(creator, idx, len(creators))
                all_creator_funders[creator] = result["funders"]

                for funder in result["funders"].keys():
                    total_unique_funders.add(funder)

            # Store results
            print(f"\n[STORE] Saving to database...")
            await self._store_funders(all_creator_funders)

            # Summary
            print(f"\n{'='*80}")
            print(f"[SUMMARY]")
            print(f"  Creators processed: {len(creators)}")
            print(f"  Unique funder accounts found: {len(total_unique_funders)}")

            if total_unique_funders:
                print(f"\n[FUNDERS FOUND]:\n")
                for funder in sorted(list(total_unique_funders))[:10]:
                    print(f"  {funder}")

        except Exception as e:
            print(f"[ERROR] {e}")

    async def _store_funders(self, all_funders: Dict[str, Dict[str, float]]):
        """Store funder data"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Ensure table exists
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS creator_funders_fixed (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    creator_address TEXT NOT NULL,
                    funder_address TEXT NOT NULL,
                    total_amount_sol REAL,
                    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(creator_address, funder_address)
                )
            """)

            count = 0
            for creator, funders in all_funders.items():
                for funder, amount in funders.items():
                    try:
                        cursor.execute(
                            "INSERT OR REPLACE INTO creator_funders_fixed (creator_address, funder_address, total_amount_sol) VALUES (?, ?, ?)",
                            (creator, funder, amount)
                        )
                        count += 1
                    except Exception as e:
                        print(f"[STORE_ERROR] {e}")

            conn.commit()
            conn.close()

            print(f"✅ Stored {count} creator-funder relationships\n")

        except Exception as e:
            print(f"[DB_ERROR] {e}")


async def main():
    print(f"[START] FIXED Creator Funding Extraction at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*80)

    extractor = CreatorFundingFixedExtractor()
    await extractor.run()

    print("="*80)
    print(f"[DONE] Complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


if __name__ == "__main__":
    asyncio.run(main())
