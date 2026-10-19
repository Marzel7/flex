"""Deterministic compatibility projection from H8 rows to H4 runtime db artifacts."""

from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping
import sqlite3

from .psi0h_h8_bounded_historical_backfill_execution import SCHEMA_VERSION as H8_SCHEMA_VERSION
from .psi0h_h4_historical_operation_census import _file_identity

SCHEMA_VERSION = "psi0h-h8-to-h4-runtime-projection.v1"
RUN_ID = "psi0h-h8-to-h4-runtime-projection"
H4_MILESTONE = "PSI0G-B"
H4_COMPAT_SCHEMA_VERSION = "1.0.0"
COMPAT_CONTRACT_VERSION = "psi0h-h8-runtime-projection.v1"

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


class Psi0hH8ToH4RuntimeProjectionError(RuntimeError):
    pass


_RUNTIME_SCHEMA = """CREATE TABLE IF NOT EXISTS operation_contract_versions (
    contract_id TEXT NOT NULL,
    contract_version TEXT NOT NULL,
    contract_digest TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    registered_at INTEGER NOT NULL,
    PRIMARY KEY(contract_id, contract_version),
    UNIQUE(contract_digest)
);
CREATE TABLE IF NOT EXISTS operation_contract_activation_events (
    event_id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    contract_version TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    reason TEXT NOT NULL,
    occurred_at INTEGER NOT NULL,
    payload_digest TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS behaviour_observations (
    output_id TEXT PRIMARY KEY, contract_id TEXT NOT NULL, contract_version TEXT NOT NULL,
    producer_version TEXT NOT NULL, input_digest TEXT NOT NULL,
    payload_json TEXT NOT NULL, payload_digest TEXT NOT NULL, generated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS topology_revisions (
    output_id TEXT PRIMARY KEY, contract_id TEXT NOT NULL, contract_version TEXT NOT NULL,
    producer_version TEXT NOT NULL, input_digest TEXT NOT NULL,
    payload_json TEXT NOT NULL, payload_digest TEXT NOT NULL, generated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS detector_inputs (
    output_id TEXT PRIMARY KEY, contract_id TEXT NOT NULL, contract_version TEXT NOT NULL,
    producer_version TEXT NOT NULL, input_digest TEXT NOT NULL,
    payload_json TEXT NOT NULL, payload_digest TEXT NOT NULL, generated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS detector_results (
    output_id TEXT PRIMARY KEY, contract_id TEXT NOT NULL, contract_version TEXT NOT NULL,
    producer_version TEXT NOT NULL, input_digest TEXT NOT NULL,
    payload_json TEXT NOT NULL, payload_digest TEXT NOT NULL, generated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS operation_runtime_references (
    output_type TEXT NOT NULL,
    output_id TEXT NOT NULL,
    reference_type TEXT NOT NULL,
    reference_id TEXT NOT NULL,
    PRIMARY KEY(output_type, output_id, reference_type, reference_id)
);
CREATE TRIGGER IF NOT EXISTS operation_contract_versions_no_update BEFORE UPDATE ON operation_contract_versions
BEGIN SELECT RAISE(ABORT, 'immutable Operation Contract cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS operation_contract_versions_no_delete BEFORE DELETE ON operation_contract_versions
BEGIN SELECT RAISE(ABORT, 'immutable Operation Contract cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS behaviour_observations_no_update BEFORE UPDATE ON behaviour_observations
BEGIN SELECT RAISE(ABORT, 'immutable Behaviour Observation cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS detector_inputs_no_update BEFORE UPDATE ON detector_inputs
BEGIN SELECT RAISE(ABORT, 'immutable Detector Input cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS detector_results_no_update BEFORE UPDATE ON detector_results
BEGIN SELECT RAISE(ABORT, 'immutable Detector Result cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS topology_revisions_no_update BEFORE UPDATE ON topology_revisions
BEGIN SELECT RAISE(ABORT, 'immutable Topology Revision cannot be updated'); END;
CREATE TRIGGER IF NOT EXISTS operation_runtime_references_no_update BEFORE UPDATE ON operation_runtime_references
BEGIN SELECT RAISE(ABORT, 'immutable runtime reference cannot be updated'); END;
"""


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _digest(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


def _to_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _safe_payload(payload: Any) -> Mapping[str, Any]:
    if isinstance(payload, Mapping):
        return payload
    return {}


def _extract_subjects(payload: Mapping[str, Any]) -> list[str]:
    candidate: list[str] = []
    seen: set[str] = set()
    for field in ("subjects", "wallets", "wallet", "creator", "funder", "recipient", "source", "destination"):
        value = payload.get(field)
        if value is None:
            continue
        values = value if isinstance(value, list) else [value]
        for item in values:
            if not isinstance(item, str):
                continue
            item = item.strip()
            if item and item not in seen:
                seen.add(item)
                candidate.append(item)
    return candidate


def _derive_local_role(payload: Mapping[str, Any]) -> str:
    roles = payload.get("roles")
    if isinstance(roles, Mapping):
        for role in ("creator", "funder", "recipient", "source", "destination", "wallet", "signer"):
            value = roles.get(role)
            if isinstance(value, str):
                normal = value.strip()
                if normal:
                    return normal
    for fallback in ("creator", "funder", "recipient", "source", "destination", "wallet"):
        value = payload.get(fallback)
        if isinstance(value, str):
            normal = value.strip()
            if normal:
                return normal
    return ""


def _stable_ref_signature(payload: Mapping[str, Any]) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    mechanism = payload.get("mechanism")
    if isinstance(mechanism, str):
        mechanism = mechanism.strip()
        if mechanism:
            fields.append(("mechanism", mechanism))
    event_types = payload.get("event_types")
    if isinstance(event_types, list):
        normalized = sorted(item.strip() for item in event_types if isinstance(item, str) and item.strip())
        if normalized:
            fields.append(("event_types", "|".join(normalized)))
    source_path = payload.get("source_path")
    if isinstance(source_path, str):
        source_path = source_path.strip()
        if source_path:
            fields.append(("source_path", source_path))
    return fields


def _normalise_id_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value)


