"""PSI0H-H5 historical source-expansion reconciliation contract."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping, Iterable

SCHEMA_VERSION = "psi0h-h5.historical-source-expansion.v1"

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


BLOCKER_NO_OPERATION_BOUNDARY = "NO_STABLE_OPERATION_BOUNDARY"
BLOCKER_ADDRESS_MOTIFS_ONLY = "ADDRESS_LEVEL_MOTIFS_ONLY"
BLOCKER_TOPOLOGY_ROLE_TEMPORAL_INCOMPLETE = "REQUIRED_TOPOLOGY_ROLE_TEMPORAL_INCOMPLETE"
BLOCKER_LINEAGE_PROVENANCE_INSUFFICIENT = "LINEAGE_PROVENANCE_INSUFFICIENT"
BLOCKER_SOURCE_NOT_RETAINED = "SOURCE_WAS_NEVER_RETAINED"


class Psi0hH5HistoricalSourceExpansionError(RuntimeError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _digest(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


@dataclass(frozen=True)
class _OperationSourceRow:
    source_path: str
    source_identity: dict[str, int]
    evidence_rows: int
    primitive_rows: int
    operation_candidates: tuple[str, ...]
    subject_fields_present: tuple[str, ...]
    primitive_types: tuple[str, ...]
    provenance_links: int
    has_temporal_windows: bool
    has_topology_role_fields: bool
    blockers: tuple[str, ...]


def _file_identity(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {
        "device": int(stat.st_dev),
        "inode": int(stat.st_ino),
        "size_bytes": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def _safe_load_json(value: Any) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
    else:
        parsed = value
    if isinstance(parsed, Mapping):
        return parsed
    return None


def _safe_rows(conn: sqlite3.Connection, query: str, sample_limit: int = 0) -> int:
    try:
        if sample_limit:
            query = f"SELECT COUNT(*) FROM ({query} LIMIT {int(sample_limit)})"
        return int(conn.execute(query).fetchone()[0])
    except Exception:
        return 0


def _collect_operation_tokens(payload: Mapping[str, Any]) -> set[str]:
    op_fields = ("operation_id", "operation_key", "operation", "operation_identity")
    operation_ids = set()
    for field in op_fields:
        value = payload.get(field)
        if isinstance(value, str):
            value = value.strip()
            if value:
                operation_ids.add(value)
    return operation_ids


def _collect_subject_fields(payload: Mapping[str, Any]) -> set[str]:
    candidates = (
        "wallet",
        "funder",
        "recipient",
        "source",
        "destination",
        "creator",
        "activation_sender",
        "asset",
    )
    present = {field for field in candidates if field in payload}
    return present


def _has_topology_role_fields(payload: Mapping[str, Any]) -> bool:
    role_markers = (
        "source",
        "destination",
        "wallet",
        "creator",
        "funder",
        "recipient",
        "signer",
        "freshness_state",
        "reference_event",
    )
    return any(field in payload for field in role_markers)


def _iter_candidate_evidence_dbs(
    manifest: Mapping[str, Any],
    evidence_root: Path | str | None = None,
    h4_source_candidates: Iterable[Mapping[str, Any]] | None = None,
) -> list[Path]:
    evidences: list[Path] = []
    root = Path(evidence_root or Path(__file__).resolve().parents[3] / "database" / "evidence_platform")

    for source_row in h4_source_candidates or []:
        source_path = source_row.get("source_path") if isinstance(source_row, Mapping) else None
        if isinstance(source_path, str) and source_path:
            candidate = Path(source_path)
            if candidate.exists():
                evidences.append(candidate.resolve())

    manifest_candidates = manifest.get("selection", {}) if isinstance(manifest, Mapping) else {}
    runtime_db_path = manifest_candidates.get("runtime_db_path") if isinstance(manifest_candidates, Mapping) else None
    if isinstance(runtime_db_path, str):
        candidate = Path(runtime_db_path)
        if candidate.exists():
            evidences.append(candidate.resolve())

    manifest_files = manifest.get("files") if isinstance(manifest, Mapping) else None
    if isinstance(manifest_files, Mapping):
        for value in manifest_files.values():
            if isinstance(value, Mapping):
                candidate = Path(str(value.get("path") or "")).resolve()
                if candidate.suffix == ".db" and candidate.exists():
                    evidences.append(candidate)

    if not evidences:
        for path in sorted(root.glob("**/*.db")):
            if (
                "evidence.db" in path.name
                or "compact_shadow.db" in path.name
                or path.name in {"analysis.sqlite", "primitive_replay.sqlite", "authority_projection.sqlite"}
            ):
                evidences.append(path)

    # dedupe while keeping deterministic order
    unique: list[Path] = []
    seen: set[Path] = set()
    for item in evidences:
        try:
            if item in seen:
                continue
            seen.add(item)
            unique.append(item)
        except TypeError:
            # Path is hashable, but guard in constrained contexts.
            unique.append(item)
    return unique


def _classify_source(conn: sqlite3.Connection, source_path: Path) -> _OperationSourceRow:
    source_identity = _file_identity(source_path)

    evidence_rows = _safe_rows(
        conn,
        "SELECT 1 FROM normalized_evidence_records",
        sample_limit=1000,
    )
    primitive_rows = _safe_rows(
        conn,
        "SELECT 1 FROM primitive_observations",
        sample_limit=1000,
    )
    provenance_links = _safe_rows(
        conn,
        "SELECT COUNT(*) FROM evidence_provenance WHERE provider_request_id IS NOT NULL",
        sample_limit=1000,
    )

    operation_ids: set[str] = set()
    subject_fields: set[str] = set()
    primitive_types: set[str] = set()
    has_temporal_windows = False
    has_topology_role_fields = False

    # Pull a bounded sample to avoid heavy scans.
    try:
        for payload_json, _observed_at in conn.execute(
            "SELECT payload_json, observed_at FROM normalized_evidence_records LIMIT 500"
        ).fetchall():
            payload = _safe_load_json(payload_json)
            if not payload:
                continue
            operation_ids.update(_collect_operation_tokens(payload))
            subject_fields.update(_collect_subject_fields(payload))
            has_topology_role_fields = has_topology_role_fields or _has_topology_role_fields(payload)
    except Exception:
        pass

    try:
        for primitive_type, payload_json in conn.execute(
            "SELECT primitive_type, output_payload_json FROM primitive_observations LIMIT 500"
        ).fetchall():
            payload = _safe_load_json(payload_json)
            if not payload:
                continue
            operation_ids.update(_collect_operation_tokens(payload))
            subject_fields.update(_collect_subject_fields(payload))
            if payload.get("window") and isinstance(payload.get("window"), Mapping):
                window = payload["window"]
                if isinstance(window.get("start"), int) or isinstance(window.get("end"), int):
                    has_temporal_windows = True
            if "window_start" in payload or "window_end" in payload:
                if isinstance(payload.get("window_start"), int) or isinstance(payload.get("window_end"), int):
                    has_temporal_windows = True
            if primitive_type and primitive_type[0]:
                primitive_types.add(str(primitive_type[0]))
    except Exception:
        pass

    try:
        for primitive_type, in conn.execute(
            "SELECT DISTINCT primitive_type FROM primitive_observations LIMIT 200"
        ).fetchall():
            if primitive_type:
                primitive_types.add(str(primitive_type))
    except Exception:
        pass

    blockers = set()
    if evidence_rows == 0 and primitive_rows == 0:
        blockers.add(BLOCKER_SOURCE_NOT_RETAINED)
    else:
        if not operation_ids:
            blockers.add(BLOCKER_NO_OPERATION_BOUNDARY)
            if subject_fields:
                blockers.add(BLOCKER_ADDRESS_MOTIFS_ONLY)

        required_types = {
            "LAUNCH_SIGNER",
            "LAUNCH_ACTIVATION",
            "DIRECT_COUNTERPARTY",
            "ECONOMIC_FUNDING",
            "SYSTEM_TRANSFER",
            "WALLET_FRESH_AT_EVENT",
        }
        if not has_topology_role_fields or not (primitive_rows and (primitive_types & required_types)):
            blockers.add(BLOCKER_TOPOLOGY_ROLE_TEMPORAL_INCOMPLETE)
        if not has_temporal_windows and not (primitive_rows and primitive_types):
            blockers.add(BLOCKER_TOPOLOGY_ROLE_TEMPORAL_INCOMPLETE)

        if provenance_links == 0:
            blockers.add(BLOCKER_LINEAGE_PROVENANCE_INSUFFICIENT)

    return _OperationSourceRow(
        source_path=str(source_path),
        source_identity=source_identity,
        evidence_rows=evidence_rows,
        primitive_rows=primitive_rows,
        operation_candidates=tuple(sorted(operation_ids)),
        subject_fields_present=tuple(sorted(subject_fields)),
        primitive_types=tuple(sorted(primitive_types)),
        provenance_links=provenance_links,
        has_temporal_windows=has_temporal_windows,
        has_topology_role_fields=has_topology_role_fields,
        blockers=tuple(sorted(blockers)),
    )


def qualify_historical_source_expansion(
    *, h4_artifact: Mapping[str, Any], evidence_root: str | Path | None = None, maximum_sources: int = 500
) -> dict[str, Any]:
    if not isinstance(h4_artifact, Mapping):
        raise Psi0hH5HistoricalSourceExpansionError("PSI0H_H5_H4_ARTIFACT_INVALID")
    if h4_artifact.get("schema_version") != "psi0h-h4.historical-operation-census.v1":
        raise Psi0hH5HistoricalSourceExpansionError("PSI0H_H5_H4_BINDING_INVALID")
    if not isinstance(maximum_sources, int) or not 1 <= maximum_sources <= 2000:
        raise Psi0hH5HistoricalSourceExpansionError("PSI0H_H5_MAX_SOURCES_INVALID")

    h4_source_candidates = h4_artifact.get("discovered_populations")
    if not isinstance(h4_source_candidates, list):
        raise Psi0hH5HistoricalSourceExpansionError("PSI0H_H5_H4_DISCOVERED_POPULATIONS_INVALID")

    known_ids = sorted(
        {
            row.get("operation_id")
            for row in h4_source_candidates
            if isinstance(row, Mapping) and isinstance(row.get("operation_id"), str)
        }
    )

    manifest_payload = {
        "run_id": h4_artifact.get("run_id"),
        "status": h4_artifact.get("status"),
        "files": h4_artifact.get("files", {}),
    }
    manifest_payload.setdefault("files", h4_artifact.get("files", {}))

    source_rows = []
    reconstructable_operations = []
    missing_reasons_counter: dict[str, int] = {}
    def _scan_paths(paths: list[Path]) -> None:
        nonlocal source_rows, reconstructable_operations
        for path in paths:
            if len(source_rows) >= maximum_sources:
                break
            if not path.exists():
                continue
            conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
            conn.execute("PRAGMA busy_timeout = 250")
            try:
                tables = {
                    row[0] for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                if not {"normalized_evidence_records", "primitive_observations"} & tables:
                    source_row = {
                        "source_path": str(path),
                        "source_identity": _file_identity(path),
                        "evidence_rows": 0,
                        "primitive_rows": 0,
                        "provenance_links": 0,
                        "primitive_types": [],
                        "subject_fields": [],
                        "candidate_operation_ids": [],
                        "has_temporal_windows": False,
                        "has_topology_role_fields": False,
                        "blocking_reasons": [BLOCKER_SOURCE_NOT_RETAINED],
                    }
                    source_rows.append(source_row)
                    for block in source_row["blocking_reasons"]:
                        missing_reasons_counter[block] = missing_reasons_counter.get(block, 0) + 1
                    continue

                row = _classify_source(conn, path)
            finally:
                conn.close()

            source_rows.append(
                {
                    "source_path": row.source_path,
                    "source_identity": row.source_identity,
                    "evidence_rows": row.evidence_rows,
                    "primitive_rows": row.primitive_rows,
                    "provenance_links": row.provenance_links,
                    "primitive_types": list(row.primitive_types),
                    "subject_fields": list(row.subject_fields_present),
                    "candidate_operation_ids": list(row.operation_candidates),
                    "has_temporal_windows": row.has_temporal_windows,
                    "has_topology_role_fields": row.has_topology_role_fields,
                    "blocking_reasons": list(row.blockers),
                }
            )
            for block in row.blockers:
                missing_reasons_counter[block] = missing_reasons_counter.get(block, 0) + 1

            for op_id in row.operation_candidates:
                if op_id in known_ids:
                    continue
                reconstructable_operations.append(
                    {
                        "operation_id": op_id,
                        "source_path": row.source_path,
                        "evidence_rows": row.evidence_rows,
                        "primitive_rows": row.primitive_rows,
                        "blocking_reasons": list(row.blockers),
                    }
                )

    _scan_paths(
        _iter_candidate_evidence_dbs(
            manifest_payload,
            evidence_root=evidence_root,
            h4_source_candidates=h4_source_candidates,
        )
    )

    # H4-linked hints are the primary source set for H5. If hints are not evidence containers,
    # we intentionally hold with explicit blockers rather than broad recursive expansion in a single pass.

    source_rows.sort(key=lambda item: item["source_path"])
    reconstructable_operations = sorted(reconstructable_operations, key=lambda item: item["operation_id"])

    blockers = []
    if not reconstructable_operations:
        blockers.append("NO_ADDITIONAL_OPERATION_POPULATIONS_DERIVED")
    if len(h4_source_candidates) < 2:
        blockers.append("H4_SCOPE_LIMITS_RECONCILIATION")

    status = "PASS" if source_rows else "HOLD"
    result = {
        "schema_version": SCHEMA_VERSION,
        "milestone": "PSI0H-H5",
        "status": status,
        "verdict": "H5_SOURCE_EXPANSION_RECONCILIATION_PASS"
        if status == "PASS"
        else "H5_SOURCE_EXPANSION_RECONCILIATION_HOLD",
        "selection": {
            "h4_artifact_digest": h4_artifact.get("artifact_digest"),
            "h4_status": h4_artifact.get("status"),
            "h4_operation_count": len(h4_source_candidates),
        },
        "candidate_source_paths_scanned": len(source_rows),
        "reconstructed_additional_operation_population_count": len(reconstructable_operations),
        "expanded_populations": reconstructable_operations,
        "source_inventory_rows": source_rows,
        "missing_reason_counts": missing_reasons_counter,
        "blockers": blockers,
        "authority": dict(AUTHORITY),
        "scope": {
            "comparison": False,
            "candidate_generation": False,
            "candidate_disposition": False,
            "monitoring": False,
            "provider_or_rpc_calls": 0,
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
            "artifact_source_path": "PSI0H-H4",
            "known_operation_count": len(known_ids),
            "h4_manifest_path": h4_artifact.get("manifest_path"),
        },
        "source_evidence_root": str(
            Path(evidence_root or Path(__file__).resolve().parents[3] / "database" / "evidence_platform")
        ),
    }
    result["artifact_digest"] = _digest(result)
    return result


def verify_historical_source_expansion(record: Mapping[str, Any]) -> bool:
    if not isinstance(record, Mapping):
        raise Psi0hH5HistoricalSourceExpansionError("PSI0H_H5_RECORD_INVALID")
    for key in ("artifact_digest", "schema_version", "status", "source_inventory_rows", "expanded_populations"):
        if key not in record:
            raise Psi0hH5HistoricalSourceExpansionError("PSI0H_H5_RECORD_INVALID")
    if record["schema_version"] != SCHEMA_VERSION:
        raise Psi0hH5HistoricalSourceExpansionError("PSI0H_H5_RECORD_SCHEMA_MISMATCH")

    digest = str(record["artifact_digest"])
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise Psi0hH5HistoricalSourceExpansionError("PSI0H_H5_RECORD_DIGEST_INVALID")
    replay = dict(record)
    replay.pop("artifact_digest")
    if _digest(replay) != digest:
        raise Psi0hH5HistoricalSourceExpansionError("PSI0H_H5_RECORD_DIGEST_MISMATCH")

    if not isinstance(record["source_inventory_rows"], list):
        raise Psi0hH5HistoricalSourceExpansionError("PSI0H_H5_RECORD_SOURCE_INVENTORY_INVALID")
    if not isinstance(record["expanded_populations"], list):
        raise Psi0hH5HistoricalSourceExpansionError("PSI0H_H5_RECORD_EXPANDED_POPULATIONS_INVALID")
    return True
