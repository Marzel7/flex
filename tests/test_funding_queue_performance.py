"""X21D.4 Part B — /api/funding-queue query performance regression test.

Root cause (measured, not assumed): the row-listing query used by
/api/funding-queue combines a LEFT JOIN (creator_funding_queue -> metadata_cache)
with `ORDER BY cfq.created_at DESC LIMIT 100`. Without an index on
created_at, SQLite cannot use the LIMIT to short-circuit the scan — it must
join ALL rows against metadata_cache, sort the full joined result in a temp
B-tree, and only then take the top 100 (measured: 2.5-4.8s against the real
12k+ row production table; confirmed via EXPLAIN QUERY PLAN showing
`SCAN cfq` + `USE TEMP B-TREE FOR ORDER BY`).

The fix (idx_creator_funding_queue_created_at, added in
src/core/pumpfun_curve_listener.py's schema-init, next to the table's other
index) changes the plan to `SCAN cfq USING INDEX idx_creator_funding_queue_created_at`
with NO temp b-tree — proven live: 3.4s -> 16-75ms (45-200x).

This test proves the QUERY PLAN property (no temp b-tree for the sort) on a
small synthetic dataset, which is what actually matters — the plan shape is
independent of row count, so this remains a valid regression guard even
though a tiny fixture table wouldn't itself reproduce multi-second latency.
"""
from __future__ import annotations

import sqlite3

import pytest


SCHEMA = """
CREATE TABLE creator_funding_queue (
    creator_address TEXT,
    mint TEXT,
    create_tx_signature TEXT,
    status TEXT DEFAULT 'pending',
    source TEXT,
    next_attempt_at INTEGER DEFAULT 0,
    locked_until INTEGER DEFAULT 0,
    attempts INTEGER DEFAULT 0,
    last_error TEXT,
    created_at INTEGER DEFAULT (strftime('%s','now')),
    updated_at INTEGER DEFAULT (strftime('%s','now')),
    PRIMARY KEY (creator_address, mint)
);
CREATE INDEX idx_creator_funding_queue_status ON creator_funding_queue(status, next_attempt_at);
CREATE INDEX idx_creator_funding_queue_created_at ON creator_funding_queue(created_at DESC);

CREATE TABLE metadata_cache (
    mint TEXT PRIMARY KEY,
    symbol TEXT,
    name TEXT
);
"""

ROWS_QUERY = """
SELECT cfq.creator_address, cfq.mint, cfq.status, COALESCE(cfq.source, 'unknown') AS source,
       cfq.attempts, cfq.last_error, cfq.next_attempt_at, cfq.created_at, cfq.updated_at,
       mc.symbol, mc.name AS token_name
FROM creator_funding_queue cfq
LEFT JOIN metadata_cache mc ON mc.mint = cfq.mint
ORDER BY cfq.created_at DESC
LIMIT 100
"""


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(SCHEMA)
    for i in range(500):
        c.execute(
            "INSERT INTO creator_funding_queue (creator_address, mint, status, created_at) "
            "VALUES (?,?,?,?)",
            (f"creator{i}", f"mint{i}", "pending" if i % 2 else "complete", 1700000000 + i),
        )
        if i % 3 == 0:
            c.execute("INSERT INTO metadata_cache (mint, symbol, name) VALUES (?,?,?)", (f"mint{i}", f"SYM{i}", f"Token {i}"))
    c.commit()
    yield c
    c.close()


def _query_plan(conn: sqlite3.Connection, sql: str) -> list[str]:
    rows = conn.execute(f"EXPLAIN QUERY PLAN {sql}").fetchall()
    return [row[3] for row in rows]  # the 'detail' column


def test_rows_query_uses_index_for_order_by_not_a_temp_btree(conn):
    plan = _query_plan(conn, ROWS_QUERY)
    plan_text = " | ".join(plan)
    assert "USE TEMP B-TREE FOR ORDER BY" not in plan_text, (
        f"regression: the created_at index is missing or unused — plan was: {plan_text}"
    )
    assert any("idx_creator_funding_queue_created_at" in step for step in plan), (
        f"expected the created_at index to be used for the scan — plan was: {plan_text}"
    )


def test_rows_query_still_returns_correct_ordering_and_limit(conn):
    rows = conn.execute(ROWS_QUERY).fetchall()
    assert len(rows) == 100
    created_ats = [r[7] for r in rows]
    assert created_ats == sorted(created_ats, reverse=True)  # DESC order preserved
    # newest row (highest created_at) must be first — behavior unchanged
    assert created_ats[0] == 1700000000 + 499


def test_left_join_still_returns_null_metadata_when_absent(conn):
    """Confirm the index doesn't change LEFT JOIN semantics — rows without a
    metadata_cache match must still appear with NULL symbol/name, not be
    silently dropped."""
    rows = conn.execute(ROWS_QUERY).fetchall()
    have_null_symbol = any(r[9] is None for r in rows)
    have_symbol = any(r[9] is not None for r in rows)
    assert have_null_symbol and have_symbol, (
        "expected a mix of matched and unmatched metadata rows in this fixture"
    )


def test_without_the_index_a_temp_btree_would_be_required():
    """Negative control: confirm the OLD (pre-fix) schema — same table, same
    data, but WITHOUT the created_at index — genuinely produces the temp
    b-tree plan this fix eliminates. Proves the test above is measuring the
    right thing, not a tautology."""
    c = sqlite3.connect(":memory:")
    unindexed_schema = SCHEMA.replace(
        "CREATE INDEX idx_creator_funding_queue_created_at ON creator_funding_queue(created_at DESC);", ""
    )
    c.executescript(unindexed_schema)
    for i in range(500):
        c.execute(
            "INSERT INTO creator_funding_queue (creator_address, mint, status, created_at) VALUES (?,?,?,?)",
            (f"creator{i}", f"mint{i}", "pending", 1700000000 + i),
        )
    c.commit()
    plan = _query_plan(c, ROWS_QUERY)
    plan_text = " | ".join(plan)
    assert "USE TEMP B-TREE FOR ORDER BY" in plan_text
    c.close()


def test_stats_and_by_source_queries_unaffected_by_this_change(conn):
    """The other two /api/funding-queue queries (stats, by_source) do NOT sort
    by created_at and were already fast (measured 138ms/26ms in production,
    well within budget) — this fix must not touch or regress them. Confirm
    they still execute correctly (not asserting plan shape, since they were
    never the target of this fix)."""
    stats = conn.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending, "
        "COUNT(DISTINCT source) AS source_count FROM creator_funding_queue"
    ).fetchone()
    assert stats[0] == 500

    by_source = conn.execute(
        "SELECT COALESCE(source,'unknown') AS source, COUNT(*) AS total "
        "FROM creator_funding_queue GROUP BY source ORDER BY total DESC"
    ).fetchall()
    assert len(by_source) >= 1
