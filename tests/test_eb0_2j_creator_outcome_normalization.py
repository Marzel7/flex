import hashlib
from pathlib import Path
import sqlite3
import asyncio

import pytest

from src.evidence.contracts.creator_historical_outcome_extractor import OutcomePolicy, extract_creator_historical_outcomes
from src.evidence.contracts.creator_historical_outcome_normalization import (
    CreatorOutcomeNormalizationError,
    SourceHighWaters,
    materialize_normalized_fixture,
    normalize_creator_outcome_sources,
)
from src.core.ws_cascade_store import ensure_cascade_schema
from src.creators.repository import CreatorRepository


def _sources(root: Path):
    main, ops, creator = root / "main.db", root / "ops.db", root / "creator.db"
    c = sqlite3.connect(main); c.executescript("""
      CREATE TABLE token_analysis(mint TEXT PRIMARY KEY,pf_ws_creator TEXT,creator_mismatch INTEGER,first_observed_at REAL,first_observed_mc REAL,first_observed_price REAL,first_observed_source TEXT);
      INSERT INTO token_analysis VALUES('MintA','CreatorA',0,1100,150000,NULL,'fixture');
      INSERT INTO token_analysis VALUES('MintB','CreatorB',1,1100,NULL,0.1,'fixture');
      INSERT INTO token_analysis VALUES('Outside','OutsideCreator',0,1100,999999,NULL,'fixture');
    """); c.commit(); c.close()
    c = sqlite3.connect(ops); c.executescript("""
      CREATE TABLE wt_watchtower_launches(mint TEXT,creator_wallet TEXT,create_signature TEXT,create_time INTEGER,create_slot INTEGER,creator_extraction_method TEXT,confidence TEXT,recorded_at INTEGER);
      INSERT INTO wt_watchtower_launches VALUES('MintA','CreatorA','SigA',1000,50,'CLOSE_ACCOUNT_DESTINATION','STRICT',1001);
      INSERT INTO wt_watchtower_launches VALUES('MintB','OtherCreator','SigB',1000,51,'CLOSE_ACCOUNT_DESTINATION','STRICT',1001);
      INSERT INTO wt_watchtower_launches VALUES('Outside','OutsideCreator','SigO',1000,52,'CLOSE_ACCOUNT_DESTINATION','STRICT',1001);
    """); c.commit(); c.close()
    c = sqlite3.connect(creator); c.executescript("""
      CREATE TABLE creator_tokens(creator_address TEXT,mint TEXT,created_at INTEGER);
      INSERT INTO creator_tokens VALUES('CreatorA','MintA',1000);
      INSERT INTO creator_tokens VALUES('CreatorB','MintB',1000);
      INSERT INTO creator_tokens VALUES('OutsideCreator','Outside',1000);
    """); c.commit(); c.close()
    return main, ops, creator


def _high_waters():
    return SourceHighWaters(3, 3, 3, 2_000_000_000_000)


def _hashes(paths):
    return [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]


def test_split_sources_normalize_and_feed_eb0_2g_without_complete_negative_claim(tmp_path):
    paths = _sources(tmp_path)
    before = _hashes(paths)
    normalized = normalize_creator_outcome_sources(*paths, cohort_mints=("MintA", "MintB"), high_waters=_high_waters())
    assert [row["event_kind"] for row in normalized.canonical_observations].count("CHAIN_BIRTH") == 2
    assert [row["event_kind"] for row in normalized.canonical_observations].count("MARKET_FIRST_OBSERVED") == 2
    assert [row["mint"] for row in normalized.creator_identity_facts] == ["MintA"]
    assert normalized.creator_identity_facts[0]["resolution_method"] == "CANONICAL_CREATE_PROOF"
    assert all(row["full_horizon_complete"] == 0 for row in normalized.observation_window_facts)
    assert normalized.excluded_mints == {"MintB": "MISSING_AMBIGUOUS_OR_MISMATCHED_CREATOR_IDENTITY"}
    assert _hashes(paths) == before

    output = tmp_path / "normalized.db"
    materialize_normalized_fixture(normalized, output)
    result = extract_creator_historical_outcomes(
        output, policies=(OutcomePolicy("CHAIN_BIRTH", "MARKET_CAP_AT_LEAST_BY_HORIZON", 500_000_000_000, "100000"),)
    )
    assert result.qualified_mints == ("MintA",)
    assert result.excluded_mints == {"MintB": "MISSING_OR_AMBIGUOUS_CREATOR_IDENTITY"}
    assert result.unknown_count == 0
    assert result.eligible_denominator_count == 0
    assert result.manifests[0].facts[0].outcome_state == "OBSERVED_TRUE"
    assert result.manifests[0].facts[0].completeness_state == "PARTIAL"


