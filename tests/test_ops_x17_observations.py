"""Sprint X17 contracts for canonical operator observation materialization."""
from __future__ import annotations

import sqlite3
import socket
import time
from types import SimpleNamespace

import pytest

from src.core.database_write_service import DatabaseWriteService
from src.ops.behaviour_engine import BehaviourEngine, DIMENSION_OBSERVATION_TYPES
from src.ops.observation_materializer import ObservationMaterializationPipeline, ObservationMaterializer
from src.ops.observation_store import ObservationStore
from src.ops.operator_observation import OperatorObservation
from src.ops.operator_writer import OperatorWriter


OPERATOR_ID = "operator-x17"


def _database(tmp_path):
    path = str(tmp_path / "operations.db")
    service = DatabaseWriteService()
    writer = OperatorWriter(path, write_service=service)
    writer.initialize_schema()
    now = int(time.time())
    writer.transaction("fixture-operator", lambda conn: (
        conn.execute(
            "INSERT INTO operators(operator_id,status,confidence,review_state,created_at,updated_at) "
            "VALUES(?, 'CONFIRMED', 'HIGH', 'REVIEWED', ?, ?)",
            (OPERATOR_ID, now, now),
        ),
        conn.execute(
            "INSERT INTO operator_entities(operator_id,entity_address,entity_type,confidence,"
            "evidence_count,added_at) VALUES(?, 'entity-a', 'TREASURY', 'HIGH', 1, ?)",
            (OPERATOR_ID, now),
        ),
    ))
    store = ObservationStore(path, write_service=service)
    store.initialize_schema()
    return path, service, store


def _observation(kind="LAUNCH", *, index=1, timestamp=100):
    return OperatorObservation(
        operator_id=OPERATOR_ID,
        observation_type=kind,
        entity=f"entity-{index}",
        timestamp=timestamp + index,
        source="test:local",
        confidence=0.9,
        provenance={"table": "fixture", "record_key": index},
        metadata={
            "create_time": timestamp + index,
            "creator_wallet": f"creator-{index}",
            "operation_uuid": f"campaign-{index}",
            "first_seen": timestamp + index,
            "last_seen": timestamp + index + 10,
            "wallet": f"entity-{index}",
            "role": "RELAY",
            "migrated": kind == "MIGRATION",
        },
    )


class _Provider:
    def __init__(self, name, observations):
        self.name = name
        self.observations = observations

    def materialize(self, operator_id, entities):
        assert operator_id == OPERATOR_ID
        assert entities[0]["entity_address"] == "entity-a"
        return list(self.observations)


class _MissingProvider:
    name = "missing_source"

    def materialize(self, operator_id, entities):
        raise sqlite3.OperationalError("no such table")


def test_observation_id_is_deterministic_and_confidence_is_bounded():
    first = _observation()
    second = _observation()
    assert first.observation_id == second.observation_id
    assert OperatorObservation(**{**first.to_dict(), "confidence": 4}).confidence == 1.0
    assert _observation("FUTURE_PROVIDER_EVENT").observation_type == "FUTURE_PROVIDER_EVENT"


@pytest.mark.parametrize("kind", ["LAUNCH", "BUY", "RELAY", "TREASURY", "COORDINATION"])
def test_archetype_provider_shapes_are_supported(kind):
    observation = _observation(kind)
    assert observation.observation_type == kind
    assert observation.entity
    assert observation.provenance["record_key"] == 1


@pytest.mark.parametrize("archetype,kinds", [
    ("launch_operator", {"LAUNCH", "CREATOR", "MIGRATION"}),
    ("buy_swarm", {"BUY", "COORDINATION"}),
    ("relay_network", {"RELAY", "INFRASTRUCTURE"}),
    ("treasury_only", {"TREASURY", "FUNDING"}),
    ("launcher", {"LAUNCH", "CREATOR"}),
    ("mixed_infrastructure", {"TREASURY", "RELAY", "BUY", "LAUNCH"}),
])
def test_operator_archetypes_keep_their_distinct_observation_sets(archetype, kinds):
    rows = [_observation(kind, index=index) for index, kind in enumerate(sorted(kinds), 1)]
    assert {row.observation_type for row in rows} == kinds, archetype


def test_materializer_is_deterministic_and_missing_provider_isolated(tmp_path):
    path, _, _ = _database(tmp_path)
    rows = [_observation("LAUNCH", index=2), _observation("LAUNCH", index=1)]
    materializer = ObservationMaterializer(
        path, path, providers=[_MissingProvider(), _Provider("launches", rows + [rows[0]])]
    )
    first, counts = materializer.materialize(OPERATOR_ID)
    second, _ = materializer.materialize(OPERATOR_ID)
    assert [row.observation_id for row in first] == [row.observation_id for row in second]
    assert len(first) == 2
    assert counts == {"missing_source": 0, "launches": 3}


