"""X69.3 developer-only validation workspace acceptance tests."""
from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from src.ops.reconciliation_diagnostics import (
    EXPECTED_DIFFERENCE,
    MATCH,
    ReconciliationDiagnosticsService,
    record_detail,
)
from src.ops.reconciliation_diagnostics_routes import (
    reconciliation_diagnostics_bp,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def workspace():
    return ReconciliationDiagnosticsService(
        str(ROOT / "database" / "wt_ops_v2.db"),
        str(ROOT / "database" / "flex_complete_database.db"),
    ).build()


def _named(workspace, fragment):
    return next(
        record for record in workspace.records
        if fragment in record.legacy_projection.get("family_name", "")
    )


def test_live_shadow_metrics_and_replays_are_clean(workspace):
    assert workspace.metrics == {
        "total_investigation_populations": 281,
        "total_shadow_records": 282,
        "agreement_count": 192,
        "expected_differences": 90,
        "unexpected_differences": 0,
        "infrastructure_populations": 9,
        "rejected_populations": 9,
        "review_populations": 4,
        "operator_candidates": 0,
        "confirmed_operations": 1,
        "retired_populations": 0,
        "unresolved_populations": 259,
        "deterministic_replay_failures": 0,
    }
    assert all(record.replay.identical for record in workspace.records)


def test_required_named_population_outcomes(workspace):
    watchtower = _named(workspace, "WATCHTOWER")
    b48 = _named(workspace, "B48k")
    c7 = _named(workspace, "C7Ha")

    assert (watchtower.legacy_projection["stage"], watchtower.disposition.disposition) == (
        "CONFIRMED", "CONFIRMED_OPERATION"
    )
    assert watchtower.difference.classification == MATCH
    assert (b48.legacy_projection["stage"], b48.disposition.disposition) == (
        "CONFIRMED", "UNRESOLVED"
    )
    assert b48.difference.classification == EXPECTED_DIFFERENCE
    assert "control-bearing evidence" in b48.difference.explanation
    assert (c7.legacy_projection["stage"], c7.disposition.disposition) == (
        "EMERGING", "REVIEW"
    )
    assert c7.difference.classification == EXPECTED_DIFFERENCE


def test_infrastructure_and_background_controls(workspace):
    infrastructure = [r for r in workspace.records if r.disposition.disposition == "INFRASTRUCTURE"]
    assert infrastructure
    assert all(r.disposition.contradictory_evidence for r in infrastructure)
    random_background = next(
        r for r in workspace.records if r.legacy_projection["stage"] == "BACKGROUND"
    )
    assert random_background.disposition.disposition == "UNRESOLVED"


def test_complete_package_is_inspectable(workspace):
    detail = record_detail(workspace.records[0])
    assert set(detail["evidence_package"]) >= {
        "population", "supporting_evidence", "contradictory_evidence", "context",
        "missing_evidence", "dependency_groups", "explainability", "provenance",
        "disposition", "package_id",
    }
    assert set(detail["disposition"]) >= {
        "disposition", "reasoning_chain", "supporting_evidence",
        "contradictory_evidence", "missing_evidence",
        "dependency_groups_consulted", "result_id",
    }


@pytest.fixture
def diagnostic_app(monkeypatch, workspace):
    app = Flask(__name__, template_folder=str(ROOT / "templates"))
    app.testing = True
    app.register_blueprint(reconciliation_diagnostics_bp)
    monkeypatch.setattr(
        "src.ops.reconciliation_diagnostics_routes._workspace", lambda: workspace
    )
    return app


def test_page_data_detail_and_replay_routes(diagnostic_app, workspace):
    client = diagnostic_app.test_client()
    page = client.get("/diagnostics/reconciliation")
    assert page.status_code == 200
    assert b"DEVELOPER ONLY" in page.data
    assert page.headers["Cache-Control"] == "no-store, private"

    data = client.get("/diagnostics/reconciliation/data")
    assert data.status_code == 200
    assert data.json["metrics"]["unexpected_differences"] == 0
    record = workspace.records[0]
    encoded_id = record.population.population_id.replace("/", "%2F")
    detail = client.get(f"/diagnostics/reconciliation/population/{encoded_id}")
    assert detail.status_code == 200
    replay = client.post(
        f"/diagnostics/reconciliation/replay/{encoded_id}",
        query_string={"revision": record.population.revision_id},
    )
    assert replay.status_code == 200
    assert replay.json["replay"]["identical"] is True
    stale = client.post(
        f"/diagnostics/reconciliation/replay/{encoded_id}",
        query_string={"revision": "ipr:stale"},
    )
    assert stale.status_code == 409


def test_access_is_hidden_when_disabled_and_restricted_when_remote(monkeypatch):
    app = Flask(__name__, template_folder=str(ROOT / "templates"))
    app.register_blueprint(reconciliation_diagnostics_bp)
    monkeypatch.delenv("RECONCILIATION_DIAGNOSTICS_ENABLED", raising=False)
    assert app.test_client().get("/diagnostics/reconciliation").status_code == 404

    monkeypatch.setenv("RECONCILIATION_DIAGNOSTICS_ENABLED", "1")
    assert app.test_client().get(
        "/diagnostics/reconciliation", environ_base={"REMOTE_ADDR": "10.0.0.8"}
    ).status_code == 403
    monkeypatch.setenv("RECONCILIATION_DIAGNOSTICS_TOKEN", "secret")
    assert app.test_client().get(
        "/diagnostics/reconciliation",
        environ_base={"REMOTE_ADDR": "10.0.0.8"},
        headers={"X-Reconciliation-Diagnostics-Token": "secret"},
    ).status_code == 200


def test_workspace_is_not_linked_from_normal_navigation():
    for path in (ROOT / "templates").glob("*.html"):
        if path.name != "reconciliation_diagnostics.html":
            assert "/diagnostics/reconciliation" not in path.read_text(errors="ignore")


def test_shadow_resolver_has_no_production_consumer_imports():
    # X69.4 adds one presentation-only projection; no attribution consumer may
    # import the resolver directly.
    allowed = {"reconciliation_diagnostics.py", "reconciliation_metadata.py"}
    offenders = []
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text(errors="ignore")
        if "from src.ops.disposition_resolver import" in text and path.name not in allowed:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
