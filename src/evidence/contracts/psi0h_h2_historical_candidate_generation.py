"""PSI0H-H2 historical candidate-generation contract."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from math import comb
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "psi0h-h2.historical-candidate-generation.v1"
MAX_OPERATIONS = 2000
MAX_CANDIDATES = 250000
AUTHORITY = {
    "comparison": False,
    "candidate_generation": False,
    "candidate_disposition": False,
    "supported": False,
    "same_operation": False,
    "same_human": False,
    "alerting": False,
    "monitoring": False,
    "consumer": False,
    "policy": False,
    "ranking": False,
    "trading": False,
    "integration": False,
    "deployment": False,
    "activation": False,
}


class Psi0hH2HistoricalCandidateGenerationError(RuntimeError):
    pass


RELATIONSHIP_SHARED_BEHAVIOUR = "shared_behaviour"
RELATIONSHIP_CONTINUITY = "evidence_of_continuity"
RELATIONSHIP_FAMILY = "possible_operational_family"
RELATIONSHIP_INSUFFICIENT = "insufficient_evidence"


@dataclass(frozen=True)
class _OperationRecord:
    operation_id: str
    source_path: str
    observation_start: int
    observation_end: int
    evidence_count: int
    primitive_count: int
    candidate_count: int
    subject_count: int
    behaviour_observation_ids: tuple[str, ...]
    discovery_snapshot_digest: str
    evidence_ids_digest: str
    contract_id: str
    contract_digest: str


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _digest(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


def _as_list(values: object) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise Psi0hH2HistoricalCandidateGenerationError("PSI0H_H2_OPERATION_LIST_INVALID")
    out = []
    for value in values:
        if not isinstance(value, str) or not value:
            raise Psi0hH2HistoricalCandidateGenerationError("PSI0H_H2_OPERATION_LIST_INVALID")
        out.append(value)
    return out


def _is_int(value: object, *, minimum: int = 0) -> bool:
    return isinstance(value, int) and value >= minimum


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_manifest(path: str | Path) -> tuple[str, list[dict[str, Any]]]:
    manifest_path = Path(path)
    if not manifest_path.exists():
        raise Psi0hH2HistoricalCandidateGenerationError(f"PSI0H_H2_MANIFEST_MISSING:{path}")
    payload = _read_json(manifest_path)
    if not isinstance(payload, Mapping):
        raise Psi0hH2HistoricalCandidateGenerationError("PSI0H_H2_MANIFEST_INVALID")
    if payload.get("schema_version") == "1.0.0" and payload.get("milestone") == "PSI0G-B":
        pass
    elif payload.get("schema_version") == "psi0h-h4.historical-operation-census.v1" and payload.get("milestone") == "PSI0H-H4":
        pass
    else:
        raise Psi0hH2HistoricalCandidateGenerationError("PSI0H_H2_MANIFEST_INVALID")
    operations = payload.get("operations") if payload.get("schema_version") == "1.0.0" else payload.get("discovered_populations")
    if not isinstance(operations, list) or not operations:
        raise Psi0hH2HistoricalCandidateGenerationError("PSI0H_H2_MANIFEST_NO_OPERATION_CONTEXT")
    return manifest_path.as_posix(), operations


def _parse_operation_rows(operations: Sequence[Mapping[str, Any]], eligible_ids: set[str]) -> list[_OperationRecord]:
    parsed: list[_OperationRecord] = []
    seen: set[str] = set()
    for row in operations:
        if not isinstance(row, Mapping):
            raise Psi0hH2HistoricalCandidateGenerationError("PSI0H_H2_OPERATION_INVALID")

        operation_id = str(row.get("operation_key") or row.get("operation_id") or "").strip()
        if not operation_id:
            raise Psi0hH2HistoricalCandidateGenerationError("PSI0H_H2_OPERATION_ID_INVALID")
        if operation_id not in eligible_ids:
            continue
        if operation_id in seen:
            raise Psi0hH2HistoricalCandidateGenerationError(f"PSI0H_H2_OPERATION_DUPLICATE:{operation_id}")
        seen.add(operation_id)

        source = row.get("source")
        source_path = row.get("source_path")
        if isinstance(source, Mapping):
            source_path = source.get("path") or source_path
        if not isinstance(source_path, str) or not source_path:
            raise Psi0hH2HistoricalCandidateGenerationError(f"PSI0H_H2_OPERATION_SOURCE_PATH_INVALID:{operation_id}")

        behaviour_ids = _as_list(row.get("behaviour_observation_ids") or row.get("evidence_refs") or [])
        if not behaviour_ids:
            # allowed to be empty; H2 should capture insufficient evidence
            pass

        start = row.get("observation_start", row.get("first_observed_utc", 1))
        end = row.get("observation_end", row.get("last_observed_utc", 1))
        if start is None:
            start = 1
        if end is None:
            end = 1
        if start < 1:
            start = 1
        if end < 1:
            end = 1
        if not (_is_int(start, minimum=1) and _is_int(end, minimum=1) and start <= end):
            raise Psi0hH2HistoricalCandidateGenerationError(f"PSI0H_H2_OPERATION_TIME_WINDOW_INVALID:{operation_id}")

        evidence_count = row.get("evidence_count")
        if evidence_count is None:
            evidence_count = 0
        primitive_count = row.get("primitive_count")
        if primitive_count is None:
            primitive_count = row.get("primitive_reference_count")
        if primitive_count is None and isinstance(row.get("primitive_refs"), list):
            primitive_count = len(row.get("primitive_refs"))
        candidate_count = row.get("candidate_count", row.get("subject_count", 0))
        subject_count = row.get("subject_count", 0)
        for field_name, value in (
            ("evidence_count", evidence_count),
            ("primitive_count", primitive_count),
            ("candidate_count", candidate_count),
            ("subject_count", subject_count),
        ):
            if not _is_int(value, minimum=0):
                raise Psi0hH2HistoricalCandidateGenerationError(f"PSI0H_H2_OPERATION_FIELD_INVALID:{operation_id}:{field_name}")
        if evidence_count == 0 and primitive_count == 0 and candidate_count == 0:
            raise Psi0hH2HistoricalCandidateGenerationError(f"PSI0H_H2_OPERATION_EMPTY:{operation_id}")

        snapshot_digest = row.get("discovery_snapshot_digest") or row.get("evidence_ids_digest") or ""
        evidence_ids_digest = row.get("evidence_ids_digest") or row.get("evidence_ids_digest") or ""
        contract_id = row.get("contract_id")
        contract_digest = row.get("contract_digest") or row.get("primitive_ids_digest") or ""
        if not contract_id:
            contract_id = f"PSI0H-H2:{operation_id}"
        if not contract_digest:
            contract_digest = _digest({
                "operation_id": operation_id,
                "discovery_snapshot_digest": snapshot_digest,
                "evidence_ids_digest": evidence_ids_digest,
                "subject_count": subject_count,
            })
        if not all(isinstance(value, str) and value for value in (snapshot_digest, evidence_ids_digest, contract_id, contract_digest)):
            raise Psi0hH2HistoricalCandidateGenerationError(f"PSI0H_H2_OPERATION_DIGEST_INVALID:{operation_id}")

        parsed.append(
            _OperationRecord(
                operation_id=operation_id,
                source_path=str(source_path),
                observation_start=int(start),
                observation_end=int(end),
                evidence_count=int(evidence_count),
                primitive_count=int(primitive_count),
                candidate_count=int(candidate_count),
                subject_count=int(subject_count),
                behaviour_observation_ids=tuple(sorted(set(behaviour_ids))),
                discovery_snapshot_digest=snapshot_digest,
                evidence_ids_digest=evidence_ids_digest,
                contract_id=contract_id,
                contract_digest=contract_digest,
            )
        )
    return parsed


def _time_overlap(start_a: int, end_a: int, start_b: int, end_b: int) -> int:
    start = max(start_a, start_b)
    end = min(end_a, end_b)
    if end < start:
        return 0
    return end - start + 1


def _classify_pair(a: _OperationRecord, b: _OperationRecord) -> tuple[str, list[str], list[str]]:
    shared = sorted(set(a.behaviour_observation_ids).intersection(set(b.behaviour_observation_ids)))
    shared_count = len(shared)

    continuity = []
    shared_behaviour = []
    possible_family = []
    insufficient = []
    shared_evidence = []

    overlap = _time_overlap(a.observation_start, a.observation_end, b.observation_start, b.observation_end)
    if shared_count:
        shared_behaviour.append("shared_behaviour_observations")
        shared_evidence.append(f"shared_behaviour_observation_count:{shared_count}")
        if overlap > 0 and a.subject_count > 0 and b.subject_count > 0:
            continuity.append("temporal_overlap_with_behaviour_overlap")
        if overlap > 0 and (a.subject_count > 100 and b.subject_count > 100):
            possible_family.append("behaviour_overlap_plus_volume")

    if not shared_count:
        insufficient.append("no_shared_behaviour_observations")

    if continuity:
        relation = RELATIONSHIP_CONTINUITY
        evidence = continuity + shared_evidence
    elif shared_behaviour:
        if possible_family:
            relation = RELATIONSHIP_FAMILY
            evidence = shared_evidence + possible_family
        else:
            relation = RELATIONSHIP_SHARED_BEHAVIOUR
            evidence = shared_evidence
    else:
        relation = RELATIONSHIP_INSUFFICIENT
        evidence = insufficient

    return relation, evidence, shared


def qualify_historical_candidate_generation(
    *, h1_artifact: Mapping[str, Any], manifest_path: str | None = None,
    maximum_candidates: int = 100,
) -> dict[str, Any]:
    if not isinstance(h1_artifact, Mapping):
        raise Psi0hH2HistoricalCandidateGenerationError("PSI0H_H2_H1_ARTIFACT_INVALID")
    if maximum_candidates is None:
        maximum_candidates = 0
    if not isinstance(maximum_candidates, int) or maximum_candidates < 0:
        raise Psi0hH2HistoricalCandidateGenerationError("PSI0H_H2_MAX_CANDIDATES_INVALID")

    for key in ("eligible_operations",):
        if key not in h1_artifact:
            raise Psi0hH2HistoricalCandidateGenerationError("PSI0H_H2_H1_ARTIFACT_MISSING")

    eligible = h1_artifact.get("eligible_operations")
    if not isinstance(eligible, list):
        raise Psi0hH2HistoricalCandidateGenerationError("PSI0H_H2_H1_ELIGIBLE_INVALID")
    if not eligible:
        raise Psi0hH2HistoricalCandidateGenerationError("PSI0H_H2_H1_NO_ELIGIBLE_OPERATIONS")
    if len(eligible) > MAX_OPERATIONS:
        raise Psi0hH2HistoricalCandidateGenerationError("PSI0H_H2_H1_EXCESS_OPERATIONS")

    eligible_ids = set()
    for row in eligible:
        if not isinstance(row, Mapping):
            raise Psi0hH2HistoricalCandidateGenerationError("PSI0H_H2_H1_ELIGIBLE_ROW_INVALID")
        operation_id = str(row.get("operation_id") or "").strip()
        if not operation_id:
            raise Psi0hH2HistoricalCandidateGenerationError("PSI0H_H2_H1_ELIGIBLE_ID_INVALID")
        if operation_id in eligible_ids:
            raise Psi0hH2HistoricalCandidateGenerationError(f"PSI0H_H2_H1_ELIGIBLE_DUPLICATE:{operation_id}")
        eligible_ids.add(operation_id)

    if not manifest_path:
        manifest_path = h1_artifact.get("manifest_source_path")
    if not manifest_path:
        raise Psi0hH2HistoricalCandidateGenerationError("PSI0H_H2_MANIFEST_PATH_MISSING")

    _, manifest_operations = _load_manifest(manifest_path)
    parsed = _parse_operation_rows(manifest_operations, eligible_ids=eligible_ids)
    if len(parsed) != len(eligible_ids):
        missing = sorted(eligible_ids - {row.operation_id for row in parsed})
        raise Psi0hH2HistoricalCandidateGenerationError(f"PSI0H_H2_ELIGIBLE_NOT_FOUND_IN_MANIFEST:{','.join(missing)}")

    parsed.sort(key=lambda item: item.operation_id)
    # preserve stable input-only mapping for replay
    operation_by_id = {row.operation_id: row for row in parsed}
    # Candidate generation is pairwise only; no synthetic clustering.
    pair_candidates = []
    pair_ids = sorted(operation_by_id)
    for i, left_id in enumerate(pair_ids):
        for right_id in pair_ids[i + 1 :]:
            left = operation_by_id[left_id]
            right = operation_by_id[right_id]
            relation, evidence, shared = _classify_pair(left, right)
            candidate_id = _digest({
                "left": left_id,
                "right": right_id,
                "relation": relation,
                "shared": shared,
                "evidence": evidence,
            })
            candidate = {
                "continuity_candidate_id": candidate_id,
                "operation_ids": [left_id, right_id],
                "relationship": relation,
                "shared_behaviour_observation_count": len(shared),
                "shared_behaviour_observation_ids": shared,
                "continuity_evidence": evidence,
                "identity_guarded": True,
                "same_operation_claim": False,
                "same_human_claim": False,
                "source_scope": {
                    "same_operation": False,
                    "same_human": False,
                },
                "lineage": {
                    "evidence_ids_digests": [left.evidence_ids_digest, right.evidence_ids_digest],
                    "discovery_snapshot_digests": [left.discovery_snapshot_digest, right.discovery_snapshot_digest],
                    "contract_ids": [left.contract_id, right.contract_id],
                    "contract_digests": [left.contract_digest, right.contract_digest],
                },
            }
            if relation == RELATIONSHIP_INSUFFICIENT:
                candidate["missing_evidence_reasons"] = ["NO_SHARED_BEHAVIOUR_OBS"]
            pair_candidates.append(candidate)

    theoretical_pairs = comb(len(pair_ids), 2) if len(pair_ids) >= 2 else 0
    truncated = False
    if maximum_candidates and len(pair_candidates) > maximum_candidates:
        pair_candidates = pair_candidates[:maximum_candidates]
        truncated = True

    supported_candidates = [row for row in pair_candidates if row["relationship"] == RELATIONSHIP_CONTINUITY]
    partial_candidates = [row for row in pair_candidates if row["relationship"] in (RELATIONSHIP_SHARED_BEHAVIOUR, RELATIONSHIP_FAMILY)]
    insufficient_candidates = [row for row in pair_candidates if row["relationship"] == RELATIONSHIP_INSUFFICIENT]

    status = "PASS" if pair_candidates else "HOLD"
    result = {
        "schema_version": SCHEMA_VERSION,
        "milestone": "PSI0H-H2",
        "status": status,
        "verdict": "H2_HISTORICAL_CANDIDATE_GENERATION_PASS" if status == "PASS" else "H2_HISTORICAL_CANDIDATE_GENERATION_HOLD_EMPTY",
        "pair_count": len(pair_candidates),
        "insufficient_candidates": len(insufficient_candidates),
        "continuity_candidates": len(supported_candidates),
        "partial_candidates": len(partial_candidates),
        "candidate_rows": pair_candidates,
        "source": {
            "h1_artifact_digest": h1_artifact.get("artifact_digest"),
            "manifest_source_path": str(Path(manifest_path)),
            "manifest_digest": h1_artifact.get("manifest_digest"),
            "source_manifest_digest": h1_artifact.get("manifest_digest"),
        },
        "scope": {
            "comparison": False,
            "candidate_generation": True,
            "candidate_disposition": False,
            "provider_or_rpc_calls": 0,
            "comparison_authorized": False,
            "monitoring": False,
            "activation": False,
        },
        "required_scope": {
            "observation_only": True,
            "provider_or_rpc_calls": 0,
            "comparison": False,
            "monitoring": False,
            "activation": False,
        },
        "authority": dict(AUTHORITY),
        "relationship_classes": {
            RELATIONSHIP_CONTINUITY: len(supported_candidates),
            RELATIONSHIP_FAMILY: len([row for row in pair_candidates if row["relationship"] == RELATIONSHIP_FAMILY]),
            RELATIONSHIP_SHARED_BEHAVIOUR: len([row for row in pair_candidates if row["relationship"] == RELATIONSHIP_SHARED_BEHAVIOUR]),
            RELATIONSHIP_INSUFFICIENT: len(insufficient_candidates),
        },
        "eligible_operations": [row["operation_id"] for row in eligible],
        "blockers": [],
        "selection": {
            "input_count": len(parsed),
            "pair_limit": maximum_candidates or theoretical_pairs,
            "theoretical_pairs": theoretical_pairs,
            "evaluated_pairs": len(pair_candidates),
            "pairing_strategy": "all_ordered_pairs_sorted_ids",
            "truncated": truncated,
        },
    }
    result["artifact_digest"] = _digest(result)
    return result


def verify_historical_candidate_generation(record: Mapping[str, Any]) -> bool:
    if not isinstance(record, Mapping):
        raise Psi0hH2HistoricalCandidateGenerationError("PSI0H_H2_RECORD_INVALID")
    required = ("artifact_digest", "schema_version", "status", "candidate_rows", "pair_count", "eligible_operations")
    if not all(key in record for key in required):
        raise Psi0hH2HistoricalCandidateGenerationError("PSI0H_H2_RECORD_INVALID")
    artifact_digest = str(record["artifact_digest"])
    if len(artifact_digest) != 64 or any(ch not in "0123456789abcdef" for ch in artifact_digest):
        raise Psi0hH2HistoricalCandidateGenerationError("PSI0H_H2_RECORD_DIGEST_INVALID")

    replay = dict(record)
    replay.pop("artifact_digest")
    expected = _digest(replay)
    if expected != artifact_digest:
        raise Psi0hH2HistoricalCandidateGenerationError("PSI0H_H2_RECORD_DIGEST_MISMATCH")

    if not isinstance(record.get("candidate_rows"), list):
        raise Psi0hH2HistoricalCandidateGenerationError("PSI0H_H2_RECORD_CANDIDATES_INVALID")
    return True
