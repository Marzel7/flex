import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_creator_window_artifacts_are_bounded_and_shadow_only():
    manifest = json.loads((ROOT / "docs/audits/c357_creator_window_shadow_manifest.v1.json").read_text())
    capture = json.loads((ROOT / "docs/audits/c357_creator_window_shadow_capture.v1.json").read_text())
    decode = json.loads((ROOT / "docs/audits/c357_creator_window_shadow_decode.v1.json").read_text())
    assert manifest["population"] == capture["creator_count"] == decode["population"] == 161
    assert not capture["errors"]
    assert decode["fixed_adjacent_transaction_budget"] == decode["provider_calls"] == 390
    assert not decode["transaction_errors"]
    assert decode["safety"] == {"membership_mutation": False, "fingerprint_change": False, "queue_change": False, "production_change": False, "recursive_follow_up": False}
    assert decode["comparison_only"]["C357_BASELINE"]["population"] == 38
    assert decode["comparison_only"]["EXACT_COMPATIBLE_COLLISION"]["population"] == 123


def test_creator_window_replay_never_calls_provider():
    result = subprocess.run([sys.executable, "scripts/c357_creator_window_shadow.py", "--replay"], cwd=ROOT, check=True, capture_output=True, text=True)
    assert "REPLAY_PASS" in result.stdout
    assert "provider_calls_during_replay=0" in result.stdout
