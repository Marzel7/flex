import sqlite3

import pytest

from src.evidence.contracts.current_market_data_contract import (
    build_current_market_data_contract,
    validate_current_market_data_contract,
)


def _compact_db(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
      CREATE TABLE token_analysis(
        mint TEXT, first_observed_mc REAL, first_observed_price REAL,
        first_observed_at INTEGER, first_observed_source TEXT,
        first_observed_confidence REAL
      );
      CREATE TABLE token_market_cap_peaks(mint TEXT, peak_market_cap REAL);
    """)
    conn.close()


def test_current_contract_is_deterministic_and_compact_only(tmp_path):
    path = tmp_path / "compact.sqlite"; _compact_db(path)
    first = build_current_market_data_contract()
    assert first == build_current_market_data_contract()
    assert validate_current_market_data_contract(path) == first
    assert "token_price_snapshots" not in first.required_relations


def test_current_contract_fails_closed_for_missing_compact_authority(tmp_path):
    path = tmp_path / "missing.sqlite"
    sqlite3.connect(path).close()
    with pytest.raises(RuntimeError, match="REQUIRED_RELATION_MISSING"):
        validate_current_market_data_contract(path)
