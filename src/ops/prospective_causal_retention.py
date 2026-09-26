"""Generic future-only causal-witness retention and prospective admission.

This module is deliberately detached from operation nomination.  A detector
supplies a compact, already-retained causal route; this module records no raw
provider payload and never treats nomination as membership.
"""
from __future__ import annotations

import hashlib
import json
from typing import Mapping

RELATION_VERSION = "OPERATION_MEMBER_CAUSAL_WITNESS_V1"
EVENT_VERSION = "OPERATION_MEMBER_COMMITTED_V1"


def witness_identity(record: Mapping) -> str:
    """Stable identity over immutable causal evidence, never mutable state."""
    fields = {k: record.get(k) for k in (
        "operation_id", "mint", "witness_type", "root", "coordinator",
        "lower_signature", "upper_signature", "slot", "transaction_order",
        "instruction_order", "source_evidence_id", "qualification_version",
    )}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def assess_prospective_candidate(candidate: Mapping, anchors: set[tuple[str, str]]) -> dict:
    """Apply deterministic generic admission gates to retained evidence only."""
    if candidate.get("selected_upstream_conflict"):
        return {"state": "CONFLICT", "reason": "selected_upstream_disagrees"}
    required = ("operation_id", "mint", "root", "coordinator", "lower_signature",
                "upper_signature", "slot", "transaction_order", "instruction_order",
                "source_evidence_id", "qualification_version")
    if not candidate.get("nominated") or any(candidate.get(k) in (None, "") for k in required):
        return {"state": "INSUFFICIENT_EVIDENCE", "reason": "missing_retained_causal_witness"}
    route = (candidate["root"], candidate["coordinator"])
    if route not in anchors:
        return {"state": "INSUFFICIENT_EVIDENCE", "reason": "no_matching_operation_anchor"}
    return {"state": "QUALIFIED_PROSPECTIVE_MEMBER", "reason": "causal_witness_matches_anchor",
            "witness_id": witness_identity(candidate), "automatic_membership_allowed": False}


def member_committed_event(operation_id: str, mint: str, membership_id: str, witness_id: str,
                           committed_at: int, qualification_version: str) -> dict:
    """Generic post-commit envelope; callers must invoke only after commit."""
    return {"event_version": EVENT_VERSION, "operation_id": operation_id, "mint": mint,
            "membership_id": membership_id, "witness_id": witness_id,
            "committed_at": committed_at, "qualification_semantic_version": qualification_version}
