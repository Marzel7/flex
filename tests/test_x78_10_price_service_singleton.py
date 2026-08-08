"""X78.10 -- get_price_service() singleton construction race.

Found live during X78.10 production validation (immediately after deploying
X78.9): the /api/price/health endpoint calls both get_price_worker() (fixed
in X78.9 Phase 12) and get_price_service() -- the latter had the identical
unsynchronized `if _x is None` race, just in a sibling module that wasn't
audited during X78.9. Confirmed via production logs: repeated
CrossProcessDatabaseWriteTimeout on price_service.py:339 waiting on itself
(same PID, different ThreadPoolExecutor threads) roughly every 60s under
concurrent health-endpoint traffic. X78.9's bounded timeout correctly kept
each individual wait from hanging forever, but the underlying race was still
producing them on a ~60s cadence. This mirrors test_x78_9_price_worker_singleton.py's
approach: a lightweight stand-in class rather than the real TokenPriceService,
whose __init__ has heavy DB/thread-pool side effects unrelated to the race
itself.
"""
import threading
import time

import pytest

import src.core.price_service as price_service_module


@pytest.fixture(autouse=True)
def _reset_singleton():
    original_service = price_service_module._price_service
    original_error = price_service_module._price_service_init_error
    price_service_module._price_service = None
    price_service_module._price_service_init_error = None
    yield
    price_service_module._price_service = original_service
    price_service_module._price_service_init_error = original_error


class _FakeService:
    construction_count = 0
    construction_lock = threading.Lock()

    def __init__(self, db_path):
        with _FakeService.construction_lock:
            _FakeService.construction_count += 1
        # Simulate real _ensure_tables() + _load_circuit_breaker_state()
        # taking measurable time -- the window where unsynchronized callers
        # raced live.
        time.sleep(0.05)
        self.db_path = db_path


def test_concurrent_callers_construct_exactly_once(monkeypatch):
    monkeypatch.setattr(price_service_module, "TokenPriceService", _FakeService)
    _FakeService.construction_count = 0

    results = []
    results_lock = threading.Lock()
    barrier = threading.Barrier(6)

    def call():
        barrier.wait(timeout=2)
        service = price_service_module.get_price_service()
        with results_lock:
            results.append(service)

    threads = [threading.Thread(target=call, name=f"gthread-{i}") for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert _FakeService.construction_count == 1, (
        f"expected exactly one construction, got {_FakeService.construction_count}"
    )
    assert len(results) == 6
    assert len({id(s) for s in results}) == 1


def test_sequential_calls_reuse_the_same_instance(monkeypatch):
    monkeypatch.setattr(price_service_module, "TokenPriceService", _FakeService)
    _FakeService.construction_count = 0

    first = price_service_module.get_price_service()
    second = price_service_module.get_price_service()

    assert first is second
    assert _FakeService.construction_count == 1


def test_singleton_guard_is_not_the_db_write_lock(monkeypatch):
    from src.utils.db_locking import DB_WRITE_LOCK

    monkeypatch.setattr(price_service_module, "TokenPriceService", _FakeService)
    _FakeService.construction_count = 0

    assert DB_WRITE_LOCK.acquire(timeout=0.1), "test setup: expected DB_WRITE_LOCK to be free"
    try:
        t0 = time.monotonic()
        service = price_service_module.get_price_service()
        elapsed = time.monotonic() - t0
    finally:
        DB_WRITE_LOCK.release()

    assert service is not None
    assert elapsed < 1.0, f"get_price_service() took {elapsed}s while DB_WRITE_LOCK was held"


def test_construction_failure_is_cached_and_reraised_not_retried_forever(monkeypatch):
    attempts = {"count": 0}

    class _FailingService:
        def __init__(self, db_path):
            attempts["count"] += 1
            raise RuntimeError("simulated _ensure_tables failure")

    monkeypatch.setattr(price_service_module, "TokenPriceService", _FailingService)

    with pytest.raises(RuntimeError, match="simulated _ensure_tables failure"):
        price_service_module.get_price_service()
    with pytest.raises(RuntimeError, match="simulated _ensure_tables failure"):
        price_service_module.get_price_service()

    assert attempts["count"] == 1
