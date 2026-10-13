"""Dormant, provider-free contract for universal incremental price windows.

This module is deliberately not wired into a worker.  It supplies the small
reducer used to qualify the invariant before DEV activation: a successful
checkpoint commits only a proven complete interval, while peaks and Current
MC are reduced from every qualified observation in that interval.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CADENCE_VERSION = "UNIVERSAL_CADENCE_POLICY_C_V1"
POLICY_C_CHECKPOINTS = ((0, 2 * 3600, 15 * 60), (2 * 3600, 6 * 3600, 30 * 60),
                        (6 * 3600, 12 * 3600, 60 * 60), (12 * 3600, 24 * 3600, 2 * 3600))


@dataclass(frozen=True)
class WindowState:
    last_successful_coverage_boundary: int
    running_peak_mc_usd: float | None = None
    running_peak_timestamp: int | None = None
    current_mc_usd: float | None = None
    current_mc_timestamp: int | None = None
    cadence_version: str = CADENCE_VERSION
    terminal: bool = False


def policy_c_lifecycle_calls_through(age_seconds: int) -> int:
    """Checkpoint calls only; entry discovery is intentionally excluded."""
    age = max(0, min(int(age_seconds), 24 * 3600))
    return sum(max(0, min(age, end) - start) // every
               for start, end, every in POLICY_C_CHECKPOINTS)


def next_checkpoint_at(*, entry_timestamp: int, age_seconds: int) -> int | None:
    age = max(0, int(age_seconds))
    for start, end, every in POLICY_C_CHECKPOINTS:
        if start <= age < end:
            return int(entry_timestamp) + ((age // every) + 1) * every
    return None


def reduce_checkpoint(state: WindowState, *, checkpoint_boundary: int,
                      response_coverage_start: int, response_coverage_end: int,
                      observations: list[dict[str, Any]], budget_available: bool = True) -> dict[str, Any]:
    """Reduce one bounded provider response without operation-specific policy.

    Coverage must contain both requested boundaries.  A denied budget or an
    incomplete response is a deferred/no-commit result: no price is invented
    and the durable boundary is left untouched.
    """
    requested_start, requested_end = state.last_successful_coverage_boundary, int(checkpoint_boundary)
    if state.terminal:
        return {"status": "TERMINAL_NO_PROVIDER_CALL", "provider_call": False, "state": state}
    if not budget_available:
        return {"status": "BUDGET_DEFERRED", "provider_call": False, "state": state}
    if requested_end <= requested_start:
        return {"status": "INVALID_BOUNDARY", "provider_call": False, "state": state}
    if int(response_coverage_start) > requested_start or int(response_coverage_end) < requested_end:
        return {"status": "INCOMPLETE_WINDOW", "provider_call": True, "state": state}
    qualified = [x for x in observations if requested_start <= int(x["timestamp"]) <= requested_end
                 and float(x["mc_usd"]) > 0]
    if not qualified:
        return {"status": "INCOMPLETE_WINDOW", "provider_call": True, "state": state}
    peak = max(qualified, key=lambda x: (float(x.get("high_mc_usd", x["mc_usd"])), -int(x["timestamp"])))
    latest = max(qualified, key=lambda x: int(x["timestamp"]))
    window_peak = float(peak.get("high_mc_usd", peak["mc_usd"]))
    old_peak = state.running_peak_mc_usd
    if old_peak is None or window_peak > old_peak:
        running_peak, running_peak_timestamp = window_peak, int(peak["timestamp"])
    else:
        running_peak, running_peak_timestamp = old_peak, state.running_peak_timestamp
    next_state = WindowState(requested_end, running_peak, running_peak_timestamp,
                             float(latest["mc_usd"]), int(latest["timestamp"]),
                             state.cadence_version, False)
    return {"status": "QUALIFIED", "provider_call": True, "window_peak_mc_usd": window_peak,
            "state": next_state, "observations_committed": len(qualified)}
