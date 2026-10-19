"""RA1 local contracts for bounded retained-acquisition architecture preflight.

This module contains local-only, deterministic models and verifiers used by
RA1 design work. No production writes or live writer activation are performed
through this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Mapping


GIB_BYTES = 1024 * 1024 * 1024
MIB_BYTES = 1024 * 1024

RA1_SCHEMA_VERSION = "ra1_retained_acquisition_architecture.v1"
VERDICT_QUALIFIED = "RA1_BOUNDED_RETENTION_ARCHITECTURE_QUALIFIED"
VERDICT_HOLD = "HOLD_REPLAY_ARCHIVE_CONTRACT_UNRESOLVED"

RESOURCE_STATE_NORMAL = "NORMAL"
RESOURCE_STATE_DEGRADED = "DEGRADED"
RESOURCE_STATE_CRITICAL = "CRITICAL"

OUTCOME_RECORD_FULL_PAYLOAD = "RECORD_FULL_PAYLOAD"
OUTCOME_RECORD_METADATA_ONLY = "RECORD_METADATA_ONLY"
OUTCOME_RECORD_GAP = "RECORD_GAP_STATE"


@dataclass(frozen=True)
class RetentionResourcePolicy:
    normal_min_free_bytes: int = 20 * GIB_BYTES
    degraded_min_free_bytes: int = 15 * GIB_BYTES
    critical_min_free_bytes: int = 10 * GIB_BYTES
    hard_floor_bytes: int = 1 * GIB_BYTES


@dataclass(frozen=True)
class RetentionBudget:
    daily_payload_bytes: int
    per_hour_payload_bytes: int
    max_payload_bytes_per_correlation: int
    max_payloads_per_correlation: int
    max_payload_bytes_per_mint: int
    max_payloads_per_mint: int
    max_payload_bytes_per_observation: int
    hot_payload_window_days: int = 3
    metadata_bytes_per_observation: int = 2048


@dataclass(frozen=True)
class RetainedObservationEvent:
    observation_id: str
    correlation_id: str
    launch_mint: str
    payload_bytes: int
    timestamp_utc: str
    cold_archive_verified: bool
    provider: str = "provider-default"


@dataclass(frozen=True)
class RetentionLedger:
    day: str
    payload_bytes_day: int
    payload_bytes_by_correlation: Mapping[str, int]
    payload_bytes_by_mint: Mapping[str, int]
    payload_count_by_correlation: Mapping[str, int]
    payload_count_by_mint: Mapping[str, int]
    payload_events: int


def _to_day(timestamp_utc: str) -> str:
    return datetime.fromisoformat(timestamp_utc.replace("Z", "+00:00")).date().isoformat()


def bytes_gib(value: int) -> float:
    return round(value / GIB_BYTES, 6)


def bytes_mib(value: int) -> float:
    return round(value / MIB_BYTES, 6)


def resource_state(free_bytes: int, policy: RetentionResourcePolicy = RetentionResourcePolicy()) -> str:
    if free_bytes < policy.hard_floor_bytes:
        return RESOURCE_STATE_CRITICAL
    if free_bytes < policy.critical_min_free_bytes:
        return RESOURCE_STATE_CRITICAL
    if free_bytes < policy.degraded_min_free_bytes:
        return RESOURCE_STATE_DEGRADED
    if free_bytes < policy.normal_min_free_bytes:
        return RESOURCE_STATE_DEGRADED
    return RESOURCE_STATE_NORMAL


def resource_state_transition(actions: list[tuple[str, int]], policy: RetentionResourcePolicy = RetentionResourcePolicy()) -> list[str]:
    """Evaluate a deterministic sequence of free-space updates."""
    return [resource_state(free_bytes, policy=policy) for _, free_bytes in actions]


def estimate_growth(observed_db_bytes: int, observed_observations: int, observed_days: float) -> dict[str, Any]:
    if observed_days <= 0:
        raise ValueError("observed_days must be positive")
    if observed_observations <= 0:
        raise ValueError("observed_observations must be positive")
    gb_day = bytes_gib(int(observed_db_bytes / observed_days))
    obs_day = observed_observations / observed_days
    bytes_per_obs = observed_db_bytes / observed_observations
    return {
        "observed_growth": {
            "measured_bytes_total": observed_db_bytes,
            "measured_observations": observed_observations,
            "observed_days": observed_days,
            "observed_gb_per_day": gb_day,
            "observed_observations_per_day": obs_day,
            "bytes_per_observation": bytes_per_obs,
        },
        "projected": {
            "projected_30day_gb": gb_day * 30,
            "projected_30day_observations": obs_day * 30,
        },
    }


def candidate_budget_projection(observed_gb_per_day: float, options: Mapping[str, int]) -> dict[str, dict[str, Any]]:
    """Return projection and reduction metrics for candidate daily budgets."""
    projections: dict[str, dict[str, Any]] = {}
    reduction_denominator = max(observed_gb_per_day, 1e-9)
    for label, daily_bytes in options.items():
        candidate_gb_day = bytes_gib(daily_bytes)
        reduction = max(0.0, 1.0 - (candidate_gb_day / reduction_denominator))
        projections[label] = {
            "daily_bytes": daily_bytes,
            "daily_gb": candidate_gb_day,
            "weekly_gb": candidate_gb_day * 7,
            "monthly_gb": candidate_gb_day * 30,
            "reduction_ratio": reduction,
            "reduction_percent": round(reduction * 100.0, 3),
        }
    return projections


def recommend_budget_option(projections: Mapping[str, Mapping[str, Any]], minimum_ratio: float = 0.80) -> str:
    candidates = {
        label: details
        for label, details in projections.items()
        if details["reduction_ratio"] >= minimum_ratio
    }
    if not candidates:
        raise ValueError("no candidate budget satisfies minimum reduction")
    return max(candidates, key=lambda label: candidates[label]["daily_gb"])


def projected_ledger_hot_store(
    observations_per_day: float,
    budget: RetentionBudget,
    metadata_bytes_per_observation: int | None = None,
) -> dict[str, float]:
    metadata_bytes = metadata_bytes_per_observation or budget.metadata_bytes_per_observation
    payload_hot_bytes = budget.daily_payload_bytes * budget.hot_payload_window_days
    metadata_hot_bytes = observations_per_day * metadata_bytes * budget.hot_payload_window_days
    return {
        "observed_daily_observations": observations_per_day,
        "metadata_bytes_per_observation": metadata_bytes,
        "payload_bytes_hot_window": payload_hot_bytes,
        "metadata_bytes_hot_window": metadata_hot_bytes,
        "total_hot_window_bytes": payload_hot_bytes + metadata_hot_bytes,
        "hot_window_days": budget.hot_payload_window_days,
        "metadata_hot_window_gb": bytes_gib(int(metadata_hot_bytes)),
        "payload_hot_window_gb": bytes_gib(int(payload_hot_bytes)),
        "total_hot_window_gb": bytes_gib(int(payload_hot_bytes + metadata_hot_bytes)),
    }


def retention_decision(
    state: RetentionLedger,
    event: RetainedObservationEvent,
    policy: RetentionResourcePolicy,
    budget: RetentionBudget,
    free_bytes: int,
) -> tuple[str, str, RetentionLedger]:
    state_day = state.day
    event_day = _to_day(event.timestamp_utc)
    if event_day != state_day:
        raise ValueError("ledger-day mismatch")

    if event.payload_bytes <= 0:
        raise ValueError("payload_bytes must be positive")

    resource = resource_state(free_bytes, policy)
    if resource == RESOURCE_STATE_CRITICAL:
        return OUTCOME_RECORD_METADATA_ONLY, "resource_critical_no_full_payload", state

    correlation_bytes = state.payload_bytes_by_correlation.get(event.correlation_id, 0)
    mint_bytes = state.payload_bytes_by_mint.get(event.launch_mint, 0)
    correlation_count = state.payload_count_by_correlation.get(event.correlation_id, 0)
    mint_count = state.payload_count_by_mint.get(event.launch_mint, 0)

    if event.payload_bytes > budget.max_payload_bytes_per_observation:
        return OUTCOME_RECORD_METADATA_ONLY, "per_observation_ceiling", state

    if not event.cold_archive_verified and resource != RESOURCE_STATE_NORMAL:
        return OUTCOME_RECORD_METADATA_ONLY, "cold_archive_not_verified", state

    if state.payload_bytes_day + event.payload_bytes > budget.daily_payload_bytes:
        return OUTCOME_RECORD_METADATA_ONLY, "daily_budget_exhausted", state
    if correlation_bytes + event.payload_bytes > budget.max_payload_bytes_per_correlation:
        return OUTCOME_RECORD_METADATA_ONLY, "correlation_payload_ceiling", state
    if correlation_count + 1 > budget.max_payloads_per_correlation:
        return OUTCOME_RECORD_METADATA_ONLY, "correlation_count_ceiling", state
    if mint_bytes + event.payload_bytes > budget.max_payload_bytes_per_mint:
        return OUTCOME_RECORD_METADATA_ONLY, "mint_payload_ceiling", state
    if mint_count + 1 > budget.max_payloads_per_mint:
        return OUTCOME_RECORD_METADATA_ONLY, "mint_count_ceiling", state

    updated = RetentionLedger(
        day=state.day,
        payload_bytes_day=state.payload_bytes_day + event.payload_bytes,
        payload_bytes_by_correlation={**state.payload_bytes_by_correlation, event.correlation_id: correlation_bytes + event.payload_bytes},
        payload_bytes_by_mint={**state.payload_bytes_by_mint, event.launch_mint: mint_bytes + event.payload_bytes},
        payload_count_by_correlation={**state.payload_count_by_correlation, event.correlation_id: correlation_count + 1},
        payload_count_by_mint={**state.payload_count_by_mint, event.launch_mint: mint_count + 1},
        payload_events=state.payload_events + 1,
    )
    return OUTCOME_RECORD_FULL_PAYLOAD, "within_contract", updated


def fresh_ledger(timestamp_utc: str) -> RetentionLedger:
    return RetentionLedger(
        day=_to_day(timestamp_utc),
        payload_bytes_day=0,
        payload_bytes_by_correlation={},
        payload_bytes_by_mint={},
        payload_count_by_correlation={},
        payload_count_by_mint={},
        payload_events=0,
    )
