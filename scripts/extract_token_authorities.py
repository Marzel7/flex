#!/usr/bin/env python3
"""
Extract REAL token authorities/creators from on-chain token metadata.

For each token mint:
1. Fetch the token account from on-chain state
2. Extract the mint authority (the actual creator)
3. Store this as the real creator address
4. Then we can track their SOL transfers
"""

import sqlite3
import asyncio
import aiohttp
import os
from typing import Optional, Dict
from datetime import datetime
from base64 import b64decode
import struct

DB_PATH = "pumpswap_tokens.db"

# RPC configuration
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
        self.rpc_index = 0

    async def _post_rpc(self, payload: dict, timeout: int = 10) -> Optional[dict]:
        """Post to RPC with failover"""
        try:
            async with aiohttp.ClientSession() as session:
                for i, rpc_url in enumerate(RPC_URLS):
                    try:
                        async with session.post(
                            rpc_url,
                            json=payload,
                            timeout=aiohttp.ClientTimeout(total=timeout)
                        ) as resp:
                            if resp.status == 200:
                                return await resp.json()
                    except Exception:
                        continue
                return None
        except Exception as e:
            print(f"[RPC_ERROR] {e}")
            return None

    async def get_token_authority(self, mint: str) -> Optional[str]:
        """Extract mint authority from token metadata"""
        try:
            # Get the token account data
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [mint, {"encoding": "base64"}]
            }

            result = await self._post_rpc(payload)
            if not result or "result" not in result:
                return None

            account_info = result["result"]
            if not account_info or not account_info.get("value"):
                return None

            # Get the data field
            data_b64 = account_info["value"].get("data", [None])[0]
            if not data_b64:
                return None

            # Decode base64 data
            data = b64decode(data_b64)

            # Token mint layout:
            # 0-1: decimals (u8 + padding)
            # 2-34: mint_authority (pubkey, 32 bytes)
            # 34-66: supply (u64, 8 bytes)
            # ...

            if len(data) < 82:  # Not enough data
                return None

            # Extract mint authority (32 bytes at offset 2)
            mint_authority_bytes = data[2:34]

            # Convert to base58 address
            from base58 import b58encode
            authority_address = b58encode(mint_authority_bytes).decode()

            return authority_address

        except Exception as e:
            print(f"[ERROR] Failed to extract authority for {mint}: {e}")
            return None

    async def process_token(self, mint: str, token_short: str) -> Optional[str]:
        """Process a single token to extract its authority"""
        authority = await self.get_token_authority(mint)

        if authority:
            print(f"[AUTHORITY] {token_short} → {authority[:40]}...")
            return authority
        else:
            print(f"[NO_AUTHORITY] {token_short}")
            return None

    async def process_all_tokens(self):
        """Process all tokens to extract authorities"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Get all tokens
            cursor.execute("SELECT mint FROM token_analysis ORDER BY created_at DESC")
            tokens = [row[0] for row in cursor.fetchall()]
            conn.close()

            print(f"\n[EXTRACT] Processing {len(tokens)} token mints to extract authorities...\n")

            authorities_found = {}
            no_authority = []

            # Process each token
            for idx, mint in enumerate(tokens, 1):
                token_short = f"{mint[:8]}...{mint[-4:]}"
                print(f"[{idx}/{len(tokens)}]", end=" ")
                authority = await self.process_token(mint, token_short)

                if authority:
                    authorities_found[mint] = authority
                else:
                    no_authority.append(mint)

            # Store extracted authorities
            print(f"\n[STORE] Storing extracted authorities in database...\n")
            await self._store_authorities(authorities_found)

            # Summary
            print(f"\n[SUMMARY]")
            print(f"  Tokens processed: {len(tokens)}")
            print(f"  Authorities extracted: {len(authorities_found)}")
            print(f"  No authority found: {len(no_authority)}")

            # Show top creators (authorities used by multiple tokens)
            if authorities_found:
                creator_counts = {}
                for mint, auth in authorities_found.items():
                    creator_counts[auth] = creator_counts.get(auth, 0) + 1

                top_creators = sorted(
                    creator_counts.items(),
                    key=lambda x: x[1],
                    reverse=True
                )

                print(f"\n[TOP_CREATORS] Authorities used by multiple tokens:\n")
                for auth, count in top_creators[:10]:
                    if count > 1:
                        print(f"  {auth[:40]}... - {count} tokens")

        except Exception as e:
            print(f"[ERROR] Failed to process tokens: {e}")

    async def _store_authorities(self, authorities_found: Dict[str, str]):
        """Store extracted authorities in database"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            for mint, authority in authorities_found.items():
                cursor.execute(
                    "UPDATE token_analysis SET creator_address = ? WHERE mint = ? AND creator_address IS NULL",
                    (authority, mint)
                )

            conn.commit()
            conn.close()
            print(f"✅ Stored {len(authorities_found)} token authorities\n")

        except Exception as e:
            print(f"[DB_ERROR] Failed to store authorities: {e}")


async def main():
    print(f"[EXTRACT] Starting token authority extraction at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*100 + "\n")

    extractor = TokenAuthorityExtractor()
    await extractor.process_all_tokens()

    print("\n" + "="*100)
    print(f"[EXTRACT] Complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


if __name__ == "__main__":
    asyncio.run(main())
