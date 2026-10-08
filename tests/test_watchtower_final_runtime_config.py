import subprocess
from pathlib import Path

from scripts.verify_watchtower_final_runtime import ROOT, verify


def test_config_has_no_secrets_or_dirty_runtime_paths():
    text = (ROOT / "config/watchtower_final_runtime.json").read_text()
    assert "BIRDEYE=" not in text
    assert "HELIUS=" not in text
    assert "watchtower-two-attempt" not in text


def test_ledger_identity_and_runtime_modules():
    result = verify(subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip())
    assert result["worker"] == "src.ops.operation_monitor_service"
    assert result["product"] == "src.dev_runtime"


def test_launcher_dependency_closure_is_committed():
    launcher = (ROOT / "scripts/launch_watchtower_final.sh").read_text()
    required = (
        ROOT / "scripts/launch_watchtower_final.sh",
        ROOT / "scripts/verify_watchtower_final_runtime.py",
        ROOT / "config/watchtower_final_runtime.json",
    )
    assert "verify_watchtower_final_runtime.py" in launcher
    for path in required:
        relative = path.relative_to(ROOT)
        assert path.is_file()
        subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "--error-unmatch", str(relative)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
