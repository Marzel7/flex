import json
from pathlib import Path
import sqlite3

import pytest

from src.evidence.contracts.birth_valuation_census import (
    BirthValuationCensusError,
    _timed,
    extract_birth_valuation_census,
)


MINT = "MintCensus111111111111111111111111111111"


def _platform_receive(mint=MINT):
    return {"mint": mint, "receive_utc_ns": 120_000_000_000, "source": "fixture",
            "source_schema_version": "1", "source_record_digest": f"receive-{mint}"}


def _migration_receive(mint=MINT):
    return {"mint": mint, "receive_utc_ns": 200_000_000_000,
            "signature": f"migration-{mint}", "source": "fixture",
            "source_schema_version": "1", "source_record_digest": f"migration-digest-{mint}"}


def _database(path: Path, *, members: int = 1) -> None:
    db = sqlite3.connect(path)
    db.executescript("""
    CREATE TABLE token_analysis(
      mint TEXT, migrated_at INTEGER, first_observed_mc TEXT, first_observed_price TEXT,
      first_observed_at INTEGER, first_observed_source TEXT, first_observed_confidence TEXT);
    CREATE TABLE normalized_evidence_records(
      fact_family TEXT, payload_json TEXT, raw_artifact_digest TEXT, acquired_at INTEGER,
      source_id TEXT, source_version TEXT, verification_state TEXT);
    CREATE TABLE token_price_snapshots(
      snapshot_id INTEGER PRIMARY KEY, mint TEXT, price_usd REAL, market_cap REAL,
      source TEXT, captured_at INTEGER, created_at INTEGER);
    CREATE TABLE eb0_platform_receive_evidence(
      mint TEXT, receive_utc_ns INTEGER, source TEXT, source_schema_version TEXT,
      source_record_digest TEXT);
    CREATE TABLE eb0_migration_receive_evidence(
      mint TEXT, receive_utc_ns INTEGER, signature TEXT, source TEXT,
      source_schema_version TEXT, source_record_digest TEXT);
    """)
    for ordinal in range(members):
        mint = MINT if ordinal == 0 else f"Mint{ordinal:04d}"
        db.execute("INSERT INTO token_analysis VALUES(?,?,?,?,?,?,?)",
                   (mint, 200 + ordinal, "12000", "0.00012", 150, "local-fixture", "HIGH"))
        db.execute("INSERT INTO token_price_snapshots VALUES(?,?,?,?,?,?,?)",
                   (ordinal + 1, mint, 0.0002, 20000, "snapshot-fixture", 160, 161))
        payload = {"mint": mint, "creation_signature": f"sig-{ordinal}",
                   "creation_timestamp": 100, "creation_slot": 10,
                   "program_id": "pump", "source_platform": "pumpfun"}
        db.execute("INSERT INTO normalized_evidence_records VALUES(?,?,?,?,?,?,?)",
                   ("LaunchFact", json.dumps(payload), f"launch-{ordinal}", 110,
                    "fixture", "1", "VERIFIED"))
        db.execute("INSERT INTO eb0_platform_receive_evidence VALUES(?,?,?,?,?)",
                   (mint, 120_000_000_000, "fixture", "1", f"receive-{ordinal}"))
        db.execute("INSERT INTO eb0_migration_receive_evidence VALUES(?,?,?,?,?,?)",
                   (mint, (200 + ordinal) * 1_000_000_000, f"migration-{ordinal}",
                    "fixture", "1", f"migration-digest-{ordinal}"))
    db.commit()
    db.close()


def test_query_only_extraction_is_deterministic_and_preserves_four_kinds(tmp_path):
    path = tmp_path / "frozen.sqlite"
    _database(path)
    before = path.read_bytes()
    kwargs = {"high_water_migrated_at": 250,
              "platform_receive_records": [_platform_receive()],
              "migration_receive_records": [_migration_receive()]}
    first = extract_birth_valuation_census(path, **kwargs)
    second = extract_birth_valuation_census(path, **kwargs)
    assert first == second
    assert path.read_bytes() == before
    assert first.selected_mints == (MINT,)
    assert first.corpora[0].manifest.event_counts == {
        "CHAIN_BIRTH": 1, "MARKET_FIRST_OBSERVED": 2,
        "MIGRATION": 1, "PLATFORM_FIRST_SEEN": 1,
    }
    assert first.excluded_observation_count == 2


def test_high_water_excludes_later_rows(tmp_path):
    path = tmp_path / "frozen.sqlite"
    _database(path, members=2)
    result = extract_birth_valuation_census(path, high_water_migrated_at=200)
    assert result.selected_mints == (MINT,)