def _derive_continuity_refs(payload: Mapping[str, Any]) -> list[str]:
    signature = _stable_ref_signature(payload)
    if not signature:
        return []

    continuity_refs: list[str] = []
    roles = payload.get("roles")
    role_addresses = []
    if isinstance(roles, Mapping):
        for role in ("creator", "funder", "recipient", "destination", "source"):
            value = _normalise_id_value(roles.get(role))
            if value:
                role_addresses.append((role, value))

    wallet = _normalise_id_value(payload.get("wallet"))
    if wallet:
        role_addresses.append(("wallet", wallet))

    for role_name, address in role_addresses:
        continuity_refs.append(
            f"ps0h-h8.continuity.role.v1:{ _digest({'role': role_name, 'address': address, 'signature': signature}) }"
        )

    wallets = sorted(set(_extract_subjects(payload)))
    if len(wallets) >= 2:
        continuity_refs.append(
            f"ps0h-h8.continuity.wallet_pair.v1:{ _digest({'wallets': wallets[:2], 'signature': signature}) }"
        )
    if wallets:
        continuity_refs.append(
            f"ps0h-h8.continuity.wallet_set.v1:{ _digest({'wallets': wallets, 'signature': signature}) }"
        )

    event_time = payload.get("observed_at")
    if isinstance(event_time, int) and event_time:
        continuity_refs.append(
            f"ps0h-h8.continuity.event_time.v1:{ _digest({'event_time': event_time, 'signature': signature}) }"
        )
    return continuity_refs


def _normalize_output_id(prefix: str, suffix: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(suffix))
    return f"{prefix}:{safe}"


def _write_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_RUNTIME_SCHEMA)


