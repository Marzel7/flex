#!/usr/bin/env python3
"""
Extract creator information from Solana Explorer using public API.

For tokens without Metaplex metadata, we can get creator info by:
1. Looking up the token mint creation transaction
2. Finding who initialized the token
3. Extracting the owner/authority from the transaction
"""

import requests
import json
import time
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "pumpswap_tokens.db"

def get_token_info_from_explorer(mint):
    """
    Get token info from Solana Explorer API.
    
    The explorer has a public API that shows token creation details.
    """
    try:
        # Try Solana Explorer token info endpoint
        url = f"https://api.solana.fm/v0/tokens/{mint}"
        resp = requests.get(url, timeout=10)
        
        if resp.status_code == 200:
            data = resp.json()
            if "result" in data:
                token_data = data["result"]
                # Extract mint authority (creator)
                if "owner" in token_data:
                    return token_data["owner"]
                if "authority" in token_data:
                    return token_data["authority"]
                if "mint_authority" in token_data:
                    return token_data["mint_authority"]
    except:
        pass
    
    return None

def extract_creators_from_transactions():
    """Extract creators by looking at token creation transactions"""
    import sqlite3
    
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        cursor = conn.cursor()
        
        # Get rugged tokens without token_creator, ordered by creator activity
        cursor.execute("""
            SELECT mint, post_migration_creator_activity_ratio
            FROM token_analysis
            WHERE rug_indicator = 'quick_peak_low_mc'
              AND token_creator IS NULL
              AND post_migration_creator_activity_ratio > 0.30
            ORDER BY post_migration_creator_activity_ratio DESC
            LIMIT 10
        """)
        
        tokens = cursor.fetchall()
        conn.close()
        
        print("\nAttempting to extract creators from Solana Explorer...\n")
        print("="*80)
        
        creators_found = {}
        
        for mint, activity_ratio in tokens:
            print(f"\nToken: {mint}")
            print(f"Creator Activity: {activity_ratio*100:.1f}%")
            
            # Try Explorer API
            creator = get_token_info_from_explorer(mint)
            
            if creator:
                print(f"✓ Creator found: {creator}")
                creators_found[mint] = creator
            else:
                print(f"✗ Could not extract creator from Explorer")
                print(f"  Manual lookup: https://explorer.solana.com/address/{mint}")
            
            time.sleep(0.5)  # Rate limiting
        
        print("\n" + "="*80)
        print(f"\nSummary: Found {len(creators_found)} creators\n")
        
        for mint, creator in creators_found.items():
            print(f"{mint}")
            print(f"  Creator: {creator}\n")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    extract_creators_from_transactions()
