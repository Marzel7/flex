import json

from flask import Flask

from src.ops import dev016_lifecycle_metrics as metrics
from src.ops.operator_routes import operator_bp


def _journal(root, name, row):
    path = root / "journals" / name
    path.mkdir(parents=True)
    (path / "journal.json").write_text(json.dumps(row))


def test_projection_marks_only_retained_window_peak_as_observed(tmp_path, monkeypatch):
    _journal(tmp_path, "one", {
        "mint": "mint-one", "state": "COMPLETED", "creation_timestamp": 100,
        "provider_provenance": "BIRDEYE_OHLCV_MCAP",
        "lifecycle_metrics": {
            "coverage_confidence": "PARTIAL_OBSERVED_WINDOW",
            "first_observed_timestamp": 120, "observed_maximum_mc_usd": 500,
            "observed_maximum_timestamp": 180, "last_observed_mc_usd": 250,
            "last_observed_timestamp": 220,
            "peak_to_subsequent_trough": {"observed_minimum_mc_usd": 200, "observed_minimum_timestamp": 200, "drawdown_percent": 60},
        },
    })
    monkeypatch.setattr(metrics, "COHORT_ROOT", tmp_path)

    result = metrics.lifecycle_metrics("mint-one")

    assert result["observed_candle_peak_market_cap"]["value"] == 500
    assert result["verified_historical_ath_market_cap"]["status"] == "INSUFFICIENT_EVIDENCE"
    assert result["seconds_launch_to_observed_candle_peak"]["value"] == 80
    assert result["seconds_first_observed_candle_to_peak"]["value"] == 60
    assert result["max_observed_drawdown_percent"]["value"] == 60
    assert result["opening_market_cap"]["status"] == "INSUFFICIENT_EVIDENCE"


def test_projection_joins_dev013_first_available_trade_without_relabelling_it(tmp_path, monkeypatch):
    _journal(tmp_path, "one", {
        "mint": "mint-one", "state": "COMPLETED", "creation_timestamp": 100,
        "lifecycle_metrics": {"observed_maximum_mc_usd": 300, "observed_maximum_timestamp": 200},
    })
    monkeypatch.setattr(metrics, "COHORT_ROOT", tmp_path)
    monkeypatch.setattr(metrics, "_dev013_first_available", lambda _: {
        "entry_mc_usd": 100, "entry_timestamp": 120, "first_available_signature": "sig", "first_available_venue": "pump_amm",
    })

    result = metrics.lifecycle_metrics("mint-one")

    assert result["opening_market_cap"]["value"] == 100
    assert result["opening_market_cap_semantic"] == "FIRST_AVAILABLE_TRADE_MARKET_CAP"
    assert result["launch_time_market_cap"]["status"] == "INSUFFICIENT_EVIDENCE"
    assert result["first_available_candle_market_cap"]["status"] == "INSUFFICIENT_EVIDENCE"
    assert result["gain_from_opening_market_cap_percent"]["value"] == 200
    assert result["opening_to_observed_candle_peak_multiple"]["value"] == 3


def test_projection_preserves_no_valid_candles(tmp_path, monkeypatch):
    _journal(tmp_path, "empty", {"mint": "mint-empty", "state": "NO_VALID_CANDLES", "creation_timestamp": 100})
    monkeypatch.setattr(metrics, "COHORT_ROOT", tmp_path)

    result = metrics.lifecycle_metrics("mint-empty")

    assert result["status"] == "NO_VALID_CANDLES"
    assert result["evidence_status"] == "NO_RETAINED_VALID_CANDLES"


def test_coverage_does_not_count_missing_opening_evidence(tmp_path, monkeypatch):
    _journal(tmp_path, "one", {"mint": "mint-one", "state": "COMPLETED", "lifecycle_metrics": {"last_observed_mc_usd": 10}})
    _journal(tmp_path, "two", {"mint": "mint-two", "state": "NO_VALID_CANDLES"})
    monkeypatch.setattr(metrics, "COHORT_ROOT", tmp_path)
    monkeypatch.setattr(metrics, "_assigned_mints", lambda: {"mint-one", "outside"})
    monkeypatch.setattr(metrics, "_dev013_first_available_mints", lambda: {"mint-one"})
    monkeypatch.setattr(metrics, "_opening_baseline", lambda mint: {"market_cap_usd": 10, "timestamp": 1, "baseline_type": "MARKET_FIRST_OBSERVED"} if mint == "mint-one" else None)

    result = metrics.coverage()

    assert result["dev014_cohort_count"] == 2
    assert result["tokens_with_lifecycle_evidence"] == 1
    assert result["opening_market_cap_coverage"] == 1
    assert result["latest_market_cap_coverage"] == 1
    assert result["tokens_outside_dev014_count"] == 1


def test_manifest_contains_only_unrecoverable_mints(tmp_path, monkeypatch):
    _journal(tmp_path, "one", {"mint": "recover", "state": "COMPLETED", "creation_timestamp": 10})
    _journal(tmp_path, "two", {"mint": "gap", "state": "NO_VALID_CANDLES", "creation_timestamp": 20})
    monkeypatch.setattr(metrics, "COHORT_ROOT", tmp_path)
    monkeypatch.setattr(metrics, "_opening_baseline", lambda mint: {"market_cap_usd": 1, "timestamp": 10, "baseline_type": "MARKET_FIRST_OBSERVED"} if mint == "recover" else None)

    assert metrics.acquisition_manifest() == [{
        "mint": "gap", "existing_evidence_status": "NO_VALID_CANDLES", "missing_baseline_type": "EARLIEST_DEFENSIBLE_MARKET_CAP", "earliest_known_relevant_timestamp": 20,
        "proposed_bounded_method": "DEV013_FIRST_AVAILABLE_TRADE", "estimated_provider_request_cost": {"helius_max": 1, "birdeye_max": 1},
    }]


def test_routes_expose_read_only_projection(tmp_path, monkeypatch):
    _journal(tmp_path, "one", {"mint": "mint-one", "state": "COMPLETED", "lifecycle_metrics": {}})
    monkeypatch.setattr(metrics, "COHORT_ROOT", tmp_path)
    monkeypatch.setattr(metrics, "_assigned_mints", lambda: {"mint-one"})
    app = Flask(__name__)
    app.register_blueprint(operator_bp)

    response = app.test_client().get("/api/operations/live-monitor/watchtower/mint-one/lifecycle-metrics")

    assert response.status_code == 200
    assert response.get_json()["provider_calls"] == 0
    assert response.get_json()["lifecycle"]["status"] == "OBSERVED_LIFECYCLE_AVAILABLE"

    gaps = app.test_client().get("/api/operations/live-monitor/watchtower/opening-mc-gaps")
    assert gaps.status_code == 200
    assert gaps.get_json()["opening_mc_gap_count"] == 1
    assert gaps.get_json()["provider_calls"] == 0
