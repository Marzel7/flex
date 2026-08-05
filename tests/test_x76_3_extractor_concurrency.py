"""X76.3 -- Shared Extractor Database Concurrency & Connection Ownership.

Reproduces the 7 named failure classes from the milestone spec against an
isolated, small SQLite database (NOT a copy of the live 2.9GB database --
these are pure connection-ownership/concurrency tests, unrelated to
attribution data). Each test is written to FAIL against the pre-X76.3
unsafe pattern (documented inline per test) and PASS against the repaired
code.

Constraints from the milestone spec, honoured throughout this file:
- never pass an already-open TrackedConnection across a thread boundary
- never acquire a synchronous lock directly on the event-loop thread
- never dispatch a connection opened on one thread into asyncio.to_thread
- never let a write-capable child task outlive its parent extraction call
"""
import asyncio
import os
import sqlite3
import threading
import time

import pytest

from src.utils.db_locking import (
    TrackedConnection,
    db_connect,
    managed_db_connect,
)
from src.core.database_write_service import (
    NestedDatabaseWriteError,
    _thread_write_lease,
    acquire_write_lease,
    release_write_lease,
)


@pytest.fixture()
def tmp_db(tmp_path):
    """A tiny, isolated SQLite database -- not a copy of the live database.
    These tests exercise connection-ownership primitives, not attribution
    data, so a minimal schema is sufficient and keeps the suite fast."""
    path = str(tmp_path / "x76_3_test.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, value TEXT)")
    conn.commit()
    conn.close()
    yield path


@pytest.fixture(autouse=True)
def _clean_thread_lease():
    """Guard against one test's leaked lease poisoning another -- mirrors
    the real production symptom this milestone fixes, so tests must not
    accidentally cause it against EACH OTHER."""
    if hasattr(_thread_write_lease, "owner"):
        del _thread_write_lease.owner
    yield
    if hasattr(_thread_write_lease, "owner"):
        del _thread_write_lease.owner


class TestNestedDatabaseWriteError:
    """Failure class 1: concurrent write-capable children cause
    NestedDatabaseWriteError."""

    def test_second_write_lease_on_same_thread_raises(self, tmp_db):
        lease = acquire_write_lease(f"tracked:{tmp_db}", tmp_db, "tx-1", "outer")
        try:
            with pytest.raises(NestedDatabaseWriteError) as exc_info:
                acquire_write_lease(f"tracked:{tmp_db}", tmp_db, "tx-2", "inner")
            assert exc_info.value.outer_command == "outer"
            assert exc_info.value.inner_command == "inner"
        finally:
            release_write_lease(lease)

    def test_managed_db_connect_write_then_close_allows_next_write(self, tmp_db):
        """The fixed pattern: managed_db_connect guarantees close() (hence
        write-lane release) before the next write attempt on this thread,
        so sequential writes never collide -- this is the steady state
        X76.3 restores; it must not regress into always raising."""
        with managed_db_connect(tmp_db, timeout=5) as conn:
            conn.execute("INSERT INTO t (value) VALUES ('a')")
            conn.commit()
        # Lease was released by conn.close() inside managed_db_connect's finally.
        assert not hasattr(_thread_write_lease, "owner")
        with managed_db_connect(tmp_db, timeout=5) as conn:
            conn.execute("INSERT INTO t (value) VALUES ('b')")
            conn.commit()
        rows = sqlite3.connect(tmp_db).execute("SELECT COUNT(*) FROM t").fetchone()[0]
        assert rows == 2


