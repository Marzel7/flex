from pathlib import Path

from flask import Flask

from src.ops import operator_routes


ROOT = Path(__file__).resolve().parents[1]


def text(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def test_operations_registry_has_one_compact_vertical_lifecycle_list():
    page = text("templates/operators_index.html")
    assert "Operations Registry" in page
    assert "reg-row" in page
    assert "or-card" not in page
    for section in (
        "Confirmed Operations",
        "Investigation Populations",
        "Review",
        "Infrastructure",
    ):
        assert section in page
    assert 'id="section-confirmed" open' in page
    assert 'id="section-investigation" open' in page
    assert 'id="section-infrastructure"' in page
    assert "Promotion Readiness" not in page
    assert "Run Resolver" not in page
    assert "d==='INFRASTRUCTURE'&&p.title?p.title" in page


def test_registry_has_single_kpi_row_search_and_required_filters():
    page = text("templates/operators_index.html")
    assert page.count('class="ip-strip"') == 1
    assert page.count('id="reg-search"') == 1
    for label in (
        "Confirmed", "Active", "Dormant", "Investigation", "Review",
        "Infrastructure", "Merged", "Retired", "Split",
    ):
        assert f">{label}</button>" in page
    assert "/api/ops/operators/search?q=" in page
    assert "/api/ops/emerging-operators?limit=500" in page


def test_legacy_operations_routes_redirect_and_profile_route_remains():
    app = Flask(__name__, template_folder=str(ROOT / "templates"))
    app.register_blueprint(operator_routes.operator_bp)
    with app.test_client() as client:
        plain = client.get("/intelligence/operations")
        review = client.get("/intelligence/operations?focus=review")
    assert plain.status_code == 302
    assert plain.headers["Location"].endswith("/intelligence/operators")
    assert review.status_code == 302
    assert review.headers["Location"].endswith("/intelligence/operators?focus=review")
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert "/intelligence/operations/<path:entity>" in rules
    assert "/intelligence/operator/<operator_id>" in rules


def test_primary_navigation_and_back_links_use_registry():
    primary = "\n".join(text(path) for path in (
        "templates/partials/sidebar.html",
        "templates/operation_profile.html",
        "templates/operator_intelligence.html",
        "templates/ops_shell_index.html",
        "templates/discovery.html",
    ))
    assert 'href="/intelligence/operators">Operations Registry' in primary
    assert 'href="/intelligence/operations">Operation Intelligence' not in primary
    assert "/intelligence/operations?focus=review" not in primary


def test_registry_reuses_identity_and_population_profiles():
    page = text("templates/operators_index.html")
    assert "'/intelligence/operator/'" in page
    assert "'/intelligence/operations/'" in page
    assert "/api/operators/promotions/" not in page
    assert "/api/ops/operations/" not in page
