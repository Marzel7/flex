import json
import subprocess
import sys
from pathlib import Path


SCRIPT = Path("scripts/run_ops_discovery_p3r_s2b_source_snapshot_isolated.py")


def run(tmp_path, run_id, outcome="pass"):
    return subprocess.run([sys.executable, str(SCRIPT), "--dry-run", "--dry-run-outcome", outcome, "--run-id", run_id, "--output-root", str(tmp_path)], text=True, capture_output=True, check=False)


def test_fixture_run_has_isolated_attributable_pass_lifecycle(tmp_path):
    run_id = "s2b-source-snapshot-fixture-pass-0001"
    result = run(tmp_path, run_id)
    lifecycle = json.loads((tmp_path / run_id / "lifecycle.json").read_text())
    assert result.returncode == 0
    assert lifecycle["status"] == "PASS"
    assert lifecycle["run_id"] == run_id
    assert lifecycle["runner"]["pid"]
    assert lifecycle["runner"]["ppid"] == lifecycle["launcher"]["pid"]
    assert lifecycle["runner"]["command"]
    assert (tmp_path / run_id / "stdout.log").read_text().strip() == "fixture-run-id=" + run_id
    assert (tmp_path / run_id / "snapshot.sqlite").exists()
    assert lifecycle["legacy_quarantine_excluded"]["terminal_audit"]["path"].endswith("source_boundary_snapshot.json")


def test_collision_refuses_reuse_without_mutating_existing_namespace(tmp_path):
    run_id = "s2b-source-snapshot-fixture-collision-0001"
    first = run(tmp_path, run_id)
    before = (tmp_path / run_id / "lifecycle.json").read_bytes()
    second = run(tmp_path, run_id)
    assert first.returncode == 0
    assert second.returncode == 4
    assert "RUN_NAMESPACE_COLLISION" in second.stderr
    assert (tmp_path / run_id / "lifecycle.json").read_bytes() == before


def test_interrupted_fixture_is_terminal_hold_and_never_promotes_temp_snapshot(tmp_path):
    run_id = "s2b-source-snapshot-fixture-interrupted-0001"
    result = run(tmp_path, run_id, "interrupted")
    lifecycle = json.loads((tmp_path / run_id / "lifecycle.json").read_text())
    assert result.returncode == 3
    assert lifecycle["status"] == "HOLD_INTERRUPTED"
    assert lifecycle["temporary_snapshot_non_authoritative"] is True
    assert (tmp_path / run_id / "runner_candidate.sqlite.tmp").exists()
    assert not (tmp_path / run_id / "snapshot.sqlite").exists()
