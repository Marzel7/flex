"""Explicit, audited promotion of X16B proposals into canonical operators.

Proposal generation remains read-only.  This service is the single operational
write boundary: reject/defer append governance history, while approve serialises
the reviewed evidence package into the four canonical operator tables in one
SQLite transaction.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from typing import Any, Callable

from src.ops.identity_framework import PROMOTION_ELIGIBLE, IdentityEvaluation
from src.ops.operator_model import DDL, EVIDENCE_CATALOGUE
from src.ops.operator_resolver import OperatorResolver
from src.core.database_write_service import database_write_service, execute_script


class PromotionError(Exception):
    def __init__(self, message: str, code: str = "INVALID_PROMOTION", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _readonly(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


class PromotionService:
    def __init__(
        self,
        ops_db_path: str,
        live_db_path: str,
        *,
        resolver_factory: Callable[[], OperatorResolver] | None = None,
        activation: Callable[[str], dict[str, Any]] | None = None,
        transaction_hook: Callable[[sqlite3.Connection], None] | None = None,
        write_service: Any = None,
    ) -> None:
        self.ops_db_path = ops_db_path
        self.live_db_path = live_db_path
        self._resolver_factory = resolver_factory or (
            lambda: OperatorResolver(None, ops_db_path, live_db_path)
        )
        self._activation = activation or self._activate_downstream
        self._transaction_hook = transaction_hook
        self._write_service = write_service or database_write_service
        self._write_database = f"operations:{os.path.realpath(ops_db_path)}"
        self._write_service.register_database(self._write_database, ops_db_path)

    def ensure_schema(self) -> None:
        self._write_service.submit(
            self._write_database,
            "operator-schema-upgrade",
            lambda conn: execute_script(conn, DDL),
        )

    def _current(self) -> tuple[IdentityEvaluation, tuple[Any, ...]]:
        resolver = self._resolver_factory()
        evaluation = resolver.evaluate()
        return evaluation, tuple(resolver.propose(evaluation))

    @staticmethod
    def _observations(evaluation: IdentityEvaluation, candidate_key: str) -> dict[str, list[dict]]:
        return {
            "identity": [o.to_dict() for o in evaluation.identity if o.candidate_key == candidate_key],
            "supporting": [o.to_dict() for o in evaluation.supporting if o.candidate_key == candidate_key],
            "context": [o.to_dict() for o in evaluation.context if o.candidate_key == candidate_key],
            "contradictions": [o.to_dict() for o in evaluation.contradictions if o.candidate_key == candidate_key],
        }

    def _history(self, proposal_id: str | None = None) -> list[dict[str, Any]]:
        try:
            with _readonly(self.ops_db_path) as conn:
                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='operator_promotion_reviews'"
                ).fetchone()
                if not exists:
                    return []
                if proposal_id:
                    rows = conn.execute(
                        "SELECT * FROM operator_promotion_reviews WHERE proposal_id=? "
                        "ORDER BY timestamp DESC, promotion_review_id DESC", (proposal_id,)
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM operator_promotion_reviews "
                        "ORDER BY timestamp DESC, promotion_review_id DESC"
                    ).fetchall()
                return [{**dict(row), "evidence_snapshot": json.loads(row["evidence_snapshot"])} for row in rows]
        except (sqlite3.Error, OSError, json.JSONDecodeError):
            return []

    def _package(self, proposal: Any, evaluation: IdentityEvaluation,
                 history: list[dict[str, Any]]) -> dict[str, Any]:
        observations = self._observations(evaluation, proposal.candidate_key)
        current_history = [h for h in history if h["proposal_id"] == proposal.proposal_id]
        same = [h for h in current_history if h["proposal_fingerprint"] == proposal.proposal_fingerprint]
        status = proposal.decision
        if any(h["decision"] == "APPROVE" for h in current_history):
            status = "APPROVED"
        elif any(h["decision"] == "REJECT" for h in same):
            status = "REJECTED"
        elif any(h["decision"] == "DEFER" for h in same):
            status = "DEFERRED"
        entities = sorted({e for group in observations.values() for item in group
                           for e in item.get("entities", [])})
        operations = sorted({o for group in observations.values() for item in group
                             for o in item.get("operations", [])})
        legacy = sorted(
            {(item.get("legacy_source"), item.get("legacy_identifier"))
             for group in observations.values() for item in group if item.get("legacy_source")},
            key=lambda item: (str(item[0]), str(item[1])),
        )
        return {
            **proposal.to_dict(),
            "current": True,
            "archived": status in {"APPROVED", "REJECTED"},
            "status": status,
            "display_name": self._display_name(proposal.candidate_key),
            "evidence": observations,
            "evidence_counts": {key: len(value) for key, value in observations.items()},
            "related_entities": entities,
            "operations": operations,
            "legacy_lineage": [{"source": s, "identifier": i} for s, i in legacy],
            "discovery_history": [
                {
                    "stage": "IDENTITY_EVALUATION" if group == "identity" else group.upper(),
                    "observation_id": item.get("observation_id") or item.get("contradiction_id"),
                    "evidence_type": item.get("evidence_type"),
                    "reason": item.get("reason"),
                    "source_tables": item.get("source_tables", []),
                    "legacy_source": item.get("legacy_source"),
                }
                for group in ("identity", "supporting", "context", "contradictions")
                for item in observations[group]
            ],
            "review_history": current_history,
        }

    def list(self) -> dict[str, Any]:
        evaluation, proposals = self._current()
        history = self._history()
        packages = [self._package(p, evaluation, history) for p in proposals]
        current_ids = {p.proposal_id for p in proposals}
        for proposal_id in sorted({h["proposal_id"] for h in history} - current_ids):
            archived = [h for h in history if h["proposal_id"] == proposal_id]
            packages.append(self._archived_package(archived))
        return {"proposals": packages, "count": len(packages), "generated_at": int(time.time())}

    def detail(self, proposal_id: str) -> dict[str, Any]:
        evaluation, proposals = self._current()
        proposal = next((p for p in proposals if p.proposal_id == proposal_id), None)
        if proposal is None:
            archived = self._history(proposal_id)
            if archived:
                return self._archived_package(archived)
            raise PromotionError("Promotion proposal not found.", "PROPOSAL_NOT_FOUND", 404)
        return self._package(proposal, evaluation, self._history(proposal_id))

    @staticmethod
    def _archived_package(history: list[dict[str, Any]]) -> dict[str, Any]:
        latest = history[0]
        package = dict(latest.get("evidence_snapshot") or {})
        status = {"APPROVE": "APPROVED", "REJECT": "REJECTED", "DEFER": "DEFERRED"}.get(
            latest.get("decision"), "ARCHIVED"
        )
        package.update({
            "proposal_id": latest["proposal_id"], "status": status,
            "current": False, "archived": True, "review_history": history,
        })
        return package

    def decide(self, proposal_id: str, decision: str, payload: dict[str, Any]) -> dict[str, Any]:
        decision = decision.upper()
        if decision not in {"APPROVE", "REJECT", "DEFER"}:
            raise PromotionError("Unknown promotion decision.")
        reviewer = str(payload.get("reviewer") or "").strip()
        reason = str(payload.get("reason") or "").strip()
        if not reviewer or not reason:
            raise PromotionError("Reviewer and reason are required.", "REVIEW_METADATA_REQUIRED")

        evaluation, proposals = self._current()
        proposal = next((p for p in proposals if p.proposal_id == proposal_id), None)
        if proposal is None:
            raise PromotionError("Promotion proposal not found or no longer present.", "PROPOSAL_NOT_FOUND", 404)
        if payload.get("proposal_fingerprint") != proposal.proposal_fingerprint:
            raise PromotionError("The proposal changed after it was loaded. Review the current evidence.",
                                 "STALE_PROPOSAL", 409)
        if payload.get("identity_fingerprint") != proposal.identity_fingerprint:
            raise PromotionError("The identity evidence changed after it was loaded.",
                                 "STALE_IDENTITY", 409)
        if decision == "APPROVE":
            if proposal.decision != PROMOTION_ELIGIBLE:
                raise PromotionError("Only promotion-eligible proposals can be approved.",
                                     "NOT_PROMOTION_ELIGIBLE", 409)
            if proposal.contradiction_ids:
                raise PromotionError("Contradictory identity evidence must be resolved first.",
                                     "CONTRADICTION_PRESENT", 409)

        snapshot = self._package(proposal, evaluation, self._history(proposal_id))
        if decision == "APPROVE":
            result = self._approve(proposal, snapshot, reviewer, reason,
                                   str(payload.get("supporting_notes") or "").strip())
            if not result.get("idempotent"):
                try:
                    result["downstream_activation"] = self._activation(result["canonical_operator_id"])
                except Exception as exc:  # canonical governance decision remains durable
                    result["downstream_activation"] = {"ok": False, "error": str(exc)}
            return result
        return self._noncanonical(proposal, snapshot, decision, reviewer, reason,
                                  str(payload.get("supporting_notes") or "").strip())

    def _approve(self, proposal: Any, snapshot: dict[str, Any], reviewer: str,
                 reason: str, notes: str) -> dict[str, Any]:
        operator_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"operator-promotion:{proposal.proposal_id}"))
        review_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"promotion:{proposal.proposal_fingerprint}:APPROVE"))
        now = int(time.time())
        def transaction(conn: sqlite3.Connection) -> dict[str, Any]:
            mark_phase = getattr(self._write_service, "mark_phase", lambda _phase: None)
            conn.execute("PRAGMA foreign_keys=ON")
            # Re-evaluate after acquiring the serialisation lock.  This closes
            # the gap between the request-level validation and canonical write.
            mark_phase("proposal-revalidation-started")
            _locked_evaluation, locked_proposals = self._current()
            locked = next((p for p in locked_proposals if p.proposal_id == proposal.proposal_id), None)
            if locked is None or locked.proposal_fingerprint != proposal.proposal_fingerprint:
                raise PromotionError("The proposal changed before the promotion lock was acquired.",
                                     "STALE_PROPOSAL", 409)
            if locked.identity_fingerprint != proposal.identity_fingerprint:
                raise PromotionError("The identity evidence changed before promotion.",
                                     "STALE_IDENTITY", 409)
            if locked.decision != PROMOTION_ELIGIBLE or locked.contradiction_ids:
                raise PromotionError("The proposal is no longer safe to promote.",
                                     "PROMOTION_NO_LONGER_SAFE", 409)
            existing = conn.execute(
                "SELECT * FROM operator_promotion_reviews WHERE proposal_fingerprint=? AND decision='APPROVE'",
                (proposal.proposal_fingerprint,),
            ).fetchone()
            if existing:
                return {"decision": "APPROVE", "promotion_review_id": existing["promotion_review_id"],
                        "canonical_operator_id": existing["canonical_operator_id"], "idempotent": True}
            prior_approval = conn.execute(
                "SELECT canonical_operator_id FROM operator_promotion_reviews "
                "WHERE proposal_id=? AND decision='APPROVE' LIMIT 1", (proposal.proposal_id,)
            ).fetchone()
            if prior_approval:
                raise PromotionError("This candidate has already been promoted from an earlier proposal version.",
                                     "ALREADY_PROMOTED", 409)
            rejected = conn.execute(
                "SELECT 1 FROM operator_promotion_reviews WHERE proposal_fingerprint=? AND decision='REJECT'",
                (proposal.proposal_fingerprint,),
            ).fetchone()
            if rejected:
                raise PromotionError("This exact proposal was rejected and is archived.", "PROPOSAL_REJECTED", 409)

            mark_phase("first-insert-attempted:operators")
            conn.execute(
                "INSERT INTO operators(operator_id,status,confidence,first_seen,last_seen,summary,review_state,display_name,created_at,updated_at) "
                "VALUES(?, 'CONFIRMED', 'CERTAIN', ?, ?, ?, 'REVIEWED', ?, ?, ?)",
                (operator_id, now, now, f"Canonical operator promoted from {proposal.candidate_key} after analyst review.",
                 self._display_name(proposal.candidate_key), now, now),
            )
            observations = [item for key in ("identity", "supporting", "context")
                            for item in snapshot["evidence"][key]]
            all_entities = sorted({entity for item in observations for entity in item.get("entities", [])})
            counts = {entity: sum(entity in item.get("entities", []) for item in observations)
                      for entity in all_entities}
            mark_phase("table-write-group-started:operator_entities")
            for entity in all_entities:
                conn.execute(
                    "INSERT INTO operator_entities(operator_id,entity_address,entity_type,confidence,evidence_count,added_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (operator_id, entity, self._entity_type(entity, observations), "HIGH", counts[entity], now),
                )
            mark_phase("table-write-group-started:operator_evidence")
            for item in observations:
                entities = item.get("entities", [])
                operations = item.get("operations", [])
                conn.execute(
                    "INSERT INTO operator_evidence(evidence_id,operator_id,evidence_type,evidence_category,source_operation,entity_a,entity_b,weight,details,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid5(uuid.NAMESPACE_URL, f"operator-evidence:{operator_id}:{item['observation_id']}")),
                     operator_id, item["evidence_type"], item["category"],
                     operations[0] if len(operations) == 1 else proposal.candidate_key,
                     entities[0] if entities else None, entities[1] if len(entities) > 1 else None,
                     EVIDENCE_CATALOGUE[item["evidence_type"]]["weight"], _canonical_json(item), now),
                )
            canonical_review_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"operator-review:{review_id}"))
            mark_phase("table-write-group-started:operator_reviews")
            conn.execute(
                "INSERT INTO operator_reviews(review_id,operator_id,decision,reviewer,timestamp,reason) VALUES(?,?,?,?,?,?)",
                (canonical_review_id, operator_id, "CONFIRMED", reviewer, now, reason),
            )
            mark_phase("table-write-group-started:operator_promotion_reviews")
            self._insert_ledger(conn, review_id, proposal, "APPROVE", reviewer, now,
                                reason, notes, snapshot, operator_id)
            if self._transaction_hook:
                self._transaction_hook(conn)
            return {"decision": "APPROVE", "promotion_review_id": review_id,
                    "canonical_operator_id": operator_id, "idempotent": False}

        return self._write_service.submit(
            self._write_database,
            "operator-promotion-approve",
            transaction,
        )

    def _noncanonical(self, proposal: Any, snapshot: dict[str, Any], decision: str,
                      reviewer: str, reason: str, notes: str) -> dict[str, Any]:
        review_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
                                  f"promotion:{proposal.proposal_fingerprint}:{decision}"))
        now = int(time.time())
        def transaction(conn: sqlite3.Connection) -> dict[str, Any]:
            conn.execute("PRAGMA foreign_keys=ON")
            if conn.execute(
                "SELECT 1 FROM operator_promotion_reviews WHERE proposal_id=? AND decision='APPROVE'",
                (proposal.proposal_id,),
            ).fetchone():
                raise PromotionError("This candidate is already canonical; its promotion cannot be reversed here.",
                                     "ALREADY_PROMOTED", 409)
            existing = conn.execute(
                "SELECT promotion_review_id FROM operator_promotion_reviews "
                "WHERE proposal_fingerprint=? AND decision=?",
                (proposal.proposal_fingerprint, decision),
            ).fetchone()
            if existing:
                return {"decision": decision, "promotion_review_id": existing[0],
                        "canonical_operator_id": None, "idempotent": True}
            self._insert_ledger(conn, review_id, proposal, decision, reviewer, now,
                                reason, notes, snapshot, None)
            if self._transaction_hook:
                self._transaction_hook(conn)
            return {"decision": decision, "promotion_review_id": review_id,
                    "canonical_operator_id": None, "idempotent": False}

        return self._write_service.submit(
            self._write_database,
            f"operator-promotion-{decision.lower()}",
            transaction,
        )

    @staticmethod
    def _insert_ledger(conn: sqlite3.Connection, review_id: str, proposal: Any,
                       decision: str, reviewer: str, timestamp: int, reason: str,
                       notes: str, snapshot: dict[str, Any], operator_id: str | None) -> None:
        audit_snapshot = dict(snapshot)
        # The immutable snapshot captures the evidence reviewed, not copies of
        # earlier ledger rows (which would recursively inflate every decision).
        audit_snapshot.pop("review_history", None)
        conn.execute(
            "INSERT INTO operator_promotion_reviews(promotion_review_id,proposal_id,proposal_fingerprint,identity_fingerprint,candidate_key,decision,reviewer,timestamp,reason,supporting_notes,evidence_snapshot,canonical_operator_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (review_id, proposal.proposal_id, proposal.proposal_fingerprint,
             proposal.identity_fingerprint, proposal.candidate_key, decision, reviewer,
             timestamp, reason, notes or None, _canonical_json(audit_snapshot), operator_id),
        )

    @staticmethod
    def _display_name(candidate_key: str) -> str:
        if "watchtower" in candidate_key.lower():
            return "WATCHTOWER"
        tail = candidate_key.rsplit(":", 1)[-1]
        return tail if len(tail) <= 24 else f"{tail[:10]}…{tail[-6:]}"

    @staticmethod
    def _entity_type(entity: str, observations: list[dict[str, Any]]) -> str:
        text = _canonical_json([o for o in observations if entity in o.get("entities", [])]).upper()
        for needle, role in (("TREASURY", "TREASURY"), ("CREATOR", "CREATOR"),
                             ("SIGNALLER", "SIGNALLER"), ("RELAY", "RELAY"),
                             ("SUBPROV", "SUB_PROVISIONER"), ("HUB", "SUB_PROVISIONER")):
            if needle in text:
                return role
        return "UNKNOWN"

    @staticmethod
    def _activate_downstream(operator_id: str) -> dict[str, Any]:
        # Use the route-owned engine instances so the freshly generated outputs
        # are immediately visible to the UI. Every operation is read-only and
        # bounded to the promoted operator (similarity compares only its pairs).
        from src.ops.behaviour_routes import _get_engine as behaviour_engine
        from src.ops.behaviour_change_routes import _get_engine as change_engine
        from src.ops.similarity_routes import _get_engine as similarity_engine
        from src.ops.assessment_routes import _get_engine as assessment_engine
        from src.ops.forecast_routes import _get_engine as forecast_engine, _store_history
        from src.core.db import DB_PATH, OPS_DB_PATH
        from src.ops.observation_materializer import ObservationMaterializationPipeline

        materialization = ObservationMaterializationPipeline(
            str(OPS_DB_PATH), str(DB_PATH)
        ).run(operator_id)
        behaviour = behaviour_engine().compute(operator_id)
        change = change_engine().compare(operator_id)
        similarity = similarity_engine().compute_for_operator(operator_id)
        assessment = assessment_engine().assess(operator_id)
        forecast = forecast_engine().forecast(operator_id, "OBSERVING")
        _store_history(operator_id, forecast)
        return {
            "ok": True,
            "operator_id": operator_id,
            "materialization": materialization,
            "behaviour_fingerprint": behaviour.fingerprint,
            "change_fingerprint": change.fingerprint,
            "similarity_available": similarity.available,
            "similarity_comparisons": similarity.comparisons_attempted,
            "assessment_fingerprint": assessment.fingerprint,
            "forecast_fingerprint": forecast.forecast_fingerprint,
        }
