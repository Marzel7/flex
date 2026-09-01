"""PSI0H-H7R legacy candidate-source reconciliation boundary.

This boundary is read-only. It inspects the legacy candidate rows selected by
PSI0H-H7 and determines whether each can be upgraded to a
reconstructable historical operation source without providers.

Outcomes are:
* RECONSTRUCTABLE_AFTER_LOCAL_REBIND
* REQUIRES_BOUNDED_HISTORICAL_BACKFILL
* CANDIDATE_ONLY_NOT_RECONSTRUCTABLE
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping

SCHEMA_VERSION = "psi0h-h7r.legacy-candidate-reconstruction-reconciliation.v1"
RUN_ID = "psi0h-h7r-legacy-candidate-reconstruction"

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

H7_SCHEMA_VERSION = "psi0h-h7.bounded-historical-backfill-preflight.v1"

VERDICT_READY_REBOUND = "PSI0H_H7R_READY_RECONSTRUCTION_REBIND"
VERDICT_HOLD_RECONCILIATION = "PSI0H_H7R_HOLD_LEGACY_RECONCILIATION"

OUTCOME_RECONSTRUCTABLE_AFTER_LOCAL_REBIND = "RECONSTRUCTABLE_AFTER_LOCAL_REBIND"
OUTCOME_REQUIRES_BOUNDED_HISTORICAL_BACKFILL = "REQUIRES_BOUNDED_HISTORICAL_BACKFILL"
OUTCOME_CANDIDATE_ONLY_NOT_RECONSTRUCTABLE = "CANDIDATE_ONLY_NOT_RECONSTRUCTABLE"

BLOCKER_H7_SOURCE_INVALID = "PSI0H_H7R_H7_SOURCE_INVALID"
BLOCKER_H7_LEGACY_SOURCE_MISSING = "PSI0H_H7R_H7_LEGACY_SOURCE_MISSING"
BLOCKER_H7_SOURCE_PATH_MISSING = "PSI0H_H7R_SOURCE_PATH_MISSING"
BLOCKER_H7_SOURCE_OPEN_FAILED = "PSI0H_H7R_SOURCE_OPEN_FAILED"
BLOCKER_H7_NO_ROWS_OR_FACTS = "PSI0H_H7R_NO_ROWS_OR_FACTS"

MAX_REBIND_SAMPLE_ROWS = 500


class Psi0hH7RLegacyCandidateReconstructionReconciliationError(RuntimeError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _digest(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


def _safe_load_json(value: Any) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, Mapping) else None


def _collect_source_evidence_metrics(*, path: Path, sample_limit: int) -> dict[str, Any]:
    """Return compact evidence signals for a source path."""
    metrics: dict[str, Any] = {
        "sampled_evidence_rows": 0,
        "sampled_primitive_rows": 0,
        "has_operation_id": False,
        "has_topology_fields": False,
        "has_role_fields": False,
        "has_mechanism_fields": False,
        "has_temporal_window": False,
        "has_event_type_field": False,
        "has_lineage": False,
        "provenance_links": 0,
        "source_scan_tables": [],
    }

    if not path.exists():
        return metrics

    conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        metrics["source_scan_tables"] = sorted(tables)

        def _mark_payload(payload_json: Any) -> None:
            payload = _safe_load_json(payload_json)
            if not payload:
                return
            if any(k in payload for k in ("operation_id", "operation", "operation_key", "contract_id")):
                metrics["has_operation_id"] = True
            if any(k in payload for k in ("source", "destination", "wallet", "wallets", "creator", "recipient", "funder")):
                metrics["has_topology_fields"] = True
            if any(k in payload for k in ("funder", "recipient", "signer", "activation_sender")):
                metrics["has_role_fields"] = True
            if any(k in payload for k in ("mechanism", "roles", "event_types", "event_type")):
                metrics["has_mechanism_fields"] = True
            if any(k in payload for k in ("window", "window_start", "window_end", "event_time")):
                metrics["has_temporal_window"] = True
            if any(k in payload for k in ("event_types", "fact_family")):
                metrics["has_event_type_field"] = True
            if any(k in payload for k in ("observed_at", "event_time", "block_time", "window")):
                metrics["has_lineage"] = True

        if "normalized_evidence_records" in tables:
            rows = conn.execute(
                "SELECT evidence_id, payload_json FROM normalized_evidence_records LIMIT ?",
                (sample_limit,),
            ).fetchall()
            for row in rows:
                metrics["sampled_evidence_rows"] += 1
                _mark_payload(row["payload_json"])

        if "primitive_observations" in tables:
            rows = conn.execute(
                "SELECT primitive_type, output_payload_json, subjects_json, parameters_json, window_start, window_end "
                "FROM primitive_observations LIMIT ?",
                (sample_limit,),
            ).fetchall()
            for row in rows:
                metrics["sampled_primitive_rows"] += 1
                if row["output_payload_json"]:
                    _mark_payload(row["output_payload_json"])
                if row["parameters_json"]:
                    _mark_payload(row["parameters_json"])
                if row["subjects_json"]:
                    subjects = _safe_load_json(row["subjects_json"])
                    if isinstance(subjects, Mapping) and (
                        "wallet" in subjects
                        or "wallets" in subjects
                        or "funder" in subjects
                        or "recipient" in subjects
                    ):
                        metrics["has_topology_fields"] = True
                        metrics["has_role_fields"] = True
                if row["window_start"] is not None or row["window_end"] is not None:
                    metrics["has_temporal_window"] = True
                    metrics["has_lineage"] = True

        if "normalized_evidence_provenance" in tables:
            metrics["provenance_links"] = int(
                conn.execute(
                    "SELECT COUNT(*) FROM normalized_evidence_provenance WHERE provider_request_id IS NOT NULL"
                ).fetchone()[0]
                or 0
            )

    except Exception:
        return metrics
    finally:
        conn.close()

    return metrics


def _classify_legacy_source(
    *,
    legacy_row: Mapping[str, Any],
    evidence_metrics: Mapping[str, Any],
) -> dict[str, Any]:
    source_path = str(legacy_row.get("source_path", ""))
    path_exists = bool(source_path)
    row_ceiling = int(legacy_row.get("row_reconstruction_ceiling") or 0)
    source_identity = legacy_row.get("source_identity", {}) if isinstance(legacy_row, Mapping) else {}

    blockers: list[str] = []
    if not source_path:
        blockers.append(BLOCKER_H7_SOURCE_PATH_MISSING)

    row = dict(legacy_row)
    missing_fields: list[str] = []
    if not bool(evidence_metrics.get("has_operation_id", False)):
        missing_fields.append("operation_id")
    if not bool(evidence_metrics.get("has_topology_fields", False)):
        missing_fields.append("topology_fields")
    if not bool(evidence_metrics.get("has_role_fields", False)):
        missing_fields.append("subject_roles")
    if not bool(evidence_metrics.get("has_mechanism_fields", False)):
        missing_fields.append("mechanism_fields")
    if not bool(evidence_metrics.get("has_temporal_window", False)):
        missing_fields.append("event_window")

    can_bind_locally = (
        row.get("reconstructable", False)
        or (
            evidence_metrics.get("has_operation_id", False)
            and evidence_metrics.get("has_topology_fields", False)
            and evidence_metrics.get("has_role_fields", False)
            and evidence_metrics.get("has_mechanism_fields", False)
            and evidence_metrics.get("has_temporal_window", False)
            and evidence_metrics.get("has_event_type_field", False)
            and int(row_ceiling or 0) > 0
        )
    )

    evidence_rows = int(legacy_row.get("evidence_rows", 0) or 0)
    primitive_rows = int(legacy_row.get("primitive_rows", 0) or 0)
    provenance_links = int(evidence_metrics.get("provenance_links", 0) or 0)

    if (evidence_rows <= 0 and primitive_rows <= 0) or not path_exists:
        outcome = OUTCOME_CANDIDATE_ONLY_NOT_RECONSTRUCTABLE
        blockers.append(BLOCKER_H7_NO_ROWS_OR_FACTS)
        recoverability = "absent_rows"
    elif can_bind_locally:
        outcome = OUTCOME_RECONSTRUCTABLE_AFTER_LOCAL_REBIND
        recoverability = "local_rebind_possible"
    elif evidence_rows > 0 and (evidence_metrics.get("has_topology_fields") or evidence_metrics.get("has_role_fields")):
        outcome = OUTCOME_REQUIRES_BOUNDED_HISTORICAL_BACKFILL
        recoverability = "boundaries_or_linking_gaps"
    else:
        outcome = OUTCOME_CANDIDATE_ONLY_NOT_RECONSTRUCTABLE
        recoverability = "no_reconstructable_signal"

    row_size_bytes = int((source_identity or {}).get("size_bytes") or 0)
    bytes_feasible = bool(row_ceiling >= 0 and (row_size_bytes == 0 or row_size_bytes < 4 * 1024 * 1024 * 1024))

    recommendation = "Retain as legacy-only and exclude from H7 candidate execution."
    if outcome == OUTCOME_RECONSTRUCTABLE_AFTER_LOCAL_REBIND:
        recommendation = (
            "Promote this row into PSI0H-H7 source_class RECONSTRUCTABLE_OPERATION_SOURCE and re-run H7 planning "
            "with exact same bounded ceilings."
        )
    elif outcome == OUTCOME_REQUIRES_BOUNDED_HISTORICAL_BACKFILL:
        recommendation = (
            "Keep this source as legacy-candidate-only evidence and run bounded historical backfill source-binder "
            "to recover operation-boundary fields before H8."
        )

    return {
        "source_path": source_path,
        "source_identity": source_identity,
        "row_reconstruction_ceiling": int(row_ceiling),
        "row_bound_feasible": row_ceiling > 0,
        "bytes_feasible": bool(bytes_feasible),
        "h7_blockers": list(row.get("blocking_reasons", []) if isinstance(row.get("blocking_reasons"), list) else []),
        "evidence_rows": evidence_rows,
        "primitive_rows": primitive_rows,
        "source_scan_metrics": {
            "sampled_evidence_rows": int(evidence_metrics.get("sampled_evidence_rows", 0)),
            "sampled_primitive_rows": int(evidence_metrics.get("sampled_primitive_rows", 0)),
            "provenance_links": int(provenance_links),
            "has_topology_fields": bool(evidence_metrics.get("has_topology_fields", False)),
            "has_role_fields": bool(evidence_metrics.get("has_role_fields", False)),
            "has_mechanism_fields": bool(evidence_metrics.get("has_mechanism_fields", False)),
            "has_temporal_window": bool(evidence_metrics.get("has_temporal_window", False)),
            "has_event_type_field": bool(evidence_metrics.get("has_event_type_field", False)),
            "has_lineage": bool(evidence_metrics.get("has_lineage", False)),
        },
        "missing_required_fields": missing_fields,
        "recoverability": recoverability,
        "outcome": outcome,
        "classification_reasons": blockers,
        "recommendation": recommendation,
    }


def qualify_legacy_candidate_reconstruction_reconciliation(
    *,
    h7_artifact: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(h7_artifact, Mapping):
        raise Psi0hH7RLegacyCandidateReconstructionReconciliationError("PSI0H_H7R_H7_ARTIFACT_INVALID")
    if h7_artifact.get("schema_version") != H7_SCHEMA_VERSION:
        raise Psi0hH7RLegacyCandidateReconstructionReconciliationError("PSI0H_H7R_H7_SCHEMA_MISMATCH")
    if h7_artifact.get("status") != "PASS":
        raise Psi0hH7RLegacyCandidateReconstructionReconciliationError("PSI0H_H7R_H7_STATUS_INVALID")

    source_plan = h7_artifact.get("source_plan", {})
    if not isinstance(source_plan, Mapping):
        raise Psi0hH7RLegacyCandidateReconstructionReconciliationError("PSI0H_H7R_SOURCE_PLAN_INVALID")

    legacy_candidates = source_plan.get("legacy_candidate_sources", [])
    if not isinstance(legacy_candidates, list):
        legacy_candidates = []

    diagnostics: list[dict[str, Any]] = []
    outcome_counts: dict[str, int] = {
        OUTCOME_RECONSTRUCTABLE_AFTER_LOCAL_REBIND: 0,
        OUTCOME_REQUIRES_BOUNDED_HISTORICAL_BACKFILL: 0,
        OUTCOME_CANDIDATE_ONLY_NOT_RECONSTRUCTABLE: 0,
    }

    for legacy_row in legacy_candidates:
        if not isinstance(legacy_row, Mapping):
            continue

        source_path = str(legacy_row.get("source_path", ""))
        if not source_path:
            diag = {
                "source_path": "",
                "source_identity": {},
                "outcome": OUTCOME_CANDIDATE_ONLY_NOT_RECONSTRUCTABLE,
                "classification_reasons": [BLOCKER_H7_SOURCE_PATH_MISSING],
            }
            diagnostics.append(diag)
            outcome_counts[OUTCOME_CANDIDATE_ONLY_NOT_RECONSTRUCTABLE] += 1
            continue

        metrics = _collect_source_evidence_metrics(path=Path(source_path), sample_limit=MAX_REBIND_SAMPLE_ROWS)
        if not Path(source_path).exists():
            metrics = dict(metrics)
            metrics["source_scan_tables"] = []
            diag = _classify_legacy_source(legacy_row=legacy_row, evidence_metrics=metrics)
            diag["classification_reasons"].append(BLOCKER_H7_SOURCE_OPEN_FAILED)
            outcome = OUTCOME_CANDIDATE_ONLY_NOT_RECONSTRUCTABLE
        else:
            diag = _classify_legacy_source(legacy_row=legacy_row, evidence_metrics=metrics)
            outcome = str(diag["outcome"])

        diag["source_scan_tables"] = metrics.get("source_scan_tables", [])
        diag["h7_legacy_blockers"] = list(legacy_row.get("blocking_reasons", []))
        diag["scan_horizon"] = {
            "max_rows_sampled": MAX_REBIND_SAMPLE_ROWS,
            "path_exists": Path(source_path).exists(),
        }
        diagnostics.append(diag)
        outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1

    reconstructable_count = outcome_counts[OUTCOME_RECONSTRUCTABLE_AFTER_LOCAL_REBIND]
    requires_backfill_count = outcome_counts[OUTCOME_REQUIRES_BOUNDED_HISTORICAL_BACKFILL]
    candidate_only_count = outcome_counts[OUTCOME_CANDIDATE_ONLY_NOT_RECONSTRUCTABLE]

    if reconstructable_count:
        status = "PASS"
        verdict = VERDICT_READY_REBOUND
        next_decision = "RERUN_H7"
        next_instruction = (
            "At least one legacy source can be promoted via local evidence rebind. Update H7 to include only "
            "promoted sources and rerun H7 then H8 with unchanged 20/20 boundaries."
        )
        blockers: list[str] = []
    elif requires_backfill_count:
        status = "PASS"
        verdict = VERDICT_HOLD_RECONCILIATION
        next_decision = "RECONCILE_WITH_BOUNDED_HISTORICAL_BACKFILL"
        next_instruction = (
            "Legacy sources remain candidate-only but show reconstructable seeds. Run bounded historical "
            "backfill/reconstruction capture with explicit request budgets before rerunning H7/H8."
        )
        blockers = ["MISSING_OPERATION_BOUNDARY_FOR_SELECTED_LEGACY_SOURCES"]
    else:
        status = "HOLD"
        verdict = VERDICT_HOLD_RECONCILIATION
        next_decision = "EXPAND_SOURCE_INPUTS"
        next_instruction = (
            "No legacy source can be reconstructed from local retained evidence. Locate additional retained historical "
            "sources or explicit acquisition path before retrying H7/H8."
        )
        blockers = ["CANDIDATE_ONLY_NO_RECONSTRUCTION_PATH"]

    result = {
        "schema_version": SCHEMA_VERSION,
        "run_id": RUN_ID,
        "milestone": "PSI0H-H7R",
        "status": status,
        "verdict": verdict,
        "h7_artifact": h7_artifact.get("artifact_digest"),
        "h7_status": h7_artifact.get("status"),
        "legacy_source_count": len(legacy_candidates),
        "classifications": {
            OUTCOME_RECONSTRUCTABLE_AFTER_LOCAL_REBIND: reconstructable_count,
            OUTCOME_REQUIRES_BOUNDED_HISTORICAL_BACKFILL: requires_backfill_count,
            OUTCOME_CANDIDATE_ONLY_NOT_RECONSTRUCTABLE: candidate_only_count,
        },
        "diagnostics": diagnostics,
        "authority": dict(AUTHORITY),
        "scope": {
            "comparison": False,
            "candidate_generation": False,
            "candidate_disposition": False,
            "monitoring": False,
            "policy": False,
            "activation": False,
            "service_changes": False,
            "provider_access": False,
            "source_read": True,
        },
        "next_action": {
            "decision": next_decision,
            "required_authorization": "NONE",
            "instruction": next_instruction,
        },
        "blockers": blockers,
    }

    result["artifact_digest"] = _digest(result)
    return result


def verify_h7r_reconciliation(record: Mapping[str, Any]) -> bool:
    if not isinstance(record, Mapping):
        raise Psi0hH7RLegacyCandidateReconstructionReconciliationError("PSI0H_H7R_RECORD_INVALID")
    if record.get("schema_version") != SCHEMA_VERSION:
        raise Psi0hH7RLegacyCandidateReconstructionReconciliationError("PSI0H_H7R_RECORD_SCHEMA_MISMATCH")
    artifact_digest = str(record.get("artifact_digest", ""))
    if len(artifact_digest) != 64:
        raise Psi0hH7RLegacyCandidateReconstructionReconciliationError("PSI0H_H7R_RECORD_DIGEST_INVALID")

    replay = dict(record)
    replay.pop("artifact_digest", None)
    if _digest(replay) != artifact_digest:
        raise Psi0hH7RLegacyCandidateReconstructionReconciliationError("PSI0H_H7R_RECORD_DIGEST_MISMATCH")

    diagnostics = record.get("diagnostics")
    if not isinstance(diagnostics, list):
        raise Psi0hH7RLegacyCandidateReconstructionReconciliationError("PSI0H_H7R_RECORD_DIAGNOSTICS_INVALID")
    return True
