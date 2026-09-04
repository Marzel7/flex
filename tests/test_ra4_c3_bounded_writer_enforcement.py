"""RA4-C3: direct enforcement tests for RetainedAcquisitionStoreV2.

Prior tests (test_oip_v2_2e_1c1_retained_observations.py,
test_ra4_retained_acquisition_shadow_activation.py) qualify the LEGACY
store's fail-open behavior and the FACTORY's store-selection wiring, but
none of them exercise RetainedAcquisitionStoreV2._write_budgeted() -- the
daily payload cap and the 10 GiB disk-reserve floor -- against a real
SQLite write. This file closes that gap. All writes go to tmp_path-scoped
temporary databases; no production path is touched.
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from src.acquisition.retained_observations import (
    GIB_BYTES,
    RetainedAcquisitionStore,
    RetainedAcquisitionStoreV2,
    SCHEMA_VERSION_V2,
    _build_v2_hot_payload,
)
from src.acquisition.transaction import AcquisitionMetadata, AcquisitionResponse
from src.evidence.artifacts import ArtifactStore

MINT = "11111111111111111111111111111111"


def response(*, provider="helius_rpc", body=b'{"result":{}}', correlation="correlation"):
    metadata = AcquisitionMetadata(
        "acquisition", correlation, "creator_funding", "creator", MINT,
        "json_rpc", provider, "getTransaction", 1, None, 10.0, "miss", 0,
    )
    return AcquisitionResponse(200, {"result": {}}, None, {"Content-Type": "application/json"}, metadata, 1.0, body, "EXACT_PROVIDER_ARTIFACT")


def v2_store(root: Path, *, daily_cap_bytes: int = GIB_BYTES) -> RetainedAcquisitionStoreV2:
    return RetainedAcquisitionStoreV2(
        root / "retained_v2.db", ArtifactStore(root / "artifacts", enabled=True),
        daily_payload_cap_bytes=daily_cap_bytes,
    )


# --- hard daily cap ------------------------------------------------------

class _AboveFloorUsage:
    free = 15 * GIB_BYTES


def test_daily_cap_has_a_1mb_minimum_clamp(tmp_path):
    """The constructor enforces max(1_000_000, requested_cap) -- a requested
    cap below 1MB is silently raised to the 1MB floor, not honored literally.
    This is a real behavior worth documenting explicitly, not assumed."""
    st = v2_store(tmp_path, daily_cap_bytes=100)
    assert st.daily_payload_cap_bytes == 1_000_000


def test_write_budgeted_refuses_once_daily_cap_exceeded(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.disk_usage", lambda path: _AboveFloorUsage())
    st = v2_store(tmp_path, daily_cap_bytes=1_000_000)  # the effective minimum
    ok1, state1 = st._write_budgeted(900_000)
    assert ok1 is True
    ok2, state2 = st._write_budgeted(900_000)  # 900k+900k > 1,000,000
    assert ok2 is False
    assert state2["used_payload_bytes"] == 900_000  # second write did NOT count toward usage


def test_budget_guard_writes_no_artifact_or_shadow_row(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.disk_usage", lambda path: _AboveFloorUsage())
    st = v2_store(tmp_path, daily_cap_bytes=1_000_000)
    st._payload_bytes = 1_000_000  # simulate the day's budget already fully consumed
    with mock.patch.object(st.artifacts, "put", wraps=st.artifacts.put) as put:
        result = st.retain(response(), http_method="POST", url="https://mainnet.helius-rpc.com/", request_payload={"a": 1})
    assert result is None
    assert st.last_retention_status == "SHADOW_BUDGET_GUARD"
    put.assert_not_called()
    import sqlite3
    assert not st.path.exists()
    conn = sqlite3.connect(":memory:")
    # No shadow DB was created, so there is necessarily no compact fallback
    # row and no raw artifact/sidecar.
    conn.close()


def test_disk_guard_writes_no_artifact_or_shadow_row(tmp_path, monkeypatch):
    st = v2_store(tmp_path, daily_cap_bytes=GIB_BYTES)
    class LowDisk: free = 5 * GIB_BYTES
    monkeypatch.setattr("shutil.disk_usage", lambda path: LowDisk())
    with mock.patch.object(st.artifacts, "put", wraps=st.artifacts.put) as put:
        assert st.retain(response(), http_method="POST", url="https://mainnet.helius-rpc.com/", request_payload={}) is None
    assert st.last_retention_status == "SHADOW_DISK_GUARD"
    put.assert_not_called()
    assert not st.path.exists()


def test_explicit_shadow_disable_writes_nothing(tmp_path, monkeypatch):
    st = RetainedAcquisitionStoreV2(tmp_path / "shadow.db", ArtifactStore(tmp_path / "artifacts", enabled=True), shadow_enabled=False)
    with mock.patch.object(st.artifacts, "put", wraps=st.artifacts.put) as put:
        assert st.retain(response(), http_method="POST", url="https://mainnet.helius-rpc.com/", request_payload={}) is None
    assert st.last_retention_status == "SHADOW_DISABLED"
    put.assert_not_called()
    assert not st.path.exists()


def test_retain_stores_full_payload_when_within_budget(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.disk_usage", lambda path: _AboveFloorUsage())
    st = v2_store(tmp_path, daily_cap_bytes=GIB_BYTES)
    st.retain(response(), http_method="POST", url="https://mainnet.helius-rpc.com/", request_payload={"a": 1})
    import sqlite3, json
    conn = sqlite3.connect(st.path)
    row = conn.execute("SELECT payload_json FROM retained_acquisition_observations").fetchone()
    conn.close()
    stored = json.loads(row[0])
    assert "response_data" in stored  # full payload field present when budget allows


# --- disk reserve floor ----------------------------------------------------

def test_write_budgeted_refuses_below_10gib_disk_reserve(tmp_path, monkeypatch):
    st = v2_store(tmp_path, daily_cap_bytes=GIB_BYTES)

    class FakeUsage:
        free = 5 * GIB_BYTES  # below the 10 GiB floor

    monkeypatch.setattr("shutil.disk_usage", lambda path: FakeUsage())
    ok, state = st._write_budgeted(100)
    assert ok is False
    assert state["used_payload_bytes"] == 0  # nothing was counted


def test_write_budgeted_allows_above_10gib_disk_reserve(tmp_path, monkeypatch):
    st = v2_store(tmp_path, daily_cap_bytes=GIB_BYTES)

    class FakeUsage:
        free = 15 * GIB_BYTES  # above the floor

    monkeypatch.setattr("shutil.disk_usage", lambda path: FakeUsage())
    ok, state = st._write_budgeted(100)
    assert ok is True
    assert state["used_payload_bytes"] == 100


# --- hot payload preserves required replay/evidence fields ------------------

def test_hot_payload_preserves_required_identity_fields(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.disk_usage", lambda path: _AboveFloorUsage())
    st = v2_store(tmp_path, daily_cap_bytes=GIB_BYTES)
    resp = response()
    full_value = st.retain(resp, http_method="POST", url="https://mainnet.helius-rpc.com/?api-key=SECRET", request_payload={"a": 1})
    from dataclasses import asdict
    _, hot_json, _ = _build_v2_hot_payload(
        value=full_value, metadata=asdict(resp.metadata), sanitized_url="https://mainnet.helius-rpc.com/",
    )
    import json
    hot = json.loads(hot_json)
    # Required for deterministic replay / migration lineage / correlation identity
    for required_field in (
        "observation_id", "acquisition_id", "correlation_id", "launch_mint",
        "http_method", "url", "response_status", "artifact_digest",
        "artifact_size_bytes", "artifact_compressed_bytes", "content_type",
        "retained_at",
    ):
        assert required_field in hot, f"missing required field: {required_field}"
    # Credential must never leak into a stored payload
    assert "SECRET" not in hot["url"]
    assert "api-key" not in hot["url"]
    # Provider/method identity preserved via metadata subset
    assert hot["metadata"]["provider"] == "helius_rpc"
    assert hot["metadata"]["method"] == "getTransaction"
    # Request/response identity preserved as digests (not full bulk payload)
    assert hot.get("request_payload_sha256") is not None
    assert hot.get("response_headers_sha256") is not None


# --- legacy vs bounded store selection (config-only, no production writes) --

def test_legacy_store_has_no_budget_or_disk_enforcement():
    """Confirms the CONTRAST: the legacy store has no _write_budgeted at all --
    this is why it grows unboundedly and why cutover matters."""
    assert not hasattr(RetainedAcquisitionStore, "_write_budgeted")
    assert not hasattr(RetainedAcquisitionStore, "daily_payload_cap_bytes")


def test_bounded_store_default_cap_matches_1_gib_target():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        real = RetainedAcquisitionStoreV2(Path(d) / "x.db", ArtifactStore(Path(d) / "a", enabled=True))
        assert real.daily_payload_cap_bytes == GIB_BYTES  # exactly the 1 GiB/day target
