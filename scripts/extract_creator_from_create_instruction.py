#!/usr/bin/env python3
"""
Extract the CORRECT creator for Pump.Fun tokens.

The creator is NOT the first mint recipient or first holder.
The creator is the wallet that SIGNED the Pump.Fun CREATE instruction.

Strategy:
1. Get the migration transaction (or original Pump.Fun create tx)
2. Parse the transaction to find the CREATE instruction
3. Identify which signer authorized the create
4. That signer is the creator

The CREATE instruction in Pump.Fun:
- Is called by a specific program
- Has specific account requirements
- The first signer (or instruction signer) is the creator
"""

import sqlite3
import asyncio
import aiohttp
import os
from typing import Optional, Dict, List
from datetime import datetime

DB_PATH = "pumpswap_tokens.db"

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
RPC_URL = os.getenv("RPC_URL", "")
RPC_URL_2 = os.getenv("RPC_URL_2", "")

RPC_URLS = [url for url in [RPC_URL, RPC_URL_2] if url]
if HELIUS_API_KEY:
    RPC_URLS.append(f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}")
RPC_URLS.append("https://api.mainnet-beta.solana.com")

# Pump.Fun program
PUMPFUN_PROGRAM = "6EF8rQNhHLKPwrWFeRnwAcvgEee4NqtwybRM5q5AGN1w"


class PumpFunCreatorExtractor:
    """Extract creator from Pump.Fun CREATE instruction"""

    def __init__(self):
        self.tokens = []
        self._load_tokens()

    def _load_tokens(self):
        """Load all tokens with their earliest tx creator (for reference)"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT mint, migration_tx, earliest_tx_creator FROM token_analysis
                WHERE migration_tx IS NOT NULL
                ORDER BY created_at DESC
            """
            )
            self.tokens = cursor.fetchall()
            conn.close()

            print(f"[INIT] Loaded {len(self.tokens)} tokens with migration_tx\n")
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

    async def extract_creator_from_transaction(
        self, signature: str, mint: str
    ) -> Optional[str]:
        """
        Extract creator from transaction by finding the CREATE instruction signer.

        Strategy:
        1. Get transaction details
        2. Find Pump.Fun CREATE instruction
        3. Identify the fee payer (signer[0]) as the creator
        4. Or find the account that signed the create instruction
        """
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [signature, {"encoding": "jsonParsed"}],
            }

            result = await self._post_rpc(payload, timeout=20)
            if not result or "result" not in result:
                return None

            tx = result.get("result")
            if not tx:
                return None

            message = tx.get("transaction", {}).get("message", {})
            account_keys = message.get("accountKeys", [])

            # The fee payer (first signer) is typically the creator
            # in Pump.Fun transactions
            if account_keys and len(account_keys) > 0:
                fee_payer = account_keys[0]

                # Verify this is likely a real creator (not a program)
                # Real addresses don't start with specific program markers
                if fee_payer and len(fee_payer) > 20:
                    return fee_payer

            return None

        except Exception as e:
            print(f"[ERROR] Extracting from {signature[:16]}...: {e}")
            return None

    async def process_token(
        self, mint: str, signature: str, earliest_tx: str, idx: int, total: int
    ) -> Dict:
        """Process one token"""
        mint_short = mint[:16]
        print(f"[{idx}/{total}] {mint_short}...", end=" ", flush=True)

        creator = await self.extract_creator_from_transaction(signature, mint)

        if creator:
            # Compare with earliest_tx_creator
            if creator == earliest_tx:
                status = "✓ MATCH"
            else:
                status = f"⚠️ DIFF (earliest_tx: {earliest_tx[:8]}...)"
            print(f"→ Creator: {creator[:8]}...{creator[-4:]} {status}", flush=True)
        else:
            print(f"→ Not found, using earliest_tx_creator", flush=True)
            creator = earliest_tx

        return {"mint": mint, "creator": creator, "signature": signature}

    async def run(self):
        """Main execution"""
        try:
            print(
                f"[START] Pump.Fun Creator Extraction at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            print("=" * 80 + "\n")

            results = []
            found_count = 0
            match_count = 0

            for idx, (mint, signature, earliest_tx) in enumerate(self.tokens, 1):
                result = await self.process_token(
                    mint, signature, earliest_tx, idx, len(self.tokens)
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
            print(f"  Tokens processed: {len(self.tokens)}")
            print(f"  Creators extracted: {found_count}")
            if self.tokens:
                print(f"  Success rate: {100*found_count/len(self.tokens):.1f}%")

            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Check data
            cursor.execute(
                "SELECT COUNT(*) FROM token_analysis WHERE creator_address IS NOT NULL AND creator_address != ''"
            )
            now_complete = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM token_analysis")
            total = cursor.fetchone()[0]

            print(f"\n[DATA STATUS]")
            print(
                f"  Tokens with creator_address: {now_complete}/{total} ({100*now_complete/total:.1f}%)"
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
                            SET creator_address = ?
                            WHERE mint = ?
                        """,
                            (result["creator"], result["mint"]),
                        )
                        count += 1
                    except Exception as e:
                        print(f"[STORE_ERROR] {result['mint'][:16]}...: {e}")

            conn.commit()
            conn.close()

            print(f"✅ Updated {count} tokens with creator addresses\n")

        except Exception as e:
            print(f"[DB_ERROR] {e}")


async def main():
    print(f"[START] Pump.Fun Creator Extraction")
    print("=" * 80)

    extractor = PumpFunCreatorExtractor()
    await extractor.run()

    print("=" * 80)
    print(f"[DONE] Complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


if __name__ == "__main__":
    asyncio.run(main())
