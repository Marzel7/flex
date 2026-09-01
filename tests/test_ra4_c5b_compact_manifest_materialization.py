"""RA4-C5B: focused tests for the compact retirement manifest materializer.

All tests run against tiny synthetic fixture SQLite files -- never the real
61.6 GiB legacy DB, and never make any network/provider calls. These mirror
the exact fixture-first validation approach used before the real
materialization run (which caught a real column-count bug before it ever
touched production data).
"""
from __future__ import annotations

import importlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import scripts.run_ra4_c5b_compact_manifest_materialization as materializer


def _build_fixture_legacy_db(path: Path, rows: list[dict]) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE retained_acquisition_observations (observation_id TEXT PRIMARY KEY, "
        "schema_version INTEGER, launch_mint TEXT, acquisition_id TEXT, correlation_id TEXT, "
        "payload_json TEXT, retained_at INTEGER)"
    )
    conn.execute("CREATE TABLE retained_acquisition_outcomes (acquisition_id TEXT PRIMARY KEY, outcome TEXT, recorded_at INTEGER)")
    conn.execute(
        "CREATE TABLE retained_acquisition_gaps (gap_id TEXT PRIMARY KEY, acquisition_id TEXT, "
        "launch_mint TEXT, correlation_id TEXT, purpose TEXT, provider TEXT, method TEXT, "
        "reason TEXT, recorded_at INTEGER)"
    )
    for i, row in enumerate(rows):
        payload = {
            "artifact_compressed_bytes": 10, "artifact_digest": row["digest"],
            "artifact_representation": "EXACT_PROVIDER_ARTIFACT", "artifact_size_bytes": 20,
            "content_type": "application/json", "http_method": "GET",
            "metadata": {
                "acquisition_id": row["acquisition_id"], "correlation_id": row["correlation_id"],
                "creator": "creatorX", "launch": row["mint"], "method": "getTransaction",
                "provider": "helius_rpc", "purpose": "creator_funding", "request_type": "x",
                "retry_count": 0, "timestamp": 1000.0 + i,
            },
            "raw_body_base64": "AAAA", "request_payload": None,
            "response_data": {"ok": True}, "response_headers": {"Content-Type": "application/json"},
            "response_status": 200, "response_text": "{}", "schema_version": 1,
            "url": f"https://x/?api-key=SECRET&i={i}",
        }
        conn.execute(
            "INSERT INTO retained_acquisition_observations VALUES (?,?,?,?,?,?,?)",
            (row["observation_id"], 1, row["mint"], row["acquisition_id"], row["correlation_id"], json.dumps(payload), 1000 + i),
        )
        conn.execute("INSERT INTO retained_acquisition_outcomes VALUES (?,?,?)", (row["acquisition_id"], "RETAINED", 1000 + i))
    conn.commit()
    conn.close()


def _run_materializer(tmp_path: Path, rows: list[dict]) -> Path:
    legacy_path = tmp_path / "legacy.db"
    compact_path = tmp_path / "compact.db"
    _build_fixture_legacy_db(legacy_path, rows)
    importlib.reload(materializer)
    materializer.LEGACY_PATH = legacy_path
    materializer.COMPACT_PATH = compact_path
    materializer.FETCH_CHUNK = 2  # force multi-chunk streaming even for tiny fixtures
    materializer.main()
    return compact_path


def _rows(n: int) -> list[dict]:
    return [
        {
            "observation_id": f"obs{i:03d}",
            "acquisition_id": f"acq{i:03d}",
            "correlation_id": f"corr{i % 3:03d}",
            "mint": f"mint{i % 2:03d}",
            "digest": f"digest{i:03d}",
        }
        for i in range(n)
    ]


def test_streaming_materialization_exact_row_and_mint_accounting(tmp_path):
    rows = _rows(7)
    compact_path = _run_materializer(tmp_path, rows)
    conn = sqlite3.connect(f"file:{compact_path}?mode=ro", uri=True)
    row_count = conn.execute("SELECT COUNT(*) FROM compact_observations").fetchone()[0]
    mint_count = conn.execute("SELECT COUNT(DISTINCT launch_mint) FROM compact_observations").fetchone()[0]
    conn.close()
    assert row_count == 7
    assert mint_count == 2  # mint000, mint001


def test_compact_row_content_matches_source_deterministically(tmp_path):
    rows = _rows(3)
    compact_path = _run_materializer(tmp_path, rows)
    conn = sqlite3.connect(f"file:{compact_path}?mode=ro", uri=True)
    got = conn.execute(
        "SELECT observation_id, acquisition_id, correlation_id, launch_mint, artifact_digest, url "
        "FROM compact_observations WHERE observation_id='obs001'"
    ).fetchone()
    conn.close()
    assert got == ("obs001", "acq001", "corr001", "mint001", "digest001", "https://x/?i=1")
    assert "SECRET" not in got[5]  # url sanitization preserved


def test_compact_excludes_bulk_payload_fields(tmp_path):
    compact_path = _run_materializer(tmp_path, _rows(2))
    conn = sqlite3.connect(f"file:{compact_path}?mode=ro", uri=True)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(compact_observations)")}
    conn.close()
    for excluded in ("response_data", "raw_body_base64", "response_text", "request_payload", "response_headers"):
        assert excluded not in cols


def test_outcome_aggregate_matches_source(tmp_path):
    compact_path = _run_materializer(tmp_path, _rows(5))
    conn = sqlite3.connect(f"file:{compact_path}?mode=ro", uri=True)
    got = dict(conn.execute("SELECT outcome, count FROM compact_outcomes_summary"))
    conn.close()
    assert got == {"RETAINED": 5}


def test_manifest_metadata_binds_source_identity(tmp_path):
    legacy_path = tmp_path / "legacy.db"
    compact_path = tmp_path / "compact.db"
    _build_fixture_legacy_db(legacy_path, _rows(1))
    importlib.reload(materializer)
    materializer.LEGACY_PATH = legacy_path
    materializer.COMPACT_PATH = compact_path
    materializer.main()

    conn = sqlite3.connect(f"file:{compact_path}?mode=ro", uri=True)
    meta = dict(conn.execute("SELECT key, value FROM manifest_metadata"))
    conn.close()
    assert meta["source_path"] == str(legacy_path)
    assert meta["source_row_count"] == "1"
    assert int(meta["source_size_bytes"]) == legacy_path.stat().st_size


def test_fail_closed_on_existing_compact_output(tmp_path):
    legacy_path = tmp_path / "legacy.db"
    compact_path = tmp_path / "compact.db"
    _build_fixture_legacy_db(legacy_path, _rows(1))
    compact_path.write_text("pre-existing, must not be overwritten")

    importlib.reload(materializer)
    materializer.LEGACY_PATH = legacy_path
    materializer.COMPACT_PATH = compact_path
    with pytest.raises(SystemExit) as exc_info:
        materializer.main()
    assert exc_info.value.code == 1
    assert compact_path.read_text() == "pre-existing, must not be overwritten"


def test_legacy_db_opened_strictly_read_only(tmp_path):
    """The materializer must never write to the source DB -- confirmed by
    checking the source file's mtime is unchanged after materialization."""
    legacy_path = tmp_path / "legacy.db"
    compact_path = tmp_path / "compact.db"
    _build_fixture_legacy_db(legacy_path, _rows(3))
    mtime_before = legacy_path.stat().st_mtime

    importlib.reload(materializer)
    materializer.LEGACY_PATH = legacy_path
    materializer.COMPACT_PATH = compact_path
    materializer.main()

    assert legacy_path.stat().st_mtime == mtime_before
