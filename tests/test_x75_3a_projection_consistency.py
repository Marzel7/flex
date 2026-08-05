"""X75.3A PART 8 -- Projection consistency checks (read-only, no repair).

Reports on two persisting cross-table gaps X74.2/X75.3 both independently
flagged:

  1. operator_entities (populated, ~70 rows) vs operator_identity_assets
     (the X73.0 governance-lifecycle asset ledger -- historically empty).
  2. wt_treasury_review (decisions recorded directly on the row via
     status/reviewed_by/reviewed_at) vs wt_treasury_review_actions (the
     X74.1 immutable audit log meant to record every review action --
     historically empty despite real decisions existing).

Per the X75.3A task brief: "Do not repair these tables in this milestone."
These tests only assert the CURRENT state and classify it (historical /
expected / broken) -- they do not fix, backfill, or write to anything.
"""
from __future__ import annotations

import os
import sqlite3

import pytest

_LIVE_DB = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "database", "wt_ops_v2.db"
))


def _skip_if_no_live_db():
    if not os.path.exists(_LIVE_DB) or os.path.getsize(_LIVE_DB) < 1024:
        pytest.skip("live database/wt_ops_v2.db not present")


@pytest.fixture(scope="module")
def live_conn():
    _skip_if_no_live_db()
    conn = sqlite3.connect(f"file:{_LIVE_DB}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    yield conn
    conn.close()


def test_report_operator_entities_vs_operator_identity_assets(live_conn):
    """CLASSIFICATION: BROKEN (not historical/expected).

    operator_identity_assets is the X73.0 governance-lifecycle asset
    ledger, written by OperatorIdentityGovernanceService.expand() -- it
    should accumulate one row per (operator_id, asset_type, asset_value)
    every time a treasury/subprov/etc. is formally attached to an operator
    identity via that service. operator_entities is the older, still-
    populated role table that DOES get 70 real rows via
    watchtower_alignment.py's reconcile_confirmed_treasury() (called
    directly from treasury_bank.promote_to_confirmed(), NOT through
    OperatorIdentityGovernanceService.expand()). The two tables are fed by
    two DIFFERENT, non-overlapping write paths -- operator_entities' path
    is exercised in production; operator_identity_assets' path
    (OperatorIdentityGovernanceService.expand(), which X74.1's Treasury
    Review APPROVE_TREASURY action calls) has never actually been invoked
    by a real analyst action (confirmed separately in X74.2). This is a
    genuine dual-path gap, not a designed one -- the governance ledger
    exists but its only real-world entry point has not yet been used."""
    entities_count = live_conn.execute("SELECT COUNT(*) FROM operator_entities").fetchone()[0]
    assets_count = live_conn.execute("SELECT COUNT(*) FROM operator_identity_assets").fetchone()[0]
    print(f"\noperator_entities: {entities_count} rows")
    print(f"operator_identity_assets: {assets_count} rows")
    print("Classification: BROKEN -- two independent write paths exist "
          "(watchtower_alignment.reconcile_confirmed_treasury vs "
          "OperatorIdentityGovernanceService.expand); only the former is "
          "exercised in production today, so operator_identity_assets "
          "never accumulates despite being the newer, intended ledger.")
    assert entities_count > 0, "expected operator_entities to be populated (sanity check on live data)"
    # Not asserting assets_count == 0 as a hard requirement -- if this ever
    # becomes non-zero, that's a sign the governance ledger has started
    # being exercised, which is a state change worth noticing, not failing
    # a test over. Report only.


def test_report_treasury_review_vs_treasury_review_actions(live_conn):
    """CLASSIFICATION: BROKEN (not historical/expected).

    wt_treasury_review's status/reviewed_by/reviewed_at columns ARE being
    written directly (117 non-PENDING_REVIEW rows confirm real decisions
    exist) via treasury_bank.promote_to_confirmed()/reject_candidate() --
    but neither of those functions writes to
    wt_treasury_review_actions. That table is only ever written by
    src/ops/treasury_review_workspace.py's perform_action() dispatch (the
    X74.1 analyst workspace's own action handlers), which is a separate,
    newer code path that has not yet been used for any of the 117 existing
    decisions (all of which predate X74.1, or were made through the older
    treasury_bank.py functions directly). This is the same shape of gap as
    the assets-ledger one above: a newer, more complete audit mechanism
    exists in code but has an empty real-world track record because the
    decisions that exist were all made through the older path."""
    review_count = live_conn.execute("SELECT COUNT(*) FROM wt_treasury_review").fetchone()[0]
    decided_count = live_conn.execute(
        "SELECT COUNT(*) FROM wt_treasury_review WHERE status != 'PENDING_REVIEW'"
    ).fetchone()[0]
    actions_count = live_conn.execute("SELECT COUNT(*) FROM wt_treasury_review_actions").fetchone()[0]
    print(f"\nwt_treasury_review: {review_count} rows ({decided_count} decided, non-pending)")
    print(f"wt_treasury_review_actions: {actions_count} rows")
    print("Classification: BROKEN -- decisions are recorded directly on "
          "wt_treasury_review (status/reviewed_by/reviewed_at) via the "
          "older treasury_bank.py functions; wt_treasury_review_actions "
          "is only written by the newer treasury_review_workspace.py "
          "analyst-action dispatch, which has 0 real invocations to date.")
    assert review_count > 0, "expected wt_treasury_review to be populated (sanity check on live data)"
    assert decided_count > 0, "expected at least some non-pending decisions to exist (sanity check)"
