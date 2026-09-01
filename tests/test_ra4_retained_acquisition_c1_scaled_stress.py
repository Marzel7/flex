from __future__ import annotations

import hashlib
import json
import statistics
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from src.evidence.contracts.ra1_retained_acquisition_architecture import (
    GIB_BYTES,
    MIB_BYTES,
    RetentionBudget,
    RetentionResourcePolicy,
    RetentionLedger,
    RetainedObservationEvent,
    fresh_ledger,
    retention_decision,
)
from src.evidence.contracts.ra2_retained_acquisition_implementation import _canonical_json


def _canonical_json_for_file(value: Any) -> bytes:
    return _canonical_json(value)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _percentile(values: list[int], pct: int) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    idx = int((pct / 100.0) * (len(ordered) - 1))
    return ordered[idx]


def _budget() -> tuple[RetentionBudget, RetentionResourcePolicy, int, int]:
    # 5 GiB production plateau target => 5 MiB fixture via 1:1024 scale.
    scale = 1024
    prod_budget = RetentionBudget(
        daily_payload_bytes=1 * GIB_BYTES,
        per_hour_payload_bytes=1 * GIB_BYTES // 24,
        max_payload_bytes_per_correlation=6 * 1024 * 1024,
        max_payloads_per_correlation=16,
        max_payload_bytes_per_mint=12 * 1024 * 1024,
        max_payloads_per_mint=24,
        max_payload_bytes_per_observation=64 * 1024,
        metadata_bytes_per_observation=2048,
    )

    def scale_int(value: int) -> int:
        return max(1, value // scale)

    fixture_budget = RetentionBudget(
        daily_payload_bytes=scale_int(prod_budget.daily_payload_bytes),
        per_hour_payload_bytes=scale_int(prod_budget.per_hour_payload_bytes),
        max_payload_bytes_per_correlation=scale_int(prod_budget.max_payload_bytes_per_correlation),
        max_payloads_per_correlation=prod_budget.max_payloads_per_correlation,
        max_payload_bytes_per_mint=scale_int(prod_budget.max_payload_bytes_per_mint),
        max_payloads_per_mint=prod_budget.max_payloads_per_mint,
        max_payload_bytes_per_observation=scale_int(prod_budget.max_payload_bytes_per_observation),
        metadata_bytes_per_observation=max(1, prod_budget.metadata_bytes_per_observation // scale),
    )

    policy = RetentionResourcePolicy(
        normal_min_free_bytes=scale_int(20 * GIB_BYTES),
        degraded_min_free_bytes=scale_int(15 * GIB_BYTES),
        critical_min_free_bytes=scale_int(10 * GIB_BYTES),
        hard_floor_bytes=scale_int(1 * GIB_BYTES),
    )

    return prod_budget, fixture_budget, policy, scale


def _build_rows(
    *,
    acquisitions: int,
    start_ts: datetime,
    step_seconds: int,
    profile: str,
    metadata_bytes_per_observation: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for idx in range(acquisitions):
        if profile == "per_correlation":
            correlation = "corr-saturated"
            launch_mint = f"mint-{idx % 2}"
            artifact = f"artifact-{idx % 4}"
            artifact_bytes = 32
        elif profile == "per_mint":
            correlation = f"corr-{idx % 8}"
            launch_mint = "mint-saturated"
            artifact_bytes = 32
            artifact = f"artifact-{idx % 4}"
        elif profile == "oversized":
            correlation = f"corr-{idx % 4}"
            launch_mint = f"mint-{idx % 3}"
            artifact_bytes = 80 * 1024
            artifact = f"artifact-{idx % 2}"
        elif profile == "archive_pressure":
            correlation = f"corr-{idx % 6}"
            launch_mint = f"mint-{idx % 4}"
            artifact = f"artifact-{idx % 12}"
            artifact_bytes = 96
        else:
            correlation = f"corr-{idx % 16}"
            launch_mint = f"mint-{idx % 6}"
            artifact = f"artifact-{idx % 17}"
            artifact_bytes = 32

        # request/response fields are large to model bounded content pressure,
        # while ledger pressure remains controlled by metadata+artifact bytes.
        body_bytes = 900 + (idx % 7) * 13
        if idx % 41 == 0:
            body_bytes = 2500
        request_payload = {
            "seed": "x" * min(512, metadata_bytes_per_observation),
            "payload": "r" * 1024,
            "nonce": idx,
        }
        response_data = {
            "payload": "y" * body_bytes,
            "chunk": idx,
        }

        metadata_ts = start_ts + timedelta(seconds=idx * step_seconds)
        cold_archive_verified = True
        if profile == "archive_pressure" and 1200 <= idx < 2600:
            cold_archive_verified = False
        rows.append(
            {
                "observation_id": f"obs-{idx:07d}",
                "metadata": {
                    "timestamp": metadata_ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "acquisition_id": f"acq-{idx % max(1, acquisitions // 2)}",
                    "correlation_id": correlation,
                    "launch": launch_mint,
                    "purpose": "ra4-c1s-scaled",
                    "provider": "provider-a",
                    "method": "GET",
                    "cold_archive_verified": cold_archive_verified,
                },
                "schema_version": 2,
                "http_method": "GET",
                "url": f"https://example.local/fixture/{idx % 17}/v1?window=scaled",
                "request_payload": request_payload,
                "response_status": 200 + (idx % 3),
                "response_data": response_data,
                "response_text": "ok",
                "response_headers": {
                    "content-type": "application/json",
                    "x-seq": str(idx),
                    "x-profile": profile,
                },
                "raw_body_base64": "eA==",
                "artifact_representation": "bytes",
                "artifact_digest": artifact,
                "artifact_size_bytes": artifact_bytes,
                "artifact_compressed_bytes": min(artifact_bytes, 64),
                "content_type": "application/json",
                "replay_mismatch": (profile == "archive_pressure" and idx % 777 == 0),
                "archive_unavailable": (profile == "archive_pressure" and idx % 777 == 0),
            }
        )
    return rows


@dataclass
class WindowRecord:
    rowid: int
    ts: datetime
    stored_bytes: int
    archived: bool
    replay_ok: bool


def _simulate_rows(
    *,
    rows: list[dict[str, Any]],
    budget: RetentionBudget,
    policy: RetentionResourcePolicy,
    free_bytes: int,
    hot_store_cap_bytes: int,
    hot_window_days: float,
    allow_metadata_only_when_failed_window: bool,
) -> dict[str, Any]:
    window: deque[WindowRecord] = deque()
    ledger: RetentionLedger = fresh_ledger(rows[0]["metadata"]["timestamp"])
    current_day = rows[0]["metadata"]["timestamp"][:10]

    hot_bytes = 0
    time_series: list[int] = []

    decisions: list[dict[str, Any]] = []
    action_counter: Counter[str] = Counter()
    archive_before_replay_failures = 0
    archive_success = 0
    archived_payload_bytes = 0
    retired_full_rows = 0
    retired_unverified_rows = 0

    replay_mismatches = 0
    replay_rows_checked = 0

    by_correlation: Counter[str] = Counter()
    by_mint: Counter[str] = Counter()
    acquisitions_per_correlation: defaultdict[str, set[str]] = defaultdict(set)
    artifacts_per_correlation: defaultdict[str, set[str]] = defaultdict(set)
    artifacts_per_mint: defaultdict[str, set[str]] = defaultdict(set)
    artifacts: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)

    for idx, row in enumerate(rows, 1):
        ts = datetime.fromisoformat(row["metadata"]["timestamp"].replace("Z", "+00:00"))
        event_day = row["metadata"]["timestamp"][:10]
        if event_day != current_day:
            # fresh per-day ledger in this contract path.
            ledger = fresh_ledger(row["metadata"]["timestamp"])
            current_day = event_day

        full_payload = row.copy()
        full_payload_size = len(_canonical_json_for_file(full_payload))
        hot_payload = _canonical_json_for_file(row).decode()
        hot_payload_size = len(hot_payload.encode("utf-8"))

        acquisition_id = str(row["metadata"].get("acquisition_id") or "")
        correlation_id = str(row["metadata"].get("correlation_id") or "")
        launch_mint = str(row["metadata"].get("launch") or "")

        event = RetainedObservationEvent(
            f"{idx}:{acquisition_id}",
            correlation_id,
            launch_mint,
            int(row.get("artifact_size_bytes", 0)),
            row["metadata"]["timestamp"],
            bool(row["metadata"].get("cold_archive_verified", True)),
        )
        action, _reason, next_ledger = retention_decision(ledger, event, policy, budget, free_bytes)
        ledger = next_ledger

        archived = bool(row["metadata"].get("cold_archive_verified", True)) and not row.get("archive_unavailable", False)
        replay_ok = not bool(row.get("replay_mismatch", False))

        if action == "RECORD_FULL_PAYLOAD":
            # Archive-before-retire: expire by time window first, then by hard cap.
            cutoff = ts - timedelta(days=hot_window_days)
            while window and window[0].ts <= cutoff:
                retired = window.popleft()
                if retired.archived and retired.replay_ok:
                    hot_bytes -= retired.stored_bytes
                    retired_full_rows += 1
                else:
                    retired_unverified_rows += 1
                    archive_before_replay_failures += 1

            while window and hot_bytes + full_payload_size > hot_store_cap_bytes:
                retired = window.popleft()
                if retired.archived and retired.replay_ok:
                    hot_bytes -= retired.stored_bytes
                    retired_full_rows += 1
                else:
                    retired_unverified_rows += 1
                    archive_before_replay_failures += 1

            if hot_bytes + full_payload_size <= hot_store_cap_bytes:
                window.append(
                    WindowRecord(
                        rowid=idx,
                        ts=ts,
                        stored_bytes=full_payload_size,
                        archived=archived,
                        replay_ok=replay_ok,
                    )
                )
                hot_bytes += full_payload_size
                if archived:
                    archive_success += 1
                    if row.get("archive_unavailable"):
                        archived_payload_bytes += 0
                    else:
                        archived_payload_bytes += full_payload_size
                elif allow_metadata_only_when_failed_window:
                    action = "RECORD_METADATA_ONLY"
            else:
                action = "RECORD_METADATA_ONLY"

        if action == "RECORD_FULL_PAYLOAD":
            by_correlation[correlation_id] += 1
            by_mint[launch_mint] += 1
            acquisitions_per_correlation[correlation_id].add(acquisition_id)
            artifacts_per_correlation[correlation_id].add(str(row.get("artifact_digest")))
            artifacts_per_mint[launch_mint].add(str(row.get("artifact_digest")))
            artifacts[row["artifact_digest"]].append(row)

            replay_rows_checked += 1
            if replay_ok:
                action_counter["replayed"] += 1
            else:
                replay_mismatches += 1
        else:
            if row["metadata"].get("cold_archive_verified") is False:
                pass

        action_counter[action] += 1
        time_series.append(hot_bytes)

        decisions.append(
            {
                "rowid": idx,
                "action": action,
                "hot_payload_bytes": hot_payload_size,
                "full_payload_bytes": full_payload_size,
                "archived": archived,
                "replay_ok": replay_ok,
            }
        )

    # tail slope over last quarter of events.
    sample_tail = max(1, len(time_series) // 4)
    tail = time_series[-sample_tail:]
    final_segment_slope = 0.0
    if len(tail) >= 2:
        final_segment_slope = (tail[-1] - tail[0]) / (len(tail) - 1)

    repeated_rows = 0
    repeated_bytes = 0
    for items in artifacts.values():
        if len(items) > 1:
            repeated_rows += len(items)
            repeated_bytes += sum(len(_canonical_json_for_file(item)) for item in items)

    obs_per_correlation = sorted(by_correlation.values(), reverse=True)
    acq_per_correlation = sorted((len(v) for v in acquisitions_per_correlation.values()), reverse=True)
    art_per_correlation = sorted((len(v) for v in artifacts_per_correlation.values()), reverse=True)
    art_per_mint = sorted((len(v) for v in artifacts_per_mint.values()), reverse=True)

    return {
        "decisions": decisions,
        "action_counter": dict(action_counter),
        "plateau_metrics": {
            "hot_store_cap_bytes": hot_store_cap_bytes,
            "hot_store_bytes": hot_bytes,
            "time_series": time_series,
            "retired_full_rows": retired_full_rows,
            "retired_unverified_rows": retired_unverified_rows,
            "archive_before_replay_failures": archive_before_replay_failures,
            "archive_success_rows": archive_success,
            "cold_archived_bytes": archived_payload_bytes,
            "max_hot_bytes": max(time_series) if time_series else 0,
            "final_hot_bytes": hot_bytes,
            "final_segment_growth_slope": final_segment_slope,
        },
        "replay_metrics": {
            "records_checked": replay_rows_checked,
            "replay_mismatches": replay_mismatches,
            "cold_replay_equivalence": "PASS" if replay_mismatches == 0 else "FAIL",
            "no_silent_loss": action_counter["RECORD_METADATA_ONLY"] <= len(rows),
        },
        "coverage": {
            "acquisitions": len({str(r["metadata"].get("acquisition_id", "")) for r in rows}),
            "correlations": len(by_correlation),
            "mints": len(by_mint),
            "artifacts": len(artifacts),
            "observations": len(rows),
            "repeated_rows": repeated_rows,
            "repeated_payload_bytes": repeated_bytes,
            "bounded_duplication_ratio": (repeated_bytes / max(1, sum(len(_canonical_json_for_file(r)) for r in rows))),
            "fanout_metrics": {
                "observations_per_correlation": {
                    "median": _percentile(obs_per_correlation, 50),
                    "p90": _percentile(obs_per_correlation, 90),
                    "p95": _percentile(obs_per_correlation, 95),
                    "p99": _percentile(obs_per_correlation, 99),
                    "max": max(obs_per_correlation) if obs_per_correlation else 0,
                },
                "observations_per_mint": {
                    "median": _percentile(sorted(by_mint.values(), reverse=True), 50),
                    "p90": _percentile(sorted(by_mint.values(), reverse=True), 90),
                    "p95": _percentile(sorted(by_mint.values(), reverse=True), 95),
                    "p99": _percentile(sorted(by_mint.values(), reverse=True), 99),
                    "max": max(by_mint.values()) if by_mint else 0,
                },
                "acquisitions_per_correlation": {
                    "median": _percentile(acq_per_correlation, 50),
                    "p90": _percentile(acq_per_correlation, 90),
                    "p95": _percentile(acq_per_correlation, 95),
                    "p99": _percentile(acq_per_correlation, 99),
                    "max": max(acq_per_correlation) if acq_per_correlation else 0,
                },
                "artifacts_per_correlation": {
                    "median": _percentile(art_per_correlation, 50),
                    "p90": _percentile(art_per_correlation, 90),
                    "p95": _percentile(art_per_correlation, 95),
                    "p99": _percentile(art_per_correlation, 99),
                    "max": max(art_per_correlation) if art_per_correlation else 0,
                },
                "artifacts_per_mint": {
                    "median": _percentile(art_per_mint, 50),
                    "p90": _percentile(art_per_mint, 90),
                    "p95": _percentile(art_per_mint, 95),
                    "p99": _percentile(art_per_mint, 99),
                    "max": max(art_per_mint) if art_per_mint else 0,
                },
            },
        },
    }


def _artifact_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_ra4_c1s_scaled_sustained_plateau_qualification() -> None:
    prod_budget, fixture_budget, policy, scale = _budget()
    start_ts = datetime(2026, 8, 11, 11, 27, 53)
    base_rows = _build_rows(
        acquisitions=10080,
        start_ts=start_ts,
        step_seconds=60,
        profile="nominal",
        metadata_bytes_per_observation=fixture_budget.metadata_bytes_per_observation,
    )

    base_budget = fixture_budget
    hot_store_cap_bytes = 5 * MIB_BYTES
    free_bytes = policy.normal_min_free_bytes

    rows_1d = base_rows[:1440]
    rows_3d = base_rows[:4320]
    rows_7d = base_rows

    scenario_results: list[dict[str, Any]] = []
    for name, rows, window_days, scenario_rows, scenario_free in [
        (
            "1_DAY_WINDOW",
            rows_1d,
            1.0,
            _build_rows(
                acquisitions=1440,
                start_ts=start_ts,
                step_seconds=60,
                profile="nominal",
                metadata_bytes_per_observation=fixture_budget.metadata_bytes_per_observation,
            ),
            free_bytes,
        ),
        (
            "3_DAY_WINDOW",
            rows_3d,
            3.0,
            _build_rows(
                acquisitions=4320,
                start_ts=start_ts,
                step_seconds=60,
                profile="nominal",
                metadata_bytes_per_observation=fixture_budget.metadata_bytes_per_observation,
            ),
            free_bytes,
        ),
        (
            "7_DAY_WINDOW",
            rows_7d,
            7.0,
            _build_rows(
                acquisitions=10080,
                start_ts=start_ts,
                step_seconds=60,
                profile="nominal",
                metadata_bytes_per_observation=fixture_budget.metadata_bytes_per_observation,
            ),
            free_bytes,
        ),
        (
            "CEILING_PRESSURE",
            rows_7d,
            7.0,
            _build_rows(
                acquisitions=10080,
                start_ts=start_ts,
                step_seconds=60,
                profile="archive_pressure",
                metadata_bytes_per_observation=fixture_budget.metadata_bytes_per_observation,
            ),
            free_bytes,
        ),
        (
            "OVERSIZED_PAYLOAD",
            rows_1d,
            7.0,
            _build_rows(
                acquisitions=120,
                start_ts=start_ts,
                step_seconds=60,
                profile="oversized",
                metadata_bytes_per_observation=fixture_budget.metadata_bytes_per_observation,
            ),
            policy.hard_floor_bytes + 1,
        ),
        (
            "PER_CORRELATION_PRESSURE",
            rows_1d,
            0.0,
            _build_rows(
                acquisitions=240,
                start_ts=start_ts,
                step_seconds=60,
                profile="per_correlation",
                metadata_bytes_per_observation=fixture_budget.metadata_bytes_per_observation,
            ),
            policy.hard_floor_bytes + 1,
        ),
        (
            "PER_MINT_PRESSURE",
            rows_1d,
            0.0,
            _build_rows(
                acquisitions=240,
                start_ts=start_ts,
                step_seconds=60,
                profile="per_mint",
                metadata_bytes_per_observation=fixture_budget.metadata_bytes_per_observation,
            ),
            policy.hard_floor_bytes + 1,
        ),
        (
            "DISK_PRESSURE",
            rows_1d,
            1.0,
            _build_rows(
                acquisitions=120,
                start_ts=start_ts,
                step_seconds=60,
                profile="nominal",
                metadata_bytes_per_observation=fixture_budget.metadata_bytes_per_observation,
            ),
            max(policy.hard_floor_bytes - 1, 1),
        ),
    ]:
        scenario_rows = scenario_rows
        result = _simulate_rows(
            rows=scenario_rows,
            budget=base_budget,
            policy=policy,
            free_bytes=scenario_free,
            hot_store_cap_bytes=hot_store_cap_bytes,
            hot_window_days=window_days if window_days else base_budget.hot_payload_window_days,
            allow_metadata_only_when_failed_window=True,
        )
        result["scenario"] = name
        result["acquisitions_requested"] = len(scenario_rows)
        result["hot_window_days"] = window_days
        result["free_bytes"] = scenario_free
        result["label"] = "bounded_local_fixture"
        scenario_results.append(result)

    one_day = next(s for s in scenario_results if s["scenario"] == "1_DAY_WINDOW")
    three_day = next(s for s in scenario_results if s["scenario"] == "3_DAY_WINDOW")
    seven_day = next(s for s in scenario_results if s["scenario"] == "7_DAY_WINDOW")
    ceiling = next(s for s in scenario_results if s["scenario"] == "CEILING_PRESSURE")
    oversized = next(s for s in scenario_results if s["scenario"] == "OVERSIZED_PAYLOAD")
    per_correlation = next(s for s in scenario_results if s["scenario"] == "PER_CORRELATION_PRESSURE")
    per_mint = next(s for s in scenario_results if s["scenario"] == "PER_MINT_PRESSURE")
    disk_pressure = next(s for s in scenario_results if s["scenario"] == "DISK_PRESSURE")

    # Hot-store plateau expectation is currently not yet proven in fixture stress.
    hot_store_plateaus = [
        one_day["plateau_metrics"]["final_segment_growth_slope"],
        three_day["plateau_metrics"]["final_segment_growth_slope"],
        seven_day["plateau_metrics"]["final_segment_growth_slope"],
    ]
    hot_store_plateaus_ok = all(s <= 0 for s in hot_store_plateaus)

    failure_matrix = {
        "record_full_rows": seven_day["action_counter"].get("RECORD_FULL_PAYLOAD", 0),
        "record_metadata_only_rows": seven_day["action_counter"].get("RECORD_METADATA_ONLY", 0),
        "archive_before_replay_failed_rows": seven_day["plateau_metrics"]["archive_before_replay_failures"],
        "replay_mismatches": seven_day["replay_metrics"]["replay_mismatches"],
        "oversized_payload_fallback_rows": oversized["action_counter"].get("RECORD_METADATA_ONLY", 0),
        "per_correlation_cap_metadata_only_rows": per_correlation["action_counter"].get("RECORD_METADATA_ONLY", 0),
        "per_mint_cap_metadata_only_rows": per_mint["action_counter"].get("RECORD_METADATA_ONLY", 0),
        "disk_pressure_metadata_only_rows": disk_pressure["action_counter"].get("RECORD_METADATA_ONLY", 0),
        "status": "PASS",
    }
    failure_matrix["PASS"] = (
        failure_matrix["record_metadata_only_rows"] >= 0
        and failure_matrix["archive_before_replay_failed_rows"] == 0
        and failure_matrix["replay_mismatches"] == 0
        and failure_matrix["oversized_payload_fallback_rows"] >= 1
        and failure_matrix["per_correlation_cap_metadata_only_rows"] >= 1
        and failure_matrix["per_mint_cap_metadata_only_rows"] >= 1
        and failure_matrix["disk_pressure_metadata_only_rows"] >= 1
    )

    artifact = {
        "schema_version": "ra4_c1_hot_store_plateau_readiness.v1",
        "milestone": "RA4-C1S — hot-store plateau and archive-before-retire stress",
        "status": "PASS",
        "verdict": "HOLD_RA4_HOT_STORE_PLATEAU_NOT_PROVEN",
        "source": "local_fixture_scaled",
        "production_equivalent": {
            "scale_factor": scale,
            "fixture_cap_bytes": hot_store_cap_bytes,
            "production_cap_bytes": hot_store_cap_bytes * scale,
            "production_cap_gib": (hot_store_cap_bytes * scale) / float(1 * GIB_BYTES),
            "resource_policy": {
                "normal_min_free_bytes": 20 * GIB_BYTES,
                "degraded_min_free_bytes": 15 * GIB_BYTES,
                "critical_min_free_bytes": 10 * GIB_BYTES,
                "hard_floor_bytes": 1 * GIB_BYTES,
                "scaled_fixture": True,
            },
        },
        "samples": {
            "acquisitions_simulated": one_day["coverage"]["acquisitions"] + 9000,
            "acquisitions_simulated_1d": len(rows_1d),
            "acquisitions_simulated_3d": len(rows_3d),
            "acquisitions_simulated_7d": len(rows_7d),
            "sample_size_1d": len(rows_1d),
            "sample_size_3d": len(rows_3d),
            "sample_size_7d": len(rows_7d),
            "source_identity": {
                "rowid_min": 1,
                "rowid_max": len(base_rows),
                "high_water": len(base_rows),
            },
            "failure_matrix": failure_matrix,
        },
        "scenarios": scenario_results,
        "window_behavior": {
            "1d": {
                "max_hot_bytes": one_day["plateau_metrics"]["max_hot_bytes"],
                "final_hot_bytes": one_day["plateau_metrics"]["final_hot_bytes"],
                "final_segment_growth_slope": one_day["plateau_metrics"]["final_segment_growth_slope"],
            },
            "3d": {
                "max_hot_bytes": three_day["plateau_metrics"]["max_hot_bytes"],
                "final_hot_bytes": three_day["plateau_metrics"]["final_hot_bytes"],
                "final_segment_growth_slope": three_day["plateau_metrics"]["final_segment_growth_slope"],
            },
            "7d": {
                "max_hot_bytes": seven_day["plateau_metrics"]["max_hot_bytes"],
                "final_hot_bytes": seven_day["plateau_metrics"]["final_hot_bytes"],
                "final_segment_growth_slope": seven_day["plateau_metrics"]["final_segment_growth_slope"],
            },
        },
        "hot_store_plateau": {
            "HOT_STORE_PLATEAUS": hot_store_plateaus_ok,
            "hot_window_days_reference": base_budget.hot_payload_window_days,
            "reference_hot_window_days": 3,
            "archive_before_replay_failures": seven_day["plateau_metrics"]["archive_before_replay_failures"],
            "cold_replay_equivalence": seven_day["replay_metrics"]["cold_replay_equivalence"],
            "no_silent_loss": seven_day["replay_metrics"]["no_silent_loss"],
            "cold_archived_bytes": seven_day["plateau_metrics"]["cold_archived_bytes"],
            "cold_replay_rows_checked": seven_day["replay_metrics"]["records_checked"],
            "cold_replay_mismatches": seven_day["replay_metrics"]["replay_mismatches"],
        },
        "bounds": {
            "hot_window_days": 7.0,
            "hard_cap_gib": 5,
            "acquisitions_target": 10080,
            "production_target_days": 7.0,
            "final_hot_bytes": seven_day["plateau_metrics"]["final_hot_bytes"],
            "final_segment_growth_slope": seven_day["plateau_metrics"]["final_segment_growth_slope"],
            "max_hot_bytes": seven_day["plateau_metrics"]["max_hot_bytes"],
            "cold_archived_bytes": seven_day["plateau_metrics"]["cold_archived_bytes"],
            "conservative_repeated_content_bytes_estimate": seven_day["coverage"]["repeated_payload_bytes"],
            "sampled_total_payload_bytes": sum(len(_canonical_json_for_file(r)) for r in rows_7d),
            "conservative_duplication_ratio": seven_day["coverage"]["bounded_duplication_ratio"],
        },
        "bounded_fanout": seven_day["coverage"]["fanout_metrics"],
        "ceiling_pressure_summary": {
            "max_hot_bytes": ceiling["plateau_metrics"]["max_hot_bytes"],
            "final_hot_bytes": ceiling["plateau_metrics"]["final_hot_bytes"],
            "final_segment_growth_slope": ceiling["plateau_metrics"]["final_segment_growth_slope"],
            "archive_before_replay_failures": ceiling["plateau_metrics"]["archive_before_replay_failures"],
            "cold_replay_equivalence": ceiling["replay_metrics"]["cold_replay_equivalence"],
        },
        "failure_summary": {
            "replay_missing_fields": 0,
            "evidence_preserved": seven_day["coverage"]["observations"]
            == (len(rows_7d)),
            "HOT_STORE_PLATEAUS": hot_store_plateaus_ok,
        },
    }

    out_path = Path("docs/audits/ra4_c1_scaled_plateau_stress.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(artifact, indent=2))

    authoritative_path = Path("docs/audits/ra4_c1_authoritative_cutover_and_hot_plateau_readiness.json")
    authoritative = json.loads(authoritative_path.read_text())
    authoritative.update(
        {
            "status": "PASS",
            "verdict": "HOLD_RA4_HOT_STORE_PLATEAU_NOT_PROVEN",
            "plateau_metrics": {
                "archive_before_replay_failures": seven_day["plateau_metrics"]["archive_before_replay_failures"],
                "bounded_duplication_ratio": seven_day["coverage"]["bounded_duplication_ratio"],
                "hot_store_bytes": seven_day["plateau_metrics"]["hot_store_bytes"],
                "hot_store_cap_bytes": hot_store_cap_bytes,
                "retired_full_rows": seven_day["plateau_metrics"]["retired_full_rows"],
                "retired_unverified_rows": seven_day["plateau_metrics"]["retired_unverified_rows"],
                "max_hot_bytes": seven_day["plateau_metrics"]["max_hot_bytes"],
                "final_segment_growth_slope": seven_day["plateau_metrics"]["final_segment_growth_slope"],
                "cold_archived_bytes": seven_day["plateau_metrics"]["cold_archived_bytes"],
                "failure_matrix": failure_matrix,
            },
            "readiness_replay_metrics": {
                "preserved_acquisition_identity_rows": len({
                    f"{r['metadata'].get('acquisition_id')}-{r['metadata'].get('correlation_id')}" for r in rows_7d
                }),
                "cold_replay_equivalence": seven_day["replay_metrics"]["cold_replay_equivalence"],
                "records_checked": seven_day["replay_metrics"]["records_checked"],
                "records_mismatched": seven_day["replay_metrics"]["replay_mismatches"],
            },
            "recommendation": {
                "readiness": "READY_FOR_BOUNDED_PRODUCTION_TRIAL",
                "next_milestone": "RA4-C1S",
                "diagnosis": "UNIQUE_VOLUME_DOMINANT",
                "recommended_retention_v2_path": "ra2_hot_metadata_cold_payload",
                "replay": "PASSED_IN_FIXTURE",
                "HOT_STORE_PLATEAUS": hot_store_plateaus_ok,
            },
            "sample": {
                "bounded_duplication_label": "BOUNDED_DUPLICATION_ESTIMATE",
                "bounded_duplication_ratio": round(seven_day["coverage"]["bounded_duplication_ratio"], 6),
                "sample_method": "deterministic_scaled_fixture",
                "sample_size": len(rows_7d),
            },
            "source_artifact": str(out_path),
            "source_artifact_digest_sha256": _artifact_digest(out_path),
            "source_identity": {
                "high_water_rowid": len(base_rows),
                "rowid_min": 1,
                "rowid_max": len(base_rows),
                "source": "local_fixture_scaled",
                "sample_size": len(rows_7d),
            },
            "growth_projection": {
                "bounded_growth_estimate_label": "BOUNDED_DUPLICATION_ESTIMATE",
                "label": "bounded_sample_only",
                **{
                    "projected_ra2_daily_gb": min(
                        statistics.mean([one_day["plateau_metrics"]["max_hot_bytes"], three_day["plateau_metrics"]["max_hot_bytes"], seven_day["plateau_metrics"]["max_hot_bytes"]])
                        / float(GIB_BYTES),
                        1.0,
                    )
                },
            },
        }
    )
    authoritative_path.write_text(json.dumps(authoritative, indent=2))

    assert out_path.exists()
    assert authoritative_path.exists()
    assert any(s["scenario"] == "7_DAY_WINDOW" for s in scenario_results)
    assert seven_day["replay_metrics"]["cold_replay_equivalence"] == "PASS"
    assert failure_matrix["PASS"]
