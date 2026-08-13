import hashlib
from pathlib import Path
import sqlite3

import pytest

from src.evidence.contracts.creator_historical_outcome_extractor import (
    CreatorHistoricalOutcomeExtractorError,
    OutcomePolicy,
    extract_creator_historical_outcomes,
)


def _database(path: Path, *, extra_column=False):
    conn = sqlite3.connect(path)
    extra = ", extra TEXT" if extra_column else ""
    conn.executescript(f"""
        CREATE TABLE cohort_mints(position INTEGER, mint TEXT{extra});
        CREATE TABLE eb0_1_canonical_observations(
          mint TEXT,event_kind TEXT,event_time_utc_ns INTEGER,source TEXT,
          source_version TEXT,observed_at_utc_ns INTEGER,
          price_or_market_cap_value TEXT,valuation_semantics TEXT,
          quality_state TEXT,completeness_state TEXT,source_record_digest TEXT);
        CREATE TABLE creator_identity_facts(
          mint TEXT,creator TEXT,resolution_method TEXT,source TEXT,
          source_version TEXT,source_record_digest TEXT);
        CREATE TABLE observation_window_facts(
          mint TEXT,observed_through_utc_ns INTEGER,full_horizon_complete INTEGER,
          source TEXT,source_version TEXT,source_record_digest TEXT);
    """)
    conn.executemany("INSERT INTO cohort_mints(position,mint) VALUES (?,?)", [(0,"MintA"),(1,"MintB"),(2,"MintC")])
    rows = [
        ("MintA","CHAIN_BIRTH",1000,"frozen","v1",1000,None,"UNKNOWN","OBSERVED","NOT_OBSERVED","a-birth"),
        ("MintA","MIGRATION",1300,"frozen","v1",1300,None,"UNKNOWN","OBSERVED","NOT_OBSERVED","a-migration"),
        ("MintB","CHAIN_BIRTH",1000,"frozen","v1",1000,None,"UNKNOWN","OBSERVED","NOT_OBSERVED","b-birth"),
        ("MintB","MARKET_FIRST_OBSERVED",1200,"frozen","v1",1200,"150000","MARKET_CAP_AT_EVENT","OBSERVED","COMPLETE","b-market"),
        ("MintC","CHAIN_BIRTH",1000,"frozen","v1",1000,None,"UNKNOWN","OBSERVED","NOT_OBSERVED","c-birth"),
    ]
    conn.executemany("INSERT INTO eb0_1_canonical_observations VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.executemany("INSERT INTO creator_identity_facts VALUES (?,?,?,?,?,?)", [
        ("MintA","CreatorA","PF_WS_CREATOR_VERIFIED","identity","v1","id-a"),
        ("MintB","CreatorB","CANONICAL_CREATE_PROOF","identity","v1","id-b"),
    ])
    conn.executemany("INSERT INTO observation_window_facts VALUES (?,?,?,?,?,?)", [
        ("MintA",1600,1,"window","v1","window-a"),
        ("MintB",1300,0,"window","v1","window-b"),
        ("MintC",1600,1,"window","v1","window-c"),
    ])
    conn.commit(); conn.close()


POLICIES = (
    OutcomePolicy("CHAIN_BIRTH", "MIGRATION_BY_HORIZON", 500),
    OutcomePolicy("CHAIN_BIRTH", "MARKET_CAP_AT_LEAST_BY_HORIZON", 500, "100000"),
)


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_query_only_extractor_accounts_for_qualified_excluded_unknown_and_denominator(tmp_path):
    path = tmp_path / "frozen.db"; _database(path)
    before = _sha(path)
    result = extract_creator_historical_outcomes(path, policies=POLICIES)
    assert result.selected_mints == ("MintA", "MintB", "MintC")
    assert result.qualified_mints == ("MintA", "MintB")
    assert result.excluded_mints == {"MintC": "MISSING_OR_AMBIGUOUS_CREATOR_IDENTITY"}
    assert result.policy_count == 2
    assert result.fact_count == 4
    assert result.eligible_denominator_count == 2
    assert result.unknown_count == 1
    assert len(result.corpora) == 2
    assert _sha(path) == before


def test_results_are_deterministic_and_do_not_use_unselected_rows(tmp_path):
    path = tmp_path / "frozen.db"; _database(path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO eb0_1_canonical_observations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 ("Unselected","MIGRATION",1,"x","v1",1,None,"UNKNOWN","OBSERVED","NOT_OBSERVED","x"))
    conn.commit(); conn.close()
    first = extract_creator_historical_outcomes(path, policies=POLICIES)
    second = extract_creator_historical_outcomes(path, policies=POLICIES)
    assert first == second
    assert "Unselected" not in first.selected_mints


def test_exact_schema_allowlist_rejects_extra_columns(tmp_path):
    path = tmp_path / "bad.db"; _database(path, extra_column=True)
    with pytest.raises(CreatorHistoricalOutcomeExtractorError, match="SCHEMA_COLUMN_MISMATCH"):
        extract_creator_historical_outcomes(path, policies=POLICIES)


def test_invalid_cohort_and_policy_bounds_fail_closed(tmp_path):
    path = tmp_path / "frozen.db"; _database(path)
    with pytest.raises(CreatorHistoricalOutcomeExtractorError, match="INVALID_POLICY_COUNT"):
        extract_creator_historical_outcomes(path, policies=())
    conn = sqlite3.connect(path); conn.execute("UPDATE cohort_mints SET position=9 WHERE mint='MintB'"); conn.commit(); conn.close()
    with pytest.raises(CreatorHistoricalOutcomeExtractorError, match="INVALID_COHORT"):
        extract_creator_historical_outcomes(path, policies=POLICIES)


def test_active_query_deadline_fails_closed(tmp_path):
    path = tmp_path / "frozen.db"; _database(path)
    ticks = iter([0.0, 2.0])
    with pytest.raises(CreatorHistoricalOutcomeExtractorError, match="QUERY_TIMEOUT"):
        extract_creator_historical_outcomes(
            path, policies=POLICIES, max_query_seconds=1.0, clock=lambda: next(ticks, 2.0)
        )


def test_ambiguous_identity_and_unqualified_fallback_are_excluded(tmp_path):
    path = tmp_path / "frozen.db"; _database(path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO creator_identity_facts VALUES (?,?,?,?,?,?)",
                 ("MintA","Other","PF_WS_CREATOR_VERIFIED","identity","v1","other"))
    conn.execute("UPDATE creator_identity_facts SET resolution_method='EARLIEST_TX_CREATOR' WHERE mint='MintB'")
    conn.commit(); conn.close()
    result = extract_creator_historical_outcomes(path, policies=POLICIES)
    assert result.qualified_mints == ()
    assert result.excluded_mints["MintA"] == "MISSING_OR_AMBIGUOUS_CREATOR_IDENTITY"
    assert result.excluded_mints["MintB"].startswith("UNQUALIFIED_INPUT:")