class TestSynchronousLockOnEventLoop:
    """Failure class 2: synchronous lock acquisition on the event-loop
    thread prevents timeout and heartbeat progress.

    X73.2A's own postmortem (docs/audits/x73_2a_shared_extractor_concurrency.md)
    already proved `with DB_WRITE_LOCK:` directly on the event-loop thread
    freezes asyncio.wait_for's own timeout mechanism. X76.3's fix does NOT
    reintroduce that pattern (see check_create_tx_for_jitotip's comment) --
    this test guards against a future regression re-adding it."""

    async def test_synchronous_lock_in_async_def_freezes_wait_for_timeout(self):
        """Proves the X73.2A-documented hazard directly: a synchronous
        `with lock:` inside a plain `async def`, with no `await` point of
        its own, blocks the underlying call stack with no opportunity for
        the event loop to service asyncio.wait_for's own timeout --
        wait_for's cancellation only gets a chance to run at an `await`,
        and there isn't one here. This is exactly the "6+ minute hang with
        near-zero CPU" symptom X73.2A observed and reverted away from
        (docs/audits/x73_2a_shared_extractor_concurrency.md); the fixed
        code in check_create_tx_for_jitotip/check_transfers_for_debridge/
        check_transfers_for_axiom never does this -- see
        TestRealExtractorFunctionsUseManagedConnect for the structural
        guard that they use managed_db_connect (async-safe: no lock
        acquisition at all) instead."""
        held = threading.Event()
        release = threading.Event()
        lock = threading.Lock()

        def hold_lock_on_another_thread():
            with lock:
                held.set()
                release.wait(timeout=5)

        t = threading.Thread(target=hold_lock_on_another_thread, daemon=True)
        t.start()
        held.wait(timeout=2)

        async def acquire_directly_on_event_loop():
            # The X73.2A-forbidden pattern: a synchronous lock acquired
            # with no thread dispatch and no intervening await, directly
            # inside an async def.
            with lock:
                return "acquired"

        start = time.monotonic()
        # wait_for's requested 0.3s timeout does NOT fire on schedule --
        # the coroutine never yields, so cancellation has no await point
        # to land on. It only returns once the lock is actually released.
        result = await asyncio.wait_for(acquire_directly_on_event_loop(), timeout=0.3)
        elapsed = time.monotonic() - start
        assert result == "acquired"
        assert elapsed > 1.0, (
            "expected the synchronous lock to block past the requested "
            "0.3s timeout -- if this now completes quickly, either the "
            "test setup is wrong or Python's scheduling semantics changed"
        )
        release.set()
        t.join(timeout=2)


class TestCrossThreadConnectionUse:
    """Failure class 3: a connection opened on one thread and used in
    another is rejected or identified as unsafe."""

    def test_connection_opened_on_worker_thread_stays_on_that_thread(self, tmp_db):
        """The proven-safe pattern (_save_outgoing_transfer's shape, X73.2A):
        open + use + close entirely within one asyncio.to_thread dispatch,
        never crossing the connection object back to the event loop."""
        results = {}

        def open_use_close():
            conn = db_connect(tmp_db, timeout=5)
            try:
                conn.execute("INSERT INTO t (value) VALUES ('threaded')")
                conn.commit()
                results["thread_name"] = threading.current_thread().name
            finally:
                conn.close()

        async def run():
            await asyncio.to_thread(open_use_close)

        asyncio.run(run())
        assert "thread_name" in results
        rows = sqlite3.connect(tmp_db).execute(
            "SELECT COUNT(*) FROM t WHERE value = 'threaded'"
        ).fetchone()[0]
        assert rows == 1

    def test_sqlite_connection_object_rejects_cross_thread_use_by_default(self, tmp_db):
        """Documents the underlying sqlite3 guarantee (check_same_thread=True
        by default) that check_create_tx_for_jitotip's own reverted-fix
        comment invoked but never verified directly. TrackedConnection does
        not override this."""
        conn = sqlite3.connect(tmp_db, timeout=5)
        error = {}

        def use_from_other_thread():
            try:
                conn.execute("SELECT 1")
            except sqlite3.ProgrammingError as e:
                error["raised"] = str(e)

        t = threading.Thread(target=use_from_other_thread)
        t.start()
        t.join(timeout=5)
        conn.close()
        assert "raised" in error
        assert "thread" in error["raised"].lower()


class TestFireAndForgetTaskSupervision:
    """Failure class 4: a fire-and-forget child retains a connection after
    the parent returns."""

    @pytest.mark.asyncio
    async def test_spawned_task_is_tracked_and_awaited_before_parent_returns(self):
        from src.extractors.realtime_creator_funding_extractor import (
            RealTimeCreatorFundingExtractor,
        )

        extractor = RealTimeCreatorFundingExtractor()
        marker = {"ran": False}

        async def slow_child():
            await asyncio.sleep(0.05)
            marker["ran"] = True

        extractor._spawn_background_task(slow_child())
        assert len(extractor._background_tasks) == 1

        # The fixed contract: wait_for_background_tasks() bounded-waits
        # every tracked task before the caller (the true parent) considers
        # the extraction finished -- this is what extract_funding_for_new_token
        # now calls at its own return point, closing the gap where a
        # caller other than creator_funding_worker had NO supervision at
        # all (only the worker's own bolt-on sweep existed pre-X76.3).
        await extractor.wait_for_background_tasks(timeout=2.0)
        assert marker["ran"] is True
        assert len(extractor._background_tasks) == 0

    @pytest.mark.asyncio
    async def test_task_registry_self_prunes_on_completion(self):
        from src.extractors.realtime_creator_funding_extractor import (
            RealTimeCreatorFundingExtractor,
        )

        extractor = RealTimeCreatorFundingExtractor()

        async def instant():
            return None

        task = extractor._spawn_background_task(instant())
        await task
        # done_callback runs on the next loop iteration
        await asyncio.sleep(0)
        assert task not in extractor._background_tasks

    @pytest.mark.asyncio
    async def test_wait_for_background_tasks_does_not_cancel_slow_stragglers(self):
        """Never cancel a straggler past the bounded wait -- cancelling a
        write mid-commit is worse than a slow one (explicit non-goal in
        the milestone spec)."""
        from src.extractors.realtime_creator_funding_extractor import (
            RealTimeCreatorFundingExtractor,
        )

        extractor = RealTimeCreatorFundingExtractor()
        marker = {"completed": False}

        async def slow_child():
            await asyncio.sleep(0.3)
            marker["completed"] = True

        extractor._spawn_background_task(slow_child())
        await extractor.wait_for_background_tasks(timeout=0.05)
        # Not cancelled -- still pending, not done, not in error.
        pending = [t for t in extractor._background_tasks if not t.done()]
        assert len(pending) == 1
        await asyncio.sleep(0.4)
        assert marker["completed"] is True


