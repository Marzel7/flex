#!/usr/bin/env python3
"""
WATCHTOWER Hypothesis Validator
Fetches on-chain data to validate: SUB_PROV -> relay -> creator -> CREATE timing
and mint PDA derivation
"""
import requests
import json
import time
import base64
from dataclasses import dataclass
from typing import Optional

API_KEY = "16f1a5fc-2592-466c-a5d4-b5799ae8da96"
HELIUS_BASE = f"https://api.helius.xyz/v0"
RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={API_KEY}"
PUMP_FUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

def rpc_call(method, params):
    r = requests.post(RPC_URL, json={"jsonrpc":"2.0","id":1,"method":method,"params":params}, timeout=30)
    return r.json().get("result")

def helius_tx(sigs):
    r = requests.post(f"{HELIUS_BASE}/transactions/?api-key={API_KEY}",
                      json={"transactions": sigs}, timeout=30)
    return r.json()

def helius_addr_txs(addr, limit=50, before=None):
    url = f"{HELIUS_BASE}/addresses/{addr}/transactions?api-key={API_KEY}&limit={limit}"
    if before:
        url += f"&before={before}"
    r = requests.get(url, timeout=30)
    return r.json()

def get_account_info(pubkey):
    result = rpc_call("getAccountInfo", [pubkey, {"encoding": "base58"}])
    return result

def check_account_age(pubkey):
    """Get number of transactions for an account - proxy for age"""
    # Get first few txs to see if it's new
    txs = helius_addr_txs(pubkey, limit=10)
    return txs

# =========================================================
# KNOWN LAUNCHES
# =========================================================
launches = [
    {
        "name": "Gaynald Trump",
        "token": "Gaynald",
        "mint": "CUdwRcEH2fqEuKQkALbHzpv81XUKbEamCEPreHSBpump",
        "sub_prov": "DzRrCaXNDG5usCo4oEtAPW8wVrEAwysVddgobrdUjXJ1",
        "creator": "8RW8MeyB9AzBS9TiZtTtuCh6yzib6PLrC7bRtmh3bfJe",
        "relay1": "GkBWhAtpG3LPs27BXJPBtEZHUMcsqqkivg4MgWnUVfdc",
        "treasury_ts": 1780260731,
        "creator_funded_ts": 1780261006,
        "create_ts": 1780261007,
        "create_sig": "5fyyCmfAYhHS898pPedHuEoEJpdrusofsgBRycPxazQ7BC21CVSQPiUGSAE4EwxhKq3xKJL77hQ1ZV5rbXpFdYUr",
    },
    {
        "name": "TRUMPCUM",
        "token": "Trump Community",
        "mint": "8AYsSaPyptd6dgQ1dvXsEbPZMuzM6MMRQXAJM9pQpump",
        "sub_prov": "2ujRcf1fwQjW8cjUPK6krBJBMdbiMiSKvNscYjdbFW6R",
        "creator": "6NV84W76QUxAicY4dGACtuuTfCr6QJU3ZfyRmRP6CgY5",
        "relay1": "86FJkrU9nEaVhjYemE4jhd3y26FHup8ZZ7zVeLV1wW8L",
        "relay2": "NEgS7RU23dCuCBkSK4CB",
        "treasury_ts": 1780135638,   # batch at 09:02:58
        "creator_funded_ts": 1780135661,  # relay outbound from sub_prov
        "create_ts": 1780136608,     # 09:23:28
        "create_sig": "9ri9eZHwn7LFv5PFtjodwrgKex4KAkRXtsnTN3oZB1219t2t8aSB6fFbpc5xMMTRF1ebC8jY6gGwd6GFfGi9x89",
    },
    {
        "name": "Sellategy",
        "token": "Sellategy",
        "mint": "3Cj1XSskaWrKMo2xN4ucnUi94JFZXTSePGAv4sZApump",
        "sub_prov": "8U7zfBcS7UWhpHiQLvExLNd6tvtEsGFX1MP1N8QhmoPK",
        "creator": "HLucJQyQy6XmiudWYE5XA4t5y8o5WAJr1CoFE2BsFA2a",
        "creator_funded_ts": 1780327840,
        "create_ts": 1780328196,
        "create_sig": None,  # not provided
    },
]

