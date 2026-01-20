#!/usr/bin/env python3
"""
Extract token creators using a comprehensive multi-method approach:

1. PRIMARY: Metaplex metadata via Helius DAS API (for 15% of tokens)
2. FALLBACK: earliest_tx_creator (for remaining 85% of tokens)

This gives us 100% coverage with best-effort accuracy.

The strategy is:
- Use Metaplex creators where available (authoritative source)
- Use earliest_tx_creator as proxy where Metaplex isn't available
- Flag the method used so analysis can account for accuracy differences
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
    print("[ERROR] HELIUS_API_KEY not set")
    exit(1)

HELIUS_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"


class ComprehensiveCreatorExtractor:
    """Extract creators using multi-method approach"""

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

    async def _query_das_api(self, mint: str) -> Optional[str]:
        """Query Helius DAS API for metadata creator"""
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
                        result = data.get("result", {})
                        creators = result.get("creators", [])
                        if creators and len(creators) > 0:
                            return creators[0].get("address")
        except Exception:
            pass

        return None

    async def process_token(
        self, mint: str, earliest_tx: str, existing_metadata: Optional[str], idx: int, total: int
    ) -> Dict:
        """Process one token"""
        mint_short = mint[:16]
        print(f"[{idx}/{total}] {mint_short}...", end=" ", flush=True)

        # If we already have metadata from earlier extraction, use it
        if existing_metadata:
            print(f"→ From metadata (existing)", flush=True)
            return {
                "mint": mint,
                "final_creator": existing_metadata,
                "method": "metaplex_metadata",
            }

        # Try DAS API
        creator = await self._query_das_api(mint)
        if creator:
            creator_short = f"{creator[:8]}...{creator[-4:]}"
            print(f"→ From metadata (new): {creator_short}", flush=True)
            return {"mint": mint, "final_creator": creator, "method": "metaplex_metadata"}

        # Fallback to earliest_tx_creator
        earliest_short = f"{earliest_tx[:8]}...{earliest_tx[-4:]}"
        print(f"→ From earliest_tx: {earliest_short}", flush=True)
        return {
            "mint": mint,
            "final_creator": earliest_tx,
            "method": "earliest_tx_creator_fallback",
        }

    async def run(self):
        """Main execution"""
        try:
            print(
                f"[START] Comprehensive Creator Extraction at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            print("=" * 80 + "\n")

            results = []
            methods = {"metaplex_metadata": 0, "earliest_tx_creator_fallback": 0}

            for idx, (mint, earliest_tx, existing_meta) in enumerate(self.tokens, 1):
                result = await self.process_token(mint, earliest_tx, existing_meta, idx, len(self.tokens))
                results.append(result)
                methods[result["method"]] += 1

            # Summary
            print(f"\n{'='*80}")
            print(f"[SUMMARY]")
            print(f"  Total tokens: {len(self.tokens)}")
            print(f"  Using Metaplex metadata: {methods['metaplex_metadata']} ({100*methods['metaplex_metadata']/len(self.tokens):.1f}%)")
            print(f"  Using earliest_tx fallback: {methods['earliest_tx_creator_fallback']} ({100*methods['earliest_tx_creator_fallback']/len(self.tokens):.1f}%)")

            # Store results
            await self._store_results(results)

        except Exception as e:
            print(f"[ERROR] {e}")

    async def _store_results(self, results: List[Dict]):
        """Store final creators to database"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Create new column for final creator and method
            try:
                cursor.execute(
                    "ALTER TABLE token_analysis ADD COLUMN final_creator_address TEXT"
                )
            except:
                pass

            try:
                cursor.execute(
                    "ALTER TABLE token_analysis ADD COLUMN creator_extraction_method TEXT"
                )
            except:
                pass

            count = 0
            for result in results:
                try:
                    cursor.execute(
                        """
                        UPDATE token_analysis
                        SET final_creator_address = ?, creator_extraction_method = ?
                        WHERE mint = ?
                    """,
                        (result["final_creator"], result["method"], result["mint"]),
                    )
                    count += 1
                except Exception as e:
                    pass

            conn.commit()
            conn.close()

            print(f"✅ Updated {count} tokens with final creators\n")

        except Exception as e:
            print(f"[DB_ERROR] {e}")


async def main():
    extractor = ComprehensiveCreatorExtractor()
    await extractor.run()

    print("=" * 80)
    print(f"[DONE] Complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


if __name__ == "__main__":
    asyncio.run(main())
