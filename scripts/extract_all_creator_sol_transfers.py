#!/usr/bin/env python3
"""
Extract and store ALL SOL transfers (in/out) for EVERY token creator.

For each creator:
1. Get all their transactions
2. Find accounts that sent SOL TO them (funders)
3. Find accounts they sent SOL TO (recipients/treasuries)
4. Store both relationships
"""

import sqlite3
import asyncio
import aiohttp
import os
from typing import Optional, Dict, List, Tuple
from datetime import datetime

DB_PATH = "pumpswap_tokens.db"

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
RPC_URL = os.getenv("RPC_URL", "")
RPC_URL_2 = os.getenv("RPC_URL_2", "")

RPC_URLS = [url for url in [RPC_URL, RPC_URL_2] if url]
if HELIUS_API_KEY:
    RPC_URLS.append(f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}")
RPC_URLS.append("https://api.mainnet-beta.solana.com")


class CompleteSOLTransferExtractor:
    """Extract ALL SOL transfers for every creator"""

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

    async def extract_sol_transfers(self, creator: str, signature: str) -> Tuple[Dict[str, float], Dict[str, float]]:
        """
        Extract SOL transfers from a transaction.

        Returns:
        - inbound: {sender_address: amount_sol}
        - outbound: {recipient_address: amount_sol}
        """
        inbound = {}
        outbound = {}

        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [signature, {"encoding": "jsonParsed"}]
            }

            result = await self._post_rpc(payload)
            if not result or "result" not in result:
                return inbound, outbound

            tx = result["result"]
            if not tx or not tx.get("transaction"):
                return inbound, outbound

            meta = tx.get("meta")
            if not meta:
                return inbound, outbound

            # Get balance changes
            pre_balances = meta.get("preBalances", [])
            post_balances = meta.get("postBalances", [])
            accounts = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])

            if not accounts or len(accounts) == 0:
                return inbound, outbound

            # Find creator's index
            creator_idx = None
            for idx, acc in enumerate(accounts):
                acc_addr = acc if isinstance(acc, str) else acc.get("pubkey", "")
                if acc_addr == creator:
                    creator_idx = idx
                    break

            if creator_idx is None:
                return inbound, outbound

            if creator_idx >= len(pre_balances) or creator_idx >= len(post_balances):
                return inbound, outbound

            # Creator's balance change
            creator_pre = pre_balances[creator_idx]
            creator_post = post_balances[creator_idx]
            creator_change = creator_post - creator_pre

            # Scan all other accounts in transaction
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
                if acc_change < -100000:  # > 0.0001 SOL sent
                    # If creator received SOL in this tx, this account funded creator
                    if creator_change > 100000:
                        amount_sol = abs(acc_change) / 1e9
                        if acc_addr not in inbound:
                            inbound[acc_addr] = 0
                        inbound[acc_addr] += amount_sol

                # Account received SOL (positive change)
                if acc_change > 100000:  # > 0.0001 SOL received
                    # If creator sent SOL in this tx, creator sent to this account
                    if creator_change < -100000:
                        amount_sol = acc_change / 1e9
                        if acc_addr not in outbound:
                            outbound[acc_addr] = 0
                        outbound[acc_addr] += amount_sol

        except Exception as e:
            print(f"[TX_ERROR] Processing {signature[:16]}...: {e}")

        return inbound, outbound

    async def process_creator(self, creator: str, idx: int, total: int) -> Dict:
        """Process one creator"""
        creator_short = f"{creator[:8]}...{creator[-4:]}"
        print(f"[{idx}/{total}] {creator_short}", end=" ", flush=True)

        # Get all transactions
        signatures = await self.get_creator_transactions(creator, limit=100)
        print(f"({len(signatures)} txs)", end=" ", flush=True)

        all_inbound = {}
        all_outbound = {}
        inbound_count = 0
        outbound_count = 0

        # Process each transaction
        for sig in signatures:
            inbound, outbound = await self.extract_sol_transfers(creator, sig)

            if inbound:
                inbound_count += 1
                for addr, amount in inbound.items():
                    if addr not in all_inbound:
                        all_inbound[addr] = 0
                    all_inbound[addr] += amount

            if outbound:
                outbound_count += 1
                for addr, amount in outbound.items():
                    if addr not in all_outbound:
                        all_outbound[addr] = 0
                    all_outbound[addr] += amount

        # Display results
        result_str = ""
        if all_inbound:
            result_str += f"IN:{len(all_inbound)} "
        if all_outbound:
            result_str += f"OUT:{len(all_outbound)} "

        print(f"→ {result_str if result_str else 'no transfers'}", flush=True)

        if all_inbound:
            for addr, amount in sorted(all_inbound.items(), key=lambda x: x[1], reverse=True)[:2]:
                print(f"    ← {addr[:20]}...: {amount:.6f} SOL", flush=True)

        if all_outbound:
            for addr, amount in sorted(all_outbound.items(), key=lambda x: x[1], reverse=True)[:2]:
                print(f"    → {addr[:20]}...: {amount:.6f} SOL", flush=True)

        return {
            "creator": creator,
            "inbound": all_inbound,
            "outbound": all_outbound
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

            print(f"\n[START] Extracting ALL SOL transfers for {len(creators)} creators...\n")

            all_data = {}
            total_inbound = 0
            total_outbound = 0

            # Process each creator
            for idx, creator in enumerate(creators, 1):
                result = await self.process_creator(creator, idx, len(creators))
                all_data[creator] = result

                total_inbound += len(result["inbound"])
                total_outbound += len(result["outbound"])

            # Store results
            print(f"\n[STORE] Saving to database...")
            await self._store_transfers(all_data)

            # Summary
            print(f"\n{'='*80}")
            print(f"[SUMMARY]")
            print(f"  Creators processed: {len(creators)}")
            print(f"  Total inbound relationships: {total_inbound}")
            print(f"  Total outbound relationships: {total_outbound}")
            print(f"  Total SOL transfers: {total_inbound + total_outbound}")

        except Exception as e:
            print(f"[ERROR] {e}")

    async def _store_transfers(self, all_data: Dict):
        """Store all transfer data to database"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Create tables
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS creator_sol_inbound (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    creator_address TEXT NOT NULL,
                    sender_address TEXT NOT NULL,
                    total_amount_sol REAL,
                    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(creator_address, sender_address)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS creator_sol_outbound (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    creator_address TEXT NOT NULL,
                    recipient_address TEXT NOT NULL,
                    total_amount_sol REAL,
                    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(creator_address, recipient_address)
                )
            """)

            # Store inbound transfers
            inbound_count = 0
            for creator, data in all_data.items():
                for sender, amount in data["inbound"].items():
                    try:
                        cursor.execute(
                            """INSERT OR REPLACE INTO creator_sol_inbound
                               (creator_address, sender_address, total_amount_sol)
                               VALUES (?, ?, ?)""",
                            (creator, sender, amount)
                        )
                        inbound_count += 1
                    except Exception as e:
                        print(f"[ERROR] Storing inbound: {e}")

            # Store outbound transfers
            outbound_count = 0
            for creator, data in all_data.items():
                for recipient, amount in data["outbound"].items():
                    try:
                        cursor.execute(
                            """INSERT OR REPLACE INTO creator_sol_outbound
                               (creator_address, recipient_address, total_amount_sol)
                               VALUES (?, ?, ?)""",
                            (creator, recipient, amount)
                        )
                        outbound_count += 1
                    except Exception as e:
                        print(f"[ERROR] Storing outbound: {e}")

            conn.commit()
            conn.close()

            print(f"✅ Stored {inbound_count} inbound transfers")
            print(f"✅ Stored {outbound_count} outbound transfers\n")

        except Exception as e:
            print(f"[DB_ERROR] {e}")


async def main():
    print(f"[START] Complete SOL Transfer Extraction at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*80)

    extractor = CompleteSOLTransferExtractor()
    await extractor.run()

    print("="*80)
    print(f"[DONE] Complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


if __name__ == "__main__":
    asyncio.run(main())
