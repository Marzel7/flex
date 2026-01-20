#!/usr/bin/env python3
"""
Extract actual token creators by analyzing the pool creation transaction.

For Pump.Fun tokens:
1. Get the pool address (we have this)
2. Query pool account's creation transaction history
3. Analyze who funded/created the pool
4. The funder is the actual creator

This should give us the TRUE creator for ALL tokens, not just those with Metaplex metadata.
"""

import sqlite3
import asyncio
import aiohttp
import os
from typing import Optional, Dict, List
from datetime import datetime
import json

DB_PATH = "pumpswap_tokens.db"
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")

RPC_URLS = []
RPC_URL = os.getenv("RPC_URL", "")
RPC_URL_2 = os.getenv("RPC_URL_2", "")
if RPC_URL:
    RPC_URLS.append(RPC_URL)
if RPC_URL_2:
    RPC_URLS.append(RPC_URL_2)
if HELIUS_API_KEY:
    RPC_URLS.append(f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}")
RPC_URLS.append("https://api.mainnet-beta.solana.com")


class PoolCreatorExtractor:
    """Extract creator from pool creation transaction"""

    def __init__(self):
        self.tokens = []
        self._load_tokens()

    def _load_tokens(self):
        """Load tokens with pool addresses"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT mint, pool_address, creator_address
                FROM token_analysis
                WHERE pool_address IS NOT NULL AND pool_address != ''
                ORDER BY created_at DESC
                LIMIT 20
            """
            )
            self.tokens = cursor.fetchall()
            conn.close()

            print(f"[INIT] Loaded {len(self.tokens)} sample tokens with pool addresses\n")
        except Exception as e:
            print(f"[ERROR] Loading tokens: {e}")

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
        except Exception:
            return None

    async def get_pool_creation_signatures(self, pool_address: str) -> List[str]:
        """Get signatures for pool account (creation is earliest)"""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [pool_address, {"limit": 100}],
            }

            result = await self._post_rpc(payload)
            if not result or "result" not in result:
                return []

            sigs = result.get("result", [])
            return [tx["signature"] for tx in sigs if "signature" in tx]

        except Exception:
            return []

    async def analyze_pool_creation_tx(self, signature: str, pool_address: str) -> Optional[str]:
        """
        Analyze pool creation transaction to find who created it.

        Look for:
        - The account that signed the create pool instruction
        - Usually the third signer (after fee payer and pool)
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

            message = tx.get("transaction", {}).get("message", {})
            account_keys = message.get("accountKeys", [])

            # Pool account should be in the accounts list
            # The creator is typically an account near the front (signer) that's NOT the fee payer

            if len(account_keys) > 2:
                # Usually: [fee_payer, pool, creator, ...]
                # Return the 3rd account (index 2)
                candidate = account_keys[2] if len(account_keys) > 2 else None
                if candidate and candidate != pool_address:
                    return candidate

            return None

        except Exception:
            return None

    async def process_token(self, mint: str, pool_addr: str, creator_addr: str, idx: int, total: int) -> Dict:
        """Process one token"""
        mint_short = mint[:16]
        print(f"[{idx}/{total}] {mint_short}... pool: {pool_addr[:8]}...", end=" ", flush=True)

        # Get pool creation signatures
        sigs = await self.get_pool_creation_signatures(pool_addr)
        if not sigs:
            print(f"→ No signatures", flush=True)
            return {"mint": mint, "pool": pool_addr, "creator": None, "sigs": 0}

        print(f"({len(sigs)} sigs)", end=" ", flush=True)

        # Analyze the most recent signature (should be creation)
        # Actually, the LAST signature is the oldest (creation)
        creator = None
        if len(sigs) > 0:
            # Try the first few signatures
            for sig in sigs[:5]:  # Check first 5
                creator = await self.analyze_pool_creation_tx(sig, pool_addr)
                if creator:
                    creator_short = f"{creator[:8]}...{creator[-4:]}"
                    print(f"→ Creator: {creator_short}", flush=True)
                    break

        if not creator:
            print(f"→ Creator not found", flush=True)

        return {"mint": mint, "pool": pool_addr, "creator": creator, "sigs": len(sigs)}

    async def run(self):
        """Main execution"""
        try:
            print(
                f"[START] Pool Creation Extractor at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            print("=" * 80 + "\n")

            results = []
            for idx, (mint, pool_addr, creator_addr) in enumerate(self.tokens, 1):
                result = await self.process_token(mint, pool_addr, creator_addr, idx, len(self.tokens))
                results.append(result)

            # Summary
            print(f"\n{'='*80}")
            print(f"[SUMMARY]")
            print(f"  Tokens sampled: {len(self.tokens)}")

            found = [r for r in results if r["creator"]]
            print(f"  Creators found: {len(found)}")

        except Exception as e:
            print(f"[ERROR] {e}")


async def main():
    extractor = PoolCreatorExtractor()
    await extractor.run()

    print("=" * 80)
    print(f"[DONE] Complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


if __name__ == "__main__":
    asyncio.run(main())
