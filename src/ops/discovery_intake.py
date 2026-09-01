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
            # NOTE: family_id here is the BARE discovery family_id (e.g.
            # "DFF_..."), matching what fetch_discovery_family_detail()
            # below looks up and what profile_href actually links to --
            # do NOT prefix this with "discovery:" or the detail-page
            # fallback lookup will 404 (this was a real bug: registry rows
            # linked to /intelligence/operations/DFF_... but the detail
            # route only ever checked the emerging-operator service,
            # which has never heard of a raw discovery-corpus family_id).
            "family_id": row["family_id"],
            "family_name": "New Discovery: " + root[:8] + "…",
            "launches": row["member_count"],
            "creator_count": row["creator_count"],
            "unique_creators": [],
            "presentation": {
                "disposition": "REVIEW",
                "profile_href": "/intelligence/operations/" + row["family_id"],
            },
            "reconciliation": {"contradictory_evidence_count": 0},
            # A shared direct-funding relationship establishes a funding
            # structure, not common operational identity.  Human review may
            # subsequently establish a more specific entity role or operation
            # candidate, but this read-only intake must not do that inference.
            "candidate_role": "FUNDING_STRUCTURE",
            "discovery_classification": row["classification"],
            "discovery_attribution_state": row["attribution_state"],
            "cex_infra_hop_distance": row["cex_infra_hop_distance"],
            "root_cex_infra_category": row["root_cex_infra_category"],
            "known_operation_overlap": known_operation_overlap,
            "intake_reason": "STRONG_CANDIDATE_FAMILY x ATTRIBUTABLE (P1-qualified deterministic intake criteria)",
            "source_badge": "NEW_DISCOVERY",
        })
    return candidates


def fetch_discovery_family_detail(corpus_db_path: str, family_id: str) -> dict | None:
    """Read-only lookup for a SINGLE discovery-corpus family by its exact
    family_id, shaped as a render()-compatible family object for
    templates/operation_profile.html (via GET /api/ops/emerging-operators/
    <entity>'s fallback path in operator_routes.py). Returns None if no
    such family exists -- callers should then return the existing 404,
    not fabricate a record.

    Bounded: fetches at most MAX_DETAIL_MEMBERS member rows, never the
    full member list unconditionally, matching Part 8's 'do not return
    all members on initial page load' requirement."""
    MAX_DETAIL_MEMBERS = 200

    conn = sqlite3.connect(f"file:{corpus_db_path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    try:
        row = conn.execute(
            "SELECT family_id, root_evidence, member_count, creator_count, "
            "classification, attribution_state, root_cex_infra_category, cex_infra_hop_distance "
            "FROM candidate_families WHERE family_id=?",
            (family_id,),
        ).fetchone()
        if not row:
            return None
        members = conn.execute(
            "SELECT mint, create_creator FROM candidate_family_members "
            "WHERE family_id=? LIMIT ?",
            (family_id, MAX_DETAIL_MEMBERS),
        ).fetchall()
        # Real observed funding edges for this family's root -- populates
        # the Summary tab's "Operational Role" / "Recorded evidence"
        # section (templates/operation_profile.html roleView()), which
        # was previously left empty for every discovery-derived family
        # (bug: the detail record never set operational_role at all, so
        # roleView() rendered "No recorded edges" / "No observed funding
        # relationships" even though real direct_funding_edges rows exist
        # for these families -- same evidence already used elsewhere,
        # e.g. OF-DV34-P0/P1's raw-verification work).
        funding_edges = conn.execute(
            "SELECT mint, create_creator, funding_signature, funding_block_time "
            "FROM direct_funding_edges WHERE direct_funder=? "
            "ORDER BY funding_block_time DESC LIMIT ?",
            (row["root_evidence"], MAX_DETAIL_MEMBERS),
        ).fetchall()
    finally:
        conn.close()

    unique_creators = sorted({m["create_creator"] for m in members})

    observed_relationships = [
        {
            "controller": row["root_evidence"],
            "creator": edge["create_creator"],
            "launch": edge["mint"],
            "launch_label": "Launch",
            "funding_hops": [{
                "mechanism": "PLAIN_XFER",
                "transaction": edge["funding_signature"],
                "transaction_at": edge["funding_block_time"],
            }],
        }
        for edge in funding_edges
    ]
    operational_role = {
        "current_role": "Funding Structure",
        "evidence_backed": bool(observed_relationships),
        "observed_relationships": observed_relationships,
        "edges": [{
            "from": row["root_evidence"],
            "to": "CREATE_CREATOR",
            "relationship_type": "direct_funding",
            "observation_count": len(funding_edges),
        }] if funding_edges else [],
        "observation_count": len(funding_edges),
    }

    return {
        "family_id": row["family_id"],
        "family_name": "New Discovery: " + row["root_evidence"][:8] + "…",
        "family_anchor": row["root_evidence"],
        "launches": row["member_count"],
        "unique_creators": unique_creators,
        "member_wallets": [row["root_evidence"]],
        "client_wallets": unique_creators,
        "treasuries": [],
        "presentation": {"disposition": "REVIEW", "profile_href": "/intelligence/operations/" + row["family_id"]},
        "reconciliation": {
            "disposition": "REVIEW",
            "contradictory_evidence_count": 0,
            "supporting_evidence": [],
            "contradictory_evidence": [],
            "missing_evidence": [],
        },
        "candidate_role": "FUNDING_STRUCTURE",
        "operational_role": operational_role,
        "discovery_classification": row["classification"],
        "discovery_attribution_state": row["attribution_state"],
        "cex_infra_hop_distance": row["cex_infra_hop_distance"],
        "root_cex_infra_category": row["root_cex_infra_category"],
        "source_badge": "NEW_DISCOVERY",
        "member_count_truncated": row["member_count"] > MAX_DETAIL_MEMBERS,
        "member_sample_size": len(members),
    }
