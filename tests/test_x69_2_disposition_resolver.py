import dataclasses
import inspect

from src.ops.disposition_resolver import (
    CONFIRMED_OPERATION,
    INFRASTRUCTURE,
    OPERATOR_CANDIDATE,
    REJECTED,
    RETIRED,
    REVIEW,
    UNRESOLVED,
    DispositionResolver,
)
from src.ops.emerging_operator_service import EmergingOperatorService
from src.ops.evidence_reconciliation import (
    EvidenceItem,
    EvidenceProvenance,
    EvidenceReconciliationService,
)
from src.ops.investigation_population import InvestigationPopulation


def _population(*, exclusions=(), warnings=()):
    return InvestigationPopulation(
        population_id="family:resolver-test",
        anchor="CONTROLLER",
        population_basis=(),
        members=("CONTROLLER",),
        launches=("MINT",),
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
            "edge_times": (100,), "evidence": (), "templates": (),
            "campaigns": (), "infrastructure_roles": (), "session_amounts": ((),),
        },
    )


def _service():
    return EvidenceReconciliationService(infrastructure_lookup=lambda _: None)


def _item(evidence_type, role="SUPPORTING", group="control"):
    return EvidenceItem(
        evidence_type=evidence_type,
        role=role,
        statement=f"Observed {evidence_type} fact.",
        entities=("CONTROLLER",),
        launches=("MINT",),
        provenance=EvidenceProvenance(
            source="test_persisted_evidence", table="test_evidence", registry=None,
            rpc=False, transaction_signature=None, observation_window=(100, 200),
            dependency_group=group, completeness="OBSERVED",
        ),
    )


def _with_item(package, item, section="supporting_evidence"):
    values = getattr(package, section) + (item,)
    all_items = list(
        values
        + (package.contradictory_evidence if section != "contradictory_evidence" else ())
        + (package.context if section != "context" else ())
        + package.missing_evidence
    )
    if section == "supporting_evidence":
        all_items = list(values + package.contradictory_evidence + package.context + package.missing_evidence)
    elif section == "contradictory_evidence":
        all_items = list(package.supporting_evidence + values + package.context + package.missing_evidence)
    elif section == "context":
        all_items = list(package.supporting_evidence + package.contradictory_evidence + values + package.missing_evidence)
    return dataclasses.replace(
        package,
        **{section: values},
        dependency_groups=_service()._dependency_groups(all_items),
        provenance=tuple(dict.fromkeys(value.provenance for value in all_items)),
    )


def test_resolver_is_pure_package_only_and_deterministic():
    signature = inspect.signature(DispositionResolver.resolve)
    assert tuple(signature.parameters) == ("package",)

    package = _service().build(_population())
    first = DispositionResolver.resolve(package)
    second = DispositionResolver.resolve(package)
    reordered = dataclasses.replace(
        package,
        supporting_evidence=tuple(reversed(package.supporting_evidence)),
        context=tuple(reversed(package.context)),
        missing_evidence=tuple(reversed(package.missing_evidence)),
    )
    third = DispositionResolver.resolve(reordered)

    assert first == second == third
    assert first.result_id == second.result_id == third.result_id


def test_sparse_population_resolves_unresolved_with_complete_explanation():
    result = DispositionResolver.resolve(_service().build(_population()))

    assert result.disposition == UNRESOLVED
    assert result.supporting_evidence
    assert result.missing_evidence
    assert result.dependency_groups_consulted
    assert result.decision_evidence_ids
    assert result.reasoning_chain[-1] == "The shadow disposition remains UNRESOLVED."


def test_known_infrastructure_resolves_infrastructure():
    package = _service().build(_population(exclusions=({
        "type": "CEX", "detail": "Known exchange", "source": "infra_mapping",
    },)))
    result = DispositionResolver.resolve(package)

    assert result.disposition == INFRASTRUCTURE
    assert any(item.evidence_type == "CEX" for item in result.contradictory_evidence)


