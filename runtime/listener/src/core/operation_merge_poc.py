#!/usr/bin/env python3
"""
Operation-Merge Evidence Test — PoC stage 2.

Answers the unresolved core question the discovery PoC exposed:

    Treasury discovery -> operation IDENTITY.

    Are Cgwr5FAa and yUpm7rKXPs SIBLINGS in one operation, or two separate
    operations running the same playbook?

Merge discipline (non-negotiable, mirrors every prior audit):
  * Merge requires HARD INFRASTRUCTURE EVIDENCE — shared wallets, not shared style.
  * Allowed merge evidence:
        - shared collectors           (sweep hubs feeding both treasuries)
        - shared pass-throughs        (forwarders on both value chains)
        - shared terminal destinations(both consolidate to the same wallet)
        - shared upstream funders      (both seeded by the same source)
        - shared sweep counterparties  (same sub-wallets sweeping into both)
  * Template + burst-timing are CORROBORATING ONLY. They may raise confidence on an
    already-infrastructure-linked pair, but can NEVER cause a merge by themselves.

Outcome routes the Phase-1 model:
    LINKED  (infra overlap >= threshold)  -> operation = TREASURY CLUSTER
    UNLINKED(template/timing only)        -> operation FAMILY of separate roots

This is a read-only analysis. It writes nothing and changes no behaviour.

Run:
    python -m src.core.operation_merge_poc TREASURY_A TREASURY_B
    # defaults to Cgwr5FAa vs yUpm7rKXPs
"""

from __future__ import annotations

import os
import sys
import json
import urllib.request
import urllib.error
from collections import defaultdict

HELIUS_API_KEY = os.environ.get("HELIUS_API_KEY", "")

MIN_SOL = 0.01
BIG_SOL = 1.0
# how many hops of neighborhood to gather around each treasury for overlap testing
NEIGH_HOPS = 2
# infra-overlap thresholds: merge requires at least this much HARD overlap
MERGE_MIN_SHARED_WALLETS = 3          # >=3 shared infra wallets, OR
MERGE_MIN_SHARED_TERMINAL = 1         # >=1 shared terminal/upstream funder
ATA_RENT = 2_039_280


def _addr_txs(addr: str) -> list:
    url = (f"https://api.helius.xyz/v0/addresses/{addr}/transactions/"
           f"?api-key={HELIUS_API_KEY}&limit=100")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "flex-op-merge/0.1"})
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read().decode())
        return d if isinstance(d, list) else []
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return []


def _flows(addr: str):
    """Return (inbound_sources, outbound_targets, fanin, fanout, big_counterparties, times)."""
    insrc = defaultdict(float)
    outdst = defaultdict(float)
    times = []
    for t in _addr_txs(addr):
        ts = t.get("timestamp", 0) or 0
        if ts:
            times.append(ts)
        for nt in t.get("nativeTransfers", []):
            v = nt.get("amount", 0) / 1e9
            if v < MIN_SOL:
                continue
            if nt.get("toUserAccount") == addr:
                insrc[nt.get("fromUserAccount")] += v
            if nt.get("fromUserAccount") == addr:
                outdst[nt.get("toUserAccount")] += v
    return insrc, outdst, times


def neighborhood(treasury: str, hops: int = NEIGH_HOPS):
    """Gather the wallet neighborhood around a treasury: all in/out counterparties
    out to `hops`, tagged by category. Returns dicts of wallet-sets."""
    collectors = set()       # nodes that fan IN heavily (sweep hubs)
    passthroughs = set()     # 1-in-1-out forwarders on the chains
    upstreams = set()        # big inbound funders of the treasury
    terminals = set()        # big outbound destinations of the treasury
    all_neigh = set()
    frontier = {treasury}
    visited = set()
    for _ in range(hops):
        nxt = set()
        for node in frontier:
            if node in visited or not node:
                continue
            visited.add(node)
            insrc, outdst, _ = _flows(node)
            fanin = len(insrc)
            fanout = len(outdst)
            for w, v in insrc.items():
                if not w:
                    continue
                all_neigh.add(w)
                if node == treasury and v >= BIG_SOL:
                    upstreams.add(w)
                nxt.add(w)
            for w, v in outdst.items():
                if not w:
                    continue
                all_neigh.add(w)
                if node == treasury and v >= BIG_SOL:
                    terminals.add(w)
                nxt.add(w)
            # classify the node itself within the neighborhood
            if node != treasury:
                if fanin >= 20:
                    collectors.add(node)
                elif fanin <= 2 and fanout <= 2:
                    passthroughs.add(node)
        frontier = nxt - visited
    return {
        "all": all_neigh, "collectors": collectors, "passthroughs": passthroughs,
        "upstreams": upstreams, "terminals": terminals,
    }


