#!/usr/bin/env python3
"""
Extract REAL token creators from migration transaction signers.

For each migration transaction:
1. Fetch the full transaction
2. Extract the signers (accounts that signed the transaction)
3. The first signer is typically the creator/payer
4. Store the real creator address
5. Then we can trace their SOL transfers
"""

import sqlite3
import asyncio
import aiohttp
import os
from typing import Optional, List, Dict
from datetime import datetime

DB_PATH = "pumpswap_tokens.db"

# RPC configuration
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
RPC_URL = os.getenv("RPC_URL", "")
RPC_URL_2 = os.getenv("RPC_URL_2", "")

RPC_URLS = [url for url in [RPC_URL, RPC_URL_2] if url]
if HELIUS_API_KEY:
    RPC_URLS.append(f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}")
RPC_URLS.append("https://api.mainnet-beta.solana.com")


class RealCreatorExtractor:
    """Extract real creator addresses from migration transaction signers"""

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

    async def get_transaction_signers(self, signature: str) -> List[str]:
        """Extract signers (creators) from a transaction"""
        signers = []

        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [signature, {"encoding": "jsonParsed"}]
            }

            result = await self._post_rpc(payload)
            if not result or "result" not in result:
                return signers

            tx = result["result"]
            if not tx or not tx.get("transaction"):
                return signers

            # Get account keys
            accounts = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])
            if not accounts:
                return signers

            # Get the header which tells us how many signers there are
            header = tx.get("transaction", {}).get("message", {}).get("header", {})
            num_signers = header.get("numSignedAccounts", 0)

            # First N accounts are the signers
            for i in range(min(num_signers, len(accounts))):
                account = accounts[i]
                account_addr = account if isinstance(account, str) else account.get("pubkey", "")
                if account_addr:
                    signers.append(account_addr)

        except Exception as e:
            print(f"[ERROR] Failed to extract signers from {signature}: {e}")

        return signers

    async def process_token(self, mint: str, migration_tx: str, token_short: str) -> Optional[str]:
        """Process a single token's migration transaction"""
        signers = await self.get_transaction_signers(migration_tx)

        if signers:
            # First signer is typically the creator/payer
            creator = signers[0]
            print(f"[CREATOR] {token_short}")
            print(f"  Migration TX: {migration_tx[:20]}...")
            print(f"  Creator (first signer): {creator[:40]}...")
            if len(signers) > 1:
                print(f"  Other signers: {len(signers) - 1}")
            print()
            return creator
        else:
            print(f"[NO_SIGNERS] {token_short} - {migration_tx[:20]}...")
            return None

    async def process_all_tokens(self):
        """Process all tokens to extract real creators"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Get all tokens with migration transactions
            cursor.execute("""
                SELECT mint, migration_tx
                FROM token_analysis
                WHERE migration_tx IS NOT NULL
                ORDER BY created_at DESC
            """)
            tokens = cursor.fetchall()
            conn.close()

            print(f"\n[EXTRACT] Processing {len(tokens)} migration transactions...\n")

            creators_found = {}
            no_signers = []

            # Process each token
            for idx, (mint, migration_tx) in enumerate(tokens, 1):
                token_short = f"{mint[:8]}...{mint[-4:]}"
                print(f"[{idx}/{len(tokens)}]", end=" ")
                creator = await self.process_token(mint, migration_tx, token_short)

                if creator:
                    creators_found[mint] = creator
                else:
                    no_signers.append(mint)

            # Store extracted creators
            print(f"\n[STORE] Storing extracted creators in database...\n")
            await self._store_creators(creators_found)

            # Summary
            print(f"\n[SUMMARY]")
            print(f"  Tokens processed: {len(tokens)}")
            print(f"  Creators extracted: {len(creators_found)}")
            print(f"  No signers found: {len(no_signers)}")

        except Exception as e:
            print(f"[ERROR] Failed to process tokens: {e}")

    async def _store_creators(self, creators_found: Dict[str, str]):
        """Store extracted creators in database"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            for mint, creator in creators_found.items():
                cursor.execute(
                    "UPDATE token_analysis SET creator_address = ? WHERE mint = ?",
                    (creator, mint)
                )

            conn.commit()
            conn.close()
            print(f"✅ Stored {len(creators_found)} creator addresses\n")

        except Exception as e:
            print(f"[DB_ERROR] Failed to store creators: {e}")


async def main():
    print(f"[EXTRACT] Starting real creator extraction at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*100 + "\n")

    extractor = RealCreatorExtractor()
    await extractor.process_all_tokens()

    print("\n" + "="*100)
    print(f"[EXTRACT] Complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


if __name__ == "__main__":
    asyncio.run(main())
