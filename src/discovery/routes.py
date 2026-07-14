"""HTTP and page routes for the read-only Discovery workspace."""

from __future__ import annotations


def register_discovery_routes(app: object) -> None:
    from flask import jsonify, render_template, request

    from src.discovery.service import DiscoveryService

    service = DiscoveryService()

    @app.route("/discovery")  # type: ignore[attr-defined]
    def discovery_page():
        return render_template(
            "discovery.html",
            entity_id=(request.args.get("entity") or "").strip(),
            entity_type=(request.args.get("type") or "auto").strip(),
            active_page="discovery",
        )

    @app.route("/api/discovery/entity/<path:entity_id>")  # type: ignore[attr-defined]
    def discovery_entity(entity_id: str):
        return jsonify(service.resolve(entity_id, request.args.get("type", "auto")))

    @app.route("/api/discovery/search")  # type: ignore[attr-defined]
    def discovery_search():
        return jsonify(service.search(request.args.get("q", ""), request.args.get("limit", 25)))

    @app.route("/api/discovery/recent")  # type: ignore[attr-defined]
    def discovery_recent():
        return jsonify(service.recent(request.args.get("limit", 20)))
