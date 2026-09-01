"""RA4-C5: focused, local, read-only checks supporting the legacy
retained_acquisition.db retirement qualification.

These tests do NOT touch the real production legacy DB or make any
provider calls. They exercise the two properties the C5 preflight
(docs/audits/ra4_c5_legacy_retained_acquisition_retirement_preflight.json)
relies on:

1. RetainedAcquisitionStoreV2 has zero construction-time or read-time
   dependency on the legacy store's path -- it can be built and read
   from an isolated temp directory with no legacy DB anywhere on disk.
2. A RetainedObservation can be reconstructed from a V2-shaped payload
   via the module-level _observation_from_payload() helper, and its
   artifact_digest resolves against the store's OWN ArtifactStore
   (not the legacy store's artifact root) -- this was the exact
   distinction the live investigation initially got wrong (see the
   preflight's Part 7 "important_correction_disclosed" note).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.acquisition.retained_observations import (
    RetainedAcquisitionStoreV2,
    _observation_from_payload,
)
from src.acquisition.transaction import AcquisitionMetadata, AcquisitionResponse
from src.evidence.artifacts import ArtifactStore
from src.evidence.errors import ArtifactCorruption

MINT = "11111111111111111111111111111111"


def _response():
    metadata = AcquisitionMetadata(
        "acquisition", "correlation", "creator_funding", "creator", MINT,
        "json_rpc", "helius_rpc", "getTransaction", 1, None, 10.0, "miss", 0,
    )
    return AcquisitionResponse(
        200, {"result": {"ok": True}}, None, {"Content-Type": "application/json"},
        metadata, 1.0, b'{"result":{"ok":true}}', "EXACT_PROVIDER_ARTIFACT",
    )


def test_v2_store_has_no_legacy_path_dependency(tmp_path):
    store = RetainedAcquisitionStoreV2(
        tmp_path / "isolated_v2.db", ArtifactStore(tmp_path / "artifacts", enabled=True),
    )
    assert "retained_acquisition.db" not in str(store.path)
    assert store.get() == []  # succeeds with no legacy DB anywhere on disk


def test_replay_reconstruction_and_artifact_resolution_without_legacy_db(tmp_path):
    artifact_store = ArtifactStore(tmp_path / "artifacts", enabled=True)
    store = RetainedAcquisitionStoreV2(tmp_path / "v2.db", artifact_store)
    value = store.retain(
        _response(), http_method="POST", url="https://mainnet.helius-rpc.com/",
        request_payload={"a": 1},
    )

    import sqlite3, json
    conn = sqlite3.connect(store.path)
    row = conn.execute("SELECT payload_json FROM retained_acquisition_observations").fetchone()
    conn.close()
    payload = json.loads(row[0])

    obs = _observation_from_payload(payload, fallback_observation_id="fallback")
    assert obs.artifact_digest == value.artifact_digest

    # resolve against the STORE'S OWN artifact root, not any shared/legacy root
    resolved = artifact_store.get(obs.artifact_digest)
    assert resolved is not None


def test_observation_from_payload_reads_hot_payload_top_level_identity(tmp_path):
    """RA4-C5A found _observation_from_payload() didn't read hot payloads'
    top-level acquisition_id/correlation_id/launch_mint (only nested metadata,
    which full payloads use but hot payloads don't populate the same way).
    RA4-C5B fix: backfill from top level when metadata lacks these keys."""
    hot_payload = {
        "schema_version": 2,
        "observation_id": "obs123",
        "acquisition_id": "acq-hot",
        "correlation_id": "corr-hot",
        "launch_mint": "mintHot",
        "http_method": "POST",
        "url": "https://mainnet.helius-rpc.com/",
        "response_status": 200,
        "response_data_present": True,
        "response_text_present": False,
        "artifact_digest": "digestHot",
        "artifact_size_bytes": 10,
        "artifact_compressed_bytes": 5,
        "content_type": "application/json",
        "metadata": {"launch": "mintHot", "purpose": "creator_funding", "provider": "helius_rpc", "method": "getTransaction"},
        "retained_at": 1700000000,
    }
    obs = _observation_from_payload(hot_payload, fallback_observation_id="fallback")
    assert obs.metadata.get("acquisition_id") == "acq-hot"
    assert obs.metadata.get("correlation_id") == "corr-hot"
    assert obs.metadata.get("launch") == "mintHot"


def test_observation_from_payload_full_payload_unaffected_by_hot_backfill(tmp_path):
    """The backfill must not override values already present in a full
    payload's nested metadata (setdefault, not overwrite)."""
    full_payload = {
        "schema_version": 2,
        "observation_id": "obs456",
        "http_method": "GET",
        "url": "https://mainnet.helius-rpc.com/",
        "response_status": 200,
        "artifact_digest": "digestFull",
        "artifact_size_bytes": 10,
        "artifact_compressed_bytes": 5,
        "content_type": "application/json",
        "metadata": {"acquisition_id": "acq-full", "correlation_id": "corr-full", "launch": "mintFull"},
    }
    obs = _observation_from_payload(full_payload, fallback_observation_id="fallback")
    assert obs.metadata.get("acquisition_id") == "acq-full"
    assert obs.metadata.get("correlation_id") == "corr-full"
    assert obs.metadata.get("launch") == "mintFull"


def test_wrong_artifact_root_does_not_resolve_v2_digest(tmp_path):
    """Regression guard for the exact investigation mistake this milestone made:
    querying a different (e.g. legacy-shared) artifact root for a V2 digest
    must NOT silently succeed or be mistaken for a real evidence gap."""
    store_a = ArtifactStore(tmp_path / "artifacts_a", enabled=True)
    store_b = ArtifactStore(tmp_path / "artifacts_b", enabled=True)  # a different root entirely
    v2 = RetainedAcquisitionStoreV2(tmp_path / "v2.db", store_a)
    value = v2.retain(
        _response(), http_method="POST", url="https://mainnet.helius-rpc.com/",
        request_payload={"a": 1},
    )
    assert store_a.get(value.artifact_digest) is not None
    # a digest that only exists in store_a must FAIL CLOSED (raise), not silently
    # return None, when looked up against a different, genuinely isolated root --
    # this is exactly the FileNotFoundError-derived error the live investigation
    # hit when it mistakenly queried the wrong artifact root for a V2 digest.
    with pytest.raises(ArtifactCorruption):
        store_b.get(value.artifact_digest)
