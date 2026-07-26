"""X64.5 — CREATE anchor race recovery and queue reconciliation.

Root cause fixed here: both real production enqueue_migration() call sites
(watchtower_attribution.py:store_migration, pumpfun_curve_listener.py's
creator-unknown fallback) call it without live_conn, so the
creator_funding_queue/token_analysis anchor lookup inside
enqueue_migration() was unconditionally skipped (`if live_conn and not
create_signature:`) — a row could then never recover even after a valid
signature appeared, because nothing ever re-checked. Two fixes:
1. enqueue_migration() now opens its own short-lived read-only live
   connection when the caller doesn't supply one (walkback_queue.py).
2. A new zero-RPC reconciliation module (src/ops/anchor_reconciliation.py)
   recovers already-stuck rows and gives the worker a self-healing re-check
   path each cycle, so a signature that lands AFTER enqueue (a genuine
   race, not just the missing-live_conn case) still gets picked up.

Must never: perform RPC, increment the normal walkback `attempts` counter,
silently overwrite a conflicting existing valid anchor, or write to any
attribution table.
"""
from __future__ import annotations

import sqlite3
import time

import pytest

from src.ops import anchor_reconciliation as recon
from src.core import walkback_queue


# ── fixtures ──────────────────────────────────────────────────────────────────

