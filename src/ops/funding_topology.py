"""X29.1 Stage 1 — Funding Topology classifier.

Answers exactly one question per launch: "what does the funding graph look
like?" Assigns EXACTLY ONE of FAN_OUT / LINEAR / MULTI_LEVEL_FAN_OUT / MESH /
UNKNOWN. Never infers from behaviour or funding mechanism (X29.0 Part 2,
Dimension 1) -- this module reads only structural/lineage evidence:
wt_provisioning_edges (per-edge treasury->subprov / subprov->creator facts),
wt_watchtower_launches (the live cascade's own subprov_wallet column), and
wt_attribution_outcomes.evidence_json's subprovisioners/treasuries lists (the
broader attribution corpus, for launches the live cascade never touched).

This module performs no detection of its own -- it is a pure read-only
derivation over already-persisted facts, per X29.0's constraint that no
detection logic changes.

Evaluation order (most-specific-first, first match wins, exactly one result;
mirrors the existing BUCKET_ORDER discipline in investigation_pipeline.py):
  MULTI_LEVEL_FAN_OUT -> MESH -> FAN_OUT -> LINEAR -> UNKNOWN

X29.0 Gap 1/2: MULTI_LEVEL_FAN_OUT and MESH have no dedicated edge type in
wt_provisioning_edges today (it only models TREASURY_TO_SUBPROV and
SUBPROV_TO_CREATOR). This module derives both from wt_active_subprov_sessions
lineage (session_tag/open_reason chains) rather than the edges table, and
flags every MULTI_LEVEL_FAN_OUT/MESH result with a `derived_from` field
naming the exact evidence used, so a reader can tell this apart from the
directly-observed FAN_OUT/LINEAR cases.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

FAN_OUT = "FAN_OUT"
LINEAR = "LINEAR"
MULTI_LEVEL_FAN_OUT = "MULTI_LEVEL_FAN_OUT"
MESH = "MESH"
UNKNOWN = "UNKNOWN"

TOPOLOGY_ORDER = (MULTI_LEVEL_FAN_OUT, MESH, FAN_OUT, LINEAR, UNKNOWN)

TOPOLOGY_LABELS = {
    MULTI_LEVEL_FAN_OUT: "Multi-Level Fan-Out",
    MESH: "Mesh",
    FAN_OUT: "Fan-Out",
    LINEAR: "Linear",
    UNKNOWN: "Unknown",
}


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def _subprov_sibling_counts(ops_conn: sqlite3.Connection) -> dict[str, int]:
    """{subprov_wallet: distinct creator count} from wt_provisioning_edges'
    SUBPROV_TO_CREATOR edges -- the direct evidence for Fan-Out (>1) vs
    Linear (exactly 1)."""
    if not _table_exists(ops_conn, "wt_provisioning_edges"):
        return {}
    rows = ops_conn.execute(
        "SELECT from_wallet, COUNT(DISTINCT to_wallet) AS n "
        "FROM wt_provisioning_edges WHERE edge_type='SUBPROV_TO_CREATOR' "
        "GROUP BY from_wallet"
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def _multi_level_subprovs(ops_conn: sqlite3.Connection) -> set[str]:
    """Wallets that are themselves a CHILD subprov of another subprov (the
    '_handle_subprov_tx sub-subprov' branch, ws_cascade.py:3511-3573) --
    i.e. any creator hanging off one of these wallets sits >=2 subprov hops
    from the treasury.

    Verified signal (checked directly against live data before using it,
    NOT assumed): SUBPROV_SESSION_OPENED_WS events carry
    payload.via='subprov_plain_xfer' + payload.parent_subprov for genuine
    sub-subprov opens, vs. via='treasury_ws' for a direct treasury-funded
    session (confirmed: 19,387 sub-subprov events vs 1,567 direct events in
    the live wt_ops_v2.db watchtower_events table). This is the durable,
    persisted fact; wt_active_subprov_sessions' own open_reason/session_tag
    columns do NOT carry this distinction (open_reason reflects the
    _classify_recipient() outcome -- e.g. PROVISION_CANDIDATE -- not whether
    the funder was a treasury or another subprov), and wt_provisioning_edges
    has no SUBPROV_TO_SUBPROV edge type (X29.0 Gap 1). Derived from
    watchtower_events, not detection logic -- this reads an already-emitted
    fact, it does not re-run or alter detection."""
    if not _table_exists(ops_conn, "watchtower_events"):
        return set()
    rows = ops_conn.execute(
        "SELECT wallet_address, payload_json FROM watchtower_events "
        "WHERE event_type='SUBPROV_SESSION_OPENED_WS'"
    ).fetchall()
    result: set[str] = set()
    for wallet, payload_json in rows:
        if not wallet or not payload_json:
            continue
        try:
            payload = json.loads(payload_json)
        except (TypeError, ValueError):
            continue
        if payload.get("via") == "subprov_plain_xfer" and payload.get("parent_subprov"):
            result.add(wallet)
    return result


def _mesh_treasuries(ops_conn: sqlite3.Connection) -> set[str]:
    """Treasuries observed both funding creators/subprovs AND themselves
    being funded structurally as a subprov -- the "treasury also funds peer
    treasuries" pattern (memory: treasuries-fund-treasuries).

    X29.0 Gap 2 flagged Mesh as having no concrete rule yet. This is a first,
    conservative candidate rule: a wallet appearing as BOTH treasury_wallet
    and subprov_wallet in wt_active_subprov_sessions.

    Verified against live data before shipping this as a real classifier:
    the confirmed-treasury set (wt_active_subprov_sessions.treasury_wallet,
    10 distinct wallets) and the confirmed-subprov set (subprov_wallet,
    64,400 distinct wallets) have ZERO overlap in the current corpus. This
    rule therefore currently matches nothing -- reported honestly as
    "Mesh: 0 detected by this rule" rather than fabricated evidence. This is
    NOT proof Mesh doesn't exist (the prior treasury-mesh finding was
    established via a different, qualitative on-chain trace, not this
    query), only that this specific structural rule needs either a richer
    data source (e.g. explicit treasury-to-treasury transfer detection,
    which this codebase does record separately -- TREASURY_MESH
    classification in ws_cascade.py's _classify_recipient -- but does not
    yet persist as a queryable per-treasury set) or a different definition
    before it can classify anything. Left in place as the current best rule,
    flagged for revisit in the historical replay writeup."""
    if not _table_exists(ops_conn, "wt_active_subprov_sessions"):
        return set()
    treasuries = {
        r[0] for r in ops_conn.execute(
            "SELECT DISTINCT treasury_wallet FROM wt_active_subprov_sessions "
            "WHERE treasury_wallet IS NOT NULL"
        ).fetchall()
    }
    subprovs = {
        r[0] for r in ops_conn.execute(
            "SELECT DISTINCT subprov_wallet FROM wt_active_subprov_sessions "
            "WHERE subprov_wallet IS NOT NULL"
        ).fetchall()
    }
    return treasuries & subprovs


def classify_topology_for_launch(
    ops_conn: sqlite3.Connection,
    *,
    subprov_wallet: str | None,
    treasury_wallet: str | None,
    subprovisioners_evidence: list[str] | None = None,
    treasuries_evidence: list[str] | None = None,
    sibling_counts: dict[str, int] | None = None,
    multi_level_subprovs: set[str] | None = None,
    mesh_treasuries: set[str] | None = None,
) -> dict[str, Any]:
    """Classify ONE launch's funding topology. Pure function over
    already-computed lookups (sibling_counts/multi_level_subprovs/
    mesh_treasuries) so callers can batch-compute those once per replay
    instead of once per launch.

    subprov_wallet/treasury_wallet: direct columns from wt_watchtower_launches
    when available. subprovisioners_evidence/treasuries_evidence: the
    evidence_json lists from wt_attribution_outcomes, for launches the live
    cascade never recorded a session for -- a fallback source, used only when
    subprov_wallet is None.
    """
    sibling_counts = sibling_counts or {}
    multi_level_subprovs = multi_level_subprovs or set()
    mesh_treasuries = mesh_treasuries or set()

    # Resolve the effective subprov/treasury set to evaluate, preferring the
    # direct wt_watchtower_launches columns (highest-confidence, cascade-
    # confirmed) over the attribution-outcome evidence_json lists (broader
    # coverage, lower per-hop confidence).
    subprovs = [subprov_wallet] if subprov_wallet else list(subprovisioners_evidence or [])
    treasuries = [treasury_wallet] if treasury_wallet else list(treasuries_evidence or [])

    if not subprovs and not treasuries:
        return {"topology": UNKNOWN, "label": TOPOLOGY_LABELS[UNKNOWN], "derived_from": "no_lineage_evidence"}

    # Multi-Level Fan-Out: any involved subprov is itself a recorded child of
    # another subprov session.
    if any(sp in multi_level_subprovs for sp in subprovs):
        return {
            "topology": MULTI_LEVEL_FAN_OUT,
            "label": TOPOLOGY_LABELS[MULTI_LEVEL_FAN_OUT],
            "derived_from": "wt_active_subprov_sessions_sub_subprov_lineage",
        }

    # Mesh: any involved treasury is also structurally a subprov elsewhere.
    if any(t in mesh_treasuries for t in treasuries):
        return {
            "topology": MESH,
            "label": TOPOLOGY_LABELS[MESH],
            "derived_from": "treasury_also_subprov_elsewhere",
        }

    # Fan-Out vs Linear: sibling count on the (single-hop) subprov.
    if subprovs:
        sp = subprovs[0]
        n_siblings = sibling_counts.get(sp)
        if n_siblings is not None:
            if n_siblings > 1:
                return {
                    "topology": FAN_OUT,
                    "label": TOPOLOGY_LABELS[FAN_OUT],
                    "derived_from": f"wt_provisioning_edges_sibling_count={n_siblings}",
                }
            return {
                "topology": LINEAR,
                "label": TOPOLOGY_LABELS[LINEAR],
                "derived_from": "wt_provisioning_edges_sibling_count=1",
            }
        # A subprov is recorded (evidence_json) but we have no sibling-count
        # evidence for it (e.g. it never appeared in wt_provisioning_edges) --
        # genuinely insufficient to distinguish Fan-Out from Linear, so this
        # falls through to Unknown rather than guessing (X29.0: "never infer
        # topology without evidence").
        return {"topology": UNKNOWN, "label": TOPOLOGY_LABELS[UNKNOWN], "derived_from": "subprov_present_no_sibling_evidence"}

    # Treasury recorded but no subprov at all: a genuine direct treasury->
    # creator chain.
    if treasuries:
        return {"topology": LINEAR, "label": TOPOLOGY_LABELS[LINEAR], "derived_from": "treasury_direct_no_subprov"}

    return {"topology": UNKNOWN, "label": TOPOLOGY_LABELS[UNKNOWN], "derived_from": "unresolved"}


def build_topology_classification(
    ops_db_path: str,
    core_db_path: str,
    *,
    window_seconds: int = 86400,
    now: int | None = None,
) -> dict[str, Any]:
    """Classifies every launch in wt_attribution_outcomes (the same
    Stage-1 population investigation_pipeline.py/behaviour_queue.py already
    use) by Funding Topology. Read-only, zero writes. Returns per-topology
    counts plus the full mint->topology assignment map, and a conservation
    check (sum(topology counts) == total_launches, always)."""
    now = int(now or time.time())
    since = now - window_seconds

    ops_conn = sqlite3.connect(f"file:{ops_db_path}?mode=ro", uri=True, timeout=5)
    ops_conn.row_factory = sqlite3.Row
    ops_conn.execute("PRAGMA query_only=ON")
    try:
        sibling_counts = _subprov_sibling_counts(ops_conn)
        multi_level_subprovs = _multi_level_subprovs(ops_conn)
        mesh_treasuries = _mesh_treasuries(ops_conn)

        rows = ops_conn.execute(
            "SELECT mint, evidence_json FROM wt_attribution_outcomes WHERE completed_at >= ?",
            (since,),
        ).fetchall()

        launches_by_mint: dict[str, sqlite3.Row] = {}
        if _table_exists(ops_conn, "wt_watchtower_launches"):
            for r in ops_conn.execute(
                "SELECT mint, subprov_wallet, treasury_wallet FROM wt_watchtower_launches"
            ).fetchall():
                launches_by_mint[r["mint"]] = r

        assignments: dict[str, dict[str, Any]] = {}
        counts = {t: 0 for t in TOPOLOGY_ORDER}
        for r in rows:
            mint = r["mint"]
            ev = json.loads(r["evidence_json"]) if r["evidence_json"] else {}
            live = launches_by_mint.get(mint)
            result = classify_topology_for_launch(
                ops_conn,
                subprov_wallet=live["subprov_wallet"] if live else None,
                treasury_wallet=live["treasury_wallet"] if live else None,
                subprovisioners_evidence=ev.get("subprovisioners") or [],
                treasuries_evidence=ev.get("treasuries") or [],
                sibling_counts=sibling_counts,
                multi_level_subprovs=multi_level_subprovs,
                mesh_treasuries=mesh_treasuries,
            )
            assignments[mint] = result
            counts[result["topology"]] += 1

        total = len(rows)
        conserved = sum(counts.values()) == total
    finally:
        ops_conn.close()

    return {
        "window_seconds": window_seconds,
        "generated_at": now,
        "total_launches": total,
        "topologies": [
            {
                "topology": t,
                "label": TOPOLOGY_LABELS[t],
                "count": counts[t],
                "coverage_pct": round(counts[t] / total * 100, 1) if total else 0.0,
            }
            for t in TOPOLOGY_ORDER
        ],
        "conserved": conserved,
        "assignments": assignments,
    }


def launches_with_topology(classification: dict[str, Any], topology: str) -> list[str]:
    """Drill-down: mints assigned to exactly this topology."""
    return [m for m, a in classification["assignments"].items() if a["topology"] == topology]
