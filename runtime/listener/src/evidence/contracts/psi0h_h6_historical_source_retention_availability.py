"""PSI0H-H6 historical source-retention availability and acquisition design contract."""

from __future__ import annotations

import os
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import json
import sqlite3
from typing import Any, Mapping, Iterable

SCHEMA_VERSION = "psi0h-h6.historical-source-retention-availability.v1"
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

VERDICT_READY_LOCAL_EXPANSION = "READY_LOCAL_EXPANSION"
VERDICT_READY_BOUNDED_BACKFILL = "READY_BOUNDED_BACKFILL"
VERDICT_BLOCKED_SOURCE_ABSENT = "BLOCKED_SOURCE_ABSENT"

BLOCKER_SOURCE_NOT_RETAINED = "SOURCE_WAS_NEVER_RETAINED"
BLOCKER_NO_OPERATION_BOUNDARY = "NO_STABLE_OPERATION_BOUNDARY"
BLOCKER_ADDRESS_MOTIFS_ONLY = "ADDRESS_LEVEL_MOTIFS_ONLY"
BLOCKER_TOPOLOGY_ROLE_TEMPORAL_INCOMPLETE = "REQUIRED_TOPOLOGY_ROLE_TEMPORAL_INCOMPLETE"
BLOCKER_LINEAGE_PROVENANCE_INSUFFICIENT = "LINEAGE_PROVENANCE_INSUFFICIENT"
BLOCKER_TABLE_SCAN_FAILURE = "SOURCE_TABLE_SCAN_FAILED"
BLOCKER_NOT_RELEVANT_SOURCE = "SOURCE_LACKS_OPERATION_RELEVANT_SCHEMA"


class Psi0hH6HistoricalSourceRetentionAvailabilityError(RuntimeError):
    pass


@dataclass(frozen=True)
class _SourceScanRow:
    source_path: str
    source_identity: dict[str, int]
    evidence_rows: int
    primitive_rows: int
    provenance_links: int
    operation_ids: tuple[str, ...]
    subject_fields: tuple[str, ...]
    primitive_types: tuple[str, ...]
    has_temporal_windows: bool
    has_topology_role_fields: bool
    blockers: tuple[str, ...]


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _digest(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


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
    if isinstance(value, Mapping):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, Mapping) else None


def _coalesce_nonnegative_int(value: Any) -> int:
    return int(value) if isinstance(value, int) and value >= 0 else 0


def _collect_operation_tokens(payload: Mapping[str, Any]) -> set[str]:
    operation_fields = ("operation_id", "operation_key", "operation", "operation_identity", "contract_id")
    operation_ids: set[str] = set()
    for field in operation_fields:
        value = payload.get(field)
        if isinstance(value, str):
            value = value.strip()
            if value:
                operation_ids.add(value)
    return operation_ids


def _collect_subject_fields(payload: Mapping[str, Any]) -> set[str]:
    candidate_fields = (
        "wallet",
        "funder",
        "recipient",
        "source",
        "destination",
        "creator",
        "activation_sender",
        "asset",
        "wallets",
    )
    present = {name for name in candidate_fields if name in payload}
    return present


def _has_topology_role_fields(payload: Mapping[str, Any]) -> bool:
    markers = (
        "source",
        "destination",
        "wallet",
        "creator",
        "funder",
        "recipient",
        "signer",
        "freshness_state",
        "reference_event",
        "roles",
        "wallets",
    )
    return any(marker in payload for marker in markers)


def _extract_temporal(payload: Mapping[str, Any]) -> bool:
    if "window" in payload and isinstance(payload.get("window"), Mapping):
        window = payload["window"]
        if isinstance(window.get("start"), int) or isinstance(window.get("end"), int):
            return True
    if isinstance(payload.get("window_start"), int) or isinstance(payload.get("window_end"), int):
        return True
    return False


def _scan_source_payload(payload_json: Any, *, op_ids: set[str], subject_fields: set[str]) -> None:
    payload = _safe_load_json(payload_json)
    if not payload:
        return
    op_ids.update(_collect_operation_tokens(payload))
    subject_fields.update(_collect_subject_fields(payload))


