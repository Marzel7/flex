"""Provider-free regression coverage for post-reduction health semantics."""

from pathlib import Path

from src.ops.mission_control_capabilities import _compute_creator_funding


ROOT = Path(__file__).resolve().parents[1]


def test_creator_funding_stopped_with_work_remains_critical() -> None:
    result = _compute_creator_funding({
        "intelligence": {
            "funding_worker_status": "STOPPED",
            "funding_queue_pending": 1,
            "funding_queue_oldest_pending_age_secs": 10,
        }
    })
    assert result["status"] == "CRITICAL"


def test_health_dashboard_keeps_walkback_failure_and_drops_snapshot_fetch() -> None:
    source = (ROOT / "templates/system_health_dashboard.html").read_text()
    start = source.index("async function renderIntelligenceGroup")
    end = source.index("function renderWalkbackCandidateHealth", start)
    group = source[start:end]

    assert "/api/ops-v2/walkback-candidate-health" in group
    assert "wbStatus === 'STOPPED' || wbStatus === 'STALLED'" in group
    assert "/api/ops-v2/intelligence-snapshots/health" not in group
    assert "Intelligence Snapshots" not in group


def test_system_health_route_is_retained() -> None:
    source = (ROOT / "src/core/main.py").read_text()
    assert "@app.route('/system-health')" in source
    assert "system_health_dashboard.html" in source


def test_current_health_block_has_no_retired_watch_pipeline_probe() -> None:
    source = (ROOT / "src/core/main.py").read_text()
    start = source.index("def api_health_full():")
    end = source.index("@app.route('/api/db-health')", start)
    block = source[start:end]
    assert "worker_name='watch-pipeline'" not in block


def test_system_health_omits_retired_price_diagnostics_and_fetch() -> None:
    source = (ROOT / "templates/system_health_dashboard.html").read_text()
    live_markup = source[:source.index("{% endblock %}")]
    for retired_label in (
        "grp-diag", "Pool Pricing", "Price Source Health", "Circuit Breaker",
        "Rolling Window (5-min)", "Snapshot Cleanup (24h)",
        "Local Flask Process Diagnostics", "First Snapshot Health",
    ):
        assert retired_label not in live_markup
    assert "${API_BASE}/price/health" not in source


def test_walkback_keeps_current_recovery_summary_not_history_table() -> None:
    source = (ROOT / "templates/system_health_dashboard.html").read_text()
    start = source.index("function renderWalkbackCandidateHealth")
    end = source.index("function _fmtDuration", start)
    block = source[start:end]
    assert "Latest recovery:" in block
    assert "self_kill_last_hour" in block
    assert "self_kill_last_day" in block
    assert "Recovery History (last 5)" not in block
    assert "Discovery Diagnostics" not in block
    assert "walkback-candidate-health" in source


def test_system_health_omits_legacy_watchtower_telemetry_tiers() -> None:
    source = (ROOT / "templates/system_health_dashboard.html").read_text()
    start = source.index("async function renderIntelligenceGroup")
    end = source.index("function renderWalkbackCandidateHealth", start)
    group = source[start:end]
    for retired_ui in (
        "Tier 1 — Creator Prediction", "Tier 2 — Infrastructure Telemetry",
        "tier1Monitor", "tier2Monitor", "WATCHTOWER intelligence tiers",
        "Treasury Review →", "Discovery Diagnostics →",
    ):
        assert retired_ui not in group
    for health_only_fetch in (
        "/api/watchtower/webhook-status", "/api/watchtower/hit-activity",
        "/api/watchtower/relay-telemetry", "/api/watchtower/relay-counterparties",
    ):
        assert health_only_fetch not in group
    assert "renderWalkbackCandidateHealth(wb)" in group
