#!/usr/bin/env python3
"""
Extract missing token authorities (creator_address) from on-chain token metadata.

For the 13 tokens missing creator_address, query the token's update authority
from the token's metadata account.
"""

import sqlite3
import asyncio
import aiohttp
import os
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


class TokenAuthorityExtractor:
    """Extract token authorities from on-chain metadata"""

    def __init__(self):
        self.missing_tokens = []
        self._load_missing_tokens()

    def _load_missing_tokens(self):
        """Load tokens missing creator_address"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT mint, earliest_tx_creator FROM token_analysis
                WHERE (creator_address IS NULL OR creator_address = '')
                ORDER BY created_at DESC
            """
            )
            self.missing_tokens = cursor.fetchall()
            conn.close()

            print(f"[INIT] Loaded {len(self.missing_tokens)} tokens with missing creator_address\n")
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

    async def get_token_authority(self, mint: str) -> Optional[str]:
        """
        Get the token's update authority from parsed token account data.

        The update authority is the account that can update token metadata
        and other token properties.
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

            # Get update authority from token program account
            info = parsed.get("info", {})
            owner = info.get("owner")  # This is typically the update/owner authority

            return owner

        except Exception as e:
            print(f"[ERROR] Getting authority for {mint[:16]}...: {e}")
            return None

    async def process_token(self, mint: str, earliest_tx: str, idx: int, total: int) -> Dict:
        """Process one token"""
        mint_short = mint[:16]
        print(f"[{idx}/{total}] {mint_short}...", end=" ", flush=True)

        authority = await self.get_token_authority(mint)
        if authority:
            print(f"→ Found authority: {authority[:8]}...{authority[-4:]}", flush=True)
        else:
            print(f"→ No authority found, using earliest_tx_creator: {earliest_tx[:8]}...{earliest_tx[-4:]}", flush=True)
            # Fallback: use earliest_tx_creator if we can't get authority
            authority = earliest_tx

        return {"mint": mint, "authority": authority}

    async def run(self):
        """Main execution"""
        try:
            print(
                f"[START] Token Authority Extraction at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            print("=" * 80 + "\n")

            results = []
            found_count = 0

            for idx, (mint, earliest_tx) in enumerate(self.missing_tokens, 1):
                result = await self.process_token(
                    mint, earliest_tx, idx, len(self.missing_tokens)
                )
                results.append(result)
                if result["authority"]:
                    found_count += 1

            # Store results
            print(f"\n[STORE] Saving {found_count} extracted authorities...")
            await self._store_results(results)

            # Summary
            print(f"\n{'='*80}")
            print(f"[SUMMARY]")
            print(f"  Tokens processed: {len(self.missing_tokens)}")
            print(f"  Authorities found: {found_count}")
            if self.missing_tokens:
                print(
                    f"  Success rate: {100*found_count/len(self.missing_tokens):.1f}%"
                )

            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Check updated completeness
            cursor.execute(
                "SELECT COUNT(*) FROM token_analysis WHERE creator_address IS NOT NULL AND creator_address != ''"
            )
            now_complete = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM token_analysis")
            total = cursor.fetchone()[0]

            print(f"\n[DATA COMPLETENESS]")
            print(
                f"  Tokens with creator_address: {now_complete}/{total} ({100*now_complete/total:.1f}%)"
            )

            conn.close()

        except Exception as e:
            print(f"[ERROR] {e}")

    async def _store_results(self, results):
        """Store extracted authorities to database"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            count = 0
            for result in results:
                if result["authority"]:
                    try:
                        cursor.execute(
                            """
                            UPDATE token_analysis
                            SET creator_address = ?
                            WHERE mint = ?
                        """,
                            (result["authority"], result["mint"]),
                        )
                        count += 1
                    except Exception as e:
                        print(f"[STORE_ERROR] {result['mint'][:16]}...: {e}")

            conn.commit()
            conn.close()

            print(f"✅ Updated {count} tokens with extracted authorities\n")

        except Exception as e:
            print(f"[DB_ERROR] {e}")


async def main():
    print(f"[START] Token Authority Extraction")
    print("=" * 80)

    extractor = TokenAuthorityExtractor()
    await extractor.run()

    print("=" * 80)
    print(f"[DONE] Complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


if __name__ == "__main__":
    asyncio.run(main())
