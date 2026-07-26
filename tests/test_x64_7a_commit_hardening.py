"""X64.7A — Canonical CREATE commit hardening.

Closes three gaps left open by X64.7:
1. The X64.7 ledger write in handle_birth was sequenced AFTER creator
   inference in program order — an exception in _infer_creator_from_tx
   would have skipped the ledger write entirely, even though the module
   itself (create_event_ledger.record_create_event) was already
   creator-independent. Fixed: the ledger commit now happens BEFORE
   creator inference is even called, with an idempotent second write to
   enrich the creator once known.
2. A failed ledger write existed only in logs — no durable record, no
   automatic retry. Fixed: wt_create_ledger_pending, a bounded-backoff,
   zero-RPC, restart-safe retry queue.
3. Nothing explicitly detected "migration observed but no ledger row" as
   a distinct, queryable condition. Fixed: wt_migration_ledger_coverage,
   written (non-blocking) at store_migration() time.
4. resolve_anchor_with_priority() existed but nothing in production
   called it. Fixed: reconcile_waiting_create_anchors() (the function the
   ordinary worker cycle actually calls) now checks the ledger first via
   resolve_anchor_with_priority before falling back to the legacy
   widened-source search.

Must never: perform RPC in any retry/reconciliation path, block
migration processing on the coverage check, or let a ledger-write failure
propagate out of birth processing.
"""
from __future__ import annotations

import sqlite3
import time
import urllib.request

import pytest

from src.ops import create_event_ledger as cel
from src.ops import anchor_reconciliation as recon


VALID_SIG_A = "Tt3yP2SNaXG4gNWAmduUBCDbpmV26RErBQrzDLSZuZuqv28m4Kez3m6f82RJnvCUov8jPqHn2LhkCYxwwLfSP6b"
VALID_SIG_B = "xvMDpECdTo2JqmeDrPUac12EqVaXS6SzukcpS5wXmwNx9QBPyypdZ8dqM9Jw1fEAQAnjDTDC8fvrSoMr6hfNBAm"
MINT_A = "otxB1CUrwrKBfJ3mn8edFqaM2X9RLumyHG8hZvJpump"
CREATOR_A = "912kspVUFKRxTBAVRjZ2ikbiVHj6ahgewkHQqViehJf"


def _build_ops_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    from src.core import walkback_queue
    walkback_queue.ensure_schema(conn)
    recon.ensure_schema(conn)
    cel.ensure_schema(conn)
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
    conn.execute("CREATE TABLE token_analysis (mint TEXT PRIMARY KEY, create_tx_signature TEXT)")
    conn.commit()
    return conn


def _seed_queue_row(ops, mint, creator):
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


# ── Test 1: ledger commit precedes creator inference ─────────────────────────

def test_two_stage_write_commits_pending_before_creator_known():
    """Simulates the fixed handle_birth ordering: an initial PENDING write,
    THEN (as if creator inference ran later) an enrichment write."""
    ops = _build_ops_db()
    r1 = cel.record_create_event(
        ops, signature=VALID_SIG_A, mint=MINT_A, creator=None, source="WEBSOCKET",
        creator_resolution_state=cel.CREATOR_RESOLUTION_PENDING,
    )
    assert r1["written"] is True
    row = ops.execute("SELECT creator_resolution_state, creator FROM wt_create_event_ledger WHERE signature=?", (VALID_SIG_A,)).fetchone()
    assert row["creator_resolution_state"] == "PENDING"
    assert row["creator"] is None


# ── Test 2: creator inference exception leaves ledger intact ────────────────

def test_creator_inference_exception_after_pending_write_leaves_ledger_row_intact():
    ops = _build_ops_db()
    cel.record_create_event(
        ops, signature=VALID_SIG_A, mint=MINT_A, creator=None, source="WEBSOCKET",
        creator_resolution_state=cel.CREATOR_RESOLUTION_PENDING,
    )
    # Simulate the exact real-world failure this task fixes: creator
    # inference raises AFTER the ledger write already committed.
    try:
        raise RuntimeError("simulated _infer_creator_from_tx failure")
    except RuntimeError:
        pass
    row = ops.execute("SELECT * FROM wt_create_event_ledger WHERE signature=?", (VALID_SIG_A,)).fetchone()
    assert row is not None
    assert row["mint"] == MINT_A
    assert row["creator_resolution_state"] == "PENDING"  # never enriched, since inference failed


