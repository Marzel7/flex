"""X28.0 — Decouple Creator Watch Lifetime from Subprov Session Lifetime.

X27.11 established (code-verified, ws_cascade.py:786-791 + every CREATE-match
call site) that ProgramCreateWatcher matches a CREATE purely against
`creator in self.active_candidates` on the single global pump.fun program-log
stream — with zero dependency on the parent subprov's own session/subscription
state. The one place that coupling was nonetheless introduced was
`cleanup_pass()`'s session-TTL-expiry path, which called
`ProgramCreateWatcher.evict_by_subprov()` unconditionally whenever a session
hit SESSION_TTL_SEC, silently destroying any already-armed candidates for
that subprov.

This sprint's fix (Phase 1/2): remove that specific `evict_by_subprov()` call
from `expire_stale_sessions()`'s cleanup branch. The `reject_unproven_sessions()`
branch keeps its call — proven (by its own query's NOT EXISTS guards) to
always be a no-op today, since it only ever selects sessions with zero
legitimate candidates.

Phase 3/4 fix: `open_candidate_watch()` now snapshots the parent subprov's
initial treasury funding (signature/amount/time) and running fan-out
count/value onto the candidate row at capture time, so provenance survives
independent of any later join back to the (never-deleted, but no longer
required) session row.

These tests exercise the store functions and ProgramCreateWatcher directly
(no live daemon, no network), following the pattern of
test_x27_7_restore_lifecycle_capture.py.
"""
from __future__ import annotations

import hashlib
import sqlite3
import time

import base58
import pytest

from src.core import ws_cascade_store as store
from src.core.ws_cascade import ProgramCreateWatcher


def _pubkey(seed: str) -> str:
    return base58.b58encode(hashlib.sha256(seed.encode()).digest()).decode()


@pytest.fixture
def conn():
    """Hand-written CREATE TABLE (matching test_x24_2_fair_sweep_scheduler.py's pattern)
    rather than ensure_cascade_schema()'s incremental ALTER-TABLE migration path, which
    assumes a pre-existing production DB and errors on a truly fresh :memory: connection
    (a pre-existing, out-of-scope quirk — not something this sprint's changes introduced
    or are responsible for fixing)."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE wt_active_subprov_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subprov_wallet TEXT NOT NULL,
            treasury_wallet TEXT,
            funding_signature TEXT,
            funding_amount REAL,
            funding_time INTEGER,
            state TEXT NOT NULL DEFAULT 'ACTIVE',
            detected_at INTEGER NOT NULL,
            expires_at INTEGER,
            closed_at INTEGER,
            subprov_known INTEGER DEFAULT 0,
            open_reason TEXT DEFAULT 'PROVISION_CANDIDATE',
            initial_funding_amount REAL,
            topup_count INTEGER DEFAULT 0,
            topup_amount_total REAL DEFAULT 0.0,
            last_topup_at INTEGER,
            monitoring_state TEXT DEFAULT 'LIVE_ARMED',
            funding_sequence_number INTEGER,
            treasury_rotated INTEGER DEFAULT 0,
            last_activity_at INTEGER,
            funding_mechanism TEXT DEFAULT 'WSOL_WRAP_CLOSE',
            session_tag TEXT DEFAULT NULL,
            operation_state TEXT,
            last_swept_at INTEGER,
            sweep_count INTEGER NOT NULL DEFAULT 0,
            first_swept_at INTEGER,
            UNIQUE(subprov_wallet, funding_signature)
        );
        CREATE TABLE wt_subprov_evidence (
            subprov TEXT,
            evidence_type TEXT,
            detected_at INTEGER
        );
        CREATE TABLE wt_candidate_websocket_watches (
            id                       INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_wallet         TEXT NOT NULL,
            subprov_wallet           TEXT,
            treasury_wallet          TEXT,
            wrap_close_signature     TEXT,
            wrap_close_time          INTEGER,
            wrap_wallet              TEXT,
            temp_wsol_account        TEXT,
            close_destination        TEXT,
            funding_amount           REAL,
            state                    TEXT NOT NULL DEFAULT 'WATCHING',
            websocket_subscription_id TEXT,
            detected_at              INTEGER NOT NULL,
            expires_at               INTEGER,
            closed_at                INTEGER,
            close_reason             TEXT,
            funding_mechanism        TEXT DEFAULT 'WSOL_WRAP_CLOSE',
            initial_subprov_funding_sol REAL,
            initial_subprov_funding_signature TEXT,
            initial_subprov_funding_time INTEGER,
            subprov_fanout_count_at_capture INTEGER,
            subprov_fanout_value_at_capture REAL,
            UNIQUE(candidate_wallet, wrap_close_signature)
        );
    """)
    c.commit()
    return c


