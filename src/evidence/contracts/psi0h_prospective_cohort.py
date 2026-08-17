"""PSI0H-B bounded observation-only cohort eligibility and freezing contract."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "psi0h-b.prospective-observation-cohort.v1"
MAX_COHORT = 20
AUTHORITY = {
    "comparison": False, "candidate_generation": False, "candidate_disposition": False,
    "supported": False, "same_operation": False, "same_human": False,
    "alerting": False, "monitoring": False, "consumer": False, "policy": False,
    "ranking": False, "trading": False, "integration": False, "deployment": False,
    "activation": False,
}


class Psi0hProspectiveCohortError(RuntimeError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def _digest(value: object) -> str:
    return sha256(_canonical(value).rstrip(b"\n")).hexdigest()


def freeze_prospective_observation_cohort(
    *, cutoff: int, baseline_observation_ids: Sequence[str],
    baseline_evidence_ids: Sequence[str], baseline_primitive_ids: Sequence[str],
    primitive_rows: Sequence[Mapping[str, Any]], maximum: int = MAX_COHORT,
) -> dict[str, Any]:
    if not isinstance(cutoff, int) or cutoff < 0 or not isinstance(maximum, int) or not 1 <= maximum <= MAX_COHORT:
        raise Psi0hProspectiveCohortError("PSI0H_B_BOUND_INVALID")
    baseline_observations = set(baseline_observation_ids)
    baseline_evidence = set(baseline_evidence_ids)
    baseline_primitives = set(baseline_primitive_ids)
    if any(not isinstance(value, str) or not value for group in (
            baseline_observations, baseline_evidence, baseline_primitives) for value in group):
        raise Psi0hProspectiveCohortError("PSI0H_B_BASELINE_INVALID")
    eligible = []
    rejection_counts = {
        "WINDOW_NOT_STRICTLY_POST_CUTOFF": 0, "PRIMITIVE_IN_BASELINE": 0,
        "EVIDENCE_IN_BASELINE": 0, "EVIDENCE_NOT_STRICTLY_POST_CUTOFF": 0,
        "EVIDENCE_ABSENT": 0,
    }
    seen = set()
    for value in primitive_rows:
        if not isinstance(value, Mapping):
            raise Psi0hProspectiveCohortError("PSI0H_B_PRIMITIVE_INVALID")
        row = dict(value)
        primitive_id = row.get("primitive_id")
        window = row.get("observation_window")
        evidence = row.get("evidence")
        if (not isinstance(primitive_id, str) or not primitive_id or primitive_id in seen or
                not isinstance(window, Mapping) or not isinstance(window.get("start"), int) or
                not isinstance(window.get("end"), int) or window["start"] > window["end"] or
                not isinstance(evidence, (list, tuple))):
            raise Psi0hProspectiveCohortError("PSI0H_B_PRIMITIVE_INVALID")
        seen.add(primitive_id)
        reasons = []
        if window["start"] <= cutoff:
            reasons.append("WINDOW_NOT_STRICTLY_POST_CUTOFF")
        if primitive_id in baseline_primitives:
            reasons.append("PRIMITIVE_IN_BASELINE")
        if not evidence:
            reasons.append("EVIDENCE_ABSENT")
        evidence_ids = []
        for item in evidence:
            if (not isinstance(item, Mapping) or not isinstance(item.get("evidence_id"), str) or
                    not isinstance(item.get("observed_at"), int)):
                raise Psi0hProspectiveCohortError("PSI0H_B_EVIDENCE_INVALID")
            evidence_ids.append(item["evidence_id"])
            if item["evidence_id"] in baseline_evidence:
                reasons.append("EVIDENCE_IN_BASELINE")
            if item["observed_at"] <= cutoff:
                reasons.append("EVIDENCE_NOT_STRICTLY_POST_CUTOFF")
        if len(evidence_ids) != len(set(evidence_ids)):
            raise Psi0hProspectiveCohortError("PSI0H_B_EVIDENCE_INVALID")
        reasons = sorted(set(reasons))
        if reasons:
            for reason in reasons:
                rejection_counts[reason] += 1
            continue
        identity = {
            "primitive_id": primitive_id, "primitive_type": row.get("primitive_type"),
            "observation_window": dict(window), "evidence_ids": sorted(evidence_ids),
            "generated_at": row.get("generated_at"),
        }
        eligible.append({**identity, "observation_unit_id": _digest(identity)})
    eligible.sort(key=lambda row: (
        row["observation_window"]["start"], row["observation_window"]["end"], row["primitive_id"]
    ))
    selected = eligible[:maximum]
    cohort_identity = {
        "cutoff": cutoff, "maximum": maximum,
        "selected_observation_unit_ids": [row["observation_unit_id"] for row in selected],
    }
    result = {
        "schema_version": SCHEMA_VERSION,
        "status": "PASS" if selected else "HOLD",
        "cutoff": cutoff, "maximum": maximum,
        "primitive_input_count": len(primitive_rows), "eligible_count": len(eligible),
        "selected_count": len(selected), "selected": selected,
        "rejection_counts": rejection_counts,
        "cohort_digest": _digest(cohort_identity),
        "comparison_performed": False, "shadow_only": True,
        "authority": dict(AUTHORITY),
    }
    result["replay_digest"] = _digest(result)
    return result
