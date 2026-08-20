"""STORAGE-LIFECYCLE-P2.2: automated lifecycle runner tests.

All destructive-path tests use isolated tmp_path fixtures. No real
production database or the real database/intelligence_snapshots
directory is ever targeted. No provider calls.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ops.storage_lifecycle_runner import (  # noqa: E402
    MIN_TEMP_FILE_AGE_SECONDS,
    PROTECTED_EXACT_PATHS,
    PlannedAction,
    _find_abandoned_temp_files,
    _is_protected,
    execute,
    plan,
    run_lifecycle_pass,
    validate,
)
from src.ops.storage_lock_safety import CleanupLeaseHeldError, acquire_cleanup_lease  # noqa: E402


def _make_tmp_file(directory: Path, pid: int, age_seconds: float, content: bytes = b"x" * 100) -> Path:
    ts_ns = time.time_ns()
    path = directory / f"snapshot.json.tmp.{pid}.{ts_ns}"
    path.write_bytes(content)
    mtime = time.time() - age_seconds
    os.utime(path, (mtime, mtime))
    return path


# ── PLAN / VALIDATE / EXECUTE separation ─────────────────────────────────

def test_plan_finds_old_dead_pid_temp_files(tmp_path):
    _make_tmp_file(tmp_path, pid=999_999, age_seconds=MIN_TEMP_FILE_AGE_SECONDS + 3600)
    actions = _find_abandoned_temp_files(str(tmp_path), min_age_seconds=MIN_TEMP_FILE_AGE_SECONDS)
    assert len(actions) == 1
    assert actions[0].action_type == "DELETE_ABANDONED_TEMP"


def test_plan_excludes_too_young_temp_files(tmp_path):
    _make_tmp_file(tmp_path, pid=999_999, age_seconds=60)  # 1 minute old
    actions = _find_abandoned_temp_files(str(tmp_path), min_age_seconds=MIN_TEMP_FILE_AGE_SECONDS)
    assert actions == []


def test_plan_excludes_alive_pid_temp_files(tmp_path):
    """Uses this test process's own PID -- guaranteed alive."""
    my_pid = os.getpid()
    _make_tmp_file(tmp_path, pid=my_pid, age_seconds=MIN_TEMP_FILE_AGE_SECONDS + 3600)
    actions = _find_abandoned_temp_files(str(tmp_path), min_age_seconds=MIN_TEMP_FILE_AGE_SECONDS)
    assert actions == [], "must never plan deletion of a file whose PID is alive, regardless of age"


def test_plan_excludes_non_matching_naming(tmp_path):
    old_file = tmp_path / "not_a_temp_file.json"
    old_file.write_text("data")
    mtime = time.time() - (MIN_TEMP_FILE_AGE_SECONDS + 3600)
    os.utime(old_file, (mtime, mtime))
    actions = _find_abandoned_temp_files(str(tmp_path), min_age_seconds=MIN_TEMP_FILE_AGE_SECONDS)
    assert actions == []


def test_validate_rejects_if_file_deleted_between_plan_and_validate(tmp_path):
    f = _make_tmp_file(tmp_path, pid=999_999, age_seconds=MIN_TEMP_FILE_AGE_SECONDS + 3600)
    action = PlannedAction(action_type="DELETE_ABANDONED_TEMP", path=str(f), reason="test")
    f.unlink()  # simulate the file vanishing between PLAN and VALIDATE
    eligible, reason = validate(action)
    assert not eligible
    assert reason == "FILE_NO_LONGER_EXISTS"


def test_validate_rejects_if_pid_became_alive(tmp_path):
    f = _make_tmp_file(tmp_path, pid=999_999, age_seconds=MIN_TEMP_FILE_AGE_SECONDS + 3600)
    action = PlannedAction(action_type="DELETE_ABANDONED_TEMP", path=str(f), reason="test")
    # rewrite the path to embed a live PID, simulating "eligibility changed"
    my_pid = os.getpid()
    new_path = f.parent / f"snapshot.json.tmp.{my_pid}.{time.time_ns()}"
    f.rename(new_path)
    action.path = str(new_path)
    eligible, reason = validate(action)
    assert not eligible
    assert reason == "PID_NOW_ALIVE"


def test_validate_accepts_genuinely_eligible_file(tmp_path):
    f = _make_tmp_file(tmp_path, pid=999_999, age_seconds=MIN_TEMP_FILE_AGE_SECONDS + 3600)
    action = PlannedAction(action_type="DELETE_ABANDONED_TEMP", path=str(f), reason="test")
    eligible, reason = validate(action)
    assert eligible
    assert reason == "ELIGIBLE"


def test_execute_deletes_exact_file_only(tmp_path):
    f = _make_tmp_file(tmp_path, pid=999_999, age_seconds=MIN_TEMP_FILE_AGE_SECONDS + 3600)
    sibling = tmp_path / "unrelated.txt"
    sibling.write_text("keep me")
    action = PlannedAction(action_type="DELETE_ABANDONED_TEMP", path=str(f), reason="test", bytes_estimate=100)
    outcome = execute(action)
    assert outcome["result"] == "SUCCESS"
    assert not f.exists()
    assert sibling.exists()


