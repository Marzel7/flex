from dataclasses import replace

import pytest

from src.evidence.contracts.creator_historical_outcome import project_creator_historical_outcomes
from src.evidence.contracts.creator_historical_outcome_corpus import (
    CreatorHistoricalOutcomeCorpusError,
    assemble_creator_historical_outcome_corpora,
    verify_creator_historical_outcome_corpora,
)
from src.evidence.contracts.creator_historical_outcome_manifest import (
    build_creator_historical_outcome_manifest,
)


def _fact(*, creator="CreatorA", mint="MintA", state="OBSERVED_TRUE",
          completeness="COMPLETE", quality="VERIFIED", source="source-a",
          horizon=500, threshold=None, digest="digest-a"):
    return project_creator_historical_outcomes([{
        "creator": creator,
        "mint": mint,
        "cohort_event_time_utc_ns": 1000,
        "outcome_kind": "MARKET_CAP_AT_LEAST_BY_HORIZON" if threshold else "MIGRATION_BY_HORIZON",
        "horizon_utc_ns": horizon,
        "observed_through_utc_ns": 1000 + (horizon if completeness == "COMPLETE" else 100),
        "outcome_state": state,
        "outcome_event_time_utc_ns": 1050 if state == "OBSERVED_TRUE" else None,
        "threshold_value": threshold,
        "source": source,
        "source_version": "eb0.2c.v1",
        "quality_state": quality,
        "completeness_state": completeness,
        "source_record_digest": digest,
    }])[0]


def test_corpora_group_by_creator_and_are_input_order_independent():
    first = build_creator_historical_outcome_manifest([
        _fact(), _fact(creator="CreatorB", mint="MintB", digest="b")
    ])
    second = build_creator_historical_outcome_manifest([
        _fact(mint="MintC", state="UNKNOWN", completeness="NOT_OBSERVED",
              quality="UNKNOWN", digest="c")
    ])
    forward = assemble_creator_historical_outcome_corpora([first, second])
    reverse = assemble_creator_historical_outcome_corpora([second, first])
    assert forward == reverse
    assert [item.creator for item in forward] == ["CreatorA", "CreatorB"]
    assert verify_creator_historical_outcome_corpora(forward, [first, second]) is True


def test_preserves_distinct_mint_horizon_threshold_state_quality_and_source_facts():
    facts = [
        _fact(),
        _fact(mint="MintB", digest="mint"),
        _fact(horizon=700, digest="horizon"),
        _fact(threshold="100000", digest="threshold"),
        _fact(state="OBSERVED_FALSE", digest="negative"),
        _fact(quality="CONFLICTING", source="source-b", digest="conflict"),
    ]
    corpus = assemble_creator_historical_outcome_corpora([
        build_creator_historical_outcome_manifest(facts)
    ])[0]
    assert corpus.fact_count == 6
    assert corpus.mint_count == 2
    assert len({item.fact_id for item in corpus.facts}) == 6
    assert corpus.conflicting_fact_count == 1


def test_coverage_counts_are_counts_not_rates_or_scores():
    manifest = build_creator_historical_outcome_manifest([
        _fact(),
        _fact(mint="MintB", state="OBSERVED_FALSE", digest="negative"),
        _fact(mint="MintC", state="UNKNOWN", completeness="NOT_OBSERVED",
              quality="UNKNOWN", digest="unknown"),
    ])
    corpus = assemble_creator_historical_outcome_corpora([manifest])[0]
    assert corpus.eligible_denominator_count == 2
    assert corpus.unknown_count == 1
    assert corpus.not_observed_count == 1
    assert not any("rate" in key or "score" in key for key in corpus.to_dict())


def test_exact_fact_replay_across_manifests_is_deduplicated_with_lineage_preserved():
    fact = _fact()
    first = build_creator_historical_outcome_manifest([fact])
    second = build_creator_historical_outcome_manifest([fact, _fact(mint="MintB", digest="b")])
    corpus = assemble_creator_historical_outcome_corpora([first, second])[0]
    assert corpus.fact_count == 2
    assert corpus.source_manifest_digests == tuple(sorted({first.manifest_digest, second.manifest_digest}))


def test_empty_unverified_and_replay_tampering_fail_closed():
    with pytest.raises(CreatorHistoricalOutcomeCorpusError, match="EMPTY_INPUT"):
        assemble_creator_historical_outcome_corpora([])
    manifest = build_creator_historical_outcome_manifest([_fact()])
    with pytest.raises(CreatorHistoricalOutcomeCorpusError, match="UNVERIFIED_MANIFEST"):
        assemble_creator_historical_outcome_corpora([replace(manifest, manifest_digest="bad")])
    corpus = assemble_creator_historical_outcome_corpora([manifest])
    with pytest.raises(CreatorHistoricalOutcomeCorpusError, match="REPLAY_MISMATCH"):
        verify_creator_historical_outcome_corpora(
            [replace(corpus[0], corpus_digest="bad")], [manifest]
        )
