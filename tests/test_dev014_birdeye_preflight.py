from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_dev014_birdeye_preflight.py"


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
