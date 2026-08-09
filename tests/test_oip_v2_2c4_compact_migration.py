import sqlite3

import pytest

from src.evidence.compact_migration import CompactMigrationSidecar


def fixture(tmp_path):
    canonical, sidecar = tmp_path/"canonical.db", tmp_path/"compact.db"
    with sqlite3.connect(canonical) as db:
        db.execute("CREATE TABLE primitive_evidence_inputs(primitive_id TEXT,evidence_id TEXT,PRIMARY KEY(primitive_id,evidence_id))")
        db.executemany("INSERT INTO primitive_evidence_inputs VALUES(?,?)",
                       (("p1","e1"),("p1","e2"),("p2","e1")))
    return canonical, sidecar


def test_resumable_build_delta_capture_cutover_and_rollback(tmp_path):
    canonical, sidecar = fixture(tmp_path)
    migration = CompactMigrationSidecar(canonical, sidecar)
    assert migration.begin("generation-1")["source_high_water"] == 3
    assert migration.build_batch(1)["cursor"] == 1
    with sqlite3.connect(canonical) as db:
        db.execute("INSERT INTO primitive_evidence_inputs VALUES('p3','e3')")
    migration.close()

    resumed = CompactMigrationSidecar(canonical, sidecar)
    assert resumed.state()["source_cursor"] == 1
    resumed.build_batch(10)
    assert resumed.apply_deltas()["inserted"] == 1
    result = resumed.cutover(writer_paused=True, authority_generation="authority-digest")
    assert result["validation"]["exact"] is True
    assert result["control"]["reader_mode"] == "COMPACT"
    assert result["control"]["authority_generation"] == "authority-digest"
    rollback = resumed.rollback()
    assert rollback["reader_mode"] == "CANONICAL"
    assert rollback["writer_mode"] == "CANONICAL_WITH_DELTA"
    with sqlite3.connect(canonical) as db:
        assert db.execute("SELECT COUNT(*) FROM primitive_evidence_inputs").fetchone()[0] == 4
    resumed.close()


def test_cutover_fails_closed_without_pause_or_complete_build(tmp_path):
    canonical, sidecar = fixture(tmp_path)
    migration = CompactMigrationSidecar(canonical, sidecar); migration.begin("g")
    with pytest.raises(RuntimeError, match="writer pause"):
        migration.cutover(writer_paused=False, authority_generation="a")
    with pytest.raises(RuntimeError, match="base build incomplete"):
        migration.cutover(writer_paused=True, authority_generation="a")
    migration.close()


def test_delta_replay_is_idempotent(tmp_path):
    canonical, sidecar = fixture(tmp_path)
    migration = CompactMigrationSidecar(canonical, sidecar); migration.begin("g")
    migration.build_batch(10)
    with sqlite3.connect(canonical) as db:
        db.execute("INSERT INTO primitive_evidence_inputs VALUES('p3','e3')")
    first = migration.apply_deltas(); second = migration.apply_deltas()
    assert first["inserted"] == 1
    assert second["rows"] == 0
    assert migration.validate()["exact"] is True
    migration.close()
