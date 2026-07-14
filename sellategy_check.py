#!/usr/bin/env python3
import requests

API_KEY = "16f1a5fc-2592-466c-a5d4-b5799ae8da96"

def rpc(method, params):
    r = requests.post(f"https://mainnet.helius-rpc.com/?api-key={API_KEY}",
                      json={"jsonrpc":"2.0","id":1,"method":method,"params":params}, timeout=30)
    return r.json().get("result")

for sig in ["2EqDkaM9KD51UXVkjDeaUzVc3KBqpuoUoGxvSL3Jk43zPiXVyZPsfBW",
            "2rJuhWRJezm5qzRdMagUA783ytZLfm8NDHcUnJ3yqRchVVZFpepXwN8"]:
    r = rpc("getTransaction", [sig, {"encoding":"json","maxSupportedTransactionVersion":0}])
    if r:
        tx = r['transaction']
        accts = tx['message']['accountKeys']
        header = tx['message'].get('header',{})
        n_sig = header.get('numRequiredSignatures',0)
        print(f"Sig: {sig[:50]}")
        print(f"blockTime={r.get('blockTime')} numSig={n_sig} n_accts={len(accts)}")
        for i,a in enumerate(accts[:6]):
            role = "SIGNER" if i < n_sig else "other"
            print(f"  [{i}] {a} ({role})")
        prog_ids = list(set([accts[ix['programIdIndex']] for ix in tx['message'].get('instructions',[])]))
        print(f"  Programs: {prog_ids}")
        print()