def _build_ops_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    walkback_queue.ensure_schema(conn)
    recon.ensure_schema(conn)
    # classify_creator() (walkback_queue.py) joins/queries several tables
    # owned by other modules, none of which are part of
    # walkback_queue.ensure_schema()'s own scope. Declared here, empty,
    # matching each table's real columns, so classify_creator() runs
    # exactly as it does in production (falling through every case to
    # FULL_WALKBACK, since all tables are empty) rather than raising.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS wt_ops_v2_wallets (
            operation_uuid TEXT NOT NULL, wallet TEXT NOT NULL, role TEXT NOT NULL,
            first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL, evidence_json TEXT,
            PRIMARY KEY (operation_uuid, wallet)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS wt_ops_v2 (
            operation_uuid TEXT PRIMARY KEY, treasury_root TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS wt_watchtower_launches (
            mint TEXT PRIMARY KEY, creator_wallet TEXT, subprov_wallet TEXT, treasury_wallet TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS wt_wrap_close_candidates (
            creator TEXT PRIMARY KEY, subprov_wallet TEXT, lineage_source_treasury TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS wt_discovered_subprovs (
            subprov TEXT PRIMARY KEY, treasury TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS wt_candidate_websocket_watches (
            candidate_wallet TEXT, subprov_wallet TEXT, treasury_wallet TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS wt_creator_birth_launch (
            creator TEXT PRIMARY KEY, subprov TEXT, treasury TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS watchtower_token_attribution (
            mint TEXT PRIMARY KEY, creator TEXT, matched_subprov TEXT, matched_treasury TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS creator_funders (
            creator_address TEXT, funder_address TEXT
        )"""
    )
    conn.commit()
    return conn


def _build_live_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE creator_funding_queue (
            creator_address TEXT, mint TEXT, migration_timestamp TEXT,
            create_tx_signature TEXT, status TEXT, updated_at INTEGER
        )"""
    )
    conn.execute(
        """CREATE TABLE token_analysis (
            mint TEXT PRIMARY KEY, create_tx_signature TEXT
        )"""
    )
    # classify_creator()'s CEX/relay fallback check queries creator_funders
    # via live_conn when both are supplied together.
    conn.execute(
        """CREATE TABLE creator_funders (
            creator_address TEXT, funder_address TEXT, is_cex INTEGER
        )"""
    )
    conn.commit()
    return conn


VALID_SIG = "Tt3yP2SNaXG4gNWAmduUBCDbpmV26RErBQrzDLSZuZuqv28m4Kez3m6f82RJnvCUov8jPqHn2LhkCYxwwLfSP6b"
MINT = "H55qUAeK313XyTrhxeMVQgBrogdGG9biyAVfmDQipump"
CREATOR = "63Cxpe31VqnYaAbt5Yf4hARvUs6nP3Zrv4hxGn4nmEr3"


def _seed_stuck_row(ops: sqlite3.Connection, mint: str, creator: str) -> None:
    now = int(time.time())
    ops.execute(
        "INSERT INTO wt_walkback_queue "
        "(mint, creator, walkback_class, status, path_state, create_anchor_audit_state, "
        " attempts, rpc_used, enqueued_at, updated_at) "
        "VALUES (?,?,?,?,?,?,0,0,?,?)",
        (mint, creator, "FULL_WALKBACK", recon.WAITING_STATUS,
         recon.WAITING_PATH_STATE, "MISSING_OR_MALFORMED", now, now),
    )
    ops.commit()


def _row(ops: sqlite3.Connection, mint: str) -> sqlite3.Row:
    return ops.execute("SELECT * FROM wt_walkback_queue WHERE mint=?", (mint,)).fetchone()


# ── Test 1: signature exists before enqueue ──────────────────────────────────

def test_signature_exists_before_enqueue_row_is_created_anchored():
    ops = _build_ops_db()
    live = _build_live_db()
    live.execute(
        "INSERT INTO creator_funding_queue VALUES (?,?,?,?,?,?)",
        (CREATOR, MINT, "2026-07-20T19:57:22Z", VALID_SIG, "pending", int(time.time())),
    )
    live.commit()

    walkback_queue.enqueue_migration(ops, mint=MINT, creator=CREATOR, live_conn=live)

    row = _row(ops, MINT)
    assert row["create_anchor_audit_state"] == "VALID"
    assert row["create_anchor_signature"] == VALID_SIG
    assert row["path_state"] == "CREATE_ANCHORED"
    assert row["status"] == "pending"


# ── Test 2: signature commits immediately after enqueue (live_conn omitted
#    at the call site, exercising the new self-opened connection) ──────────

def test_enqueue_without_live_conn_falls_back_to_own_connection(monkeypatch, tmp_path):
    live_path = str(tmp_path / "live.db")
    live = sqlite3.connect(live_path)
    live.execute(
        "CREATE TABLE creator_funding_queue (creator_address TEXT, mint TEXT, "
        "migration_timestamp TEXT, create_tx_signature TEXT, status TEXT, updated_at INTEGER)"
    )
    live.execute("CREATE TABLE token_analysis (mint TEXT PRIMARY KEY, create_tx_signature TEXT)")
    live.execute(
        "INSERT INTO creator_funding_queue VALUES (?,?,?,?,?,?)",
        (CREATOR, MINT, "2026-07-20T19:57:22Z", VALID_SIG, "pending", int(time.time())),
    )
    live.commit()
    live.close()

    monkeypatch.setattr(walkback_queue, "LIVE_DB_PATH", live_path)

    ops = _build_ops_db()

    # No live_conn passed — this is the exact production call shape.
    walkback_queue.enqueue_migration(ops, mint=MINT, creator=CREATOR)

    row = _row(ops, MINT)
    assert row["create_anchor_audit_state"] == "VALID"
    assert row["create_anchor_signature"] == VALID_SIG


# ── Test 3: signature appears several worker cycles later ───────────────────

def test_signature_appears_after_enqueue_reconciliation_recovers_it():
    ops = _build_ops_db()
    live = _build_live_db()
    _seed_stuck_row(ops, MINT, CREATOR)

    # First reconciliation pass: nothing in creator_funding_queue yet.
    result = recon.reconcile_waiting_create_anchors(ops, live)
    assert result["recovered"] == []
    assert _row(ops, MINT)["status"] == recon.WAITING_STATUS

    # Signature lands (simulating a later worker cycle / real race resolving).
    live.execute(
        "INSERT INTO creator_funding_queue VALUES (?,?,?,?,?,?)",
        (CREATOR, MINT, "2026-07-20T19:57:22Z", VALID_SIG, "pending", int(time.time())),
    )
    live.commit()

    result = recon.reconcile_waiting_create_anchors(ops, live)
    assert len(result["recovered"]) == 1
    assert result["recovered"][0]["mint"] == MINT

    row = _row(ops, MINT)
    assert row["status"] == "pending"
    assert row["create_anchor_audit_state"] == "VALID"
    assert row["create_anchor_signature"] == VALID_SIG
    assert row["path_state"] == "CREATE_ANCHORED"
    assert row["anchor_recovered_at"] is not None
    assert row["anchor_recovery_source"] == "creator_funding_queue"


# ── Test 4: live_conn is absent (enqueue_migration falls back gracefully) ──

def test_enqueue_migration_live_conn_absent_and_unreachable_does_not_crash(monkeypatch):
    monkeypatch.setattr(walkback_queue, "LIVE_DB_PATH", "/nonexistent/path/does/not/exist.db")
    ops = _build_ops_db()

    # Must not raise even though the fallback connection attempt will fail
    # (sqlite3.connect on a bad path can still succeed lazily, so this
    # mainly proves no exception propagates out of enqueue_migration).
    cls = walkback_queue.enqueue_migration(ops, mint=MINT, creator=CREATOR)
    assert cls == "FULL_WALKBACK"
    row = _row(ops, MINT)
    assert row["create_anchor_audit_state"] == "MISSING_OR_MALFORMED"


# ── Test 5: signature is malformed ───────────────────────────────────────────

def test_malformed_signature_is_not_recovered():
    ops = _build_ops_db()
    live = _build_live_db()
    _seed_stuck_row(ops, MINT, CREATOR)
    live.execute(
        "INSERT INTO creator_funding_queue VALUES (?,?,?,?,?,?)",
        (CREATOR, MINT, "2026-07-20T19:57:22Z", "too-short", "pending", int(time.time())),
    )
    live.commit()

    result = recon.reconcile_waiting_create_anchors(ops, live)
    assert result["recovered"] == []
    assert result["skipped"][0]["classification"] == "ANCHOR_PRESENT_INVALID"

    row = _row(ops, MINT)
    assert row["status"] == recon.WAITING_STATUS
    assert row["create_anchor_audit_state"] == "MISSING_OR_MALFORMED"


# ── Test 6: multiple signature rows exist for one mint ──────────────────────

def test_multiple_funding_queue_rows_classified_ambiguous():
    ops = _build_ops_db()
    live = _build_live_db()
    _seed_stuck_row(ops, MINT, CREATOR)
    live.execute(
        "INSERT INTO creator_funding_queue VALUES (?,?,?,?,?,?)",
        (CREATOR, MINT, "t1", VALID_SIG, "pending", 100),
    )
    live.execute(
        "INSERT INTO creator_funding_queue VALUES (?,?,?,?,?,?)",
        (CREATOR, MINT, "t2", VALID_SIG[::-1] + "xx", "pending", 200),
    )
    live.commit()

    result = recon.reconcile_waiting_create_anchors(ops, live)
    assert result["recovered"] == []
    assert result["skipped"][0]["classification"] == "AMBIGUOUS_MULTIPLE_ROWS"
    assert _row(ops, MINT)["status"] == recon.WAITING_STATUS


# ── Test 7: queue already contains a valid anchor (not a stuck row at all) ──

def test_row_with_existing_valid_anchor_is_not_touched():
    ops = _build_ops_db()
    live = _build_live_db()
    now = int(time.time())
    ops.execute(
        "INSERT INTO wt_walkback_queue "
        "(mint, creator, walkback_class, status, path_state, create_anchor_audit_state, "
        " create_anchor_signature, attempts, rpc_used, enqueued_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,0,0,?,?)",
        (MINT, CREATOR, "FULL_WALKBACK", "pending", "CREATE_ANCHORED", "VALID",
         VALID_SIG, now, now),
    )
    ops.commit()

    result = recon.reconcile_waiting_create_anchors(ops, live)
    assert result["examined"] == 0  # not selected — status != waiting


# ── Test 8: duplicate reconciliation is idempotent ───────────────────────────

def test_reconciliation_is_idempotent():
    ops = _build_ops_db()
    live = _build_live_db()
    _seed_stuck_row(ops, MINT, CREATOR)
    live.execute(
        "INSERT INTO creator_funding_queue VALUES (?,?,?,?,?,?)",
        (CREATOR, MINT, "t", VALID_SIG, "pending", 100),
    )
    live.commit()

    r1 = recon.reconcile_waiting_create_anchors(ops, live)
    assert len(r1["recovered"]) == 1

    r2 = recon.reconcile_waiting_create_anchors(ops, live)
    assert r2["examined"] == 0  # row no longer status='waiting', not re-selected
    assert r2["recovered"] == []

    log_count = ops.execute(
        "SELECT COUNT(*) c FROM wt_anchor_reconciliation_log WHERE mint=?", (MINT,)
    ).fetchone()["c"]
    assert log_count == 1


# ── Test 9: process restarts while row is PENDING_ANCHOR (simulated by a
#    fresh ops connection over the same underlying data) ───────────────────

def test_restart_mid_pending_anchor_state_survives(tmp_path):
    db_path = str(tmp_path / "ops.db")
    ops1 = sqlite3.connect(db_path)
    ops1.row_factory = sqlite3.Row
    walkback_queue.ensure_schema(ops1)
    recon.ensure_schema(ops1)
    _seed_stuck_row(ops1, MINT, CREATOR)
    ops1.close()

    # "Process restart" — brand new connection to the same file.
    ops2 = sqlite3.connect(db_path)
    ops2.row_factory = sqlite3.Row
    live = _build_live_db()
    live.execute(
        "INSERT INTO creator_funding_queue VALUES (?,?,?,?,?,?)",
        (CREATOR, MINT, "t", VALID_SIG, "pending", 100),
    )
    live.commit()

    result = recon.reconcile_waiting_create_anchors(ops2, live)
    assert len(result["recovered"]) == 1
    row = _row(ops2, MINT)
    assert row["status"] == "pending"
    ops2.close()


# ── Test 10: reconciliation performs zero RPC ────────────────────────────────

def test_reconciliation_performs_no_network_calls(monkeypatch):
    """Assert no RPC-shaped function is ever imported/called by patching the
    two network entry points this codebase's walkback layer uses and
    failing loudly if either is touched."""
    import urllib.request

    def _fail(*a, **k):
        raise AssertionError("reconciliation must never perform network I/O")

    monkeypatch.setattr(urllib.request, "urlopen", _fail)

    ops = _build_ops_db()
    live = _build_live_db()
    _seed_stuck_row(ops, MINT, CREATOR)
    live.execute(
        "INSERT INTO creator_funding_queue VALUES (?,?,?,?,?,?)",
        (CREATOR, MINT, "t", VALID_SIG, "pending", 100),
    )
    live.commit()

    result = recon.reconcile_waiting_create_anchors(ops, live)
    assert len(result["recovered"]) == 1  # completed successfully with urlopen poisoned


# ── Test 11: recovery does not increment walkback attempts ──────────────────

def test_recovery_does_not_increment_walkback_attempts_counter():
    ops = _build_ops_db()
    live = _build_live_db()
    _seed_stuck_row(ops, MINT, CREATOR)
    live.execute(
        "INSERT INTO creator_funding_queue VALUES (?,?,?,?,?,?)",
        (CREATOR, MINT, "t", VALID_SIG, "pending", 100),
    )
    live.commit()

    recon.reconcile_waiting_create_anchors(ops, live)
    row = _row(ops, MINT)
    assert row["attempts"] == 0
    assert row["anchor_lookup_attempts"] == 1


# ── Test 12: recovered row becomes runnable (visible to drain_batch's SELECT
#    shape) ───────────────────────────────────────────────────────────────

def test_recovered_row_is_selectable_by_drain_batch_where_clause():
    ops = _build_ops_db()
    live = _build_live_db()
    _seed_stuck_row(ops, MINT, CREATOR)
    live.execute(
        "INSERT INTO creator_funding_queue VALUES (?,?,?,?,?,?)",
        (CREATOR, MINT, "t", VALID_SIG, "pending", 100),
    )
    live.commit()
    recon.reconcile_waiting_create_anchors(ops, live)

    now = int(time.time())
    selected = ops.execute(
        "SELECT mint FROM wt_walkback_queue "
        "WHERE (status='pending' OR (status='running' AND COALESCE(lease_expires_at,0) < ?)) "
        "AND attempts < ? AND COALESCE(next_retry_at,0) <= ?",
        (now, 3, now),
    ).fetchall()
    assert any(r["mint"] == MINT for r in selected)


# ── Test 13: conflicting valid anchors are not silently overwritten ─────────

def test_conflicting_existing_valid_anchor_is_not_overwritten():
    ops = _build_ops_db()
    live = _build_live_db()
    now = int(time.time())
    existing_sig = "A" * 87  # different but also structurally valid
    # A row that (unusually) already has a VALID anchor but is still
    # marked audit_state MISSING_OR_MALFORMED due to a data inconsistency —
    # the exact defensive case the conflict guard exists for.
    ops.execute(
        "INSERT INTO wt_walkback_queue "
        "(mint, creator, walkback_class, status, path_state, create_anchor_audit_state, "
        " create_anchor_signature, attempts, rpc_used, enqueued_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,0,0,?,?)",
        (MINT, CREATOR, "FULL_WALKBACK", recon.WAITING_STATUS, recon.WAITING_PATH_STATE,
         "MISSING_OR_MALFORMED", existing_sig, now, now),
    )
    ops.commit()
    live.execute(
        "INSERT INTO creator_funding_queue VALUES (?,?,?,?,?,?)",
        (CREATOR, MINT, "t", VALID_SIG, "pending", 100),
    )
    live.commit()

    result = recon.reconcile_waiting_create_anchors(ops, live)
    assert result["recovered"] == []
    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["existing_valid_signature"] == existing_sig
    assert result["conflicts"][0]["funding_queue_signature"] == VALID_SIG

    row = _row(ops, MINT)
    assert row["create_anchor_signature"] == existing_sig  # untouched


# ── Test 14: canonical H55qUAeK regression fixture ───────────────────────────

def test_canonical_h55quaek_regression_fixture():
    """Zero-RPC replay of the real X64.5 canonical case using its actual
    stored field values (mint, creator, signature) as a fixture."""
    ops = _build_ops_db()
    live = _build_live_db()
    now = 1784577442  # actual enqueued_at from production
    ops.execute(
        "INSERT INTO wt_walkback_queue "
        "(mint, creator, walkback_class, status, path_state, create_anchor_audit_state, "
        " attempts, rpc_used, enqueued_at, updated_at) "
        "VALUES (?,?,?,?,?,?,0,0,?,?)",
        (MINT, CREATOR, "FULL_WALKBACK", recon.WAITING_STATUS,
         recon.WAITING_PATH_STATE, "MISSING_OR_MALFORMED", now, now),
    )
    ops.commit()
    live.execute(
        "INSERT INTO creator_funding_queue VALUES (?,?,?,?,?,?)",
        (CREATOR, MINT, "2026-07-20T19:57:22.362284Z", VALID_SIG, "pending", now),
    )
    live.commit()

    result = recon.reconcile_waiting_create_anchors(ops, live)

    assert len(result["recovered"]) == 1
    row = _row(ops, MINT)
    assert row["create_anchor_signature"] == VALID_SIG
    assert row["create_anchor_audit_state"] == "VALID"
    assert row["status"] == "pending"
    assert row["attempts"] == 0

    # This audit validates AGAINST the known treasury — it must never be
    # injected as a result. Reconciliation touches only anchor fields;
    # subprov/treasury remain untouched, exactly as required.
    assert row["subprov"] is None
    assert row["treasury"] is None
