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
