"""X29.3 — Funding Boundary Intelligence (renamed from X29.2's Capital Origin).

Covers every required test case:
  1. RPC-backed CEX evidence -> BOUNDED_OBSERVATION
  2. Static CEX match, no signature -> STATIC_MATCH
  3. Funder timestamp after launch -> UNRESOLVED (non-causal)
  4. Valid relay evidence -> boundary_type=RELAY
  5. No bridge evidence remains valid, returns zero safely
  6. PROVEN requires history_exhausted=true
  7. A bounded walk can never become PROVEN by default
  8. WATCHTOWER remains CANONICAL_OPERATOR_REACHED (structural, not this module's concern --
     verified by confirming this module is never consulted on that code path)
  9. Funding Boundary does not override operational attribution
  10. Existing API response remains backwards compatible, plus a computed origin_proven field
  11. UI never displays Initial Funder for bounded evidence (covered at the
      derivation layer: BOUNDED_OBSERVATION/STATIC_MATCH never set history_exhausted=1,
      so the PROVEN-only origin_proven flag can never fire for them)
  12. Backfill is idempotent
  13. Duplicate boundary observations do not create duplicate rows
  14. SOL-to-lamport conversion is exact
  15. Missing or mixed timestamp formats are normalized safely
  16. Analytics exclude STATIC_MATCH from positive relationship counts
  17. No RPC is invoked by API serialization or UI routes (verified by
      inspecting derive_funding_boundary/get_funding_boundary/serialize_funding_boundary
      signatures -- none accept or construct an RPC client)
  18. origin_proven is always exactly (boundary_status == PROVEN), never stored separately
"""
from __future__ import annotations

import sqlite3

import pytest

