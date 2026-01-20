#!/usr/bin/env python3
"""
Comprehensive SOL Transfer Extraction

Extracts ALL inbound and outbound SOL transfers for every creator in the database.
- Inbound: All signatures where creator receives SOL
- Outbound: All signatures where creator sends SOL

Stores in creator_sol_transfers and creator_funders tables.
"""

import sqlite3
import asyncio
import aiohttp
import json
from typing import Optional, List, Dict
from datetime import datetime
import os

DB_PATH = "pumpswap_tokens.db"
RPC_URLS = [
    "https://api.mainnet-beta.solana.com",
    "https://api.helius-rpc.com/?api-key=" + os.getenv("HELIUS_API_KEY", ""),
]

class ComprehensiveSOLExtractor:
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH, timeout=60)
        self.cursor = self.conn.cursor()
        self.rpc_url_index = 0
        self.stats = {
            'creators_processed': 0,
            'inbound_transfers': 0,
            'outbound_transfers': 0,
            'total_sol_inbound': 0,
            'total_sol_outbound': 0,
            'errors': 0
        }

    def get_all_creators(self) -> List[str]:
        """Get all creator addresses from database"""
        self.cursor.execute("SELECT DISTINCT final_creator_address FROM token_analysis WHERE final_creator_address IS NOT NULL")
        return [row[0] for row in self.cursor.fetchall()]

    async def _post_rpc(self, payload: Dict, timeout: int = 10) -> Optional[Dict]:
        """Make RPC call with fallback"""
        for attempt in range(len(RPC_URLS)):
            url = RPC_URLS[self.rpc_url_index % len(RPC_URLS)]
            self.rpc_url_index += 1

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                        if resp.status == 200:
                            return await resp.json()
            except Exception as e:
                if attempt == len(RPC_URLS) - 1:
                    print(f"[RPC] Failed all endpoints: {e}")
                continue

        return None

    async def get_all_signatures(self, creator: str, limit: int = 1000) -> List[str]:
        """Get all signatures for a creator"""
        signatures = []
        before = None

        while len(signatures) < limit:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [creator, {"limit": 100}]
            }

            if before:
                payload["params"][1]["before"] = before

            result = await self._post_rpc(payload, timeout=15)

            if not result or "result" not in result:
                break

            batch = result["result"]
            if not batch:
                break

            signatures.extend([tx["signature"] for tx in batch])
            before = batch[-1]["signature"]

            if len(batch) < 100:  # Last batch
                break

        return signatures[:limit]

    async def parse_transaction(self, sig: str, creator: str) -> Dict:
        """Parse transaction to find SOL transfers"""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
        }

        result = await self._post_rpc(payload, timeout=15)

        if not result or "result" not in result or result["result"] is None:
            return {"inbound": [], "outbound": []}

        tx = result["result"]
        transfers = {"inbound": [], "outbound": []}

        try:
            # Check meta for token balances
            if "meta" not in tx or not tx["meta"]:
                return transfers

            meta = tx["meta"]
            pre_balances = meta.get("preBalances", [])
            post_balances = meta.get("postBalances", [])

            # Get accounts from transaction
            message = tx.get("transaction", {}).get("message", {})
            accounts = message.get("accountKeys", [])

            if not accounts or not pre_balances or not post_balances:
                return transfers

            # Find creator index
            creator_idx = None
            for i, acc in enumerate(accounts):
                if isinstance(acc, dict) and acc.get("pubkey") == creator:
                    creator_idx = i
                    break
                elif isinstance(acc, str) and acc == creator:
                    creator_idx = i
                    break

            if creator_idx is None:
                return transfers

            # Calculate creator balance change
            if creator_idx < len(pre_balances) and creator_idx < len(post_balances):
                balance_change = post_balances[creator_idx] - pre_balances[creator_idx]

                if balance_change > 0:
                    # Inbound transfer
                    transfers["inbound"].append({
                        "amount": balance_change / 1e9,  # Convert lamports to SOL
                        "signature": sig
                    })
                elif balance_change < 0:
                    # Outbound transfer
                    transfers["outbound"].append({
                        "amount": abs(balance_change) / 1e9,
                        "signature": sig
                    })

            return transfers

        except Exception as e:
            print(f"[PARSE] Error parsing tx {sig}: {e}")
            return transfers

    async def extract_for_creator(self, creator: str):
        """Extract all SOL transfers for a creator"""
        print(f"\n📊 Processing: {creator[:40]}...")

        # Get all signatures
        sigs = await self.get_all_signatures(creator, limit=1000)
        print(f"   Found {len(sigs)} signatures")

        inbound_total = 0
        outbound_total = 0
        inbound_txs = []
        outbound_txs = []

        # Parse each transaction
        for i, sig in enumerate(sigs):
            if i % 100 == 0:
                print(f"   Parsing: {i}/{len(sigs)}")

            transfers = await self.parse_transaction(sig, creator)

            for t in transfers["inbound"]:
                inbound_total += t["amount"]
                inbound_txs.append(t)
                self.stats['inbound_transfers'] += 1
                self.stats['total_sol_inbound'] += t["amount"]

            for t in transfers["outbound"]:
                outbound_total += t["amount"]
                outbound_txs.append(t)
                self.stats['outbound_transfers'] += 1
                self.stats['total_sol_outbound'] += t["amount"]

        # Store in database
        self._store_transfers(creator, inbound_txs, outbound_txs)

        print(f"   ✓ Inbound: {len(inbound_txs)} txs, {inbound_total:.4f} SOL")
        print(f"   ✓ Outbound: {len(outbound_txs)} txs, {outbound_total:.4f} SOL")

        self.stats['creators_processed'] += 1

    def _store_transfers(self, creator: str, inbound: List[Dict], outbound: List[Dict]):
        """Store transfers in database"""
        try:
            # Store outbound
            for tx in outbound:
                self.cursor.execute("""
                    INSERT OR IGNORE INTO creator_sol_transfers
                    (creator_address, destination_address, total_amount, transfer_count)
                    VALUES (?, ?, ?, 1)
                """, (creator, "unknown_destination", tx["amount"]))

            # Store inbound
            for tx in inbound:
                self.cursor.execute("""
                    INSERT OR IGNORE INTO creator_funders
                    (creator_address, funder_address, amount_sol)
                    VALUES (?, ?, ?)
                """, (creator, "unknown_funder", tx["amount"]))

            self.conn.commit()
        except Exception as e:
            print(f"[DB] Error storing transfers: {e}")
            self.stats['errors'] += 1

    async def run(self):
        """Run comprehensive extraction"""
        creators = self.get_all_creators()
        print(f"\n🚀 Starting comprehensive SOL extraction for {len(creators)} creators")
        print("="*80)

        for creator in creators:
            try:
                await self.extract_for_creator(creator)
            except Exception as e:
                print(f"[ERROR] Failed to process {creator}: {e}")
                self.stats['errors'] += 1
                continue

        self._print_summary()

    def _print_summary(self):
        """Print extraction summary"""
        print("\n" + "="*80)
        print("EXTRACTION COMPLETE")
        print("="*80)
        print(f"\nProcessed: {self.stats['creators_processed']} creators")
        print(f"\nInbound Transfers:")
        print(f"  Total: {self.stats['inbound_transfers']}")
        print(f"  SOL: {self.stats['total_sol_inbound']:.4f}")
        print(f"\nOutbound Transfers:")
        print(f"  Total: {self.stats['outbound_transfers']}")
        print(f"  SOL: {self.stats['total_sol_outbound']:.4f}")
        print(f"\nErrors: {self.stats['errors']}")

async def main():
    extractor = ComprehensiveSOLExtractor()
    await extractor.run()

if __name__ == "__main__":
    asyncio.run(main())
