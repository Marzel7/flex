"""PSI0G-D4 fail-closed preflight for a future real-provenance retention write.

This module never writes a store and never grants disposition or downstream authority.
It proves whether an exact reviewed projection is eligible for a separately authorized
real retention publisher, without invoking PSI0F-F13's fixture-only publisher.
"""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Mapping


SCHEMA_VERSION = "psi0g-d4.real-retention-preflight.v1"
EXPECTED_PROJECTION_SCHEMA = "psi0g-d3.operation-projection.v2"
EXPECTED_MANIFEST_SCHEMA = "psi0g-d3.publication.v1"
EXPECTED_POPULATION = ["watchtower", "three_sw2"]


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def digest(value: object) -> str:
    return sha256(canonical(value).rstrip(b"\n")).hexdigest()


def projection_digest(value: object) -> str:
    return sha256(canonical(value)).hexdigest()


def assess_real_retention_preflight(
    projection: Mapping[str, Any], projection_manifest: Mapping[str, Any],
    disposition: Mapping[str, Any] | None,
) -> dict[str, Any]:
    blockers: list[str] = []
    candidate = projection.get("candidate", {})
    candidate_id = candidate.get("candidate_id")

    if projection.get("schema_version") != EXPECTED_PROJECTION_SCHEMA:
        blockers.append("D4_PROJECTION_SCHEMA_MISMATCH")
    if projection_manifest.get("schema_version") != EXPECTED_MANIFEST_SCHEMA:
        blockers.append("D4_MANIFEST_SCHEMA_MISMATCH")
    if projection_manifest.get("status") != "PASS":
        blockers.append("D4_PROJECTION_NOT_PASS")
    if projection_manifest.get("candidate_id") != candidate_id:
        blockers.append("D4_CANDIDATE_MANIFEST_IDENTITY_MISMATCH")
    if projection_manifest.get("projection_sha256") != projection_digest(projection):
        blockers.append("D4_PROJECTION_DIGEST_MISMATCH")
    if candidate.get("population") != EXPECTED_POPULATION:
        blockers.append("D4_POPULATION_DRIFT")
    if candidate.get("quality_state") != "DEGRADED":
        blockers.append("D4_QUALITY_STATE_DRIFT")
    if len(candidate.get("missing_evidence", ())) != 14:
        blockers.append("D4_MISSING_EVIDENCE_DRIFT")
    if candidate.get("contradictory_evidence") != []:
        blockers.append("D4_CONTRADICTORY_EVIDENCE_PRESENT")
    if projection.get("disposition") is not None or any(projection.get("authority", {}).values()):
        blockers.append("D4_PROJECTION_AUTHORITY_DRIFT")

    if disposition is None:
        blockers.append("D4_EXACT_HUMAN_DISPOSITION_ABSENT")
    else:
        if disposition.get("schema_version") != "psi0g-d4.human-disposition.v1":
            blockers.append("D4_DISPOSITION_SCHEMA_MISMATCH")
        if disposition.get("status") != "PASS" or disposition.get("reviewer_class") != "HUMAN_REVIEW":
            blockers.append("D4_HUMAN_REVIEW_INVALID")
        if disposition.get("candidate_id") != candidate_id:
            blockers.append("D4_DISPOSITION_CANDIDATE_MISMATCH")
        if disposition.get("candidate_input_digest") != candidate.get("input_digest"):
            blockers.append("D4_DISPOSITION_INPUT_MISMATCH")
        if disposition.get("operation_ids") != EXPECTED_POPULATION:
            blockers.append("D4_DISPOSITION_POPULATION_DRIFT")
        if disposition.get("nomination_state") != "PROPOSED":
            blockers.append("D4_DISPOSITION_NOT_PROPOSED")
        if sorted(disposition.get("unresolved_missing_evidence", ())) != sorted(candidate.get("missing_evidence", ())):
            blockers.append("D4_DISPOSITION_MISSING_EVIDENCE_DRIFT")
        if disposition.get("contradictory_evidence") != candidate.get("contradictory_evidence"):
            blockers.append("D4_DISPOSITION_CONTRADICTION_DRIFT")
        if any(disposition.get("authority", {}).values()):
            blockers.append("D4_DISPOSITION_AUTHORITY_DRIFT")
        guards = disposition.get("semantic_guards", {})
        expected_guards = {
            "operations_remain_separate": True,
            "same_operation_claim": False,
            "same_person_or_operator_claim": False,
            "missing_evidence_is_negative_evidence": False,
        }
        if guards != expected_guards:
            blockers.append("D4_SEMANTIC_GUARD_DRIFT")

    identity = {
        "projection_digest": projection_digest(projection),
        "projection_manifest_digest": digest(projection_manifest),
        "candidate_id": candidate_id,
        "disposition_digest": digest(disposition) if disposition is not None else None,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "READY" if not blockers else "HOLD",
        "identity": identity,
        "preflight_digest": digest(identity),
        "blockers": blockers,
        "fixture_f13_invoked": False,
        "store_written": False,
        "real_retention_write_authorized": False,
        "downstream_authority": {
            "supported": False, "same_operation": False, "same_human": False,
            "publication": False, "monitoring": False, "activation": False,
        },
        "next_boundary": (
            "SEPARATELY_AUTHORIZE_REAL_RETENTION_PUBLISHER_IMPLEMENTATION"
            if not blockers else "OBTAIN_EXACT_D3_HUMAN_DISPOSITION"
        ),
    }
