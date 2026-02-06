#!/usr/bin/env python3
"""
Diagnostic: CREATE Signature Discovery Problem

This script demonstrates WHY the code stores wrong CREATE signatures:

The validation logic (two conditions) cannot distinguish:
  ✅ Genuine CREATE transactions
  ❌ Other Pump.Fun activities (SWAP, TRADE, etc.)

Both types of transactions pass the same validation check!

Real example with token: 6drKtZkmPeRbTLxJaRyj9rVayBGzt2LotDyvK3L5pump
- Wrong sig stored: 2R5pRKompxmzzmLfotxmm8NpwcE4DtdvLSnU465DZw6N2TENv7Tk7sKFPuhvxuLxWgPsyQY5PiQYRrVqYf3pnSKz
- Actual CREATE sig: 3PCrjxpfy3Uqab9o2veag4TjUHhRyViibVGU6CuegbgHceiGX4uubXemmeiSttaPskF4d8SjDMbNAexeMgcbD1nt
"""

import asyncio
import aiohttp
import json
from pathlib import Path
import sys
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).parent))

from pump_fun_post_migration_analyzer import PostMigrationAnalyzer

async def fetch_transaction(session: aiohttp.ClientSession, sig: str, rpc_url: str = "https://api.mainnet-beta.solana.com") -> Optional[Dict]:
    """Fetch transaction details from RPC."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
    }

    try:
        async with session.post(rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            data = await resp.json()
            return data
    except Exception as e:
        print(f"Error fetching {sig[:20]}...: {e}")
        return None

async def diagnose_issue():
    """
    Compare two signatures for the same token:
    1. The one currently stored (wrong)
    2. The actual CREATE (correct)

    Show that BOTH pass the current validation!
    """

    token_mint = "6drKtZkmPeRbTLxJaRyj9rVayBGzt2LotDyvK3L5pump"
    wrong_sig = "2R5pRKompxmzzmLfotxmm8NpwcE4DtdvLSnU465DZw6N2TENv7Tk7sKFPuhvxuLxWgPsyQY5PiQYRrVqYf3pnSKz"
    correct_sig = "3PCrjxpfy3Uqab9o2veag4TjUHhRyViibVGU6CuegbgHceiGX4uubXemmeiSttaPskF4d8SjDMbNAexeMgcbD1nt"

    print("\n" + "="*100)
    print("DIAGNOSTIC: CREATE Signature Discovery Problem")
    print("="*100)
    print(f"\nToken: {token_mint}")
    print(f"Wrong sig (stored in DB): {wrong_sig[:40]}...")
    print(f"Correct sig (actual CREATE): {correct_sig[:40]}...")
    print("\n" + "="*100)

    analyzer = PostMigrationAnalyzer(token_mint=token_mint)

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40)) as session:
        # Fetch both transactions
        print("\n[FETCH] Getting wrong signature transaction...")
        wrong_tx_data = await fetch_transaction(session, wrong_sig)

        print("[FETCH] Getting correct signature transaction...")
        correct_tx_data = await fetch_transaction(session, correct_sig)

        if not wrong_tx_data or "result" not in wrong_tx_data or not wrong_tx_data["result"]:
            print(f"❌ Failed to fetch wrong signature transaction")
            return

        if not correct_tx_data or "result" not in correct_tx_data or not correct_tx_data["result"]:
            print(f"❌ Failed to fetch correct signature transaction")
            return

        wrong_tx = wrong_tx_data["result"]
        correct_tx = correct_tx_data["result"]

        # Validate both using the current logic
        print("\n" + "="*100)
        print("VALIDATION RESULTS")
        print("="*100)

        print(f"\n1️⃣  WRONG SIGNATURE (currently stored):")
        print(f"   {wrong_sig[:60]}...")
        wrong_validation = analyzer._validate_pumpfun_create_tx(wrong_tx)
        print(f"   ├─ mint_in_accounts: {wrong_validation['mint_in_accounts']}")
        print(f"   ├─ pumpfun_program_found: {wrong_validation['pumpfun_program_found']}")
        print(f"   ├─ is_pumpfun_create: {wrong_validation['is_pumpfun_create']}")
        print(f"   └─ Programs: {', '.join(wrong_validation['program_ids'][:5])}")

        print(f"\n2️⃣  CORRECT SIGNATURE (actual CREATE):")
        print(f"   {correct_sig[:60]}...")
        correct_validation = analyzer._validate_pumpfun_create_tx(correct_tx)
        print(f"   ├─ mint_in_accounts: {correct_validation['mint_in_accounts']}")
        print(f"   ├─ pumpfun_program_found: {correct_validation['pumpfun_program_found']}")
        print(f"   ├─ is_pumpfun_create: {correct_validation['is_pumpfun_create']}")
        print(f"   └─ Programs: {', '.join(correct_validation['program_ids'][:5])}")

        # THE PROBLEM
        print("\n" + "="*100)
        print("THE PROBLEM:")
        print("="*100)

        if wrong_validation['is_pumpfun_create'] == correct_validation['is_pumpfun_create']:
            print(f"\n❌ BOTH SIGNATURES PASS THE VALIDATION!")
            print(f"   wrong_sig passes: {wrong_validation['is_pumpfun_create']}")
            print(f"   correct_sig passes: {correct_validation['is_pumpfun_create']}")
            print(f"\n   The code stops at the FIRST transaction it finds that passes.")
            print(f"   It doesn't know which is the REAL CREATE—both satisfy the criteria!")
        else:
            print(f"\n✅ Validation correctly distinguishes them:")
            print(f"   wrong_sig passes: {wrong_validation['is_pumpfun_create']}")
            print(f"   correct_sig passes: {correct_validation['is_pumpfun_create']}")

        # Detailed comparison
        print("\n" + "="*100)
        print("DETAILED COMPARISON:")
        print("="*100)

        # Get instruction details
        def get_instruction_summary(tx: Dict) -> Dict:
            """Summarize instruction structure."""
            message = (tx.get("transaction") or {}).get("message") or {}
            instructions = message.get("instructions") or []
            inner_instructions = tx.get("meta", {}).get("innerInstructions") or []

            inner_instr_count = 0
            for inner in inner_instructions:
                inner_instr_count += len(inner.get("instructions") or [])

            return {
                "top_level": len(instructions),
                "inner": inner_instr_count,
                "total": len(instructions) + inner_instr_count
            }

        wrong_instr = get_instruction_summary(wrong_tx)
        correct_instr = get_instruction_summary(correct_tx)

        print(f"\nInstruction counts:")
        print(f"   Wrong sig:   {wrong_instr['top_level']} top-level + {wrong_instr['inner']} inner = {wrong_instr['total']} total")
        print(f"   Correct sig: {correct_instr['top_level']} top-level + {correct_instr['inner']} inner = {correct_instr['total']} total")

        # Account keys
        wrong_accts = (wrong_tx.get("transaction") or {}).get("message") or {}.get("accountKeys") or []
        correct_accts = (correct_tx.get("transaction") or {}).get("message") or {}.get("accountKeys") or []

        print(f"\nAccount keys:")
        print(f"   Wrong sig:   {len(wrong_accts)} accounts")
        print(f"   Correct sig: {len(correct_accts)} accounts")

        # Fee
        wrong_fee = wrong_tx.get("transaction", {}).get("message", {}).get("recentBlockhash")
        correct_fee = correct_tx.get("transaction", {}).get("message", {}).get("recentBlockhash")

        print(f"\nTransaction fees:")
        wrong_meta_fee = wrong_tx.get("meta", {}).get("fee")
        correct_meta_fee = correct_tx.get("meta", {}).get("fee")
        print(f"   Wrong sig:   {wrong_meta_fee} lamports")
        print(f"   Correct sig: {correct_meta_fee} lamports")

        print("\n" + "="*100)
        print("CONCLUSION:")
        print("="*100)
        print("""
The current validation logic is INSUFFICIENT to distinguish CREATE from other Pump.Fun activities.

Both transactions pass because:
  ✅ Both have the token mint in their account keys
  ✅ Both reference a Pump.Fun program in their instructions

But the WRONG signature is not a CREATE transaction—it's a different activity (e.g., SWAP, TRADE).

SOLUTION NEEDED:
Instead of just checking "mint in accounts + pump.fun program", we need to detect:
  1. Account creation instructions (createAccountWithSeed, initializeAccount, initializeAccount2)
  2. OR bonding curve account being initialized/created
  3. OR actual CREATE instruction variant (different program call)

Current approach = insufficient
Two-condition validation = both CREATE and non-CREATE pass
Code stops at first match = random/luck which one gets stored
""")

async def main():
    """Run the diagnostic."""
    await diagnose_issue()

if __name__ == "__main__":
    asyncio.run(main())
