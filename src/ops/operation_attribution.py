"""Authoritative Operation Registry attribution for platform consumers.

This module is deliberately a projection over ``EmergingOperatorService``.
It does not infer identity, apply promotion gates, or persist another ledger.
Consumers may cache their presentation, but operation identity always comes
from the Registry's cached family composition.
"""
from __future__ import annotations

import os
import sqlite3
import time
from typing import Any, Iterable

from src.ops.emerging_operator_service import EmergingOperatorService
from src.ops.watchtower_alignment import WATCHTOWER_OPERATOR_ID


REGISTRY_VERSION = "operation-registry-v1"
_INDEX_CACHE: dict[tuple[str, str], tuple[float, dict[str, dict[str, Any]], dict[str, dict[str, Any]]]] = {}


def clear_operation_attribution_cache(ops_db_path: str | None = None) -> None:
    for key in list(_INDEX_CACHE):
        if ops_db_path is None or key[0] == ops_db_path:
            _INDEX_CACHE.pop(key, None)

_STATE_FOR_STAGE = {
    "CONFIRMED": "CONFIRMED_OPERATION",
    "EMERGING": "EMERGING_OPERATION",
    "ESTABLISHED": "EMERGING_OPERATION",
    "CANDIDATE": "CANDIDATE_FAMILY",
    "SIGNIFICANT_ACTIVE": "CANDIDATE_FAMILY",
    "BACKGROUND": "UNKNOWN",
    "DORMANT": "UNKNOWN",
    "RETIRED": "UNKNOWN",
}


def _confidence(family: dict[str, Any]) -> str:
    if family.get("stage") == "CONFIRMED":
        return "CONFIRMED"
    score = int((family.get("evidence_completeness") or {}).get("score") or 0)
    if score >= 75:
        return "HIGH"
    if score >= 50:
        return "MEDIUM"
    if score:
        return "LOW"
    return "UNKNOWN"


def _assignment(family: dict[str, Any]) -> dict[str, Any]:
    stage = str(family.get("stage") or "BACKGROUND").upper()
    family_id = family.get("family_id")
    name = family.get("family_name") or family_id
    return {
        "operation_id": family.get("canonical_operator_id") or family_id,
        "family_id": family_id,
        "operation_name": name,
        "lifecycle": stage,
        "state": _STATE_FOR_STAGE.get(stage, "UNKNOWN"),
        "evidence_source": "operation_registry",
        "confidence": _confidence(family),
        "registry_version": REGISTRY_VERSION,
        "profile_href": f"/intelligence/operations/{family_id}" if family_id else None,
        "timeline_href": f"/intelligence/operations/{family_id}?tab=timeline" if family_id else None,
        "evidence_href": f"/api/ops/emerging-operators/{family_id}" if family_id else None,
    }


def unknown_assignment() -> dict[str, Any]:
    return {
        "operation_id": None, "family_id": None, "operation_name": "Unknown",
        "lifecycle": "UNKNOWN", "state": "UNKNOWN",
        "evidence_source": "operation_registry", "confidence": "UNKNOWN",
        "registry_version": REGISTRY_VERSION, "profile_href": None,
        "timeline_href": None, "evidence_href": None,
    }


