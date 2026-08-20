"""STORAGE-LIFECYCLE-P2 Part 2/4: durable shadow-retention budget tests.

Proves: process restart does NOT reset the retention allowance.
All tests use isolated tmp_path fixtures -- never the real
database/evidence_platform/production/retained_acquisition_shadow.db.
No provider calls.
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.acquisition.retained_observations import RetainedAcquisitionStoreV2  # noqa: E402
from src.evidence.artifacts import ArtifactStore  # noqa: E402


def _make_store(tmp_path, *, cap_bytes: int = 1_000_000) -> RetainedAcquisitionStoreV2:
    db_path = tmp_path / "shadow.db"
    artifacts = ArtifactStore(tmp_path / "artifacts", enabled=True)
    return RetainedAcquisitionStoreV2(db_path, artifacts, daily_payload_cap_bytes=cap_bytes)


def _insert_observation(db_path: Path, *, payload_bytes: int, observation_id: str) -> None:
    """Directly inserts a row bypassing retain() -- simulates prior-process
    writes without needing a full AcquisitionResponse fixture."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS retained_acquisition_observations "
        "(observation_id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL, launch_mint TEXT, "
        "acquisition_id TEXT NOT NULL, correlation_id TEXT NOT NULL, payload_json TEXT NOT NULL, retained_at INTEGER NOT NULL)"
    )
    conn.execute(
        "INSERT INTO retained_acquisition_observations VALUES (?,2,NULL,?,?,?,?)",
        (observation_id, observation_id, observation_id, "x" * payload_bytes, int(time.time())),
    )
    conn.commit()
    conn.close()


def test_fresh_store_reconciles_to_zero(tmp_path):
    store = _make_store(tmp_path)
    assert store._payload_bytes == 0
    assert store._observation_count == 0


def test_restart_does_not_reset_allowance_below_cap(tmp_path):
    """The core proof: consume some budget, 'restart' (construct a new
    store instance against the same DB file), verify usage is NOT zero."""
    db_path = tmp_path / "shadow.db"
    artifacts = ArtifactStore(tmp_path / "artifacts", enabled=True)
    store1 = RetainedAcquisitionStoreV2(db_path, artifacts, daily_payload_cap_bytes=1_000_000)
    _insert_observation(db_path, payload_bytes=300_000, observation_id="obs-1")

    # simulate restart: construct a NEW instance against the SAME file
    store2 = RetainedAcquisitionStoreV2(db_path, artifacts, daily_payload_cap_bytes=1_000_000)
    assert store2._payload_bytes == 300_000, "restart must see the durable usage, not reset to 0"
    assert store2._observation_count == 1


def test_restart_does_not_reset_allowance_at_exactly_cap(tmp_path):
    # NOTE: RetainedAcquisitionStoreV2 enforces a hard floor of 1,000,000
    # bytes on daily_payload_cap_bytes (max(1_000_000, x)) -- pre-existing
    # behavior, not something this milestone changes. Cap values below
    # that floor are silently clamped up, so this test uses exactly the
    # floor value to test the "at cap" boundary precisely.
    db_path = tmp_path / "shadow.db"
    artifacts = ArtifactStore(tmp_path / "artifacts", enabled=True)
    RetainedAcquisitionStoreV2(db_path, artifacts, daily_payload_cap_bytes=1_000_000)
    _insert_observation(db_path, payload_bytes=1_000_000, observation_id="obs-exact")

    store2 = RetainedAcquisitionStoreV2(db_path, artifacts, daily_payload_cap_bytes=1_000_000)
    assert store2._payload_bytes == 1_000_000
    assert not store2._is_within_budget(1)  # exactly at cap -- one more byte must not fit


def test_restart_does_not_reset_allowance_above_cap(tmp_path):
    """DB already exceeds the cap (e.g. cap was lowered, or hot-payload
    fallback rows accumulated past it) -- restart must see this and refuse
    ALL further full-budget writes, not grant a fresh allowance."""
    db_path = tmp_path / "shadow.db"
    artifacts = ArtifactStore(tmp_path / "artifacts", enabled=True)
    RetainedAcquisitionStoreV2(db_path, artifacts, daily_payload_cap_bytes=1_000_000)
    _insert_observation(db_path, payload_bytes=1_500_000, observation_id="obs-over")

    store2 = RetainedAcquisitionStoreV2(db_path, artifacts, daily_payload_cap_bytes=1_000_000)
    assert store2._payload_bytes == 1_500_000
    assert not store2._is_within_budget(1)


def test_multiple_restarts_accumulate_correctly(tmp_path):
    db_path = tmp_path / "shadow.db"
    artifacts = ArtifactStore(tmp_path / "artifacts", enabled=True)
    RetainedAcquisitionStoreV2(db_path, artifacts, daily_payload_cap_bytes=10_000_000)
    _insert_observation(db_path, payload_bytes=100_000, observation_id="r1")
    s2 = RetainedAcquisitionStoreV2(db_path, artifacts, daily_payload_cap_bytes=10_000_000)
    assert s2._payload_bytes == 100_000
    _insert_observation(db_path, payload_bytes=200_000, observation_id="r2")
    s3 = RetainedAcquisitionStoreV2(db_path, artifacts, daily_payload_cap_bytes=10_000_000)
    assert s3._payload_bytes == 300_000
    _insert_observation(db_path, payload_bytes=50_000, observation_id="r3")
    s4 = RetainedAcquisitionStoreV2(db_path, artifacts, daily_payload_cap_bytes=10_000_000)
    assert s4._payload_bytes == 350_000