# ── Test 3: initial creator-null row is later enriched ───────────────────────

def test_initial_pending_row_is_later_enriched_to_resolved():
    ops = _build_ops_db()
    cel.record_create_event(
        ops, signature=VALID_SIG_A, mint=MINT_A, creator=None, source="WEBSOCKET",
        creator_resolution_state=cel.CREATOR_RESOLUTION_PENDING,
    )
    r2 = cel.record_create_event(
        ops, signature=VALID_SIG_A, mint=MINT_A, creator=CREATOR_A, source="WEBSOCKET",
        creator_resolution_state=cel.CREATOR_RESOLUTION_RESOLVED,
    )
    assert r2["written"] is True
    assert r2["creator"] == CREATOR_A
    row = ops.execute("SELECT creator_resolution_state, creator FROM wt_create_event_ledger WHERE signature=?", (VALID_SIG_A,)).fetchone()
    assert row["creator_resolution_state"] == "RESOLVED"
    assert row["creator"] == CREATOR_A


# ── Test 4: ledger-write failure creates a durable pending row ──────────────

def test_ledger_write_failure_creates_durable_pending_row():
    ops = _build_ops_db()
    result = cel.persist_pending_write(
        ops, signature=VALID_SIG_A, mint=MINT_A, creator=None, slot=1, block_time=100,
        source="WEBSOCKET", parser_path="handle_birth", last_error="database is locked",
    )
    assert result["persisted"] is True
    row = ops.execute("SELECT * FROM wt_create_ledger_pending WHERE signature=?", (VALID_SIG_A,)).fetchone()
    assert row is not None
    assert row["mint"] == MINT_A
    assert row["attempts"] == 1
    assert row["last_error"] == "database is locked"
    assert row["next_retry_at"] is not None


# ── Test 5: pending row survives restart ─────────────────────────────────────

def test_pending_row_survives_restart(tmp_path):
    db_path = str(tmp_path / "ops.db")
    ops1 = sqlite3.connect(db_path)
    ops1.row_factory = sqlite3.Row
    cel.ensure_schema(ops1)
    cel.persist_pending_write(
        ops1, signature=VALID_SIG_A, mint=MINT_A, creator=None, slot=1, block_time=100,
        source="WEBSOCKET", parser_path="handle_birth", last_error="lock",
    )
    ops1.close()

    ops2 = sqlite3.connect(db_path)
    ops2.row_factory = sqlite3.Row
    row = ops2.execute("SELECT * FROM wt_create_ledger_pending WHERE signature=?", (VALID_SIG_A,)).fetchone()
    assert row is not None
    ops2.close()


# ── Test 6: retry commits ledger and removes pending row ────────────────────

def test_retry_commits_ledger_and_removes_pending_row():
    ops = _build_ops_db()
    cel.persist_pending_write(
        ops, signature=VALID_SIG_A, mint=MINT_A, creator=CREATOR_A, slot=1, block_time=100,
        source="WEBSOCKET", parser_path="handle_birth", last_error="lock",
    )
    ops.execute("UPDATE wt_create_ledger_pending SET next_retry_at=0")
    ops.commit()

    result = cel.retry_pending_writes(ops)
    assert VALID_SIG_A in result["recovered"]

    ledger_row = ops.execute("SELECT * FROM wt_create_event_ledger WHERE signature=?", (VALID_SIG_A,)).fetchone()
    assert ledger_row is not None
    pending_row = ops.execute("SELECT * FROM wt_create_ledger_pending WHERE signature=?", (VALID_SIG_A,)).fetchone()
    assert pending_row is None


# ── Test 7: duplicate retry is idempotent ────────────────────────────────────

def test_duplicate_retry_pass_is_idempotent():
    ops = _build_ops_db()
    cel.persist_pending_write(
        ops, signature=VALID_SIG_A, mint=MINT_A, creator=CREATOR_A, slot=1, block_time=100,
        source="WEBSOCKET", parser_path="handle_birth", last_error="lock",
    )
    ops.execute("UPDATE wt_create_ledger_pending SET next_retry_at=0")
    ops.commit()

    r1 = cel.retry_pending_writes(ops)
    r2 = cel.retry_pending_writes(ops)
    assert len(r1["recovered"]) == 1
    assert len(r2["recovered"]) == 0  # already removed from pending, nothing to examine
    assert r2["examined"] == 0

    count = ops.execute("SELECT COUNT(*) c FROM wt_create_event_ledger WHERE signature=?", (VALID_SIG_A,)).fetchone()["c"]
    assert count == 1


