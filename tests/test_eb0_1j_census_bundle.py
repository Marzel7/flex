import json
from pathlib import Path
import sqlite3

import pytest

from src.evidence.contracts.birth_valuation_census import extract_birth_valuation_census
from src.evidence.contracts.birth_valuation_census_bundle import (
    CensusBundleError,
    verify_census_bundle,
    write_census_bundle,
)


MINT = "MintBundle1111111111111111111111111111111"
SCHEMA_DIGESTS = {"primary": "a" * 64, "evidence": "b" * 64}


def _result(path: Path):
    db = sqlite3.connect(path)
    db.executescript("""
    CREATE TABLE token_analysis(mint TEXT,migrated_at INTEGER,first_observed_mc REAL,
      first_observed_price REAL,first_observed_at INTEGER,first_observed_source TEXT,
      first_observed_confidence REAL);
    CREATE TABLE token_price_snapshots(snapshot_id INTEGER,mint TEXT,price_usd REAL,
      market_cap REAL,source TEXT,captured_at INTEGER,created_at INTEGER);
    CREATE TABLE normalized_evidence_records(fact_family TEXT,payload_json TEXT,
      raw_artifact_digest TEXT,acquired_at INTEGER,source_id TEXT,source_version TEXT,
      verification_state TEXT);
    """)
    db.execute("INSERT INTO token_analysis VALUES(?,?,?,?,?,?,?)",
               (MINT, 200, 12000.0, 0.00012, 150, "fixture", 0.95))
    payload = {"mint": MINT, "creation_signature": "sig", "creation_timestamp": 100,
               "creation_slot": 10, "program_id": "pump", "source_platform": "pumpfun"}
    db.execute("INSERT INTO normalized_evidence_records VALUES(?,?,?,?,?,?,?)",
               ("LaunchFact", json.dumps(payload), "launch-digest", 110,
                "fixture", "1", "VERIFIED"))
    db.commit()
    db.close()
    return extract_birth_valuation_census(path, high_water_migrated_at=250)


def test_bundle_is_complete_credential_free_and_replayable(tmp_path):
    result = _result(tmp_path / "fixture.sqlite")
    output = tmp_path / "bundle"
    bundle = write_census_bundle(
        result, output, run_id="fixture-run-1", source_schema_fingerprints=SCHEMA_DIGESTS
    )
    assert verify_census_bundle(output) == bundle
    assert {item.name for item in output.iterdir()} == {
        "run.json", "aggregate.json", "corpora.json", "hashes.json"}
    joined = b"".join(item.read_bytes() for item in output.iterdir())
    assert b"api_key" not in joined.lower()
    assert b"provider_payload" not in joined.lower()


def test_same_inputs_and_run_id_produce_identical_files(tmp_path):
    result = _result(tmp_path / "fixture.sqlite")
    first = tmp_path / "first"
    second = tmp_path / "second"
    write_census_bundle(result, first, run_id="stable", source_schema_fingerprints=SCHEMA_DIGESTS)
    write_census_bundle(result, second, run_id="stable", source_schema_fingerprints=SCHEMA_DIGESTS)
    assert {p.name: p.read_bytes() for p in first.iterdir()} == {
        p.name: p.read_bytes() for p in second.iterdir()}


def test_nonempty_output_and_overwrite_are_rejected(tmp_path):
    result = _result(tmp_path / "fixture.sqlite")
    output = tmp_path / "bundle"
    output.mkdir()
    (output / "foreign.txt").write_text("occupied", encoding="utf-8")
    with pytest.raises(CensusBundleError, match="OUTPUT_NOT_EMPTY"):
        write_census_bundle(result, output, run_id="run", source_schema_fingerprints=SCHEMA_DIGESTS)


def test_tamper_and_extra_file_fail_replay(tmp_path):
    result = _result(tmp_path / "fixture.sqlite")
    output = tmp_path / "bundle"
    write_census_bundle(result, output, run_id="run", source_schema_fingerprints=SCHEMA_DIGESTS)
    (output / "aggregate.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(CensusBundleError, match="FILE_DIGEST_MISMATCH"):
        verify_census_bundle(output)
    (output / "extra").write_text("x", encoding="utf-8")
    with pytest.raises(CensusBundleError, match="FILE_SET_MISMATCH"):
        verify_census_bundle(output)


def test_invalid_run_or_schema_fingerprint_fails_before_output(tmp_path):
    result = _result(tmp_path / "fixture.sqlite")
    with pytest.raises(CensusBundleError, match="INVALID_RUN_ID"):
        write_census_bundle(result, tmp_path / "one", run_id="../escape",
                            source_schema_fingerprints=SCHEMA_DIGESTS)
    with pytest.raises(CensusBundleError, match="INVALID_SCHEMA_FINGERPRINT"):
        write_census_bundle(result, tmp_path / "two", run_id="safe",
                            source_schema_fingerprints={"primary": "not-a-digest"})