def test_missing_db_file_reconciles_to_zero_not_fail_closed(tmp_path):
    """A brand-new store (no file yet) is a genuinely fresh, zero-usage
    state -- this is NOT the 'corrupted/missing accounting' fail-closed
    case, it's the correct zero case."""
    db_path = tmp_path / "does_not_exist_yet.db"
    artifacts = ArtifactStore(tmp_path / "artifacts", enabled=True)
    store = RetainedAcquisitionStoreV2(db_path, artifacts, daily_payload_cap_bytes=1_000_000)
    assert store._payload_bytes == 0
    assert store._observation_count == 0


def test_corrupted_db_fails_closed_to_full_budget(tmp_path):
    """A DB file exists but is not valid SQLite (or the accounting query
    fails for any reason) -- must fail CLOSED (budget = fully consumed),
    never fail open (budget = fresh)."""
    db_path = tmp_path / "corrupt.db"
    db_path.write_bytes(b"this is not a valid sqlite file at all, just garbage bytes")
    artifacts = ArtifactStore(tmp_path / "artifacts", enabled=True)
    store = RetainedAcquisitionStoreV2(db_path, artifacts, daily_payload_cap_bytes=1_000_000)
    assert store._payload_bytes == 1_000_000  # == cap, i.e. fully consumed
    assert not store._is_within_budget(1)


def test_stale_metadata_missing_table_fails_closed(tmp_path):
    """DB file exists and is valid SQLite, but the expected table doesn't
    exist yet (e.g. a stale/partial file) -- must also fail closed."""
    db_path = tmp_path / "stale.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE some_other_table (x INTEGER)")
    conn.commit()
    conn.close()

    artifacts = ArtifactStore(tmp_path / "artifacts", enabled=True)
    store = RetainedAcquisitionStoreV2(db_path, artifacts, daily_payload_cap_bytes=1_000_000)
    assert store._payload_bytes == 1_000_000
    assert not store._is_within_budget(1)


def test_db_restored_from_backup_reconciles_from_restored_state(tmp_path):
    """A DB 'restored from backup' is indistinguishable from a normal
    restart from this store's perspective -- reconciliation must read
    whatever durable state is actually present, proving the mechanism
    doesn't depend on any restart-specific signal (like a PID file)."""
    original_db = tmp_path / "original.db"
    artifacts = ArtifactStore(tmp_path / "artifacts", enabled=True)
    RetainedAcquisitionStoreV2(original_db, artifacts, daily_payload_cap_bytes=1_000_000)
    _insert_observation(original_db, payload_bytes=400_000, observation_id="pre-backup")

    import shutil as _shutil
    restored_db = tmp_path / "restored_from_backup.db"
    _shutil.copy(original_db, restored_db)

    store = RetainedAcquisitionStoreV2(restored_db, artifacts, daily_payload_cap_bytes=1_000_000)
    assert store._payload_bytes == 400_000


def test_concurrent_writer_initialization_does_not_double_count(tmp_path):
    """Two store instances constructed against the same DB (simulating two
    process starts close together) must each independently reconcile to
    the SAME durable value -- neither should see the other's in-memory
    counter (there isn't one to see), proving no double-counting risk."""
    db_path = tmp_path / "shadow.db"
    artifacts = ArtifactStore(tmp_path / "artifacts", enabled=True)
    RetainedAcquisitionStoreV2(db_path, artifacts, daily_payload_cap_bytes=1_000_000)
    _insert_observation(db_path, payload_bytes=200_000, observation_id="shared")

    store_a = RetainedAcquisitionStoreV2(db_path, artifacts, daily_payload_cap_bytes=1_000_000)
    store_b = RetainedAcquisitionStoreV2(db_path, artifacts, daily_payload_cap_bytes=1_000_000)
    assert store_a._payload_bytes == store_b._payload_bytes == 200_000


def test_reconciliation_is_read_only_never_writes():
    """Structural guard: _reconcile_durable_usage must never execute an
    INSERT/UPDATE/DELETE/CREATE -- it opens the connection mode=ro."""
    src = (ROOT / "src/acquisition/retained_observations.py").read_text()
    method_start = src.index("    def _reconcile_durable_usage")
    method_end = src.index("\n    def _connect", method_start)
    body = src[method_start:method_end]
    assert 'f"file:{self.path.resolve()}?mode=ro"' in body
    execute_lines = [ln for ln in body.splitlines() if ".execute(" in ln]
    combined = "\n".join(execute_lines).upper()
    for verb in ("INSERT INTO", "UPDATE ", "DELETE FROM", "CREATE TABLE"):
        assert verb not in combined


def test_no_vacuum_or_schema_migration_in_reconciliation():
    src = (ROOT / "src/acquisition/retained_observations.py").read_text()
    method_start = src.index("def _reconcile_durable_usage")
    method_end = src.index("\n    def _connect")
    body = src[method_start:method_end].upper()
    assert "VACUUM" not in body
    assert "ALTER TABLE" not in body


def test_reconciliation_does_not_touch_real_production_shadow_db():
    """Structural guard: no sqlite3.connect(...) call in this test module
    may reference the real production shadow DB path."""
    src = Path(__file__).read_text()
    for line in src.splitlines():
        if "sqlite3.connect(" in line or "RetainedAcquisitionStoreV2(" in line:
            assert "database/evidence_platform" not in line
