import inspect
import sqlite3
import threading
from pathlib import Path

import pytest

from src.ops.identity_framework import (
    EVIDENCE_CONTEXT,
    EVIDENCE_IDENTITY,
    EVIDENCE_SUPPORTING,
    ContradictionObservation,
    EvidenceObservation,
    IdentityEvaluation,
    IdentityObservation,
    PromotionDecisionEngine,
)
from src.ops.promotion_service import PromotionError, PromotionService
from src.discovery.service import DiscoveryService
from src.ops.operator_similarity import OperatorSimilarityEngine
from src.ops.operator_reader import OperatorReader
from src.ops.operator_writer import OperatorWriter
from src.core.database_write_service import (
    DatabaseWriteService,
    NestedDatabaseWriteError,
    database_write_service,
)


CANDIDATE = "legacy:watchtower"


def initialize_ops_db(path):
    # Production wt_ops_v2.db is already WAL. Test databases opt into the same
    # pre-existing deployment mode before the architecture under test starts.
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
    OperatorWriter(str(path)).initialize_schema()


def evaluation(*, contradiction=False):
    identity = (
        IdentityObservation(
            candidate_key=CANDIDATE,
            evidence_type="CONFIRMED_INFRASTRUCTURE_REUSE",
            category=EVIDENCE_IDENTITY,
            confidence=.85,
            reason="Three infrastructure wallets recur across two operations.",
            source_tables=("known_treasury_hubs", "watchtower_operations"),
            entities=("HUB111", "HUB222", "HUB333"),
            operations=("op-a", "op-b"),
            legacy_source="watchtower_operations",
            legacy_identifier="WATCHTOWER",
        ),
        IdentityObservation(
            candidate_key=CANDIDATE,
            evidence_type="VANITY_ADDRESS_FAMILY",
            category=EVIDENCE_IDENTITY,
            confidence=.75,
            reason="Confirmed infrastructure shares a deliberate prefix.",
            source_tables=("wt_vanity_families",),
            entities=("HUB111", "HUB222"),
            operations=("op-a", "op-b"),
        ),
    )
    supporting = (EvidenceObservation(
        candidate_key=CANDIDATE,
        evidence_type="MATCHING_FUNDING_TEMPLATE",
        category=EVIDENCE_SUPPORTING,
        confidence=.3,
        reason="Operations share a funding template.",
        source_tables=("creator_funders",),
        operations=("op-a", "op-b"),
    ),)
    context = (EvidenceObservation(
        candidate_key=CANDIDATE,
        evidence_type="CHAIN_ACTIVITY",
        category=EVIDENCE_CONTEXT,
        confidence=0,
        reason="The identity chain covers two operations.",
        source_tables=("watchtower_operations",),
        entities=("HUB111", "HUB222", "HUB333"),
        operations=("op-a", "op-b"),
    ),)
    contradictions = ()
    if contradiction:
        contradictions = (ContradictionObservation(
            candidate_key=CANDIDATE,
            reason="The same wallet has conflicting confirmed ownership.",
            source_tables=("operator_entities",),
            related_entities=("HUB111",),
        ),)
    return IdentityEvaluation(identity, supporting, context, contradictions)


class Resolver:
    def __init__(self, current):
        self.current = current

    def evaluate(self):
        return self.current

    def propose(self, current):
        return PromotionDecisionEngine().decide(current)


@pytest.fixture
def service(tmp_path):
    ops = tmp_path / "ops.db"
    live = tmp_path / "live.db"
    live.touch()
    initialize_ops_db(ops)
    current = evaluation()
    calls = []
    svc = PromotionService(
        str(ops), str(live), resolver_factory=lambda: Resolver(current),
        activation=lambda operator_id: calls.append(operator_id) or {"ok": True},
    )
    svc.activation_calls = calls
    return svc


def request_for(proposal, **overrides):
    body = {
        "reviewer": "analyst@example",
        "reason": "Two independent identity classes establish common control.",
        "supporting_notes": "Legacy lineage and context inspected.",
        "proposal_fingerprint": proposal["proposal_fingerprint"],
        "identity_fingerprint": proposal["identity_fingerprint"],
    }
    body.update(overrides)
    return body


