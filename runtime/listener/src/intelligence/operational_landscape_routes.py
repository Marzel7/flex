"""Read-only Operational Landscape UI and JSON API."""
from flask import Blueprint, abort, jsonify, render_template, request

from src.intelligence.operational_landscape import (
    DATASETS, LandscapeUnavailable, landscape, motif, neighbourhood,
)

operational_landscape_bp = Blueprint("operational_landscape", __name__)


def _dataset() -> str:
    value = request.args.get("dataset", DATASETS[0])
    if value not in DATASETS:
        abort(400)
    return value


def _json(call):
    try:
        value = call()
    except LandscapeUnavailable as exc:
        return jsonify({"ok": False, "error": str(exc), "read_only": True}), 503
    if value is None:
        abort(404)
    response = jsonify({"ok": True, **value})
    response.headers["Cache-Control"] = "no-store"
    return response


@operational_landscape_bp.route("/intelligence/landscape")
def landscape_page():
    return render_template("operational_landscape.html", active_page="operational_landscape")


@operational_landscape_bp.route("/intelligence/landscape/motifs/<motif_id>")
def motif_page(motif_id: str):
    return render_template("motif_explorer.html", active_page="operational_landscape", motif_id=motif_id)


@operational_landscape_bp.route("/intelligence/landscape/neighbourhoods/<neighbourhood_id>")
def neighbourhood_page(neighbourhood_id: str):
    return render_template("neighbourhood_explorer.html", active_page="operational_landscape", neighbourhood_id=neighbourhood_id)


@operational_landscape_bp.route("/api/intelligence/landscape")
def landscape_api():
    return _json(lambda: landscape(_dataset()))


@operational_landscape_bp.route("/api/intelligence/landscape/motifs/<motif_id>")
def motif_api(motif_id: str):
    return _json(lambda: motif(motif_id, _dataset()))


@operational_landscape_bp.route("/api/intelligence/landscape/neighbourhoods/<neighbourhood_id>")
def neighbourhood_api(neighbourhood_id: str):
    return _json(lambda: neighbourhood(neighbourhood_id, _dataset()))


def register_operational_landscape_routes(app) -> None:
    app.register_blueprint(operational_landscape_bp)
