from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts.run_ra0_retained_acquisition_bounded_experiment import (
    _percentile,
    _resolve_sample_rowids,
    _wal_artifacts,
    _open_readonly_db,
    RA0ExperimentConfig,
    run_experiment,
)


def _make_retained_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "retained.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE retained_acquisition_observations (
            observation_id TEXT PRIMARY KEY,
            schema_version INTEGER,
            launch_mint TEXT,
            acquisition_id TEXT,
            correlation_id TEXT,
            payload_json TEXT NOT NULL,
            retained_at INTEGER
        )
        """
    )
    conn.execute("CREATE TABLE retained_acquisition_outcomes (acquisition_id TEXT PRIMARY KEY, payload_json TEXT)")
    conn.execute("CREATE TABLE retained_acquisition_gaps (acquisition_id TEXT, gap_start INTEGER, gap_end INTEGER)")
    conn.commit()

    payload_template = {
        "acquisition_id": "acq",
        "correlation_id": "corr",
        "metadata": {"timestamp": "1760000000", "launch": "launch-1", "purpose": "probe", "provider": "provider-a"},
        "http_method": "GET",
        "url": "https://example.test",
        "request_payload": {"q": "x"},
        "response_status": 200,
        "artifact_digest": "digest-1",
        "response_data": {"ok": True},
        "response_text": "ok",
        "response_headers": {"x": "1"},
        "raw_body_base64": None,
        "artifact_representation": "bytes",
        "content_type": "application/json",
    }

    for idx in range(1, 6):
        payload = dict(payload_template)
        payload["acquisition_id"] = f"acq-{idx%2}"
        payload["correlation_id"] = f"corr-{idx%3}"
        payload["request_payload"] = {"n": idx}
        payload["artifact_digest"] = "digest-shared" if idx <= 4 else "digest-unique"
        payload["metadata"] = {
            "timestamp": str(1700000000 + idx),
            "launch": "launch-1",
            "purpose": "probe",
            "provider": "provider-a",
        }
        row = (f"obs-{idx}", 1, payload["metadata"]["launch"], payload["acquisition_id"], payload["correlation_id"], json.dumps(payload, sort_keys=True), 1_700_000_000 + idx)
        conn.execute("INSERT INTO retained_acquisition_observations VALUES (?,?,?,?,?,?,?)", row)
        conn.execute("INSERT OR REPLACE INTO retained_acquisition_outcomes VALUES (?, ?)", (payload["acquisition_id"], "{}"))

    conn.commit()
    conn.close()
    return db_path


def test_percentile_picks_expected_values():
    assert _percentile([1, 2, 3, 4, 5], 50) == 3
    assert _percentile([1, 2, 3, 4], 90) == 4
    assert _percentile([], 50) is None


def test_resolve_sample_rowids_is_deterministic(tmp_path: Path):
    conn = sqlite3.connect(tmp_path / "sample.db")
    conn.execute("CREATE TABLE retained_acquisition_observations (observation_id TEXT)")
    for i in range(1, 101):
        conn.execute("INSERT INTO retained_acquisition_observations VALUES(?)", (str(i),))
    conn.commit()
    rowids, windows = _resolve_sample_rowids(conn, row_count=100, rowid_min=1, rowid_max=100, high_water_rowid=100, ceiling=5)
    second_pass, second_windows = _resolve_sample_rowids(conn, row_count=100, rowid_min=1, rowid_max=100, high_water_rowid=100, ceiling=5)
    conn.close()
    assert rowids == second_pass
    assert windows == second_windows
    assert len(rowids) == 5


def test_resolve_sample_rowids_respects_high_water(tmp_path: Path):
    conn = sqlite3.connect(tmp_path / "sample.db")
    conn.execute("CREATE TABLE retained_acquisition_observations (observation_id TEXT)")
    for i in range(1, 101):
        conn.execute("INSERT INTO retained_acquisition_observations VALUES(?)", (str(i),))
    conn.commit()
    rowids, _ = _resolve_sample_rowids(conn, row_count=100, rowid_min=1, rowid_max=100, high_water_rowid=50, ceiling=60)
    conn.close()
    assert max(rowids) <= 50
    assert len(rowids) <= 60


def test_open_readonly_db_disables_immutable_for_wal_artifacts(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "retained.db"
    db_path.write_text("")
    wal_path, shm_path = _wal_artifacts(db_path)
    wal_path.write_text("")
    shm_path.write_text("")
    captured: list[tuple[str, bool]] = []

    original_connect = sqlite3.connect

    def fake_connect(*args, **kwargs):
        uri = str(args[0]) if args else kwargs.get("database")
        captured.append((uri or "", kwargs.get("uri", False)))
        original = original_connect(*args, **kwargs)
        original.close()
        raise sqlite3.OperationalError("read-only path for non-db test")

    monkeypatch.setattr("sqlite3.connect", fake_connect)
    try:
        _open_readonly_db(db_path, immutable_mode=False)
    except sqlite3.OperationalError:
        pass
    finally:
        monkeypatch.setattr("sqlite3.connect", original_connect)

    assert captured and "mode=ro&immutable=0" in captured[0][0]


def test_run_experiment_reads_and_reports_duplicates(tmp_path: Path):
    db_path = _make_retained_db(tmp_path)
    config = RA0ExperimentConfig(db_path=db_path, sample_ceiling=10, artifacts_to_check=10, min_free_bytes=1)
    experiment, preflight = run_experiment(config)

    assert experiment["stats"]["observation_count"] == 5
    assert experiment["stats"]["unique_correlation_ids"] == 3
    assert experiment["stats"]["unique_launch_mints"] == 1
    assert experiment["stats"]["repeated_artifact_groups"] == 1
    assert preflight["diagnosis"] in {
        "UNIQUE_VOLUME_DOMINANT",
        "MIXED_DUPLICATION_AND_VOLUME",
        "DUPLICATION_DOMINANT",
        "UNRESOLVED",
    }
    assert preflight["label"] == "BOUNDED_DUPLICATION_ESTIMATE"
    assert isinstance(preflight["blocked_outcome"], list)
