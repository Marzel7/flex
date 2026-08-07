"""X78.3 Phase 1: deterministic reproduction of the RPCCache nested-write
collision found live during X78.2's sanity window.

Root cause (proven via source inspection + live-signature match, see
docs/audits/x78_3_creator_funding_rpc_cache_nested_write_repair.md):

RealTimeCreatorFundingExtractor._flush_page_batch()'s transfer_index
insert block (realtime_creator_funding_extractor.py, inside the
`if transfer_index_rows:` guard) wraps `cursor.executemany(...)` +
`conn.commit()` in its own inner try/except. cursor.executemany() on a
malformed row raises AFTER acquiring extraction_conn's write lease
(executemany is write-shaped SQL, so _acquire_write_lane() has already
fired) but BEFORE conn.commit() releases it. The inner except caught and
logged the failure WITHOUT rolling back or re-raising -- unlike the
function's OUTER except (X78.0's fix, still correct), which does roll
back. That left extraction_conn's write lease held for the rest of the
extraction (and beyond, on that thread), so the next write ANYWHERE on
the same thread -- RPCCache._get_conn() (the pattern actually observed
live), a later page's own extraction_conn write (the rarer
extract_for_creator -> extract_for_creator self-collision X78.2 also
observed), or any other db_locking-monkeypatched sqlite3.connect() caller
-- raised NestedDatabaseWriteError.

This reproduces the exact live signature:
  outer_command=realtime_creator_funding_extractor.py:<flush_page_batch's
    caller line> in extract_for_creator
  inner_command=rpc_cache.py:68 in _get_conn

using the REAL _flush_page_batch method and REAL RPCCache/db_connect
primitives -- no mocks of TrackedConnection or the write-lease guard.
"""
from __future__ import annotations

import os
import sys

import pytest

from src.core.database_write_service import NestedDatabaseWriteError, _thread_write_lease
from src.utils.db_locking import db_connect
from src.extractors.realtime_creator_funding_extractor import RealTimeCreatorFundingExtractor
from src.core.rpc_cache import RPCCache


def _clear_thread_lease():
    if hasattr(_thread_write_lease, "owner"):
        del _thread_write_lease.owner


@pytest.fixture(autouse=True)
def _isolate():
    _clear_thread_lease()
    yield
    _clear_thread_lease()


