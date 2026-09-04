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

    assert "wbStatus === 'STOPPED' || wbStatus === 'STALLED'" in group
    assert "/api/ops-v2/intelligence-snapshots/health" not in group
    assert "Intelligence Snapshots" not in group


def test_system_health_route_is_retained() -> None:
    source = (ROOT / "src/core/main.py").read_text()
    assert "@app.route('/system-health')" in source
    assert "system_health_dashboard.html" in source


def test_db_health_omits_unused_expired_rpc_cache_scan() -> None:
    source = (ROOT / "src/core/main.py").read_text()
    start = source.index("def api_db_health():")
    end = source.index("@app.route('/api/debug/db-connections')", start)
    block = source[start:end]
    assert "rpc_cache_expired" not in block
    assert "rpc_cache_rows" in block
    assert "cached_at + ttl_seconds <=" not in block


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


def test_system_health_progressively_renders_core_before_diagnostics() -> None:
    source = (ROOT / "templates/system_health_dashboard.html").read_text()
    start = source.index("function updateDashboard()")
    end = source.index("document.getElementById('refreshBtn')", start)
    bootstrap = source[start:end]

    assert "Promise.all" not in bootstrap
    assert "fetch('/api/health/full')" in bootstrap
    assert "renderIntelligenceGroup(fullHealth, walkbackHealth, !walkbackSettled)" in bootstrap
    assert "fetch('/api/ops-v2/walkback-candidate-health')" in bootstrap
    assert "renderIntelligenceGroup(fullHealth, walkbackHealth, false)" in bootstrap
    assert "systemHealthRefreshGeneration" in bootstrap
    for endpoint in (
        "/api/db-health",
        "/api/listener-recovery-status",
        "/api/internal/funding-queue-stats",
    ):
        assert endpoint in bootstrap


def test_system_health_walkback_has_explicit_pending_state_and_runtime_is_deferred() -> None:
    source = (ROOT / "templates/system_health_dashboard.html").read_text()
    assert "function renderWalkbackCandidatePending()" in source
    assert "Loading current Walkback health…" in source
    assert "runtimeBudgetPollingStarted" in source
    assert "setInterval(loadSHRuntimeBudget, 30000)" in source
    assert source.count("fetch('/api/health/full')") == 1
    assert source.count("fetch('/api/ops-v2/walkback-candidate-health')") == 1


def test_operator_registry_polling_is_visible_tab_only_and_minute_cadence() -> None:
    source = (ROOT / "templates/operators_index.html").read_text()
    assert "window.setInterval(refreshAll, 60000)" in source
    assert "if (!document.hidden)" in source
    assert "visibilitychange" in source
    assert "},15000)" not in source


def test_operator_detail_secondary_fetches_are_post_render_deferred() -> None:
    source = (ROOT / "templates/operator_intelligence.html").read_text()
    start = source.index("var operatorDetailNativeFetch")
    end = source.index("/* OPS-UI-P2:", start)
    gate = source[start:end]

    assert "window.requestIdleCallback" in source
    assert "window.setTimeout(releaseOperatorDetailPostRenderFetches, 0)" in source
    assert "window.fetch = operatorDetailNativeFetch" in source
    assert "operatorDetailDeferredFetches.push" in gate
    for deferred_path in (
        "/api/ops/investigation/",
        "/api/ops/inbox/operator-resolution",
        "/observations",
        "/behaviour",
        "/behaviour/change",
        "/assessment",
        "/forecast",
        "/similarity",
    ):
        assert deferred_path in gate

    # Command-centre enrichment stays eager and is excluded from the gate.
    assert "/api/ops/emerging-operators/" in source
    assert "/api/ops/treasury-review" in source
    assert "emerging-operators" not in gate
    assert "treasury-review" not in gate
