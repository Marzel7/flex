import pytest

from src.evidence.contracts.birth_valuation_corpus import assemble_birth_valuation_corpora
from src.evidence.contracts.creator_historical_outcome_adapters import (
    CreatorHistoricalOutcomeAdapterError,
    adapt_creator_outcome,
    build_creator_identity_fact,
    build_observation_window_fact,
)


def _record(kind, time, value=None, semantics="UNKNOWN", completeness="NOT_OBSERVED"):
    return {
        "mint": "MintA",
        "event_kind": kind,
        "event_time_utc_ns": time,
        "source": "frozen",
        "source_version": "v1",
        "observed_at_utc_ns": time,
        "price_or_market_cap_value": value,
        "valuation_semantics": semantics,
        "quality_state": "OBSERVED",
        "completeness_state": completeness,
        "source_record_digest": f"digest-{kind}-{time}",
    }


def _inputs(*records, through=2000, complete=True, method="PF_WS_CREATOR_VERIFIED"):
    corpus = assemble_birth_valuation_corpora(records)[0]
    identity = build_creator_identity_fact(
        mint="MintA", creator="CreatorA", resolution_method=method,
        source="frozen_identity", source_version="v1", source_record_digest="creator-digest",
    )
    window = build_observation_window_fact(
        mint="MintA", observed_through_utc_ns=through, full_horizon_complete=complete,
        source="frozen_window", source_version="v1", source_record_digest="window-digest",
    )
    return corpus, identity, window


def test_proven_migration_positive_maps_inside_horizon():
    corpus, identity, window = _inputs(
        _record("CHAIN_BIRTH", 1000), _record("MIGRATION", 1300), through=1600
    )
    fact = adapt_creator_outcome(
        corpus=corpus, creator_identity=identity, observation_window=window,
        cohort_event_kind="CHAIN_BIRTH", outcome_kind="MIGRATION_BY_HORIZON",
        horizon_utc_ns=500,
    )[0]
    assert fact.outcome_state == "OBSERVED_TRUE"
    assert fact.outcome_event_time_utc_ns == 1300
    assert fact.denominator_eligible is True


def test_market_threshold_positive_uses_event_value_not_legacy_peak():
    corpus, identity, window = _inputs(
        _record("CHAIN_BIRTH", 1000),
        _record("MARKET_FIRST_OBSERVED", 1200, "150000", "MARKET_CAP_AT_EVENT", "COMPLETE"),
        through=1400, complete=False,
    )
    fact = adapt_creator_outcome(
        corpus=corpus, creator_identity=identity, observation_window=window,
        cohort_event_kind="CHAIN_BIRTH", outcome_kind="MARKET_CAP_AT_LEAST_BY_HORIZON",
        horizon_utc_ns=500, threshold_value="100000",
    )[0]
    assert fact.outcome_state == "OBSERVED_TRUE"
    assert fact.completeness_state == "PARTIAL"
    assert fact.denominator_eligible is False


def test_absence_is_unknown_without_explicit_complete_window():
    corpus, identity, window = _inputs(
        _record("CHAIN_BIRTH", 1000), through=1600, complete=False
    )
    fact = adapt_creator_outcome(
        corpus=corpus, creator_identity=identity, observation_window=window,
        cohort_event_kind="CHAIN_BIRTH", outcome_kind="MIGRATION_BY_HORIZON",
        horizon_utc_ns=500,
    )[0]
    assert fact.outcome_state == "UNKNOWN"
    assert fact.completeness_state == "NOT_OBSERVED"
    assert fact.denominator_eligible is False


def test_explicit_complete_window_permits_negative():
    corpus, identity, window = _inputs(_record("CHAIN_BIRTH", 1000), through=1600)
    fact = adapt_creator_outcome(
        corpus=corpus, creator_identity=identity, observation_window=window,
        cohort_event_kind="CHAIN_BIRTH", outcome_kind="MIGRATION_BY_HORIZON",
        horizon_utc_ns=500,
    )[0]
    assert fact.outcome_state == "OBSERVED_FALSE"
    assert fact.denominator_eligible is True


def test_ambiguous_creator_fallback_and_market_cohort_are_rejected():
    with pytest.raises(CreatorHistoricalOutcomeAdapterError, match="UNQUALIFIED_CREATOR_IDENTITY"):
        _inputs(_record("CHAIN_BIRTH", 1000), method="EARLIEST_TX_CREATOR")

    corpus, identity, window = _inputs(
        _record("MARKET_FIRST_OBSERVED", 1000, "1", "MARKET_CAP_AT_EVENT", "COMPLETE")
    )
    with pytest.raises(CreatorHistoricalOutcomeAdapterError, match="UNQUALIFIED_COHORT_EVENT_KIND"):
        adapt_creator_outcome(
            corpus=corpus, creator_identity=identity, observation_window=window,
            cohort_event_kind="MARKET_FIRST_OBSERVED", outcome_kind="MIGRATION_BY_HORIZON",
            horizon_utc_ns=500,
        )


def test_mint_mismatch_and_incomplete_cutoff_fail_closed():
    corpus, identity, window = _inputs(_record("CHAIN_BIRTH", 1000), through=1200)
    wrong_identity = build_creator_identity_fact(
        mint="OtherMint", creator="CreatorA", resolution_method="PF_WS_CREATOR_VERIFIED",
        source="frozen", source_version="v1", source_record_digest="x",
    )
    with pytest.raises(CreatorHistoricalOutcomeAdapterError, match="MINT_MISMATCH"):
        adapt_creator_outcome(
            corpus=corpus, creator_identity=wrong_identity, observation_window=window,
            cohort_event_kind="CHAIN_BIRTH", outcome_kind="MIGRATION_BY_HORIZON",
            horizon_utc_ns=500,
        )

    fact = adapt_creator_outcome(
        corpus=corpus, creator_identity=identity, observation_window=window,
        cohort_event_kind="CHAIN_BIRTH", outcome_kind="MIGRATION_BY_HORIZON",
        horizon_utc_ns=500,
    )[0]
    assert fact.outcome_state == "UNKNOWN"


def test_adapter_output_is_deterministic_and_provenance_bound():
    corpus, identity, window = _inputs(
        _record("CHAIN_BIRTH", 1000), _record("MIGRATION", 1300), through=1600
    )
    kwargs = dict(
        corpus=corpus, creator_identity=identity, observation_window=window,
        cohort_event_kind="CHAIN_BIRTH", outcome_kind="MIGRATION_BY_HORIZON",
        horizon_utc_ns=500,
    )
    assert adapt_creator_outcome(**kwargs) == adapt_creator_outcome(**kwargs)
    changed_window = build_observation_window_fact(
        mint="MintA", observed_through_utc_ns=1700, full_horizon_complete=True,
        source="frozen_window", source_version="v1", source_record_digest="changed",
    )
    assert adapt_creator_outcome(**{**kwargs, "observation_window": changed_window}) != adapt_creator_outcome(**kwargs)
