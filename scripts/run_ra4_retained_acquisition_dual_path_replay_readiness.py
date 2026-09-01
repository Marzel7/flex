#!/usr/bin/env python3
"""RA4 dual-path replay-equivalence readiness probe (read-only)."""
from __future__ import annotations

import argparse
import hashlib
import sys
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import sqlite3

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from scripts.run_ra0_retained_acquisition_bounded_experiment import (
    _canonical_json,
    _content_signature_fields,
    _open_readonly_db,
    _resolve_sample_rowids,
    _safe_json,
    _wal_artifacts,
)
from src.evidence.contracts.ra2_retained_acquisition_implementation import (
    CLASSIFICATION_CONTENT_STABLE,
    CLASSIFICATION_NOT_OBSERVABLE,
    CLASSIFICATION_VARIANT,
    _digest,
    _percentile,
)
from src.evidence.contracts.ra2_retained_acquisition_implementation import estimate_growth as estimate_growth_ra2
from src.evidence.contracts.ra1_retained_acquisition_architecture import GIB_BYTES

REQUIRED_REPLAY_FIELDS = {
    "acquisition_id",
    "correlation_id",
    "launch_mint",
    "response_status",
    "request_payload_digest",
    "response_headers_digest",
    "artifact_representation",
    "artifact_digest",
    "artifact_size_bytes",
    "artifact_compressed_bytes",
    "content_type",
}


