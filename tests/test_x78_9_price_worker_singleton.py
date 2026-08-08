"""X78.9 Phase 12 -- get_price_worker() singleton construction race.

py-spy on a live gunicorn worker (6 gthread workers, all inside
BackgroundPriceWorker.__init__ -> _ensure_tables()) confirmed concurrent
callers previously raced the `if _price_worker is None` check with no lock,
each starting its own construction (and its own redundant _ensure_tables()
DDL write) before any of them could publish the singleton. This exercises
the fix (double-checked locking with a dedicated process-local lock) without
needing a real DB-backed BackgroundPriceWorker, whose __init__ has heavy
side effects (DB connections, websocket clients) unrelated to the race
itself.
"""
import threading
import time

import pytest

import src.core.price_worker as price_worker_module


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Every test starts from a clean singleton state and restores it after,
    so this file never leaks state into other tests running in the same
    process/session."""
    original_worker = price_worker_module._price_worker
    original_error = price_worker_module._price_worker_init_error
    price_worker_module._price_worker = None
    price_worker_module._price_worker_init_error = None
    yield
    price_worker_module._price_worker = original_worker
    price_worker_module._price_worker_init_error = original_error


class _FakeWorker:
    """Stand-in for BackgroundPriceWorker: counts constructions and records
    which thread built the instance that actually got returned, without any
    of the real class's DB/network side effects."""
    construction_count = 0
    construction_lock = threading.Lock()

    def __init__(self, db_path):
        with _FakeWorker.construction_lock:
            _FakeWorker.construction_count += 1
        # Simulate real _ensure_tables() taking measurable time -- this is
        # exactly the window where unsynchronized callers used to race.
        time.sleep(0.05)
        self.db_path = db_path
        self.built_by = threading.current_thread().name


def test_concurrent_callers_construct_exactly_once(monkeypatch):
    monkeypatch.setattr(price_worker_module, "BackgroundPriceWorker", _FakeWorker)
    _FakeWorker.construction_count = 0

    results = []
    results_lock = threading.Lock()
    barrier = threading.Barrier(6)

    def call():
        barrier.wait(timeout=2)  # maximize the chance of a real race
        worker = price_worker_module.get_price_worker()
        with results_lock:
            results.append(worker)

    threads = [threading.Thread(target=call, name=f"gthread-{i}") for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert _FakeWorker.construction_count == 1, (
        f"expected exactly one construction, got {_FakeWorker.construction_count} -- "
        "concurrent callers built separate instances"
    )
    assert len(results) == 6
    assert len({id(w) for w in results}) == 1, "not all callers received the same singleton instance"


def test_sequential_calls_reuse_the_same_instance(monkeypatch):
    monkeypatch.setattr(price_worker_module, "BackgroundPriceWorker", _FakeWorker)
    _FakeWorker.construction_count = 0

    first = price_worker_module.get_price_worker()
    second = price_worker_module.get_price_worker()

    assert first is second
    assert _FakeWorker.construction_count == 1


def test_singleton_guard_is_not_the_db_write_lock(monkeypatch):
    """Phase 12 explicit requirement: singleton construction must not be
    guarded by the cross-process/global DB write lane -- a slow or wedged
    unrelated writer holding that lock must not block price-worker
    construction (and vice versa)."""
    from src.utils.db_locking import DB_WRITE_LOCK

    monkeypatch.setattr(price_worker_module, "BackgroundPriceWorker", _FakeWorker)
    _FakeWorker.construction_count = 0

    assert DB_WRITE_LOCK.acquire(timeout=0.1), "test setup: expected DB_WRITE_LOCK to be free"
    try:
        # If get_price_worker() used the global write lock, this call would
        # block for the outer acquire()'s duration since it's held above.
        t0 = time.monotonic()
        worker = price_worker_module.get_price_worker()
        elapsed = time.monotonic() - t0
    finally:
        DB_WRITE_LOCK.release()

    assert worker is not None
    assert elapsed < 1.0, f"get_price_worker() took {elapsed}s while DB_WRITE_LOCK was held -- guards are coupled"


def test_construction_failure_is_cached_and_reraised_not_retried_forever(monkeypatch):
    """A failing __init__ must not leave every subsequent caller silently
    retrying construction (and re-running whatever expensive/faulty setup
    caused the failure) forever -- the failure should be deterministic."""
    attempts = {"count": 0}

    class _FailingWorker:
        def __init__(self, db_path):
            attempts["count"] += 1
            raise RuntimeError("simulated _ensure_tables failure")

    monkeypatch.setattr(price_worker_module, "BackgroundPriceWorker", _FailingWorker)

    with pytest.raises(RuntimeError, match="simulated _ensure_tables failure"):
        price_worker_module.get_price_worker()
    with pytest.raises(RuntimeError, match="simulated _ensure_tables failure"):
        price_worker_module.get_price_worker()

    assert attempts["count"] == 1, "construction should only be attempted once, then cached/reraised"
