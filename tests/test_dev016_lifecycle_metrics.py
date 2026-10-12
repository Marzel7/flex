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

    result = metrics.coverage()

    assert result["dev014_cohort_count"] == 2
    assert result["tokens_with_lifecycle_evidence"] == 1
    assert result["opening_market_cap_coverage"] == 0
    assert result["latest_market_cap_coverage"] == 1
    assert result["tokens_outside_dev014_count"] == 1


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