def counts(path):
    with sqlite3.connect(path) as conn:
        return {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("operators", "operator_entities", "operator_evidence",
                              "operator_reviews", "operator_promotion_reviews")}


def test_list_exposes_exact_explainable_package(service):
    result = service.list()
    assert result["count"] == 1
    proposal = result["proposals"][0]
    assert proposal["decision"] == "PROMOTION_ELIGIBLE"
    assert proposal["status"] == "PROMOTION_ELIGIBLE"
    assert proposal["evidence_counts"] == {
        "identity": 2, "supporting": 1, "context": 1, "contradictions": 0,
    }
    assert proposal["proposal_id"].startswith("proposal-")
    assert proposal["proposal_fingerprint"] != proposal["identity_fingerprint"]
    assert proposal["legacy_lineage"] == [
        {"source": "watchtower_operations", "identifier": "WATCHTOWER"}
    ]


def test_approve_populates_canonical_model_once_and_activates(service):
    proposal = service.list()["proposals"][0]
    first = service.decide(proposal["proposal_id"], "APPROVE", request_for(proposal))
    assert first["idempotent"] is False
    assert first["downstream_activation"] == {"ok": True}
    assert service.activation_calls == [first["canonical_operator_id"]]
    assert counts(service.ops_db_path) == {
        "operators": 1, "operator_entities": 3, "operator_evidence": 4,
        "operator_reviews": 1, "operator_promotion_reviews": 1,
    }
    with sqlite3.connect(service.ops_db_path) as conn:
        assert conn.execute("SELECT status,confidence,review_state FROM operators").fetchone() == (
            "CONFIRMED", "CERTAIN", "REVIEWED"
        )

    second = service.decide(proposal["proposal_id"], "APPROVE", request_for(proposal))
    assert second["idempotent"] is True
    assert counts(service.ops_db_path)["operators"] == 1
    assert len(service.activation_calls) == 1


@pytest.mark.parametrize("decision", ["REJECT", "DEFER"])
def test_reject_and_defer_are_idempotent_and_noncanonical(service, decision):
    proposal = service.list()["proposals"][0]
    body = request_for(proposal)
    assert service.decide(proposal["proposal_id"], decision, body)["idempotent"] is False
    assert service.decide(proposal["proposal_id"], decision, body)["idempotent"] is True
    result = counts(service.ops_db_path)
    assert result["operator_promotion_reviews"] == 1
    assert result["operators"] == result["operator_entities"] == 0
    assert result["operator_evidence"] == result["operator_reviews"] == 0


def test_stale_proposal_and_identity_are_rejected(service):
    proposal = service.list()["proposals"][0]
    with pytest.raises(PromotionError, match="proposal changed") as stale:
        service.decide(proposal["proposal_id"], "APPROVE",
                       request_for(proposal, proposal_fingerprint="old"))
    assert stale.value.code == "STALE_PROPOSAL"
    with pytest.raises(PromotionError, match="identity evidence changed") as identity:
        service.decide(proposal["proposal_id"], "APPROVE",
                       request_for(proposal, identity_fingerprint="old"))
    assert identity.value.code == "STALE_IDENTITY"
    assert counts(service.ops_db_path)["operators"] == 0


def test_proposal_is_revalidated_inside_serialised_transaction(tmp_path):
    ops, live = tmp_path / "ops.db", tmp_path / "live.db"
    live.touch(); initialize_ops_db(ops)
    initial = evaluation()
    changed = IdentityEvaluation(
        initial.identity,
        (EvidenceObservation(
            candidate_key=CANDIDATE, evidence_type="MATCHING_FUNDING_TEMPLATE",
            category=EVIDENCE_SUPPORTING, confidence=.3,
            reason="A newly materialised funding template changed the package.",
            source_tables=("creator_funders",), operations=("op-a", "op-b"),
        ),),
        initial.context,
        (),
    )
    calls = {"count": 0}
    def factory():
        calls["count"] += 1
        return Resolver(changed if calls["count"] >= 3 else initial)
    svc = PromotionService(str(ops), str(live), resolver_factory=factory)
    proposal = svc.list()["proposals"][0]
    with pytest.raises(PromotionError) as exc:
        svc.decide(proposal["proposal_id"], "APPROVE", request_for(proposal))
    assert exc.value.code == "STALE_PROPOSAL"
    assert all(value == 0 for value in counts(str(ops)).values())


