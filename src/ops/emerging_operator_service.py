"""Read-only X20 emerging-operator projection.

The registry deliberately owns no persistence.  Its membership comes exclusively
from the X19.7 seed contract, while identity and promotion state come exclusively
from the X16 evaluator.  All history is reconstructed from immutable, persisted
observations so opening this workspace cannot create intelligence.
"""
from __future__ import annotations

import json
import os
import sqlite3
from collections import defaultdict
from typing import Any, Callable

from src.ops.attribution_outcome import emerging_operator_seeds
from src.ops.identity_framework import PromotionDecisionEngine
from src.ops.operator_resolver import OperatorResolver


_DECISION_RANK = {
    "PROMOTION_ELIGIBLE": 4,
    "REVIEW_REQUIRED": 3,
    "REVIEW_CANDIDATE": 2,
    "INSUFFICIENT": 1,
}


def _json(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _timestamp(value: Any) -> int | None:
    try:
        return int(float(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


class EmergingOperatorService:
    """Compose explainable candidates without opening a write-capable connection."""

    def __init__(
        self,
        ops_db_path: str,
        live_db_path: str,
        resolver_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.ops_db_path = ops_db_path
        self.live_db_path = live_db_path
        self._resolver_factory = resolver_factory or (
            lambda: OperatorResolver(None, ops_db_path, live_db_path)
        )

    @staticmethod
    def _connect(path: str) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn

    @staticmethod
    def _tables(conn: sqlite3.Connection) -> set[str]:
        return {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}

    def list(self, limit: int = 200) -> dict[str, Any]:
        limit = max(1, min(int(limit), 500))
        candidates = self._compose()
        return {
            "candidates": candidates[:limit],
            "count": min(len(candidates), limit),
            "total": len(candidates),
            "intake_contract": "/api/ops-v2/emerging-operator-seeds",
            "read_only": True,
        }

    def get(self, entity: str) -> dict[str, Any] | None:
        entity = (entity or "").strip()
        return next((item for item in self._compose() if item["terminal_entity"] == entity), None)

    def recent_events(self, limit: int = 20) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for candidate in self._compose():
            entity = candidate["terminal_entity"]
            href = f"/intelligence/emerging-operators?entity={entity}"
            observation_events = [
                event for event in candidate["growth_timeline"]
                if event["event_type"] == "OBSERVATION"
            ]
            significant_events = [
                event for event in candidate["growth_timeline"]
                if event["event_type"] != "OBSERVATION"
            ]
            # Mission Control shows candidate-level changes, not every raw queue
            # row. The full immutable observation history remains in the workspace.
            for event in observation_events[-1:] + significant_events:
                kind = event["event_type"]
                if kind == "OBSERVATION":
                    kind = (
                        "NEW_UNKNOWN_INFRASTRUCTURE"
                        if event["observation_count"] == 1
                        else "EMERGING_CANDIDATE_STRENGTHENED"
                    )
                events.append({
                    "timestamp": event.get("timestamp"),
                    "state": "PROVISIONAL" if candidate["review_status"] != "MONITORING" else "EMERGING",
                    "kind": kind,
                    "message": event["label"],
                    "entity": {"id": entity, "type": "emerging_candidate"},
                    "href": href,
                })
        events.sort(key=lambda item: (item.get("timestamp") or 0, item["kind"]), reverse=True)
        return events[:max(1, min(int(limit), 50))]

    def _compose(self) -> list[dict[str, Any]]:
        if not os.path.exists(self.ops_db_path):
            return []
        evaluation = self._resolver_factory().evaluate()
        proposals = PromotionDecisionEngine().decide(evaluation)
        with self._connect(self.ops_db_path) as ops:
            seeds = emerging_operator_seeds(ops)
            ops_tables = self._tables(ops)
            live = self._connect(self.live_db_path) if os.path.exists(self.live_db_path) else None
            try:
                live_tables = self._tables(live) if live else set()
                candidates = [
                    self._candidate(seed, ops, ops_tables, live, live_tables,
                                    evaluation, proposals)
                    for seed in seeds
                ]
            finally:
                if live is not None:
                    live.close()
        candidates.sort(key=lambda item: (
            _DECISION_RANK.get(item["review_status"], 0),
            item["last_seen_at"] or 0,
            item["terminal_entity"],
        ), reverse=True)
        return candidates

    def _candidate(self, seed, ops, ops_tables, live, live_tables,
                   evaluation, proposals) -> dict[str, Any]:
        entity = seed["terminal_entity"]
        observations = self._outcomes(ops, ops_tables, entity)
        mints = tuple(sorted({row["mint"] for row in observations if row.get("mint")}))
        launch_rows = self._launch_rows(ops, ops_tables, live, live_tables, mints)

        creators = sorted({row.get("creator") for row in launch_rows if row.get("creator")})
        subprovisioners = sorted({row.get("subprov") for row in launch_rows if row.get("subprov")})
        treasuries = sorted({row.get("treasury") for row in launch_rows if row.get("treasury")})
        operations = sorted({row.get("operation_uuid") for row in launch_rows if row.get("operation_uuid")})
        templates: dict[tuple[str, Any], int] = defaultdict(int)
        for row in launch_rows:
            mechanism, amount = row.get("funding_mechanism"), row.get("funding_amount_sol")
            if mechanism is not None or amount is not None:
                templates[(mechanism or "Recorded funding", amount)] += 1

        linked_identity = tuple(
            item for item in evaluation.identity if entity in item.entities
        )
        linked_keys = {item.candidate_key for item in linked_identity}
        linked_proposals = [item for item in proposals if item.candidate_key in linked_keys]
        linked_proposals.sort(key=lambda item: (
            _DECISION_RANK.get(item.decision, 0), len(item.identity_classes),
            item.identity_confidence, item.candidate_key,
        ), reverse=True)
        primary = linked_proposals[0] if linked_proposals else None
        primary_key = primary.candidate_key if primary else None
        identity = [item for item in linked_identity if item.candidate_key == primary_key]
        contradictions = [
            item for item in evaluation.contradictions
            if item.candidate_key == primary_key or entity in item.related_entities
        ]

        first_seen = _timestamp(seed.get("first_seen_at"))
        last_seen = _timestamp(seed.get("last_seen_at"))
        timeline = self._timeline(observations, identity, first_seen)
        review_status = primary.decision if primary else "MONITORING"
        if primary and review_status == "PROMOTION_ELIGIBLE":
            timeline.append({
                "event_type": "PROMOTION_ELIGIBLE",
                "timestamp": max([item.get("timestamp") or 0 for item in timeline] or [last_seen or 0]),
                "day": self._day(first_seen, last_seen),
                "label": "Promotion eligible — analyst approval required",
                "observation_count": len(observations),
            })

        return {
            "terminal_entity": entity,
            "terminal_entity_type": seed.get("terminal_entity_type"),
            "current_attribution_outcome": "UNKNOWN_INFRASTRUCTURE",
            "is_canonical_operator": False,
            "canonical_operator_id": None,
            "observation_count": len(observations),
            "observed_launches": len(mints),
            "source_mints": list(mints),
            "first_seen_at": first_seen,
            "last_seen_at": last_seen,
            "time_span_seconds": max(0, (last_seen or 0) - (first_seen or 0)),
            "unique_creators": creators,
            "unique_sub_provisioners": subprovisioners,
            "treasuries": treasuries,
            "campaigns": operations,
            "campaign_count": len(operations),
            "funding_templates": [
                {"mechanism": key[0], "amount_sol": key[1], "observation_count": count}
                for key, count in sorted(templates.items(), key=lambda item: str(item[0]))
            ],
            "identity_classes": list(primary.identity_classes) if primary else [],
            "identity_class_count": len(primary.identity_classes) if primary else 0,
            "confidence": primary.identity_confidence if primary else seed.get("confidence"),
            "review_status": review_status,
            "review_label": self._review_label(review_status),
            "identity_evidence": [item.to_dict() for item in identity],
            "contradictions": [item.to_dict() for item in contradictions],
            "supporting_evidence": [self._outcome_evidence(row) for row in observations],
            "growth_timeline": sorted(timeline, key=lambda item: (
                item.get("timestamp") or 0, item["event_type"], item["label"]
            )),
            "promotion_handoff": ({
                "proposal_id": primary.proposal_id,
                "proposal_fingerprint": primary.proposal_fingerprint,
                "identity_fingerprint": primary.identity_fingerprint,
                "href": "/intelligence/operator-promotions",
                "requires_analyst_approval": True,
            } if primary else None),
        }

    @staticmethod
    def _review_label(status: str) -> str:
        return {
            "MONITORING": "Observation only — monitoring",
            "REVIEW_CANDIDATE": "Review candidate",
            "REVIEW_REQUIRED": "Review required",
            "PROMOTION_ELIGIBLE": "Promotion eligible",
        }.get(status, status.replace("_", " ").title())

    @staticmethod
    def _day(first: int | None, current: int | None) -> int:
        if not first or not current:
            return 1
        return max(1, ((current - first) // 86400) + 1)

    def _timeline(self, outcomes, identity, first_seen):
        timeline = []
        count = 0
        for row in sorted(outcomes, key=lambda item: (
            _timestamp(item.get("completed_at")) or 0, item.get("mint") or ""
        )):
            count += 1
            ts = _timestamp(row.get("completed_at"))
            timeline.append({
                "event_type": "OBSERVATION", "timestamp": ts,
                "day": self._day(first_seen, ts),
                "label": f"{count} persisted unknown-infrastructure observation{'s' if count != 1 else ''}",
                "observation_count": count, "source_mint": row.get("mint"),
            })
        seen: set[str] = set()
        for item in sorted(identity, key=lambda value: (value.evidence_type, value.observation_id)):
            if item.evidence_type in seen:
                continue
            seen.add(item.evidence_type)
            details = dict(item.details)
            ts = _timestamp(details.get("first_seen")) or first_seen
            timeline.append({
                "event_type": "IDENTITY_CLASS_ADDED", "timestamp": ts,
                "day": self._day(first_seen, ts),
                "label": f"Identity class added: {item.evidence_type.replace('_', ' ').title()}",
                "observation_count": len(outcomes), "identity_class": item.evidence_type,
            })
        return timeline

    @staticmethod
    def _outcome_evidence(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "mint": row.get("mint"), "completed_at": row.get("completed_at"),
            "confidence": row.get("confidence"), "stop_reason": row.get("stop_reason"),
            "evidence": _json(row.get("evidence_json"), {}),
            "href": f"/discovery?entity={row.get('mint')}&type=token",
        }

    @staticmethod
    def _outcomes(conn, tables, entity):
        if "wt_attribution_outcomes" not in tables:
            return []
        rows = conn.execute(
            "SELECT * FROM wt_attribution_outcomes WHERE terminal_entity=? "
            "AND outcome_type='UNKNOWN_INFRASTRUCTURE' "
            "AND should_seed_emerging_operator=1 ORDER BY completed_at,mint",
            (entity,),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _launch_rows(ops, ops_tables, live, live_tables, mints):
        if not mints:
            return []
        values: dict[str, dict[str, Any]] = {mint: {"mint": mint} for mint in mints}
        placeholders = ",".join("?" for _ in mints)
        if "wt_walkback_queue" in ops_tables:
            columns = {row[1] for row in ops.execute("PRAGMA table_info(wt_walkback_queue)")}
            selected = [name for name in (
                "mint", "creator", "subprov", "treasury", "funding_mechanism",
                "funder_amount_sol",
            ) if name in columns]
            for row in ops.execute(
                f"SELECT {','.join(selected)} FROM wt_walkback_queue "
                f"WHERE mint IN ({placeholders})", mints
            ).fetchall():
                values[row["mint"]].update(dict(row))
                if "funder_amount_sol" in columns:
                    values[row["mint"]]["funding_amount_sol"] = row["funder_amount_sol"]
        if "wt_token_lifecycle" in ops_tables:
            for row in ops.execute(
                f"SELECT mint,operation_uuid,creator,subprov,treasury FROM wt_token_lifecycle "
                f"WHERE mint IN ({placeholders})", mints
            ).fetchall():
                for key, value in dict(row).items():
                    if value is not None:
                        values[row["mint"]][key] = value
        if live is not None and "wt_ops_v2_creators" in live_tables:
            try:
                for row in live.execute(
                    f"SELECT token_mint mint,operation_uuid,creator_wallet creator," 
                    f"funding_amount_sol,template_base funding_mechanism FROM wt_ops_v2_creators "
                    f"WHERE token_mint IN ({placeholders})", mints
                ).fetchall():
                    for key, value in dict(row).items():
                        if value is not None:
                            values[row["mint"]][key] = value
            except sqlite3.Error:
                pass
        return list(values.values())
