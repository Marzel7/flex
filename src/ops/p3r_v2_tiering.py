"""Shared deterministic P3R v2 candidate and tier constructors.

This is a new qualification lineage.  It intentionally does not consume or
attempt to recreate the lost v1 candidate identities.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from statistics import median
from typing import Iterable


DISCOVERY_CONTRACT_VERSION = "P3R_V2_CANDIDATE_DISCOVERY_CONTRACT.v1"
FINGERPRINT_CONTRACT_VERSION = "P3R_V2_FINGERPRINT_CONTRACTS.v1"
TIER_CONTRACT_VERSION = "P3R_V2_EVIDENCE_TIERS.v1"


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def base_fingerprint(edges: Iterable[tuple[int, str, int | None]]) -> dict:
    """Address-free selected-edge signature; zero/null amounts are non-positive."""
    values = {
        (int(depth), str(mechanism), int(amount) if amount not in (None, 0) else None)
        for depth, mechanism, amount in edges
    }
    normalized = [
        {"hop_depth": depth, "mechanism": mechanism, "amount_lamports": amount}
        for depth, mechanism, amount in sorted(
            values, key=lambda item: (item[0], item[1], item[2] is None, item[2] or 0)
        )
    ]
    return {"contract": FINGERPRINT_CONTRACT_VERSION, "kind": "BASE_SELECTED_EDGE", "edges": normalized}


def alternative_fingerprint(edges: Iterable[tuple[int, str, int | None]]) -> dict:
    return {"contract": FINGERPRINT_CONTRACT_VERSION, "kind": "ALTERNATIVE_EDGE", "edges": base_fingerprint(edges)["edges"]}


def atomic_fingerprint(order: object, has_create: int, has_sync_native: int,
                       has_close: int, transfer_lamports: int | None) -> dict:
    """Address-free atomic WSOL fingerprint; no wallet identities are retained."""
    return {
        "contract": FINGERPRINT_CONTRACT_VERSION,
        "kind": "ATOMIC_WSOL",
        "instruction_order": order,
        "has_create": bool(has_create),
        "has_sync_native": bool(has_sync_native),
        "has_close": bool(has_close),
        "transfer_lamports": int(transfer_lamports) if transfer_lamports not in (None, 0) else None,
    }


def stable_candidate_id(fingerprint: dict) -> str:
    return "p3r-v2-" + digest(fingerprint)[:20]


def max_window(times: list[int], seconds: int) -> int:
    best = left = 0
    for right, value in enumerate(times):
        while value - times[left] > seconds:
            left += 1
        best = max(best, right - left + 1)
    return best


def activity_metrics(times: Iterable[int], cutoff: int) -> dict:
    values = sorted(int(value) for value in times)
    if not values:
        return {"total_observations": 0, "activity_state": "ACTIVITY_UNKNOWN"}
    gaps = [right - left for left, right in zip(values, values[1:])]
    recent = {f"last_{days}d": sum(value > cutoff - days * 86400 for value in values)
              for days in (1, 3, 7, 14, 30)}
    if recent["last_30d"] == 0:
        state = "DORMANT"
    elif recent["last_7d"] >= 7 and max_window(values, 86400) >= 2:
        state = "VERY_HIGH_ACTIVITY"
    elif recent["last_7d"] >= 3 and max_window(values, 86400) >= 1:
        state = "HIGH_ACTIVITY"
    elif recent["last_30d"] >= 2:
        state = "REGULAR_ACTIVITY"
    else:
        state = "LOW_ACTIVITY"
    return {
        "total_observations": len(values), "first_observed": values[0], "last_observed": values[-1],
        "time_since_last": cutoff - values[-1], "median_inter_observation_gap_seconds": median(gaps) if gaps else None,
        "max_rolling_24h": max_window(values, 86400), "active_days": len({value // 86400 for value in values}),
        "observations_per_active_day": len(values) / len({value // 86400 for value in values}),
        "activity_state": state, **recent,
    }


def recurrence_state(fingerprints: Iterable[dict], member_count: int, *, minimum: int = 3) -> tuple[str, float, int]:
    values = [digest(value) for value in fingerprints]
    if not values:
        return "NOT_OBSERVED", 0.0, 0
    _, count = Counter(values).most_common(1)[0]
    coverage = len(values) / member_count if member_count else 0.0
    return ("STRONGLY_RECURRENT" if len(values) >= minimum and coverage >= 0.5 and count >= minimum and count / len(values) >= 0.6 else "OBSERVED_NOT_STRONGLY_RECURRENT", coverage, count)


def assign_tier(activity_state: str, base_strong: bool, alternative: str,
                atomic: str, address_blind: bool) -> str:
    watch_now = activity_state in {"VERY_HIGH_ACTIVITY", "HIGH_ACTIVITY"}
    if watch_now and base_strong and alternative == "STRONGLY_RECURRENT" and atomic == "STRONGLY_RECURRENT" and address_blind:
        return "V2_TIER_1_ACTIVE_MULTI_LAYER"
    if watch_now and base_strong and alternative == "STRONGLY_RECURRENT":
        return "V2_TIER_2_ACTIVE_STRUCTURAL"
    if watch_now and base_strong:
        return "V2_TIER_3_ACTIVE_BASE"
    if activity_state == "DORMANT":
        return "V2_DORMANT"
    return "V2_WATCH_LATER"
