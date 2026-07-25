"""X67.21 -- Integration-layer tests: rollout-mode configuration, decision
mapping, shadow safety, enforcement safety, idempotency, and historical
anomaly regression, per X67.21's required test coverage list.
"""
from __future__ import annotations

import os
import sqlite3

import pytest

from src.ops.watchtower_canonical_predicate import (
    CanonicalEvidenceInput,
    ConflictSignal,
    MechanismEvidence,
    SessionEvidence,
    TreasuryConfirmationEvidence,
)
from src.ops.watchtower_canonical_integration import (
    CanonicalIntegrationResult,
    evaluate_canonical_decision,
    ensure_telemetry_schema,
    get_canonical_predicate_mode,
    record_comparison_telemetry,
)


def _clean_env(monkeypatch):
    monkeypatch.delenv("WATCHTOWER_CANONICAL_PREDICATE_MODE", raising=False)


def _evidence(
    *, mechanism="WSOL_WRAP_CLOSE", evidence_tier="WALKBACK_RECOVERED",
    conflicts=None, treasury_confirmation=None, session_evidence=None,
):
    return CanonicalEvidenceInput(
        mint="TestMint111111111111111111111111111111111",
        treasury_wallet="Treasury111111111111111111111111111111111",
        subprov_wallet="Subprov111111111111111111111111111111111",
        creator_wallet="Creator111111111111111111111111111111111",
        treasury_confirmation=treasury_confirmation or TreasuryConfirmationEvidence(confirmed=True),
        session_evidence=session_evidence or SessionEvidence(exists=True, state="EXPIRED", topology="DIRECT"),
        mechanism_evidence=MechanismEvidence(value=mechanism, evidence_tier=evidence_tier),
        conflict_evidence=conflicts or [],
    )


# ── Configuration ────────────────────────────────────────────────────────────

def test_default_mode_is_shadow(monkeypatch):
    _clean_env(monkeypatch)
    assert get_canonical_predicate_mode() == "shadow"


def test_explicit_legacy_mode(monkeypatch):
    monkeypatch.setenv("WATCHTOWER_CANONICAL_PREDICATE_MODE", "legacy")
    assert get_canonical_predicate_mode() == "legacy"


def test_explicit_shadow_mode(monkeypatch):
    monkeypatch.setenv("WATCHTOWER_CANONICAL_PREDICATE_MODE", "shadow")
    assert get_canonical_predicate_mode() == "shadow"


def test_explicit_enforce_mode(monkeypatch):
    monkeypatch.setenv("WATCHTOWER_CANONICAL_PREDICATE_MODE", "enforce")
    assert get_canonical_predicate_mode() == "enforce"


def test_invalid_value_falls_back_to_legacy_never_enforce(monkeypatch):
    monkeypatch.setenv("WATCHTOWER_CANONICAL_PREDICATE_MODE", "definitely_not_a_real_mode")
    assert get_canonical_predicate_mode() == "legacy"


def test_case_insensitive_mode_value(monkeypatch):
    monkeypatch.setenv("WATCHTOWER_CANONICAL_PREDICATE_MODE", "SHADOW")
    assert get_canonical_predicate_mode() == "shadow"


# ── Legacy mode behaviour ────────────────────────────────────────────────────

def test_legacy_mode_uses_legacy_decision_and_skips_predicate():
    calls = []

    def build_evidence():
        calls.append("called")
        return _evidence()

    result = evaluate_canonical_decision(
        path="path_a_candidate_workflow", mint="m1", build_evidence=build_evidence,
        legacy_decision="ACCEPTED", legacy_reason="LEGACY_OK", mode="legacy",
    )
    assert result.authoritative_decision == "ACCEPTED"
    assert result.mode == "legacy"
    assert result.predicate_result is None
    assert calls == []  # predicate/evidence gathering not required to run in LEGACY


# ── Shadow mode: legacy remains authoritative, comparison recorded ──────────

def test_shadow_mode_legacy_authoritative_when_matching():
    result = evaluate_canonical_decision(
        path="path_a_candidate_workflow", mint="m1",
        build_evidence=lambda: _evidence(mechanism="WSOL_WRAP_CLOSE"),
        legacy_decision="ACCEPTED", legacy_reason="LEGACY_OK", mode="shadow",
    )
    assert result.authoritative_decision == "ACCEPTED"  # legacy's own decision
    assert result.mode == "shadow"
    assert result.predicate_result is not None
    assert result.decisions_match is True
    assert result.divergence_code == "MATCH_ACCEPT"


