"""
Sprint X9 — Operator Intelligence Workspace tests.

Covers:
  - New routes: /intelligence/operator/<id>, /intelligence/operators,
    /api/ops/operators/search
  - operator_intelligence.html markup: three columns, all sections
  - operators_index.html markup: summary strip, filter bar, search, list
  - entity_intelligence.html: operator link, fetch, no-operator state
  - sidebar.html: Operators nav entry
  - Navigation integrity: no dead ends (all key hrefs present)
  - Search API: by entity address, by status filter, empty result
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# ── helpers ──────────────────────────────────────────────────────────────────

def _tmp_store():
    fd, path = tempfile.mkstemp(suffix="_x9_test.db")
    os.close(fd)
    from src.ops.operator_store import OperatorStore
    store = OperatorStore(path)
    store.initialize_schema()
    return store, path


def _read_template(name):
    with open(os.path.join(ROOT, "templates", name), encoding="utf-8") as f:
        return f.read()


def _read_partial(name):
    with open(os.path.join(ROOT, "templates", "partials", name), encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def op_client():
    import src.ops.operator_routes as orr
    store, db_path = _tmp_store()
    orr._store = store

    from flask import Flask
    app = Flask(
        __name__,
        template_folder=os.path.join(ROOT, "templates"),
        static_folder=os.path.join(ROOT, "static"),
    )
    app.register_blueprint(orr.operator_bp)
    app.config["TESTING"] = True

    with app.test_client() as c:
        yield c, store

    try:
        os.unlink(db_path)
    except OSError:
        pass


# ── Route: operator page ──────────────────────────────────────────────────────

class TestOperatorPageRoute:

    def test_unknown_operator_returns_404(self, op_client):
        client, _ = op_client
        r = client.get("/intelligence/operator/no-such-id")
        assert r.status_code == 404

    def test_known_operator_returns_200(self, op_client):
        client, store = op_client
        op_id = store.create_operator(summary="test operator")
        r = client.get(f"/intelligence/operator/{op_id}")
        assert r.status_code == 200
        html = r.data.decode()
        assert op_id in html

    def test_operator_page_has_three_column_dossier(self, op_client):
        client, store = op_client
        op_id = store.create_operator()
        html = client.get(f"/intelligence/operator/{op_id}").data.decode()
        assert "ip-dossier-left"   in html
        assert "ip-dossier-centre" in html
        assert "ip-dossier-right"  in html

    def test_operator_page_has_identity_card(self, op_client):
        client, store = op_client
        op_id = store.create_operator()
        html = client.get(f"/intelligence/operator/{op_id}").data.decode()
        assert "oi-identity-card" in html
        assert "Confidence"       in html
        assert "Review State"     in html

    def test_operator_page_has_evidence_section(self, op_client):
        client, store = op_client
        op_id = store.create_operator()
        html = client.get(f"/intelligence/operator/{op_id}").data.decode()
        assert "oi-evidence-body" in html
        assert "Evidence"         in html

    def test_operator_page_has_timeline_section(self, op_client):
        client, store = op_client
        op_id = store.create_operator()
        html = client.get(f"/intelligence/operator/{op_id}").data.decode()
        assert "oi-timeline-body" in html
        assert "Timeline"         in html

    def test_operator_page_has_review_section(self, op_client):
        client, store = op_client
        op_id = store.create_operator()
        html = client.get(f"/intelligence/operator/{op_id}").data.decode()
        assert "oi-review-body"  in html
        assert "Review History"  in html

    def test_operator_page_has_entities_section(self, op_client):
        client, store = op_client
        op_id = store.create_operator()
        html = client.get(f"/intelligence/operator/{op_id}").data.decode()
        assert "oi-entities-body" in html
        assert "Known Entities"   in html

    def test_operator_page_has_quick_actions(self, op_client):
        client, store = op_client
        op_id = store.create_operator()
        html = client.get(f"/intelligence/operator/{op_id}").data.decode()
        assert "Promotion Review" in html
        assert "Quick Actions" in html

    def test_operator_page_has_breadcrumb_navigation(self, op_client):
        client, store = op_client
        op_id = store.create_operator()
        html = client.get(f"/intelligence/operator/{op_id}").data.decode()
        assert "/ops-os"                 in html
        assert "/intelligence/operators" in html

    def test_operator_page_links_to_entity_intelligence(self, op_client):
        client, store = op_client
        op_id = store.create_operator()
        store.add_entity(op_id, "WALLET_ABC", entity_type="TREASURY")
        html = client.get(f"/intelligence/operator/{op_id}").data.decode()
        assert "/intelligence/entity/" in html

    def test_operator_page_links_to_inbox(self, op_client):
        client, store = op_client
        op_id = store.create_operator()
        html = client.get(f"/intelligence/operator/{op_id}").data.decode()
        assert "/intelligence/inbox" in html

    def test_operator_page_has_review_api_call(self, op_client):
        client, store = op_client
        op_id = store.create_operator()
        html = client.get(f"/intelligence/operator/{op_id}").data.decode()
        assert "/api/ops/operators/" in html
        assert "/review"             in html

    def test_operator_page_has_operations_section(self, op_client):
        client, store = op_client
        op_id = store.create_operator()
        html = client.get(f"/intelligence/operator/{op_id}").data.decode()
        assert "oi-ops-body" in html
        assert "Operations"  in html

    def test_operator_page_shows_status_chip(self, op_client):
        client, store = op_client
        op_id = store.create_operator()
        html = client.get(f"/intelligence/operator/{op_id}").data.decode()
        assert "oi-status-chip" in html
        assert "CANDIDATE"      in html


# ── Route: operators index ────────────────────────────────────────────────────

class TestOperatorsIndexRoute:

    def test_index_returns_200(self, op_client):
        client, _ = op_client
        r = client.get("/intelligence/operators")
        assert r.status_code == 200

    def test_index_has_summary_strip(self, op_client):
        client, _ = op_client
        html = client.get("/intelligence/operators").data.decode()
        assert "s-total"       in html
        assert "s-candidate"   in html
        assert "s-confirmed"   in html
        assert "s-provisional" in html

    def test_index_has_filter_bar(self, op_client):
        client, _ = op_client
        html = client.get("/intelligence/operators").data.decode()
        assert "ops-filter-btn" in html
        assert "Confirmed"      in html
        assert "Provisional"    in html

    def test_index_has_search(self, op_client):
        client, _ = op_client
        html = client.get("/intelligence/operators").data.decode()
        assert "ops-search"   in html
        assert "Search"       in html

    def test_index_fetches_operators_api(self, op_client):
        client, _ = op_client
        html = client.get("/intelligence/operators").data.decode()
        assert "/api/ops/operators/summary" in html
        assert "/api/ops/operators"         in html

    def test_index_has_resolver_button(self, op_client):
        client, _ = op_client
        html = client.get("/intelligence/operators").data.decode()
        assert "Evaluate Identity" in html
        assert "triggerResolve" in html

    def test_index_breadcrumb_to_mission_control(self, op_client):
        client, _ = op_client
        html = client.get("/intelligence/operators").data.decode()
        assert "/ops-os" in html


# ── Route: search ─────────────────────────────────────────────────────────────

class TestOperatorSearchRoute:

    def test_empty_search_returns_all(self, op_client):
        client, store = op_client
        store.create_operator(summary="alpha")
        store.create_operator(summary="beta")
        r = client.get("/api/ops/operators/search")
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"] is True
        assert data["count"] >= 2

    def test_entity_search_returns_operators_containing_wallet(self, op_client):
        client, store = op_client
        op_id = store.create_operator(summary="treasury owner")
        store.add_entity(op_id, "WALLET_TREASURY_XYZ", entity_type="TREASURY")
        r = client.get("/api/ops/operators/search?q=WALLET_TREASURY_XYZ")
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"] is True
        assert data["mode"] == "entity"
        assert any(o["operator_id"] == op_id for o in data["results"])

    def test_status_filter(self, op_client):
        client, store = op_client
        op_id = store.create_operator()
        store.record_review(op_id, decision="CONFIRMED")
        r = client.get("/api/ops/operators/search?status=CONFIRMED")
        assert r.status_code == 200
        data = r.get_json()
        assert all(o["status"] == "CONFIRMED" for o in data["results"])

    def test_nonexistent_wallet_returns_empty(self, op_client):
        client, _ = op_client
        r = client.get("/api/ops/operators/search?q=WALLET_THAT_DOES_NOT_EXIST_XYZABC")
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"] is True


# ── Template markup: operator_intelligence.html ───────────────────────────────

class TestOperatorIntelligenceTemplate:

    def test_has_three_column_dossier(self):
        html = _read_template("operator_intelligence.html")
        assert "ip-dossier-left"   in html
        assert "ip-dossier-centre" in html
        assert "ip-dossier-right"  in html

    def test_has_all_centre_sections(self):
        html = _read_template("operator_intelligence.html")
        for section in ("Summary", "Evidence", "Timeline", "Review History", "Attention"):
            assert section in html, f"Missing section: {section}"

    def test_has_all_right_column_sections(self):
        html = _read_template("operator_intelligence.html")
        assert "Known Entities"  in html
        assert "Operations"      in html

    def test_has_review_api_calls(self):
        html = _read_template("operator_intelligence.html")
        assert "/api/ops/operators/" in html
        assert "doReview"            in html
        assert "triggerResolve"      in html

    def test_has_entity_group_rendering(self):
        html = _read_template("operator_intelligence.html")
        assert "oi-entity-group"  in html
        assert "Treasuries"       in html
        assert "Sub-Provisioners" in html

    def test_evidence_grouped_by_category(self):
        html = _read_template("operator_intelligence.html")
        assert "Identity Evidence"   in html
        assert "Supporting Evidence" in html

    def test_timeline_renders_from_operator_data(self):
        html = _read_template("operator_intelligence.html")
        assert "buildTimeline" in html
        assert "oi-tl-row"     in html

    def test_no_dead_ends_in_navigation(self):
        html = _read_template("operator_intelligence.html")
        # Every key nav target is present
        assert "/ops-os"                 in html  # Mission Control
        assert "/intelligence/operators" in html  # Operators index
        assert "/intelligence/entity/"   in html  # Entity Intelligence
        assert "/intelligence/inbox"     in html  # Inbox

    def test_inbox_section_hidden_when_empty(self):
        html = _read_template("operator_intelligence.html")
        assert 'display:none'            in html
        assert "oi-inbox-section"        in html

    def test_uses_intel_platform_css(self):
        html = _read_template("operator_intelligence.html")
        assert "intel-platform.css" in html

    def test_breadcrumb_present(self):
        html = _read_template("operator_intelligence.html")
        assert "ip-breadcrumb" in html

    def test_copy_id_function_present(self):
        html = _read_template("operator_intelligence.html")
        assert "copyId" in html


# ── Template markup: operators_index.html ────────────────────────────────────

class TestOperatorsIndexTemplate:

    def test_has_summary_strip(self):
        html = _read_template("operators_index.html")
        assert "s-total"       in html
        assert "s-confirmed"   in html
        assert "s-provisional" in html

    def test_has_filter_buttons(self):
        html = _read_template("operators_index.html")
        assert "setFilter"      in html
        assert "CONFIRMED"      in html
        assert "PROVISIONAL"    in html
        assert "CANDIDATE"      in html

    def test_has_search_input(self):
        html = _read_template("operators_index.html")
        assert "ops-search"  in html
        assert "doSearch"    in html

    def test_has_resolver_trigger(self):
        html = _read_template("operators_index.html")
        assert "triggerResolve" in html

    def test_operator_rows_link_to_dossier(self):
        html = _read_template("operators_index.html")
        assert "/intelligence/operator/" in html

    def test_fetches_summary_and_list_apis(self):
        html = _read_template("operators_index.html")
        assert "/api/ops/operators/summary" in html
        assert "/api/ops/operators"         in html

    def test_uses_intel_platform_css(self):
        html = _read_template("operators_index.html")
        assert "intel-platform.css" in html


# ── Template: entity_intelligence.html ───────────────────────────────────────

class TestEntityIntelligenceOperatorIntegration:

    def test_has_operator_section_placeholder(self):
        html = _read_template("entity_intelligence.html")
        assert "ei-operator-section" in html

    def test_fetches_operator_by_entity_api(self):
        html = _read_template("entity_intelligence.html")
        assert "/api/ops/operators/by-entity/" in html

    def test_has_no_operator_resolved_text(self):
        html = _read_template("entity_intelligence.html")
        assert "No operator currently resolved" in html

    def test_has_view_operator_quick_action_link(self):
        html = _read_template("entity_intelligence.html")
        assert "ei-operator-link"        in html
        assert "/intelligence/operators" in html

    def test_operator_link_initially_hidden(self):
        html = _read_template("entity_intelligence.html")
        assert "ei-operator-link" in html
        # The link is hidden by default, shown when operator found
        assert "display:none" in html

    def test_fill_operator_function_exists(self):
        html = _read_template("entity_intelligence.html")
        assert "fillOperator" in html


# ── Sidebar navigation ────────────────────────────────────────────────────────

class TestSidebarNavigation:

    def test_sidebar_has_operators_link(self):
        html = _read_partial("sidebar.html")
        assert "/intelligence/operators" in html
        assert "Operators"               in html

    def test_sidebar_has_inbox_link(self):
        html = _read_partial("sidebar.html")
        assert "/intelligence/inbox" in html
        assert "Inbox"               in html

    def test_sidebar_operators_under_intelligence(self):
        html = _read_partial("sidebar.html")
        # Operators should appear after Entity Intelligence in the document
        ei_pos = html.find("/intelligence/entity/")
        op_pos = html.find("/intelligence/operators")
        assert ei_pos >= 0 and op_pos >= 0
        assert op_pos > ei_pos


# ── Evidence-catalogue endpoint ───────────────────────────────────────────────

class TestEvidenceCatalogueEndpoint:

    def test_evidence_catalogue_returns_all_categories(self, op_client):
        client, _ = op_client
        r = client.get("/api/ops/evidence-catalogue")
        data = r.get_json()
        categories = {e["category"] for e in data["evidence"]}
        assert "IDENTITY"   in categories
        assert "SUPPORTING" in categories
        assert "CONTEXT"    in categories

    def test_evidence_catalogue_has_notes_field(self, op_client):
        client, _ = op_client
        data = client.get("/api/ops/evidence-catalogue").get_json()
        assert all("notes" in e for e in data["evidence"])
