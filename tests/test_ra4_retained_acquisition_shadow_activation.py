from __future__ import annotations

from src.acquisition import factory
from src.acquisition.retained_observations import RetainedAcquisitionStoreV2


def _set_common_retention_env(monkeypatch, tmp_path):
    monkeypatch.setenv("RETAINED_ACQUISITION_DEGRADED_PATH", str(tmp_path / "retention_degraded"))
    monkeypatch.setenv("RETAINED_ACQUISITION_JOURNAL_BUFFER_SIZE", "8")
    monkeypatch.setenv("RETAINED_ACQUISITION_V2_DAILY_PAYLOAD_CAP_BYTES", str(1024 * 1024))


def test_shadow_only_activation_selects_ra4_store(monkeypatch, tmp_path):
    _set_common_retention_env(monkeypatch, tmp_path)
    monkeypatch.setenv("RETAINED_ACQUISITION_OBSERVATIONS_ENABLED", "0")
    monkeypatch.setenv("RETAINED_ACQUISITION_V2_SHADOW_ENABLED", "1")
    monkeypatch.setenv("RETAINED_ACQUISITION_V2_SHADOW_DATABASE_PATH", str(tmp_path / "retained_shadow.db"))
    monkeypatch.setenv("RETAINED_ACQUISITION_V2_SHADOW_ARTIFACT_PATH", str(tmp_path / "retained_shadow_artifacts"))
    configured = factory._configured_retained_store()
    assert configured is not None
    store, _journal = configured
    assert type(store).__name__ == "RetainedAcquisitionStoreV2"
    assert isinstance(store, RetainedAcquisitionStoreV2)
    assert str(tmp_path / "retained_shadow.db") in str(store.path)


def test_parallel_activation_enables_shadow_and_legacy_side_by_side(monkeypatch, tmp_path):
    _set_common_retention_env(monkeypatch, tmp_path)
    monkeypatch.setenv("RETAINED_ACQUISITION_OBSERVATIONS_ENABLED", "1")
    monkeypatch.setenv("RETAINED_ACQUISITION_DATABASE_PATH", str(tmp_path / "retained_legacy.db"))
    monkeypatch.setenv("RETAINED_ACQUISITION_V2_SHADOW_ENABLED", "1")
    monkeypatch.setenv("RETAINED_ACQUISITION_V2_SHADOW_DATABASE_PATH", str(tmp_path / "retained_shadow.db"))
    monkeypatch.setenv("RETAINED_ACQUISITION_V2_SHADOW_ARTIFACT_PATH", str(tmp_path / "retained_shadow_artifacts"))

    configured = factory._configured_retained_store()
    assert configured is not None
    store, _journal = configured
    assert type(store).__name__ == "_ParallelRetainedAcquisitionStore"
    assert len(store.stores) == 2
