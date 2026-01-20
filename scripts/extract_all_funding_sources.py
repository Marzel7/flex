#!/usr/bin/env python3
"""
Extract and log ALL funding sources for token creators.

This script:
1. Gets all 104 token creators
2. Scans ALL their transactions
3. Finds ALL accounts that sent SOL to creators
4. Logs each funding relationship
5. Identifies distribution hubs (accounts funding 3+ creators)
6. Flags suspicious funding patterns
"""

import sqlite3
import asyncio
import aiohttp
import os
from typing import List, Dict, Optional, Set
from datetime import datetime
from collections import defaultdict

DB_PATH = "pumpswap_tokens.db"

# RPC configuration
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
RPC_URL = os.getenv("RPC_URL", "")
RPC_URL_2 = os.getenv("RPC_URL_2", "")

RPC_URLS = [url for url in [RPC_URL, RPC_URL_2] if url]
if HELIUS_API_KEY:
    RPC_URLS.append(f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}")
RPC_URLS.append("https://api.mainnet-beta.solana.com")


class CompleteFundingExtractor:
    """Extract ALL funding sources for token creators"""

    def __init__(self):
        self.rpc_index = 0
        self.funding_map = defaultdict(set)  # funder -> set of creators funded

    async def _post_rpc(self, payload: dict, timeout: int = 10) -> Optional[dict]:
        """Post to RPC with failover"""
        try:
            async with aiohttp.ClientSession() as session:
                for i, rpc_url in enumerate(RPC_URLS):
                    try:
                        async with session.post(
                            rpc_url,
                            json=payload,
                            timeout=aiohttp.ClientTimeout(total=timeout)
                        ) as resp:
                            if resp.status == 200:
                                return await resp.json()
                    except Exception:
                        continue
                return None
        except Exception as e:
            print(f"[RPC_ERROR] {e}")
            return None

    async def get_all_funders_for_creator(self, creator: str, max_txs: int = 100) -> Dict[str, float]:
        """Get ALL accounts that funded this creator"""
        funders = {}

        try:
            # Get all transactions for creator
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [creator, {"limit": max_txs}]
            }

            result = await self._post_rpc(payload)
            if not result or "result" not in result:
                return {}

            signatures = [tx["signature"] for tx in result["result"]]
            if not signatures:
                return {}

            # Scan each transaction for funding
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

                meta = tx.get("meta", {})
                if not meta:
                    continue

                pre_balances = meta.get("preBalances", [])
                post_balances = meta.get("postBalances", [])
                accounts = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])

                if not accounts or len(accounts) == 0:
                    continue

                # Find creator's account index
                creator_index = None
                for i, account in enumerate(accounts):
                    account_addr = account if isinstance(account, str) else account.get("pubkey", "")
                    if account_addr == creator:
                        creator_index = i
                        break

                if creator_index is None or creator_index >= len(pre_balances) or creator_index >= len(post_balances):
                    continue

                creator_pre = pre_balances[creator_index]
                creator_post = post_balances[creator_index]
                creator_change = creator_post - creator_pre

                # Look for SOL increases (funding)
                if creator_change > 100000:  # > 0.0001 SOL
                    # Find the source(s)
                    for i, account in enumerate(accounts):
                        if i >= len(pre_balances) or i >= len(post_balances):
                            continue

                        account_addr = account if isinstance(account, str) else account.get("pubkey", "")
                        if account_addr == creator:
                            continue

                        pre_bal = pre_balances[i]
                        post_bal = post_balances[i]
                        balance_change = post_bal - pre_bal

                        # Account with negative balance (sent SOL)
                        if balance_change < -100000:  # > 0.0001 SOL sent
                            amount_sol = abs(balance_change) / 1e9
                            if account_addr not in funders:
                                funders[account_addr] = 0
                            funders[account_addr] += amount_sol

        except Exception as e:
            print(f"[ERROR] Failed to extract funders for {creator}: {e}")

        return funders

    async def process_creator(self, creator: str, creator_short: str) -> Dict:
        """Process a single creator's funding sources"""
        print(f"[FUNDING] Processing {creator_short}...", flush=True)

        funders = await self.get_all_funders_for_creator(creator)

        if funders:
            print(f"[FUNDING]   Found {len(funders)} funder(s)", flush=True)
            for funder, amount in sorted(funders.items(), key=lambda x: x[1], reverse=True)[:3]:
                funder_short = f"{funder[:8]}...{funder[-4:]}"
                print(f"[FUNDING]     ← {funder_short}: {amount:.4f} SOL", flush=True)
                # Track in map
                self.funding_map[funder].add(creator)

        return {
            "creator": creator,
            "funders": funders
        }

    async def process_all_creators(self):
        """Process all token creators"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Get all unique creators
            cursor.execute("SELECT DISTINCT earliest_tx_creator FROM token_analysis WHERE earliest_tx_creator IS NOT NULL")
            all_creators = [row[0] for row in cursor.fetchall()]
            conn.close()

            print(f"\n[FUNDING] Processing {len(all_creators)} unique token creators...\n")

            # Process each creator
            for idx, creator in enumerate(all_creators, 1):
                creator_short = f"{creator[:8]}...{creator[-4:]}"
                print(f"[{idx}/{len(all_creators)}]", end=" ")
                result = await self.process_creator(creator, creator_short)

                # Store in database
                await self._store_funding_sources(creator, result["funders"])

            print(f"\n[FUNDING] ✅ Extraction complete!")

            # Analyze funding patterns
            await self._analyze_funding_hubs()

        except Exception as e:
            print(f"[ERROR] Failed to process creators: {e}")

    async def _store_funding_sources(self, creator: str, funders: Dict[str, float]):
        """Store funding sources in database"""
        if not funders:
            return

        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            for funder, amount in funders.items():
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO funding_sources
                    (creator_address, funder_address, amount_sol, first_detected_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (creator, funder, amount)
                )

            conn.commit()
            conn.close()

        except Exception as e:
            print(f"[DB_ERROR] Failed to store funding sources: {e}")

    async def _analyze_funding_hubs(self):
        """Identify and flag distribution hubs"""
        print(f"\n[ANALYSIS] Analyzing funding patterns...\n")

        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Find hubs (accounts funding 3+ creators)
            cursor.execute("""
                SELECT funder_address, COUNT(DISTINCT creator_address) as creator_count
                FROM funding_sources
                GROUP BY funder_address
                HAVING creator_count >= 3
                ORDER BY creator_count DESC
            """)

            hubs = cursor.fetchall()

            print(f"[ANALYSIS] 🎯 DISTRIBUTION HUBS ({len(hubs)} found):\n")

            for funder, count in hubs:
                print(f"💰 {funder[:40]}...")
                print(f"   Funds: {count} creators")

                # Mark as distribution hub
                cursor.execute(
                    """
                    UPDATE funding_sources
                    SET is_distribution_hub = 1, funding_hub_count = ?
                    WHERE funder_address = ?
                    """,
                    (count, funder)
                )

                # Get the creators it funds
                cursor.execute("""
                    SELECT creator_address FROM funding_sources
                    WHERE funder_address = ?
                """, (funder,))

                creators = [row[0] for row in cursor.fetchall()]
                print(f"   Creators:")
                for c in creators[:5]:
                    print(f"     - {c[:20]}...")
                if len(creators) > 5:
                    print(f"     ... and {len(creators) - 5} more")
                print()

            conn.commit()
            conn.close()

        except Exception as e:
            print(f"[ANALYSIS_ERROR] {e}")


async def main():
    print(f"[FUNDING] Starting complete funding extraction at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*100 + "\n")

    extractor = CompleteFundingExtractor()
    await extractor.process_all_creators()

    print("\n" + "="*100)
    print(f"[FUNDING] Complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


if __name__ == "__main__":
    asyncio.run(main())
