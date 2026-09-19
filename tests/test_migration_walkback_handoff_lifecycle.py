import os
import sqlite3
import threading

import pytest

from src.ops import migration_walkback_handoff_retry as handoff


def _databases(tmp_path):
    live_path = tmp_path / "live.db"
    ops_path = tmp_path / "ops.db"
    live = sqlite3.connect(live_path)
    live.execute(
        "CREATE TABLE token_analysis("
        "mint TEXT PRIMARY KEY,migrated_at INTEGER,migration_tx TEXT,"
        "earliest_tx_creator TEXT)"
    )
    live.execute("CREATE TABLE probe(value TEXT)")
    handoff.ensure_schema(live)
    live.commit()
    live.close()
    ops = sqlite3.connect(ops_path)
    ops.execute("CREATE TABLE wt_walkback_queue(mint TEXT PRIMARY KEY)")
    ops.commit()
    ops.close()
    return str(live_path), str(ops_path)


def _insert_queue(conn, *, mint, creator, live_conn):
    assert live_conn.execute("PRAGMA query_only").fetchone()[0] == 1
    assert not live_conn.in_transaction
    conn.execute("INSERT INTO wt_walkback_queue VALUES(?)", (mint,))


def test_long_discovery_is_query_only_and_does_not_block_live_writer(tmp_path, monkeypatch):
    live_path, ops_path = _databases(tmp_path)
    live = sqlite3.connect(live_path)
    live.execute("INSERT INTO token_analysis VALUES('mint',150,'sig',NULL)")
    live.commit()
    live.close()
    entered = threading.Event()
    release = threading.Event()

    def slow_classification(creator, ops_conn, live_conn):
        assert ops_conn.execute("PRAGMA query_only").fetchone()[0] == 1
        assert live_conn.execute("PRAGMA query_only").fetchone()[0] == 1
        assert not ops_conn.in_transaction and not live_conn.in_transaction
        entered.set()
        assert release.wait(5)
        return ("FULL_WALKBACK", None, None, None, "no_creator")

    monkeypatch.setattr(handoff, "classify_creator", slow_classification)
    monkeypatch.setattr(handoff, "enqueue_migration", _insert_queue)
    errors = []

    def maintenance():
        try:
            handoff.run_maintenance(live_path, ops_path, t0=100, now=200)
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    thread = threading.Thread(target=maintenance)
    thread.start()
    assert entered.wait(5)
    concurrent = sqlite3.connect(live_path, timeout=0.2)
    concurrent.execute("INSERT INTO probe VALUES('funding-writer')")
    concurrent.commit()
    concurrent.close()
    release.set()
    thread.join(5)
    assert not thread.is_alive() and not errors
    check = sqlite3.connect(live_path)
    assert check.execute("SELECT value FROM probe").fetchone()[0] == "funding-writer"
    assert check.execute(
        "SELECT state FROM migration_walkback_handoff WHERE mint='mint'"
    ).fetchone()[0] == handoff.COMPLETE
    check.close()


def test_ops_work_never_observes_a_live_write_transaction(tmp_path, monkeypatch):
    live_path, ops_path = _databases(tmp_path)
    live = sqlite3.connect(live_path)
    live.execute("INSERT INTO token_analysis VALUES('mint',150,'sig',NULL)")
    handoff.record_intent(live, mint="mint", creator=None,
                          migration_tx="sig", migrated_at=150, now=100)
    live.commit()
    live.close()
    monkeypatch.setattr(handoff, "enqueue_migration", _insert_queue)
    result = handoff.run_maintenance(live_path, ops_path, t0=None, now=200)
    assert result["outcomes"] == [("mint", handoff.COMPLETE)]


def test_sql_failure_rolls_back_ops_and_persists_retry_separately(tmp_path, monkeypatch):
    live_path, ops_path = _databases(tmp_path)
    live = sqlite3.connect(live_path)
    handoff.record_intent(live, mint="mint", creator=None,
                          migration_tx="sig", migrated_at=150, now=100)
    live.commit()
    live.close()

    def fail_after_insert(conn, *, mint, creator, live_conn):
        conn.execute("INSERT INTO wt_walkback_queue VALUES(?)", (mint,))
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(handoff, "enqueue_migration", fail_after_insert)
    result = handoff.run_maintenance(live_path, ops_path, t0=None, now=200)
    assert result["outcomes"] == [("mint", handoff.RETRY)]
    ops = sqlite3.connect(ops_path)
    assert ops.execute("SELECT count(*) FROM wt_walkback_queue").fetchone()[0] == 0
    ops.close()
    live = sqlite3.connect(live_path)
    assert live.execute(
        "SELECT state,attempt_count FROM migration_walkback_handoff WHERE mint='mint'"
    ).fetchone() == (handoff.RETRY, 1)
    live.execute("INSERT INTO probe VALUES('released')")
    live.commit()
    live.close()


def test_cancellation_rolls_back_and_closes_both_connections(tmp_path, monkeypatch):
    live_path, ops_path = _databases(tmp_path)
    live = sqlite3.connect(live_path)
    handoff.record_intent(live, mint="mint", creator=None,
                          migration_tx="sig", migrated_at=150, now=100)
    live.commit()
    live.close()

    def cancel(conn, *, mint, creator, live_conn):
        conn.execute("INSERT INTO wt_walkback_queue VALUES(?)", (mint,))
        raise KeyboardInterrupt("cancelled")

    monkeypatch.setattr(handoff, "enqueue_migration", cancel)
    with pytest.raises(KeyboardInterrupt, match="cancelled"):
        handoff.run_maintenance(live_path, ops_path, t0=None, now=200)
    ops = sqlite3.connect(ops_path, timeout=0.2)
    assert ops.execute("SELECT count(*) FROM wt_walkback_queue").fetchone()[0] == 0
    ops.execute("INSERT INTO wt_walkback_queue VALUES('after-cancel')")
    ops.commit()
    ops.close()
    live = sqlite3.connect(live_path, timeout=0.2)
    assert live.execute(
        "SELECT state FROM migration_walkback_handoff WHERE mint='mint'"
    ).fetchone()[0] == handoff.PENDING
    live.execute("INSERT INTO probe VALUES('after-cancel')")
    live.commit()
    live.close()


def test_repeated_empty_cycles_do_not_accumulate_descriptors(tmp_path):
    live_path, ops_path = _databases(tmp_path)
    fd_dir = "/dev/fd"
    before = len(os.listdir(fd_dir))
    for _ in range(40):
        assert handoff.run_maintenance(
            live_path, ops_path, t0=None, now=200
        )["outcomes"] == []
    after = len(os.listdir(fd_dir))
    assert after <= before + 2
