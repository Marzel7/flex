from dataclasses import replace

import pytest

from src.evidence.contracts.production_shadow_boundary import (
    AUTHORITY_CLASS,
    ProductionShadowBoundaryError,
    build_production_shadow_boundary,
    classify_read_only_statement,
    verify_production_shadow_boundary,
)


SURFACES = (
    {"database_id": "main", "relation_name": "token_analysis", "relation_type": "TABLE"},
    {"database_id": "ops", "relation_name": "wt_watchtower_launches", "relation_type": "TABLE"},
)


def _boundary():
    return build_production_shadow_boundary(engineering_revision="eeaa1ecc", surfaces=SURFACES)


def test_boundary_is_deterministic_immutable_and_non_authorizing():
    first = _boundary()
    second = build_production_shadow_boundary(
        engineering_revision="eeaa1ecc", surfaces=reversed(SURFACES)
    )
    assert first == second
    assert verify_production_shadow_boundary(first)
    assert first.authority_class == AUTHORITY_CLASS
    assert not first.grants_extraction_authority
    assert not first.grants_activation_authority
    assert not first.allows_evidence_extraction
    assert not first.allows_shadow_evidence_output
    assert not first.allows_production_writes
    assert not first.allows_provider_rpc


@pytest.mark.parametrize(
    "sql,expected",
    [
        ("PRAGMA query_only", "PRAGMA_QUERY_ONLY"),
        ("PRAGMA table_info(token_analysis)", "PRAGMA_TABLE_INFO"),
        ("PRAGMA index_list(token_analysis)", "PRAGMA_INDEX_LIST"),
        ("SELECT rowid FROM token_analysis WHERE rowid<=?", "SELECT_METADATA_ONLY"),
        (
            "EXPLAIN QUERY PLAN SELECT rowid FROM token_analysis WHERE rowid<=?",
            "EXPLAIN_QUERY_PLAN_SELECT",
        ),
    ],
)
def test_only_allowlisted_read_only_statement_classes_are_accepted(sql, expected):
    assert classify_read_only_statement(_boundary(), database_id="main", sql=sql) == expected


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE token_analysis SET mint='x'",
        "SELECT rowid FROM token_analysis; SELECT 1",
        "CREATE TEMP TABLE x(a)",
        "ATTACH DATABASE 'x' AS x",
        "SELECT rowid FROM unknown_table",
    ],
)
def test_writes_multistatements_temporary_objects_and_unknown_surfaces_fail_closed(sql):
    with pytest.raises(ProductionShadowBoundaryError):
        classify_read_only_statement(_boundary(), database_id="main", sql=sql)


def test_surface_schema_duplicate_and_identifier_drift_fail_closed():
    with pytest.raises(ProductionShadowBoundaryError, match="DUPLICATE"):
        build_production_shadow_boundary(engineering_revision="eeaa1ecc", surfaces=(*SURFACES, SURFACES[0]))
    with pytest.raises(ProductionShadowBoundaryError, match="INVALID_RELATION_NAME"):
        build_production_shadow_boundary(
            engineering_revision="eeaa1ecc",
            surfaces=({"database_id": "main", "relation_name": "token-analysis", "relation_type": "TABLE"},),
        )


def test_digest_or_authority_mutation_fails_replay():
    boundary = _boundary()
    with pytest.raises(ProductionShadowBoundaryError, match="REPLAY"):
        verify_production_shadow_boundary(replace(boundary, boundary_digest="0" * 64))
    with pytest.raises(ProductionShadowBoundaryError):
        verify_production_shadow_boundary(
            replace(boundary, grants_extraction_authority=True)
        )