def test_missing_market_or_window_completeness_remains_explicit(tmp_path):
    paths = _sources(tmp_path)
    normalized = normalize_creator_outcome_sources(*paths, cohort_mints=("MintA",), high_waters=_high_waters())
    assert normalized.observation_window_facts[0]["full_horizon_complete"] == 0
    output = tmp_path / "normalized.db"; materialize_normalized_fixture(normalized, output)
    result = extract_creator_historical_outcomes(
        output, policies=(OutcomePolicy("CHAIN_BIRTH", "MIGRATION_BY_HORIZON", 500_000_000_000),)
    )
    fact = result.manifests[0].facts[0]
    assert fact.outcome_state == "UNKNOWN"
    assert fact.completeness_state == "NOT_OBSERVED"
    assert fact.denominator_eligible is False


def test_high_water_and_cohort_exclude_later_and_unselected_rows(tmp_path):
    paths = _sources(tmp_path)
    first = normalize_creator_outcome_sources(*paths, cohort_mints=("MintA",), high_waters=SourceHighWaters(1, 1, 1, 2_000_000_000_000))
    second = normalize_creator_outcome_sources(*paths, cohort_mints=("MintA",), high_waters=SourceHighWaters(1, 1, 1, 2_000_000_000_000))
    assert first == second
    assert all(row["mint"] == "MintA" for row in first.canonical_observations)


def test_mismatch_and_ambiguous_membership_fail_identity_closed(tmp_path):
    paths = _sources(tmp_path)
    c = sqlite3.connect(paths[2]); c.execute("INSERT INTO creator_tokens VALUES('Other','MintA',1002)"); c.commit(); c.close()
    normalized = normalize_creator_outcome_sources(*paths, cohort_mints=("MintA", "MintB"), high_waters=SourceHighWaters(3,3,4,2_000_000_000_000))
    assert normalized.creator_identity_facts == ()
    assert set(normalized.excluded_mints) == {"MintA", "MintB"}


def test_weaker_registry_provenance_never_becomes_identity_or_create_proof(tmp_path):
    paths = _sources(tmp_path)
    c = sqlite3.connect(paths[1])
    c.execute("DELETE FROM wt_watchtower_launches WHERE mint='MintA'")
    c.executemany("INSERT INTO wt_watchtower_launches VALUES(?,?,?,?,?,?,?,?)", [
        ("MintA","CreatorA",None,1000,None,"WALKBACK_RECOVERED","WALKBACK",1001),
        ("MintA","CreatorA",None,1000,None,"POST_MIG_BACKFILL","BACKFILL",1001),
        ("MintA","CreatorA","ManualSig",1000,50,"MANUAL","MANUAL_ATTESTATION",1001),
    ])
    c.commit(); c.close()
    normalized = normalize_creator_outcome_sources(
        *paths, cohort_mints=("MintA",),
        high_waters=SourceHighWaters(3, 5, 3, 2_000_000_000_000),
    )
    assert normalized.creator_identity_facts == ()
    assert not any(row["event_kind"] == "CHAIN_BIRTH" for row in normalized.canonical_observations)
    assert normalized.excluded_mints == {"MintA": "MISSING_AMBIGUOUS_OR_MISMATCHED_CREATOR_IDENTITY"}


def test_schema_deadline_invalid_bounds_and_existing_output_fail_closed(tmp_path):
    paths = _sources(tmp_path)
    with pytest.raises(CreatorOutcomeNormalizationError, match="INVALID_COHORT"):
        normalize_creator_outcome_sources(*paths, cohort_mints=(), high_waters=_high_waters())
    ticks = iter([0.0, 2.0])
    with pytest.raises(CreatorOutcomeNormalizationError, match="QUERY_TIMEOUT"):
        normalize_creator_outcome_sources(*paths, cohort_mints=("MintA",), high_waters=_high_waters(), max_query_seconds=1, clock=lambda: next(ticks, 2.0))
    normalized = normalize_creator_outcome_sources(*paths, cohort_mints=("MintA",), high_waters=_high_waters())
    output = tmp_path / "exists.db"; output.write_text("no")
    with pytest.raises(CreatorOutcomeNormalizationError, match="OUTPUT_EXISTS"):
        materialize_normalized_fixture(normalized, output)


def test_schema_initializers_create_mint_leading_indexes_and_query_plans(tmp_path):
    ops = sqlite3.connect(tmp_path / "ops-index.db")
    ensure_cascade_schema(ops)
    ops_plan = ops.execute(
        "EXPLAIN QUERY PLAN SELECT rowid,mint FROM wt_watchtower_launches "
        "WHERE rowid<=? AND mint IN (?) ORDER BY mint,rowid",
        (100, "MintA"),
    ).fetchall()
    assert any("ix_launches_mint" in row[3] for row in ops_plan)
    ops.close()

    creator_path = tmp_path / "creator-index.db"
    asyncio.run(CreatorRepository(str(creator_path), asyncio.Lock()).ensure_schema())
    creator = sqlite3.connect(creator_path)
    creator_plan = creator.execute(
        "EXPLAIN QUERY PLAN SELECT rowid,creator_address,mint FROM creator_tokens "
        "WHERE rowid<=? AND mint IN (?) ORDER BY mint,rowid",
        (100, "MintA"),
    ).fetchall()
    assert any("idx_creator_tokens_mint" in row[3] for row in creator_plan)
    creator.close()
