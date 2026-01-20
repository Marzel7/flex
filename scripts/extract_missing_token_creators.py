#!/usr/bin/env python3
"""
Extract missing token_creator (mint authority) from on-chain token metadata.

For 87 incomplete tokens, query the mint authority and populate the token_creator field.
The token_creator is the account that created the token and has the mint authority.
"""

import sqlite3
import asyncio
import aiohttp
import os
import json
from typing import Optional, Dict
from datetime import datetime

DB_PATH = "pumpswap_tokens.db"

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
RPC_URL = os.getenv("RPC_URL", "")
RPC_URL_2 = os.getenv("RPC_URL_2", "")

RPC_URLS = [url for url in [RPC_URL, RPC_URL_2] if url]
if HELIUS_API_KEY:
    RPC_URLS.append(f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}")
RPC_URLS.append("https://api.mainnet-beta.solana.com")


class TokenCreatorExtractor:
    """Extract token creators from on-chain metadata"""

    def __init__(self):
        self.incomplete_tokens = []
        self._load_incomplete_tokens()

    def _load_incomplete_tokens(self):
        """Load tokens missing token_creator field"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT mint FROM token_analysis
                WHERE token_creator IS NULL OR token_creator = ''
                ORDER BY created_at DESC
            """
            )
            self.incomplete_tokens = [row[0] for row in cursor.fetchall()]
            conn.close()

            print(
                f"[INIT] Loaded {len(self.incomplete_tokens)} incomplete tokens\n"
            )
        except Exception as e:
            print(f"[ERROR] Loading tokens: {e}")

    async def _post_rpc(
        self, payload: dict, timeout: int = 10
    ) -> Optional[dict]:
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

    async def get_mint_authority(self, mint: str) -> Optional[str]:
        """
        Get the mint authority for a token.

        The mint authority is the account that can mint new tokens,
        typically the token creator.
        """
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getParsedAccountInfo",
                "params": [mint],
            }

            result = await self._post_rpc(payload)
            if not result or "result" not in result:
                return None

            account_data = result.get("result")
            if not account_data or not account_data.get("value"):
                return None

            parsed = account_data["value"].get("data", {}).get("parsed", {})
            if not parsed:
                return None

            # Get mint authority from token program account
            info = parsed.get("info", {})
            mint_authority = info.get("mintAuthority")

            return mint_authority

        except Exception as e:
            print(f"[ERROR] Getting mint authority for {mint[:16]}...: {e}")
            return None

    async def process_token(self, mint: str, idx: int, total: int) -> Dict:
        """Process one token"""
        mint_short = mint[:16]
        print(f"[{idx}/{total}] {mint_short}...", end=" ", flush=True)

        creator = await self.get_mint_authority(mint)
        if creator:
            print(f"→ Found creator: {creator[:8]}...{creator[-4:]}", flush=True)
        else:
            print(f"→ No creator found", flush=True)

        return {"mint": mint, "creator": creator}

    async def run(self):
        """Main execution"""
        try:
            print(
                f"[START] Token Creator Extraction at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            print("=" * 80 + "\n")

            results = []
            found_count = 0

            for idx, mint in enumerate(self.incomplete_tokens, 1):
                result = await self.process_token(
                    mint, idx, len(self.incomplete_tokens)
                )
                results.append(result)
                if result["creator"]:
                    found_count += 1

            # Store results
            print(f"\n[STORE] Saving {found_count} extracted creators...")
            await self._store_results(results)

            # Summary
            print(f"\n{'='*80}")
            print(f"[SUMMARY]")
            print(f"  Tokens processed: {len(self.incomplete_tokens)}")
            print(f"  Creators found: {found_count}")
            print(f"  Success rate: {100*found_count/len(self.incomplete_tokens):.1f}%")

            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Check updated completeness
            cursor.execute(
                "SELECT COUNT(*) FROM token_analysis WHERE token_creator IS NOT NULL AND token_creator != ''"
            )
            now_complete = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM token_analysis")
            total = cursor.fetchone()[0]

            print(f"\n[DATA COMPLETENESS]")
            print(
                f"  Tokens with token_creator: {now_complete}/{total} ({100*now_complete/total:.1f}%)"
            )

            conn.close()

        except Exception as e:
            print(f"[ERROR] {e}")

    async def _store_results(self, results):
        """Store extracted creators to database"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            count = 0
            for result in results:
                if result["creator"]:
                    try:
                        cursor.execute(
                            """
                            UPDATE token_analysis
                            SET token_creator = ?
                            WHERE mint = ?
                        """,
                            (result["creator"], result["mint"]),
                        )
                        count += 1
                    except Exception as e:
                        print(f"[STORE_ERROR] {result['mint'][:16]}...: {e}")

            conn.commit()
            conn.close()

            print(f"✅ Updated {count} tokens with extracted creators\n")

        except Exception as e:
            print(f"[DB_ERROR] {e}")


async def main():
    print(f"[START] Token Creator Extraction")
    print("=" * 80)

    extractor = TokenCreatorExtractor()
    await extractor.run()

    print("=" * 80)
    print(f"[DONE] Complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


if __name__ == "__main__":
    asyncio.run(main())
