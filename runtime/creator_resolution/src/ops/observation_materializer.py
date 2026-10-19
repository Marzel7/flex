"""Deterministic read-only materialization plus an explicit persistence pipeline."""
from __future__ import annotations

import sqlite3

from src.ops.observation_providers import ObservationProvider, default_observation_providers
from src.ops.observation_store import ObservationStore
from src.ops.operator_observation import OperatorObservation
from src.ops.operator_reader import OperatorReader


class ObservationMaterializer:
    """Reads identity and existing intelligence; never persists or detects anything."""

    def __init__(self, ops_db: str, live_db: str,
                 providers: list[ObservationProvider] | None = None) -> None:
        self._reader = OperatorReader(ops_db)
        self.providers = providers or default_observation_providers(ops_db, live_db)

    def materialize(self, operator_id: str) -> tuple[list[OperatorObservation], dict[str, int]]:
        operator = self._reader.fetch_operator(operator_id)
        if not operator:
            raise ValueError(f"Canonical operator not found: {operator_id}")
        entities = list(operator.get("entities", []))
        combined: dict[str, OperatorObservation] = {}
        counts: dict[str, int] = {}
        for provider in self.providers:
            try:
                rows = provider.materialize(operator_id, entities)
            except (sqlite3.Error, OSError, KeyError, TypeError, ValueError):
                rows = []
            counts[provider.name] = len(rows)
            for observation in rows:
                combined[observation.observation_id] = observation
        observations = sorted(
            combined.values(), key=lambda o: (o.timestamp, o.observation_type, o.observation_id)
        )
        return observations, counts


class ObservationMaterializationPipeline:
    def __init__(self, ops_db: str, live_db: str, *, providers=None, write_service=None) -> None:
        self.materializer = ObservationMaterializer(ops_db, live_db, providers)
        self.store = ObservationStore(ops_db, write_service=write_service)

    def run(self, operator_id: str) -> dict:
        observations, counts = self.materializer.materialize(operator_id)
        return self.store.persist(operator_id, observations, counts)
