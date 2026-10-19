"""
Cross-Operation Intelligence API routes.

GET /api/intelligence/entity/<entity_id>/relationships
GET /api/intelligence/cross-operation/stats
"""
from __future__ import annotations

import dataclasses


def register_cross_operation_routes(app) -> None:
    from flask import Blueprint, jsonify

    from src.cross_operation.service import entity_relationships, global_stats

    bp = Blueprint("cross_operation", __name__)

    @bp.route("/api/intelligence/entity/<entity_id>/relationships")
    def entity_rels(entity_id: str):
        overlap = entity_relationships(entity_id)
        return jsonify({
            "entity_id":       overlap.entity_id,
            "observed_by":     overlap.observed_by,
            "operation_count": overlap.operation_count,
            "relationships": [
                {
                    "relationship_id":       r.relationship_id,
                    "entity_a":              r.entity_a,
                    "entity_b":              r.entity_b,
                    "relationship_type":     r.relationship_type,
                    "confidence":            r.confidence,
                    "supporting_operations": list(r.supporting_operations),
                    "supporting_evidence":   list(r.supporting_evidence),
                    "first_seen":            r.first_seen,
                    "last_seen":             r.last_seen,
                    "provenance":            r.provenance,
                    "metadata":              r.metadata,
                }
                for r in overlap.relationships
            ],
            "unified_timeline": overlap.unified_timeline,
            "generated_at":    overlap.generated_at,
        })

    @bp.route("/api/intelligence/cross-operation/stats")
    def cross_op_stats():
        stats = global_stats()
        return jsonify(dataclasses.asdict(stats))

    app.register_blueprint(bp)
