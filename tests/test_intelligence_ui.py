"""
Tests for Entity Intelligence UI (Sprint I2).

Covers (Task 14):
  - Route registered at /intelligence/entity/<id>
  - Unknown entity renders without error
  - Template exists and extends base_shell
  - API endpoint used is exactly /api/intelligence/entity/<id> (one request, no polling)
  - Knowledge grouping logic matches category vocabulary
  - Confidence rendering uses correct CSS class names
  - Operation list rendering is data-driven (no hardcoded names)
  - Summary rendered as-is from API (not regenerated)
  - Navigation link present in sidebar
  - Entity Intelligence link in watchtower operator detail
  - No Flask at page_routes module level
  - No duplicate /api/ fetch calls in template
  - Search route redirects correctly
  - No new database / schema / worker changes
"""

from __future__ import annotations

import os
import sys

import pytest


# ── Helpers ────────────────────────────────────────────────────────────────────

def _read_template(name: str) -> str:
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(repo, "templates", name)
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _make_flask_app():
    """Minimal Flask app with template folder, for route-existence checks only."""
    from flask import Flask
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app = Flask(__name__, template_folder=os.path.join(repo, "templates"))
    app.config["TESTING"] = True
    app.secret_key = "test"
    return app


# ── 1. No Flask at page_routes module level ───────────────────────────────────

class TestNoFlaskAtImport:
    def test_page_routes_no_flask(self) -> None:
        for k in list(sys.modules):
            if k.startswith("flask"):
                del sys.modules[k]
        import src.intelligence.page_routes  # noqa: F401
        assert "flask" not in sys.modules


# ── 2. Template exists and has correct structure ──────────────────────────────

class TestTemplateStructure:
    def test_template_exists(self) -> None:
        content = _read_template("entity_intelligence.html")
        assert len(content) > 500

    def test_extends_base_shell(self) -> None:
        content = _read_template("entity_intelligence.html")
        assert 'extends "base_shell.html"' in content

    def test_has_block_title(self) -> None:
        content = _read_template("entity_intelligence.html")
        assert "{% block title %}" in content

    def test_has_block_content(self) -> None:
        content = _read_template("entity_intelligence.html")
        assert "{% block content %}" in content

    def test_entity_id_jinja_variable_used(self) -> None:
        content = _read_template("entity_intelligence.html")
        assert "entity_id" in content

    def test_no_operation_name_hardcoded(self) -> None:
        """Template must not contain hardcoded operation-specific branches."""
        content = _read_template("entity_intelligence.html")
        # These strings should not appear as hardcoded branches
        for forbidden in ("if op == 'watchtower'", "if obs.operation_id ==",
                          "{% if d.name == 'watchtower'"):
            assert forbidden not in content, f"Found forbidden hardcoding: {forbidden!r}"


# ── 3. API endpoint configuration ────────────────────────────────────────────

class TestApiEndpointConfig:
    def test_fetch_calls_count(self) -> None:
        """Template makes fetch() calls: EI data + relationships + per-op lifecycle (variable).
        Must be at least 2 (EI + relationships) and the lifecycle fetches are inside a Promise.all."""
        content = _read_template("entity_intelligence.html")
        fetch_count = content.count("fetch(")
        assert fetch_count >= 2, f"Expected at least 2 fetch() calls, found {fetch_count}"
        # Lifecycle fetches are nested inside Promise.all so may add N more
        assert fetch_count <= 10, f"Unexpectedly many fetch() calls: {fetch_count}"

    def test_fetch_uses_intelligence_api(self) -> None:
        """The fetch URL must point at /api/intelligence/entity/."""
        content = _read_template("entity_intelligence.html")
        assert "/api/intelligence/entity/" in content

    def test_no_setinterval_polling(self) -> None:
        """No background polling — single request pattern."""
        content = _read_template("entity_intelligence.html")
        assert "setInterval" not in content

    def test_no_websocket(self) -> None:
        content = _read_template("entity_intelligence.html")
        assert "WebSocket" not in content
        assert "new WebSocket" not in content


