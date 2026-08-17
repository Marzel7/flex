"""PSI0H-D isolated prospective evidence-to-primitive cohort contract."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "psi0h-d.prospective-operational-derivation.v1"
MAX_ENVELOPES = 100
MAX_PRIMITIVES = 20
AUTHORITY = {
    "comparison": False, "candidate_generation": False, "candidate_disposition": False,
    "supported": False, "same_operation": False, "same_human": False,
    "alerting": False, "monitoring": False, "consumer": False, "policy": False,
    "ranking": False, "trading": False, "integration": False, "deployment": False,
    "activation": False,
}


class Psi0hProspectiveDerivationError(RuntimeError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _digest(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


def _is_digest(value: object) -> bool:
    return (isinstance(value, str) and len(value) == 64 and
            all(character in "0123456789abcdef" for character in value))


def qualify_prospective_derivation(
    *, cutoff: int, interval_start: int, interval_end: int,
    envelopes: Sequence[Mapping[str, Any]], evidence_rows: Sequence[Mapping[str, Any]],
    primitive_rows: Sequence[Mapping[str, Any]], baseline_evidence_ids: Sequence[str] = (),
    baseline_primitive_ids: Sequence[str] = (), maximum_primitives: int = MAX_PRIMITIVES,
) -> dict[str, Any]:
    """Validate and freeze a bounded isolated event-time derivation lineage.

    Inputs are already captured/normalized/derived representations. This function neither
    acquires nor derives evidence and therefore cannot manufacture missing production facts.
    """
    if (not all(isinstance(v, int) for v in (cutoff, interval_start, interval_end)) or
            not cutoff < interval_start <= interval_end or
            not isinstance(maximum_primitives, int) or
            not 1 <= maximum_primitives <= MAX_PRIMITIVES or len(envelopes) > MAX_ENVELOPES or
            len(primitive_rows) > maximum_primitives):
        raise Psi0hProspectiveDerivationError("PSI0H_D_BOUND_INVALID")

    baseline_evidence = set(baseline_evidence_ids)
    baseline_primitives = set(baseline_primitive_ids)
    if any(not isinstance(value, str) or not value
           for value in baseline_evidence | baseline_primitives):
        raise Psi0hProspectiveDerivationError("PSI0H_D_BASELINE_INVALID")
    envelope_index: dict[str, dict[str, Any]] = {}
    for source in envelopes:
        row = dict(source)
        envelope_id = row.get("envelope_id")
        event_time = row.get("event_time")
        acquired_at = row.get("acquired_at")
        artifact_digest = row.get("artifact_digest")
        if (not isinstance(envelope_id, str) or not envelope_id or envelope_id in envelope_index or
                not isinstance(event_time, int) or not interval_start <= event_time <= interval_end or
                not isinstance(acquired_at, int) or acquired_at < event_time or
                not _is_digest(artifact_digest)):
            raise Psi0hProspectiveDerivationError("PSI0H_D_ENVELOPE_INVALID")
        envelope_index[envelope_id] = {
            "envelope_id": envelope_id, "event_time": event_time,
            "acquired_at": acquired_at, "artifact_digest": artifact_digest,
        }

    evidence_index: dict[str, dict[str, Any]] = {}
    for source in evidence_rows:
        row = dict(source)
        evidence_id = row.get("evidence_id")
        envelope_id = row.get("envelope_id")
        fact_family = row.get("fact_family")
        event_time = row.get("event_time")
        payload_digest = row.get("payload_digest")
        if (not isinstance(evidence_id, str) or not evidence_id or evidence_id in evidence_index or
                evidence_id in baseline_evidence or envelope_id not in envelope_index or
                not isinstance(fact_family, str) or not fact_family or
                not isinstance(event_time, int) or event_time != envelope_index[envelope_id]["event_time"] or
                not _is_digest(payload_digest)):
            raise Psi0hProspectiveDerivationError("PSI0H_D_EVIDENCE_INVALID")
        evidence_index[evidence_id] = {
            "evidence_id": evidence_id, "envelope_id": envelope_id,
            "fact_family": fact_family, "event_time": event_time,
            "payload_digest": payload_digest,
        }

    selected = []
    seen_primitives = set()
    for source in primitive_rows:
        row = dict(source)
        primitive_id = row.get("primitive_id")
        primitive_type = row.get("primitive_type")
        start, end = row.get("window_start"), row.get("window_end")
        evidence_ids = row.get("evidence_ids")
        generated_at = row.get("generated_at")
        missing_inputs = row.get("missing_inputs")
        if (not isinstance(primitive_id, str) or not primitive_id or primitive_id in seen_primitives or
                primitive_id in baseline_primitives or not isinstance(primitive_type, str) or
                not primitive_type or not isinstance(start, int) or not isinstance(end, int) or
                not interval_start <= start <= end <= interval_end or
                not isinstance(generated_at, int) or generated_at < end or
                not isinstance(evidence_ids, (list, tuple)) or not evidence_ids or
                len(evidence_ids) != len(set(evidence_ids)) or
                not isinstance(missing_inputs, (list, tuple)) or
                any(not isinstance(value, str) or not value for value in missing_inputs)):
            raise Psi0hProspectiveDerivationError("PSI0H_D_PRIMITIVE_INVALID")
        if any(value not in evidence_index for value in evidence_ids):
            raise Psi0hProspectiveDerivationError("PSI0H_D_LINEAGE_INCOMPLETE")
        event_times = [evidence_index[value]["event_time"] for value in evidence_ids]
        if start != min(event_times) or end != max(event_times):
            raise Psi0hProspectiveDerivationError("PSI0H_D_EVENT_TIME_DRIFT")
        seen_primitives.add(primitive_id)
        identity = {
            "primitive_id": primitive_id, "primitive_type": primitive_type,
            "window_start": start, "window_end": end, "generated_at": generated_at,
            "evidence_ids": sorted(evidence_ids), "missing_inputs": sorted(missing_inputs),
        }
        selected.append({**identity, "observation_unit_id": _digest(identity)})

    used_evidence = {value for row in selected for value in row["evidence_ids"]}
    if set(evidence_index) != used_evidence:
        raise Psi0hProspectiveDerivationError("PSI0H_D_UNBOUND_EVIDENCE")
    used_envelopes = {evidence_index[value]["envelope_id"] for value in used_evidence}
    if set(envelope_index) != used_envelopes:
        raise Psi0hProspectiveDerivationError("PSI0H_D_UNBOUND_ENVELOPE")

    selected.sort(key=lambda row: (row["window_start"], row["window_end"], row["primitive_id"]))
    lineage = {
        "cutoff": cutoff, "interval_start": interval_start, "interval_end": interval_end,
        "envelopes": [envelope_index[key] for key in sorted(envelope_index)],
        "evidence": [evidence_index[key] for key in sorted(evidence_index)],
        "primitives": selected,
    }
    result = {
        "schema_version": SCHEMA_VERSION, "status": "PASS" if selected else "HOLD",
        "cutoff": cutoff, "interval_start": interval_start, "interval_end": interval_end,
        "envelope_count": len(envelope_index), "evidence_count": len(evidence_index),
        "primitive_count": len(selected), "selected": selected,
        "lineage_digest": _digest(lineage), "shadow_only": True,
        "comparison_performed": False, "authority": dict(AUTHORITY),
    }
    result["replay_digest"] = _digest(result)
    return result
