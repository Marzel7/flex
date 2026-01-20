#!/usr/bin/env python3
"""
Parse inner instructions from migration transaction to find creator account.

The Pump.Fun Migrate instruction creates multiple accounts, including:
- Token mint
- Associated token account
- Pool account
- Creator's token account

By analyzing the inner instructions, we can identify which account is the creator's.
"""

import sqlite3
import asyncio
import aiohttp
import os
from typing import Optional, Dict, List
from datetime import datetime
import json

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


class InnerInstructionCreatorExtractor:
    """Extract creator from inner instructions"""

    def __init__(self):
        self.tokens = []
        self._load_tokens()

    def _load_tokens(self):
        """Load tokens with migration transactions"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT mint, migration_tx
                FROM token_analysis
                WHERE migration_tx IS NOT NULL AND migration_tx != ''
                  AND correct_creator_address IS NULL
                ORDER BY created_at DESC
                LIMIT 3
            """
            )
            self.tokens = cursor.fetchall()
            conn.close()

            print(f"[INIT] Loaded {len(self.tokens)} sample tokens\n")
        except Exception as e:
            print(f"[ERROR] Loading tokens: {e}")

    async def _post_rpc(self, payload: dict, timeout: int = 15) -> Optional[dict]:
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

    async def get_migration_transaction(self, signature: str) -> Optional[Dict]:
        """Get full migration transaction"""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
            }

            result = await self._post_rpc(payload, timeout=20)
            if not result or "result" not in result:
                return None

            return result.get("result")

        except Exception:
            return None

    def analyze_inner_instructions(self, tx: Dict, mint: str) -> Optional[str]:
        """Analyze inner instructions to find creator"""

        if not tx:
            return None

        try:
            meta = tx.get("meta", {})
            inner_instructions = meta.get("innerInstructions", [])
            accounts = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])

            print(f"  Total account keys: {len(accounts)}")
            print(f"  Total inner instruction sets: {len(inner_instructions)}")

            # Look for InitializeMint instruction which creates the token mint
            # The creator should be related to who initiated this

            # Typically:
            # Accounts[0] = Fee payer
            # Accounts[1+] = Other accounts involved in transaction
            # One of these should be the creator

            # Parse inner instructions to find account interactions
            account_usage = {}
            for instr_set_idx, instr_set in enumerate(inner_instructions):
                instructions = instr_set.get("instructions", [])
                for instr in instructions:
                    accounts_in_instr = instr.get("accounts", [])
                    for acc_idx in accounts_in_instr:
                        if acc_idx not in account_usage:
                            account_usage[acc_idx] = 0
                        account_usage[acc_idx] += 1

            print(f"  Most used account indices: {sorted(account_usage.items(), key=lambda x: x[1], reverse=True)[:5]}")

            # Account 0 is always fee payer (most used), skip it
            # Look for the next most frequently used account that's not a system/program account

            most_used = sorted(account_usage.items(), key=lambda x: x[1], reverse=True)
            for acc_idx, usage_count in most_used[:10]:
                if acc_idx < len(accounts):
                    acc_addr = accounts[acc_idx]
                    if isinstance(acc_addr, dict):
                        acc_addr = acc_addr.get("pubkey", str(acc_addr))

                    # Skip known program accounts
                    known_programs = [
                        "11111111111111111111111111111111",  # System
                        "TokenkegQfeZyiNwAJsyFbPUwJ6sBw3tnQnAW", # Token Program
                        "ComputeBudget111111111111111111111111111111",  # Compute Budget
                        "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",  # ATA
                        "metaqbxxUerdq8VvvrVKaSbxVrFffcXupa2Bw531qqq",  # Metaplex
                        "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",  # Token Metadata
                    ]

                    if acc_addr not in known_programs:
                        print(f"  Candidate creator (used {usage_count} times): {acc_addr[:16]}...")
                        return acc_addr

            return None

        except Exception as e:
            print(f"  Error analyzing: {e}")
            return None

    async def process_token(self, mint: str, sig: str, idx: int, total: int) -> Dict:
        """Process one token"""
        mint_short = mint[:16]
        print(f"\n[{idx}/{total}] {mint_short}... TX: {sig[:16]}...")

        tx = await self.get_migration_transaction(sig)
        creator = self.analyze_inner_instructions(tx, mint)

        if creator:
            print(f"  ✅ Found creator: {creator[:16]}...")
            return {"mint": mint, "creator": creator}
        else:
            print(f"  ❌ Could not determine creator")
            return {"mint": mint, "creator": None}

    async def run(self):
        """Main execution"""
        try:
            print(
                f"[START] Inner Instruction Parser at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            print("=" * 80)

            for idx, (mint, sig) in enumerate(self.tokens, 1):
                await self.process_token(mint, sig, idx, len(self.tokens))

        except Exception as e:
            print(f"[ERROR] {e}")


async def main():
    extractor = InnerInstructionCreatorExtractor()
    await extractor.run()

    print("\n" + "=" * 80)
    print(f"[DONE] Complete at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")


if __name__ == "__main__":
    asyncio.run(main())
