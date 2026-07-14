"""
Knowledge Layer — HTTP routes.

GET /api/knowledge/entity/<entity_id>

Returns all KnowledgeItems derivable for the given wallet address.
No Flask imported at module level (lazy import inside register function).
"""

from __future__ import annotations


def register_knowledge_routes(app: object) -> None:
    """Register Knowledge Layer routes on a Flask app instance."""
    from flask import jsonify

    from src.knowledge.engine import enrich
    from src.knowledge.rules import REGISTRY

    @app.route("/api/knowledge/entity/<entity_id>")  # type: ignore[attr-defined]
    def knowledge_entity(entity_id: str):
        items = enrich(entity_id)
        return jsonify({
            "entity_id": entity_id,
            "item_count": len(items),
            "items": [item.to_dict() for item in items],
        })

    @app.route("/api/knowledge/rules")  # type: ignore[attr-defined]
    def knowledge_rules():
        return jsonify({
            "rule_count": len(REGISTRY.rules),
            "rules": [
                {
                    "rule_id":     r.rule_id,
                    "category":    r.category,
                    "description": r.description,
                }
                for r in REGISTRY.rules
            ],
        })
