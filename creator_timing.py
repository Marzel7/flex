#!/usr/bin/env python3
import requests

API_KEY = "16f1a5fc-2592-466c-a5d4-b5799ae8da96"

def rpc(method, params):
    r = requests.post(f"https://mainnet.helius-rpc.com/?api-key={API_KEY}",
                      json={"jsonrpc":"2.0","id":1,"method":method,"params":params}, timeout=30)
    return r.json().get("result")

# TRUMPCUM CREATE tx blocktime
r2 = rpc("getTransaction", [
    "9ri9eZHwn7LFv5PFtjodwrgKex4KAkRXtsnTN3oZB1219t2t8aSB6fFbpc5xMMTRF1ebC8jY6gGwd6GFfGi9x89",
    {"encoding": "json", "maxSupportedTransactionVersion": 0}
])
if r2:
    tx = r2['transaction']
    msg = tx['message']
    accts = msg['accountKeys']
    header = msg.get('header', {})
    n_signers = header.get('numRequiredSignatures', 0)
    n_readonly_signed = header.get('numReadonlySignedAccounts', 0)
    n_readonly_unsigned = header.get('numReadonlyUnsignedAccounts', 0)
    n_unsigned_writable = len(accts) - n_signers - n_readonly_unsigned
    print("TRUMPCUM CREATE TX:")
    print(f"Block time: {r2.get('blockTime')}")
    print(f"numRequiredSignatures: {n_signers}")
    for i, a in enumerate(accts):
        if i < n_signers:
            role = "SIGNER" if i < (n_signers - n_readonly_signed) else "SIGNER+RO"
        elif i < n_signers + n_unsigned_writable:
            role = "WRITABLE"
        else:
            role = "READONLY"
        print(f"  [{i}] {a}  ({role})")
    print(f"Signatures: {len(tx.get('signatures',[]))}")
    for sig in tx.get('signatures', []):
        print(f"  {sig}")

# Creator wallet histories via RPC getSignaturesForAddress
for label, addr in [
    ("Gaynald creator", "8RW8MeyB9AzBS9TiZtTtuCh6yzib6PLrC7bRtmh3bfJe"),
    ("TRUMPCUM creator", "6NV84W76QUxAicY4dGACtuuTfCr6QJU3ZfyRmRP6CgY5"),
    ("Sellategy creator", "HLucJQyQy6XmiudWYE5XA4t5y8o5WAJr1CoFE2BsFA2a"),
]:
    sigs = rpc("getSignaturesForAddress", [addr, {"limit": 10}])
    print(f"\n{label} ({addr[:20]}) sigs:")
    if sigs:
        for s in sigs:
            print(f"  blockTime={s.get('blockTime')} err={s.get('err')} sig={s.get('signature','')[:55]}")
    else:
        print("  None")
