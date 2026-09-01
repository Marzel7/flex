"""PSI0H-H7C bounded operation-boundary evidence reconstruction boundary.

This boundary takes PSI0H-H7R legacy candidate diagnostics and attempts a
second-pass, boundary-level reconstruction focused only on missing operation-boundary
fields needed for H7/H8 eligibility.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

from src.evidence.contracts.psi0h_h7b_bounded_historical_reconstruction_capture import (
    SCHEMA_VERSION as _H7B_SCHEMA_VERSION,
    _collect_reconstruction_rows as _collect_rows,
)

SCHEMA_VERSION = "psi0h-h7c.operation-boundary-reconstruction.v1"
RUN_ID = "psi0h-h7c-operation-boundary-reconstruction"
H7R_SCHEMA_VERSION = "psi0h-h7r.legacy-candidate-reconstruction-reconciliation.v1"
AUTHORIZATION_ENV = "PSI0H_H7C_OPERATION_BOUNDARY_AUTHORIZED"
AUTHORIZATION_VALUE = "1"

OUTCOME_RECONSTRUCTABLE_OPERATION_BOUNDARY = "RECONSTRUCTABLE_OPERATION_BOUNDARY"
OUTCOME_REQUIRES_BOUNDED_OP_BOUNDARY_BACKFILL = "REQUIRES_BOUNDED_HISTORICAL_OPERATION_BOUNDARY_BACKFILL"
OUTCOME_CANDIDATE_ONLY_NOT_RECONSTRUCTABLE = "CANDIDATE_ONLY_NOT_RECONSTRUCTABLE"

BLOCKER_H7C_AUTH_MISSING = "H7C_AUTHORIZATION_MISSING"
BLOCKER_H7C_H7R_SCHEMA_MISMATCH = "H7C_H7R_SCHEMA_MISMATCH"
BLOCKER_H7C_H7R_STATUS_INVALID = "H7C_H7R_STATUS_INVALID"
BLOCKER_H7C_H7R_DIAGNOSTICS_INVALID = "H7C_H7R_DIAGNOSTICS_INVALID"
BLOCKER_H7C_SOURCE_MISSING = "H7C_SOURCE_PATH_MISSING"
BLOCKER_H7C_OUTPUT_EXISTS = "H7C_OUTPUT_EXISTS"
BLOCKER_H7C_NO_ROWS = "H7C_NO_ROWS_FOR_BOUNDARY_RECONSTRUCTION"
BLOCKER_H7C_SOURCE_CHANGED = "H7C_SOURCE_IDENTITY_DRIFT"
BLOCKER_H7C_RECORD_SCHEMA_INVALID = "H7C_RECORD_SCHEMA_INVALID"
BLOCKER_H7C_RECORD_DIGEST_MISMATCH = "H7C_RECORD_DIGEST_MISMATCH"
BLOCKER_H7C_RECORD_INVALID = "H7C_RECORD_INVALID"
BLOCKER_H7C_RECORD_AUTHORITY_EXPANDED = "H7C_RECORD_AUTHORITY_EXPANDED"
BLOCKER_H7C_RECORD_STATUS_INVALID = "H7C_RECORD_STATUS_INVALID"
BLOCKER_H7C_RECORD_INVALID_INPUT = "H7C_RECORD_INVALID_INPUT"

VERDICT_READY_BOUNDARY_RECONSTRUCTION = "H7C_READY_OPERATION_BOUNDARY_RECONSTRUCTION"
VERDICT_HOLD_PARTIAL_RECONSTRUCTION = "H7C_PARTIAL_OPERATION_BOUNDARY_RECONSTRUCTION"
VERDICT_HOLD_RECONSTRUCTION_REQUIRED = "H7C_RECONSTRUCTION_REQUIRED"

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


class Psi0hH7COperationBoundaryReconstructionError(RuntimeError):
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


def _sorted_subjects(payload: Mapping[str, Any]) -> list[str]:
    keys = ("source", "destination", "wallet", "wallets", "funder", "recipient", "creator")
    subjects: list[str] = []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str):
            subjects.append(value)
        elif isinstance(value, (list, tuple)):
            subjects.extend([x for x in value if isinstance(x, str)])
    if isinstance(payload.get("roles"), Mapping):
        for val in payload["roles"].values():
            if isinstance(val, str):
                subjects.append(val)
    return sorted({v for v in subjects if v})


def _collect_candidate_field(values: list[Any]) -> list[Any]:
    counter = Counter([v for v in values if v not in (None, "", [])])
    if not counter:
        return []
    return counter.most_common()


def _pick_most_common_string(counter: Counter[str]) -> str:
    if not counter:
        return ""
    value, _ = counter.most_common(1)[0]
    return str(value)


def _derive_profile(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    op_ids: list[str] = []
    mechanisms: list[Any] = []
    windows: list[tuple[int | None, int | None]] = []
    subjects = []
    funders = []
    recipients = []
    roles: list[Any] = []
    sources = []
    destinations = []
    wallets = []
    event_types = []
    missing: list[str] = []

    for row in rows:
        payload = _safe_json(row.get("payload")) or {}
        if isinstance(payload, Mapping):
            op = payload.get("operation_id") or payload.get("operation")
            if isinstance(op, str) and op:
                op_ids.append(op)
            if payload.get("mechanism"):
                mechanisms.append(payload.get("mechanism"))
            ws = payload.get("window")
            if isinstance(ws, Mapping):
                windows.append(
                    (
                        int(ws["start"]) if isinstance(ws.get("start"), int) else None,
                        int(ws.get("end")) if isinstance(ws.get("end"), int) else None,
                    )
                )
            elif isinstance(payload.get("window_start"), int) or isinstance(payload.get("window_end"), int):
                windows.append((payload.get("window_start"), payload.get("window_end")))
            if payload.get("source"):
                sources.append(payload.get("source"))
            if payload.get("destination"):
                destinations.append(payload.get("destination"))
            if payload.get("wallet"):
                wallets.append(payload.get("wallet"))
            fw = payload.get("funder")
            rw = payload.get("recipient")
            if isinstance(fw, str) and fw:
                funders.append(fw)
            if isinstance(rw, str) and rw:
                recipients.append(rw)
            if isinstance(payload.get("roles"), Mapping):
                roles.append(payload.get("roles"))
            et = payload.get("event_types")
            if isinstance(et, list):
                event_types.extend(et)
            elif isinstance(et, str) and et:
                event_types.append(et)

    profile: dict[str, Any] = {}
    if op_ids:
        profile["operation_id"] = _pick_most_common_string(Counter(op_ids))
    if mechanisms:
        profile["mechanism"] = _pick_most_common_string(Counter(mechanisms))
    if sources:
        profile["source"] = _pick_most_common_string(Counter(sources))
    if destinations:
        profile["destination"] = _pick_most_common_string(Counter(destinations))
    if wallets:
        profile["wallet"] = _pick_most_common_string(Counter(wallets))
    if funders:
        profile["funder"] = _pick_most_common_string(Counter(funders))
    if recipients:
        profile["recipient"] = _pick_most_common_string(Counter(recipients))
    if event_types:
        profile["event_types"] = sorted(set(str(x) for x in event_types if isinstance(x, str)))
    if roles:
        merged_roles = {}
        for role_map in roles:
            if isinstance(role_map, Mapping):
                merged_roles.update({k: v for k, v in role_map.items() if isinstance(k, str)})
        if merged_roles:
            profile["roles"] = merged_roles

    if windows:
        starts = [w[0] for w in windows if isinstance(w[0], int)]
        ends = [w[1] for w in windows if isinstance(w[1], int)]
        profile["window"] = {}
        if starts:
            profile["window"]["start"] = min(starts)
        if ends:
            profile["window"]["end"] = max(ends)

    # Boundary quality hints used only to short-circuit when profile is too weak.
    if not op_ids:
        missing.append("operation_id")
    if not mechanisms:
        missing.append("mechanism")
    if not (sources or wallets or funders or recipients):
        missing.append("topology_seed")
    if not (windows or sources or destinations or funders or recipients):
        missing.append("event_window")
    return profile, missing


def _reconstructed_row_payload(payload: Any, *, profile: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(payload, Mapping):
        payload = {}
    else:
        payload = dict(payload)
    recovered: list[str] = []

    if not payload.get("operation_id") and profile.get("operation_id"):
        payload["operation_id"] = profile["operation_id"]
        recovered.append("operation_id")
    if not payload.get("mechanism") and profile.get("mechanism"):
        payload["mechanism"] = profile["mechanism"]
        recovered.append("mechanism")
    if not payload.get("source") and profile.get("source"):
        payload["source"] = profile["source"]
        recovered.append("source")
    if not payload.get("destination") and profile.get("destination"):
        payload["destination"] = profile["destination"]
        recovered.append("destination")
    if not payload.get("funder") and profile.get("funder"):
        payload["funder"] = profile["funder"]
        recovered.append("funder")
    if not payload.get("recipient") and profile.get("recipient"):
        payload["recipient"] = profile["recipient"]
        recovered.append("recipient")
    if not payload.get("roles") and profile.get("roles"):
        payload["roles"] = profile["roles"]
        recovered.append("roles")
    if not payload.get("event_types") and profile.get("event_types"):
        payload["event_types"] = profile["event_types"]
        recovered.append("event_types")
    if (not payload.get("window")) and isinstance(profile.get("window"), Mapping):
        payload["window"] = profile["window"]
        recovered.append("window")
    elif payload.get("window") and isinstance(payload.get("window"), Mapping):
        # ensure event window has at least start/end when available from profile
        if "start" not in payload["window"] and profile.get("window", {}).get("start") is not None:
            payload["window"]["start"] = profile["window"]["start"]
            recovered.append("window.start")
        if "end" not in payload["window"] and profile.get("window", {}).get("end") is not None:
            payload["window"]["end"] = profile["window"]["end"]
            recovered.append("window.end")

    return payload, recovered


def _row_boundary_complete(payload: Mapping[str, Any]) -> bool:
    if not isinstance(payload, Mapping):
        return False
    if not payload.get("operation_id"):
        return False
    if not any(k in payload for k in ("source", "destination", "wallet", "roles", "funder", "recipient")):
        return False
    if "roles" not in payload and not (payload.get("funder") and payload.get("recipient")):
        return False
    if not payload.get("mechanism") and not payload.get("event_types"):
        return False
    if not isinstance(payload.get("window"), Mapping) and not (
        isinstance(payload.get("window_start"), int) and isinstance(payload.get("window_end"), int)
    ):
        return False
    if not any(k in payload for k in ("observed_at", "block_time", "event_time")):
        return False
    return True


def _write_boundary_store(*, destination_root: Path, source_path: str, source_rows: list[dict[str, Any]]) -> str:
    destination_root.mkdir(parents=True, exist_ok=True)
    source_leaf = Path(source_path).name or "source"
    out_path = destination_root / f"boundary_reconstructed_{source_leaf}"
    if out_path.suffix != ".db":
        out_path = out_path.with_suffix(".db")
    if out_path.exists():
        raise Psi0hH7COperationBoundaryReconstructionError("PSI0H_H7C_OUTPUT_REPLACEMENT_ATTEMPTED")

    conn = sqlite3.connect(out_path)
    try:
        c = conn.cursor()
        c.execute(
            """
            CREATE TABLE reconstructed_boundary_rows(
                source_row_id TEXT PRIMARY KEY,
                evidence_payload_json TEXT,
                primitive_payload_json TEXT,
                source_path TEXT,
                operation_id TEXT,
                boundary_complete INTEGER,
                recovered_fields_json TEXT,
                reconstructed_at INTEGER
            )
            """
        )
        for row in source_rows:
            payload = _safe_json(row.get("payload")) or {}
            payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            if row.get("kind") == "evidence":
                c.execute(
                    "INSERT INTO reconstructed_boundary_rows(source_row_id,evidence_payload_json,primitive_payload_json,source_path,operation_id,boundary_complete,recovered_fields_json,reconstructed_at)"
                    " VALUES (?,?,?,?,?,?,?, strftime('%s','now'))",
                    (
                        f"evidence::{row['source_row_id']}",
                        payload_json,
                        None,
                        source_path,
                        str(payload.get("operation_id", "")),
                        1 if _row_boundary_complete(payload) else 0,
                        json.dumps(row.get("recovered_fields", []), sort_keys=True),
                    ),
                )
            else:
                c.execute(
                    "INSERT INTO reconstructed_boundary_rows(source_row_id,evidence_payload_json,primitive_payload_json,source_path,operation_id,boundary_complete,recovered_fields_json,reconstructed_at)"
                    " VALUES (?,?,?,?,?,?,?, strftime('%s','now'))",
                    (
                        f"primitive::{row['source_row_id']}",
                        None,
                        payload_json,
                        source_path,
                        str(payload.get("operation_id", "")),
                        1 if _row_boundary_complete(payload) else 0,
                        json.dumps(row.get("recovered_fields", []), sort_keys=True),
                    ),
                )
        conn.commit()
    finally:
        conn.close()
    return str(out_path)


def _file_identity(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {
        "device": int(stat.st_dev),
        "inode": int(stat.st_ino),
        "size_bytes": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def qualify_h7c_operation_boundary_reconstruction(
    *,
    h7r_artifact: Mapping[str, Any],
    maximum_candidates: int = 40,
    row_ceiling_default: int = 200,
    destination: str | Path | None = None,
) -> dict[str, Any]:
    if not isinstance(h7r_artifact, Mapping):
        raise Psi0hH7COperationBoundaryReconstructionError("H7C_H7R_ARTIFACT_INVALID")
    if h7r_artifact.get("schema_version") != H7R_SCHEMA_VERSION:
        raise Psi0hH7COperationBoundaryReconstructionError(BLOCKER_H7C_H7R_SCHEMA_MISMATCH)
    if h7r_artifact.get("status") != "PASS":
        raise Psi0hH7COperationBoundaryReconstructionError(BLOCKER_H7C_H7R_STATUS_INVALID)

    diagnostics = h7r_artifact.get("diagnostics")
    if not isinstance(diagnostics, list):
        raise Psi0hH7COperationBoundaryReconstructionError(BLOCKER_H7C_H7R_DIAGNOSTICS_INVALID)

    if os.environ.get(AUTHORIZATION_ENV) != AUTHORIZATION_VALUE:
        raise Psi0hH7COperationBoundaryReconstructionError(BLOCKER_H7C_AUTH_MISSING)

    if not destination:
        destination = Path("docs/audits/psi0h_h7c_operation_boundary_reconstruction")
    destination_root = Path(destination)
    artifact_path = destination_root / "psi0h_h7c_bounded_operation_boundary_reconstruction.json"
    if artifact_path.exists():
        raise Psi0hH7COperationBoundaryReconstructionError(BLOCKER_H7C_OUTPUT_EXISTS)

    selected = diagnostics[: max(0, int(maximum_candidates))]
    executions: list[dict[str, Any]] = []
    source_plan_rows: list[dict[str, Any]] = []
    reconstructable_count = 0
    requires_backfill_count = 0
    candidate_only_count = 0
    overall_blockers: list[str] = []

    for idx, diag in enumerate(selected):
        if not isinstance(diag, Mapping):
            candidate_only_count += 1
            continue
        source_path = str(diag.get("source_path", "") or "")
        source_identity = diag.get("source_identity", {})
        row_ceiling = int(diag.get("row_reconstruction_ceiling") or row_ceiling_default)
        if row_ceiling <= 0:
            row_ceiling = row_ceiling_default

        if not source_path:
            execution = {
                "source_path": source_path,
                "outcome": OUTCOME_CANDIDATE_ONLY_NOT_RECONSTRUCTABLE,
                "recovery_blockers": [BLOCKER_H7C_SOURCE_MISSING],
                "source_row_sample": 0,
            }
            requires_backfill_count += 1
            overall_blockers.append(BLOCKER_H7C_SOURCE_MISSING)
            source_plan_rows.append({"source_path": source_path, "candidate_index": idx, "outcome": execution["outcome"]})
            executions.append(execution)
            continue

        src = Path(source_path)
        source_rows = _collect_rows(source_path=source_path, row_ceiling=max(1, int(row_ceiling)))
        if not source_rows:
            execution = {
                "source_path": source_path,
                "outcome": OUTCOME_CANDIDATE_ONLY_NOT_RECONSTRUCTABLE,
                "recovery_blockers": [BLOCKER_H7C_NO_ROWS],
                "source_row_sample": 0,
            }
            candidate_only_count += 1
            overall_blockers.append(BLOCKER_H7C_NO_ROWS)
            executions.append(execution)
            source_plan_rows.append({"source_path": source_path, "candidate_index": idx, "outcome": execution["outcome"]})
            continue

        if isinstance(source_identity, Mapping):
            current_identity = _file_identity(src)
            drifted = any(
                isinstance(current_identity.get(k), int) and current_identity.get(k) != source_identity.get(k)
                for k in ("size_bytes", "inode", "mtime_ns", "device")
            )
        else:
            current_identity = _file_identity(src)
            source_identity = {}
            drifted = False

        if drifted:
            execution = {
                "source_path": source_path,
                "outcome": OUTCOME_REQUIRES_BOUNDED_OP_BOUNDARY_BACKFILL,
                "recovery_blockers": [BLOCKER_H7C_SOURCE_CHANGED],
                "source_row_sample": min(len(source_rows), row_ceiling),
                "source_identity_drift": True,
            }
            requires_backfill_count += 1
            overall_blockers.append(BLOCKER_H7C_SOURCE_CHANGED)
            executions.append(execution)
            source_plan_rows.append({"source_path": source_path, "candidate_index": idx, "outcome": execution["outcome"]})
            continue

        profile, missing_profile = _derive_profile(source_rows)
        reconstructed_rows = []
        recovered_rows = []
        for row in source_rows:
            payload, recovered_fields = _reconstructed_row_payload(_safe_json(row.get("payload")) or {}, profile=profile)
            row["payload"] = payload
            row["recovered_fields"] = recovered_fields
            reconstructed_rows.append(row)
            if recovered_fields:
                recovered_rows.append(row)

        complete = 0
        for row in reconstructed_rows:
            if _row_boundary_complete(_safe_json(row.get("payload")) or {}):
                complete += 1
        if not profile.get("operation_id"):
            missing_profile.append("operation_id")

        if complete and not missing_profile:
            store_path = _write_boundary_store(
                destination_root=destination_root / "stores",
                source_path=source_path,
                source_rows=reconstructed_rows,
            )
            reconstructable_count += 1
            execution = {
                "source_path": source_path,
                "source_identity": source_identity,
                "source_identity_drift": False,
                "row_reconstruction_ceiling": row_ceiling,
                "source_row_sample": min(len(source_rows), row_ceiling),
                "outcome": OUTCOME_RECONSTRUCTABLE_OPERATION_BOUNDARY,
                "reconstructed_store_path": store_path,
                "recovered_boundary_fields": sorted({f for row in reconstructed_rows for f in row.get("recovered_fields", [])}),
                "boundary_complete_rows": complete,
                "row_count": len(source_rows),
            }
        else:
            missing = sorted(set(missing_profile + [f"ROW_COMPLETENESS_{complete}/{len(source_rows)}"]))
            requires_backfill_count += 1
            execution = {
                "source_path": source_path,
                "source_identity": source_identity,
                "source_identity_drift": False,
                "row_reconstruction_ceiling": row_ceiling,
                "source_row_sample": min(len(source_rows), row_ceiling),
                "outcome": OUTCOME_REQUIRES_BOUNDED_OP_BOUNDARY_BACKFILL,
                "recovery_blockers": [f"MISSING_OPERATION_BOUNDARY_{x}" for x in missing],
                "boundary_complete_rows": complete,
                "row_count": len(source_rows),
            }
            overall_blockers.append("MISSING_OPERATION_BOUNDARY")
            if candidate_only_count < 0:
                candidate_only_count = 0

        source_plan_rows.append(
            {
                "source_path": source_path,
                "candidate_index": idx,
                "outcome": execution["outcome"],
                "source_row_count": len(source_rows),
                "row_reconstruction_ceiling": row_ceiling,
                "candidate_only_recovered_count": len(recovered_rows),
            }
        )
        executions.append(execution)

    if reconstructable_count:
        status = "PASS"
        verdict = VERDICT_READY_BOUNDARY_RECONSTRUCTION
        next_decision = "RERUN_H7_WITH_BOUNDARY_STORES"
        next_instruction = (
            "At least one legacy candidate now has complete operation-boundary reconstruction. "
            "Use the reconstructed boundary stores for H7/H8 progression."
        )
        stop_conditions: list[str] = []
    elif source_plan_rows:
        status = "HOLD"
        verdict = VERDICT_HOLD_RECONSTRUCTION_REQUIRED
        next_decision = "LOCATE_OPERATION_BOUNDARY_SOURCE_CAPTURE"
        next_instruction = (
            "No source reached operation-boundary completeness. Preserve all candidate rows and blockers for a narrower backfill/capture pass."
        )
        stop_conditions = ["NO_RECONSTRUCTABLE_OPERATION_BOUNDARY"]
        candidate_only_count = len(source_plan_rows) - reconstructable_count - requires_backfill_count
    else:
        status = "HOLD"
        verdict = VERDICT_HOLD_PARTIAL_RECONSTRUCTION
        next_decision = "INVALID_H7R_INPUT"
        next_instruction = "H7R artifact did not contain usable legacy candidates."
        stop_conditions = ["H7R_DIAGNOSTICS_EMPTY_OR_INVALID"]

    result = {
        "schema_version": SCHEMA_VERSION,
        "run_id": RUN_ID,
        "milestone": "PSI0H-H7C",
        "status": status,
        "verdict": verdict,
        "h7r_artifact": h7r_artifact.get("artifact_digest"),
        "h7r_status": h7r_artifact.get("status"),
        "h7r_verdict": h7r_artifact.get("verdict"),
        "source_plan": {
            "candidate_count": len(source_plan_rows),
            "legacy_source_rows": len(source_plan_rows),
            "row_ceiling_default": int(row_ceiling_default),
            "max_candidates": int(maximum_candidates),
            "reconstructable_source_count": reconstructable_count,
            "reconstructed_source_rows": [
                {
                    "source_path": item["source_path"],
                    "source_identity": source_identity,
                    "source_row_count": item.get("source_row_count"),
                    "source_row_reconstruction_ceiling": item.get("row_reconstruction_ceiling", row_ceiling_default),
                }
                for item, source_identity in zip(
                    source_plan_rows,
                    [diag.get("source_identity", {}) for diag in selected],
                )
                if item["outcome"] == OUTCOME_RECONSTRUCTABLE_OPERATION_BOUNDARY
            ],
        },
        "reconstruction": {
            "destination_root": str(destination_root),
            "classifications": {
                OUTCOME_RECONSTRUCTABLE_OPERATION_BOUNDARY: reconstructable_count,
                OUTCOME_REQUIRES_BOUNDED_OP_BOUNDARY_BACKFILL: requires_backfill_count,
                OUTCOME_CANDIDATE_ONLY_NOT_RECONSTRUCTABLE: candidate_only_count,
            },
            "execution": executions,
            "stop_conditions": stop_conditions,
            "selected_legacy_candidate_count": len(selected),
            "source_rows_recovered": len(executions),
            "recovered_source_rows": [
                item for item in source_plan_rows if item["outcome"] == OUTCOME_RECONSTRUCTABLE_OPERATION_BOUNDARY
            ],
        },
        "reconstructable_source_count": reconstructable_count,
        "blockers": sorted(set(overall_blockers)),
        "authority": dict(AUTHORITY),
        "scope": {
            "comparison": False,
            "monitoring": False,
            "provider_access": False,
            "source_read": True,
            "candidate_generation": False,
            "candidate_disposition": False,
        },
        "next_action": {
            "decision": next_decision,
            "instruction": next_instruction,
            "required_authorization": "NONE",
        },
    }

    result["artifact_path"] = str(artifact_path)
    result["artifact_digest"] = _digest({k: v for k, v in result.items() if k != "artifact_digest"})
    return result


def verify_h7c_operation_boundary_reconstruction(record: Mapping[str, Any]) -> bool:
    if not isinstance(record, Mapping):
        raise Psi0hH7COperationBoundaryReconstructionError(BLOCKER_H7C_RECORD_INVALID)
    if record.get("schema_version") != SCHEMA_VERSION:
        raise Psi0hH7COperationBoundaryReconstructionError(BLOCKER_H7C_RECORD_SCHEMA_INVALID)

    digest = str(record.get("artifact_digest", ""))
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise Psi0hH7COperationBoundaryReconstructionError(BLOCKER_H7C_RECORD_DIGEST_MISMATCH)
    replay = dict(record)
    replay.pop("artifact_digest")
    if _digest(replay) != digest:
        raise Psi0hH7COperationBoundaryReconstructionError(BLOCKER_H7C_RECORD_DIGEST_MISMATCH)

    if record.get("status") not in {"PASS", "HOLD"}:
        raise Psi0hH7COperationBoundaryReconstructionError(BLOCKER_H7C_RECORD_STATUS_INVALID)
    if any(record.get("authority", {}).values()):
        raise Psi0hH7COperationBoundaryReconstructionError(BLOCKER_H7C_RECORD_AUTHORITY_EXPANDED)
    return True
