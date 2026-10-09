"""Provider-free contract tests for the Batch 4 per-request runtime gate."""
from __future__ import annotations

import importlib.util
import json
import threading
from types import SimpleNamespace

import pytest

from src.ops.dev014_batch4_runtime_gate import Batch4RuntimeGate, RuntimeGateDenied, resolve_runtime_authority
from src.ops.token_data_provider_bindings import ProviderTransportOutcome


def _authority(tmp_path, *, bind="0.0.0.0:5002"):
    api_root = tmp_path / "api"; (api_root / "config").mkdir(parents=True)
    (api_root / "config" / "gunicorn.conf.py").write_text(f'bind = "{bind}"\n')
    db = tmp_path / "canonical.db"; db.write_text(""); db.with_name("canonical.db-wal").write_bytes(b"x")
    listener = tmp_path / "listener.log"
    listener.write_text('[WAL_CHECKPOINT] {"status":"ok","busy":0,"checkpointed_frames":2,"remaining_frames":1}\n')
    funding = tmp_path / "funding.log"; funding.write_text("")
    resolution = tmp_path / "resolution.log"; resolution.write_text("")
    config = tmp_path / "supervisord.conf"
    config.write_text(f'''[program:watchtower_api]\nenvironment=WATCHTOWER_FINAL_ROOT="{api_root}",DB_PATH="{db}"\n[program:watchtower_listener]\nstdout_logfile={listener}\n[program:creator_funding_worker]\nstdout_logfile={funding}\n[program:creator_resolution_worker]\nstdout_logfile={resolution}\n''')
    return resolve_runtime_authority(config), funding, resolution


def _health(*, healthy=True, funding_age=1, resolution_age=2):
    return (200, {"healthy": healthy, "db": "ok", "wal_warn": False, "workers": {"creator-funding": {"stale": False, "age_s": funding_age}, "creator-resolution": {"stale": False, "age_s": resolution_age}}})


def _processes(extra=()):
    return ["1 0 supervisord -c config", "2 1 python -m src.core.creator_funding_worker", "3 1 python -m src.core.creator_resolution_worker", *extra]


def _gate(authority, *, health=None, processes=None, free=8 * 1024**3, wal_size=None):
    return Batch4RuntimeGate(authority, health_fetch=lambda _url: health or _health(), process_lines=lambda: processes or _processes(), disk_usage=lambda _path: SimpleNamespace(free=free), wal_size=wal_size)


def test_resolves_declared_api_binding_not_port_8080(tmp_path):
    authority, _, _ = _authority(tmp_path)
    assert authority.api_url == "http://127.0.0.1:5002/healthz"


def test_declared_api_binding_has_no_port_8080_fallback(tmp_path):
    authority, _, _ = _authority(tmp_path)
    assert ":8080" not in authority.api_url


def test_rejects_wrong_or_missing_declared_api_identity(tmp_path):
    authority, _, _ = _authority(tmp_path)
    text = authority.supervisor_config.read_text().replace("WATCHTOWER_FINAL_ROOT=\"", "WRONG_ROOT=\"")
    authority.supervisor_config.write_text(text)
    with pytest.raises(RuntimeGateDenied, match="SUPERVISOR_ENV_MISSING:WATCHTOWER_FINAL_ROOT"):
        resolve_runtime_authority(authority.supervisor_config)
    with pytest.raises(RuntimeGateDenied, match="API_BINDING_NOT_LOCAL"):
        _authority(tmp_path / "bad", bind="10.0.0.9:5002")


@pytest.mark.parametrize("health", [_health(healthy=False), _health(funding_age=120), _health(resolution_age=120)])
def test_rejects_unhealthy_or_stale_creator_health(tmp_path, health):
    authority, _, _ = _authority(tmp_path)
    with pytest.raises(RuntimeGateDenied): _gate(authority, health=health).check()


def test_rejects_disk_wal_checkpoint_and_duplicate_failures(tmp_path):
    authority, _, _ = _authority(tmp_path)
    with pytest.raises(RuntimeGateDenied, match="DISK_HEADROOM_DENIED"): _gate(authority, free=4 * 1024**3).check()
    with pytest.raises(RuntimeGateDenied, match="WAL_SIZE_DENIED"):
        _gate(authority, wal_size=lambda _path: 500 * 1024**2).check()
    authority.listener_log.write_text('[WAL_CHECKPOINT] {"status":"ok","busy":1,"checkpointed_frames":0,"remaining_frames":4}\n')
    with pytest.raises(RuntimeGateDenied, match="CHECKPOINT_OBSTRUCTION"): _gate(authority).check()
    authority.listener_log.write_text('[WAL_CHECKPOINT] {"status":"ok","busy":0,"checkpointed_frames":2,"remaining_frames":0}\n')
    with pytest.raises(RuntimeGateDenied, match="DUPLICATE_OR_MISSING_RUNTIME"):
        _gate(authority, processes=_processes(["4 1 python -m src.core.creator_funding_worker"])).check()


