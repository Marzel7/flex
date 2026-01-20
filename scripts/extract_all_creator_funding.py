#!/usr/bin/env python3
"""
Extract funding sources for ALL token creators.

This script:
1. Gets all 104 unique token creators from token_analysis
2. Fetches their first transaction (funding transaction)
3. Extracts who funded them (SOL transfers FROM other accounts TO the creator)
4. Builds a complete funding graph
5. Identifies centralization points and master accounts
"""

import sqlite3
import asyncio
import aiohttp
import json
import os
from typing import List, Dict, Optional, Tuple
from datetime import datetime
from dotenv import load_dotenv
from collections import defaultdict

load_dotenv()

DB_PATH = "pumpswap_tokens.db"

# RPC configuration
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
RPC_URL = os.getenv("RPC_URL", "")
RPC_URL_2 = os.getenv("RPC_URL_2", "")

RPC_URLS = [url for url in [RPC_URL, RPC_URL_2] if url]
if HELIUS_API_KEY:
    RPC_URLS.append(f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}")
RPC_URLS.append("https://api.mainnet-beta.solana.com")


class CreatorFundingExtractor:
    """Extract funding sources for all token creators"""

    def __init__(self):
        self.rpc_index = 0
        self.funding_graph = defaultdict(list)  # creator -> list of funders

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

    async def get_creator_first_signature(self, creator: str) -> Optional[str]:
        """Get the FIRST transaction for a creator (funding transaction)"""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [
                    creator,
                    {"limit": 1000}  # Get many to find the earliest
                ]
            }

            result = await self._post_rpc(payload)
            if result and "result" in result:
                sigs = result["result"]
                if sigs:
                    # Last one in the list is the earliest
                    return sigs[-1]["signature"]
            return None
        except Exception as e:
            print(f"[ERROR] Failed to get creator signatures: {e}")
            return None

    async def extract_funding_sources(self, creator: str, signature: str) -> List[Dict]:
        """Extract who funded this creator (SOL transfers TO the creator)"""
        funding_sources = []

        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [signature, {"encoding": "jsonParsed"}]
            }

            result = await self._post_rpc(payload)
            if not result or "result" not in result:
                return []

            tx = result["result"]
            if not tx or not tx.get("transaction"):
                return []

            # Get transaction metadata
            meta = tx.get("meta", {})
            if not meta:
                return []

            # Extract account changes to find SOL transfers TO the creator
            pre_balances = meta.get("preBalances", [])
            post_balances = meta.get("postBalances", [])
            accounts = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])

            if not accounts or len(accounts) == 0:
                return []

            # Find the creator's account index
            creator_index = None
            for i, account in enumerate(accounts):
                account_addr = account if isinstance(account, str) else account.get("pubkey", "")
                if account_addr == creator:
                    creator_index = i
                    break

            if creator_index is None:
                return []

            # Get creator's balance change
            if creator_index >= len(pre_balances) or creator_index >= len(post_balances):
                return []

            creator_pre = pre_balances[creator_index]
            creator_post = post_balances[creator_index]
            creator_change = creator_post - creator_pre

            # If creator received SOL (positive change)
            if creator_change > 0:
                # Find who sent it (account with negative balance change)
                for i, account in enumerate(accounts):
                    if i >= len(pre_balances) or i >= len(post_balances):
                        continue

                    account_addr = account if isinstance(account, str) else account.get("pubkey", "")
                    if account_addr == creator:
                        continue  # Skip the creator

                    pre_bal = pre_balances[i]
                    post_bal = post_balances[i]
                    balance_change = post_bal - pre_bal

                    # Account with negative balance (sent SOL)
                    if balance_change < 0 and abs(balance_change) > 100000:  # > 0.0001 SOL
                        funding_sources.append({
                            "funder": account_addr,
                            "amount": abs(balance_change) / 1e9,  # Convert to SOL
                            "signature": signature
                        })

        except Exception as e:
            print(f"[ERROR] Failed to extract funding from {signature}: {e}")

        return funding_sources

    async def process_creator(self, creator: str, creator_short: str) -> Dict:
        """Process a single creator's funding"""
        print(f"[FUNDING] Processing {creator_short}...", flush=True)

        # Get creator's first (earliest) transaction
        signature = await self.get_creator_first_signature(creator)
        if not signature:
            print(f"[FUNDING]   No transactions found", flush=True)
            return {"creator": creator, "funders": []}

        # Extract funding sources
        funders = await self.extract_funding_sources(creator, signature)

        if funders:
            print(f"[FUNDING]   Found {len(funders)} funder(s)", flush=True)
            for f in funders[:3]:
                funder_short = f"{f['funder'][:8]}...{f['funder'][-4:]}"
                print(f"[FUNDING]     ← {funder_short}: {f['amount']:.4f} SOL", flush=True)
                # Track in graph
                self.funding_graph[f['funder']].append(creator)

        return {
            "creator": creator,
            "funders": funders
        }

    async def process_all_creators(self):
        """Process all 104 token creators"""
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
                await self.process_creator(creator, creator_short)

            print(f"\n[FUNDING] ✅ Processing complete!")
            return self.funding_graph

        except Exception as e:
            print(f"[ERROR] Failed to process creators: {e}")
            return {}

    async def analyze_funding_graph(self, funding_graph: Dict):
        """Analyze the funding graph for patterns"""
        print(f"\n{'='*100}")
        print("FUNDING GRAPH ANALYSIS")
        print(f"{'='*100}\n")

        # Find top funders (accounts that funded many creators)
        top_funders = sorted(
            [(funder, len(creators)) for funder, creators in funding_graph.items()],
            key=lambda x: x[1],
            reverse=True
        )

        print(f"📊 TOP FUNDERS (accounts that funded multiple creators):\n")
        if top_funders:
            for funder, count in top_funders[:20]:
                print(f"💰 {funder[:40]}...")
                print(f"   Funded {count} creator(s)")
                print()
        else:
            print("No multi-creator funders found\n")

        # Save to database
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS creator_funding_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    creator_address TEXT NOT NULL,
                    funder_address TEXT NOT NULL,
                    amount_sol REAL,
                    first_tx_signature TEXT,
                    extracted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(creator_address, funder_address)
                )
            """)

            # Store funding data - we'll populate this after we get the data
            conn.commit()
            conn.close()

        except Exception as e:
            print(f"[DB_ERROR] {e}")


async def main():
    print(f"[FUNDING] Starting creator funding extraction at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*100 + "\n")

    extractor = CreatorFundingExtractor()
    funding_graph = await extractor.process_all_creators()
    await extractor.analyze_funding_graph(funding_graph)

    print("\n" + "="*100)
    print(f"[FUNDING] Complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


if __name__ == "__main__":
    asyncio.run(main())
