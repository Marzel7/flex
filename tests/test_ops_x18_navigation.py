"""Sprint X18 navigation and information-architecture contracts."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SIDEBAR = (ROOT / "templates/partials/sidebar.html").read_text()
SPEC = (ROOT / "docs/audits/X18_NAVIGATION_INFORMATION_ARCHITECTURE.md").read_text()
WIREFRAME = (ROOT / "docs/audits/X18_WIREFRAMES.html").read_text()


def test_navigation_is_ordered_by_analyst_work():
    labels = [
        ">◆ Mission Control<", ">Investigate<", ">Operations<", ">Review<", ">Analysis<",
        ">Legacy Features ", ">Legacy WATCHTOWER ", ">System<",
    ]
    positions = [SIDEBAR.index(label) for label in labels]
    assert positions == sorted(positions)


def test_canonical_routes_are_primary_and_preserved():
    routes = [
        "/ops-os", "/discovery", "/intelligence/entity/", "/intelligence/operators",
        "/ops", "/watchtower/intelligence", "/ops-os/launcher-observatory",
        "/ops-os/buy-swarm-observatory", "/intelligence/inbox",
        "/intelligence/operator-promotions", "/intelligence/cross-operation",
        "/intelligence/knowledge",
    ]
    for route in routes:
        assert f'href="{route}"' in SIDEBAR


def test_legacy_pages_remain_reachable_and_are_marked():
    for route in (
        "/live-launches", "/approval-queue", "/funding-queue", "/network-approval",
        "/predictions", "/spike-analysis", "/network-diagram", "/token-intelligence",
        "/creator-analysis", "/funder-intelligence",
        "/watchtower/operators", "/watchtower/interceptor", "/ops/tokens",
        "/ops/detection-health", "/ops/discovery-assurance", "/ops/webhook-coverage",
    ):
        assert f'href="{route}"' in SIDEBAR
    assert SIDEBAR.count("legacy-tag") >= 20


def test_home_redirects_to_mission_control():
    main_source = (ROOT / "src/core/main.py").read_text()
    assert "return redirect('/ops-os')" in main_source


def test_contextual_entity_landing_redirects_to_discovery():
    from flask import Flask
    from src.intelligence.page_routes import register_intelligence_page_routes

    app = Flask(__name__, template_folder=str(ROOT / "templates"))
    register_intelligence_page_routes(app)
    response = app.test_client().get("/intelligence/entity/")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/discovery")


def test_spec_contains_all_required_deliverables():
    for section in (
        "New navigation hierarchy", "Analyst journeys", "Page ownership matrix",
        "Current page audit and disposition", "Duplication rationalisation",
        "Component reuse plan", "Legacy migration plan", "Wireframes",
    ):
        assert section in SPEC
    assert "No route is deleted in X18" in SPEC


def test_wireframes_cover_the_reasoning_journey_and_depth_levels():
    for label in (
        "Mission Control", "Discovery", "Entity Intelligence", "Operator Intelligence",
        "Level 1", "Level 2", "Level 3",
    ):
        assert label in WIREFRAME
    assert "What requires analyst attention?" in WIREFRAME
    assert "How did we establish this?" in WIREFRAME
    assert "What do we know about this actor?" in WIREFRAME
