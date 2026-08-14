from dataclasses import replace
import sqlite3

import pytest

from src.evidence.contracts.production_shadow_index_repair import (
    SOURCE_AUDIT_DIGEST,
    ProductionShadowIndexRepairError,
    apply_fixture_index_repairs,
    build_production_shadow_index_repair_contract,
    verify_index_repair_result,
    verify_production_shadow_index_repair_contract,
)


def _connections():
    evidence = sqlite3.connect(":memory:")
    evidence.execute("CREATE TABLE normalized_evidence_records(evidence_id TEXT PRIMARY KEY, fact_family TEXT NOT NULL)")
    evidence.execute("INSERT INTO normalized_evidence_records VALUES ('e1','birth')")
    main = sqlite3.connect(":memory:")
    main.execute("CREATE TABLE token_analysis(mint TEXT PRIMARY KEY, migrated_at INTEGER)")
    main.execute("INSERT INTO token_analysis VALUES ('m1',123)")
    return {"evidence": evidence, "main": main}


def _contract():
    return build_production_shadow_index_repair_contract(engineering_revision="36d5fc15")


def test_contract_binds_exact_repairs_statements_and_authority():
    contract = _contract()
    assert verify_production_shadow_index_repair_contract(contract)
    assert contract.source_audit_digest == SOURCE_AUDIT_DIGEST
    assert [(r.database_id, r.relation_name, r.index_columns) for r in contract.repairs] == [
        ("evidence", "normalized_evidence_records", ("fact_family",)),
        ("main", "token_analysis", ("migrated_at", "mint")),
    ]
    assert all(r.statement.startswith("CREATE INDEX IF NOT EXISTS ") for r in contract.repairs)
    assert all(";" not in r.statement for r in contract.repairs)
    assert contract.fixture_only
    assert not contract.allows_production_access
    assert not contract.allows_production_ddl
    assert not contract.grants_extraction_authority
    assert not contract.grants_activation_authority


def test_fixture_repair_is_idempotent_preserves_rows_and_replays():
    connections = _connections()
    before = {key: list(db.execute(f"SELECT * FROM {'normalized_evidence_records' if key == 'evidence' else 'token_analysis'}")) for key, db in connections.items()}
    first = apply_fixture_index_repairs(
        _contract(), connections, fixture_authorization="FROZEN_OR_EPHEMERAL_FIXTURE_ONLY"
    )
    assert first.statements_executed == 2
    assert verify_index_repair_result(first)
    second = apply_fixture_index_repairs(
        _contract(), connections, fixture_authorization="FROZEN_OR_EPHEMERAL_FIXTURE_ONLY"
    )
    assert second.statements_executed == 0
    assert len(second.already_compatible_indexes) == 2
    after = {key: list(db.execute(f"SELECT * FROM {'normalized_evidence_records' if key == 'evidence' else 'token_analysis'}")) for key, db in connections.items()}
    assert before == after


def test_existing_compatible_prefix_performs_no_work():
    connections = _connections()
    connections["evidence"].execute("CREATE INDEX existing_fact_family ON normalized_evidence_records(fact_family, evidence_id)")
    connections["main"].execute("CREATE INDEX existing_migration ON token_analysis(migrated_at, mint)")
    result = apply_fixture_index_repairs(
        _contract(), connections, fixture_authorization="FROZEN_OR_EPHEMERAL_FIXTURE_ONLY"
    )
    assert result.statements_executed == 0
    assert result.created_indexes == ()


def test_conflicting_named_index_fails_closed():
    connections = _connections()
    connections["evidence"].execute(
        "CREATE INDEX idx_psi0a_normalized_evidence_fact_family ON normalized_evidence_records(evidence_id)"
    )
    with pytest.raises(ProductionShadowIndexRepairError, match="CONFLICTING_INDEX"):
        apply_fixture_index_repairs(
            _contract(), connections, fixture_authorization="FROZEN_OR_EPHEMERAL_FIXTURE_ONLY"
        )


@pytest.mark.parametrize(
    "ddl",
    [
        "ALTER TABLE normalized_evidence_records ADD COLUMN extra TEXT",
        "DROP TABLE normalized_evidence_records",
        "CREATE INDEX bad ON normalized_evidence_records(fact_family); DROP TABLE normalized_evidence_records",
    ],
)
def test_contract_replay_rejects_non_index_or_multistatement_ddl(ddl):
    contract = _contract()
    repair = replace(contract.repairs[0], statement=ddl)
    with pytest.raises(ProductionShadowIndexRepairError, match="REPLAY_MISMATCH"):
        verify_production_shadow_index_repair_contract(
            replace(contract, repairs=(repair, contract.repairs[1]))
        )


def test_unknown_schema_type_drift_and_authority_expansion_fail_closed():
    connections = _connections()
    connections["main"].execute("DROP TABLE token_analysis")
    connections["main"].execute("CREATE TABLE token_analysis(mint TEXT PRIMARY KEY, migrated_at TEXT)")
    with pytest.raises(ProductionShadowIndexRepairError, match="COLUMN_OR_TYPE_DRIFT"):
        apply_fixture_index_repairs(
            _contract(), connections, fixture_authorization="FROZEN_OR_EPHEMERAL_FIXTURE_ONLY"
        )
    with pytest.raises(ProductionShadowIndexRepairError, match="REPLAY_MISMATCH"):
        verify_production_shadow_index_repair_contract(
            replace(_contract(), allows_production_ddl=True)
        )


def test_fixture_authorization_and_exact_database_set_are_required():
    with pytest.raises(ProductionShadowIndexRepairError, match="FIXTURE_AUTHORIZATION"):
        apply_fixture_index_repairs(_contract(), _connections(), fixture_authorization="PRODUCTION")
    connections = _connections()
    connections["extra"] = sqlite3.connect(":memory:")
    with pytest.raises(ProductionShadowIndexRepairError, match="DATABASE_SET_MISMATCH"):
        apply_fixture_index_repairs(
            _contract(), connections, fixture_authorization="FROZEN_OR_EPHEMERAL_FIXTURE_ONLY"
        )
