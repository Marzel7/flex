#!/usr/bin/env python3
"""
Investigate PDA derivation and querying
"""
import requests
from solders.pubkey import Pubkey

# Check if the PUMPFUN_PROGRAM_ID is correct
PUMPFUN_PROGRAM_ID_V1 = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"  # From listener
PUMPFUN_PROGRAM_ID_V2 = "pmpA9A9n7CdrzJcm4E3rhZ4J8p9F3ZzK8Y9zCjR4Z5x"  # From V2 analyzer

test_mint = "BZ68YAqHkALtecENB5oy6B4qbTmf2Q8onCwzEtScpump"
RPC_URL = "https://api.mainnet-beta.solana.com"

print(f"Testing: {test_mint}\n")
print(f"{'='*70}")

# Try with V2 program ID
print(f"\n1. Using V2 Program ID: {PUMPFUN_PROGRAM_ID_V2}")
try:
    mint_pk = Pubkey.from_string(test_mint)
    pda_v2, _ = Pubkey.find_program_address(
        [b"bonding_curve", bytes(mint_pk)],
        Pubkey.from_string(PUMPFUN_PROGRAM_ID_V2)
    )
    print(f"   Bonding Curve PDA: {pda_v2}")
    
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSignaturesForAddress",
        "params": [str(pda_v2), {"limit": 10}]
    }
    res = requests.post(RPC_URL, json=payload, timeout=10).json()
    sigs = res.get("result", [])
    print(f"   Signatures found: {len(sigs)}")
except Exception as e:
    print(f"   Error: {e}")

# Try with V1 program ID
print(f"\n2. Using V1 Program ID: {PUMPFUN_PROGRAM_ID_V1}")
try:
    mint_pk = Pubkey.from_string(test_mint)
    pda_v1, _ = Pubkey.find_program_address(
        [b"bonding_curve", bytes(mint_pk)],
        Pubkey.from_string(PUMPFUN_PROGRAM_ID_V1)
    )
    print(f"   Bonding Curve PDA: {pda_v1}")
    
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSignaturesForAddress",
        "params": [str(pda_v1), {"limit": 10}]
    }
    res = requests.post(RPC_URL, json=payload, timeout=10).json()
    sigs = res.get("result", [])
    print(f"   Signatures found: {len(sigs)}")
except Exception as e:
    print(f"   Error: {e}")

# Try other seed variations
print(f"\n3. Trying other seed patterns...")
seeds_to_try = [
    [b"bonding_curve"],
    [b"bonding"],
    [b"curve", bytes(mint_pk)],
    [bytes(mint_pk)],
]

for seed in seeds_to_try:
    try:
        pda_test, _ = Pubkey.find_program_address(
            seed,
            Pubkey.from_string(PUMPFUN_PROGRAM_ID_V2)
        )
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [str(pda_test), {"limit": 10}]
        }
        res = requests.post(RPC_URL, json=payload, timeout=10).json()
        sigs = res.get("result", [])
        if sigs:
            print(f"   ✅ Found {len(sigs)} sigs with seed: {seed}")
            print(f"      PDA: {pda_test}")
    except:
        pass

print(f"\n{'='*70}\n")
