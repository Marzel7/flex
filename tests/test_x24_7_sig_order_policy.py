"""X24.7 — pluggable signature-processing-order policy tests.

Context: full-population historical replay (39 confirmed launches, the
complete reconstructable set, not a sample) showed ALTERNATING newest/oldest
processing order improves both median AND P95 inspections-to-creator versus
the pre-X24.7 oldest-first order inside catch_up_subprov()'s per-signature
loop -- NOT fair_sweep_candidates() (which orders which SESSIONS get swept,
a separate, unaffected mechanism).

Critical correctness dependency discovered during implementation: the durable
cursor (wt_subprov_sig_cursor.last_seen_sig) was only correct under the old
oldest-first order because the loop's LAST call happened to be for the
newest signature; subprov_sig_mark_done() unconditionally overwrites the
cursor on every call. Reordering without fixing this would silently corrupt
the cursor every cycle. The fix: _process_subprov_sig_durable(advance_cursor=
False) skips the cursor write per-signature (still marks the retry row DONE,
safe in any order); catch_up_subprov() advances the cursor exactly once,
after the batch, to the newest signature that was ACTUALLY successfully
processed (not merely the newest in the fetched batch).
"""
from __future__ import annotations

import asyncio
import os
import sqlite3

import pytest

os.environ.setdefault("HELIUS_API_KEY", "test-key-not-used-network-is-mocked")


# ── Part 1: pure unit tests for _order_signature_indices ────────────────────

def _get_order_fn():
    from src.core import ws_cascade as wc
    return wc._order_signature_indices


def test_fifo_policy_is_oldest_first():
    order_fn = _get_order_fn()
    assert order_fn(5, "FIFO") == [4, 3, 2, 1, 0]


def test_oldest_first_alias_matches_fifo():
    order_fn = _get_order_fn()
    assert order_fn(5, "OLDEST_FIRST") == order_fn(5, "FIFO")


def test_newest_first_policy():
    order_fn = _get_order_fn()
    assert order_fn(5, "NEWEST_FIRST") == [0, 1, 2, 3, 4]


def test_alternating_policy_even_count():
    order_fn = _get_order_fn()
    order = order_fn(6, "ALTERNATING")
    assert order == [0, 5, 1, 4, 2, 3]


def test_alternating_policy_odd_count():
    order_fn = _get_order_fn()
    order = order_fn(7, "ALTERNATING")
    assert order == [0, 6, 1, 5, 2, 4, 3]


def test_alternating_single_session():
    order_fn = _get_order_fn()
    assert order_fn(1, "ALTERNATING") == [0]


def test_alternating_empty_session():
    order_fn = _get_order_fn()
    assert order_fn(0, "ALTERNATING") == []
    assert order_fn(0, "FIFO") == []
    assert order_fn(0, "NEWEST_FIRST") == []


@pytest.mark.parametrize("n", [0, 1, 2, 3, 4, 5, 7, 8, 10, 13, 50, 481])
@pytest.mark.parametrize("policy", ["FIFO", "OLDEST_FIRST", "NEWEST_FIRST", "ALTERNATING"])
def test_every_policy_is_a_strict_permutation_no_starvation(n, policy):
    """The core fairness guarantee: every index 0..n-1 appears exactly once,
    regardless of policy or session size (even/odd/tiny/huge). This is what
    makes X24.7 a prioritisation policy and never a filter -- structurally,
    not by convention."""
    order_fn = _get_order_fn()
    order = order_fn(n, policy)
    assert len(order) == n
    assert sorted(order) == list(range(n))
    assert len(set(order)) == len(order), "duplicate index -- would double-process a signature"


def test_unrecognised_policy_fails_safe_to_fifo(caplog):
    order_fn = _get_order_fn()
    order = order_fn(5, "NONSENSE_POLICY")
    assert order == [4, 3, 2, 1, 0]  # same as FIFO


def test_default_policy_is_alternating():
    from src.core import ws_cascade as wc
    assert wc.SUBPROV_SIG_ORDER_POLICY == "ALTERNATING"


def test_deterministic_ordering_same_input_same_output():
    order_fn = _get_order_fn()
    for policy in ("FIFO", "NEWEST_FIRST", "ALTERNATING"):
        a = order_fn(37, policy)
        b = order_fn(37, policy)
        assert a == b, f"{policy} must be deterministic"


# ── Part 2: integration tests for catch_up_subprov()'s reordering + cursor ──

def _make_ops_db(path: str):
    conn = sqlite3.connect(path)
    from src.core import ws_cascade_store as store
    store.ensure_cascade_schema(conn)
    try:
        conn.execute(
            "ALTER TABLE wt_active_subprov_sessions ADD COLUMN "
            "monitoring_state TEXT DEFAULT 'LIVE_ARMED'")
    except sqlite3.OperationalError:
        pass
    conn.execute(
        "CREATE TABLE IF NOT EXISTS wt_subprov_sig_cursor ("
        "subprov_wallet TEXT PRIMARY KEY, last_seen_sig TEXT, last_seen_slot INTEGER, "
        "last_seen_at INTEGER, updated_at INTEGER NOT NULL)")
    conn.commit()
    conn.close()