def test_contradiction_blocks_approval(tmp_path):
    ops, live = tmp_path / "ops.db", tmp_path / "live.db"
    live.touch(); initialize_ops_db(ops)
    current = evaluation(contradiction=True)
    svc = PromotionService(str(ops), str(live), resolver_factory=lambda: Resolver(current))
    proposal = svc.list()["proposals"][0]
    assert proposal["decision"] == "REVIEW_REQUIRED"
    with pytest.raises(PromotionError) as exc:
        svc.decide(proposal["proposal_id"], "APPROVE", request_for(proposal))
    assert exc.value.code == "NOT_PROMOTION_ELIGIBLE"


def test_rejected_fingerprint_cannot_later_be_approved(service):
    proposal = service.list()["proposals"][0]
    body = request_for(proposal)
    service.decide(proposal["proposal_id"], "REJECT", body)
    with pytest.raises(PromotionError) as exc:
        service.decide(proposal["proposal_id"], "APPROVE", body)
    assert exc.value.code == "PROPOSAL_REJECTED"
    assert counts(service.ops_db_path)["operators"] == 0


def test_ledger_is_immutable(service):
    proposal = service.list()["proposals"][0]
    service.decide(proposal["proposal_id"], "DEFER", request_for(proposal))
    with sqlite3.connect(service.ops_db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("UPDATE operator_promotion_reviews SET reason='changed'")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("DELETE FROM operator_promotion_reviews")


def test_failure_before_commit_rolls_back_every_table(service):
    proposal = service.list()["proposals"][0]
    service._transaction_hook = lambda _conn: (_ for _ in ()).throw(RuntimeError("stop"))
    with pytest.raises(RuntimeError, match="stop"):
        service.decide(proposal["proposal_id"], "APPROVE", request_for(proposal))
    assert all(value == 0 for value in counts(service.ops_db_path).values())


def test_approval_does_not_modify_legacy_intelligence(service):
    with sqlite3.connect(service.ops_db_path) as conn:
        conn.execute("CREATE TABLE legacy_intelligence(id TEXT PRIMARY KEY, payload TEXT)")
        conn.execute("INSERT INTO legacy_intelligence VALUES('legacy-1','unchanged')")
    proposal = service.list()["proposals"][0]
    service.decide(proposal["proposal_id"], "APPROVE", request_for(proposal))
    with sqlite3.connect(service.ops_db_path) as conn:
        assert conn.execute("SELECT * FROM legacy_intelligence").fetchall() == [
            ("legacy-1", "unchanged")
        ]


def test_required_review_metadata(service):
    proposal = service.list()["proposals"][0]
    with pytest.raises(PromotionError) as exc:
        service.decide(proposal["proposal_id"], "DEFER", request_for(proposal, reviewer=""))
    assert exc.value.code == "REVIEW_METADATA_REQUIRED"


def test_archived_decision_remains_readable_when_proposal_disappears(service):
    proposal = service.list()["proposals"][0]
    service.decide(proposal["proposal_id"], "REJECT", request_for(proposal))
    service._resolver_factory = lambda: Resolver(IdentityEvaluation())
    archived = service.detail(proposal["proposal_id"])
    assert archived["status"] == "REJECTED"
    assert archived["archived"] is True
    assert archived["proposal_fingerprint"] == proposal["proposal_fingerprint"]
    assert service.list()["proposals"][0]["review_history"][0]["reason"]


def test_discovery_and_operator_views_include_promotion_lineage(service):
    proposal = service.list()["proposals"][0]
    result = service.decide(proposal["proposal_id"], "APPROVE", request_for(proposal))
    operator_id = result["canonical_operator_id"]
    operator = OperatorReader(service.ops_db_path).fetch_operator(operator_id)
    assert operator["promotion_history"][0]["proposal_fingerprint"] == proposal["proposal_fingerprint"]
    discovery = DiscoveryService(service.ops_db_path, service.live_db_path)
    resolved = discovery.resolve(operator_id, "operator")
    kinds = [node["kind"] for node in resolved["timeline"]]
    assert "IDENTITY_EVALUATION" in kinds
    assert "ANALYST_REVIEW" in kinds
    assert "CANONICAL_PROMOTION" in kinds
    assert kinds.index("IDENTITY_EVALUATION") < kinds.index("ANALYST_REVIEW") < kinds.index("CANONICAL_PROMOTION")
    assert discovery.recent()["events"][0]["kind"] == "OPERATOR_PROMOTION_REVIEW"


def test_approved_promotion_cannot_be_rejected_or_deferred(service):
    proposal = service.list()["proposals"][0]
    body = request_for(proposal)
    service.decide(proposal["proposal_id"], "APPROVE", body)
    for decision in ("REJECT", "DEFER"):
        with pytest.raises(PromotionError) as exc:
            service.decide(proposal["proposal_id"], decision, body)
        assert exc.value.code == "ALREADY_PROMOTED"


def test_targeted_similarity_activation_is_read_only_and_bounded(tmp_path):
    path = tmp_path / "similarity.db"
    initialize_ops_db(path)
    writer = OperatorWriter(str(path))
    op_id = "test-operator"
    writer.transaction("test-operator-fixture", lambda conn: (
        conn.execute(
            "INSERT INTO operators(operator_id,status,confidence,summary,review_state,display_name,created_at,updated_at) "
            "VALUES(?, 'CONFIRMED', 'CERTAIN', '', 'REVIEWED', 'test', 1, 1)",
            (op_id,),
        ),
        conn.execute(
            "INSERT INTO operator_entities(operator_id,entity_address,entity_type,confidence,evidence_count,added_at) "
            "VALUES(?, 'TREASURY1', 'TREASURY', 'HIGH', 1, 1)",
            (op_id,),
        ),
    ))
    before = counts(str(path))
    snapshot = OperatorSimilarityEngine(str(path)).compute_for_operator(op_id)
    assert snapshot.available is True
    assert snapshot.metrics["activation_operator_id"] == op_id
    assert snapshot.comparisons_attempted == 0
    assert counts(str(path)) == before


def test_canonical_api_and_workspace_navigation(service, monkeypatch):
    from flask import Flask
    from src.ops import operator_routes

    app = Flask(__name__, template_folder=str(Path(__file__).parents[1] / "templates"),
                static_folder=str(Path(__file__).parents[1] / "static"))
    app.testing = True
    monkeypatch.setattr(operator_routes, "_promotion_service", service)
    app.register_blueprint(operator_routes.operator_bp)
    client = app.test_client()

    listed = client.get("/api/operators/promotions")
    assert listed.status_code == 200
    proposal = listed.get_json()["proposals"][0]
    assert client.get(f"/api/operators/promotions/{proposal['proposal_id']}").status_code == 200
    approved = client.post(
        f"/api/operators/promotions/{proposal['proposal_id']}/approve",
        json=request_for(proposal),
    )
    assert approved.status_code == 200
    assert approved.get_json()["canonical_operator_id"]

    page = client.get("/intelligence/operator-promotions")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    for text in ("Operator Promotion Review", "Approve", "Reject", "Defer",
                 "/api/operators/promotions"):
        assert text in html

    # The legacy endpoint can no longer bypass fingerprint-bound governance.
    legacy = client.post("/api/ops/operators/anything/review", json={"decision": "CONFIRMED"})
    assert legacy.status_code == 409
    assert legacy.get_json()["code"] == "PROMOTION_REVIEW_REQUIRED"


def test_concurrent_approvals_serialize_without_duplicates(service):
    proposal = service.list()["proposals"][0]
    barrier = threading.Barrier(3)
    results, errors = [], []

    def approve():
        barrier.wait()
        try:
            results.append(service.decide(
                proposal["proposal_id"], "APPROVE", request_for(proposal)
            ))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=approve) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)

    assert not errors
    assert sorted(result["idempotent"] for result in results) == [False, True]
    assert counts(service.ops_db_path) == {
        "operators": 1, "operator_entities": 3, "operator_evidence": 4,
        "operator_reviews": 1, "operator_promotion_reviews": 1,
    }


