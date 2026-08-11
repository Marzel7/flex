import importlib
import sqlite3


def _worker():
    return importlib.import_module("src.core.creator_resolution_worker")


def test_true_wal_pin_requires_large_wal_and_repeated_stagnant_frame_gap():
    worker = _worker()
    previous = {"busy": 0, "log_frames": 100, "checkpointed_frames": 40}
    sample = {"busy": 0, "log_frames": 120, "checkpointed_frames": 40}

    assert worker._wal_sample_is_stalled(sample, previous) is True
    assert worker._wal_is_critically_pinned(64.0, worker.WAL_BUSY_CYCLES) is True


def test_large_fully_checkpointed_wal_is_not_a_pin():
    worker = _worker()
    previous = {"busy": 1, "log_frames": 20_000, "checkpointed_frames": 20_000}
    sample = {"busy": 1, "log_frames": 20_000, "checkpointed_frames": 20_000}

    assert worker._wal_sample_is_stalled(sample, previous) is False
    assert worker._wal_is_critically_pinned(512.0, 0) is False


def test_checkpoint_progress_resets_stall_even_when_busy_is_reported():
    worker = _worker()
    previous = {"busy": 1, "log_frames": 100, "checkpointed_frames": 40}
    sample = {"busy": 1, "log_frames": 120, "checkpointed_frames": 80}

    assert worker._wal_sample_is_stalled(sample, previous) is False


def test_unavailable_checkpoint_sample_is_not_reader_pin_evidence():
    worker = _worker()
    previous = {"busy": 0, "log_frames": 100, "checkpointed_frames": 40}
    sample = {"busy": 1, "log_frames": -1, "checkpointed_frames": -1}

    assert worker._wal_sample_is_stalled(sample, previous) is False


def test_process_local_connection_attribution_is_exposed(monkeypatch):
    worker = _worker()
    expected = {
        "open_count": 1,
        "oldest": [{"connection_id": "c-1", "transaction_open": True}],
    }
    locking = importlib.import_module("src.utils.db_locking")
    monkeypatch.setattr(locking, "get_open_connection_summary", lambda limit=25: expected)

    assert worker._self_connection_summary() == expected


def test_sqlite_reader_fixture_exposes_stagnant_gap_then_checkpoint_progress(tmp_path):
    worker = _worker()
    path = tmp_path / "wal-pin.db"
    writer = sqlite3.connect(path)
    reader = sqlite3.connect(path)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE facts(value INTEGER)")
        writer.commit()

        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM facts").fetchone()
        for value in range(50):
            writer.execute("INSERT INTO facts VALUES (?)", (value,))
            writer.commit()
        first_raw = writer.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        first = dict(zip(("busy", "log_frames", "checkpointed_frames"), first_raw))

        for value in range(50, 100):
            writer.execute("INSERT INTO facts VALUES (?)", (value,))
            writer.commit()
        second_raw = writer.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        second = dict(zip(("busy", "log_frames", "checkpointed_frames"), second_raw))

        assert first["log_frames"] > first["checkpointed_frames"]
        assert worker._wal_sample_is_stalled(second, first) is True

        reader.close()
        reader = None
        released_raw = writer.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        released = dict(zip(("busy", "log_frames", "checkpointed_frames"), released_raw))
        assert released["checkpointed_frames"] == released["log_frames"]
        assert worker._wal_sample_is_stalled(released, second) is False
    finally:
        if reader is not None:
            reader.close()
        writer.close()
