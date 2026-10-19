"""
Operator Similarity API routes — Sprint X12.

All routes read from the pre-computed in-process SimilaritySnapshot.
No route triggers unbounded pairwise computation on page load.

Endpoints:
  GET /api/operators/<id>/similarity           — top similar operators
  GET /api/operators/<id>/similarity/<other>   — explainable pair comparison
  GET /api/operators/similarity/summary        — cached platform summary
  POST /api/operators/similarity/refresh       — manually trigger refresh (background thread)
"""

from __future__ import annotations

import threading

from flask import Blueprint, jsonify, request

from src.ops.operator_similarity import (
    MAX_RESULTS_PER_OP,
    OperatorSimilarityEngine,
    SimilaritySnapshot,
    _EMPTY_SNAPSHOT,
)

similarity_bp = Blueprint("similarity", __name__)

_engine: OperatorSimilarityEngine | None = None
_refresh_lock = threading.Lock()


def _get_engine() -> OperatorSimilarityEngine:
    global _engine
    if _engine is None:
        from src.core.db import OPS_DB_PATH
        _engine = OperatorSimilarityEngine(str(OPS_DB_PATH))
    return _engine


def _unavailable() -> dict:
    return {
        "ok":          False,
        "unavailable": True,
        "message":     "Similarity snapshot not yet computed. "
                       "POST /api/operators/similarity/refresh to trigger.",
    }


@similarity_bp.route("/api/operators/<operator_id>/similarity")
def operator_similarity(operator_id: str):
    """
    Top similar operators for a given operator.

    Query params:
      limit          — max results (default MAX_RESULTS_PER_OP)
      minimum_band   — VERY_HIGH | HIGH | MODERATE | LOW (default: all)
    """
    snap = _get_engine().current_snapshot()
    if not snap.available:
        return jsonify(_unavailable()), 200  # not a 503 — detection must not be impacted

    limit = min(int(request.args.get("limit", MAX_RESULTS_PER_OP)), MAX_RESULTS_PER_OP)
    min_band = request.args.get("minimum_band", "").upper()
    band_order = ["LOW", "MODERATE", "HIGH", "VERY_HIGH"]
    min_idx = band_order.index(min_band) if min_band in band_order else 0

    results = snap.for_operator(operator_id)
    results = [
        r for r in results
        if r.similarity_band in band_order
        and band_order.index(r.similarity_band) >= min_idx
    ][:limit]

    return jsonify({
        "ok":          True,
        "operator_id": operator_id,
        "computed_at": snap.computed_at,
        "count":       len(results),
        "results":     [r.to_dict() for r in results],
    })


@similarity_bp.route("/api/operators/<operator_id>/similarity/<other_operator_id>")
def operator_similarity_pair(operator_id: str, other_operator_id: str):
    """Full explainable comparison between two specific operators."""
    engine = _get_engine()
    result = engine.compare_pair(operator_id, other_operator_id)

    if result is None:
        snap = engine.current_snapshot()
        if not snap.available:
            return jsonify(_unavailable()), 200
        return jsonify({
            "ok":            False,
            "operator_a":    operator_id,
            "operator_b":    other_operator_id,
            "message":       "No similarity result found for this pair. "
                             "They may have been pruned (low similarity) "
                             "or the snapshot may need refreshing.",
        })

    return jsonify({"ok": True, "comparison": result.to_dict()})


@similarity_bp.route("/api/operators/similarity/summary")
def similarity_summary():
    """
    Platform-level similarity summary.

    Always reads from the cached snapshot — never triggers computation.
    """
    snap = _get_engine().current_snapshot()
    if not snap.available:
        return jsonify({**_unavailable(), "snapshot": snap.to_summary_dict()})

    return jsonify({
        "ok":      True,
        "summary": snap.to_summary_dict(),
    })


@similarity_bp.route("/api/operators/similarity/refresh", methods=["POST"])
def trigger_refresh():
    """
    Manually trigger a similarity refresh in a background thread.

    The request returns immediately. The computation runs off the hot path.
    The next call to /similarity will read the updated snapshot.
    """
    engine = _get_engine()

    if not _refresh_lock.acquire(blocking=False):
        return jsonify({"ok": False, "message": "Refresh already in progress."}), 200

    def _run():
        try:
            engine.compute_snapshot()
        finally:
            _refresh_lock.release()

    t = threading.Thread(target=_run, daemon=True, name="similarity-refresh")
    t.start()

    return jsonify({"ok": True, "message": "Similarity refresh started in background."})


def register_similarity_routes(app) -> None:
    app.register_blueprint(similarity_bp)
    print("[SIMILARITY] Operator Similarity registered (/api/operators/<id>/similarity)")
