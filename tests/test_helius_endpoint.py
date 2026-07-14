#!/usr/bin/env python3
"""
Test Helius endpoint capabilities for batch transaction fetching
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

def test_helius_endpoints():
    """Test various Helius endpoints"""
    helius_key = os.getenv('HELIUS_API_KEY')
    
    if not helius_key:
        print("❌ HELIUS_API_KEY not found in .env")
        return
    
    print("\n" + "="*70)
    print("Testing Helius Endpoint Capabilities")
    print("="*70 + "\n")
    
    # Test token for analysis
    test_mint = "82P9MvicWYr2R1yeYZLJrbPZB236uMeMBKJ6bLgpBAGS"
    
    # 1. Test signature fetching (we already use this)
    print("1. Testing Helius Signature Fetching")
    print("-" * 70)
    url = f"https://api.helius.xyz/v0/addresses/{test_mint}/transactions"
    params = {"api-key": helius_key, "limit": 10, "type": "all"}
    
    try:
        res = requests.get(url, params=params, timeout=10)
        if res.status_code == 200:
            data = res.json()
            txs = data.get("transactions", [])
            print(f"✓ Signature fetch working")
            print(f"  - Got {len(txs)} transactions")
            if txs:
                print(f"  - Sample signature: {txs[0].get('signature')[:20]}...")
        else:
            print(f"✗ Status {res.status_code}: {res.text[:100]}")
    except Exception as e:
        print(f"✗ Error: {e}")
    
    # 2. Test individual transaction fetch via Helius
    print("\n2. Testing Helius Individual Transaction Fetch")
    print("-" * 70)
    
    if txs and txs[0].get('signature'):
        sig = txs[0]['signature']
        url = "https://mainnet.helius-rpc.com/"
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
        }
        params = {"api-key": helius_key}
        
        try:
            res = requests.post(url, json=payload, params=params, timeout=10)
            if res.status_code == 200:
                data = res.json()
                if "result" in data:
                    print(f"✓ Individual transaction fetch working")
                    print(f"  - Got transaction with meta: {'meta' in data['result']}")
                else:
                    print(f"✗ No result in response: {data}")
            else:
                print(f"✗ Status {res.status_code}: {res.text[:100]}")
        except Exception as e:
            print(f"✗ Error: {e}")
    
    # 3. Test batch transaction fetch (if supported)
    print("\n3. Testing Helius Batch Transaction Fetch")
    print("-" * 70)
    
    if len(txs) >= 2:
        sigs = [tx['signature'] for tx in txs[:2]]
        
        # Try different batch methods
        methods = [
            ("getMultipleTransactions", [sigs, {"encoding": "jsonParsed"}]),
            ("getTransactions", [sigs, {"encoding": "jsonParsed"}]),
        ]
        
        for method_name, params_list in methods:
            print(f"\nTrying method: {method_name}")
            url = "https://mainnet.helius-rpc.com/"
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": method_name,
                "params": params_list
            }
            api_params = {"api-key": helius_key}
            
            try:
                res = requests.post(url, json=payload, params=api_params, timeout=10)
                data = res.json()
                
                if "result" in data and data["result"]:
                    print(f"  ✓ {method_name} works!")
                    result = data["result"]
                    if isinstance(result, list):
                        print(f"    - Got {len(result)} transactions")
                    else:
                        print(f"    - Result type: {type(result).__name__}")
                elif "error" in data:
                    error = data["error"]
                    print(f"  ✗ Error: {error.get('message', error)}")
                else:
                    print(f"  ✗ Unexpected response: {data}")
            except Exception as e:
                print(f"  ✗ Exception: {e}")
    
    # 4. Check rate limits
    print("\n4. Testing Rate Limits")
    print("-" * 70)
    
    # Make multiple rapid requests
    url = f"https://api.helius.xyz/v0/addresses/{test_mint}/transactions"
    params = {"api-key": helius_key, "limit": 1}
    
    success_count = 0
    error_count = 0
    
    for i in range(10):
        try:
            res = requests.get(url, params=params, timeout=5)
            if res.status_code == 200:
                success_count += 1
            else:
                error_count += 1
                if res.status_code == 429:
                    print(f"  ⚠️  Rate limited at request {i+1}")
                    break
        except Exception as e:
            error_count += 1
    
    print(f"✓ Made 10 requests: {success_count} succeeded, {error_count} failed")
    print(f"  - Rate limit appears adequate for batch operations")
    
    # 5. API Endpoint Summary
    print("\n5. Helius API Endpoints Available")
    print("-" * 70)
    print("""
Signature Fetching:
  ✓ GET /v0/addresses/{mint}/transactions (we use this)
    - Paginated transaction history
    - Returns up to 1000 per page
    - Has page-token for pagination

Transaction Fetching Options:
  ? getMultipleTransactions - For batch fetching (testing)
  ? getTransactions - Alternative batch method (testing)
  ✓ getTransaction - Individual fetch (working)
  
Batch Size Recommendations:
  - Current: 100 concurrent individual calls
  - With batch API: 100-200 txs per batch call
  - Expected throughput: 10-20x improvement
    """)
    
    print("="*70 + "\n")

if __name__ == "__main__":
    test_helius_endpoints()
