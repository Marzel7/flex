"""Cascade clean-source paths must remain bound to established external state."""

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_cascade_import_binds_logs_and_armed_file_outside_checkout(tmp_path):
    snapshot = tmp_path / "established" / "ws_snapshot.log"
    price = tmp_path / "established" / "ws_price_trace.log"
    armed = tmp_path / "established" / "armed_mode.txt"
    armed.parent.mkdir()
    armed.write_text("0")
    checkout_armed_before = (ROOT / "database" / "armed_mode.txt").read_bytes()
    environment = os.environ.copy()
    environment.update({
        "PYTHONPATH": str(ROOT),
        "WS_SNAPSHOT_LOG_PATH": str(snapshot),
        "WS_PRICE_TRACE_LOG_PATH": str(price),
        "WS_ARMED_STATE_PATH": str(armed),
        "DB_NULL_OWNER_DIAGNOSTICS_PATH": str(tmp_path / "established" / "null_owner.jsonl"),
    })
    code = """
import json, os
from src.core import ws_snapshot_logger, ws_price_tracer, ws_cascade
print('CASCADE_PATHS=' + json.dumps({
    'snapshot': ws_snapshot_logger._LOG_PATH,
    'price': ws_price_tracer._LOG_PATH,
    'armed': ws_cascade._ARMED_STATE_FILE,
    'armed_value': ws_cascade._armed_file_val,
    'null_owner': os.environ['DB_NULL_OWNER_DIAGNOSTICS_PATH'],
}))
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path,
                            env=environment, capture_output=True, text=True,
                            timeout=20, check=True)
    output = next(line.removeprefix("CASCADE_PATHS=") for line in result.stdout.splitlines()
                  if line.startswith("CASCADE_PATHS="))
    paths = json.loads(output)
    assert paths == {
        "snapshot": str(snapshot), "price": str(price), "armed": str(armed),
        "armed_value": "0",
        "null_owner": str(tmp_path / "established" / "null_owner.jsonl"),
    }
    assert snapshot.exists() and price.exists()
    assert not (ROOT / "logs" / "ws_snapshot.log").exists()
    assert not (ROOT / "logs" / "ws_price_trace.log").exists()
    assert (ROOT / "database" / "armed_mode.txt").read_bytes() == checkout_armed_before
    assert not (ROOT / "logs" / "diagnostics" / "x78_20_null_owner_episodes.jsonl").exists()


def test_null_owner_diagnostic_uses_explicit_external_path(tmp_path, monkeypatch):
    from src.core import database_write_service as service

    output = tmp_path / "established" / "null_owner.jsonl"
    monkeypatch.setenv("DB_NULL_OWNER_DIAGNOSTICS_PATH", str(output))
    monkeypatch.setattr(service, "probe_kernel_flock", lambda _path: {})
    monkeypatch.setattr(service, "_read_owner_metadata", lambda _path: {})
    monkeypatch.setattr(service.subprocess, "run", lambda *a, **k: type("Result", (), {"stdout": ""})())
    service._capture_null_owner_episode(
        database=str(tmp_path / "db"), lock_path=str(tmp_path / "lock"),
        owner_path=str(tmp_path / "owner"), command="offline-test",
        wait_seconds=1.0, blocked_episode_id="offline-test",
    )
    assert len(output.read_text().splitlines()) == 1
    assert not (ROOT / "logs" / "diagnostics" / "x78_20_null_owner_episodes.jsonl").exists()
