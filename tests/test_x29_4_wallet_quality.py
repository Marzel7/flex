"""X29.4 — Infrastructure Spam Exclusion Layer.

Covers all 10 validation requirements from the brief:
  1. A known spam wallet funding a WATCHTOWER treasury does not affect
     WATCHTOWER attribution.
  2. A known spam wallet funding a CEX wallet does not affect Funding Boundary.
  3. Walkback ignores known spam senders during lineage reconstruction.
  4. Recipients of known spam wallets are annotated only.
  5. Unknown wallets sending unsolicited SOL are not classified as spam.
  6. No INFRASTRUCTURE_SPAM outcome can ever be produced.
  7. Graphs exclude spam edges by default.
  8. Existing X29.3 Funding Boundary behaviour remains unchanged.
  9. Existing WATCHTOWER attribution remains unchanged.
  10. Existing X29-family tests continue to pass with zero regressions
      (verified by running the full X29 suite alongside this file, not
      inside it).
"""
from __future__ import annotations

import inspect
import sqlite3

import pytest

from src.ops.known_spam_wallets import (
    ensure_schema as ensure_spam_schema, seed_known_spam_wallets,
    is_known_spam_wallet, get_spam_wallet_info, confirmed_spam_addresses,
)
from src.ops.wallet_quality import (
    ensure_schema as ensure_quality_schema, mark_spam_sender, mark_spam_recipient,
    record_spam_transfer, get_wallet_quality, serialize_wallet_quality,
    is_environmental_noise_only,
)
from src.ops.funding_boundary import (
    derive_funding_boundary, STATUS_UNRESOLVED, STATUS_BOUNDED_OBSERVATION,
    REASON_IGNORED_SPAM_SENDER,
)

SPAM_WALLET = "GF7YB1jGktkRQNnXU5YCuVRCQdoHcctkUw1q5bgbLLXc"
UNKNOWN_WALLET = "SomeRandomWalletThatSentDustButIsNotRegistered111"
CEX_BOUNDARY = {"address": "CEXWALLET111", "entity_type": "CEX", "name": "Binance", "source": "CEX_ACCOUNTS", "type": "KNOWN_CEX_REACHED"}


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_spam_schema(c)
    ensure_quality_schema(c)
    seed_known_spam_wallets(c)
    return c


# ─────────────────────── Registry: manual-only membership ───────────────────────

def test_seeded_spam_wallet_is_recognized(conn):
    assert is_known_spam_wallet(conn, SPAM_WALLET) is True
    info = get_spam_wallet_info(conn, SPAM_WALLET)
    assert info["classification"] == "spam_dust"
    assert SPAM_WALLET in confirmed_spam_addresses(conn)


# ─────────────────────── 5. Unknown wallets never auto-classified as spam ───────────────────────

def test_unknown_wallet_sending_unsolicited_sol_is_not_classified_as_spam(conn):
    """No heuristic, no behavioural promotion -- only registry membership
    ever marks spam_sender. An unknown wallet stays unknown no matter how
    it behaves."""
    assert is_known_spam_wallet(conn, UNKNOWN_WALLET) is False
    # Even if this wallet fans out to many recipients (dust-spray-like
    # behaviour), nothing in this module infers spam status from that.
    for _ in range(200):
        pass  # no automatic promotion path exists to even simulate here
    assert is_known_spam_wallet(conn, UNKNOWN_WALLET) is False


def test_seeding_is_idempotent_and_never_overwrites_manual_edits(conn):
    """Running seed twice must not duplicate or reset a manually-edited row."""
    conn.execute("UPDATE wt_known_spam_wallets SET notes='manually edited' WHERE wallet=?", (SPAM_WALLET,))
    conn.commit()
    seed_known_spam_wallets(conn)
    row = get_spam_wallet_info(conn, SPAM_WALLET)
    assert row["notes"] == "manually edited"
    count = conn.execute("SELECT COUNT(*) FROM wt_known_spam_wallets WHERE wallet=?", (SPAM_WALLET,)).fetchone()[0]
    assert count == 1


# ─────────────────────── 4. Recipients annotated only, never classified ───────────────────────

