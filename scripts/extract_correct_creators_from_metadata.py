#!/usr/bin/env python3
"""
Extract the CORRECT token creators from Helius DAS API (Metaplex metadata).

The creator_address we extracted from transaction signers was WRONG.
The actual creator is in the Metaplex metadata creators array.

This script:
1. For each token mint
2. Query Helius DAS API getAsset
3. Extract creators[0].address as the actual token creator
4. Store as correct_creator in database
"""

import sqlite3
import asyncio
import aiohttp
import os
from typing import Optional, Dict, List
from datetime import datetime

DB_PATH = "pumpswap_tokens.db"
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")

if not HELIUS_API_KEY:
    print("[ERROR] HELIUS_API_KEY not set in environment")
    exit(1)

HELIUS_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"


class MetadataCreatorExtractor:
    """Extract correct creators from Metaplex metadata via DAS API"""

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
                SELECT mint, created_at
                FROM token_analysis
                ORDER BY created_at DESC
            """
            )
            self.tokens = cursor.fetchall()
            conn.close()

            print(f"[INIT] Loaded {len(self.tokens)} tokens\n")
        except Exception as e:
            print(f"[ERROR] Loading tokens: {e}")

    async def _query_das_api(self, mint: str) -> Optional[Dict]:
        """Query Helius DAS API for token metadata"""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAsset",
                "params": {"id": mint},
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    HELIUS_URL,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if "result" in data and data["result"]:
                            return data["result"]
        except Exception:
            pass

        return None

    async def extract_creator(self, mint: str) -> Optional[str]:
        """Extract the actual creator from Metaplex metadata"""
        try:
            asset = await self._query_das_api(mint)

            if not asset:
                return None

            creators = asset.get("creators", [])
            if creators and len(creators) > 0:
                # First creator is typically the main creator
                return creators[0].get("address")

            return None

        except Exception as e:
            return None

    async def process_token(
        self, mint: str, idx: int, total: int
    ) -> Dict:
        """Process one token"""
        mint_short = mint[:16]
        print(f"[{idx}/{total}] {mint_short}...", end=" ", flush=True)

        creator = await self.extract_creator(mint)

        if creator:
            creator_short = f"{creator[:8]}...{creator[-4:]}"
            print(f"→ {creator_short}", flush=True)
        else:
            print(f"→ Not found", flush=True)

        return {"mint": mint, "creator": creator}

    async def run(self):
        """Main execution"""
        try:
            print(
                f"[START] Metadata Creator Extraction at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            print("=" * 80 + "\n")

            results = []
            found_count = 0

            for idx, (mint, created_at) in enumerate(self.tokens, 1):
                result = await self.process_token(mint, idx, len(self.tokens))
                results.append(result)
                if result["creator"]:
                    found_count += 1

            # Summary
            print(f"\n{'='*80}")
            print(f"[SUMMARY]")
            print(f"  Tokens processed: {len(self.tokens)}")
            print(f"  Creators found: {found_count}")
            if self.tokens:
                print(f"  Success rate: {100*found_count/len(self.tokens):.1f}%")

            # Store results
            await self._store_results(results)

        except Exception as e:
            print(f"[ERROR] {e}")

    async def _store_results(self, results: List[Dict]):
        """Store extracted creators to database"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Create column if it doesn't exist
            cursor.execute(
                """
                ALTER TABLE token_analysis
                ADD COLUMN correct_creator_address TEXT
            """
            )

            count = 0
            for result in results:
                if result["creator"]:
                    try:
                        cursor.execute(
                            """
                            UPDATE token_analysis
                            SET correct_creator_address = ?
                            WHERE mint = ?
                        """,
                            (result["creator"], result["mint"]),
                        )
                        count += 1
                    except Exception as e:
                        pass

            conn.commit()
            conn.close()

            print(f"✅ Updated {count} tokens with correct creators\n")

        except Exception as e:
            print(f"[DB_ERROR] {e}")


async def main():
    extractor = MetadataCreatorExtractor()
    await extractor.run()

    print("=" * 80)
    print(f"[DONE] Complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


if __name__ == "__main__":
    asyncio.run(main())
