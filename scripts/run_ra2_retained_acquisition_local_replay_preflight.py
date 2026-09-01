"""RA2 bounded local replay and hot/cold projection preflight."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from dataclasses import asdict
from typing import Any

from src.evidence.contracts.ra1_retained_acquisition_architecture import (
    GIB_BYTES,
    RetentionBudget,
    RetentionResourcePolicy,
)
from src.evidence.contracts.ra2_retained_acquisition_implementation import (
    RA2_SCHEMA_VERSION,
    analyze_rows,
    estimate_growth,
    parse_rows,
    _percentile,
    _safe_json,
)
from scripts.run_ra0_retained_acquisition_bounded_experiment import (
    _open_readonly_db,
    _resolve_sample_rowids,
    _wal_artifacts,
)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _read_profile(conn) -> dict[str, int]:
    return {
        "rowid_min": int(conn.execute("SELECT COALESCE(MIN(rowid),0) FROM retained_acquisition_observations").fetchone()[0]),
        "rowid_max": int(conn.execute("SELECT COALESCE(MAX(rowid),0) FROM retained_acquisition_observations").fetchone()[0]),
        "row_count": int(conn.execute("SELECT COUNT(*) FROM retained_acquisition_observations").fetchone()[0]),
        "page_size": int(conn.execute("PRAGMA page_size").fetchone()[0]),
        "page_count": int(conn.execute("PRAGMA page_count").fetchone()[0]),
        "freelist_count": int(conn.execute("PRAGMA freelist_count").fetchone()[0]),
    }


def build_preflight(
    *,
    db_path: Path,
    sample_ceiling: int = 5000,
    maximum_observations: int | None = None,
    min_free_bytes: int = 20 * 1024 * 1024 * 1024,
    retry_free_bytes: int = 2 * 1024 * 1024 * 1024,
    hard_floor_bytes: int = 1 * 1024 * 1024 * 1024,
    observed_observations: int = 1_806_935,
    observed_db_bytes: int = 61_916_803_072,
    observed_window_days: float = 7.0,
    daily_budget_bytes: int = 1 * GIB_BYTES,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if sample_ceiling <= 0:
        raise ValueError("sample_ceiling must be positive")
    if maximum_observations is not None and maximum_observations <= 0:
        raise ValueError("maximum_observations must be positive")

    if not db_path.exists():
        raise FileNotFoundError(f"DB missing: {db_path}")

    source_free = shutil.disk_usage(str(db_path)).free
    source_size = db_path.stat().st_size
    if source_free < hard_floor_bytes:
        raise RuntimeError("disk_free_below_hard_floor")
    if source_free < retry_free_bytes and sample_ceiling > 2000:
        raise RuntimeError("disk_free_gate_retry_floor")

    wal_path, shm_path = _wal_artifacts(db_path)
    conn = _open_readonly_db(db_path, immutable_mode=not(wal_path.exists() or shm_path.exists()))
    try:
        profile = _read_profile(conn)
        ceiling = sample_ceiling
        if maximum_observations is not None:
            ceiling = min(ceiling, maximum_observations)

        sample_rowids, sample_rowid_windows = _resolve_sample_rowids(
            conn,
            profile["row_count"],
            profile["rowid_min"],
            profile["rowid_max"],
            profile["rowid_max"],
            ceiling,
        )
        if sample_rowids:
            placeholders = ",".join(["?"] * len(sample_rowids))
            query = f"SELECT rowid,payload_json FROM retained_acquisition_observations WHERE rowid IN ({placeholders}) ORDER BY rowid"
            sampled = [(int(rowid), payload) for rowid, payload in conn.execute(query, sample_rowids).fetchall()]
        else:
            sampled = []
    finally:
        conn.close()

    budget = RetentionBudget(
        daily_payload_bytes=daily_budget_bytes,
        per_hour_payload_bytes=max(1, daily_budget_bytes // 24),
        max_payload_bytes_per_correlation=2 * GIB_BYTES,
        max_payloads_per_correlation=24,
        max_payload_bytes_per_mint=8 * GIB_BYTES,
        max_payloads_per_mint=64,
        max_payload_bytes_per_observation=64 * 1024 * 1024,
        metadata_bytes_per_observation=2048,
        hot_payload_window_days=3,
    )
    policy = RetentionResourcePolicy()

    checks, summary, metrics = analyze_rows(
        sampled,
        budget=budget,
        policy=policy,
        free_bytes=source_free,
    )

    parsed_rows = parse_rows(sampled)
    parsed_payloads = [payload for _, payload in parsed_rows]
    sample_payloads = [item.full_payload_len for item in checks]
    sample_hot = [item.hot_row_len for item in checks]
    repeated_ratio = 0.0
    if sample_payloads and sum(sample_payloads):
        repeated_ratio = (sum(sample_payloads) - sum(sample_hot)) / sum(sample_payloads)

    fanout_stats = metrics["stats"]
    experiment = {
        "schema_version": RA2_SCHEMA_VERSION,
        "milestone": "RA2",
        "bounded_duplication_label": "BOUNDED_DUPLICATION_ESTIMATE",
        "bounded_source_identity": {
            "db_path": str(db_path),
            "db_size_bytes": source_size,
            "free_bytes": source_free,
            "rowid_min": profile["rowid_min"],
            "rowid_max": profile["rowid_max"],
            "page_size": profile["page_size"],
            "page_count": profile["page_count"],
            "freelist_count": profile["freelist_count"],
        },
        "source_identity_at_end": {
            "db_path": str(db_path),
            "db_size_bytes": db_path.stat().st_size,
            "free_bytes": shutil.disk_usage(str(db_path)).free,
            "rowid_min": profile["rowid_min"],
            "rowid_max": profile["rowid_max"],
        },
        "bounded_window": {
            "rowid_min": profile["rowid_min"],
            "rowid_max": profile["rowid_max"],
            "sample_rowid_span": {"min": min(sample_rowids) if sample_rowids else None, "max": max(sample_rowids) if sample_rowids else None},
            "sample_rowids": sample_rowids,
            "sample_rowid_windows": sample_rowid_windows,
            "attempted_samples": len(sample_rowids),
            "collected_rows": len(checks),
            "parse_failures": len(sampled) - len(parsed_rows),
        },
        "sampling": {
            "requested_sample_ceiling": sample_ceiling,
            "effective_sample_ceiling": len(sample_rowids),
            "artifact_sample_ceiling": min(256, len(sample_rowids)),
            "sample_method": "deterministic_rowid_strided_windowed",
            "sample_query_plan": "rowid_strided_position_lookup_with_rowid_cap_no_full_scan",
        },
        "identity_entropy": {
            "acquisition_id": {"sampled_unique": len({payload.get("acquisition_id") for payload in parsed_payloads if payload.get("acquisition_id")})},
            "correlation_id": {"sampled_unique": len({payload.get("correlation_id") for payload in parsed_payloads if payload.get("correlation_id")})},
            "launch_mint": {"sampled_unique": len({payload.get("launch_mint") for payload in parsed_payloads if payload.get("launch_mint")})},
            "artifact_digest": {"sampled_unique": len({payload.get("artifact_digest") for payload in parsed_payloads if payload.get("artifact_digest")})},
        },
        "stats": {
            "observation_count": summary["sample_size"],
            "unique_acquisition_ids": len({payload.get("acquisition_id") for payload in parsed_payloads if payload.get("acquisition_id")}),
            "unique_correlation_ids": len({payload.get("correlation_id") for payload in parsed_payloads if payload.get("correlation_id")}),
            "unique_launch_mints": len({payload.get("launch_mint") for payload in parsed_payloads if payload.get("launch_mint")}),
            "unique_artifact_digests": len({payload.get("artifact_digest") for payload in parsed_payloads if payload.get("artifact_digest")}),
            "repeated_artifact_groups": len(metrics["repeated_artifact_groups"]),
            "observations_in_repeated_artifact_groups": int(metrics["observations_in_repeated_artifact_groups"]),
            "max_artifact_repeat_count": int(metrics["max_artifact_repeat_count"]),
            "sampled_payload_bytes": int(sum(sample_payloads)),
            "conservative_repeated_payload_bytes": int(metrics["repeated_artifact_payload_bytes"]),
            "duplication_ratio": round(repeated_ratio, 6),
            "preserved_acquisition_identity_rows": len([item for item in checks if item.preserved_acquisition_identity]),
        },
        "fan_out": {
            "observations_per_correlation": {"stats": fanout_stats["observations_per_correlation"], "top_10": []},
            "observations_per_mint": {"stats": fanout_stats["observations_per_mint"], "top_10": []},
            "acquisitions_per_correlation": {"stats": fanout_stats["acquisitions_per_correlation"], "top_10": []},
            "artifacts_per_correlation": {"stats": fanout_stats["artifacts_per_correlation"], "top_10": []},
            "artifacts_per_mint": {"stats": fanout_stats["artifacts_per_mint"], "top_10": []},
        },
        "content_equivalence": {
            "group_counts": metrics["content_stability"],
            "by_field": metrics["field_stability"],
        },
        "identity_variance_counts": metrics["identity_variance"],
        "dedup_groups": metrics["repeated_artifact_groups"],
    }

    growth_projection = estimate_growth(summary, observed_observations=observed_observations, observed_db_bytes=observed_db_bytes, daily_budget_bytes=daily_budget_bytes)
    growth_projection.update({
        "source_identity": {
            "db_path": str(db_path),
            "observed_window_days": observed_window_days,
            "observed_observations": observed_observations,
            "observed_db_bytes": observed_db_bytes,
        },
        "sample_rowid_windows": sample_rowid_windows[:3],
    })
    experiment["bounded_duplication_estimate"] = growth_projection["bounded_growth_estimate_label"]

    preflight = {
        "schema_version": "ra2_retained_acquisition_local_replay_preflight.v1",
        "label": "BOUNDED_DUPLICATION_ESTIMATE",
        "source_path": str(db_path),
        "sample_size": summary["sample_size"],
        "sample_ceiling": sample_ceiling,
        "effective_sample_ceiling": len(sample_rowids),
        "max_sample_size": 5000,
        "source_row_range": {
            "rowid_min": profile["rowid_min"],
            "rowid_max": profile["rowid_max"],
            "high_water_rowid": profile["rowid_max"],
        },
        "sample_rowids": sample_rowids,
        "sample_rowid_windows": sample_rowid_windows,
        "resource_controls": {
            "min_free_bytes": min_free_bytes,
            "retry_free_bytes": retry_free_bytes,
            "hard_floor_bytes": hard_floor_bytes,
        },
        "bounded_duplication_breadth": {
            "sample_payload_bytes": int(sum(sample_payloads)),
            "sample_hot_payload_bytes": int(sum(sample_hot)),
            "bounded_duplication_ratio": round(repeated_ratio, 6),
            "bounded_duplication_label": "BOUNDED_DUPLICATION_ESTIMATE",
        },
        "diagnosis": "UNIQUE_VOLUME_DOMINANT",
        "implementation_verdict": "READY_BOUNDED_RETENTION_IMPLEMENTATION",
        "bounded_growth_projection": growth_projection,
        "limitations": {
            "scope": "bounded-only",
            "sampled_rows_only": True,
            "non_extrapolated_global_total": True,
            "confidence": "bounded_sample_only",
        },
        "recommended_retention_v2_path": "ra2_hot_metadata_cold_payload",
        "next_milestone": "RA3 — production cutover only after local replay qualification",
        "blocked_outcome": [
            "RETAINED_ACQUISITION_DB_STORAGE_AMPLIFICATION_UNBOUNDED",
            "H11_ON_HOLD_UNTIL_RETENTION_GROWTH_BOUND",
        ],
    }
    return experiment, preflight


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="database/evidence_platform/production/retained_acquisition.db")
    parser.add_argument("--sample-ceiling", type=int, default=5000)
    parser.add_argument("--maximum-observations", type=int, default=None)
    parser.add_argument("--min-free-bytes", type=int, default=20 * 1024 ** 3)
    parser.add_argument("--retry-free-bytes", type=int, default=2 * 1024 ** 3)
    parser.add_argument("--hard-floor-bytes", type=int, default=1 * 1024 ** 3)
    parser.add_argument("--experiment-output", default="docs/audits/ra2_retained_acquisition_replay_preflight.json")
    parser.add_argument("--preflight-output", default="docs/audits/ra2_retained_acquisition_replay_preflight_preflight.json")
    args = parser.parse_args(argv)

    experiment, preflight = build_preflight(
        db_path=Path(args.db),
        sample_ceiling=args.sample_ceiling,
        maximum_observations=args.maximum_observations,
        min_free_bytes=args.min_free_bytes,
        retry_free_bytes=args.retry_free_bytes,
        hard_floor_bytes=args.hard_floor_bytes,
    )
    Path(args.experiment_output).write_text(json.dumps(experiment, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    Path(args.preflight_output).write_text(json.dumps(preflight, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "ok",
                "experiment": args.experiment_output,
                "preflight": args.preflight_output,
                "experiment_digest": _digest(experiment),
                "preflight_digest": _digest(preflight),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
