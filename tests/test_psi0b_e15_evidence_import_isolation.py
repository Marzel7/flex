import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/run_psi0b_production_shadow.py"


def test_direct_entrypoint_help_uses_dependency_minimal_imports_from_arbitrary_cwd(tmp_path):
    environment = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    result = subprocess.run(
        [str(SCRIPT), "--help"], cwd=tmp_path, env=environment,
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    assert "--execute" in result.stdout


def test_contract_import_does_not_eagerly_load_optional_runtime_modules(tmp_path):
    code = """
import json, sys
from src.evidence.contracts import production_shadow_telemetry_observer
print(json.dumps({name: name in sys.modules for name in (
    'src.evidence.service', 'src.evidence.mirror',
    'src.acquisition.transaction', 'aiohttp',
)}, sort_keys=True))
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT)
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=tmp_path, env=environment,
        capture_output=True, text=True, check=True,
    )
    assert json.loads(result.stdout) == {
        "aiohttp": False,
        "src.acquisition.transaction": False,
        "src.evidence.mirror": False,
        "src.evidence.service": False,
    }


def test_public_exports_remain_compatible_when_explicitly_requested():
    from src.evidence import EvidenceConfig, EvidencePlatform
    from src.evidence.config import EvidenceConfig as DirectConfig
    from src.evidence.service import EvidencePlatform as DirectPlatform

    assert EvidenceConfig is DirectConfig
    assert EvidencePlatform is DirectPlatform
    assert {"EvidenceConfig", "EvidencePlatform"}.issubset(dir(__import__("src.evidence", fromlist=["*"])))