def test_shadow_mode_records_divergence_without_changing_outcome():
    # Legacy says ACCEPTED, predicate would say REJECTED (identity unconfirmed).
    result = evaluate_canonical_decision(
        path="path_a_candidate_workflow", mint="m1",
        build_evidence=lambda: _evidence(
            treasury_confirmation=TreasuryConfirmationEvidence(confirmed=False),
        ),
        legacy_decision="ACCEPTED", legacy_reason="LEGACY_OK", mode="shadow",
    )
    assert result.authoritative_decision == "ACCEPTED"  # legacy STILL wins in shadow
    assert result.decisions_match is False
    assert result.divergence_code == "LEGACY_ACCEPT_PREDICATE_REJECT"


def test_shadow_mode_adapter_exception_never_interrupts_caller():
    def boom():
        raise RuntimeError("simulated adapter failure")

    result = evaluate_canonical_decision(
        path="path_a_candidate_workflow", mint="m1", build_evidence=boom,
        legacy_decision="ACCEPTED", legacy_reason="LEGACY_OK", mode="shadow",
    )
    # No exception raised; legacy decision still authoritative.
    assert result.authoritative_decision == "ACCEPTED"
    assert result.predicate_error is not None
    assert result.predicate_result is None


def test_shadow_mode_predicate_exception_never_interrupts_caller(monkeypatch):
    import src.ops.watchtower_canonical_integration as integration_module

    def boom_predicate(_evidence):
        raise ValueError("simulated predicate failure")

    monkeypatch.setattr(integration_module, "evaluate_watchtower_canonical_eligibility", boom_predicate)

    result = evaluate_canonical_decision(
        path="path_b_walkback", mint="m1", build_evidence=lambda: _evidence(),
        legacy_decision="REJECTED", legacy_reason="LEGACY_FAIL", mode="shadow",
    )
    assert result.authoritative_decision == "REJECTED"
    assert result.predicate_error is not None


# ── Enforce mode: predicate becomes authoritative ───────────────────────────

def test_enforce_mode_accepted_maps_to_promotion_eligibility():
    result = evaluate_canonical_decision(
        path="path_a_candidate_workflow", mint="m1",
        build_evidence=lambda: _evidence(mechanism="WSOL_WRAP_CLOSE"),
        legacy_decision="REJECTED", legacy_reason="LEGACY_FAIL", mode="enforce",
    )
    assert result.authoritative_decision == "ACCEPTED"
    assert result.mode == "enforce"


def test_enforce_mode_review_required_never_promotes():
    result = evaluate_canonical_decision(
        path="path_a_candidate_workflow", mint="m1",
        build_evidence=lambda: _evidence(
            conflicts=[ConflictSignal(code="MULTI_SOURCE_RELAY")],
        ),
        legacy_decision="ACCEPTED", legacy_reason="LEGACY_OK", mode="enforce",
    )
    assert result.authoritative_decision == "REVIEW_REQUIRED"
    assert result.authoritative_decision != "ACCEPTED"


def test_enforce_mode_rejected_never_promotes():
    result = evaluate_canonical_decision(
        path="path_a_candidate_workflow", mint="m1",
        build_evidence=lambda: _evidence(
            treasury_confirmation=TreasuryConfirmationEvidence(confirmed=False),
        ),
        legacy_decision="ACCEPTED", legacy_reason="LEGACY_OK", mode="enforce",
    )
    assert result.authoritative_decision == "REJECTED"


def test_enforce_mode_review_not_collapsed_into_rejection():
    """The distinct REVIEW_REQUIRED decision must never be reported as
    REJECTED -- these are different downstream handling paths."""
    result = evaluate_canonical_decision(
        path="path_a_candidate_workflow", mint="m1",
        build_evidence=lambda: _evidence(mechanism="UNVERIFIED", evidence_tier="WALKBACK_RECOVERED"),
        legacy_decision="ACCEPTED", legacy_reason="LEGACY_OK", mode="enforce",
    )
    assert result.authoritative_decision == "REVIEW_REQUIRED"
    assert result.authoritative_decision != "REJECTED"


