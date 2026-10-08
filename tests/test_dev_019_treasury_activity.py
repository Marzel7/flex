from __future__ import annotations

import sqlite3

from src.ops.treasury_activity import (
    ACTIVE, ACTIVITY_UNKNOWN, DEEP_DISCOVERY, DORMANT, HOT, RETIRED_CANDIDATE,
    SUPPRESS_ROUTINE_DEEP_SCAN, TARGETED_LAUNCH_BACKWARD, VERIFY_FUNDING,
    classify_activity, discovery_priority, is_meaningful_funding,
)
from src.ops.treasury_mesh_discovery_engine import (
    activity_snapshot, ensure_schema, record_activity_meaningful_funding,
    record_activity_transaction,
)

NOW = 1_000_000_000
DAY = 24 * 60 * 60


def test_complete_activity_windows_and_unknown_fail_closed():
    assert classify_activity(now=NOW, last_transaction_at=NOW - 7 * DAY, coverage_complete=True) == HOT
    assert classify_activity(now=NOW, last_transaction_at=NOW - 8 * DAY, coverage_complete=True) == ACTIVE
    assert classify_activity(now=NOW, last_transaction_at=NOW - 31 * DAY, coverage_complete=True) == DORMANT
    assert classify_activity(now=NOW, last_transaction_at=NOW - 91 * DAY, coverage_complete=True) == RETIRED_CANDIDATE
    assert classify_activity(now=NOW, last_transaction_at=NOW - DAY, coverage_complete=False) == ACTIVITY_UNKNOWN
    assert classify_activity(now=NOW, last_transaction_at=None, coverage_complete=True) == ACTIVITY_UNKNOWN


def test_transaction_activity_is_not_meaningful_funding_and_dust_is_not_reactivation():
    assert not is_meaningful_funding(lamports=1, balance_delta_verified=True, material_screening_lamports=10_000_000_000)
    assert not is_meaningful_funding(lamports=20_000_000_000, balance_delta_verified=False, material_screening_lamports=10_000_000_000)
    assert is_meaningful_funding(lamports=20_000_000_000, balance_delta_verified=True, material_screening_lamports=10_000_000_000)
    assert discovery_priority(activity_class=HOT, has_recent_meaningful_funding=False, launch_backward_evidence=False) == VERIFY_FUNDING


def test_dormant_suppression_reactivation_and_launch_backward_override():
    assert discovery_priority(activity_class=DORMANT, has_recent_meaningful_funding=True, launch_backward_evidence=False) == SUPPRESS_ROUTINE_DEEP_SCAN
    assert discovery_priority(activity_class=RETIRED_CANDIDATE, has_recent_meaningful_funding=False, launch_backward_evidence=True) == TARGETED_LAUNCH_BACKWARD
    assert discovery_priority(activity_class=HOT, has_recent_meaningful_funding=True, launch_backward_evidence=False) == DEEP_DISCOVERY


def test_compact_activity_store_preserves_identity_outside_the_activity_signal():
    conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row; ensure_schema(conn)
    record_activity_transaction(conn, address="confirmed-retired", observed_at=NOW - 100 * DAY, now=NOW)
    record_activity_meaningful_funding(conn, address="confirmed-retired", observed_at=NOW - DAY, now=NOW)
    snap = activity_snapshot(conn, address="confirmed-retired")
    assert snap["last_transaction_at"] == NOW - 100 * DAY
    assert snap["last_meaningful_funding_at"] == NOW - DAY
    assert "identity" not in snap and "confirmed" not in snap
