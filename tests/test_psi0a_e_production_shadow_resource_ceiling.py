from dataclasses import replace

import pytest

from src.evidence.contracts.production_shadow_resource_ceiling import (
    QUERY_IDS,
    ProductionShadowResourceCeilingError,
    ResourceUsageProposal,
    build_production_shadow_resource_ceiling_contract,
    validate_resource_usage_proposal,
    verify_production_shadow_resource_ceiling_contract,
)


def _usage(**changes):
    values = {
        "query_rows": tuple((query_id, 100) for query_id in QUERY_IDS),
        "query_canonical_bytes": tuple((query_id, 1024) for query_id in QUERY_IDS),
        "query_seconds": tuple((query_id, 0.5) for query_id in QUERY_IDS),
        "transaction_seconds": tuple((query_id, 0.75) for query_id in QUERY_IDS),
        "query_temporary_bytes": tuple(
            (query_id, 1024 if query_id == "snapshot_selected_cohort" else 0)
            for query_id in QUERY_IDS
        ),
        "total_wall_seconds": 4.0,
        "connections_opened": 5,
        "maximum_concurrent_connections": 1,
        "process_rss_delta_bytes": 1024 * 1024,
        "sqlite_temporary_bytes": 1024,
    }
    values.update(changes)
    return ResourceUsageProposal(**values)


def _replace_value(items, query_id, value):
    return tuple((key, value if key == query_id else current) for key, current in items)


def test_contract_is_immutable_exact_and_non_authorizing():
    contract = build_production_shadow_resource_ceiling_contract()
    assert verify_production_shadow_resource_ceiling_contract(contract)
    assert tuple(item.query_id for item in contract.query_ceilings) == QUERY_IDS
    assert not contract.pagination_allowed
    assert not contract.retry_allowed
    assert not contract.failover_allowed
    assert not contract.adaptive_widening_allowed
    assert not contract.grants_extraction_authority
    assert not contract.grants_activation_authority


def test_snapshot_temporary_ordering_is_the_only_bounded_temp_surface():
    contract = build_production_shadow_resource_ceiling_contract()
    snapshot = contract.query_ceilings[-1]
    assert snapshot.query_id == "snapshot_selected_cohort"
    assert snapshot.permits_bounded_temporary_ordering
    assert snapshot.maximum_rows == 5_000
    assert snapshot.maximum_temporary_bytes == 32 * 1024 * 1024
    assert all(not item.permits_bounded_temporary_ordering for item in contract.query_ceilings[:-1])
    assert all(item.maximum_temporary_bytes == 0 for item in contract.query_ceilings[:-1])


def test_bounded_usage_passes_exact_accounting():
    assert validate_resource_usage_proposal(
        build_production_shadow_resource_ceiling_contract(), _usage()
    )


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("pagination_attempts", "WIDENING_OR_RETRY"),
        ("retry_attempts", "WIDENING_OR_RETRY"),
        ("failover_attempts", "WIDENING_OR_RETRY"),
        ("adaptive_limit_changes", "WIDENING_OR_RETRY"),
    ],
)
def test_retry_pagination_failover_and_widening_fail_closed(field, reason):
    with pytest.raises(ProductionShadowResourceCeilingError, match=reason):
        validate_resource_usage_proposal(
            build_production_shadow_resource_ceiling_contract(), _usage(**{field: 1})
        )


def test_each_query_dimension_fails_closed_with_named_reason():
    contract = build_production_shadow_resource_ceiling_contract()
    base = _usage()
    cases = (
        ("query_rows", 5_001, "QUERY_ROW_CEILING"),
        ("query_canonical_bytes", 20 * 1024 * 1024, "QUERY_BYTE_CEILING"),
        ("query_seconds", 5.01, "QUERY_DEADLINE"),
        ("transaction_seconds", 6.01, "TRANSACTION_LIFETIME"),
        ("query_temporary_bytes", 1, "QUERY_TEMPORARY_CEILING"),
    )
    for field, value, reason in cases:
        query_id = "creator_selected_cohort"
        changed = _replace_value(getattr(base, field), query_id, value)
        with pytest.raises(ProductionShadowResourceCeilingError, match=reason):
            validate_resource_usage_proposal(contract, replace(base, **{field: changed}))


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"total_wall_seconds": 30.01}, "WALL_DEADLINE"),
        ({"connections_opened": 6}, "CONNECTION_CEILING"),
        ({"maximum_concurrent_connections": 2}, "CONCURRENT_CONNECTION"),
        ({"process_rss_delta_bytes": 129 * 1024 * 1024}, "MEMORY_CEILING"),
        ({"sqlite_temporary_bytes": 33 * 1024 * 1024}, "SQLITE_TEMPORARY"),
    ],
)
def test_global_resource_ceilings_fail_closed(changes, reason):
    with pytest.raises(ProductionShadowResourceCeilingError, match=reason):
        validate_resource_usage_proposal(
            build_production_shadow_resource_ceiling_contract(), _usage(**changes)
        )


def test_missing_duplicate_and_residual_accounting_fail_closed():
    contract = build_production_shadow_resource_ceiling_contract()
    base = _usage()
    with pytest.raises(ProductionShadowResourceCeilingError, match="ROW_ACCOUNTING"):
        validate_resource_usage_proposal(contract, replace(base, query_rows=base.query_rows[:-1]))
    duplicate = base.query_rows[:-1] + ((base.query_rows[0][0], 1),)
    with pytest.raises(ProductionShadowResourceCeilingError, match="ROW_ACCOUNTING"):
        validate_resource_usage_proposal(contract, replace(base, query_rows=duplicate))
    with pytest.raises(ProductionShadowResourceCeilingError, match="ACCOUNTING_RESIDUAL"):
        validate_resource_usage_proposal(contract, replace(base, sqlite_temporary_bytes=2048))


def test_contract_mutation_fails_exact_replay():
    contract = build_production_shadow_resource_ceiling_contract()
    with pytest.raises(ProductionShadowResourceCeilingError, match="CONTRACT_REPLAY"):
        verify_production_shadow_resource_ceiling_contract(
            replace(contract, maximum_total_rows=contract.maximum_total_rows + 1)
        )
