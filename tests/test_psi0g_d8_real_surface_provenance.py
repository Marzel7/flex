import json
from pathlib import Path

import pytest

from src.evidence.contracts.psi0g_real_surface_provenance import (
    Psi0gRealSurfaceProvenanceError,
    publish_real_provenance_surface,
    replay_real_provenance_surface,
)


ROOT = Path(__file__).resolve().parents[1]
D5 = ROOT / "docs/audits/psi0g_runs/psi0g-d5-real-provenance-retention-20260817-02"
D6 = ROOT / "docs/audits/psi0g_d6_d5_to_f13_compatible_adapter.json"
D7 = ROOT / "docs/audits/psi0g_runs/psi0g-d7-first-real-known-behaviour-surface-20260817-01"
AUTH = ROOT / "docs/audits/psi0g_d8_real_provenance_transition_authorization.json"


def publish(path: Path):
    return publish_real_provenance_surface(
        path, d5_path=D5, d6_audit_path=D6, d7_path=D7, authorization_path=AUTH,
    )


def test_exact_source_bytes_are_preserved_and_only_provenance_changes(tmp_path):
    result = publish(tmp_path / "real-surface")
    source_bytes = (D7 / "surface" / "surface.json").read_bytes()
    assert (result.path / "source-surface.json").read_bytes() == source_bytes
    source = json.loads(source_bytes)
    surface = json.loads((result.path / "surface.json").read_bytes())
    assert {key for key in source if source[key] != surface[key]} == {"fixture_only", "provenance_class"}
    assert surface["fixture_only"] is False
    assert surface["provenance_class"] == "RETAINED_REAL_KNOWN_BEHAVIOUR_OPERATIONAL_SURFACE"
    assert result.semantic_digest == json.loads((result.path / "transition.json").read_text())["source_semantic_digest"]


def test_real_surface_remains_non_authoritative_and_default_off(tmp_path):
    result = publish(tmp_path / "real-surface")
    surface = json.loads((result.path / "surface.json").read_text())
    transition = json.loads((result.path / "transition.json").read_text())
    assert not surface["consumer_enabled"] and surface["default_off"]
    assert not any(surface["authority"].values()) and not any(surface["interpretation"].values())
    assert not any(transition["authority"].values())
    nomination = surface["operational_roles"]["FUNDING_AND_LAUNCH_OPERATION"]["nominations"][0]
    assert nomination["nomination_state"] == "PROPOSED"
    assert nomination["quality_state"] == "DEGRADED" and nomination["completeness_state"] == "PARTIAL"


def test_replay_is_deterministic(tmp_path):
    result = publish(tmp_path / "real-surface")
    replay = replay_real_provenance_surface(
        result.path, d5_path=D5, d6_audit_path=D6, d7_path=D7, authorization_path=AUTH,
    )
    assert replay == result


def test_surface_or_transition_tamper_fails_closed(tmp_path):
    result = publish(tmp_path / "real-surface")
    path = result.path / "surface.json"
    value = json.loads(path.read_text())
    value["consumer_enabled"] = True
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
    with pytest.raises(Psi0gRealSurfaceProvenanceError, match="HASH_REPLAY_MISMATCH"):
        replay_real_provenance_surface(
            result.path, d5_path=D5, d6_audit_path=D6, d7_path=D7, authorization_path=AUTH,
        )


def test_destination_reuse_is_rejected(tmp_path):
    destination = tmp_path / "real-surface"
    destination.mkdir()
    with pytest.raises(Psi0gRealSurfaceProvenanceError, match="DESTINATION_NOT_NEW"):
        publish(destination)
