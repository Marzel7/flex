import json
from pathlib import Path

from scripts.recover_leviathan_post_last_confirmed import digest, recover


def test_recovery_is_deterministic_and_read_only(tmp_path):
    first = recover("database/wt_ops_v2.db")
    second = recover("database/wt_ops_v2.db")
    assert first["artifact_digest"] == second["artifact_digest"] == digest(first)
    assert first["provider_calls"] == 0
    assert first["observation_window"] == "POST_LEVIATHAN_OBSERVATION_WINDOW_EXISTS"
    output = tmp_path / "recovery.json"
    output.write_text(json.dumps(first, sort_keys=True))
    assert json.loads(output.read_text())["counts"]["post_boundary_launches"] > 0