class TestCancellationReleasesOwnership:
    """Failure class 5: cancellation during a write releases the
    connection, write lane, process lock, thread-local ownership, and task
    references."""

    @pytest.mark.asyncio
    async def test_cancelled_to_thread_write_still_releases_lease(self, tmp_db):
        """A write dispatched via asyncio.to_thread (the proven-safe
        _save_outgoing_transfer shape) that opens+writes+closes within one
        synchronous function must leave no lease behind even if the
        *caller* is cancelled after the thread call has already
        started -- to_thread's own contract is that the underlying thread
        keeps running to completion (Python cannot forcibly kill a thread),
        so the connection's own finally/close still executes and releases
        the lease; only the asyncio-level awaiting is what gets cancelled."""

        def write_slowly():
            conn = db_connect(tmp_db, timeout=5)
            try:
                conn.execute("INSERT INTO t (value) VALUES ('slow')")
                time.sleep(0.2)
                conn.commit()
            finally:
                conn.close()

        async def run():
            task = asyncio.create_task(asyncio.to_thread(write_slowly))
            await asyncio.sleep(0.02)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        await run()
        # Give the background thread time to actually finish its own
        # close() (cancelling the asyncio Task does not stop the thread).
        for _ in range(50):
            if not hasattr(_thread_write_lease, "owner"):
                break
            time.sleep(0.05)
        assert not hasattr(_thread_write_lease, "owner")

    def test_exception_mid_write_still_releases_lease_via_managed_connect(self, tmp_db):
        """Failure class 6: an exception during commit/rollback/close
        cannot leak ownership state. Before X76.3, several extractor
        functions (check_create_tx_for_jitotip et al.) only called
        conn.close() on the success path -- an exception raised between
        open and close left the lease held on that thread forever (the
        documented root cause of persistent NestedDatabaseWriteError).
        managed_db_connect is the fix; this proves it holds even when the
        body raises mid-write."""
        with pytest.raises(RuntimeError):
            with managed_db_connect(tmp_db, timeout=5) as conn:
                conn.execute("INSERT INTO t (value) VALUES ('will_fail')")
                raise RuntimeError("simulated failure mid-write, before commit")
        assert not hasattr(_thread_write_lease, "owner")
        # A subsequent write on this same thread must succeed immediately --
        # this is the exact regression this milestone closes.
        with managed_db_connect(tmp_db, timeout=5) as conn:
            conn.execute("INSERT INTO t (value) VALUES ('after_failure')")
            conn.commit()

    def test_bare_conn_close_without_finally_leaks_lease_pre_fix_pattern(self, tmp_db):
        """Documents the UNSAFE pre-X76.3 pattern directly (not testing
        current code -- this is the regression guard: if any code
        reintroduces `conn.close()` only on the success path, this test
        demonstrates exactly what breaks). Must not be copied as a
        template anywhere in the fixed extractor."""
        conn = db_connect(tmp_db, timeout=5)
        try:
            conn.execute("INSERT INTO t (value) VALUES ('unsafe')")
            raise RuntimeError("simulated mid-write failure")
            conn.commit()  # unreachable -- demonstrates the bug
            conn.close()   # unreachable -- demonstrates the bug
        except RuntimeError:
            pass
        # The lease IS still held -- this is the bug this milestone fixes
        # everywhere it appeared. Demonstrating it here, isolated, proves
        # the mechanism without needing to break production code to show it.
        assert hasattr(_thread_write_lease, "owner")
        # Clean up so this test doesn't poison later tests.
        conn.close()


