"""PSI0H-H7B bounded historical reconstruction capture boundary.

Reads PSI0H-H7R diagnostics and attempts to reconstruct missing
operation-boundary fields for legacy candidates into an isolated store.

No provider calls are performed in this boundary. Authority is limited to:
read-only source scanning plus isolated reconstruction output.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "psi0h-h7b.bounded-historical-reconstruction-capture.v1"
RUN_ID = "psi0h-h7b-bounded-historical-reconstruction-capture"

H7R_SCHEMA_VERSION = "psi0h-h7r.legacy-candidate-reconstruction-reconciliation.v1"
H7_SCHEMA_VERSION = "psi0h-h7.bounded-historical-backfill-preflight.v1"

AUTHORIZATION_ENV = "PSI0H_H7B_RECONSTRUCTION_AUTHORIZED"
AUTHORIZATION_VALUE = "1"

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

VERDICT_READY_BOUNDARY_CAPTURE = "H7B_READY_OPERATION_BOUNDARY_CAPTURE"
VERDICT_HOLD_PARTIAL_RECONSTRUCTION = "H7B_PARTIAL_RECONSTRUCTION_CAPTURE"
VERDICT_HOLD_RECONSTRUCTION_REQUIRED = "H7B_RECONSTRUCTION_REQUIRED"

OUTCOME_RECONSTRUCTABLE_OPERATION_SOURCE = "RECONSTRUCTABLE_OPERATION_SOURCE"
OUTCOME_REQUIRES_BOUNDED_HISTORICAL_BACKFILL = "REQUIRES_BOUNDED_HISTORICAL_BACKFILL"
OUTCOME_CANDIDATE_ONLY_NOT_RECONSTRUCTABLE = "CANDIDATE_ONLY_NOT_RECONSTRUCTABLE"

SOURCE_CLASS_RECONSTRUCTABLE_OPERATION_SOURCE = "RECONSTRUCTABLE_OPERATION_SOURCE"
SOURCE_CLASS_LEGACY_CANDIDATE_ONLY = "LEGACY_CANDIDATE_ONLY"

RECONSTRUCTION_BLOCKER_NO_ROWS = "H7B_NO_ROWS_FOR_RECONSTRUCTION"
RECONSTRUCTION_BLOCKER_SOURCE_MISSING = "H7B_SOURCE_PATH_MISSING"
RECONSTRUCTION_BLOCKER_SOURCE_CHANGED = "H7B_SOURCE_IDENTITY_DRIFT"
RECONSTRUCTION_BLOCKER_AUTH_MISSING = "H7B_AUTHORIZATION_MISSING"
RECONSTRUCTION_BLOCKER_OUTPUT_EXISTS = "H7B_OUTPUT_EXISTS"
RECONSTRUCTION_BLOCKER_SCHEME_INVALID = "H7B_ARTIFACT_SCHEMA_INVALID"

REQUIRED_OPERATION_BOUNDARY_FIELDS = ("operation_id",)


class Psi0hH7BBoundedHistoricalReconstructionCaptureError(RuntimeError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _digest(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


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


def _file_identity(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {
        "device": int(stat.st_dev),
        "inode": int(stat.st_ino),
        "size_bytes": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def _sorted_subjects(payload: Mapping[str, Any]) -> tuple[str, ...]:
    subjects: set[str] = set()
    for key in ("wallet", "source", "destination", "recipient", "funder", "signer", "activation_sender", "creator"):
        value = payload.get(key)
        if isinstance(value, str):
            value = value.strip()
            if value:
                subjects.add(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    item = item.strip()
                    if item:
                        subjects.add(item)
    wallets = payload.get("wallets")
    if isinstance(wallets, list):
        for item in wallets:
            if isinstance(item, str):
                item = item.strip()
                if item:
                    subjects.add(item)
    subject_roles = payload.get("roles")
    if isinstance(subject_roles, Mapping):
        for item in subject_roles.values():
            if isinstance(item, str):
                item = item.strip()
                if item:
                    subjects.add(item)
    return tuple(sorted(subjects))


def _window_fingerprint(payload: Mapping[str, Any], *, fallback_event_time: Any) -> str:
    window_start = (
        payload.get("window_start")
        or (payload.get("window", {}) if isinstance(payload.get("window"), Mapping) else {}).get("start")
        or payload.get("event_time")
        or payload.get("observed_at")
        or fallback_event_time
        or 0
    )
    window_end = (
        payload.get("window_end")
        or (payload.get("window", {}) if isinstance(payload.get("window"), Mapping) else {}).get("end")
        or window_start
    )
    return f"{window_start}|{window_end}"


def _event_fields_present(payload: Mapping[str, Any]) -> bool:
    return bool(
        payload.get("fact_family")
        or payload.get("event_types")
        or payload.get("event_type")
        or payload.get("event_discriminator")
    )


def _topology_fields_present(payload: Mapping[str, Any], *, subjects: tuple[str, ...]) -> bool:
    if subjects:
        return True
    return any(k in payload for k in ("wallet", "wallets", "source", "destination", "recipient", "funder"))


def _mechanism_fields_present(payload: Mapping[str, Any]) -> bool:
    if payload.get("mechanism"):
        return True
    roles = payload.get("roles")
    if isinstance(roles, Mapping) and roles:
        return True
    return any(k in payload for k in ("event_types", "fact_family"))


def _derive_operation_id(
    *,
    source_path: str,
    fallback_event_time: Any,
    evidence_id: str,
    payload: Mapping[str, Any],
    subjects: tuple[str, ...],
) -> str:
    candidate_fields = []
    existing = payload.get("operation_id") or payload.get("operation") or payload.get("operation_key")
    if isinstance(existing, str) and existing.strip():
        return existing.strip()
    candidate_fields.append(str(payload.get("natural_key") or ""))
    candidate_fields.extend(subjects)
    candidate_fields.append(_window_fingerprint(payload, fallback_event_time=fallback_event_time))
    candidate_fields.append(str(payload.get("signature") or evidence_id))
    candidate_fields.append(str(source_path))
    seed = "|".join(x for x in candidate_fields if x)
    digest = sha256(seed.encode("utf-8")).hexdigest()
    return f"recon_{digest[:16]}"


def _collect_reconstruction_rows(*, source_path: Path, row_ceiling: int) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{source_path}?mode=ro&immutable=1", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        rows: list[dict[str, Any]] = []
        tables = {str(name) for name, in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        primitive_cols = set()
        if "primitive_observations" in tables:
            primitive_cols = {str(name) for name, _, *_ in conn.execute("PRAGMA table_info(primitive_observations)")}
        if "normalized_evidence_records" in tables:
            for row in conn.execute(
                "SELECT evidence_id, payload_json, observed_at FROM normalized_evidence_records ORDER BY rowid LIMIT ?",
                (row_ceiling,),
            ).fetchall():
                payload = _safe_json(row["payload_json"])
                if payload is None:
                    continue
                rows.append(
                    {
                        "kind": "evidence",
                        "source_row_id": str(row["evidence_id"]),
                        "observed_at": row["observed_at"] if isinstance(row["observed_at"], int) else None,
                        "payload": dict(payload),
                    }
                )
                if len(rows) >= row_ceiling:
                    break
        if "primitive_observations" in tables and len(rows) < row_ceiling:
            remaining = row_ceiling - len(rows)
            columns = [
                "primitive_id",
                "primitive_type",
                "output_payload_json",
                "window_start",
                "window_end",
                "generated_at",
            ]
            if "missing_inputs_json" in primitive_cols:
                columns.append("missing_inputs_json")
            else:
                columns.append("NULL AS missing_inputs_json")
            if "subjects_json" in primitive_cols:
                columns.append("subjects_json")
            else:
                columns.append("NULL AS subjects_json")
            if "parameters_json" in primitive_cols:
                columns.append("parameters_json")
            else:
                columns.append("NULL AS parameters_json")
            query = f"SELECT {', '.join(columns)} FROM primitive_observations ORDER BY rowid LIMIT ?"
            for row in conn.execute(query, (remaining,)).fetchall():
                payload = _safe_json(row["output_payload_json"]) or {}
                rows.append(
                    {
                        "kind": "primitive",
                        "source_row_id": str(row["primitive_id"]),
                        "observed_at": row["window_end"] if row["window_end"] is not None else row["window_start"],
                        "payload": dict(payload),
                        "primitive_type": row["primitive_type"],
                        "window_start": row["window_start"],
                        "window_end": row["window_end"],
                        "generated_at": row["generated_at"],
                        "subjects_json": row["subjects_json"],
                        "parameters_json": row["parameters_json"],
                        "missing_inputs_json": row["missing_inputs_json"],
                    }
                )
        return rows
    finally:
        conn.close()


def _write_reconstruction_store(*, destination_root: Path, source_path: str, source_rows: list[dict[str, Any]]) -> str:
    destination_root.mkdir(parents=True, exist_ok=True)
    source_leaf = Path(source_path).name or "source"
    out_path = destination_root / f"reconstructed_{source_leaf}.db"
    if out_path.exists():
        # Explicitly keep the capture write-only; avoid overwriting.
        raise Psi0hH7BBoundedHistoricalReconstructionCaptureError("PSI0H_H7B_OUTPUT_REPLACEMENT_ATTEMPTED")

    conn = sqlite3.connect(out_path)
    try:
        c = conn.cursor()
        c.execute(
            """
            CREATE TABLE normalized_evidence_records(
                evidence_id TEXT PRIMARY KEY,
                payload_json TEXT,
                observed_at INTEGER,
                source_row_id TEXT,
                reconstruction_marker TEXT
            )
            """
        )
        c.execute(
            """
            CREATE TABLE primitive_observations(
                primitive_id TEXT PRIMARY KEY,
                primitive_type TEXT,
                output_payload_json TEXT,
                observed_at INTEGER,
                window_start INTEGER,
                window_end INTEGER,
                source_row_id TEXT,
                subjects_json TEXT,
                parameters_json TEXT,
                missing_inputs_json TEXT,
                reconstruction_marker TEXT
            )
            """
        )
        c.execute(
            """
            CREATE TABLE reconstruction_audit(
                source_path TEXT,
                source_row_id TEXT,
                operation_id TEXT,
                operation_recovered INTEGER,
                reconstruction_source TEXT,
                reconstruction_mode TEXT,
                reconstructed_at INTEGER
            )
            """
        )
        for row in source_rows:
            if row["kind"] == "evidence":
                c.execute(
                    "INSERT INTO normalized_evidence_records(evidence_id,payload_json,observed_at,source_row_id,reconstruction_marker) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        f"rec_evidence_{row['source_row_id']}",
                        json.dumps(row["payload"], sort_keys=True, separators=(",", ":")),
                        row["observed_at"],
                        row["source_row_id"],
                        row["reconstruction_marker"],
                    ),
                )
            else:
                c.execute(
                    "INSERT INTO primitive_observations(primitive_id,primitive_type,output_payload_json,observed_at,window_start,window_end,"
                    "source_row_id,subjects_json,parameters_json,missing_inputs_json,reconstruction_marker) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        f"rec_primitive_{row['source_row_id']}",
                        row["primitive_type"],
                        json.dumps(row["payload"], sort_keys=True, separators=(",", ":")),
                        row["observed_at"],
                        row["window_start"],
                        row["window_end"],
                        row["source_row_id"],
                        row["subjects_json"],
                        row["parameters_json"],
                        row["missing_inputs_json"],
                        row["reconstruction_marker"],
                    ),
                )
        c.execute(
            "INSERT INTO reconstruction_audit(source_path,source_row_id,operation_id,operation_recovered,reconstruction_source,reconstruction_mode,reconstructed_at) "
            "VALUES (?,?,?,?,?,?, strftime('%s','now'))",
            (
                source_path,
                row["source_row_id"],
                row["operation_id"],
                1 if row["operation_recovered"] else 0,
                row["reconstruction_source"],
                row["reconstruction_mode"],
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return str(out_path)


def _reconstruct_candidate_rows(
    *, source_path: str, row_ceiling: int, source_identity: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], bool]:
    src = Path(source_path)
    if not src.exists():
        return [], [], [{"blocker": RECONSTRUCTION_BLOCKER_SOURCE_MISSING, "source_path": source_path}], True

    live_identity = _file_identity(src)
    drifted = any(live_identity.get(key) != source_identity.get(key) for key in source_identity.keys() if isinstance(source_identity.get(key), int))
    source_rows = _collect_reconstruction_rows(source_path=src, row_ceiling=max(1, int(row_ceiling)))
    if not source_rows:
        return [], [], [{"blocker": RECONSTRUCTION_BLOCKER_NO_ROWS, "source_path": source_path}], drifted

    reconstructed_rows: list[dict[str, Any]] = []
    for row in source_rows:
        payload = _safe_json(json.dumps(row["payload"]) if not isinstance(row["payload"], str) else row["payload"]) or {}
        if not isinstance(payload, Mapping):
            payload = {}
        subjects = _sorted_subjects(payload)
        operation_id = _derive_operation_id(
            source_path=source_path,
            fallback_event_time=row.get("observed_at"),
            evidence_id=row.get("source_row_id") or "",
            payload=payload,
            subjects=subjects,
        )
        row["operation_id"] = operation_id
        row["operation_recovered"] = "operation_id" not in payload
        row["reconstruction_source"] = "H7R_LEGACY_SOURCE_REBIND"
        row["reconstruction_mode"] = "local_topology_and_mechanism_rebind"
        row["operation_subject_count"] = len(subjects)
        row["reconstruction_marker"] = row["reconstruction_source"]
        row["payload"]["operation_id"] = operation_id
        # Preserve all original evidence bytes while adding the missing boundary
        # field only when derivable.
        if row["kind"] == "evidence":
            reconstructed_rows.append(row)
        else:
            reconstructed_rows.append(row)

    diagnostics = []
    for row in reconstructed_rows[:]:
        payload = row["payload"]
        if not isinstance(payload, Mapping):
            payload = {}
        subject_ok = _topology_fields_present(payload, subjects=_sorted_subjects(payload))
        mechanism_ok = _mechanism_fields_present(payload)
        event_ok = _event_fields_present(payload)
        required_ok = bool(row.get("operation_id"))
        if not (subject_ok and mechanism_ok and event_ok and required_ok):
            diagnostics.append(
                {
                    "source_row_id": row["source_row_id"],
                    "reason": "INCOMPLETE_OPERATION_BOUNDARY_AFTER_REBIND",
                    "operation_recovered": bool(row["operation_recovered"]),
                }
            )
    return reconstructed_rows, source_rows, diagnostics, drifted


def qualify_h7b_reconstruction_capture(
    *,
    h7r_artifact: Mapping[str, Any],
    maximum_candidates: int = 40,
    row_ceiling_default: int = 200,
    destination: str | Path | None = None,
) -> dict[str, Any]:
    if not isinstance(h7r_artifact, Mapping):
        raise Psi0hH7BBoundedHistoricalReconstructionCaptureError("PSI0H_H7B_H7R_ARTIFACT_INVALID")
    if h7r_artifact.get("schema_version") != H7R_SCHEMA_VERSION:
        raise Psi0hH7BBoundedHistoricalReconstructionCaptureError("PSI0H_H7B_H7R_SCHEMA_MISMATCH")
    if h7r_artifact.get("status") != "PASS":
        raise Psi0hH7BBoundedHistoricalReconstructionCaptureError("PSI0H_H7B_H7R_STATUS_INVALID")
    if not h7r_artifact.get("diagnostics"):
        raise Psi0hH7BBoundedHistoricalReconstructionCaptureError("PSI0H_H7B_H7R_NO_DIAGNOSTICS")

    if os.environ.get(AUTHORIZATION_ENV) != AUTHORIZATION_VALUE:
        raise Psi0hH7BBoundedHistoricalReconstructionCaptureError(RECONSTRUCTION_BLOCKER_AUTH_MISSING)
    if not destination:
        destination = Path("docs/audits/psi0h_h7b_bounded_reconstruction_capture")
    destination_root = Path(destination)
    output_path = destination_root / "psi0h_h7b_bounded_historical_reconstruction_capture.json"
    if output_path.exists():
        # One shot capture only for artifact path.
        raise Psi0hH7BBoundedHistoricalReconstructionCaptureError(RECONSTRUCTION_BLOCKER_OUTPUT_EXISTS)

    destination_root.mkdir(parents=True, exist_ok=True)

    diagnostics_in: list[dict[str, Any]] = h7r_artifact.get("diagnostics", [])
    if not isinstance(diagnostics_in, list):
        raise Psi0hH7BBoundedHistoricalReconstructionCaptureError("PSI0H_H7B_H7R_DIAGNOSTICS_INVALID")

    executions: list[dict[str, Any]] = []
    reconstructed_sources: list[dict[str, Any]] = []
    overall_blockers: list[str] = []
    reconstructable_count = 0
    requires_backfill_count = 0
    candidate_only_count = 0

    selected = diagnostics_in[:max(0, int(maximum_candidates))]
    for idx, diag in enumerate(selected):
        source_path = str(diag.get("source_path", ""))
        source_identity = diag.get("source_identity", {})
        row_ceiling = int(diag.get("row_reconstruction_ceiling") or row_ceiling_default)
        if row_ceiling <= 0:
            row_ceiling = row_ceiling_default

        rec_rows, source_rows, row_blockers, drifted = _reconstruct_candidate_rows(
            source_path=source_path, row_ceiling=row_ceiling, source_identity=source_identity if isinstance(source_identity, Mapping) else {}
        )
        execution_summary = {
            "source_path": source_path,
            "source_identity": source_identity if isinstance(source_identity, Mapping) else {},
            "row_reconstruction_ceiling": row_ceiling,
            "reconstructed_row_count": len(rec_rows),
            "source_row_sample": min(len(source_rows), row_ceiling),
            "source_identity_drift": drifted,
            "row_blockers": row_blockers,
            "reconstructed_store_path": None,
            "outcome": OUTCOME_CANDIDATE_ONLY_NOT_RECONSTRUCTABLE,
            "missing_boundary_fields_recovered": [],
        }

        if drifted:
            execution_summary["outcome"] = OUTCOME_REQUIRES_BOUNDED_HISTORICAL_BACKFILL
            execution_summary["recovery_blockers"] = [RECONSTRUCTION_BLOCKER_SOURCE_CHANGED]
            requires_backfill_count += 1
            overall_blockers.append(RECONSTRUCTION_BLOCKER_SOURCE_CHANGED)

        if not source_rows:
            execution_summary["outcome"] = OUTCOME_CANDIDATE_ONLY_NOT_RECONSTRUCTABLE
            execution_summary["recovery_blockers"] = [RECONSTRUCTION_BLOCKER_NO_ROWS]
            candidate_only_count += 1
            overall_blockers.append(RECONSTRUCTION_BLOCKER_NO_ROWS)
        elif not row_blockers:
            operation_ids = sorted({r["operation_id"] for r in rec_rows if isinstance(r, Mapping) and r.get("operation_id")})
            if operation_ids:
                store_path = _write_reconstruction_store(
                    destination_root=destination_root / "stores",
                    source_path=source_path,
                    source_rows=rec_rows,
                )
                execution_summary["reconstructed_store_path"] = store_path
                execution_summary["outcome"] = OUTCOME_RECONSTRUCTABLE_OPERATION_SOURCE
                execution_summary["reconstructed_operation_count"] = len(operation_ids)
                execution_summary["reconstructed_operations"] = operation_ids
                execution_summary["missing_boundary_fields_recovered"] = ["operation_id"]
                reconstructable_count += 1
            else:
                execution_summary["outcome"] = OUTCOME_CANDIDATE_ONLY_NOT_RECONSTRUCTABLE
                execution_summary["recovery_blockers"] = ["MISSING_OPERATION_RECONSTRUCTION"]
                candidate_only_count += 1
                overall_blockers.append("MISSING_OPERATION_RECONSTRUCTION")
        else:
            execution_summary["outcome"] = OUTCOME_REQUIRES_BOUNDED_HISTORICAL_BACKFILL
            execution_summary["recovery_blockers"] = [b["reason"] for b in row_blockers]
            execution_summary["missing_boundary_fields_recovered"] = []
            requires_backfill_count += 1
            overall_blockers.append("BOUNDARY_PARTIAL")

        reconstructed_sources.append({
            "source_path": source_path,
            "row_reconstruction_ceiling": row_ceiling,
            "source_identity": source_identity if isinstance(source_identity, Mapping) else {},
            "source_class": SOURCE_CLASS_LEGACY_CANDIDATE_ONLY,
            "candidate_index": idx,
            "execution": execution_summary,
        })
        executions.append(execution_summary)

    if reconstructable_count:
        status = "PASS"
        verdict = VERDICT_READY_BOUNDARY_CAPTURE
        next_decision = "RERUN_H7_WITH_RECONSTRUCTED_SOURCES"
        next_instruction = (
            "At least one legacy source has reconstructed operation-boundary fields. "
            "Use reconstructed store paths as source candidates for next H7 capture."
        )
        stop_conditions = []
    elif reconstructed_sources:
        status = "HOLD"
        verdict = VERDICT_HOLD_RECONSTRUCTION_REQUIRED
        next_decision = "EXTEND_PROVIDER_OR_SOURCE_RECONSTRUCTION"
        next_instruction = (
            "No source was fully reconstructable without additional evidence. Preserve candidate list and bounds for explicit next-bound acquisition."
        )
        stop_conditions = ["NO_RECONSTRUCTABLE_OPERATION_BOUNDARY"]
    else:
        status = "HOLD"
        verdict = VERDICT_HOLD_PARTIAL_RECONSTRUCTION
        next_decision = "INVALID_H7R_INPUT"
        next_instruction = "H7R artifact did not contain usable legacy candidates."
        stop_conditions = ["H7R_DIAGNOSTICS_EMPTY_OR_INVALID"]

    result = {
        "schema_version": SCHEMA_VERSION,
        "run_id": RUN_ID,
        "milestone": "PSI0H-H7B",
        "status": status,
        "verdict": verdict,
        "h7r_artifact": h7r_artifact.get("artifact_digest"),
        "h7r_status": h7r_artifact.get("status"),
        "h7r_verdict": h7r_artifact.get("verdict"),
        "reconstruction": {
            "destination_root": str(destination_root),
            "selected_legacy_candidate_count": len(reconstructed_sources),
            "classifications": {
                OUTCOME_RECONSTRUCTABLE_OPERATION_SOURCE: reconstructable_count,
                OUTCOME_REQUIRES_BOUNDED_HISTORICAL_BACKFILL: requires_backfill_count,
                OUTCOME_CANDIDATE_ONLY_NOT_RECONSTRUCTABLE: candidate_only_count,
            },
            "reconstructed_sources": reconstructed_sources,
            "execution": executions,
            "stop_conditions": sorted(set(stop_conditions)),
            "max_candidates": int(maximum_candidates),
            "row_ceiling_default": int(row_ceiling_default),
        },
        "reconstructable_source_count": reconstructable_count,
        "source_plan": {
            "candidate_count": len(reconstructed_sources),
            "reconstructable_source_count": reconstructable_count,
            "legacy_source_rows": len(reconstructed_sources),
            "reconstructed_source_rows": [
                {
                    "source_path": item["execution"]["reconstructed_store_path"],
                    "source_identity": item.get("source_identity", {}),
                    "row_reconstruction_ceiling": item["row_reconstruction_ceiling"],
                    "source_class": "RECONSTRUCTABLE_OPERATION_SOURCE" if item["execution"]["outcome"] == OUTCOME_RECONSTRUCTABLE_OPERATION_SOURCE else item["execution"]["outcome"],
                    "reconstructable": item["execution"]["outcome"] == OUTCOME_RECONSTRUCTABLE_OPERATION_SOURCE,
                }
                for item in reconstructed_sources
                if item["execution"]["outcome"] == OUTCOME_RECONSTRUCTABLE_OPERATION_SOURCE
            ],
        },
        "authority": dict(AUTHORITY),
        "scope": {
            "source_read": True,
            "provider_access": False,
            "comparison": False,
            "monitoring": False,
            "candidate_generation": False,
            "candidate_disposition": False,
            "activation": False,
            "policy": False,
            "ranking": False,
        },
        "next_action": {
            "decision": next_decision,
            "instruction": next_instruction,
            "required_authorization": "NONE",
        },
        "blockers": sorted(set(overall_blockers)),
    }

    if destination is not None:
        result["artifact_path"] = str(destination_root / "psi0h_h7b_bounded_historical_reconstruction_capture.json")
    result["artifact_digest"] = _digest(result)
    return result


def verify_h7b_reconstruction_capture(record: Mapping[str, Any]) -> bool:
    if not isinstance(record, Mapping):
        raise Psi0hH7BBoundedHistoricalReconstructionCaptureError("PSI0H_H7B_RECORD_INVALID")
    if record.get("schema_version") != SCHEMA_VERSION:
        raise Psi0hH7BBoundedHistoricalReconstructionCaptureError("PSI0H_H7B_RECORD_SCHEMA_INVALID")
    digest = str(record.get("artifact_digest", ""))
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise Psi0hH7BBoundedHistoricalReconstructionCaptureError("PSI0H_H7B_RECORD_DIGEST_INVALID")
    replay = dict(record)
    replay.pop("artifact_digest")
    if _digest(replay) != digest:
        raise Psi0hH7BBoundedHistoricalReconstructionCaptureError("PSI0H_H7B_RECORD_DIGEST_MISMATCH")
    if record.get("status") not in {"PASS", "HOLD"}:
        raise Psi0hH7BBoundedHistoricalReconstructionCaptureError("PSI0H_H7B_RECORD_STATUS_INVALID")
    if any(record.get("authority", {}).values()):
        raise Psi0hH7BBoundedHistoricalReconstructionCaptureError("PSI0H_H7B_RECORD_AUTHORITY_EXPANDED")
    return True
