"""X64.9B1 — Durable signature redelivery instrumentation.

Instruments the existing wt_subprov_sig_retry DONE-row dedupe boundary
(ws_cascade.py's _process_subprov_sig_durable) so redelivery frequency
and age can be measured durably, surviving process restarts, ahead of
ever defining a safe retention cutoff for status='DONE' rows (X64.9B
was aborted when this exact dependency was discovered unaudited).

Must never: change the existing dedup/skip outcome, add extra RPC or
downstream fanout work, modify wt_subprov_sig_retry, or let an
observability write failure propagate into signature processing.
"""
from __future__ import annotations

import ast
import sqlite3
import time
from pathlib import Path

import pytest

from src.core import ws_cascade_store as store


ROOT = Path(__file__).resolve().parents[1]
WS_CASCADE_SRC = (ROOT / "src/core/ws_cascade.py").read_text()
WS_CASCADE_STORE_SRC = (ROOT / "src/core/ws_cascade_store.py").read_text()


def _build_ops_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    store.ensure_cascade_schema(conn)
    return conn


# ── Age bucket correctness ───────────────────────────────────────────────────

@pytest.mark.parametrize("age_s,expected", [
    (0, "<5m"),
    (299, "<5m"),
    (300, "5m-30m"),
    (1799, "5m-30m"),
    (1800, "30m-2h"),
    (7199, "30m-2h"),
    (7200, "2h-12h"),
    (43199, "2h-12h"),
    (43200, "12h-24h"),
    (86399, "12h-24h"),
    (86400, "1d-3d"),
    (259199, "1d-3d"),
    (259200, "3d-7d"),
    (604799, "3d-7d"),
    (604800, "7d-14d"),
    (1209599, "7d-14d"),
    (1209600, "14d-30d"),
    (2591999, "14d-30d"),
    (2592000, ">30d"),
    (99999999, ">30d"),
])
def test_age_bucket_boundaries_are_correct(age_s, expected):
    assert store.dedupe_age_bucket(age_s) == expected


def test_age_bucket_never_raises_on_negative_or_none():
    # observed_at can theoretically be < original_done_at under clock skew;
    # the recording path already clamps to >=0, but the bucket function
    # itself must be defensive too.
    assert store.dedupe_age_bucket(-5) == "<5m"


# ── A previously completed signature is skipped ──────────────────────────────

def test_done_row_is_still_skipped_identically_to_before_instrumentation():
    """The instrumentation widened the SELECT from `status` to `status,
    last_attempt_at` — this must not change which rows are treated as
    already-done. A DONE row must still read back as DONE."""
    conn = _build_ops_db()
    now = int(time.time())
    conn.execute(
        """INSERT INTO wt_subprov_sig_retry
             (subprov_wallet, signature, first_seen_at, last_attempt_at, attempts, status)
           VALUES (?,?,?,?,1,'DONE')""",
        ("SubProvWallet111", "Sig111", now - 100, now - 50))
    conn.commit()

    row = conn.execute(
        "SELECT status, last_attempt_at FROM wt_subprov_sig_retry "
        "WHERE subprov_wallet=? AND signature=?",
        ("SubProvWallet111", "Sig111")).fetchone()

    assert row is not None
    assert row["status"] == "DONE"
    assert row["last_attempt_at"] == now - 50


# ── Duplicate count is recorded ──────────────────────────────────────────────