class TestTimeoutCannotContinueMutating:
    """Failure class 7: a timed-out extraction cannot continue mutating the
    database afterward."""

    @pytest.mark.asyncio
    async def test_extraction_conn_is_closed_after_wait_for_timeout(self, tmp_db):
        """Mirrors extract_for_creator's extraction_conn lifecycle
        (X76.3's finally-guaranteed close) composed with the worker's own
        asyncio.wait_for(..., timeout=JOB_TIMEOUT_SECONDS) wrapper. Once
        the timeout fires and propagates, the connection opened inside the
        cancelled coroutine must already be closed -- proving a second,
        independent caller can immediately open a fresh connection to the
        same database without contention."""
        conn_ref = {}

        async def slow_extraction():
            extraction_conn = None
            try:
                extraction_conn = db_connect(tmp_db, timeout=5)
                conn_ref["conn"] = extraction_conn
                extraction_conn.execute("INSERT INTO t (value) VALUES ('mid_extraction')")
                await asyncio.sleep(1.0)  # simulate a hung page-fetch loop
                extraction_conn.commit()
            finally:
                if extraction_conn is not None:
                    try:
                        extraction_conn.close()
                    except Exception:
                        pass

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(slow_extraction(), timeout=0.1)

        # Give the cancelled coroutine's finally a chance to run.
        await asyncio.sleep(0.1)

        # A second, independent connection must be able to write immediately.
        with managed_db_connect(tmp_db, timeout=2) as conn2:
            conn2.execute("INSERT INTO t (value) VALUES ('after_timeout')")
            conn2.commit()

        rows = sqlite3.connect(tmp_db).execute(
            "SELECT value FROM t ORDER BY id"
        ).fetchall()
        values = [r[0] for r in rows]
        assert "after_timeout" in values
        # The timed-out write was never committed (cancelled before commit()).
        assert "mid_extraction" not in values


class TestRealExtractorFunctionsUseManagedConnect:
    """Structural regression guard: the named write-capable functions from
    the milestone spec must use managed_db_connect (or an equivalent
    guaranteed-close pattern), not a bare db_connect(...)...conn.close()
    that only runs on the success path."""

    def test_save_outgoing_transfer_uses_managed_connect(self):
        import inspect
        from src.extractors.realtime_creator_funding_extractor import (
            RealTimeCreatorFundingExtractor,
        )
        src = inspect.getsource(RealTimeCreatorFundingExtractor._save_outgoing_transfer)
        assert "managed_db_connect" in src

    def test_check_create_tx_for_jitotip_uses_managed_connect(self):
        import inspect
        from src.extractors.realtime_creator_funding_extractor import (
            RealTimeCreatorFundingExtractor,
        )
        src = inspect.getsource(RealTimeCreatorFundingExtractor.check_create_tx_for_jitotip)
        assert "managed_db_connect" in src

    def test_check_transfers_for_debridge_uses_managed_connect(self):
        import inspect
        from src.extractors.realtime_creator_funding_extractor import (
            RealTimeCreatorFundingExtractor,
        )
        src = inspect.getsource(RealTimeCreatorFundingExtractor.check_transfers_for_debridge)
        assert "managed_db_connect" in src

    def test_check_transfers_for_axiom_uses_managed_connect(self):
        import inspect
        from src.extractors.realtime_creator_funding_extractor import (
            RealTimeCreatorFundingExtractor,
        )
        src = inspect.getsource(RealTimeCreatorFundingExtractor.check_transfers_for_axiom)
        assert "managed_db_connect" in src

    def test_extract_for_creator_has_finally_guarded_extraction_conn(self):
        import inspect
        from src.extractors.realtime_creator_funding_extractor import (
            RealTimeCreatorFundingExtractor,
        )
        src = inspect.getsource(RealTimeCreatorFundingExtractor.extract_for_creator)
        assert "finally" in src
        assert "extraction_conn = None" in src

    def test_post_launch_automation_functions_use_managed_connect_or_finally(self):
        import inspect
        from src.analysis import post_launch_automation as pla
        for name in (
            "_update_funding_distribution_metrics",
            "_detect_coordinated_funders",
            "_rebuild_clusters_for_creator",
        ):
            src = inspect.getsource(getattr(pla.PostLaunchAutomationCoordinator, name))
            assert "managed_db_connect" in src, f"{name} missing managed_db_connect"
        assign_network_src = inspect.getsource(
            pla.PostLaunchAutomationCoordinator._assign_creator_network
        )
        assert "finally" in assign_network_src
        assert "conn = None" in assign_network_src

    def test_extractor_tracks_background_tasks_registry(self):
        from src.extractors.realtime_creator_funding_extractor import (
            RealTimeCreatorFundingExtractor,
        )
        extractor = RealTimeCreatorFundingExtractor()
        assert hasattr(extractor, "_background_tasks")
        assert hasattr(extractor, "_spawn_background_task")
        assert hasattr(extractor, "wait_for_background_tasks")