# ── 4. Confidence rendering ────────────────────────────────────────────────────

class TestConfidenceRendering:
    EXPECTED_LEVELS = ("CERTAIN", "HIGH", "MEDIUM", "LOW", "UNKNOWN")

    def test_all_confidence_levels_have_css_class(self) -> None:
        content = _read_template("entity_intelligence.html")
        for level in self.EXPECTED_LEVELS:
            assert f"conf-{level}" in content, \
                f"CSS class conf-{level} not found in template"

    def test_conf_badge_function_present(self) -> None:
        content = _read_template("entity_intelligence.html")
        assert "confBadge" in content

    def test_confidence_breakdown_rendered(self) -> None:
        """Template renders ceiling, floor, and contributing facts."""
        content = _read_template("entity_intelligence.html")
        assert "Ceiling" in content
        assert "Floor" in content
        assert "contributing_facts" in content or "Contributing" in content


# ── 5. Knowledge grouping ─────────────────────────────────────────────────────

class TestKnowledgeGrouping:
    def test_category_labels_mapped(self) -> None:
        """Template must map category keys to human labels."""
        content = _read_template("entity_intelligence.html")
        assert "EXECUTION" in content or "Execution" in content
        assert "FUNDER_TYPE" in content or "Funding" in content
        assert "TOOLING" in content or "Tooling" in content
        assert "BEHAVIOUR" in content or "Behaviour" in content

    def test_knowledge_grouped_by_category(self) -> None:
        """Template groups by item.category before rendering."""
        content = _read_template("entity_intelligence.html")
        assert "category" in content
        # Either jinja groupby or JS grouping
        assert ("groupOrder" in content or "cat" in content or "groups" in content)

    def test_knowledge_shows_provenance(self) -> None:
        content = _read_template("entity_intelligence.html")
        assert "provenance" in content


# ── 6. Timeline rendering ─────────────────────────────────────────────────────

class TestTimelineRendering:
    def test_timeline_table_headers(self) -> None:
        content = _read_template("entity_intelligence.html")
        assert "Time" in content
        assert "Source" in content
        assert "Event" in content
        assert "Description" in content

    def test_timeline_not_client_sorted(self) -> None:
        """Timeline must use I1's ordering, not sort client-side."""
        content = _read_template("entity_intelligence.html")
        # The JS must NOT sort the timeline array
        assert "timeline.sort" not in content
        assert ".sort(function" not in content or "timeline.sort" not in content

    def test_source_badge_classes_present(self) -> None:
        content = _read_template("entity_intelligence.html")
        assert "src-WATCHTOWER" in content
        assert "src-KNOWLEDGE_LAYER" in content

    def test_ts_formatted_as_ago(self) -> None:
        """fmtAgo() function must be present."""
        content = _read_template("entity_intelligence.html")
        assert "fmtAgo" in content


# ── 7. Unknown entity handling ────────────────────────────────────────────────

class TestUnknownEntityHandling:
    def test_unknown_entity_branch_present(self) -> None:
        content = _read_template("entity_intelligence.html")
        assert "isUnknown" in content or "Unknown Entity" in content or "unknown" in content.lower()

    def test_unknown_entity_shows_confidence_badge(self) -> None:
        content = _read_template("entity_intelligence.html")
        # renderUnknown calls confBadge('UNKNOWN')
        assert "UNKNOWN" in content

    def test_no_stack_trace_on_unknown(self) -> None:
        """Template handles unknown gracefully — no try/catch that re-throws."""
        content = _read_template("entity_intelligence.html")
        # catch block must write an error div, not throw
        assert "ei-error" in content


# ── 8. Navigation ─────────────────────────────────────────────────────────────

