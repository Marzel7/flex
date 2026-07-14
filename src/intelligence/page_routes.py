"""
Entity Intelligence — server-rendered page routes.

GET /intelligence/entity/<entity_id>   → renders entity_intelligence.html
GET /intelligence/search               → redirects to /intelligence/entity/<id>
                                         (search-by-address form target)

No Flask imported at module level.
"""

from __future__ import annotations


def register_intelligence_page_routes(app: object) -> None:
    from flask import redirect, render_template, request, url_for

    @app.route("/intelligence/entity/<entity_id>")  # type: ignore[attr-defined]
    def intelligence_entity_page(entity_id: str):
        return render_template(
            "entity_intelligence.html",
            entity_id=entity_id,
            active_page="entity_intelligence",
        )

    @app.route("/intelligence/entity/")  # type: ignore[attr-defined]
    def intelligence_entity_landing():
        """Entity pages are contextual; direct navigation begins at Discovery."""
        return redirect("/discovery")

    @app.route("/intelligence/cross-operation")  # type: ignore[attr-defined]
    def intelligence_cross_operation_page():
        return render_template(
            "cross_operation_intelligence.html",
            active_page="cross_operation",
        )

    @app.route("/intelligence/knowledge")  # type: ignore[attr-defined]
    def intelligence_knowledge_page():
        return render_template(
            "knowledge.html",
            active_page="knowledge",
        )

    @app.route("/intelligence/search")  # type: ignore[attr-defined]
    def intelligence_search():
        address = (request.args.get("address") or "").strip()
        if address:
            return redirect(f"/intelligence/entity/{address}")
        return redirect("/ops-os")
