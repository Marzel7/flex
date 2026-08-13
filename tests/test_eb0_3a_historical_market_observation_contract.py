import json
from pathlib import Path

import pytest

from src.evidence.contracts.historical_market_observation import (
    AUTHORITY_CLASS,
    HistoricalMarketObservationError,
    project_historical_market_observations,
    projection_digest,
)


FIXTURE = Path(__file__).parent / "fixtures" / "eb0_3a_historical_market_observations.json"


def _records():
    return json.loads(FIXTURE.read_text())["records"]


def test_projection_is_deterministic_idempotent_and_decimal_normalized():
    records = _records()
    first = project_historical_market_observations(records)
    replay = project_historical_market_observations(list(reversed(records)) + records[:1])
    assert first == replay
    assert projection_digest(first) == projection_digest(replay)
    observed = next(item for item in first if item.quality_state == "OBSERVED")
    assert observed.open_value == "0.001"
    assert observed.volume_value == "2500"


def test_provider_evidence_is_always_supplemental_and_mint_bound():
    facts = project_historical_market_observations(_records())
    assert all(item.authority_class == AUTHORITY_CLASS for item in facts)
    assert all(item.mint for item in facts)
    promoted = dict(_records()[0], authority_class="CANONICAL_CHAIN_BIRTH")
    with pytest.raises(HistoricalMarketObservationError, match="AUTHORITY_PROMOTION_REJECTED"):
        project_historical_market_observations([promoted])


def test_versioned_conflicts_remain_separate_facts():
    conflicts = [item for item in project_historical_market_observations(_records()) if item.conflict_group_id]
    assert len(conflicts) == 2
    assert {item.provider for item in conflicts} == {"frozen_provider_a", "frozen_provider_b"}
    assert len({item.observation_id for item in conflicts}) == 2


def test_request_accounting_and_endpoint_provenance_change_identity():
    record = _records()[0]
    changed = dict(record, physical_request_sequence=2)
    first = project_historical_market_observations([record])[0]
    second = project_historical_market_observations([changed])[0]
    assert first.provenance_digest != second.provenance_digest
    assert first.observation_id != second.observation_id


def test_earliest_observation_limitations_are_explicit_and_bounded():
    facts = project_historical_market_observations(_records())
    assert {item.earliest_observation_semantics for item in facts} == {
        "NOT_ASSERTED", "PAGE_EARLIEST_NOT_HISTORY", "PROVIDER_WINDOW_EARLIEST"
    }
    invalid = dict(_records()[0], earliest_observation_semantics="CHAIN_EARLIEST")
    with pytest.raises(HistoricalMarketObservationError, match="UNKNOWN_EARLIEST_SEMANTICS"):
        project_historical_market_observations([invalid])


@pytest.mark.parametrize("field,value", [
    ("high_value", "0.0008"),
    ("low_value", "0.0013"),
    ("volume_value", "-1"),
    ("interval_end_utc_ns", 1000000000),
])
def test_invalid_market_ranges_and_interval_timing_fail_closed(field, value):
    record = dict(_records()[0], **{field: value})
    with pytest.raises(HistoricalMarketObservationError):
        project_historical_market_observations([record])


def test_conflict_and_completeness_semantics_fail_closed():
    missing_group = dict(_records()[1], conflict_group_id=None)
    with pytest.raises(HistoricalMarketObservationError, match="CONFLICT_GROUP_REQUIRED"):
        project_historical_market_observations([missing_group])
    invalid_complete = dict(_records()[0], completeness_state="FULL_HISTORY_COMPLETE")
    with pytest.raises(HistoricalMarketObservationError, match="UNKNOWN_COMPLETENESS_STATE"):
        project_historical_market_observations([invalid_complete])