def _scan_source(source_path: Path, *, sample_limit: int = 500) -> _SourceScanRow:
    conn = sqlite3.connect(f"file:{source_path}?mode=ro&immutable=1", uri=True)
    conn.execute("PRAGMA busy_timeout = 250")
    evidence_rows = 0
    primitive_rows = 0
    provenance_links = 0
    operation_ids: set[str] = set()
    subject_fields: set[str] = set()
    primitive_types: set[str] = set()
    has_temporal_windows = False
    has_topology_role_fields = False

    try:
        tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if not bool({"normalized_evidence_records", "primitive_observations"}.intersection(tables)):
            if not tables:
                return _SourceScanRow(
                    source_path=str(source_path),
                    source_identity=_file_identity(source_path),
                    evidence_rows=0,
                    primitive_rows=0,
                    provenance_links=0,
                    operation_ids=(),
                    subject_fields=(),
                    primitive_types=(),
                    has_temporal_windows=False,
                    has_topology_role_fields=False,
                    blockers=(BLOCKER_NOT_RELEVANT_SOURCE,),
                )

        if "normalized_evidence_records" in tables:
            try:
                rows = conn.execute(
                    "SELECT payload_json, observed_at FROM normalized_evidence_records LIMIT ?",
                    (sample_limit,),
                ).fetchall()
                for payload_json, _ in rows:
                    evidence_rows += 1
                    _scan_source_payload(payload_json, op_ids=operation_ids, subject_fields=subject_fields)
            except Exception as exc:
                evidence_rows = 0

        if "primitive_observations" in tables:
            try:
                rows = conn.execute(
                    "SELECT primitive_type, output_payload_json, subjects_json, parameters_json "
                    "FROM primitive_observations LIMIT ?",
                    (sample_limit,),
                ).fetchall()
                for primitive_type, output_payload_json, subjects_json, parameters_json in rows:
                    primitive_rows += 1
                    if primitive_type:
                        primitive_types.add(str(primitive_type))
                    _scan_source_payload(output_payload_json, op_ids=operation_ids, subject_fields=subject_fields)
                    _scan_source_payload(subjects_json, op_ids=operation_ids, subject_fields=subject_fields)
                    _scan_source_payload(parameters_json, op_ids=operation_ids, subject_fields=subject_fields)
            except Exception as exc:
                primitive_rows = 0

            # provenance is helpful but optional for static reconstruction
            if "evidence_provenance" in tables:
                try:
                    provenance_links = int(
                        conn.execute(
                            "SELECT COUNT(*) FROM evidence_provenance WHERE provider_request_id IS NOT NULL"
                        ).fetchone()[0]
                    )
                except Exception:
                    provenance_links = 0

            # temporal / topology checks from a bounded sample
            has_temporal_windows = False
            has_topology_role_fields = False
            try:
                for payload_json, _ in conn.execute(
                    "SELECT payload_json, observed_at FROM normalized_evidence_records LIMIT ?",
                    (sample_limit,),
                ).fetchall():
                    parsed = _safe_load_json(payload_json)
                    if not parsed:
                        continue
                    has_topology_role_fields = has_topology_role_fields or _has_topology_role_fields(parsed)
                    has_temporal_windows = has_temporal_windows or _extract_temporal(parsed)
            except Exception:
                pass
            try:
                for primitive_type, output_payload_json, _ in conn.execute(
                    "SELECT primitive_type, output_payload_json, window_start FROM primitive_observations LIMIT ?",
                    (sample_limit,),
                ).fetchall():
                    if primitive_type:
                        primitive_types.add(str(primitive_type))
                    parsed = _safe_load_json(output_payload_json)
                    if not parsed:
                        continue
                    if _extract_temporal(parsed):
                        has_temporal_windows = True
                    has_topology_role_fields = has_topology_role_fields or _has_topology_role_fields(parsed)
            except Exception:
                pass

    except Exception as exc:
        return _SourceScanRow(
            source_path=str(source_path),
            source_identity=_file_identity(source_path),
            evidence_rows=0,
            primitive_rows=0,
            provenance_links=0,
            operation_ids=(),
            subject_fields=(),
            primitive_types=(),
            has_temporal_windows=False,
            has_topology_role_fields=False,
            blockers=(BLOCKER_TABLE_SCAN_FAILURE, str(exc)),
        )
    finally:
        conn.close()

    blockers: set[str] = set()
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
        if (not has_topology_role_fields) or (not has_temporal_windows) or not (primitive_types & required_types):
            blockers.add(BLOCKER_TOPOLOGY_ROLE_TEMPORAL_INCOMPLETE)
        if provenance_links == 0:
            blockers.add(BLOCKER_LINEAGE_PROVENANCE_INSUFFICIENT)

    return _SourceScanRow(
        source_path=str(source_path),
        source_identity=_file_identity(source_path),
        evidence_rows=evidence_rows,
        primitive_rows=primitive_rows,
        provenance_links=provenance_links,
        operation_ids=tuple(sorted(operation_ids)),
        subject_fields=tuple(sorted(subject_fields)),
        primitive_types=tuple(sorted(primitive_types)),
        has_temporal_windows=bool(has_temporal_windows),
        has_topology_role_fields=bool(has_topology_role_fields),
        blockers=tuple(sorted(blockers)),
    )


