import sqlite3
import json
from hashlib import sha256

import pytest

from src.evidence.contracts.creator_historical_outcome_bundle import (
    CreatorHistoricalOutcomeBundleError,
    verify_creator_historical_outcome_bundle,
    write_creator_historical_outcome_bundle,
)
from src.evidence.contracts.creator_historical_outcome_extractor import (
    OutcomePolicy,
    extract_creator_historical_outcomes,
)


POLICIES = (OutcomePolicy("CHAIN_BIRTH", "MIGRATION_BY_HORIZON", 500),)
REVISION = "1d91db846c7d4362d8086df6182b5fa5276d7955"


def _result(path):
    db = sqlite3.connect(path)
    db.executescript("""
      CREATE TABLE cohort_mints(position INTEGER,mint TEXT);
      CREATE TABLE eb0_1_canonical_observations(mint TEXT,event_kind TEXT,
        event_time_utc_ns INTEGER,source TEXT,source_version TEXT,
        observed_at_utc_ns INTEGER,price_or_market_cap_value TEXT,
        valuation_semantics TEXT,quality_state TEXT,completeness_state TEXT,
        source_record_digest TEXT);
      CREATE TABLE creator_identity_facts(mint TEXT,creator TEXT,resolution_method TEXT,
        source TEXT,source_version TEXT,source_record_digest TEXT);
      CREATE TABLE observation_window_facts(mint TEXT,observed_through_utc_ns INTEGER,
        full_horizon_complete INTEGER,source TEXT,source_version TEXT,source_record_digest TEXT);
      INSERT INTO cohort_mints VALUES(0,'MintA');
      INSERT INTO eb0_1_canonical_observations VALUES
        ('MintA','CHAIN_BIRTH',1000,'fixture','v1',1000,NULL,'UNKNOWN','OBSERVED','NOT_OBSERVED','birth'),
        ('MintA','MIGRATION',1300,'fixture','v1',1300,NULL,'UNKNOWN','OBSERVED','NOT_OBSERVED','migration');
      INSERT INTO creator_identity_facts VALUES
        ('MintA','CreatorA','PF_WS_CREATOR_VERIFIED','fixture','v1','identity');
      INSERT INTO observation_window_facts VALUES
        ('MintA',1600,1,'fixture','v1','window');
    """)
    db.commit(); db.close()
    return extract_creator_historical_outcomes(path, policies=POLICIES)


def _empty_result(path):
    db = sqlite3.connect(path)
    db.executescript("""
      CREATE TABLE cohort_mints(position INTEGER,mint TEXT);
      CREATE TABLE eb0_1_canonical_observations(mint TEXT,event_kind TEXT,
        event_time_utc_ns INTEGER,source TEXT,source_version TEXT,
        observed_at_utc_ns INTEGER,price_or_market_cap_value TEXT,
        valuation_semantics TEXT,quality_state TEXT,completeness_state TEXT,
        source_record_digest TEXT);
      CREATE TABLE creator_identity_facts(mint TEXT,creator TEXT,resolution_method TEXT,
        source TEXT,source_version TEXT,source_record_digest TEXT);
      CREATE TABLE observation_window_facts(mint TEXT,observed_through_utc_ns INTEGER,
        full_horizon_complete INTEGER,source TEXT,source_version TEXT,source_record_digest TEXT);
      INSERT INTO cohort_mints VALUES(0,'MintWithoutEvidence');
    """)
    db.commit(); db.close()
    return extract_creator_historical_outcomes(path, policies=POLICIES)


def _rehash_bundle(output):
    canonical = lambda value: json.dumps(
        value, sort_keys=True, separators=(",", ":")
    ).encode() + b"\n"
    hashes = json.loads((output / "hashes.json").read_text())
    for name in hashes["files"]:
        hashes["files"][name] = sha256((output / name).read_bytes()).hexdigest()
    hashes["bundle_digest"] = sha256(canonical(hashes["files"])).hexdigest()
    (output / "hashes.json").write_bytes(canonical(hashes))


def test_bundle_is_canonical_complete_and_replayable(tmp_path):
    result = _result(tmp_path / "fixture.db")
    output = tmp_path / "bundle"
    bundle = write_creator_historical_outcome_bundle(
        result, output, run_id="fixture-run", engineering_revision=REVISION, policies=POLICIES
    )
    assert verify_creator_historical_outcome_bundle(output) == bundle
    assert {item.name for item in output.iterdir()} == {
        "run.json", "accounting.json", "manifests.json", "corpora.json", "hashes.json"
    }


def test_fully_accounted_empty_result_is_replayable(tmp_path):
    result = _empty_result(tmp_path / "empty.db")
    assert result.qualified_mints == ()
    output = tmp_path / "empty-bundle"
    bundle = write_creator_historical_outcome_bundle(
        result, output, run_id="empty-run", engineering_revision=REVISION, policies=POLICIES
    )
    assert verify_creator_historical_outcome_bundle(output) == bundle