# =========================================================
# Q1: Creator wallet history — is it brand new?
# =========================================================
print("=" * 70)
print("Q1: CREATOR WALLET HISTORIES")
print("=" * 70)

for launch in launches:
    print(f"\n--- {launch['name']} ---")
    print(f"Creator: {launch['creator']}")
    creator_txs = check_account_age(launch['creator'])
    if isinstance(creator_txs, list):
        print(f"  Visible txs (limit 10): {len(creator_txs)}")
        if creator_txs:
            oldest = creator_txs[-1]
            print(f"  Oldest visible sig: {oldest.get('signature','')[:60]}")
            print(f"  Oldest visible ts: {oldest.get('timestamp')} type: {oldest.get('type')}")
            newest = creator_txs[0]
            print(f"  Newest sig: {newest.get('signature','')[:60]}")
            print(f"  Newest ts: {newest.get('timestamp')} type: {newest.get('type')}")
    else:
        print(f"  Error or empty: {creator_txs}")
    time.sleep(0.3)

# =========================================================
# Q2: Timing gaps
# =========================================================
print("\n" + "=" * 70)
print("Q2: TIMING GAPS")
print("=" * 70)
for launch in launches:
    funded_ts = launch.get('creator_funded_ts')
    create_ts = launch.get('create_ts')
    treasury_ts = launch.get('treasury_ts')
    gap = create_ts - funded_ts if (funded_ts and create_ts) else None
    treasury_gap = (funded_ts - treasury_ts) if (treasury_ts and funded_ts) else None
    print(f"\n{launch['name']}:")
    if treasury_ts:
        print(f"  TREASURY_TX ts:     {treasury_ts}")
    print(f"  Creator funded ts:  {funded_ts}")
    print(f"  CREATE ts:          {create_ts}")
    if treasury_gap is not None:
        print(f"  Treasury -> Funded: {treasury_gap}s")
    if gap is not None:
        print(f"  Funded -> CREATE:   {gap}s  *** KEY GAP ***")

# =========================================================
# Q3: Fetch CREATE transactions for mint PDA analysis
# =========================================================
print("\n" + "=" * 70)
print("Q3: CREATE TRANSACTION ANALYSIS — MINT PDA DERIVATION")
print("=" * 70)

create_sigs = [l['create_sig'] for l in launches if l.get('create_sig')]
if create_sigs:
    txs = helius_tx(create_sigs)
    for tx in txs:
        sig = tx.get('signature','')[:60]
        ts = tx.get('timestamp')
        # find which launch
        launch_name = None
        for l in launches:
            if l.get('create_sig','').startswith(sig[:40]):
                launch_name = l['name']
                break
        print(f"\n  Sig: {sig}")
        print(f"  TS: {ts}, Launch: {launch_name}")

        accts = [a.get('account','') for a in tx.get('accountData',[])]
        print(f"  Accounts in tx ({len(accts)}):")
        for a in accts:
            print(f"    {a}")

        # Look at instructions for pump.fun
        for ix in tx.get('instructions', []):
            prog = ix.get('programId','')
            if PUMP_FUN_PROGRAM in prog:
                print(f"  PUMP.FUN INSTRUCTION:")
                print(f"    accounts: {ix.get('accounts',[])}")
                print(f"    data: {ix.get('data','')[:100]}")

        # inner instructions too
        for iix in tx.get('innerInstructions', []):
            for inner in iix.get('instructions', []):
                prog = inner.get('programId','')
                if PUMP_FUN_PROGRAM in prog:
                    print(f"  INNER PUMP.FUN:")
                    print(f"    accounts: {inner.get('accounts',[])}")

# =========================================================
# Q4: Try to derive mint PDA from creator
# =========================================================
print("\n" + "=" * 70)
print("Q4: MINT PDA DERIVATION TEST")
print("=" * 70)