def test_enforce_mode_adapter_failure_does_not_promote():
    def boom():
        raise RuntimeError("simulated adapter failure")

    result = evaluate_canonical_decision(
        path="path_a_candidate_workflow", mint="m1", build_evidence=boom,
        legacy_decision="ACCEPTED", legacy_reason="LEGACY_OK", mode="enforce",
    )
    assert result.authoritative_decision != "ACCEPTED"
    assert result.authoritative_decision == "REVIEW_REQUIRED"
    assert result.divergence_code == "PREDICATE_EVALUATION_ERROR"


def test_enforce_mode_predicate_failure_does_not_promote(monkeypatch):
    import src.ops.watchtower_canonical_integration as integration_module

    def boom_predicate(_evidence):
        raise ValueError("simulated predicate failure")

    monkeypatch.setattr(integration_module, "evaluate_watchtower_canonical_eligibility", boom_predicate)

    result = evaluate_canonical_decision(
        path="path_b_walkback", mint="m1", build_evidence=lambda: _evidence(),
        legacy_decision="ACCEPTED", legacy_reason="LEGACY_OK", mode="enforce",
    )
    assert result.authoritative_decision != "ACCEPTED"


def test_enforce_mode_no_silent_fallback_to_legacy_acceptance_on_error():
    """Even though legacy said ACCEPTED, an evaluation error in ENFORCE
    mode must NEVER silently adopt that legacy acceptance."""
    def boom():
        raise RuntimeError("boom")

    result = evaluate_canonical_decision(
        path="path_a_candidate_workflow", mint="m1", build_evidence=boom,
        legacy_decision="ACCEPTED", legacy_reason="LEGACY_OK", mode="enforce",
    )
    assert result.authoritative_decision != "ACCEPTED"


# ── Historical anomaly regression (X67.19/X67.20 findings) ──────────────────

def test_mechanism_label_variation_remains_informational():
    result = evaluate_canonical_decision(
        path="path_a_candidate_workflow", mint="m1",
        build_evidence=lambda: _evidence(
            conflicts=[ConflictSignal(code="MECHANISM_LABEL_VARIATION")],
        ),
        legacy_decision="ACCEPTED", legacy_reason="LEGACY_OK", mode="enforce",
    )
    assert result.authoritative_decision == "ACCEPTED"


def test_mechanism_conflict_different_signature_remains_reviewable():
    result = evaluate_canonical_decision(
        path="path_a_candidate_workflow", mint="m1",
        build_evidence=lambda: _evidence(
            conflicts=[ConflictSignal(code="MECHANISM_CONFLICT", redecode_attempted=False)],
        ),
        legacy_decision="ACCEPTED", legacy_reason="LEGACY_OK", mode="enforce",
    )
    assert result.authoritative_decision == "REVIEW_REQUIRED"


def test_conflict_lineage_remains_blocking():
    result = evaluate_canonical_decision(
        path="path_a_candidate_workflow", mint="m1",
        build_evidence=lambda: _evidence(
            conflicts=[ConflictSignal(code="LINEAGE_CONFLICT")],
        ),
        legacy_decision="ACCEPTED", legacy_reason="LEGACY_OK", mode="enforce",
    )
    assert result.authoritative_decision == "REJECTED"


def test_identity_unconfirmed_remains_rejected():
    result = evaluate_canonical_decision(
        path="path_a_candidate_workflow", mint="m1",
        build_evidence=lambda: _evidence(
            treasury_confirmation=TreasuryConfirmationEvidence(confirmed=False),
        ),
        legacy_decision="ACCEPTED", legacy_reason="LEGACY_OK", mode="enforce",
    )
    assert result.authoritative_decision == "REJECTED"


def test_known_non_watchtower_control_remains_rejected():
    """Simulates a control mint: no treasury confirmation, no session
    evidence -- the shared predicate must reject it regardless of mode."""
    result = evaluate_canonical_decision(
        path="path_b_walkback", mint="control_mint",
        build_evidence=lambda: _evidence(
            treasury_confirmation=TreasuryConfirmationEvidence(confirmed=False),
            session_evidence=SessionEvidence(exists=False, state="ABSENT", topology="UNKNOWN"),
        ),
        legacy_decision="REJECTED", legacy_reason="NOT_WATCHTOWER", mode="enforce",
    )
    assert result.authoritative_decision == "REJECTED"