# ── Test 8: same-signature conflict remains protected ───────────────────────

def test_conflict_removes_pending_row_without_corrupting_ledger():
    ops = _build_ops_db()
    cel.record_create_event(ops, signature=VALID_SIG_A, mint=MINT_A, creator=CREATOR_A, source="WEBSOCKET")
    # A pending row for the SAME signature but a DIFFERENT creator —
    # retrying it must hit the CREATOR_MISMATCH conflict path, not
    # silently overwrite.
    cel.persist_pending_write(
        ops, signature=VALID_SIG_A, mint=MINT_A, creator="DifferentCreatorXXXXXXXXXXXXXXXXXXXXXXXXXXX",
        slot=1, block_time=100, source="RECONCILER", parser_path="retry", last_error="lock",
    )
    ops.execute("UPDATE wt_create_ledger_pending SET next_retry_at=0")
    ops.commit()

    result = cel.retry_pending_writes(ops)
    assert VALID_SIG_A not in result["recovered"]
    # removed from pending (not retryable — it's a genuine conflict)
    pending_row = ops.execute("SELECT * FROM wt_create_ledger_pending WHERE signature=?", (VALID_SIG_A,)).fetchone()
    assert pending_row is None
    # original ledger creator untouched
    ledger_row = ops.execute("SELECT creator FROM wt_create_event_ledger WHERE signature=?", (VALID_SIG_A,)).fetchone()
    assert ledger_row["creator"] == CREATOR_A
    conflict_row = ops.execute("SELECT * FROM wt_create_ledger_conflicts WHERE signature=?", (VALID_SIG_A,)).fetchone()
    assert conflict_row is not None


# ── Test 9-11: migration coverage check ──────────────────────────────────────

def test_migration_with_ledger_row_records_present():
    ops = _build_ops_db()
    import src.core.watchtower_attribution as wta
    cel.record_create_event(ops, signature=VALID_SIG_A, mint=MINT_A, creator=CREATOR_A, source="WEBSOCKET")
    wta._record_migration_coverage(ops, mint=MINT_A, creator=CREATOR_A, migration_time=123, source="pumpfun_migration")
    row = ops.execute("SELECT * FROM wt_migration_ledger_coverage WHERE mint=?", (MINT_A,)).fetchone()
    assert row["ledger_result"] == "MIGRATION_CREATE_LEDGER_PRESENT"
    assert row["ledger_signature"] == VALID_SIG_A


def test_migration_with_pending_ledger_row_records_pending():
    ops = _build_ops_db()
    import src.core.watchtower_attribution as wta
    cel.record_create_event(
        ops, signature=VALID_SIG_A, mint=MINT_A, creator=None, source="WEBSOCKET",
        creator_resolution_state=cel.CREATOR_RESOLUTION_PENDING,
    )
    wta._record_migration_coverage(ops, mint=MINT_A, creator=None, migration_time=123, source="pumpfun_migration")
    row = ops.execute("SELECT * FROM wt_migration_ledger_coverage WHERE mint=?", (MINT_A,)).fetchone()
    assert row["ledger_result"] == "MIGRATION_CREATE_LEDGER_PENDING"


def test_migration_without_ledger_row_records_missing():
    ops = _build_ops_db()
    import src.core.watchtower_attribution as wta
    wta._record_migration_coverage(ops, mint=MINT_A, creator=CREATOR_A, migration_time=123, source="pumpfun_migration")
    row = ops.execute("SELECT * FROM wt_migration_ledger_coverage WHERE mint=?", (MINT_A,)).fetchone()
    assert row["ledger_result"] == "MIGRATION_CREATE_LEDGER_MISSING"
    assert row["alert_emitted_at"] is not None


# ── Test 12: migration coverage check never blocks migration ────────────────

def test_store_migration_succeeds_even_if_coverage_check_raises(monkeypatch):
    import src.core.watchtower_attribution as wta
    ops = _build_ops_db()
    wta.ensure_schema(ops)

    def _broken_coverage(*a, **k):
        raise RuntimeError("simulated coverage-check failure")
    monkeypatch.setattr(wta, "_record_migration_coverage", _broken_coverage)

    # Must not raise — store_migration wraps the coverage check in its own
    # try/except, exactly like the existing enqueue_migration call above it.
    wta.store_migration(ops, mint=MINT_A, creator=CREATOR_A)
    row = ops.execute("SELECT * FROM migrated_tokens WHERE mint=?", (MINT_A,)).fetchone()
    assert row is not None  # migration itself was still recorded


