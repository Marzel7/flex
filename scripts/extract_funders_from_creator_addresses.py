#!/usr/bin/env python3
"""
Extract creator funders using the creator_address field from database.

This is different from earliest_tx_creator - it uses the actual creator
addresses that were extracted or identified.
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


class CreatorAddressFunderExtractor:
    """Extract funders using creator_address field from database"""

    def __init__(self):
        self.creators = {}  # {creator_address: token_mint}
        self._load_creators()

    def _load_creators(self):
        """Load all creator addresses from database"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT creator_address, mint FROM token_analysis WHERE creator_address IS NOT NULL AND creator_address != ''"
            )
            self.creators = {row[0]: row[1] for row in cursor.fetchall()}
            conn.close()
            print(f"[INIT] Loaded {len(self.creators)} creators from creator_address field\n")
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
        """Get all transactions for a creator"""
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

            signatures = [tx["signature"] for tx in sigs_data if "signature" in tx]
            return signatures

        except Exception as e:
            print(f"[ERROR] Getting transactions for {creator[:8]}...: {e}")
            return []

    async def find_inbound_funders(self, creator: str, signature: str) -> Dict[str, float]:
        """Find accounts that sent SOL TO this creator"""
        funders = {}

        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [signature, {"encoding": "jsonParsed"}],
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

            creator_pre = pre_balances[creator_idx]
            creator_post = post_balances[creator_idx]
            creator_change = creator_post - creator_pre

            # If creator received SOL
            if creator_change > 100000:  # > 0.0001 SOL
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
                    if acc_change < -100000:
                        abs_change = abs(acc_change)
                        if abs_change >= creator_change - 10000:
                            amount_sol = abs_change / 1e9
                            if amount_sol > 0:
                                if acc_addr not in funders:
                                    funders[acc_addr] = 0
                                funders[acc_addr] += amount_sol

        except Exception as e:
            print(f"[TX_ERROR] Processing {signature[:16]}...: {e}")

        return funders

    async def process_creator(self, creator: str, idx: int, total: int) -> Dict[str, float]:
        """Process one creator to find funders"""
        creator_short = f"{creator[:8]}...{creator[-4:]}"
        print(f"[{idx}/{total}] {creator_short}", end=" ", flush=True)

        signatures = await self.get_creator_transactions(creator, limit=100)
        print(f"({len(signatures)} txs)", end=" ", flush=True)

        all_funders = {}
        funding_txs = 0

        for sig in signatures:
            funders = await self.find_inbound_funders(creator, sig)
            if funders:
                funding_txs += 1
                for addr, amount in funders.items():
                    if addr not in all_funders:
                        all_funders[addr] = 0
                    all_funders[addr] += amount

        if all_funders:
            print(f"→ Found {len(all_funders)} funder(s) in {funding_txs} tx(s)")
            for addr, amount in sorted(all_funders.items(), key=lambda x: x[1], reverse=True)[
                :2
            ]:
                addr_short = f"{addr[:8]}...{addr[-4:]}"
                print(f"    ← {addr_short}: {amount:.6f} SOL", flush=True)
        else:
            print(f"→ No funders found", flush=True)

        return all_funders

    async def run(self):
        """Main execution"""
        try:
            print(
                f"[START] Funder Extraction from creator_address field at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            print("=" * 80 + "\n")

            all_creator_funders = {}
            all_unique_funders = set()
            total_funding = 0

            # Process each creator
            for idx, creator in enumerate(sorted(self.creators.keys()), 1):
                funders = await self.process_creator(creator, idx, len(self.creators))
                if funders:
                    all_creator_funders[creator] = funders
                    for funder in funders.keys():
                        all_unique_funders.add(funder)
                    total_funding += sum(funders.values())

            # Store results
            print(f"\n[STORE] Saving to database...")
            await self._store_funders(all_creator_funders)

            # Summary
            print(f"\n{'='*80}")
            print(f"[SUMMARY]")
            print(f"  Creators analyzed: {len(self.creators)}")
            print(f"  Creators with funders: {len(all_creator_funders)}")
            print(f"  Unique funder accounts: {len(all_unique_funders)}")
            print(f"  Total SOL distributed to creators: {total_funding:.6f}")

            if all_unique_funders:
                print(f"\n[FUNDERS DISCOVERED]:\n")
                for funder in sorted(list(all_unique_funders))[:20]:
                    funder_short = f"{funder[:8]}...{funder[-4:]}"
                    creators_funded = sum(
                        1 for f in all_creator_funders.values() if funder in f
                    )
                    total_sent = sum(
                        amount
                        for f in all_creator_funders.values()
                        for funder_addr, amount in f.items()
                        if funder_addr == funder
                    )
                    print(
                        f"  {funder_short}: Funds {creators_funded} creator(s), {total_sent:.6f} SOL total"
                    )

        except Exception as e:
            print(f"[ERROR] {e}")

    async def _store_funders(self, all_creator_funders: Dict[str, Dict[str, float]]):
        """Store funder-creator relationships"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Create table if needed
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS creator_funders_from_addresses (
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
            for creator, funders in all_creator_funders.items():
                for funder, amount in funders.items():
                    try:
                        cursor.execute(
                            """INSERT OR REPLACE INTO creator_funders_from_addresses
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
    print(f"[START] Creator Address Funder Extraction")
    print("=" * 80)

    extractor = CreatorAddressFunderExtractor()
    await extractor.run()

    print("=" * 80)
    print(f"[DONE] Complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


if __name__ == "__main__":
    asyncio.run(main())