# ── Telemetry ────────────────────────────────────────────────────────────────

@pytest.fixture
def telemetry_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_telemetry_schema(conn)
    return conn


def test_telemetry_write_succeeds_and_is_append_only(telemetry_conn):
    result = evaluate_canonical_decision(
        path="path_a_candidate_workflow", mint="m1",
        build_evidence=lambda: _evidence(), legacy_decision="ACCEPTED",
        legacy_reason="LEGACY_OK", mode="shadow",
    )
    record_comparison_telemetry(telemetry_conn, result, mint="m1")
    record_comparison_telemetry(telemetry_conn, result, mint="m1")
    rows = telemetry_conn.execute(
        "SELECT COUNT(*) c FROM wt_canonical_predicate_comparisons WHERE mint='m1'"
    ).fetchone()
    assert rows["c"] == 2  # append-only: two calls -> two rows, never updated in place


def test_telemetry_failure_never_raises(monkeypatch):
    """A telemetry write against a connection with no schema at all must
    not raise -- it must log and return silently."""
    conn = sqlite3.connect(":memory:")  # no schema created deliberately
    conn.close()  # force every execute() to fail

    result = evaluate_canonical_decision(
        path="path_a_candidate_workflow", mint="m1",
        build_evidence=lambda: _evidence(), legacy_decision="ACCEPTED",
        legacy_reason="LEGACY_OK", mode="shadow",
    )
    # Must not raise despite the closed connection.
    record_comparison_telemetry(conn, result, mint="m1")


# ── Idempotency: Path A and Path B racing the same mint ─────────────────────

def _shared_writer_schema(conn):
    conn.executescript(
        """
        CREATE TABLE wt_attribution_outcomes (
            mint TEXT PRIMARY KEY, outcome_type TEXT, stop_reason TEXT,
            terminal_entity TEXT, terminal_entity_type TEXT, confidence TEXT,
            evidence_json TEXT, operator_id TEXT,
            should_seed_emerging_operator INTEGER, should_retry INTEGER,
            completed_at INTEGER, source_queue_updated_at INTEGER,
            materialized_at INTEGER
        );
        CREATE TABLE wt_watchtower_launches (
            id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT, creator_wallet TEXT NOT NULL,
            create_signature TEXT, create_time INTEGER, create_slot INTEGER,
            treasury_wallet TEXT, subprov_wallet TEXT, subprov_funding_sol REAL,
            wrap_close_sol REAL, wrap_close_signature TEXT,
            birth_to_launch_seconds INTEGER, create_to_migration_secs INTEGER,
            detection_source TEXT, detection_delay_seconds INTEGER,
            funding_mechanism TEXT DEFAULT 'WSOL_WRAP_CLOSE',
            creator_extraction_method TEXT DEFAULT 'CLOSE_ACCOUNT_DESTINATION',
            confidence TEXT DEFAULT 'STRICT', state TEXT DEFAULT 'FIRED_CREATE',
            recorded_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            UNIQUE(creator_wallet, create_signature)
        );
        CREATE TABLE wt_walkback_queue (
            mint TEXT PRIMARY KEY, creator TEXT, subprov TEXT, treasury TEXT,
            funding_mechanism TEXT, create_anchor_signature TEXT,
            create_anchor_block_time INTEGER, funder_sig TEXT, funder_amount_sol REAL
        );
        CREATE TABLE wt_candidate_websocket_watches (
            candidate_wallet TEXT, subprov_wallet TEXT, state TEXT, close_reason TEXT, closed_at INTEGER
        );
        """
    )
    conn.commit()


