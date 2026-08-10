def test_wal_watchdog_requires_size_and_persistent_contention(monkeypatch):
    from src.core import creator_funding_worker as worker

    monkeypatch.setattr(worker, "WAL_ALERT_MB", 64)
    monkeypatch.setattr(worker, "WAL_BUSY_CYCLES", 3)

    assert not worker._wal_is_critically_pinned(38.8, 3)
    assert not worker._wal_is_critically_pinned(64.0, 2)
    assert worker._wal_is_critically_pinned(64.0, 3)
