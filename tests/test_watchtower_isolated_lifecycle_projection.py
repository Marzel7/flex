from src.ops.watchtower_price_fact_contract import summarize_observed_lifecycle


def test_observed_lifecycle_projection_requires_strict_post_peak_order_and_labels_gap():
    result = summarize_observed_lifecycle(
        creation_timestamp=0,
        requested_start=0,
        requested_end=3600,
        interval_seconds=900,
        candles=[
            {"timestamp": 0, "open": 10, "high": 20, "low": 10, "close": 18},
            {"timestamp": 900, "open": 18, "high": 30, "low": 15, "close": 16},
            {"timestamp": 1800, "open": 16, "high": 18, "low": 3, "close": 4},
            {"timestamp": 2700, "open": 4, "high": 12, "low": 4, "close": 8},
        ],
    )
    assert result["coverage_confidence"] == "CONTIGUOUS_REQUESTED_WINDOW"
    assert result["observed_maximum_timestamp"] == 900
    assert result["peak_to_subsequent_trough"]["observed_minimum_timestamp"] == 1800
    assert result["recovery_peak"]["observed_maximum_timestamp"] == 2700
    assert result["last_observed_mc_usd"] == 8.0


def test_observed_lifecycle_projection_never_fabricates_trough_or_complete_coverage():
    result = summarize_observed_lifecycle(
        creation_timestamp=100,
        requested_start=100,
        requested_end=2800,
        interval_seconds=900,
        candles=[{"timestamp": 100, "open": 10, "high": 20, "low": 10, "close": 15}, {"timestamp": 1900, "open": 15, "high": 16, "low": 12, "close": 13}],
    )
    assert result["coverage_confidence"] == "PARTIAL_OBSERVED_WINDOW"
    assert result["peak_to_subsequent_trough"]["observed_minimum_timestamp"] == 1900
    assert result["recovery_peak"] == "UNAVAILABLE_NO_LATER_CANDLE"
