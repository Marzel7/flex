"""OPS-UI-P3: discovery-corpus intake into the EXISTING Analyst Queue.

Reuses the P1-qualified, deterministic intake criteria
(classification == STRONG_CANDIDATE_FAMILY, attribution_state ==
ATTRIBUTABLE -- see docs/audits/ops_ui_p1_discovery_intake_contract.json)
and produces family-shaped dicts compatible with the SAME rendering
function (familyRow) the existing operators_index.html Analyst Queue
already uses -- no second queue, no new workflow, no schema change.

Read-only against database/local_operation_discovery_corpus.db. Never
writes, never touches operators/operator_entities/wt_confirmed_treasuries,
never promotes anything. The human review/promotion workflow is entirely
unchanged; this module only supplies additional candidate rows to the
existing review surface.
"""
from __future__ import annotations

import sqlite3

INTAKE_CLASSIFICATION = "STRONG_CANDIDATE_FAMILY"
INTAKE_ATTRIBUTION_STATE = "ATTRIBUTABLE"
MAX_INTAKE_CANDIDATES = 20  # bounded -- never dump all 385 families


def fetch_discovery_intake_candidates(corpus_db_path: str, *, known_operator_entities: frozenset[str] = frozenset()) -> list[dict]:
    """Returns eligible discovery-corpus families as minimal family-shaped
    dicts, ready for the frontend's existing familyRow(f, 'review')
    renderer. Excludes any family whose root already appears in
    known_operator_entities (Part 14 -- Watchtower/3SW2 discovery-overlap
    guard: never silently merge a discovery family into an existing
    canonical operator's queue presentation)."""
    conn = sqlite3.connect(f"file:{corpus_db_path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    try:
        rows = conn.execute(
            "SELECT family_id, root_evidence, member_count, creator_count, "
            "classification, attribution_state, root_cex_infra_category, cex_infra_hop_distance "
            "FROM candidate_families "
            "WHERE classification=? AND attribution_state=? "
            "ORDER BY member_count DESC LIMIT ?",
            (INTAKE_CLASSIFICATION, INTAKE_ATTRIBUTION_STATE, MAX_INTAKE_CANDIDATES),
        ).fetchall()
    finally:
        conn.close()

    candidates = []
    for row in rows:
        root = row["root_evidence"]
        known_operation_overlap = root in known_operator_entities
        candidates.append({
            "family_id": "discovery:" + row["family_id"],
            "family_name": "New Discovery: " + root[:8] + "…",
            "launches": row["member_count"],
            "creator_count": row["creator_count"],
            "unique_creators": [],
            "presentation": {
                "disposition": "REVIEW",
                "profile_href": "/intelligence/operations/" + row["family_id"],
            },
            "reconciliation": {"contradictory_evidence_count": 0},
            "candidate_role": "PROVISIONING_NETWORK_CANDIDATE",
            "discovery_classification": row["classification"],
            "discovery_attribution_state": row["attribution_state"],
            "cex_infra_hop_distance": row["cex_infra_hop_distance"],
            "root_cex_infra_category": row["root_cex_infra_category"],
            "known_operation_overlap": known_operation_overlap,
            "intake_reason": "STRONG_CANDIDATE_FAMILY x ATTRIBUTABLE (P1-qualified deterministic intake criteria)",
            "source_badge": "NEW_DISCOVERY",
        })
    return candidates
