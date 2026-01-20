#!/usr/bin/env python3
"""
Extract the correct token owner/authority from on-chain token metadata.

For each token mint, query the token account data to get the actual owner.
This is the authoritative source for who controls the token.
"""

import sqlite3
import asyncio
import aiohttp
import os
import base58
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


class TokenOwnerExtractor:
    """Extract correct token owners from on-chain metadata"""

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
                SELECT mint, earliest_tx_creator FROM token_analysis
                ORDER BY created_at DESC
            """
            )
            self.tokens = cursor.fetchall()
            conn.close()

            print(f"[INIT] Loaded {len(self.tokens)} tokens\n")
        except Exception as e:
            print(f"[ERROR] Loading tokens: {e}")

    async def _post_rpc(
        self, payload: dict, timeout: int = 15
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

    async def get_token_owner(self, mint: str) -> Optional[str]:
        """
        Get the actual token owner from token metadata.

        The owner is the account that has control over the token.
        This is found in the token's account data on-chain.
        """
        try:
            # Get token account info with parsed data
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

            # Try parsed data first
            parsed = account_data["value"].get("data", {}).get("parsed", {})
            if parsed and parsed.get("type") == "mint":
                info = parsed.get("info", {})
                owner = info.get("owner")
                if owner:
                    return owner

            # If not parsed, try raw data
            raw_data = account_data["value"].get("data")
            if isinstance(raw_data, list) and len(raw_data) > 0:
                # Try decoding raw base58 data
                try:
                    # Token metadata is at specific offsets
                    # For SPL token: owner is at offset 32-64 (32 bytes)
                    decoded = base58.b58decode(raw_data[0])
                    if len(decoded) >= 64:
                        owner_bytes = decoded[32:64]
                        owner = base58.b58encode(owner_bytes).decode()
                        return owner
                except Exception:
                    pass

            return None

        except Exception as e:
            print(f"[ERROR] Getting owner for {mint[:16]}...: {e}")
            return None

    async def process_token(self, mint: str, earliest_tx: str, idx: int, total: int) -> Dict:
        """Process one token"""
        mint_short = mint[:16]
        print(f"[{idx}/{total}] {mint_short}...", end=" ", flush=True)

        owner = await self.get_token_owner(mint)
        if owner:
            print(f"→ Found owner: {owner[:8]}...{owner[-4:]}", flush=True)
        else:
            print(f"→ No owner found", flush=True)

        return {"mint": mint, "owner": owner}

    async def run(self):
        """Main execution"""
        try:
            print(
                f"[START] Token Owner Extraction at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            print("=" * 80 + "\n")

            results = []
            found_count = 0
            updated_count = 0

            for idx, (mint, earliest_tx) in enumerate(self.tokens, 1):
                result = await self.process_token(
                    mint, earliest_tx, idx, len(self.tokens)
                )
                results.append(result)
                if result["owner"]:
                    found_count += 1

            # Store results
            print(f"\n[STORE] Saving {found_count} extracted owners...")
            updated_count = await self._store_results(results)

            # Summary
            print(f"\n{'='*80}")
            print(f"[SUMMARY]")
            print(f"  Tokens processed: {len(self.tokens)}")
            print(f"  Owners found: {found_count}")
            if self.tokens:
                print(
                    f"  Success rate: {100*found_count/len(self.tokens):.1f}%"
                )
            print(f"  Owners updated in DB: {updated_count}")

        except Exception as e:
            print(f"[ERROR] {e}")

    async def _store_results(self, results):
        """Store extracted owners to database"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            count = 0
            for result in results:
                if result["owner"]:
                    try:
                        cursor.execute(
                            """
                            UPDATE token_analysis
                            SET creator_address = ?
                            WHERE mint = ?
                        """,
                            (result["owner"], result["mint"]),
                        )
                        count += 1
                    except Exception as e:
                        print(f"[STORE_ERROR] {result['mint'][:16]}...: {e}")

            conn.commit()
            conn.close()

            print(f"✅ Updated {count} tokens with correct owners\n")
            return count

        except Exception as e:
            print(f"[DB_ERROR] {e}")
            return 0


async def main():
    print(f"[START] Token Owner Extraction")
    print("=" * 80)

    extractor = TokenOwnerExtractor()
    await extractor.run()

    print("=" * 80)
    print(f"[DONE] Complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


if __name__ == "__main__":
    asyncio.run(main())
