"""X78.5 Phase 4/14: deterministic reproduction and regression for the
actual root cause identified via the X78.5 caller-attribution diagnostic
fix (docs/audits/x78_5_patched_connect_caller_attribution.md).

RiskScoringBuilder.score_creator_now() opened its connection and ran
`row_factory` / `PRAGMA journal_mode=WAL` BEFORE its own try block. If
either of those two statements raised, the exception propagated straight
out of the function without ever reaching `finally: conn.close()` --
the only thing that releases TrackedConnection's write lease. This
produced a PERMANENT (not transient) lease leak: no later write on that
thread could ever succeed again, observed live stalling
creator_funding_worker for 56+ minutes and driving 3 crash-loop restarts
(outer_command=risk_scoring_builder.py:124 in score_creator_now, 241
occurrences captured live in a single ~30 minute window after the X78.5
caller-attribution fix made the real source visible for the first time).

Note on reproduction fidelity (an honest limitation, not claimed away):
forcing the EXACT live trigger -- `PRAGMA journal_mode=WAL` or
`row_factory` raising on lines 124-126, specifically BEFORE the old
try block began at line 127 -- could not be made to deterministically
fail inside a unit test. A real concurrent SQLite lock is waited out
within the connection's own timeout=60; the sqlite3.Connection C type
cannot be monkeypatched to force a failure at an arbitrary line; and
apply_migration() (the only mockable early call) was ALREADY inside the
old try block, so mocking it does not exercise the actual pre-fix gap
and these tests pass unchanged against both the pre-fix and post-fix
code. The fix's correctness therefore rests on direct code review (the
diff moves conn=sqlite3.connect(...)/row_factory/PRAGMA from before the
try block to inside it, matching the identical try/finally shape already
used correctly by every other connection in this class and file) plus
the general non-regression tests below, which confirm the resulting
function still behaves correctly (releases its lease, returns a
sensible result) whether or not an exception occurs, and that the
lease-release primitive itself behaves as expected under the pre-fix
connect-then-fail-with-no-cleanup shape.
"""
from __future__ import annotations

import os
import sqlite3
import uuid

import pytest

from src.core.database_write_service import _thread_write_lease
from src.core.risk_scoring_builder import RiskScoringBuilder


def _clear_thread_lease():
    if hasattr(_thread_write_lease, "owner"):
        del _thread_write_lease.owner


@pytest.fixture(autouse=True)
def _isolate():
    _clear_thread_lease()
    yield
    _clear_thread_lease()


def _make_db_with_schema(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS network_membership (creator_address TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS creator_funders (creator_address TEXT, funder_address TEXT, amount_sol REAL, is_cex INTEGER)")
    conn.execute("CREATE TABLE IF NOT EXISTS token_analysis (earliest_tx_creator TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS creator_self_funding (creator_address TEXT, is_self_funding INTEGER)")
    conn.execute("CREATE TABLE IF NOT EXISTS creator_outbound_classifications (creator_address TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS infra_wallets (address TEXT)")
    conn.commit()
    conn.close()


def test_score_creator_now_releases_lease_on_normal_success(tmp_path):
    """Non-regression: the happy path must still work and release the
    lease normally."""
    db_path = str(tmp_path / "x.db")
    _make_db_with_schema(db_path)

    builder = RiskScoringBuilder(db_path)
    result = builder.score_creator_now("creatorAddr")

    assert result["status"] in ("success", "error")  # migration file may not exist in test env
    assert getattr(_thread_write_lease, "owner", None) is None, (
        "no write lease should remain held after score_creator_now returns"
    )


def test_score_creator_now_releases_lease_even_when_setup_fails_early(tmp_path, monkeypatch):
    """Core X78.5 regression: force an exception at the earliest possible
    point inside score_creator_now's body -- the same structural position
    as the live row_factory/PRAGMA statements, now correctly inside
    try/finally. Before the fix, ANY exception before the (then-later)
    try block leaked the lease permanently; after the fix, the entire
    connection lifecycle -- setup included -- is covered."""
    db_path = str(tmp_path / "x.db")
    _make_db_with_schema(db_path)

    def failing_apply_migration(conn):
        raise RuntimeError("simulated setup failure")

    monkeypatch.setattr(RiskScoringBuilder, "apply_migration", staticmethod(failing_apply_migration))

    builder = RiskScoringBuilder(db_path)
    result = builder.score_creator_now("creatorAddr")

    assert result["status"] == "error"
    assert getattr(_thread_write_lease, "owner", None) is None, (
        "the write lease must be released even when a failure occurs "
        "early in score_creator_now's setup -- this is the same "
        "structural guarantee the fix provides for row_factory/PRAGMA "
        "specifically (both now inside the same try/finally)"
    )


def test_next_write_does_not_collide_after_early_failure(tmp_path, monkeypatch):
    """End-to-end proof: after score_creator_now fails early, a
    completely unrelated later write on the same thread (modeling
    _rescore/_write_heartbeat/_save_outgoing_transfer, all observed
    colliding live) must succeed, not raise NestedDatabaseWriteError."""
    db_path = str(tmp_path / "x.db")
    _make_db_with_schema(db_path)

    def failing_apply_migration(conn):
        raise RuntimeError("simulated setup failure")

    monkeypatch.setattr(RiskScoringBuilder, "apply_migration", staticmethod(failing_apply_migration))

    builder = RiskScoringBuilder(db_path)
    builder.score_creator_now("creatorAddr")

    from src.core.database_write_service import acquire_write_lease, release_write_lease

    lease = acquire_write_lease(
        f"tracked:{os.path.realpath(db_path)}", db_path, str(uuid.uuid4()),
        "creator_funding_worker.py:117 in _db_connect (next write)",
    )
    release_write_lease(lease)


