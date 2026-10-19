from __future__ import annotations

from flask import Blueprint, Flask, jsonify

from .config import EvidenceConfig
from .service import EvidencePlatform


def create_evidence_health_blueprint(platform: EvidencePlatform) -> Blueprint:
    """Opt-in blueprint. EP1.0 does not register it with the production app."""
    blueprint = Blueprint("evidence_health", __name__)

    @blueprint.get("/api/evidence/health")
    def evidence_health():
        if not platform.config.health_enabled:
            return jsonify({"status": "DISABLED"}), 404
        payload = platform.health()
        return jsonify(payload), 200 if payload["status"] in {"HEALTHY", "DISABLED"} else 503

    @blueprint.get("/api/evidence/metrics")
    def evidence_metrics():
        if not platform.config.health_enabled:
            return jsonify({"status": "DISABLED"}), 404
        return jsonify({"status": "OK", "metrics": platform.metrics.snapshot()})

    return blueprint


def create_evidence_health_app(config: EvidenceConfig | None = None) -> Flask:
    """Standalone health application; never imported by the production app."""
    platform = EvidencePlatform(config or EvidenceConfig.from_env())
    app = Flask("evidence_health")
    app.register_blueprint(create_evidence_health_blueprint(platform))
    return app


def main() -> int:
    import os
    config = EvidenceConfig.from_env()
    if not (config.platform_enabled and config.health_enabled):
        return 0
    create_evidence_health_app(config).run(
        host=os.environ.get("EVIDENCE_HEALTH_HOST", "127.0.0.1"),
        port=int(os.environ.get("EVIDENCE_HEALTH_PORT", "5012")),
        debug=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
