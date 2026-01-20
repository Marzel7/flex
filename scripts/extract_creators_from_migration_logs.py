#!/usr/bin/env python3
"""
Extract creator from migration transaction logs and inner instructions.

Pump.Fun likely emits events in transaction logs that contain creator information.
Parse the migration transaction's logs to find creator data.
"""

import sqlite3
import asyncio
import aiohttp
import os
from typing import Optional, Dict, List
from datetime import datetime
import json

DB_PATH = "pumpswap_tokens.db"

RPC_URLS = []
RPC_URL = os.getenv("RPC_URL", "")
RPC_URL_2 = os.getenv("RPC_URL_2", "")
if RPC_URL:
    RPC_URLS.append(RPC_URL)
if RPC_URL_2:
    RPC_URLS.append(RPC_URL_2)

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
if HELIUS_API_KEY:
    RPC_URLS.append(f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}")
RPC_URLS.append("https://api.mainnet-beta.solana.com")


class MigrationLogCreatorExtractor:
    """Extract creator from migration transaction logs"""

    def __init__(self):
        self.tokens = []
        self._load_tokens()

    def _load_tokens(self):
        """Load tokens with migration transactions"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT mint, migration_tx, creator_address
                FROM token_analysis
                WHERE migration_tx IS NOT NULL AND migration_tx != ''
                  AND correct_creator_address IS NULL
                ORDER BY created_at DESC
                LIMIT 5
            """
            )
            self.tokens = cursor.fetchall()
            conn.close()

            print(f"[INIT] Loaded {len(self.tokens)} sample tokens without metadata\n")
        except Exception as e:
            print(f"[ERROR] Loading tokens: {e}")

    async def _post_rpc(self, payload: dict, timeout: int = 15) -> Optional[dict]:
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
                                if "result" in data:
                                    return data
                    except:
                        continue
        except:
            pass
        return None

    async def get_migration_transaction_logs(self, signature: str) -> Optional[Dict]:
        """Get migration transaction with all logs and inner instructions"""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
            }

            result = await self._post_rpc(payload, timeout=20)
            if not result or "result" not in result:
                return None

            tx = result.get("result")
            if not tx:
                return None

            return {
                "logs": tx.get("meta", {}).get("logMessages", []),
                "inner_instructions": tx.get("meta", {}).get("innerInstructions", []),
                "accounts": tx.get("transaction", {}).get("message", {}).get("accountKeys", []),
            }

        except Exception as e:
            return None

    async def process_token(self, mint: str, sig: str, creator_addr: str, idx: int, total: int) -> Dict:
        """Process one token"""
        mint_short = mint[:16]
        print(f"\n[{idx}/{total}] {mint_short}...")
        print(f"  Migration TX: {sig[:16]}...")
        print(f"  Recorded creator: {creator_addr[:16]}...")

        tx_data = await self.get_migration_transaction_logs(sig)

        if tx_data:
            logs = tx_data.get("logs", [])
            print(f"  Logs ({len(logs)}):")
            for log in logs[:5]:
                print(f"    {log}")

            inner = tx_data.get("inner_instructions", [])
            print(f"  Inner instructions: {len(inner)}")
            if inner:
                for i, instr_set in enumerate(inner[:2]):
                    instructions = instr_set.get("instructions", [])
                    print(f"    Set {i}: {len(instructions)} instructions")

        return {"mint": mint, "signature": sig}

    async def run(self):
        """Main execution"""
        try:
            print(
                f"[START] Migration Log Analysis at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            print("=" * 80)

            for idx, (mint, sig, creator_addr) in enumerate(self.tokens, 1):
                await self.process_token(mint, sig, creator_addr, idx, len(self.tokens))

        except Exception as e:
            print(f"[ERROR] {e}")


async def main():
    extractor = MigrationLogCreatorExtractor()
    await extractor.run()

    print("\n" + "=" * 80)
    print(f"[DONE] Complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


if __name__ == "__main__":
    asyncio.run(main())