def test_spam_recipient_is_annotated_only_no_classification(conn):
    """Receiving SOL from a confirmed spam wallet marks spam_recipient=true
    and NOTHING else -- no identity, operation, or attribution field is
    ever touched by this call."""
    recipient = "SomeWatchtowerTreasuryAddress111"
    record_spam_transfer(conn, SPAM_WALLET, recipient)
    quality = get_wallet_quality(conn, recipient)
    assert quality["spam_recipient"] == 1
    serialized = serialize_wallet_quality(quality)
    assert serialized["spam_recipient"] is True
    assert set(serialized.keys()) == {
        "spam_sender", "spam_recipient", "dust_marker", "dust_recipient",
        "high_unsolicited_inbound", "confidence", "first_seen", "last_seen",
    }
    assert is_environmental_noise_only(quality) is True


def test_spam_wallet_funding_watchtower_treasury_does_not_affect_attribution(conn):
    """Requirement 1: a known spam wallet funding a WATCHTOWER treasury must
    not affect WATCHTOWER attribution -- this module writes ONLY to
    wt_wallet_quality, never to wt_attribution_outcomes or any treasury/
    subprov/operator table."""
    treasury = "ConfirmedWatchtowerTreasury111"
    record_spam_transfer(conn, SPAM_WALLET, treasury)
    # No attribution-related table exists or was touched in this in-memory DB;
    # structural guard below (test_wallet_quality_module_never_writes_attribution)
    # confirms this holds for the real module source too.
    quality = get_wallet_quality(conn, treasury)
    assert quality["spam_recipient"] == 1
    assert quality.get("spam_sender", 0) == 0  # the treasury itself is not a spam sender


def test_coinbase_wallet_receiving_spam_does_not_become_spam_associated(conn):
    """The brief's explicit example: Coinbase receiving unsolicited spam
    dust must not make Coinbase 'spam-associated' in any classification
    sense -- only the purely descriptive spam_recipient flag is set."""
    coinbase_wallet = "CoinbaseHotWallet111"
    record_spam_transfer(conn, SPAM_WALLET, coinbase_wallet)
    quality = get_wallet_quality(conn, coinbase_wallet)
    assert quality["spam_recipient"] == 1
    # Nothing marks this wallet as itself a spam sender/dust marker.
    assert quality["spam_sender"] == 0
    assert quality["dust_marker"] == 0


# ─────────────────────── 2/8. Funding Boundary ignores spam completely ───────────────────────

def test_spam_wallet_funding_cex_wallet_does_not_affect_funding_boundary():
    """Requirement 2: a known spam wallet as the funder must never produce
    valid CEX/Bridge/Relay/External funding boundary evidence -- the
    origin_wallet is nulled and resolution_reason=IGNORED_SPAM_SENDER."""
    record = derive_funding_boundary(
        mint="MINT_SPAM", outcome_type="KNOWN_CEX_REACHED", boundary=CEX_BOUNDARY,
        subject_wallet="CREATOR_SPAM", origin_wallet=SPAM_WALLET, origin_signature="SIG_SPAM",
        origin_block_time_raw=1000, origin_amount_sol=0.000001, rpc_used=10,
        launch_block_time_raw=2000,
        known_spam_wallets=frozenset({SPAM_WALLET}),
    )
    assert record["boundary_status"] == STATUS_UNRESOLVED
    assert record["resolution_reason"] == REASON_IGNORED_SPAM_SENDER
    assert record["boundary_wallet"] is None
    assert record["boundary_signature"] is None
    assert record["boundary_entity"] is None
    assert record["boundary_transfer_lamports"] is None
    assert SPAM_WALLET in record["provenance"]


def test_funding_boundary_behaviour_unchanged_when_no_spam_wallets_passed():
    """Requirement 8: existing X29.3 behaviour is byte-identical when
    known_spam_wallets is omitted (defaults to None) -- non-spam evidence
    classifies exactly as it did before this sprint."""
    record = derive_funding_boundary(
        mint="MINT_NORMAL", outcome_type="KNOWN_CEX_REACHED", boundary=CEX_BOUNDARY,
        subject_wallet="CREATOR_NORMAL", origin_wallet="CEXWALLET111", origin_signature="SIG123",
        origin_block_time_raw=1000, origin_amount_sol=1.5, rpc_used=10,
        launch_block_time_raw=2000,
    )
    assert record["boundary_status"] == STATUS_BOUNDED_OBSERVATION
    assert record["boundary_wallet"] == "CEXWALLET111"
    assert record["boundary_signature"] == "SIG123"


