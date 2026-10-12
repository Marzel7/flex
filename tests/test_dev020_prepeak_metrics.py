import pytest

from src.ops.dev020_prepeak_metrics import (
    INSUFFICIENT_EVIDENCE,
    OBSERVED_CONTIGUOUS,
    OBSERVED_SPARSE,
    calculate_prepeak_observed_metrics,
)
from src.ops.watchtower_historical_executor import HistoricalExecutor


def candle(timestamp, opening, high=None, low=None, close=None):
    return {"timestamp": timestamp, "open": opening, "high": high if high is not None else opening,
            "low": low if low is not None else opening, "close": close if close is not None else opening}


def calculate(candles, **overrides):
    values = {"opening_market_cap": 100, "opening_timestamp": 0, "observed_peak_market_cap": 400,
              "observed_peak_timestamp": 600, "requested_window_start": 0, "requested_window_end": 600}
    values.update(overrides)
    return calculate_prepeak_observed_metrics(candles, **values)


def test_contiguous_metrics_use_only_earlier_interval_highs_and_conservative_close_recovery():
    result = calculate([
        candle(0, 100, high=110, low=95, close=105),
        candle(60, 105, high=160, low=130, close=150),
        candle(120, 150, high=155, low=90, close=100),
        candle(180, 100, high=159, low=120, close=158),
        candle(240, 158, high=165, low=150, close=158),
        candle(300, 160, high=170, low=160, close=165),
        candle(360, 165, high=175, low=160, close=165),
        candle(420, 165, high=180, low=170, close=175),
        candle(480, 175, high=190, low=170, close=180),
        candle(540, 180, high=200, low=175, close=190),
    ])

    assert result["observation_coverage"]["qualification_status"] == OBSERVED_CONTIGUOUS
    assert result["lowest_observed_market_cap"] == 90
    assert result["below_opening_market_cap"] is True
    assert result["maximum_observed_prepeak_drawdown_percent"] == pytest.approx(43.75)
    assert result["drawdown_preceding_high_timestamp"] == 60
    assert result["drawdown_subsequent_low_timestamp"] == 120
    assert result["recovery_time_seconds"] == 180
    assert result["opening_to_observed_peak_multiple"] == 4


def test_sparse_observations_are_valid_but_never_claim_complete_coverage():
    result = calculate([
        candle(0, 100, high=125, low=100, close=120),
        candle(60, 120, high=130, low=110, close=125),
        candle(600 - 60, 100, high=110, low=80, close=85),
    ])

    coverage = result["observation_coverage"]
    assert coverage["qualification_status"] == OBSERVED_SPARSE
    assert coverage["qualifying_candle_count"] == 3
    assert coverage["missing_interval_count"] == 7
    assert coverage["largest_unobserved_gap_seconds"] == 420
    assert coverage["coverage_is_complete"] is False
    assert result["maximum_observed_prepeak_drawdown_percent"] == pytest.approx((130 - 80) * 100 / 130)


def test_same_interval_high_and_low_never_create_a_drawdown_or_recovery_ordering_claim():
    result = calculate([candle(0, 100, high=200, low=50, close=100)], requested_window_end=60)

    assert result["observation_coverage"]["qualification_status"] == INSUFFICIENT_EVIDENCE
    assert result["maximum_observed_prepeak_drawdown_percent"] is None
    assert result["recovery_time_seconds"] is None


def test_uranus_historical_proof_is_not_replayed_without_retained_candles():
    # The provider sequence was intentionally not retained. This compact fixture
    # validates the documented reducer semantics, not a fabricated replay.
    result = calculate([
        candle(0, 130710.4999, high=159232.94878182322, low=120000, close=150000),
        candle(60, 150000, high=155000, low=91491.36766641765, close=100000),
        candle(540, 100000, high=170000, low=140000, close=160000),
    ], opening_market_cap=130710.4999, observed_peak_market_cap=788884.3545,
       observed_peak_timestamp=2580, requested_window_end=600)

    assert result["observation_coverage"]["qualification_status"] == OBSERVED_SPARSE
    assert result["maximum_observed_prepeak_drawdown_percent"] == pytest.approx(42.542439635545094)
    assert result["recovery_time_seconds"] == 480


def test_existing_executor_exposes_observed_reducer_without_provider_or_raw_retention(tmp_path):
    executor = HistoricalExecutor(str(tmp_path / "journal.db"), binding=lambda _: pytest.fail("provider called"))
    result = executor.derive_prepeak_metrics(
        [candle(0, 100), candle(60, 110, high=130, low=90, close=95)],
        opening_market_cap=100, opening_timestamp=0, observed_peak_market_cap=200,
        observed_peak_timestamp=120, requested_window_start=0, requested_window_end=120,
    )

    assert result["observation_coverage"]["qualification_status"] == OBSERVED_CONTIGUOUS
    assert executor.calls == 0