def test_split_primary_and_evidence_sources_are_supported(tmp_path):
    primary = tmp_path / "primary.sqlite"
    evidence = tmp_path / "evidence.sqlite"
    _database(primary)
    source = sqlite3.connect(primary)
    row = source.execute("SELECT * FROM normalized_evidence_records").fetchone()
    source.close()
    db = sqlite3.connect(evidence)
    db.execute("CREATE TABLE normalized_evidence_records(fact_family TEXT,payload_json TEXT,"
               "raw_artifact_digest TEXT,acquired_at INTEGER,source_id TEXT,source_version TEXT,"
               "verification_state TEXT)")
    db.execute("INSERT INTO normalized_evidence_records VALUES(?,?,?,?,?,?,?)", row)
    db.commit()
    db.close()

    result = extract_birth_valuation_census(
        primary, evidence_source_path=evidence, high_water_migrated_at=250
    )
    assert result.corpora[0].manifest.event_counts == {
        "CHAIN_BIRTH": 1, "MARKET_FIRST_OBSERVED": 2,
    }
    assert result.missing_event_kind_counts == {
        "CHAIN_BIRTH": 0, "PLATFORM_FIRST_SEEN": 1,
        "MIGRATION": 1, "MARKET_FIRST_OBSERVED": 0,
    }


def test_missing_all_qualified_evidence_is_accounted_without_timestamp_substitution(tmp_path):
    path = tmp_path / "frozen.sqlite"
    _database(path)
    db = sqlite3.connect(path)
    db.execute("UPDATE token_analysis SET first_observed_at=NULL,first_observed_mc=NULL,first_observed_price=NULL")
    db.execute("DELETE FROM token_price_snapshots")
    db.execute("DELETE FROM normalized_evidence_records")
    db.commit()
    db.close()
    result = extract_birth_valuation_census(path, high_water_migrated_at=250)
    assert result.corpora == ()
    assert result.mints_without_canonical_evidence == (MINT,)
    assert result.missing_event_kind_counts == {kind: 1 for kind in (
        "CHAIN_BIRTH", "PLATFORM_FIRST_SEEN", "MIGRATION", "MARKET_FIRST_OBSERVED")}


def test_noncanonical_limit_and_timeout_bound_fail_closed(tmp_path):
    path = tmp_path / "frozen.sqlite"
    _database(path)
    with pytest.raises(BirthValuationCensusError, match="MINT_LIMIT_MUST_BE_5000"):
        extract_birth_valuation_census(path, high_water_migrated_at=250, mint_limit=4_999)
    with pytest.raises(BirthValuationCensusError, match="INVALID_QUERY_BOUND"):
        extract_birth_valuation_census(path, high_water_migrated_at=250, max_query_seconds=31)


def test_more_than_5000_members_selects_latest_5000_and_accounts_for_remainder(tmp_path):
    path = tmp_path / "frozen.sqlite"
    _database(path, members=5_001)
    result = extract_birth_valuation_census(path, high_water_migrated_at=10_000)
    assert result.eligible_mint_count == 5_001
    assert len(result.selected_mints) == 5_000
    assert result.excluded_by_cohort_bound_count == 1
    assert MINT not in result.selected_mints
    assert result.selected_mints[:2] == ("Mint5000", "Mint4999")


def test_duplicate_mint_uses_latest_eligible_row_once(tmp_path):
    path = tmp_path / "frozen.sqlite"
    _database(path)
    db = sqlite3.connect(path)
    db.execute("INSERT INTO token_analysis VALUES(?,?,?,?,?,?,?)",
               (MINT, 240, "24000", "0.00024", 140, "latest-fixture", "HIGH"))
    db.commit()
    db.close()
    result = extract_birth_valuation_census(path, high_water_migrated_at=250)
    assert result.eligible_mint_count == 1
    assert result.selected_mints == (MINT,)
    selected_market = [item for item in result.corpora[0].manifest.observations
                       if item.event_kind == "MARKET_FIRST_OBSERVED"]
    assert any(item.price_or_market_cap_value == "24000" for item in selected_market)


def test_launch_facts_are_filtered_to_selected_mints(tmp_path):
    path = tmp_path / "frozen.sqlite"
    _database(path)
    db = sqlite3.connect(path)
    for ordinal in range(100):
        payload = {"mint": f"Unselected{ordinal}", "creation_signature": f"u-{ordinal}",
                   "creation_timestamp": 100, "creation_slot": 10,
                   "program_id": "pump", "source_platform": "pumpfun"}
        db.execute("INSERT INTO normalized_evidence_records VALUES(?,?,?,?,?,?,?)",
                   ("LaunchFact", json.dumps(payload), f"unselected-{ordinal:03d}", 110,
                    "fixture", "1", "VERIFIED"))
    db.commit()
    db.close()
    result = extract_birth_valuation_census(path, high_water_migrated_at=250)
    assert result.corpora[0].manifest.event_counts["CHAIN_BIRTH"] == 1


