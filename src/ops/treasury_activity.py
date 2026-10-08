"""Provider-free activity and priority policy for DEV-019 treasury discovery.

Activity is a scan-priority signal only. It never mutates, demotes, or
deletes confirmed treasury identity; launch-backward evidence can request a
bounded targeted scan irrespective of routine activity suppression.
"""
from __future__ import annotations

from dataclasses import dataclass

HOT = "HOT"
ACTIVE = "ACTIVE"
DORMANT = "DORMANT"
RETIRED_CANDIDATE = "RETIRED_CANDIDATE"
ACTIVITY_UNKNOWN = "ACTIVITY_UNKNOWN"
DEEP_DISCOVERY = "DEEP_DISCOVERY"
VERIFY_FUNDING = "VERIFY_FUNDING"
SUPPRESS_ROUTINE_DEEP_SCAN = "SUPPRESS_ROUTINE_DEEP_SCAN"
TARGETED_LAUNCH_BACKWARD = "TARGETED_LAUNCH_BACKWARD"


@dataclass(frozen=True)
class ActivityWindows:
    hot_seconds: int = 7 * 24 * 60 * 60
    active_seconds: int = 30 * 24 * 60 * 60
    dormant_seconds: int = 90 * 24 * 60 * 60

    def validate(self) -> None:
        if not (0 < self.hot_seconds < self.active_seconds < self.dormant_seconds):
            raise ValueError("INVALID_ACTIVITY_WINDOWS")


def classify_activity(*, now: int, last_transaction_at: int | None, coverage_complete: bool,
                      windows: ActivityWindows = ActivityWindows()) -> str:
    """Classify only complete, bounded observations; otherwise fail closed."""
    windows.validate()
    if not coverage_complete or last_transaction_at is None or last_transaction_at > now:
        return ACTIVITY_UNKNOWN
    age = now - last_transaction_at
    if age <= windows.hot_seconds:
        return HOT
    if age <= windows.active_seconds:
        return ACTIVE
    if age <= windows.dormant_seconds:
        return DORMANT
    return RETIRED_CANDIDATE


def is_meaningful_funding(*, lamports: int | None, balance_delta_verified: bool,
                          material_screening_lamports: int) -> bool:
    return bool(balance_delta_verified and lamports is not None and lamports >= material_screening_lamports)


def discovery_priority(*, activity_class: str, has_recent_meaningful_funding: bool,
                       launch_backward_evidence: bool) -> str:
    """Return a priority, never an identity classification or promotion."""
    if launch_backward_evidence:
        return TARGETED_LAUNCH_BACKWARD
    if activity_class in {HOT, ACTIVE}:
        return DEEP_DISCOVERY if has_recent_meaningful_funding else VERIFY_FUNDING
    return SUPPRESS_ROUTINE_DEEP_SCAN
