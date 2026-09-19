from __future__ import annotations

import sqlite3
import inspect
from unittest.mock import patch

from src.core import walkback_queue
from src.ops import anchor_reconciliation as recon
from src.ops import create_event_ledger as ledger
from src.core import walkback_worker


def _ops() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    walkback_queue.ensure_schema(conn)
    recon.ensure_schema(conn)
    ledger.ensure_schema(conn)
    return conn


def _live() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE creator_funding_queue (mint TEXT, create_tx_signature TEXT, updated_at INTEGER)")
    conn.execute("CREATE TABLE token_analysis (mint TEXT, create_tx_signature TEXT)")
    conn.execute("CREATE TABLE wt_detected_creates (mint TEXT, create_tx_signature TEXT)")
    return conn


def test_read_only_validators_accept_current_schema_without_writes():
    conn = _ops()
    writes = []
    conn.set_trace_callback(lambda sql: writes.append(sql) if sql.lstrip().upper().startswith(
        ("CREATE", "ALTER", "INSERT", "UPDATE", "DELETE", "DROP", "REPLACE")
    ) else None)
    assert recon.validate_schema(conn) == "VALID"
    assert ledger.validate_schema(conn) == "VALID"
    assert writes == []


def test_recurring_retry_path_does_not_call_schema_ensure():
    conn = _ops()
    with patch.object(ledger, "ensure_schema", side_effect=AssertionError("runtime DDL")):
        result = ledger.retry_pending_writes(conn, ensure_schema_first=False)
    assert result == {"examined": 0, "recovered": [], "still_failing": [], "exhausted": 0}


def test_recurring_anchor_path_does_not_call_schema_ensure():
    ops = _ops()
    live = _live()
    with patch.object(recon, "ensure_schema", side_effect=AssertionError("runtime DDL")), patch.object(
        ledger, "ensure_schema", side_effect=AssertionError("runtime ledger DDL")
    ):
        result = recon.reconcile_waiting_create_anchors(
            ops, live, ensure_schema_first=False, limit=25,
        )
    assert result["examined"] == 0


def test_recurring_anchor_inventory_is_bounded_to_1_oldest_row():
    ops = _ops()
    for i in range(60):
        ops.execute(
            "INSERT INTO wt_walkback_queue "
            "(mint, walkback_class, status, path_state, create_anchor_audit_state, attempts, rpc_used, enqueued_at, updated_at) "
            "VALUES (?, 'FULL_WALKBACK', ?, ?, 'MISSING_OR_MALFORMED', 0, 0, ?, ?)",
            (f"mint-{i:02d}", recon.WAITING_STATUS, recon.WAITING_PATH_STATE, i, i),
        )
    ops.commit()
    rows = recon._stuck_rows(ops, limit=1)
    assert len(rows) == 1
    assert [row["mint"] for row in rows] == ["mint-00"]


def test_each_funder_promotion_commits_before_next_candidate():
    source = inspect.getsource(walkback_worker.promote_recurring_funders)
    commit = source.index("ops.commit()")
    counted = source.index("promoted += 1")
    assert commit < counted
    assert "if promoted:" not in source
