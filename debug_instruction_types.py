#!/usr/bin/env python3
"""Debug script to see actual instruction types in the transactions."""

import asyncio
import aiohttp
import json

async def debug_instructions():
    token_mint = "6drKtZkmPeRbTLxJaRyj9rVayBGzt2LotDyvK3L5pump"
    wrong_sig = "2R5pRKompxmzzmLfotxmm8NpwcE4DtdvLSnU465DZw6N2TENv7Tk7sKFPuhvxuLxWgPsyQY5PiQYRrVqYf3pnSKz"
    correct_sig = "3PCrjxpfy3Uqab9o2veag4TjUHhRyViibVGU6CuegbgHceiGX4uubXemmeiSttaPskF4d8SjDMbNAexeMgcbD1nt"

    async with aiohttp.ClientSession() as session:
        for label, sig in [("WRONG (SELL)", wrong_sig), ("CORRECT (CREATE)", correct_sig)]:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
            }

            async with session.post("https://api.mainnet-beta.solana.com", json=payload) as resp:
                data = await resp.json()
                tx = data["result"]

                print(f"\n{'='*100}")
                print(f"{label}")
                print(f"{'='*100}")

                message = tx.get("transaction", {}).get("message", {})
                instructions = message.get("instructions", [])
                inner_instructions = tx.get("meta", {}).get("innerInstructions", [])

                print(f"\nTOP-LEVEL INSTRUCTIONS ({len(instructions)}):")
                for i, instr in enumerate(instructions):
                    prog_id = instr.get("programId", "?")[:16]
                    itype = instr.get("type", "?")
                    parsed = instr.get("parsed", {})
                    ptype = parsed.get("type", "?") if parsed else "?"
                    print(f"  [{i}] Program: {prog_id}... | type: {itype} | parsed.type: {ptype}")

                print(f"\nINNER INSTRUCTIONS ({len(inner_instructions)} groups):")
                for g, inner_group in enumerate(inner_instructions):
                    inner_instrs = inner_group.get("instructions", [])
                    print(f"  Group {g} ({len(inner_instrs)} instructions):")
                    for j, instr in enumerate(inner_instrs):
                        prog_id = instr.get("programId", "?")[:16]
                        itype = instr.get("type", "?")
                        parsed = instr.get("parsed", {})
                        ptype = parsed.get("type", "?") if parsed else "?"
                        print(f"    [{j}] Program: {prog_id}... | type: {itype} | parsed.type: {ptype}")

asyncio.run(debug_instructions())
