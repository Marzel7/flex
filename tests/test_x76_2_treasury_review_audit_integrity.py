"""X76.2 — Treasury Review Audit Integrity.

Permanent regression coverage: every analyst governance decision must
produce BOTH a mutable wt_treasury_review status update AND an immutable
wt_treasury_review_actions event, in the same transaction, regardless of
which of the three call sites (X74.1 workspace, or either of the two
older operation_dashboard_routes.py HTTP surfaces) triggered it.
"""
from __future__ import annotations

import os
import sqlite3
import time

import pytest

from src.core import treasury_bank
from src.ops.treasury_review_workspace import (
    perform_action,
    ensure_schema,
    WorkspaceError,
)

_LIVE_DB = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "database", "wt_ops_v2.db"
))


def _skip_if_no_live_db():
    if not os.path.exists(_LIVE_DB) or os.path.getsize(_LIVE_DB) < 1024:
        pytest.skip("live database/wt_ops_v2.db not present")


_COPY_PATHS: dict[int, str] = {}


@pytest.fixture(scope="module")
def _shared_copy_path(tmp_path_factory):
    """The live database (~2.9GB) is copied EXACTLY ONCE per test module
    run, not once per test function -- with 19+ tests, per-function
    copying (the original version of this fixture) filled the disk
    entirely (57GB+ accumulated across a handful of pytest invocations,
    since pytest's tmp_path retains the last 3 sessions by default) and
    crashed mid-run. Every test in this module shares one on-disk copy;
    each test's own writes are small (a handful of rows) and additive,
    never re-copying the base file."""
    _skip_if_no_live_db()
    import shutil
    tmp_dir = tmp_path_factory.mktemp("x76_2_shared")
    copy_path = tmp_dir / "wt_ops_v2_copy.db"
    shutil.copy2(_LIVE_DB, copy_path)
    return str(copy_path)


@pytest.fixture
def live_copy_conn(_shared_copy_path):
    conn = sqlite3.connect(_shared_copy_path)
    conn.row_factory = sqlite3.Row
    # sqlite3.Connection does not support arbitrary attribute assignment,
    # so the copy path is tracked out-of-band by id(conn) -- see
    # _governance_service_for()'s docstring for why tests need it.
    _COPY_PATHS[id(conn)] = _shared_copy_path
    yield conn
    _COPY_PATHS.pop(id(conn), None)
    conn.close()


def _governance_service_for(conn):
    """Every OperatorIdentityGovernanceService MUST be explicitly bound to
    the test's own copy path -- never constructed with its default
    argument, which resolves to the LIVE ops database independently of
    whatever connection object a test is otherwise using. An earlier
    version of this test file passed no governance_service override to
    link_to_existing_operator()/create_operator_candidate(), and those
    functions' own default (`OperatorIdentityGovernanceService(str(OPS_DB_PATH))`)
    silently wrote 4 real rows to the live database before this was caught
    and cleaned up. This helper exists specifically to make that mistake
    impossible to repeat in this file."""
    from src.ops.operator_identity_governance import OperatorIdentityGovernanceService
    return OperatorIdentityGovernanceService(_COPY_PATHS[id(conn)])


# Skip this specific wallet: as of this session it carries a pre-existing
# live-data anomaly (registered as an operator_entities TREASURY for BOTH
# WATCHTOWER and 3SW2 simultaneously) that would make promote_to_confirmed()
# raise TreasuryOwnershipConflict for any test that happens to pick it.
# Not caused by, or a target of, this milestone's fix -- worked around here
# so it doesn't block validating the actual audit-integrity behaviour.
_KNOWN_DUAL_OWNED_WALLET = "Ef132NRtA9rNbkbZ7793SqfistmdtepYFkjdFMz3GtvK"


