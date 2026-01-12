#!/usr/bin/env python3
"""
Backfill migration transaction signatures for tokens that were analyzed before this feature.
This allows the live price updater to work with existing tokens.
"""

import sqlite3
import asyncio
import aiohttp
import os
from dotenv import load_dotenv

load_dotenv()

DB_PATH = "pumpswap_tokens.db"
RPC_HTTP = os.getenv("RPC_URL", "https://api.mainnet-beta.solana.com")

async def get_token_signatures(mint: str, limit: int = 1000) -> list:
    """Get all signatures for a token's account"""
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [mint, {"limit": limit}]
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(RPC_HTTP, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    sigs = data.get("result", [])
                    return [sig["signature"] for sig in sigs]
    except Exception as e:
        print(f"Error getting signatures for {mint}: {e}")
    return []

async def find_migration_tx(mint: str) -> str:
    """Find the migration transaction by looking for CreateAccount or InitializeMint events"""
    sigs = await get_token_signatures(mint, 100)
    
    for sig in sigs:
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [sig, {"encoding": "json", "maxSupportedTransactionVersion": 0}]
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(RPC_HTTP, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        tx = data.get("result")
                        if not tx:
                            continue
                        
                        meta = tx.get("meta", {})
                        logs = meta.get("logMessages", [])
                        
                        # Look for migration indicators in logs
                        logs_text = " ".join(logs)
                        if "Program pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA invoke" in logs_text:
                            print(f"  ✓ Found migration tx for {mint[:16]}...: {sig[:16]}...")
                            return sig
        except Exception as e:
            print(f"  Error checking tx {sig[:16]}...: {e}")
            continue
    
    print(f"  ⚠ No migration tx found for {mint[:16]}...")
    return None

async def backfill_token(mint: str):
    """Backfill migration_tx for a single token"""
    tx = await find_migration_tx(mint)
    
    if tx:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("UPDATE token_analysis SET migration_tx = ? WHERE mint = ?", (tx, mint))
        conn.commit()
        conn.close()
        print(f"  ✅ Stored migration tx for {mint[:16]}...")
    
    await asyncio.sleep(0.5)  # Rate limit

async def main():
    """Backfill all tokens"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT mint FROM token_analysis WHERE migration_tx IS NULL ORDER BY analyzed_at DESC")
    tokens = [row[0] for row in cursor.fetchall()]
    conn.close()
    
    print(f"\n[BACKFILL] Backfilling {len(tokens)} tokens with migration transactions...\n")
    
    for i, mint in enumerate(tokens, 1):
        print(f"[{i}/{len(tokens)}] {mint[:16]}...")
        await backfill_token(mint)
    
    print(f"\n[BACKFILL] ✓ Complete\n")

if __name__ == "__main__":
    asyncio.run(main())
