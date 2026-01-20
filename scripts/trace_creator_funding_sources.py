#!/usr/bin/env python3
"""
Trace where token creators (CREATE instruction signers) received their SOL funding.

These are the creator_address accounts that actually signed the Pump.Fun CREATE instruction.

Strategy:
1. Get all unique creator_address accounts (CREATE instruction signers)
2. For each creator, find ALL their transaction signatures (they may only have 1-2)
3. Analyze those transactions to see who funded them
4. Identify master funder account(s)
5. Trace the funding chain

This reveals the pre-funding infrastructure that supplies creators with SOL
to sign CREATE instructions, then the creators remain dormant.
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


class CreatorFundingTracer:
    """Trace where creators (CREATE signers) received their initial SOL funding"""

    def __init__(self):
        self.creators = {}  # {creator_address: {mints: [list]}}
        self.load_creators()

    def load_creators(self):
        """Load all unique creator_address accounts (CREATE instruction signers)"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT DISTINCT creator_address, GROUP_CONCAT(mint, ',')
                FROM token_analysis
                WHERE creator_address IS NOT NULL AND creator_address != ''
                GROUP BY creator_address
                ORDER BY creator_address
            """
            )
            for creator, mints_str in cursor.fetchall():
                self.creators[creator] = {
                    "mints": mints_str.split(","),
                    "token_count": len(mints_str.split(",")),
                }

            conn.close()
            print(f"[INIT] Loaded {len(self.creators)} unique creator_address accounts (CREATE signers)\n")
        except Exception as e:
            print(f"[ERROR] Loading creators: {e}")

    async def _post_rpc(self, payload: dict, timeout: int = 10) -> Optional[dict]:
        """Post to RPC with failover"""
        try:
            async with aiohttp.ClientSession() as session:
                for rpc_url in RPC_URLS:
                    try:
                        async with session.post(
                            rpc_url,
                            json=payload,
                            timeout=aiohttp.ClientTimeout(total=timeout),
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
        """Get all transactions for a creator account"""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [creator, {"limit": limit}],
            }

            result = await self._post_rpc(payload)
            if not result or "result" not in result:
                return []

            sigs_data = result.get("result")
            if not sigs_data:
                return []

            return [tx["signature"] for tx in sigs_data if "signature" in tx]

        except Exception as e:
            print(f"[ERROR] Getting transactions for {creator[:8]}...: {e}")
            return []

    async def analyze_transaction_for_funding(
        self, signature: str, creator: str
    ) -> Optional[str]:
        """
        Analyze a transaction to find who sent SOL to the creator.

        Returns the funder address (account that decreased SOL while creator increased it)
        """
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [signature, {"encoding": "jsonParsed"}],
            }

            result = await self._post_rpc(payload)
            if not result or "result" not in result:
                return None

            tx = result["result"]
            if not tx or not tx.get("transaction"):
                return None

            meta = tx.get("meta")
            if not meta:
                return None

            pre_balances = meta.get("preBalances", [])
            post_balances = meta.get("postBalances", [])
            accounts = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])

            if not accounts or len(accounts) == 0:
                return None

            # Find creator's index
            creator_idx = None
            for idx, acc in enumerate(accounts):
                acc_addr = acc if isinstance(acc, str) else acc.get("pubkey", "")
                if acc_addr == creator:
                    creator_idx = idx
                    break

            if creator_idx is None or creator_idx >= len(pre_balances):
                return None

            creator_pre = pre_balances[creator_idx]
            creator_post = post_balances[creator_idx]
            creator_change = creator_post - creator_pre

            # Look for account that sent SOL to creator (creator received SOL)
            if creator_change > 0:  # Creator received SOL
                for idx, acc in enumerate(accounts):
                    if idx >= len(pre_balances) or idx >= len(post_balances):
                        continue

                    acc_addr = acc if isinstance(acc, str) else acc.get("pubkey", "")
                    if acc_addr == creator:
                        continue

                    pre_bal = pre_balances[idx]
                    post_bal = post_balances[idx]
                    acc_change = post_bal - pre_bal

                    # Funder: account decreased SOL, creator increased SOL
                    if acc_change < 0:
                        abs_acc_decrease = abs(acc_change)
                        # Check if the decrease roughly matches the increase (accounting for fees)
                        if abs_acc_decrease >= creator_change - 10000:
                            return acc_addr

            return None

        except Exception as e:
            return None

    async def process_creator(
        self, creator: str, idx: int, total: int, info: dict
    ) -> Dict:
        """Process one creator to trace funding"""
        creator_short = f"{creator[:8]}...{creator[-4:]}"
        token_count = info["token_count"]
        print(
            f"[{idx}/{total}] {creator_short} ({token_count} tokens)",
            end=" ",
            flush=True,
        )

        # Get all transactions for this creator
        transactions = await self.get_creator_transactions(creator, limit=100)
        print(f"({len(transactions)} sigs)", end=" ", flush=True)

        if not transactions:
            print(f"→ No transactions", flush=True)
            return {
                "creator": creator,
                "token_count": token_count,
                "transactions": [],
                "funders": [],
            }

        # Analyze each transaction to find funding
        funders = []
        for sig in transactions:
            funder = await self.analyze_transaction_for_funding(sig, creator)
            if funder:
                funders.append(funder)

        if funders:
            # Get unique funders
            unique_funders = list(set(funders))
            funder_short = f"{unique_funders[0][:8]}...{unique_funders[0][-4:]}"
            print(f"→ Funded by {funder_short} ({len(unique_funders)} unique source(s))", flush=True)
        else:
            print(f"→ No funding source found", flush=True)

        return {
            "creator": creator,
            "token_count": token_count,
            "transactions": transactions,
            "funders": list(set(funders)),  # Unique funders
        }

    async def run(self):
        """Main execution"""
        try:
            print(
                f"[START] Creator Funding Source Tracing at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            print("=" * 80 + "\n")

            results = []
            for idx, (creator, info) in enumerate(sorted(self.creators.items()), 1):
                result = await self.process_creator(creator, idx, len(self.creators), info)
                results.append(result)

            # Summary
            print(f"\n{'='*80}")
            print(f"[SUMMARY]")
            print(f"  Total creators analyzed: {len(self.creators)}")

            funded_creators = [r for r in results if r["funders"]]
            print(f"  Creators with identifiable funder: {len(funded_creators)}")

            if funded_creators:
                # Group by funder
                funder_groups = {}
                for r in funded_creators:
                    for funder in r["funders"]:
                        if funder not in funder_groups:
                            funder_groups[funder] = {"creators": [], "tokens": 0}
                        if r["creator"] not in funder_groups[funder]["creators"]:
                            funder_groups[funder]["creators"].append(r["creator"])
                            funder_groups[funder]["tokens"] += r["token_count"]

                print(f"\n[MASTER FUNDERS]")
                for funder, data in sorted(
                    funder_groups.items(),
                    key=lambda x: len(x[1]["creators"]),
                    reverse=True,
                ):
                    funder_short = f"{funder[:8]}...{funder[-4:]}"
                    print(
                        f"  {funder_short}: funded {len(data['creators'])} creators controlling {data['tokens']} tokens"
                    )

            # Store results
            await self._store_results(results)

        except Exception as e:
            print(f"[ERROR] {e}")

    async def _store_results(self, results: List[Dict]):
        """Store tracing results to database"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Create table for funding sources
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS creator_funding_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    creator_address TEXT UNIQUE NOT NULL,
                    transaction_count INTEGER,
                    funding_sources TEXT,
                    analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """
            )

            count = 0
            for r in results:
                try:
                    funding_sources_str = ",".join(r["funders"]) if r["funders"] else None
                    cursor.execute(
                        """INSERT OR REPLACE INTO creator_funding_sources
                           (creator_address, transaction_count, funding_sources)
                           VALUES (?, ?, ?)""",
                        (r["creator"], len(r["transactions"]), funding_sources_str),
                    )
                    count += 1
                except Exception as e:
                    print(f"[STORE_ERROR] {r['creator'][:16]}...: {e}")

            conn.commit()
            conn.close()

            print(f"✅ Stored funding source analysis for {count} creators\n")

        except Exception as e:
            print(f"[DB_ERROR] {e}")


async def main():
    tracer = CreatorFundingTracer()
    await tracer.run()

    print("=" * 80)
    print(f"[DONE] Complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


if __name__ == "__main__":
    asyncio.run(main())