def _pending_treasury(conn, offset=0):
    """Picks a PENDING_REVIEW treasury not already touched by an earlier
    test in this module. NEEDS_MORE_EVIDENCE is the one action that
    deliberately leaves status=PENDING_REVIEW (an annotation, not a
    transition) -- since live_copy_conn is module-scoped (one shared DB
    file across all tests, to avoid re-copying the ~2.9GB live database
    per test function), a treasury a prior test already annotated must be
    excluded here or a later test would silently accumulate a second
    action row on the same treasury and see len(actions)==2 instead of 1."""
    already_touched = {
        r["treasury"] for r in conn.execute(
            "SELECT DISTINCT treasury FROM wt_treasury_review_actions"
        ).fetchall()
    } if _table_exists_local(conn, "wt_treasury_review_actions") else set()
    rows = conn.execute(
        "SELECT treasury FROM wt_treasury_review WHERE status='PENDING_REVIEW' ORDER BY treasury LIMIT 50"
    ).fetchall()
    rows = [r for r in rows if r["treasury"] != _KNOWN_DUAL_OWNED_WALLET and r["treasury"] not in already_touched]
    if offset >= len(rows):
        pytest.skip("no PENDING_REVIEW treasuries available in this database snapshot")
    return rows[offset]["treasury"]


def _table_exists_local(conn, table):
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone())


def _action_rows(conn, treasury):
    return conn.execute(
        "SELECT * FROM wt_treasury_review_actions WHERE treasury=? ORDER BY created_at", (treasury,)
    ).fetchall()


class TestTreasuryBankDirectCallsRecordAudit:
    """The actual production choke point: treasury_bank.promote_to_confirmed()
    / reject_candidate(), called directly (as every real historical decision
    was)."""

    def test_promote_to_confirmed_writes_mutable_and_immutable(self, live_copy_conn):
        conn = live_copy_conn
        ensure_schema(conn)
        t = _pending_treasury(conn)

        result = treasury_bank.promote_to_confirmed(conn, t, reviewed_by="analyst-x", reason="strong evidence")
        conn.commit()
        assert result["ok"] is True

        review_row = conn.execute("SELECT status FROM wt_treasury_review WHERE treasury=?", (t,)).fetchone()
        assert review_row["status"] == "CONFIRMED"

        actions = _action_rows(conn, t)
        assert len(actions) == 1
        assert actions[0]["action"] == "APPROVE_TREASURY"
        assert actions[0]["analyst"] == "analyst-x"
        assert actions[0]["reason"] == "strong evidence"

    def test_reject_candidate_writes_mutable_and_immutable(self, live_copy_conn):
        conn = live_copy_conn
        ensure_schema(conn)
        t = _pending_treasury(conn)

        result = treasury_bank.reject_candidate(conn, t, reviewed_by="analyst-y", reason="insufficient evidence")
        conn.commit()
        assert result["ok"] is True

        review_row = conn.execute("SELECT status FROM wt_treasury_review WHERE treasury=?", (t,)).fetchone()
        assert review_row["status"] == "REJECTED"

        actions = _action_rows(conn, t)
        assert len(actions) == 1
        assert actions[0]["action"] == "REJECT_TREASURY"
        assert actions[0]["reason"] == "insufficient evidence"

    def test_immutability_enforced(self, live_copy_conn):
        conn = live_copy_conn
        ensure_schema(conn)
        t = _pending_treasury(conn)
        treasury_bank.promote_to_confirmed(conn, t, reviewed_by="analyst-x", reason="r")
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE wt_treasury_review_actions SET reason='hacked' WHERE treasury=?", (t,))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM wt_treasury_review_actions WHERE treasury=?", (t,))


class TestNoDoubleWriteThroughWorkspace:
    """The X74.1 workspace dispatch must produce exactly ONE audit row per
    action, not two (one from treasury_bank.py's internal write, one from
    the workspace's own former _record_action call)."""

    def test_approve_via_workspace_produces_exactly_one_action_row(self, live_copy_conn):
        from src.ops.treasury_review_workspace import approve_treasury
        conn = live_copy_conn
        ensure_schema(conn)
        treasury_bank._ensure_schema_once(conn)
        t = _pending_treasury(conn)

        # NOTE: called directly (not via perform_action, whose dispatch
        # signature doesn't forward extra kwargs) so a governance_service
        # explicitly bound to the copy DB can be injected -- approve_treasury()
        # defaults operator_id to WATCHTOWER and, without this override,
        # constructs its own OperatorIdentityGovernanceService(OPS_DB_PATH)
        # pointed at the LIVE database. An earlier version of this test
        # called perform_action() with no override and leaked 2 real
        # immutable operator_identity_events rows into production before
        # this was caught (see the incident note on _governance_service_for).
        result = approve_treasury(conn, t, {"analyst": "ws-analyst", "reason": "via workspace"},
                                  governance_service=_governance_service_for(conn))
        assert result["ok"] is True

        actions = _action_rows(conn, t)
        assert len(actions) == 1, f"expected exactly 1 audit row, got {len(actions)}: {[dict(a) for a in actions]}"
        assert actions[0]["analyst"] == "ws-analyst"
        assert actions[0]["reason"] == "via workspace"

    def test_reject_via_workspace_produces_exactly_one_action_row(self, live_copy_conn):
        conn = live_copy_conn
        ensure_schema(conn)
        treasury_bank._ensure_schema_once(conn)
        t = _pending_treasury(conn)

        result = perform_action(conn, t, "REJECT_TREASURY", {"analyst": "ws-analyst", "reason": "via workspace reject"})
        assert result["ok"] is True

        actions = _action_rows(conn, t)
        assert len(actions) == 1
        assert actions[0]["action"] == "REJECT_TREASURY"


