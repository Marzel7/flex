#!/usr/bin/env python3
import requests, datetime

API_KEY = "16f1a5fc-2592-466c-a5d4-b5799ae8da96"

def rpc(method, params):
    r = requests.post(f"https://mainnet.helius-rpc.com/?api-key={API_KEY}",
                      json={"jsonrpc":"2.0","id":1,"method":method,"params":params}, timeout=30)
    return r.json().get("result")

# Sellategy - check the 2 txs at blocktime=1780328196
for sig in ["2EqDkaM9KD51UXVkjDeaUzVc3KBqpuoUoGxvSL3Jk43zPiXVyZPsfBW",
            "2rJuhWRJezm5qzRdMagUA783ytZLfm8NDHcUnJ3yqRchVVZFpepXwN8"]:
    r = rpc("getTransaction", [sig, {"encoding":"json","maxSupportedTransactionVersion":0}])
    if r:
        tx = r['transaction']
        accts = tx['message']['accountKeys']
        header = tx['message'].get('header',{})
        n_sig = header.get('numRequiredSignatures',0)
        n_ro_u = header.get('numReadonlyUnsignedAccounts',0)
        n_uw = len(accts) - n_sig - n_ro_u
        print(f"\nSig: {sig[:55]}")
        print(f"BlockTime: {r.get('blockTime')}")
        print(f"numRequiredSignatures: {n_sig}")
        for i, a in enumerate(accts):
            if i < n_sig: role = "SIGNER"
            elif i < n_sig+n_uw: role = "WRITABLE"
            else: role = "READONLY"
            print(f"  [{i}] {a} ({role})")

# Timestamp clarification
print("\n=== TIMESTAMP ANALYSIS ===")
for label, ts in [
    ("TRUMPCUM treasury batch", 1780135638),
    ("TRUMPCUM relay outbound from SUB_PROV", 1780135661),
    ("TRUMPCUM creator receives (return tx)", 1780135710),
    ("TRUMPCUM CREATE (helius enhanced says)", 1780133008),
    ("TRUMPCUM creator earliest sig", 1780133057),
    ("TRUMPCUM CREATE (problem stmt 09:23:28)", 1780136608),
    ("Gaynald treasury_tx", 1780260731),
    ("Gaynald creator funded", 1780261006),
    ("Gaynald CREATE", 1780261007),
    ("Sellategy creator funded", 1780327840),
    ("Sellategy CREATE", 1780328196),
]:
    dt = datetime.datetime.utcfromtimestamp(ts)
    print(f"  {label}: {ts} = {dt.strftime('%H:%M:%S')}")
