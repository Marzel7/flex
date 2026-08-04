"""X20 emerging discovery remains deterministic, read-only and governance-bound."""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from flask import Flask

from src.ops.attribution_outcome import ensure_schema
from src.ops.emerging_operator_service import EmergingOperatorService
from src.ops.identity_framework import IdentityEvaluation, IdentityObservation
from src.ops.operator_model import EVIDENCE_IDENTITY


def _digest(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _databases(tmp_path):
    ops_path, live_path = tmp_path / "ops.db", tmp_path / "live.db"
    conn = sqlite3.connect(ops_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE wt_walkback_queue (mint TEXT PRIMARY KEY,creator TEXT,subprov TEXT,"
        "treasury TEXT,funding_mechanism TEXT,funder_amount_sol REAL)"
    )
    conn.execute(
        "CREATE TABLE wt_token_lifecycle (mint TEXT PRIMARY KEY,operation_uuid TEXT,"
        "creator TEXT,subprov TEXT,treasury TEXT)"
    )
    conn.execute("CREATE TABLE operators (operator_id TEXT PRIMARY KEY)")
    ensure_schema(conn)
    conn.commit()
    conn.close()
    sqlite3.connect(live_path).close()
    return ops_path, live_path


def _outcome(conn, mint, entity, completed, *, outcome="UNKNOWN_INFRASTRUCTURE", seed=1):
    conn.execute(
        "INSERT INTO wt_attribution_outcomes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (mint, outcome, "Unexplained persisted infrastructure.", entity, "TREASURY",
         "MEDIUM", '{"source":"persisted"}', None, seed, 0, completed, completed, completed),
    )


def _seed(conn, entity="INFRA"):
    conn.execute(
        "INSERT INTO wt_unknown_infrastructure_registry VALUES (?,?,?,?,?,?,?,?,?,?)",
        (entity, "TREASURY", "M1", "M2", 2, "MEDIUM", "{}", 1, 100, 300),
    )
    _outcome(conn, "M1", entity, 100)
    _outcome(conn, "M2", entity, 300)
    conn.executemany(
        "INSERT INTO wt_walkback_queue VALUES (?,?,?,?,?,?)",
        [("M1", "C1", "S1", entity, "WSOL", 7.0),
         ("M2", "C2", "S1", entity, "WSOL", 7.0)],
    )
    conn.executemany(
        "INSERT INTO wt_token_lifecycle VALUES (?,?,?,?,?)",
        [("M1", "OP1", "C1", "S1", entity), ("M2", "OP2", "C2", "S1", entity)],
    )


class _Resolver:
    def __init__(self, evaluation):
        self.evaluation = evaluation

    def evaluate(self):
        return self.evaluation


def _identity(entity, evidence_type, confidence=0.9):
    return IdentityObservation(
        candidate_key=f"wallet:{entity}", evidence_type=evidence_type,
        category=EVIDENCE_IDENTITY, confidence=confidence,
        reason="Existing X16 identity observation.", source_tables=("existing_x16",),
        entities=(entity,), operations=("OP1", "OP2"), details=(("first_seen", 200),),
    )


def test_registry_accumulates_only_persisted_unknown_outcomes(tmp_path):
    ops, live = _databases(tmp_path)
    conn = sqlite3.connect(ops)
    _seed(conn)
    _outcome(conn, "EXCLUDED", "OTHER", 400, outcome="LINEAGE_GAP", seed=0)
    conn.commit(); conn.close()

    service = EmergingOperatorService(
        str(ops), str(live), resolver_factory=lambda: _Resolver(IdentityEvaluation())
    )
    before = _digest(ops)
    result = service.list()
    assert _digest(ops) == before
    assert result["read_only"] is True
    assert [item["terminal_entity"] for item in result["candidates"]] == ["INFRA"]
    candidate = result["candidates"][0]
    assert candidate["observation_count"] == candidate["observed_launches"] == 2
    assert candidate["unique_creators"] == ["C1", "C2"]
    assert candidate["campaigns"] == ["OP1", "OP2"]
    assert candidate["funding_templates"] == [
        {"mechanism": "WSOL", "amount_sol": 7.0, "observation_count": 2}
    ]
    assert candidate["review_status"] == "MONITORING"
    assert candidate["promotion_handoff"] is None
    assert candidate["is_canonical_operator"] is False
    assert not ({"behaviour", "assessment", "forecast"} & candidate.keys())


def test_growth_is_deterministic_cumulative_and_never_overwritten(tmp_path):
    ops, live = _databases(tmp_path)
    conn = sqlite3.connect(ops); _seed(conn); conn.commit(); conn.close()
    service = EmergingOperatorService(
        str(ops), str(live), resolver_factory=lambda: _Resolver(IdentityEvaluation())
    )
    first = service.list()
    second = service.list()
    assert first == second
    timeline = first["candidates"][0]["growth_timeline"]
    assert [event["observation_count"] for event in timeline] == [1, 2]
    assert [event["source_mint"] for event in timeline] == ["M1", "M2"]


def test_existing_x16_threshold_and_promotion_handoff_are_reused(tmp_path):
    ops, live = _databases(tmp_path)
    conn = sqlite3.connect(ops); _seed(conn); conn.commit(); conn.close()
    one = IdentityEvaluation(identity=(_identity("INFRA", "SHARED_TREASURY_ROOT", 1.0),))
    service = EmergingOperatorService(str(ops), str(live), lambda: _Resolver(one))
    candidate = service.get("INFRA")
    assert candidate["review_status"] == "REVIEW_CANDIDATE"
    assert candidate["identity_classes"] == ["SHARED_TREASURY_ROOT"]
    assert candidate["promotion_handoff"]["requires_analyst_approval"] is True

    two = IdentityEvaluation(identity=(
        _identity("INFRA", "SHARED_TREASURY_ROOT", 1.0),
        _identity("INFRA", "CONFIRMED_INFRASTRUCTURE_REUSE", .85),
    ))
    service = EmergingOperatorService(str(ops), str(live), lambda: _Resolver(two))
    candidate = service.get("INFRA")
    assert candidate["review_status"] == "PROMOTION_ELIGIBLE"
    assert candidate["promotion_handoff"]["href"] == "/intelligence/operator-promotions"
    assert any(e["event_type"] == "PROMOTION_ELIGIBLE" for e in candidate["growth_timeline"])
    conn = sqlite3.connect(ops)
    assert conn.execute("SELECT COUNT(*) FROM operators").fetchone()[0] == 0
    conn.close()


def test_unrelated_x16_candidates_cannot_leak_identity(tmp_path):
    ops, live = _databases(tmp_path)
    conn = sqlite3.connect(ops); _seed(conn); conn.commit(); conn.close()
    evaluation = IdentityEvaluation(identity=(
        _identity("SOME_OTHER_ENTITY", "SHARED_TREASURY_ROOT", 1.0),
        _identity("SOME_OTHER_ENTITY", "CONFIRMED_INFRASTRUCTURE_REUSE", .85),
    ))
    candidate = EmergingOperatorService(
        str(ops), str(live), lambda: _Resolver(evaluation)
    ).get("INFRA")
    assert candidate["review_status"] == "MONITORING"
    assert candidate["identity_classes"] == []


def test_mission_events_are_candidate_growth_not_raw_queue_state(tmp_path):
    ops, live = _databases(tmp_path)
    conn = sqlite3.connect(ops); _seed(conn); conn.commit(); conn.close()
    events = EmergingOperatorService(
        str(ops), str(live), lambda: _Resolver(IdentityEvaluation())
    ).recent_events(10)
    assert [event["kind"] for event in events] == ["EMERGING_CANDIDATE_STRENGTHENED"]
    assert all(event["entity"]["type"] == "emerging_candidate" for event in events)


def test_workspace_api_and_navigation_expose_no_write_action(tmp_path, monkeypatch):
    from src.ops import operator_routes

    ops, live = _databases(tmp_path)
    conn = sqlite3.connect(ops); _seed(conn); conn.commit(); conn.close()
    service = EmergingOperatorService(
        str(ops), str(live), lambda: _Resolver(IdentityEvaluation())
    )
    monkeypatch.setattr(operator_routes, "_emerging_service", service)
    root = Path(__file__).resolve().parents[1]
    app = Flask(__name__, template_folder=str(root / "templates"),
                static_folder=str(root / "static"))
    app.register_blueprint(operator_routes.operator_bp)
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert "/api/ops/emerging-operators" in rules
    assert "/api/ops/emerging-operators/<path:entity>" in rules
    assert "/intelligence/emerging-operators" in rules
    assert "/intelligence/operations" in rules
    assert "/intelligence/operations/<path:entity>" in rules
    assert service.list()["candidates"][0]["terminal_entity"] == "INFRA"
    source = (root / "templates/emerging_operators.html").read_text()
    assert "Emerging Operators" in source
    assert "cannot become canonical operators here" in source
    assert b"/intelligence/operations" in (root / "templates/partials/sidebar.html").read_bytes()


def test_discovery_level_one_distinguishes_emerging_from_canonical(tmp_path):
    from src.discovery.service import DiscoveryService

    ops, live = _databases(tmp_path)
    conn = sqlite3.connect(ops); _seed(conn); conn.commit(); conn.close()
    result = DiscoveryService(str(ops), str(live)).resolve("INFRA", "auto")
    assert result["subject"]["type"] == "emerging_candidate"
    assert result["emerging_candidate"]["current_attribution_outcome"] == "UNKNOWN_INFRASTRUCTURE"
    assert result["emerging_candidate"]["is_canonical_operator"] is False
