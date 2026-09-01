from __future__ import annotations

from pathlib import Path

from src.evidence.contracts.ra1_retained_acquisition_architecture import RetentionBudget, RetentionResourcePolicy
from src.evidence.contracts.ra2_retained_acquisition_implementation import (
    _canonical_json,
    RA4_C1_HOT_STORE_CAP_BYTES,
    simulate_hot_store_plateau,
)


def _fixture_payload(idx: int, *, acquisition: str, correlation: str, mint: str, artifact: str, status: int = 200, cold_archive_verified: bool = True) -> dict:
    return {
        "observation_id": f"obs-{idx}",
        "metadata": {
            "timestamp": f"2026-08-18T00:00:{idx:02d}Z",
            "acquisition_id": acquisition,
            "correlation_id": correlation,
            "launch": mint,
            "purpose": "probe",
            "provider": "provider-a",
            "method": "GET",
            "cold_archive_verified": cold_archive_verified,
        },
        "schema_version": 2,
        "http_method": "GET",
        "url": "https://example.local/data",
        "request_payload": {"idx": idx, "payload": "x" * 32},
        "response_status": status,
        "response_data": {"value": idx, "blob": "y" * 32},
        "response_text": "ok",
        "response_headers": {"content-type": "application/json"},
        "raw_body_base64": "eA==",
        "artifact_representation": "bytes",
        "artifact_digest": artifact,
        "artifact_size_bytes": 128,
        "artifact_compressed_bytes": 32,
        "content_type": "application/json",
    }


def _as_rows(rows: list[dict[str, object]]) -> list[tuple[int, str]]:
    return [(idx + 1, _canonical_json(row).decode()) for idx, row in enumerate(rows)]


def _simulate(rows, *, daily_budget: int = 1 * 1024 * 1024 * 1024, policy: RetentionResourcePolicy | None = None, free_bytes: int = 25 * 1024 ** 3):
    budget = RetentionBudget(
        daily_payload_bytes=daily_budget,
        per_hour_payload_bytes=daily_budget // 24,
        max_payload_bytes_per_correlation=6 * 1024 * 1024,
        max_payloads_per_correlation=16,
        max_payload_bytes_per_mint=12 * 1024 * 1024,
        max_payloads_per_mint=24,
        max_payload_bytes_per_observation=64 * 1024 * 1024,
        metadata_bytes_per_observation=2048,
    )
    policy = policy or RetentionResourcePolicy(
        normal_min_free_bytes=20 * 1024 ** 3,
        degraded_min_free_bytes=15 * 1024 ** 3,
        critical_min_free_bytes=10 * 1024 ** 3,
        hard_floor_bytes=1 * 1024 ** 3,
    )
    return simulate_hot_store_plateau(_as_rows(rows), budget=budget, policy=policy, free_bytes=free_bytes)


def test_plateau_cap_allows_archive_before_retire_with_verified_window():
    rows = [
        _fixture_payload(1, acquisition="acq-1", correlation="corr-1", mint="mint-a", artifact="shared-artifact", cold_archive_verified=True),
        _fixture_payload(2, acquisition="acq-2", correlation="corr-2", mint="mint-a", artifact="shared-artifact", cold_archive_verified=True),
    ]
    # Small cap forces retirement when fitting both rows.
    result = _simulate(rows, free_bytes=25 * 1024 ** 3, daily_budget=2 * 1024 * 1024)
    result["plateau_metrics"]["hot_store_cap_bytes"] = RA4_C1_HOT_STORE_CAP_BYTES
    assert result["status"] == "PASS"
    assert result["plateau_metrics"]["archive_before_replay_failures"] == 0
    assert result["plateau_metrics"]["retired_full_rows"] >= 0


