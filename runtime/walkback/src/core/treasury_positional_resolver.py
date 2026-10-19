"""
Positional Treasury Resolver — (b), the controlled follow-up.

The current classifier (operation_discovery_poc._classify_role) assigns roles by wallet
SHAPE (fan-count + a value guard). That is role *hygiene*, not treasury *truth*: any
account can send small or large amounts, so shape-based classification leaks. The
durable definition is POSITIONAL:

    TREASURY = the highest upstream convergence root inside a confirmed
               launch-producing lineage, with no meaningful intra-operation inbound.

This resolver is NON-DESTRUCTIVE and PRELIMINARY. It runs AFTER reconstruction, walks each
operation's funding graph UPSTREAM from its migrated creators to the convergence root, and
RECORDS a comparison — it does NOT change any wt_ops_v2_wallets role. The live monitor keeps
using the guarded classifier; this layer surfaces where the assigned treasury may disagree
with the positional root, FOR HUMAN REVIEW.

KNOWN LIMITATIONS (it is not yet trustworthy enough to auto-act):
  - The persisted edge graph (wt_ops_v2_edges) does not fully link creator→…→treasury for
    every operation, so upstream walks can overshoot (stop at a pass-through) or under-reach.
  - Single-creator operations give trivial "confidence" (1/1) that isn't real convergence.
  - Real treasuries have incidental intra-op inbound (dust/returns), so "funded from within
    the op" over-flags them.
  Net: it reliably catches the OBVIOUS case (a high-fan distributor crowned as treasury,
  e.g. 46f7→5ED5, independently re-found) but still false-flags some real treasuries. Treat
  ROLE_MISMATCH_REVIEW as "candidate for a human to look at", never as ground truth. The
  durable fix is the full upstream-lineage edge build — a separate piece of data work.

Output per operation (wt_ops_v2_treasury_resolution):
  - current_assigned_treasury   (what the classifier called the root)
  - positional_root_candidate   (the true convergence root by graph position)
  - confidence
  - evidence_path               (the upstream walk that reached the root)
  - status                      OK | ROLE_MISMATCH_REVIEW | UNRESOLVED
  - reason

Dashboard surfaces ROLE_MISMATCH_REVIEW rows. Roles are corrected later, deliberately —
never automatically from this resolver.
"""
from __future__ import annotations

import os
import time
import json
from typing import Optional

OPS_DB_PATH = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "database", "wt_ops_v2.db"))

# a hop counts as "meaningful funding" if it carries at least this much — filters dust so
# the convergence walk follows capital flow, not noise.
MIN_FUNDING_SOL = 0.5
MAX_UPSTREAM_HOPS = 8
_resolution_schema_ensured = False

try:
    from src.utils.db_locking import db_connect
except Exception:                                    # pragma: no cover
    import sqlite3
    def db_connect(path, timeout=30, row_factory=None):
        c = sqlite3.connect(path, timeout=timeout)
        if row_factory:
            c.row_factory = row_factory
        return c


