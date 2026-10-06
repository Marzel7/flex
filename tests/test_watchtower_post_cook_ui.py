from src.ops.operator_routes import _watchtower_display_fields


def _row(**extra):
    return {"operation_id": "watchtower", "monitor_state": "PRICE_MONITOR_COMPLETE_COLLAPSED", "entry_status": "QUALIFIED", "final_proven_ath_mc": 300.0, **extra}


def test_sparse_terminal_is_visible_history_with_observed_only_semantics():
    row = _watchtower_display_fields(_row(final_ath_resolution="15m:SPARSE:OBSERVED_ONLY"))
    assert row["history_visible"] is True
    assert row["coverage_class"] == "SPARSE"
    assert row["terminal_metric_exactness"] == "OBSERVED_ONLY"
    assert row["lifecycle_class"] == "TERMINAL_SPARSE_OBSERVED"


def test_cook_sparse_regression_fixture_preserves_authoritative_values():
    row = _watchtower_display_fields(_row(
        entry_mc_usd=113464.45625213276, latest_mc_usd=2186.75765258587,
        final_proven_ath_mc=115379.256102627, final_ath_multiple=1.01687576809287,
        drawdown_percent=98.104722004239, entry_timestamp=1791269958,
        final_ath_bucket_start=1791270000,
        entry_method="MIGRATION_SECOND_MC_FALLBACK",
        final_ath_resolution="15m:SPARSE:OBSERVED_ONLY",
    ))
    assert (row["entry_mc_usd"], row["final_proven_ath_mc"], row["final_ath_multiple"], row["latest_mc_usd"]) == (113464.45625213276, 115379.256102627, 1.01687576809287, 2186.75765258587)
    assert row["drawdown_percent"] == 98.104722004239
    assert row["final_ath_bucket_start"] - row["entry_timestamp"] == 42
    assert row["coverage_class"] == "SPARSE" and row["terminal_metric_exactness"] == "OBSERVED_ONLY"


def test_complete_terminal_keeps_complete_semantics():
    row = _watchtower_display_fields(_row())
    assert row["history_visible"] is True
    assert row["coverage_class"] == "COMPLETE"
    assert row["terminal_metric_exactness"] == "COMPLETE"


def test_waiting_token_is_not_history():
    row = _watchtower_display_fields(_row(monitor_state="WAITING_FOR_ENTRY_REFERENCE", entry_status="WAITING_FOR_ENTRY_REFERENCE", final_proven_ath_mc=None))
    assert row["history_visible"] is False
    assert row["lifecycle_class"] == "WAITING_FOR_ENTRY_REFERENCE"


def test_monitor_template_uses_observed_labels_and_no_synthetic_zeroes():
    text = open("templates/operations_live_monitor.html").read()
    assert "Observed Peak" in text and "Last Observed MC" in text
    assert "Migration-second fallback" in text and "+1 second" in text
    assert "'—'" in text
