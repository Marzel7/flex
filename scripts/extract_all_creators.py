#!/usr/bin/env python3
"""
Extract actual creator wallets from token creation transactions.

For each rugged token, find the first signer (fee payer) who created the token.
"""

import requests
import json
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "pumpswap_tokens.db"
RPC_URL = "https://api.mainnet-beta.solana.com"

def get_token_creator(mint):
    """Get the creator wallet from token creation transaction"""
    try:
        # Get earliest signature (token creation)
        sig_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [mint, {"limit": 1000}]  # Get all to find earliest
        }
        
        resp = requests.post(RPC_URL, json=sig_payload, timeout=10)
        data = resp.json()
        
        if not ("result" in data and data["result"]):
            return None
        
        # Get the OLDEST signature (last in list)
        sigs = data["result"]
        if not sigs:
            return None
        
        creation_sig = sigs[-1]["signature"]
        
        # Fetch the creation transaction
        tx_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [creation_sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
        }
        
        tx_resp = requests.post(RPC_URL, json=tx_payload, timeout=10)
        tx_data = tx_resp.json()
        
        if "result" not in tx_data or not tx_data["result"]:
            return None
        
        tx = tx_data["result"]
        account_keys = tx["transaction"]["message"]["accountKeys"]
        
        # First signer is the fee payer (creator)
        if account_keys:
            first_signer = account_keys[0]
            if isinstance(first_signer, dict):
                return first_signer.get("pubkey")
            else:
                return first_signer
        
        return None
        
    except Exception as e:
        return None

def extract_all_creators():
    """Extract creators for all high-activity rug tokens"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        cursor = conn.cursor()
        
        # Get high-activity rug tokens
        cursor.execute("""
            SELECT mint, post_migration_creator_activity_ratio
            FROM token_analysis
            WHERE rug_indicator = 'quick_peak_low_mc'
              AND token_creator IS NULL
              AND post_migration_creator_activity_ratio > 0.30
            ORDER BY post_migration_creator_activity_ratio DESC
        """)
        
        tokens = cursor.fetchall()
        conn.close()
        
        print(f"\nExtracting creators for {len(tokens)} high-activity rug tokens...\n")
        print("="*90)
        
        creators_found = {}
        
        for i, (mint, activity_ratio) in enumerate(tokens, 1):
            print(f"[{i}/{len(tokens)}] {mint[:40]}... ({activity_ratio*100:.1f}% activity)", end="")
            
            creator = get_token_creator(mint)
            
            if creator:
                creators_found[mint] = creator
                print(f" ✓ Creator: {creator}")
            else:
                print(f" ✗ Could not extract")
            
            time.sleep(0.3)  # Rate limiting
        
        print("\n" + "="*90)
        print(f"\nFOUND {len(creators_found)} CREATORS:\n")
        
        # Group by creator
        creator_tokens = {}
        for mint, creator in creators_found.items():
            if creator not in creator_tokens:
                creator_tokens[creator] = []
            creator_tokens[creator].append(mint)
        
        # Print creators with their tokens
        for creator, mints in sorted(creator_tokens.items(), key=lambda x: len(x[1]), reverse=True):
            print(f"\nCreator: {creator}")
            print(f"  Rug tokens launched: {len(mints)}")
            for mint in mints[:3]:  # Show first 3
                print(f"    • {mint[:45]}")
            if len(mints) > 3:
                print(f"    ... and {len(mints)-3} more")
        
        # Print as wallet list for blocking
        print("\n" + "="*90)
        print("\nWALLETS TO BLOCK (copy to trading blacklist):\n")
        for creator in sorted(creator_tokens.keys()):
            count = len(creator_tokens[creator])
            print(f"{creator}  # {count} rug token(s)")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    extract_all_creators()
