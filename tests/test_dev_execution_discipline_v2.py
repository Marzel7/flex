import inspect
import json
import subprocess
from pathlib import Path

import pytest

from scripts import validate_dev_discipline as guard


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, text=True, capture_output=True).stdout.strip()


def _repo(tmp_path, name):
    root = tmp_path / name
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "dev@example.invalid")
    _git(root, "config", "user.name", "DEV Test")
    (root / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    _git(root, "add", "tracked.txt")
    _git(root, "commit", "-m", "baseline")
    _git(root, "branch", "-M", "codex/test-dev")
    return root


def _entry(root, **overrides):
    entry = {
        "dev_id": "DEV-TEST-A",
        "objective": "guard test",
        "branch": "codex/test-dev",
        "worktree": str(root),
        "starting_sha": _git(root, "rev-parse", "HEAD"),
        "current_sha": _git(root, "rev-parse", "HEAD"),
        "source_authority": "test source",
        "configuration_authority": "test configuration",
        "definition_of_done": ["tests pass"],
        "acceptance_criteria": ["guard accepts"],
        "dependencies": [],
        "status": "IMPLEMENTING",
        "next_action": "implement",
        "qualification_evidence": [],
        "integration_promotion_status": "NOT_PROMOTED",
        "touched_services": ["service-a"],
        "shared_mutable_state": ["state-a"],
    }
    entry.update(overrides)
    return entry


def test_admission_accepts_clean_and_rejects_dirty_or_missing_sha(tmp_path):
    root = _repo(tmp_path, "clean")
    guard.validate_dev_entry(_entry(root), root, [])
    (root / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(guard.GuardError, match="DIRTY_STARTING_WORKTREE"):
        guard.validate_dev_entry(_entry(root), root, [])
    _git(root, "checkout", "--", "tracked.txt")
    with pytest.raises(guard.GuardError, match="MISSING_STARTING_SHA"):
        guard.validate_dev_entry(_entry(root, starting_sha="0" * 40), root, [])


def test_two_independent_devs_are_allowed_but_shared_service_is_rejected(tmp_path):
    left = _repo(tmp_path, "left")
    right = _repo(tmp_path, "right")
    _git(right, "branch", "-M", "codex/test-dev-b")
    first = _entry(left)
    second = _entry(
        right,
        dev_id="DEV-TEST-B",
        branch="codex/test-dev-b",
        touched_services=["service-b"],
        shared_mutable_state=["state-b"],
    )
    register = {"policy": {"max_active_implementation_devs": 2}, "devs": [first, second]}
    guard.validate_register(register, left, [])
    second["touched_services"] = ["service-a"]
    with pytest.raises(guard.GuardError, match="CONCURRENT_SERVICE_OWNERSHIP"):
        guard.validate_register(register, left, [])


def test_test_paths_are_distinct_temporary_and_protected_paths_fail(tmp_path):
    first = Path("/private/tmp") / "discipline-v2-a"
    second = Path("/private/tmp") / "discipline-v2-b"
    guard.validate_test_paths([str(first), str(second)], [str(tmp_path / "production")])
    with pytest.raises(guard.GuardError, match="TEST_PATHS_NOT_ISOLATED"):
        guard.validate_test_paths([str(first), str(first)], [])
    with pytest.raises(guard.GuardError, match="TEST_PATH_OUTSIDE_TEMPORARY_ROOT"):
        guard.validate_test_paths([str(tmp_path / "not-tmp"), str(second)], [])


def test_missing_launcher_dependency_fails_qualification(tmp_path):
    root = _repo(tmp_path, "launcher")
    (root / "scripts").mkdir()
    (root / "scripts" / "launch.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (root / "config").mkdir()
    (root / "config" / "runtime.json").write_text("{}\n", encoding="utf-8")
    _git(root, "add", "scripts/launch.sh", "config/runtime.json")
    _git(root, "commit", "-m", "launcher")
    guard.validate_launcher(root, "scripts/launch.sh", ["config/runtime.json"])
    with pytest.raises(guard.GuardError, match="MISSING_LAUNCHER_DEPENDENCY"):
        guard.validate_launcher(root, "scripts/launch.sh", ["config/missing.json"])


def test_missing_monitoring_capability_fails_integration_admission():
    requirements = {"monitor_worker": "operational", "monitor_bridge": "operational"}
    actual = {
        "monitor_worker": {"state": "PRESENT_AND_REACHABLE"},
        "monitor_bridge": {"state": "MISSING_OR_DISCONNECTED"},
    }
    with pytest.raises(guard.GuardError, match="MISSING_OPERATIONAL_CAPABILITY:monitor_bridge"):
        guard.validate_capabilities(requirements, actual)


def test_handoff_requires_commit_clean_tree_remote_and_limitations(tmp_path):
    root = _repo(tmp_path, "handoff")
    contract = {
        "committed_sha": _git(root, "rev-parse", "HEAD"),
        "regression_results": "1 passed",
        "remote_verified": True,
        "limitations": "none",
        "next_action": "stop",
    }
    guard.validate_handoff(contract, root)
    contract["remote_verified"] = False
    with pytest.raises(guard.GuardError, match="HANDOFF_REMOTE_UNVERIFIED"):
        guard.validate_handoff(contract, root)


def test_guard_is_offline_and_never_uses_supervisor_control():
    source = inspect.getsource(guard)
    assert "supervisorctl" not in source
    assert "supervisord" not in source
    assert '["git", "-C"' in source
