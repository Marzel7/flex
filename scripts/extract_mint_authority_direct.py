#!/usr/bin/env python3
"""
Extract mint authority directly from on-chain token account data.

SPL Token mint account structure (base64 encoded):
- Offset 0-8: Discriminator (for Metaplex)
- Offset 0-32: First account (varies by implementation)
- ...

For SPL Token Program (not Metaplex):
The mint account contains:
- Mint authority (optional)
- Decimals
- etc.

We can decode the raw account data to find the mint authority.
"""

import sqlite3
import asyncio
import aiohttp
import os
import base64
import struct
from typing import Optional, Dict, List
from datetime import datetime

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


class MintAuthorityExtractor:
    """Extract mint authority from raw token account data"""

    def __init__(self):
        self.tokens = []
        self._load_tokens()

    def _load_tokens(self):
        """Load tokens WITHOUT metadata"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT mint
                FROM token_analysis
                WHERE correct_creator_address IS NULL
                ORDER BY created_at DESC
                LIMIT 10
            """
            )
            self.tokens = [row[0] for row in cursor.fetchall()]
            conn.close()

            print(f"[INIT] Loaded {len(self.tokens)} sample tokens without metadata\n")
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
                                if "result" in data:
                                    return data
                    except:
                        continue
        except:
            pass
        return None

    async def get_mint_authority(self, mint: str) -> Optional[Dict]:
        """Get mint authority from raw account data"""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [mint, {"encoding": "base64"}],
            }

            result = await self._post_rpc(payload)
            if not result or "result" not in result:
                return None

            account = result.get("result", {}).get("value")
            if not account:
                return None

            data = account.get("data", [None, None])[0]  # base64 string
            if not data:
                return None

            # Decode base64
            try:
                decoded = base64.b64decode(data)
            except:
                return None

            # SPL Token Mint structure:
            # 0-32: mint_authority (or [255] if None)
            # 32: decimals
            # etc.

            if len(decoded) < 46:  # Not enough data for proper parsing
                return {
                    "length": len(decoded),
                    "raw_hex": decoded[:32].hex(),
                }

            # First 32 bytes is the mint authority (or empty/None marker)
            authority_bytes = decoded[:32]

            # Check if it's all zeros or the None marker
            if authority_bytes == b"\x00" * 32:
                return {"authority": None, "reason": "all_zeros"}
            if authority_bytes == b"\xff" * 32:
                return {"authority": None, "reason": "none_marker"}

            # Try to parse as Pubkey (32 bytes, base58 encoded when displayed)
            # This is tricky - we'd need to convert bytes to base58
            # For now, return the raw hex
            return {
                "raw_bytes": authority_bytes.hex(),
                "length": len(decoded),
            }

        except Exception as e:
            return None

    async def process_token(self, mint: str, idx: int, total: int) -> Dict:
        """Process one token"""
        mint_short = mint[:16]
        print(f"[{idx}/{total}] {mint_short}...", end=" ", flush=True)

        data = await self.get_mint_authority(mint)

        if data:
            print(f"→ Got data: {data}", flush=True)
        else:
            print(f"→ No data", flush=True)

        return {"mint": mint, "data": data}

    async def run(self):
        """Main execution"""
        try:
            print(
                f"[START] Mint Authority Extraction at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            print("=" * 80 + "\n")

            for idx, mint in enumerate(self.tokens, 1):
                await self.process_token(mint, idx, len(self.tokens))

        except Exception as e:
            print(f"[ERROR] {e}")


async def main():
    extractor = MintAuthorityExtractor()
    await extractor.run()

    print("=" * 80)
    print(f"[DONE] Complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


if __name__ == "__main__":
    asyncio.run(main())
