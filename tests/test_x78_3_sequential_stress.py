"""X78.3 Phase 15: sequential stress test -- 100 simulated extraction pages
against the real _flush_page_batch, mixing well-formed pages, malformed
(failing) transfer_index rows, and interleaved RPCCache activity.

Expected: NestedDatabaseWriteError == 0, no leaked write leases, no
regression to cache functionality.
"""
from __future__ import annotations

import random

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
async def test_100_sequential_pages_zero_nested_write_errors(tmp_path):
    db_path = str(tmp_path / "extraction.db")
    conn = _make_extraction_conn(db_path)
    cache = RPCCache(db_path)

    extractor = RealTimeCreatorFundingExtractor.__new__(RealTimeCreatorFundingExtractor)
    extractor.domain_resolver = None

    random.seed(2024)
    nested_errors = 0
    N = 100

    for i in range(N):
        malformed = random.random() < 0.3
        if malformed:
            rows = [("sig", "a", "b", 1)]  # wrong arity -> raises
        else:
            rows = [(f"sig{i}", "a", "b", 100, 1234567890 + i, 1234567890.0 + i)]

        try:
            await extractor._flush_page_batch(
                conn,
                creator=f"creator{i % 5}",
                funders_delta={f"funder{i}": {"amount": 1.0}},
                recipients_delta={},
                domain_addrs=set(),
                jito_events=[],
                transfer_index_rows=rows,
            )
        except NestedDatabaseWriteError:
            nested_errors += 1

        # Interleave real cache activity on the same thread, exactly like
        # get_transaction()/get_signatures_until_time() do during paging.
        try:
            cache.set(f"key{i}", {"v": i}, "getTransaction")
            cache.get(f"key{i}")
        except NestedDatabaseWriteError:
            nested_errors += 1

    assert nested_errors == 0, f"{nested_errors} NestedDatabaseWriteError collisions occurred"
    assert getattr(_thread_write_lease, "owner", None) is None, "no lease should remain held after the run"

    # Cache still functional.
    assert cache.get("key99") == {"v": 99}

    conn.close()