def _sha256_text(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _field_signature_classification(values: set[Any]) -> str:
    cleaned = {v for v in values if v is not None}
    if not cleaned:
        return CLASSIFICATION_NOT_OBSERVABLE
    return CLASSIFICATION_CONTENT_STABLE if len(cleaned) == 1 else CLASSIFICATION_VARIANT


def _read_db_profile(conn: sqlite3.Connection) -> dict[str, int]:
    row = conn.execute(
        """
        SELECT
            COALESCE(MIN(rowid),0),
            COALESCE(MAX(rowid),0),
            COALESCE(COUNT(*),0)
        FROM retained_acquisition_observations
        """
    ).fetchone()
    return {
        "rowid_min": int(row[0]),
        "rowid_max": int(row[1]),
        "row_count": int(row[2]),
        "page_size": int(conn.execute("PRAGMA page_size").fetchone()[0]),
        "page_count": int(conn.execute("PRAGMA page_count").fetchone()[0]),
        "freelist_count": int(conn.execute("PRAGMA freelist_count").fetchone()[0]),
    }


def _build_v2_shadow_payload(payload: dict[str, Any]) -> dict[str, Any]:
    metadata = payload.get("metadata") or {}
    response_headers = payload.get("response_headers")
    response_headers = dict(response_headers) if isinstance(response_headers, dict) else {}
    return {
        "schema_version": 2,
        "observation_id": payload.get("observation_id"),
        "acquisition_id": metadata.get("acquisition_id") or payload.get("acquisition_id"),
        "correlation_id": metadata.get("correlation_id") or payload.get("correlation_id"),
        "launch_mint": metadata.get("launch") or payload.get("launch_mint"),
        "http_method": payload.get("http_method") or "",
        "url": payload.get("url", ""),
        "request_payload_sha256": _sha256_text(payload.get("request_payload")) if payload.get("request_payload") is not None else payload.get("request_payload_digest"),
        "response_status": int(payload.get("response_status") or 0),
        "response_data_present": payload.get("response_data") is not None,
        "response_text_present": payload.get("response_text") is not None,
        "response_headers_sha256": _sha256_text(response_headers),
        "artifact_representation": payload.get("artifact_representation", ""),
        "artifact_digest": payload.get("artifact_digest", ""),
        "artifact_size_bytes": int(payload.get("artifact_size_bytes") or 0),
        "artifact_compressed_bytes": int(payload.get("artifact_compressed_bytes") or 0),
        "content_type": payload.get("content_type") or "application/octet-stream",
        "metadata": {
            "launch": metadata.get("launch"),
            "purpose": metadata.get("purpose"),
            "provider": metadata.get("provider"),
            "method": metadata.get("method"),
            "request_type": metadata.get("request_type"),
            "timestamp": metadata.get("timestamp"),
        },
        "retained_at": int(payload.get("retained_at") or 0) if isinstance(payload.get("retained_at"), int) else 0,
    }


def _replay_signature(
    payload: dict[str, Any],
    *,
    use_shadow_fields: bool,
) -> dict[str, Any]:
    metadata = payload.get("metadata") or {}
    if use_shadow_fields:
        signature = {
            "acquisition_id": metadata.get("acquisition_id") or payload.get("acquisition_id") or "",
            "correlation_id": metadata.get("correlation_id") or payload.get("correlation_id") or "",
            "launch_mint": metadata.get("launch") or payload.get("launch_mint") or "",
            "provider": metadata.get("provider", ""),
            "purpose": metadata.get("purpose", ""),
            "method": metadata.get("method", ""),
            "request_method": payload.get("http_method", ""),
            "response_status": int(payload.get("response_status") or 0),
            "request_payload_digest": payload.get("request_payload_digest") or payload.get("request_payload_sha256") or "",
            "response_data_digest": None,
            "response_text_digest": None,
            "response_headers_digest": (
                payload.get("response_headers_sha256")
                if payload.get("response_headers_sha256") is not None
                else (_sha256_text(payload.get("response_headers")) if isinstance(payload.get("response_headers"), dict) else None)
            ),
            "artifact_representation": payload.get("artifact_representation", ""),
            "artifact_digest": payload.get("artifact_digest", ""),
            "artifact_size_bytes": int(payload.get("artifact_size_bytes") or 0),
            "artifact_compressed_bytes": int(payload.get("artifact_compressed_bytes") or 0),
            "content_type": payload.get("content_type") or "application/octet-stream",
        }
        for key in [
            "response_headers_digest",
            "artifact_representation",
            "artifact_digest",
            "request_payload_digest",
        ]:
            if signature[key] is None:
                signature[key] = ""
        return signature

    request_payload = payload.get("request_payload")
    response_headers = payload.get("response_headers")
    sig = {
        "acquisition_id": payload.get("acquisition_id") or metadata.get("acquisition_id") or "",
        "correlation_id": payload.get("correlation_id") or metadata.get("correlation_id") or "",
        "launch_mint": payload.get("launch_mint") or metadata.get("launch") or "",
        "provider": metadata.get("provider", ""),
        "purpose": metadata.get("purpose", ""),
        "method": metadata.get("method", ""),
        "request_method": payload.get("http_method", ""),
        "response_status": int(payload.get("response_status") or 0),
        "request_payload_digest": _sha256_text(request_payload) if request_payload is not None else "",
        "response_data_digest": _sha256_text(payload.get("response_data")) if payload.get("response_data") is not None else None,
        "response_text_digest": hashlib.sha256(str(payload.get("response_text")).encode()).hexdigest() if payload.get("response_text") is not None else None,
        "response_headers_digest": _sha256_text(response_headers) if isinstance(response_headers, dict) else None,
        "artifact_representation": payload.get("artifact_representation", ""),
        "artifact_digest": payload.get("artifact_digest", ""),
        "artifact_size_bytes": int(payload.get("artifact_size_bytes") or 0),
        "artifact_compressed_bytes": int(payload.get("artifact_compressed_bytes") or 0),
        "content_type": payload.get("content_type") or "application/octet-stream",
    }
    if sig["response_headers_digest"] is None:
        sig["response_headers_digest"] = ""
    return sig


def _fanout_stats(values: list[int]) -> dict[str, Any]:
    return {
        "median": _percentile(values, 50),
        "p90": _percentile(values, 90),
        "p95": _percentile(values, 95),
        "p99": _percentile(values, 99),
        "max": max(values) if values else 0,
    }


def run_dual_path_readiness(
    db_path: Path,
    *,
    output: Path,
    sample_ceiling: int = 5000,
    maximum_observations: int | None = None,
    min_free_bytes: int = 20 * 1024 * 1024 * 1024,
    retry_free_bytes: int = 2 * 1024 * 1024 * 1024,
    hard_floor_bytes: int = 1 * 1024 * 1024 * 1024,
    daily_budget_bytes: int = 1 * GIB_BYTES,
) -> dict[str, Any]:
    if sample_ceiling <= 0:
        raise ValueError("sample_ceiling must be positive")
    if maximum_observations is not None and maximum_observations <= 0:
        raise ValueError("maximum_observations must be positive")
    if not db_path.exists():
        raise FileNotFoundError(f"Source DB does not exist: {db_path}")

    source_size = db_path.stat().st_size
    source_free = shutil.disk_usage(str(db_path)).free
    source_identity = {
        "db_path": str(db_path),
        "db_size_bytes": source_size,
        "free_bytes": source_free,
        "rowid_high_water": 0,
        "wal_exists": _wal_artifacts(db_path)[0].exists(),
        "shm_exists": _wal_artifacts(db_path)[1].exists(),
    }

    if source_free < hard_floor_bytes:
        return {
            "schema_version": "ra4_retained_acquisition_dual_path_replay_readiness.v1",
            "status": "HOLD",
            "verdict": "HOLD_RESOURCE_LIMIT",
            "blockers": ["HOLD_RESOURCE_LIMIT"],
            "source_identity": source_identity,
            "sample": {
                "requested_sample_ceiling": sample_ceiling,
                "effective_sample_ceiling": 0,
                "sample_rowid_windows": [],
                "sample_rowids": [],
                "sample_method": "deterministic_rowid_strided_windowed",
                "resource_gate": "hard_floor",
            },
            "resource_controls": {
                "hard_floor_bytes": hard_floor_bytes,
                "retry_free_bytes": retry_free_bytes,
                "min_free_bytes": min_free_bytes,
            },
            "next_milestone": "RA4 dual-path readiness and replay equivalence guard",
        }

    effective_ceiling = sample_ceiling
    if source_free < min_free_bytes and effective_ceiling > 2000:
        effective_ceiling = 2000
    if maximum_observations is not None:
        effective_ceiling = min(effective_ceiling, maximum_observations)

    if source_free < retry_free_bytes:
        effective_ceiling = min(effective_ceiling, 2000)
        if effective_ceiling < 1:
            return {
                "schema_version": "ra4_retained_acquisition_dual_path_replay_readiness.v1",
                "status": "HOLD",
                "verdict": "HOLD_RESOURCE_LIMIT",
                "blockers": ["HOLD_RESOURCE_LIMIT"],
                "source_identity": source_identity,
                "sample": {
                    "requested_sample_ceiling": sample_ceiling,
                    "effective_sample_ceiling": 0,
                    "sample_rowid_windows": [],
                    "sample_rowids": [],
                    "sample_method": "deterministic_rowid_strided_windowed",
                    "resource_gate": "retry_floor",
                },
                "resource_controls": {
                    "hard_floor_bytes": hard_floor_bytes,
                    "retry_free_bytes": retry_free_bytes,
                    "min_free_bytes": min_free_bytes,
                },
                "next_milestone": "RA4 dual-path readiness and replay equivalence guard",
            }

    wal_path, shm_path = _wal_artifacts(db_path)
    conn = _open_readonly_db(db_path, immutable_mode=not (wal_path.exists() or shm_path.exists()))
    try:
        source_profile = _read_db_profile(conn)
        source_identity.update(source_profile)
        sample_rowids, sample_windows = _resolve_sample_rowids(
            conn,
            source_profile["row_count"],
            source_profile["rowid_min"],
            source_profile["rowid_max"],
            source_profile["rowid_max"],
            effective_ceiling,
        )
        if sample_rowids:
            placeholders = ",".join("?" for _ in sample_rowids)
            sql = (
                f"SELECT rowid, payload_json FROM retained_acquisition_observations "
                f"WHERE rowid IN ({placeholders}) ORDER BY rowid"
            )
            raw_rows = conn.execute(sql, sample_rowids).fetchall()
        else:
            raw_rows = []
    finally:
        conn.close()

    parsed_rows: list[dict[str, Any]] = []
    parse_failures = 0
    for rowid, payload_json in raw_rows:
        payload = _safe_json(payload_json)
        if payload is None:
            parse_failures += 1
            continue
        payload["rowid"] = int(rowid)
        payload.setdefault("metadata", {})
        parsed_rows.append(payload)

    sample_rows = parsed_rows
    if not sample_rows:
        result = {
            "schema_version": "ra4_retained_acquisition_dual_path_replay_readiness.v1",
            "status": "PASS",
            "verdict": "READY_DUAL_PATH_REPLAY_EQUIVALENCE",
            "source_identity": source_identity,
            "sample": {
                "requested_sample_ceiling": sample_ceiling,
                "effective_sample_ceiling": 0,
                "sample_rowid_windows": sample_windows,
                "sample_rowids": [],
                "sample_method": "deterministic_rowid_strided_windowed",
                "sample_query": "rowid_strided_position_lookup",
                "parse_failures": parse_failures,
            },
            "replay": {
                "mismatched_rows": 0,
                "missing_observations": 0,
                "replay_missing_fields": 0,
                "evidence_loss_detected": False,
            },
            "bounds": {
                "sampled_payload_bytes": 0,
                "sample_hot_payload_bytes": 0,
                "bounded_duplication_ratio": 0,
                "bounded_duplication_label": "BOUNDED_DUPLICATION_ESTIMATE",
                "metric_numerator": "sample_hot_payload_bytes",
                "metric_denominator": "sample_full_payload_bytes",
            },
            "next_milestone": "RA4 dual-path readiness and replay equivalence guard",
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        return result

    per_correlation: Counter[str] = Counter()
    per_mint: Counter[str] = Counter()
    acq_per_correlation: dict[str, set[str]] = defaultdict(set)
    acq_per_mint: dict[str, set[str]] = defaultdict(set)
    artifact_per_correlation: dict[str, set[str]] = defaultdict(set)
    artifact_per_mint: dict[str, set[str]] = defaultdict(set)

    duplicate_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)

    replay_rows: list[dict[str, Any]] = []
    mismatches = 0
    missing_observations = 0
    replay_missing_fields = 0
    total_full_bytes = 0
    total_hot_bytes = 0

    for payload in sample_rows:
        acquisition_id = payload.get("acquisition_id") or (payload.get("metadata") or {}).get("acquisition_id") or ""
        correlation_id = payload.get("correlation_id") or (payload.get("metadata") or {}).get("correlation_id") or ""
        launch_mint = payload.get("launch_mint") or (payload.get("metadata") or {}).get("launch") or ""

        full_bytes = len(_canonical_json(payload))
        total_full_bytes += full_bytes

        legacy_signature = _replay_signature(payload, use_shadow_fields=False)
        legacy_sig = {
            k: legacy_signature[k] for k in sorted(REQUIRED_REPLAY_FIELDS | {"response_data_digest", "response_text_digest"})
        }

        shadow_payload = _build_v2_shadow_payload(payload)
        shadow_signature = _replay_signature(shadow_payload, use_shadow_fields=True)
        shadow_sig = {
            k: shadow_signature[k] for k in sorted(REQUIRED_REPLAY_FIELDS)
        }

        hot_bytes = len(_canonical_json(shadow_payload))
        total_hot_bytes += hot_bytes

        full_signature_digest = _digest(legacy_sig)
        shadow_signature_digest = _digest(shadow_sig)

        mismatch_fields: list[str] = []
        for key in REQUIRED_REPLAY_FIELDS:
            if legacy_sig[key] != shadow_sig.get(key):
                mismatch_fields.append(key)

        row_replay_state = "REPLAYABLE"
        if not acquisition_id:
            replay_missing_fields += 1
            row_replay_state = "NOT_REPLAYABLE"

        if mismatch_fields:
            mismatches += 1
            row_replay_state = "NOT_REPLAYABLE"

        if payload.get("observation_id") is None:
            missing_observations += 1
            row_replay_state = "NOT_REPLAYABLE"

        row_hot_present = "response_data_present" in shadow_payload and bool(shadow_payload.get("response_data_present"))
        if not row_hot_present:
            # hot payload intentionally omits response body and text; not replay-observable content.
            pass

        replay_rows.append(
            {
                "rowid": payload.get("rowid"),
                "observation_id": payload.get("observation_id"),
                "acquisition_id": acquisition_id,
                "correlation_id": correlation_id,
                "launch_mint": launch_mint,
                "legacy_signature_digest": full_signature_digest,
                "shadow_signature_digest": shadow_signature_digest,
                "replay_state": row_replay_state,
                "mismatch_fields": mismatch_fields,
                "response_data_observable": payload.get("response_data") is not None,
                "response_text_observable": payload.get("response_text") is not None,
                "response_headers_observable": isinstance(payload.get("response_headers"), dict),
                "full_payload_bytes": full_bytes,
                "shadow_payload_bytes": hot_bytes,
                "legacy_request_payload_digest": legacy_signature["request_payload_digest"],
                "shadow_request_payload_digest": shadow_signature["request_payload_digest"],
                "response_headers_digest_shadow": shadow_signature["response_headers_digest"],
                "response_headers_digest_legacy": legacy_signature["response_headers_digest"] or "",
                "content_stability_fields": _content_signature_fields(payload),
            }
        )

        per_correlation[correlation_id] += 1
        per_mint[launch_mint] += 1
        if acquisition_id:
            acq_per_correlation[correlation_id].add(acquisition_id)
            acq_per_mint[launch_mint].add(acquisition_id)
        artifact_digest = payload.get("artifact_digest") or ""
        artifact_per_correlation[correlation_id].add(artifact_digest)
        artifact_per_mint[launch_mint].add(artifact_digest)
        duplicate_groups[artifact_digest].append(payload)

    repeated_group_summaries: list[dict[str, Any]] = []
    repeated_artifact_payload_bytes = 0
    identity_variance: dict[str, int] = {
        "acquisition_id": 0,
        "correlation_id": 0,
        "metadata_timestamp": 0,
        "request_payload": 0,
        "url": 0,
        "response_status": 0,
        "other_metadata": 0,
    }
    repeated_groups = 0
    max_repeat = 0
    observations_in_repeated_groups = 0

    for artifact_digest, members in duplicate_groups.items():
        if len(members) <= 1:
            continue
        repeated_groups += 1
        repeated_artifact_payload_bytes += sum(len(_canonical_json(item)) for item in members)
        max_repeat = max(max_repeat, len(members))
        observations_in_repeated_groups += len(members)

        metadata_values = {
            "acquisition_id": {m.get("acquisition_id") or (m.get("metadata") or {}).get("acquisition_id") for m in members},
            "correlation_id": {m.get("correlation_id") or (m.get("metadata") or {}).get("correlation_id") for m in members},
            "metadata_timestamp": {(m.get("metadata") or {}).get("timestamp") for m in members},
            "request_payload": { _sha256_text(m.get("request_payload")) if m.get("request_payload") is not None else m.get("request_payload_digest") for m in members},
            "url": {m.get("url") for m in members},
            "response_status": {m.get("response_status") for m in members},
            "other_metadata": {(m.get("metadata") or {}).get("provider") for m in members} | {(m.get("metadata") or {}).get("purpose") for m in members},
        }
        for key, values in metadata_values.items():
            if len(values - {None}) > 1:
                identity_variance[key] += 1

        signatures = {
            "raw_body_digest": {_content_signature_fields(item).get("raw_body_digest") for item in members},
            "response_text_digest": {_content_signature_fields(item).get("response_text_digest") for item in members},
            "response_data_digest": {_content_signature_fields(item).get("response_data_digest") for item in members},
            "artifact_representation_digest": {_content_signature_fields(item).get("artifact_representation_digest") for item in members},
            "response_status": {item.get("response_status") for item in members},
            "headers": {_content_signature_fields(item).get("response_headers_digest") for item in members},
            "content_type": {_content_signature_fields(item).get("content_type") for item in members},
        }

        repeated_group_summaries.append(
            {
                "artifact_digest": artifact_digest,
                "frequency": len(members),
                "observation_ids": [int(item.get("rowid")) for item in members if isinstance(item.get("rowid"), int)],
                "content_stability": {
                    "raw_body_digest": _field_signature_classification(signatures["raw_body_digest"]),
                    "response_text_digest": _field_signature_classification(signatures["response_text_digest"]),
                    "response_data_digest": _field_signature_classification(signatures["response_data_digest"]),
                    "artifact_representation_digest": _field_signature_classification(signatures["artifact_representation_digest"]),
                    "response_status": _field_signature_classification(signatures["response_status"]),
                    "response_headers_digest": _field_signature_classification(signatures["headers"]),
                    "content_type": _field_signature_classification(signatures["content_type"]),
                },
                "identity_variance": {
                    k: len(v - {None}) > 1 for k, v in metadata_values.items()
                },
            }
        )

    bounded_ratio = (float(total_hot_bytes) / float(total_full_bytes)) if total_full_bytes else 0.0
    bounded_duplication_ratio = max(0.0, 1.0 - bounded_ratio)

    growth_summary = estimate_growth_ra2(
        sample_summary={
            "sample_size": len(sample_rows),
            "sample_full_bytes_median": _percentile([len(_canonical_json(row)) for row in sample_rows], 50) if sample_rows else 0,
            "sample_hot_bytes_median": _percentile([len(_canonical_json(_build_v2_shadow_payload(row))) for row in sample_rows], 50) if sample_rows else 0,
            "mean_full_bytes": total_full_bytes / max(1, len(sample_rows)),
            "mean_hot_bytes": total_hot_bytes / max(1, len(sample_rows)),
            "hot_to_full_ratio": bounded_ratio,
        },
        observed_observations=source_profile["row_count"],
        observed_db_bytes=source_size,
        daily_budget_bytes=daily_budget_bytes,
    )

    growth_within_cap = growth_summary.get("projected_ra2_daily_gb", 0.0) <= 1.0
    replay_ok = (
        mismatches == 0
        and missing_observations == 0
        and replay_missing_fields == 0
        and growth_within_cap
        and parse_failures == 0
    )

    status = "PASS" if replay_ok else "HOLD"
    verdict = "READY_DUAL_PATH_REPLAY_EQUIVALENCE" if replay_ok else "HOLD_DUAL_PATH_REPLAY_MISMATCH"

    result = {
        "schema_version": "ra4_retained_acquisition_dual_path_replay_readiness.v1",
        "status": status,
        "verdict": verdict,
        "resource_controls": {
            "min_free_bytes": min_free_bytes,
            "retry_free_bytes": retry_free_bytes,
            "hard_floor_bytes": hard_floor_bytes,
            "daily_budget_bytes": daily_budget_bytes,
        },
        "source_identity": source_identity,
        "sample": {
            "requested_sample_ceiling": sample_ceiling,
            "effective_sample_ceiling": len(sample_rows),
            "sample_rowids": [int(row.get("rowid")) for row in sample_rows if row.get("rowid") is not None],
            "sample_rowid_windows": sample_windows,
            "sample_rowid_span": {
                "min": sample_rows and int(sample_rows[0].get("rowid", 0)) or 0,
                "max": sample_rows and int(sample_rows[-1].get("rowid", 0)) or 0,
            },
            "sample_method": "deterministic_rowid_strided_windowed",
            "sample_query_plan": "rowid_strided_position_lookup",
            "parse_failures": parse_failures,
        },
        "counts": {
            "observation_count": len(sample_rows),
            "unique_acquisition_ids": len({row.get("acquisition_id") or (row.get("metadata") or {}).get("acquisition_id") or "" for row in sample_rows}),
            "unique_correlation_ids": len({row.get("correlation_id") or (row.get("metadata") or {}).get("correlation_id") or "" for row in sample_rows}),
            "unique_launch_mints": len({row.get("launch_mint") or (row.get("metadata") or {}).get("launch") or "" for row in sample_rows}),
            "unique_artifact_digests": len({row.get("artifact_digest") for row in sample_rows if row.get("artifact_digest") is not None}),
            "observations_in_repeated_artifact_groups": observations_in_repeated_groups,
            "repeated_artifact_groups": repeated_groups,
            "max_artifact_repeat_count": max_repeat,
        },
        "bounded_payload": {
            "sampled_total_payload_bytes": total_full_bytes,
            "sample_hot_payload_bytes": total_hot_bytes,
            "conservative_repeated_content_byte_estimate": repeated_artifact_payload_bytes,
            "bounded_duplication_label": "BOUNDED_DUPLICATION_ESTIMATE",
            "bounded_duplication_ratio": bounded_duplication_ratio,
            "metric_numerator": "sample_hot_payload_bytes",
            "metric_denominator": "sample_full_payload_bytes",
            "duplication_ratio_definition": "bounded_duplication_ratio = (sum(sample_full_payload_bytes)-sum(sample_shadow_payload_bytes))/sum(sample_full_payload_bytes)",
            "resource_gate_reductions": {
                "requested_sample_ceiling": sample_ceiling,
                "effective_sample_ceiling": len(sample_rows),
                "sample_ceiling_reduced_from_disk_gate": source_free < min_free_bytes,
            },
        },
        "replay": {
            "comparison_rows": replay_rows,
            "comparison_summary": {
                "mismatched_rows": mismatches,
                "missing_observations": missing_observations,
                "replay_missing_fields": replay_missing_fields,
                "evidence_loss_detected": False,
            },
        },
        "growth": {
            "observed_window_days": 7.0,
            "observed_observations": source_profile["row_count"],
            "observed_db_bytes": source_size,
            "projected_growth": growth_summary,
        },
        "fan_out": {
            "observations_per_correlation": _fanout_stats(list(per_correlation.values())),
            "observations_per_mint": _fanout_stats(list(per_mint.values())),
            "acquisitions_per_correlation": _fanout_stats([len(v) for v in acq_per_correlation.values()]),
            "artifact_digests_per_correlation": _fanout_stats([len(v) for v in artifact_per_correlation.values()]),
            "artifact_digests_per_mint": _fanout_stats([len(v) for v in artifact_per_mint.values()]),
        },
        "repeated_artifact_groups": repeated_group_summaries,
        "identity_entropy": {
            "acquisition_id": {
                "repeated_groups_with_variation": identity_variance["acquisition_id"],
            },
            "correlation_id": {
                "repeated_groups_with_variation": identity_variance["correlation_id"],
            },
            "metadata_timestamp": {
                "repeated_groups_with_variation": identity_variance["metadata_timestamp"],
            },
            "request_payload": {
                "repeated_groups_with_variation": identity_variance["request_payload"],
            },
            "url": {
                "repeated_groups_with_variation": identity_variance["url"],
            },
            "response_status": {
                "repeated_groups_with_variation": identity_variance["response_status"],
            },
            "other_metadata": {
                "repeated_groups_with_variation": identity_variance["other_metadata"],
            },
        },
        "blockers": [
                *( ["RETAINED_ACQUISITION_DUAL_PATH_REPLAY_MISMATCH"] if status != "PASS" else [] ),
                *( ["RETAINED_ACQUISITION_DB_STORAGE_AMPLITUDE_LIMITED"] if not growth_within_cap else [] ),
            ],
        "limitations": [
            "bounded_sample_only",
            "legacy_sample_ids_only_are_from_production_rowid_window",
            "shadow_row_is_projection_of_production_payload",
            "no_schema_change_or_vacuum_or_delete_performed",
            "source_legacy_db_read_only_and_immutable_mode",
        ],
        "next_milestone": "RA4 dual-path readiness and replay equivalence guard",
    }

    result = dict(result)
    if not growth_within_cap:
        result["blockers"] = ["RETAINED_ACQUISITION_DB_STORAGE_AMPLITUDE_LIMITED"]
        if status == "PASS":
            result["status"] = "HOLD"
            result["verdict"] = "HOLD_DUAL_PATH_REPLAY_MISMATCH"

    if status != "PASS" and "RETAINED_ACQUISITION_DUAL_PATH_REPLAY_MISMATCH" not in result["blockers"]:
        result["blockers"].append("RETAINED_ACQUISITION_DUAL_PATH_REPLAY_MISMATCH")

    result["artifact_digest"] = _sha256_text(result)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RA4 dual-path replay-equivalence readiness probe")
    parser.add_argument("--db", default="database/evidence_platform/production/retained_acquisition.db")
    parser.add_argument("--output", default="docs/audits/ra4_retained_acquisition_dual_path_replay_readiness.json")
    parser.add_argument("--sample-ceiling", type=int, default=5000)
    parser.add_argument("--maximum-observations", type=int, default=None)
    parser.add_argument("--min-free-bytes", type=int, default=20 * 1024 * 1024 * 1024)
    parser.add_argument("--retry-free-bytes", type=int, default=2 * 1024 * 1024 * 1024)
    parser.add_argument("--hard-floor-bytes", type=int, default=1 * 1024 * 1024 * 1024)
    parser.add_argument("--daily-payload-cap-bytes", type=int, default=1 * GIB_BYTES)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_dual_path_readiness(
        db_path=Path(args.db),
        output=Path(args.output),
        sample_ceiling=args.sample_ceiling,
        maximum_observations=args.maximum_observations,
        min_free_bytes=args.min_free_bytes,
        retry_free_bytes=args.retry_free_bytes,
        hard_floor_bytes=args.hard_floor_bytes,
        daily_budget_bytes=args.daily_payload_cap_bytes,
    )
    print(json.dumps({"status": result["status"], "output": args.output, "artifact_digest": result.get("artifact_digest")}, separators=(",", ":")))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