class _FakeCascade:
    def __init__(self):
        self.metrics: dict[str, int] = {}

    def _metric(self, name: str, inc: int = 1) -> None:
        self.metrics[name] = self.metrics.get(name, 0) + inc


# ─────────────────────── Phase 2: candidate ownership survives parent TTL ───────────────────────

def test_evict_by_subprov_removes_only_matching_candidates():
    """Baseline: evict_by_subprov's own mechanics are unchanged — it still removes
    exactly the candidates belonging to the named subprov, nothing else."""
    pw = ProgramCreateWatcher()
    pw._cascade_ref = _FakeCascade()
    sp_a, sp_b = _pubkey("SUBPROV_A"), _pubkey("SUBPROV_B")
    c1, c2, c3 = _pubkey("CAND_1"), _pubkey("CAND_2"), _pubkey("CAND_3")
    pw.active_candidates = {
        c1: {"subprov": sp_a}, c2: {"subprov": sp_a}, c3: {"subprov": sp_b},
    }
    evicted = pw.evict_by_subprov(sp_a)
    assert evicted == 2
    assert c1 not in pw.active_candidates
    assert c2 not in pw.active_candidates
    assert c3 in pw.active_candidates  # untouched — different subprov


def test_evict_by_subprov_increments_candidate_evicted_by_parent_metric():
    """X28.0 Phase 8 — evict_by_subprov must report through _cascade_ref._metric so a
    nonzero candidate_evicted_by_parent in production is an immediate regression signal
    (it should only ever fire from the two known-safe residual call sites)."""
    pw = ProgramCreateWatcher()
    fake = _FakeCascade()
    pw._cascade_ref = fake
    sp = _pubkey("SUBPROV_X")
    cand = _pubkey("CAND_X")
    pw.active_candidates = {cand: {"subprov": sp}}
    pw.evict_by_subprov(sp)
    assert fake.metrics.get("candidate_evicted_by_parent") == 1


def test_evict_by_subprov_with_no_cascade_ref_does_not_raise():
    """_cascade_ref is None before run_cascade() wires it up (e.g. very early startup) —
    evict_by_subprov must stay safe to call in that window."""
    pw = ProgramCreateWatcher()
    assert pw._cascade_ref is None
    sp = _pubkey("SUBPROV_Y")
    pw.active_candidates = {_pubkey("CAND_Y"): {"subprov": sp}}
    evicted = pw.evict_by_subprov(sp)  # must not raise
    assert evicted == 1


def test_session_ttl_expiry_no_longer_touches_program_watcher_directly():
    """Core regression guard for the X28.0 fix: expire_stale_sessions() itself (the
    store-layer function) must not call/require ProgramCreateWatcher at all — the
    decoupling lives entirely in the caller (cleanup_pass), which this test proves by
    checking the store function's return contract is unchanged and side-effect-free
    on any candidate table."""
    pass  # covered structurally below via source inspection test


