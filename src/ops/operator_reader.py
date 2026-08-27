"""Read-only access to the canonical operator model."""
from __future__ import annotations

import contextlib
import json
import sqlite3


_PROVISIONAL_900B = "p3r-v2-900b89587c6987d582df"
_PROVISIONAL_900B_DETAIL = {
    "selected_funding_pattern": "Hop-1 WSOL_WRAP_CLOSE",
    "exact_amount_lamports": 999985000,
    "human_amount": "1 SOL - 15,000 lamports",
    "atomic_lifecycle": "createAccountWithSeed -> initializeAccount3 -> transfer -> syncNative -> closeAccount",
    "atomic_dominant_coverage": "31/44",
    "wsol_lifecycle_coverage": "44/44",
    "role_pattern": "44 rotating creators; candidate_parent is the direct-funder role; 10 distinct direct funders; top five funders cover 39/44 (88.64%).",
    "profile": "Recurrent near-1-SOL temporary-WSOL provision-and-close variant with rotating creators and recurrent direct-funder infrastructure.",
    "validation": {"h0": "Behaviour-only: precision 74.58%, recall 100.00%.", "h1": "Hybrid recurrent-funder review-confidence contract: precision 90.70%, recall 88.64%; 4 known false positives remain."},
    "analysis": "Behaviour is strongly recurrent and recurrent direct-funder infrastructure improves precision, but the zero-false-positive automatic-attribution gate is not met. Bounded deeper-hop RPC and provenance studies did not close that gap.",
    "history": ["P3R candidate discovery and operation-priority ranking.", "Distinctiveness and bounded RPC discriminator investigations.", "Hybrid recurrent-funder qualification and provisional Active Operations admission."],
}


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
                qualification = conn.execute(
                    "SELECT qualification_category,automation_eligibility,detector_version,parent_mechanism,source_candidate_id,benchmark_json "
                    "FROM operation_qualification_contracts WHERE operator_id=? ORDER BY created_at DESC LIMIT 1",
                    (operator_id,),
                ).fetchone()
                if qualification:
                    op["qualification_contract"] = {**dict(qualification), "benchmark": json.loads(qualification["benchmark_json"] or "{}")}
                    op["qualification_category"] = qualification["qualification_category"]
                else:
                    op["qualification_category"] = "CONFIRMED"
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
                profile = conn.execute(
                    "SELECT source_candidate_id, profile_version, status, provenance_json, "
                    "member_mints_json, reviewed_at, reviewer "
                    "FROM operation_behavioural_profiles WHERE operator_id=? "
                    "ORDER BY profile_version DESC LIMIT 1",
                    (operator_id,),
                ).fetchone()
                if profile:
                    op["behavioural_profile"] = {
                        **dict(profile),
                        "provenance": json.loads(profile["provenance_json"]),
                        "member_mints": json.loads(profile["member_mints_json"]),
                    }
                    mints = op["behavioural_profile"]["member_mints"]
                    if mints and conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wt_attribution_outcomes'"
                    ).fetchone():
                        placeholders = ",".join("?" for _ in mints)
                        rows = conn.execute(
                            "SELECT mint, outcome_type, terminal_entity, evidence_json, completed_at "
                            f"FROM wt_attribution_outcomes WHERE mint IN ({placeholders})", mints,
                        ).fetchall()
                        history = [
                            {**dict(item), "details": json.loads(item["evidence_json"] or "{}")}
                            for item in rows
                        ]
                        history_by_mint = {item["mint"]: item for item in history}
                        if history_by_mint and conn.execute(
                            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wt_provisioning_edges'"
                        ).fetchone():
                            edge_rows = conn.execute(
                                "SELECT source_mint, funding_tx_signature, funding_block_time, funding_mechanism "
                                f"FROM wt_provisioning_edges WHERE edge_type='SUBPROV_TO_CREATOR' AND source_mint IN ({placeholders})",
                                mints,
                            ).fetchall()
                            for edge in edge_rows:
                                history_by_mint[edge["source_mint"]].update(dict(edge))
                        op["retained_funding_history"] = history
                snapshot = conn.execute(
                    "SELECT observed_at, timestamp_semantics, metrics_json, activity_state "
                    "FROM operation_activity_snapshots WHERE operator_id=? "
                    "ORDER BY observed_at DESC LIMIT 1",
                    (operator_id,),
                ).fetchone()
                if snapshot:
                    op["activity_snapshot"] = {
                        **dict(snapshot),
                        "metrics": json.loads(snapshot["metrics_json"]),
                    }
                if op.get("qualification_category") == "CONFIRMED" and op.get("display_name") != "WATCHTOWER" and conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='operator_launch_membership'"
                ).fetchone():
                    op["recent_launches"] = [dict(r) for r in conn.execute(
                        "SELECT m.mint,COALESCE(q.creator,'') AS creator_wallet,COALESCE(q.create_anchor_block_time,q.funder_block_time,q.completed_at,m.assigned_at) AS create_time,q.treasury AS treasury_wallet,q.subprov AS subprov_wallet,q.funder_sig AS wrap_close_signature,q.funding_mechanism FROM operator_launch_membership m LEFT JOIN wt_walkback_queue q ON q.mint=m.mint WHERE m.operator_id=? ORDER BY create_time DESC LIMIT 250",
                        (operator_id,),
                    ).fetchall()]
                if (op.get("behavioural_profile", {}).get("provenance", {}).get("detector_version")
                        == "D3DE_D0_EXACT_SELECTED_FOUR_STEP_LADDER.v1"):
                    provenance = op["behavioural_profile"]["provenance"]
                    evidence = provenance.get("historical_member_evidence", [])
                    op["summary_model"] = {
                        "activity": {"state": op.get("activity_snapshot", {}).get("activity_state", "ACTIVE"),
                                     "metrics": op.get("activity_snapshot", {}).get("metrics", {})},
                        "fingerprint": {
                            "route": "Parent hop 4 → parent hop 3 → parent hop 2 → direct funder → creator",
                            "funding": "Exact selected ladder: 29,999,985,000 → 29,999,990,000 → 14,479,000 → 2,074,000 lamports",
                            "mechanism": "PLAIN_XFER → WSOL_WRAP_CLOSE → WSOL_WRAP_CLOSE → WSOL_WRAP_CLOSE",
                            "atomic_sequence": provenance.get("atomic_sequence", []),
                            "address_behaviour": "Address-independent: 9 creators, 9 direct funders, and 33 parents rotate across the frozen cohort.",
                            "profile": "D0 exact selected route · frozen validation 9 TP / 0 FP / 0 FN across 12,041 observable retained mints.",
                        },
                        "all_launches": [{"mint": item["mint"], "creator": item.get("creator"),
                                          "intermediary": item.get("direct_funder"), "signature": item.get("selected_edges", [{}])[0].get("signature"),
                                          "create_time": item.get("observed_at"), "mechanism": "D0 CONFIRMED"}
                                         for item in evidence],
                    }
                if op.get("qualification_category") == "PROVISIONAL" and conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='provisional_operation_activity_observations'"
                ).fetchone():
                    rows = conn.execute(
                        "SELECT o.mint,o.observed_at AS create_time,o.state,o.provenance_json,e.wallet AS creator,e.candidate_parent AS direct_funder,e.signature,e.hop_depth AS selected_hop,e.mechanism,e.amount_lamports AS selected_amount_lamports,a.instruction_order_json AS atomic_sequence_json FROM provisional_operation_activity_observations o LEFT JOIN wt_walkback_edge_candidates e ON e.mint=o.mint AND e.selection_status='SELECTED' AND e.hop_depth=1 LEFT JOIN wt_walkback_atomic_flows a ON a.signature=e.signature AND a.mint=o.mint WHERE o.operator_id=? ORDER BY o.observed_at DESC LIMIT 250",
                        (operator_id,),
                    ).fetchall()
                    op["recent_launches"] = []
                    for row in rows:
                        launch = dict(row)
                        launch.update({"intermediary": launch["direct_funder"], "provisional_state": launch["state"], "match": launch["state"].replace("_", " ")})
                        if launch.get("atomic_sequence_json"):
                            launch["atomic_sequence"] = json.loads(launch["atomic_sequence_json"])
                        op["recent_launches"].append(launch)
                    if op.get("qualification_contract", {}).get("source_candidate_id") == _PROVISIONAL_900B:
                        op["provisional_detail"] = _PROVISIONAL_900B_DETAIL
                        op["summary_model"] = {"activity": {"state": "PROVISIONAL", "metrics": op.get("activity_snapshot", {}).get("metrics", {})}, "fingerprint": {"route": "Direct funder -> rotating creator", "funding": "999,985,000 lamports (1 SOL - 15,000 lamports)", "mechanism": "Hop-1 WSOL_WRAP_CLOSE", "atomic_sequence": [_PROVISIONAL_900B_DETAIL["atomic_lifecycle"] + " (31/44)"], "address_behaviour": _PROVISIONAL_900B_DETAIL["role_pattern"] + " Broader WSOL lifecycle 44/44.", "profile": _PROVISIONAL_900B_DETAIL["profile"]}, "recent_launches": op["recent_launches"]}
                        op["evidence"].append({"evidence_type": "PROVISIONAL_OPERATION_CONTRACT", "details": _PROVISIONAL_900B_DETAIL})
                # WATCHTOWER's canonical launch ledger predates generic
                # membership. The display therefore combines ledger entries
                # with strict confirmed membership-only launches, using their
                # retained queue route fields. This is a read-only projection.
                if op.get("display_name") == "WATCHTOWER" and conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wt_watchtower_launches'"
                ).fetchone():
                    has_membership = conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='operator_launch_membership'"
                    ).fetchone()
                    has_queue = conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wt_walkback_queue'"
                    ).fetchone()
                    if has_membership and has_queue:
                        op["recent_launches"] = [dict(r) for r in conn.execute(
                            "SELECT mint, creator_wallet, create_time, treasury_wallet, subprov_wallet, "
                            "wrap_close_signature, funding_mechanism FROM ("
                            "SELECT mint, creator_wallet, create_time, treasury_wallet, subprov_wallet, "
                            "wrap_close_signature, funding_mechanism "
                            "FROM wt_watchtower_launches WHERE mint IS NOT NULL "
                            "AND COALESCE(state, 'FIRED_CREATE') != 'PENDING_REVIEW' "
                            "UNION ALL "
                            "SELECT q.mint, q.creator AS creator_wallet, "
                            "COALESCE(q.create_anchor_block_time,q.funder_block_time,q.completed_at) AS create_time, "
                            "q.treasury AS treasury_wallet, q.subprov AS subprov_wallet, "
                            "q.funder_sig AS wrap_close_signature, q.funding_mechanism "
                            "FROM operator_launch_membership m JOIN wt_walkback_queue q ON q.mint=m.mint "
                            "WHERE m.operator_id=? AND q.intelligence_outcome='WATCHTOWER_CONFIRMED' "
                            "AND NOT EXISTS (SELECT 1 FROM wt_watchtower_launches l WHERE l.mint=q.mint)"
                            ") ORDER BY create_time DESC LIMIT 250",
                            (operator_id,),
                        ).fetchall()]
                    else:
                        op["recent_launches"] = [dict(r) for r in conn.execute(
                            "SELECT mint, creator_wallet, create_time, treasury_wallet, subprov_wallet, "
                            "wrap_close_signature, funding_mechanism "
                            "FROM wt_watchtower_launches WHERE mint IS NOT NULL "
                            "AND COALESCE(state, 'FIRED_CREATE') != 'PENDING_REVIEW' "
                            "ORDER BY create_time DESC LIMIT 250"
                        ).fetchall()]
                from src.ops.operator_identity_governance import read_identity_lifecycle
                op["identity_lifecycle"] = read_identity_lifecycle(self._path, operator_id)
                if op["identity_lifecycle"]:
                    op["identity_status"] = op["identity_lifecycle"]["identity_status"]
                    op["activity_status"] = op["identity_lifecycle"]["activity_status"]
                from src.ops.operation_fingerprint_monitoring import build_fingerprint_health
                health = build_fingerprint_health(op)
                if health:
                    from src.ops.operation_fingerprint_drift import latest_health
                    health = build_fingerprint_health(op, latest_health(conn, operator_id, health["fingerprint_id"]))
                op["fingerprint_health"] = health
                from src.ops.operation_identity_metadata import identity_metadata
                member_count = conn.execute("SELECT COUNT(*) FROM operator_launch_membership WHERE operator_id=?", (operator_id,)).fetchone()[0]
                op["identity_metadata"] = identity_metadata(conn, op["display_name"], int(member_count))
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

    def fetch_active_manual_operators(self, *, limit: int = 200) -> list[dict]:
        """Return only operations explicitly admitted to the active registry."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT o.*, d.disposition, d.updated_at AS disposition_updated_at, "
                    "COALESCE(q.qualification_category,'CONFIRMED') AS qualification_category, q.automation_eligibility, q.detector_version, q.parent_mechanism, q.benchmark_json, s.metrics_json, s.activity_state "
                    "FROM operators o "
                    "JOIN operation_registry_dispositions d "
                    "ON d.operator_id=o.operator_id "
                    "LEFT JOIN operation_qualification_contracts q ON q.operator_id=o.operator_id "
                    "LEFT JOIN operation_activity_snapshots s ON s.snapshot_id=("
                    "SELECT snapshot_id FROM operation_activity_snapshots "
                    "WHERE operator_id=o.operator_id ORDER BY observed_at DESC LIMIT 1) "
                    "WHERE d.disposition='ACTIVE_MANUAL' AND o.status!='MERGED' "
                    "ORDER BY d.updated_at DESC, o.operator_id ASC LIMIT ?",
                    (limit,),
                ).fetchall()
                result = []
                for row in rows:
                    value = dict(row)
                    value["qualification_benchmark"] = json.loads(value.pop("benchmark_json") or "{}")
                    metrics = json.loads(value.pop("metrics_json") or "{}")
                    value["total_launches"] = metrics.get("total_observed_launches")
                    value["average_inter_launch_gap_seconds"] = metrics.get("average_inter_launch_gap_seconds")
                    value["last_observed_launch_timestamp"] = metrics.get("last_observed_launch_timestamp")
                    result.append(value)
                result.sort(
                    key=lambda value: (
                        value["last_observed_launch_timestamp"] is not None,
                        value["last_observed_launch_timestamp"] or 0,
                    ),
                    reverse=True,
                )
                return result
        except (sqlite3.Error, OSError):
            return []

    def fetch_operation_review_queue(self, *, limit: int = 50) -> list[dict]:
        """Return non-member walkback results that require analyst review.

        This is deliberately a read-only projection.  A row in this queue is
        not an attribution and must never affect an operator's membership or
        activity snapshot.
        """
        try:
            with self._connect() as conn:
                if not conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wt_watchtower_launches'"
                ).fetchone():
                    return []
                row = conn.execute(
                    "SELECT COUNT(DISTINCT mint) AS pending_launches, "
                    "MAX(COALESCE(create_time, recorded_at)) AS last_observed "
                    "FROM wt_watchtower_launches WHERE state='PENDING_REVIEW'"
                ).fetchone()
                if not row or not row["pending_launches"]:
                    return []
                return [{
                    "candidate_operator_id": "04265d9f-6eb2-568c-a49e-9253091a4dbb",
                    "candidate_operator_name": "WATCHTOWER",
                    "pending_launches": row["pending_launches"],
                    "last_observed": row["last_observed"],
                    "review_state": "PENDING_ROUTE_REVIEW",
                    "review_reason": "Walkback association was retained, but the verified provisioning route required for automatic membership is incomplete.",
                }]
        except (sqlite3.Error, OSError):
            return []

    def fetch_operator_review_candidates(self, operator_id: str, *, limit: int = 500) -> list[dict]:
        """Return pending token evidence for one proposed operation, never membership."""
        if operator_id != "04265d9f-6eb2-568c-a49e-9253091a4dbb":
            return []
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT mint, creator_wallet, create_time, treasury_wallet, subprov_wallet, "
                    "wrap_close_signature, funding_mechanism, recorded_at "
                    "FROM wt_watchtower_launches WHERE state='PENDING_REVIEW' "
                    "ORDER BY COALESCE(create_time, recorded_at) DESC, id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                return [dict(row) for row in rows]
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
            # Funding recurrence is a neutral structural fact.  It cannot
            # establish a provisioner role or common operation identity on
            # its own.
            candidate_role = "FUNDING_STRUCTURE"
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
