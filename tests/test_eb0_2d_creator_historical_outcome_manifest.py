from dataclasses import replace

import pytest

from src.evidence.contracts.creator_historical_outcome import (
    project_creator_historical_outcomes,
)
from src.evidence.contracts.creator_historical_outcome_manifest import (
    CreatorHistoricalOutcomeManifestError,
    build_creator_historical_outcome_manifest,
    verify_creator_historical_outcome_manifest,
)


def _fact(**changes):
    record = {
        "creator": "CreatorA",
        "mint": "MintA",
        "cohort_event_time_utc_ns": 1000,
        "outcome_kind": "MIGRATION_BY_HORIZON",
        "horizon_utc_ns": 500,
        "observed_through_utc_ns": 1600,
        "outcome_state": "OBSERVED_TRUE",
        "outcome_event_time_utc_ns": 1300,
        "threshold_value": None,
        "source": "EB0_2C_FROZEN_CANONICAL_ADAPTER",
        "source_version": "eb0.2c.v1",
        "quality_state": "VERIFIED",
        "completeness_state": "COMPLETE",
        "source_record_digest": "digest-a",
    }
    record.update(changes)
    return project_creator_historical_outcomes([record])[0]


def test_manifest_is_order_independent_and_replay_verifiable():
    facts = [
        _fact(),
        _fact(mint="MintB", outcome_state="OBSERVED_FALSE", outcome_event_time_utc_ns=None, source_record_digest="digest-b"),
        _fact(mint="MintC", outcome_state="UNKNOWN", outcome_event_time_utc_ns=None,
              observed_through_utc_ns=1200, completeness_state="NOT_OBSERVED",
              quality_state="UNKNOWN", source_record_digest="digest-c"),
    ]
    forward = build_creator_historical_outcome_manifest(facts)
    reverse = build_creator_historical_outcome_manifest(reversed(facts))
    assert forward == reverse
    assert verify_creator_historical_outcome_manifest(forward, facts) is True


def test_counts_expose_denominator_missingness_quality_and_completeness():
    facts = [
        _fact(),
        _fact(mint="MintB", outcome_state="OBSERVED_FALSE", outcome_event_time_utc_ns=None, source_record_digest="digest-b"),
        _fact(mint="MintC", outcome_state="UNKNOWN", outcome_event_time_utc_ns=None,
              observed_through_utc_ns=1200, completeness_state="NOT_OBSERVED",
              quality_state="UNKNOWN", source_record_digest="digest-c"),
        _fact(mint="MintD", source="second-source", quality_state="CONFLICTING", source_record_digest="digest-d"),
    ]
    manifest = build_creator_historical_outcome_manifest(facts)
    assert manifest.fact_count == 4
    assert manifest.eligible_denominator_count == 3
    assert manifest.unknown_count == 1
    assert manifest.not_observed_count == 1
    assert manifest.conflicting_fact_count == 1
    assert manifest.outcome_state_counts == {"OBSERVED_FALSE": 1, "OBSERVED_TRUE": 2, "UNKNOWN": 1}


def test_distinct_creators_mints_horizons_thresholds_and_sources_are_preserved():
    facts = [
        _fact(),
        _fact(creator="CreatorB", source_record_digest="creator-b"),
        _fact(mint="MintB", source_record_digest="mint-b"),
        _fact(horizon_utc_ns=700, observed_through_utc_ns=1800, source_record_digest="horizon"),
        _fact(source="second-source", source_record_digest="source"),
        _fact(outcome_kind="MARKET_CAP_AT_LEAST_BY_HORIZON", threshold_value="100000", source_record_digest="threshold"),
    ]
    manifest = build_creator_historical_outcome_manifest(facts)
    assert manifest.fact_count == 6
    assert len({item.fact_id for item in manifest.facts}) == 6


def test_empty_duplicate_and_noncanonical_inputs_fail_closed():
    with pytest.raises(CreatorHistoricalOutcomeManifestError, match="EMPTY_INPUT"):
        build_creator_historical_outcome_manifest([])
    fact = _fact()
    with pytest.raises(CreatorHistoricalOutcomeManifestError, match="DUPLICATE_INPUT"):
        build_creator_historical_outcome_manifest([fact, fact])
    tampered = replace(fact, denominator_eligible=False)
    with pytest.raises(CreatorHistoricalOutcomeManifestError, match="NONCANONICAL_FACT"):
        build_creator_historical_outcome_manifest([tampered])


def test_version_and_content_tampering_fail_replay():
    fact = _fact()
    manifest = build_creator_historical_outcome_manifest([fact])
    with pytest.raises(CreatorHistoricalOutcomeManifestError, match="SCHEMA_VERSION_MISMATCH"):
        verify_creator_historical_outcome_manifest(replace(manifest, schema_version="bad"), [fact])
    with pytest.raises(CreatorHistoricalOutcomeManifestError, match="CONTRACT_VERSION_MISMATCH"):
        verify_creator_historical_outcome_manifest(replace(manifest, contract_version="bad"), [fact])
    with pytest.raises(CreatorHistoricalOutcomeManifestError, match="ADAPTER_VERSION_MISMATCH"):
        verify_creator_historical_outcome_manifest(replace(manifest, adapter_version="bad"), [fact])
    with pytest.raises(CreatorHistoricalOutcomeManifestError, match="REPLAY_MISMATCH"):
        verify_creator_historical_outcome_manifest(replace(manifest, manifest_digest="bad"), [fact])
