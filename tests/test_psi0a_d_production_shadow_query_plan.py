import json
import sqlite3

import pytest

from src.evidence.contracts.production_shadow_query_plan import (
    PSI0A_D2A_CONTRACT_DIGEST,
    PSI0A_D2A_ENGINEERING_REVISION,
    PSI0A_D2A_SUPERSEDED_CONTRACT_DIGEST,
    ProductionShadowQueryPlanError,
    build_psi0a_d2a_rebound_contract,
    build_production_shadow_query_contract,
    qualify_production_shadow_query_plans,
    verify_production_shadow_plan_qualification,
    verify_production_shadow_query_contract,
)


MANIFEST = "d956bc24c1cd160162acaaad5bc466a2dece78ea34fc1f5238bc80728d4283f5"
BOUNDARY = "fdf11dc5e29c176d3724a4ccd1e3ff56584727512853bfb58a71fb3979c246f8"


def _contract():
    return build_production_shadow_query_contract(
        engineering_revision="0ab2e8e7", canonical_manifest_digest=MANIFEST,
        read_boundary_digest=BOUNDARY,
    )


def _sources(tmp_path):
    definitions = {
        "creator":
            "CREATE TABLE creator_tokens(creator_address TEXT,mint TEXT,created_at INTEGER);"
            "CREATE INDEX idx_creator_mint ON creator_tokens(mint)",
        "evidence":
            "CREATE TABLE normalized_evidence_records(fact_family TEXT,payload_json TEXT,"
            "raw_artifact_digest TEXT,acquired_at INTEGER,source_id TEXT,source_version TEXT,"
            "verification_state TEXT);CREATE INDEX idx_evidence_family ON normalized_evidence_records(fact_family)",
        "main":
            "CREATE TABLE token_analysis(mint TEXT,migrated_at INTEGER,first_observed_mc REAL,"
            "first_observed_price REAL,first_observed_at INTEGER,first_observed_source TEXT,"
            "first_observed_confidence REAL,pf_ws_creator TEXT,creator_mismatch INTEGER);"
            "CREATE INDEX idx_main_mint ON token_analysis(mint);"
            "CREATE TABLE token_price_snapshots(snapshot_id INTEGER,mint TEXT,price_usd REAL,"
            "market_cap REAL,source TEXT,captured_at INTEGER,created_at INTEGER);"
            "CREATE INDEX idx_snapshot_mint_time ON token_price_snapshots(mint,captured_at)",
        "ops":
            "CREATE TABLE wt_watchtower_launches(mint TEXT,creator_wallet TEXT,create_signature TEXT,"
            "create_time INTEGER,create_slot INTEGER,creator_extraction_method TEXT,confidence TEXT,"
            "recorded_at INTEGER);CREATE INDEX idx_ops_mint ON wt_watchtower_launches(mint)",
    }
    paths = {}
    for name, statements in definitions.items():
        path = tmp_path / f"{name}.db"; connection = sqlite3.connect(path)
        connection.executescript(statements); connection.commit(); connection.close(); paths[name] = path
    return paths


def _parameters():
    return {
        "creator_rowid_upper_inclusive": 10, "evidence_rowid_upper_inclusive": 10,
        "token_analysis_rowid_upper_inclusive": 10, "snapshot_rowid_upper_inclusive": 10,
        "ops_rowid_upper_inclusive": 10, "cohort_mints_json": json.dumps(["mint-a"]),
        "fact_family": "LaunchFact", "row_limit": 100,
    }


def test_contract_is_exact_replayable_bounded_and_non_authorizing():
    contract = _contract()
    assert verify_production_shadow_query_contract(contract)
    assert len(contract.templates) == 5
    assert all("rowid<=?" in item.sql and "LIMIT ?" in item.sql for item in contract.templates)
    assert not contract.grants_extraction_authority
    assert not contract.grants_activation_authority


def test_d2a_rebind_changes_only_revision_and_contract_identity():
    previous = _contract()
    rebound = build_psi0a_d2a_rebound_contract()
    assert rebound.templates == previous.templates
    assert rebound.contract_version == previous.contract_version
    assert rebound.canonical_manifest_digest == previous.canonical_manifest_digest
    assert rebound.read_boundary_digest == previous.read_boundary_digest
    assert rebound.authority_class == previous.authority_class
    assert rebound.grants_extraction_authority is False
    assert rebound.grants_activation_authority is False
    assert previous.contract_digest == PSI0A_D2A_SUPERSEDED_CONTRACT_DIGEST
    assert rebound.engineering_revision == PSI0A_D2A_ENGINEERING_REVISION
    assert rebound.contract_digest == PSI0A_D2A_CONTRACT_DIGEST
    assert rebound.contract_digest != previous.contract_digest
    assert verify_production_shadow_query_contract(rebound)


def test_fixture_plans_use_indexes_without_executing_selects(tmp_path):
    result = qualify_production_shadow_query_plans(
        _contract(), _sources(tmp_path), _parameters(), input_fingerprint="1" * 64,
    )
    assert result.compatible_query_count == 5
    assert result.incompatible_query_count == 0
    assert result.select_templates_executed == 0
    assert result.production_rows_read == 0
    assert verify_production_shadow_plan_qualification(result)


def test_missing_index_fails_plan_compatibility(tmp_path):
    paths = _sources(tmp_path)
    connection = sqlite3.connect(paths["ops"]); connection.execute("DROP INDEX idx_ops_mint"); connection.close()
    result = qualify_production_shadow_query_plans(
        _contract(), paths, _parameters(), input_fingerprint="2" * 64,
    )
    finding = next(item for item in result.findings if item.query_id == "ops_selected_cohort")
    assert not finding.compatible


@pytest.mark.parametrize("seconds", [0, -1, 2.1, True])
def test_invalid_deadline_fails_closed(tmp_path, seconds):
    with pytest.raises(ProductionShadowQueryPlanError, match="INVALID_QUERY_DEADLINE"):
        qualify_production_shadow_query_plans(
            _contract(), _sources(tmp_path), _parameters(), input_fingerprint="3" * 64,
            max_query_seconds=seconds,
        )


def test_missing_parameter_and_source_expansion_fail_closed(tmp_path):
    paths = _sources(tmp_path)
    params = _parameters(); params.pop("row_limit")
    with pytest.raises(ProductionShadowQueryPlanError, match="MISSING_PARAMETER"):
        qualify_production_shadow_query_plans(_contract(), paths, params, input_fingerprint="4" * 64)
    with pytest.raises(ProductionShadowQueryPlanError, match="SOURCE_SET"):
        qualify_production_shadow_query_plans(
            _contract(), {**paths, "extra": paths["main"]}, _parameters(), input_fingerprint="5" * 64,
        )
