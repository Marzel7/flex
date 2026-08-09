import sqlite3

import pytest

from src.evidence.compact_migration import CompactMigrationSidecar


def corpus(tmp_path):
    root = tmp_path / "shadow"
    root.mkdir()
    canonical, sidecar = root / "canonical.db", root / "compact.db"
    with sqlite3.connect(canonical) as db:
        db.execute("""CREATE TABLE primitive_evidence_inputs(
          primitive_id TEXT,evidence_id TEXT,PRIMARY KEY(primitive_id,evidence_id))""")
        db.executemany("INSERT INTO primitive_evidence_inputs VALUES(?,?)",
                       ((f"p{i // 4}", f"e{i}") for i in range(40)))
    return root, canonical, sidecar


def test_shadow_guard_and_closed_transitions(tmp_path):
    root, canonical, sidecar = corpus(tmp_path)
    with pytest.raises(RuntimeError, match="shadow-only"):
        CompactMigrationSidecar(canonical, sidecar, shadow_root=tmp_path / "other")
    migration = CompactMigrationSidecar(canonical, sidecar, shadow_root=root)
    migration.begin(1)
    with pytest.raises(RuntimeError, match="invalid migration transition"):
        migration.transition("COMPACT_ACTIVE")
    migration.close()


def test_interruption_delta_capture_pause_cutover_rollback_and_recutover(tmp_path):
    root, canonical, sidecar = corpus(tmp_path)
    migration = CompactMigrationSidecar(canonical, sidecar, shadow_root=root)
    state = migration.begin(1)
    assert state["source_high_water"] == 40
    assert migration.build_batch(7)["cursor"] == 7

    # A committed write and its outbox row share the canonical transaction;
    # a rolled-back transaction produces neither relation nor outbox event.
    migration.write_relations((("new-p", "new-e"),))
    migration.write_relations((("rolled", "back"),), rollback=True)
    migration.close()
    with sqlite3.connect(canonical) as db:
        assert db.execute("SELECT COUNT(*) FROM compact_migration_delta").fetchone()[0] == 1
        assert db.execute("SELECT 1 FROM primitive_evidence_inputs WHERE primitive_id='rolled'").fetchone() is None

    # Process restart resumes from the committed cursor rather than zero.
    migration = CompactMigrationSidecar(canonical, sidecar, shadow_root=root)
    assert migration.state()["source_cursor"] == 7
    while migration.state()["state"] == "BUILDING": migration.build_batch(9)
    first = migration.apply_deltas()
    assert first["inserted"] == 1
    assert migration.apply_deltas()["rows"] == 0
    started = migration.prepare_cutover()
    with pytest.raises(RuntimeError, match="migration-paused"):
        migration.write_relations((("late", "write"),))
    result = migration.cutover(writer_paused=True, authority_generation="authority-v1")
    assert result["validation"]["exact"] is True
    assert result["control"]["reader_mode"] == "COMPACT"
    assert result["control"]["writer_mode"] == "COMPACT"
    assert started > 0

    # Compact-active writes are journaled and reconciled before rollback.
    migration.write_relations((("post", "cutover"), ("post", "cutover")))
    assert migration.repository.contains("post", "cutover")
    rollback = migration.rollback()
    assert rollback["reader_mode"] == "CANONICAL"
    with sqlite3.connect(canonical) as db:
        assert db.execute("SELECT 1 FROM primitive_evidence_inputs WHERE primitive_id='post'").fetchone()

    # Canonical activity after rollback catches up and a second cutover succeeds.
    migration.write_relations((("after", "rollback"),))
    migration.transition("CATCHING_UP")
    migration.apply_deltas()
    migration.prepare_cutover()
    second = migration.cutover(writer_paused=True, authority_generation="authority-v1")
    assert second["validation"]["exact"] is True
    assert second["control"]["reader_mode"] == "COMPACT"
    migration.close()


def test_crash_boundaries_are_recoverable_from_persisted_state(tmp_path):
    root, canonical, sidecar = corpus(tmp_path)
    migration = CompactMigrationSidecar(canonical, sidecar, shadow_root=root)
    migration.begin(3, migration_id="crash-matrix")
    migration.build_batch(11)
    cursor = migration.state()["source_cursor"]
    migration.close()  # crash during base build
    migration = CompactMigrationSidecar(canonical, sidecar, shadow_root=root)
    assert migration.state()["source_cursor"] == cursor
    while migration.state()["state"] == "BUILDING": migration.build_batch(11)
    migration.write_relations((("delta", "one"), ("delta", "two")))
    migration.apply_deltas(batch_size=1, through_sequence=1)
    delta_cursor = migration.state()["delta_cursor"]
    migration.close()  # crash during delta replay
    migration = CompactMigrationSidecar(canonical, sidecar, shadow_root=root)
    assert migration.state()["delta_cursor"] == delta_cursor
    migration.apply_deltas(batch_size=1)
    migration.prepare_cutover()
    migration.close()  # crash after final sidecar commit, before switch
    migration = CompactMigrationSidecar(canonical, sidecar, shadow_root=root)
    assert migration.control()["reader_mode"] == "CANONICAL"
    migration.cutover(writer_paused=True, authority_generation="authority-v1")
    migration.close()  # crash immediately after switch
    migration = CompactMigrationSidecar(canonical, sidecar, shadow_root=root)
    assert migration.control()["reader_mode"] == "COMPACT"
    assert migration.state()["state"] == "COMPACT_ACTIVE"
    migration.close()