def _insert_contract_versions(conn: sqlite3.Connection, *, op_ids: Iterable[str], h8_payload: Mapping[str, Any], registered_at: int) -> None:
    for op_id in sorted(set(op_ids)):
        contract_payload = {
            "contract_id": op_id,
            "contract_version": COMPAT_CONTRACT_VERSION,
            "source": "PSI0H-H8 projection compatibility",
            "h8_artifact_digest": h8_payload.get("artifact_digest"),
        }
        contract_digest = _digest(contract_payload)
        payload_json = json.dumps(contract_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        conn.execute(
            "INSERT OR IGNORE INTO operation_contract_versions VALUES (?,?,?,?,?)",
            (op_id, COMPAT_CONTRACT_VERSION, contract_digest, payload_json, registered_at),
        )


def _manifest_body_for_digest(manifest_payload: Mapping[str, Any]) -> dict[str, Any]:
    projected = dict(manifest_payload)
    projected.pop("manifest_digest", None)
    projected.pop("runtime_identity", None)
    projected.pop("runtime_db_path", None)
    files = projected.get("files")
    if isinstance(files, Mapping):
        files_copy = {**files}
        if "operation-runtime.db" in files_copy:
            files_copy["operation-runtime.db"] = {"path": "operation-runtime.db"}
        projected["files"] = files_copy
    return projected


def _manifest_digest(manifest_payload: Mapping[str, Any]) -> str:
    return _digest(_manifest_body_for_digest(manifest_payload))


def _insert_outputs(conn: sqlite3.Connection, *, rows: Iterable[tuple[str, str, str, Mapping[str, Any]]], table_name: str) -> dict[str, int]:
    inserted = 0
    skipped = 0
    for output_id, op_id, _, payload in rows:
        if not op_id:
            skipped += 1
            continue
        observed_at = _to_int(payload.get("observed_at"), 0)
        compact_payload = dict(payload)
        compact_payload["subjects"] = _extract_subjects(payload)
        compact_payload["local_role"] = _derive_local_role(payload)
        compact_payload["operation_id"] = op_id
        compact_payload["evidence_refs"] = sorted(
            set(
                str(v) for v in compact_payload.get("evidence_refs", [])
                if isinstance(v, str) and v
            ) | set(_derive_continuity_refs(payload))
        )
        compact_payload["primitive_refs"] = list(compact_payload.get("primitive_refs", []))
        payload_json = json.dumps(compact_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        payload_digest = _digest(compact_payload)
        input_digest = _digest((payload_json, output_id, table_name))
        result = conn.execute(
            f"INSERT OR IGNORE INTO {table_name} VALUES (?,?,?,?,?,?,?,?)",
            (
                output_id,
                op_id,
                COMPAT_CONTRACT_VERSION,
                "h8-runtime-projection",
                input_digest,
                payload_json,
                payload_digest,
                observed_at,
            ),
        )
        if result.rowcount:
            inserted += 1

        for reference_type, key in (("evidence_refs", "evidence_id"), ("primitive_refs", "primitive_id")):
            value = payload.get(key)
            if not value:
                continue
            for ref in ([value] if not isinstance(value, list) else value):
                if ref is None or ref == "":
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO operation_runtime_references VALUES (?,?,?,?)",
                    (table_name, output_id, reference_type, str(ref)),
                )

    return {"inserted": inserted, "skipped": skipped}


def project_h8_to_h4_runtime(*, h8_artifact: Mapping[str, Any], runtime_db_path: str | Path, manifest_path: str | Path | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(h8_artifact, Mapping):
        raise Psi0hH8ToH4RuntimeProjectionError("PSI0H_H8_H4_H8_ARTIFACT_INVALID")
    if h8_artifact.get("schema_version") != H8_SCHEMA_VERSION:
        raise Psi0hH8ToH4RuntimeProjectionError("PSI0H_H8_H4_H8_SCHEMA_MISMATCH")
    if h8_artifact.get("status") not in {"PASS", "HOLD"}:
        raise Psi0hH8ToH4RuntimeProjectionError("PSI0H_H8_H4_H8_STATUS_INVALID")

    execution = h8_artifact.get("execution")
    if not isinstance(execution, Mapping):
        raise Psi0hH8ToH4RuntimeProjectionError("PSI0H_H8_H4_H8_EXECUTION_INVALID")

    evidence_rows = execution.get("evidence_rows", [])
    primitive_rows = execution.get("primitive_rows", [])
    if not isinstance(evidence_rows, list) or not isinstance(primitive_rows, list):
        raise Psi0hH8ToH4RuntimeProjectionError("PSI0H_H8_H4_H8_ROWS_INVALID")

    runtime_path = Path(runtime_db_path)
    runtime_path = runtime_path.resolve()
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    if runtime_path.exists():
        raise Psi0hH8ToH4RuntimeProjectionError("PSI0H_H8_H4_RUNTIME_EXISTS")

    projected_rows: list[tuple[str, str, str, Mapping[str, Any]]] = []
    op_ids: list[str] = []
    dropped_rows = {"missing_contract_id": 0, "non_object_payload": 0}

    # Evidence rows first to preserve best-effort evidence->primitive lineage in same operation.
    for index, row in enumerate(evidence_rows):
        if not isinstance(row, Mapping):
            dropped_rows["non_object_payload"] += 1
            continue
        payload = _safe_payload(row.get("payload"))
        if not payload:
            dropped_rows["non_object_payload"] += 1
            continue
        op_id = str(row.get("operation_id") or "").strip()
        if not op_id:
            dropped_rows["missing_contract_id"] += 1
            continue

        merged = dict(payload)
        merged["observed_at"] = _to_int(row.get("event_time"), 0)
        merged["window_start"] = _to_int(merged.get("window_start"), merged["observed_at"])
        merged["window_end"] = _to_int(merged.get("window_end"), merged["observed_at"])
        merged["window"] = {
            "start": merged["window_start"],
            "end": merged["window_end"],
        }
        merged["evidence_id"] = str(row.get("evidence_id", f"evidence_{index}"))
        merged.setdefault("source_path", str(payload.get("source_path") or row.get("source_path", "")))

        projected_rows.append((
            _normalize_output_id("psi0h_h8_to_h4_behaviour", merged["evidence_id"]),
            op_id,
            "behaviour_observations",
            merged,
        ))
        op_ids.append(op_id)

    evidence_by_operation: dict[str, list[str]] = {}
    for _, op_id, _, payload in projected_rows:
        evidence_by_operation.setdefault(op_id, []).append(str(payload.get("evidence_id")))

    for index, row in enumerate(primitive_rows):
        if not isinstance(row, Mapping):
            dropped_rows["non_object_payload"] += 1
            continue
        payload = _safe_payload(row.get("payload"))
        if not payload:
            dropped_rows["non_object_payload"] += 1
            continue
        op_id = str(row.get("operation_id") or "").strip()
        if not op_id:
            dropped_rows["missing_contract_id"] += 1
            continue

        merged = dict(payload)
        merged["observed_at"] = _to_int(row.get("event_time"), _to_int(merged.get("observed_at"), 0))
        merged["window_start"] = _to_int(merged.get("window_start"), merged["observed_at"])
        merged["window_end"] = _to_int(merged.get("window_end"), merged["observed_at"])
        merged["window"] = {
            "start": merged["window_start"],
            "end": merged["window_end"],
        }
        merged["primitive_id"] = str(row.get("primitive_id", f"primitive_{index}"))
        merged["generated_at"] = _to_int(row.get("generated_at"), merged["observed_at"])

        merged["evidence_refs"] = sorted(
            set(str(v) for v in merged.get("evidence_refs", []) if isinstance(v, str) and v)
            | set(_derive_continuity_refs(payload))
        )

        linked = evidence_by_operation.get(op_id)
        if linked:
            merged["evidence_refs"] = sorted(set(merged.get("evidence_refs", []) + [linked[0]]))
        projected_rows.append((
            _normalize_output_id("psi0h_h8_to_h4_detector_input", merged["primitive_id"]),
            op_id,
            "detector_inputs",
            merged,
        ))
        op_ids.append(op_id)

    if not projected_rows:
        raise Psi0hH8ToH4RuntimeProjectionError("PSI0H_H8_H4_NO_PROJECTABLE_ROWS")

    conn = sqlite3.connect(runtime_path)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        _write_schema(conn)
        if op_ids:
            _insert_contract_versions(conn, op_ids=op_ids, h8_payload=h8_artifact, registered_at=0)

        evidence_payloads = [item for item in projected_rows if item[2] == "behaviour_observations"]
        primitive_payloads = [item for item in projected_rows if item[2] == "detector_inputs"]
        evidence_summary = _insert_outputs(conn, rows=evidence_payloads, table_name="behaviour_observations")
        primitive_summary = _insert_outputs(conn, rows=primitive_payloads, table_name="detector_inputs")

        manifest_payload = {
            "schema_version": H4_COMPAT_SCHEMA_VERSION,
            "milestone": H4_MILESTONE,
            "status": "PASS",
            "run_id": RUN_ID,
            "source": {
                "path": "PSI0H-H8 to PSI0G-B compatibility projection",
            },
            "files": {
                "operation-runtime.db": {"path": str(runtime_path)}
            },
            "run": {
                "h8_artifact_digest": h8_artifact.get("artifact_digest"),
                "source_evidence_rows": len(evidence_rows),
                "source_primitive_rows": len(primitive_rows),
                "kept_evidence_rows": evidence_summary["inserted"],
                "kept_primitive_rows": primitive_summary["inserted"],
                "dropped_rows": dict(dropped_rows),
            },
            "runtime_db_path": str(runtime_path),
            "operation_ids": sorted(set(op_ids)),
            "projection_schema": SCHEMA_VERSION,
        }
        manifest_payload["runtime_identity"] = _file_identity(runtime_path)
        manifest_payload["manifest_digest"] = _manifest_digest(manifest_payload)
        manifest_path = Path(manifest_path or (runtime_path.parent / "manifest.json"))
        manifest_path.write_text(json.dumps(manifest_payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        conn.commit()
    finally:
        conn.close()

    return manifest_payload, {
        "runtime_db_path": str(runtime_path),
        "runtime_exists": runtime_path.exists(),
        "manifest_path": str(manifest_path),
    }


def verify_projection_manifest(manifest: Mapping[str, Any]) -> bool:
    if not isinstance(manifest, Mapping):
        raise Psi0hH8ToH4RuntimeProjectionError("PSI0H_H8_H4_MANIFEST_INVALID")
    if manifest.get("schema_version") != H4_COMPAT_SCHEMA_VERSION:
        raise Psi0hH8ToH4RuntimeProjectionError("PSI0H_H8_H4_MANIFEST_SCHEMA_MISMATCH")
    if manifest.get("milestone") != H4_MILESTONE:
        raise Psi0hH8ToH4RuntimeProjectionError("PSI0H_H8_H4_MANIFEST_MILESTONE_MISMATCH")
    if manifest.get("status") != "PASS":
        raise Psi0hH8ToH4RuntimeProjectionError("PSI0H_H8_H4_MANIFEST_STATUS_INVALID")

    files = manifest.get("files")
    if not isinstance(files, Mapping) or "operation-runtime.db" not in files:
        raise Psi0hH8ToH4RuntimeProjectionError("PSI0H_H8_H4_MANIFEST_FILES_MISSING")

    runtime_map = files["operation-runtime.db"]
    runtime_path = Path(runtime_map.get("path") if isinstance(runtime_map, Mapping) else str(runtime_map))
    if not runtime_path.is_file():
        raise Psi0hH8ToH4RuntimeProjectionError("PSI0H_H8_H4_MANIFEST_RUNTIME_MISSING")

    manifest_digest = manifest.get("manifest_digest")
    if not isinstance(manifest_digest, str) or len(manifest_digest) != 64:
        raise Psi0hH8ToH4RuntimeProjectionError("PSI0H_H8_H4_MANIFEST_DIGEST_INVALID")

    replay = dict(manifest)
    replay.pop("manifest_digest")
    if _manifest_digest(replay) != manifest_digest:
        raise Psi0hH8ToH4RuntimeProjectionError("PSI0H_H8_H4_MANIFEST_DIGEST_MISMATCH")
    return True


def read_json_path(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise Psi0hH8ToH4RuntimeProjectionError("PSI0H_H8_H4_INPUT_NOT_OBJECT")
    return payload
