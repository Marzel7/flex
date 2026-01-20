#!/usr/bin/env python3
"""
Extract token creators from Pump.Fun API.

Pump.Fun likely exposes token metadata via their own API,
which might have the actual creator information.

Try: https://pump.fun/api/v1/token/{mint}
or: https://api.pump.fun/v1/tokens/{mint}
"""

import asyncio
import aiohttp
import sqlite3
from datetime import datetime
from typing import Optional, Dict, List

DB_PATH = "pumpswap_tokens.db"


class PumpFunCreatorExtractor:
    """Extract creators from Pump.Fun API"""

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
                SELECT mint
                FROM token_analysis
                WHERE correct_creator_address IS NULL
                ORDER BY created_at DESC
                LIMIT 30
            """
            )
            self.tokens = [row[0] for row in cursor.fetchall()]
            conn.close()

            print(f"[INIT] Loaded {len(self.tokens)} tokens without metadata\n")
        except Exception as e:
            print(f"[ERROR] Loading tokens: {e}")

    async def _fetch_pumpfun_api(self, mint: str) -> Optional[Dict]:
        """Try various Pump.Fun API endpoints"""
        endpoints = [
            f"https://pump.fun/api/v1/token/{mint}",
            f"https://api.pump.fun/v1/tokens/{mint}",
            f"https://api.pump.fun/token/{mint}",
            f"https://pump.fun/api/token/{mint}",
        ]

        try:
            async with aiohttp.ClientSession() as session:
                for endpoint in endpoints:
                    try:
                        async with session.get(
                            endpoint,
                            timeout=aiohttp.ClientTimeout(total=5),
                        ) as resp:
                            if resp.status == 200:
                                return await resp.json()
                    except:
                        continue
        except:
            pass

        return None

    async def process_token(self, mint: str, idx: int, total: int) -> Dict:
        """Process one token"""
        mint_short = mint[:16]
        print(f"[{idx}/{total}] {mint_short}...", end=" ", flush=True)

        data = await self._fetch_pumpfun_api(mint)

        if data:
            print(f"✅ Found API data", flush=True)
            return {"mint": mint, "data": data}
        else:
            print(f"❌ No API data", flush=True)
            return {"mint": mint, "data": None}

    async def run(self):
        """Main execution"""
        try:
            print(
                f"[START] Pump.Fun API Creator Extraction at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            print("=" * 80 + "\n")

            results = []
            for idx, mint in enumerate(self.tokens, 1):
                result = await self.process_token(mint, idx, len(self.tokens))
                results.append(result)

            # Summary
            print(f"\n{'='*80}")
            found = [r for r in results if r["data"]]
            print(f"[SUMMARY]")
            print(f"  Tokens queried: {len(self.tokens)}")
            print(f"  API responses: {len(found)}")

            if found:
                print(f"\n[SAMPLE API RESPONSE]")
                import json
                print(json.dumps(found[0]["data"], indent=2)[:500])

        except Exception as e:
            print(f"[ERROR] {e}")


async def main():
    extractor = PumpFunCreatorExtractor()
    await extractor.run()

    print("=" * 80)
    print(f"[DONE] Complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


if __name__ == "__main__":
    asyncio.run(main())
