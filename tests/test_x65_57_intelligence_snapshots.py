"""X65.57 — Persisted Discovery Intelligence Snapshots.

Unit tests for src/ops/intelligence_snapshots.py — the file-per-key,
atomic-write persistence layer for the LAST SUCCESSFUL result of
build_operational_intelligence()/build_pipeline_health(). These are
derived cache artefacts, never operational data: SQLite is never touched
by this module, so tests only need a temp directory.
"""
import json
import os
import time

import pytest

import src.ops.intelligence_snapshots as snap


@pytest.fixture(autouse=True)
def _isolated_snapshot_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(snap, "SNAPSHOT_DIR", str(tmp_path / "snapshots"))
    yield


def test_write_then_read_round_trips_payload():
    payload = {"total_launches": 42, "records": {"abc": {"x": 1}}}
    snap.write_snapshot("operational_intelligence", 86400, payload, build_duration_ms=1234.5)

    result = snap.read_snapshot("operational_intelligence", 86400)
    assert result is not None
    assert result.function == "operational_intelligence"
    assert result.window_seconds == 86400
    assert result.payload == payload
    assert result.build_duration_ms == 1234.5
    assert result.schema_version == snap.SNAPSHOT_SCHEMA_VERSION
    assert isinstance(result.computed_at, float)


def test_write_is_atomic_no_tmp_file_left_behind():
    snap.write_snapshot("pipeline_health", 604800, {"a": 1}, build_duration_ms=10.0)
    files = os.listdir(snap.SNAPSHOT_DIR)
    assert len(files) == 1
    assert not files[0].endswith(".tmp") and ".tmp." not in files[0]


def test_different_keys_produce_different_files_no_collision():
    snap.write_snapshot("operational_intelligence", 86400, {"a": 1}, build_duration_ms=1.0)
    snap.write_snapshot("operational_intelligence", 604800, {"a": 2}, build_duration_ms=1.0)
    snap.write_snapshot("pipeline_health", 86400, {"a": 3}, build_duration_ms=1.0)

    assert snap.read_snapshot("operational_intelligence", 86400).payload == {"a": 1}
    assert snap.read_snapshot("operational_intelligence", 604800).payload == {"a": 2}
    assert snap.read_snapshot("pipeline_health", 86400).payload == {"a": 3}


def test_missing_snapshot_returns_none_not_an_exception():
    assert snap.read_snapshot("operational_intelligence", 999999) is None


def test_corrupt_json_is_ignored_not_raised(tmp_path):
    os.makedirs(snap.SNAPSHOT_DIR, exist_ok=True)
    path = snap._snapshot_path("operational_intelligence", 86400)
    with open(path, "w") as f:
        f.write("{not valid json")
    assert snap.read_snapshot("operational_intelligence", 86400) is None


def test_schema_version_mismatch_is_ignored_not_treated_as_a_valid_snapshot():
    snap.write_snapshot("operational_intelligence", 86400, {"a": 1}, build_duration_ms=1.0)
    path = snap._snapshot_path("operational_intelligence", 86400)
    with open(path) as f:
        record = json.load(f)
    record["schema_version"] = snap.SNAPSHOT_SCHEMA_VERSION + 1
    with open(path, "w") as f:
        json.dump(record, f)

    assert snap.read_snapshot("operational_intelligence", 86400) is None


def test_missing_required_field_is_ignored():
    os.makedirs(snap.SNAPSHOT_DIR, exist_ok=True)
    path = snap._snapshot_path("operational_intelligence", 86400)
    with open(path, "w") as f:
        json.dump({"function": "operational_intelligence", "schema_version": snap.SNAPSHOT_SCHEMA_VERSION}, f)
    assert snap.read_snapshot("operational_intelligence", 86400) is None


def test_a_later_successful_write_overwrites_the_previous_snapshot():
    snap.write_snapshot("operational_intelligence", 86400, {"v": 1}, build_duration_ms=1.0)
    snap.write_snapshot("operational_intelligence", 86400, {"v": 2}, build_duration_ms=1.0)
    result = snap.read_snapshot("operational_intelligence", 86400)
    assert result.payload == {"v": 2}


def test_computed_at_reflects_write_time_not_a_fixed_value():
    before = time.time()
    snap.write_snapshot("operational_intelligence", 86400, {"a": 1}, build_duration_ms=1.0)
    after = time.time()
    result = snap.read_snapshot("operational_intelligence", 86400)
    assert before <= result.computed_at <= after


def test_snapshot_directory_created_on_demand():
    assert not os.path.exists(snap.SNAPSHOT_DIR)
    snap.write_snapshot("operational_intelligence", 86400, {"a": 1}, build_duration_ms=1.0)
    assert os.path.isdir(snap.SNAPSHOT_DIR)