def template_timing(treasury: str):
    """Corroborating-only signals: dominant creator-funding template + activity window."""
    insrc, outdst, times = _flows(treasury)
    # infer template by sampling outbound amounts that match the ATA tail downstream
    # (cheap proxy: treasury's outbound 'big' amounts cluster size)
    window = (min(times), max(times)) if times else (None, None)
    return {"window": window, "outflow_buckets": sorted(
        {round(v, 0) for v in outdst.values() if v >= BIG_SOL}, reverse=True)[:6]}


def assess(a: str, b: str):
    print(f"\n[MERGE-TEST] A={a[:12]}…  B={b[:12]}…")
    print("  gathering neighborhoods (this is several RPC hops each)…")
    na = neighborhood(a)
    nb = neighborhood(b)

    # ---- HARD infrastructure overlap (the only thing that can cause a merge) ----
    shared_all = (na["all"] & nb["all"]) - {a, b}
    shared_collectors = na["collectors"] & nb["collectors"]
    shared_pass = na["passthroughs"] & nb["passthroughs"]
    shared_terminals = (na["terminals"] & nb["terminals"]) | \
                       (na["terminals"] & {b}) | (nb["terminals"] & {a})
    shared_upstreams = na["upstreams"] & nb["upstreams"]
    # direct link: is one treasury a counterparty of the other?
    direct = (b in na["all"]) or (a in nb["all"])

    print("\n  --- HARD INFRASTRUCTURE EVIDENCE ---")
    print(f"  direct treasury<->treasury edge : {direct}")
    print(f"  shared collectors               : {len(shared_collectors)}  {[w[:8] for w in list(shared_collectors)[:4]]}")
    print(f"  shared pass-throughs            : {len(shared_pass)}  {[w[:8] for w in list(shared_pass)[:4]]}")
    print(f"  shared upstream funders         : {len(shared_upstreams)}  {[w[:8] for w in list(shared_upstreams)[:4]]}")
    print(f"  shared terminal destinations    : {len(shared_terminals)}  {[w[:8] for w in list(shared_terminals)[:4]]}")
    print(f"  shared neighborhood wallets(any): {len(shared_all)}  {[w[:8] for w in list(shared_all)[:6]]}")

    # ---- corroborating-only signals ----
    ta, tb = template_timing(a), template_timing(b)
    print("\n  --- CORROBORATING ONLY (cannot cause a merge) ---")
    print(f"  A outflow buckets: {ta['outflow_buckets']}")
    print(f"  B outflow buckets: {tb['outflow_buckets']}")

    # ---- decision (infra gates the merge; corroboration only annotates) ----
    hard_infra = (
        direct
        or len(shared_collectors) >= 1
        or len(shared_pass) >= MERGE_MIN_SHARED_WALLETS
        or len(shared_upstreams) >= MERGE_MIN_SHARED_TERMINAL
        or len(shared_terminals) >= MERGE_MIN_SHARED_TERMINAL
        or len(shared_all) >= MERGE_MIN_SHARED_WALLETS
    )
    verdict = "LINKED" if hard_infra else "UNLINKED"
    print("\n  ================= VERDICT =================")
    if verdict == "LINKED":
        print("  LINKED  -> these treasuries share INFRASTRUCTURE.")
        print("  Phase-1 model: operation = TREASURY CLUSTER (merge the roots).")
    else:
        print("  UNLINKED -> no shared infrastructure found (template/timing only).")
        print("  Phase-1 model: operation FAMILY of separate treasury-root operations.")
        print("  (Same playbook, distinct organisations OR distinct cells — do NOT merge.)")
    print("  ==========================================")
    return verdict


def main():
    a = sys.argv[1] if len(sys.argv) > 1 else "Cgwr5FAa6d39tqJXKgDkxhopgJuuJA6s8bZfZGY9hkTe"
    b = sys.argv[2] if len(sys.argv) > 2 else "yUpm7rKXPs7J2NXbBHARGBQ9ajyuYh9Pj1Zudu3f1iz"
    assess(a, b)


if __name__ == "__main__":
    main()