from src.ops.funding_boundary import (
    ensure_schema, derive_funding_boundary, upsert_funding_boundary, get_funding_boundary,
    serialize_funding_boundary, is_boundary_proven_valid, age_bucket_for,
    STATUS_PROVEN, STATUS_BOUNDED_OBSERVATION, STATUS_STATIC_MATCH, STATUS_UNRESOLVED,
    TYPE_CEX, TYPE_RELAY, TYPE_BRIDGE, TYPE_UNKNOWN,
    REASON_NON_CAUSAL_FUNDING_EVENT, REASON_STATIC_REGISTRY_MATCH_ONLY,
)
from src.ops.funding_boundary_analytics import (
    boundary_wallet_profile, recurring_boundary_wallets, funding_boundary_metrics,
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_schema(c)
    return c


CEX_BOUNDARY = {"address": "CEXWALLET111", "entity_type": "CEX", "name": "Binance", "source": "CEX_ACCOUNTS", "type": "KNOWN_CEX_REACHED"}
RELAY_BOUNDARY = {"address": "RELAYWALLET1", "entity_type": "RELAY", "name": "Jupiter", "source": "INFRASTRUCTURE_ACCOUNTS", "type": "KNOWN_RELAY_REACHED"}


# ─────────────────────── 1. RPC-backed CEX evidence -> BOUNDED_OBSERVATION ───────────────────────

def test_rpc_backed_cex_evidence_becomes_bounded_observation():
    record = derive_funding_boundary(
        mint="MINT1", outcome_type="KNOWN_CEX_REACHED", boundary=CEX_BOUNDARY,
        subject_wallet="CREATOR1", origin_wallet="CEXWALLET111", origin_signature="SIG123",
        origin_block_time_raw=1000, origin_amount_sol=1.5, rpc_used=10,
        launch_block_time_raw=2000,
    )
    assert record["boundary_status"] == STATUS_BOUNDED_OBSERVATION
    assert record["boundary_type"] == TYPE_CEX
    assert record["boundary_signature"] == "SIG123"
    assert record["history_exhausted"] == 0
    assert record["pagination_limit_reached"] == 1


# ─────────────────────── 2. Static CEX match, no signature -> STATIC_MATCH ───────────────────────

def test_static_cex_match_no_signature_becomes_static_match():
    record = derive_funding_boundary(
        mint="MINT2", outcome_type="KNOWN_CEX_REACHED", boundary=CEX_BOUNDARY,
        subject_wallet="CREATOR2", origin_wallet=None, origin_signature=None,
        origin_block_time_raw=None, origin_amount_sol=None, rpc_used=None,
        launch_block_time_raw=2000,
    )
    assert record["boundary_status"] == STATUS_STATIC_MATCH
    assert record["resolution_reason"] == REASON_STATIC_REGISTRY_MATCH_ONLY
    assert record["boundary_signature"] is None
    assert record["boundary_wallet"] == "CEXWALLET111"  # falls back to boundary address


# ─────────────────────── 3. Funder timestamp after launch -> UNRESOLVED (non-causal) ───────────────────────

def test_funder_timestamp_after_launch_becomes_unresolved_non_causal():
    record = derive_funding_boundary(
        mint="MINT3", outcome_type="KNOWN_CEX_REACHED", boundary=CEX_BOUNDARY,
        subject_wallet="CREATOR3", origin_wallet="CEXWALLET111", origin_signature="SIG_FUTURE",
        origin_block_time_raw=5000, origin_amount_sol=2.0, rpc_used=10,
        launch_block_time_raw=1000,  # launch BEFORE the "funding" tx
    )
    assert record["boundary_status"] == STATUS_UNRESOLVED
    assert record["resolution_reason"] == REASON_NON_CAUSAL_FUNDING_EVENT
    # rejected source must not be presented as valid boundary
    assert record["boundary_wallet"] is None
    assert record["boundary_signature"] is None
    assert record["boundary_entity"] is None
    # but preserved in provenance for diagnostics
    assert "CEXWALLET111" in record["provenance"]
    assert "SIG_FUTURE" in record["provenance"]


# ─────────────────────── 4. Valid relay evidence -> boundary_type=RELAY ───────────────────────

def test_relay_evidence_produces_relay_type():
    record = derive_funding_boundary(
        mint="MINT4", outcome_type="KNOWN_RELAY_REACHED", boundary=RELAY_BOUNDARY,
        subject_wallet="CREATOR4", origin_wallet="RELAYWALLET1", origin_signature="SIG_RELAY",
        origin_block_time_raw=100, origin_amount_sol=0.5, rpc_used=8,
        launch_block_time_raw=2000,
    )
    assert record["boundary_type"] == TYPE_RELAY
    assert record["boundary_status"] == STATUS_BOUNDED_OBSERVATION


# ─────────────────────── 5. No bridge evidence remains valid, returns zero safely ───────────────────────

def test_no_bridge_evidence_returns_zero_safely(conn):
    metrics = funding_boundary_metrics(conn)
    assert metrics["funding_boundary_by_type"].get("BRIDGE", 0) == 0
    # unknown outcome_type with no boundary at all
    record = derive_funding_boundary(
        mint="MINT5", outcome_type="KNOWN_BRIDGE_REACHED", boundary=None,
        subject_wallet="CREATOR5", origin_wallet=None, origin_signature=None,
        origin_block_time_raw=None, origin_amount_sol=None, rpc_used=None,
        launch_block_time_raw=None,
    )
    assert record["boundary_status"] == STATUS_UNRESOLVED
    assert record["boundary_type"] == "BRIDGE"


# ─────────────────────── 6/7. PROVEN requires history_exhausted; bounded walk can never default to PROVEN ───────────────────────

def test_proven_requires_history_exhausted_true():
    proven_record = {"boundary_status": STATUS_PROVEN, "history_exhausted": 1}
    not_proven_record = {"boundary_status": STATUS_PROVEN, "history_exhausted": 0}
    assert is_boundary_proven_valid(proven_record) is True
    assert is_boundary_proven_valid(not_proven_record) is False


def test_bounded_walk_never_defaults_to_proven():
    """derive_funding_boundary must NEVER set boundary_status=PROVEN or
    history_exhausted=1 from any combination of inputs it can receive today
    -- the only evidence source (FULL_WALKBACK) cannot prove exhaustion,
    per X29.1.4's investigation."""
    cases = [
        dict(mint="M", outcome_type="KNOWN_CEX_REACHED", boundary=CEX_BOUNDARY, subject_wallet="C",
             origin_wallet="W", origin_signature="S", origin_block_time_raw=1, origin_amount_sol=1.0,
             rpc_used=99999, launch_block_time_raw=99999999),
        dict(mint="M2", outcome_type="KNOWN_RELAY_REACHED", boundary=RELAY_BOUNDARY, subject_wallet="C2",
             origin_wallet=None, origin_signature=None, origin_block_time_raw=None, origin_amount_sol=None,
             rpc_used=None, launch_block_time_raw=None),
    ]
    for kwargs in cases:
        record = derive_funding_boundary(**kwargs)
        assert record["boundary_status"] != STATUS_PROVEN
        assert record["history_exhausted"] == 0


# ─────────────────────── 8/9. WATCHTOWER / operational attribution never overridden ───────────────────────

def test_funding_boundary_module_has_no_outcome_type_writing_capability():
    """Structural guard: this module must have no function that writes to
    wt_attribution_outcomes or accepts/mutates outcome_type — Funding
    Boundary is purely additive and can never override operational
    attribution."""
    import src.ops.funding_boundary as fb_module
    import inspect
    source = inspect.getsource(fb_module)
    assert "UPDATE wt_attribution_outcomes" not in source
    assert "INSERT INTO wt_attribution_outcomes" not in source


def test_watchtower_investigation_pipeline_untouched_by_this_sprint():
    """X29.1.4 confirmed CANONICAL_OPERATOR_REACHED resolves and returns
    before the CEX-boundary branch in derive_outcome() — this sprint must
    not have touched attribution_outcome.py's derive_outcome ordering at
    all. Static guard: the function's source must be unchanged in the
    specific sense that it still checks operator_ids before _boundary()."""
    import inspect
    from src.ops import attribution_outcome
    source = inspect.getsource(attribution_outcome.derive_outcome)
    operator_check_pos = source.find("operator_ids")
    boundary_check_pos = source.find("_boundary(")
    assert operator_check_pos != -1 and boundary_check_pos != -1
    assert operator_check_pos < boundary_check_pos, (
        "operator resolution must still be checked BEFORE the CEX/bridge/relay "
        "boundary branch — this is what keeps WATCHTOWER attribution independent "
        "of funding boundary; X29.2/X29.3 must not have reordered this"
    )


# ─────────────────────── 10. Existing API response remains backwards compatible ───────────────────────

def test_serialize_funding_boundary_null_when_no_record():
    assert serialize_funding_boundary(None) is None


def test_serialize_funding_boundary_shape_includes_origin_proven():
    record = {
        "boundary_status": "BOUNDED_OBSERVATION", "boundary_type": "CEX", "boundary_wallet": "W",
        "boundary_entity": "Binance", "boundary_signature": "SIG", "boundary_block_time": 1000,
        "boundary_age_at_launch_seconds": 10800, "boundary_hop_depth": None,
        "boundary_transfer_lamports": 1200000000, "history_exhausted": 0,
        "pagination_limit_reached": 1, "resolution_reason": "BOUNDED_WALK_COMPLETE",
    }
    result = serialize_funding_boundary(record)
    assert set(result.keys()) == {
        "status", "type", "wallet", "entity", "signature", "block_time",
        "age_at_launch_seconds", "hop_depth", "transfer_lamports",
        "history_exhausted", "pagination_limit_reached", "resolution_reason",
        "origin_proven",
    }
    assert result["status"] == "BOUNDED_OBSERVATION"
    assert result["history_exhausted"] is False
    assert result["pagination_limit_reached"] is True
    assert result["origin_proven"] is False


# ─────────────────────── 12/13. Backfill/upsert idempotency, no duplicate rows ───────────────────────

def test_upsert_funding_boundary_is_idempotent_no_duplicate_rows(conn):
    record = derive_funding_boundary(
        mint="MINTX", outcome_type="KNOWN_CEX_REACHED", boundary=CEX_BOUNDARY,
        subject_wallet="CREATORX", origin_wallet="CEXWALLET111", origin_signature="SIGX",
        origin_block_time_raw=100, origin_amount_sol=1.0, rpc_used=10, launch_block_time_raw=2000,
    )
    upsert_funding_boundary(conn, record)
    upsert_funding_boundary(conn, record)
    upsert_funding_boundary(conn, record)
    conn.commit()
    count = conn.execute(
        "SELECT COUNT(*) FROM wt_funding_boundary WHERE launch_mint=? AND subject_wallet=?",
        ("MINTX", "CREATORX"),
    ).fetchone()[0]
    assert count == 1


def test_upsert_funding_boundary_updates_in_place_on_reclassification(conn):
    """Running the derivation twice with DIFFERENT evidence for the same
    (mint, subject_wallet) key must update the existing row, not insert a
    second one — the row reflects the latest known state."""
    r1 = derive_funding_boundary(
        mint="MINTY", outcome_type="KNOWN_CEX_REACHED", boundary=CEX_BOUNDARY,
        subject_wallet="CREATORY", origin_wallet=None, origin_signature=None,
        origin_block_time_raw=None, origin_amount_sol=None, rpc_used=None, launch_block_time_raw=2000,
    )
    upsert_funding_boundary(conn, r1)
    conn.commit()
    assert get_funding_boundary(conn, "MINTY")["boundary_status"] == STATUS_STATIC_MATCH

    r2 = derive_funding_boundary(
        mint="MINTY", outcome_type="KNOWN_CEX_REACHED", boundary=CEX_BOUNDARY,
        subject_wallet="CREATORY", origin_wallet="CEXWALLET111", origin_signature="SIG_NEW",
        origin_block_time_raw=100, origin_amount_sol=1.0, rpc_used=10, launch_block_time_raw=2000,
    )
    upsert_funding_boundary(conn, r2)
    conn.commit()
    row = get_funding_boundary(conn, "MINTY")
    assert row["boundary_status"] == STATUS_BOUNDED_OBSERVATION
    count = conn.execute("SELECT COUNT(*) FROM wt_funding_boundary WHERE launch_mint='MINTY'").fetchone()[0]
    assert count == 1


# ─────────────────────── 14. SOL-to-lamport conversion is exact ───────────────────────

@pytest.mark.parametrize("sol,expected_lamports", [
    (1.0, 1_000_000_000),
    (0.000000001, 1),
    (1.5, 1_500_000_000),
    (0.655948, 655948000),
])
def test_sol_to_lamport_conversion_is_exact(sol, expected_lamports):
    record = derive_funding_boundary(
        mint="M", outcome_type="KNOWN_CEX_REACHED", boundary=CEX_BOUNDARY, subject_wallet="C",
        origin_wallet="W", origin_signature="S", origin_block_time_raw=1, origin_amount_sol=sol,
        rpc_used=1, launch_block_time_raw=999999,
    )
    assert record["boundary_transfer_lamports"] == expected_lamports
    assert record["boundary_transfer_sol"] == sol


def test_none_amount_produces_none_lamports():
    record = derive_funding_boundary(
        mint="M", outcome_type="KNOWN_CEX_REACHED", boundary=CEX_BOUNDARY, subject_wallet="C",
        origin_wallet=None, origin_signature=None, origin_block_time_raw=None, origin_amount_sol=None,
        rpc_used=None, launch_block_time_raw=None,
    )
    assert record["boundary_transfer_lamports"] is None


# ─────────────────────── 15. Mixed/missing timestamp formats normalized safely ───────────────────────

def test_iso8601_launch_timestamp_normalized_correctly():
    """token_analysis.created_at is stored as either unix epoch or ISO-8601
    (X29.1.4 finding) -- both must produce the same age calculation."""
    record_epoch = derive_funding_boundary(
        mint="M1", outcome_type="KNOWN_CEX_REACHED", boundary=CEX_BOUNDARY, subject_wallet="C",
        origin_wallet="W", origin_signature="S", origin_block_time_raw=1700000000, origin_amount_sol=1.0,
        rpc_used=1, launch_block_time_raw=1700086400,  # +1 day in epoch seconds
    )
    assert record_epoch["boundary_age_at_launch_seconds"] == 86400

    record_iso = derive_funding_boundary(
        mint="M2", outcome_type="KNOWN_CEX_REACHED", boundary=CEX_BOUNDARY, subject_wallet="C",
        origin_wallet="W", origin_signature="S", origin_block_time_raw=1700000000, origin_amount_sol=1.0,
        rpc_used=1, launch_block_time_raw="2023-11-15T22:13:20+00:00",  # same instant as 1700000000+86400
    )
    assert record_iso["boundary_age_at_launch_seconds"] == 86400


def test_missing_launch_timestamp_leaves_age_none_not_fabricated():
    record = derive_funding_boundary(
        mint="M3", outcome_type="KNOWN_CEX_REACHED", boundary=CEX_BOUNDARY, subject_wallet="C",
        origin_wallet="W", origin_signature="S", origin_block_time_raw=1000, origin_amount_sol=1.0,
        rpc_used=1, launch_block_time_raw=None,
    )
    assert record["boundary_age_at_launch_seconds"] is None
    assert record["boundary_status"] == STATUS_BOUNDED_OBSERVATION  # still classified, just no age


def test_unparseable_timestamp_string_produces_none_not_crash():
    record = derive_funding_boundary(
        mint="M4", outcome_type="KNOWN_CEX_REACHED", boundary=CEX_BOUNDARY, subject_wallet="C",
        origin_wallet="W", origin_signature="S", origin_block_time_raw=1000, origin_amount_sol=1.0,
        rpc_used=1, launch_block_time_raw="not-a-real-timestamp",
    )
    assert record["boundary_age_at_launch_seconds"] is None


# ─────────────────────── 16. Analytics exclude STATIC_MATCH from positive relationship counts ───────────────────────

def test_analytics_exclude_static_match_from_positive_counts(conn):
    # One BOUNDED_OBSERVATION (positive) + one STATIC_MATCH (not positive) from the same boundary wallet
    r1 = derive_funding_boundary(
        mint="MA", outcome_type="KNOWN_CEX_REACHED", boundary=CEX_BOUNDARY, subject_wallet="CREATOR_A",
        origin_wallet="CEXWALLET111", origin_signature="SIG_A", origin_block_time_raw=100,
        origin_amount_sol=1.0, rpc_used=10, launch_block_time_raw=2000,
    )
    r2 = derive_funding_boundary(
        mint="MB", outcome_type="KNOWN_CEX_REACHED", boundary=CEX_BOUNDARY, subject_wallet="CREATOR_B",
        origin_wallet=None, origin_signature=None, origin_block_time_raw=None,
        origin_amount_sol=None, rpc_used=None, launch_block_time_raw=2000,
    )
    upsert_funding_boundary(conn, r1)
    upsert_funding_boundary(conn, r2)
    conn.commit()

    profile = boundary_wallet_profile(conn, "CEXWALLET111")
    assert profile["launches_downstream"] == 1  # only the BOUNDED_OBSERVATION mint counts
    assert profile["status_distribution"].get("BOUNDED_OBSERVATION") == 1
    assert profile["status_distribution"].get("STATIC_MATCH") == 1  # visible, but not counted as positive


def test_recurring_boundary_wallets_only_counts_positive_statuses(conn):
    for i in range(3):
        r = derive_funding_boundary(
            mint=f"MINT_R{i}", outcome_type="KNOWN_CEX_REACHED", boundary=CEX_BOUNDARY,
            subject_wallet=f"CREATOR_R{i}", origin_wallet="RECURRING_WALLET", origin_signature=f"SIG{i}",
            origin_block_time_raw=100, origin_amount_sol=1.0, rpc_used=10, launch_block_time_raw=2000,
        )
        upsert_funding_boundary(conn, r)
    # a 4th, STATIC_MATCH-only mint must NOT count toward the threshold
    static_r = derive_funding_boundary(
        mint="MINT_STATIC", outcome_type="KNOWN_CEX_REACHED", boundary={
            "address": "RECURRING_WALLET", "entity_type": "CEX", "name": "Binance", "source": "x", "type": "KNOWN_CEX_REACHED"
        }, subject_wallet="CREATOR_STATIC", origin_wallet=None, origin_signature=None,
        origin_block_time_raw=None, origin_amount_sol=None, rpc_used=None, launch_block_time_raw=2000,
    )
    upsert_funding_boundary(conn, static_r)
    conn.commit()

    recurring = recurring_boundary_wallets(conn, min_launches=3)
    wallets = {p["boundary_wallet"] for p in recurring}
    assert "RECURRING_WALLET" in wallets
    profile = next(p for p in recurring if p["boundary_wallet"] == "RECURRING_WALLET")
    assert profile["launches_downstream"] == 3  # NOT 4 — the static match doesn't count


# ─────────────────────── 17. No RPC invoked anywhere in this module ───────────────────────

def test_no_rpc_client_imported_or_constructed():
    import inspect
    import src.ops.funding_boundary as fb_module
    import src.ops.funding_boundary_analytics as analytics_module
    for module in (fb_module, analytics_module):
        source = inspect.getsource(module)
        assert "requests." not in source
        assert "getSignaturesForAddress" not in source
        assert "getTransaction" not in source
        assert "helius" not in source.lower()


# ─────────────────────── 18. origin_proven is always exactly (boundary_status == PROVEN) ───────────────────────

@pytest.mark.parametrize("status,expected", [
    (STATUS_PROVEN, True),
    (STATUS_BOUNDED_OBSERVATION, False),
    (STATUS_STATIC_MATCH, False),
    (STATUS_UNRESOLVED, False),
])
def test_origin_proven_computed_never_stored_separately(status, expected):
    record = {"boundary_status": status}
    result = serialize_funding_boundary(record)
    assert result["origin_proven"] is expected


# ─────────────────────── Metrics ───────────────────────

def test_funding_boundary_metrics_zero_state_on_empty_table(conn):
    metrics = funding_boundary_metrics(conn)
    assert metrics["funding_boundary_total"] == 0
    assert metrics["funding_boundary_by_status"] == {}


def test_age_bucket_boundaries():
    assert age_bucket_for(None) == "unknown"
    assert age_bucket_for(0) == "<=1d"
    assert age_bucket_for(86400) == "<=1d"
    assert age_bucket_for(86400 * 2) == "1-7d"
    assert age_bucket_for(86400 * 8) == "8-30d"
    assert age_bucket_for(86400 * 31) == "31-100d"
    assert age_bucket_for(86400 * 101) == ">100d"
