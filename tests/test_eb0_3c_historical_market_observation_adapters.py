import copy
import json
from pathlib import Path

import pytest

from src.evidence.contracts.historical_market_observation_adapters import (
    HistoricalMarketObservationAdapterError,
    adapt_market_kline_from_transport,
    adapt_market_kline_projection,
    canonical_digest,
)


FIXTURE = Path(__file__).parent / "fixtures" / "eb0_3c_market_kline_projection.json"


def _projection():
    value = json.loads(FIXTURE.read_text())
    value["response_digest"] = canonical_digest(value["candles"])
    return value


class FakeTransport:
    def __init__(self, projection):
        self.projection = projection
        self.calls = 0

    def load_projection(self):
        self.calls += 1
        return self.projection


def test_fake_transport_is_called_once_and_maps_bounded_ohlcv():
    transport = FakeTransport(_projection())
    result = adapt_market_kline_from_transport(transport)
    assert transport.calls == 1
    assert len(result.observations) == 2
    assert result.request_cost_units == 2
    assert {item.interval for item in result.observations} == {"1m"}
    assert {item.market_cap_value for item in result.observations} == {None}
    assert {item.earliest_observation_semantics for item in result.observations} == {
        "PAGE_EARLIEST_NOT_HISTORY"
    }
    assert {item.open_value for item in result.observations} == {"0.001", "0.0012"}


def test_projection_replay_is_deterministic():
    first = adapt_market_kline_projection(_projection())
    second = adapt_market_kline_projection(_projection())
    assert first == second


@pytest.mark.parametrize("mutation,match", [
    (lambda value: value.update({"api_key": "secret"}), "SCHEMA_DRIFT"),
    (lambda value: value.update({"interval": "5m"}), "INTERVAL_NOT_1M"),
    (lambda value: value.update({"completeness_state": "COMPLETE_INTERVAL"}), "COMPLETENESS_PROMOTION"),
    (lambda value: value.update({"earliest_observation_semantics": "PROVIDER_WINDOW_EARLIEST"}), "EARLIEST_SEMANTICS_PROMOTION"),
    (lambda value: value.update({"response_digest": "0" * 64}), "RESPONSE_DIGEST_MISMATCH"),
])
def test_schema_authority_and_digest_changes_fail_closed(mutation, match):
    value = _projection()
    mutation(value)
    with pytest.raises(HistoricalMarketObservationAdapterError, match=match):
        adapt_market_kline_projection(value)


def test_credential_market_cap_pagination_and_policy_fields_are_rejected_recursively():
    for field in ("authorization", "market_cap", "cursor", "ranking_score", "creator_policy"):
        value = _projection()
        value["candles"][0][field] = "forbidden"
        value["response_digest"] = canonical_digest(value["candles"])
        with pytest.raises(HistoricalMarketObservationAdapterError, match="CANDLE_SCHEMA_DRIFT|FORBIDDEN_FIELD"):
            adapt_market_kline_projection(value)


def test_invalid_ohlc_and_unbounded_rows_fail_closed():
    invalid = _projection()
    invalid["candles"][0]["high"] = "0.0001"
    invalid["response_digest"] = canonical_digest(invalid["candles"])
    with pytest.raises(Exception, match="INVALID_OHLC_RANGE"):
        adapt_market_kline_projection(invalid)

    unbounded = _projection()
    unbounded["candles"] = [copy.deepcopy(unbounded["candles"][0]) for _ in range(1001)]
    unbounded["response_digest"] = canonical_digest(unbounded["candles"])
    with pytest.raises(HistoricalMarketObservationAdapterError, match="ROW_CEILING_EXCEEDED"):
        adapt_market_kline_projection(unbounded)


def test_missing_accounting_and_conflict_semantics_fail_closed():
    missing = _projection()
    del missing["request_cost_units"]
    with pytest.raises(HistoricalMarketObservationAdapterError, match="SCHEMA_DRIFT"):
        adapt_market_kline_projection(missing)

    conflict = _projection()
    conflict["quality_state"] = "CONFLICTING"
    with pytest.raises(HistoricalMarketObservationAdapterError, match="CONFLICT_GROUP_REQUIRED"):
        adapt_market_kline_projection(conflict)
