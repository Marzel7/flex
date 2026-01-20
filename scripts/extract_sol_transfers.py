#!/usr/bin/env python3
"""
Extract SOL transfers from rugged token creators.

This script:
1. Gets all blocked creators (known ruggers)
2. Fetches their transactions after rug detection
3. Extracts SOL transfer destinations (treasury addresses)
4. Stores in creator_sol_transfers table
5. Builds creator networks based on shared destinations
"""

import sqlite3
import asyncio
import aiohttp
import json
import os
from typing import List, Dict, Optional, Tuple
from datetime import datetime
from dotenv import load_dotenv

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


class SOLTransferExtractor:
    """Extract SOL transfers from creator transactions"""

    def __init__(self):
        self.rpc_index = 0

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

    async def get_creator_transactions(self, creator: str, limit: int = 50) -> List[str]:
        """Get transaction signatures for a creator"""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [
                    creator,
                    {"limit": limit}
                ]
            }

            result = await self._post_rpc(payload)
            if result and "result" in result:
                return [tx["signature"] for tx in result["result"]]
            return []
        except Exception as e:
            print(f"[ERROR] Failed to get creator transactions: {e}")
            return []

    async def extract_sol_transfers(self, creator: str, signatures: List[str]) -> List[Dict]:
        """Extract SOL transfer destinations from creator's transactions"""
        transfers = []

        for sig in signatures[:10]:  # Limit to 10 transactions per creator
            try:
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

                # Look for SOL transfers in the transaction
                meta = tx.get("meta", {})
                if not meta:
                    continue

                # Extract post-balances to find SOL movements
                pre_balances = meta.get("preBalances", [])
                post_balances = meta.get("postBalances", [])
                accounts = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])

                if not accounts or len(accounts) == 0:
                    continue

                # Find accounts with SOL balance changes
                for i, account in enumerate(accounts):
                    if i >= len(pre_balances) or i >= len(post_balances):
                        continue

                    pre_bal = pre_balances[i]
                    post_bal = post_balances[i]
                    balance_change = post_bal - pre_bal

                    # Detect SOL outflows from creator (negative balance change)
                    if balance_change < 0 and abs(balance_change) > 1000000:  # > 0.001 SOL
                        account_addr = account if isinstance(account, str) else account.get("pubkey", "")
                        if account_addr and account_addr != creator:
                            transfers.append({
                                "destination": account_addr,
                                "amount": abs(balance_change) / 1e9,  # Convert to SOL
                                "signature": sig
                            })

            except Exception as e:
                print(f"[ERROR] Failed to extract from {sig}: {e}")
                continue

        return transfers

    async def process_creator(self, creator: str, creator_short: str) -> Dict:
        """Process a single creator's transactions"""
        print(f"[EXTRACT] Processing {creator_short}...", flush=True)

        # Get creator's transactions
        signatures = await self.get_creator_transactions(creator)
        if not signatures:
            print(f"[EXTRACT]   No transactions found", flush=True)
            return {}

        print(f"[EXTRACT]   Found {len(signatures)} transactions", flush=True)

        # Extract SOL transfers
        transfers = await self.extract_sol_transfers(creator, signatures)

        if transfers:
            print(f"[EXTRACT]   Found {len(transfers)} SOL transfers", flush=True)
            for t in transfers[:3]:
                dest_short = f"{t['destination'][:8]}...{t['destination'][-4:]}"
                print(f"[EXTRACT]     → {dest_short}: {t['amount']:.4f} SOL", flush=True)

        return {
            "creator": creator,
            "transactions": len(signatures),
            "transfers": transfers
        }

    async def process_all_creators(self):
        """Process all blocked creators"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Get all blocked creators
            cursor.execute("SELECT creator_address, reputation FROM creator_blocklist")
            blocked_creators = cursor.fetchall()
            conn.close()

            print(f"\n[EXTRACT] Processing {len(blocked_creators)} blocked creators...\n")

            # Process each creator
            for creator, reputation in blocked_creators:
                creator_short = f"{creator[:8]}...{creator[-4:]}"
                result = await self.process_creator(creator, creator_short)

                # Store results
                if result.get("transfers"):
                    await self._store_transfers(creator, result["transfers"])

            print(f"\n[EXTRACT] ✅ Processing complete!")

        except Exception as e:
            print(f"[ERROR] Failed to process creators: {e}")

    async def _store_transfers(self, creator: str, transfers: List[Dict]):
        """Store extracted SOL transfers to database"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            for transfer in transfers:
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO creator_sol_transfers
                    (creator_address, destination_address, total_amount, transfer_count, first_detected_at, last_detected_at)
                    VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (creator, transfer["destination"], transfer["amount"])
                )

            conn.commit()
            conn.close()

        except sqlite3.OperationalError:
            # Table doesn't exist yet - create it
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS creator_sol_transfers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    creator_address TEXT NOT NULL,
                    destination_address TEXT NOT NULL,
                    total_amount REAL DEFAULT 0,
                    transfer_count INTEGER DEFAULT 0,
                    first_detected_at TIMESTAMP,
                    last_detected_at TIMESTAMP,
                    is_pool_address INTEGER DEFAULT 0,
                    UNIQUE(creator_address, destination_address)
                )
            """)

            for transfer in transfers:
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO creator_sol_transfers
                    (creator_address, destination_address, total_amount, transfer_count, first_detected_at, last_detected_at)
                    VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (creator, transfer["destination"], transfer["amount"])
                )

            conn.commit()
            conn.close()

        except Exception as e:
            print(f"[ERROR] Failed to store transfers: {e}")


async def main():
    print(f"[EXTRACT] Starting SOL transfer extraction at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 100 + "\n")

    extractor = SOLTransferExtractor()
    await extractor.process_all_creators()

    print("\n" + "=" * 100)
    print(f"[EXTRACT] Complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


if __name__ == "__main__":
    asyncio.run(main())
