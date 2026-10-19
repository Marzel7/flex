"""Pure PSI0G-D2 human disposition binding and F5/F9 compatibility check."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Mapping

from .operational_family_rematerialization import AUTHORITY_KEYS
from .operational_family_retention_bundle import (
    OperationalFamilyRetentionBundleError,
    build_fixture_operational_family_retention_bundle,
    build_operational_family_retention_bundle_contract,
    replay_fixture_operational_family_retention_bundle,
)


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def digest(value: object) -> str:
    return sha256(canonical(value).rstrip(b"\n")).hexdigest()


def _bind(projection: Mapping[str, Any], *, fixture_only: bool) -> dict[str, Any]:
    candidate = dict(projection["candidate"])
    identity = {
        "candidate_id": candidate["candidate_id"], "input_digest": candidate["input_digest"],
        "population": sorted(candidate["population"]),
        "supporting_evidence_ids": sorted(candidate["supporting_evidence_ids"]),
        "supporting_primitive_ids": sorted(candidate["supporting_primitive_ids"]),
        "supporting_behaviour_observation_ids": sorted(candidate["supporting_behaviour_observation_ids"]),
        "supporting_topology_revision_ids": sorted(candidate["supporting_topology_revision_ids"]),
        "quality_state": candidate["quality_state"],
        "missing_evidence": sorted(candidate["missing_evidence"]),
        "contradictory_evidence": sorted(candidate["contradictory_evidence"]),
        "lifecycle": candidate["lifecycle"],
    }
    authority = {key: False for key in AUTHORITY_KEYS}
    disposition = {
        "candidate_id": candidate["candidate_id"],
        "group_id": digest(["PSI0G_OPERATION_FAMILY_CANDIDATE", candidate["candidate_id"]]),
        "operation_ids": list(candidate["population"]), "nomination_state": "PROPOSED",
        "supporting_identity_digest": digest(identity), "authority": authority,
    }
    disposition["review_id"] = digest(disposition)
    values = {
        "cohort": projection["cohort"], "evaluations": projection["evaluations"],
        "runtime": projection["runtime"], "candidates": [candidate],
        "dispositions": [disposition], "vocabulary": projection["vocabulary"],
    }
    contract = build_operational_family_retention_bundle_contract()
    try:
        bundle = build_fixture_operational_family_retention_bundle(contract, **values)
        replay = replay_fixture_operational_family_retention_bundle(contract, bundle.files)
        compatibility = {
            "f9_contract_digest": contract.contract_digest,
            "fixture_compatibility_bundle_digest": bundle.bundle_digest,
            "fixture_compatibility_source_digest": replay.source_digest,
            "structurally_accepted": True, "blocker": None,
            "real_provenance_retained": False, "f13_publication_authorized": False,
            "fixture_only": fixture_only,
        }
    except OperationalFamilyRetentionBundleError as exc:
        compatibility = {
            "f9_contract_digest": contract.contract_digest,
            "fixture_compatibility_bundle_digest": None,
            "fixture_compatibility_source_digest": None,
            "structurally_accepted": False, "blocker": str(exc),
            "real_provenance_retained": False, "f13_publication_authorized": False,
            "fixture_only": fixture_only,
        }
    return {"values": values, "compatibility": compatibility}


def assess_fixture_structural_compatibility(projection: Mapping[str, Any]) -> dict[str, Any]:
    """Exercise frozen F5/F9 schemas without granting or retaining real authority."""
    return _bind(projection, fixture_only=True)["compatibility"]


def bind_proposed_disposition(projection: Mapping[str, Any]) -> dict[str, Any]:
    if projection.get("disposition") is not None or any(projection.get("authority", {}).values()):
        raise ValueError("PSI0G_D2_PROJECTION_AUTHORITY_DRIFT")
    candidate = dict(projection["candidate"])
    if (candidate.get("candidate_id") != "95fba7d16194a1b2c03970910b5c737c70da669988fb2c317321318c41814505" or
            candidate.get("population") != ["watchtower", "three_sw2"] or
            candidate.get("quality_state") != "INCOMPLETE" or
            candidate.get("contradictory_evidence") != [] or
            len(candidate.get("missing_evidence", ())) != 14):
        raise ValueError("PSI0G_D2_CANDIDATE_IDENTITY_DRIFT")
    bound = _bind(projection, fixture_only=False)
    disposition = bound["values"]["dispositions"][0]
    return {
        "values": bound["values"],
        "review_metadata": [{
            "review_id": disposition["review_id"], "reviewer_class": "HUMAN_REVIEW",
            "reason_codes": ["EVIDENCE_INCOMPLETE", "RECURRING_BEHAVIOUR"],
            "reviewed_sequence": 0,
        }],
        "compatibility": bound["compatibility"],
    }
