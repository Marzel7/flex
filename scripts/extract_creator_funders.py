#!/usr/bin/env python3
"""
Extract FUNDING SOURCES for all token creators.

For EVERY token creator (earliest_tx_creator):
1. Get all their transactions
2. Find inbound SOL transfers (who funded them)
3. Log each funding account with amount
4. Identify funding hubs (accounts that fund 3+ creators)
"""

import sqlite3
import asyncio
import aiohttp
import os
from typing import Optional, Dict, List
from datetime import datetime

DB_PATH = "pumpswap_tokens.db"

# RPC configuration
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
RPC_URL = os.getenv("RPC_URL", "")
RPC_URL_2 = os.getenv("RPC_URL_2", "")

RPC_URLS = [url for url in [RPC_URL, RPC_URL_2] if url]
if HELIUS_API_KEY:
    RPC_URLS.append(f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}")
RPC_URLS.append("https://api.mainnet-beta.solana.com")


class CreatorFunderExtractor:
    """Extract who funded token creators"""

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
                                # Check if result is valid
                                if "result" in data and data["result"] is not None:
                                    return data
                    except Exception:
                        continue
                return None
        except Exception as e:
            print(f"[RPC_ERROR] {e}")
            return None

    async def get_creator_signatures(self, creator: str, limit: int = 100) -> List[str]:
        """Get transaction signatures for a creator"""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [creator, {"limit": limit}]
            }

            result = await self._post_rpc(payload)
            if not result:
                print(f"[RPC_FAIL] No response for {creator[:8]}...")
                return []

            sigs_data = result.get("result")
            if not sigs_data:
                print(f"[NO_SIGS] {creator[:8]}... has no transactions")
                return []

            signatures = [tx["signature"] for tx in sigs_data if "signature" in tx]
            print(f"[SIGS_OK] {creator[:8]}... has {len(signatures)} transactions")
            return signatures

        except Exception as e:
            print(f"[ERROR] Getting signatures for {creator[:8]}...: {e}")
            return []

    async def get_inbound_transfers(self, creator: str) -> Dict[str, float]:
        """Get all accounts that sent SOL TO this creator"""
        funders = {}

        try:
            signatures = await self.get_creator_signatures(creator, limit=100)
            if not signatures:
                return funders

            # Process each transaction
            for sig in signatures:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransaction",
                    "params": [sig, {"encoding": "jsonParsed"}]
                }

                result = await self._post_rpc(payload)
                if not result or "result" not in result:
                    continue

                tx = result["result"]
                if not tx or not tx.get("transaction"):
                    continue

                meta = tx.get("meta")
                if not meta:
                    continue

                # Get balance changes
                pre_balances = meta.get("preBalances", [])
                post_balances = meta.get("postBalances", [])
                accounts = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])

                if not accounts or len(accounts) == 0:
                    continue

                # Find creator in accounts
                creator_idx = None
                for idx, acc in enumerate(accounts):
                    acc_addr = acc if isinstance(acc, str) else acc.get("pubkey", "")
                    if acc_addr == creator:
                        creator_idx = idx
                        break

                if creator_idx is None:
                    continue

                if creator_idx >= len(pre_balances) or creator_idx >= len(post_balances):
                    continue

                # Check if creator received SOL
                creator_change = post_balances[creator_idx] - pre_balances[creator_idx]
                if creator_change <= 0:
                    continue  # Creator didn't receive SOL in this tx

                # Find who sent it (account with matching negative change)
                for idx, acc in enumerate(accounts):
                    if idx >= len(pre_balances) or idx >= len(post_balances):
                        continue

                    acc_addr = acc if isinstance(acc, str) else acc.get("pubkey", "")
                    if acc_addr == creator:
                        continue

                    acc_change = post_balances[idx] - pre_balances[idx]

                    # Check if this account sent SOL that matches creator's increase
                    if acc_change < 0 and abs(acc_change) >= abs(creator_change):
                        amount_sol = abs(acc_change) / 1e9
                        if amount_sol > 0.0001:  # Only track meaningful amounts
                            if acc_addr not in funders:
                                funders[acc_addr] = 0
                            funders[acc_addr] += amount_sol

        except Exception as e:
            print(f"[TX_ERROR] Processing transactions for {creator[:8]}...: {e}")

        return funders

    async def process_creator(self, creator: str, idx: int, total: int) -> Dict:
        """Process one creator"""
        creator_short = f"{creator[:8]}...{creator[-4:]}"
        print(f"\n[{idx}/{total}] {creator_short}", end=" ")

        funders = await self.get_inbound_transfers(creator)

        if funders:
            print(f"→ Found {len(funders)} funder(s)")
            for addr, amount in sorted(funders.items(), key=lambda x: x[1], reverse=True)[:3]:
                addr_short = f"{addr[:8]}...{addr[-4:]}"
                print(f"  ← {addr_short}: {amount:.6f} SOL")
        else:
            print(f"→ No funders found")

        return {
            "creator": creator,
            "funders": funders
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

            print(f"\n[START] Processing {len(creators)} creators to extract funders...\n")

            all_funders = {}
            total_funder_accounts = set()

            # Process each creator
            for idx, creator in enumerate(creators, 1):
                result = await self.process_creator(creator, idx, len(creators))
                all_funders[creator] = result["funders"]

                for funder in result["funders"].keys():
                    total_funder_accounts.add(funder)

            # Store results
            print(f"\n[STORE] Storing results to database...")
            await self._store_funders(all_funders)

            # Summary
            print(f"\n{'='*80}")
            print(f"[SUMMARY]")
            print(f"  Creators processed: {len(creators)}")
            print(f"  Unique funder accounts found: {len(total_funder_accounts)}")

            # Find top funders
            funder_counts = {}
            for creator_funders in all_funders.values():
                for funder in creator_funders.keys():
                    funder_counts[funder] = funder_counts.get(funder, 0) + 1

            top_funders = sorted(funder_counts.items(), key=lambda x: x[1], reverse=True)[:10]

            if top_funders:
                print(f"\n[TOP_FUNDERS] Accounts that funded multiple creators:\n")
                for funder, count in top_funders:
                    if count > 1:
                        print(f"  {funder[:40]}... - funded {count} creators")

        except Exception as e:
            print(f"[ERROR] {e}")

    async def _store_funders(self, all_funders: Dict[str, Dict[str, float]]):
        """Store funder data to database"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Ensure table exists
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS creator_funders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    creator_address TEXT NOT NULL,
                    funder_address TEXT NOT NULL,
                    amount_sol REAL,
                    first_detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(creator_address, funder_address)
                )
            """)

            # Create indexes
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_creator_funders ON creator_funders(creator_address)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_funder_accounts ON creator_funders(funder_address)")

            # Store data
            count = 0
            for creator, funders in all_funders.items():
                for funder, amount in funders.items():
                    try:
                        cursor.execute(
                            "INSERT OR REPLACE INTO creator_funders (creator_address, funder_address, amount_sol) VALUES (?, ?, ?)",
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
    print(f"[START] Creator Funder Extraction at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*80)

    extractor = CreatorFunderExtractor()
    await extractor.run()

    print("="*80)
    print(f"[DONE] Complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


if __name__ == "__main__":
    asyncio.run(main())