def test_empty_result_with_inconsistent_accounting_fails_closed(tmp_path):
    result = _empty_result(tmp_path / "empty.db")
    output = tmp_path / "empty-bundle"
    write_creator_historical_outcome_bundle(
        result, output, run_id="empty-run", engineering_revision=REVISION, policies=POLICIES
    )
    accounting = json.loads((output / "accounting.json").read_text())
    accounting["qualified_mints"] = ["MintWithoutEvidence"]
    canonical = json.dumps(accounting, sort_keys=True, separators=(",", ":")) + "\n"
    (output / "accounting.json").write_text(canonical)
    _rehash_bundle(output)
    with pytest.raises(CreatorHistoricalOutcomeBundleError, match="ACCOUNTING_MISMATCH"):
        verify_creator_historical_outcome_bundle(output)


def test_same_inputs_produce_identical_bundle_bytes(tmp_path):
    result = _result(tmp_path / "fixture.db")
    first, second = tmp_path / "first", tmp_path / "second"
    write_creator_historical_outcome_bundle(result, first, run_id="stable", engineering_revision=REVISION, policies=POLICIES)
    write_creator_historical_outcome_bundle(result, second, run_id="stable", engineering_revision=REVISION, policies=POLICIES)
    assert {p.name: p.read_bytes() for p in first.iterdir()} == {p.name: p.read_bytes() for p in second.iterdir()}


def test_nonempty_output_and_overwrite_are_rejected(tmp_path):
    result = _result(tmp_path / "fixture.db")
    output = tmp_path / "bundle"; output.mkdir(); (output / "foreign").write_text("x")
    with pytest.raises(CreatorHistoricalOutcomeBundleError, match="OUTPUT_NOT_EMPTY"):
        write_creator_historical_outcome_bundle(result, output, run_id="run", engineering_revision=REVISION, policies=POLICIES)


def test_missing_extra_and_altered_files_fail_verification(tmp_path):
    result = _result(tmp_path / "fixture.db")
    output = tmp_path / "bundle"
    write_creator_historical_outcome_bundle(result, output, run_id="run", engineering_revision=REVISION, policies=POLICIES)
    (output / "accounting.json").write_text("{}\n")
    with pytest.raises(CreatorHistoricalOutcomeBundleError, match="FILE_DIGEST_MISMATCH"):
        verify_creator_historical_outcome_bundle(output)
    (output / "extra").write_text("x")
    with pytest.raises(CreatorHistoricalOutcomeBundleError, match="FILE_SET_MISMATCH"):
        verify_creator_historical_outcome_bundle(output)


def test_policy_revision_and_run_validation_fail_before_output(tmp_path):
    result = _result(tmp_path / "fixture.db")
    with pytest.raises(CreatorHistoricalOutcomeBundleError, match="POLICY_OR_RESULT_MISMATCH"):
        write_creator_historical_outcome_bundle(
            result, tmp_path / "one", run_id="run", engineering_revision=REVISION,
            policies=(OutcomePolicy("CHAIN_BIRTH", "MIGRATION_BY_HORIZON", 700),),
        )
    with pytest.raises(CreatorHistoricalOutcomeBundleError, match="INVALID_ENGINEERING_REVISION"):
        write_creator_historical_outcome_bundle(result, tmp_path / "two", run_id="run", engineering_revision="not-git", policies=POLICIES)
    with pytest.raises(CreatorHistoricalOutcomeBundleError, match="INVALID_RUN_ID"):
        write_creator_historical_outcome_bundle(result, tmp_path / "three", run_id="../bad", engineering_revision=REVISION, policies=POLICIES)


def test_reencoded_noncanonical_json_fails_even_with_recomputed_hashes(tmp_path):
    result = _result(tmp_path / "fixture.db")
    output = tmp_path / "bundle"
    write_creator_historical_outcome_bundle(
        result, output, run_id="run", engineering_revision=REVISION, policies=POLICIES
    )
    run = json.loads((output / "run.json").read_text())
    (output / "run.json").write_text(json.dumps(run, indent=2) + "\n")
    hashes = json.loads((output / "hashes.json").read_text())
    hashes["files"]["run.json"] = sha256((output / "run.json").read_bytes()).hexdigest()
    canonical = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    hashes["bundle_digest"] = sha256(canonical(hashes["files"])).hexdigest()
    (output / "hashes.json").write_bytes(canonical(hashes))
    with pytest.raises(CreatorHistoricalOutcomeBundleError, match="NONCANONICAL_JSON"):
        verify_creator_historical_outcome_bundle(output)
