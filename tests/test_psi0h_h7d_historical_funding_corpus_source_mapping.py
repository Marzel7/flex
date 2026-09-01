import json
import sqlite3
from pathlib import Path

import pytest

from src.evidence.contracts.psi0h_h7d_historical_funding_corpus_source_mapping import (
    BLOCKER_H7D_NO_ROLE_TOPOLOGY,
    SOURCE_CLASS_BOUNDARY_CAPABLE,
    SOURCE_CLASS_NOT_RETAINED,
    SOURCE_CLASS_CANDIDATE_ONLY,
    VERDICT_HOLD_PARTIAL_CORPUS,
    VERDICT_READY_BOUNDARY_CAPABLE,
    Psi0hH7DHistoricalFundingCorpusSourceMappingError,
    qualify_historical_funding_corpus_source_mapping,
    verify_h7d_source_mapping,
)


def _write_capable_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute(
        "CREATE TABLE creator_funders("
        "creator_address TEXT, funder_address TEXT, amount_sol REAL, first_detected_at INTEGER)"
    )
    c.execute(
        "CREATE TABLE creator_tx_ledger("
        "creator_pubkey TEXT, signature TEXT, slot INTEGER, blockTime INTEGER, delta_sol_lamports INTEGER, tx_type TEXT, counterparty TEXT, source TEXT)"
    )
    c.execute(
        "CREATE TABLE token_analysis("
        "mint TEXT, earliest_tx_creator TEXT, migration_tx TEXT, migrated_at INTEGER, migration_slot INTEGER)"
    )

    c.execute(
        "INSERT INTO creator_funders VALUES (?,?,?,?)",
        ("c1", "f1", 1.0, 100),
    )
    c.execute(
        "INSERT INTO creator_funders VALUES (?,?,?,?)",
        ("c1", "f1", 2.0, 200),
    )
    c.execute(
        "INSERT INTO creator_tx_ledger VALUES (?,?,?,?,?,?,?,?)",
        ("c1", "s1", 1, 1000, 100, "SYSTEM_TRANSFER", "x1", "source1"),
    )
    c.execute(
        "INSERT INTO token_analysis VALUES (?,?,?,?,?)",
        ("m1", "c1", "tx1", 3000, 10),
    )
    conn.commit()
    conn.close()


def _write_candidate_only_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute("CREATE TABLE creator_funders(creator_address TEXT, funder_address TEXT, amount_sol REAL, first_detected_at INTEGER)")
    # no launch fields are present in this schema sample
    c.execute("INSERT INTO creator_funders VALUES (?,?,?,?)", ("a1", "b1", 0.5, 111))
    conn.commit()
    conn.close()


def test_h7d_classifies_boundary_capable(tmp_path):
    db = tmp_path / "capable.db"
    _write_capable_db(db)
    result = qualify_historical_funding_corpus_source_mapping(
        funding_sources=[str(db)],
        maximum_rows_per_source=250000,
    )

    verify_h7d_source_mapping(result)
    assert result["status"] == "PASS"
    assert result["verdict"] == VERDICT_READY_BOUNDARY_CAPABLE
    assert result["source_inventory"]["reconstructable_source_count"] == 1
    assert result["source_inventory"]["source_rows"][0]["source_class"] == SOURCE_CLASS_BOUNDARY_CAPABLE
    assert result["source_inventory"]["source_rows"][0]["row_counts"]["creator_funders"] == 2


def test_h7d_holds_candidate_only(tmp_path):
    db = tmp_path / "candidate.db"
    _write_candidate_only_db(db)
    result = qualify_historical_funding_corpus_source_mapping(
        funding_sources=[str(db)],
        maximum_rows_per_source=250000,
    )
    verify_h7d_source_mapping(result)
    assert result["status"] == "HOLD"
    assert result["verdict"] == VERDICT_HOLD_PARTIAL_CORPUS
    assert result["source_inventory"]["reconstructable_source_count"] == 0
    assert result["source_inventory"]["source_rows"][0]["source_class"] == SOURCE_CLASS_CANDIDATE_ONLY
    assert BLOCKER_H7D_NO_ROLE_TOPOLOGY not in result["source_inventory"]["source_rows"][0]["source_blockers"]


def test_h7d_missing_source_returns_not_retained():
    result = qualify_historical_funding_corpus_source_mapping(
        funding_sources=["/tmp/definitely_missing_938271.db"],
        maximum_rows_per_source=250000,
    )
    verify_h7d_source_mapping(result)
    assert result["status"] == "HOLD"
    assert result["source_inventory"]["source_rows"][0]["source_class"] == SOURCE_CLASS_NOT_RETAINED


def test_h7d_invalid_inputs_rejected():
    with pytest.raises(Psi0hH7DHistoricalFundingCorpusSourceMappingError, match="H7D_SOURCE_LIST_INVALID"):
        qualify_historical_funding_corpus_source_mapping(funding_sources=[], maximum_rows_per_source=1)

    with pytest.raises(Psi0hH7DHistoricalFundingCorpusSourceMappingError, match="H7D_MAX_ROWS_PER_SOURCE_INVALID"):
        qualify_historical_funding_corpus_source_mapping(funding_sources=["/tmp/x"], maximum_rows_per_source=0)


def test_h7d_runner_writes_artifact(tmp_path, monkeypatch):
    from scripts.run_psi0h_h7d_historical_funding_corpus_source_mapping import run

    db = tmp_path / "capable.db"
    _write_capable_db(db)
    out = tmp_path / "out" / "h7d.json"
    payload = run(funding_dbs=[str(db)], output=str(out), max_rows_per_source=250000)
    artifact = json.loads(out.read_text(encoding="utf-8"))
    assert payload["artifact"] == str(out)
    assert artifact["schema_version"] == "psi0h-h7d.historical-funding-corpus-source-mapping.v1"
    verify_h7d_source_mapping(artifact)
