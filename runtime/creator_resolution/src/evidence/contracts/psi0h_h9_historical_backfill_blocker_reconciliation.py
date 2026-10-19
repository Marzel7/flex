"""PSI0H-H9 historical backfill blocker reconciliation boundary.

This is a read-only diagnostic that inspects PSI0H-H7 planning and PSI0H-H8
execution artifacts to classify why no replayable primitives were produced.

It does not invoke any source capture, does not compare candidates, and does not
change operational authority. The contract is intended to decide whether the empty
primitive pool is due to source selection, budgeting, missing evidence families,
or reconstruction prerequisites.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "psi0h-h9.historical-backfill-blocker-reconciliation.v1"
RUN_ID = "psi0h-h9-historical-backfill-blocker-reconciliation"
H7_SCHEMA_VERSION = "psi0h-h7.bounded-historical-backfill-preflight.v1"
H8_SCHEMA_VERSION = "psi0h-h8.bounded-historical-backfill-execution.v1"

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

BLOCKER_SOURCE_PATH_MISSING = "SOURCE_PATH_MISSING"
BLOCKER_SOURCE_OPEN_FAILED = "SOURCE_OPEN_FAILED"
BLOCKER_SOURCE_EMPTY = "SOURCE_EMPTY"
BLOCKER_NO_PRIMITIVE_FAMILY = "NO_RECONSTRUCTABLE_PRIMITIVE_FAMILY"
BLOCKER_REQUIRED_FIELD_GAPS = "REQUIRED_OPERATION_FAMILY_FIELDS_MISSING"
BLOCKER_EVENT_LINEAGE_GAPS = "EVENT_LINEAGE_MISSING"
BLOCKER_ROW_BUDGET_PREVENTED_PRIMITIVE_DERIVATION = "ROW_BUDGET_PREVENTED_PRIMITIVE_DERIVATION"
BLOCKER_H7_SELECTED_NON_RECONSTRUCTABLE = "H7_SELECTED_NON_RECONSTRUCTABLE_SOURCE"
BLOCKER_H6_RECONTEXT_MAY_BE_NEEDED = "OLDER_PRE_E_SOURCE_RECONCILIATION_NEEDED"
BLOCKER_H8_ALREADY_EMPTY_POOL = "H8_EXECUTION_PRIMITIVE_POOL_EMPTY"

VERDICT_H9_BLOCKERS_FOUND = "PSI0H_H9_BLOCKERS_IDENTIFIED"
VERDICT_H9_INCORRECT_SELECTION = "PSI0H_H9_WRONG_SOURCE_RECONCILIATION_REQUIRED"
VERDICT_H9_RETRY_REQUIRED = "PSI0H_H9_RETRY_WITH_BOUNDARY_REVISIONS"
VERDICT_H9_NO_ACTION_NEEDED = "PSI0H_H9_NO_ACTION_NEEDED"


class Psi0hH9HistoricalBackfillBlockerReconciliationError(RuntimeError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _digest(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise Psi0hH9HistoricalBackfillBlockerReconciliationError("PSI0H_H9_INPUT_ARTIFACT_NOT_MAPPING")
    return payload


def _safe_json(value: Any) -> Mapping[str, Any] | None:
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


@dataclass(frozen=True)
class _SourceDiagnostic:
    source_path: str
    row_ceiling: int
    h7_blockers: tuple[str, ...]
    h7_reconstructable: bool
    h8_evidence_rows: int
    h8_primitive_rows: int
    h8_source_path_present: bool
    source_opened: bool
    evidence_family_present: bool
    primitive_table_present: bool
    primitive_row_available: bool
    required_fields_present: bool
    event_lineage_present: bool
    reasons: tuple[str, ...]
    recommendation: str


def _required_fields_present(row: Mapping[str, Any]) -> bool:
    required = {
        "operation_id",
        "wallet",
        "wallets",
        "creator",
        "funder",
        "recipient",
        "signer",
        "source",
        "destination",
        "roles",
        "mechanism",
    }
    evidence_fields = set(row.keys())
    return bool(required & evidence_fields)


def _event_lineage_present(row: Mapping[str, Any]) -> bool:
    if "window" in row and isinstance(row["window"], Mapping):
        if isinstance(row["window"].get("start"), int) and isinstance(row["window"].get("end"), int):
            return True
    if "window_start" in row and "window_end" in row:
        return isinstance(row["window_start"], int) and isinstance(row["window_end"], int)
    return False


def _scan_source(payload: Path) -> tuple[bool, bool, bool, bool]:
    """Return `(evidence_present, primitive_table_present, primitive_row_present, payload_field_present)`.

    `payload_field_present` is True when at least one row from evidence or primitive
    shows operation/topology/lineage fields.
    """

    if not payload.exists():
        return False, False, False, False

    conn = sqlite3.connect(f"file:{payload}?mode=ro&immutable=1", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        tables = {name for name, in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if not tables:
            return False, False, False, False

        evidence_present = False
        primitive_present = "primitive_observations" in tables
        primitive_row_present = False
        payload_fields_present = False

        if "normalized_evidence_records" in tables:
            row = conn.execute(
                "SELECT payload_json FROM normalized_evidence_records ORDER BY rowid LIMIT 1"
            ).fetchone()
            if row:
                payload_fields = _safe_json(row["payload_json"])
                if payload_fields:
                    evidence_present = True
                    payload_fields_present = payload_fields_present or _required_fields_present(payload_fields) or _event_lineage_present(payload_fields)

        if primitive_present:
            primitive_id = conn.execute("SELECT primitive_id, output_payload_json FROM primitive_observations ORDER BY rowid LIMIT 1").fetchone()
            primitive_row_present = primitive_id is not None
            if primitive_id is not None:
                payload_fields = _safe_json(primitive_id["output_payload_json"])
                if payload_fields:
                    payload_fields_present = payload_fields_present or _required_fields_present(payload_fields) or _event_lineage_present(payload_fields)

        return evidence_present, primitive_present, primitive_row_present, payload_fields_present
    finally:
        conn.close()


def _looks_pre_e_path(source_path: str) -> bool:
    return "oip_v2_1" in source_path


def _classify_source(
    *,
    source_path: str,
    h7_row_ceiling: int,
    h7_blockers: tuple[str, ...],
    h7_reconstructable: bool,
    h8_evidence_rows: int,
    h8_primitive_rows: int,
) -> _SourceDiagnostic:
    path_obj = Path(source_path)

    if not path_obj.exists():
        return _SourceDiagnostic(
            source_path=source_path,
            row_ceiling=h7_row_ceiling,
            h7_blockers=h7_blockers,
            h7_reconstructable=h7_reconstructable,
            h8_evidence_rows=h8_evidence_rows,
            h8_primitive_rows=h8_primitive_rows,
            h8_source_path_present=True,
            source_opened=False,
            evidence_family_present=False,
            primitive_table_present=False,
            primitive_row_available=False,
            required_fields_present=False,
            event_lineage_present=False,
            reasons=(BLOCKER_SOURCE_PATH_MISSING,),
            recommendation="Rebind H7 source plan to a reachable persistent source path.",
        )

    try:
        evidence_present, primitive_present, primitive_row_present, field_present = _scan_source(path_obj)
    except Exception:
        return _SourceDiagnostic(
            source_path=source_path,
            row_ceiling=h7_row_ceiling,
            h7_blockers=h7_blockers,
            h7_reconstructable=h7_reconstructable,
            h8_evidence_rows=h8_evidence_rows,
            h8_primitive_rows=h8_primitive_rows,
            h8_source_path_present=True,
            source_opened=False,
            evidence_family_present=False,
            primitive_table_present=False,
            primitive_row_available=False,
            required_fields_present=False,
            event_lineage_present=False,
            reasons=(BLOCKER_SOURCE_OPEN_FAILED,),
            recommendation="Repair source DB access path and schema before replaying H8.",
        )

    blockers: list[str] = []
    if not evidence_present and not primitive_present:
        blockers.append(BLOCKER_SOURCE_EMPTY)
    elif primitive_present and not primitive_row_present:
        blockers.append(BLOCKER_SOURCE_EMPTY)

    if not h7_reconstructable:
        blockers.append(BLOCKER_H7_SELECTED_NON_RECONSTRUCTABLE)

    if h8_primitive_rows == 0:
        if h8_evidence_rows >= max(1, h7_row_ceiling):
            blockers.append(BLOCKER_ROW_BUDGET_PREVENTED_PRIMITIVE_DERIVATION)
        if not primitive_row_present:
            blockers.append(BLOCKER_NO_PRIMITIVE_FAMILY)

    if not field_present:
        blockers.append(BLOCKER_REQUIRED_FIELD_GAPS)

    reasons_set = set(blockers)
    # lineage checks only when we had payload rows
    required_present = field_present
    event_lineage_present = False
    if path_obj.exists() and (evidence_present or primitive_row_present):
        # Best-effort lineage check
        conn = sqlite3.connect(f"file:{path_obj}?mode=ro&immutable=1", uri=True)
        try:
            conn.row_factory = sqlite3.Row
            first_payload = conn.execute(
                "SELECT payload_json FROM normalized_evidence_records ORDER BY rowid LIMIT 1"
            ).fetchone()
            if first_payload:
                parsed = _safe_json(first_payload["payload_json"])
                if parsed:
                    event_lineage_present = _event_lineage_present(parsed)

            if not event_lineage_present:
                table_names = {
                    name
                    for name, in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                }
                if "primitive_observations" in table_names:
                    row = conn.execute(
                        "SELECT output_payload_json FROM primitive_observations ORDER BY rowid LIMIT 1"
                    ).fetchone()
                    if row:
                        parsed = _safe_json(row["output_payload_json"])
                        if parsed:
                            event_lineage_present = _event_lineage_present(parsed)
        finally:
            conn.close()

    if not event_lineage_present:
        reasons_set.add(BLOCKER_EVENT_LINEAGE_GAPS)

    if h7_blockers:
        if "ADDRESS_LEVEL_MOTIFS_ONLY" in h7_blockers:
            reasons_set.add(BLOCKER_REQUIRED_FIELD_GAPS)
        if "NO_STABLE_OPERATION_BOUNDARY" in h7_blockers:
            reasons_set.add(BLOCKER_EVENT_LINEAGE_GAPS)

    if _looks_pre_e_path(source_path) and {
        BLOCKER_NO_PRIMITIVE_FAMILY,
        BLOCKER_H7_SELECTED_NON_RECONSTRUCTABLE,
        BLOCKER_REQUIRED_FIELD_GAPS,
    }.intersection(reasons_set):
        reasons_set.add(BLOCKER_H6_RECONTEXT_MAY_BE_NEEDED)

    if not reasons_set:
        # Conservative fallback where source still appears reconcilable but did not emit primitives.
        reasons_set.add(BLOCKER_NO_PRIMITIVE_FAMILY)

    recommendation = "Reconcile H7 source selection and rerun H8 with split evidence/primitive ceilings."
    if BLOCKER_H7_SELECTED_NON_RECONSTRUCTABLE in reasons_set:
        recommendation = "H7 selected partial-reconstructability sources; tighten source scan to already-reconstructable cohorts or expand historical source evidence."
    if BLOCKER_SOURCE_OPEN_FAILED in reasons_set or BLOCKER_SOURCE_PATH_MISSING in reasons_set:
        recommendation = "Repair source-path availability and rerun H7/H8 with exact bound snapshot checks."

    return _SourceDiagnostic(
        source_path=source_path,
        row_ceiling=h7_row_ceiling,
        h7_blockers=h7_blockers,
        h7_reconstructable=h7_reconstructable,
        h8_evidence_rows=h8_evidence_rows,
        h8_primitive_rows=h8_primitive_rows,
        h8_source_path_present=True,
        source_opened=True,
        evidence_family_present=evidence_present,
        primitive_table_present=primitive_present,
        primitive_row_available=primitive_row_present,
        required_fields_present=required_present,
        event_lineage_present=event_lineage_present,
        reasons=tuple(sorted(reasons_set)),
        recommendation=recommendation,
    )


def _count_rows(rows: list[dict[str, Any]], source_path: str, field: str) -> int:
    return sum(1 for row in rows if str(row.get("source_path")) == source_path and isinstance(row, Mapping) and field in row)


def qualify_h9_backfill_blocker_reconciliation(
    *,
    h7_artifact: Mapping[str, Any],
    h8_artifact: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(h7_artifact, Mapping):
        raise Psi0hH9HistoricalBackfillBlockerReconciliationError("PSI0H_H9_H7_ARTIFACT_INVALID")
    if not isinstance(h8_artifact, Mapping):
        raise Psi0hH9HistoricalBackfillBlockerReconciliationError("PSI0H_H9_H8_ARTIFACT_INVALID")

    if h7_artifact.get("schema_version") != H7_SCHEMA_VERSION:
        raise Psi0hH9HistoricalBackfillBlockerReconciliationError("PSI0H_H9_H7_SCHEMA_MISMATCH")
    if h8_artifact.get("schema_version") != H8_SCHEMA_VERSION:
        raise Psi0hH9HistoricalBackfillBlockerReconciliationError("PSI0H_H9_H8_SCHEMA_MISMATCH")
    if h7_artifact.get("status") != "PASS":
        raise Psi0hH9HistoricalBackfillBlockerReconciliationError("PSI0H_H9_H7_NOT_PASS")
    if h8_artifact.get("status") not in {"PASS", "HOLD"}:
        raise Psi0hH9HistoricalBackfillBlockerReconciliationError("PSI0H_H9_H8_STATUS_INVALID")

    source_plan = h7_artifact.get("source_plan", {})
    if not isinstance(source_plan, Mapping):
        raise Psi0hH9HistoricalBackfillBlockerReconciliationError("PSI0H_H9_H7_SOURCE_PLAN_INVALID")
    candidate_sources = source_plan.get("candidate_sources", [])
    if not isinstance(candidate_sources, list):
        raise Psi0hH9HistoricalBackfillBlockerReconciliationError("PSI0H_H9_H7_CANDIDATE_SOURCES_INVALID")

    execution = h8_artifact.get("execution", {})
    if not isinstance(execution, Mapping):
        raise Psi0hH9HistoricalBackfillBlockerReconciliationError("PSI0H_H9_H8_EXECUTION_INVALID")

    evidence_rows = execution.get("evidence_rows", [])
    primitive_rows = execution.get("primitive_rows", [])
    if not isinstance(evidence_rows, list) or not isinstance(primitive_rows, list):
        raise Psi0hH9HistoricalBackfillBlockerReconciliationError("PSI0H_H9_H8_EXECUTION_ROW_SHAPE_INVALID")

    diagnostics: list[dict[str, Any]] = []
    aggregate_reasons: Counter[str] = Counter()
    reconstructable_sources = 0

    for row in candidate_sources:
        if not isinstance(row, Mapping):
            continue

        source_path = str(row.get("source_path", ""))
        if not source_path:
            continue

        row_ceiling = int(row.get("row_reconstruction_ceiling") or 0)
        h7_blockers = tuple(str(item) for item in row.get("blocking_reasons", ()) if isinstance(item, str))
        h7_reconstructable = bool(row.get("reconstructable", False))
        h8_evidence_count = _count_rows(evidence_rows, source_path, "evidence_id")
        h8_primitive_count = _count_rows(primitive_rows, source_path, "primitive_id")

        diag = _classify_source(
            source_path=source_path,
            h7_row_ceiling=row_ceiling,
            h7_blockers=h7_blockers,
            h7_reconstructable=h7_reconstructable,
            h8_evidence_rows=h8_evidence_count,
            h8_primitive_rows=h8_primitive_count,
        )
        diagnostics.append(
            {
                "source_path": diag.source_path,
                "row_ceiling": diag.row_ceiling,
                "h7_blockers": list(diag.h7_blockers),
                "h7_reconstructable": diag.h7_reconstructable,
                "h8_selection": {
                    "evidence_rows": diag.h8_evidence_rows,
                    "primitive_rows": diag.h8_primitive_rows,
                },
                "source_opened": diag.source_opened,
                "evidence_family_present": diag.evidence_family_present,
                "primitive_table_present": diag.primitive_table_present,
                "primitive_row_available": diag.primitive_row_available,
                "required_fields_present": diag.required_fields_present,
                "event_lineage_present": diag.event_lineage_present,
                "reasons": list(diag.reasons),
                "recommendation": diag.recommendation,
            }
        )

        for reason in diag.reasons:
            aggregate_reasons[reason] += 1

        if not {
            BLOCKER_SOURCE_EMPTY,
            BLOCKER_SOURCE_OPEN_FAILED,
            BLOCKER_SOURCE_PATH_MISSING,
            BLOCKER_H7_SELECTED_NON_RECONSTRUCTABLE,
            BLOCKER_H6_RECONTEXT_MAY_BE_NEEDED,
        }.intersection(set(diag.reasons)):
            reconstructable_sources += 1

    execution_primitive_count = int(execution.get("primitive_count", 0) or 0)

    if execution_primitive_count == 0:
        aggregate_reasons[BLOCKER_H8_ALREADY_EMPTY_POOL] += 1

    if not diagnostics:
        verdict = VERDICT_H9_INCORRECT_SELECTION
        blockers = [BLOCKER_H6_RECONTEXT_MAY_BE_NEEDED]
        status = "HOLD"
    else:
        status = "PASS" if execution_primitive_count > 0 else "HOLD"
        if execution_primitive_count > 0:
            verdict = VERDICT_H9_NO_ACTION_NEEDED
            blockers = []
        elif aggregate_reasons:
            blockers = sorted(aggregate_reasons.keys())
            if any(
                reason in aggregate_reasons
                for reason in (
                    BLOCKER_SOURCE_EMPTY,
                    BLOCKER_H6_RECONTEXT_MAY_BE_NEEDED,
                    BLOCKER_SOURCE_OPEN_FAILED,
                    BLOCKER_SOURCE_PATH_MISSING,
                )
            ):
                verdict = VERDICT_H9_INCORRECT_SELECTION
            else:
                verdict = VERDICT_H9_BLOCKERS_FOUND
        else:
            verdict = VERDICT_H9_RETRY_REQUIRED
            blockers = [VERDICT_H9_RETRY_REQUIRED.lower()]

    next_action = {
        "decision": "STOP_AND_RECONCILE_BEFORE_NEXT_H8",
        "instruction": (
            "Classify and apply source/budget/fields adjustments before any additional H8 boundary run. "
            "No source writes, provider calls, comparison, or candidate disposition is allowed."
        ),
        "verdict": verdict,
        "recommended_next": [
            "rerun H9 after H7 plan correction",
            "bind explicit primitive budget strategy before another H8",
            "if no reconcilable sources remain, locate alternate source artifacts (older pre-E groups)",
        ],
    }

    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "milestone": "PSI0H-H9",
        "run_id": RUN_ID,
        "status": status,
        "verdict": verdict,
        "h7_artifact": h8_artifact.get("h7_artifact") if isinstance(h8_artifact.get("h7_artifact"), str) else None,
        "h8_artifact": h8_artifact.get("artifact_digest"),
        "execution_status": h8_artifact.get("execution_status"),
        "h7_selection_count": len(candidate_sources),
        "h8_source_rows": len(evidence_rows),
        "h8_primitive_rows": execution_primitive_count,
        "diagnostics": diagnostics,
        "blockers": [],
        "reason_counts": dict(aggregate_reasons),
        "reconstructable_source_count": reconstructable_sources,
        "next_action": next_action,
        "scope": {
            "comparison": False,
            "candidate_generation": False,
            "candidate_disposition": False,
            "monitoring": False,
            "policy": False,
            "provider_access": False,
            "source_read": True,
            "service_changes": False,
            "activation": False,
        },
        "authority": dict(AUTHORITY),
    }

    if status == "PASS":
        result["blockers"] = []
    else:
        result["blockers"] = sorted(set(blockers + [str(r) for r in aggregate_reasons.keys()]))
    result["artifact_digest"] = _digest(result)
    return result


def verify_h9_blocker_reconciliation(record: Mapping[str, Any]) -> bool:
    if not isinstance(record, Mapping):
        raise Psi0hH9HistoricalBackfillBlockerReconciliationError("PSI0H_H9_RECORD_INVALID")
    if record.get("schema_version") != SCHEMA_VERSION:
        raise Psi0hH9HistoricalBackfillBlockerReconciliationError("PSI0H_H9_RECORD_SCHEMA_MISMATCH")
    digest = str(record.get("artifact_digest", ""))
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise Psi0hH9HistoricalBackfillBlockerReconciliationError("PSI0H_H9_RECORD_DIGEST_INVALID")

    replay = dict(record)
    replay.pop("artifact_digest")
    if _digest(replay) != digest:
        raise Psi0hH9HistoricalBackfillBlockerReconciliationError("PSI0H_H9_RECORD_DIGEST_MISMATCH")
    if any(record.get("authority", {}).values()):
        raise Psi0hH9HistoricalBackfillBlockerReconciliationError("PSI0H_H9_RECORD_AUTHORITY_EXPANDED")
    return True
