"""X67.18 -- Unit tests for the shared canonical eligibility predicate
(watchtower_canonical_predicate.py), per X67.17's test specification
(Sec. 11, items 1-12, 16-17 -- the items that don't require live
database/backtest data, which are covered separately by
scripts/x67_18_backtest.py and its own report).
"""
from src.ops.watchtower_canonical_predicate import (
    CanonicalEvidenceInput,
    ConflictSignal,
    MechanismEvidence,
    SessionEvidence,
    TreasuryConfirmationEvidence,
    evaluate_watchtower_canonical_eligibility,
    REASON_TO_DECISION,
)


def _evidence(
    *, treasury_confirmed=True, session_topology="DIRECT", session_exists=True,
    session_state="EXPIRED", relay_status="NONE", relay_wallet=None,
    mechanism="WSOL_WRAP_CLOSE", evidence_tier="WALKBACK_RECOVERED",
    conflicts=None,
):
    return CanonicalEvidenceInput(
        mint="TestMint111111111111111111111111111111111",
        treasury_wallet="Treasury111111111111111111111111111111111",
        subprov_wallet="Subprov111111111111111111111111111111111",
        creator_wallet="Creator111111111111111111111111111111111",
        treasury_confirmation=TreasuryConfirmationEvidence(confirmed=treasury_confirmed),
        session_evidence=SessionEvidence(
            exists=session_exists, state=session_state, topology=session_topology,
            relay_wallet=relay_wallet, relay_status=relay_status,
        ),
        mechanism_evidence=MechanismEvidence(value=mechanism, evidence_tier=evidence_tier),
        conflict_evidence=conflicts or [],
    )


# --- Test 1: genuine account-close accepted ---

def test_genuine_wsol_wrap_close_accepted():
    result = evaluate_watchtower_canonical_eligibility(_evidence(
        mechanism="WSOL_WRAP_CLOSE", evidence_tier="WALKBACK_RECOVERED",
    ))
    assert result.eligible is True
    assert result.decision == "ACCEPTED"
    assert result.decision_reason == "CANONICAL_CONFIRMED"


# --- Test 2: genuine seeded-account accepted ---

def test_genuine_seeded_account_close_accepted():
    result = evaluate_watchtower_canonical_eligibility(_evidence(
        mechanism="SEEDED_ACCOUNT_CLOSE", evidence_tier="HISTORICAL_RECONSTRUCTION",
    ))
    assert result.eligible is True
    assert result.decision_reason == "CANONICAL_CONFIRMED"


# --- Test 3: genuine plain-transfer accepted (RPC-verified) ---

def test_genuine_plain_xfer_accepted_when_rpc_verified():
    result = evaluate_watchtower_canonical_eligibility(_evidence(
        mechanism="PLAIN_XFER", evidence_tier="RPC_VERIFIED",
    ))
    assert result.eligible is True
    assert result.decision_reason == "CANONICAL_CONFIRMED"


def test_plain_xfer_walkback_only_is_insufficient_not_accepted():
    """A stored PLAIN_XFER label that was never RPC-verified must NOT pass --
    unlike account-close mechanisms, plain transfers are not self-verifying
    by instruction shape (X67.17 S1c/S2)."""
    result = evaluate_watchtower_canonical_eligibility(_evidence(
        mechanism="PLAIN_XFER", evidence_tier="WALKBACK_RECOVERED",
    ))
    assert result.eligible is False
    assert result.decision == "INSUFFICIENT_EVIDENCE"
    assert result.decision_reason == "EVIDENCE_INSUFFICIENT"


# --- Test 4: relay-assisted accepted when evidence sufficient ---

def test_relay_assisted_accepted_when_relay_unclassified_but_no_conflict():
    result = evaluate_watchtower_canonical_eligibility(_evidence(
        mechanism="PLAIN_XFER", evidence_tier="RPC_VERIFIED",
        session_topology="RELAY_ASSISTED", relay_status="EXTERNAL_UNCLASSIFIED",
        relay_wallet="Relay11111111111111111111111111111111111",
    ))
    assert result.eligible is True
    assert result.decision_reason == "CANONICAL_CONFIRMED"
    assert result.relay_wallet == "Relay11111111111111111111111111111111111"
    assert result.relay_classification == "EXTERNAL_UNCLASSIFIED"


# --- Test 5: exchange-boundary conflict on the relay path -> review, not reject ---

