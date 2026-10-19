"""
Operations OS — Observatory Demo provider routes.

Serves static capability payloads for the observatory_demo operation.
This operation exists solely to validate framework extensibility (A5).

No database. No detection logic. No WATCHTOWER imports.
"""

import time
from flask import Blueprint, jsonify

demo_provider_bp = Blueprint("demo_provider", __name__)


@demo_provider_bp.route("/api/ops-demo/health")
def demo_health():
    return jsonify({
        "ok":                    True,
        "status":                "HEALTHY",
        "pipeline_active":       True,
        "last_event_detected_at": int(time.time()) - 42,
        "generated_at":          int(time.time()),
        "worker_states":         {"signal_scanner": "RUNNING"},
        "active_alert_count":    0,
    })


@demo_provider_bp.route("/api/ops-demo/failure-attribution")
def demo_failure_attribution():
    return jsonify({
        "ok":          True,
        "generated_at": int(time.time()),
        "failure_breakdown": {
            "DISC_ROOT_UNKNOWN":   3,
            "DET_FETCH_TIMEOUT":   1,
            "DET_UNCLASSIFIED":    0,
        },
        "worst_nodes": {
            "origin":       [],
            "intermediate": [],
        },
    })


@demo_provider_bp.route("/api/ops-demo/alerts")
def demo_alerts():
    return jsonify({
        "ok":           True,
        "generated_at": int(time.time()),
        "active_count": 0,
        "active":       [],
        "recovered":    [],
    })


def register_demo_provider_routes(app):
    app.register_blueprint(demo_provider_bp)
