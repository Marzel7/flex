"""Regression guards for the retired generic funding UI slice.

These checks deliberately inspect route declarations without importing the
production Flask application, so they cannot create provider/RPC traffic.
"""

import ast
from pathlib import Path


RETIRED_ROUTES = {
    "/networks",
    "/creator-network/<network_name>",
    "/api/funding-networks",
    "/api/funding-networks-list",
    "/api/funding-network-details/<int:network_id>",
    "/api/build-funding-networks",
}

RETIRED_PAGE_ROUTES = {
    "/live-launches", "/pumpfun", "/coordinated-funder-analysis/<creator_address>",
    "/db-serializer", "/usage", "/coordinated-funders", "/clusters",
    "/funder-details/<funder_address>", "/network-intelligence", "/ecosystems",
    "/risk-scoring", "/predictions", "/approval-queue", "/network-approval",
    "/top-funding-hubs", "/funding-hub/<hub_address>", "/profitable-creators",
    "/profitable-networks", "/profitable-funders", "/ecosystem", "/ecosystem-creators",
    "/ecosystem-networks", "/ecosystem-funders", "/ecosystem-clusters",
    "/creator-analysis", "/creators", "/network-monitoring",
    "/network-monitoring/alerts.csv", "/settings", "/rpc-savings-dashboard",
    "/funding-queue", "/transfer-graph", "/network-diagram", "/network-diagram/htx",
    "/network-diagram/okx", "/network-diagram/watchtower",
    "/network-diagram/coinbase-cluster", "/watchtower", "/watchtower/operator/<address>",
    "/watchtower/intelligence", "/watchtower/operations", "/command-center",
    "/watchtower/operators", "/watchtower/candidate/<mint>", "/watchtower/dashboard",
    "/watchtower/interceptor", "/webhook-monitor", "/webhook-metrics", "/coordinators",
    "/test-prices", "/spike-analysis", "/funder-intelligence", "/funding",
}


def _declared_routes(path: str) -> set[str]:
    tree = ast.parse(Path(path).read_text())
    routes = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "route":
            continue
        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            routes.add(node.args[0].value)
    return routes


def test_retired_generic_funding_routes_are_not_declared():
    routes = _declared_routes("src/core/main.py")
    assert (RETIRED_ROUTES | RETIRED_PAGE_ROUTES).isdisjoint(routes)


def test_retired_generic_read_model_and_template_are_removed():
    assert not Path("src/ops/generic_funding_network_read_model.py").exists()
    assert not Path("templates/networks_dashboard.html").exists()


def test_retired_routes_are_not_linked_or_polled_by_the_sidebar():
    sidebar = Path("templates/partials/sidebar.html").read_text()
    for route in (
        "/live-launches", "/approval-queue", "/funding-queue",
        "/network-approval", "/network-diagram", "/creator-analysis",
        "/funder-intelligence", "/watchtower/operators",
        "/watchtower/interceptor",
    ):
        assert f'href="{route}"' not in sidebar
    assert "/api/funding-queue" not in sidebar


def test_retained_operation_and_health_routes_remain_declared():
    routes = _declared_routes("src/ops/operator_routes.py")
    assert "/intelligence/operators" in routes
    assert "/intelligence/operator/<operator_id>" in routes
    assert "/intelligence/potential-operations" in routes
    assert "/intelligence/potential-operations/<candidate_id>" in routes
    assert "/system-health" in _declared_routes("src/core/main.py")
    assert "/ops-os" in _declared_routes("src/ops/shell_routes.py")
