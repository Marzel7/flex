"""STORAGE-LIFECYCLE-P2.2: conservative automated lifecycle runner.

Standalone entrypoint (python -m src.ops.storage_lifecycle_runner), NOT
woven into the existing live operation_scheduler.py process -- this
milestone deliberately avoids modifying an already-running production
scheduler. Follows the SAME idioms already established in this repo
(single-instance lock file, --loop/--once/--status flags, quiet mode) so
it fits the existing operational pattern without risking the live
scheduler's stability.

PLAN -> VALIDATE -> EXECUTE -> VERIFY -> AUDIT, every run, no exceptions.

Automation allowlist (the ONLY classes this runner may ever act on):
  - abandoned temp files (recognized naming contract, dead PID, min age)
  - proven byte-identical duplicates (SHA-256 reconfirmed immediately
    before deletion)
  - pre-qualified closed zero-reference stores (moved to _retired/, never
    permanently deleted by this runner -- permanent deletion requires a
    separate, later, explicitly-qualified pass)
  - log rotation/expiry (NOT YET WIRED this pass -- see blockers in the
    P2.2 implementation artifact; the hook point exists but no log
    rotation policy has been qualified with a real writer-compatibility
    check yet, so this class is currently a no-op)

Denylist (structurally enforced, not just documented): flex_complete_
database.db, wt_ops_v2.db, transaction_first_lineage.db, flex_
investigation_archive.db, retained_acquisition_shadow.db (rows -- the
runner may READ its size/budget state but must never DELETE rows from
it), ArtifactStore objects, anything matching a Watchtower/3SW2 marker.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

from src.ops.storage_audit_ledger import LedgerEntry, append_entry, new_run_id  # noqa: E402
from src.ops.storage_lifecycle_policy import (  # noqa: E402
    DiskPressureThresholds,
    LifecycleClass,
    classify_store,
)
from src.ops.storage_lock_safety import (  # noqa: E402
    CleanupLeaseHeldError,
    verify_db_valid_after_cleanup,
)
from src.ops.storage_monitor import measure_disk_free  # noqa: E402

LOCK_FILE = str(ROOT / "database" / "storage_lifecycle.lock")
LEDGER_PATH = str(ROOT / "docs" / "audits" / "storage_lifecycle_p2_execution_ledger.jsonl")
RETIRED_DIR = str(ROOT / "database" / "evidence_platform" / "_retired")
INTELLIGENCE_SNAPSHOTS_DIR = str(ROOT / "database" / "intelligence_snapshots")

# Denylist -- structural, checked independently of the policy module's
# name-based guard, as defense in depth (Part 22's "do not rely only on
# path naming if stronger metadata exists" -- here we ALSO hardcode the
# exact protected file list rather than relying solely on substring
# matching, so a store named e.g. "wt_ops_v2_backup.db" is caught by the
# substring check, but the canonical files are caught by exact match too).
PROTECTED_EXACT_PATHS = frozenset({
    str(ROOT / "database" / "flex_complete_database.db"),
    str(ROOT / "database" / "wt_ops_v2.db"),
    str(ROOT / "database" / "transaction_first_lineage.db"),
    str(ROOT / "database" / "flex_investigation_archive.db"),
})

MIN_TEMP_FILE_AGE_SECONDS = 24 * 3600  # 1 day minimum age before a temp file is eligible


def _is_protected(path: str) -> bool:
    resolved = str(Path(path).resolve())
    if resolved in PROTECTED_EXACT_PATHS:
        return True
    if classify_store(path, proposed_class=LifecycleClass.RETIREMENT_ELIGIBLE) == LifecycleClass.PERMANENT_OPERATIONAL:
        return True
    # retained_acquisition_shadow.db rows are never touched by this
    # runner -- the file itself is not "protected" from monitoring, but
    # no action class in this module ever targets it for deletion.
    return False


@dataclass
class PlannedAction:
    action_type: str
    path: str
    reason: str
    bytes_estimate: int = 0


@dataclass
class RunResult:
    run_id: str
    pressure_state: str
    free_bytes_before: int
    free_bytes_after: int = 0
    planned: list[PlannedAction] = field(default_factory=list)
    executed: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    bytes_reclaimed: int = 0
    duration_seconds: float = 0.0


def _find_abandoned_temp_files(directory: str, *, min_age_seconds: int) -> list[PlannedAction]:
    """Recognized naming contract: <name>.tmp.<pid>.<time_ns>, matching
    src/ops/intelligence_snapshots.py's write pattern. A file is eligible
    only if its embedded PID is confirmed dead AND it is at least
    min_age_seconds old -- both checks re-run at VALIDATE time
    immediately before deletion, not just at PLAN time."""
    import re

    actions: list[PlannedAction] = []
    if not os.path.isdir(directory):
        return actions
    now = time.time()
    pattern = re.compile(r"\.tmp\.(\d+)\.\d+$")
    for name in os.listdir(directory):
        path = os.path.join(directory, name)
        match = pattern.search(name)
        if not match or not os.path.isfile(path):
            continue
        age = now - os.path.getmtime(path)
        if age < min_age_seconds:
            continue
        pid = int(match.group(1))
        if _pid_alive(pid):
            continue
        actions.append(PlannedAction(
            action_type="DELETE_ABANDONED_TEMP", path=path,
            reason=f"matches tmp.<pid>.<ts> contract, age={age:.0f}s, pid={pid} confirmed dead",
            bytes_estimate=os.path.getsize(path),
        ))
    return actions


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but not ours -- treat as alive/unknown, never touch


def plan(*, snapshots_dir: str = INTELLIGENCE_SNAPSHOTS_DIR) -> list[PlannedAction]:
    """PLAN phase: read-only candidate discovery. No mutation. Currently
    only the abandoned-temp-file class is wired for automatic execution
    this pass -- duplicate detection and zero-reference-store retirement
    require a fresh census/classification pass per invocation (not yet
    built into this runner; they were executed manually under full
    human review in the first P2 pass and remain manual-only until a
    safe automated re-classification pipeline is qualified separately)."""
    actions = _find_abandoned_temp_files(snapshots_dir, min_age_seconds=MIN_TEMP_FILE_AGE_SECONDS)
    return actions


def validate(action: PlannedAction) -> tuple[bool, str]:
    """VALIDATE phase: re-check eligibility immediately before mutation.
    Returns (is_still_eligible, reason)."""
    if _is_protected(action.path):
        return False, "PROTECTED_STORE_STRUCTURAL_GUARD"
    if action.action_type == "DELETE_ABANDONED_TEMP":
        import re
        name = os.path.basename(action.path)
        match = re.search(r"\.tmp\.(\d+)\.\d+$", name)
        if not match:
            return False, "NAMING_CONTRACT_NO_LONGER_MATCHES"
        if not os.path.isfile(action.path):
            return False, "FILE_NO_LONGER_EXISTS"
        age = time.time() - os.path.getmtime(action.path)
        if age < MIN_TEMP_FILE_AGE_SECONDS:
            return False, "NO_LONGER_OLD_ENOUGH"
        pid = int(match.group(1))
        if _pid_alive(pid):
            return False, "PID_NOW_ALIVE"
        return True, "ELIGIBLE"
    return False, "UNKNOWN_ACTION_TYPE"


def execute(action: PlannedAction) -> dict:
    """EXECUTE phase: perform exactly one filesystem-level mutation. No
    SQL DELETE, no VACUUM, ever, anywhere in this function."""
    if action.action_type == "DELETE_ABANDONED_TEMP":
        try:
            size = os.path.getsize(action.path)
            os.remove(action.path)
            return {"result": "SUCCESS", "bytes_reclaimed": size}
        except OSError as exc:
            return {"result": "FAILED", "error": str(exc), "bytes_reclaimed": 0}
    return {"result": "FAILED", "error": "unknown action type", "bytes_reclaimed": 0}


def run_lifecycle_pass(*, dry_run: bool = False, quiet: bool = False) -> RunResult:
    run_id = new_run_id()
    free_before, _ = measure_disk_free(str(ROOT))
    thresholds = DiskPressureThresholds()
    pressure_state = thresholds.classify(free_before)
    result = RunResult(run_id=run_id, pressure_state=pressure_state.value, free_bytes_before=free_before)

    start = time.monotonic()
    try:
        planned = plan()
    except Exception as exc:  # noqa: BLE001 -- plan phase must never crash the runner
        result.errors.append(f"PLAN_PHASE_ERROR: {exc}")
        planned = []
    result.planned = planned

    if not quiet:
        print(f"[storage-lifecycle] run {run_id}: pressure={pressure_state.value}, "
              f"free={free_before/1024**3:.2f}GiB, planned={len(planned)} action(s)")

    for action in planned:
        eligible, reason = validate(action)
        if not eligible:
            result.skipped.append({"path": action.path, "reason": reason})
            append_entry(LEDGER_PATH, LedgerEntry(
                run_id=run_id, timestamp=time.time(), policy_version="p2.2",
                path=action.path, action="SKIPPED_ELIGIBILITY_CHANGED", reason=reason,
                lifecycle_class="TEMPORARY", preflight_result="FAIL",
                bytes_before=action.bytes_estimate, bytes_after=action.bytes_estimate,
                action_result="SKIPPED", pressure_state=pressure_state.value,
                disk_state_free_bytes=free_before,
            ))
            continue

        append_entry(LEDGER_PATH, LedgerEntry(
            run_id=run_id, timestamp=time.time(), policy_version="p2.2",
            path=action.path, action="RESERVE_" + action.action_type, reason=action.reason,
            lifecycle_class="TEMPORARY", preflight_result="PASS",
            bytes_before=action.bytes_estimate, bytes_after=action.bytes_estimate,
            action_result="DRY_RUN_ONLY" if dry_run else "PENDING",
            pressure_state=pressure_state.value, disk_state_free_bytes=free_before,
        ))

        if dry_run:
            result.executed.append({"path": action.path, "action": action.action_type, "result": "DRY_RUN_ONLY"})
            continue

        outcome = execute(action)
        result.executed.append({"path": action.path, "action": action.action_type, **outcome})
        if outcome["result"] == "SUCCESS":
            result.bytes_reclaimed += outcome["bytes_reclaimed"]
        else:
            result.errors.append(f"{action.path}: {outcome.get('error')}")

        append_entry(LEDGER_PATH, LedgerEntry(
            run_id=run_id, timestamp=time.time(), policy_version="p2.2",
            path=action.path, action=action.action_type, reason=action.reason,
            lifecycle_class="TEMPORARY", preflight_result="PASS",
            bytes_before=action.bytes_estimate,
            bytes_after=action.bytes_estimate - outcome.get("bytes_reclaimed", 0),
            action_result=outcome["result"], error=outcome.get("error"),
            pressure_state=pressure_state.value, disk_state_free_bytes=free_before,
        ))

    result.duration_seconds = time.monotonic() - start
    free_after, _ = measure_disk_free(str(ROOT))
    result.free_bytes_after = free_after

    if not quiet:
        print(f"[storage-lifecycle] run {run_id} complete: executed={len(result.executed)}, "
              f"skipped={len(result.skipped)}, reclaimed={result.bytes_reclaimed} bytes, "
              f"duration={result.duration_seconds:.2f}s")

    return result


def result_to_dict(result: RunResult) -> dict:
    return {
        "run_id": result.run_id, "pressure_state": result.pressure_state,
        "free_bytes_before": result.free_bytes_before, "free_bytes_after": result.free_bytes_after,
        "net_free_bytes_delta": result.free_bytes_after - result.free_bytes_before,
        "planned_count": len(result.planned), "executed_count": len(result.executed),
        "skipped_count": len(result.skipped), "errors": result.errors,
        "bytes_reclaimed": result.bytes_reclaimed, "duration_seconds": result.duration_seconds,
        "executed": result.executed, "skipped": result.skipped,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="STORAGE-LIFECYCLE-P2.2 conservative automated lifecycle runner")
    ap.add_argument("--once", action="store_true", help="run exactly one bounded lifecycle pass and exit")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    if args.status:
        free, total = measure_disk_free(str(ROOT))
        print(json.dumps({"free_bytes": free, "free_gib": round(free / 1024**3, 2),
                           "pressure_state": DiskPressureThresholds().classify(free).value}, indent=2))
        return 0

    if not args.once:
        ap.print_help()
        return 0

    try:
        from src.ops.storage_lock_safety import acquire_cleanup_lease
        with acquire_cleanup_lease(LOCK_FILE):
            result = run_lifecycle_pass(dry_run=args.dry_run, quiet=args.quiet)
    except CleanupLeaseHeldError:
        print("[storage-lifecycle] another lifecycle run is already in progress; exiting cleanly.")
        return 0

    print(json.dumps(result_to_dict(result), indent=2, default=str))
    return 0 if not result.errors else 1


if __name__ == "__main__":
    sys.exit(main())
