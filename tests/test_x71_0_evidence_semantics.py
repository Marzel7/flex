"""X71.0 immutable evidence-semantics acceptance tests."""
from __future__ import annotations

import dataclasses
import sqlite3

import pytest

from src.ops.disposition_resolver import OPERATOR_CANDIDATE, UNRESOLVED, DispositionResolver
from src.ops.evidence_reconciliation import (
    EvidenceItem,
    EvidenceProvenance,
    EvidenceReconciliationService,
)
from src.ops.evidence_semantics import (
    APPLICABLE,
    NOT_APPLICABLE,
    PARTIALLY_APPLICABLE,
    SHARED_PROVENANCE,
    TRUE,
    EvidenceSemanticsService,
)
from src.ops.investigation_population import InvestigationPopulation


def _population(launches=("MINT-A", "MINT-B")):
    return InvestigationPopulation(
        population_id="family:semantics", anchor="ANCHOR", population_basis=(),
        members=("ANCHOR",), launches=launches, timeline=(),
        metadata={
            "treasuries": ("TREASURY",), "member_treasuries": (),
            "creators": ("CREATOR",), "mechanisms": (), "signatures": (),
            "first_seen_at": 1, "last_seen_at": 4, "session_count": 0,
            "active_session_count": 0, "observation_count": 2,
            "launch_count_hint": len(launches), "sources": ("wt_provisioning_edges",),
            "exclusions": (), "warnings": (), "edge_times": (), "evidence": (),
            "templates": (), "campaigns": (), "infrastructure_roles": (),
            "session_amounts": (),
        },
    )


@pytest.fixture
def reuse_db(tmp_path):
    path = tmp_path / "ops.db"
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE wt_provisioning_edges (
        edge_id TEXT, to_wallet TEXT, source_mint TEXT,
        funding_tx_signature TEXT, first_observed_by_flex INTEGER,
        last_observed_by_flex INTEGER)""")
    conn.executemany("INSERT INTO wt_provisioning_edges VALUES (?,?,?,?,?,?)", [
        ("edge-a", "CREATOR", "MINT-A", "sig-a", 1, 1),
        ("edge-b", "CREATOR", "MINT-B", "sig-b", 2, 2),
        ("edge-z", "CREATOR", "MINT-Z", "sig-z", 3, 3),
    ])
    conn.commit()
    conn.close()
    return str(path)


def _items(package):
    lineage = next(item for item in package.supporting_evidence if item.evidence_type == "PROVISIONING_LINEAGE")
    reuse = next(item for item in package.supporting_evidence if item.evidence_type == "CREATOR_REUSE_CONTROL")
    return lineage, reuse


def test_truth_applicability_and_provenance_are_separate_and_immutable(reuse_db):
    package = EvidenceReconciliationService(reuse_db, infrastructure_lookup=lambda _: None).build(
        _population(("MINT-Q",))
    )
    semantics = EvidenceSemanticsService.evaluate(package)
    lineage, reuse = _items(package)
    semantic_reuse = semantics.observation(reuse.evidence_id)

    assert semantic_reuse.truth == TRUE
    assert semantic_reuse.applicability == NOT_APPLICABLE
    assert semantic_reuse.eligible is False
    assert semantics.are_independent(lineage, reuse) is False
    relationship = next(item for item in semantics.provenance_relationships if {
        item.left_evidence_id, item.right_evidence_id
    } == {lineage.evidence_id, reuse.evidence_id})
    assert relationship.independence == SHARED_PROVENANCE
    with pytest.raises(dataclasses.FrozenInstanceError):
        semantic_reuse.eligible = True


def test_partial_population_scope_is_not_eligible(reuse_db):
    package = EvidenceReconciliationService(reuse_db, infrastructure_lookup=lambda _: None).build(
        _population(("MINT-A",))
    )
    semantics = EvidenceSemanticsService.evaluate(package)
    _, reuse = _items(package)
    assert semantics.observation(reuse.evidence_id).applicability == PARTIALLY_APPLICABLE
    assert DispositionResolver.resolve(package).disposition == UNRESOLVED


def test_fully_applicable_reuse_still_shares_lineage_provenance(reuse_db):
    package = EvidenceReconciliationService(reuse_db, infrastructure_lookup=lambda _: None).build(
        _population(("MINT-A", "MINT-B", "MINT-Z"))
    )
    semantics = EvidenceSemanticsService.evaluate(package)
    lineage, reuse = _items(package)
    assert semantics.observation(reuse.evidence_id).applicability == APPLICABLE
    assert semantics.observation(reuse.evidence_id).provenance_independence == SHARED_PROVENANCE
    assert semantics.observation(reuse.evidence_id).eligible is False
    assert semantics.are_independent(lineage, reuse) is False
    assert DispositionResolver.resolve(package).disposition == UNRESOLVED


def test_generic_independent_applicable_control_can_promote(reuse_db):
    reconciler = EvidenceReconciliationService(reuse_db, infrastructure_lookup=lambda _: None)
    package = reconciler.build(_population())
    control = EvidenceItem(
        evidence_type="RETURN_TO_CONTROLLER", role="SUPPORTING",
        statement="Persisted settlement records a return to controller.",
        entities=("ANCHOR",), launches=package.population.launches,
        provenance=EvidenceProvenance(
            source="creator_outgoing_transfers", table="creator_outgoing_transfers",
            registry=None, rpc=False, transaction_signature=None,
            observation_window=(1, 4), dependency_group="settlement",
            completeness="OBSERVED",
        ),
    )
    all_items = list(
        package.supporting_evidence + (control,) + package.contradictory_evidence
        + package.context + package.missing_evidence
    )
    package = dataclasses.replace(
        package,
        supporting_evidence=package.supporting_evidence + (control,),
        dependency_groups=reconciler._dependency_groups(all_items),
        provenance=tuple(dict.fromkeys(item.provenance for item in all_items)),
    )
    semantics = EvidenceSemanticsService.evaluate(package)
    lineage = next(item for item in package.supporting_evidence if item.evidence_type == "PROVISIONING_LINEAGE")
    assert semantics.are_independent(lineage, control)
    assert not any({item.left_evidence_id, item.right_evidence_id} == {
        lineage.evidence_id, control.evidence_id
    } for item in semantics.provenance_relationships)
    assert DispositionResolver.resolve(package).disposition == OPERATOR_CANDIDATE


def test_semantic_replay_is_deterministic(reuse_db):
    package = EvidenceReconciliationService(reuse_db, infrastructure_lookup=lambda _: None).build(_population())
    first = EvidenceSemanticsService.evaluate(package)
    second = EvidenceSemanticsService.evaluate(package)
    assert first == second
    assert first.semantics_id == second.semantics_id
    assert DispositionResolver.resolve(package).result_id == DispositionResolver.resolve(package).result_id
