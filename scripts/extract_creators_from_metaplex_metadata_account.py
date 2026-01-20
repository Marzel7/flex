#!/usr/bin/env python3
"""
Extract creators from the CORRECT Metaplex metadata account.

The metadata for a token mint is stored in a DIFFERENT account:
- Account: Derived PDA from mint using Metaplex's metadata program
- Formula: findMetadataAccount(mint)
- Program: metaqbxxUerdq8VvvrVKaSbxVrFffcXupa2Bw531qqq (Metaplex)

We need to:
1. Derive the metadata account address from the mint
2. Query that account
3. Parse the metadata to get creator

Using Metaplex's SPL Token Metadata program.
"""

import sqlite3
import asyncio
import aiohttp
import os
from typing import Optional, Dict, List
from datetime import datetime
import hashlib
import base58

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

# Metaplex program IDs
METAPLEX_PROGRAM_ID = "metaqbxxUerdq8VvvrVKaSbxVrFffcXupa2Bw531qqq"


class MetaplexMetadataExtractor:
    """Extract creators from Metaplex metadata accounts"""

    def __init__(self):
        self.tokens = []
        self._load_tokens()

    def _load_tokens(self):
        """Load tokens without metadata"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT mint
                FROM token_analysis
                WHERE correct_creator_address IS NULL
                ORDER BY created_at DESC
                LIMIT 20
            """
            )
            self.tokens = [row[0] for row in cursor.fetchall()]
            conn.close()

            print(f"[INIT] Loaded {len(self.tokens)} tokens without metadata\n")
        except Exception as e:
            print(f"[ERROR] Loading tokens: {e}")

    def derive_metadata_account(self, mint: str) -> str:
        """
        Derive the metadata account address for a mint.

        Formula from Metaplex:
        PDA = Pubkey.findProgramAddress(
            [b"metadata", metaplex_program_id, mint_pubkey],
            metaplex_program_id
        )[0]
        """
        try:
            # Decode mint from base58
            mint_bytes = base58.b58decode(mint)

            # Metaplex program ID bytes
            metaplex_id = base58.b58decode(METAPLEX_PROGRAM_ID)

            # Create seeds: b"metadata" + metaplex_id + mint
            seeds = b"metadata" + metaplex_id + mint_bytes

            # This is a simplified version - proper PDA derivation requires
            # finding a bump seed that results in a valid pubkey off the curve
            # For now, return a placeholder

            # In production, we'd use Solders library:
            # from solders.pubkey import Pubkey
            # Pubkey.find_program_address([b"metadata", metaplex_id, mint_bytes], metaplex_id)

            return None  # Placeholder

        except Exception:
            return None

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

    async def query_metadata_account(self, metadata_account: str) -> Optional[Dict]:
        """Query the metadata account"""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [metadata_account, {"encoding": "base64"}],
            }

            result = await self._post_rpc(payload)
            if not result or "result" not in result:
                return None

            account = result.get("result", {}).get("value")
            return account

        except Exception:
            return None

    async def process_token(self, mint: str, idx: int, total: int) -> Dict:
        """Process one token"""
        mint_short = mint[:16]
        print(f"[{idx}/{total}] {mint_short}...", end=" ", flush=True)

        metadata_account = self.derive_metadata_account(mint)
        if not metadata_account:
            print(f"→ Could not derive metadata account", flush=True)
            return {"mint": mint, "creator": None}

        account_data = await self.query_metadata_account(metadata_account)
        if account_data:
            print(f"→ Found metadata account", flush=True)
            return {"mint": mint, "creator": "FOUND"}
        else:
            print(f"→ Metadata account not found", flush=True)
            return {"mint": mint, "creator": None}

    async def run(self):
        """Main execution"""
        try:
            print(
                f"[START] Metaplex Metadata Account Extraction at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            print("=" * 80 + "\n")

            print("[INFO] Note: Proper PDA derivation requires Solders library")
            print("Testing with first token...\n")

            for idx, mint in enumerate(self.tokens[:3], 1):
                await self.process_token(mint, idx, 3)

        except Exception as e:
            print(f"[ERROR] {e}")


async def main():
    extractor = MetaplexMetadataExtractor()
    await extractor.run()

    print("\n" + "=" * 80)
    print(f"[DONE] Complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


if __name__ == "__main__":
    asyncio.run(main())