def test_execute_handles_missing_file_gracefully(tmp_path):
    action = PlannedAction(action_type="DELETE_ABANDONED_TEMP", path=str(tmp_path / "gone.tmp.1.1"), reason="test")
    outcome = execute(action)
    assert outcome["result"] == "FAILED"


# ── Denylist / protection ────────────────────────────────────────────────

def test_protected_exact_paths_include_all_four_databases():
    protected_names = {os.path.basename(p) for p in PROTECTED_EXACT_PATHS}
    assert protected_names == {
        "flex_complete_database.db", "wt_ops_v2.db",
        "transaction_first_lineage.db", "flex_investigation_archive.db",
    }


def test_is_protected_rejects_main_db():
    assert _is_protected(str(ROOT / "database" / "flex_complete_database.db"))


def test_is_protected_rejects_watchtower_db():
    assert _is_protected(str(ROOT / "database" / "wt_ops_v2.db"))


def test_is_protected_rejects_watchtower_named_variant():
    """Defense in depth: a hypothetical differently-named Watchtower store
    must also be caught by the substring guard, not just the exact list."""
    assert _is_protected("database/wt_ops_v2_backup_variant.db")
    assert _is_protected("database/watchtower_shadow_something.db")


def test_is_protected_allows_ordinary_temp_directory():
    assert not _is_protected(str(ROOT / "database" / "intelligence_snapshots"))


def test_validate_rejects_action_targeting_protected_path():
    action = PlannedAction(
        action_type="DELETE_ABANDONED_TEMP",
        path=str(ROOT / "database" / "wt_ops_v2.db"),
        reason="malicious or buggy plan somehow targeted a protected store",
    )
    eligible, reason = validate(action)
    assert not eligible
    assert reason == "PROTECTED_STORE_STRUCTURAL_GUARD"


# ── Full pass (plan -> validate -> execute -> audit) ─────────────────────

def test_full_lifecycle_pass_dry_run_performs_no_deletion(tmp_path, monkeypatch):
    _make_tmp_file(tmp_path, pid=999_999, age_seconds=MIN_TEMP_FILE_AGE_SECONDS + 3600)
    import src.ops.storage_lifecycle_runner as runner_mod

    monkeypatch.setattr(runner_mod, "INTELLIGENCE_SNAPSHOTS_DIR", str(tmp_path))
    monkeypatch.setattr(runner_mod, "LEDGER_PATH", str(tmp_path / "ledger.jsonl"))

    def fake_plan():
        return runner_mod._find_abandoned_temp_files(str(tmp_path), min_age_seconds=MIN_TEMP_FILE_AGE_SECONDS)
    monkeypatch.setattr(runner_mod, "plan", fake_plan)

    result = runner_mod.run_lifecycle_pass(dry_run=True, quiet=True)
    assert result.bytes_reclaimed == 0
    assert len(list(tmp_path.glob("*.tmp.*"))) == 1  # file still present


def test_full_lifecycle_pass_real_run_deletes_eligible_file(tmp_path, monkeypatch):
    f = _make_tmp_file(tmp_path, pid=999_999, age_seconds=MIN_TEMP_FILE_AGE_SECONDS + 3600)
    import src.ops.storage_lifecycle_runner as runner_mod

    monkeypatch.setattr(runner_mod, "LEDGER_PATH", str(tmp_path / "ledger.jsonl"))

    def fake_plan():
        return runner_mod._find_abandoned_temp_files(str(tmp_path), min_age_seconds=MIN_TEMP_FILE_AGE_SECONDS)
    monkeypatch.setattr(runner_mod, "plan", fake_plan)

    result = runner_mod.run_lifecycle_pass(dry_run=False, quiet=True)
    assert result.bytes_reclaimed > 0
    assert not f.exists()
    assert result.errors == []


def test_lifecycle_pass_skips_ineligible_and_records_audit(tmp_path, monkeypatch):
    """A file that becomes ineligible between PLAN and VALIDATE (deleted
    out from under the runner) must be recorded as SKIPPED_ELIGIBILITY_
    CHANGED, not crash the run."""
    f = _make_tmp_file(tmp_path, pid=999_999, age_seconds=MIN_TEMP_FILE_AGE_SECONDS + 3600)
    import src.ops.storage_lifecycle_runner as runner_mod

    ledger_path = str(tmp_path / "ledger.jsonl")
    monkeypatch.setattr(runner_mod, "LEDGER_PATH", ledger_path)

    planned = [runner_mod.PlannedAction(action_type="DELETE_ABANDONED_TEMP", path=str(f), reason="test", bytes_estimate=100)]
    monkeypatch.setattr(runner_mod, "plan", lambda: planned)
    f.unlink()  # vanish before validate runs

    result = runner_mod.run_lifecycle_pass(dry_run=False, quiet=True)
    assert len(result.skipped) == 1
    assert result.skipped[0]["reason"] == "FILE_NO_LONGER_EXISTS"

    from src.ops.storage_audit_ledger import read_ledger
    entries = read_ledger(ledger_path)
    assert any(e["action"] == "SKIPPED_ELIGIBILITY_CHANGED" for e in entries)