def test_new_critical_event_fails_before_a_second_admission(tmp_path):
    authority, funding, _ = _authority(tmp_path)
    gate = _gate(authority)
    assert gate.check()["api_url"].endswith(":5002/healthz")
    funding.write_text("CRITICAL_WAL_PINNED\n")
    with pytest.raises(RuntimeGateDenied, match="NEW_CRITICAL_WAL_EVENT"): gate.check()


def test_healthy_gate_is_read_only_and_does_not_create_threads_or_files(tmp_path):
    authority, _, _ = _authority(tmp_path)
    before = {path.name for path in tmp_path.iterdir()}
    threads = {thread.ident for thread in threading.enumerate()}
    result = _gate(authority).check()
    assert result["storage"]["wal_bytes"] == 1
    assert {path.name for path in tmp_path.iterdir()} == before
    assert {thread.ident for thread in threading.enumerate()} == threads


def _runner_module():
    path = __import__("pathlib").Path(__file__).resolve().parents[1] / "scripts" / "run_watchtower_recent_first_price_forensics_batch_4.py"
    spec = importlib.util.spec_from_file_location("batch4_runner_for_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _runner_fixture(tmp_path, monkeypatch, *, gate_error=None):
    module = _runner_module()
    audits = tmp_path / "docs" / "audits"; audits.mkdir(parents=True)
    records = [{"rank": rank, "mint": f"mint-{rank}", "anchor_class": "QUALIFIED_ENTRY_ANCHOR", "anchor_timestamp": 120} for rank in range(31, 41)]
    population = audits / "population.json"
    population.write_text(json.dumps({"launches": [{"mint": item["mint"], "evidence": {"opening": {"entry_mc_usd": 10, "provenance": "fixture"}}} for item in records]}))
    anchors = audits / "anchors.json"; anchors.write_text("{}")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "OUTPUT", audits / "out.json")
    monkeypatch.setattr(module, "POPULATION", population)
    monkeypatch.setattr(module, "ANCHORS", anchors)
    monkeypatch.setattr(module, "_records", lambda: records)
    monkeypatch.setenv("BIRDEYE", "fixture")
    monkeypatch.setenv("DEV014_SUPERVISOR_CONFIG", str(tmp_path / "supervisord.conf"))
    class Gate:
        def check(self):
            if gate_error: raise RuntimeGateDenied(gate_error)
            return {"ok": True}
    monkeypatch.setattr(module, "resolve_runtime_authority", lambda _path: object())
    monkeypatch.setattr(module, "Batch4RuntimeGate", lambda _authority: Gate())
    admissions = []
    class Budget:
        def __init__(self, *_args): pass
        def admit(self, **kwargs): admissions.append(kwargs)
    monkeypatch.setattr(module, "HistoricalForensicsBudgetAdmission", Budget)
    calls = []
    class Transport:
        def __init__(self, **_kwargs): pass
        def __call__(self, request):
            calls.append(request)
            return ProviderTransportOutcome(200, {"success": True, "data": {"items": [{"unixTime": 120, "o": 10, "h": 11, "l": 9, "c": 10}]}}, {})
    monkeypatch.setattr(module, "BirdeyeProductionBinding", Transport)
    return module, admissions, calls


def test_failed_runtime_gate_prevents_budget_debit_and_provider_call(tmp_path, monkeypatch):
    module, admissions, calls = _runner_fixture(tmp_path, monkeypatch, gate_error="DISK_HEADROOM_DENIED")
    with pytest.raises(SystemExit, match="DISK_HEADROOM_DENIED"): module.main()
    assert admissions == [] and calls == []


def test_healthy_runtime_gate_reaches_exactly_one_admission_per_request_then_transport(tmp_path, monkeypatch):
    module, admissions, calls = _runner_fixture(tmp_path, monkeypatch)
    assert module.main() == 0
    assert len(admissions) == len(calls) == 10
    assert len({item["request_identity"] for item in admissions}) == 10