def ensure_resolution_schema(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wt_ops_v2_treasury_resolution (
            operation_uuid            TEXT PRIMARY KEY,
            current_assigned_treasury TEXT,
            positional_root_candidate TEXT,
            confidence                REAL,
            evidence_path             TEXT,     -- JSON list of wallets root→…→creator
            status                    TEXT,     -- OK | ROLE_MISMATCH_REVIEW | UNRESOLVED
            reason                    TEXT,
            resolved_at               INTEGER
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_tres_res_status ON wt_ops_v2_treasury_resolution(status)")
    conn.commit()


def _intra_op_funders(conn, op_uuid: str, wallet: str) -> list:
    """Wallets INSIDE this operation that funded `wallet` with >= MIN_FUNDING_SOL.
    Uses the persisted edge graph (no RPC) — the lineage was already reconstructed."""
    rows = conn.execute(
        "SELECT from_wallet, amount_sol FROM wt_ops_v2_edges "
        "WHERE operation_uuid=? AND to_wallet=? AND amount_sol >= ? "
        "ORDER BY amount_sol DESC", (op_uuid, wallet, MIN_FUNDING_SOL)).fetchall()
    # only consider funders that are themselves part of this op's wallet set (intra-op)
    op_wallets = {r[0] for r in conn.execute(
        "SELECT wallet FROM wt_ops_v2_wallets WHERE operation_uuid=?", (op_uuid,)).fetchall()}
    out = []
    for frm, amt in rows:
        if frm and frm != wallet:
            out.append((frm, amt, frm in op_wallets))
    return out


def _walk_to_root(conn, op_uuid: str, start_wallet: str) -> tuple:
    """Walk UPSTREAM from a creator/funder to the convergence root: the wallet with NO
    meaningful intra-operation inbound. Returns (root, path, reason)."""
    path = [start_wallet]
    cur = start_wallet
    seen = {start_wallet}
    for _ in range(MAX_UPSTREAM_HOPS):
        funders = _intra_op_funders(conn, op_uuid, cur)
        # prefer an intra-op funder (stays inside the lineage); fall back to any
        intra = [f for f in funders if f[2]]
        nxt = (intra or funders)
        # convergence root = no meaningful inbound at all
        if not nxt:
            return cur, path, "convergence_root (no intra-op inbound)"
        candidate = nxt[0][0]
        if candidate in seen:                       # cycle guard
            return cur, path, "cycle — stopped at last node"
        seen.add(candidate)
        path.append(candidate)
        cur = candidate
    return cur, path, "max_hops reached"


def resolve_operation(conn, op_uuid: str) -> dict:
    """Resolve one operation's positional treasury root and compare to the assigned one."""
    assigned = (conn.execute(
        "SELECT treasury_root FROM wt_ops_v2 WHERE operation_uuid=?", (op_uuid,)).fetchone() or [None])[0]
    # seed from the operation's migrated creators (confirmed launch-producing lineage)
    creators = [r[0] for r in conn.execute(
        "SELECT creator_wallet FROM wt_ops_v2_creators "
        "WHERE operation_uuid=? AND migration_time IS NOT NULL", (op_uuid,)).fetchall()]
    if not creators:
        return {"operation_uuid": op_uuid, "current_assigned_treasury": assigned,
                "positional_root_candidate": None, "confidence": 0.0,
                "evidence_path": [], "status": "UNRESOLVED",
                "reason": "no migrated creators to seed the lineage"}

    # walk up from each creator; the most-common convergence root wins
    from collections import Counter
    roots = Counter()
    best_path = []
    for c in creators:
        root, path, _reason = _walk_to_root(conn, op_uuid, c)
        roots[root] += 1
        if len(path) > len(best_path):
            best_path = path
    positional_root, votes = roots.most_common(1)[0]
    confidence = round(votes / len(creators), 2)

    # CONFIDENCE GATE. If the creators' walks DON'T converge to a single root (low
    # confidence), the persisted edge graph is too sparse for a reliable positional
    # call — the upstream lineage back to the treasury isn't fully linked. Mark these
    # UNRESOLVED rather than crying mismatch on incomplete data. We only flag a
    # ROLE_MISMATCH_REVIEW when the walk converges strongly AND disagrees with the
    # assigned treasury — the high-signal, data-complete case (e.g. 46f7→5ED5).
    # A real mismatch (like 46f7) is where the ASSIGNED treasury is itself funded from
    # within the operation — i.e. it's NOT actually the upstream root, something inside
    # the op funds it. A single-creator overshoot (walk stops one hop past a real
    # treasury at a pass-through) is NOT that: the real treasury has no intra-op inbound.
    # So the decisive test: does the assigned treasury have meaningful intra-op inbound?
    assigned_intra_inbound = bool(_intra_op_funders(conn, op_uuid, assigned)) if assigned else False
    CONVERGENCE_MIN = 0.5
    multi_creator = len(creators) >= 2

    if positional_root == assigned:
        status, reason = "OK", "positional root == assigned treasury"
    elif not assigned_intra_inbound:
        # assigned treasury is itself a root (nothing inside the op funds it) → the
        # walk merely overshot to a downstream pass-through. NOT a real mismatch.
        status = "OK"
        reason = "assigned treasury is an upstream root (no intra-op inbound); walk overshoot ignored"
        positional_root = assigned
    elif multi_creator and confidence < CONVERGENCE_MIN:
        status = "UNRESOLVED"
        reason = (f"creators' walks don't converge (conf {confidence}) — edge graph too "
                  f"sparse for a reliable positional root; needs full upstream lineage")
        positional_root = None
    else:
        # assigned treasury IS funded from inside the op AND a different root converges →
        # the real failure mode (assigned is a mid-chain distributor, not the source).
        status = "ROLE_MISMATCH_REVIEW"
        reason = (f"assigned={(assigned or '')[:10]}… is funded from within the op; funding "
                  f"converges upstream to {positional_root[:10]}… ({votes}/{len(creators)})")
    return {
        "operation_uuid": op_uuid, "current_assigned_treasury": assigned,
        "positional_root_candidate": positional_root, "confidence": confidence,
        "evidence_path": list(reversed(best_path)),   # root → … → creator
        "status": status, "reason": reason,
    }


def run_resolver(conn=None, persist: bool = True) -> dict:
    """Resolve all operations. NON-DESTRUCTIVE — records comparisons, changes no roles."""
    own = conn is None
    if own:
        conn = db_connect(OPS_DB_PATH, timeout=30)
    try:
        ensure_resolution_schema(conn)
        now = int(time.time())
        ops = [r[0] for r in conn.execute("SELECT operation_uuid FROM wt_ops_v2").fetchall()]
        stats = {"resolved": 0, "ok": 0, "mismatch": 0, "unresolved": 0}
        for op in ops:
            r = resolve_operation(conn, op)
            stats["resolved"] += 1
            stats[{"OK": "ok", "ROLE_MISMATCH_REVIEW": "mismatch", "UNRESOLVED": "unresolved"}[r["status"]]] += 1
            if persist:
                conn.execute(
                    "INSERT INTO wt_ops_v2_treasury_resolution (operation_uuid, "
                    "current_assigned_treasury, positional_root_candidate, confidence, "
                    "evidence_path, status, reason, resolved_at) VALUES (?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(operation_uuid) DO UPDATE SET "
                    "current_assigned_treasury=excluded.current_assigned_treasury, "
                    "positional_root_candidate=excluded.positional_root_candidate, "
                    "confidence=excluded.confidence, evidence_path=excluded.evidence_path, "
                    "status=excluded.status, reason=excluded.reason, resolved_at=excluded.resolved_at",
                    (op, r["current_assigned_treasury"], r["positional_root_candidate"],
                     r["confidence"], json.dumps(r["evidence_path"]), r["status"],
                     r["reason"], now))
        if persist:
            conn.commit()
        return stats
    finally:
        if own:
            conn.close()


def resolutions(conn=None) -> list:
    """Read the comparison table for the dashboard."""
    global _resolution_schema_ensured
    own = conn is None
    if own:
        conn = db_connect(OPS_DB_PATH, timeout=15)
    try:
        if not _resolution_schema_ensured:
            try:
                ensure_resolution_schema(conn)
            except Exception:
                pass  # schema already exists or locked — table was created at first run
            _resolution_schema_ensured = True
        cols = ["operation_uuid", "current_assigned_treasury", "positional_root_candidate",
                "confidence", "evidence_path", "status", "reason", "resolved_at"]
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM wt_ops_v2_treasury_resolution "
            "ORDER BY CASE status WHEN 'ROLE_MISMATCH_REVIEW' THEN 0 WHEN 'UNRESOLVED' THEN 1 "
            "ELSE 2 END, resolved_at DESC").fetchall()
        out = []
        for r in rows:
            d = {c: r[i] for i, c in enumerate(cols)}
            try:
                d["evidence_path"] = json.loads(d["evidence_path"]) if d["evidence_path"] else []
            except Exception:
                d["evidence_path"] = []
            out.append(d)
        return out
    finally:
        if own:
            conn.close()


if __name__ == "__main__":
    print(json.dumps(run_resolver(), indent=2))
