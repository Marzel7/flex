#!/usr/bin/env python3
"""
Debug getMultipleTransactions with Helius
"""
import requests
import os
from dotenv import load_dotenv

load_dotenv()

helius_key = os.getenv('HELIUS_API_KEY', '')
if not helius_key:
    print("❌ HELIUS_API_KEY not set")
    exit(1)

helius_rpc = f"https://mainnet.helius-rpc.com/?api-key={helius_key}"
mint = "82P9MvicWYr2R1yeYZLJrbPZB236uMeMBKJ6bLgpBAGS"

print(f"Getting signatures for {mint}...\n")

# Get signatures
payload = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "getSignaturesForAddress",
    "params": [mint, {"limit": 2}]
}
res = requests.post(helius_rpc, json=payload, timeout=10).json()
sigs = res.get("result", [])
print(f"Found {len(sigs)} signatures")

if sigs:
    sig_list = [s["signature"] for s in sigs]
    print(f"Signatures: {sig_list}\n")
    
    # Test getMultipleTransactions
    print("Testing getMultipleTransactions on Helius...")
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getMultipleTransactions",
        "params": [sig_list, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
    }
    
    res = requests.post(helius_rpc, json=payload, timeout=20).json()
    
    if "error" in res:
        print(f"❌ Error: {res['error']}")
    else:
        result = res.get("result", [])
        print(f"✅ Result returned {len(result)} items")
        
        non_null = [tx for tx in result if tx is not None]
        print(f"   Non-null: {len(non_null)}")
        
        if result:
            print(f"\nFirst transaction:")
            print(f"  Type: {type(result[0])}")
            if result[0]:
                print(f"  Has meta: {'meta' in result[0]}")