def test_path_a_and_path_b_racing_same_mint_produces_one_registry_row():
    """Both paths ultimately call the SAME idempotent writer
    (promote_walkback_confirmed_watchtower) -- simulate Path B "winning"
    the race first, then Path A attempting the same mint immediately after;
    exactly one canonical row must result, per the task's explicit
    idempotency requirement."""
    from src.core.watchtower_registry_promotion import promote_walkback_confirmed_watchtower
    from src.ops.watchtower_alignment import WATCHTOWER_OPERATOR_ID

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _shared_writer_schema(conn)
    conn.execute(
        "INSERT INTO wt_walkback_queue VALUES (?,?,?,?,?,?,?,?,?)",
        ("RACE_MINT", "CreatorRace", "SubprovRace", "TreasuryRace",
         "WSOL_WRAP_CLOSE", "SIG_CREATE", 1000, "SIG_WRAP", 1.5),
    )
    conn.commit()

    evidence = {"creator": "CreatorRace", "treasuries": ["TreasuryRace"],
                "subprovisioners": ["SubprovRace"]}

    # "Path B" promotes first.
    result_b = promote_walkback_confirmed_watchtower(
        conn, "RACE_MINT", outcome_type="CANONICAL_OPERATOR_REACHED",
        operator_id=WATCHTOWER_OPERATOR_ID, evidence=evidence, completed_at=1000,
    )
    assert result_b["action"] == "promoted"

    # "Path A" attempts the same mint immediately after (simulating a race
    # or a subsequent independent evaluation reaching the same conclusion).
    result_a = promote_walkback_confirmed_watchtower(
        conn, "RACE_MINT", outcome_type="CANONICAL_OPERATOR_REACHED",
        operator_id=WATCHTOWER_OPERATOR_ID, evidence=evidence, completed_at=1001,
    )
    assert result_a["action"] == "already_present"

    count = conn.execute("SELECT COUNT(*) c FROM wt_watchtower_launches WHERE mint='RACE_MINT'").fetchone()["c"]
    assert count == 1


def test_retried_promotion_after_partial_failure_is_idempotent():
    from src.core.watchtower_registry_promotion import promote_walkback_confirmed_watchtower
    from src.ops.watchtower_alignment import WATCHTOWER_OPERATOR_ID

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _shared_writer_schema(conn)
    conn.execute(
        "INSERT INTO wt_walkback_queue VALUES (?,?,?,?,?,?,?,?,?)",
        ("RETRY_MINT", "CreatorRetry", "SubprovRetry", "TreasuryRetry",
         "WSOL_WRAP_CLOSE", "SIG_CREATE2", 2000, "SIG_WRAP2", 2.0),
    )
    conn.commit()
    evidence = {"creator": "CreatorRetry", "treasuries": ["TreasuryRetry"],
                "subprovisioners": ["SubprovRetry"]}

    r1 = promote_walkback_confirmed_watchtower(
        conn, "RETRY_MINT", outcome_type="CANONICAL_OPERATOR_REACHED",
        operator_id=WATCHTOWER_OPERATOR_ID, evidence=evidence, completed_at=2000,
    )
    assert r1["action"] == "promoted"
    # Simulated retry (e.g. after a caller-side crash before it recorded success).
    r2 = promote_walkback_confirmed_watchtower(
        conn, "RETRY_MINT", outcome_type="CANONICAL_OPERATOR_REACHED",
        operator_id=WATCHTOWER_OPERATOR_ID, evidence=evidence, completed_at=2000,
    )
    assert r2["action"] == "already_present"
    count = conn.execute("SELECT COUNT(*) c FROM wt_watchtower_launches WHERE mint='RETRY_MINT'").fetchone()["c"]
    assert count == 1


def test_telemetry_stores_conflicts_as_structured_json(telemetry_conn):
    result = evaluate_canonical_decision(
        path="path_a_candidate_workflow", mint="m1",
        build_evidence=lambda: _evidence(
            conflicts=[ConflictSignal(code="SHARED_RELAY_SESSION_VOLUME")],
        ),
        legacy_decision="ACCEPTED", legacy_reason="LEGACY_OK", mode="shadow",
    )
    record_comparison_telemetry(telemetry_conn, result, mint="m1")
    row = telemetry_conn.execute(
        "SELECT predicate_conflicts_json, predicate_version, adapter_version "
        "FROM wt_canonical_predicate_comparisons WHERE mint='m1'"
    ).fetchone()
    import json
    conflicts = json.loads(row["predicate_conflicts_json"])
    assert "SHARED_RELAY_SESSION_VOLUME" in conflicts
    assert row["predicate_version"] == "X67.20"
    assert row["adapter_version"] == "X67.20"
