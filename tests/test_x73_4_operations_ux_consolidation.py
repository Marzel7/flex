from pathlib import Path

from flask import Flask

from src.ops import operator_routes


ROOT = Path(__file__).resolve().parents[1]


def text(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def test_primary_navigation_converges_on_operations_registry():
    sidebar = text("templates/partials/sidebar.html")
    assert 'href="/intelligence/operations">Operation Intelligence' not in sidebar
    assert 'href="/intelligence/operators">Operations Registry' in sidebar
    assert 'href="/intelligence/operator-promotions"' not in sidebar
    assert "Canonical Operators" not in sidebar


def test_operations_page_is_the_four_section_investigation_workspace():
    page = text("templates/emerging_operators.html")
    for heading in ("Confirmed Operations", "Investigation Populations", "Review", "Infrastructure"):
        assert f"<h2>{heading}</h2>" in page
    assert 'id="or-operator-candidates"' not in page
    assert "no governance actions occur here" in page
    assert "/intelligence/operators" in page
    assert "Expand Identity" not in page
    assert "Merge Identity" not in page
    assert "Split Identity" not in page


def test_operator_registry_remains_vertical_and_profiles_own_governance():
    index = text("templates/operators_index.html")
    profile = text("templates/operator_intelligence.html")
    assert "Operations Registry" in index
    assert "reg-row" in index and "or-card" not in index
    for detail in ("Permanent Identity Timeline", "oi-evidence-section", "oi-review-history"):
        assert detail in profile
    assert "/intelligence/operator-promotions" not in index + profile


def test_population_profile_owns_promotion_and_relationship_context():
    profile = text("templates/operation_profile.html")
    for label in (
        "Current operator", "Parent investigation", "Child identities",
        "Investigation summary", "Govern identity",
    ):
        assert label in profile
    assert "Confirm Operation" in profile
    assert "/api/operators/promotions/" in profile
    assert "/approve" in profile
    assert "proposal_fingerprint" in profile
    assert "identity_fingerprint" in profile
    assert "/api/ops/operations/'+encodeURIComponent(f.family_id)+'/confirm" not in profile


def test_legacy_promotion_page_redirects_but_apis_remain_registered():
    app = Flask(__name__)
    app.register_blueprint(operator_routes.operator_bp)
    with app.test_client() as client:
        response = client.get("/intelligence/operator-promotions")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/intelligence/operators?focus=review")
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert "/api/operators/promotions" in rules
    assert "/api/operators/promotions/<proposal_id>" in rules
    assert "/api/operators/promotions/<proposal_id>/approve" in rules
    assert "/api/operators/promotions/<proposal_id>/reject" in rules
    assert "/api/operators/promotions/<proposal_id>/defer" in rules


def test_no_primary_surface_links_to_retired_promotion_page():
    primary = "\n".join(text(path) for path in (
        "templates/partials/sidebar.html", "templates/emerging_operators.html",
        "templates/operation_profile.html", "templates/operators_index.html",
        "templates/operator_intelligence.html", "templates/ops_shell_index.html",
        "templates/discovery.html",
    ))
    assert 'href="/intelligence/operator-promotions"' not in primary
    assert "href:'/intelligence/operator-promotions'" not in primary


def test_component_inventory_records_move_merge_and_retire_decisions():
    inventory = text("docs/audits/x73_4_operations_ux_component_inventory.md")
    for term in (
        "Component Inventory", "Identity %", "Review Candidate",
        "Cross-operation wallet overlap", "Promotion Proposal list",
        "Retire", "Move", "Merge", "Compatibility",
    ):
        assert term in inventory