def _make_extraction_conn(db_path: str):
    conn = db_connect(db_path, timeout=30)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS creator_funders (
            creator_address TEXT, funder_address TEXT, amount_sol REAL,
            first_detected_at TEXT, is_cex INTEGER, cex_exchange TEXT,
            cex_type TEXT, is_classified INTEGER, fully_analyzed INTEGER,
            PRIMARY KEY (creator_address, funder_address)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS creator_receivers (
            creator_address TEXT, receiver_address TEXT, amount_sol REAL,
            receiver_type TEXT, receiver_name TEXT, first_detected_at TEXT,
            PRIMARY KEY (creator_address, receiver_address)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS creator_service_history (
            creator_address TEXT, tag TEXT, amount_sol REAL, tx_signature TEXT,
            mint TEXT, network_fee_sol REAL, tip_percentage REAL, tx_type TEXT,
            created_at TEXT, PRIMARY KEY (creator_address, tx_signature, tag)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transfer_index (
            signature TEXT, source TEXT, destination TEXT, amount_lamports INTEGER,
            slot INTEGER, block_time INTEGER, indexed_at REAL, is_valid INTEGER,
            transfer_type TEXT
        )
    """)
    conn.execute("CREATE TABLE IF NOT EXISTS cex_wallets (cex_address TEXT, exchange_name TEXT, wallet_type TEXT, is_active INTEGER)")
    conn.commit()
    return conn


@pytest.mark.asyncio
async def test_malformed_transfer_index_row_leaks_lease_pre_fix_behaviour_documented(tmp_path):
    """Documents the PRE-FIX failure mode directly against the real
    _flush_page_batch, by feeding it a malformed transfer_index row (wrong
    tuple arity) so cursor.executemany() raises after acquiring the lease.
    This test asserts the CURRENT (fixed) behaviour: the lease is released
    via rollback, not leaked."""
    db_path = str(tmp_path / "extraction.db")
    conn = _make_extraction_conn(db_path)

    extractor = RealTimeCreatorFundingExtractor.__new__(RealTimeCreatorFundingExtractor)
    extractor.domain_resolver = None

    malformed_transfer_index_rows = [("sig1", "a", "b", 100)]  # wrong arity (4 not 6)

    await extractor._flush_page_batch(
        conn,
        creator="creatorAddr",
        funders_delta={},
        recipients_delta={},
        domain_addrs=set(),
        jito_events=[],
        transfer_index_rows=malformed_transfer_index_rows,
    )

    assert getattr(_thread_write_lease, "owner", None) is None, (
        "extraction_conn's write lease must be released (via rollback) "
        "after a transfer_index insert failure, not leaked -- this is "
        "the X78.3 fix"
    )

    conn.close()


@pytest.mark.asyncio
async def test_rpc_cache_does_not_collide_after_transfer_index_failure(tmp_path):
    """The core X78.3 regression: after _flush_page_batch handles a
    transfer_index failure, RPCCache (a completely different connection,
    same thread) must be able to acquire its own write lease without
    colliding -- this is the exact live signature from X78.2's sanity
    window (outer=extract_for_creator, inner=rpc_cache.py:68)."""
    db_path = str(tmp_path / "extraction.db")
    conn = _make_extraction_conn(db_path)

    extractor = RealTimeCreatorFundingExtractor.__new__(RealTimeCreatorFundingExtractor)
    extractor.domain_resolver = None

    await extractor._flush_page_batch(
        conn,
        creator="creatorAddr",
        funders_delta={},
        recipients_delta={},
        domain_addrs=set(),
        jito_events=[],
        transfer_index_rows=[("sig1", "a", "b", 100)],  # malformed -> raises
    )

    cache = RPCCache(db_path)
    try:
        cache.set("k1", {"result": "x"}, "getTransaction")
    except NestedDatabaseWriteError:
        pytest.fail(
            "RPCCache.set() collided with extraction_conn's lease after "
            "_flush_page_batch's transfer_index failure -- the lease was "
            "not properly released"
        )

    result = cache.get("k1")
    assert result == {"result": "x"}, "cache must remain functional after the failure path"

    conn.close()


@pytest.mark.asyncio
async def test_self_collision_across_pages_also_resolved(tmp_path):
    """The rarer extract_for_creator -> extract_for_creator self-collision
    X78.2 observed (5 occurrences) shares the same root cause: after a
    transfer_index failure on page N, page N+1's own _flush_page_batch
    call on the SAME extraction_conn must not collide with the
    (supposedly released) lease from page N."""
    db_path = str(tmp_path / "extraction.db")
    conn = _make_extraction_conn(db_path)

    extractor = RealTimeCreatorFundingExtractor.__new__(RealTimeCreatorFundingExtractor)
    extractor.domain_resolver = None

    # Page N: malformed row triggers the failure path.
    await extractor._flush_page_batch(
        conn, creator="creatorAddr", funders_delta={}, recipients_delta={},
        domain_addrs=set(), jito_events=[],
        transfer_index_rows=[("sig1", "a", "b", 100)],
    )

    # Page N+1: a normal, well-formed flush on the SAME connection.
    await extractor._flush_page_batch(
        conn, creator="creatorAddr",
        funders_delta={"funderX": {"amount": 1.5}},
        recipients_delta={},
        domain_addrs=set(), jito_events=[],
        transfer_index_rows=[("sig2", "c", "d", 200, 1234567890, 1234567890.0)],
    )

    row = conn.execute(
        "SELECT amount_sol FROM creator_funders WHERE funder_address=?", ("funderX",)
    ).fetchone()
    assert row is not None and row[0] == 1.5, "page N+1's write must have actually committed"

    conn.close()


@pytest.mark.asyncio
async def test_well_formed_transfer_index_rows_still_commit_normally(tmp_path):
    """Non-regression: the fix must not affect the happy path -- a
    well-formed batch still commits transfer_index rows normally."""
    db_path = str(tmp_path / "extraction.db")
    conn = _make_extraction_conn(db_path)

    extractor = RealTimeCreatorFundingExtractor.__new__(RealTimeCreatorFundingExtractor)
    extractor.domain_resolver = None

    await extractor._flush_page_batch(
        conn, creator="creatorAddr", funders_delta={}, recipients_delta={},
        domain_addrs=set(), jito_events=[],
        transfer_index_rows=[("sig1", "a", "b", 100, 1234567890, 1234567890.0)],
    )

    row = conn.execute("SELECT signature FROM transfer_index WHERE signature=?", ("sig1",)).fetchone()
    assert row is not None, "well-formed transfer_index rows must still be inserted and committed"
    assert getattr(_thread_write_lease, "owner", None) is None

    conn.close()