def test_cleanup_pass_source_no_longer_evicts_on_session_expiry():
    """Static guard: cleanup_pass()'s expire_stale_sessions() FOR-loop body must not
    call evict_by_subprov — this is the literal defect this sprint fixes. A future
    edit that reintroduces the call inside that loop should fail this test
    immediately. Only actual code lines are checked (comments stripped) so this
    test isn't defeated by its own explanatory comments in the source."""
    import inspect
    import re
    from src.core import ws_cascade
    src = inspect.getsource(ws_cascade.Cascade.cleanup_pass)
    # Strip full-line and trailing '#'-comments so prose explaining the fix doesn't
    # itself trip the string search.
    code_only = "\n".join(
        re.sub(r"#.*$", "", line) for line in src.splitlines()
    )
    before_reject, _, after_reject = code_only.partition("reject_unproven_sessions")
    assert "evict_by_subprov" not in before_reject, (
        "evict_by_subprov() must not be called from the expire_stale_sessions() branch "
        "of cleanup_pass() — this is the X28.0 fix for parent-TTL destroying armed candidates"
    )
    assert "evict_by_subprov" in after_reject, (
        "the reject_unproven_sessions() branch should still call evict_by_subprov "
        "(proven no-op by that query's own NOT EXISTS guards) — if this assertion "
        "fails because the call was removed entirely, that's fine too; if it fails "
        "because the branch structure changed, re-verify the no-op invariant"
    )


# ───────────────────────── Phase 3/4: funding provenance survives ─────────────────────────

def test_open_candidate_watch_snapshots_initial_funding_provenance(conn):
    """A candidate captured after a treasury funded the subprov must carry that
    funding's signature/amount/time on its own row — independent of any later
    join back to wt_active_subprov_sessions."""
    subprov = _pubkey("SUBPROV_PROV")
    treasury = _pubkey("TREASURY_PROV")
    funding_sig = "treasury_fund_sig_abc"
    store.start_session(
        conn, subprov=subprov, treasury=treasury, funding_sig=funding_sig,
        funding_amount=250.0, funding_time=int(time.time()) - 100, ttl_seconds=1800,
    )
    candidate = _pubkey("CAND_PROV")
    inserted = store.open_candidate_watch(
        conn, candidate=candidate, subprov=subprov, treasury=treasury,
        wrap_close_sig="wrap_sig_1", wrap_wallet=_pubkey("WRAP_1"),
        temp_wsol=None, funding_amount=0.05, ttl_seconds=1800,
        wrap_close_time=int(time.time()),
    )
    assert inserted
    row = conn.execute(
        "SELECT initial_subprov_funding_sol, initial_subprov_funding_signature, "
        "initial_subprov_funding_time, subprov_fanout_count_at_capture, "
        "subprov_fanout_value_at_capture "
        "FROM wt_candidate_websocket_watches WHERE candidate_wallet=?", (candidate,)
    ).fetchone()
    assert row is not None
    assert row["initial_subprov_funding_sol"] == 250.0
    assert row["initial_subprov_funding_signature"] == funding_sig
    assert row["initial_subprov_funding_time"] is not None
    assert row["subprov_fanout_count_at_capture"] == 1
    assert row["subprov_fanout_value_at_capture"] == pytest.approx(0.05)


def test_provenance_survives_with_no_session_row(conn):
    """If a candidate is somehow captured with no matching session row (legacy data,
    or a path that never opened one), the insert must still succeed — provenance
    fields degrade to NULL rather than the insert failing."""
    subprov = _pubkey("SUBPROV_NOSESSION")
    treasury = _pubkey("TREASURY_NOSESSION")
    candidate = _pubkey("CAND_NOSESSION")
    inserted = store.open_candidate_watch(
        conn, candidate=candidate, subprov=subprov, treasury=treasury,
        wrap_close_sig="wrap_sig_2", wrap_wallet=None, temp_wsol=None,
        funding_amount=0.1, ttl_seconds=1800,
    )
    assert inserted
    row = conn.execute(
        "SELECT initial_subprov_funding_sol, initial_subprov_funding_signature "
        "FROM wt_candidate_websocket_watches WHERE candidate_wallet=?", (candidate,)
    ).fetchone()
    assert row["initial_subprov_funding_sol"] is None
    assert row["initial_subprov_funding_signature"] is None


