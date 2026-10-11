from scripts.audit_watchtower_lifecycle_mcap_accuracy import chronological_summary


def test_chronological_summary_rejects_reverse_and_same_candle_ordering():
    result = chronological_summary([
        {"observed_peak_timestamp": 10, "observed_minimum_timestamp": 20, "range_drawdown_percent": 95},
        {"observed_peak_timestamp": 20, "observed_minimum_timestamp": 10, "range_drawdown_percent": 95},
        {"observed_peak_timestamp": 10, "observed_minimum_timestamp": 10, "range_drawdown_percent": 95},
        {},
    ])
    assert result["chronological_collapse_eligible"] == 1
    assert result["severe_90pct_or_more"] == {
        "PEAK_THEN_TROUGH": 1,
        "TROUGH_BEFORE_PEAK": 1,
        "INTRA_CANDLE_ORDER_UNKNOWN": 1,
        "UNAVAILABLE": 0,
    }
