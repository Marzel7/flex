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
    try:
        from src.ops.reconciliation_metadata import clear_reconciliation_metadata_cache
        clear_reconciliation_metadata_cache(ops_db_path)
    except Exception:
        pass

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


def _assignment(
    family: dict[str, Any], reconciliation: dict[str, Any] | None = None
) -> dict[str, Any]:
    stage = str(family.get("stage") or "BACKGROUND").upper()
    family_id = family.get("family_id")
    name = family.get("family_name") or family_id
    assignment = {
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
    if reconciliation is not None:
        assignment["reconciliation"] = {
            **reconciliation,
            "dependency_groups": list(reconciliation.get("dependency_groups") or []),
        }
    return assignment


def _copy_assignment(assignment: dict[str, Any]) -> dict[str, Any]:
    result = dict(assignment)
    reconciliation = result.get("reconciliation")
    if reconciliation is not None:
        result["reconciliation"] = {
            **reconciliation,
            "dependency_groups": list(reconciliation.get("dependency_groups") or []),
        }
    return result


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
        families = self.registry._compose()
        try:
            from src.ops.reconciliation_metadata import build_reconciliation_metadata
            reconciliation_by_family = build_reconciliation_metadata(self.registry, families)
        except Exception:
            reconciliation_by_family = {}
        for family in families:
            assignment = _assignment(
                family, reconciliation_by_family.get(str(family.get("family_id")))
            )
            for mint in family.get("launch_list") or []:
                current = tokens.get(mint)
                if current is None or priority[assignment["state"]] > priority[current["state"]]:
                    tokens[mint] = assignment
            for entity in [family.get("family_id"), *(family.get("member_wallets") or [])]:
                if entity:
                    current = entities.get(str(entity))
                    if current is None or priority[assignment["state"]] > priority[current["state"]]:
                        entities[str(entity)] = assignment
        # X73.0 canonical identity lifecycle overlays current infrastructure,
        # launch assignments and merge redirects.  This makes Discovery and
        # Walkback resolve rotations to the living identity immediately,
        # without waiting for a new Investigation Population projection.
        try:
            conn = sqlite3.connect(f"file:{cache_key[0]}?mode=ro", uri=True, timeout=5)
            conn.row_factory = sqlite3.Row
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            redirects = {}
            if "operator_identity_merges" in tables:
                redirects = {str(row[0]): str(row[1]) for row in conn.execute(
                    "SELECT source_operator_id,destination_operator_id FROM operator_identity_merges"
                )}
            operator_rows = {
                str(row["operator_id"]): dict(row)
                for row in conn.execute("SELECT * FROM operators")
            }
            states = {}
            if "operator_identity_state" in tables:
                states = {str(row["operator_id"]): dict(row) for row in conn.execute(
                    "SELECT * FROM operator_identity_state"
                )}

            def destination(operator_id):
                seen = set()
                while operator_id in redirects and operator_id not in seen:
                    seen.add(operator_id)
                    operator_id = redirects[operator_id]
                return operator_id

            def identity_assignment(operator_id):
                operator_id = destination(str(operator_id))
                row = operator_rows.get(operator_id)
                if not row:
                    return None
                state = states.get(operator_id) or {}
                identity_status = str(state.get("identity_status") or row.get("status") or "CONFIRMED")
                activity_status = str(state.get("activity_status") or "ACTIVE")
                return {
                    "operation_id": operator_id,
                    "family_id": f"canonical:{operator_id}",
                    "operation_name": row.get("display_name") or row.get("summary") or operator_id,
                    "lifecycle": identity_status,
                    "identity_status": identity_status,
                    "activity_status": activity_status,
                    # Review/dormancy/retirement never erase historical attribution.
                    "state": "CONFIRMED_OPERATION",
                    "evidence_source": "canonical_operator_identity",
                    "confidence": row.get("confidence") or "CONFIRMED",
                    "registry_version": REGISTRY_VERSION,
                    "profile_href": f"/intelligence/operator/{operator_id}",
                    "timeline_href": f"/intelligence/operator/{operator_id}#timeline",
                    "evidence_href": f"/api/ops/operators/{operator_id}",
                }

            for operator_id in operator_rows:
                assignment = identity_assignment(operator_id)
                if assignment:
                    entities[operator_id] = assignment
                    entities[f"canonical:{operator_id}"] = assignment

            # Canonical family projections retain the originating population
            # and its launch list, but their navigation target must be the
            # living Operator Identity rather than the legacy family profile.
            for family in families:
                canonical_id = family.get("canonical_operator_id")
                if not canonical_id and str(family.get("family_id") or "").startswith("canonical:"):
                    canonical_id = str(family["family_id"]).split(":", 1)[1]
                assignment = identity_assignment(canonical_id) if canonical_id else None
                if assignment:
                    for mint in family.get("launch_list") or []:
                        tokens[str(mint)] = assignment

            for row in conn.execute("SELECT operator_id,entity_address FROM operator_entities"):
                assignment = identity_assignment(row["operator_id"])
                if assignment:
                    entities[str(row["entity_address"])] = assignment
            if "operator_identity_assets" in tables:
                for row in conn.execute(
                    "SELECT operator_id,asset_value FROM operator_identity_assets WHERE status='ACTIVE'"
                ):
                    assignment = identity_assignment(row["operator_id"])
                    if assignment:
                        entities[str(row["asset_value"])] = assignment
            if "operator_launch_membership" in tables:
                for row in conn.execute("SELECT operator_id,mint FROM operator_launch_membership"):
                    assignment = identity_assignment(row["operator_id"])
                    if assignment:
                        tokens[str(row["mint"])] = assignment
            # Historical source IDs remain resolvable after a merge.
            for source_id, destination_id in redirects.items():
                assignment = identity_assignment(destination_id)
                if assignment:
                    entities[source_id] = assignment
                    entities[f"canonical:{source_id}"] = assignment
            conn.close()
        except (OSError, sqlite3.Error):
            pass
        # Canonical launch membership is itself a Registry source. Minimal
        # installations and old fixtures may not carry enough enrichment
        # tables for the family composer to materialise its canonical card;
        # keep those launches authoritative here instead of falling back in
        # each consumer to WATCHTOWER-specific attribution logic.
        try:
            conn = sqlite3.connect(f"file:{cache_key[0]}?mode=ro", uri=True, timeout=5)
            canonical = entities.get(WATCHTOWER_OPERATOR_ID) or next((value for value in tokens.values()
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
            has_outcomes = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wt_attribution_outcomes'"
            ).fetchone()
            if has_canonical:
                for row in conn.execute("SELECT mint FROM wt_watchtower_launches WHERE mint IS NOT NULL"):
                    mint = str(row[0])
                    # Membership alone is not stronger than a completed,
                    # typed walkback conclusion. A launch explicitly ending
                    # without attribution must remain unresolved until an
                    # audited canonical assignment exists.
                    typed = None
                    if has_outcomes:
                        typed = conn.execute(
                            "SELECT outcome_type,operator_id FROM wt_attribution_outcomes WHERE mint=?",
                            (mint,),
                        ).fetchone()
                    if not typed or typed[0] == "CANONICAL_OPERATOR_REACHED" or typed[1]:
                        tokens[mint] = canonical
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
        return self._current_lifecycle(_copy_assignment(tokens.get(str(mint), unknown_assignment())))

    def resolve_many(self, mints: Iterable[str]) -> dict[str, dict[str, Any]]:
        tokens, _ = self._index()
        result = {
            str(mint): _copy_assignment(tokens.get(str(mint), unknown_assignment()))
            for mint in mints
        }
        confirmed = self._confirmed_family_ids()
        for assignment in result.values():
            if assignment.get("family_id") in confirmed:
                assignment.update(lifecycle="CONFIRMED", state="CONFIRMED_OPERATION", confidence="CONFIRMED")
        return result

    def resolve_entity(self, entity: str) -> dict[str, Any]:
        tokens, entities = self._index()
        return self._current_lifecycle(_copy_assignment(
            tokens.get(str(entity), entities.get(str(entity), unknown_assignment()))
        ))

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
        families = self.registry._compose()
        try:
            from src.ops.reconciliation_metadata import build_reconciliation_metadata
            reconciliation_by_family = build_reconciliation_metadata(self.registry, families)
        except Exception:
            reconciliation_by_family = {}
        for family in families:
            assignment = _assignment(
                family, reconciliation_by_family.get(str(family.get("family_id")))
            )
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