def _insert_session(path, subprov, expires_at, detected_at=0):
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO wt_active_subprov_sessions "
        "(subprov_wallet, state, detected_at, expires_at) VALUES (?, 'ACTIVE', ?, ?)",
        (subprov, detected_at, expires_at))
    conn.commit()
    conn.close()


@pytest.fixture
def ops_db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "sig_order_test.db")
    _make_ops_db(path)
    monkeypatch.setenv("OPS_V2_DB_PATH", path)
    import importlib
    from src.core import ws_cascade_store as store_mod
    importlib.reload(store_mod)
    from src.core import ws_cascade as wc_mod
    importlib.reload(wc_mod)
    return path, wc_mod


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _fake_sigs(n, prefix="SIG"):
    """Returns n fake signature dicts in newest-first order (matching
    Solana's getSignaturesForAddress contract) -- index 0 = newest."""
    return [{"signature": f"{prefix}{i}", "slot": 1000 - i, "err": None} for i in range(n)]


def test_cursor_advances_to_newest_signature_under_alternating_order(ops_db_path, monkeypatch):
    """The core correctness proof: even though ALTERNATING processes signatures
    out of chronological order, the durable cursor must still end up pointing
    at the newest signature in the batch (SIG0), never at whatever signature
    happened to be visited last in the loop."""
    path, wc = ops_db_path
    subprov = "SUBPROV_ALT_TEST"
    _insert_session(path, subprov, expires_at=999999)
    casc = wc.Cascade()

    sigs = _fake_sigs(7)

    async def fake_arpc(method, params, timeout=None):
        assert method == "getSignaturesForAddress"
        return sigs

    monkeypatch.setattr(wc, "_arpc", fake_arpc)

    processed_order = []

    def fake_process(subprov_, sig_, *, slot=None, source="WS", advance_cursor=True, prefetched_tx=None):
        processed_order.append(sig_)
        return []

    monkeypatch.setattr(casc, "_process_subprov_sig_durable", fake_process)

    outcome = _run(casc.catch_up_subprov(subprov))
    assert outcome == "SUCCESS"

    # Processing order must match the alternating policy: newest, oldest, ...
    assert processed_order == ["SIG0", "SIG6", "SIG1", "SIG5", "SIG2", "SIG4", "SIG3"]

    # Cursor must point at SIG0 (the newest), NOT SIG3 (the last one visited).
    conn = sqlite3.connect(path)
    cursor = conn.execute(
        "SELECT last_seen_sig FROM wt_subprov_sig_cursor WHERE subprov_wallet=?", (subprov,)
    ).fetchone()
    conn.close()
    assert cursor is not None
    assert cursor[0] == "SIG0", (
        f"cursor should advance to the newest signature (SIG0), got {cursor[0]!r} -- "
        "this would be the exact silent-corruption bug X24.7 had to fix"
    )


def test_cursor_does_not_advance_past_a_failed_newest_signature(ops_db_path, monkeypatch):
    """If the NEWEST signature in the batch fails, the cursor must not advance
    to it -- it must fall back to the newest signature that actually
    succeeded, exactly matching pre-X24.7 semantics (a failed signature never
    advanced the cursor before either)."""
    path, wc = ops_db_path
    subprov = "SUBPROV_FAIL_NEWEST"
    _insert_session(path, subprov, expires_at=999999)
    casc = wc.Cascade()

    sigs = _fake_sigs(5)

    async def fake_arpc(method, params, timeout=None):
        return sigs

    monkeypatch.setattr(wc, "_arpc", fake_arpc)

    def fake_process(subprov_, sig_, *, slot=None, source="WS", advance_cursor=True, prefetched_tx=None):
        if sig_ == "SIG0":  # the newest signature fails
            raise RuntimeError("simulated getTransaction failure")
        return []

    monkeypatch.setattr(casc, "_process_subprov_sig_durable", fake_process)

    outcome = _run(casc.catch_up_subprov(subprov))
    assert outcome == "SUCCESS"  # the catch_up call itself still succeeds; per-sig failure is separate

    conn = sqlite3.connect(path)
    cursor = conn.execute(
        "SELECT last_seen_sig FROM wt_subprov_sig_cursor WHERE subprov_wallet=?", (subprov,)
    ).fetchone()
    conn.close()
    assert cursor is not None
    assert cursor[0] == "SIG1", (
        f"cursor should fall back to the newest SUCCEEDING signature (SIG1), got {cursor[0]!r} "
        "-- must never skip past a failed signature"
    )


