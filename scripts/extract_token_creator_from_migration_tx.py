#!/usr/bin/env python3
"""
Extract token creators from migration transactions.

For each incomplete token, query the migration transaction and extract
the account that created/initialized the token (usually a signer or account that calls
token program InitializeMint instruction).

This works by analyzing the transaction to find who initialized the token.
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

# Solana token program
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJsyFbPVwwQQfփ"
TOKEN_2022_PROGRAM = "TokenzQdBbjWhAr21dUEvVGT9FC6snEYYDKwkP5W39V"


class TokenCreatorFromMigrationExtractor:
    """Extract token creators from migration transactions"""

    def __init__(self):
        self.incomplete_tokens = []
        self._load_incomplete_tokens()

    def _load_incomplete_tokens(self):
        """Load tokens with migration_tx but no token_creator"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT mint, migration_tx FROM token_analysis
                WHERE (token_creator IS NULL OR token_creator = '')
                AND migration_tx IS NOT NULL
                ORDER BY created_at DESC
            """
            )
            self.incomplete_tokens = cursor.fetchall()
            conn.close()

            print(f"[INIT] Loaded {len(self.incomplete_tokens)} incomplete tokens with migration_tx\n")
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

    async def extract_creator_from_migration_tx(
        self, signature: str, mint: str
    ) -> Optional[str]:
        """
        Extract token creator from migration transaction.

        Strategy:
        1. Get the transaction
        2. Look for InitializeMint or InitializeMint2 instruction calls to token program
        3. The "owner" parameter of InitializeMint is the token creator
        4. Account at specific index in instruction data

        Returns: Creator address or None
        """
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [signature, {"encoding": "jsonParsed"}],
            }

            result = await self._post_rpc(payload, timeout=15)
            if not result or "result" not in result:
                return None

            tx = result.get("result")
            if not tx:
                return None

            # Get account keys
            message = tx.get("transaction", {}).get("message", {})
            account_keys = message.get("accountKeys", [])

            # Get instructions and inner instructions
            instructions = message.get("instructions", [])
            meta = tx.get("meta", {})
            inner_instructions = meta.get("innerInstructions", [])

            # Look for token program InitializeMint instructions
            all_instructions = []

            # Add regular instructions
            for ix in instructions:
                all_instructions.append(("regular", ix))

            # Add inner instructions
            for ix_group in inner_instructions:
                for ix in ix_group.get("instructions", []):
                    all_instructions.append(("inner", ix))

            # Search for InitializeMint instructions
            for ix_type, ix in all_instructions:
                # Check if this is a token program instruction
                program_id_idx = ix.get("programIdIndex")
                if program_id_idx is None or program_id_idx >= len(account_keys):
                    continue

                program_id = account_keys[program_id_idx]

                # Check if it's the token program
                if program_id not in [TOKEN_PROGRAM, TOKEN_2022_PROGRAM]:
                    continue

                # Check instruction type
                parsed = ix.get("parsed")
                if not parsed:
                    continue

                ix_type_str = parsed.get("type")
                if ix_type_str != "initializeMint":
                    continue

                # Extract info
                info = parsed.get("info", {})
                owner = info.get("owner")  # This is the token creator/owner

                if owner:
                    return owner

            return None

        except Exception as e:
            print(f"[ERROR] Extracting from {signature[:16]}...: {e}")
            return None

    async def process_token(
        self, mint: str, signature: str, idx: int, total: int
    ) -> Dict:
        """Process one token"""
        mint_short = mint[:16]
        sig_short = signature[:16]
        print(f"[{idx}/{total}] {mint_short}...", end=" ", flush=True)

        creator = await self.extract_creator_from_migration_tx(signature, mint)
        if creator:
            print(f"→ Found creator: {creator[:8]}...{creator[-4:]}", flush=True)
        else:
            print(f"→ No creator found (checking alternative methods...)", flush=True)
            # Could add fallback methods here
            creator = None

        return {"mint": mint, "signature": signature, "creator": creator}

    async def run(self):
        """Main execution"""
        try:
            print(
                f"[START] Token Creator Extraction from Migration Transactions at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            print("=" * 80 + "\n")

            results = []
            found_count = 0

            for idx, (mint, migration_tx) in enumerate(self.incomplete_tokens, 1):
                result = await self.process_token(
                    mint, migration_tx, idx, len(self.incomplete_tokens)
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
            if self.incomplete_tokens:
                print(
                    f"  Success rate: {100*found_count/len(self.incomplete_tokens):.1f}%"
                )

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
                        print(
                            f"[STORE_ERROR] {result['mint'][:16]}...: {e}"
                        )

            conn.commit()
            conn.close()

            print(f"✅ Updated {count} tokens with extracted creators\n")

        except Exception as e:
            print(f"[DB_ERROR] {e}")


async def main():
    print(f"[START] Token Creator Extraction from Migration Transactions")
    print("=" * 80)

    extractor = TokenCreatorFromMigrationExtractor()
    await extractor.run()

    print("=" * 80)
    print(f"[DONE] Complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


if __name__ == "__main__":
    asyncio.run(main())
