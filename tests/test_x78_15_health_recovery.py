def test_wal_watchdog_requires_size_and_persistent_contention(monkeypatch):
    from src.core import creator_funding_worker as worker

    monkeypatch.setattr(worker, "WAL_ALERT_MB", 64)
    monkeypatch.setattr(worker, "WAL_BUSY_CYCLES", 3)

    assert not worker._wal_is_critically_pinned(38.8, 3)
    assert not worker._wal_is_critically_pinned(64.0, 2)
    assert worker._wal_is_critically_pinned(64.0, 3)


def test_listener_fd_watchdog_requires_persistent_high_samples():
    from src.core.pumpfun_curve_listener import _next_fd_high_cycle

    cycles = _next_fd_high_cycle(13, threshold=12, previous=0)
    assert cycles == 1
    assert _next_fd_high_cycle(1, threshold=12, previous=cycles) == 0
    assert _next_fd_high_cycle(13, threshold=12, previous=2) == 3
