import sqlite3

import pytest

from src.core import treasury_bank
from src.ops import attribution_outcome


@pytest.mark.parametrize("module", [treasury_bank, attribution_outcome])
def test_leaf_validator_accepts_its_legacy_schema(module):
    conn = sqlite3.connect(":memory:")
    module.ensure_schema(conn)
    assert module.validate_schema(conn) == "VALID"


@pytest.mark.parametrize("module,table", [
    (treasury_bank, "wt_confirmed_treasuries"),
    (attribution_outcome, "wt_attribution_outcomes"),
])
def test_leaf_validator_fails_closed_for_missing_table(module, table):
    conn = sqlite3.connect(":memory:"); module.ensure_schema(conn)
    conn.execute(f"DROP TABLE {table}")
    assert module.validate_schema(conn).startswith("SCHEMA_MIGRATION_REQUIRED:missing_table")


@pytest.mark.parametrize("module,table,column", [
    (treasury_bank, "wt_confirmed_treasuries", "provenance"),
    (attribution_outcome, "wt_attribution_outcomes", "materialized_at"),
])
def test_leaf_validator_fails_closed_for_missing_column(module, table, column):
    conn = sqlite3.connect(":memory:"); module.ensure_schema(conn)
    conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    assert module.validate_schema(conn).startswith("SCHEMA_MIGRATION_REQUIRED:missing_column")


@pytest.mark.parametrize("module,index", [(treasury_bank, "ix_tfd_wallet"), (attribution_outcome, "ix_wao_type_time")])
def test_leaf_validator_fails_closed_for_missing_index(module, index):
    conn = sqlite3.connect(":memory:"); module.ensure_schema(conn)
    conn.execute(f"DROP INDEX {index}")
    assert module.validate_schema(conn).startswith("SCHEMA_MIGRATION_REQUIRED:missing_index")