def test_archive_before_retire_ordering_is_detected_for_unverified_oldest_row():
    rows = [
        _fixture_payload(1, acquisition="acq-1", correlation="corr-1", mint="mint-a", artifact="shared", cold_archive_verified=False),
        _fixture_payload(2, acquisition="acq-2", correlation="corr-2", mint="mint-a", artifact="shared", cold_archive_verified=True),
        _fixture_payload(3, acquisition="acq-3", correlation="corr-3", mint="mint-a", artifact="shared", cold_archive_verified=True),
    ]
    result = simulate_hot_store_plateau(
        _as_rows(rows),
        budget=RetentionBudget(
            daily_payload_bytes=2 * 1024 * 1024,
            per_hour_payload_bytes=2 * 1024 * 1024 // 24,
            max_payload_bytes_per_correlation=6 * 1024 * 1024,
            max_payloads_per_correlation=16,
            max_payload_bytes_per_mint=12 * 1024 * 1024,
            max_payloads_per_mint=24,
            max_payload_bytes_per_observation=64 * 1024 * 1024,
            metadata_bytes_per_observation=2048,
        ),
        policy=RetentionResourcePolicy(
            normal_min_free_bytes=20 * 1024 ** 3,
            degraded_min_free_bytes=15 * 1024 ** 3,
            critical_min_free_bytes=10 * 1024 ** 3,
            hard_floor_bytes=1 * 1024 ** 3,
        ),
        free_bytes=25 * 1024 ** 3,
        hot_store_cap_bytes=700,
    )
    assert result["verdict"] == "HOLD_ARCHIVE_PRE_RETIRED"
    assert result["plateau_metrics"]["archive_before_replay_failures"] >= 1


def test_oversized_payload_falls_back_to_metadata_only_under_budgets():
    base = _fixture_payload(1, acquisition="acq-large", correlation="corr-large", mint="mint-a", artifact="big", cold_archive_verified=True)
    base["response_data"] = {"blob": "z" * (300 * 1024)}
    rows = [base]
    budget_override = RetentionBudget(
        daily_payload_bytes=1 * 1024 * 1024,
        per_hour_payload_bytes=1 * 1024 * 1024,
        max_payload_bytes_per_correlation=2 * 1024 * 1024,
        max_payloads_per_correlation=100,
        max_payload_bytes_per_mint=2 * 1024 * 1024,
        max_payloads_per_mint=100,
        max_payload_bytes_per_observation=16 * 1024,  # force metadata-only
        metadata_bytes_per_observation=2048,
    )
    budget = budget_override
    policy = RetentionResourcePolicy(
        normal_min_free_bytes=20 * 1024 ** 3,
        degraded_min_free_bytes=15 * 1024 ** 3,
        critical_min_free_bytes=10 * 1024 ** 3,
        hard_floor_bytes=1 * 1024 ** 3,
    )
    result = simulate_hot_store_plateau(_as_rows([base]), budget=budget, policy=policy, free_bytes=25 * 1024 ** 3)
    assert result["summary"]["sample_size"] == 1
    assert any(d["action"] == "RECORD_METADATA_ONLY" for d in result["decisions"])


def test_disk_pressure_forces_metadata_only_path_without_hard_floor():
    rows = [_fixture_payload(i, acquisition=f"acq-{i}", correlation="corr", mint="mint", artifact=f"d-{i}") for i in range(3)]
    result = _simulate(rows, free_bytes=500 * 1024 * 1024, daily_budget=1_000_000_000)
    assert result["status"] == "HOLD"
    assert result["verdict"] == "HOLD_RESOURCE_LIMIT"
    assert result["blockers"] == ["HOLD_RESOURCE_LIMIT"]


def test_hot_representation_is_bounded_smaller_than_full_for_fixture_set(tmp_path: Path):
    del tmp_path
    rows = [
        _fixture_payload(i, acquisition=f"acq-{i % 3}", correlation=f"corr-{i % 2}", mint=f"mint-{i % 2}", artifact="shared")
        for i in range(12)
    ]
    result = _simulate(rows)
    assert result["summary"]["sample_size"] == 12
    assert result["plateau_metrics"]["bounded_duplication_ratio"] > 0.0
    assert "stats" in result["metrics"]
