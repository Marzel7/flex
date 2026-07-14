"""Read-only transaction telemetry for managed SQLite writes."""
from flask import Blueprint, jsonify, request

from src.core.database_write_service import database_write_service


database_write_bp = Blueprint("database_write_telemetry", __name__)


@database_write_bp.route("/api/database-writes/telemetry")
def database_write_telemetry():
    database = request.args.get("database") or None
    limit = min(max(int(request.args.get("limit", 100)), 1), 1000)
    rows = database_write_service.telemetry(database=database, limit=limit)
    return jsonify({"ok": True, "count": len(rows), "transactions": rows})


@database_write_bp.route("/api/database-writes/diagnostics/<path:database>")
def database_write_diagnostics(database: str):
    return jsonify({
        "ok": True,
        **database_write_service.diagnostics(
            database, waiting_command=request.args.get("waiting_command")
        ),
    })


def register_database_write_routes(app) -> None:
    app.register_blueprint(database_write_bp)

