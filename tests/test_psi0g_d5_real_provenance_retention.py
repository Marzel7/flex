import json
from pathlib import Path

import pytest

from src.evidence.contracts.psi0g_real_provenance_retention import (
    Psi0gRealProvenanceRetentionError,
    replay_real_candidate_provenance,
    retain_real_candidate_provenance,
)
from tests.test_psi0g_d4_real_retention_preflight import inputs


def retain(path: Path):
    projection, manifest, disposition = inputs()
    from src.evidence.contracts.psi0g_real_retention_preflight import assess_real_retention_preflight
    preflight = assess_real_retention_preflight(projection, manifest, disposition)
    authorization = {
        "schema_version": "psi0g-d5.local-retention-authorization.v1",
        "status": "AUTHORIZED", "candidate_id": projection["candidate"]["candidate_id"],
        "preflight_digest": preflight["preflight_digest"],
        "action": "LOCAL_IMMUTABLE_PROVENANCE_RETENTION",
        "authority": {
            "supported": False, "same_operation": False, "same_human": False,
            "publication": False, "monitoring": False, "activation": False,
        },
    }
    return retain_real_candidate_provenance(
        path, projection=projection, projection_manifest=manifest, disposition=disposition,
        authorization=authorization,
    )


def test_exact_ready_provenance_is_atomically_retained_and_replayed(tmp_path):
    retained = retain(tmp_path / "retained")
    replay = replay_real_candidate_provenance(retained.path)
    assert replay.retention_id == retained.retention_id
    assert replay.manifest_digest == retained.manifest_digest
    manifest = json.loads((retained.path / "manifest.json").read_text())
    assert not manifest["fixture_f13_invoked"] and not manifest["f13_input_store_created"]
    assert not any(manifest["authority"].values())


def test_destination_is_create_once(tmp_path):
    destination = tmp_path / "retained"
    destination.mkdir()
    with pytest.raises(Psi0gRealProvenanceRetentionError, match="DESTINATION_NOT_NEW"):
        retain(destination)


def test_nonready_or_cross_candidate_review_cannot_be_retained(tmp_path):
    projection, manifest, disposition = inputs()
    disposition["candidate_id"] = "old"
    with pytest.raises(Psi0gRealProvenanceRetentionError, match="PREFLIGHT_NOT_READY"):
        retain_real_candidate_provenance(
            tmp_path / "retained", projection=projection,
            projection_manifest=manifest, disposition=disposition, authorization={},
        )
    assert not (tmp_path / "retained").exists()


def test_write_requires_exact_local_retention_authorization(tmp_path):
    projection, manifest, disposition = inputs()
    with pytest.raises(Psi0gRealProvenanceRetentionError, match="AUTHORIZATION_INVALID"):
        retain_real_candidate_provenance(
            tmp_path / "retained", projection=projection,
            projection_manifest=manifest, disposition=disposition, authorization={},
        )


def test_payload_tamper_fails_replay(tmp_path):
    retained = retain(tmp_path / "retained")
    path = retained.path / "disposition.json"
    value = json.loads(path.read_text())
    value["nomination_state"] = "SUPPORTED"
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
    with pytest.raises(Psi0gRealProvenanceRetentionError, match="PAYLOAD_IDENTITY_DRIFT"):
        replay_real_candidate_provenance(retained.path)


def test_extra_file_and_symlink_fail_closed(tmp_path):
    retained = retain(tmp_path / "retained")
    (retained.path / "extra").write_text("x")
    with pytest.raises(Psi0gRealProvenanceRetentionError, match="FILE_SET_DRIFT"):
        replay_real_candidate_provenance(retained.path)
    (retained.path / "extra").unlink()
    link = tmp_path / "link"
    link.symlink_to(retained.path)
    with pytest.raises(Psi0gRealProvenanceRetentionError, match="SOURCE_INVALID"):
        replay_real_candidate_provenance(link)
