"""STORAGE-LIFECYCLE-P1: retention classification and disk-pressure policy.

Design-only module: defines the lifecycle classes, the disk-pressure
thresholds/states, and the SQLite lock-safety contract that any future
cleanup implementation MUST honour. This module contains NO destructive
code path -- it has no delete/vacuum/rotate function. It only classifies
and reports.

WATCHTOWER_CANONICAL_HISTORY_PRESERVATION_REQUIRED: any store/table that
is (or could be) part of Watchtower's or 3SW2's canonical/confirmed
history must always classify as PERMANENT_OPERATIONAL, never anything
lower. This is enforced structurally in classify_store() below -- there
is no code path that can assign a Watchtower-tagged store a lower class.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class LifecycleClass(str, Enum):
    PERMANENT_OPERATIONAL = "PERMANENT_OPERATIONAL"
    PERMANENT_EVIDENCE_INDEX = "PERMANENT_EVIDENCE_INDEX"
    HOT_REPLAYABLE = "HOT_REPLAYABLE"
    COLD_REPLAYABLE = "COLD_REPLAYABLE"
    COMPACTABLE = "COMPACTABLE"
    REGENERABLE = "REGENERABLE"
    DIAGNOSTIC = "DIAGNOSTIC"
    TEMPORARY = "TEMPORARY"
    RETIREMENT_ELIGIBLE = "RETIREMENT_ELIGIBLE"
    UNKNOWN_NEEDS_HUMAN_REVIEW = "UNKNOWN_NEEDS_HUMAN_REVIEW"


# No automated deletion permitted for these classes, ever, regardless of
# disk-pressure state. This is the structural enforcement point.
NO_AUTOMATED_DELETION_CLASSES = frozenset({
    LifecycleClass.PERMANENT_OPERATIONAL,
    LifecycleClass.PERMANENT_EVIDENCE_INDEX,
    LifecycleClass.UNKNOWN_NEEDS_HUMAN_REVIEW,
})


class DiskPressureState(str, Enum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    PRESSURE = "PRESSURE"
    EMERGENCY = "EMERGENCY"
    HARD_FLOOR = "HARD_FLOOR"


@dataclass(frozen=True)
class DiskPressureThresholds:
    """Bytes of free disk space at which each state begins. Config, not
    magic constants baked into logic -- callers should load these from a
    config source (env var / config file) in a real deployment; these are
    the P1-proposed STARTING values from the human directive, explicitly
    not treated as immutable truth."""
    warning_free_bytes: int = 30 * 1024 ** 3   # ~30 GiB
    pressure_free_bytes: int = 20 * 1024 ** 3  # ~20 GiB
    emergency_free_bytes: int = 15 * 1024 ** 3  # ~15 GiB
    hard_floor_free_bytes: int = 10 * 1024 ** 3  # ~10 GiB

    def classify(self, free_bytes: int) -> DiskPressureState:
        if free_bytes <= self.hard_floor_free_bytes:
            return DiskPressureState.HARD_FLOOR
        if free_bytes <= self.emergency_free_bytes:
            return DiskPressureState.EMERGENCY
        if free_bytes <= self.pressure_free_bytes:
            return DiskPressureState.PRESSURE
        if free_bytes <= self.warning_free_bytes:
            return DiskPressureState.WARNING
        return DiskPressureState.NORMAL


# What each pressure state is ALLOWED to consider retiring. This is an
# allow-list, not a trigger -- reaching a state does not mean action is
# taken, only that certain classes BECOME eligible for consideration by a
# (future, not-yet-authorized) cleanup planner.
PRESSURE_STATE_ELIGIBLE_CLASSES: dict[DiskPressureState, frozenset[LifecycleClass]] = {
    DiskPressureState.NORMAL: frozenset({LifecycleClass.TEMPORARY}),
    DiskPressureState.WARNING: frozenset({LifecycleClass.TEMPORARY, LifecycleClass.DIAGNOSTIC}),
    DiskPressureState.PRESSURE: frozenset({
        LifecycleClass.TEMPORARY, LifecycleClass.DIAGNOSTIC,
        LifecycleClass.RETIREMENT_ELIGIBLE, LifecycleClass.REGENERABLE,
    }),
    DiskPressureState.EMERGENCY: frozenset({
        LifecycleClass.TEMPORARY, LifecycleClass.DIAGNOSTIC,
        LifecycleClass.RETIREMENT_ELIGIBLE, LifecycleClass.REGENERABLE,
        LifecycleClass.COMPACTABLE, LifecycleClass.COLD_REPLAYABLE,
    }),
    # HARD_FLOOR: fail closed on optional disk-growing work; the eligible
    # set is intentionally NOT larger than EMERGENCY's -- hard floor means
    # stop starting new optional work, not "delete more aggressively."
    DiskPressureState.HARD_FLOOR: frozenset({
        LifecycleClass.TEMPORARY, LifecycleClass.DIAGNOSTIC,
        LifecycleClass.RETIREMENT_ELIGIBLE, LifecycleClass.REGENERABLE,
        LifecycleClass.COMPACTABLE, LifecycleClass.COLD_REPLAYABLE,
    }),
}


def eligible_for_pressure_state(lifecycle_class: LifecycleClass, state: DiskPressureState) -> bool:
    """Never eligible if the class forbids automated deletion outright,
    regardless of pressure state -- this check runs BEFORE the pressure
    allow-list, so it cannot be bypassed by any future pressure-state
    misconfiguration."""
    if lifecycle_class in NO_AUTOMATED_DELETION_CLASSES:
        return False
    return lifecycle_class in PRESSURE_STATE_ELIGIBLE_CLASSES.get(state, frozenset())


WATCHTOWER_TAGGED_STORE_MARKERS = (
    "wt_ops_v2", "watchtower", "wt_confirmed_treasuries",
    "wt_watchtower_launches", "wt_watchtower_candidates",
    "three_sw2", "3sw2",
)


def classify_store(store_name: str, *, proposed_class: LifecycleClass) -> LifecycleClass:
    """Structural Watchtower/3SW2 guard: if a store name matches any
    Watchtower/3SW2 marker, the class is forced to PERMANENT_OPERATIONAL
    regardless of what was proposed -- WATCHTOWER_CANONICAL_HISTORY_
    PRESERVATION_REQUIRED cannot be weakened by miscategorization."""
    lowered = store_name.lower()
    if any(marker in lowered for marker in WATCHTOWER_TAGGED_STORE_MARKERS):
        return LifecycleClass.PERMANENT_OPERATIONAL
    return proposed_class