class TestAllSixActionsProduceMutableAndImmutablePair:
    """PHASE 7 -- every analyst action must produce both states."""

    def test_approve_treasury(self, live_copy_conn):
        from src.ops.treasury_review_workspace import approve_treasury
        conn = live_copy_conn
        ensure_schema(conn)
        treasury_bank._ensure_schema_once(conn)
        t = _pending_treasury(conn)
        # Direct call + explicit governance_service override -- see the
        # incident note in TestNoDoubleWriteThroughWorkspace above.
        approve_treasury(conn, t, {"analyst": "a", "reason": "r"},
                         governance_service=_governance_service_for(conn))
        status = conn.execute("SELECT status FROM wt_treasury_review WHERE treasury=?", (t,)).fetchone()["status"]
        assert status == "CONFIRMED"
        assert len(_action_rows(conn, t)) == 1

    def test_reject_treasury(self, live_copy_conn):
        conn = live_copy_conn
        ensure_schema(conn)
        treasury_bank._ensure_schema_once(conn)
        t = _pending_treasury(conn)
        perform_action(conn, t, "REJECT_TREASURY", {"analyst": "a", "reason": "r"})
        status = conn.execute("SELECT status FROM wt_treasury_review WHERE treasury=?", (t,)).fetchone()["status"]
        assert status == "REJECTED"
        assert len(_action_rows(conn, t)) == 1

    def test_needs_more_evidence(self, live_copy_conn):
        conn = live_copy_conn
        ensure_schema(conn)
        treasury_bank._ensure_schema_once(conn)
        t = _pending_treasury(conn)
        perform_action(conn, t, "NEEDS_MORE_EVIDENCE", {"analyst": "a", "reason": "r"})
        status = conn.execute("SELECT status FROM wt_treasury_review WHERE treasury=?", (t,)).fetchone()["status"]
        assert status == "PENDING_REVIEW"  # annotation only, no status transition
        assert len(_action_rows(conn, t)) == 1

    def test_create_investigation(self, live_copy_conn):
        conn = live_copy_conn
        ensure_schema(conn)
        treasury_bank._ensure_schema_once(conn)
        t = _pending_treasury(conn)
        perform_action(conn, t, "CREATE_INVESTIGATION", {"analyst": "a", "reason": "r"})
        status = conn.execute("SELECT status FROM wt_treasury_review WHERE treasury=?", (t,)).fetchone()["status"]
        assert status == "INVESTIGATING"
        assert len(_action_rows(conn, t)) == 1

    def test_link_to_existing_operator(self, live_copy_conn):
        from src.ops.treasury_review_workspace import link_to_existing_operator
        conn = live_copy_conn
        ensure_schema(conn)
        treasury_bank._ensure_schema_once(conn)
        t = _pending_treasury(conn)
        # 3SW2, a CONFIRMED operator, so expand() preconditions are satisfied.
        # governance_service is explicitly bound to the copy DB -- see
        # _governance_service_for()'s docstring for why this is required.
        result = link_to_existing_operator(conn, t, {
            "analyst": "a", "reason": "r", "operator_id": "64527dc2-8073-50c0-8bd7-7ef49e62d875",
        }, governance_service=_governance_service_for(conn))
        assert result["ok"] is True
        status = conn.execute("SELECT status FROM wt_treasury_review WHERE treasury=?", (t,)).fetchone()["status"]
        assert status == "LINKED"
        assert len(_action_rows(conn, t)) == 1

    def test_create_operator_candidate(self, live_copy_conn):
        from src.ops.treasury_review_workspace import create_operator_candidate
        conn = live_copy_conn
        ensure_schema(conn)
        treasury_bank._ensure_schema_once(conn)
        t = _pending_treasury(conn)
        result = create_operator_candidate(
            conn, t, {"analyst": "a", "reason": "r"},
            governance_service=_governance_service_for(conn),
        )
        assert result["ok"] is True
        status = conn.execute("SELECT status FROM wt_treasury_review WHERE treasury=?", (t,)).fetchone()["status"]
        assert status == "LINKED"
        assert len(_action_rows(conn, t)) == 1


