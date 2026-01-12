#!/usr/bin/env python3
"""
Test different RPC methods
"""
import requests

mint = "82P9MvicWYr2R1yeYZLJrbPZB236uMeMBKJ6bLgpBAGS"
RPC_URL = "https://api.mainnet-beta.solana.com"

# Get signatures
payload = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "getSignaturesForAddress",
    "params": [mint, {"limit": 1}]
}
res = requests.post(RPC_URL, json=payload, timeout=10).json()
sigs = res.get("result", [])

if sigs:
    sig = sigs[0]["signature"]
    print(f"Testing with signature: {sig[:20]}...\n")
    
    # Method 1: getTransaction (individual)
    print("Method 1: getTransaction (individual)")
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
    }
    res = requests.post(RPC_URL, json=payload, timeout=10).json()
    result = res.get("result")
    if result:
        print(f"  ✅ Result: Has data")
        if result.get("meta"):
            print(f"     - Has meta")
    else:
        print(f"  ❌ Result: {result}")
    
    # Method 2: getMultipleTransactions (batch)
    print("\nMethod 2: getMultipleTransactions (batch)")
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getMultipleTransactions",
        "params": [[sig], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
    }
    res = requests.post(RPC_URL, json=payload, timeout=20).json()
    result = res.get("result")
    print(f"  Result type: {type(result)}")
    if isinstance(result, list):
        print(f"  List length: {len(result)}")
        if result:
            print(f"  First item: {result[0]}")
    else:
        print(f"  Result: {result}")
    
    error = res.get("error")
    if error:
        print(f"  ❌ Error: {error}")
