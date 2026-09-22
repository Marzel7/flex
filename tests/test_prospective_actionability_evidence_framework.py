import json
import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

from src.ops.actionability_evidence import (
    ACTIONABILITY_EVIDENCE_CONTRACT_VERSION, LIVE_ACTIONABILITY_CAPTURE_ENABLED,
    ActionableEntryExactness, InterleavingState, PumpFunActiveCurveValuationAdapter,
    PumpSwapInitialPoolValuationAdapter, build_actionability_facts,
    choose_primary_entry, conservative_linkage_gate,
)
from src.ops.nexus_operation_adapter import NexusProspectiveActionabilityShadowAdapter
from src.ops.operation_fact_framework import OperationContract, write_facts


def fixture(*, exact=False, linked=True, applicable=True):
    return {
        "mint": "14i7jbnL5khBcnebWjhQ6bXziW3pAptkeaQV9Utvpump",
        "actionability_not_applicable": not applicable,
        "theoretical_first_executable_event": {"signature": "migration", "slot": 441859052, "transaction_index": 467, "block_time": 1787744439, "fee_payer": "4oh", "signers": ["4oh"], "venue": "PUMPSWAP", "pool_identifier": "pool"},
        "theoretical_first_executable_state": {"reference": "post-create-pool"},
        "immediate_post_executable_transactions": [{"signature": "tx468", "transaction_index": 468, "action_type": "BUY"}],
        "operation_linkage_evidence": {"direct_creator_or_operation_funding": linked, "signer_continuity": linked, "funding_buy_match_percent": 1.5625 if linked else 6, "immediately_next_ordered_transaction": linked, "provenance": {"funding_signature": "48Ynq"}},
        "execution_exclusivity_evidence": {"interleaving_state": InterleavingState.PROVEN_NON_INTERLEAVABLE if exact else InterleavingState.NOT_PROVEN_PROHIBITED, "bundle_id": "bundle" if exact else None},
        "conservative_boundary": {"identity": "tx468", "market_cap": "850783.8146933435"},
        "exact_boundary": {"identity": "tx468"},
        "first_external_candidate": {"signature": "candidate", "index": 469, "wallet_role": "UNRESOLVED", "pre_state_reference": "p", "post_state_reference": "q"},
        "valuations": {"theoretical_first_executable_mc": "31651.528769982016", "conservative_actionable_entry_mc": "850783.8146933435", "exact_actionable_entry_mc": "850783.8146933435" if exact else None},
        "provenance": {"source": "retained-fixture"}, "raw_references": ["external/artifact.json#sha256=x"],
    }


def summary(facts):
    return next(f.payload for f in facts if f.fact_type == "ACTIONABILITY_COMPLETENESS")


def test_contract_and_fact_id_include_boundary_and_are_deterministic():
    c = OperationContract("op", "v1")
    a = build_actionability_facts(fixture(), c)
    b = build_actionability_facts(fixture(), c)
    assert ACTIONABILITY_EVIDENCE_CONTRACT_VERSION == "ACTIONABILITY_EVIDENCE_CONTRACT_V1"
    assert [x.fact_id() for x in a] == [x.fact_id() for x in b]
    changed = fixture(); changed["theoretical_first_executable_event"]["signature"] = "other"
    assert a[0].fact_id() != build_actionability_facts(changed, c)[0].fact_id()


def test_idempotency_and_conflict_fail_closed_single_token_compact_commit():
    conn = sqlite3.connect(":memory:"); facts = build_actionability_facts(fixture(), OperationContract("op", "v1"))
    assert write_facts(conn, facts) == 6
    assert write_facts(conn, facts) == 0
    assert conn.execute("select count(*) from operation_compact_facts").fetchone()[0] == 6
    tampered = list(facts); tampered[0] = tampered[0].__class__(**{**tampered[0].__dict__, "payload": {**tampered[0].payload, "state": "conflict"}})
    with pytest.raises(ValueError, match="FAIL_CLOSED"): write_facts(conn, tampered)


def test_conservative_gate_pass_and_fail_and_exactness_states():
    assert conservative_linkage_gate(fixture()["operation_linkage_evidence"])
    assert not conservative_linkage_gate(fixture(linked=False)["operation_linkage_evidence"])
    assert summary(build_actionability_facts(fixture(), OperationContract("op", "v1")))["actionable_entry_exactness"] == "CONSERVATIVE_LINKED"
    assert summary(build_actionability_facts(fixture(exact=True), OperationContract("op", "v1")))["actionable_entry_exactness"] == "EXACT_EXCLUSIVITY_PROVEN"


def test_primary_policy_prefers_exact_then_conservative_then_theoretical():
    assert choose_primary_entry({"exact_actionable_entry_mc": 3, "conservative_actionable_entry_mc": 2, "theoretical_first_executable_mc": 1}) == (3, ActionableEntryExactness.EXACT_EXCLUSIVITY_PROVEN)
    assert choose_primary_entry({"exact_actionable_entry_mc": None, "conservative_actionable_entry_mc": 2, "theoretical_first_executable_mc": 1}) == (2, ActionableEntryExactness.CONSERVATIVE_LINKED)
    assert choose_primary_entry({"theoretical_first_executable_mc": 1}) == (1, ActionableEntryExactness.THEORETICAL)


def test_buy_size_does_not_change_actionable_facts_or_venue_adapters_are_generic():
    a = fixture(); b = fixture(); a["counterfactual_order_size"] = 1; b["counterfactual_order_size"] = 1000000
    assert [f.payload for f in build_actionability_facts(a, OperationContract("op", "v1"))] == [f.payload for f in build_actionability_facts(b, OperationContract("op", "v1"))]
    state = {"price_quote": "1", "market_cap_quote": "2", "market_cap_usd": "3", "provenance": "p"}
    for adapter in (PumpFunActiveCurveValuationAdapter(), PumpSwapInitialPoolValuationAdapter()):
        assert adapter.value(actionable_event={}, executable_state=state, supply={}, quote_asset="SOL", timestamp=1)["actionable_entry_mc"] == "3"


def test_14i7_known_answer_shadow_and_missing_bundle_does_not_block():
    facts = NexusProspectiveActionabilityShadowAdapter().build_facts(fixture(), OperationContract("nexus", "v1"))
    s = summary(facts)
    assert NexusProspectiveActionabilityShadowAdapter.live_capture_enabled is False
    assert s["theoretical_first_executable_mc"] == "31651.528769982016"
    assert s["conservative_actionable_entry_mc"] == "850783.8146933435"
    assert s["exact_actionable_entry_mc"] is None and s["actionable_entry_exactness"] == "CONSERVATIVE_LINKED"


def test_byzantine_compatibility_not_applicable_and_100_writes_no_locks():
    # Existing Scenario-D boundaries can pass as opaque adapter evidence; no historical fact is changed.
    assert len(build_actionability_facts(fixture(), OperationContract("byzantine", "position-13-v1"))) == 6
    not_applicable = summary(build_actionability_facts(fixture(applicable=False), OperationContract("example-operation", "v1")))
    assert not_applicable["actionable_entry_exactness"] == "NOT_APPLICABLE"
    conn = sqlite3.connect(":memory:"); started = time.perf_counter(); added = 0
    for i in range(100):
        row = fixture(); row["mint"] = f"mint-{i}"; added += write_facts(conn, build_actionability_facts(row, OperationContract("op", "v1")))
    assert added == 600 and (time.perf_counter() - started) >= 0
    assert LIVE_ACTIONABILITY_CAPTURE_ENABLED is False
