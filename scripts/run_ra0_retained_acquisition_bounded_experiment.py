"""RA0 bounded retained-acquisition storage amplification experiment.

Read-only, no writes. Produces deterministic bounded sample evidence and bounded
bounds estimation only (no production writes/migrations).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlite3


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _percentile(values: list[int], pct: int) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil((pct / 100.0) * len(ordered)) - 1))
    return ordered[index]


def _safe_json(s: Any) -> dict[str, Any] | None:
    if not isinstance(s, str):
        return None
    try:
        parsed = json.loads(s)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _wal_artifacts(path: Path) -> tuple[Path, Path]:
    base = str(path.resolve())
    return Path(base + "-wal"), Path(base + "-shm")


def _open_readonly_db(path: Path, *, immutable_mode: bool) -> sqlite3.Connection:
    immutable_bit = 1 if immutable_mode else 0
    conn = sqlite3.connect(
        f"file:{path.resolve()}?mode=ro&immutable={immutable_bit}",
        uri=True,
        timeout=2,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=2000")
    return conn


def _read_db_profile(conn: sqlite3.Connection) -> dict[str, Any]:
    def query_scalar(query: str) -> Any:
        try:
            return conn.execute(query).fetchone()[0]
        except Exception:
            return None

    return {
        "rowid_min": int(query_scalar("SELECT COALESCE(MIN(rowid),0) FROM retained_acquisition_observations")),
        "rowid_max": int(query_scalar("SELECT COALESCE(MAX(rowid),0) FROM retained_acquisition_observations")),
        "row_count": int(query_scalar("SELECT COUNT(*) FROM retained_acquisition_observations")),
        "page_size": int(query_scalar("PRAGMA page_size")),
        "page_count": int(query_scalar("PRAGMA page_count")),
        "freelist_count": int(query_scalar("PRAGMA freelist_count")),
        "time_min": None,
        "time_max": None,
    }


def _resolve_sample_rowids(
    conn: sqlite3.Connection,
    row_count: int,
    rowid_min: int,
    rowid_max: int,
    high_water_rowid: int,
    ceiling: int,
) -> tuple[list[int], list[dict[str, Any]]]:
    """Deterministic, spread rowid selection with no full-table sort."""
    if row_count <= 0:
        return [], []

    target = min(row_count, ceiling)
    rowid_cap = max(rowid_min, min(rowid_max, high_water_rowid))
    if target <= 1:
        resolved = rowid_min if rowid_min <= rowid_cap else rowid_cap
        return [resolved], [{"position": rowid_min, "resolved_rowid": resolved}]

    span = max(rowid_max - rowid_min, 1)
    target_minus_one = target - 1
    rowid_positions = [rowid_min + int((span * i) // target_minus_one) for i in range(target)]

    selected: list[int] = []
    seen = set()
    windows: list[dict[str, Any]] = []

    for position in rowid_positions:
        if position > rowid_cap:
            continue
        row = conn.execute(
            "SELECT rowid FROM retained_acquisition_observations WHERE rowid >= ? AND rowid <= ? ORDER BY rowid LIMIT 1",
            (position, rowid_cap),
        ).fetchone()
        if row is None:
            continue
        rowid = int(row[0])
        if rowid in seen:
            continue
        seen.add(rowid)
        selected.append(rowid)
        windows.append({"position": position, "resolved_rowid": rowid})

    # Deterministic fill if gaps are hit.
    if len(selected) < target:
        fallback = conn.execute(
            "SELECT rowid FROM retained_acquisition_observations WHERE rowid <= ? ORDER BY rowid LIMIT ?",
            (rowid_cap, target - len(selected)),
        ).fetchall()
        for row in fallback:
            rowid = int(row[0])
            if rowid in seen:
                continue
            seen.add(rowid)
            selected.append(rowid)
            windows.append({"position": rowid, "resolved_rowid": rowid})

    return selected, windows


def _content_signature_fields(payload: dict[str, Any]) -> dict[str, Any]:
    raw_body_b64 = payload.get("raw_body_base64")
    response_data = payload.get("response_data")
    response_text = payload.get("response_text")

    if isinstance(raw_body_b64, str) and raw_body_b64:
        try:
            raw_bytes = base64.b64decode(raw_body_b64)
            raw_body_digest = hashlib.sha256(raw_bytes).hexdigest()
            raw_body_len = len(raw_bytes)
        except Exception:
            raw_body_digest = None
            raw_body_len = 0
    else:
        raw_body_digest = None
        raw_body_len = 0

    if response_data is None:
        response_data_digest = None
        response_data_len = 0
    else:
        serialized = _canonical_json(response_data)
        response_data_digest = hashlib.sha256(serialized).hexdigest()
        response_data_len = len(serialized)

    if response_text is None:
        response_text_digest = None
        response_text_len = 0
    else:
        text_bytes = str(response_text).encode()
        response_text_digest = hashlib.sha256(text_bytes).hexdigest()
        response_text_len = len(text_bytes)

    headers = payload.get("response_headers")
    response_headers_digest = _digest(headers) if isinstance(headers, dict) else None

    return {
        "raw_body_digest": raw_body_digest,
        "raw_body_len": raw_body_len,
        "response_text_digest": response_text_digest,
        "response_text_len": response_text_len,
        "response_data_digest": response_data_digest,
        "response_data_len": response_data_len,
        "artifact_representation_digest": _digest(payload.get("artifact_representation")),
        "response_status": payload.get("response_status"),
        "response_headers_digest": response_headers_digest,
        "content_type": payload.get("content_type"),
    }


def _field_classification(group_values: set[Any]) -> str:
    if not group_values or all(v is None for v in group_values):
        return "NOT_OBSERVABLE"
    cleaned = {v for v in group_values if v is not None}
    if len(cleaned) == 1:
        return "CONTENT_STABLE_FOR_SAMPLED_ARTIFACT"
    return "VARIES_WITHIN_ARTIFACT_GROUP"


@dataclass(frozen=True)
class RA0ExperimentConfig:
    db_path: Path
    sample_ceiling: int = 25000
    artifacts_to_check: int = 256
    min_free_bytes: int = 20 * 1024 * 1024 * 1024
    retry_free_bytes: int = 2 * 1024 * 1024 * 1024
    hard_floor_bytes: int = 1 * 1024 * 1024 * 1024
    maximum_observations: int | None = None


def _fanout_stats(counters: list[int]) -> dict[str, int | None]:
    return {
        "n": len(counters),
        "min": min(counters) if counters else None,
        "max": max(counters) if counters else None,
        "median": _percentile(counters, 50),
        "p90": _percentile(counters, 90),
        "p95": _percentile(counters, 95),
        "p99": _percentile(counters, 99),
    }


def run_experiment(
    config: RA0ExperimentConfig,
    *,
    force_sample_ceiling: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if config.sample_ceiling <= 0 or config.artifacts_to_check <= 0:
        raise ValueError("sample ceilings must be positive")

    if config.maximum_observations is not None:
        if config.maximum_observations <= 0:
            raise ValueError("maximum_observations must be positive")

    if not config.db_path.exists():
        raise FileNotFoundError(f"DB missing: {config.db_path}")

    source_free = shutil.disk_usage(str(config.db_path)).free
    source_size = config.db_path.stat().st_size
    wal_path, shm_path = _wal_artifacts(config.db_path)
    source_identity = {
        "db_path": str(config.db_path),
        "db_size_bytes": source_size,
        "free_bytes": source_free,
        "db_mtime_ns": config.db_path.stat().st_mtime_ns,
        "wal_exists": wal_path.exists(),
        "shm_exists": shm_path.exists(),
    }

    effective_ceiling = config.sample_ceiling
    if config.maximum_observations is not None:
        effective_ceiling = min(effective_ceiling, config.maximum_observations)
    if force_sample_ceiling is not None:
        effective_ceiling = min(effective_ceiling, force_sample_ceiling)
    if source_free < config.hard_floor_bytes:
        raise RuntimeError("disk_free_below_hard_floor")
    if source_free < config.retry_free_bytes:
        raise RuntimeError("disk_free_gate_fail_closed")

    if source_free < config.min_free_bytes and effective_ceiling >= 5000:
        raise RuntimeError("disk_free_gate_fail_closed")

    conn = _open_readonly_db(config.db_path, immutable_mode=not (source_identity["wal_exists"] or source_identity["shm_exists"]))
    try:
        profile = _read_db_profile(conn)
        sample_rowids, sample_rowid_windows = _resolve_sample_rowids(
            conn,
            profile["row_count"],
            profile["rowid_min"],
            profile["rowid_max"],
            profile["rowid_max"],
            effective_ceiling,
        )
        if not sample_rowids:
            source_rows: list[dict[str, Any]] = []
        else:
            placeholders = ",".join(["?"] * len(sample_rowids))
            query = f"SELECT rowid, payload_json FROM retained_acquisition_observations WHERE rowid IN ({placeholders}) ORDER BY rowid"
            source_rows = [dict(r) for r in conn.execute(query, sample_rowids).fetchall()]
    finally:
        conn.close()

    parsed_rows: list[dict[str, Any]] = []
    parse_failures = 0
    for row in source_rows:
        payload = _safe_json(row.get("payload_json"))
        if payload is None:
            parse_failures += 1
            continue
        payload["rowid"] = int(row["rowid"])
        payload.setdefault("metadata", {})
        payload.setdefault("payload_json_len", len(row["payload_json"]))
        parsed_rows.append(payload)

    # Identity entropy fields
    identity_fields = {
        "acquisition_id": [],
        "correlation_id": [],
        "metadata_timestamp": [],
        "launch_mint": [],
        "purpose": [],
        "provider": [],
        "method": [],
        "url": [],
        "request_payload": [],
        "response_status": [],
        "artifact_digest": [],
    }

    per_correlation = Counter()
    per_launch = Counter()
    acq_per_correlation: dict[str, set[str]] = defaultdict(set)
    acq_per_launch: dict[str, set[str]] = defaultdict(set)
    artifacts_per_correlation: dict[str, set[str]] = defaultdict(set)
    artifacts_per_launch: dict[str, set[str]] = defaultdict(set)

    artifact_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    payload_lengths: list[int] = []

    for item in parsed_rows:
        row_len = int(item.get("payload_json_len", 0))
        payload_lengths.append(row_len)

        acq = item.get("acquisition_id")
        corr = item.get("correlation_id") or item.get("metadata", {}).get("correlation_id")
        launch = item.get("launch_mint") or item.get("metadata", {}).get("launch")
        purpose = item.get("metadata", {}).get("purpose")
        provider = item.get("metadata", {}).get("provider")
        artifact_digest = item.get("artifact_digest")

        identity = {
            "acquisition_id": acq,
            "correlation_id": corr,
            "metadata_timestamp": item.get("metadata", {}).get("timestamp"),
            "launch_mint": launch,
            "purpose": purpose,
            "provider": provider,
            "method": item.get("http_method"),
            "url": item.get("url"),
            "request_payload": item.get("request_payload"),
            "response_status": item.get("response_status"),
            "artifact_digest": artifact_digest,
        }

        for key, value in identity.items():
            identity_fields[key].append(value)

        if corr is not None:
            per_correlation[corr] += 1
            if acq is not None:
                acq_per_correlation[corr].add(str(acq))
            if artifact_digest is not None:
                artifacts_per_correlation[corr].add(str(artifact_digest))

        if launch is not None:
            per_launch[launch] += 1
            if acq is not None:
                acq_per_launch[launch].add(str(acq))
            if artifact_digest is not None:
                artifacts_per_launch[launch].add(str(artifact_digest))

        if artifact_digest:
            artifact_rows[str(artifact_digest)].append(item)

    fanout = {
        "observations_per_correlation": {
            "stats": _fanout_stats(list(per_correlation.values())),
            "top_10": [{"correlation_id": k, "observations": v} for k, v in per_correlation.most_common(10)],
        },
        "observations_per_launch": {
            "stats": _fanout_stats(list(per_launch.values())),
            "top_10": [{"launch_mint": k, "observations": v} for k, v in per_launch.most_common(10)],
        },
        "acquisitions_per_correlation": {
            "stats": _fanout_stats([len(v) for v in acq_per_correlation.values()]),
            "top_10": [{"correlation_id": k, "acquisitions": len(v)} for k, v in sorted(acq_per_correlation.items(), key=lambda kv: len(kv[1]), reverse=True)[:10]],
        },
        "acquisitions_per_launch": {
            "stats": _fanout_stats([len(v) for v in acq_per_launch.values()]),
            "top_10": [{"launch_mint": k, "acquisitions": len(v)} for k, v in sorted(acq_per_launch.items(), key=lambda kv: len(kv[1]), reverse=True)[:10]],
        },
        "artifacts_per_correlation": {
            "stats": _fanout_stats([len(v) for v in artifacts_per_correlation.values()]),
            "top_10": [{"correlation_id": k, "artifacts": len(v)} for k, v in sorted(artifacts_per_correlation.items(), key=lambda kv: len(kv[1]), reverse=True)[:10]],
        },
        "artifacts_per_launch": {
            "stats": _fanout_stats([len(v) for v in artifacts_per_launch.values()]),
            "top_10": [{"launch_mint": k, "artifacts": len(v)} for k, v in sorted(artifacts_per_launch.items(), key=lambda kv: len(kv[1]), reverse=True)[:10]],
        },
    }

    repeated_artifact_groups: list[dict[str, Any]] = []
    repeated_artifact_rows = 0
    repeated_artifact_payload_bytes = 0
    max_repeat_freq = 0
    bounded_dup_bytes = 0
    content_equivalence = {
        "CONTENT_STABLE_FOR_SAMPLED_ARTIFACT": 0,
        "VARIES_WITHIN_ARTIFACT_GROUP": 0,
        "NOT_OBSERVABLE": 0,
    }
    content_equivalence_by_field = {
        "raw_body_digest": {"CONTENT_STABLE_FOR_SAMPLED_ARTIFACT": 0, "VARIES_WITHIN_ARTIFACT_GROUP": 0, "NOT_OBSERVABLE": 0},
        "response_text_digest": {"CONTENT_STABLE_FOR_SAMPLED_ARTIFACT": 0, "VARIES_WITHIN_ARTIFACT_GROUP": 0, "NOT_OBSERVABLE": 0},
        "response_data_digest": {"CONTENT_STABLE_FOR_SAMPLED_ARTIFACT": 0, "VARIES_WITHIN_ARTIFACT_GROUP": 0, "NOT_OBSERVABLE": 0},
        "artifact_representation_digest": {"CONTENT_STABLE_FOR_SAMPLED_ARTIFACT": 0, "VARIES_WITHIN_ARTIFACT_GROUP": 0, "NOT_OBSERVABLE": 0},
        "response_status": {"CONTENT_STABLE_FOR_SAMPLED_ARTIFACT": 0, "VARIES_WITHIN_ARTIFACT_GROUP": 0, "NOT_OBSERVABLE": 0},
        "response_headers_digest": {"CONTENT_STABLE_FOR_SAMPLED_ARTIFACT": 0, "VARIES_WITHIN_ARTIFACT_GROUP": 0, "NOT_OBSERVABLE": 0},
        "content_type": {"CONTENT_STABLE_FOR_SAMPLED_ARTIFACT": 0, "VARIES_WITHIN_ARTIFACT_GROUP": 0, "NOT_OBSERVABLE": 0},
    }

    identity_variance = {
        "acquisition_id": 0,
        "correlation_id": 0,
        "metadata_timestamp": 0,
        "request_payload": 0,
        "url": 0,
        "response_status": 0,
        "other_metadata": 0,
    }

    for artifact_digest, members in sorted(artifact_rows.items(), key=lambda item: len(item[1]), reverse=True)[: config.artifacts_to_check]:
        freq = len(members)
        if freq <= 1:
            continue

        repeated_artifact_rows += 1
        repeated_artifact_payload_bytes += sum(int(item.get("payload_json_len", 0)) for item in members)
        max_repeat_freq = max(max_repeat_freq, freq)

        signatures = [_content_signature_fields(item) for item in members]
        full_signature = {_digest(s) for s in signatures}
        if len(full_signature) == 1:
            group_stability = "CONTENT_STABLE_FOR_SAMPLED_ARTIFACT"
        elif len(full_signature) > 1:
            group_stability = "VARIES_WITHIN_ARTIFACT_GROUP"
        else:
            group_stability = "NOT_OBSERVABLE"
        content_equivalence[group_stability] += 1

        for field in content_equivalence_by_field:
            vals = {s[field] for s in signatures}
            cls = _field_classification(vals)
            content_equivalence_by_field[field][cls] += 1

        by_fields = {
            "acquisition_id": {item.get("acquisition_id") for item in members},
            "correlation_id": {item.get("correlation_id") or item.get("metadata", {}).get("correlation_id") for item in members},
            "metadata_timestamp": {item.get("metadata", {}).get("timestamp") for item in members},
            "request_payload": {_digest(item.get("request_payload")) for item in members},
            "url": {item.get("url") for item in members},
            "response_status": {item.get("response_status") for item in members},
            "other_metadata": {
                item.get("metadata", {}).get("purpose") for item in members
            } | {
                item.get("metadata", {}).get("provider") for item in members
            } | {
                item.get("http_method") for item in members
            },
        }

        for field, values in by_fields.items():
            identity_variance[field] += int(len({v for v in values if v is not None}) > 1)

        repeated_payload_bytes = [int(item.get("payload_json_len", 0)) for item in members]
        bounded_dup_bytes += max(0, sum(repeated_payload_bytes) - max(repeated_payload_bytes))

        repeated_artifact_groups.append({
            "artifact_digest": artifact_digest,
            "frequency": freq,
            "payload_json_rows": freq,
            "payload_json_bytes": sum(int(item.get("payload_json_len", 0)) for item in members),
            "identity_variance": {k: int(len(v) > 1) for k, v in by_fields.items()},
            "content_stability": group_stability,
        })

    # bounded only, not extrapolated beyond sampled rows.
    sample_size = len(parsed_rows)
    repeated_ratio = repeated_artifact_payload_bytes / sample_size if sample_size else 0.0
    if repeated_ratio >= 0.5 and content_equivalence["CONTENT_STABLE_FOR_SAMPLED_ARTIFACT"] > 0:
        diagnosis = "DUPLICATION_DOMINANT"
    elif repeated_artifact_rows == 0:
        diagnosis = "UNIQUE_VOLUME_DOMINANT"
    elif repeated_ratio > 0.2:
        diagnosis = "MIXED_DUPLICATION_AND_VOLUME"
    else:
        diagnosis = "UNRESOLVED"

    if diagnosis == "DUPLICATION_DOMINANT":
        implementation_verdict = "READY_DEDUPLICATED_RETENTION_IMPLEMENTATION"
    elif diagnosis == "UNIQUE_VOLUME_DOMINANT":
        implementation_verdict = "READY_BOUNDED_RETENTION_IMPLEMENTATION"
    elif diagnosis == "MIXED_DUPLICATION_AND_VOLUME":
        implementation_verdict = "READY_MIXED_RETENTION_IMPLEMENTATION"
    else:
        implementation_verdict = "HOLD_RESOURCE_LIMIT"

    end_source_size = config.db_path.stat().st_size
    end_source_free = shutil.disk_usage(str(config.db_path)).free
    end_source_identity = {
        **{k: v for k, v in source_identity.items() if k not in ("db_size_bytes", "free_bytes")},
        "db_size_bytes": end_source_size,
        "free_bytes": end_source_free,
        "db_mtime_ns": config.db_path.stat().st_mtime_ns,
    }

    experiment = {
        "schema_version": "ra0_retained_bounded_experiment.v1",
        "milestone": "RA0",
        "bounded_duplication_label": "BOUNDED_DUPLICATION_ESTIMATE",
        "bounded_duplication_extrapolation": "bounded_sample_only",
        "bounded_source_identity": source_identity,
        "source_identity_at_end": end_source_identity,
        "bounded_window": {
            "rowid_min": profile["rowid_min"],
            "rowid_max": profile["rowid_max"],
            "high_water_rowid": profile["rowid_max"],
            "time_window_utc": {"min": profile["time_min"], "max": profile["time_max"]},
            "sample_rowid_span": {"min": min(sample_rowids) if sample_rowids else None, "max": max(sample_rowids) if sample_rowids else None},
            "sample_rowids": sample_rowids,
            "sample_rowid_windows": sample_rowid_windows,
            "attempted_samples": len(sample_rowids),
            "collected_rows": sample_size,
            "parse_failures": parse_failures,
        },
        "sampling": {
            "requested_sample_ceiling": config.sample_ceiling,
            "effective_sample_ceiling": effective_ceiling,
            "artifact_sample_ceiling": config.artifacts_to_check,
            "sample_method": "deterministic_rowid_strided_windowed",
            "sample_query_plan": "rowid_strided_position_lookup_with_rowid_cap_no_full_scan",
        },
        "identity_entropy": {
            "acquisition_id": {"sampled_unique": len(set(identity_fields["acquisition_id"]))},
            "correlation_id": {"sampled_unique": len(set(identity_fields["correlation_id"]))},
            "launch_mint": {"sampled_unique": len(set(identity_fields["launch_mint"]))},
            "artifact_digest": {"sampled_unique": len(set(identity_fields["artifact_digest"]))},
        },
        "stats": {
            "observation_count": sample_size,
            "unique_acquisition_ids": len(set(identity_fields["acquisition_id"])),
            "unique_correlation_ids": len(set(identity_fields["correlation_id"])),
            "unique_launch_mints": len(set(identity_fields["launch_mint"])),
            "unique_artifact_digests": len(set(identity_fields["artifact_digest"])),
            "repeated_artifact_groups": repeated_artifact_rows,
            "observations_in_repeated_artifact_groups": repeated_artifact_rows > 0 and sum(g["payload_json_rows"] for g in repeated_artifact_groups) or 0,
            "max_artifact_repeat_count": max_repeat_freq,
            "sampled_payload_bytes": sum(payload_lengths),
            "conservative_repeated_payload_bytes": bounded_dup_bytes,
            "duplication_ratio": repeated_ratio,
        },
        "fan_out": fanout,
        "content_equivalence": {
            "group_counts": content_equivalence,
            "by_field": content_equivalence_by_field,
        },
        "identity_variance_counts": identity_variance,
        "dedup_groups": repeated_artifact_groups,
    }

    preflight = {
        "schema_version": "ra0_retained_deduplicated_preflight.v1",
        "label": "BOUNDED_DUPLICATION_ESTIMATE",
        "source_path": str(config.db_path),
        "sample_size": sample_size,
        "sample_ceiling": config.sample_ceiling,
        "effective_sample_ceiling": effective_ceiling,
        "sample_method": "deterministic_rowid_strided_windowed",
        "sample_rowids": sample_rowids,
        "sample_rowid_windows": sample_rowid_windows,
        "source_row_range": {
            "rowid_min": profile["rowid_min"],
            "rowid_max": profile["rowid_max"],
            "high_water_rowid": profile["rowid_max"],
        },
        "limitations": {
            "scope": "bounded-only",
            "sampled_rows_only": True,
            "non_extrapolated_global_total": True,
            "confidence": "bounded_sample_estimate",
        },
        "bounded_duplication_breadth": {
            "bounded_duplication_estimate_bytes": bounded_dup_bytes,
            "bounded_duplication_ratio": repeated_ratio,
        },
        "diagnosis": diagnosis,
        "implementation_verdict": implementation_verdict,
        "resource_controls": {
            "min_free_bytes": config.min_free_bytes,
            "retry_free_bytes": config.retry_free_bytes,
            "hard_floor_bytes": config.hard_floor_bytes,
            "max_sample_size": effective_ceiling,
            "requested_sample_ceiling": config.sample_ceiling,
            "max_artifacts_to_compare": config.artifacts_to_check,
            "retry_fallback_sample_size": 2000,
        },
        "blocked_outcome": [
            "RETAINED_ACQUISITION_DB_STORAGE_AMPLIFICATION_UNBOUNDED",
            "H11_ON_HOLD_UNTIL_RETENTION_GROWTH_BOUND",
        ],
        "recommended_retention_v2_path": "deduplicate content+identity via retained_v2 writer with bounded per-group controls",
        "next_milestone": "RA0 — retained-acquisition storage amplification diagnosis and deduplicated retention preflight",
    }

    experiment["bounded_duplication_estimate"] = preflight["diagnosis"]
    return experiment, preflight


def _write_outputs(experiment: dict[str, Any], preflight: dict[str, Any], experiment_path: Path, preflight_path: Path) -> tuple[str, str]:
    experiment_text = json.dumps(experiment, sort_keys=True, separators=(",", ":")) + "\n"
    preflight_text = json.dumps(preflight, sort_keys=True, separators=(",", ":")) + "\n"
    experiment_path.write_text(experiment_text, encoding="utf-8")
    preflight_path.write_text(preflight_text, encoding="utf-8")
    return _digest(experiment), _digest(preflight)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="database/evidence_platform/production/retained_acquisition.db")
    parser.add_argument("--sample-ceiling", type=int, default=5000)
    parser.add_argument("--maximum-observations", type=int, default=None)
    parser.add_argument("--artifact-samples", type=int, default=256)
    parser.add_argument("--min-free-bytes", type=int, default=20 * 1024 * 1024 * 1024)
    parser.add_argument("--retry-free-bytes", type=int, default=2 * 1024 * 1024 * 1024)
    parser.add_argument("--hard-floor-bytes", type=int, default=1 * 1024 * 1024 * 1024)
    parser.add_argument("--experiment-output", default="docs/audits/ra0_retained_acquisition_bounded_experiment.json")
    parser.add_argument("--preflight-output", default="docs/audits/ra0_retained_acquisition_deduplicated_retention_preflight.json")
    args = parser.parse_args(argv)

    config = RA0ExperimentConfig(
        db_path=Path(args.db),
        sample_ceiling=args.sample_ceiling,
        artifacts_to_check=args.artifact_samples,
        min_free_bytes=args.min_free_bytes,
        retry_free_bytes=args.retry_free_bytes,
        hard_floor_bytes=args.hard_floor_bytes,
        maximum_observations=args.maximum_observations,
    )
    requested_ceiling = config.sample_ceiling if args.maximum_observations is None else min(config.sample_ceiling, args.maximum_observations)
    requested_ceiling = min(requested_ceiling, 5000)

    source_free = shutil.disk_usage(str(config.db_path)).free
    if source_free >= config.min_free_bytes:
        primary_ceiling = requested_ceiling
    elif source_free >= config.retry_free_bytes:
        primary_ceiling = 2000
    else:
        print(json.dumps({"status": "blocked", "reason": "disk_free_below_retry_floor"}))
        return 1

    try:
        # Smoke check first: validate deterministic read path safely before larger bounded sample.
        run_experiment(config, force_sample_ceiling=min(25, requested_ceiling))
    except Exception as exc:
        print(json.dumps({"status": "smoke_failed", "error": repr(exc)}))
        return 1

    try:
        experiment, preflight = run_experiment(config, force_sample_ceiling=primary_ceiling)
    except Exception as exc:
        fallback_reason = repr(exc)
        if primary_ceiling > 2000:
            try:
                experiment, preflight = run_experiment(config, force_sample_ceiling=2000)
            except Exception as retry_exc:
                fallback_preflight = {
                    "schema_version": "ra0_retained_deduplicated_preflight.v1",
                    "label": "BOUNDED_DUPLICATION_ESTIMATE",
                    "source_path": str(config.db_path),
                    "sample_size": 0,
                    "status": "hold",
                    "blocked_outcome": [
                        "RETAINED_ACQUISITION_DB_STORAGE_AMPLIFICATION_UNBOUNDED",
                        "H11_ON_HOLD_UNTIL_RETENTION_GROWTH_BOUND",
                        "RA0_DIAGNOSTIC_RESOURCE_FLOOR_NOT_MET",
                    ],
                    "implementation_verdict": "HOLD_RESOURCE_LIMIT",
                    "retry_fallback_sample_size": 2000,
                    "primary_error": fallback_reason,
                    "retry_error": repr(retry_exc),
                }
                preflight = fallback_preflight
                experiment = {
                    "schema_version": "ra0_retained_bounded_experiment.v1",
                    "bounded_duplication_label": "BOUNDED_DUPLICATION_ESTIMATE",
                    "bounded_duplication_extrapolation": "bounded_sample_only",
                    "bounded_source_identity": {
                        "db_path": str(config.db_path),
                        "db_size_bytes": config.db_path.stat().st_size,
                        "free_bytes": shutil.disk_usage(str(config.db_path)).free,
                    },
                    "bounded_window": {"rowid_max": None, "attempted_samples": 0, "collected_rows": 0, "parse_failures": 0},
                    "sampling": {"requested_sample_ceiling": config.sample_ceiling, "effective_sample_ceiling": 2000, "artifact_sample_ceiling": config.artifacts_to_check, "sample_method": "deterministic_rowid_strided_windowed", "sample_query_plan": "rowid_strided_position_lookup_with_rowid_cap_no_full_scan"},
                    "identity_entropy": {},
                    "stats": {"observation_count": 0},
                    "fan_out": {},
                    "content_equivalence": {},
                    "identity_variance_counts": {},
                    "dedup_groups": [],
                    "bounded_duplication_estimate": "UNRESOLVED",
                }
        else:
            print(json.dumps({"status": "run_failed", "error": repr(exc)}))
            return 1

    experiment_digest, preflight_digest = _write_outputs(
        experiment,
        preflight,
        Path(args.experiment_output),
        Path(args.preflight_output),
    )

    print(
        json.dumps(
            {
                "status": "ok",
                "experiment": str(args.experiment_output),
                "experiment_digest": experiment_digest,
                "preflight": str(args.preflight_output),
                "preflight_digest": preflight_digest,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