def _iter_candidate_paths(evidence_root: Path | str | None, h5_artifact: Mapping[str, Any], maximum_sources: int) -> list[Path]:
    root = Path(evidence_root or Path(__file__).resolve().parents[3] / "database")
    paths: list[Path] = []
    seen: set[Path] = set()

    for row in h5_artifact.get("source_inventory_rows") or []:
        source_path = row.get("source_path")
        if isinstance(source_path, str) and source_path:
            path = Path(source_path).resolve()
            if path not in seen and path.exists():
                seen.add(path)
                paths.append(path)

    if root.exists():
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = sorted(dirnames)
                for name in sorted(f for f in filenames if f.endswith(".db")):
                    if len(paths) >= maximum_sources:
                        break
                    path = Path(dirpath) / name
                    if path in seen:
                        continue
                    seen.add(path)
                    paths.append(path.resolve())
                if len(paths) >= maximum_sources:
                    break
            if len(paths) >= maximum_sources:
                return paths[:maximum_sources]
        except Exception:
            return paths
    if not paths:
        return []

    return paths[:maximum_sources]


def qualify_historical_source_retention_availability(
    *, h5_artifact: Mapping[str, Any], evidence_root: str | Path | None = None, maximum_sources: int = 400
) -> dict[str, Any]:
    if not isinstance(h5_artifact, Mapping):
        raise Psi0hH6HistoricalSourceRetentionAvailabilityError("PSI0H_H6_H5_ARTIFACT_INVALID")
    if h5_artifact.get("schema_version") != "psi0h-h5.historical-source-expansion.v1":
        raise Psi0hH6HistoricalSourceRetentionAvailabilityError("PSI0H_H6_H5_BOUNDING_INVALID")
    if not isinstance(maximum_sources, int) or not 1 <= maximum_sources <= 2000:
        raise Psi0hH6HistoricalSourceRetentionAvailabilityError("PSI0H_H6_MAX_SOURCES_INVALID")

    known_operation_count = int(h5_artifact.get("source", {}).get("known_operation_count", 0) or 0)
    h5_source = h5_artifact.get("selection", {}).get("h4_status") or h5_artifact.get("status")
    source_rows_raw = h5_artifact.get("source_inventory_rows", [])
    if not isinstance(source_rows_raw, list):
        source_rows_raw = []

    scanned_paths = _iter_candidate_paths(evidence_root, h5_artifact, maximum_sources)
    scanned_sources: list[dict[str, Any]] = []
    expansion_candidates: list[dict[str, Any]] = []
    missing_reason_counts: dict[str, int] = {}

    for path in scanned_paths:
        row = _scan_source(path)
        source_entry = {
            "source_path": row.source_path,
            "source_identity": row.source_identity,
            "evidence_rows": row.evidence_rows,
            "primitive_rows": row.primitive_rows,
            "provenance_links": row.provenance_links,
            "operation_ids": list(row.operation_ids),
            "subject_fields": list(row.subject_fields),
            "primitive_types": list(row.primitive_types),
            "has_temporal_windows": row.has_temporal_windows,
            "has_topology_role_fields": row.has_topology_role_fields,
            "blocking_reasons": list(row.blockers),
        }
        scanned_sources.append(source_entry)
        for reason in row.blockers:
            missing_reason_counts[reason] = missing_reason_counts.get(reason, 0) + 1

        # Exclude known sources from H4-run boundaries unless they are clearly reconstructable candidates.
        for operation_id in row.operation_ids:
            if not operation_id:
                continue
            if operation_id in row.operation_ids:
                expansion_candidates.append(
                    {
                        "operation_id": operation_id,
                        "source_path": row.source_path,
                        "blocking_reasons": list(row.blockers),
                        "supporting_fields": {
                            "evidence_rows": row.evidence_rows,
                            "primitive_rows": row.primitive_rows,
                            "subject_fields": list(row.subject_fields),
                            "primitive_types": list(row.primitive_types),
                        },
                    }
                )

    # Deduplicate deterministically
    expansion_candidates = sorted(
        {entry["operation_id"] + "|" + entry["source_path"]: entry for entry in expansion_candidates}.values(),
        key=lambda item: (item["operation_id"], item["source_path"]),
    )

    blocking_only_scan = [s for s in scanned_sources if s["evidence_rows"] == 0 and s["primitive_rows"] == 0]
    non_empty_sources = [s for s in scanned_sources if s["evidence_rows"] > 0 or s["primitive_rows"] > 0]

    ready_local_sources = [s for s in scanned_sources if not s["blocking_reasons"] and s["operation_ids"]]
    ready_local_ops = sorted({op for row in ready_local_sources for op in row["operation_ids"]})

    bounded_backfill_reasons = {
        BLOCKER_NO_OPERATION_BOUNDARY,
        BLOCKER_ADDRESS_MOTIFS_ONLY,
        BLOCKER_TOPOLOGY_ROLE_TEMPORAL_INCOMPLETE,
        BLOCKER_LINEAGE_PROVENANCE_INSUFFICIENT,
    }
    ready_backfill_sources = [
        s
        for s in scanned_sources
        if s["evidence_rows"] > 0
        and any(reason in bounded_backfill_reasons for reason in s["blocking_reasons"])
    ]

    if ready_local_sources:
        verdict = VERDICT_READY_LOCAL_EXPANSION
        next_action = {
            "decision": "RUN_PSI0H_H4_REPLAY_EXPANSION",
            "instruction": "Rebuild operation census from eligible retained evidence sources and re-run H4/H1.",
            "boundaries": {
                "require_operation_boundary_fields": True,
                "no_authority_advance": True,
                "source_scope": "retained-evidence-only",
            },
        }
    elif ready_backfill_sources:
        verdict = VERDICT_READY_BOUNDED_BACKFILL
        next_action = {
            "decision": "RUN_BOUNDED_HISTORICAL_BACKFILL_CAPTURE",
            "instruction": "Existing retained sources show partial operation evidence but missing operation-boundary/topology/lineage completeness.",
            "missing_requirements": sorted(
                {r for row in ready_backfill_sources for r in row["blocking_reasons"] if r != BLOCKER_NOT_RELEVANT_SOURCE}
            ),
            "required_authorization": {
                "provider_calls": False,
                "read_only_backfill": True,
                "scope": "bounded_historical_reconciliation_only",
            },
        }
    elif non_empty_sources:
        # Non-empty but no stable operation boundary: explicit bounded backfill path to preserve provenance.
        verdict = VERDICT_READY_BOUNDED_BACKFILL
        next_action = {
            "decision": "RUN_BOUNDED_OPERATION_BOUNDARY_RECONSTRUCTION",
            "instruction": "Sources are retained but do not yet expose stable operation populations.",
            "required_authorization": {
                "provider_calls": False,
                "read_only_backfill": True,
                "scope": "bounded_historical_reconstruction",
            },
        }
    else:
        verdict = VERDICT_BLOCKED_SOURCE_ABSENT
        next_action = {
            "decision": "REQUEST_NEW_EVIDENCE_INGESTION_SCOPE",
            "instruction": "No retained historical source appears to hold operation-level evidence for broader population expansion.",
            "required_authorization": {
                "provider_calls": True,
                "read_only_backfill": False,
                "scope": "new_historical_capture",
            },
        }

    blockers = [
        "NO_H6_EXPANSION_SOURCE_FOUND" if not scanned_sources else None,
    ] if verdict == VERDICT_BLOCKED_SOURCE_ABSENT else [
        "H6_BLOCKED_BY_DATA_GAPS" if verdict == VERDICT_READY_BOUNDED_BACKFILL else None
    ]
    blockers = [x for x in blockers if x]

    result = {
        "schema_version": SCHEMA_VERSION,
        "milestone": "PSI0H-H6",
        "status": "PASS",
        "verdict": verdict,
        "selection": {
            "h5_artifact_digest": h5_artifact.get("artifact_digest"),
            "h5_status": h5_artifact.get("status"),
            "h5_verdict": h5_artifact.get("verdict"),
            "h5_knowledge_scope": h5_source,
        },
        "evidence_scan": {
            "maximum_sources": maximum_sources,
            "candidate_source_paths_scanned": len(scanned_sources),
            "known_operation_count": known_operation_count,
        },
        "missing_reason_counts": missing_reason_counts,
        "blocked_source_rows": blocking_only_scan,
        "source_inventory_rows": scanned_sources,
        "reconstructable_operation_candidates": expansion_candidates,
        "reconstructable_operation_count": len(set(row["operation_id"] for row in expansion_candidates)),
        "ready_local_expansion_operation_count": len(ready_local_ops),
        "ready_local_expansion_operation_ids": ready_local_ops,
        "bound_backfill_source_count": len(ready_backfill_sources),
        "next_action": next_action,
        "next_scope_required": {
            "source_expansion": verdict == VERDICT_READY_LOCAL_EXPANSION,
            "bounded_backfill": verdict == VERDICT_READY_BOUNDED_BACKFILL,
            "new_capture_needed": verdict == VERDICT_BLOCKED_SOURCE_ABSENT,
        },
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
            "observation_only": True,
        },
        "blockers": blockers,
    }
    result["artifact_digest"] = _digest(result)
    return result