class OperationAttributionService:
    """Resolve token or evidence entity identity solely through the Registry."""

    def __init__(self, ops_db_path: str, live_db_path: str) -> None:
        self.registry = EmergingOperatorService(ops_db_path, live_db_path)

    def _index(self) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        cache_key = (self.registry.ops_db_path, self.registry.live_db_path)
        cached = _INDEX_CACHE.get(cache_key)
        if cached and time.monotonic() - cached[0] < self.registry.refresh_seconds:
            return cached[1], cached[2]
        tokens: dict[str, dict[str, Any]] = {}
        entities: dict[str, dict[str, Any]] = {}
        priority = {"UNKNOWN": 0, "CANDIDATE_FAMILY": 1,
                    "EMERGING_OPERATION": 2, "CONFIRMED_OPERATION": 3}
        for family in self.registry._compose():
            assignment = _assignment(family)
            for mint in family.get("launch_list") or []:
                current = tokens.get(mint)
                if current is None or priority[assignment["state"]] > priority[current["state"]]:
                    tokens[mint] = assignment
            for entity in [family.get("family_id"), *(family.get("member_wallets") or [])]:
                if entity:
                    entities[str(entity)] = assignment
        # Canonical launch membership is itself a Registry source. Minimal
        # installations and old fixtures may not carry enough enrichment
        # tables for the family composer to materialise its canonical card;
        # keep those launches authoritative here instead of falling back in
        # each consumer to WATCHTOWER-specific attribution logic.
        try:
            conn = sqlite3.connect(f"file:{cache_key[0]}?mode=ro", uri=True, timeout=5)
            canonical = next((value for value in tokens.values()
                              if value["state"] == "CONFIRMED_OPERATION"
                              and str(value["operation_name"]).upper() == "WATCHTOWER"), None)
            canonical = canonical or {
                "operation_id": "WATCHTOWER", "family_id": "canonical:WATCHTOWER",
                "operation_name": "WATCHTOWER", "lifecycle": "CONFIRMED",
                "state": "CONFIRMED_OPERATION", "evidence_source": "operation_registry",
                "confidence": "CONFIRMED", "registry_version": REGISTRY_VERSION,
                "profile_href": "/intelligence/operations/canonical:WATCHTOWER",
                "timeline_href": "/intelligence/operations/canonical:WATCHTOWER?tab=timeline",
                "evidence_href": "/api/ops/emerging-operators/canonical:WATCHTOWER",
            }
            has_canonical = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wt_watchtower_launches'"
            ).fetchone()
            if has_canonical:
                for row in conn.execute("SELECT mint FROM wt_watchtower_launches WHERE mint IS NOT NULL"):
                    tokens[str(row[0])] = canonical
            has_outcomes = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wt_attribution_outcomes'"
            ).fetchone()
            if has_outcomes:
                outcome_columns = {row[1] for row in conn.execute(
                    "PRAGMA table_info(wt_attribution_outcomes)"
                )}
                if {"mint", "operator_id"} <= outcome_columns:
                    for row in conn.execute(
                        "SELECT mint FROM wt_attribution_outcomes "
                        "WHERE operator_id=? AND mint IS NOT NULL", (WATCHTOWER_OPERATOR_ID,)
                    ):
                        tokens[str(row[0])] = canonical
            conn.close()
        except (OSError, sqlite3.Error):
            pass
        if os.path.exists(cache_key[0]) and os.path.exists(cache_key[1]):
            _INDEX_CACHE[cache_key] = (time.monotonic(), tokens, entities)
        return tokens, entities

    def resolve_operation_for_token(self, mint: str) -> dict[str, Any]:
        tokens, _ = self._index()
        return self._current_lifecycle(dict(tokens.get(str(mint), unknown_assignment())))

    def resolve_many(self, mints: Iterable[str]) -> dict[str, dict[str, Any]]:
        tokens, _ = self._index()
        result = {str(mint): dict(tokens.get(str(mint), unknown_assignment())) for mint in mints}
        confirmed = self._confirmed_family_ids()
        for assignment in result.values():
            if assignment.get("family_id") in confirmed:
                assignment.update(lifecycle="CONFIRMED", state="CONFIRMED_OPERATION", confidence="CONFIRMED")
        return result

    def resolve_entity(self, entity: str) -> dict[str, Any]:
        tokens, entities = self._index()
        return self._current_lifecycle(dict(tokens.get(str(entity), entities.get(str(entity), unknown_assignment()))))

    def _confirmed_family_ids(self) -> set[str]:
        try:
            conn = sqlite3.connect(f"file:{self.registry.ops_db_path}?mode=ro", uri=True, timeout=5)
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wt_operation_family_confirmations'"
            ).fetchone()
            rows = set()
            if exists:
                rows = {str(row[0]) for row in conn.execute(
                    "SELECT family_id FROM wt_operation_family_confirmations WHERE confirmed=1"
                )}
            conn.close()
            return rows
        except (OSError, sqlite3.Error):
            return set()

    def _current_lifecycle(self, assignment: dict[str, Any]) -> dict[str, Any]:
        if assignment.get("family_id") in self._confirmed_family_ids():
            assignment.update(lifecycle="CONFIRMED", state="CONFIRMED_OPERATION", confidence="CONFIRMED")
        return assignment

    def search(self, query: str, limit: int = 25) -> list[dict[str, Any]]:
        q = str(query or "").strip().lower()
        if not q:
            return []
        results = []
        for family in self.registry._compose():
            assignment = _assignment(family)
            haystack = [family.get("family_id"), family.get("family_name"),
                        *(family.get("member_wallets") or []), *(family.get("launch_list") or [])]
            if any(q in str(value or "").lower() for value in haystack):
                results.append({
                    "id": family["family_id"], "type": "operation_family",
                    "label": assignment["operation_name"], "state": assignment["state"],
                    "operation_attribution": assignment,
                    "href": assignment["profile_href"],
                })
        return results[:max(1, min(int(limit), 50))]
