from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SIDEBAR = (ROOT / "templates" / "partials" / "sidebar.html").read_text()
SHELL = (ROOT / "templates" / "ops_shell_index.html").read_text()


def test_shell_uses_lightweight_liveness_without_operational_health_dependency():
    assert "fetch('/healthz'" in SIDEBAR
    assert "/api/health/full" not in SIDEBAR
    assert "detailed health in System Health" in SIDEBAR


def test_shell_liveness_is_bounded_and_non_overlapping():
    assert "healthRequestInFlight" in SIDEBAR
    assert "AbortController" in SIDEBAR
    assert "}, 3000);" in SIDEBAR


def test_operations_panels_have_bounded_stale_safe_requests():
    assert "var PANEL_REQUEST_DEADLINE_MS = 3000;" in SHELL
    assert "panelRequestVersions" in SHELL
    assert "Stale response" in SHELL
    for panel, endpoint in (
        ("lifecycle", "/api/ops/lifecycle/platform"),
        ("inbox", "/api/ops/inbox/summary"),
        ("discovery-recent", "/api/discovery/recent?limit=6"),
        ("promotions", "/api/operators/promotions"),
        ("emerging-operators", "/api/ops/emerging-operators?limit=6"),
        ("convergence", "/api/discovery/convergence?behaviour_limit=1"),
        ("treasury-review", "/api/ops/treasury-review?status=PENDING_REVIEW&limit=1"),
        ("operator-summary", "/api/ops/operators/summary"),
    ):
        assert "fetchPanelJson('%s', '%s')" % (panel, endpoint) in SHELL


def test_panel_failures_have_explicit_non_loading_states():
    assert "Lifecycle UNAVAILABLE." in SHELL
    assert "Inbox unavailable." in SHELL
    assert "Discovery intelligence unavailable." in SHELL
    assert "textContent = 'UNKNOWN'" in SHELL
