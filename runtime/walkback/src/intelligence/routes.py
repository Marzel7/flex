"""
Entity Intelligence — HTTP routes.

GET /api/intelligence/entity/<entity_id>

No Flask imported at module level (lazy import inside register function).
Everything computed on request. No persistence.
"""

from __future__ import annotations


def register_intelligence_routes(app: object) -> None:
    """Register Entity Intelligence routes on a Flask app instance."""
    from flask import jsonify, request

    from src.intelligence.service import resolve

    @app.route("/api/intelligence/entity/<entity_id>")  # type: ignore[attr-defined]
    def intelligence_entity(entity_id: str):
        summary = resolve(entity_id)
        return jsonify(summary.to_dict())

    @app.route("/api/intelligence/entity/<entity_id>/summary")  # type: ignore[attr-defined]
    def intelligence_entity_summary(entity_id: str):
        """Lightweight endpoint returning only the summary sentences and confidence."""
        summary = resolve(entity_id)
        return jsonify({
            "entity_id":         summary.entity_id,
            "entity_type":       summary.entity_type,
            "summary_sentences": summary.summary_sentences,
            "confidence":        summary.confidence.to_dict(),
            "first_seen":        summary.first_seen,
            "last_seen":         summary.last_seen,
            "generated_at":      summary.generated_at,
        })
