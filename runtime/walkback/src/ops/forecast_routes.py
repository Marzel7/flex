"""
Sprint X14 — Lifecycle Forecast API routes.

All routes read from pre-computed / freshly-computed forecast outputs.
No route triggers unbounded computation or hot-path DB writes.

GET /api/operators/<id>/forecast               — current forecast
GET /api/operators/<id>/forecast/history       — forecast history (in-memory, last N)
GET /api/forecast/platform-summary             — platform-level view
"""

from __future__ import annotations

import collections
import threading
import time
from flask import Blueprint, jsonify, request

forecast_bp = Blueprint("forecast", __name__)

_engine = None
_history: dict[str, collections.deque] = {}   # operator_id → deque of LifecycleForecast
_HISTORY_MAX = 20
_history_lock = threading.Lock()


def _get_engine():
    global _engine
    if _engine is None:
        from src.core.db import OPS_DB_PATH, LIVE_DB_PATH
        from src.ops.assessment_engine import AssessmentEngine
        from src.ops.behaviour_engine import BehaviourEngine
        from src.ops.behaviour_change import BehaviourChangeEngine
        from src.ops.operator_similarity import OperatorSimilarityEngine
        from src.ops.forecast_engine import ForecastEngine

        beh  = BehaviourEngine(str(OPS_DB_PATH), str(LIVE_DB_PATH))
        chg  = BehaviourChangeEngine(str(OPS_DB_PATH), str(LIVE_DB_PATH))
        sim  = OperatorSimilarityEngine(str(OPS_DB_PATH))
        asm  = AssessmentEngine(beh, chg, sim)
        _engine = ForecastEngine(asm, beh, chg)
    return _engine


def _store_history(operator_id: str, forecast) -> None:
    with _history_lock:
        if operator_id not in _history:
            _history[operator_id] = collections.deque(maxlen=_HISTORY_MAX)
        _history[operator_id].appendleft(forecast)


def _unavailable(operator_id: str = "") -> dict:
    return {
        "ok":          False,
        "unavailable": True,
        "operator_id": operator_id,
        "message":     "Forecast is not available.",
    }


@forecast_bp.route("/api/operators/<operator_id>/forecast")
def operator_forecast(operator_id: str):
    """Current lifecycle forecast for one operator."""
    state = request.args.get("lifecycle_state", "OBSERVING").upper()
    try:
        engine   = _get_engine()
        forecast = engine.forecast(operator_id, state)
        prior    = _latest_history(operator_id)
        _store_history(operator_id, forecast)
        invalidated = None
        if prior is not None and prior.forecast_fingerprint != forecast.forecast_fingerprint:
            stale, reason = prior.is_stale_against(
                current_state          = forecast.current_state,
                assessment_fingerprint = forecast.assessment_fingerprint,
                behaviour_fingerprint  = forecast.behaviour_fingerprint,
                change_fingerprint     = forecast.change_fingerprint,
            )
            if stale:
                invalidated = {"prior_forecast_id": prior.forecast_id, "reason": reason}
        payload: dict = {"ok": True, "forecast": forecast.to_dict()}
        if invalidated is not None:
            payload["invalidated_prior"] = invalidated
        return jsonify(payload)
    except Exception as exc:
        return jsonify({**_unavailable(operator_id), "error": str(type(exc).__name__)}), 200


def _latest_history(operator_id: str):
    with _history_lock:
        dq = _history.get(operator_id)
        return dq[0] if dq else None


@forecast_bp.route("/api/operators/<operator_id>/forecast/history")
def operator_forecast_history(operator_id: str):
    """
    Recent forecast history for one operator (in-memory, last 20).

    Each entry is annotated `current` (still valid against the latest known
    inputs) or `superseded` (a newer entry in this same history invalidated
    it) — computed by fingerprint comparison, never left to guesswork.
    """
    with _history_lock:
        history = list(_history.get(operator_id, []))
    entries = []
    for i, f in enumerate(history):
        d = f.to_dict()
        d["superseded"] = i > 0 and history[0].forecast_fingerprint != f.forecast_fingerprint
        entries.append(d)
    return jsonify({
        "ok":          True,
        "operator_id": operator_id,
        "count":       len(history),
        "history":     entries,
    })


@forecast_bp.route("/api/forecast/platform-summary")
def forecast_platform_summary():
    """Platform-level forecast summary."""
    try:
        id_param    = request.args.get("operator_ids", "")
        state_param = request.args.get("lifecycle_states", "")

        if id_param:
            op_ids = [x.strip() for x in id_param.split(",") if x.strip()][:50]
            states = state_param.split(",") if state_param else ["OBSERVING"] * len(op_ids)
            pairs  = list(zip(op_ids, states))[:50]
        else:
            from src.core.db import OPS_DB_PATH
            from src.ops.forecast_engine import ForecastEngine
            op_ids = ForecastEngine.list_operator_ids(str(OPS_DB_PATH), limit=50)
            pairs  = [(op_id, "OBSERVING") for op_id in op_ids]

        if not pairs:
            return jsonify({"ok": True, "summary": {
                "computed_at": int(time.time()),
                "operators_assessed": 0, "operators_errored": 0,
                "transition_counts": {}, "armed_active_count": 0,
                "armed_active_forecasts": [],
            }})

        engine  = _get_engine()
        summary = engine.platform_summary(pairs)
        return jsonify({"ok": True, "summary": summary})
    except Exception as exc:
        return jsonify({
            "ok": False, "message": "Platform forecast summary unavailable.",
            "error": str(type(exc).__name__),
        }), 200


def register_forecast_routes(app) -> None:
    app.register_blueprint(forecast_bp)
    print("[FORECAST] Lifecycle Forecast registered (/api/operators/<id>/forecast)")
