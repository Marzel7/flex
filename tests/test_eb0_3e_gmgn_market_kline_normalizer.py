import json
from dataclasses import replace
from pathlib import Path

import pytest

from src.evidence.contracts.gmgn_market_kline_normalizer import (
    GmgnMarketKlineNormalizerError,
    RequestMetadata,
    normalize_gmgn_market_kline,
    normalize_gmgn_market_kline_from_transport,
)


FIXTURE = Path(__file__).parent / "fixtures" / "eb0_3e_gmgn_market_kline_envelope.json"


def _envelope():
    return json.loads(FIXTURE.read_text())


def _metadata():
    return RequestMetadata(
        platform_mint="8TQAPEgP8jcWPeLTiQhFCFtPYwKhpjRvTngoPAmjpump",
        provider_version="gmgn-cli-1.5.6",
        endpoint_version="v1",
        interval="1m",
        request_from_ms=1785699480000,
        request_to_ms=1785703080000,
        observed_at_ms=1786645752000,
        request_run_id="frozen-eb0-3e",
        physical_request_sequence=1,
        request_cost_units=2,
        physical_requests_observed=1,
        retry=False,
        failover=False,
        pagination=False,
    )


class FakeTransport:
    def __init__(self, envelope):
        self.envelope = envelope
        self.calls = 0

    def load_envelope(self):
        self.calls += 1
        return self.envelope


def test_live_shape_normalizes_and_replays_through_eb0_3c():
    result = normalize_gmgn_market_kline(_envelope(), _metadata())
    assert len(result.adapter_result.observations) == 4
    assert result.projection["candles"][0]["time_ms"] == 1785699480000
    assert result.discarded_fields == ("source", "amount")
    assert all(item.market_cap_value is None for item in result.adapter_result.observations)
    assert all(item.completeness_state == "PARTIAL_INTERVAL" for item in result.adapter_result.observations)


def test_fake_transport_is_invoked_exactly_once_and_replay_is_deterministic():
    transport = FakeTransport(_envelope())
    first = normalize_gmgn_market_kline_from_transport(transport, _metadata())
    second = normalize_gmgn_market_kline(_envelope(), _metadata())
    assert transport.calls == 1
    assert first == second


@pytest.mark.parametrize("field", ["cursor", "api_key", "market_cap", "ranking_score"])
def test_envelope_and_candle_schema_drift_fail_closed(field):
    value = _envelope()
    value[field] = "forbidden"
    with pytest.raises(GmgnMarketKlineNormalizerError, match="ENVELOPE_SCHEMA_DRIFT"):
        normalize_gmgn_market_kline(value, _metadata())
    value = _envelope()
    value["list"][0][field] = "forbidden"
    with pytest.raises(GmgnMarketKlineNormalizerError, match="CANDLE_SCHEMA_DRIFT"):
        normalize_gmgn_market_kline(value, _metadata())


@pytest.mark.parametrize("changes,match", [
    ({"physical_requests_observed": 2}, "PHYSICAL_REQUEST_COUNT_MISMATCH"),
    ({"request_cost_units": 3}, "REQUEST_COST_MISMATCH"),
    ({"retry": True}, "REQUEST_SCOPE_EXPANSION"),
    ({"pagination": True}, "REQUEST_SCOPE_EXPANSION"),
    ({"interval": "5m"}, "INTERVAL_NOT_1M"),
])
def test_request_accounting_and_scope_expansion_fail_closed(changes, match):
    with pytest.raises(GmgnMarketKlineNormalizerError, match=match):
        normalize_gmgn_market_kline(_envelope(), replace(_metadata(), **changes))


def test_provider_only_fields_are_validated_then_not_retained():
    invalid_amount = _envelope()
    invalid_amount["list"][0]["amount"] = "not-a-number"
    with pytest.raises(GmgnMarketKlineNormalizerError, match="INVALID_AMOUNT"):
        normalize_gmgn_market_kline(invalid_amount, _metadata())
    result = normalize_gmgn_market_kline(_envelope(), _metadata())
    assert all("amount" not in row and "source" not in row for row in result.projection["candles"])


def test_invalid_ohlc_is_rejected_by_eb0_3c_backstop():
    value = _envelope()
    value["list"][0]["high"] = "0.0000001"
    with pytest.raises(Exception, match="INVALID_OHLC_RANGE"):
        normalize_gmgn_market_kline(value, _metadata())