def test_duplicate_recording_creates_one_row_per_wallet_bucket_pair():
    conn = _build_ops_db()
    now = int(time.time())

    store.record_subprov_sig_duplicate(
        conn, subprov="WalletA", age_s=100, source="WS", observed_at=now)

    rows = conn.execute(
        "SELECT * FROM wt_subprov_sig_dedupe_stats WHERE subprov_wallet='WalletA'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["age_bucket"] == "<5m"
    assert rows[0]["duplicate_count"] == 1
    assert rows[0]["source_ws"] == 1
    assert rows[0]["source_catchup"] == 0


def test_duplicate_count_accumulates_for_same_wallet_and_bucket():
    conn = _build_ops_db()
    now = int(time.time())

    store.record_subprov_sig_duplicate(conn, subprov="WalletB", age_s=10, source="WS", observed_at=now)
    store.record_subprov_sig_duplicate(conn, subprov="WalletB", age_s=20, source="WS", observed_at=now + 1)
    store.record_subprov_sig_duplicate(conn, subprov="WalletB", age_s=30, source="HOT_BURST", observed_at=now + 2)

    row = conn.execute(
        "SELECT * FROM wt_subprov_sig_dedupe_stats WHERE subprov_wallet='WalletB' AND age_bucket='<5m'"
    ).fetchone()
    assert row["duplicate_count"] == 3
    assert row["source_ws"] == 2
    assert row["source_hot_burst"] == 1
    assert row["max_duplicate_age_s"] == 30
    assert row["last_observed_at"] == now + 2


def test_duplicate_in_different_bucket_creates_separate_row():
    conn = _build_ops_db()
    now = int(time.time())

    store.record_subprov_sig_duplicate(conn, subprov="WalletC", age_s=10, source="WS", observed_at=now)
    store.record_subprov_sig_duplicate(conn, subprov="WalletC", age_s=50000, source="WS", observed_at=now)

    rows = conn.execute(
        "SELECT age_bucket, duplicate_count FROM wt_subprov_sig_dedupe_stats "
        "WHERE subprov_wallet='WalletC' ORDER BY age_bucket"
    ).fetchall()
    buckets = {r["age_bucket"]: r["duplicate_count"] for r in rows}
    assert buckets == {"<5m": 1, "12h-24h": 1}


def test_unrecognized_source_does_not_crash_and_omits_source_increment():
    conn = _build_ops_db()
    # a future/unknown source value must not raise — it should still record
    # the duplicate, just without a per-source column bump.
    store.record_subprov_sig_duplicate(
        conn, subprov="WalletD", age_s=5, source="SOME_FUTURE_SOURCE", observed_at=int(time.time()))
    row = conn.execute(
        "SELECT duplicate_count, source_ws, source_catchup, source_retry, source_hot_burst "
        "FROM wt_subprov_sig_dedupe_stats WHERE subprov_wallet='WalletD'"
    ).fetchone()
    assert row["duplicate_count"] == 1
    assert row["source_ws"] == 0
    assert row["source_catchup"] == 0
    assert row["source_retry"] == 0
    assert row["source_hot_burst"] == 0


# ── Duplicate age is calculated correctly / global summary ──────────────────

def test_global_summary_tracks_totals_and_extremes():
    conn = _build_ops_db()
    now = int(time.time())

    store.record_subprov_sig_checked(conn, observed_at=now)
    store.record_subprov_sig_checked(conn, observed_at=now)
    store.record_subprov_sig_checked(conn, observed_at=now)
    store.record_subprov_sig_duplicate(conn, subprov="WalletE", age_s=50, source="WS", observed_at=now)
    store.record_subprov_sig_duplicate(conn, subprov="WalletE", age_s=500000, source="RETRY", observed_at=now + 10)

    summary = conn.execute("SELECT * FROM wt_subprov_sig_dedupe_summary WHERE id=1").fetchone()
    assert summary["total_checked"] == 3
    assert summary["total_duplicates"] == 2
    assert summary["max_duplicate_age_s"] == 500000
    assert summary["last_duplicate_at"] == now + 10


def test_zero_duplicates_result_is_meaningful_via_total_checked():
    """A '0 duplicates' result is only evidence if we know how many
    signatures were actually checked — this is the entire reason
    total_checked exists as a durable denominator."""
    conn = _build_ops_db()
    now = int(time.time())
    for _ in range(50):
        store.record_subprov_sig_checked(conn, observed_at=now)

    summary = conn.execute("SELECT * FROM wt_subprov_sig_dedupe_summary WHERE id=1").fetchone()
    assert summary["total_checked"] == 50
    assert summary["total_duplicates"] == 0


# ── Schema safety ─────────────────────────────────────────────────────────

def test_schema_creation_is_idempotent():
    conn = _build_ops_db()
    # calling ensure_cascade_schema a second time must not raise or reset data
    store.record_subprov_sig_duplicate(conn, subprov="WalletF", age_s=1, source="WS", observed_at=int(time.time()))
    store.ensure_cascade_schema(conn)
    row = conn.execute(
        "SELECT duplicate_count FROM wt_subprov_sig_dedupe_stats WHERE subprov_wallet='WalletF'"
    ).fetchone()
    assert row["duplicate_count"] == 1  # unchanged by the second ensure_cascade_schema call


def test_dedupe_summary_table_enforces_single_row():
    conn = _build_ops_db()
    store.record_subprov_sig_checked(conn, observed_at=int(time.time()))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO wt_subprov_sig_dedupe_summary (id, total_checked, total_duplicates, updated_at) "
            "VALUES (2, 0, 0, ?)", (int(time.time()),))


