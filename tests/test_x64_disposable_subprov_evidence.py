"""X64 — Disposable Sub-Provisioner Evidence Preservation.

Root cause fixed here: the FULL_WALKBACK terminal branch (hop1 resolved,
hop1 not already known as a subprov/treasury, hop2 not found) collapsed a
directly-observed WSOL_WRAP_CLOSE/SEEDED_ACCOUNT_CLOSE handoff into
NO_ATTRIBUTION_FOUND with subprov=NULL — identical to the case where no
WATCHTOWER evidence was ever found at all. Disposable sub-provisioners are,
by the X62 primitive, expected to be single-use and therefore usually
unknown at hop1 with no resolvable hop2 upstream; the old branch silently
discarded exactly the evidence this pipeline exists to detect.

The fix (src/core/walkback_worker.py, FULL_WALKBACK's final `else`) checks
whether hop1's own funding mechanism was itself the handoff primitive
(_is_disposable_subprov_handoff). If so, the row completes as LINEAGE_GAP
with subprov=hop1, confirmed_subprov=False — reusing the existing
_ensure_subprov_lead/_mark_complete discovery-lead machinery unchanged.
Ordinary PLAIN_XFER/UNKNOWN hop1s still terminate as NO_ATTRIBUTION_FOUND,
identically to before. No new RPC call, table, or queue is introduced.

This must never make WATCHTOWER_CONFIRMED reachable from mechanism
evidence alone, and must never change the no-hop1, known-subprov,
known-treasury, or hop2-found code paths.
"""
from __future__ import annotations

import sqlite3
import time
from unittest.mock import patch

import pytest

import src.core.walkback_worker as ww
from src.core.walkback_worker import (
    _is_disposable_subprov_handoff,
    _process_row,
)


# ── schema fixture ───────────────────────────────────────────────────────────

