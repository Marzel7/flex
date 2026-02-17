#!/usr/bin/env python3
"""
Fast funder interaction extraction - just get addresses they interact with.
NO amount calculations, NO CEX/INFRA classification, NO enrichment.
"""

import sqlite3
import aiohttp
import asyncio
import os
from typing import Dict, Set
from dotenv import load_dotenv

load_dotenv()

DB_PATH = "pumpswap_tokens.db"
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "").strip()

def save_funder_interactions(funder_address: str, interaction_addresses: Set[str]):
    """Save funder interaction addresses to database (just the counterparties)"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()

        for counterparty in interaction_addresses:
            if counterparty == funder_address:
                continue  # Skip self
            
            cursor.execute("""
                INSERT OR IGNORE INTO funder_interactions
                (funder_address, counterparty_address, first_detected_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            """, (funder_address, counterparty))
        
        conn.commit()
        conn.close()
        
        if interaction_addresses:
            print(f"[FUNDER_FAST] ✅ Saved {len(interaction_addresses)} interaction addresses for {funder_address[:16]}...", flush=True)
        
        return len(interaction_addresses)
    
    except Exception as e:
        print(f"[FUNDER_FAST] ⚠ Error saving interactions: {e}", flush=True)
        return 0


async def extract_funder_interactions(funder_address: str) -> Dict:
    """
    Fast extraction: Get all addresses a funder interacts with.
    NO amounts, NO classification, NO enrichment - just addresses.
    """
    interactions = set()
    
    print(f"[FUNDER_FAST] 🔗 Scanning interactions for {funder_address[:16]}...", flush=True)
    
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://api-mainnet.helius-rpc.com/v0/addresses/{funder_address}/transactions"
            
            before_sig = None
            page = 0
            total_txs = 0
            
            while page < 20:  # Limit to 20 pages max
                page += 1
                query_url = f"{url}?api-key={HELIUS_API_KEY}&limit=100&sort-order=desc"
                if before_sig:
                    query_url += f"&before={before_sig}"
                
                try:
                    async with session.get(query_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        if resp.status != 200:
                            break
                        
                        txs = await resp.json()
                        if not txs:
                            break
                        
                        total_txs += len(txs)
                        
                        for tx in txs:
                            # Extract counterparties from native transfers
                            native = tx.get("nativeTransfers") or []
                            for nt in native:
                                frm = nt.get("fromUserAccount")
                                to = nt.get("toUserAccount")
                                amt = nt.get("amount", 0)
                                
                                # Only include transfers > 0.001 SOL to avoid dust
                                if amt > 1_000_000:
                                    if frm == funder_address and to and to != funder_address:
                                        interactions.add(to)
                                    elif to == funder_address and frm and frm != funder_address:
                                        interactions.add(frm)
                        
                        if txs:
                            before_sig = txs[-1].get("signature")
                            await asyncio.sleep(0.1)
                        else:
                            break
                            
                except Exception as e:
                    print(f"[FUNDER_FAST]    Page {page} error: {e}", flush=True)
                    break
        
        print(f"[FUNDER_FAST] ✅ Found {len(interactions)} interaction addresses ({total_txs} txs scanned)", flush=True)
        
        # Save to database
        saved_count = save_funder_interactions(funder_address, interactions)
        
        return {
            "status": "success",
            "funder": funder_address,
            "interaction_count": len(interactions),
            "saved_count": saved_count,
            "total_txs_scanned": total_txs
        }
        
    except Exception as e:
        print(f"[FUNDER_FAST] ⚠ Error: {e}", flush=True)
        return {"status": "error", "funder": funder_address, "error": str(e)}


async def extract_for_creator_funders(creator_address: str) -> Dict:
    """Extract interactions for all funders of a creator"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()
        
        # Get all funders for this creator
        cursor.execute("""
            SELECT DISTINCT funder_address
            FROM creator_funders
            WHERE creator_address = ? AND fully_analyzed = 1 AND is_cex = 0
            ORDER BY amount_sol DESC
        """, (creator_address,))
        
        funders = [row[0] for row in cursor.fetchall()]
        conn.close()
        
        print(f"[FUNDER_FAST] 🚀 Extracting interactions for {len(funders)} funders of {creator_address[:16]}...", flush=True)
        
        results = []
        for funder in funders:
            result = await extract_funder_interactions(funder)
            results.append(result)
            await asyncio.sleep(0.2)  # Rate limit
        
        return {
            "status": "success",
            "creator": creator_address,
            "funders_processed": len(results),
            "results": results
        }
        
    except Exception as e:
        print(f"[FUNDER_FAST] ⚠ Error: {e}", flush=True)
        return {"status": "error", "error": str(e)}


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        creator = sys.argv[1]
        result = asyncio.run(extract_for_creator_funders(creator))
        print(f"\n✅ {result}")
    else:
        print("Usage: python3 funder_fast_interactions.py <creator_address>")
