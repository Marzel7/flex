"""Read-only access to the canonical operator model."""
from __future__ import annotations

import contextlib
import json
import sqlite3


class OperatorReader:
    """A pure reader: URI read-only, query-only, no DDL or persistent PRAGMAs."""

    def __init__(self, db_path: str) -> None:
        self._path = db_path

    @contextlib.contextmanager
    def _connect(self):
        conn = sqlite3.connect(f"file:{self._path}?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        try:
            yield conn
        finally:
            conn.close()

    def fetch_operator(self, operator_id: str) -> dict | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM operators WHERE operator_id = ?", (operator_id,)
                ).fetchone()
                if not row:
                    return None
                op = dict(row)
                op["entities"] = [dict(r) for r in conn.execute(
                    "SELECT * FROM operator_entities WHERE operator_id = ? "
                    "ORDER BY evidence_count DESC", (operator_id,)
                ).fetchall()]
                op["evidence"] = [
                    {**dict(r), "details": json.loads(r["details"] or "{}")}
                    for r in conn.execute(
                        "SELECT * FROM operator_evidence WHERE operator_id = ? "
                        "ORDER BY created_at DESC", (operator_id,)
                    ).fetchall()
                ]
                op["reviews"] = [dict(r) for r in conn.execute(
                    "SELECT * FROM operator_reviews WHERE operator_id = ? "
                    "ORDER BY timestamp DESC", (operator_id,)
                ).fetchall()]
                op["promotion_history"] = [
                    {**dict(r), "evidence_snapshot": json.loads(r["evidence_snapshot"])}
                    for r in conn.execute(
                        "SELECT * FROM operator_promotion_reviews "
                        "WHERE canonical_operator_id = ? ORDER BY timestamp DESC",
                        (operator_id,),
                    ).fetchall()
                ]
                from src.ops.operator_identity_governance import read_identity_lifecycle
                op["identity_lifecycle"] = read_identity_lifecycle(self._path, operator_id)
                if op["identity_lifecycle"]:
                    op["identity_status"] = op["identity_lifecycle"]["identity_status"]
                    op["activity_status"] = op["identity_lifecycle"]["activity_status"]
                return op
        except (sqlite3.Error, OSError, json.JSONDecodeError):
            return None

    def fetch_all_operators(self, *, exclude_rejected: bool = True, limit: int = 200) -> list[dict]:
        try:
            with self._connect() as conn:
                where = "WHERE status != 'REJECTED'" if exclude_rejected else ""
                rows = conn.execute(
                    f"SELECT * FROM operators {where} ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                result = []
                from src.ops.operator_identity_governance import read_identity_lifecycle
                for row in rows:
                    value = dict(row)
                    lifecycle = read_identity_lifecycle(self._path, value["operator_id"])
                    value["identity_status"] = lifecycle.get("identity_status", value.get("status"))
                    value["activity_status"] = lifecycle.get("activity_status", "ACTIVE")
                    value["merged_into_operator_id"] = lifecycle.get("merged_into_operator_id")
                    result.append(value)
                return result
        except (sqlite3.Error, OSError):
            return []

    def fetch_by_entity(self, entity_address: str) -> list[dict]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT o.*,oe.entity_type,oe.confidence AS entity_confidence,"
                    "oe.evidence_count FROM operators o JOIN operator_entities oe "
                    "ON o.operator_id=oe.operator_id WHERE oe.entity_address=? "
                    "AND o.status!='REJECTED' ORDER BY o.updated_at DESC",
                    (entity_address,),
                ).fetchall()
                return [dict(row) for row in rows]
        except (sqlite3.Error, OSError):
            return []

    def fetch_unified_investigation(self, entity_address: str) -> dict:
        """Read-time-only projection combining canonical operator state
        (this DB), main-DB historical funding relationships, and the new
        discovery-corpus evidence-qualification layer for a single entity
        address. Three independent read-only connections, no ATTACH, no
        writes, no schema change -- see docs/audits/ops_ui_p1_*.json for
        the full contract this implements.

        `operators.status` (read via fetch_by_entity, which already only
        reads the operators/operator_entities tables) is the ONLY field
        this method ever surfaces as canonical authority_state; every
        other field below is EVIDENCE_QUALIFICATION / DISCOVERY / FUNDING
        context and can never override it."""
        canonical_matches = self.fetch_by_entity(entity_address)
        authority_state = canonical_matches[0]["status"] if canonical_matches else None
        canonical_operator_id = canonical_matches[0]["operator_id"] if canonical_matches else None

        historical_population = 0
        try:
            from src.core.db import DB_PATH
            main_conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=10)
            main_conn.execute("PRAGMA query_only=ON")
            try:
                row = main_conn.execute(
                    "SELECT COUNT(*) FROM creator_funders WHERE funder_address=?",
                    (entity_address,),
                ).fetchone()
                historical_population = row[0] if row else 0
            finally:
                main_conn.close()
        except (sqlite3.Error, OSError):
            historical_population = None

        discovery = {
            "high_qualified_count": 0, "family_id": None, "classification": None,
            "attribution_state": None, "root_cex_infra_category": None,
            "cex_infra_hop_distance": None, "member_count": 0, "creator_count": 0,
        }
        try:
            from src.discovery.local_operation_discovery_projection import OUTPUT_DB
            disc_conn = sqlite3.connect(f"file:{OUTPUT_DB}?mode=ro", uri=True, timeout=10)
            disc_conn.row_factory = sqlite3.Row
            disc_conn.execute("PRAGMA query_only=ON")
            try:
                edge_count = disc_conn.execute(
                    "SELECT COUNT(*) FROM direct_funding_edges WHERE direct_funder=?",
                    (entity_address,),
                ).fetchone()
                discovery["high_qualified_count"] = edge_count[0] if edge_count else 0
                fam = disc_conn.execute(
                    "SELECT family_id, classification, attribution_state, "
                    "root_cex_infra_category, cex_infra_hop_distance, member_count, creator_count "
                    "FROM candidate_families WHERE root_evidence=? "
                    "ORDER BY member_count DESC LIMIT 1",
                    (entity_address,),
                ).fetchone()
                if fam:
                    discovery.update(dict(fam))
            finally:
                disc_conn.close()
        except (sqlite3.Error, OSError, ImportError):
            pass

        valid_not_high = None
        historical_only = None
        if historical_population is not None:
            accounted = discovery["high_qualified_count"]
            remainder = historical_population - accounted
            if remainder >= 0:
                # Without a per-member classification pass (out of scope for
                # a bounded read-time projection), the remainder is reported
                # as a single VALID_HISTORICAL_ASSOCIATION_OR_HISTORICAL_ONLY
                # bucket rather than guessing an 82/18-style split that would
                # require the full OF-DV34-P3-style reconciliation query.
                valid_not_high = remainder

        candidate_role = None
        if discovery["classification"] == "SERVICE_DISTRIBUTION_CLUSTER":
            candidate_role = "SERVICE_DISTRIBUTION_NETWORK"
        elif discovery["classification"] in ("STRONG_CANDIDATE_FAMILY", "PARTIAL_CANDIDATE_FAMILY"):
            candidate_role = "PROVISIONING_NETWORK_CANDIDATE"
        elif discovery["attribution_state"] == "NON_ATTRIBUTIVE_PROVENANCE":
            candidate_role = "CEX_INFRA_PROVENANCE_CLUSTER"

        return {
            "entity_address": entity_address,
            "identity": {
                "canonical_operator_id": canonical_operator_id,
                "authority_state": authority_state,
                "candidate_role": candidate_role,
                "promotion_eligible": authority_state == "CONFIRMED",
            },
            "historical_population": {
                "count": historical_population,
                "source": "creator_funders (main DB)",
            },
            "evidence_qualification": {
                "high_qualified_count": discovery["high_qualified_count"],
                "valid_not_high_or_historical_only": valid_not_high,
                "source": "local_operation_discovery_corpus.db direct_funding_edges",
            },
            "discovery": {
                "family_id": discovery["family_id"],
                "classification": discovery["classification"],
                "attribution_state": discovery["attribution_state"],
                "member_count": discovery["member_count"],
                "creator_count": discovery["creator_count"],
            },
            "funding_topology": {
                "cex_infra_hop_distance": discovery["cex_infra_hop_distance"],
                "root_cex_infra_category": discovery["root_cex_infra_category"],
            },
            "authority_note": "authority_state is read ONLY from operators.status via fetch_by_entity; no field in evidence_qualification or discovery can set or override it.",
        }

    def fetch_summary(self) -> dict:
        empty = {
            "total": 0, "candidates": 0, "review_candidates": 0,
            "provisional": 0, "confirmed": 0, "rejected": 0,
            "review_pending": 0, "merged": 0, "split": 0, "retired": 0,
            "active": 0, "quiet": 0, "dormant": 0, "reactivated": 0,
        }
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS total,"
                    "SUM(CASE WHEN status='CANDIDATE' THEN 1 ELSE 0 END) AS candidates,"
                    "SUM(CASE WHEN status='REVIEW_CANDIDATE' THEN 1 ELSE 0 END) AS review_candidates,"
                    "SUM(CASE WHEN status='PROVISIONAL' THEN 1 ELSE 0 END) AS provisional,"
                    "SUM(CASE WHEN status='CONFIRMED' THEN 1 ELSE 0 END) AS confirmed,"
                    "SUM(CASE WHEN status='REJECTED' THEN 1 ELSE 0 END) AS rejected,"
                    "SUM(CASE WHEN status IN ('REVIEW','MERGE_REVIEW','SPLIT_REVIEW') THEN 1 ELSE 0 END) AS review_pending "
                    "FROM operators"
                ).fetchone()
                if not row:
                    return empty
                result = {**empty, **{key: (value or 0) for key, value in dict(row).items()}}
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "operator_identity_state" in tables:
                    lifecycle = conn.execute(
                        "SELECT "
                        "SUM(identity_status='MERGED') merged,SUM(identity_status='SPLIT') split,"
                        "SUM(identity_status='RETIRED') retired,SUM(activity_status='ACTIVE') active,"
                        "SUM(activity_status='QUIET') quiet,SUM(activity_status='DORMANT') dormant,"
                        "SUM(activity_status='REACTIVATED') reactivated "
                        "FROM operator_identity_state"
                    ).fetchone()
                    result.update({key: (value or 0) for key, value in dict(lifecycle).items()})
                return result
        except (sqlite3.Error, OSError):
            return empty
