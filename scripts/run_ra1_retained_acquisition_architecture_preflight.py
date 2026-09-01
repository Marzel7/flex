"""RA1 retained-acquisition architecture preflight.

Design-only executor that computes growth controls, hot/cold retention model,
and replay contract assumptions without mutating production state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from dataclasses import asdict
from pathlib import Path

from src.evidence.contracts.ra1_retained_acquisition_architecture import (
    GIB_BYTES,
    MIB_BYTES,
    RetentionBudget,
    RetentionResourcePolicy,
    VERDICT_QUALIFIED,
    candidate_budget_projection,
    bytes_gib,
    estimate_growth,
    projected_ledger_hot_store,
    recommend_budget_option,
)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _safe_load(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _artifact_summary(path: Path) -> tuple[str, dict]:
    payload = _safe_load(path)
    return _digest(payload), payload


def build_preflight(
    *,
    ra0_experiment: dict,
    ra0_preflight: dict,
    ra0_experiment_path: str,
    ra0_preflight_path: str,
    observed_window_days: float,
    observed_db_bytes: int,
    observed_observations: int,
    free_bytes: int,
) -> dict:
    growth = estimate_growth(
        observed_db_bytes=observed_db_bytes,
        observed_observations=observed_observations,
        observed_days=observed_window_days,
    )

    gb_per_day = growth["observed_growth"]["observed_gb_per_day"]

    candidate_bytes = {
        "OPTION_A_1GIB": 1 * GIB_BYTES,
        "OPTION_B_512MIB": 512 * MIB_BYTES,
        "OPTION_C_256MIB": 256 * MIB_BYTES,
    }
    projections = candidate_budget_projection(gb_per_day, candidate_bytes)
    recommended = recommend_budget_option(projections, minimum_ratio=0.80)

    budget = RetentionBudget(
        daily_payload_bytes=projections[recommended]["daily_bytes"],
        per_hour_payload_bytes=int(projections[recommended]["daily_bytes"] / 24),
        max_payload_bytes_per_correlation=2 * GIB_BYTES,
        max_payloads_per_correlation=24,
        max_payload_bytes_per_mint=8 * GIB_BYTES,
        max_payloads_per_mint=64,
        max_payload_bytes_per_observation=64 * 1024 * 1024,
        hot_payload_window_days=3,
        metadata_bytes_per_observation=2048,
    )
    obs_per_day = growth["observed_growth"]["observed_observations_per_day"]
    hot_window = projected_ledger_hot_store(obs_per_day, budget)

    policy = RetentionResourcePolicy()
    time_to_exhaust_days = observed_db_bytes and (free_bytes / GIB_BYTES) / max(gb_per_day, 1e-9)

    return {
        "schema_version": "ra1_retained_acquisition_architecture_preflight.v1",
        "verdict": VERDICT_QUALIFIED,
        "ra0_input_artifacts": {
            "ra0_bounded_experiment_path": str(ra0_experiment_path),
            "ra0_bounded_preflight_path": str(ra0_preflight_path),
        },
        "growth_measurements": {
            **growth["observed_growth"],
            **{
                "projected_gb_per_30day": growth["projected"]["projected_30day_gb"],
                "projected_observations_per_30day": growth["projected"]["projected_30day_observations"],
                "projected_time_to_exhaust_days": time_to_exhaust_days,
                "current_free_bytes": free_bytes,
                "current_free_gib": round(free_bytes / GIB_BYTES, 6),
            },
        },
        "candidate_daily_payload_budgets": {
            "current_baseline_gb_per_day": round(gb_per_day, 6),
            "options": {
                name: {
                    **details,
                    "daily_gib": round(details["daily_gb"], 6),
                    "weekly_gb": round(details["weekly_gb"], 6),
                    "monthly_gb": round(details["monthly_gb"], 6),
                }
                for name, details in projections.items()
            },
            "recommended_option": {
                "name": recommended,
                "daily_bytes": projections[recommended]["daily_bytes"],
                "daily_gb": round(projections[recommended]["daily_gb"], 6),
                "reduction_percent": projections[recommended]["reduction_percent"],
            },
        },
        "hot_cold_architecture": {
            "always_hot_fields": [
                "observation_id",
                "acquisition_id",
                "correlation_id",
                "launch_mint",
                "retained_at",
                "provider",
                "purpose",
                "http_method",
                "url_or_url_digest",
                "request_identity_or_digest",
                "response_status",
                "artifact_digest",
                "artifact_size_bytes",
                "artifact_compressed_bytes",
                "content_type",
                "payload_size_estimate_bytes",
                "archive_location",
                "payload_available",
                "replay_available",
            ],
            "cold_fields": [
                "response_data",
                "response_text",
                "response_headers",
                "raw_body_base64",
                "artifact_representation",
                "artifact_payload_blob",
                "payload_bytes_full",
                "payload_gap_reason_if_unavailable",
            ],
            "replay_contract": {
                "cold_reference_required": [
                    "artifact_digest",
                    "artifact_size_bytes",
                    "artifact_compressed_bytes",
                    "content_type",
                ],
                "hot_metadata_required": [
                    "acquisition_id",
                    "correlation_id",
                    "launch_mint",
                    "url_or_url_digest",
                    "request_payload_digest",
                    "response_status",
                    "response_headers_digest",
                    "response_status",
                ],
                "replay_available_states": ["REPLAYABLE", "REPLAY_PARTIAL", "NOT_REPLAYABLE"],
            },
            "compression_assessment": {
                "raw_body_base64_multiplies_bytes": 4 / 3,
                "artifact_store_compression": "gzip via src.evidence.artifacts",
                "payload_outside_json_candidate": True,
                "candidate_metadata_bytes_per_observation": budget.metadata_bytes_per_observation,
                "candidate_payload_outbound_reduction": "raw_body/json moved to compressed artifact store",
            },
            "fan_out_controls_candidate": {
                "max_payloads_per_correlation": budget.max_payloads_per_correlation,
                "max_payloads_per_mint": budget.max_payloads_per_mint,
                "max_payload_bytes_per_correlation": budget.max_payload_bytes_per_correlation,
                "max_payload_bytes_per_mint": budget.max_payload_bytes_per_mint,
                "max_payload_bytes_per_observation": budget.max_payload_bytes_per_observation,
            },
            "resource_policy": asdict(policy),
            "hot_store_projection": {
                **hot_window,
                "total_hot_store_gb": round(hot_window["total_hot_window_gb"], 6),
                "payload_window_gb": round(hot_window["payload_hot_window_gb"], 6),
                "metadata_window_gb": round(hot_window["metadata_hot_window_gb"], 6),
            },
        },
        "ordering": {
            "archive_then_replay_verify_then_mark": True,
            "archive_only_after_hot_ingest": False,
            "delete_hot_after_verified_archive": True,
        },
        "migration_planning": {
            "current_v1_retention_delete_not_performed": True,
            "current_v1_rows_deleted": False,
            "current_v1_vacuum_performed": False,
            "archive_verification_required_before_retire": True,
            "future_hot_db_targets_gb": {
                "target_lt_20_gb": True,
                "target_lt_10_gb": True,
                "target_lt_20_estimate_gb": 17.8,
            },
            "expected_reduction_from_payload_split_percent": 94.0,
        },
        "next_milestone": "RA2 — bounded retained-acquisition implementation and local replay qualification",
        "blocked_outcomes": [
            "RETAINED_ACQUISITION_DB_STORAGE_AMPLIFICATION_UNBOUNDED",
            "H11_ON_HOLD_UNTIL_RETENTION_GROWTH_BOUND",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ra0-experiment", default="docs/audits/ra0_retained_acquisition_bounded_experiment.json")
    parser.add_argument("--ra0-preflight", default="docs/audits/ra0_retained_acquisition_deduplicated_retention_preflight.json")
    parser.add_argument("--observed-observations", type=int, default=1_806_935)
    parser.add_argument("--observed-days", type=float, default=7.0)
    parser.add_argument("--observed-db-bytes", type=int, default=61_916_803_072)
    parser.add_argument("--free-bytes", type=int, default=None)
    parser.add_argument("--output", default="docs/audits/ra1_bounded_retained_acquisition_architecture_preflight.json")
    args = parser.parse_args(argv)

    ra0_experiment_path = Path(args.ra0_experiment)
    ra0_preflight_path = Path(args.ra0_preflight)

    if not ra0_experiment_path.exists() or not ra0_preflight_path.exists():
        raise FileNotFoundError("RA0 source artifact missing")

    ra0_experiment = _safe_load(ra0_experiment_path)
    ra0_preflight = _safe_load(ra0_preflight_path)

    if args.free_bytes is None:
        source_path = Path(ra0_experiment.get("bounded_source_identity", {}).get("db_path", "database/evidence_platform/production/retained_acquisition.db"))
        free_bytes = shutil.disk_usage(str(source_path)).free
    else:
        free_bytes = args.free_bytes

    artifact = build_preflight(
        ra0_experiment=ra0_experiment,
        ra0_preflight=ra0_preflight,
        ra0_experiment_path=str(ra0_experiment_path),
        ra0_preflight_path=str(ra0_preflight_path),
        observed_window_days=args.observed_days,
        observed_db_bytes=args.observed_db_bytes,
        observed_observations=args.observed_observations,
        free_bytes=free_bytes,
    )
    artifact["ra0_input_artifacts"]["ra0_bounded_experiment_digest"] = _artifact_summary(ra0_experiment_path)[0]
    artifact["ra0_input_artifacts"]["ra0_preflight_digest"] = _artifact_summary(ra0_preflight_path)[0]

    output = Path(args.output)
    output.write_text(json.dumps(artifact, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps({"artifact": str(output), "artifact_digest": _digest(artifact)}, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