def test_default_materializer_is_read_only_and_never_opens_network(tmp_path, monkeypatch):
    path, _, _ = _database(tmp_path)
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("network used"))
    observations, _ = ObservationMaterializer(path, path).materialize(OPERATOR_ID)
    assert observations
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM operator_observations").fetchone()[0] == 0
    finally:
        conn.close()


def test_pipeline_is_idempotent_and_uses_one_managed_persist(tmp_path):
    path, service, store = _database(tmp_path)
    provider = _Provider("launches", [_observation(index=i) for i in range(1, 4)])
    pipeline = ObservationMaterializationPipeline(
        path, path, providers=[provider], write_service=service
    )
    before = len(service.telemetry())
    assert pipeline.run(OPERATOR_ID)["observation_count"] == 3
    assert pipeline.run(OPERATOR_ID)["observation_count"] == 3
    commands = [entry for entry in service.telemetry()[before:]
                if entry.get("command") == "operator-observation-materialize"]
    assert len(commands) == 2
    assert len(store.fetch(OPERATOR_ID)) == 3


def test_observation_persist_rolls_back_run_and_rows_together(tmp_path):
    path, service, store = _database(tmp_path)
    writer = OperatorWriter(path, write_service=service)
    writer.transaction("fixture-trigger", lambda conn: conn.execute(
        "CREATE TRIGGER reject_observation BEFORE INSERT ON operator_observations "
        "BEGIN SELECT RAISE(ABORT, 'forced failure'); END"
    ))
    with pytest.raises(sqlite3.IntegrityError, match="forced failure"):
        store.persist(OPERATOR_ID, [_observation()], {"fixture": 1})
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM operator_observations").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM operator_observation_runs").fetchone()[0] == 0
    finally:
        conn.close()


def test_behaviour_reads_observation_table_without_source_feature_tables(tmp_path):
    path, _, store = _database(tmp_path)
    rows = [_observation("LAUNCH", index=i) for i in range(1, 4)]
    store.persist(OPERATOR_ID, rows, {"launches": 3})
    profile = BehaviourEngine(path, path).compute(OPERATOR_ID)
    launch = next(dimension for dimension in profile.dimensions if dimension.key == "launch")
    assert profile.total_observations == 3
    assert next(fact for fact in launch.facts if fact.key == "observed_launches").raw == 3


def test_every_behaviour_dimension_declares_observation_types():
    assert set(DIMENSION_OBSERVATION_TYPES) == {
        "campaign", "funding", "launch", "operational", "outcome"
    }
    assert all(types for types in DIMENSION_OBSERVATION_TYPES.values())
    assert DIMENSION_OBSERVATION_TYPES["launch"] >= {"LAUNCH", "MIGRATION"}


def test_promotion_post_processing_materializes_before_downstream(monkeypatch):
    from src.ops.promotion_service import PromotionService
    import src.ops.assessment_routes as assessment_routes
    import src.ops.behaviour_change_routes as change_routes
    import src.ops.behaviour_routes as behaviour_routes
    import src.ops.forecast_routes as forecast_routes
    import src.ops.observation_materializer as materializer_module
    import src.ops.similarity_routes as similarity_routes

    calls = []

    class Pipeline:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, operator_id):
            calls.append("materialize")
            return {"status": "READY", "observation_count": 1}

    monkeypatch.setattr(materializer_module, "ObservationMaterializationPipeline", Pipeline)
    monkeypatch.setattr(behaviour_routes, "_get_engine", lambda: SimpleNamespace(
        compute=lambda operator_id: calls.append("behaviour") or SimpleNamespace(fingerprint="b")
    ))
    monkeypatch.setattr(change_routes, "_get_engine", lambda: SimpleNamespace(
        compare=lambda operator_id: calls.append("change") or SimpleNamespace(fingerprint="c")
    ))
    monkeypatch.setattr(similarity_routes, "_get_engine", lambda: SimpleNamespace(
        compute_for_operator=lambda operator_id: calls.append("similarity") or SimpleNamespace(
            available=True, comparisons_attempted=0
        )
    ))
    monkeypatch.setattr(assessment_routes, "_get_engine", lambda: SimpleNamespace(
        assess=lambda operator_id: calls.append("assessment") or SimpleNamespace(fingerprint="a")
    ))
    monkeypatch.setattr(forecast_routes, "_get_engine", lambda: SimpleNamespace(
        forecast=lambda operator_id, lifecycle: calls.append("forecast") or SimpleNamespace(
            forecast_fingerprint="f"
        )
    ))
    monkeypatch.setattr(forecast_routes, "_store_history", lambda *args: calls.append("history"))

    result = PromotionService._activate_downstream(OPERATOR_ID)
    assert result["ok"] is True
    assert calls == [
        "materialize", "behaviour", "change", "similarity",
        "assessment", "forecast", "history",
    ]
