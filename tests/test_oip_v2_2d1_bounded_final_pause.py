import sqlite3
import time

import pytest

from src.evidence.compact_migration import CompactMigrationSidecar


AUTHORITY = "authority-v1"


def ready_migration(tmp_path, count=40):
    root = tmp_path / "shadow"
    root.mkdir()
    canonical, sidecar = root / "canonical.db", root / "compact.db"
    with sqlite3.connect(canonical) as db:
        db.execute("""CREATE TABLE primitive_evidence_inputs(
          primitive_id TEXT,evidence_id TEXT,PRIMARY KEY(primitive_id,evidence_id))""")
        db.executemany("INSERT INTO primitive_evidence_inputs VALUES(?,?)",
                       ((f"p{i}", f"e{i}") for i in range(count)))
    migration = CompactMigrationSidecar(canonical, sidecar, shadow_root=root)
    migration.begin(1, "bounded-final-pause")
    while migration.state()["state"] == "BUILDING":
        migration.build_batch(13)
    migration.apply_deltas()
    return migration


def prevalidate(migration):
    return migration.prevalidate(
        authority_generation=AUTHORITY,
        current_authoritative_count=346_730,
        current_authority_provenance_count=6_457_475,
    )


def test_full_validation_is_outside_pause_and_final_delta_is_bounded(tmp_path, monkeypatch):
    migration = ready_migration(tmp_path)
    boundary = prevalidate(migration)
    assert boundary["writers_live"] is True
    assert migration.control()["writers_paused"] is False

    migration.write_relations((("new-1", "new-e1"),))
    migration.write_relations((("new-2", "new-e2"), ("new-3", "new-e3")))
    migration.write_relations((("p0", "e0"),))  # semantic duplicate: no outbox event
    migration.write_relations((("rolled", "back"),), rollback=True)

    # Prove the bounded path does not call the exhaustive validator while paused.
    exhaustive = migration.validate
    monkeypatch.setattr(migration, "validate", lambda: pytest.fail("full validation inside pause"))
    cutover = migration.bounded_cutover(
        authority_generation=AUTHORITY, max_pause_ms=30_000)
    monkeypatch.setattr(migration, "validate", exhaustive)

    bounded = cutover["bounded_validation"]
    assert cutover["pause_ms"] < 30_000
    assert bounded["delta_events"] == 3
    assert bounded["new_relations"] == 3
    assert bounded["missing_delta_relations"] == 0
    assert bounded["full_digest_inside_pause"] is False
    assert bounded["full_anti_join_inside_pause"] is False
    assert migration.validate()["exact"] is True
    migration.close()


def test_prevalidation_invalidates_if_outbox_moves_during_full_scan(tmp_path, monkeypatch):
    migration = ready_migration(tmp_path)
    exhaustive = migration.validate

    def validation_with_concurrent_write():
        result = exhaustive()
        migration.write_relations((("concurrent", "write"),))
        return result

    monkeypatch.setattr(migration, "validate", validation_with_concurrent_write)
    with pytest.raises(RuntimeError, match="boundary changed"):
        prevalidate(migration)
    assert migration.control()["reader_mode"] == "CANONICAL"
    assert migration.control()["writers_paused"] is False
    migration.close()


@pytest.mark.parametrize("delta_size", [0, 10, 100, 1_000])
def test_pause_scales_with_bounded_delta_not_full_corpus(tmp_path, delta_size):
    migration = ready_migration(tmp_path, count=1_100)
    prevalidate(migration)
    migration.write_relations(
        ((f"delta-p-{i}", f"delta-e-{i}") for i in range(delta_size)))
    result = migration.bounded_cutover(
        authority_generation=AUTHORITY, max_pause_ms=30_000)
    assert result["pause_ms"] < 30_000
    assert result["bounded_validation"]["delta_events"] == delta_size
    assert result["bounded_validation"]["new_relations"] == delta_size
    migration.close()


def test_timeout_aborts_to_live_canonical_authority(tmp_path, monkeypatch):
    migration = ready_migration(tmp_path)
    prevalidate(migration)
    original_pause = migration.pause_writers

    def expired_pause():
        original_pause()
        return time.monotonic_ns() - 31_000_000_000

    monkeypatch.setattr(migration, "pause_writers", expired_pause)
    with pytest.raises(TimeoutError, match="pause limit"):
        migration.bounded_cutover(authority_generation=AUTHORITY, max_pause_ms=30_000)
    assert migration.control()["reader_mode"] == "CANONICAL"
    assert migration.control()["writers_paused"] is False
    assert migration.state()["state"] == "CATCHING_UP"
    migration.close()


def test_short_cutover_compact_write_rollback_and_second_short_cutover(tmp_path):
    migration = ready_migration(tmp_path)
    prevalidate(migration)
    first = migration.bounded_cutover(authority_generation=AUTHORITY)
    migration.write_relations((("compact", "write"),))
    rollback = migration.rollback(max_pause_ms=30_000)
    assert rollback["pause_ms"] < 30_000
    assert rollback["reader_mode"] == "CANONICAL"
    assert rollback["reconciliation"]["inserted"] == 1
    assert migration.validate()["exact"] is True

    migration.transition("CATCHING_UP")
    migration.apply_deltas()
    prevalidate(migration)
    migration.write_relations((("second", "delta"),))
    second = migration.bounded_cutover(authority_generation=AUTHORITY)
    assert first["pause_ms"] < 30_000
    assert second["pause_ms"] < 30_000
    assert migration.validate()["exact"] is True
    migration.close()