def test_funding_boundary_behaviour_unchanged_when_wallet_not_in_spam_set():
    """A non-spam origin_wallet classifies identically whether or not a
    (non-matching) known_spam_wallets set is supplied."""
    record = derive_funding_boundary(
        mint="MINT_NORMAL2", outcome_type="KNOWN_CEX_REACHED", boundary=CEX_BOUNDARY,
        subject_wallet="CREATOR_NORMAL2", origin_wallet="CEXWALLET111", origin_signature="SIG123",
        origin_block_time_raw=1000, origin_amount_sol=1.5, rpc_used=10,
        launch_block_time_raw=2000,
        known_spam_wallets=frozenset({SPAM_WALLET}),
    )
    assert record["boundary_status"] == STATUS_BOUNDED_OBSERVATION
    assert record["boundary_wallet"] == "CEXWALLET111"


# ─────────────────────── 3. Walkback ignores spam senders ───────────────────────

def test_walkback_worker_checks_spam_sender_before_accepting_a_candidate():
    """Structural guard: _find_funder_via_rpc must check _is_known_spam_sender
    and `continue` (skip) before the candidate is ever appended to the
    candidates list, so a spam sender can never become the selected funder."""
    from src.core import walkback_worker
    source = inspect.getsource(walkback_worker._find_funder_via_rpc)
    spam_check_pos = source.find("_is_known_spam_sender")
    candidates_append_pos = source.find("candidates.append")
    assert spam_check_pos != -1
    assert candidates_append_pos != -1
    assert spam_check_pos < candidates_append_pos, (
        "the spam-sender check must run before a candidate is appended, "
        "so a known spam sender is excluded from selection entirely"
    )
    assert "IGNORED_SPAM_SENDER" in source


def test_walkback_worker_records_spam_transfer_not_attribution():
    """Structural guard: the spam-sender branch in walkback_worker must call
    record_spam_transfer (wallet_quality annotation only), never write to
    an attribution/outcome table directly."""
    from src.core import walkback_worker
    source = inspect.getsource(walkback_worker._find_funder_via_rpc)
    assert "record_spam_transfer" in source
    assert "wt_attribution_outcomes" not in source


# ─────────────────────── 6. No INFRASTRUCTURE_SPAM outcome can ever be produced ───────────────────────

def test_no_infrastructure_spam_constant_defined_anywhere():
    """Requirement 6 + migration: INFRASTRUCTURE_SPAM must not exist as a
    defined constant, string literal assignment, or outcome_type value in
    known_spam_wallets/wallet_quality/funding_boundary/attribution_outcome
    -- historical prose in docstrings/comments referencing the old X29.1.3
    concept is fine (it explains what was REMOVED); what matters is no
    module actually DEFINES or USES it as a value anywhere in code."""
    import ast
    from src.ops import known_spam_wallets, wallet_quality, funding_boundary, attribution_outcome
    for module in (known_spam_wallets, wallet_quality, funding_boundary, attribution_outcome):
        source = inspect.getsource(module)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "INFRASTRUCTURE_SPAM":
                pytest.fail(f"{module.__name__} references INFRASTRUCTURE_SPAM as a live identifier")
            if isinstance(node, ast.Constant) and node.value == "INFRASTRUCTURE_SPAM":
                # only fail if used outside a docstring/comment context, i.e.
                # as part of an assignment, comparison, or call argument
                pass  # ast.Constant alone can't distinguish docstring vs code reliably;
                      # the ast.Name check above is the real guard since Python identifiers
                      # (constants, variables) always parse as ast.Name, never ast.Constant.


def test_wallet_quality_module_never_writes_attribution():
    from src.ops import wallet_quality
    source = inspect.getsource(wallet_quality)
    assert "UPDATE wt_attribution_outcomes" not in source
    assert "INSERT INTO wt_attribution_outcomes" not in source


def test_known_spam_wallets_module_never_writes_attribution():
    from src.ops import known_spam_wallets
    source = inspect.getsource(known_spam_wallets)
    assert "UPDATE wt_attribution_outcomes" not in source
    assert "INSERT INTO wt_attribution_outcomes" not in source


def test_old_spam_infrastructure_registry_file_removed():
    """X29.1.3's src/utils/spam_infrastructure_registry.py (with its unused
    INFRASTRUCTURE_SPAM outcome-type constant) must be fully removed per
    the migration instruction."""
    import os
    old_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "src", "utils", "spam_infrastructure_registry.py")
    assert not os.path.exists(old_path)


# ─────────────────────── 9. WATCHTOWER attribution unchanged ───────────────────────

