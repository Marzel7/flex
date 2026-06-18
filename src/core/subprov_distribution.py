"""
Subprov distribution tier — read-only derivation of the mid-tier "subprov that
also funds other subprovs", plus a zero-RPC backfill of immediate_funder.

CONFIRMED MODEL (on-chain, 2026-06-18):
    root treasury  →  distribution subprov  →  subprov  →  wrap-close creator

Roles are NON-EXCLUSIVE: a wallet can be a SUBPROV (wrap-closes to creators)
AND a funder of other subprovs, WITHOUT becoming a treasury.

GUARDRAIL (the whole point): a wallet is only a SUBPROV-DISTRIBUTOR if it funds
wallets that THEMSELVES produce creator-directed wrap-closes. That filters out
the ~5,615 raw mid-chain nodes (collectors / sweep / buy-swarm / pass-through)
which are mid-chain in the edges but are NOT real subprovs.

This module NEVER re-roots treasuries and does ZERO RPC. It only reads existing
local tables (wt_wrap_close_candidates, wt_discovered_subprovs, wt_ops_v2_edges).
"""
from __future__ import annotations

import os
import sqlite3
from typing import Dict, List, Optional

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OPS_DB_PATH = os.environ.get("OPS_V2_DB_PATH", os.path.join(_REPO_ROOT, "database", "wt_ops_v2.db"))


def _ro_conn(path: str = OPS_DB_PATH) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=15)
    c.row_factory = sqlite3.Row
    return c


def _real_subprovs(conn) -> set:
    """Wallets that PRODUCE creator-directed wrap-closes — the only wallets allowed
    to be called subprovs (the mechanism guardrail)."""
    return {r[0] for r in conn.execute(
        "SELECT DISTINCT subprov_wallet FROM wt_wrap_close_candidates "
        "WHERE subprov_wallet IS NOT NULL").fetchall()}


def backfill_immediate_funder(db_path: str = OPS_DB_PATH) -> Dict:
    """ZERO-RPC backfill of wt_discovered_subprovs.immediate_funder / funder_is_subprov
    from the local wrap-close lineage. The immediate funder of a subprov is the
    lineage_source_treasury recorded on its wrap-close rows (the wallet that seeded
    it). funder_is_subprov=1 iff that funder is itself a real subprov.

    NOTE: today this leaves the distribution tier mostly empty — the upstream trace
    records each subprov's funder as a 'treasury' and never followed a subprov's
    DOWNSTREAM fan-out, so local data has no subprov→subprov hops yet. The backfill
    populates what IS known and flags any distribution node it finds; the tier fills
    in going forward (scheduler forward-population) or via a targeted trace. We do
    NOT fabricate edges that were never collected.
    """
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        real = {r[0] for r in conn.execute(
            "SELECT DISTINCT subprov_wallet FROM wt_wrap_close_candidates "
            "WHERE subprov_wallet IS NOT NULL").fetchall()}
        # immediate funder per subprov = the lineage_source_treasury on its wrap-closes
        # (the wallet that seeded it). Pick the most recent non-null.
        rows = conn.execute(
            "SELECT subprov_wallet, lineage_source_treasury "
            "FROM wt_wrap_close_candidates "
            "WHERE subprov_wallet IS NOT NULL AND lineage_source_treasury IS NOT NULL").fetchall()
        funder_by_subprov: Dict[str, str] = {}
        for r in rows:
            funder_by_subprov.setdefault(r["subprov_wallet"], r["lineage_source_treasury"])

        updated = 0
        distribution = 0
        for subprov, funder in funder_by_subprov.items():
            is_sp = 1 if funder in real else 0
            if is_sp:
                distribution += 1
            # only fill if currently NULL (never overwrite a forward-populated value)
            cur = conn.execute(
                "UPDATE wt_discovered_subprovs "
                "SET immediate_funder=COALESCE(immediate_funder,?), "
                "    funder_is_subprov=MAX(funder_is_subprov,?) "
                "WHERE subprov=?",
                (funder, is_sp, subprov))
            updated += cur.rowcount
        conn.commit()
        return {"subprovs_seen": len(funder_by_subprov), "rows_updated": updated,
                "distribution_funders_found": distribution}
    finally:
        conn.close()


def mid_tier_subprovs(db_path: str = OPS_DB_PATH) -> List[Dict]:
    """Read-only DISTRIBUTION-NODE derivation: real subprovs that fund OTHER real
    subprovs. Returns [{distributor, subprovs_funded:[...], n}]. Empty until the
    distribution tier is populated (forward-population or targeted trace).

    Sources, in order of reliability:
      1. wt_discovered_subprovs where funder_is_subprov=1 (immediate_funder is the distributor)
      2. wt_ops_v2_edges where a real subprov funds a real subprov (zero-RPC cross-check)
    Both are filtered to real wrap-close producers — never raw mid-chain nodes.
    """
    conn = _ro_conn(db_path)
    try:
        real = _real_subprovs(conn)
        funded_by: Dict[str, set] = {}

        # source 1: the flag set by backfill / forward-population
        for r in conn.execute(
            "SELECT subprov, immediate_funder FROM wt_discovered_subprovs "
            "WHERE funder_is_subprov=1 AND immediate_funder IS NOT NULL").fetchall():
            if r["immediate_funder"] in real:
                funded_by.setdefault(r["immediate_funder"], set()).add(r["subprov"])

        # source 2: edges cross-check (a real subprov → a real subprov)
        try:
            for r in conn.execute("SELECT from_wallet, to_wallet FROM wt_ops_v2_edges").fetchall():
                fw, tw = r["from_wallet"], r["to_wallet"]
                if fw != tw and fw in real and tw in real:
                    funded_by.setdefault(fw, set()).add(tw)
        except Exception:
            pass

        out = [{"distributor": k, "subprovs_funded": sorted(v), "n": len(v)}
               for k, v in funded_by.items()]
        out.sort(key=lambda x: x["n"], reverse=True)
        return out
    finally:
        conn.close()


def record_verified_distribution(distributor: str, downstream: List[str],
                                 db_path: str = OPS_DB_PATH) -> Dict:
    """Seed a VERIFIED (on-chain) distribution relationship into local tables:
    `distributor` is a real subprov that funds each subprov in `downstream`.

    Used to import chains confirmed by on-chain trace (the data the upstream
    pipeline doesn't yet collect downstream). For each downstream subprov we set
    immediate_funder=distributor, funder_is_subprov=1. Idempotent. Does NOT touch
    `treasury` (root) — only adds the distribution tier. Caller must have verified
    that distributor wrap-closes to creators AND each downstream wrap-closes to
    creators (the mechanism guardrail) before calling this.
    """
    import time as _t
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        now = int(_t.time())
        n = 0
        for sub in downstream:
            # ensure a row exists, then set the distribution funder (never overwrite treasury)
            conn.execute(
                "INSERT OR IGNORE INTO wt_discovered_subprovs "
                "(subprov, first_seen, last_seen) VALUES (?,?,?)", (sub, now, now))
            conn.execute(
                "UPDATE wt_discovered_subprovs "
                "SET immediate_funder=?, funder_is_subprov=1, last_seen=? WHERE subprov=?",
                (distributor, now, sub))
            n += 1
        conn.commit()
        return {"distributor": distributor, "downstream_recorded": n}
    finally:
        conn.close()


if __name__ == "__main__":
    print("backfill:", backfill_immediate_funder())
    nodes = mid_tier_subprovs()
    print(f"distribution nodes: {len(nodes)}")
    for n in nodes[:10]:
        print(f"  {n['distributor'][:20]} funds {n['n']} subprovs")
