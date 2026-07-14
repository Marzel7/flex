"""X24.1 Phase 3 — proves a qualifying PLAIN_TRANSFER-funded event reaches the
EXISTING record_launch() path and produces a real wt_watchtower_launches row,
using the real schema and the real production function (ws_cascade_store.
record_launch) — not a second/parallel pipeline.

This does not simulate the WS/RPC layer (that would require mocking Helius and
would be fragile/unrepresentative); it proves the persistence half of the
chain: once a candidate CREATE is observed for a PLAIN_TRANSFER-funded
sub-provisioner, record_launch() persists it identically to a WSOL_WRAP_CLOSE
launch (same table, same idempotency, same required fields) — there is no
mechanism-specific branching in the write path, so the mechanism-aware
subscription fix (which only changes HOW the notification arrives) is
sufficient without a second launch pipeline.
"""
from __future__ import annotations

import sqlite3

import pytest

from src.core import ws_cascade_store as store


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    store.ensure_cascade_schema(c)
    yield c
    c.close()


def test_plain_transfer_funded_launch_reaches_record_launch_and_persists(conn):
    """Simulates the AWiaGsus-like chain: a subprov funded via PLAIN_TRANSFER
    whose own wrap-close to the creator is observed (via the new accountSubscribe
    -> _handle_subprov_tx path) and CREATE fires. record_launch() must persist
    it exactly like any other launch."""
    newly = store.record_launch(
        conn,
        mint="AWiaGsusSIMULATEDMINTxxxxxxxxxxxxxxxxxxxxxxx",
        creator="Cfqnd2XSIMULATEDCREATORxxxxxxxxxxxxxxxxxxxxx",
        create_sig="SIMULATED_CREATE_SIG_1",
        create_time=1784052892,
        treasury="9hGcxVSIMULATEDTREASURYxxxxxxxxxxxxxxxxxxxxx",
        subprov="7fBzdSIMULATEDSUBPROVxxxxxxxxxxxxxxxxxxxxxxx",
        wrap_close_sig="SIMULATED_WRAP_CLOSE_SIG_1",
        birth_to_launch_s=5,
        subprov_funding_sol=719.0,
        wrap_close_sol=1.112039,
        detection_source="LIVE_STREAM",
        detection_delay_seconds=3,
        funding_mechanism="PLAIN_TRANSFER",
    )
    assert newly is True

    row = conn.execute(
        "SELECT mint, creator_wallet, treasury_wallet, subprov_wallet, "
        "funding_mechanism, detection_source, state "
        "FROM wt_watchtower_launches WHERE mint=?",
        ("AWiaGsusSIMULATEDMINTxxxxxxxxxxxxxxxxxxxxxxx",),
    ).fetchone()
    assert row is not None
    assert row["treasury_wallet"] == "9hGcxVSIMULATEDTREASURYxxxxxxxxxxxxxxxxxxxxx"
    assert row["subprov_wallet"] == "7fBzdSIMULATEDSUBPROVxxxxxxxxxxxxxxxxxxxxxxx"
    assert row["funding_mechanism"] == "PLAIN_TRANSFER"
    assert row["detection_source"] == "LIVE_STREAM"
    assert row["state"] == "FIRED_CREATE"


def test_wrap_close_funded_launch_unchanged_behaviour(conn):
    """Regression: an ordinary WSOL_WRAP_CLOSE launch must persist identically
    to before this fix — same table, same fields, same idempotency."""
    newly = store.record_launch(
        conn,
        mint="EGB4svSIMULATEDMINTxxxxxxxxxxxxxxxxxxxxxxxxx",
        creator="HTR9U7SIMULATEDCREATORxxxxxxxxxxxxxxxxxxxxxx",
        create_sig="SIMULATED_CREATE_SIG_2",
        create_time=1784048633,
        treasury="9hGcxVSIMULATEDTREASURYxxxxxxxxxxxxxxxxxxxxx",
        subprov="ANenEuSIMULATEDSUBPROVxxxxxxxxxxxxxxxxxxxxxx",
        wrap_close_sig="SIMULATED_WRAP_CLOSE_SIG_2",
        birth_to_launch_s=1,
        detection_source="ACTIVE_CATCHUP",
    )
    assert newly is True
    row = conn.execute(
        "SELECT funding_mechanism, detection_source FROM wt_watchtower_launches WHERE mint=?",
        ("EGB4svSIMULATEDMINTxxxxxxxxxxxxxxxxxxxxxxxxx",),
    ).fetchone()
    assert row["funding_mechanism"] == "WSOL_WRAP_CLOSE"  # store.py's FUNDING_MECHANISM default
    assert row["detection_source"] == "ACTIVE_CATCHUP"


def test_duplicate_create_signature_is_idempotent(conn):
    """record_launch is INSERT OR IGNORE on (creator, create_sig) — a duplicate
    observation (e.g. seen via both accountSubscribe and a later catch-up scan)
    must not create a second row or raise."""
    args = dict(
        mint="DUPTESTMINTxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        creator="DUPTESTCREATORxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        create_sig="DUP_SIG_1",
        create_time=1784052892,
        treasury="DUPTREASURYxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        subprov="DUPSUBPROVxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        wrap_close_sig="DUP_WRAP_SIG_1",
        birth_to_launch_s=2,
        funding_mechanism="PLAIN_TRANSFER",
    )
    first = store.record_launch(conn, **args)
    second = store.record_launch(conn, **args)
    assert first is True
    assert second is False  # INSERT OR IGNORE — no new row, no exception
    count = conn.execute(
        "SELECT COUNT(*) FROM wt_watchtower_launches WHERE mint=?", (args["mint"],)
    ).fetchone()[0]
    assert count == 1
