"""Actual-subprocess acceptance coverage for the isolated DEV-014 CLI fixture."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from src.ops.watchtower_historical_backfill_controller import HistoricalBackfillController


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "run_watchtower_historical_backfill_session.py"
RANKS = "22,23,24,26,41,43,46,47,48,50"
SCALED_RANKS = "53,56,57,58,59,60,62,64,65,66,67,70"
EXTENDED_RANKS = "71,72,74,75,77,78,79,80,82,84,86"

def _manifest(root: Path, ranks: str) -> Path:
    import importlib.util
    spec=importlib.util.spec_from_file_location('operator',CLI); op=importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(op)
    controller=HistoricalBackfillController(root/'manifest-controller.json',op._load(op.RECON),op._load(op.POP)); work={x['rank']:x for x in controller.work()}; records=[]
    for rank in map(int,ranks.split(',')):
        x=work[rank]; records.append({'rank':rank,'mint':x['mint'],'anchor':x['anchor'],'requested_window':{'time_from':x['request']['params']['time_from'],'time_to':x['request']['params']['time_to'],'interval':'1m'},'request_identity':x['request']['request_identity']})
    value={'schema':op.MANIFEST_SCHEMA,'version':1,'population_identity':op.POPULATION_IDENTITY,'authorization':{'kind':'EXPLICIT_FROZEN_MANIFEST','authority_id':'test','max_requests':len(records)},'max_authorized_requests':len(records),'records':records}; value['content_hash']=op._manifest_hash(value); path=root/'manifest.json'; path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(value)); return path


def _command(root: Path, *, scenario: str = "healthy", crash_at: str | None = None,
             hold_path: Path | None = None, ranks: str = RANKS, max_requests: int = 1,
             synthetic_clock: bool = False, synthetic_capacity: bool = False,
             max_runtime_seconds: int = 30, capacity_session: int = 1) -> list[str]:
    command = [sys.executable, str(CLI), "--mode", "execute", "--live-opt-in",
               "--test-production-fixture", "--test-scenario", scenario, "--manifest", str(_manifest(root,ranks)),
               "--state-dir", str(root / "state"), "--evidence-dir", str(root / "evidence"),
               "--queue-root", str(root / "queue"), "--max-requests", str(max_requests),
               "--max-runtime-seconds", str(max_runtime_seconds), "--max-evidence-bytes", "1048576",
               "--max-consecutive-failures", "1", "--max-health-failures", "1"]
    if crash_at:
        command.extend(("--test-crash-at", crash_at))
    if hold_path:
        command.extend(("--test-hold-path", str(hold_path), "--test-hold-seconds", "10"))
    if synthetic_clock:
        command.append("--test-synthetic-clock")
    if synthetic_capacity:
        command.append("--test-synthetic-capacity-fixture")
        command.extend(("--test-synthetic-capacity-session",str(capacity_session)))
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


def test_actual_production_operator_fixture_reconstructs_rank_71_entry_mc_without_credentials(tmp_path: Path) -> None:
    root = tmp_path / "rank-71"
    run = _run(_command(root, ranks=EXTENDED_RANKS))
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout)
    assert result["fixture_transport_calls"] == 1 and result["credential_reads"] == 0
    record = _journal(root)["records"][0]
    assert record["chronological_rank"] == 71
    assert record["state"] == "COMPLETED"
    assert record["entry_relative_observed_minima"]


def test_actual_cli_synthetic_clock_rolls_over_request_21_without_duplicate_debit(tmp_path: Path) -> None:
    root = tmp_path / "request-21"
    run = _run(_command(root, scenario="global-budget-denied", synthetic_clock=True,
                        max_runtime_seconds=3600))
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout)
    assert result["fixture_transport_calls"] == 1
    assert result["synthetic_clock"] is True and result["synthetic_time"] > 1060
    assert len(_budget_calls(root)) == 1
    assert _journal(root)["records"][0]["state"] == "COMPLETED"


def test_actual_cli_provider_backoff_uses_epoch_not_elapsed_clock(tmp_path: Path) -> None:
    blocked_root = tmp_path / "future-backoff"
    blocked = _run(_command(blocked_root, scenario="provider-backoff", synthetic_clock=True))
    assert blocked.returncode == 1 and "PROVIDER_GATE_CLOSED" in blocked.stderr
    assert not (blocked_root / "queue" / "provider_budget.json").exists()
    assert _journal(blocked_root)["records"][0]["state"] == "ADMISSION_INTENT"
    expired_root = tmp_path / "expired-backoff"
    expired = _run(_command(expired_root, scenario="provider-backoff-expired", synthetic_clock=True))
    assert expired.returncode == 0, expired.stderr
    assert json.loads(expired.stdout)["fixture_transport_calls"] == 1
    assert len(_budget_calls(expired_root)) == 1


def test_actual_cli_synthetic_clock_completes_all_frozen_eligible_identities(tmp_path: Path) -> None:
    root = tmp_path / "eligible"
    import importlib.util
    spec = importlib.util.spec_from_file_location("operator", CLI)
    op = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(op)
    controller = HistoricalBackfillController(root / "source.json", op._load(op.RECON), op._load(op.POP))
    eligible = [str(item["rank"]) for item in controller.work() if item.get("request")]
    assert len(eligible) == 561
    ranks = ",".join(eligible[:37])
    run = _run(_command(root, ranks=ranks, max_requests=37, synthetic_clock=True,
                        max_runtime_seconds=3600))
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout)
    assert result["request_count"] == result["fixture_transport_calls"] == 37
    assert result["synthetic_time"] >= 1061
    calls = _budget_calls(root)
    assert len(calls) <= 20
    assert len(_journal(root)["records"]) == 37
    assert {record["state"] for record in _journal(root)["records"]} == {"COMPLETED"}
    rejected = _run(_command(tmp_path / "request-fifty-one", ranks=ranks, max_requests=51,
                            synthetic_clock=True, max_runtime_seconds=3600))
    assert rejected.returncode == 1 and "REQUEST_CAP_EXCEEDED" in rejected.stderr


def test_actual_cli_synthetic_capacity_fixture_completes_fifty_with_two_rollovers(tmp_path: Path) -> None:
    root = tmp_path / "synthetic-fifty"
    run = _run(_command(root, max_requests=50, synthetic_clock=True, synthetic_capacity=True,
                        max_runtime_seconds=3600))
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout)
    assert result["synthetic_fixture_only"] is True
    assert result["request_count"] == result["fixture_transport_calls"] == 50
    assert result["synthetic_waits"] == 2 and result["synthetic_time"] >= 1122
    records = _journal(root)["records"]
    assert len(records) == 50 and {r["state"] for r in records} == {"COMPLETED"}
    assert {r["fixture_only"] for r in records} == {True}
    assert len({r["request_identity"] for r in records}) == 50
    assert len(_budget_calls(root)) <= 20
    rejected = _run(_command(tmp_path / "synthetic-fifty-one", max_requests=51,
                            synthetic_clock=True, synthetic_capacity=True, max_runtime_seconds=3600))
    assert rejected.returncode == 1 and "REQUEST_CAP_EXCEEDED" in rejected.stderr

def test_actual_cli_two_synthetic_sessions_are_disjoint_and_durable(tmp_path: Path) -> None:
    root=tmp_path/'two-sessions'
    first=_run(_command(root,max_requests=50,synthetic_clock=True,synthetic_capacity=True,max_runtime_seconds=3600))
    second=_run(_command(root,max_requests=50,synthetic_clock=True,synthetic_capacity=True,max_runtime_seconds=3600,capacity_session=2))
    assert first.returncode==second.returncode==0
    records=_journal(root)['records']; assert len(records)==100
    assert len({x['request_identity'] for x in records})==100


@pytest.mark.parametrize("scenario,expected", (("wait-cancel", "CANCELLED"), ("wait-health-failure", "API_HEALTH_DENIED")))
def test_actual_cli_wait_interruptions_are_provider_free(tmp_path: Path, scenario: str, expected: str) -> None:
    run = _run(_command(tmp_path / scenario, scenario=scenario, max_requests=50,
                        synthetic_clock=True, synthetic_capacity=True, max_runtime_seconds=3600))
    if expected == "CANCELLED":
        assert run.returncode == 0 and json.loads(run.stdout)["status"] == expected
    else:
        assert run.returncode == 1 and expected in run.stderr
    journal = _journal(tmp_path / scenario)
    # Request 21 has a durable PENDING identity, but its denied admission
    # never debits capacity or reaches transport.
    assert len(journal["records"]) == 21
    assert len(_budget_calls(tmp_path / scenario)) == 20


def test_actual_cli_wait_crash_recovers_without_duplicate_transport(tmp_path: Path) -> None:
    root = tmp_path / "wait-crash"
    crashed = _run(_command(root, scenario="wait-crash", max_requests=50,
                            synthetic_clock=True, synthetic_capacity=True, max_runtime_seconds=3600))
    assert crashed.returncode == 1 and "SIMULATED_WAIT_CRASH" in crashed.stderr
    assert len(_journal(root)["records"]) == 21
    restarted = _run(_command(root, max_requests=50, synthetic_clock=True,
                              synthetic_capacity=True, max_runtime_seconds=3600))
    assert restarted.returncode == 0, restarted.stderr
    assert json.loads(restarted.stdout)["fixture_transport_calls"] == 30
    assert len(_journal(root)["records"]) == 50


def test_actual_cli_synthetic_wait_respects_session_deadline(tmp_path: Path) -> None:
    root = tmp_path / "wait-deadline"
    run = _run(_command(root, max_requests=50, synthetic_clock=True,
                        synthetic_capacity=True, max_runtime_seconds=60))
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout)
    assert result["status"] == "SESSION_DEADLINE_REACHED"
    assert result["fixture_transport_calls"] == 20
    assert len(_budget_calls(root)) == 20 and len(_journal(root)["records"]) == 21
