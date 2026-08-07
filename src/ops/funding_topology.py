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

from src.ops.lineage_quarantine import eligible_session_relation

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


def _subprov_candidate_watch_counts(ops_conn: sqlite3.Connection) -> dict[str, int]:
    """X65.10 (X65.8 design) -- {subprov_wallet: distinct candidate_wallet
    count} from wt_candidate_websocket_watches: every recorded wrap-close
    destination a subprov has ever produced, creator or not. Unlike
    wt_provisioning_edges (SUBPROV_TO_CREATOR only, written exclusively by
    the walkback success path once a creator is already known -- X65.4/X65.8
    Phase 1/2), this table is written live by _handle_subprov_tx()
    (src/core/ws_cascade.py) for every wrap-close/candidate-detection event,
    independent of whether that candidate later becomes a confirmed creator.

    Measured coverage of the confirmed-WATCHTOWER population: 90.7% (39/43)
    vs. wt_provisioning_edges' 2.3% (1/43) -- see
    docs/design/x65_8/x65_8_phase2_evidence_comparison.md.

    This function is queried INDEPENDENTLY of src/ops/campaign_classification.py
    (which reads the same underlying table via its own separate query,
    _fanout_evidence_for_subprovs()) -- no cross-module call, no shared
    function, no dependency on Campaign having run. Both classifiers
    independently consume the same observed evidence; neither calls the
    other (X65.8 Phase 5's required architecture, verified by
    tests/test_x65_10_topology_independence.py)."""
    if not _table_exists(ops_conn, "wt_candidate_websocket_watches"):
        return {}
    rows = ops_conn.execute(
        "SELECT subprov_wallet, COUNT(DISTINCT candidate_wallet) AS n "
        "FROM wt_candidate_websocket_watches "
        "WHERE subprov_wallet IS NOT NULL "
        "GROUP BY subprov_wallet"
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
    session_relation = eligible_session_relation(ops_conn)
    treasuries = {
        r[0] for r in ops_conn.execute(
            f"SELECT DISTINCT treasury_wallet FROM {session_relation} "
            "WHERE treasury_wallet IS NOT NULL"
        ).fetchall()
    }
    subprovs = {
        r[0] for r in ops_conn.execute(
            f"SELECT DISTINCT subprov_wallet FROM {session_relation} "
            "WHERE subprov_wallet IS NOT NULL"
        ).fetchall()
    }
    return treasuries & subprovs


def _walkback_topology_evidence(ops_conn: sqlite3.Connection) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """Materialize selected transaction-derived walkback paths for topology.

    Queue termination JSON is used only when it records a validated hop-2
    transaction. It supplies a structural edge, never an operation identity.
    """
    by_mint: dict[str, dict[str, Any]] = {}
    children: dict[str, set[str]] = {}
    if _table_exists(ops_conn, "wt_walkback_edge_candidates"):
        for row in ops_conn.execute(
            "SELECT mint,wallet,candidate_parent,hop_depth FROM wt_walkback_edge_candidates "
            "WHERE selection_status='SELECTED'"
        ):
            mint, wallet, parent, depth = row
            if not mint or not wallet or not parent:
                continue
            evidence = by_mint.setdefault(mint, {"depth": 0, "parents": set(), "edge_count": 0})
            evidence["depth"] = max(evidence["depth"], int(depth or 0))
            evidence["parents"].add(parent)
            evidence["edge_count"] += 1
            children.setdefault(parent, set()).add(wallet)
    if _table_exists(ops_conn, "wt_walkback_queue"):
        for row in ops_conn.execute(
            "SELECT mint,termination_reason_json FROM wt_walkback_queue "
            "WHERE termination_reason_json IS NOT NULL"
        ):
            try:
                reason = json.loads(row["termination_reason_json"] or "{}")
            except (TypeError, ValueError):
                continue
            parent = reason.get("hop2_candidate")
            child = reason.get("hop1")
            signature = reason.get("hop2_signature")
            if not (parent and child and signature):
                continue
            evidence = by_mint.setdefault(row["mint"], {"depth": 0, "parents": set(), "edge_count": 0})
            evidence["depth"] = max(evidence["depth"], 2)
            evidence["parents"].add(parent)
            evidence["edge_count"] += 1
            children.setdefault(parent, set()).add(child)
    return by_mint, {parent: len(wallets) for parent, wallets in children.items()}


def classify_topology_for_launch(
    ops_conn: sqlite3.Connection,
    *,
    subprov_wallet: str | None,
    treasury_wallet: str | None,
    subprovisioners_evidence: list[str] | None = None,
    treasuries_evidence: list[str] | None = None,
    sibling_counts: dict[str, int] | None = None,
    candidate_watch_counts: dict[str, int] | None = None,
    multi_level_subprovs: set[str] | None = None,
    mesh_treasuries: set[str] | None = None,
    walkback_evidence: dict[str, Any] | None = None,
    walkback_fanout_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Classify ONE launch's funding topology. Pure function over
    already-computed lookups (sibling_counts/candidate_watch_counts/
    multi_level_subprovs/mesh_treasuries) so callers can batch-compute those
    once per replay instead of once per launch.

    subprov_wallet/treasury_wallet: direct columns from wt_watchtower_launches
    when available. subprovisioners_evidence/treasuries_evidence: the
    evidence_json lists from wt_attribution_outcomes, for launches the live
    cascade never recorded a session for -- a fallback source, used only when
    subprov_wallet is None.

    candidate_watch_counts: X65.10 (X65.8 design) -- distinct candidate_wallet
    counts from wt_candidate_websocket_watches, consulted BEFORE
    sibling_counts (wt_provisioning_edges) at the Fan-Out/Linear decision
    point, since it has far higher coverage of the cascade-confirmed
    population (X65.8 Phase 2). wt_provisioning_edges remains a fallback,
    unconditionally reachable when this evidence is absent for a subprov --
    no existing evidence path is removed.
    """
    sibling_counts = sibling_counts or {}
    candidate_watch_counts = candidate_watch_counts or {}
    multi_level_subprovs = multi_level_subprovs or set()
    mesh_treasuries = mesh_treasuries or set()
    walkback_evidence = walkback_evidence or {}
    walkback_fanout_counts = walkback_fanout_counts or {}

    # Resolve the effective subprov/treasury set to evaluate, preferring the
    # direct wt_watchtower_launches columns (highest-confidence, cascade-
    # confirmed) over the attribution-outcome evidence_json lists (broader
    # coverage, lower per-hop confidence).
    subprovs = [subprov_wallet] if subprov_wallet else list(subprovisioners_evidence or [])
    treasuries = [treasury_wallet] if treasury_wallet else list(treasuries_evidence or [])

    walkback_parents = walkback_evidence.get("parents") or set()
    walkback_depth = int(walkback_evidence.get("depth") or 0)
    branching_parents = [
        parent for parent in walkback_parents if walkback_fanout_counts.get(parent, 0) > 1
    ]

    if walkback_depth >= 2 and branching_parents:
        max_fanout = max(walkback_fanout_counts[parent] for parent in branching_parents)
        return {
            "topology": MULTI_LEVEL_FAN_OUT,
            "label": TOPOLOGY_LABELS[MULTI_LEVEL_FAN_OUT],
            "derived_from": f"selected_walkback_depth={walkback_depth};upstream_fanout={max_fanout}",
        }

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

    # Fan-Out vs Linear: candidate-watch count on the (single-hop) subprov
    # (X65.10/X65.8) -- consulted FIRST, ahead of the wt_provisioning_edges
    # sibling count, since it covers 90.7% of the cascade-confirmed
    # population vs. 2.3% for wt_provisioning_edges (X65.8 Phase 2). Falls
    # through to the existing sibling_counts logic, UNCHANGED, whenever this
    # evidence is absent for the subprov -- no existing path is removed.
    if subprovs:
        sp = subprovs[0]
        n_candidates = candidate_watch_counts.get(sp)
        if n_candidates is not None:
            if n_candidates > 1:
                return {
                    "topology": FAN_OUT,
                    "label": TOPOLOGY_LABELS[FAN_OUT],
                    "derived_from": f"wt_candidate_websocket_watches_count={n_candidates}",
                }
            return {
                "topology": LINEAR,
                "label": TOPOLOGY_LABELS[LINEAR],
                "derived_from": "wt_candidate_websocket_watches_count=1",
            }
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
        if walkback_depth >= 1:
            immediate_fanout = max(
                [walkback_fanout_counts.get(parent, 0) for parent in walkback_parents] or [0]
            )
            if immediate_fanout > 1:
                return {
                    "topology": FAN_OUT, "label": TOPOLOGY_LABELS[FAN_OUT],
                    "derived_from": f"selected_walkback_parent_fanout={immediate_fanout}",
                }
            return {
                "topology": LINEAR, "label": TOPOLOGY_LABELS[LINEAR],
                "derived_from": f"selected_walkback_depth={walkback_depth};no_observed_branch",
            }
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
    core_conn = sqlite3.connect(f"file:{core_db_path}?mode=ro", uri=True, timeout=5)
    core_conn.row_factory = sqlite3.Row
    core_conn.execute("PRAGMA query_only=ON")
    try:
        sibling_counts = _subprov_sibling_counts(ops_conn)
        candidate_watch_counts = _subprov_candidate_watch_counts(ops_conn)
        multi_level_subprovs = _multi_level_subprovs(ops_conn)
        mesh_treasuries = _mesh_treasuries(ops_conn)
        walkback_by_mint, walkback_fanout_counts = _walkback_topology_evidence(ops_conn)

        # X65.35 -- the Stage-1 population is now every mint with an
        # attribution outcome AT ALL (no completed_at filter here); windowing
        # happens below by each mint's own resolved launch create time, not
        # by when its walkback happened to finish. See
        # discovery_window.launch_create_times_for_mints's docstring for the
        # precedence and why completed_at was the wrong basis.
        from src.ops.discovery_window import launch_create_times_for_mints

        all_rows = ops_conn.execute(
            "SELECT mint, evidence_json FROM wt_attribution_outcomes",
        ).fetchall()
        all_mints = [r["mint"] for r in all_rows]
        create_times = launch_create_times_for_mints(ops_conn, core_conn, all_mints)
        rows = [
            r for r in all_rows
            if r["mint"] in create_times and create_times[r["mint"]] >= since
        ]

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
                candidate_watch_counts=candidate_watch_counts,
                multi_level_subprovs=multi_level_subprovs,
                mesh_treasuries=mesh_treasuries,
                walkback_evidence=walkback_by_mint.get(mint),
                walkback_fanout_counts=walkback_fanout_counts,
            )
            assignments[mint] = result
            counts[result["topology"]] += 1

        total = len(rows)
        conserved = sum(counts.values()) == total
    finally:
        ops_conn.close()
        core_conn.close()

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