try:
    from solders.pubkey import Pubkey

    pump_prog = Pubkey.from_string(PUMP_FUN_PROGRAM)

    for launch in launches:
        creator_str = launch['creator']
        mint_str = launch['mint']
        creator = Pubkey.from_string(creator_str)

        # Try: seeds = ["mint", creator]
        seeds = [b"mint", bytes(creator)]
        derived, bump = Pubkey.find_program_address(seeds, pump_prog)
        print(f"\n{launch['name']}:")
        print(f"  Actual mint:   {mint_str}")
        print(f"  Derived (mint+creator): {derived}  bump={bump}")
        print(f"  MATCH: {str(derived) == mint_str}")

        # Try: seeds = ["mint"]  (just the string)
        seeds2 = [b"mint"]
        try:
            derived2, bump2 = Pubkey.find_program_address(seeds2, pump_prog)
            print(f"  Derived (just 'mint'): {derived2}")
        except Exception as e:
            print(f"  seeds2 error: {e}")

except ImportError:
    print("  solders not available - trying via Node.js or manual derivation")
    # Try via node if available
    import subprocess
    node_script = """
const { PublicKey } = require('@solana/web3.js');
const PUMP_FUN = new PublicKey('6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P');
const launches = [
  { name: 'Gaynald', creator: '8RW8MeyB9AzBS9TiZtTtuCh6yzib6PLrC7bRtmh3bfJe', mint: 'CUdwRcEH2fqEuKQkALbHzpv81XUKbEamCEPreHSBpump' },
  { name: 'TRUMPCUM', creator: '6NV84W76QUxAicY4dGACtuuTfCr6QJU3ZfyRmRP6CgY5', mint: '8AYsSaPyptd6dgQ1dvXsEbPZMuzM6MMRQXAJM9pQpump' },
  { name: 'Sellategy', creator: 'HLucJQyQy6XmiudWYE5XA4t5y8o5WAJr1CoFE2BsFA2a', mint: '3Cj1XSskaWrKMo2xN4ucnUi94JFZXTSePGAv4sZApump' },
];
for (const l of launches) {
  const creator = new PublicKey(l.creator);
  const [pda, bump] = PublicKey.findProgramAddressSync(
    [Buffer.from('mint'), creator.toBuffer()],
    PUMP_FUN
  );
  console.log(l.name + ':');
  console.log('  actual: ' + l.mint);
  console.log('  derived: ' + pda.toString() + ' bump=' + bump);
  console.log('  match: ' + (pda.toString() === l.mint));
}
"""
    try:
        result = subprocess.run(['node', '-e', node_script], capture_output=True, text=True, timeout=10)
        print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr[:200])
    except Exception as e2:
        print(f"  Node also failed: {e2}")

# =========================================================
# Q5: Fetch additional SUB_PROVs for more launches
# =========================================================
print("\n" + "=" * 70)
print("Q5: ADDITIONAL SUB_PROV LAUNCHES")
print("=" * 70)

extra_sub_provs = [
    "N3TKf3wMBNu8XmZsTSnk2xWQ2LjiGvUJh1ae9Lc3dW7",
    "Dw7xNxxwuBnTfpwSrZMhcAQqFp4XNe9XNaVhhsvyx6Da",
]

for sp in extra_sub_provs:
    print(f"\nSUB_PROV: {sp}")
    txs = helius_addr_txs(sp, limit=50)
    if not isinstance(txs, list):
        print("  Error:", txs)
        continue
    # Look for small outbound transfers (relay pattern: ~10000000 lamports = 0.01 SOL)
    relay_txs = []
    for tx in txs:
        for nt in tx.get('nativeTransfers', []):
            if nt.get('fromUserAccount','').startswith(sp[:20]):
                amount = nt.get('amount', 0)
                if amount < 20_000_000:  # < 0.02 SOL = likely relay seed
                    relay_txs.append({
                        'ts': tx.get('timestamp'),
                        'sig': tx.get('signature','')[:50],
                        'to': nt.get('toUserAccount',''),
                        'amount': amount,
                    })
    print(f"  Total txs: {len(txs)}")
    print(f"  Small outbound transfers (potential relay seeds): {len(relay_txs)}")
    for rt in relay_txs[:10]:
        print(f"    ts={rt['ts']} amount={rt['amount']} to={rt['to'][:44]}")
    time.sleep(0.3)

print("\n" + "=" * 70)
print("DONE")
print("=" * 70)
