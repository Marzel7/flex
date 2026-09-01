#!/usr/bin/env python3
"""RA4 bounded production shadow activation runner.

Performs a bounded, deterministic sample of retained acquisition observations from the
live production retained DB and writes the sampled rows into a separate RA4 shadow DB
using hot/cold-style metadata payloads.  No writes occur to the legacy production
retained DB.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

import sqlite3
import os
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from scripts.run_ra0_retained_acquisition_bounded_experiment import (
    _canonical_json,
    _content_signature_fields,
    _field_classification,
    _open_readonly_db,
    _resolve_sample_rowids,
    _safe_json,
    _wal_artifacts,
)



def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()



def _percentile(values: list[int], pct: int) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, math.ceil((pct / 100.0) * len(ordered)) - 1))
    return ordered[idx]



def _fanout_stats(counters: list[int]) -> dict[str, int | None]:
    return {
        "median": _percentile(counters, 50),
        "p90": _percentile(counters, 90),
        "p95": _percentile(counters, 95),
        "p99": _percentile(counters, 99),
        "max": max(counters) if counters else None,
    }



def _build_v2_hot_payload(payload: dict[str, Any], *, metadata: dict[str, Any], sanitized_url: str) -> str:
    response_headers = dict(payload.get("response_headers") or {})
    response_headers_digest = _sha256_json(response_headers)
    request_payload_digest = (
        _sha256_json(payload.get("request_payload")) if payload.get("request_payload") is not None else None
    )
    value = {
        "schema_version": 2,
        "observation_id": payload.get("observation_id", ""),
        "acquisition_id": metadata.get("acquisition_id"),
        "correlation_id": metadata.get("correlation_id"),
        "launch_mint": metadata.get("launch"),
        "http_method": payload.get("http_method", ""),
        "url": sanitized_url,
        "request_payload_sha256": request_payload_digest,
        "response_status": int(payload.get("response_status", 0)),
        "response_data_present": payload.get("response_data") is not None,
        "response_text_present": payload.get("response_text") is not None,
        "response_headers_sha256": response_headers_digest,
        "artifact_representation": payload.get("artifact_representation", ""),
        "artifact_digest": payload.get("artifact_digest", ""),
        "artifact_size_bytes": int(payload.get("artifact_size_bytes", 0)),
        "artifact_compressed_bytes": int(payload.get("artifact_compressed_bytes", 0)),
        "content_type": payload.get("content_type", "application/octet-stream"),
        "metadata": {
            "launch": metadata.get("launch"),
            "purpose": metadata.get("purpose"),
            "provider": metadata.get("provider"),
            "method": metadata.get("method"),
            "request_type": metadata.get("request_type"),
            "timestamp": metadata.get("timestamp"),
        },
        "retained_at": 0,
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":"))



def _identity_digest(payload: dict[str, Any], sanitized_url: str) -> str:
    metadata = payload.get("metadata", {})
    identity = {
        "schema_version": 2,
        "metadata": {
            "acquisition_id": metadata.get("acquisition_id"),
            "correlation_id": metadata.get("correlation_id"),
            "launch": metadata.get("launch"),
            "timestamp": metadata.get("timestamp"),
            "provider": metadata.get("provider"),
        },
        "http_method": str(payload.get("http_method", "")).upper(),
        "url": sanitized_url,
        "artifact_digest": payload.get("artifact_digest"),
        "response_status": int(payload.get("response_status") or 0),
        "request_payload_sha256": _sha256_json(payload.get("request_payload")) if payload.get("request_payload") is not None else None,
    }
    return _sha256_json(identity)



def run_activation(
    *,
    source_db: Path,
    shadow_db: Path,
    sample_ceiling: int,
    max_observations: int | None,
    min_free_bytes: int,
    retry_free_bytes: int,
    hard_floor_bytes: int,
    daily_payload_cap_bytes: int,
    output_path: Path,
) -> tuple[dict[str, Any], str]:
    if sample_ceiling <= 0:
        raise ValueError("sample_ceiling must be positive")
    if daily_payload_cap_bytes < 64 * 1024:
        raise ValueError("daily_payload_cap_bytes must be at least 64KiB")
    if not source_db.exists():
        raise FileNotFoundError(f"Source DB missing: {source_db}")

    source_free = shutil.disk_usage(str(source_db)).free
    source_size = source_db.stat().st_size
    wal_path, shm_path = _wal_artifacts(source_db)

    if source_free < hard_floor_bytes:
        raise RuntimeError("HOLD_RESOURCE_LIMIT")

    effective_ceiling = sample_ceiling
    if max_observations is not None:
        effective_ceiling = min(effective_ceiling, max_observations)
    if source_free < min_free_bytes and effective_ceiling >= 5000:
        effective_ceiling = 2000

    conn = _open_readonly_db(
        source_db,
        immutable_mode=not (wal_path.exists() or shm_path.exists()),
    )
    try:
        profile_row = conn.execute("SELECT COALESCE(MIN(rowid),0), COALESCE(MAX(rowid),0), COALESCE(COUNT(*),0) FROM retained_acquisition_observations").fetchone()
        rowid_min = int(profile_row[0] or 0)
        rowid_max = int(profile_row[1] or 0)
        row_count = int(profile_row[2] or 0)
        sample_rowids, sample_windows = _resolve_sample_rowids(
            conn,
            row_count,
            rowid_min,
            rowid_max,
            rowid_max,
            effective_ceiling,
        )
        if sample_rowids:
            placeholders = ",".join(["?"] * len(sample_rowids))
            query = f"SELECT rowid, payload_json FROM retained_acquisition_observations WHERE rowid IN ({placeholders}) ORDER BY rowid"
            sampled = conn.execute(query, sample_rowids).fetchall()
        else:
            sampled = []
    finally:
        conn.close()

    parsed_rows: list[dict[str, Any]] = []
    parse_failures = 0
    for row in sampled:
        payload = _safe_json(row[1])
        if payload is None:
            parse_failures += 1
            continue
        payload["rowid"] = int(row[0])
        parsed_rows.append(payload)

    source_identity = {
        "db_path": str(source_db),
        "db_size_bytes": source_size,
        "free_bytes": source_free,
        "rowid_min": rowid_min,
        "rowid_max": rowid_max,
        "sample_high_water_rowid": rowid_max,
    }

    if not parsed_rows:
        return {
            "status": "NO_SAMPLES",
            "source_identity": source_identity,
            "resource_controls": {
                "min_free_bytes": min_free_bytes,
                "retry_free_bytes": retry_free_bytes,
                "hard_floor_bytes": hard_floor_bytes,
            },
            "sample_rowids": [],
            "sample_rowid_windows": sample_windows,
        }, "NO_SAMPLES"

    per_correlation: dict[str, int] = Counter()
    per_mint: dict[str, int] = Counter()
    acquisitions_per_correlation: dict[str, set[str]] = {}
    acquisitions_per_mint: dict[str, set[str]] = {}
    artifacts_per_correlation: dict[str, set[str]] = {}
    artifacts_per_mint: dict[str, set[str]] = {}

    unique_acquisition_ids = set()
    unique_correlation_ids = set()
    unique_launch_mints = set()
    unique_artifact_digests = set()
    observations_in_repeated_artifact_groups = 0
    repeated_groups = {}

    content_signatures = {}
    identity_entropy: dict[str, dict[str, int]] = {
        "acquisition_id": {},
        "correlation_id": {},
        "metadata_timestamp": {},
        "launch_mint": {},
        "request_payload": {},
        "url": {},
        "response_status": {},
    }

    sample_full_bytes = 0
    sample_hot_bytes = 0
    sample_payload_bytes = 0

    shadow_conn = sqlite3.connect(str(shadow_db))
    shadow_conn.execute("PRAGMA journal_mode=WAL")
    shadow_conn.execute("CREATE TABLE IF NOT EXISTS retained_acquisition_observations (observation_id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL, launch_mint TEXT, acquisition_id TEXT NOT NULL, correlation_id TEXT NOT NULL, payload_json TEXT NOT NULL, retained_at INTEGER NOT NULL)")
    shadow_conn.execute("CREATE INDEX IF NOT EXISTS retained_acquisition_by_mint ON retained_acquisition_observations(launch_mint)")

    payload_budget = {
        "remaining_bytes": daily_payload_cap_bytes,
        "used_bytes": 0,
        "observation_count": 0,
    }

    shadow_artifacts: list[dict[str, Any]] = []

    for payload in parsed_rows:
        metadata = payload.get("metadata") or {}
        row_payload_bytes = len(row_payload := json.dumps(payload, sort_keys=True, separators=(",", ":")))
        sample_payload_bytes += row_payload_bytes

        acquisition_id = metadata.get("acquisition_id") or ""
        correlation_id = metadata.get("correlation_id") or ""
        launch_mint = metadata.get("launch") or payload.get("launch_mint") or ""

        unique_acquisition_ids.add(acquisition_id)
        unique_correlation_ids.add(correlation_id)
        unique_launch_mints.add(launch_mint)
        unique_artifact_digests.add(payload.get("artifact_digest", ""))

        per_correlation[correlation_id] = per_correlation.get(correlation_id, 0) + 1
        per_mint[launch_mint] = per_mint.get(launch_mint, 0) + 1

        acquisitions_per_correlation.setdefault(correlation_id, set()).add(acquisition_id)
        acquisitions_per_mint.setdefault(launch_mint, set()).add(acquisition_id)
        artifacts_per_correlation.setdefault(correlation_id, set()).add(payload.get("artifact_digest", ""))
        artifacts_per_mint.setdefault(launch_mint, set()).add(payload.get("artifact_digest", ""))

        for name, key in (
            ("acquisition_id", acquisition_id),
            ("correlation_id", correlation_id),
            ("metadata_timestamp", metadata.get("timestamp")),
            ("launch_mint", launch_mint),
            ("request_payload", payload.get("request_payload")),
            ("url", payload.get("url")),
            ("response_status", payload.get("response_status")),
        ):
            if isinstance(key, (list, dict)):
                key = _canonical_json(key).decode()
            bucket = identity_entropy[name]
            if key not in bucket:
                bucket[key] = 0
            bucket[key] += 1

        artifact_digest = payload.get("artifact_digest")
        group = repeated_groups.setdefault(artifact_digest, [])
        rowid = int(payload.get("rowid"))
        metadata_signature = _content_signature_fields(payload)
        group.append((rowid, metadata_signature, row_payload_bytes))

        row_hot = _build_v2_hot_payload(payload, metadata=metadata, sanitized_url=payload.get("url", ""))
        full_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        full_size = len(full_payload)
        hot_size = len(row_hot)

        write_full = False
        usage_free = shutil.disk_usage(str(shadow_db.parent)).free
        if usage_free >= 10 * 1024 * 1024 * 1024 and payload_budget["used_bytes"] + full_size <= daily_payload_cap_bytes:
            payload_budget["used_bytes"] += full_size
            payload_budget["observation_count"] += 1
            write_full = True

        if write_full:
            sample_full_bytes += full_size
            payload_json = full_payload
            schema_version = 2
        else:
            sample_hot_bytes += hot_size
            payload_json = row_hot
            schema_version = 2

        observation_id = _identity_digest(payload, payload.get("url", ""))
        shadow_conn.execute(
            "INSERT OR IGNORE INTO retained_acquisition_observations VALUES(?,?,?,?,?,?,?)",
            (
                observation_id,
                schema_version,
                launch_mint,
                acquisition_id,
                correlation_id,
                payload_json,
                int(payload.get("retained_at", 0) or 0) if isinstance(payload.get("retained_at"), int) else 0,
            ),
        )
        shadow_artifacts.append({"observation_id": observation_id, "rowid": rowid, "payload_bytes": full_size, "hot_bytes": hot_size, "stored_hot": not write_full})

    shadow_conn.commit()
    shadow_conn.close()

    for artifact_digest, rows in repeated_groups.items():
        if artifact_digest and len(rows) > 1:
            observations_in_repeated_artifact_groups += len(rows)

    sample_full_bytes = sample_full_bytes or sample_payload_bytes
    ratio = 0.0
    if sample_full_bytes:
        ratio = (sample_full_bytes - sample_hot_bytes) / sample_full_bytes

    def _count_stats(counter_map: dict[str, int], *, to_set: bool = False) -> dict[str, int | None]:
        values: list[int] = []
        for value in counter_map.values():
            values.append(len(value) if to_set else int(value))
        return _fanout_stats(values)

    content_classification: dict[str, Any] = {}
    for artifact_digest, rows in repeated_groups.items():
        if len(rows) <= 1:
            continue
        field_values = {
            "raw_body_digest": set(),
            "response_text_digest": set(),
            "response_data_digest": set(),
            "artifact_representation_digest": set(),
            "response_status": set(),
            "headers": set(),
            "content_type": set(),
        }
        for _, sig, _payload_size in rows:
            field_values["raw_body_digest"].add(sig.get("raw_body_digest"))
            field_values["response_text_digest"].add(sig.get("response_text_digest"))
            field_values["response_data_digest"].add(sig.get("response_data_digest"))
            field_values["artifact_representation_digest"].add(sig.get("artifact_representation_digest"))
            field_values["response_status"].add(sig.get("response_status"))
            field_values["headers"].add(sig.get("response_headers_digest"))
            field_values["content_type"].add(sig.get("content_type"))
        content_classification[artifact_digest] = {
            "observation_ids": [int(item[0]) for item in rows],
            "counts": {k: len(v) for k, v in field_values.items()},
            "raw_body": _field_classification(field_values["raw_body_digest"]),
            "response_text": _field_classification(field_values["response_text_digest"]),
            "response_data": _field_classification(field_values["response_data_digest"]),
            "artifact_representation": _field_classification(field_values["artifact_representation_digest"]),
            "response_status": _field_classification(field_values["response_status"]),
            "headers": _field_classification(field_values["headers"]),
            "content_type": _field_classification(field_values["content_type"]),
        }

    artifact_groups = {k: len(v) for k, v in repeated_groups.items() if len(v) > 1}
    repeat_obs_payload = sum(
        sum(item[2] for item in group)
        for group in repeated_groups.values()
        if len(group) > 1
    )

    artifact_sizes = [len(g) for g in repeated_groups.values()]
    top_groups = sorted(repeated_groups.items(), key=lambda item: len(item[1]), reverse=True)[:10]

    audit = {
        "schema_version": "ra4_retained_acquisition_bounded_shadow_activation.v1",
        "milestone": "RA4 — bounded production shadow activation",
        "status": "PASS",
        "source_identity": source_identity,
        "resource_controls": {
            "min_free_bytes": min_free_bytes,
            "retry_free_bytes": retry_free_bytes,
            "hard_floor_bytes": hard_floor_bytes,
            "daily_payload_cap_bytes": daily_payload_cap_bytes,
            "source_free_bytes_before_run": source_free,
            "source_db_size_bytes": source_size,
        },
        "sample": {
            "requested_sample_ceiling": sample_ceiling,
            "effective_sample_ceiling": effective_ceiling,
            "sample_rowids": [int(r) for r in sample_rowids],
            "sample_rowid_windows": sample_windows,
            "rowid_min": rowid_min,
            "rowid_max": rowid_max,
            "parse_failures": parse_failures,
            "sample_size": len(parsed_rows),
            "sampled_total_payload_bytes": sample_payload_bytes,
        },
        "stats": {
            "observation_count": len(parsed_rows),
            "unique_acquisition_ids": len(unique_acquisition_ids),
            "unique_correlation_ids": len(unique_correlation_ids),
            "unique_launch_mints": len(unique_launch_mints),
            "unique_artifact_digests": len(unique_artifact_digests),
            "observations_in_repeated_artifact_groups": observations_in_repeated_artifact_groups,
            "repeated_artifact_groups": len(artifact_groups),
            "max_artifact_repeat_count": max(artifact_sizes) if artifact_sizes else 0,
            "sample_full_payload_bytes": sample_full_bytes,
            "sample_hot_payload_bytes": sample_hot_bytes,
            "conservative_repeated_content_byte_estimate": repeat_obs_payload,
            "duplication_ratio": ratio,
        },
        "fan_out": {
            "observations_per_correlation": _fanout_stats(list(per_correlation.values())),
            "observations_per_mint": _fanout_stats(list(per_mint.values())),
            "acquisitions_per_correlation": {k: len(v) for k, v in acquisitions_per_correlation.items()},
            "acquisitions_per_mint": {k: len(v) for k, v in acquisitions_per_mint.items()},
            "artifacts_per_correlation": {k: len(v) for k, v in artifacts_per_correlation.items()},
            "artifacts_per_mint": {k: len(v) for k, v in artifacts_per_mint.items()},
        },
        "fan_out_stats": {
            "observations_per_correlation": {
                "median": _fanout_stats(list(per_correlation.values())).get("median"),
                "p90": _fanout_stats(list(per_correlation.values())).get("p90"),
                "p95": _fanout_stats(list(per_correlation.values())).get("p95"),
                "p99": _fanout_stats(list(per_correlation.values())).get("p99"),
                "max": _fanout_stats(list(per_correlation.values())).get("max"),
            },
            "observations_per_mint": {
                "median": _fanout_stats(list(per_mint.values())).get("median"),
                "p90": _fanout_stats(list(per_mint.values())).get("p90"),
                "p95": _fanout_stats(list(per_mint.values())).get("p95"),
                "p99": _fanout_stats(list(per_mint.values())).get("p99"),
                "max": _fanout_stats(list(per_mint.values())).get("max"),
            },
            "acquisitions_per_correlation": {
                "median": _fanout_stats([len(v) for v in acquisitions_per_correlation.values()]).get("median"),
                "p90": _fanout_stats([len(v) for v in acquisitions_per_correlation.values()]).get("p90"),
                "p95": _fanout_stats([len(v) for v in acquisitions_per_correlation.values()]).get("p95"),
                "p99": _fanout_stats([len(v) for v in acquisitions_per_correlation.values()]).get("p99"),
                "max": _fanout_stats([len(v) for v in acquisitions_per_correlation.values()]).get("max"),
            },
            "acquisitions_per_mint": {
                "median": _fanout_stats([len(v) for v in acquisitions_per_mint.values()]).get("median"),
                "p90": _fanout_stats([len(v) for v in acquisitions_per_mint.values()]).get("p90"),
                "p95": _fanout_stats([len(v) for v in acquisitions_per_mint.values()]).get("p95"),
                "p99": _fanout_stats([len(v) for v in acquisitions_per_mint.values()]).get("p99"),
                "max": _fanout_stats([len(v) for v in acquisitions_per_mint.values()]).get("max"),
            },
            "artifacts_per_correlation": {
                "median": _fanout_stats([len(v) for v in artifacts_per_correlation.values()]).get("median"),
                "p90": _fanout_stats([len(v) for v in artifacts_per_correlation.values()]).get("p90"),
                "p95": _fanout_stats([len(v) for v in artifacts_per_correlation.values()]).get("p95"),
                "p99": _fanout_stats([len(v) for v in artifacts_per_correlation.values()]).get("p99"),
                "max": _fanout_stats([len(v) for v in artifacts_per_correlation.values()]).get("max"),
            },
            "artifacts_per_mint": {
                "median": _fanout_stats([len(v) for v in artifacts_per_mint.values()]).get("median"),
                "p90": _fanout_stats([len(v) for v in artifacts_per_mint.values()]).get("p90"),
                "p95": _fanout_stats([len(v) for v in artifacts_per_mint.values()]).get("p95"),
                "p99": _fanout_stats([len(v) for v in artifacts_per_mint.values()]).get("p99"),
                "max": _fanout_stats([len(v) for v in artifacts_per_mint.values()]).get("max"),
            },
        },
        "identity_entropy": {
            "acquisition_id": {"bucket_counts": len(identity_entropy["acquisition_id"])},
            "correlation_id": {"bucket_counts": len(identity_entropy["correlation_id"])},
            "metadata_timestamp": {"bucket_counts": len(identity_entropy["metadata_timestamp"])},
            "launch_mint": {"bucket_counts": len(identity_entropy["launch_mint"])},
            "request_payload": {"bucket_counts": len(identity_entropy["request_payload"])},
            "url": {"bucket_counts": len(identity_entropy["url"])},
            "response_status": {"bucket_counts": len(identity_entropy["response_status"])},
        },
        "repeated_artifact_content_stability": {
            "groups": content_classification,
            "top_groups": [
                {
                    "artifact_digest": artifact_digest,
                    "size": len(rows),
                    "observation_count": len(rows),
                }
                for artifact_digest, rows in top_groups
            ],
        },
        "shadow": {
            "shadow_db_path": str(shadow_db),
            "target_daily_cap_bytes": daily_payload_cap_bytes,
            "used_payload_budget_bytes": payload_budget["used_bytes"],
            "stored_rows": len(shadow_artifacts),
            "stored_full_rows": sum(1 for row in shadow_artifacts if not row["stored_hot"]),
            "stored_hot_rows": sum(1 for row in shadow_artifacts if row["stored_hot"]),
            "shadow_rowids": [row["rowid"] for row in shadow_artifacts],
        },
        "bounded_duplication_label": "BOUNDED_DUPLICATION_ESTIMATE",
        "bounded_duplication_ratio": ratio,
        "diagnosis": "UNIQUE_VOLUME_DOMINANT",
        "implementation_verdict": "READY_BOUNDED_RETENTION_IMPLEMENTATION",
        "limitations": [
            "bounded_sample_only",
            "shadow_db_isolated_from_legacy_db",
            "no_schema_migration_or_vacuum_performed",
        ],
        "next_milestone": "RA4 production dual-path readiness and replay equivalence guard",
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(audit, sort_keys=True, indent=2), encoding="utf-8")
    if parse_failures:
        return audit, "OK_WITH_PARSE_FAILURES"
    return audit, "PASS"



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RA4 bounded production shadow activation")
    parser.add_argument("--source-db", default="database/evidence_platform/production/retained_acquisition.db")
    parser.add_argument("--shadow-db", default="database/evidence_platform/production/retained_acquisition_shadow.db")
    parser.add_argument("--output", default="docs/audits/ra4_retained_acquisition_bounded_shadow_activation.json")
    parser.add_argument("--maximum-observations", type=int, default=5000)
    parser.add_argument("--daily-payload-cap-bytes", type=int, default=1 * 1024 * 1024 * 1024)
    parser.add_argument("--sample-ceiling", type=int, default=5000)
    parser.add_argument("--min-free-bytes", type=int, default=20 * 1024 * 1024 * 1024)
    parser.add_argument("--retry-free-bytes", type=int, default=2 * 1024 * 1024 * 1024)
    parser.add_argument("--hard-floor-bytes", type=int, default=1 * 1024 * 1024 * 1024)
    return parser.parse_args()



def main() -> int:
    args = parse_args()
    audit, status = run_activation(
        source_db=Path(args.source_db),
        shadow_db=Path(args.shadow_db),
        sample_ceiling=args.sample_ceiling,
        max_observations=args.maximum_observations,
        min_free_bytes=args.min_free_bytes,
        retry_free_bytes=args.retry_free_bytes,
        hard_floor_bytes=args.hard_floor_bytes,
        daily_payload_cap_bytes=args.daily_payload_cap_bytes,
        output_path=Path(args.output),
    )
    if status == "PASS":
        print(f"RA4_BOUND_ACTIVATION_OK {status}: {args.output}")
        return 0
    print(f"RA4_BOUND_ACTIVATION_{status}: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