def test_audit_ledger_records_run_id_pressure_state(tmp_path, monkeypatch):
    import src.ops.storage_lifecycle_runner as runner_mod
    ledger_path = str(tmp_path / "ledger.jsonl")
    monkeypatch.setattr(runner_mod, "LEDGER_PATH", ledger_path)
    monkeypatch.setattr(runner_mod, "plan", lambda: [])

    result = runner_mod.run_lifecycle_pass(dry_run=False, quiet=True)
    assert result.run_id.startswith("storage-lifecycle-")
    assert result.pressure_state in ("NORMAL", "WARNING", "PRESSURE", "EMERGENCY", "HARD_FLOOR")


# ── Lease / duplicate scheduler / crash recovery ─────────────────────────

def test_duplicate_invocation_exits_cleanly_not_blocking(tmp_path):
    lease_path = str(tmp_path / "lifecycle.lock")
    with acquire_cleanup_lease(lease_path):
        with pytest.raises(CleanupLeaseHeldError):
            with acquire_cleanup_lease(lease_path):
                pass  # pragma: no cover


def test_stale_lease_recoverable_after_process_exit(tmp_path):
    lease_path = str(tmp_path / "lifecycle.lock")
    with acquire_cleanup_lease(lease_path):
        pass
    with acquire_cleanup_lease(lease_path):
        pass  # must succeed -- proves the lease doesn't leak


def test_crash_before_action_leaves_no_mutation(tmp_path, monkeypatch):
    """Simulates a crash during PLAN (before any EXECUTE) -- no file
    should be touched."""
    f = _make_tmp_file(tmp_path, pid=999_999, age_seconds=MIN_TEMP_FILE_AGE_SECONDS + 3600)
    import src.ops.storage_lifecycle_runner as runner_mod

    def crashing_plan():
        raise RuntimeError("simulated crash during PLAN")
    monkeypatch.setattr(runner_mod, "plan", crashing_plan)
    monkeypatch.setattr(runner_mod, "LEDGER_PATH", str(tmp_path / "ledger.jsonl"))

    result = runner_mod.run_lifecycle_pass(dry_run=False, quiet=True)
    assert f.exists()  # untouched
    assert any("PLAN_PHASE_ERROR" in e for e in result.errors)


def test_crash_after_deletion_before_audit_is_still_auditable(tmp_path, monkeypatch):
    """The RESERVE entry is written BEFORE execute(), and the terminal
    entry after -- so even if the process died between execute() and the
    terminal audit write, the RESERVE entry alone proves intent, and a
    reconciliation pass could detect the file is gone and infer
    completion. This test verifies the RESERVE-before-execute ordering."""
    f = _make_tmp_file(tmp_path, pid=999_999, age_seconds=MIN_TEMP_FILE_AGE_SECONDS + 3600)
    import src.ops.storage_lifecycle_runner as runner_mod

    ledger_path = str(tmp_path / "ledger.jsonl")
    monkeypatch.setattr(runner_mod, "LEDGER_PATH", ledger_path)
    planned = [runner_mod.PlannedAction(action_type="DELETE_ABANDONED_TEMP", path=str(f), reason="test", bytes_estimate=100)]
    monkeypatch.setattr(runner_mod, "plan", lambda: planned)

    runner_mod.run_lifecycle_pass(dry_run=False, quiet=True)

    from src.ops.storage_audit_ledger import read_ledger
    entries = read_ledger(ledger_path)
    reserve_idx = next(i for i, e in enumerate(entries) if e["action"] == "RESERVE_DELETE_ABANDONED_TEMP")
    terminal_idx = next(i for i, e in enumerate(entries) if e["action"] == "DELETE_ABANDONED_TEMP")
    assert reserve_idx < terminal_idx


# ── No forbidden operations anywhere in the runner ───────────────────────

def test_no_sql_delete_or_vacuum_in_runner_module():
    src = (ROOT / "src/ops/storage_lifecycle_runner.py").read_text()
    for line in src.splitlines():
        if ".execute(" in line:
            upper = line.upper()
            assert "DELETE FROM" not in upper
            assert "VACUUM" not in upper
            assert "ALTER TABLE" not in upper


def test_runner_never_references_shadow_db_row_deletion():
    src = (ROOT / "src/ops/storage_lifecycle_runner.py").read_text()
    assert "DELETE FROM retained_acquisition" not in src.upper()


def test_runner_never_references_artifactstore_deletion():
    src = (ROOT / "src/ops/storage_lifecycle_runner.py").read_text()
    assert "artifacts.delete" not in src.lower()
    assert "artifactstore.delete" not in src.lower()


def test_this_test_module_never_targets_real_snapshots_directory():
    """Structural guard on the test file itself."""
    src = Path(__file__).read_text()
    for line in src.splitlines():
        if "_make_tmp_file(" in line and "def " not in line:
            assert "database/intelligence_snapshots" not in line
