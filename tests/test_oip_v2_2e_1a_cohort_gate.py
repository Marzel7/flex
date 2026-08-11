import json
from pathlib import Path

import pytest

from src.evidence.cohort_gate import CohortManifest, CohortManifestError, EvidenceMirrorCohortGate
from src.evidence.config import EvidenceConfig
from src.evidence.mirror import EvidenceMirrorPublisher
from src.acquisition.transaction import AcquisitionMetadata, AcquisitionResponse


MINT_A = "11111111111111111111111111111111"
MINT_B = "So11111111111111111111111111111111111111112"


def manifest(path: Path, mints=(MINT_A,), maximum=50):
    path.write_text(json.dumps({"cohort_id":"phase-1", "schema_version":1,
        "created_at":"2026-08-11T00:00:00Z", "purpose":"test", "maximum_mints":maximum,
        "mints":list(mints)}))
    return path


def response(mint):
    return AcquisitionResponse(200, {"result":{}}, None, {}, AcquisitionMetadata(
        "a", "c", "creator_funding", "creator", mint, "rpc", "helius_rpc", "getTransaction",
        None, None, 1.0, "none", 0), 1.0, raw_body=b"{}")


def test_manifest_is_canonical_and_membership_is_restart_deterministic(tmp_path):
    path = manifest(tmp_path / "cohort.json", (MINT_B, MINT_A))
    one = CohortManifest.load(path); two = CohortManifest.load(path)
    assert one.manifest_hash == two.manifest_hash
    assert EvidenceMirrorCohortGate(one).decide(MINT_A).state == "ACCEPTED_COHORT"
    assert EvidenceMirrorCohortGate(two).decide(MINT_B).state == "ACCEPTED_COHORT"


@pytest.mark.parametrize("mints,maximum", [((MINT_A, MINT_A), 50), ((MINT_A,) * 51, 50), (("invalid",), 50), ((MINT_A,), 51)])
def test_invalid_manifests_fail_closed(tmp_path, mints, maximum):
    with pytest.raises(CohortManifestError):
        CohortManifest.load(manifest(tmp_path / "bad.json", mints, maximum))


def test_empty_manifest_is_not_allow_all(tmp_path):
    gate = EvidenceMirrorCohortGate(CohortManifest.load(manifest(tmp_path / "empty.json", (), 0)))
    assert gate.decide(MINT_A).state == "EXCLUDED_NOT_IN_COHORT"
    assert gate.decide(None).state == "REJECTED_MISSING_COHORT_IDENTITY"


def test_mirror_excludes_noncohort_without_creating_intake(tmp_path):
    cfg = EvidenceConfig(platform_enabled=True, mirror_enabled=True, queue_enabled=True,
        artifact_store_enabled=True, mirror_cohort_enabled=True,
        mirror_cohort_manifest_path=manifest(tmp_path / "cohort.json"),
        database_path=tmp_path / "evidence.db", queue_path=tmp_path / "intake",
        artifact_path=tmp_path / "artifacts", mirror_spool_path=tmp_path / "spool")
    mirror = EvidenceMirrorPublisher(cfg)
    assert mirror.publish_nowait(response(MINT_B), http_method="POST", url="https://example.test", request_payload={}) is False
    health = mirror.health()
    assert health["metrics"]["counters"]["cohort_excluded_envelopes"] == 1
    assert not (tmp_path / "intake").exists()


def test_invalid_gate_blocks_evidence_but_not_the_response_path(tmp_path):
    cfg = EvidenceConfig(platform_enabled=True, mirror_enabled=True, mirror_cohort_enabled=True,
        mirror_cohort_manifest_path=tmp_path / "missing.json", database_path=tmp_path / "evidence.db",
        queue_path=tmp_path / "intake", artifact_path=tmp_path / "artifacts", mirror_spool_path=tmp_path / "spool")
    mirror = EvidenceMirrorPublisher(cfg)
    assert mirror.publish_nowait(response(MINT_A), http_method="POST", url="https://example.test", request_payload={}) is False
    assert mirror.health()["cohort_gate_valid"] is False