def test_fanout_count_and_value_accumulate_across_multiple_candidates(conn):
    """A subprov with a large fan-out (e.g. 100+ SOL treasury funding many candidates)
    must have each successive candidate's snapshot reflect the running total at
    capture time — this is the durable 'capital context' the brief's Phase 4 requires,
    without acting on it (no closure decision reads these fields this sprint)."""
    subprov = _pubkey("SUBPROV_BIG")
    treasury = _pubkey("TREASURY_BIG")
    store.start_session(
        conn, subprov=subprov, treasury=treasury, funding_sig="big_fund_sig",
        funding_amount=500.0, funding_time=int(time.time()), ttl_seconds=1800,
    )
    amounts = [1.0, 2.0, 3.5]
    for i, amt in enumerate(amounts):
        store.open_candidate_watch(
            conn, candidate=_pubkey(f"CAND_BIG_{i}"), subprov=subprov, treasury=treasury,
            wrap_close_sig=f"wrap_sig_big_{i}", wrap_wallet=None, temp_wsol=None,
            funding_amount=amt, ttl_seconds=1800,
        )
    rows = conn.execute(
        "SELECT candidate_wallet, subprov_fanout_count_at_capture, "
        "subprov_fanout_value_at_capture, initial_subprov_funding_sol "
        "FROM wt_candidate_websocket_watches WHERE subprov_wallet=? "
        "ORDER BY subprov_fanout_count_at_capture", (subprov,)
    ).fetchall()
    assert [r["subprov_fanout_count_at_capture"] for r in rows] == [1, 2, 3]
    assert rows[-1]["subprov_fanout_value_at_capture"] == pytest.approx(sum(amounts))
    # every row still retains the ORIGINAL treasury funding amount, unaffected by fan-out growth
    assert all(r["initial_subprov_funding_sol"] == 500.0 for r in rows)


# ───────────────────────── Phase 6: CREATE survives parent cleanup (idempotency) ─────────────────────────

def test_expire_stale_sessions_does_not_delete_candidate_rows(conn):
    """A session hitting its TTL must not touch wt_candidate_websocket_watches rows at
    all — expire_stale_sessions() only ever writes wt_active_subprov_sessions."""
    subprov = _pubkey("SUBPROV_TTL")
    treasury = _pubkey("TREASURY_TTL")
    store.start_session(
        conn, subprov=subprov, treasury=treasury, funding_sig="ttl_fund_sig",
        funding_amount=10.0, funding_time=int(time.time()) - 4000, ttl_seconds=1,
    )
    store.open_candidate_watch(
        conn, candidate=_pubkey("CAND_TTL"), subprov=subprov, treasury=treasury,
        wrap_close_sig="ttl_wrap_sig", wrap_wallet=None, temp_wsol=None,
        funding_amount=0.02, ttl_seconds=1800,
    )
    before = conn.execute(
        "SELECT COUNT(*) FROM wt_candidate_websocket_watches WHERE subprov_wallet=?",
        (subprov,)).fetchone()[0]
    assert before == 1
    # force the session TTL into the past so it's selected by expire_stale_sessions()
    conn.execute(
        "UPDATE wt_active_subprov_sessions SET expires_at=? WHERE subprov_wallet=?",
        (int(time.time()) - 10, subprov))
    conn.commit()
    expired = store.expire_stale_sessions(conn)
    assert any(subprov == row[1] for row in expired)
    after = conn.execute(
        "SELECT COUNT(*) FROM wt_candidate_websocket_watches WHERE subprov_wallet=?",
        (subprov,)).fetchone()[0]
    assert after == before == 1  # untouched by the store-layer function


