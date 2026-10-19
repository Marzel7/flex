"""PSI0H-H4R1 bounded human review contract.

Read-only presentation layer over the already-qualified, immutable
PSI0H-H4R review packet/index. Generates NO new candidates, performs NO
new discovery, makes NO provider/RPC calls, and assigns NO automatic
human disposition. Every candidate is emitted with disposition
PENDING_HUMAN_DECISION until a human explicitly records one of the
allowed review options.
"""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Mapping

SCHEMA_VERSION = "psi0h-h4r1.bounded-human-review.v1"
MILESTONE = "PSI0H-H4R1"

EXPECTED_H4R_PACKET_DIGEST = "2ed2edb6b74db6b749166b05b01189328aefdbc85e6e212f762ba9cd5f8e32bb"
EXPECTED_H4R_INDEX_DIGEST = "5cbba208ad8a4bc217237ba7a50d3ff1a4046f0157943cf062f0e37a04ba4afa"

HUMAN_REVIEW_OPTIONS = (
    "COMMON_PLAYBOOK_ONLY",
    "PLAUSIBLE_OPERATIONAL_CONTINUITY",
    "PLAUSIBLE_OPERATIONAL_FAMILY",
    "INSUFFICIENT_EVIDENCE",
    "CONFLICTING_EVIDENCE",
)

PENDING_DISPOSITION = "PENDING_HUMAN_DECISION"


