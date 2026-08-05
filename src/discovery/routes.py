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

    @app.route("/api/discovery/watchtower-recovery-diagnostics")  # type: ignore[attr-defined]
    def discovery_watchtower_recovery_diagnostics():
        """X76.4 -- read-only re-projection of the WATCHTOWER recovery
        pipeline (build_watchtower_funnel + operation_convergence +
        treasury_review_workspace + operator identity tables). Explains
        WHERE the pipeline stops for a recent window instead of only
        reporting a launch count. Writes nothing; overrides no decision."""
        import os

        from src.ops.watchtower_recovery_diagnostics import build_recovery_diagnostics

        core_db_path = os.environ.get(
            "DB_PATH",
            os.path.join(os.path.dirname(__file__), "../../database/flex_complete_database.db"),
        )
        ops_db_path = os.environ.get(
            "WT_OPS_DB_PATH",
            os.path.join(os.path.dirname(__file__), "../../database/wt_ops_v2.db"),
        )
        try:
            hours = max(1, min(int(request.args.get("hours", 72)), 720))
        except (TypeError, ValueError):
            hours = 72
        return jsonify(build_recovery_diagnostics(ops_db_path, core_db_path, window_seconds=hours * 3600))
