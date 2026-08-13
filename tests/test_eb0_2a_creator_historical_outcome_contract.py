import json
from pathlib import Path

import pytest

from src.evidence.contracts.creator_historical_outcome import (
    CreatorHistoricalOutcomeContractError,
    project_creator_historical_outcomes,
    projection_digest,
)


FIXTURE = Path(__file__).parent / "fixtures" / "eb0_2a_creator_historical_outcome.json"


def _records():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["records"]


def test_projection_is_deterministic_idempotent_and_creator_identity_is_explicit():
    records = _records()
    forward = project_creator_historical_outcomes(records)
    replay = project_creator_historical_outcomes(list(reversed(records)) + records[:1])
    assert forward == replay
    assert projection_digest(forward) == projection_digest(replay)
    assert {fact.creator for fact in forward} == {"CreatorA", "CreatorB"}


def test_complete_horizons_are_denominator_eligible_but_missing_is_not():
    facts = project_creator_historical_outcomes(_records())
    by_mint = {fact.mint: fact for fact in facts}
    assert by_mint["MintA"].denominator_eligible is True
    assert by_mint["MintB"].denominator_eligible is True
    assert by_mint["MintC"].denominator_eligible is False
    assert by_mint["MintC"].outcome_state == "UNKNOWN"
    assert by_mint["MintC"].threshold_value == "100000"


def test_censored_evidence_cannot_become_a_negative_outcome():
    record = _records()[1]
    record.update(observed_through_utc_ns=2200, completeness_state="PARTIAL")
    with pytest.raises(CreatorHistoricalOutcomeContractError, match="CENSORED_NEGATIVE_FORBIDDEN"):
        project_creator_historical_outcomes([record])


def test_future_or_post_horizon_outcome_is_rejected():
    record = _records()[0]
    record["outcome_event_time_utc_ns"] = 1700
    with pytest.raises(CreatorHistoricalOutcomeContractError, match="TRUE_OUTCOME_NOT_PROVEN_IN_HORIZON"):
        project_creator_historical_outcomes([record])

    record = _records()[0]
    record.update(outcome_event_time_utc_ns=1400, observed_through_utc_ns=1350)
    with pytest.raises(CreatorHistoricalOutcomeContractError, match="FUTURE_LEAKAGE"):
        project_creator_historical_outcomes([record])


def test_complete_unknown_and_not_observed_positive_fail_closed():
    record = _records()[2]
    record.update(observed_through_utc_ns=3600, completeness_state="COMPLETE")
    with pytest.raises(CreatorHistoricalOutcomeContractError, match="COMPLETE_OUTCOME_CANNOT_BE_UNKNOWN"):
        project_creator_historical_outcomes([record])

    record = _records()[2]
    record.update(outcome_state="OBSERVED_TRUE", outcome_event_time_utc_ns=3100)
    with pytest.raises(CreatorHistoricalOutcomeContractError, match="NOT_OBSERVED_MUST_BE_UNKNOWN"):
        project_creator_historical_outcomes([record])


def test_threshold_contract_is_outcome_specific():
    migration = _records()[0]
    migration["threshold_value"] = "1"
    with pytest.raises(CreatorHistoricalOutcomeContractError, match="UNUSED_THRESHOLD"):
        project_creator_historical_outcomes([migration])

    market = _records()[2]
    market["threshold_value"] = None
    with pytest.raises(CreatorHistoricalOutcomeContractError, match="MISSING_THRESHOLD"):
        project_creator_historical_outcomes([market])


def test_source_lineage_and_conflicts_remain_distinct_facts():
    first = _records()[0]
    second = dict(first, source="second_frozen_source", quality_state="CONFLICTING")
    facts = project_creator_historical_outcomes([first, second])
    assert len(facts) == 2
    assert len({fact.provenance_digest for fact in facts}) == 2
    assert len({fact.fact_id for fact in facts}) == 2