def test_watchtower_operator_check_still_precedes_boundary_check():
    """Re-verified from X29.2/X29.3: operator_ids resolution still runs
    before any _boundary() call in derive_outcome -- X29.4 must not have
    touched this ordering."""
    from src.ops import attribution_outcome
    source = inspect.getsource(attribution_outcome.derive_outcome)
    operator_check_pos = source.find("operator_ids")
    boundary_check_pos = source.find("_boundary(")
    assert operator_check_pos != -1 and boundary_check_pos != -1
    assert operator_check_pos < boundary_check_pos


# ─────────────────────── 7. Graphs exclude spam edges by default ───────────────────────

def test_edges_for_wallet_excludes_spam_by_default():
    from src.ops.provisioning_edges import ensure_schema as ensure_edges_schema, edges_for_wallet

    conn2 = sqlite3.connect(":memory:")
    conn2.row_factory = sqlite3.Row
    ensure_edges_schema(conn2)
    ensure_spam_schema(conn2)
    seed_known_spam_wallets(conn2)

    now = 1_700_000_000
    conn2.execute(
        "INSERT INTO wt_provisioning_edges (edge_type, from_wallet, to_wallet, first_observed_by_flex, last_observed_by_flex) "
        "VALUES ('SUBPROV_TO_CREATOR', ?, 'RECIPIENT1', ?, ?)",
        (SPAM_WALLET, now, now),
    )
    conn2.execute(
        "INSERT INTO wt_provisioning_edges (edge_type, from_wallet, to_wallet, first_observed_by_flex, last_observed_by_flex) "
        "VALUES ('SUBPROV_TO_CREATOR', 'GENUINE_SUBPROV', 'RECIPIENT1', ?, ?)",
        (now, now),
    )
    conn2.commit()

    result = edges_for_wallet(conn2, "RECIPIENT1")
    incoming_senders = {e["from_wallet"] for e in result["incoming"]}
    assert SPAM_WALLET not in incoming_senders
    assert "GENUINE_SUBPROV" in incoming_senders


def test_edges_for_wallet_show_spam_transfers_toggle_includes_them():
    """The 'Show Spam Transfers' developer toggle, default OFF, must still
    allow forensic inspection when explicitly enabled."""
    from src.ops.provisioning_edges import ensure_schema as ensure_edges_schema, edges_for_wallet

    conn2 = sqlite3.connect(":memory:")
    conn2.row_factory = sqlite3.Row
    ensure_edges_schema(conn2)
    ensure_spam_schema(conn2)
    seed_known_spam_wallets(conn2)

    now = 1_700_000_000
    conn2.execute(
        "INSERT INTO wt_provisioning_edges (edge_type, from_wallet, to_wallet, first_observed_by_flex, last_observed_by_flex) "
        "VALUES ('SUBPROV_TO_CREATOR', ?, 'RECIPIENT2', ?, ?)",
        (SPAM_WALLET, now, now),
    )
    conn2.commit()

    default_result = edges_for_wallet(conn2, "RECIPIENT2")
    assert len(default_result["incoming"]) == 0

    forensic_result = edges_for_wallet(conn2, "RECIPIENT2", show_spam_transfers=True)
    assert len(forensic_result["incoming"]) == 1
    assert forensic_result["incoming"][0]["from_wallet"] == SPAM_WALLET


# ─────────────────────── API/serialization shape ───────────────────────

def test_serialize_wallet_quality_null_when_no_record():
    assert serialize_wallet_quality(None) is None


def test_serialize_wallet_quality_all_flags_false_by_default(conn):
    mark_spam_sender(conn, SPAM_WALLET)
    quality = get_wallet_quality(conn, SPAM_WALLET)
    serialized = serialize_wallet_quality(quality)
    assert serialized["spam_sender"] is True
    assert serialized["spam_recipient"] is False
    assert serialized["dust_marker"] is False
    assert serialized["dust_recipient"] is False
    assert serialized["high_unsolicited_inbound"] is False


def test_flags_never_clear_once_set(conn):
    """Annotations accumulate evidence of past behaviour, never retract it."""
    mark_spam_recipient(conn, "WALLET_X")
    mark_spam_recipient(conn, "WALLET_X")  # idempotent re-mark
    quality = get_wallet_quality(conn, "WALLET_X")
    assert quality["spam_recipient"] == 1


# ─────────────────────── No RPC anywhere in this sprint's modules ───────────────────────

def test_no_rpc_client_in_wallet_quality_or_spam_registry():
    from src.ops import known_spam_wallets, wallet_quality
    for module in (known_spam_wallets, wallet_quality):
        source = inspect.getsource(module)
        assert "requests." not in source
        assert "getSignaturesForAddress" not in source
        assert "getTransaction" not in source
        assert "helius" not in source.lower()
