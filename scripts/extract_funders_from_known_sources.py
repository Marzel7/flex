#!/usr/bin/env python3
"""
Extract creator funders by querying FUNDER transaction histories.

Key insight: Creators don't sign funding transactions, so getSignaturesForAddress
returns nothing for inbound SOL. Instead, we query FUNDER accounts and look for
transfers TO known creators.

Strategy:
1. Start with a known funder account (found manually or from previous analysis)
2. Query their transaction history
3. Look for transfers TO known token creators
4. Store the funder-creator relationship

This approach finds funders by starting from the funders' side rather than creators' side.
"""

import sqlite3
import asyncio
import aiohttp
import os
from typing import Optional, Dict, List, Set
from datetime import datetime

DB_PATH = "pumpswap_tokens.db"

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
RPC_URL = os.getenv("RPC_URL", "")
RPC_URL_2 = os.getenv("RPC_URL_2", "")

RPC_URLS = [url for url in [RPC_URL, RPC_URL_2] if url]
if HELIUS_API_KEY:
    RPC_URLS.append(f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}")
RPC_URLS.append("https://api.mainnet-beta.solana.com")


class FunderExtractor:
    """Extract creators' funder accounts by querying funder transaction histories"""

    def __init__(self):
        self.creators_set = set()
        self._load_creators()

    def _load_creators(self):
        """Load all creator addresses from database"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT DISTINCT earliest_tx_creator FROM token_analysis WHERE earliest_tx_creator IS NOT NULL"
            )
            self.creators_set = set(row[0] for row in cursor.fetchall())
            conn.close()
            print(f"[INIT] Loaded {len(self.creators_set)} unique creators")
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

    async def get_funder_transactions(self, funder: str, limit: int = 100) -> List[str]:
        """Get all transactions for a funder account"""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [funder, {"limit": limit}],
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
            print(f"[ERROR] Getting transactions for {funder[:8]}...: {e}")
            return []

    async def check_transfers_to_creators(
        self, funder: str, signature: str
    ) -> Dict[str, float]:
        """
        Check if transaction sent SOL TO any known creators.

        Returns: {creator_address: amount_sol}
        """
        creators_funded = {}

        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [signature, {"encoding": "jsonParsed"}],
            }

            result = await self._post_rpc(payload)
            if not result or "result" not in result:
                return creators_funded

            tx = result["result"]
            if not tx or not tx.get("transaction"):
                return creators_funded

            meta = tx.get("meta")
            if not meta:
                return creators_funded

            # Get balance changes
            pre_balances = meta.get("preBalances", [])
            post_balances = meta.get("postBalances", [])
            accounts = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])

            if not accounts or len(accounts) == 0:
                return creators_funded

            # Find funder's index
            funder_idx = None
            for idx, acc in enumerate(accounts):
                acc_addr = acc if isinstance(acc, str) else acc.get("pubkey", "")
                if acc_addr == funder:
                    funder_idx = idx
                    break

            if funder_idx is None:
                return creators_funded

            if funder_idx >= len(pre_balances) or funder_idx >= len(post_balances):
                return creators_funded

            # Funder's balance change
            funder_pre = pre_balances[funder_idx]
            funder_post = post_balances[funder_idx]
            funder_change = funder_post - funder_pre

            # If funder sent SOL (balance decreased), check if creators received it
            if funder_change < -100000:  # > 0.0001 SOL sent

                # Scan all other accounts for known creators
                for idx, acc in enumerate(accounts):
                    if idx >= len(pre_balances) or idx >= len(post_balances):
                        continue

                    acc_addr = acc if isinstance(acc, str) else acc.get("pubkey", "")
                    if acc_addr not in self.creators_set:
                        continue  # Not a known creator

                    pre_bal = pre_balances[idx]
                    post_bal = post_balances[idx]
                    acc_change = post_bal - pre_bal

                    # Creator received SOL (positive change)
                    if acc_change > 100000:  # > 0.0001 SOL received
                        amount_sol = acc_change / 1e9
                        if amount_sol > 0:
                            if acc_addr not in creators_funded:
                                creators_funded[acc_addr] = 0
                            creators_funded[acc_addr] += amount_sol

        except Exception as e:
            print(f"[TX_ERROR] Processing {signature[:16]}...: {e}")

        return creators_funded

    async def process_funder(self, funder: str, idx: int, total: int) -> Dict:
        """Process one funder account"""
        funder_short = f"{funder[:8]}...{funder[-4:]}"
        print(f"[{idx}/{total}] {funder_short}", end=" ", flush=True)

        # Get all transactions
        signatures = await self.get_funder_transactions(funder, limit=100)
        print(f"({len(signatures)} txs)", end=" ", flush=True)

        all_creators_funded = {}
        funding_txs = 0

        # Process each transaction
        for sig in signatures:
            creators_funded = await self.check_transfers_to_creators(funder, sig)

            if creators_funded:
                funding_txs += 1
                for creator, amount in creators_funded.items():
                    if creator not in all_creators_funded:
                        all_creators_funded[creator] = 0
                    all_creators_funded[creator] += amount

        # Display results
        if all_creators_funded:
            print(f"→ Funded {len(all_creators_funded)} creator(s) in {funding_txs} tx(s)")
            for creator, amount in sorted(
                all_creators_funded.items(), key=lambda x: x[1], reverse=True
            )[:3]:
                creator_short = f"{creator[:8]}...{creator[-4:]}"
                print(f"    → {creator_short}: {amount:.6f} SOL", flush=True)
        else:
            print(f"→ No creators funded", flush=True)

        return {"funder": funder, "creators_funded": all_creators_funded}

    async def run(self, known_funders: List[str]):
        """Main execution"""
        try:
            print(
                f"\n[START] Extracting creators funded by {len(known_funders)} funder(s)...\n"
            )

            all_funder_data = {}
            total_creator_funders = {}

            # Process each funder
            for idx, funder in enumerate(known_funders, 1):
                result = await self.process_funder(funder, idx, len(known_funders))
                all_funder_data[funder] = result["creators_funded"]

                for creator, amount in result["creators_funded"].items():
                    if creator not in total_creator_funders:
                        total_creator_funders[creator] = []
                    total_creator_funders[creator].append(
                        {"funder": funder, "amount": amount}
                    )

            # Store results
            print(f"\n[STORE] Saving to database...")
            await self._store_funder_relationships(all_funder_data)

            # Summary
            print(f"\n{'='*80}")
            print(f"[SUMMARY]")
            print(f"  Funders analyzed: {len(known_funders)}")
            print(f"  Creators funded: {len(total_creator_funders)}")
            total_funding = sum(
                sum(f["amount"] for f in funders)
                for funders in total_creator_funders.values()
            )
            print(f"  Total SOL transferred: {total_funding:.6f}")

            if total_creator_funders:
                print(f"\n[CREATORS FUNDED]:\n")
                for creator, funders in sorted(
                    total_creator_funders.items(), key=lambda x: sum(f["amount"] for f in x[1]), reverse=True
                ):
                    creator_short = f"{creator[:8]}...{creator[-4:]}"
                    total_funded = sum(f["amount"] for f in funders)
                    print(
                        f"  {creator_short}: {total_funded:.6f} SOL from {len(funders)} funder(s)"
                    )

        except Exception as e:
            print(f"[ERROR] {e}")

    async def _store_funder_relationships(
        self, all_funder_data: Dict[str, Dict[str, float]]
    ):
        """Store funder-creator relationships to database"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Create tables
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS creator_funders_discovered (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    creator_address TEXT NOT NULL,
                    funder_address TEXT NOT NULL,
                    total_amount_sol REAL,
                    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(creator_address, funder_address)
                )
            """
            )

            count = 0
            for funder, creators_funded in all_funder_data.items():
                for creator, amount in creators_funded.items():
                    try:
                        cursor.execute(
                            """INSERT OR REPLACE INTO creator_funders_discovered
                               (creator_address, funder_address, total_amount_sol)
                               VALUES (?, ?, ?)""",
                            (creator, funder, amount),
                        )
                        count += 1
                    except Exception as e:
                        print(f"[STORE_ERROR] {e}")

            conn.commit()
            conn.close()

            print(f"✅ Stored {count} funder-creator relationships\n")

        except Exception as e:
            print(f"[DB_ERROR] {e}")


async def main():
    print(
        f"[START] Creator Funding Extraction (Funder-Side) at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    print("=" * 80)

    # Start with the known funder we discovered
    known_funders = [
        "8hfTZP4hzPh2bBwMKounGnTzpiYMK7wiyEtrgqVKHhBM",  # Known funder of CQ3k9qYC...
    ]

    extractor = FunderExtractor()
    await extractor.run(known_funders)

    print("=" * 80)
    print(f"[DONE] Complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


if __name__ == "__main__":
    asyncio.run(main())
