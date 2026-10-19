"""Generic, mark-based principal-recovery and runner calculations.

This module has no operation, provider, or execution dependency.  Callers supply
causally available price multiples and an optional severe-collapse timestamp.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Mark:
    available_at: int
    multiple: float


def first_target(marks: list[Mark], target: float, deadline: int) -> Mark | None:
    return next((m for m in marks if m.available_at <= deadline and m.multiple >= target), None)


def sell_fraction(principal_fraction: float, observed_multiple: float, haircut: float) -> float:
    """Fraction needed to return a fraction of principal at the observed mark."""
    if not 0 < principal_fraction <= 1 or observed_multiple <= 0 or not 0 <= haircut < 1:
        raise ValueError("INVALID_RECOVERY_INPUT")
    return min(1.0, principal_fraction / (observed_multiple * (1 - haircut)))


def evaluate_trade(marks: list[Mark], recovery_target: float, principal_fraction: float,
                   runner_target: float | None, haircut: float, backstop: int,
                   collapse_at: int | None = None) -> dict:
    """Evaluate a staged mark policy, returning no result for absent backstop evidence."""
    path = sorted(marks, key=lambda m: m.available_at)
    recovery = first_target(path, recovery_target, backstop)
    fallback = next((m for m in reversed(path) if m.available_at <= backstop), None)
    if fallback is None:
        return {"status": "UNRESOLVED"}
    if recovery is None:
        return {"status": "RESOLVED", "mode": "FULL_BACKSTOP", "proceeds_multiple": fallback.multiple * (1-haircut),
                "principal_target_hit": False, "runner_target_hit": False, "runner_backstop": False,
                "last_exit_at": fallback.available_at, "recovery_before_collapse": False}
    fraction = sell_fraction(principal_fraction, recovery.multiple, haircut)
    if runner_target is None:
        return {"status": "RESOLVED", "mode": "FULL_TARGET", "proceeds_multiple": recovery.multiple * (1-haircut),
                "principal_target_hit": True, "runner_target_hit": False, "runner_backstop": False,
                "sell_fraction": 1.0, "runner_fraction": 0.0, "last_exit_at": recovery.available_at,
                "recovery_before_collapse": collapse_at is not None and recovery.available_at <= collapse_at}
    runner = next((m for m in path if m.available_at >= recovery.available_at and m.available_at <= backstop and m.multiple >= runner_target), None)
    final = runner or fallback
    return {"status": "RESOLVED", "mode": "RECOVERY_RUNNER", "proceeds_multiple": principal_fraction + (1-fraction)*final.multiple*(1-haircut),
            "principal_target_hit": True, "runner_target_hit": runner is not None, "runner_backstop": runner is None,
            "sell_fraction": fraction, "runner_fraction": 1-fraction, "last_exit_at": final.available_at,
            "recovery_before_collapse": collapse_at is not None and recovery.available_at <= collapse_at}
