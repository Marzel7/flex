import sqlite3

import pytest

from src.core.creator_history_coverage import CreatorHistoryCoverageStore


def _conn():
    connection = sqlite3.connect(":memory:")
    CreatorHistoryCoverageStore().ensure_schema(connection)
    connection.commit()
    return connection


def _page(*signatures):
    return [
        {"signature": signature, "slot": index + 100, "timestamp": index + 1000}
        for index, signature in enumerate(signatures)
    ]


def _state(conn, creator="creator"):
    return conn.execute(
        "SELECT state, reason, provider_exhausted, contiguous_boundary_proven, mutable_head "
        "FROM creator_history_coverage_state WHERE creator_address = ?", (creator,)
    ).fetchone()


def test_single_page_provider_exhaustion_can_be_complete_with_boundary():
    conn = _conn()
    store = CreatorHistoryCoverageStore()
    run = store.begin_run(conn, "creator", provider="helius", method="enhanced", run_id="run")
    store.record_durable_page(conn, run, 1, None, _page("new", "old"))
    store.finish_run(
        conn, run, state="COMPLETE_EXHAUSTED", reason="provider_history_exhausted",
        provider_exhausted=True, contiguous_boundary_proven=True,
    )
    assert _state(conn) == ("COMPLETE_EXHAUSTED", "provider_history_exhausted", 1, 1, 1)


def test_deep_provider_exhaustion_without_overlap_remains_explicitly_unverified():
    conn = _conn()
    store = CreatorHistoryCoverageStore()
    run = store.begin_run(conn, "creator", provider="helius", method="enhanced", run_id="run")
    store.record_durable_page(conn, run, 1, None, _page("new", "boundary"))
    store.record_durable_page(conn, run, 2, "boundary", _page("older", "old"))
    store.finish_run(
        conn, run, state="EXHAUSTED_UNVERIFIED_CONTIGUITY",
        reason="provider_history_exhausted", provider_exhausted=True,
    )
    assert _state(conn)[0] == "EXHAUSTED_UNVERIFIED_CONTIGUITY"
    assert _state(conn)[3] == 0


def test_partial_failure_and_timeout_cannot_become_complete():
    conn = _conn()
    store = CreatorHistoryCoverageStore()
    run = store.begin_run(conn, "creator", provider="helius", method="enhanced", run_id="run")
    store.record_durable_page(conn, run, 1, None, _page("a"))
    store.finish_run(conn, run, state="FAILED", reason="provider_timeout")
    assert _state(conn)[:4] == ("FAILED", "provider_timeout", 0, 0)


def test_crash_after_facts_before_coverage_checkpoint_understates_not_overstates():
    conn = _conn()
    store = CreatorHistoryCoverageStore()
    run = store.begin_run(conn, "creator", provider="helius", method="enhanced", run_id="run")
    # Simulate page-domain facts having committed; no coverage page is written.
    conn.execute("CREATE TABLE facts (signature TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO facts VALUES ('persisted-before-crash')")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM creator_history_coverage_pages").fetchone()[0] == 0
    assert conn.execute(
        "SELECT terminal_state FROM creator_history_coverage_runs WHERE run_id = ?", (run,)
    ).fetchone()[0] == "UNKNOWN"


def test_duplicate_overlap_is_observed_not_silently_treated_as_contiguous():
    conn = _conn()
    store = CreatorHistoryCoverageStore()
    run = store.begin_run(conn, "creator", provider="helius", method="enhanced", run_id="run")
    page = store.record_durable_page(conn, run, 1, None, _page("a", "a", "b"))
    assert page.duplicate_count == 1
    with pytest.raises(ValueError):
        store.finish_run(conn, run, state="COMPLETE_EXHAUSTED", reason="bad")


def test_restart_and_version_mismatch_are_distinct_immutable_runs():
    conn = _conn()
    store = CreatorHistoryCoverageStore()
    first = store.begin_run(
        conn, "creator", provider="helius", method="enhanced", parser_version="parser-a", run_id="one"
    )
    store.finish_run(conn, first, state="PARTIAL", reason="page_cap")
    second = store.begin_run(
        conn, "creator", provider="helius", method="enhanced", parser_version="parser-b", run_id="two"
    )
    assert first != second
    assert conn.execute(
        "SELECT COUNT(DISTINCT parser_version) FROM creator_history_coverage_runs WHERE creator_address = 'creator'"
    ).fetchone()[0] == 2

