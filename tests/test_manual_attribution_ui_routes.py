from flask import Flask

from src.ops.operator_routes import operator_bp


NEXUS_CANDIDATE = "p3r-v2-6437acd385e566e301a7"


def _client(tmp_path):
    app = Flask(__name__)
    app.config["OPS_DB_PATH"] = str(tmp_path / "ops.db")
    app.config["MANUAL_ATTRIBUTION_WORKFLOW_DB_PATH"] = str(tmp_path / "manual-workflow.db")
    app.register_blueprint(operator_bp)
    return app.test_client()


def test_validate_route_accepts_only_path_identity_and_is_idempotent(tmp_path):
    client = _client(tmp_path)
    route = f"/potential-operations/{NEXUS_CANDIDATE}/validate"
    assert client.get(route).status_code == 405
    first = client.post(route, json={"families": [{"state": "PROVEN_STRONG"}]} )
    second = client.post(route)
    assert first.status_code == second.status_code == 200
    assert first.json["validation_state"] == second.json["validation_state"] == "ADDITIONAL_EVIDENCE_REQUIRED"
    assert first.json["proposal_id"] == second.json["proposal_id"]


def test_nexus_promotion_route_rejects_unproven_proposal(tmp_path):
    client = _client(tmp_path)
    validated = client.post(f"/potential-operations/{NEXUS_CANDIDATE}/validate").json
    response = client.post(f"/potential-operations/proposals/{validated['proposal_id']}/promote")
    assert response.status_code == 409
    assert "ATTRIBUTION_PROVEN" in response.json["error"]
