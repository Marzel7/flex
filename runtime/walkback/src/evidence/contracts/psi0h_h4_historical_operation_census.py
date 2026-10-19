"""PSI0H-H4 historical operation-population census contract."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping

SCHEMA_VERSION = "psi0h-h4.historical-operation-census.v1"
MAX_OPERATIONS = 200

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


class Psi0hH4HistoricalOperationCensusError(RuntimeError):
    pass


@dataclass(frozen=True)
class _PopulationState:
    operation_id: str
    source_path: str
    source_identity: dict[str, int]
    evidence_rows: int = 0
    topology_rows: int = 0
    primitive_rows: int = 0
    subject_ids: tuple[str, ...] = ()
    topology_revision_ids: tuple[str, ...] = ()
    contract_versions: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    primitive_ids: tuple[str, ...] = ()
    first_observed_utc: int = 0
    last_observed_utc: int = 0


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _digest(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _file_identity(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {
        "device": int(stat.st_dev),
        "inode": int(stat.st_ino),
        "size_bytes": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def _safe_parse(payload: Any) -> Mapping[str, Any]:
    if payload is None:
        return {}
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return {}
    else:
        parsed = payload
    if isinstance(parsed, Mapping):
        return parsed
    return {}


def _coalesce_int(value: Any) -> int:
    return int(value) if isinstance(value, int) else 0


def _load_manifest(path: str | Path) -> tuple[Path, dict[str, Any]]:
    manifest_path = Path(path)
    if not manifest_path.exists():
        raise Psi0hH4HistoricalOperationCensusError(f"PSI0H_H4_MANIFEST_MISSING:{path}")
    payload = _read_json(manifest_path)
    if not isinstance(payload, Mapping):
        raise Psi0hH4HistoricalOperationCensusError("PSI0H_H4_MANIFEST_INVALID")
    if payload.get("schema_version") != "1.0.0":
        raise Psi0hH4HistoricalOperationCensusError("PSI0H_H4_MANIFEST_INVALID_SCHEMA")
    if payload.get("milestone") != "PSI0G-B":
        raise Psi0hH4HistoricalOperationCensusError("PSI0H_H4_MANIFEST_INVALID_MILESTONE")
    if payload.get("status") != "PASS":
        raise Psi0hH4HistoricalOperationCensusError("PSI0H_H4_MANIFEST_NOT_PASS")

    files = payload.get("files")
    if not isinstance(files, Mapping):
        raise Psi0hH4HistoricalOperationCensusError("PSI0H_H4_MANIFEST_FILES_MISSING")

    runtime_entry = files.get("operation-runtime.db")
    if isinstance(runtime_entry, Mapping):
        runtime_name = runtime_entry.get("path")
    else:
        runtime_name = None

    if not runtime_name:
        runtime_candidate = manifest_path.parent / "operation-runtime.db"
    else:
        runtime_candidate = Path(runtime_name)
        if not runtime_candidate.is_absolute():
            runtime_candidate = (manifest_path.parent / runtime_name).resolve()
    if not runtime_candidate.exists():
        raise Psi0hH4HistoricalOperationCensusError(f"PSI0H_H4_RUNTIME_DB_MISSING:{runtime_candidate}")

    return runtime_candidate, payload


def _update_state(
    operation_states: dict[str, dict[str, Any]],
    output_contract_map: dict[str, str],
    op_id: str,
    source_path: str,
    source_identity: dict[str, int],
) -> dict[str, Any]:
    rec = operation_states.setdefault(
        op_id,
        {
            "operation_id": op_id,
            "source_path": source_path,
            "source_identity": source_identity,
            "evidence_rows": 0,
            "topology_rows": 0,
            "primitive_rows": 0,
            "subject_ids": set(),
            "topology_revision_ids": set(),
            "contract_versions": set(),
            "roles": set(),
            "evidence_ids": set(),
            "primitive_ids": set(),
            "first_observed_utc": None,
            "last_observed_utc": None,
        },
    )
    if output_id := rec.get("source_output_id"):
        output_contract_map.setdefault(output_id, op_id)
    return rec


def _consume_payload_row(
    operation_states: dict[str, dict[str, Any]],
    op_id: str,
    payload_json: Any,
    generated_at: Any,
    contract_version: Any,
    table: str,
) -> None:
    if not op_id:
        return
    rec = operation_states[op_id]
    rec["evidence_rows"] += 1

    if table == "topology_revisions":
        rec["topology_rows"] += 1
    if table in {"detector_inputs", "detector_results"}:
        rec["primitive_rows"] += 1

    payload = _safe_parse(payload_json)
    if "local_role" in payload:
        role = payload["local_role"]
        if isinstance(role, str) and role:
            rec["roles"].add(role)
    if isinstance(payload.get("subjects"), list):
        rec["subject_ids"].update(str(x) for x in payload["subjects"] if x)
    if "topology_revision_id" in payload and payload["topology_revision_id"]:
        rec["topology_revision_ids"].add(str(payload["topology_revision_id"]))
    if "evidence_refs" in payload and isinstance(payload["evidence_refs"], list):
        rec["evidence_ids"].update([str(v) for v in payload["evidence_refs"] if v])
    if "primitive_refs" in payload and isinstance(payload["primitive_refs"], list):
        rec["primitive_ids"].update([str(v) for v in payload["primitive_refs"] if v])

    if isinstance(contract_version, str) and contract_version:
        rec["contract_versions"].add(contract_version)

    observed = _coalesce_int(generated_at)
    if observed:
        first = rec["first_observed_utc"]
        last = rec["last_observed_utc"]
        rec["first_observed_utc"] = observed if first is None else min(first, observed)
        rec["last_observed_utc"] = observed if last is None else max(last, observed)


def _collect_from_output_tables(conn: sqlite3.Connection, operation_states: dict[str, dict[str, Any]], source_path: str, source_identity: dict[str, int]) -> dict[str, str]:
    output_to_contract: dict[str, str] = {}

    for table in ("behaviour_observations", "detector_inputs", "detector_results", "topology_revisions"):
        rows = conn.execute(f"SELECT output_id, contract_id, contract_version, payload_json, generated_at FROM {table}").fetchall()
        for output_id, contract_id, contract_version, payload_json, generated_at in rows:
            op_id = (contract_id or "").strip()
            if not op_id:
                continue
            rec = _update_state(operation_states, output_to_contract, op_id, source_path, source_identity)
            if output_id:
                output_to_contract[str(output_id)] = op_id
            rec["source_output_id"] = str(output_id)
            _consume_payload_row(operation_states, op_id, payload_json, generated_at, contract_version, table)

    # references table provides evidence/primitive IDs keyed by output_id
    for output_type, output_id, reference_type, reference_id in conn.execute(
        "SELECT output_type, output_id, reference_type, reference_id FROM operation_runtime_references"
    ).fetchall():
        op_id = output_to_contract.get(str(output_id), "")
        if not op_id:
            continue
        if reference_type in {"evidence_refs", "evidence_refs_legacy"}:
            if reference_id:
                operation_states[op_id]["evidence_ids"].add(str(reference_id))
        if reference_type in {"primitive_refs", "primitive_refs_legacy"}:
            if reference_id:
                operation_states[op_id]["primitive_ids"].add(str(reference_id))

    # collect declared contract versions and digests for completeness of lineage
    for contract_id, contract_version, contract_digest, payload_json, _registered_at in conn.execute(
        "SELECT contract_id, contract_version, contract_digest, payload_json, registered_at FROM operation_contract_versions"
    ).fetchall():
        op_id = (contract_id or "").strip()
        if not op_id:
            continue
        rec = operation_states.setdefault(
            op_id,
            {
                "operation_id": op_id,
                "source_path": source_path,
                "source_identity": source_identity,
                "evidence_rows": 0,
                "topology_rows": 0,
                "primitive_rows": 0,
                "subject_ids": set(),
                "topology_revision_ids": set(),
                "contract_versions": set(),
                "roles": set(),
                "evidence_ids": set(),
                "primitive_ids": set(),
                "first_observed_utc": None,
                "last_observed_utc": None,
            }
        )
        if isinstance(contract_version, str) and contract_version:
            rec["contract_versions"].add(contract_version)
        if contract_digest:
            rec["contract_versions"].add(f"digest:{contract_digest}")
        payload = _safe_parse(payload_json)
        if "subject_count" in payload and isinstance(payload["subject_count"], int):
            rec["evidence_rows"] += payload["subject_count"]
        if "contract_version" in payload and isinstance(payload["contract_version"], str):
            rec["contract_versions"].add(payload["contract_version"])

    return output_to_contract


def qualify_historical_operation_census(
    *, h1_artifact: Mapping[str, Any], manifest_path: str | Path | None = None, maximum_operations: int = MAX_OPERATIONS,
) -> dict[str, Any]:
    if not isinstance(h1_artifact, Mapping):
        raise Psi0hH4HistoricalOperationCensusError("PSI0H_H4_H1_ARTIFACT_INVALID")

    if not 1 <= maximum_operations <= MAX_OPERATIONS:
        raise Psi0hH4HistoricalOperationCensusError("PSI0H_H4_MAX_OPERATIONS_INVALID")

    if not manifest_path:
        manifest_path = h1_artifact.get("manifest_source_path")
    if not manifest_path:
        raise Psi0hH4HistoricalOperationCensusError("PSI0H_H4_MANIFEST_PATH_MISSING")

    runtime_path, manifest = _load_manifest(manifest_path)
    source_identity = _file_identity(runtime_path)

    conn = sqlite3.connect(f"file:{runtime_path}?mode=ro", uri=True)
    operation_states: dict[str, dict[str, Any]] = {}
    try:
        _collect_from_output_tables(conn, operation_states, str(runtime_path), source_identity)
    finally:
        conn.close()

    populations = []
    for op_id in sorted(operation_states):
        rec = operation_states[op_id]
        blockers = []
        if not rec["subject_ids"]:
            blockers.append("NO_SUBJECTS_VISIBLE")

        populations.append(
            {
                "operation_population_id": _digest(
                    {
                        "operation_id": op_id,
                        "source_path": rec["source_path"],
                        "first_observed_utc": rec["first_observed_utc"],
                        "last_observed_utc": rec["last_observed_utc"],
                    }
                ),
                "operation_id": op_id,
                "source_path": rec["source_path"],
                "source_identity": rec["source_identity"],
                "first_observed_utc": rec["first_observed_utc"] or 0,
                "last_observed_utc": rec["last_observed_utc"] or 0,
                "evidence_count": int(rec["evidence_rows"]),
                "topology_revision_count": int(rec["topology_rows"]),
                "primitive_reference_count": int(rec["primitive_rows"]),
                "subject_count": len(rec["subject_ids"]),
                "contract_versions": sorted(rec["contract_versions"]),
                "roles": sorted(rec["roles"]),
                "evidence_refs": sorted(rec["evidence_ids"]),
                "primitive_refs": sorted(rec["primitive_ids"]),
                "topology_revision_ids": sorted(rec["topology_revision_ids"]),
                "evidence_ids_digest": _digest(sorted(rec["evidence_ids"])),
                "primitive_ids_digest": _digest(sorted(rec["primitive_ids"])),
                "topology_revision_ids_digest": _digest(sorted(rec["topology_revision_ids"])),
                "identity_guarded": True,
                "same_operation_claim": False,
                "same_human_claim": False,
                "population_blockers": blockers,
            }
        )

    if len(populations) > maximum_operations:
        populations = populations[:maximum_operations]

    status = "PASS" if populations else "HOLD"
    blockers = [] if populations else ["NO_OPERATION_POPULATIONS_DERIVED"]
    if status == "PASS" and len(populations) < 2:
        blockers.append("DISCOVERY_SCOPE_YIELDS_FEW_OPERATION_POPULATIONS")

    result = {
        "schema_version": SCHEMA_VERSION,
        "milestone": "PSI0H-H4",
        "status": status,
        "verdict": "H4_HISTORICAL_OPERATION_CENSUS_PASS" if status == "PASS" else "H4_HISTORICAL_OPERATION_CENSUS_HOLD_NO_POPULATION",
        "selection": {
            "input_manifest_path": str(manifest_path),
            "runtime_db_path": str(runtime_path),
            "manifest_run_id": manifest.get("run_id"),
            "manifest_status": manifest.get("status"),
        },
        "operation_count": len(populations),
        "discovered_populations": populations,
        "authority": dict(AUTHORITY),
        "scope": {
            "comparison": False,
            "candidate_generation": False,
            "candidate_disposition": False,
            "provider_or_rpc_calls": 0,
            "monitoring": False,
            "activation": False,
            "same_operation": False,
            "same_human": False,
        },
        "required_scope": {
            "observation_only": True,
            "provider_or_rpc_calls": 0,
            "comparison": False,
            "candidate_generation": False,
            "candidate_disposition": False,
            "monitoring": False,
            "activation": False,
        },
        "source": {
            "h1_artifact_digest": h1_artifact.get("artifact_digest"),
            "h1_status": h1_artifact.get("status"),
            "manifest_source_path": h1_artifact.get("manifest_source_path"),
            "manifest_digest": manifest.get("manifest_digest"),
        },
        "blockers": blockers,
    }
    result["artifact_digest"] = _digest(result)
    return result


def verify_historical_operation_census(record: Mapping[str, Any]) -> bool:
    if not isinstance(record, Mapping):
        raise Psi0hH4HistoricalOperationCensusError("PSI0H_H4_RECORD_INVALID")
    for key in ("artifact_digest", "schema_version", "status", "operation_count", "discovered_populations"):
        if key not in record:
            raise Psi0hH4HistoricalOperationCensusError("PSI0H_H4_RECORD_INVALID")

    digest = str(record["artifact_digest"])
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise Psi0hH4HistoricalOperationCensusError("PSI0H_H4_RECORD_DIGEST_INVALID")
    replay = dict(record)
    replay.pop("artifact_digest")
    if _digest(replay) != digest:
        raise Psi0hH4HistoricalOperationCensusError("PSI0H_H4_RECORD_DIGEST_MISMATCH")

    if not isinstance(record["discovered_populations"], list):
        raise Psi0hH4HistoricalOperationCensusError("PSI0H_H4_RECORD_POPULATIONS_INVALID")

    for row in record["discovered_populations"]:
        if not isinstance(row, Mapping):
            raise Psi0hH4HistoricalOperationCensusError("PSI0H_H4_RECORD_POPULATION_INVALID")
        for key in ("operation_id", "source_path", "source_identity", "identity_guarded"):
            if key not in row:
                raise Psi0hH4HistoricalOperationCensusError("PSI0H_H4_RECORD_POPULATION_INVALID")
        if row.get("same_operation_claim") is not False or row.get("same_human_claim") is not False:
            raise Psi0hH4HistoricalOperationCensusError("PSI0H_H4_RECORD_UNSUPPORTED_DISPOSITION")

    return True