def _build_ops_db() -> sqlite3.Connection:
    """A real, on-disk-schema ops connection — same ensure_schema() chain
    production uses — so _process_row exercises real SQL, not mocks."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    from src.core import walkback_queue, deep_walkback, treasury_bank
    from src.ops import attribution_outcome, watchtower_candidates
    from src.core import watchtower_attribution

    walkback_queue.ensure_schema(conn)
    deep_walkback.ensure_schema(conn)
    treasury_bank.ensure_schema(conn)
    attribution_outcome.ensure_schema(conn)
    watchtower_candidates.ensure_schema(conn)
    watchtower_attribution.ensure_schema(conn)
    # wt_discovered_subprovs has no single ensure_schema() owner in the
    # codebase (created ad hoc by src/core/operation_scheduler.py, extended
    # by later ALTER TABLEs elsewhere) — declared here matching the live
    # production schema (confirmed via `sqlite3 database/wt_ops_v2.db
    # ".schema wt_discovered_subprovs"`) so _ensure_subprov_lead's INSERT
    # (subprov, first_creator, creator_count, treasury, treasury_known,
    #  first_seen, last_seen, wrap_close_count, confidence, state) succeeds.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wt_discovered_subprovs (
            subprov TEXT PRIMARY KEY, first_creator TEXT, creator_count INTEGER DEFAULT 1,
            treasury TEXT, treasury_known INTEGER DEFAULT 0, first_seen INTEGER, last_seen INTEGER,
            immediate_funder TEXT, funder_is_subprov INTEGER DEFAULT 0,
            confidence REAL DEFAULT 0.20, state TEXT DEFAULT 'PROVISION_CANDIDATE',
            wrap_close_count INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    return conn


def _seed_row(conn: sqlite3.Connection, *, mint: str, creator: str,
              walkback_class: str = "FULL_WALKBACK", attempts: int = 1) -> None:
    now = int(time.time())
    conn.execute(
        "INSERT INTO wt_walkback_queue "
        "(mint, creator, walkback_class, status, attempts, enqueued_at, updated_at) "
        "VALUES (?,?,?,'running',?,?,?)",
        (mint, creator, walkback_class, attempts, now, now),
    )
    conn.commit()


def _row(conn: sqlite3.Connection, mint: str) -> sqlite3.Row:
    return conn.execute(
        "SELECT * FROM wt_walkback_queue WHERE mint=?", (mint,)
    ).fetchone()


MINT = "CvP9vVUCpoDuMd2jg5qvakFsk8Ht4qQwmKtZTMeUpump"
CREATOR = "71ftvekAkhanTdJJXdZRLtz7ShkXxdAxhmVmyv2YVSFS"
HOP1 = "DCyQJVfAL37WtcwWAmLNeTatRG553WyfDNytQok41tko"
HOP1_SIG = ("NoK7KdV5UuQS9VLJ7YYf1e35Rgj6s1HR54Ht84hKgWhSkMV4DhynGtvSmHkp9pRw"
            "PR9XHdrnU7BNm37ETAjRXHq")


def _stub_find_with_evidence(hop_map):
    """hop_map: {hop_depth: FunderInfo tuple}. Returns the empty tuple for
    unmapped depths (i.e. hop2 not found), never touches the network."""
    empty = (None, None, None, None, None, None)

    def _fake(wallet, rpc_counter, ops, *, hop_depth=0, **kwargs):
        return hop_map.get(hop_depth, empty)
    return _fake


@pytest.fixture
def rpc_free_recovery(monkeypatch):
    """_process_row's FULL_WALKBACK branch calls a few zero-RPC DB-recovery
    helpers before hop1 — stub them so tests only exercise the hop1/hop2
    decision itself, matching the existing test suite's own convention of
    isolating one decision at a time."""
    monkeypatch.setattr(ww, "_recover_create_signature_from_db", lambda *a, **k: None)


# ── unit-level predicate tests ───────────────────────────────────────────────

def test_is_disposable_subprov_handoff_true_for_wsol_wrap_close():
    assert _is_disposable_subprov_handoff("WSOL_WRAP_CLOSE") is True


def test_is_disposable_subprov_handoff_true_for_seeded_account_close():
    assert _is_disposable_subprov_handoff("SEEDED_ACCOUNT_CLOSE") is True


def test_is_disposable_subprov_handoff_false_for_plain_xfer():
    assert _is_disposable_subprov_handoff("PLAIN_XFER") is False


def test_is_disposable_subprov_handoff_false_for_unknown_mechanism():
    assert _is_disposable_subprov_handoff("UNKNOWN") is False


def test_is_disposable_subprov_handoff_false_for_none():
    assert _is_disposable_subprov_handoff(None) is False


# ── Test 1: no hop1 — unchanged ──────────────────────────────────────────────

def test_no_hop1_still_terminates_no_attribution_found(rpc_free_recovery):
    conn = _build_ops_db()
    _seed_row(conn, mint=MINT, creator=CREATOR)
    row = _row(conn, MINT)

    with patch.object(ww, "_find_with_evidence", side_effect=_stub_find_with_evidence({})):
        rpc_used = _process_row(conn, row)

    final = _row(conn, MINT)
    assert final["status"] == "complete"
    assert final["intelligence_outcome"] == "NO_ATTRIBUTION_FOUND"
    assert final["subprov"] is None
    assert final["treasury"] is None
    lead = conn.execute(
        "SELECT 1 FROM wt_discovered_subprovs WHERE subprov=?", (HOP1,)
    ).fetchone()
    assert lead is None
    assert rpc_used == 0


# ── Test 2: qualifying disposable handoff, no hop2 ───────────────────────────

def test_qualifying_wsol_wrap_close_hop1_becomes_lineage_gap_lead(rpc_free_recovery):
    conn = _build_ops_db()
    _seed_row(conn, mint=MINT, creator=CREATOR)
    row = _row(conn, MINT)

    hop1_info = (HOP1, HOP1_SIG, 434118204, 1784558726, 0.112139, "WSOL_WRAP_CLOSE")
    with patch.object(ww, "_find_with_evidence",
                       side_effect=_stub_find_with_evidence({1: hop1_info})), \
         patch.object(ww, "_get_tx", return_value=None) as mock_get_tx:
        rpc_used = _process_row(conn, row)

    final = _row(conn, MINT)
    assert final["intelligence_outcome"] == "LINEAGE_GAP"
    assert final["subprov"] == HOP1
    assert final["treasury"] is None
    assert final["funder_wallet"] == HOP1
    assert final["funding_mechanism"] == "WSOL_WRAP_CLOSE"

    lead = conn.execute(
        "SELECT state FROM wt_discovered_subprovs WHERE subprov=?", (HOP1,)
    ).fetchone()
    assert lead is not None
    assert lead["state"] == "PROVISION_CANDIDATE"

    attribution = conn.execute(
        "SELECT 1 FROM watchtower_token_attribution WHERE mint=?", (MINT,)
    ).fetchone()
    assert attribution is None, "mechanism-only evidence must never write confirmed attribution"

    # exactly one RPC call in this path: the existing close-destination tx
    # re-fetch (_get_tx), already present before this fix — no NEW RPC added.
    assert mock_get_tx.call_count == 1
    assert rpc_used == 1


# ── Test 3: ordinary unresolved mechanism, no hop2 — unchanged ──────────────

def test_ordinary_plain_xfer_hop1_still_terminates_no_attribution_found(rpc_free_recovery):
    conn = _build_ops_db()
    mint = "OrdinaryPlainXferMintxxxxxxxxxxxxxxxxxxxxxx"
    creator = "OrdinaryCreatorxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    _seed_row(conn, mint=mint, creator=creator)
    row = _row(conn, mint)

    ordinary_wallet = "OrdinaryFunderWalletxxxxxxxxxxxxxxxxxxxxxxx"
    hop1_info = (ordinary_wallet, "sigordinary", 1, 1700000000, 1.0, "PLAIN_XFER")
    with patch.object(ww, "_find_with_evidence",
                       side_effect=_stub_find_with_evidence({1: hop1_info})):
        rpc_used = _process_row(conn, row)

    final = _row(conn, mint)
    assert final["intelligence_outcome"] == "NO_ATTRIBUTION_FOUND"
    assert final["subprov"] is None
    assert final["treasury"] is None
    # raw funder evidence still persisted (unchanged, pre-existing behaviour)
    assert final["funder_wallet"] == ordinary_wallet
    assert final["funding_mechanism"] == "PLAIN_XFER"

    lead = conn.execute(
        "SELECT 1 FROM wt_discovered_subprovs WHERE subprov=?", (ordinary_wallet,)
    ).fetchone()
    assert lead is None, "an ordinary unresolved transfer must not be promoted to a lead"
    assert rpc_used == 0


# ── Test 4: known subprov — unchanged ────────────────────────────────────────

def test_known_subprov_hop1_unchanged(rpc_free_recovery):
    conn = _build_ops_db()
    mint = "KnownSubprovMintxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    creator = "KnownSubprovCreatorxxxxxxxxxxxxxxxxxxxxxxxx"
    known_subprov = "KnownSubprovWalletxxxxxxxxxxxxxxxxxxxxxxxxx"
    _seed_row(conn, mint=mint, creator=creator)
    now = int(time.time())
    conn.execute(
        "INSERT INTO wt_discovered_subprovs "
        "(subprov, first_creator, creator_count, treasury, treasury_known, "
        " first_seen, last_seen, wrap_close_count, confidence, state) "
        "VALUES (?,?,1,NULL,0,?,?,1,0.9,'CONFIRMED')",
        (known_subprov, creator, now, now),
    )
    conn.commit()
    row = _row(conn, mint)

    hop1_info = (known_subprov, "sigknown", 1, now, 0.5, "WSOL_WRAP_CLOSE")
    with patch.object(ww, "_find_with_evidence",
                       side_effect=_stub_find_with_evidence({1: hop1_info})), \
         patch.object(ww, "_get_tx", return_value=None):
        _process_row(conn, row)

    final = _row(conn, mint)
    # treasury is NULL for this known subprov (no lookup row set) -> LINEAGE_GAP,
    # exactly the pre-existing behaviour of the _is_known_subprov(hop1) branch.
    assert final["intelligence_outcome"] == "LINEAGE_GAP"
    assert final["subprov"] == known_subprov


# ── Test 5: hop2 found but unresolved — unchanged ────────────────────────────

def test_hop2_found_unresolved_still_uses_deep_expansion_path(rpc_free_recovery):
    conn = _build_ops_db()
    mint = "Hop2UnresolvedMintxxxxxxxxxxxxxxxxxxxxxxxxx"
    creator = "Hop2UnresolvedCreatorxxxxxxxxxxxxxxxxxxxxxx"
    hop1_wallet = "Hop2TestHop1Walletxxxxxxxxxxxxxxxxxxxxxxxxx"
    hop2_wallet = "Hop2TestHop2Walletxxxxxxxxxxxxxxxxxxxxxxxxx"
    _seed_row(conn, mint=mint, creator=creator)
    row = _row(conn, mint)

    hop1_info = (hop1_wallet, "sighop1", 1, 1700000000, 0.5, "WSOL_WRAP_CLOSE")
    hop2_info = (hop2_wallet, "sighop2", 1, 1699999000, 2.0, "PLAIN_XFER")
    with patch.object(ww, "_find_with_evidence",
                       side_effect=_stub_find_with_evidence({1: hop1_info, 2: hop2_info})), \
         patch.object(ww, "_get_tx", return_value=None), \
         patch.object(ww, "_surface_treasury_review_lead", return_value="inserted") as mock_lead, \
         patch.object(ww, "_expand_unknown_upstream",
                       return_value={"treasury": None, "state": "ARCHIVAL_GAP",
                                     "deepest": hop2_wallet, "hop_depth": 2}):
        _process_row(conn, row)

    final = _row(conn, mint)
    assert final["intelligence_outcome"] == "LINEAGE_GAP"
    assert final["subprov"] == hop1_wallet
    mock_lead.assert_called_once()


# ── Test 6: attribution safety ───────────────────────────────────────────────

def test_mechanism_only_lead_never_writes_confirmed_attribution(rpc_free_recovery):
    conn = _build_ops_db()
    _seed_row(conn, mint=MINT, creator=CREATOR)
    row = _row(conn, MINT)

    hop1_info = (HOP1, HOP1_SIG, 434118204, 1784558726, 0.112139, "WSOL_WRAP_CLOSE")
    with patch.object(ww, "_find_with_evidence",
                       side_effect=_stub_find_with_evidence({1: hop1_info})), \
         patch.object(ww, "_get_tx", return_value=None):
        _process_row(conn, row)

    assert conn.execute(
        "SELECT COUNT(*) c FROM watchtower_token_attribution WHERE mint=?", (MINT,)
    ).fetchone()["c"] == 0


# ── Test 7: RPC count unchanged by the classification branch itself ─────────

def test_classification_branch_adds_no_rpc_beyond_existing_calls(rpc_free_recovery):
    conn = _build_ops_db()
    _seed_row(conn, mint=MINT, creator=CREATOR)
    row = _row(conn, MINT)

    hop1_info = (HOP1, HOP1_SIG, 434118204, 1784558726, 0.112139, "WSOL_WRAP_CLOSE")
    with patch.object(ww, "_find_with_evidence",
                       side_effect=_stub_find_with_evidence({1: hop1_info})) as mock_find, \
         patch.object(ww, "_get_tx", return_value=None) as mock_get_tx:
        rpc_used = _process_row(conn, row)

    # _find_with_evidence is called exactly twice: hop1 (found) then hop2
    # (the PRE-EXISTING hop2 attempt this branch always makes once hop1 is
    # neither a known subprov nor a known treasury — unchanged by this fix,
    # and returns empty here since the stub has no hop2 mapping). _get_tx is
    # called exactly once (the PRE-EXISTING close-destination re-fetch).
    # X64's classification branch itself (_is_disposable_subprov_handoff)
    # issues zero further _find_with_evidence/_get_tx calls.
    assert mock_find.call_count == 2
    assert mock_get_tx.call_count == 1
    assert rpc_used == 1


# ── Test 8: idempotency ──────────────────────────────────────────────────────

def test_qualifying_completion_is_idempotent_on_replay(rpc_free_recovery):
    """A completed queue row is never re-claimed by drain_batch in
    production (status='complete' is excluded from its SELECT), so true
    idempotency here means: calling _mark_complete twice with the same
    LINEAGE_GAP/hop1 inputs (e.g. a retried write, or a second worker that
    raced the claim and still executed its own completion write) must not
    duplicate the discovery lead, must not downgrade an existing stronger
    state, and must not write confirmed attribution from mechanism
    evidence alone."""
    conn = _build_ops_db()
    _seed_row(conn, mint=MINT, creator=CREATOR)

    ww._mark_complete(conn, MINT, "LINEAGE_GAP", HOP1, None, 1, confirmed_subprov=False)
    ww._mark_complete(conn, MINT, "LINEAGE_GAP", HOP1, None, 1, confirmed_subprov=False)

    lead_count = conn.execute(
        "SELECT COUNT(*) c FROM wt_discovered_subprovs WHERE subprov=?", (HOP1,)
    ).fetchone()["c"]
    assert lead_count == 1

    lead_state = conn.execute(
        "SELECT state FROM wt_discovered_subprovs WHERE subprov=?", (HOP1,)
    ).fetchone()["state"]
    assert lead_state == "PROVISION_CANDIDATE"

    attribution_count = conn.execute(
        "SELECT COUNT(*) c FROM watchtower_token_attribution WHERE mint=?", (MINT,)
    ).fetchone()["c"]
    assert attribution_count == 0

    final = _row(conn, MINT)
    assert final["intelligence_outcome"] == "LINEAGE_GAP"
    assert final["subprov"] == HOP1


def test_replaying_process_row_after_lead_promotion_does_not_downgrade_it(rpc_free_recovery):
    """Documents actual (pre-existing, unchanged-by-X64) behaviour if a
    row's hop1 wallet WAS somehow re-processed after already being promoted
    to a discovery lead: _is_known_subprov(hop1) now returns True (the
    PROVISION_CANDIDATE row from the first pass satisfies it — it is not
    REJECTED), so the pre-existing _is_known_subprov branch is taken, not
    the X64 branch. This is correct, pre-existing routing — not a
    regression introduced here — and does not silently create a SECOND
    discovery lead or a WATCHTOWER_CONFIRMED outcome without a resolved
    treasury."""
    conn = _build_ops_db()
    _seed_row(conn, mint=MINT, creator=CREATOR)
    row = _row(conn, MINT)

    hop1_info = (HOP1, HOP1_SIG, 434118204, 1784558726, 0.112139, "WSOL_WRAP_CLOSE")
    with patch.object(ww, "_find_with_evidence",
                       side_effect=_stub_find_with_evidence({1: hop1_info})), \
         patch.object(ww, "_get_tx", return_value=None):
        _process_row(conn, row)

        conn.execute("UPDATE wt_walkback_queue SET status='running' WHERE mint=?", (MINT,))
        conn.commit()
        row2 = _row(conn, MINT)
        _process_row(conn, row2)

    lead_count = conn.execute(
        "SELECT COUNT(*) c FROM wt_discovered_subprovs WHERE subprov=?", (HOP1,)
    ).fetchone()["c"]
    assert lead_count == 1, "no duplicate lead row from the second pass"

    final = _row(conn, MINT)
    assert final["intelligence_outcome"] == "LINEAGE_GAP"
    assert final["subprov"] == HOP1
    assert final["treasury"] is None, "still no treasury -> never WATCHTOWER_CONFIRMED"


# ── Regression: the exact traced mint, replayed from stored evidence ────────

def test_traced_mint_cvp9vv_regression_classified_as_lineage_gap(rpc_free_recovery):
    """Zero-RPC replay of the real evidence already captured for this mint
    in production (see docs/design/x64/). Confirms the corrected
    classification without re-issuing any RPC call."""
    conn = _build_ops_db()
    _seed_row(conn, mint=MINT, creator=CREATOR)
    row = _row(conn, MINT)

    hop1_info = (HOP1, HOP1_SIG, 434118204, 1784558726, 0.112139, "WSOL_WRAP_CLOSE")
    with patch.object(ww, "_find_with_evidence",
                       side_effect=_stub_find_with_evidence({1: hop1_info})), \
         patch.object(ww, "_get_tx", return_value=None):
        _process_row(conn, row)

    final = _row(conn, MINT)
    assert final["intelligence_outcome"] == "LINEAGE_GAP"
    assert final["subprov"] == HOP1
    assert final["treasury"] is None

    lead = conn.execute(
        "SELECT 1 FROM wt_discovered_subprovs WHERE subprov=?", (HOP1,)
    ).fetchone()
    assert lead is not None, f"{HOP1} must appear as an unresolved provisioning candidate"

    attribution = conn.execute(
        "SELECT 1 FROM watchtower_token_attribution WHERE mint=?", (MINT,)
    ).fetchone()
    assert attribution is None, "WATCHTOWER_CONFIRMED must not be written from mechanism evidence alone"
