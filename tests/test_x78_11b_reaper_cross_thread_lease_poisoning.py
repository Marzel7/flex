"""X78.11b -- cross-thread reaper close() leaves the OWNING thread's
_thread_write_lease.owner permanently poisoned.

Found via a live py-spy dump on a freshly-fixed (X78.11 primary repair)
creator_resolution_worker that STILL repoisoned on its very first cycle,
with outer_command == inner_command == the same _db()-wrapped call site --
meaning the thread-local guard was ALREADY poisoned before the worker's own
first write attempt ran. Root cause: db_locking.py's background
`db-conn-reaper` thread (_reap_stale_connections, default
_MAX_TXN_CONNECTION_AGE_SECS=45s) force-closes long-running in-transaction
connections that belong to OTHER threads -- exactly the shape of
creator_resolution_worker's RPC-bound MainThread work, which routinely
exceeds 45s.

TrackedConnection.close() -> _release_write_lane() -> release_write_lease()
correctly releases the CROSS-PROCESS flock and the in-process _DB_WRITE_LOCK
(a plain threading.Lock, which -- unlike RLock -- Python permits releasing
from a different thread than the one that acquired it) regardless of which
thread calls close(). But release_write_lease()'s OLD thread-local
reentrancy guard clear:

    if getattr(_thread_write_lease, "owner", None) is lease.owner:
        del _thread_write_lease.owner

only ever inspected and cleared the CALLING thread's own threading.local()
slot -- when the reaper called this, it touched (or found nothing in) the
reaper's own always-empty thread-local, never the owning thread's -- so the
owning thread's guard survived the close permanently, self-nesting on every
subsequent write forever.

Fix (this file drives + validates it): a shared, thread-independent
lease-identity registry (_active_lease_by_thread_ident, keyed by thread
ident, storing an opaque per-acquisition token). release_write_lease()
invalidates the OWNING thread's registry entry regardless of which thread
calls it -- this is safe because it's a lock-protected shared dict, not a
write into another thread's local storage. acquire_write_lease()'s
reentrancy check then compares its own thread-local `token` against the
shared registry entry for its own thread ident: if they no longer match
(because some other thread invalidated it), the local reference is stale
-- self-heal by clearing it and proceed with a normal acquisition, instead
of raising a false-positive NestedDatabaseWriteError.

Test design note: the first draft of the primary reproduction (Phase 1
below) used a FRESH threading.Thread for the "does the poisoning survive"
follow-up check and got a false negative -- a brand-new thread has its own
empty threading.local() regardless of what happened to some OTHER thread,
so it can never observe another thread's poisoning. This was caught before
being relied upon by cross-checking the raw thread-local state with plain
print()-based debugging outside pytest, which proved the SAME persistent
thread genuinely was poisoned. The corrected test (below) uses one
persistent worker thread across both the initial acquire and the later
follow-up write, exactly matching creator_resolution_worker's real
MainThread shape (one long-lived OS thread running a `while` loop across
many cycles, not a fresh thread per cycle).
"""
import sqlite3
import threading

import pytest

from src.core.database_write_service import (
    _active_lease_by_thread_ident,
    _thread_write_lease,
    acquire_write_lease,
    release_write_lease,
)


def _make_db(path: str) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE t(value INTEGER)")


@pytest.fixture(autouse=True)
def _clean_thread_lease_state():
    if hasattr(_thread_write_lease, "owner"):
        del _thread_write_lease.owner
    if hasattr(_thread_write_lease, "token"):
        del _thread_write_lease.token
    yield
    if hasattr(_thread_write_lease, "owner"):
        del _thread_write_lease.owner
    if hasattr(_thread_write_lease, "token"):
        del _thread_write_lease.token


# ── Phase 1: deterministic two-thread reproduction + fix validation ────────

