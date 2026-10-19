"""PSI0H-H4R bounded historical continuity-review packet contract."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "psi0h-h4r.historical-continuity-review-packet.v1"
REVIEW_OPTIONS = {
    "COMMON_PLAYBOOK_ONLY": "common_playbook_evidence_only",
    "PLAUSIBLE_OPERATIONAL_CONTINUITY": "evidence_suggests_possible_continuity",
    "PLAUSIBLE_OPERATIONAL_FAMILY": "evidence_suggests_family_level_review",
    "INSUFFICIENT_EVIDENCE": "continuity_signal_insufficient_for_review",
    "CONFLICTING_EVIDENCE": "conflicting_signals_across_bound_inputs",
}
BINDING_DIGESTS = {
    "h2": "db1febb4c282695cc3fdd63886e7e7cd95ea4ffaace9842f85aab23cc81f8325",
    "h3": "9baa117841ec4be023495f1627c96e9810dec43af270a132d71cdd923bf301f8",
    "h4": "cf18ed4c461d811c3485d9af31ce45694080f2f68017e730e59a214fecd005d7",
    "h4_projection_manifest": "32098746842272065e66e8bcb4ad192f31ae1bfd3c5153dfae6cdbc5d58a32c6",
}


class Psi0hH4RHistoricalContinuityReviewPacketError(RuntimeError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _digest(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


def _read_artifact(path: str | object) -> Mapping[str, Any]:
    if isinstance(path, Mapping):
        return path
    data = json.loads(open(path, encoding="utf-8").read())
    if not isinstance(data, Mapping):
        raise Psi0hH4RHistoricalContinuityReviewPacketError("PSI0H_H4R_ARTIFACT_INVALID")
    return data


def _row_dicts_by_id(operations: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for row in operations:
        if not isinstance(row, Mapping):
            raise Psi0hH4RHistoricalContinuityReviewPacketError("PSI0H_H4R_H4_OPERATION_INVALID")
        op_id = str(row.get("operation_id") or "").strip()
        if not op_id:
            raise Psi0hH4RHistoricalContinuityReviewPacketError("PSI0H_H4R_H4_OPERATION_ID_INVALID")
        out[op_id] = row
    return out


def _as_sorted_strings(values: Sequence[str] | None) -> list[str]:
    if not values:
        return []
    if not all(isinstance(v, str) for v in values):
        raise Psi0hH4RHistoricalContinuityReviewPacketError("PSI0H_H4R_H4_SEQUENCE_INVALID")
    return sorted(set(values))


def _extract_evidence_of_continuity_rows(
    candidate_rows: Sequence[Mapping[str, Any]],
    *,
    stage: str,
) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    seen: set[str] = set()
    for row in candidate_rows:
        if not isinstance(row, Mapping):
            raise Psi0hH4RHistoricalContinuityReviewPacketError(f"PSI0H_H4R_{stage}_ROW_INVALID")
        if row.get("relationship") != "evidence_of_continuity":
            continue
        candidate_id = str(row.get("continuity_candidate_id") or "").strip()
        if not candidate_id:
            raise Psi0hH4RHistoricalContinuityReviewPacketError(
                f"PSI0H_H4R_{stage}_CANDIDATE_ID_INVALID"
            )
        if candidate_id in seen:
            raise Psi0hH4RHistoricalContinuityReviewPacketError(f"PSI0H_H4R_{stage}_DUPLICATE_CANDIDATE_ID")
        seen.add(candidate_id)
        out[candidate_id] = row
    return out


def _classify_review_options(*, shared_count: int, continuity_specific: list[str], conflicts: list[str]) -> list[str]:
    if conflicts:
        return [REVIEW_OPTIONS["CONFLICTING_EVIDENCE"]]
    if not shared_count:
        return [REVIEW_OPTIONS["INSUFFICIENT_EVIDENCE"]]
    if len(continuity_specific) >= 2:
        return [REVIEW_OPTIONS["PLAUSIBLE_OPERATIONAL_FAMILY"]]
    return [REVIEW_OPTIONS["PLAUSIBLE_OPERATIONAL_CONTINUITY"]]


def _build_review_row(
    *,
    candidate_id: str,
    op_a: Mapping[str, Any],
    op_b: Mapping[str, Any],
    candidate_row: Mapping[str, Any],
    h4_manifest_digest: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    shared_behavior_ids = list(dict.fromkeys(candidate_row.get("shared_behaviour_observation_ids") or []))
    shared_behavior_count = int(candidate_row.get("shared_behaviour_observation_count") or 0)
    continuity_evidence = list(dict.fromkeys(candidate_row.get("continuity_evidence") or []))

    op_a_refs = set(op_a.get("evidence_refs") or [])
    op_b_refs = set(op_b.get("evidence_refs") or [])
    op_a_roles = set(op_a.get("roles") or [])
    op_b_roles = set(op_b.get("roles") or [])

    evidence_overlap = sorted(op_a_refs & op_b_refs)
    role_overlap = sorted(op_a_roles & op_b_roles)

    common_playbook: list[str] = []
    if shared_behavior_count:
        common_playbook.append(f"shared_behaviour_observations:{shared_behavior_count}")
    common_playbook.extend([f"shared_behaviour_id:{x}" for x in shared_behavior_ids])

    continuity_specific: list[str] = []
    if "temporal_overlap_with_behaviour_overlap" in continuity_evidence:
        continuity_specific.append("temporal_overlap_with_behaviour_overlap")
    if evidence_overlap:
        continuity_specific.append(f"evidence_ref_overlap:{len(evidence_overlap)}")
    if role_overlap:
        continuity_specific.append(f"role_overlap:{len(role_overlap)}")

    missing_evidence = list(candidate_row.get("missing_evidence_reasons") or [])
    conflicts: list[str] = []

    review_options = _classify_review_options(
        shared_count=shared_behavior_count,
        continuity_specific=continuity_specific,
        conflicts=conflicts,
    )

    feature_dimensions = {
        "shared_behaviour": bool(shared_behavior_count),
        "temporal": "temporal_overlap_with_behaviour_overlap" in continuity_evidence,
        "evidence_ref_overlap": bool(evidence_overlap),
        "role_or_topology_overlap": bool(role_overlap),
    }
    independent_dimensions = [name for name, present in feature_dimensions.items() if present]

    common_guard = {"shared_behaviour_observation_count": shared_behavior_count, "shared_behaviour_observation_ids": shared_behavior_ids}
    continuity_guard = {
        "temporal_overlap_with_behaviour_overlap": "temporal_overlap_with_behaviour_overlap" in continuity_evidence,
        "evidence_ref_overlap": evidence_overlap,
        "role_overlap": role_overlap,
    }

    review_row = {
        "continuity_candidate_id": candidate_id,
        "operation_a": {
            "operation_id": op_a.get("operation_id"),
            "operation_population_id": op_a.get("operation_population_id"),
            "source_path": op_a.get("source_path"),
            "source_identity": op_a.get("source_identity") or {},
            "evidence_count": int(op_a.get("evidence_count") or 0),
            "primitive_reference_count": int(op_a.get("primitive_reference_count") or 0),
            "subject_count": int(op_a.get("subject_count") or 0),
            "evidence_refs": _as_sorted_strings(op_a.get("evidence_refs") or []),
            "roles": _as_sorted_strings(op_a.get("roles") or []),
        },
        "operation_b": {
            "operation_id": op_b.get("operation_id"),
            "operation_population_id": op_b.get("operation_population_id"),
            "source_path": op_b.get("source_path"),
            "source_identity": op_b.get("source_identity") or {},
            "evidence_count": int(op_b.get("evidence_count") or 0),
            "primitive_reference_count": int(op_b.get("primitive_reference_count") or 0),
            "subject_count": int(op_b.get("subject_count") or 0),
            "evidence_refs": _as_sorted_strings(op_b.get("evidence_refs") or []),
            "roles": _as_sorted_strings(op_b.get("roles") or []),
        },
        "immutable_evidence_refs": {
            "lineage": candidate_row.get("lineage", {}),
            "h4_manifest_digest": h4_manifest_digest,
            "contract_versions_a": op_a.get("contract_versions"),
            "contract_versions_b": op_b.get("contract_versions"),
        },
        "candidate_nominated_by": "shared_behaviour_overlap_plus_temporal_evidence",
        "common_playbook_evidence": common_playbook,
        "continuity_specific_evidence": continuity_specific,
        "evidence_accounting": {
            "supporting_evidence_count": len(common_playbook) + len(continuity_specific),
            "supporting_primitive_count": int(op_a.get("primitive_reference_count") or 0) + int(op_b.get("primitive_reference_count") or 0),
            "independent_feature_dimensions": independent_dimensions,
            "completeness": "complete" if not missing_evidence and not conflicts else "partial",
            "missing_evidence": missing_evidence,
            "conflicts": conflicts,
            "provenance_refs": {
                "common_playbook": common_guard,
                "continuity_signal": continuity_guard,
            },
        },
        "identity_guards": {
            "source_identity_guard": True,
            "same_human_claim": False,
            "same_operation_claim": False,
            "operation_a_separate_identity": True,
            "operation_b_separate_identity": True,
        },
        "allowed_review_options": review_options,
    }

    index_row = {
        "continuity_candidate_id": candidate_id,
        "operation_a": op_a.get("operation_id"),
        "operation_b": op_b.get("operation_id"),
        "main_reason_nominated": common_playbook[:2],
        "strongest_continuity_specific_evidence": continuity_specific,
        "major_missing_or_conflicting_evidence": missing_evidence or (conflicts or []),
        "allowed_review_options": review_options,
    }
    return review_row, index_row


def qualify_historical_continuity_review_packet(
    *,
    h2_artifact: Mapping[str, Any],
    h3_artifact: Mapping[str, Any],
    h4_artifact: Mapping[str, Any],
) -> dict[str, Any]:
    if h2_artifact.get("artifact_digest") != BINDING_DIGESTS["h2"]:
        raise Psi0hH4RHistoricalContinuityReviewPacketError("PSI0H_H4R_H2_DIGEST_MISMATCH")
    if h3_artifact.get("artifact_digest") != BINDING_DIGESTS["h3"]:
        raise Psi0hH4RHistoricalContinuityReviewPacketError("PSI0H_H4R_H3_DIGEST_MISMATCH")
    if h4_artifact.get("artifact_digest") != BINDING_DIGESTS["h4"]:
        raise Psi0hH4RHistoricalContinuityReviewPacketError("PSI0H_H4R_H4_DIGEST_MISMATCH")
    h4_source = h4_artifact.get("source", {})
    if h4_source.get("manifest_digest") and h4_source.get("manifest_digest") != BINDING_DIGESTS["h4_projection_manifest"]:
        raise Psi0hH4RHistoricalContinuityReviewPacketError("PSI0H_H4R_H4_MANIFEST_DIGEST_MISMATCH")

    if not isinstance(h2_artifact.get("candidate_rows"), list):
        raise Psi0hH4RHistoricalContinuityReviewPacketError("PSI0H_H4R_H2_CANDIDATES_INVALID")
    if not isinstance(h3_artifact.get("reviewed_rows"), list):
        raise Psi0hH4RHistoricalContinuityReviewPacketError("PSI0H_H4R_H3_REVIEW_ROWS_INVALID")

    if h4_artifact.get("schema_version") != "psi0h-h4.historical-operation-census.v1":
        raise Psi0hH4RHistoricalContinuityReviewPacketError("PSI0H_H4R_H4_SCHEMA_MISMATCH")
    if h4_artifact.get("milestone") != "PSI0H-H4":
        raise Psi0hH4RHistoricalContinuityReviewPacketError("PSI0H_H4R_H4_MILESTONE_MISMATCH")

    h2_by_candidate = _extract_evidence_of_continuity_rows(h2_artifact["candidate_rows"], stage="H2")
    h3_by_candidate = _extract_evidence_of_continuity_rows(h3_artifact["reviewed_rows"], stage="H3")
    if len(h2_by_candidate) != len(h3_by_candidate) or set(h2_by_candidate) != set(h3_by_candidate):
        raise Psi0hH4RHistoricalContinuityReviewPacketError("PSI0H_H4R_H3_H2_BOUNDARY_MISMATCH")

    continuity_rows = list(h2_by_candidate.values())
    if len(continuity_rows) != 13:
        raise Psi0hH4RHistoricalContinuityReviewPacketError("PSI0H_H4R_CONTINUITY_COUNT_EXPECTED_13")

    h4_operations = _row_dicts_by_id(h4_artifact.get("discovered_populations") or [])

    reviewed_rows: list[dict[str, Any]] = []
    review_index: list[dict[str, Any]] = []
    for row in sorted(continuity_rows, key=lambda item: item["continuity_candidate_id"]):
        candidate_id = str(row.get("continuity_candidate_id") or "").strip()
        op_ids = row.get("operation_ids")
        if not isinstance(op_ids, list) or len(op_ids) != 2 or len(set(op_ids)) != 2:
            raise Psi0hH4RHistoricalContinuityReviewPacketError("PSI0H_H4R_CANDIDATE_OPERATION_IDS_INVALID")
        op_a_id, op_b_id = [str(x) for x in op_ids]
        op_a = h4_operations.get(op_a_id)
        op_b = h4_operations.get(op_b_id)
        if not op_a or not op_b:
            raise Psi0hH4RHistoricalContinuityReviewPacketError(f"PSI0H_H4R_OPERATION_NOT_IN_H4:{candidate_id}")
        reviewed, index = _build_review_row(
            candidate_id=candidate_id,
            op_a=op_a,
            op_b=op_b,
            candidate_row=row,
            h4_manifest_digest=h4_artifact.get("source", {}).get("manifest_digest"),
        )
        reviewed_rows.append(reviewed)
        review_index.append(index)

    packet = {
        "schema_version": SCHEMA_VERSION,
        "milestone": "PSI0H-H4R",
        "status": "PASS" if reviewed_rows else "HOLD",
        "verdict": "H4R_REVIEW_PACKET_PASS" if reviewed_rows else "H4R_REVIEW_PACKET_EMPTY",
        "candidate_count": len(reviewed_rows),
        "unique_operation_count": len({op for row in reviewed_rows for op in (row["operation_a"]["operation_id"], row["operation_b"]["operation_id"])}),
        "reviewed_rows": reviewed_rows,
        "review_index": review_index,
        "feature_distribution": {
            "common_playbook_rows": len([r for r in reviewed_rows if "shared_behaviour_observations" in " ".join(r["common_playbook_evidence"])]),
            "continuity_specific_rows": len([r for r in reviewed_rows if r["continuity_specific_evidence"]]),
            "continuity_rows": len(reviewed_rows),
            "rows_with_evidence_ref_overlap": sum(1 for r in reviewed_rows if any(v.startswith("evidence_ref_overlap:") for v in r["continuity_specific_evidence"])),
            "rows_with_missing_evidence": sum(1 for r in reviewed_rows if r["evidence_accounting"]["missing_evidence"]),
            "rows_with_conflicts": sum(1 for r in reviewed_rows if r["evidence_accounting"]["conflicts"]),
        },
        "review_options_distribution": {
            "COMMON_PLAYBOOK_ONLY": 0,
            "PLAUSIBLE_OPERATIONAL_CONTINUITY": 0,
            "PLAUSIBLE_OPERATIONAL_FAMILY": 0,
            "INSUFFICIENT_EVIDENCE": 0,
            "CONFLICTING_EVIDENCE": 0,
        },
        "same_operation_inference_blocked": True,
        "same_human_inference_blocked": True,
        "provenance": {
            "h2_artifact_digest": h2_artifact.get("artifact_digest"),
            "h3_artifact_digest": h3_artifact.get("artifact_digest"),
            "h4_artifact_digest": h4_artifact.get("artifact_digest"),
            "h4_projection_manifest_digest": h4_artifact.get("source", {}).get("manifest_digest"),
        },
        "scope": {
            "observation_only": True,
            "candidate_disposition": False,
            "candidate_generation": False,
            "comparison": False,
            "monitoring": False,
            "activation": False,
            "provider_or_rpc_calls": 0,
            "same_operation": False,
            "same_human": False,
        },
        "authority": {
            "candidate_generation": False,
            "candidate_disposition": False,
            "comparison": False,
            "monitoring": False,
            "activation": False,
            "supported": False,
            "same_operation": False,
            "same_human": False,
            "ranking": False,
            "policy": False,
            "alerting": False,
            "trading": False,
        },
    }
    for row in reviewed_rows:
        for opt in row["allowed_review_options"]:
            if opt == REVIEW_OPTIONS["COMMON_PLAYBOOK_ONLY"]:
                packet["review_options_distribution"]["COMMON_PLAYBOOK_ONLY"] += 1
            elif opt == REVIEW_OPTIONS["PLAUSIBLE_OPERATIONAL_CONTINUITY"]:
                packet["review_options_distribution"]["PLAUSIBLE_OPERATIONAL_CONTINUITY"] += 1
            elif opt == REVIEW_OPTIONS["PLAUSIBLE_OPERATIONAL_FAMILY"]:
                packet["review_options_distribution"]["PLAUSIBLE_OPERATIONAL_FAMILY"] += 1
            elif opt == REVIEW_OPTIONS["INSUFFICIENT_EVIDENCE"]:
                packet["review_options_distribution"]["INSUFFICIENT_EVIDENCE"] += 1
            elif opt == REVIEW_OPTIONS["CONFLICTING_EVIDENCE"]:
                packet["review_options_distribution"]["CONFLICTING_EVIDENCE"] += 1

    packet["artifact_digest"] = _digest(packet)
    return packet


def verify_historical_continuity_review_packet(record: Mapping[str, Any]) -> bool:
    if not isinstance(record, Mapping):
        raise Psi0hH4RHistoricalContinuityReviewPacketError("PSI0H_H4R_RECORD_INVALID")
    required = ("artifact_digest", "schema_version", "status", "reviewed_rows", "review_index", "candidate_count")
    if not all(k in record for k in required):
        raise Psi0hH4RHistoricalContinuityReviewPacketError("PSI0H_H4R_RECORD_INVALID")
    artifact = str(record.get("artifact_digest") or "")
    if len(artifact) != 64 or any(ch not in "0123456789abcdef" for ch in artifact):
        raise Psi0hH4RHistoricalContinuityReviewPacketError("PSI0H_H4R_RECORD_DIGEST_INVALID")

    replay = dict(record)
    replay.pop("artifact_digest")
    expected = _digest(replay)
    if expected != artifact:
        raise Psi0hH4RHistoricalContinuityReviewPacketError("PSI0H_H4R_RECORD_DIGEST_MISMATCH")

    rows = record["reviewed_rows"]
    index = record["review_index"]
    if not isinstance(rows, list) or not isinstance(index, list):
        raise Psi0hH4RHistoricalContinuityReviewPacketError("PSI0H_H4R_RECORD_ROWS_INVALID")
    if len(rows) != record["candidate_count"] or len(index) != record["candidate_count"]:
        raise Psi0hH4RHistoricalContinuityReviewPacketError("PSI0H_H4R_RECORD_COUNTS_INVALID")
    if len(rows) == 13 and len({r["continuity_candidate_id"] for r in rows}) != 13:
        raise Psi0hH4RHistoricalContinuityReviewPacketError("PSI0H_H4R_DUPLICATE_CANDIDATES")
    return True
