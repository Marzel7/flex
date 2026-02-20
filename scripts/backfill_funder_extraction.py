#!/usr/bin/env python3
"""
Backfill funder extraction for all tokens that have funders but no extracted sources.
This handles tokens identified before the automatic extraction system was enabled.
"""

import sqlite3
import sys
import time
sys.path.insert(0, '/Users/kevinkeaveney/Dev/claude/flex')

from funder_incoming_extractor import extract_for_creator

DB_PATH = "pumpswap_tokens.db"

def get_tokens_needing_extraction():
    """Get all creators with funders but no extracted sources"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get creators with funders but no extracted sources
        cursor.execute("""
            SELECT DISTINCT ta.earliest_tx_creator, ta.mint
            FROM token_analysis ta
            WHERE EXISTS (
                SELECT 1 FROM creator_funders cf 
                WHERE cf.creator_address = ta.earliest_tx_creator
            )
            AND NOT EXISTS (
                SELECT 1 FROM funder_incoming_transfers fit
                WHERE fit.funder_address IN (
                    SELECT funder_address FROM creator_funders 
                    WHERE creator_address = ta.earliest_tx_creator
                )
            )
            AND NOT EXISTS (
                SELECT 1 FROM funder_outgoing_transfers fot
                WHERE fot.funder_address IN (
                    SELECT funder_address FROM creator_funders 
                    WHERE creator_address = ta.earliest_tx_creator
                )
            )
            ORDER BY ta.created_at DESC
        """)
        
        results = cursor.fetchall()
        conn.close()
        return results
    except Exception as e:
        print(f"[DB] Error fetching tokens: {e}")
        return []

def main():
    tokens = get_tokens_needing_extraction()
    total = len(tokens)
    
    print(f"\n{'='*80}")
    print(f"[BACKFILL] Found {total} tokens needing funder extraction")
    print(f"{'='*80}\n")
    
    if total == 0:
        print("[RESULT] No tokens need extraction - all up to date!")
        return
    
    completed = 0
    skipped = 0
    
    for i, row in enumerate(tokens, 1):
        creator = row['earliest_tx_creator']
        mint = row['mint']
        
        try:
            print(f"\n[{i}/{total}] Processing {creator[:16]}... (mint: {mint[:16]}...)")
            
            # Extract for this creator
            result = extract_for_creator(creator)
            
            if 'error' in result:
                print(f"  ⚠ Skipped: {result['error']}")
                skipped += 1
            else:
                print(f"  ✅ Completed: {result.get('incoming_found', 0)} incoming, {result.get('outgoing_found', 0)} outgoing")
                completed += 1
            
            # Rate limiting - be gentle on RPC
            if i < total:
                time.sleep(0.5)
        
        except Exception as e:
            print(f"  ❌ Error: {e}")
            skipped += 1
    
    print(f"\n{'='*80}")
    print(f"[COMPLETE] Backfill extraction finished")
    print(f"  Total processed: {total}")
    print(f"  Completed: {completed}")
    print(f"  Skipped: {skipped}")
    print(f"{'='*80}\n")

if __name__ == "__main__":
    main()