def test_same_thread_self_heals_after_cross_thread_release(tmp_path):
    """The primary reproduction AND fix validation, in one deterministic
    scenario:

    Thread A (simulating creator_resolution_worker's persistent MainThread)
    acquires a lease and does NOT release it itself. Thread B (simulating
    the db-conn-reaper) then calls release_write_lease() on that SAME lease
    object -- exactly what the reaper does when it force-closes a
    long-running connection belonging to another thread. Thread A -- the
    SAME OS thread throughout, matching the real worker's persistent
    while-loop shape -- then attempts another acquisition.

    Expected (post-fix): Thread A's next acquisition SUCCEEDS (self-heals),
    not a NestedDatabaseWriteError. This is the actual production
    acceptance criterion: creator_resolution_worker's MainThread must
    survive the reaper reclaiming one of its long-running connections and
    continue making progress on its next cycle.
    """
    db_path = str(tmp_path / "flex.db")
    _make_db(db_path)

    thread_a_lease = {}
    result = {}
    barrier = threading.Barrier(2)

    def thread_a_worker():
        lease = acquire_write_lease("tracked:test", db_path, "txn-a", "thread-a-longrunning-write")
        thread_a_lease["lease"] = lease
        thread_a_lease["owner_after_acquire"] = getattr(_thread_write_lease, "owner", None)
        barrier.wait(timeout=5)  # let thread B (reaper) release while A "holds" it
        # Thread A does NOT release its own lease -- the reaper (thread B)
        # does it instead, exactly as in production.
        barrier.wait(timeout=5)  # wait for thread B to finish releasing

        # THE actual production acceptance criterion: this SAME thread's
        # next acquisition, made later in the same persistent worker loop,
        # must succeed -- not self-collide.
        try:
            lease2 = acquire_write_lease("tracked:test", db_path, "txn-a-2", "thread-a-next-write")
            result["outcome"] = "acquired"
            release_write_lease(lease2)  # clean up so the test doesn't leak state
        except Exception as exc:
            result["outcome"] = "raised"
            result["exc_type"] = type(exc).__name__

    def thread_b_reaper_release():
        barrier.wait(timeout=5)
        release_write_lease(thread_a_lease["lease"])
        barrier.wait(timeout=5)

    t_a = threading.Thread(target=thread_a_worker, name="MainThread-sim")
    t_b = threading.Thread(target=thread_b_reaper_release, name="db-conn-reaper-sim")
    t_a.start()
    t_b.start()
    t_a.join(timeout=10)
    t_b.join(timeout=10)

    assert thread_a_lease["owner_after_acquire"] is not None, (
        "thread A's acquisition should have set its own local owner"
    )
    assert result.get("outcome") == "acquired", (
        f"thread A's next acquisition after a cross-thread release must self-heal "
        f"and succeed, not self-collide -- got outcome={result.get('outcome')!r} "
        f"exc_type={result.get('exc_type')!r}"
    )


def test_shared_registry_entry_invalidated_by_cross_thread_release(tmp_path):
    """Direct check on the shared registry mechanism itself: after a
    cross-thread release, the OWNING thread's entry in
    _active_lease_by_thread_ident must be gone (or point to a different,
    newer token) -- proving invalidation actually happened, not merely that
    the end-to-end behavior happens to work out."""
    db_path = str(tmp_path / "flex.db")
    _make_db(db_path)

    thread_a_lease = {}
    thread_a_ident = {}
    barrier = threading.Barrier(2)

    def thread_a_worker():
        thread_a_ident["ident"] = threading.get_ident()
        lease = acquire_write_lease("tracked:test", db_path, "txn-a", "thread-a-work")
        thread_a_lease["lease"] = lease
        barrier.wait(timeout=5)
        barrier.wait(timeout=5)

    def thread_b_reaper_release():
        barrier.wait(timeout=5)
        release_write_lease(thread_a_lease["lease"])
        barrier.wait(timeout=5)

    t_a = threading.Thread(target=thread_a_worker, name="MainThread-sim")
    t_b = threading.Thread(target=thread_b_reaper_release, name="db-conn-reaper-sim")
    t_a.start()
    t_b.start()
    t_a.join(timeout=10)
    t_b.join(timeout=10)

    ident = thread_a_ident["ident"]
    assert _active_lease_by_thread_ident.get(ident) is None, (
        "the owning thread's registry entry must be invalidated by a "
        "cross-thread release, regardless of which thread performed it"
    )


def test_normal_same_thread_release_still_works(tmp_path):
    """Regression: the common case (a thread acquires and releases its OWN
    lease, no reaper involved) must be completely unaffected by this fix."""
    db_path = str(tmp_path / "flex.db")
    _make_db(db_path)

    lease = acquire_write_lease("tracked:test", db_path, "txn-normal", "normal-same-thread")
    assert getattr(_thread_write_lease, "owner", None) is not None
    release_write_lease(lease)
    assert getattr(_thread_write_lease, "owner", None) is None
    assert getattr(_thread_write_lease, "token", None) is None

    # A subsequent acquisition on the same thread must succeed normally.
    lease2 = acquire_write_lease("tracked:test", db_path, "txn-normal-2", "normal-same-thread-2")
    release_write_lease(lease2)


def test_genuine_same_thread_nesting_is_still_rejected(tmp_path):
    """Regression: the self-heal check must NOT weaken genuine nested-write
    detection. If a thread tries to acquire a SECOND lease while its FIRST
    one is still genuinely active (not released by anyone), this must still
    raise NestedDatabaseWriteError -- the whole point of X78.0-era
    reentrancy protection."""
    db_path = str(tmp_path / "flex.db")
    _make_db(db_path)

    lease = acquire_write_lease("tracked:test", db_path, "txn-outer", "outer-write")
    try:
        from src.core.database_write_service import NestedDatabaseWriteError
        with pytest.raises(NestedDatabaseWriteError):
            acquire_write_lease("tracked:test", db_path, "txn-inner", "inner-write")
    finally:
        release_write_lease(lease)