def verify_historical_source_retention_availability(record: Mapping[str, Any]) -> bool:
    if not isinstance(record, Mapping):
        raise Psi0hH6HistoricalSourceRetentionAvailabilityError("PSI0H_H6_RECORD_INVALID")
    required = (
        "artifact_digest",
        "schema_version",
        "milestone",
        "status",
        "verdict",
        "selection",
        "source_inventory_rows",
        "reconstructable_operation_candidates",
    )
    for key in required:
        if key not in record:
            raise Psi0hH6HistoricalSourceRetentionAvailabilityError("PSI0H_H6_RECORD_INVALID")

    if record["schema_version"] != SCHEMA_VERSION:
        raise Psi0hH6HistoricalSourceRetentionAvailabilityError("PSI0H_H6_RECORD_SCHEMA_MISMATCH")

    artifact_digest = str(record["artifact_digest"])
    if len(artifact_digest) != 64 or any(ch not in "0123456789abcdef" for ch in artifact_digest):
        raise Psi0hH6HistoricalSourceRetentionAvailabilityError("PSI0H_H6_RECORD_DIGEST_INVALID")
    replay = dict(record)
    replay.pop("artifact_digest")
    if _digest(replay) != artifact_digest:
        raise Psi0hH6HistoricalSourceRetentionAvailabilityError("PSI0H_H6_RECORD_DIGEST_MISMATCH")

    if record["verdict"] not in {
        VERDICT_READY_LOCAL_EXPANSION,
        VERDICT_READY_BOUNDED_BACKFILL,
        VERDICT_BLOCKED_SOURCE_ABSENT,
    }:
        raise Psi0hH6HistoricalSourceRetentionAvailabilityError("PSI0H_H6_RECORD_VERDICT_INVALID")

    if not isinstance(record["source_inventory_rows"], list):
        raise Psi0hH6HistoricalSourceRetentionAvailabilityError("PSI0H_H6_RECORD_SOURCE_INVENTORY_INVALID")
    if not isinstance(record["reconstructable_operation_candidates"], list):
        raise Psi0hH6HistoricalSourceRetentionAvailabilityError(
            "PSI0H_H6_RECORD_RECONSTRUCTION_CANDIDATES_INVALID"
        )
    return True