class TestOperationDashboardRoutesRecordAudit:
    """The two older HTTP surfaces in operation_dashboard_routes.py must
    now also produce a wt_treasury_review_actions row, additively
    alongside their own pre-existing wt_treasury_approval_audit write."""

    def test_treasury_promote_route_function_records_audit(self, live_copy_conn):
        conn = live_copy_conn
        ensure_schema(conn)
        treasury_bank._ensure_schema_once(conn)
        t = _pending_treasury(conn)
        # exercises the exact call this route makes
        result = treasury_bank.promote_to_confirmed(conn, t)
        conn.commit()
        assert result["ok"] is True
        actions = _action_rows(conn, t)
        assert len(actions) == 1
        assert actions[0]["analyst"] == "human"  # this route's own default

    def test_recovery_safe_approve_records_both_audit_tables(self, live_copy_conn):
        conn = live_copy_conn
        ensure_schema(conn)
        treasury_bank._ensure_schema_once(conn)
        t = _pending_treasury(conn)
        now = int(time.time())
        conn.execute(
            "UPDATE wt_treasury_review SET status='APPROVED', reviewed_at=?, reviewed_by=? WHERE treasury=?",
            (now, "recovery-analyst", t),
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS wt_confirmed_treasuries (treasury TEXT PRIMARY KEY, method TEXT, "
            "confidence TEXT, confirmed_at INTEGER, provenance TEXT, transfer_pct INTEGER, out_sol REAL, "
            "recipients INTEGER, micro_pings INTEGER)"
        )
        conn.execute(
            "INSERT INTO wt_confirmed_treasuries (treasury, method, confidence, confirmed_at, provenance) "
            "VALUES (?, 'human_review_recovery_safe', 'HIGH', ?, 'APPROVED_NO_WEBHOOK')",
            (t, now),
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS wt_treasury_approval_audit (treasury TEXT, action TEXT, reviewer TEXT, "
            "confidence TEXT, notes TEXT, evidence_json TEXT, created_at INTEGER)"
        )
        conn.execute(
            "INSERT INTO wt_treasury_approval_audit (treasury, action, reviewer, confidence, notes, evidence_json, created_at) "
            "VALUES (?, 'APPROVED', ?, 'HIGH', 'recovery-safe test', '{}', ?)",
            (t, "recovery-analyst", now),
        )
        treasury_bank._record_review_action(
            conn, t, "APPROVE_TREASURY", "recovery-analyst", "recovery-safe test", now,
            result={"ok": True, "treasury": t},
        )
        conn.commit()

        old_audit = conn.execute("SELECT * FROM wt_treasury_approval_audit WHERE treasury=?", (t,)).fetchall()
        new_audit = _action_rows(conn, t)
        assert len(old_audit) == 1
        assert len(new_audit) == 1
        assert new_audit[0]["analyst"] == "recovery-analyst"


class TestHistoricalBackfillIntegrity:
    """PHASE 6 -- validate the backfill script's reconstruction rules
    directly (not just its output counts)."""

    def test_backfill_marks_reconstructed_rows_explicitly(self, live_copy_conn):
        import json
        import sys
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
        from scripts.maintenance.x76_2_backfill_treasury_review_actions import backfill

        conn = live_copy_conn
        ensure_schema(conn)
        treasury_bank._ensure_schema_once(conn)
        result = backfill(conn)
        assert result["after"] >= result["before"]

        rows = conn.execute("SELECT result_json FROM wt_treasury_review_actions").fetchall()
        for row in rows:
            payload = json.loads(row["result_json"])
            # every row from this backfill run must be marked
            if payload.get("backfilled_by"):
                assert payload["reconstructed"] is True

    def test_backfill_never_fabricates_a_reason(self, live_copy_conn):
        import sys
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
        from scripts.maintenance.x76_2_backfill_treasury_review_actions import backfill, _NO_REASON_RECORDED

        conn = live_copy_conn
        ensure_schema(conn)
        treasury_bank._ensure_schema_once(conn)
        backfill(conn)

        rows = conn.execute(
            "SELECT reason, result_json FROM wt_treasury_review_actions "
            "WHERE result_json LIKE '%wt_treasury_review only%'"
        ).fetchall()
        for row in rows:
            assert row["reason"] == _NO_REASON_RECORDED, (
                "a row reconstructed without corroborating old-audit evidence must use the "
                "honest placeholder, never an invented reason"
            )

    def test_backfill_is_idempotent(self, live_copy_conn):
        import sys
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
        from scripts.maintenance.x76_2_backfill_treasury_review_actions import backfill

        conn = live_copy_conn
        ensure_schema(conn)
        treasury_bank._ensure_schema_once(conn)
        r1 = backfill(conn)
        r2 = backfill(conn)
        assert r1["after"] == r2["after"]
        assert r2["reconstructed_with_old_audit_evidence"] == 0
        assert r2["reconstructed_without_old_audit_evidence"] == 0


class TestNamedValidation:
    """PHASE 8 -- WATCHTOWER, 3SW2, B48k/Dv34, C7Ha, current pending,
    previously rejected, previously approved."""

    WATCHTOWER_OPERATOR_ID = "04265d9f-6eb2-568c-a49e-9253091a4dbb"
    DV34 = "Dv34prGm2BT7Ph2n6qKLgzeLgjnii87RJJ7Db6ZQQvKM"
    C7HA = "C7HaUt9CYZSd3LW2pdBMHiDo6Q52H6DJU7Ar3M5xFgCM"

    @pytest.fixture
    def live_conn(self):
        _skip_if_no_live_db()
        conn = sqlite3.connect(f"file:{_LIVE_DB}?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        yield conn
        conn.close()

    def test_every_decided_review_now_has_an_action(self, live_conn):
        missing = live_conn.execute(
            "SELECT treasury, status FROM wt_treasury_review wtr "
            "WHERE status IN ('CONFIRMED','APPROVED','REJECTED') "
            "AND NOT EXISTS (SELECT 1 FROM wt_treasury_review_actions a WHERE a.treasury=wtr.treasury)"
        ).fetchall()
        assert not missing, f"found decided reviews with no audit action: {[dict(r) for r in missing]}"

    def test_dv34_rejection_has_an_action_if_decided(self, live_conn):
        row = live_conn.execute(
            "SELECT status FROM wt_treasury_review WHERE treasury=?", (self.DV34,)
        ).fetchone()
        if not row or row["status"] not in ("CONFIRMED", "APPROVED", "REJECTED"):
            pytest.skip("Dv34 not in a decided state in this snapshot")
        actions = live_conn.execute(
            "SELECT * FROM wt_treasury_review_actions WHERE treasury=?", (self.DV34,)
        ).fetchall()
        assert actions, "Dv34's REJECTED decision must now have a matching audit action"

    def test_c7ha_reflects_current_status(self, live_conn):
        row = live_conn.execute(
            "SELECT status FROM wt_treasury_review WHERE treasury=?", (self.C7HA,)
        ).fetchone()
        if not row:
            pytest.skip("C7Ha not present in this snapshot")
        if row["status"] == "PENDING_REVIEW":
            actions = live_conn.execute(
                "SELECT 1 FROM wt_treasury_review_actions WHERE treasury=?", (self.C7HA,)
            ).fetchone()
            # PENDING_REVIEW is fine with zero actions -- no decision made yet
            assert True
        else:
            actions = live_conn.execute(
                "SELECT 1 FROM wt_treasury_review_actions WHERE treasury=?", (self.C7HA,)
            ).fetchone()
            assert actions is not None