def test_relay_assisted_exchange_pattern_requires_review():
    result = evaluate_watchtower_canonical_eligibility(_evidence(
        mechanism="PLAIN_XFER", evidence_tier="RPC_VERIFIED",
        session_topology="RELAY_ASSISTED", relay_status="EXTERNAL_EXCHANGE_PATTERN",
        relay_wallet="Relay11111111111111111111111111111111111",
    ))
    assert result.eligible is False
    assert result.decision == "REVIEW_REQUIRED"
    assert result.decision_reason == "SESSION_RELAY_ASSISTED_EXCHANGE_SIGNATURE"
    assert result.review_required is True


# --- Test 6: exchange-boundary rejected when RPC-confirmed ---

def test_exchange_boundary_rejected_when_rpc_confirmed():
    result = evaluate_watchtower_canonical_eligibility(_evidence(
        conflicts=[ConflictSignal(code="EXCHANGE_BOUNDARY", rpc_confirmed=True)],
    ))
    assert result.eligible is False
    assert result.decision == "REJECTED"
    assert result.decision_reason == "CONFLICT_EXCHANGE_BOUNDARY"


# --- Test 7: exchange-boundary NOT rejected on a bare closure note ---

def test_exchange_boundary_unconfirmed_is_insufficient_not_rejected():
    """Regression guard for X67.14's finding: a free-text 'Binance 2' closure
    note with no structured RPC evidence must not be treated as a fatal,
    settled rejection."""
    result = evaluate_watchtower_canonical_eligibility(_evidence(
        conflicts=[ConflictSignal(code="EXCHANGE_BOUNDARY", rpc_confirmed=False)],
    ))
    assert result.eligible is False
    assert result.decision == "INSUFFICIENT_EVIDENCE"
    assert result.decision_reason == "EVIDENCE_INSUFFICIENT"
    assert "rpc_confirmed_exchange_attribution" in result.missing_evidence


# --- Test 8: role collision (within-mint) rejected ---

def test_role_collision_within_mint_rejected():
    result = evaluate_watchtower_canonical_eligibility(_evidence(
        conflicts=[ConflictSignal(code="ROLE_COLLISION", detail="treasury==subprov")],
    ))
    assert result.eligible is False
    assert result.decision == "REJECTED"
    assert result.decision_reason == "CONFLICT_ROLE_COLLISION"


# --- Test 9: role collision (cross-mint variant, X67.14's CVdByCD7 finding) ---

def test_role_collision_cross_mint_variant_rejected():
    """The adapter layer is responsible for detecting the cross-mint overlap
    and reporting it as a ROLE_COLLISION conflict signal -- the predicate
    itself treats it identically to a same-mint collision."""
    result = evaluate_watchtower_canonical_eligibility(_evidence(
        conflicts=[ConflictSignal(code="ROLE_COLLISION", detail="cross-mint subprov==creator elsewhere")],
    ))
    assert result.eligible is False
    assert result.decision_reason == "CONFLICT_ROLE_COLLISION"


# --- Test 10: mechanism conflict, redecode not yet attempted -> review, not reject ---

def test_mechanism_conflict_unattempted_redecode_is_insufficient():
    result = evaluate_watchtower_canonical_eligibility(_evidence(
        conflicts=[ConflictSignal(code="MECHANISM_CONFLICT", redecode_attempted=False)],
    ))
    assert result.eligible is False
    assert result.decision == "INSUFFICIENT_EVIDENCE"
    assert result.decision_reason == "EVIDENCE_INSUFFICIENT"
    assert "mechanism_redecode" in result.missing_evidence


# --- Test 11: mechanism conflict, redecode attempted and still disagrees -> reject ---

def test_mechanism_conflict_after_redecode_rejected():
    result = evaluate_watchtower_canonical_eligibility(_evidence(
        conflicts=[ConflictSignal(code="MECHANISM_CONFLICT", redecode_attempted=True)],
    ))
    assert result.eligible is False
    assert result.decision == "REJECTED"
    assert result.decision_reason == "CONFLICT_MECHANISM"


# --- Test 12: insufficient evidence (mechanism unverified, no conflict) ---

def test_unverified_mechanism_is_insufficient():
    result = evaluate_watchtower_canonical_eligibility(_evidence(
        mechanism="UNVERIFIED", evidence_tier="WALKBACK_RECOVERED",
    ))
    assert result.eligible is False
    assert result.decision == "INSUFFICIENT_EVIDENCE"
    assert result.decision_reason == "EVIDENCE_INSUFFICIENT"


