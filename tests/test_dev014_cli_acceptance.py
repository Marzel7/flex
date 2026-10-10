"""Actual-subprocess acceptance coverage for the isolated DEV-014 CLI fixture."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "run_watchtower_historical_backfill_session.py"
RANKS = "22,23,24,26,41,43,46,47,48,50"
SCALED_RANKS = "53,56,57,58,59,60,62,64,65,66,67,70"


def _command(root: Path, *, scenario: str = "healthy", crash_at: str | None = None,
             hold_path: Path | None = None, ranks: str = RANKS, max_requests: int = 1) -> list[str]:
    command = [sys.executable, str(CLI), "--mode", "execute", "--live-opt-in",
               "--test-production-fixture", "--test-scenario", scenario, "--ranks", ranks,
               "--state-dir", str(root / "state"), "--evidence-dir", str(root / "evidence"),
               "--queue-root", str(root / "queue"), "--max-requests", str(max_requests),
               "--max-runtime-seconds", "30", "--max-evidence-bytes", "1048576",
               "--max-consecutive-failures", "1", "--max-health-failures", "1"]
    if crash_at:
        command.extend(("--test-crash-at", crash_at))
    if hold_path:
        command.extend(("--test-hold-path", str(hold_path), "--test-hold-seconds", "10"))
    return command


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=20, check=False)


def _journal(root: Path) -> dict:
    return json.loads((root / "evidence" / "journal.json").read_text())


def _budget_calls(root: Path) -> list[dict]:
    return json.loads((root / "queue" / "provider_budget.json").read_text())["calls"]


@pytest.mark.parametrize("point", ("PENDING", "AFTER_BUDGET", "ADMITTED", "ATTEMPTED", "RESPONSE", "EVIDENCE"))
def test_actual_cli_crash_restart_matrix_never_repeats_admitted_work(tmp_path: Path, point: str) -> None:
    root = tmp_path / point
    crashed = _run(_command(root, crash_at=point))
    assert crashed.returncode == 1
    first = _journal(root)["records"][0]
    if point == "PENDING":
        assert first["state"] == "PENDING"
        restarted = _run(_command(root))
        assert restarted.returncode == 0, restarted.stderr
        result = json.loads(restarted.stdout)
        assert result["fixture_transport_calls"] == 1
        assert result["credential_reads"] == 0
        assert _journal(root)["records"][0]["state"] == "COMPLETED"
        assert len(_budget_calls(root)) == 1
    elif point == "EVIDENCE":
        assert first["state"] == "ATTEMPTED" and first["evidence_identity"]
        restarted = _run(_command(root))
        assert restarted.returncode == 1
        assert _journal(root)["records"][0]["state"] == "COMPLETED"
        assert len(_budget_calls(root)) == 1
    else:
        assert first["state"] in {"ADMITTED", "ATTEMPTED"}
        restarted = _run(_command(root))
        assert restarted.returncode == 1
        recovered = _journal(root)["records"][0]
        assert recovered["state"] == "OUTCOME_UNKNOWN"
        assert recovered["recovery_reason"] == "INTERRUPTED_AFTER_POSSIBLE_ADMISSION_OR_ATTEMPT"
        assert len(_budget_calls(root)) == 1


def test_actual_cli_same_root_concurrency_is_single_owner_and_releases_lock(tmp_path: Path) -> None:
    root, hold = tmp_path / "same-root", tmp_path / "hold"
    first = subprocess.Popen(_command(root, scenario="transport-hold", hold_path=hold), cwd=ROOT,
                             text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = time.monotonic() + 10
    while not (hold / "entered").exists():
        assert time.monotonic() < deadline, "first fixture never reached deterministic transport hold"
        time.sleep(0.02)
    second = _run(_command(root))
    assert second.returncode == 1
    assert "HISTORICAL_CONTROLLER_ALREADY_OWNED" in second.stderr
    (hold / "release").write_text("release")
    first_stdout, first_stderr = first.communicate(timeout=15)
    assert first.returncode == 0, first_stderr
    result = json.loads(first_stdout)
    assert result["fixture_transport_calls"] == 1
    assert result["credential_reads"] == 0
    assert len(_budget_calls(root)) == 1
    records = _journal(root)["records"]
    assert len(records) == 1 and records[0]["state"] == "COMPLETED"
    # A third process proves that the first controller relinquished its OS lock.
    third = _run(_command(root))
    assert third.returncode == 1
    assert "HISTORICAL_CONTROLLER_ALREADY_OWNED" not in third.stderr
    assert len(_budget_calls(root)) == 1


def test_actual_production_operator_reconstruction_carries_rank_53_entry_mc(tmp_path: Path) -> None:
    root = tmp_path / "rank-53"
    run = _run(_command(root, ranks=SCALED_RANKS))
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout)
    assert result["fixture_transport_calls"] == 1 and result["credential_reads"] == 0
    record = _journal(root)["records"][0]
    assert record["chronological_rank"] == 53
    assert record["state"] == "COMPLETED"
    assert record["entry_relative_observed_minima"]
