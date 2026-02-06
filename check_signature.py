#!/usr/bin/env python3
"""Check what transaction this signature is."""

import asyncio
import aiohttp
import json
from pump_fun_post_migration_analyzer import PostMigrationAnalyzer

async def check_sig():
    sig = "2vMbMSiHnydu52RPDBt7SHrxtrPYntX4pnvieXUnXYANKW6HWP8F5UrD3iQYyNM1y2CTcMeAd6NCFTe1G7vYzWc7"

    async with aiohttp.ClientSession() as session:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
        }

        async with session.post("https://api.mainnet-beta.solana.com", json=payload) as resp:
            data = await resp.json()

            if not data.get("result"):
                print(f"Transaction not found or error: {data.get('error')}")
                return

            tx = data["result"]

            print(f"\n{'='*100}")
            print(f"SIGNATURE: {sig[:40]}...")
            print(f"{'='*100}")

            message = tx.get("transaction", {}).get("message", {})
            instructions = message.get("instructions", [])
            inner_instructions = tx.get("meta", {}).get("innerInstructions", [])

            print(f"\nTOP-LEVEL INSTRUCTIONS ({len(instructions)}):")
            for i, instr in enumerate(instructions):
                prog_id = instr.get("programId", "?")[:20]
                itype = instr.get("type", "?")
                parsed = instr.get("parsed", {})
                ptype = parsed.get("type", "?") if parsed else "?"
                print(f"  [{i}] Program: {prog_id}... | type: {itype} | parsed.type: {ptype}")

            print(f"\nINNER INSTRUCTIONS ({len(inner_instructions)} groups):")
            for g, inner_group in enumerate(inner_instructions):
                inner_instrs = inner_group.get("instructions", [])
                print(f"  Group {g} ({len(inner_instrs)} instructions):")
                for j, instr in enumerate(inner_instrs):
                    prog_id = instr.get("programId", "?")[:20]
                    itype = instr.get("type", "?")
                    parsed = instr.get("parsed", {})
                    ptype = parsed.get("type", "?") if parsed else "?"
                    print(f"    [{j}] Program: {prog_id}... | type: {itype} | parsed.type: {ptype}")

asyncio.run(check_sig())