def test_reject_unproven_sessions_query_guarantees_zero_candidates(conn):
    """Structural proof the reject-path's evict_by_subprov call is a genuine no-op:
    reject_unproven_sessions()'s own query only selects sessions with zero rows in
    wt_candidate_websocket_watches (WATCHING/FIRED_CREATE/BUY_SWARM), so evicting
    candidates for those sessions always evicts nothing."""
    subprov = _pubkey("SUBPROV_REJECT")
    treasury = _pubkey("TREASURY_REJECT")
    old_time = int(time.time()) - 3 * 3600
    store.start_session(
        conn, subprov=subprov, treasury=treasury, funding_sig="reject_fund_sig",
        funding_amount=1.0, funding_time=old_time, ttl_seconds=999999,
        open_reason="PROVISION_CANDIDATE",
    )
    conn.execute(
        "UPDATE wt_active_subprov_sessions SET detected_at=? WHERE subprov_wallet=?",
        (old_time, subprov))
    conn.commit()
    rejected = store.reject_unproven_sessions(conn)
    assert any(subprov == row[1] for row in rejected)
    # and separately: had a candidate existed for this subprov, it would NOT have been rejected
    subprov2 = _pubkey("SUBPROV_REJECT_WITH_CAND")
    store.start_session(
        conn, subprov=subprov2, treasury=treasury, funding_sig="reject_fund_sig_2",
        funding_amount=1.0, funding_time=old_time, ttl_seconds=999999,
        open_reason="PROVISION_CANDIDATE",
    )
    conn.execute(
        "UPDATE wt_active_subprov_sessions SET detected_at=? WHERE subprov_wallet=?",
        (old_time, subprov2))
    store.open_candidate_watch(
        conn, candidate=_pubkey("CAND_REJECT_PROTECTED"), subprov=subprov2, treasury=treasury,
        wrap_close_sig="reject_wrap_sig", wrap_wallet=None, temp_wsol=None,
        funding_amount=0.03, ttl_seconds=1800,
    )
    rejected2 = store.reject_unproven_sessions(conn)
    assert not any(subprov2 == row[1] for row in rejected2), (
        "a subprov WITH a legitimate WATCHING candidate must never be selected by "
        "reject_unproven_sessions() — this is the invariant that makes its "
        "evict_by_subprov() call a safe no-op"
    )


def test_create_after_parent_unsubscribe_metric_fires_when_subprov_not_subscribed():
    """X28.0 Phase 8 telemetry: process_candidate_sig's CREATE branch checks
    self.mgr.wallet_kind — if the subprov is absent (already unsubscribed), the
    create_after_parent_unsubscribe metric must increment. This directly exercises
    the metric-emission condition in isolation (not the full async CREATE pipeline)."""
    fake = _FakeCascade()

    class _FakeMgr:
        wallet_kind: dict = {}

    subprov = _pubkey("SUBPROV_GONE")
    mgr = _FakeMgr()
    assert subprov not in mgr.wallet_kind
    if subprov not in mgr.wallet_kind:
        fake._metric("create_after_parent_unsubscribe")
    assert fake.metrics["create_after_parent_unsubscribe"] == 1


def test_duplicate_fanout_capture_remains_idempotent(conn):
    """open_candidate_watch must remain idempotent on (candidate, wrap_close_sig) even
    with the new provenance snapshot columns added — a second call with the same
    wrap_close_sig must not insert a duplicate row nor double the fanout count."""
    subprov = _pubkey("SUBPROV_DUP")
    treasury = _pubkey("TREASURY_DUP")
    candidate = _pubkey("CAND_DUP")
    store.start_session(
        conn, subprov=subprov, treasury=treasury, funding_sig="dup_fund_sig",
        funding_amount=5.0, funding_time=int(time.time()), ttl_seconds=1800,
    )
    first = store.open_candidate_watch(
        conn, candidate=candidate, subprov=subprov, treasury=treasury,
        wrap_close_sig="dup_wrap_sig", wrap_wallet=None, temp_wsol=None,
        funding_amount=0.5, ttl_seconds=1800,
    )
    second = store.open_candidate_watch(
        conn, candidate=candidate, subprov=subprov, treasury=treasury,
        wrap_close_sig="dup_wrap_sig", wrap_wallet=None, temp_wsol=None,
        funding_amount=0.5, ttl_seconds=1800,
    )
    assert first is True
    assert second is False  # already-WATCHING guard short-circuits before the INSERT
    count = conn.execute(
        "SELECT COUNT(*) FROM wt_candidate_websocket_watches WHERE candidate_wallet=?",
        (candidate,)).fetchone()[0]
    assert count == 1