def test_all_signatures_processed_exactly_once_even_and_odd(ops_db_path, monkeypatch):
    """No starvation, no duplicate processing, for both even and odd batch sizes."""
    path, wc = ops_db_path
    for n in (6, 7):
        subprov = f"SUBPROV_COUNT_{n}"
        _insert_session(path, subprov, expires_at=999999)
        casc = wc.Cascade()
        sigs = _fake_sigs(n, prefix=f"S{n}_")

        async def fake_arpc(method, params, timeout=None, _sigs=sigs):
            return _sigs

        monkeypatch.setattr(wc, "_arpc", fake_arpc)

        seen = []

        def fake_process(subprov_, sig_, *, slot=None, source="WS", advance_cursor=True,
                        prefetched_tx=None, _seen=seen):
            _seen.append(sig_)
            return []

        monkeypatch.setattr(casc, "_process_subprov_sig_durable", fake_process)
        outcome = _run(casc.catch_up_subprov(subprov))
        assert outcome == "SUCCESS"
        assert len(seen) == n
        assert len(set(seen)) == n, f"n={n}: duplicate processing detected"


def test_no_signature_processed_when_batch_empty(ops_db_path, monkeypatch):
    path, wc = ops_db_path
    subprov = "SUBPROV_EMPTY"
    _insert_session(path, subprov, expires_at=999999)
    casc = wc.Cascade()

    async def fake_arpc(method, params, timeout=None):
        return []

    monkeypatch.setattr(wc, "_arpc", fake_arpc)

    called = {"n": 0}
    def fake_process(*a, **kw):
        called["n"] += 1
        return []
    monkeypatch.setattr(casc, "_process_subprov_sig_durable", fake_process)

    outcome = _run(casc.catch_up_subprov(subprov))
    assert outcome == "SUCCESS"
    assert called["n"] == 0

    conn = sqlite3.connect(path)
    cursor = conn.execute(
        "SELECT last_seen_sig FROM wt_subprov_sig_cursor WHERE subprov_wallet=?", (subprov,)
    ).fetchone()
    conn.close()
    assert cursor is None, "cursor must not be written when nothing was processed"


def test_mixed_success_and_failure_only_successes_count_for_cursor(ops_db_path, monkeypatch):
    path, wc = ops_db_path
    subprov = "SUBPROV_MIXED"
    _insert_session(path, subprov, expires_at=999999)
    casc = wc.Cascade()
    sigs = _fake_sigs(5)  # SIG0 (newest) .. SIG4 (oldest)

    async def fake_arpc(method, params, timeout=None):
        return sigs
    monkeypatch.setattr(wc, "_arpc", fake_arpc)

    fail_set = {"SIG0", "SIG2"}
    def fake_process(subprov_, sig_, *, slot=None, source="WS", advance_cursor=True, prefetched_tx=None):
        if sig_ in fail_set:
            raise RuntimeError("boom")
        return []
    monkeypatch.setattr(casc, "_process_subprov_sig_durable", fake_process)

    outcome = _run(casc.catch_up_subprov(subprov))
    assert outcome == "SUCCESS"

    conn = sqlite3.connect(path)
    cursor = conn.execute(
        "SELECT last_seen_sig FROM wt_subprov_sig_cursor WHERE subprov_wallet=?", (subprov,)
    ).fetchone()
    conn.close()
    # newest successful is SIG1 (SIG0 failed)
    assert cursor[0] == "SIG1"


def test_advance_cursor_false_does_not_write_cursor(ops_db_path):
    """Direct unit test of _process_subprov_sig_durable's advance_cursor flag:
    the retry row is still marked DONE, but the cursor table stays untouched."""
    path, wc = ops_db_path
    from src.core import ws_cascade_store as store
    subprov = "SUBPROV_FLAG_TEST"
    _insert_session(path, subprov, expires_at=999999)
    casc = wc.Cascade()

    def fake_handle(subprov_, sig_, seen_at=None, prefetched=None):
        return []
    casc._handle_subprov_tx = fake_handle

    casc._process_subprov_sig_durable(subprov, "SIGX", advance_cursor=False)

    conn = sqlite3.connect(path)
    retry_row = conn.execute(
        "SELECT status FROM wt_subprov_sig_retry WHERE subprov_wallet=? AND signature=?",
        (subprov, "SIGX")).fetchone()
    cursor_row = conn.execute(
        "SELECT * FROM wt_subprov_sig_cursor WHERE subprov_wallet=?", (subprov,)).fetchone()
    conn.close()
    assert retry_row is not None and retry_row[0] == "DONE"
    assert cursor_row is None, "advance_cursor=False must not write the cursor table"


def test_advance_cursor_true_preserves_legacy_ws_path_behaviour(ops_db_path):
    """The WS live path calls _process_subprov_sig_durable with the default
    advance_cursor=True (unchanged call site) -- must still mark DONE AND
    advance the cursor in one call, exactly as before X24.7."""
    path, wc = ops_db_path
    subprov = "SUBPROV_LEGACY_WS"
    _insert_session(path, subprov, expires_at=999999)
    casc = wc.Cascade()
    casc._handle_subprov_tx = lambda *a, **kw: []

    casc._process_subprov_sig_durable(subprov, "SIGWS1")  # advance_cursor defaults True

    conn = sqlite3.connect(path)
    cursor_row = conn.execute(
        "SELECT last_seen_sig FROM wt_subprov_sig_cursor WHERE subprov_wallet=?", (subprov,)
    ).fetchone()
    conn.close()
    assert cursor_row is not None and cursor_row[0] == "SIGWS1"
