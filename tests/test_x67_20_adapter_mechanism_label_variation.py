"""X67.20 -- Regression tests for the adapter refinement distinguishing
MECHANISM_LABEL_VARIATION (same signature, different taxonomy label -- must
NOT gate the decision) from MECHANISM_CONFLICT (different signatures --
remains a hard, unresolved-until-redecode gate).

Covers both the predicate-level vocabulary addition (informational-only,
zero decision-logic change) and the adapter-level signature comparison
(watchtower_canonical_adapters.py's _gather_common_evidence).
"""
import sqlite3

import pytest

from src.ops.watchtower_canonical_predicate import (
    CanonicalEvidenceInput,
    ConflictSignal,
    MechanismEvidence,
    SessionEvidence,
    TreasuryConfirmationEvidence,
    evaluate_watchtower_canonical_eligibility,
)
from src.ops.watchtower_canonical_adapters import build_evidence_for_registry_row


def _base_evidence(conflicts):
    return CanonicalEvidenceInput(
        mint="TestMint111111111111111111111111111111111",
        treasury_wallet="Treasury111111111111111111111111111111111",
        subprov_wallet="Subprov111111111111111111111111111111111",
        creator_wallet="Creator111111111111111111111111111111111",
        treasury_confirmation=TreasuryConfirmationEvidence(confirmed=True),
        session_evidence=SessionEvidence(exists=True, state="EXPIRED", topology="DIRECT"),
        mechanism_evidence=MechanismEvidence(value="WSOL_WRAP_CLOSE", evidence_tier="WALKBACK_RECOVERED"),
        conflict_evidence=conflicts,
    )


# --- Predicate-level: MECHANISM_LABEL_VARIATION must never gate the decision ---

def test_mechanism_label_variation_alone_does_not_block():
    ev = _base_evidence([ConflictSignal(code="MECHANISM_LABEL_VARIATION", detail="same sig, different label")])
    result = evaluate_watchtower_canonical_eligibility(ev)
    assert result.eligible is True
    assert result.decision == "ACCEPTED"
    assert result.decision_reason == "CANONICAL_CONFIRMED"
    assert "MECHANISM_LABEL_VARIATION" in result.conflicts


def test_mechanism_conflict_still_blocks_despite_new_code_existing():
    """Regression guard: adding MECHANISM_LABEL_VARIATION must not weaken
    the existing MECHANISM_CONFLICT handling."""
    ev = _base_evidence([ConflictSignal(code="MECHANISM_CONFLICT", redecode_attempted=False)])
    result = evaluate_watchtower_canonical_eligibility(ev)
    assert result.eligible is False
    assert result.decision == "INSUFFICIENT_EVIDENCE"
    assert result.decision_reason == "EVIDENCE_INSUFFICIENT"


# --- Adapter-level: same-signature vs different-signature disambiguation ---

@pytest.fixture
def prod_conn():
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ops_db = os.path.join(root, "database", "wt_ops_v2.db")
    if not os.path.exists(ops_db):
        pytest.skip("production database not present in this environment")
    conn = sqlite3.connect(f"file:{ops_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def test_avlijbdt_same_signature_passes_with_informational_flag_only(prod_conn):
    """X67.19/X67.20's core finding: AvLiJBdtb4omCymE...'s registry and
    walkback-queue rows cite the IDENTICAL signature for the creator-funding
    transaction, disagreeing only on mechanism label (SEEDED_ACCOUNT_CLOSE
    vs WSOL_WRAP_CLOSE). Must now ACCEPT, not REVIEW."""
    mint = "AvLiJBdtb4omCymEHycd9tToVH1x7HskCSjoJxjApump"
    ev = build_evidence_for_registry_row(prod_conn, mint=mint)
    result = evaluate_watchtower_canonical_eligibility(ev)
    assert result.eligible is True
    assert result.decision == "ACCEPTED"
    assert result.decision_reason == "CANONICAL_CONFIRMED"
    assert "MECHANISM_LABEL_VARIATION" in result.conflicts


def test_cptvqtf_different_signature_still_requires_review(prod_conn):
    """X67.19's other mechanism-disagreement mint: CPtvQTf8bXKPx4wQ...'s
    registry wrap_close_signature and walkback funder_sig are genuinely
    DIFFERENT transactions. Must remain under review, unchanged by the
    X67.20 adapter refinement."""
    mint = "CPtvQTf8bXKPx4wQTpVkB9StqcXnVdpz8WN2UZaopump"
    ev = build_evidence_for_registry_row(prod_conn, mint=mint)
    result = evaluate_watchtower_canonical_eligibility(ev)
    assert result.eligible is False
    assert result.decision == "INSUFFICIENT_EVIDENCE"
    assert result.decision_reason == "EVIDENCE_INSUFFICIENT"


def test_ab7xx_legacy_exception_unchanged(prod_conn):
    """AB7XX... must remain a fail (IDENTITY_UNCONFIRMED) -- untouched by
    the mechanism-label-variation adapter refinement, which only affects
    mechanism-conflict handling."""
    mint = "AB7XXeQAvN2yiqrg4MR3AbyhNdL1dAyhSon4LhLUpump"
    ev = build_evidence_for_registry_row(prod_conn, mint=mint)
    result = evaluate_watchtower_canonical_eligibility(ev)
    assert result.eligible is False
    assert result.decision == "REJECTED"
    assert result.decision_reason == "IDENTITY_UNCONFIRMED"


def test_eeujxjz_lineage_conflict_unchanged(prod_conn):
    """EeujXJZ... must remain a fail (CONFLICT_LINEAGE) -- the walkback
    pipeline's LINEAGE_GAP-promoted-to-confirmed data defect is untouched
    by the mechanism-label-variation adapter refinement."""
    mint = "EeujXJZkoyGvBmwxT4HMwVw7ZuCoNwqiWBgFWVcJpump"
    ev = build_evidence_for_registry_row(prod_conn, mint=mint)
    result = evaluate_watchtower_canonical_eligibility(ev)
    assert result.eligible is False
    assert result.decision == "REJECTED"
    assert result.decision_reason == "CONFLICT_LINEAGE"


def test_no_registry_writes_from_backtest_adapter(prod_conn):
    before = prod_conn.execute("SELECT COUNT(*) c FROM wt_watchtower_launches").fetchone()["c"]
    build_evidence_for_registry_row(prod_conn, mint="AvLiJBdtb4omCymEHycd9tToVH1x7HskCSjoJxjApump")
    after = prod_conn.execute("SELECT COUNT(*) c FROM wt_watchtower_launches").fetchone()["c"]
    assert before == after
