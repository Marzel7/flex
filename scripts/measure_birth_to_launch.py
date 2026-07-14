#!/usr/bin/env python3
"""Measure birth_to_launch_seconds for WATCHTOWER creators that ACTUALLY LAUNCHED — the
metric that decides whether the operation is STAGED (catchable, ~58min) or INSTANT
(uncatchable, ~seconds, like GOOOOAL).

Population = wt_ops_v2_creators with a real token_mint (confirmed launches). For each:
  birth   = the creator's FIRST inbound funding tx (the wrap-close that seeded it)
  launch  = the creator's pump.fun token CREATE
  gap     = launch - birth  →  CREATOR_MODE

Raw RPC only (getSignaturesForAddress + getTransaction, 1cr each). NEVER the enhanced
/v0/addresses endpoint. Caches every lookup.
"""
import os, sys, json, time, urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.utils.db_locking import db_connect

KEY = os.environ.get("HELIUS_API_KEY", "")
URL = f"https://mainnet.helius-rpc.com/?api-key={KEY}"
PUMPFUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
INSTANT_THRESHOLD_S = 60
_CACHE = {}
_calls = [0]


def rpc(method, params):
    ck = json.dumps([method, params])
    if ck in _CACHE:
        return _CACHE[ck]
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    for _ in range(4):
        try:
            _calls[0] += 1
            r = json.loads(urllib.request.urlopen(
                urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"}),
                timeout=15).read()).get("result")
            _CACHE[ck] = r
            return r
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(1.3); continue
            return None
        except Exception:
            time.sleep(0.4)
    return None


def birth_and_launch(creator):
    """Return (birth_time, launch_time, mint, launch_sig). birth = earliest tx that funds
    the creator (first inbound SOL); launch = its pump.fun token CREATE."""
    sigs = rpc("getSignaturesForAddress", [creator, {"limit": 60}]) or []
    sigs = [s for s in sigs if not s.get("err")]
    sigs.sort(key=lambda x: x.get("blockTime") or 0)        # oldest first
    birth = None
    launch = launch_mint = launch_sig = None
    for s in sigs:
        tx = rpc("getTransaction", [s["signature"], {"encoding": "jsonParsed",
                 "maxSupportedTransactionVersion": 0}])
        if not tx:
            continue
        bt = s.get("blockTime")
        meta = tx.get("meta") or {}
        msg = tx.get("transaction", {}).get("message", {})
        keys = [k.get("pubkey") if isinstance(k, dict) else k for k in msg.get("accountKeys", [])]
        logs = " ".join(meta.get("logMessages", []) or [])
        # birth = first tx where the creator's SOL balance goes UP (funded into existence)
        if birth is None and creator in keys:
            pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
            ci = keys.index(creator)
            if ci < len(post) and ci < len(pre) and post[ci] - pre[ci] > 0:
                birth = bt
        # launch = genuine pump.fun token CREATE (not the ATA createIdempotent)
        if launch is None:
            is_pump = PUMPFUN_PROGRAM in keys or PUMPFUN_PROGRAM in logs
            is_token_create = "Program log: Instruction: Create" in logs
            if is_pump and is_token_create:
                for tb in (meta.get("postTokenBalances") or []):
                    if tb.get("mint") and "So111" not in tb["mint"]:
                        launch_mint = tb["mint"]; break
                launch, launch_sig = bt, s["signature"]
        if birth and launch:
            break
    return birth, launch, launch_mint, launch_sig


def main():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    conn = db_connect(os.path.join(base, "database/wt_ops_v2.db"), timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    # launched creators (real mint) — the population that answers STAGED vs INSTANT
    rows = conn.execute(
        "SELECT DISTINCT cr.creator_wallet, o.treasury_root, cr.token_mint "
        "FROM wt_ops_v2_creators cr JOIN wt_ops_v2 o ON o.operation_uuid=cr.operation_uuid "
        "WHERE cr.token_mint NOT LIKE 'pending:%' AND cr.token_mint IS NOT NULL "
        "LIMIT 60").fetchall()
    print(f"measuring birth→launch for {len(rows)} LAUNCHED creators (raw RPC only)...\n")
    results = []
    for creator, treasury, known_mint in rows:
        if _calls[0] > 4000:
            print("  [rpc budget reached — stopping]"); break
        birth, launch, mint, lsig = birth_and_launch(creator)
        if birth and launch:
            gap = launch - birth
            mode = "INSTANT" if gap < INSTANT_THRESHOLD_S else "STAGED"
        else:
            gap, mode = None, "UNKNOWN"
        results.append((creator, treasury, birth, launch, gap, mode, mint or known_mint, lsig))
        tag = f"{gap}s {mode}" if gap is not None else f"{mode} (birth={birth} launch={launch})"
        print(f"  {creator[:12]} [{(treasury or '')[:8]}]: {tag}")

    # store
    for creator, treasury, birth, launch, gap, mode, mint, lsig in results:
        conn.execute(
            "INSERT OR REPLACE INTO wt_creator_birth_launch "
            "(creator, treasury, funded_at, launched_at, birth_to_launch_s, creator_mode, "
            " token_mint, launch_sig, measured_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (creator, treasury, birth, launch, gap, mode, mint, lsig, int(time.time())))
    conn.commit()

    # ── distribution ──
    print("\n" + "=" * 52)
    print("BIRTH → LAUNCH DISTRIBUTION  (launched WATCHTOWER creators)")
    print("=" * 52)
    buckets = [("0-10s", 0, 10), ("10-60s", 10, 60), ("1-10m", 60, 600),
               ("10-60m", 600, 3600), ("60m+", 3600, 10**9)]
    measured = [r for r in results if r[4] is not None]
    for label, lo, hi in buckets:
        n = sum(1 for r in measured if lo <= r[4] < hi)
        print(f"  {label:>8}: {n:>3} {'█'*n}")
    unknown = sum(1 for r in results if r[4] is None)
    print(f"  {'UNKNOWN':>8}: {unknown:>3}  (couldn't resolve birth+launch)")
    instant = sum(1 for r in measured if r[5] == "INSTANT")
    staged = sum(1 for r in measured if r[5] == "STAGED")
    tot = instant + staged
    print()
    if tot:
        print(f"  INSTANT (<60s, uncatchable): {instant} ({100*instant//tot}%)")
        print(f"  STAGED  (>=60s, catchable):  {staged} ({100*staged//tot}%)")
        if measured:
            gaps = sorted(r[4] for r in measured)
            print(f"  median gap: {gaps[len(gaps)//2]}s | min: {gaps[0]}s | max: {gaps[-1]}s")
        print(f"\n  → operation is {'INSTANT-dominant — endgame is real-time ATTRIBUTION' if instant>staged else 'STAGED-dominant — endgame is pre-launch PREDICTION'}")
    print(f"\n  (rpc calls: {_calls[0]})")
    conn.close()


if __name__ == "__main__":
    main()
