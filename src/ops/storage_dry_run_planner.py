"""STORAGE-LIFECYCLE-P1: dry-run cleanup planner (Part 18).

Produces a report of what a real cleanup run WOULD do, given a list of
classified stores and a current disk-pressure state. Performs ZERO
destructive mutations -- this module has no delete/vacuum/rename call
anywhere. It only reads inputs already gathered elsewhere (the storage
census) and evaluates them against storage_lifecycle_policy.

This is the P1-authorized deliverable for "P1 may implement: ... dry-run
cleanup planner" -- P1 explicitly does NOT authorize wiring this into an
automatic scheduler that actually deletes anything (see Part 26 P2
activation plan, which requires a separate explicit GO decision).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.ops.storage_lifecycle_policy import (
    DiskPressureState,
    DiskPressureThresholds,
    LifecycleClass,
    classify_store,
    eligible_for_pressure_state,
)


@dataclass(frozen=True)
class StoreCensusEntry:
    """One row of the storage census -- the minimum fields the planner
    needs. Real census data (paths, exact byte counts, etc) lives in
    docs/audits/storage_lifecycle_p1_storage_census.json; this dataclass
    is the narrow interface the planner consumes."""
    path: str
    bytes: int
    proposed_class: LifecycleClass
    reason: str


@dataclass
class PlanEntry:
    path: str
    bytes: int
    lifecycle_class: LifecycleClass
    protected_by_invariant: str | None
    eligible_this_pressure_state: bool
    action: str  # "RETAIN" | "ELIGIBLE_FOR_FUTURE_CLEANUP" | "PROTECTED"
    reason: str


@dataclass
class DryRunReport:
    pressure_state: DiskPressureState
    free_bytes_at_evaluation: int
    entries: list[PlanEntry] = field(default_factory=list)
    total_bytes_examined: int = 0
    total_bytes_eligible: int = 0
    total_bytes_protected: int = 0
    destructive_mutations_performed: int = 0  # always 0 -- structural proof this is a dry run


def plan_dry_run(
    census: list[StoreCensusEntry],
    *,
    free_bytes: int,
    thresholds: DiskPressureThresholds | None = None,
) -> DryRunReport:
    thresholds = thresholds or DiskPressureThresholds()
    pressure_state = thresholds.classify(free_bytes)

    report = DryRunReport(pressure_state=pressure_state, free_bytes_at_evaluation=free_bytes)
    for entry in census:
        final_class = classify_store(entry.path, proposed_class=entry.proposed_class)
        protected = final_class != entry.proposed_class
        eligible = eligible_for_pressure_state(final_class, pressure_state)

        if final_class in (LifecycleClass.PERMANENT_OPERATIONAL, LifecycleClass.PERMANENT_EVIDENCE_INDEX):
            action = "PROTECTED"
            invariant = "WATCHTOWER_CANONICAL_HISTORY_PRESERVATION_REQUIRED" if protected else "PERMANENT_CLASS_NO_AUTOMATED_DELETION"
        elif eligible:
            action = "ELIGIBLE_FOR_FUTURE_CLEANUP"
            invariant = None
        else:
            action = "RETAIN"
            invariant = None

        plan_entry = PlanEntry(
            path=entry.path,
            bytes=entry.bytes,
            lifecycle_class=final_class,
            protected_by_invariant=invariant,
            eligible_this_pressure_state=eligible,
            action=action,
            reason=entry.reason if not protected else f"{entry.reason} (upgraded to PERMANENT_OPERATIONAL by Watchtower/3SW2 name guard)",
        )
        report.entries.append(plan_entry)
        report.total_bytes_examined += entry.bytes
        if action == "ELIGIBLE_FOR_FUTURE_CLEANUP":
            report.total_bytes_eligible += entry.bytes
        elif action == "PROTECTED":
            report.total_bytes_protected += entry.bytes

    return report


def report_to_dict(report: DryRunReport) -> dict:
    return {
        "pressure_state": report.pressure_state.value,
        "free_bytes_at_evaluation": report.free_bytes_at_evaluation,
        "total_bytes_examined": report.total_bytes_examined,
        "total_bytes_eligible_for_future_cleanup": report.total_bytes_eligible,
        "total_bytes_protected": report.total_bytes_protected,
        "destructive_mutations_performed": report.destructive_mutations_performed,
        "entries": [
            {
                "path": e.path,
                "bytes": e.bytes,
                "lifecycle_class": e.lifecycle_class.value,
                "protected_by_invariant": e.protected_by_invariant,
                "eligible_this_pressure_state": e.eligible_this_pressure_state,
                "action": e.action,
                "reason": e.reason,
            }
            for e in report.entries
        ],
    }
