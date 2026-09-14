"""Trusted retained-evidence input resolution for manual operation validation.

This module deliberately accepts a candidate id only.  It never accepts a
browser-provided detector, evidence family, member list, or promotion field.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from src.ops.potential_operations import MEMBERSHIP


VALIDATION_INPUT_RESOLVER_VERSION = "POTENTIAL_OPERATION_VALIDATION_INPUT_RESOLVER_V1"
V1_DEFAULT_ACQUISITION_POLICY = "RETAINED_EVIDENCE_ONLY"


class ValidationInputUnresolved(ValueError):
    code = "VALIDATION_INPUT_UNRESOLVED"


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def resolve_validation_input(candidate_source_id: str) -> dict[str, Any]:
    """Resolve immutable, server-side V1 input for one Potential Operations id.

    The P3R membership artifact identifies the detector and candidate members.
    It does not by itself contain an independent attribution family, so the
    returned family set is intentionally empty.  The V1 workflow consequently
    returns ADDITIONAL_EVIDENCE_REQUIRED without acquiring new evidence.
    """
    try:
        payload = json.loads(Path(MEMBERSHIP).read_text())
        matches = [row for row in payload["families"] if row.get("candidate_id") == candidate_source_id]
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        raise ValidationInputUnresolved("retained candidate provenance unavailable") from exc
    if len(matches) != 1:
        raise ValidationInputUnresolved("candidate provenance is not uniquely resolvable")
    row = matches[0]
    detector_contract = (row.get("fingerprint") or {}).get("contract")
    mints = row.get("mints")
    membership_digest = row.get("membership_digest")
    if not detector_contract or not isinstance(mints, list) or not mints or not membership_digest:
        raise ValidationInputUnresolved("candidate provenance is incomplete")
    snapshot = {
        "candidate_source_id": candidate_source_id,
        "membership_digest": membership_digest,
        "detector_contract": detector_contract,
        "member_mints": sorted(mints),
    }
    snapshot_digest = _digest(snapshot)
    return {
        "resolver_version": VALIDATION_INPUT_RESOLVER_VERSION,
        "candidate_source_id": candidate_source_id,
        "candidate_source_type": "POTENTIAL_OPERATION",
        "detector_contract": detector_contract,
        "detector_evidence_refs": [f"{MEMBERSHIP}:{membership_digest}"],
        "candidate_member_refs": sorted(mints),
        "candidate_snapshot_ref": f"sha256:{snapshot_digest}",
        "proposed_operation_id": f"manual-v1-{snapshot_digest[:24]}",
        "proposed_operation_label": f"Potential operation {candidate_source_id}",
        "evidence_families": [],
        "common_infrastructure_exclusions": [],
        "completeness_state": "ADDITIONAL_EVIDENCE_REQUIRED",
        "acquisition_policy": V1_DEFAULT_ACQUISITION_POLICY,
        "client_supplied_evidence_accepted": False,
    }