def test_no_session_evidence_is_insufficient():
    result = evaluate_watchtower_canonical_eligibility(_evidence(
        session_exists=False, session_state="ABSENT", session_topology="UNKNOWN",
    ))
    assert result.eligible is False
    assert result.decision_reason == "SESSION_INVALID"


def test_identity_unconfirmed_rejected():
    result = evaluate_watchtower_canonical_eligibility(_evidence(treasury_confirmed=False))
    assert result.eligible is False
    assert result.decision == "REJECTED"
    assert result.decision_reason == "IDENTITY_UNCONFIRMED"


def test_lineage_conflict_rejected():
    result = evaluate_watchtower_canonical_eligibility(_evidence(
        conflicts=[ConflictSignal(code="LINEAGE_CONFLICT")],
    ))
    assert result.eligible is False
    assert result.decision_reason == "CONFLICT_LINEAGE"


def test_multi_source_relay_requires_review_not_rejection():
    result = evaluate_watchtower_canonical_eligibility(_evidence(
        conflicts=[ConflictSignal(code="MULTI_SOURCE_RELAY")],
    ))
    assert result.eligible is False
    assert result.decision == "REVIEW_REQUIRED"
    assert result.decision_reason == "CONFLICT_MULTI_SOURCE_RELAY"


# --- Test 16: high session-volume soft flag alone never blocks ---

def test_shared_relay_session_volume_alone_never_blocks():
    """Regression guard for X67.4's established precedent: session-count
    alone (informational conflict) must never cause rejection."""
    result = evaluate_watchtower_canonical_eligibility(_evidence(
        conflicts=[ConflictSignal(code="SHARED_RELAY_SESSION_VOLUME", detail="session_count=288")],
    ))
    assert result.eligible is True
    assert result.decision == "ACCEPTED"
    assert result.decision_reason == "CANONICAL_CONFIRMED"
    assert "SHARED_RELAY_SESSION_VOLUME" in result.conflicts


# --- Test 17: idempotent / pure evaluation ---

def test_predicate_is_pure_and_deterministic():
    ev = _evidence(mechanism="WSOL_WRAP_CLOSE")
    r1 = evaluate_watchtower_canonical_eligibility(ev)
    r2 = evaluate_watchtower_canonical_eligibility(ev)
    assert r1 == r2


# --- Reason-code catalogue self-consistency ---

def test_every_reason_code_maps_to_exactly_one_decision():
    # dict values are inherently single-valued per key; this test guards
    # against a future edit accidentally introducing ambiguity by asserting
    # the full, expected catalogue shape explicitly.
    assert REASON_TO_DECISION["CANONICAL_CONFIRMED"] == "ACCEPTED"
    assert REASON_TO_DECISION["IDENTITY_UNCONFIRMED"] == "REJECTED"
    assert REASON_TO_DECISION["CONFLICT_EXCHANGE_BOUNDARY"] == "REJECTED"
    assert REASON_TO_DECISION["CONFLICT_ROLE_COLLISION"] == "REJECTED"
    assert REASON_TO_DECISION["CONFLICT_MECHANISM"] == "REJECTED"
    assert REASON_TO_DECISION["CONFLICT_LINEAGE"] == "REJECTED"
    assert REASON_TO_DECISION["CREATOR_FUNDING_UNSUPPORTED"] == "REJECTED"
    assert REASON_TO_DECISION["EVIDENCE_INSUFFICIENT"] == "INSUFFICIENT_EVIDENCE"
    assert REASON_TO_DECISION["SESSION_INVALID"] == "INSUFFICIENT_EVIDENCE"
    assert REASON_TO_DECISION["SESSION_RELAY_ASSISTED_UNCLASSIFIED"] == "REVIEW_REQUIRED"
    assert REASON_TO_DECISION["SESSION_RELAY_ASSISTED_EXCHANGE_SIGNATURE"] == "REVIEW_REQUIRED"
    assert REASON_TO_DECISION["CONFLICT_MULTI_SOURCE_RELAY"] == "REVIEW_REQUIRED"
    assert REASON_TO_DECISION["MANUAL_REVIEW_REQUIRED"] == "REVIEW_REQUIRED"
