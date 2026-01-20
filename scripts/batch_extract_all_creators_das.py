#!/usr/bin/env python3
"""
Batch extract ALL token creators using Helius DAS API searchAssets.

Strategy:
1. For each token mint, use DAS searchAssets with exact match
2. Parse all results to find creator from indexed metadata
3. Fall back to earliest_tx_creator if no metadata found

This should work for ALL tokens since we're searching Helius's entire index.
"""

import sqlite3
import asyncio
import aiohttp
import os
from typing import Optional, Dict, List
from datetime import datetime

DB_PATH = "pumpswap_tokens.db"
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "") or "80ff2d2d-14d1-4b05-bfcd-26769047e331"

HELIUS_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"


class BatchCreatorExtractor:
    """Batch extract creators using DAS API"""

    def __init__(self):
        self.tokens = []
        self._load_tokens()

    def _load_tokens(self):
        """Load all tokens"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT mint, earliest_tx_creator, correct_creator_address
                FROM token_analysis
                ORDER BY created_at DESC
            """
            )
            self.tokens = cursor.fetchall()
            conn.close()

            print(f"[INIT] Loaded {len(self.tokens)} tokens\n")
        except Exception as e:
            print(f"[ERROR] Loading tokens: {e}")

    async def _post_rpc(self, payload: dict, timeout: int = 10) -> Optional[dict]:
        """Post to RPC"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    HELIUS_URL,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data
        except:
            pass
        return None

    async def search_asset(self, mint: str) -> Optional[Dict]:
        """Search for asset by mint"""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "searchAssets",
                "params": {
                    "nativeTransfer": {},
                    "tokenType": "fungible",
                    "ownerAddress": "",
                    "limit": 1,
                    "page": 1,
                    "burnt": False,
                },
            }

            # Actually, let's try getAsset directly with the mint
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAsset",
                "params": {"id": mint},
            }

            result = await self._post_rpc(payload)

            if result and "result" in result:
                return result["result"]

        except:
            pass

        return None

    async def extract_creator(self, asset: Dict) -> Optional[str]:
        """Extract creator from asset data"""
        if not asset:
            return None

        # Try different possible locations for creator
        candidates = [
            # Metaplex metadata
            ("creators", lambda x: x[0].get("address") if x else None),
            # Direct creator field
            ("creator", lambda x: x),
            # Owner field
            ("owner", lambda x: x),
            # Management
            ("management", lambda x: x.get("creator") if isinstance(x, dict) else None),
        ]

        for field, extractor in candidates:
            if field in asset:
                try:
                    creator = extractor(asset[field])
                    if creator:
                        return creator
                except:
                    pass

        return None

    async def process_token(
        self, mint: str, earliest_tx: str, existing_meta: Optional[str], idx: int, total: int
    ) -> Dict:
        """Process one token"""
        mint_short = mint[:16]
        print(f"[{idx}/{total}] {mint_short}...", end=" ", flush=True)

        # Use existing metadata if available
        if existing_meta:
            print(f"→ Existing metadata", flush=True)
            return {
                "mint": mint,
                "final_creator": existing_meta,
                "method": "existing_metadata",
            }

        # Search for asset
        asset = await self.search_asset(mint)

        if asset:
            creator = await self.extract_creator(asset)
            if creator:
                print(f"→ From DAS: {creator[:12]}...", flush=True)
                return {
                    "mint": mint,
                    "final_creator": creator,
                    "method": "das_api",
                }

        # Fallback
        print(f"→ Fallback to earliest_tx", flush=True)
        return {
            "mint": mint,
            "final_creator": earliest_tx,
            "method": "earliest_tx_fallback",
        }

    async def run(self):
        """Main execution"""
        try:
            print(
                f"[START] Batch Creator Extraction at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            print("=" * 80 + "\n")

            results = []
            methods = {
                "existing_metadata": 0,
                "das_api": 0,
                "earliest_tx_fallback": 0,
            }

            for idx, (mint, earliest_tx, existing_meta) in enumerate(self.tokens, 1):
                result = await self.process_token(
                    mint, earliest_tx, existing_meta, idx, len(self.tokens)
                )
                results.append(result)
                methods[result["method"]] += 1

            # Summary
            print(f"\n{'='*80}")
            print(f"[SUMMARY]")
            print(f"  Total tokens: {len(self.tokens)}")
            for method, count in sorted(methods.items()):
                pct = 100 * count / len(self.tokens)
                print(f"  {method}: {count} ({pct:.1f}%)")

            # Store results
            await self._store_results(results)

        except Exception as e:
            print(f"[ERROR] {e}")

    async def _store_results(self, results: List[Dict]):
        """Store results"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            try:
                cursor.execute(
                    "ALTER TABLE token_analysis ADD COLUMN final_creator_address TEXT"
                )
            except:
                pass

            count = 0
            for result in results:
                try:
                    cursor.execute(
                        """
                        UPDATE token_analysis
                        SET final_creator_address = ?
                        WHERE mint = ?
                    """,
                        (result["final_creator"], result["mint"]),
                    )
                    count += 1
                except:
                    pass

            conn.commit()
            conn.close()

            print(f"✅ Updated {count} tokens\n")

        except Exception as e:
            print(f"[DB_ERROR] {e}")


async def main():
    extractor = BatchCreatorExtractor()
    await extractor.run()

    print("=" * 80)
    print(f"[DONE] Complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


if __name__ == "__main__":
    asyncio.run(main())