def test_promotion_succeeds_with_active_read_snapshot(service):
    proposal = service.list()["proposals"][0]
    reader = sqlite3.connect(
        f"file:{service.ops_db_path}?mode=ro", uri=True, timeout=1
    )
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM operators").fetchone()
    try:
        result = service.decide(
            proposal["proposal_id"], "APPROVE", request_for(proposal)
        )
    finally:
        reader.close()
    assert result["idempotent"] is False


def test_operator_reader_never_initializes_schema(tmp_path):
    path = tmp_path / "operators.db"
    reader = OperatorReader(str(path))
    assert reader.fetch_operator("missing") is None
    assert not path.exists()

    OperatorWriter(str(path)).initialize_schema()
    assert reader.fetch_all_operators() == []


def test_promotion_source_has_no_transaction_ownership():
    source = inspect.getsource(PromotionService)
    assert "BEGIN IMMEDIATE" not in source
    assert "_connect_write" not in source
    assert "sqlite3.connect(self.ops_db_path" not in source


def test_promotion_transaction_is_in_telemetry(service):
    proposal = service.list()["proposals"][0]
    service.decide(proposal["proposal_id"], "DEFER", request_for(proposal))
    records = database_write_service.telemetry(limit=20)
    record = next(row for row in reversed(records)
                  if row["command"] == "operator-promotion-defer")
    assert record["database_path"] == str(Path(service.ops_db_path).resolve())
    assert record["transaction_id"]
    assert record["process_pid"]
    assert record["begin_timestamp"] <= record["commit_timestamp"]
    assert record["rollback"] is False
    assert record["rows_modified"] == 1