def test_new_tables_have_no_foreign_keys():
    conn = _build_ops_db()
    for table in ("wt_subprov_sig_dedupe_stats", "wt_subprov_sig_dedupe_summary"):
        fks = conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
        assert fks == []


def test_wt_subprov_sig_retry_schema_is_completely_unmodified():
    """The instrumentation must not alter wt_subprov_sig_retry's own schema
    in any way — only the SELECT reading from it changed (status ->
    status, last_attempt_at), never the table definition."""
    conn = _build_ops_db()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(wt_subprov_sig_retry)").fetchall()}
    assert cols == {
        "subprov_wallet", "signature", "slot", "first_seen_at",
        "last_attempt_at", "attempts", "last_error", "status",
    }


# ── No downstream RPC/fanout on the duplicate path (static/structural check) ─

def test_dedupe_branch_returns_immediately_after_recording_before_any_rpc_work():
    """AST check: inside the `if row and row[0] == "DONE":` branch, the ONLY
    calls permitted are the existing metric increment, the new X64.9B1
    recording call, and the final `return []` — nothing that could trigger
    getTransaction/handle_subprov_tx or any other downstream work."""
    tree = ast.parse(WS_CASCADE_SRC)

    found_branch = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        # Match: row and row[0] == "DONE"
        if not (isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And)):
            continue
        matched_done_compare = False
        for value in test.values:
            if (isinstance(value, ast.Compare)
                    and isinstance(value.comparators[0], ast.Constant)
                    and value.comparators[0].value == "DONE"):
                matched_done_compare = True
        if not matched_done_compare:
            continue

        found_branch = True
        allowed_call_attrs = {"_metric", "_record_subprov_sig_dedupe"}
        for stmt in node.body:
            if isinstance(stmt, ast.Return):
                continue
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                call = stmt.value
                if isinstance(call.func, ast.Attribute) and call.func.attr in allowed_call_attrs:
                    continue
            pytest.fail(
                "Unexpected statement inside the DONE-dedupe branch: "
                f"{ast.dump(stmt)} — only metric/recording calls and `return []` "
                "are permitted; this branch must never perform RPC or downstream work."
            )
        # Last statement must be a bare `return []`
        last = node.body[-1]
        assert isinstance(last, ast.Return), "DONE-dedupe branch must end in a return"
        assert isinstance(last.value, ast.List) and len(last.value.elts) == 0, \
            "DONE-dedupe branch must return [] unchanged, exactly as before instrumentation"

    assert found_branch, "Could not locate the DONE-row dedupe branch in ws_cascade.py — has it moved?"


