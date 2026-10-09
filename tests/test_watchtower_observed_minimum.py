import copy

import pytest

from src.ops.watchtower_observed_minimum import observed_minima


ENTRY = 121  # first wholly post-entry bucket is 180


def _candle(timestamp, low):
    return {"timestamp": timestamp, "low_mc_usd": low}


def _series(seconds=3600):
    return [_candle(timestamp, 200_000 - timestamp) for timestamp in range(180, ENTRY + seconds, 60) if timestamp + 60 <= ENTRY + seconds]


def _result(series, windows=(300, 900, 3600)):
    return observed_minima(entry_timestamp=ENTRY, entry_mc_usd=200_000, candles=series,
                            provider_provenance="BIRDEYE_V3_OHLCV_MCAP_USD_1M", windows=windows)["results"]


def test_complete_windows_are_observed_not_lifecycle_facts():
    results = _result(_series())
    assert results["300"]["minimum_status"] == "COMPLETE_OBSERVED_WINDOW"
    assert results["300"]["coverage_status"] == "COMPLETE_OBSERVED_WINDOW"
    assert results["300"]["missing_bucket_timestamps"] == []
    assert results["300"]["observed_drawdown_percent"] < 0


def test_internal_gap_is_partial_and_retained_exactly():
    series = [row for row in _series(900) if row["timestamp"] != 420]
    result = _result(series, (900,))["900"]
    assert result["minimum_status"] == "PARTIAL_OBSERVED_MINIMUM"
    assert result["coverage_status"] == "PROVIDER_MISSING_COVERAGE"
    assert result["missing_bucket_timestamps"] == [420]


def test_missing_terminal_tail_is_truncation_suspected():
    series = [row for row in _series(900) if row["timestamp"] < 720]
    result = _result(series, (900,))["900"]
    assert result["minimum_status"] == "PARTIAL_OBSERVED_MINIMUM"
    assert result["coverage_status"] == "TRUNCATION_SUSPECTED"
    assert result["missing_bucket_timestamps"] == [720, 780, 840, 900, 960]


def test_entry_straddling_and_window_straddling_candles_are_excluded():
    series = [_candle(120, 1), _candle(180, 100), _candle(240, 90), _candle(300, 80), _candle(360, 70), _candle(420, 1)]
    result = _result(series, (300,))["300"]
    assert result["observed_minimum_mc_usd"] == 70
    assert result["observed_minimum_timestamp"] == 360


def test_invalid_low_is_not_a_market_cap_or_no_trade_claim():
    series = [_candle(180, 100), _candle(240, "not-a-number"), _candle(300, 80), _candle(360, 70)]
    result = _result(series, (300,))["300"]
    assert result["minimum_status"] == "PARTIAL_OBSERVED_MINIMUM"
    assert result["coverage_status"] == "PROVIDER_MISSING_COVERAGE"
    assert result["invalid_bucket_timestamps"] == [240]


@pytest.mark.parametrize("series", [[_candle(180, 100), _candle(180, 90)], [_candle(240, 100), _candle(180, 90)]])
def test_duplicate_or_out_of_order_provider_series_fails_closed(series):
    with pytest.raises(ValueError, match="DUPLICATE_OR_OUT_OF_ORDER_CANDLES"):
        _result(series, (300,))


def test_windows_can_have_different_observed_minima():
    series = _series()
    series[0]["low_mc_usd"] = 90_000
    series[5]["low_mc_usd"] = 80_000
    series[-1]["low_mc_usd"] = 70_000
    results = _result(series)
    assert results["300"]["observed_minimum_mc_usd"] == 90_000
    assert results["900"]["observed_minimum_mc_usd"] == 80_000
    assert results["3600"]["observed_minimum_mc_usd"] == 70_000


def test_input_and_existing_fact_shape_are_not_mutated():
    candles = _series()
    before = copy.deepcopy(candles)
    historical_fact = {"entry_mc_usd": 200_000, "running_peak_mc_usd": 500_000, "final_proven_ath_mc": None}
    fact_before = copy.deepcopy(historical_fact)
    _result(candles)
    assert candles == before
    assert historical_fact == fact_before