class Psi0hH4R1BoundedHumanReviewError(RuntimeError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _digest(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


def _build_review_sheet_row(row: Mapping[str, Any]) -> dict[str, Any]:
    candidate_id = str(row.get("continuity_candidate_id") or "").strip()
    if not candidate_id:
        raise Psi0hH4R1BoundedHumanReviewError("PSI0H_H4R1_CANDIDATE_ID_INVALID")

    op_a = row.get("operation_a") or {}
    op_b = row.get("operation_b") or {}
    accounting = row.get("evidence_accounting") or {}
    guards = row.get("identity_guards") or {}

    return {
        "continuity_candidate_id": candidate_id,
        "operation_a": {
            "operation_id": op_a.get("operation_id"),
            "operation_population_id": op_a.get("operation_population_id"),
            "source_path": op_a.get("source_path"),
        },
        "operation_b": {
            "operation_id": op_b.get("operation_id"),
            "operation_population_id": op_b.get("operation_population_id"),
            "source_path": op_b.get("source_path"),
        },
        "separate_operation_identities": bool(
            guards.get("operation_a_separate_identity") and guards.get("operation_b_separate_identity")
        ),
        "common_playbook_evidence": list(row.get("common_playbook_evidence") or []),
        "continuity_specific_evidence": list(row.get("continuity_specific_evidence") or []),
        "funding_topology_mechanism_temporal_evidence": list(
            accounting.get("independent_feature_dimensions") or []
        ),
        "address_infrastructure_succession_evidence": [
            v for v in (row.get("continuity_specific_evidence") or []) if v.startswith("evidence_ref_overlap")
        ],
        "supporting_evidence_count": accounting.get("supporting_evidence_count"),
        "supporting_primitive_count": accounting.get("supporting_primitive_count"),
        "provenance": (row.get("immutable_evidence_refs") or {}),
        "missing_evidence": list(accounting.get("missing_evidence") or []),
        "conflicts": list(accounting.get("conflicts") or []),
        "identity_guards": {
            "same_human_claim": bool(guards.get("same_human_claim", False)),
            "same_operation_claim": bool(guards.get("same_operation_claim", False)),
            "operation_a_separate_identity": bool(guards.get("operation_a_separate_identity", True)),
            "operation_b_separate_identity": bool(guards.get("operation_b_separate_identity", True)),
        },
        "system_nominated_options": list(row.get("allowed_review_options") or []),
        "allowed_human_review_options": list(HUMAN_REVIEW_OPTIONS),
        "human_disposition": PENDING_DISPOSITION,
        "human_decided_by": None,
        "human_decided_at_utc": None,
        "human_notes": None,
    }


def prepare_bounded_human_review(
    *,
    h4r_packet: Mapping[str, Any],
    h4r_index: Mapping[str, Any],
) -> dict[str, Any]:
    packet_digest = h4r_packet.get("artifact_digest")
    index_digest = h4r_index.get("artifact_digest")
    if packet_digest != EXPECTED_H4R_PACKET_DIGEST:
        raise Psi0hH4R1BoundedHumanReviewError("PSI0H_H4R1_H4R_PACKET_DIGEST_MISMATCH")
    if index_digest != EXPECTED_H4R_INDEX_DIGEST:
        raise Psi0hH4R1BoundedHumanReviewError("PSI0H_H4R1_H4R_INDEX_DIGEST_MISMATCH")
    if h4r_packet.get("status") != "PASS":
        raise Psi0hH4R1BoundedHumanReviewError("PSI0H_H4R1_H4R_PACKET_NOT_PASS")
    if h4r_packet.get("candidate_count") != 13:
        raise Psi0hH4R1BoundedHumanReviewError("PSI0H_H4R1_CANDIDATE_COUNT_EXPECTED_13")

    reviewed_rows = h4r_packet.get("reviewed_rows")
    if not isinstance(reviewed_rows, list) or len(reviewed_rows) != 13:
        raise Psi0hH4R1BoundedHumanReviewError("PSI0H_H4R1_REVIEWED_ROWS_INVALID")

    sheet_rows = [_build_review_sheet_row(row) for row in reviewed_rows]
    sheet_rows.sort(key=lambda r: r["continuity_candidate_id"])

    ids = [r["continuity_candidate_id"] for r in sheet_rows]
    if len(set(ids)) != 13:
        raise Psi0hH4R1BoundedHumanReviewError("PSI0H_H4R1_DUPLICATE_CANDIDATE_ID")

    payload = {
        "schema_version": SCHEMA_VERSION,
        "milestone": MILESTONE,
        "status": "READY_FOR_HUMAN_REVIEW",
        "verdict": "PSI0H_H4R1_REVIEW_MATERIAL_PREPARED",
        "candidate_count": len(sheet_rows),
        "unique_operation_count": h4r_packet.get("unique_operation_count"),
        "review_sheet": sheet_rows,
        "pending_human_decisions": len(sheet_rows),
        "recorded_human_decisions": 0,
        "allowed_human_review_options": list(HUMAN_REVIEW_OPTIONS),
        "disallowed_terms": [
            "PROPOSED",
            "SUPPORTED",
            "SAME_OPERATION",
            "OPERATIONAL_FAMILY_MEMBERSHIP",
            "SAME_HUMAN",
            "MONITORING_WATCHLIST_MEMBERSHIP",
            "RANKING",
            "SCORING",
            "POLICY_OR_TRADING_AUTHORITY",
            "PRODUCTION_ACTIVATION",
        ],
        "scope": {
            "generates_new_candidates": False,
            "widens_operation_corpus": False,
            "performs_historical_backfill": False,
            "makes_provider_or_rpc_calls": False,
            "auto_selects_human_disposition": False,
            "operation_family_membership_authority": False,
            "same_human_or_operator_authority": False,
            "monitoring_or_watchlist_authority": False,
            "ranking_or_scoring_authority": False,
            "policy_or_trading_authority": False,
            "production_activation_authority": False,
        },
        "provenance": {
            "h4r_packet_digest": packet_digest,
            "h4r_index_digest": index_digest,
            "h4r_packet_path": "docs/audits/psi0h_h4r_historical_continuity_review_packet.json",
            "h4r_index_path": "docs/audits/psi0h_h4r_historical_continuity_review_index.json",
        },
    }
    payload["artifact_digest"] = _digest(payload)
    return payload


def verify_bounded_human_review(record: Mapping[str, Any]) -> bool:
    if not isinstance(record, Mapping):
        raise Psi0hH4R1BoundedHumanReviewError("PSI0H_H4R1_RECORD_INVALID")
    required = ("artifact_digest", "schema_version", "status", "review_sheet", "candidate_count")
    if not all(k in record for k in required):
        raise Psi0hH4R1BoundedHumanReviewError("PSI0H_H4R1_RECORD_INVALID")
    artifact = str(record.get("artifact_digest") or "")
    if len(artifact) != 64 or any(ch not in "0123456789abcdef" for ch in artifact):
        raise Psi0hH4R1BoundedHumanReviewError("PSI0H_H4R1_RECORD_DIGEST_INVALID")
    replay = dict(record)
    replay.pop("artifact_digest")
    if _digest(replay) != artifact:
        raise Psi0hH4R1BoundedHumanReviewError("PSI0H_H4R1_RECORD_DIGEST_MISMATCH")
    rows = record["review_sheet"]
    if not isinstance(rows, list) or len(rows) != record["candidate_count"]:
        raise Psi0hH4R1BoundedHumanReviewError("PSI0H_H4R1_RECORD_ROWS_INVALID")
    if len(rows) == 13 and len({r["continuity_candidate_id"] for r in rows}) != 13:
        raise Psi0hH4R1BoundedHumanReviewError("PSI0H_H4R1_DUPLICATE_CANDIDATES")
    for row in rows:
        if row.get("human_disposition") not in (PENDING_DISPOSITION,) + HUMAN_REVIEW_OPTIONS:
            raise Psi0hH4R1BoundedHumanReviewError("PSI0H_H4R1_INVALID_DISPOSITION")
    return True
