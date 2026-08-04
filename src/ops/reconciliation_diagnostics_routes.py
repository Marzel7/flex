"""Developer-only HTTP surface for X69 shadow reconciliation diagnostics."""
from __future__ import annotations

import hmac
import os
from functools import wraps

from flask import Blueprint, abort, current_app, jsonify, make_response, render_template, request


reconciliation_diagnostics_bp = Blueprint("reconciliation_diagnostics", __name__)


def _enabled() -> bool:
    configured = os.environ.get("RECONCILIATION_DIAGNOSTICS_ENABLED", "").lower()
    return configured in {"1", "true", "yes"} or current_app.debug or current_app.testing


def _is_loopback() -> bool:
    address = request.remote_addr or ""
    return address in {"127.0.0.1", "::1"} or address.startswith("127.")


def _authorised() -> bool:
    expected = os.environ.get("RECONCILIATION_DIAGNOSTICS_TOKEN")
    if not expected:
        return _is_loopback()
    supplied = request.headers.get("X-Reconciliation-Diagnostics-Token")
    return bool(supplied) and hmac.compare_digest(supplied, expected)


def diagnostics_only(view):
    @wraps(view)
    def guarded(*args, **kwargs):
        if not _enabled():
            abort(404)
        if not _authorised():
            abort(403)
        return view(*args, **kwargs)
    return guarded


def _workspace():
    from src.core.db import DB_PATH, OPS_DB_PATH
    from src.ops.reconciliation_diagnostics import ReconciliationDiagnosticsService
    return ReconciliationDiagnosticsService(str(OPS_DB_PATH), str(DB_PATH)).build()


def _no_store(response):
    response = make_response(response)
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@reconciliation_diagnostics_bp.route("/diagnostics/reconciliation")
@diagnostics_only
def reconciliation_workspace_page():
    return _no_store(render_template("reconciliation_diagnostics.html"))


@reconciliation_diagnostics_bp.route("/diagnostics/reconciliation/data")
@diagnostics_only
def reconciliation_workspace_data():
    return _no_store(jsonify(_workspace().summary()))


@reconciliation_diagnostics_bp.route(
    "/diagnostics/reconciliation/population/<path:population_id>"
)
@diagnostics_only
def reconciliation_population_detail(population_id: str):
    from src.ops.reconciliation_diagnostics import record_detail
    record = _workspace().get(population_id)
    if record is None:
        abort(404)
    return _no_store(jsonify(record_detail(record)))


@reconciliation_diagnostics_bp.route(
    "/diagnostics/reconciliation/replay/<path:population_id>", methods=["POST"]
)
@diagnostics_only
def replay_reconciliation(population_id: str):
    record = _workspace().get(population_id)
    if record is None:
        abort(404)
    requested_revision = request.args.get("revision", "")
    if requested_revision != record.population.revision_id:
        return _no_store(jsonify({
            "ok": False,
            "error": "Population revision does not match the immutable current record.",
            "requested_revision": requested_revision,
            "available_revision": record.population.revision_id,
        })), 409
    return _no_store(jsonify({
        "ok": record.replay.identical,
        "read_only": True,
        "replay": {
            "population_revision": record.replay.population_revision,
            "original_package_id": record.replay.original_package_id,
            "replay_package_id": record.replay.replay_package_id,
            "original_result_id": record.replay.original_result_id,
            "replay_result_id": record.replay.replay_result_id,
            "identical": record.replay.identical,
        },
    }))


def register_reconciliation_diagnostics_routes(app) -> None:
    app.register_blueprint(reconciliation_diagnostics_bp)