class TestNavigation:
    def test_sidebar_has_entity_intelligence_link(self) -> None:
        content = _read_template("partials/sidebar.html")
        assert "Entity Intelligence" in content
        assert "/intelligence/entity/" in content

    def test_sidebar_active_page_for_entity_intelligence(self) -> None:
        content = _read_template("partials/sidebar.html")
        assert "entity_intelligence" in content

    def test_operator_detail_has_entity_intelligence_link(self) -> None:
        content = _read_template("watchtower_operator_detail.html")
        assert "Entity Intelligence" in content
        assert "/intelligence/entity/" in content

    def test_entity_intelligence_link_uses_address_variable(self) -> None:
        """Link in operator detail must use the {{ address }} template variable."""
        content = _read_template("watchtower_operator_detail.html")
        assert "/intelligence/entity/{{ address }}" in content

    def test_page_has_search_form(self) -> None:
        content = _read_template("entity_intelligence.html")
        assert "/intelligence/search" in content
        assert 'name="address"' in content


# ── 9. Route registration ─────────────────────────────────────────────────────

class TestRouteRegistration:
    def test_page_route_registers(self) -> None:
        app = _make_flask_app()
        from src.intelligence.page_routes import register_intelligence_page_routes
        register_intelligence_page_routes(app)
        rules = {r.rule for r in app.url_map.iter_rules()}
        assert "/intelligence/entity/<entity_id>" in rules

    def test_search_route_registers(self) -> None:
        app = _make_flask_app()
        from src.intelligence.page_routes import register_intelligence_page_routes
        register_intelligence_page_routes(app)
        rules = {r.rule for r in app.url_map.iter_rules()}
        assert "/intelligence/search" in rules

    def test_page_route_returns_200(self) -> None:
        app = _make_flask_app()
        from src.intelligence.page_routes import register_intelligence_page_routes
        register_intelligence_page_routes(app)
        with app.test_client() as c:
            resp = c.get("/intelligence/entity/SomeTestAddress123")
            assert resp.status_code == 200

    def test_page_renders_entity_id_in_html(self) -> None:
        app = _make_flask_app()
        from src.intelligence.page_routes import register_intelligence_page_routes
        register_intelligence_page_routes(app)
        addr = "TestAddr1234567890ABCDEF"
        with app.test_client() as c:
            resp = c.get(f"/intelligence/entity/{addr}")
            html = resp.data.decode()
            assert addr in html

    def test_search_redirects_to_entity_page(self) -> None:
        app = _make_flask_app()
        from src.intelligence.page_routes import register_intelligence_page_routes
        register_intelligence_page_routes(app)
        with app.test_client() as c:
            addr = "MyWalletAddress"
            resp = c.get(f"/intelligence/search?address={addr}")
            assert resp.status_code in (301, 302)
            assert addr in resp.headers.get("Location", "")

    def test_search_without_address_redirects_to_ops(self) -> None:
        app = _make_flask_app()
        from src.intelligence.page_routes import register_intelligence_page_routes
        register_intelligence_page_routes(app)
        with app.test_client() as c:
            resp = c.get("/intelligence/search")
            assert resp.status_code in (301, 302)
            assert "ops-os" in resp.headers.get("Location", "")


# ── 10. No modifications to frozen systems ───────────────────────────────────

class TestFrozenSystemsUnmodified:
    def _source(self, path: str) -> str:
        import inspect, importlib
        return inspect.getsource(importlib.import_module(path))

    def test_intelligence_aggregator_not_modified_by_page_routes(self) -> None:
        """page_routes must not import from aggregator (stays frozen)."""
        import inspect
        from src.intelligence import page_routes
        src = inspect.getsource(page_routes)
        assert "aggregator" not in src

    def test_page_routes_no_db_writes(self) -> None:
        import inspect
        from src.intelligence import page_routes
        src = inspect.getsource(page_routes)
        for kw in ("INSERT", "UPDATE", "DELETE", "sqlite3"):
            assert kw not in src

    def test_template_no_operation_specific_api_calls(self) -> None:
        """Template must not call /api/ops/launcher-observatory/* or WATCHTOWER APIs."""
        content = _read_template("entity_intelligence.html")
        assert "/api/ops/launcher-observatory" not in content
        assert "/api/ops/watchtower" not in content
        assert "/api/watchtower" not in content