def test_more_than_two_launch_facts_for_selected_mint_fails_closed(tmp_path):
    path = tmp_path / "frozen.sqlite"
    _database(path)
    db = sqlite3.connect(path)
    for ordinal in (2, 3):
        payload = {"mint": MINT, "creation_signature": f"sig-{ordinal}",
                   "creation_timestamp": 100 + ordinal, "creation_slot": 10 + ordinal,
                   "program_id": "pump", "source_platform": "pumpfun"}
        db.execute("INSERT INTO normalized_evidence_records VALUES(?,?,?,?,?,?,?)",
                   ("LaunchFact", json.dumps(payload), f"launch-{ordinal}", 120,
                    "fixture", "1", "VERIFIED"))
    db.commit()
    db.close()
    with pytest.raises(BirthValuationCensusError, match="LAUNCH_FACT_OVERFLOW"):
        extract_birth_valuation_census(path, high_water_migrated_at=250)


def test_malformed_launch_payload_fails_closed(tmp_path):
    path = tmp_path / "frozen.sqlite"
    _database(path)
    db = sqlite3.connect(path)
    db.execute("INSERT INTO normalized_evidence_records VALUES(?,?,?,?,?,?,?)",
               ("LaunchFact", "{malformed", "bad-launch", 120, "fixture", "1", "VERIFIED"))
    db.commit()
    db.close()
    with pytest.raises(BirthValuationCensusError, match="INVALID_LAUNCH_PAYLOAD"):
        extract_birth_valuation_census(path, high_water_migrated_at=250)


@pytest.mark.parametrize(
    ("market_cap", "price", "expected"),
    [(12000.0, 0.00012, {"12000", "0.00012"}),
     (1.25e6, 2.5e-7, {"1250000", "0.00000025"})],
)
def test_token_analysis_real_affinity_values_are_canonical(tmp_path, market_cap, price, expected):
    path = tmp_path / "real.sqlite"
    _database(path)
    db = sqlite3.connect(path)
    db.execute("UPDATE token_analysis SET first_observed_mc=?,first_observed_price=?",
               (market_cap, price))
    db.commit()
    db.close()
    result = extract_birth_valuation_census(path, high_water_migrated_at=250)
    values = {item.price_or_market_cap_value for item in result.corpora[0].manifest.observations
              if item.event_kind == "MARKET_FIRST_OBSERVED"
              and item.event_time_utc_ns == 150_000_000_000}
    assert values == expected


@pytest.mark.parametrize("invalid", [0.0, -1.0, float("inf")])
def test_token_analysis_invalid_real_value_fails_closed(tmp_path, invalid):
    path = tmp_path / "invalid-real.sqlite"
    _database(path)
    db = sqlite3.connect(path)
    db.execute("UPDATE token_analysis SET first_observed_mc=?", (invalid,))
    db.commit()
    db.close()
    with pytest.raises(BirthValuationCensusError, match="INVALID_MARKET_VALUE"):
        extract_birth_valuation_census(path, high_water_migrated_at=250)


def test_missing_or_malformed_schema_fails_closed(tmp_path):
    missing = tmp_path / "missing.sqlite"
    sqlite3.connect(missing).close()
    with pytest.raises(BirthValuationCensusError, match="MISSING_TABLE_TOKEN_ANALYSIS"):
        extract_birth_valuation_census(missing, high_water_migrated_at=1)

    malformed = tmp_path / "malformed.sqlite"
    _database(malformed)
    db = sqlite3.connect(malformed)
    db.execute("ALTER TABLE token_analysis RENAME TO wrong_token_analysis")
    db.commit()
    db.close()
    with pytest.raises(BirthValuationCensusError, match="MISSING_TABLE_TOKEN_ANALYSIS"):
        extract_birth_valuation_census(malformed, high_water_migrated_at=250)


def test_read_only_uri_rejects_source_mutation(tmp_path):
    path = tmp_path / "frozen.sqlite"
    _database(path)
    uri = f"file:{path.resolve()}?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    db.execute("PRAGMA query_only=ON")
    with pytest.raises(sqlite3.OperationalError):
        db.execute("UPDATE token_analysis SET migrated_at=0")
    db.close()


def test_progress_handler_interrupts_at_deadline_and_is_cleared():
    db = sqlite3.connect(":memory:")
    ticks = iter([0.0, 0.5, 1.0, 1.1])
    with pytest.raises(BirthValuationCensusError, match="QUERY_TIMEOUT"):
        _timed(
            db,
            "WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<100000) "
            "SELECT sum(x) FROM n",
            (), clock=lambda: next(ticks, 1.1), max_query_seconds=1.0,
        )
    assert db.execute("SELECT 1").fetchone()[0] == 1
    db.close()


def test_progress_handler_is_cleared_after_unrelated_sqlite_error():
    db = sqlite3.connect(":memory:")
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        _timed(db, "SELECT * FROM absent_table", (), clock=lambda: 0.0,
               max_query_seconds=1.0)
    assert db.execute("SELECT 1").fetchone()[0] == 1
    db.close()
