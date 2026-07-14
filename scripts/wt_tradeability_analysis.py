#!/usr/bin/env python3
"""Skeptical quantitative analysis: post-launch tradeable upside in confirmed WATCHTOWER
tokens, measured from the EARLIEST realistic external entry. RAW decoding only.

MC proxy = the bonding-curve account's SOL balance. pump.fun's curve price is a fixed
deterministic function of curve SOL reserves, so MC scales monotonically with curve SOL —
exact for a MULTIPLE (peak/entry), which is all we need. The curve account is identified as
the largest SOL-gaining account in a Buy tx (the buyer pays SOL into it).

Per token:
  CREATE slot/time + creator → insiders = creator + buyers in create slot (+1)
  first EXTERNAL buy = first Buy by a non-insider after the insider window
  curve_sol at first ext buy = entry MC proxy ; peak curve_sol = peak MC proxy
  peak_multiple = peak_curve_sol / entry_curve_sol
"""
import os, sys, json, time, urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.utils.db_locking import db_connect

KEY = os.environ.get("HELIUS_API_KEY", "")
URL = f"https://mainnet.helius-rpc.com/?api-key={KEY}"
PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
_C, _calls = {}, [0]


def rpc(method, params):
    ck = json.dumps([method, params])
    if ck in _C:
        return _C[ck]
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    for _ in range(4):
        try:
            _calls[0] += 1
            r = json.loads(urllib.request.urlopen(
                urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"}),
                timeout=20).read()).get("result")
            _C[ck] = r
            return r
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(1.4); continue
            return None
        except Exception:
            time.sleep(0.5)
    return None


def curve_account_and_sol(tx):
    """In a Buy tx, the bonding-curve account is the largest SOL gainer; return (curve_pubkey,
    its post-balance in SOL). This is the MC proxy."""
    meta = tx.get("meta") or {}
    keys = [k.get("pubkey") if isinstance(k, dict) else k
            for k in tx.get("transaction", {}).get("message", {}).get("accountKeys", [])]
    pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
    if len(pre) != len(keys):
        return None, None
    gains = sorted(((keys[i], post[i] - pre[i], post[i]) for i in range(len(keys))),
                   key=lambda x: -x[1])
    if gains and gains[0][1] > 0:
        return gains[0][0], gains[0][2] / 1e9
    return None, None


def analyze(mint, creator):
    sigs = [s for s in (rpc("getSignaturesForAddress", [mint, {"limit": 150}]) or []) if not s.get("err")]
    sigs.sort(key=lambda x: (x.get("slot") or 0))
    create_slot = create_time = None
    insiders = {creator}
    curve_acct = None
    first_ext = None
    peak_sol, peak_time = 0.0, None
    n_buys = 0
    for s in sigs:
        tx = rpc("getTransaction", [s["signature"], {"encoding": "jsonParsed",
                 "maxSupportedTransactionVersion": 0}])
        if not tx:
            continue
        meta = tx.get("meta") or {}
        keys = [k.get("pubkey") if isinstance(k, dict) else k
                for k in tx.get("transaction", {}).get("message", {}).get("accountKeys", [])]
        logs = " ".join(meta.get("logMessages", []) or [])
        slot, bt, signer = s.get("slot"), s.get("blockTime"), (keys[0] if keys else None)
        if "Instruction: Create" in logs and PUMP in keys:
            create_slot, create_time = slot, bt
            insiders.add(signer)
            continue
        if "Instruction: Buy" in logs and PUMP in keys and create_slot is not None:
            n_buys += 1
            ca, csol = curve_account_and_sol(tx)
            if ca and (curve_acct is None or ca == curve_acct):
                curve_acct = ca
            elif ca and csol is not None and csol > peak_sol:
                pass  # still use it; curve may shift account index but balance valid
            if csol is None:
                continue
            # insider window: buys within create slot (+1) = buy-swarm / dev
            if slot is not None and slot <= create_slot + 1:
                insiders.add(signer)
            elif first_ext is None and signer not in insiders:
                first_ext = (slot, bt, csol)
            if csol > peak_sol:
                peak_sol, peak_time = csol, bt
    res = {"mint": mint, "create_time": create_time, "n_buys": n_buys,
           "insiders": len(insiders), "peak_sol": round(peak_sol, 3)}
    if first_ext and first_ext[2] and first_ext[2] > 0 and peak_sol > 0:
        res.update({
            "entry_sol": round(first_ext[2], 4),
            "peak_multiple": round(peak_sol / first_ext[2], 3),
            "slots_to_ext": (first_ext[0] - create_slot) if create_slot else None,
            "sec_ext_to_peak": (peak_time - first_ext[1]) if (peak_time and first_ext[1]) else None,
        })
    else:
        res["peak_multiple"] = None
        res["reason"] = "no external buy / zero reserves"
    return res


def main():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    conn = db_connect(os.path.join(base, "database/wt_ops_v2.db"), timeout=30)
    toks = conn.execute(
        "SELECT DISTINCT cr.token_mint, cr.creator_wallet FROM wt_ops_v2_creators cr "
        "JOIN wt_ops_v2 o ON o.operation_uuid=cr.operation_uuid "
        "JOIN wt_confirmed_treasuries ct ON ct.treasury=o.treasury_root "
        "WHERE cr.token_mint IS NOT NULL AND cr.token_mint NOT LIKE 'pending:%'").fetchall()
    conn.close()
    print(f"confirmed WATCHTOWER tokens: {len(toks)}\n")
    results = []
    for mint, creator in toks:
        if _calls[0] > 9000:
            print("  [rpc budget hit]"); break
        r = analyze(mint, creator)
        results.append(r)
        sys.stdout.flush(); print(f"  {mint[:10]}: peak_mult={r.get('peak_multiple')} entry={r.get('entry_sol')} "
              f"peak={r.get('peak_sol')} buys={r.get('n_buys')} ins={r.get('insiders')} "
              f"slots_to_ext={r.get('slots_to_ext')} ext->peak={r.get('sec_ext_to_peak')}s")
    json.dump(results, open("/tmp/wt_trade.json", "w"))
    print(f"\n  rpc calls: {_calls[0]}")


if __name__ == "__main__":
    main()
