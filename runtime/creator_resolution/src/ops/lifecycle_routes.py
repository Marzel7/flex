"""
Operations OS — Lifecycle API Routes.

GET /api/ops/<op_id>/lifecycle         → LifecycleSnapshot for one operation
GET /api/ops/lifecycle/platform        → PlatformLifecycleSummary across all ops
"""

from __future__ import annotations

import time
from flask import Blueprint, jsonify

from src.ops.lifecycle import (
    IDLE, OBSERVING, ARMED, ACTIVE, COMPLETED,
    PlatformLifecycleSummary,
)
from src.ops.lifecycle_adapters import get_lifecycle, get_all_lifecycles

lifecycle_bp = Blueprint("lifecycle", __name__)


@lifecycle_bp.route("/api/ops/<op_id>/lifecycle")
def op_lifecycle(op_id: str):
    snapshot = get_lifecycle(op_id)
    if snapshot is None:
        return jsonify({"error": f"Unknown operation: {op_id}"}), 404
    return jsonify(snapshot.to_dict())


@lifecycle_bp.route("/api/ops/lifecycle/platform")
def platform_lifecycle():
    snapshots = get_all_lifecycles()
    now = int(time.time())

    online_states = {OBSERVING, ARMED, ACTIVE}

    ops_online     = sum(1 for s in snapshots if s.lifecycle_state in online_states)
    observing_total = sum(s.counts.get(OBSERVING, 0) for s in snapshots)
    armed_total     = sum(s.counts.get(ARMED,     0) for s in snapshots)
    active_total    = sum(s.counts.get(ACTIVE,    0) for s in snapshots)
    completed_today = sum(s.counts.get(COMPLETED, 0) for s in snapshots)

    summary = PlatformLifecycleSummary(
        operations_total=len(snapshots),
        operations_online=ops_online,
        observing_total=observing_total,
        armed_total=armed_total,
        active_total=active_total,
        completed_today=completed_today,
        snapshots=snapshots,
        generated_at=now,
    )
    return jsonify(summary.to_dict())


def register_lifecycle_routes(app) -> None:
    app.register_blueprint(lifecycle_bp)
