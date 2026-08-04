import dataclasses
from types import MappingProxyType

import pytest

from src.ops.emerging_operator_service import EmergingOperatorService
from src.ops.evidence_reconciliation import (
    EvidenceReconciliationPackage,
    EvidenceReconciliationService,
    UNRESOLVED,
)
from src.ops.investigation_population import InvestigationPopulation


def _population(*, launches=("MINT",), warnings=(), exclusions=()):
    return InvestigationPopulation(
        population_id="family:test",
        anchor="CONTROLLER",
        population_basis=(),
        members=("CONTROLLER",),
        launches=launches,
        timeline=(),
        metadata={
            "treasuries": ("TREASURY",),
            "member_treasuries": (("CONTROLLER", ("TREASURY",)),),
            "creators": ("CREATOR",),
            "mechanisms": ("PLAIN_XFER",),
            "signatures": ("SIGNATURE",),
            "first_seen_at": 100,
            "last_seen_at": 200,
            "session_count": 1,
            "active_session_count": 1,
            "observation_count": 1,
            "launch_count_hint": 1,
            "sources": ("wt_provisioning_edges", "wt_active_subprov_sessions"),
            "exclusions": exclusions,
            "warnings": warnings,
            "edge_times": (100,),
            "evidence": (),
            "templates": (),
            "campaigns": (),
            "infrastructure_roles": ("PROVISIONING_HUB",),
            "session_amounts": ((1.0,),),
        },
    )


def test_population_revision_is_content_addressed_and_deeply_immutable():
    first = _population()
    same = _population()
    changed = _population(launches=("MINT", "MINT-2"))

    assert first.revision_id == same.revision_id
    assert first.revision_id != changed.revision_id
    assert first.revision_id.startswith("ipr:")
    assert isinstance(first.metadata, MappingProxyType)
    with pytest.raises(TypeError):
        first.metadata["treasuries"] = ()


def test_package_is_factual_immutable_and_locked_unresolved():
    package = EvidenceReconciliationService(
        infrastructure_lookup=lambda _: None
    ).build(_population())

    assert isinstance(package, EvidenceReconciliationPackage)
    assert package.disposition == UNRESOLVED
    assert package.population.revision_id == _population().revision_id
    assert package.supporting_evidence
    assert package.context
    assert package.missing_evidence
    assert {item.evidence_type for item in package.missing_evidence} >= {
        "SETTLEMENT_UNAVAILABLE", "CREATOR_REUSE_UNAVAILABLE",
    }
    with pytest.raises(dataclasses.FrozenInstanceError):
        package.disposition = "CONFIRMED_OPERATION"


def test_every_evidence_item_has_provenance_and_explainability_reference():
    package = EvidenceReconciliationService(
        infrastructure_lookup=lambda _: None
    ).build(_population(warnings=("observed warning",)))
    items = (
        package.supporting_evidence + package.contradictory_evidence
        + package.context + package.missing_evidence
    )
    ids = {item.evidence_id for item in items}

    for item in items:
        assert item.provenance.source
        assert item.provenance.dependency_group
        assert item.provenance.completeness
        assert item.provenance.table or item.provenance.registry or item.provenance.rpc
    statements = (
        package.explainability.why_population_exists
        + tuple(x for values in package.explainability.why_members_belong.values() for x in values)
        + package.explainability.supporting_facts
        + package.explainability.contradictory_facts
        + package.explainability.unknown_facts
    )
    assert statements
    assert all(statement.evidence_ids for statement in statements)
    assert all(ref in ids for statement in statements for ref in statement.evidence_ids)


def test_existing_observed_infrastructure_is_contradictory_not_missing():
    package = EvidenceReconciliationService(
        infrastructure_lookup=lambda entity: (
            {"kind": "CEX", "name": "Known exchange"}
            if entity == "TREASURY" else None
        )
    ).build(_population())

    assert any(item.evidence_type == "CEX" for item in package.contradictory_evidence)
    assert all(item.evidence_type != "CEX" for item in package.missing_evidence)
    assert package.disposition == UNRESOLVED


def test_no_score_lifecycle_promotion_visibility_or_assignment_fields():
    forbidden = {
        "score", "confidence", "lifecycle", "promotion", "visibility",
        "operator_id", "operation_id", "candidate", "confirmation",
    }
    model_fields = set()
    for model in (
        EvidenceReconciliationPackage,
        type(EvidenceReconciliationService(infrastructure_lookup=lambda _: None).build(_population()).population),
    ):
        model_fields.update(field.name for field in dataclasses.fields(model))
    assert forbidden.isdisjoint(model_fields)


def test_live_shadow_packages_cover_all_populations_and_named_controls():
    service = EmergingOperatorService(
        "database/wt_ops_v2.db", "database/flex_complete_database.db"
    )
    with service._connect(service.ops_db_path) as conn:
        profiles = service._discovery_profiles(conn, service._tables(conn))
    populations = service._population_builder().build(profiles)
    reconciler = EvidenceReconciliationService(service.ops_db_path)
    packages = reconciler.build_all(populations)

    assert len(packages) == len(populations)
    assert packages
    assert all(package.disposition == UNRESOLVED for package in packages)
    assert all(package.population.revision_id for package in packages)

    families = service._compose()
    for name in ("B48k", "C7Ha"):
        family = next(item for item in families if name in item["family_name"])
        assert any(
            package.population.population_id == family["family_id"]
            for package in packages
        )

    watchtower = next(item for item in families if item["family_name"] == "WATCHTOWER")
    watchtower_population = reconciler.population_from_canonical_registry(watchtower)
    watchtower_package = reconciler.build(watchtower_population)
    assert watchtower_package.population.population_id == watchtower["family_id"]
    assert watchtower_package.disposition == UNRESOLVED
