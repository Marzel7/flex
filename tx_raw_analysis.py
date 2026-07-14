#!/usr/bin/env python3
"""
Fetch raw CREATE tx to determine if mint is a signer (keypair) or PDA
"""
import hashlib, base58, struct, requests, json

API_KEY = "16f1a5fc-2592-466c-a5d4-b5799ae8da96"

def rpc(method, params):
    r = requests.post(f"https://mainnet.helius-rpc.com/?api-key={API_KEY}",
                      json={"jsonrpc":"2.0","id":1,"method":method,"params":params}, timeout=30)
    return r.json().get("result")

# Gaynald CREATE tx
result = rpc("getTransaction", [
    "5fyyCmfAYhHS898pPedHuEoEJpdrusofsgBRycPxazQ7BC21CVSQPiUGSAE4EwxhKq3xKJL77hQ1ZV5rbXpFdYUr",
    {"encoding": "json", "maxSupportedTransactionVersion": 0}
])

print("=== GAYNALD CREATE TX ===")
if result:
    tx = result['transaction']
    msg = tx['message']
    accts = msg['accountKeys']
    header = msg.get('header', {})
    n_signers = header.get('numRequiredSignatures', 0)
    n_readonly_signed = header.get('numReadonlySignedAccounts', 0)
    n_readonly_unsigned = header.get('numReadonlyUnsignedAccounts', 0)

    print(f"numRequiredSignatures: {n_signers}")
    print(f"numReadonlySignedAccounts: {n_readonly_signed}")
    print(f"numReadonlyUnsignedAccounts: {n_readonly_unsigned}")
    print(f"Total accounts: {len(accts)}")

    # In Solana tx message:
    # Accounts 0..n_signers-1 are signers
    # Accounts 0..n_signers-n_readonly_signed-1 are writable signers
    # Accounts n_signers..n_signers+n_unsigned_writable are unsigned writable
    n_unsigned_writable = len(accts) - n_signers - n_readonly_unsigned

    for i, a in enumerate(accts):
        if i < n_signers:
            role = "SIGNER+WRITABLE" if i < (n_signers - n_readonly_signed) else "SIGNER+READONLY"
        elif i < n_signers + n_unsigned_writable:
            role = "WRITABLE"
        else:
            role = "READONLY"
        print(f"  [{i}] {a}  ({role})")

    print(f"\nSignatures ({len(tx.get('signatures',[]))}):")
    for sig in tx.get('signatures', []):
        print(f"  {sig}")

    print("\nKey finding: If mint is a SIGNER, it's a keypair (NOT a PDA).")
    print("If mint is just WRITABLE (not signer), it could be a PDA.")

    print("\n=== INSTRUCTIONS ===")
    for idx, ix in enumerate(msg.get('instructions', [])):
        prog = accts[ix['programIdIndex']]
        d = ix.get('data', '')
        raw = base58.b58decode(d) if d else b''
        print(f"\nIx[{idx}]:")
        print(f"  program: {prog}")
        print(f"  accounts by index: {ix.get('accounts', [])}")
        print(f"  accounts resolved: {[accts[i] for i in ix.get('accounts',[])]}")
        print(f"  data len: {len(raw)}  disc: {raw[:8].hex() if raw else 'none'}")
        if raw:
            print(f"  full hex: {raw.hex()}")

print()
print("=" * 60)

# TRUMPCUM CREATE tx
result2 = rpc("getTransaction", [
    "9ri9eZHwn7LFv5PFtjodwrgKex4KAkRXtsnTN3oZB1219t2t8aSB6fFbpc5xMMTRF1ebC8jY6gGwd6GFfGi9x89",
    {"encoding": "json", "maxSupportedTransactionVersion": 0}
])

print("=== TRUMPCUM CREATE TX ===")
if result2:
    tx = result2['transaction']
    msg = tx['message']
    accts = msg['accountKeys']
    header = msg.get('header', {})
    n_signers = header.get('numRequiredSignatures', 0)
    n_readonly_signed = header.get('numReadonlySignedAccounts', 0)
    n_readonly_unsigned = header.get('numReadonlyUnsignedAccounts', 0)

    print(f"Block time: {result2.get('blockTime')}")
    print(f"numRequiredSignatures: {n_signers}")
    n_unsigned_writable = len(accts) - n_signers - n_readonly_unsigned

    for i, a in enumerate(accts):
        if i < n_signers:
            role = "SIGNER+WRITABLE" if i < (n_signers - n_readonly_signed) else "SIGNER+READONLY"
        elif i < n_signers + n_unsigned_writable:
            role = "WRITABLE"
        else:
            role = "READONLY"
        print(f"  [{i}] {a}  ({role})")

    print(f"\nSignatures ({len(tx.get('signatures',[]))}):")
    for sig in tx.get('signatures', []):
        print(f"  {sig}")