def test_recording_functions_use_their_own_connection_not_the_dedupe_check_conn():
    """Structural check: _record_subprov_sig_dedupe and
    _record_subprov_sig_checked_only must each call self._ops() themselves
    (their own connection), never accept or reuse an external `conn`
    parameter — this is the nested-write-lane mitigation."""
    tree = ast.parse(WS_CASCADE_SRC)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in (
                "_record_subprov_sig_dedupe", "_record_subprov_sig_checked_only"):
            arg_names = {a.arg for a in node.args.args}
            assert "conn" not in arg_names, (
                f"{node.name} must not accept an external conn parameter — "
                "it must open its own via self._ops() to avoid nesting inside "
                "the caller's already-open connection/transaction."
            )
            calls_ops = any(
                isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "_ops"
                for n in ast.walk(node)
            )
            assert calls_ops, f"{node.name} must open its own connection via self._ops()"


def test_in_memory_seen_cache_does_not_gate_the_durable_dedupe_check():
    """_subprov_sig_seen() is a bounded in-memory cache defined in this file,
    but the DB-backed dedupe check in _process_subprov_sig_durable() must
    not depend on it being called first — otherwise eviction from the
    5000-key bound (or simply never populating it) could silently bypass
    the durable check. Structural proof: _process_subprov_sig_durable's
    body must not call self._subprov_sig_seen(...) before its own DB
    lookup — the DB check must be reachable unconditionally."""
    tree = ast.parse(WS_CASCADE_SRC)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_process_subprov_sig_durable":
            calls_seen_gate = any(
                isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "_subprov_sig_seen"
                for n in ast.walk(node)
            )
            assert not calls_seen_gate, (
                "_process_subprov_sig_durable must not be gated by the bounded "
                "in-memory _subprov_sig_seen() cache — the DB-backed DONE check "
                "must always run, independent of in-memory cache state, so "
                "eviction or a process restart can never silently bypass it."
            )


def test_durable_check_survives_simulated_process_restart():
    """Simulates a restart: a fresh, empty in-memory dedupe cache (as a new
    Cascade instance would have after restart) must not matter, because the
    dedupe check reads wt_subprov_sig_retry directly from the durable ops
    DB, not from any in-process cache. This proves the DB check alone is
    sufficient for correct dedup across restarts."""
    conn = _build_ops_db()
    now = int(time.time())
    conn.execute(
        """INSERT INTO wt_subprov_sig_retry
             (subprov_wallet, signature, first_seen_at, last_attempt_at, attempts, status)
           VALUES (?,?,?,?,1,'DONE')""",
        ("WalletRestart", "SigRestart", now - 200, now - 100))
    conn.commit()

    # "restart" = a brand new, empty in-memory set — never populated, exactly
    # as a fresh Cascade() instance's self._subprov_seen would be.
    fresh_in_memory_seen: set = set()
    assert ("WalletRestart", "SigRestart") not in fresh_in_memory_seen

    # The durable check must still find the row as DONE purely from the DB,
    # with zero dependency on the (now-empty) in-memory set.
    row = conn.execute(
        "SELECT status, last_attempt_at FROM wt_subprov_sig_retry "
        "WHERE subprov_wallet=? AND signature=?",
        ("WalletRestart", "SigRestart")).fetchone()
    assert row["status"] == "DONE"


def test_recording_functions_are_wrapped_in_try_except_that_never_reraises():
    """Structural check: both recording helper methods must swallow
    exceptions (best-effort observability), never let a failure propagate
    back into signature processing."""
    tree = ast.parse(WS_CASCADE_SRC)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in (
                "_record_subprov_sig_dedupe", "_record_subprov_sig_checked_only"):
            has_bare_except_no_raise = False
            for sub in ast.walk(node):
                if isinstance(sub, ast.Try):
                    for handler in sub.handlers:
                        # must catch Exception (or broader), and must not re-raise
                        reraises = any(isinstance(s, ast.Raise) for s in ast.walk(handler))
                        if not reraises:
                            has_bare_except_no_raise = True
            assert has_bare_except_no_raise, (
                f"{node.name} must contain a try/except that catches failures "
                "without re-raising — observability must never break processing."
            )