def test_approval_uses_one_writer_and_one_connection_for_all_five_tables(tmp_path):
    ops, live = tmp_path / "ops.db", tmp_path / "live.db"
    live.touch(); initialize_ops_db(ops)
    managed = DatabaseWriteService()
    submissions = []
    table_connections = {}

    class ConnectionAudit:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, parameters=()):
            normalised = " ".join(sql.upper().split())
            for table in (
                "OPERATORS", "OPERATOR_ENTITIES", "OPERATOR_EVIDENCE",
                "OPERATOR_REVIEWS", "OPERATOR_PROMOTION_REVIEWS",
            ):
                if normalised.startswith(f"INSERT INTO {table}"):
                    table_connections.setdefault(table, set()).add(id(self._conn))
            return self._conn.execute(sql, parameters)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    class WriteAudit:
        def register_database(self, database, path):
            managed.register_database(database, path)

        def submit(self, database, command, transaction):
            submissions.append((database, command))
            return managed.submit(
                database, command, lambda conn: transaction(ConnectionAudit(conn))
            )

    current = evaluation()
    svc = PromotionService(
        str(ops), str(live), resolver_factory=lambda: Resolver(current),
        activation=lambda _operator_id: {"ok": True}, write_service=WriteAudit(),
    )
    proposal = svc.list()["proposals"][0]
    svc.decide(proposal["proposal_id"], "APPROVE", request_for(proposal))

    assert submissions == [(svc._write_database, "operator-promotion-approve")]
    assert set(table_connections) == {
        "OPERATORS", "OPERATOR_ENTITIES", "OPERATOR_EVIDENCE",
        "OPERATOR_REVIEWS", "OPERATOR_PROMOTION_REVIEWS",
    }
    assert len({connection_id for ids in table_connections.values()
                for connection_id in ids}) == 1


def test_nested_writer_from_promotion_callback_fails_and_rolls_back(tmp_path):
    ops, live = tmp_path / "ops.db", tmp_path / "live.db"
    live.touch(); initialize_ops_db(ops)
    managed = DatabaseWriteService()
    current = evaluation()
    svc = PromotionService(
        str(ops), str(live), resolver_factory=lambda: Resolver(current),
        activation=lambda _operator_id: {"ok": True}, write_service=managed,
    )
    svc._transaction_hook = lambda _conn: managed.submit(
        svc._write_database, "nested-governance-persistence", lambda conn: None
    )
    proposal = svc.list()["proposals"][0]

    with pytest.raises(NestedDatabaseWriteError) as exc:
        svc.decide(proposal["proposal_id"], "APPROVE", request_for(proposal))
    assert exc.value.outer_command == "operator-promotion-approve"
    assert exc.value.inner_command == "nested-governance-persistence"
    assert all(value == 0 for value in counts(str(ops)).values())