def test_invalid_or_noise_evidence_resolves_rejected():
    package = _service().build(_population(exclusions=({
        "type": "DUST_PATTERN", "detail": "Observed dust pattern",
        "source": "wt_active_subprov_sessions",
    },)))
    assert DispositionResolver.resolve(package).disposition == REJECTED


def test_observed_nonterminal_contradiction_resolves_review():
    package = _service().build(_population(warnings=("Observed dependency conflict",)))
    assert DispositionResolver.resolve(package).disposition == REVIEW


def test_independent_control_and_population_evidence_resolves_candidate():
    package = _service().build(_population())
    package = _with_item(package, _item("RETURN_TO_CONTROLLER", group="settlement"))
    result = DispositionResolver.resolve(package)

    assert result.disposition == OPERATOR_CANDIDATE
    assert any("different dependency groups" in reason for reason in result.reasoning_chain)


def test_explicit_retirement_fact_resolves_retired_without_clock():
    package = _service().build(_population())
    package = _with_item(
        package, _item("POPULATION_RETIRED", role="CONTEXT", group="operation_history"),
        section="context",
    )
    assert DispositionResolver.resolve(package).disposition == RETIRED


def test_canonical_promotion_fact_resolves_confirmed_operation():
    package = _service().build(_population())
    package = _with_item(
        package, _item("MANUAL_PROMOTION", role="CONTEXT", group="operation_history"),
        section="context",
    )
    result = DispositionResolver.resolve(package)
    assert result.disposition == CONFIRMED_OPERATION
    promotion = next(item for item in result.context_evidence if item.evidence_type == "MANUAL_PROMOTION")
    assert promotion.evidence_id in result.decision_evidence_ids


def test_conflicting_canonical_and_infrastructure_facts_resolve_review():
    package = _service().build(_population(exclusions=({
        "type": "CEX", "detail": "Known exchange", "source": "infra_mapping",
    },)))
    package = _with_item(
        package, _item("MANUAL_PROMOTION", role="CONTEXT", group="operation_history"),
        section="context",
    )
    assert DispositionResolver.resolve(package).disposition == REVIEW


def test_live_controls_and_all_populations_resolve_in_shadow():
    service = EmergingOperatorService(
        "database/wt_ops_v2.db", "database/flex_complete_database.db"
    )
    with service._connect(service.ops_db_path) as conn:
        populations = service._population_builder().build(
            service._discovery_profiles(conn, service._tables(conn))
        )
    reconciler = EvidenceReconciliationService(service.ops_db_path)
    packages = reconciler.build_all(populations)
    results = tuple(DispositionResolver.resolve(package) for package in packages)
    families = service._compose()
    family_by_id = {family["family_id"]: family for family in families}

    assert len(results) == len(populations) == 281
    assert all(result.disposition for result in results)
    assert len({result.result_id for result in results}) == len(results)

    b48 = next(family for family in families if "B48k" in family["family_name"])
    c7 = next(family for family in families if "C7Ha" in family["family_name"])
    by_population = {result.population_id: result for result in results}
    assert by_population[b48["family_id"]].disposition == UNRESOLVED
    assert by_population[c7["family_id"]].disposition == REVIEW

    infrastructure_results = [
        result for result in results if result.disposition == INFRASTRUCTURE
    ]
    assert infrastructure_results
    assert all(result.contradictory_evidence for result in infrastructure_results)

    background_results = [
        by_population[family["family_id"]]
        for family in families
        if family["stage"] == "BACKGROUND" and family["family_id"] in by_population
    ]
    assert background_results
    assert all(result.disposition == UNRESOLVED for result in background_results[:10])

    comparisons = tuple(
        DispositionResolver.compare_legacy(
            family_by_id[result.population_id]["stage"], result
        ) for result in results
    )
    assert len(comparisons) == len(results)
    assert all(comparison.transition for comparison in comparisons)

    watchtower = next(family for family in families if family["family_name"] == "WATCHTOWER")
    watchtower_package = reconciler.build(
        reconciler.population_from_canonical_registry(watchtower)
    )
    assert DispositionResolver.resolve(watchtower_package).disposition == CONFIRMED_OPERATION