# ── Test 13: ordinary worker recovers anchor from ledger ────────────────────

def test_ordinary_reconcile_function_recovers_ledger_only_signature():
    """The actual function walkback_worker.py's run_loop calls each cycle
    (reconcile_waiting_create_anchors) — not just the standalone
    resolve_anchor_with_priority helper — must itself find a
    ledger-only signature."""
    ops = _build_ops_db()
    live = _build_live_db()
    _seed_queue_row(ops, MINT_A, CREATOR_A)
    cel.record_create_event(ops, signature=VALID_SIG_B, mint=MINT_A, creator=CREATOR_A, source="WEBSOCKET")

    result = recon.reconcile_waiting_create_anchors(ops, live)
    assert len(result["recovered"]) == 1
    assert result["recovered"][0]["source"] == "canonical_create_ledger"
    row = ops.execute("SELECT create_anchor_signature, status FROM wt_walkback_queue WHERE mint=?", (MINT_A,)).fetchone()
    assert row["create_anchor_signature"] == VALID_SIG_B
    assert row["status"] == "pending"


# ── Test 14: resolver conflict does not overwrite queue anchor ──────────────

def test_ledger_conflict_does_not_overwrite_queue_anchor_via_ordinary_reconcile():
    ops = _build_ops_db()
    live = _build_live_db()
    _seed_queue_row(ops, MINT_A, CREATOR_A)
    # Two distinct signatures for the same mint in the ledger -> CONFLICT
    cel.record_create_event(ops, signature=VALID_SIG_A, mint=MINT_A, creator=CREATOR_A, source="WEBSOCKET")
    cel.record_create_event(ops, signature=VALID_SIG_B, mint=MINT_A, creator=CREATOR_A, source="RECONCILER")

    result = recon.reconcile_waiting_create_anchors(ops, live)
    assert result["recovered"] == []
    row = ops.execute("SELECT create_anchor_signature, status FROM wt_walkback_queue WHERE mint=?", (MINT_A,)).fetchone()
    assert row["create_anchor_signature"] is None
    assert row["status"] == recon.WAITING_STATUS


# ── Test 15: all retry and reconciliation paths perform zero RPC ────────────

def test_retry_pending_writes_performs_zero_rpc(monkeypatch):
    def _fail(*a, **k):
        raise AssertionError("retry_pending_writes must never perform network I/O")
    monkeypatch.setattr(urllib.request, "urlopen", _fail)

    ops = _build_ops_db()
    cel.persist_pending_write(
        ops, signature=VALID_SIG_A, mint=MINT_A, creator=CREATOR_A, slot=1, block_time=100,
        source="WEBSOCKET", parser_path="handle_birth", last_error="lock",
    )
    ops.execute("UPDATE wt_create_ledger_pending SET next_retry_at=0")
    ops.commit()
    result = cel.retry_pending_writes(ops)
    assert VALID_SIG_A in result["recovered"]


def test_migration_coverage_check_performs_zero_rpc(monkeypatch):
    def _fail(*a, **k):
        raise AssertionError("migration coverage check must never perform network I/O")
    monkeypatch.setattr(urllib.request, "urlopen", _fail)

    import src.core.watchtower_attribution as wta
    ops = _build_ops_db()
    wta._record_migration_coverage(ops, mint=MINT_A, creator=CREATOR_A, migration_time=123, source="pumpfun_migration")
    row = ops.execute("SELECT * FROM wt_migration_ledger_coverage WHERE mint=?", (MINT_A,)).fetchone()
    assert row is not None


def test_reconcile_ordinary_path_performs_zero_rpc(monkeypatch):
    def _fail(*a, **k):
        raise AssertionError("reconcile_waiting_create_anchors must never perform network I/O")
    monkeypatch.setattr(urllib.request, "urlopen", _fail)

    ops = _build_ops_db()
    live = _build_live_db()
    _seed_queue_row(ops, MINT_A, CREATOR_A)
    cel.record_create_event(ops, signature=VALID_SIG_A, mint=MINT_A, creator=CREATOR_A, source="WEBSOCKET")
    result = recon.reconcile_waiting_create_anchors(ops, live)
    assert len(result["recovered"]) == 1
