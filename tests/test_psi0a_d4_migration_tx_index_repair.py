import sqlite3

import pytest

from src.evidence.contracts.production_shadow_migration_tx_index_repair import (
    INDEX_NAME,
    MigrationTxIndexRepairError,
    apply_fixture_migration_tx_index_repair,
    build_migration_tx_index_repair_contract,
    verify_migration_tx_index_repair_contract,
    verify_migration_tx_index_repair_result,
)


def _contract():
    return build_migration_tx_index_repair_contract(engineering_revision="7a9c0c2b")


def _fixture(*, affinity="TEXT", existing_sql=None):
    connection = sqlite3.connect(":memory:")
    connection.execute(f"CREATE TABLE token_analysis(mint TEXT, migration_tx {affinity})")
    connection.executemany(
        "INSERT INTO token_analysis VALUES(?,?)",
        [("m1", "s1"), ("m2", "s2"), ("m3", None)],
    )
    if existing_sql:
        connection.execute(existing_sql)
    return connection


def test_contract_is_exact_single_index_fixture_only_and_replayable():
    contract = _contract()
    assert verify_migration_tx_index_repair_contract(contract)
    assert contract.index_name == INDEX_NAME
    assert contract.index_columns == ("migration_tx",)
    assert contract.statement.count("CREATE INDEX") == 1
    assert ";" not in contract.statement
    assert not contract.allows_production_access
    assert not contract.allows_production_ddl


def test_repair_preserves_rows_and_changes_reconciler_plan_to_index_search():
    connection = _fixture()
    result = apply_fixture_migration_tx_index_repair(
        _contract(), connection,
        fixture_authorization="FROZEN_OR_EPHEMERAL_FIXTURE_ONLY",
    )
    assert result.created and result.statements_executed == 1
    assert result.row_count_before == result.row_count_after == 3
    assert result.plan_before_full_scan
    assert result.plan_after_uses_required_index
    assert verify_migration_tx_index_repair_result(result)


def test_repair_is_idempotent_and_does_no_work_when_compatible():
    connection = _fixture(existing_sql="CREATE INDEX existing_migration_tx ON token_analysis(migration_tx)")
    result = apply_fixture_migration_tx_index_repair(
        _contract(), connection,
        fixture_authorization="FROZEN_OR_EPHEMERAL_FIXTURE_ONLY",
    )
    assert result.already_compatible and not result.created
    assert result.statements_executed == 0
    assert result.plan_after_uses_required_index


def test_conflicting_named_index_fails_closed():
    connection = _fixture(existing_sql=f"CREATE INDEX {INDEX_NAME} ON token_analysis(mint)")
    with pytest.raises(MigrationTxIndexRepairError, match="CONFLICTING_INDEX_DEFINITION"):
        apply_fixture_migration_tx_index_repair(
            _contract(), connection,
            fixture_authorization="FROZEN_OR_EPHEMERAL_FIXTURE_ONLY",
        )


@pytest.mark.parametrize("affinity", ["INTEGER", "REAL", "BLOB"])
def test_missing_or_incompatible_column_affinity_fails_closed(affinity):
    connection = _fixture(affinity=affinity)
    with pytest.raises(MigrationTxIndexRepairError, match="COLUMN_OR_TYPE_DRIFT"):
        apply_fixture_migration_tx_index_repair(
            _contract(), connection,
            fixture_authorization="FROZEN_OR_EPHEMERAL_FIXTURE_ONLY",
        )


def test_fixture_authorization_and_replay_tampering_fail_closed():
    connection = _fixture()
    with pytest.raises(MigrationTxIndexRepairError, match="FIXTURE_AUTHORIZATION_REQUIRED"):
        apply_fixture_migration_tx_index_repair(
            _contract(), connection, fixture_authorization="PRODUCTION"
        )
    result = apply_fixture_migration_tx_index_repair(
        _contract(), connection,
        fixture_authorization="FROZEN_OR_EPHEMERAL_FIXTURE_ONLY",
    )
    object.__setattr__(result, "row_count_after", 4)
    with pytest.raises(MigrationTxIndexRepairError, match="RESULT_REPLAY_MISMATCH"):
        verify_migration_tx_index_repair_result(result)
