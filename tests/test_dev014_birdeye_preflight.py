from __future__ import annotations

import subprocess
import sys
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_dev014_birdeye_preflight.py"


def _module():
    spec = importlib.util.spec_from_file_location("dev014_birdeye_wrapper", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_synthetic_credential_is_environment_only_and_not_disclosed(tmp_path: Path) -> None:
    secret = "synthetic-dev014-birdeye-only"
    env_file = tmp_path / ".env"
    env_file.write_text(f"BIRDEYE={secret}\n")
    result = subprocess.run([sys.executable, str(SCRIPT), "--env-file", str(env_file)], text=True, capture_output=True)
    assert result.returncode == 0
    assert '"BIRDEYE_PRESENT": true' in result.stdout
    assert '"argv_contains_credential": false' in result.stdout
    assert secret not in result.stdout
    assert secret not in result.stderr


def test_missing_credential_fails_closed_without_disclosure(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OTHER=value\n")
    result = subprocess.run([sys.executable, str(SCRIPT), "--env-file", str(env_file)], text=True, capture_output=True)
    assert result.returncode != 0
    assert "BIRDEYE_REQUIRED" in result.stderr


def test_frozen_cli_receives_secret_only_through_environment(monkeypatch) -> None:
    module = _module()
    secret = "synthetic-dev014-production-only"
    monkeypatch.setenv("BIRDEYE", secret)
    captured = {}

    def fake_run(argv, *, env, check):
        captured.update(argv=argv, env=env, check=check)
        return type("Result", (), {"returncode": 0})()

    assert module._execute_frozen_session(fake_run) == 0
    assert secret not in captured["argv"]
    assert captured["env"]["BIRDEYE"] == secret
    assert captured["env"].keys() == {"PATH", "PYTHONPATH", "BIRDEYE", module.BOUND}
    assert "--max-requests" in captured["argv"] and "11" in captured["argv"]
