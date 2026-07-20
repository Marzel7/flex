"""X29.1 — Operational Topology Intelligence Framework: the canonical model.

Combines the three independent classifiers (funding_topology.py,
operational_behaviour_tags.py, funding_mechanism.py) into ONE per-mint
record:

    {mint: {topology: str, behaviours: [str, ...], mechanisms: [str, ...]}}

This is the ONLY storage shape. Per the brief: "Do not store the hierarchy
itself. Instead store independent classifications... The hierarchy is
generated dynamically by the UI." No tree is persisted or computed here as a
data structure to store -- build_hierarchy() below computes a drill-down
VIEW on demand from this flat per-mint map, entirely derivable and never a
second source of truth.

Classifier execution order (per the brief's Stage 1/2/3): Topology first
(exactly one result), then Behaviour (additive), then Mechanism (additive).
This module does not change that order or add cross-dimension inference --
each classifier is computed independently and their results are merely
zipped together by mint.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Any

from src.ops.funding_topology import build_topology_classification, TOPOLOGY_ORDER, TOPOLOGY_LABELS
from src.ops.operational_behaviour_tags import build_behaviour_classification, BEHAVIOUR_ORDER, BEHAVIOUR_LABELS
from src.ops.funding_mechanism import build_mechanism_classification, MECHANISM_ORDER, MECHANISM_LABELS

# X29.1.3 — presentation-only grouping of the launch table by attribution
# outcome (e.g. "separate these by funding that ends at a CEX, then repeat
# creator"). Reuses src/ops/attribution_outcome.py's already-computed,
# already-persisted wt_attribution_outcomes.outcome_type -- no new
# detection or attribution logic. A small mapping decouples the UI from the
# internal outcome_type enum names, so the backend classification can be
# refined later without a UI redesign.
OUTCOME_GROUP_KNOWN_OPERATION = "KNOWN_OPERATION"
OUTCOME_GROUP_CEX_REACHED = "CEX_REACHED"
OUTCOME_GROUP_KNOWN_INFRASTRUCTURE = "KNOWN_INFRASTRUCTURE"
OUTCOME_GROUP_REPEAT_CREATOR = "REPEAT_CREATOR"
OUTCOME_GROUP_UNKNOWN_INFRASTRUCTURE = "UNKNOWN_INFRASTRUCTURE"
OUTCOME_GROUP_LINEAGE_GAP = "LINEAGE_GAP"
OUTCOME_GROUP_INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
OUTCOME_GROUP_UNATTRIBUTED = "UNATTRIBUTED"

OUTCOME_GROUP_LABELS = {
    OUTCOME_GROUP_KNOWN_OPERATION: "Known Operation",
    OUTCOME_GROUP_CEX_REACHED: "CEX Reached",
    OUTCOME_GROUP_KNOWN_INFRASTRUCTURE: "Known Infrastructure",
    OUTCOME_GROUP_REPEAT_CREATOR: "Repeat Creator",
    OUTCOME_GROUP_UNKNOWN_INFRASTRUCTURE: "Unknown Infrastructure",
    OUTCOME_GROUP_LINEAGE_GAP: "Lineage Gap",
    OUTCOME_GROUP_INSUFFICIENT_EVIDENCE: "Insufficient Evidence",
    OUTCOME_GROUP_UNATTRIBUTED: "Unattributed",
}

# Ordered most-specific-first, matching investigation_pipeline.py's own
# priority discipline; each outcome_type maps to exactly one group.
#
# CANONICAL_OPERATOR_REACHED gets its OWN group (Known Operation), separate
# from KNOWN_BRIDGE_REACHED/KNOWN_RELAY_REACHED (Known Infrastructure) --
# reaching a reviewed bridge/relay/protocol boundary is not the same
# strength of result as fully resolving a launch to a confirmed, named
# operator entity. Collapsing the two under one label hid that difference;
# this is presentation-mapping only, outcome_type/pipeline priority/
# detection logic are unchanged.
_OUTCOME_TYPE_TO_GROUP = {
    "CANONICAL_OPERATOR_REACHED": OUTCOME_GROUP_KNOWN_OPERATION,
    "KNOWN_CEX_REACHED": OUTCOME_GROUP_CEX_REACHED,
    "KNOWN_BRIDGE_REACHED": OUTCOME_GROUP_KNOWN_INFRASTRUCTURE,
    "KNOWN_RELAY_REACHED": OUTCOME_GROUP_KNOWN_INFRASTRUCTURE,
    "KNOWN_MULTI_TOKEN_CREATOR": OUTCOME_GROUP_REPEAT_CREATOR,
    "UNKNOWN_INFRASTRUCTURE": OUTCOME_GROUP_UNKNOWN_INFRASTRUCTURE,
    "LINEAGE_GAP": OUTCOME_GROUP_LINEAGE_GAP,
    "AMBIGUOUS_BRANCH": OUTCOME_GROUP_LINEAGE_GAP,
    "MAX_DEPTH": OUTCOME_GROUP_LINEAGE_GAP,
    "INSUFFICIENT_EVIDENCE": OUTCOME_GROUP_INSUFFICIENT_EVIDENCE,
}

# Known Operation first -- the strongest attribution result -- forming a
# clean attribution ladder from strongest to weakest.
OUTCOME_GROUP_ORDER = (
    OUTCOME_GROUP_KNOWN_OPERATION,
    OUTCOME_GROUP_CEX_REACHED,
    OUTCOME_GROUP_KNOWN_INFRASTRUCTURE,
    OUTCOME_GROUP_REPEAT_CREATOR,
    OUTCOME_GROUP_UNKNOWN_INFRASTRUCTURE,
    OUTCOME_GROUP_LINEAGE_GAP,
    OUTCOME_GROUP_INSUFFICIENT_EVIDENCE,
    OUTCOME_GROUP_UNATTRIBUTED,
)


def outcome_group_for(outcome_type: str | None) -> str:
    """Maps a raw wt_attribution_outcomes.outcome_type onto one presentation
    group. A mint with no attribution outcome at all (e.g. never reached by
    the attribution pipeline) maps to UNATTRIBUTED, not silently dropped."""
    if not outcome_type:
        return OUTCOME_GROUP_UNATTRIBUTED
    return _OUTCOME_TYPE_TO_GROUP.get(outcome_type, OUTCOME_GROUP_UNATTRIBUTED)


def _outcome_types_by_mint(ops_db_path: str, mints: list[str]) -> dict[str, str]:
    """Read-only lookup of wt_attribution_outcomes.outcome_type for exactly
    the given mints -- no new detection, just fetching an already-persisted
    fact. Uses the most recently completed outcome per mint if more than one
    row exists (rare; matches the same recency-preference convention used
    elsewhere in this codebase)."""
    if not mints:
        return {}
    conn = sqlite3.connect(f"file:{ops_db_path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    try:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wt_attribution_outcomes'"
        ).fetchone():
            return {}
        placeholders = ",".join("?" for _ in mints)
        rows = conn.execute(
            f"SELECT mint, outcome_type FROM wt_attribution_outcomes "
            f"WHERE mint IN ({placeholders}) "
            f"ORDER BY completed_at DESC",
            mints,
        ).fetchall()
    finally:
        conn.close()
    result: dict[str, str] = {}
    for r in rows:
        result.setdefault(r["mint"], r["outcome_type"])  # first (most recent) wins
    return result


def group_mints_by_outcome(ops_db_path: str, mints: list[str]) -> dict[str, Any]:
    """Groups an already-filtered mint list (e.g. the result of query()) by
    attribution-outcome presentation group. Pure presentation grouping over
    an existing, already-computed classification -- no new detection.
    Returns {"groups": [{"group":..., "label":..., "count":..., "mints":[...]}, ...]}
    ordered by OUTCOME_GROUP_ORDER, omitting empty groups."""
    outcome_by_mint = _outcome_types_by_mint(ops_db_path, mints)
    buckets: dict[str, list[str]] = {g: [] for g in OUTCOME_GROUP_ORDER}
    for mint in mints:
        group = outcome_group_for(outcome_by_mint.get(mint))
        buckets[group].append(mint)
    return {
        "groups": [
            {"group": g, "label": OUTCOME_GROUP_LABELS[g], "count": len(buckets[g]), "mints": buckets[g]}
            for g in OUTCOME_GROUP_ORDER
            if buckets[g]
        ],
    }


def build_operational_intelligence(
    ops_db_path: str,
    core_db_path: str,
    *,
    window_seconds: int = 86400,
    now: int | None = None,
) -> dict[str, Any]:
    """Runs all three classifiers and combines them into the canonical
    per-mint record. Read-only, zero writes -- this function does not
    persist anything; callers decide whether/how to cache the result.

    Returns:
      {
        "generated_at": ..., "window_seconds": ...,
        "total_launches": N,
        "topology_summary": [...], "behaviour_summary": [...], "mechanism_summary": [...],
        "records": {mint: {"topology": ..., "behaviours": [...], "mechanisms": [...]}},
      }
    """
    now = int(now or time.time())

    topology = build_topology_classification(ops_db_path, core_db_path, window_seconds=window_seconds, now=now)
    behaviour = build_behaviour_classification(ops_db_path, core_db_path, window_seconds=window_seconds, now=now)
    mechanism = build_mechanism_classification(ops_db_path, window_seconds=window_seconds, now=now)

    all_mints = set(topology["assignments"]) | set(behaviour["assignments"]) | set(mechanism["assignments"])
    records: dict[str, dict[str, Any]] = {}
    for mint in all_mints:
        t = topology["assignments"].get(mint)
        b = behaviour["assignments"].get(mint)
        m = mechanism["assignments"].get(mint)
        records[mint] = {
            "topology": t["topology"] if t else "UNKNOWN",
            "topology_derived_from": t.get("derived_from") if t else None,
            "behaviours": b["behaviours"] if b else [],
            "mechanisms": m["mechanisms"] if m else [],
            "creator": b.get("creator") if b else None,
        }

    return {
        "generated_at": now,
        "window_seconds": window_seconds,
        "total_launches": topology["total_launches"],
        "conserved": topology["conserved"],
        "topology_summary": topology["topologies"],
        "behaviour_summary": behaviour["behaviours"],
        "mechanism_summary": mechanism["mechanisms"],
        "records": records,
    }


def build_hierarchy(intelligence: dict[str, Any]) -> dict[str, Any]:
    """Computes the Topology -> Behaviour -> Mechanism drill-down VIEW on
    demand from the flat per-mint `records` map. Nothing here is stored --
    call this fresh from the flat map whenever the UI needs the tree; it is
    always reproducible from `records` alone, per the brief's storage-model
    requirement.

    A mint with zero behaviour tags is grouped under a "(none)" bucket at
    the behaviour level, so counts still conserve; same for zero mechanism
    tags. A mint with >1 behaviour or mechanism tag appears under EACH of
    its tags at that level -- this is the additive property surfacing
    correctly in the tree (a launch with both Rapid Birth and Burst Launcher
    contributes to both branches), which means node counts one level down
    from Topology can sum to MORE than the topology's own total. This is
    expected, not a conservation bug -- Topology itself is still exclusive
    (each mint appears under exactly one top-level node).
    """
    records = intelligence["records"]
    tree: dict[str, Any] = {}

    for topology in TOPOLOGY_ORDER:
        mints_here = [m for m, r in records.items() if r["topology"] == topology]
        node = {
            "topology": topology,
            "label": TOPOLOGY_LABELS[topology],
            "count": len(mints_here),
            "children": [],
        }
        behaviour_buckets: dict[str, list[str]] = {b: [] for b in BEHAVIOUR_ORDER}
        behaviour_buckets["_NONE_"] = []
        for m in mints_here:
            tags = records[m]["behaviours"]
            if not tags:
                behaviour_buckets["_NONE_"].append(m)
            else:
                for t in tags:
                    behaviour_buckets[t].append(m)

        for behaviour in BEHAVIOUR_ORDER:
            b_mints = behaviour_buckets[behaviour]
            if not b_mints:
                continue
            b_node = {
                "behaviour": behaviour,
                "label": BEHAVIOUR_LABELS[behaviour],
                "count": len(b_mints),
                "children": [],
            }
            mechanism_buckets: dict[str, list[str]] = {mech: [] for mech in MECHANISM_ORDER}
            mechanism_buckets["_NONE_"] = []
            for m in b_mints:
                mechs = records[m]["mechanisms"]
                if not mechs:
                    mechanism_buckets["_NONE_"].append(m)
                else:
                    for mech in mechs:
                        mechanism_buckets[mech].append(m)
            for mechanism in MECHANISM_ORDER:
                mech_mints = mechanism_buckets[mechanism]
                if not mech_mints:
                    continue
                b_node["children"].append({
                    "mechanism": mechanism,
                    "label": MECHANISM_LABELS[mechanism],
                    "count": len(mech_mints),
                })
            if mechanism_buckets["_NONE_"]:
                b_node["children"].append({
                    "mechanism": None,
                    "label": "(no mechanism evidence)",
                    "count": len(mechanism_buckets["_NONE_"]),
                })
            node["children"].append(b_node)

        if behaviour_buckets["_NONE_"]:
            none_mints = behaviour_buckets["_NONE_"]
            none_node = {"behaviour": None, "label": "(no behaviour tags)", "count": len(none_mints), "children": []}
            mechanism_buckets2: dict[str, list[str]] = {mech: [] for mech in MECHANISM_ORDER}
            mechanism_buckets2["_NONE_"] = []
            for m in none_mints:
                mechs = records[m]["mechanisms"]
                if not mechs:
                    mechanism_buckets2["_NONE_"].append(m)
                else:
                    for mech in mechs:
                        mechanism_buckets2[mech].append(m)
            for mechanism in MECHANISM_ORDER:
                mech_mints = mechanism_buckets2[mechanism]
                if mech_mints:
                    none_node["children"].append({"mechanism": mechanism, "label": MECHANISM_LABELS[mechanism], "count": len(mech_mints)})
            if mechanism_buckets2["_NONE_"]:
                none_node["children"].append({"mechanism": None, "label": "(no mechanism evidence)", "count": len(mechanism_buckets2["_NONE_"])})
            node["children"].append(none_node)

        tree[topology] = node

    return {"generated_at": intelligence["generated_at"], "tree": [tree[t] for t in TOPOLOGY_ORDER]}


def query(
    intelligence: dict[str, Any],
    *,
    topology: str | None = None,
    behaviour: str | None = None,
    mechanism: str | None = None,
) -> list[str]:
    """Cross-dimensional query over the flat records map -- the brief's
    explicit requirement that "no hierarchy should prevent cross-dimensional
    searching." Any combination of filters may be supplied; omitted filters
    are unconstrained. Examples this directly supports:
      query(intel, topology="FAN_OUT")                     -- all Fan-Out
      query(intel, behaviour="RAPID_BIRTH_LAUNCH")          -- every Rapid Birth launch, any topology
      query(intel, mechanism="WSOL_WRAP_CLOSE")             -- every Wrap-Close launch
      query(intel, topology="FAN_OUT", mechanism="PLAIN_TRANSFER")  -- Fan-Out using Plain Transfer
      query(intel, topology="MESH", behaviour="BURST_LAUNCH")       -- Mesh + Burst Launcher
    """
    out = []
    for mint, r in intelligence["records"].items():
        if topology is not None and r["topology"] != topology:
            continue
        if behaviour is not None and behaviour not in r["behaviours"]:
            continue
        if mechanism is not None and mechanism not in r["mechanisms"]:
            continue
        out.append(mint)
    return out
