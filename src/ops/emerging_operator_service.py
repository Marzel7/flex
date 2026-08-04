"""Read-only operation-family discovery projection.

This evolves the original emerging-operator projection without changing its
intake tables or workers.  Families are reconstructed from persisted canonical
entities, attribution outcomes, provisioning edges, sessions, and scored
infrastructure candidates.  Opening the page never writes intelligence.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from collections import defaultdict
from typing import Any, Callable

from src.ops.attribution_outcome import emerging_operator_seeds
from src.ops.identity_framework import PromotionDecisionEngine
from src.ops.operator_resolver import OperatorResolver
from src.ops.operation_intelligence import OperationIntelligenceAssembler
from src.utils.infra_mapping import get_funder_label


EVIDENCE_WEIGHTS = {
    "topology_reconstruction": 20,
    "transaction_provenance": 20,
    "relationship_coverage": 15,
    "identity_link_coverage": 15,
    "independent_evidence_diversity": 15,
    "uncertainty_conflict_coverage": 15,
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


def _short(value: str) -> str:
    return value if len(value) <= 8 else value[:4]


class EmergingOperatorService:
    """Aggregate explainable operation families over existing read-only data."""

    def __init__(self, ops_db_path: str, live_db_path: str,
                 resolver_factory: Callable[[], Any] | None = None) -> None:
        self.ops_db_path = ops_db_path
        self.live_db_path = live_db_path
        self._resolver_factory = resolver_factory or (
            lambda: OperatorResolver(None, ops_db_path, live_db_path)
        )
        self.minimum_launches = max(1, int(os.getenv("OPERATION_DISCOVERY_MIN_LAUNCHES", "2")))
        self.completeness_threshold = max(0, min(100, int(
            os.getenv("OPERATION_DISCOVERY_COMPLETENESS_THRESHOLD", "50")
        )))
        self.attention_budget = max(1, min(20, int(
            os.getenv("OPERATION_DISCOVERY_ATTENTION_BUDGET", "5")
        )))
        self.candidate_summary_limit = max(1, min(15, int(
            os.getenv("OPERATION_DISCOVERY_CANDIDATE_SUMMARY_LIMIT", "10")
        )))
        self.dormancy_days = max(7, int(os.getenv("OPERATION_DISCOVERY_DORMANCY_DAYS", "30")))
        self.refresh_seconds = max(1, int(os.getenv("OPERATION_DISCOVERY_REFRESH_SECONDS", "15")))
        self._cached_families: list[dict[str, Any]] | None = None
        self._cached_at = 0.0

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

    @staticmethod
    def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}

    def list(self, limit: int = 200, debug: bool = False) -> dict[str, Any]:
        limit = max(1, min(int(limit), 500))
        all_families = self._compose()
        visible_full = [f for f in all_families if f["stage"] in {"EMERGING", "ESTABLISHED", "CONFIRMED"}][:limit]
        visible = [self._list_card(f) for f in visible_full]
        candidate_summary_full = sorted(
            (f for f in all_families if f["stage"] in {"CANDIDATE", "SIGNIFICANT_ACTIVE"}),
            key=lambda f: (f["discovery_significance"]["score"], f["last_material_activity_at"] or 0, f["family_id"]),
            reverse=True,
        )[:self.candidate_summary_limit]
        if not candidate_summary_full:
            candidate_summary_full = sorted(
                (f for f in all_families if f["stage"] not in {"CONFIRMED", "RETIRED", "BACKGROUND"}),
                key=lambda f: (f["last_material_activity_at"] or 0, f["family_id"]), reverse=True,
            )[:self.candidate_summary_limit]
        candidate_summary = ([self._list_card(f) for f in candidate_summary_full]
                             if visible_full else candidate_summary_full)
        compatibility_candidates = visible if visible else candidate_summary
        stages = ("BACKGROUND", "CANDIDATE", "SIGNIFICANT_ACTIVE", "EMERGING", "ESTABLISHED", "CONFIRMED", "DORMANT", "RETIRED")
        result = {
            "families": visible, "candidates": compatibility_candidates,
            "count": len(visible), "total": len(visible),
            "confirmed_operations": [f for f in visible if f["stage"] == "CONFIRMED"],
            "emerging_operations": [f for f in visible if f["stage"] == "EMERGING"],
            "established_changes": [f for f in visible if f["stage"] == "ESTABLISHED" and f["material_change_reasons"]],
            "candidate_summary": candidate_summary,
            "funnel": {stage: sum(f["stage"] == stage for f in all_families) for stage in stages},
            "reconciliation": self._reconcile_token_states(all_families),
            "promotion_config": {
                "minimum_launches": self.minimum_launches,
                "evidence_completeness_threshold": self.completeness_threshold,
                "attention_budget": self.attention_budget,
                "candidate_summary_limit": self.candidate_summary_limit,
                "dormancy_days": self.dormancy_days,
                "confirmed_operation": "manual promotion only",
            },
            "intake_contract": "/api/ops-v2/emerging-operator-seeds",
            "read_only": True,
        }
        if debug:
            hidden = [f for f in all_families if f["stage"] not in {"EMERGING", "ESTABLISHED", "CONFIRMED"}]
            result["debug"] = {
                "enabled": True,
                "background_clusters": [f for f in hidden if f["stage"] == "BACKGROUND"],
                "candidate_families": [f for f in hidden if f["stage"] in {"CANDIDATE", "SIGNIFICANT_ACTIVE"}],
                "dormant_families": [f for f in hidden if f["stage"] == "DORMANT"],
                "retired_families": [f for f in hidden if f["stage"] == "RETIRED"],
                "rejected_families": [f for f in hidden if f["exclusion_evidence"]],
                "evidence_routing": "profiles initialized from infrastructure scoring, walkback seeds, or complete persisted session-plus-provisioning evidence before enrichment, classification, and exclusions",
                "reconciliation": [{
                    "family_id": f["family_id"], "family_name": f["family_name"],
                    "old_state": f.get("previous_stage"), "new_state": f["stage"],
                    "significance": f["discovery_significance"]["score"],
                    "completeness": f["evidence_completeness"]["score"],
                    "maturity": f["operational_maturity"]["score"],
                    "promotion_ready": f["promotion_ready"],
                    "reason": f["promotion_gates"]["reason_not_surfaced"] or ", ".join(f["why_surfaced"]),
                } for f in all_families],
            }
        return result

    @staticmethod
    def _list_card(family: dict[str, Any]) -> dict[str, Any]:
        card = dict(family)
        for key in ("supporting_evidence", "discovery_timeline", "evidence_timeline",
                    "growth_timeline", "launch_list", "unique_creators"):
            card[key] = []
        card["evidence_detail_href"] = f"/api/ops/emerging-operators/{family['family_id']}"
        card["profile_href"] = f"/intelligence/operations/{family['family_id']}"
        card["operational_intelligence_href"] = f"{card['profile_href']}?tab=operational"
        return card

    def get(self, entity: str) -> dict[str, Any] | None:
        entity = (entity or "").strip()
        all_families = self._compose()
        for family in all_families:
            if entity == family["family_id"] or entity in family["member_wallets"]:
                result = dict(family)
                result["profile_href"] = f"/intelligence/operations/{family['family_id']}"
                result["operational_intelligence_href"] = f"{result['profile_href']}?tab=operational"
                result["ecosystem_intelligence_href"] = f"{result['profile_href']}?tab=related"
                reconciliation = self._reconcile_token_states(all_families)
                result["intelligence"] = OperationIntelligenceAssembler(
                    self.ops_db_path, self.live_db_path
                ).build(result, all_families, reconciliation)
                return result
        return None

    def _reconcile_token_states(self, families: list[dict[str, Any]]) -> dict[str, Any]:
        """Build one exclusive token ledger across operation lifecycle states."""
        universe: set[str] = set()
        try:
            with self._connect(self.ops_db_path) as conn:
                tables = self._tables(conn)
                sources = (
                    ("wt_watchtower_launches", "mint"),
                    ("wt_provisioning_edges", "source_mint"),
                    ("wt_attribution_outcomes", "mint"),
                )
                for table, column in sources:
                    if table in tables and column in self._columns(conn, table):
                        universe.update(row[0] for row in conn.execute(
                            f"SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL"
                        ) if row[0])
        except (OSError, sqlite3.Error):
            pass

        category_for_stage = {
            "CONFIRMED": "confirmed", "EMERGING": "emerging",
            "ESTABLISHED": "emerging", "SIGNIFICANT_ACTIVE": "candidate",
            "CANDIDATE": "candidate", "BACKGROUND": "unknown",
            "DORMANT": "unknown", "RETIRED": "unknown",
        }
        priority = {"unknown": 0, "candidate": 1, "emerging": 2, "confirmed": 3}
        claims: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for family in families:
            category = category_for_stage.get(family["stage"], "unknown")
            for mint in family.get("launch_list") or []:
                if mint:
                    universe.add(mint)
                    claims[mint].append((category, family["family_id"]))

        assignments = {"confirmed": set(), "emerging": set(), "candidate": set(), "unknown": set()}
        overlap_mints = []
        for mint in sorted(universe):
            mint_claims = claims.get(mint, [])
            category = max((item[0] for item in mint_claims), key=priority.get, default="unknown")
            assignments[category].add(mint)
            if len({item[1] for item in mint_claims}) > 1:
                overlap_mints.append(mint)
        counts = {f"{key}_tokens": len(value) for key, value in assignments.items()}
        assigned_total = sum(counts.values())
        return {
            "total_tokens": len(universe), **counts, "assigned_total": assigned_total,
            "balanced": assigned_total == len(universe),
            "source_overlap_count": len(overlap_mints),
            "source_overlap_mints": overlap_mints[:25],
            "operation_counts": {
                "confirmed": sum(f["stage"] == "CONFIRMED" for f in families),
                "emerging": sum(f["stage"] in {"EMERGING", "ESTABLISHED"} for f in families),
                "candidate": sum(f["stage"] in {"CANDIDATE", "SIGNIFICANT_ACTIVE"} for f in families),
            },
            "contract": "each persisted discovery token is assigned once: confirmed, emerging, candidate, or unknown",
        }

    def recent_events(self, limit: int = 20) -> list[dict[str, Any]]:
        events = []
        composed = self._compose()
        event_families = [f for f in composed if f["stage"] in {"EMERGING", "CONFIRMED"}]
        if not event_families:
            event_families = sorted((f for f in composed if f["stage"] in {"CANDIDATE", "DORMANT"}),
                                    key=lambda f: (f["last_seen_at"] or 0, f["family_id"]), reverse=True)[:1]
        for family in event_families:
            if family["stage"] not in {"EMERGING", "CONFIRMED", "CANDIDATE", "DORMANT"}:
                continue
            events.append({
                "timestamp": family["last_seen_at"],
                "state": "EMERGING" if family["stage"] == "CANDIDATE" else family["stage"],
                "kind": "OPERATION_FAMILY_CONFIRMED" if family["stage"] == "CONFIRMED" else "EMERGING_CANDIDATE_STRENGTHENED",
                "message": f"{family['family_name']}: {family['observed_launches']} launches, {family['evidence_completeness']['score']}% evidence complete",
                "entity": {"id": family["family_id"], "type": "operation_family" if len(family["member_wallets"]) > 1 else "emerging_candidate"},
                "href": f"/intelligence/operations/{family['family_id']}",
            })
        return sorted(events, key=lambda e: (e["timestamp"] or 0, e["kind"]), reverse=True)[:max(1, min(int(limit), 50))]

    def _compose(self) -> list[dict[str, Any]]:
        now_mono = time.monotonic()
        if self._cached_families is not None and now_mono - self._cached_at < self.refresh_seconds:
            return self._cached_families
        if not os.path.exists(self.ops_db_path):
            return []
        # Keep the established identity evaluation in the projection.  A broken
        # optional evaluator must not hide persisted discovery evidence.
        try:
            evaluation = self._resolver_factory().evaluate()
            proposals = PromotionDecisionEngine().decide(evaluation)
        except Exception:
            evaluation, proposals = None, []
        with self._connect(self.ops_db_path) as ops:
            tables = self._tables(ops)
            families = self._canonical_families(ops, tables)
            profiles = self._discovery_profiles(ops, tables)
            families.extend(self._cluster_profiles(profiles, evaluation, proposals))
        eligible = sorted(
            (f for f in families if f["stage"] == "SIGNIFICANT_ACTIVE" and f["attention_eligible"]),
            key=lambda f: (f["discovery_significance"]["score"], f["evidence_completeness"]["score"], f["last_material_activity_at"] or 0, f["family_id"]),
            reverse=True,
        )
        for rank, family in enumerate(eligible, 1):
            family["attention_rank"] = rank
            if rank <= self.attention_budget:
                family["stage"] = family["lifecycle_state"] = "EMERGING"
                family["status"] = "Emerging"
            else:
                family["blocking_reasons"].append("outside current analyst attention budget")
        rank = {"CONFIRMED": 8, "EMERGING": 7, "ESTABLISHED": 6, "SIGNIFICANT_ACTIVE": 5, "CANDIDATE": 4, "DORMANT": 3, "BACKGROUND": 2, "RETIRED": 1}
        families.sort(key=lambda f: (rank[f["stage"]], f["discovery_significance"]["score"], f["last_seen_at"] or 0, f["family_id"]), reverse=True)
        self._cached_families, self._cached_at = families, now_mono
        return families

    def _canonical_families(self, conn, tables) -> list[dict[str, Any]]:
        if "operators" not in tables:
            return []
        columns = self._columns(conn, "operators")
        if "status" not in columns:
            return []
        rows = conn.execute("SELECT * FROM operators WHERE status='CONFIRMED' ORDER BY operator_id").fetchall()
        result = []
        for row in rows:
            op = dict(row)
            entities = []
            if "operator_entities" in tables:
                entities = [dict(r) for r in conn.execute(
                    "SELECT * FROM operator_entities WHERE operator_id=? ORDER BY entity_type,entity_address", (op["operator_id"],)
                )]
            treasuries = [e["entity_address"] for e in entities if e.get("entity_type") == "TREASURY"]
            clients = [e["entity_address"] for e in entities if e.get("entity_type") != "TREASURY"]
            launches = 0
            launch_list = []
            if "wt_watchtower_launches" in tables and str(op.get("display_name") or "").upper() == "WATCHTOWER":
                cols = self._columns(conn, "wt_watchtower_launches")
                mint_col = "mint" if "mint" in cols else "token_mint" if "token_mint" in cols else None
                if mint_col:
                    launch_list = [r[0] for r in conn.execute(f"SELECT DISTINCT {mint_col} FROM wt_watchtower_launches ORDER BY {mint_col}")]
                    launches = len(launch_list)
            name = op.get("display_name") or op["operator_id"]
            completeness = self._metric(EVIDENCE_WEIGHTS, EVIDENCE_WEIGHTS)
            result.append({
                "family_id": f"canonical:{op['operator_id']}", "family_name": name,
                "stage": "CONFIRMED", "status": "Confirmed", "lifecycle_state": "CONFIRMED",
                "first_seen_at": _timestamp(op.get("first_seen")), "last_seen_at": _timestamp(op.get("last_seen")),
                "last_material_activity_at": _timestamp(op.get("last_seen")), "state_changed_at": _timestamp(op.get("updated_at")),
                "merged_into_family_id": None, "superseded_by_family_id": None,
                "launches": launches, "observed_launches": launches, "launch_list": launch_list,
                "session_count": 0, "active_sessions": 0, "member_wallets": clients, "client_wallets": clients,
                "member_treasuries": treasuries, "treasuries": treasuries, "primary_treasury_families": treasuries,
                "unique_creators": [], "funding_mechanisms": [],
                "dominant_topology": "Canonical treasury → client → fresh creator",
                "observed_topology_variants": ["Canonical treasury → client → fresh creator"],
                "evidence_completeness": completeness, "evidence_breakdown": completeness["dimensions"],
                "missing_evidence": [], "evidence_sources": ["operators", "operator_entities", "manual_confirmation"],
                "discovery_significance": self._metric({"canonical_activity": 100}, {"canonical_activity": 100}),
                "significance_breakdown": [{"key": "canonical_activity", "label": "Canonical activity", "score": 100, "maximum": 100}],
                "material_change_reasons": [],
                "operational_maturity": self._metric({"canonical_history": 100}, {"canonical_history": 100}),
                "maturity_breakdown": [{"key": "canonical_history", "label": "Canonical history", "score": 100, "maximum": 100}],
                "promotion_ready": True, "promotion_status": "CONFIRMED", "blocking_reasons": [],
                "reviewable_reasons": ["manually confirmed through canonical promotion governance"],
                "cohesion": {"status": "CANONICAL", "score": 100, "evidence": ["manual confirmation"], "conflicts": []},
                "membership": [{"wallet": w, "membership_status": "CONFIRMED", "membership_strength": 100,
                                "membership_evidence": ["canonical operator entity"], "membership_conflicts": []} for w in clients],
                "exclusion_evidence": [], "data_quality_warnings": [], "attention_eligible": False,
                "attention_rank": None, "why_surfaced": ["canonical operation"], "dormancy_reason": None,
                "supporting_evidence": [{"type": "MANUAL_PROMOTION", "source": "operators", "detail": "Canonical operator confirmed through the existing review workflow."}],
                "discovery_timeline": [], "evidence_timeline": [], "growth_timeline": [],
                "current_classification": "Canonical", "terminal_entity": f"canonical:{op['operator_id']}",
                "observation_count": launches, "campaign_count": 0, "campaigns": [], "funding_templates": [],
                "identity_classes": [], "identity_class_count": len(treasuries), "confidence": op.get("confidence"),
                "review_status": "CONFIRMED", "review_label": "Confirmed", "promotion_handoff": None,
                "contradictions": [], "current_attribution_outcome": "CANONICAL_OPERATOR_REACHED",
                "is_canonical_operator": True, "canonical_operator_id": op["operator_id"], "read_only": True,
                "promotion_gates": {"passed": {"manual_confirmation": True}, "failed": [], "reason_not_surfaced": None},
                "previous_stage": "CONFIRMED_OPERATION",
            })
        return result

    def _discovery_profiles(self, conn, tables) -> dict[str, dict[str, Any]]:
        profiles: dict[str, dict[str, Any]] = {}
        def profile(wallet, source=None):
            existing = profiles.get(wallet)
            if existing is not None:
                if source:
                    existing["sources"].add(source)
                return existing
            sources = {source} if source else set()
            return profiles.setdefault(wallet, {
                "wallet": wallet, "sources": sources, "candidate": {}, "creators": set(),
                "treasuries": set(), "mechanisms": set(), "signatures": set(), "launches": set(),
                "first": None, "last": None, "sessions": 0, "active_sessions": 0,
                "session_times": set(), "evidence": [], "outcomes": [],
                "templates": defaultdict(int), "campaigns": set(), "session_amounts": [],
                "edge_times": [], "zero_value_edges": 0, "exclusions": [], "warnings": [],
            })
        if "wt_infrastructure_candidates" in tables:
            for row in conn.execute(
                "SELECT * FROM wt_infrastructure_candidates WHERE candidate_role IN ('OPERATIONAL_TREASURY','PROVISIONING_HUB')"
            ):
                data = dict(row); wallet = data["wallet"]
                p = profile(wallet, "wt_infrastructure_candidates"); p["candidate"] = data
                p["first"], p["last"] = _timestamp(data.get("first_seen_at")), _timestamp(data.get("last_seen_at"))
        # Seed profiles must exist before edge/session routing so exclusions see
        # the same complete evidence package as role-scored candidates.
        seeds = emerging_operator_seeds(conn) if "wt_unknown_infrastructure_registry" in tables else []
        for seed in seeds:
            p = profile(seed["terminal_entity"], "wt_attribution_outcomes")
            p["first"] = min(x for x in (p["first"], _timestamp(seed.get("first_seen_at"))) if x is not None)
            p["last"] = max(x for x in (p["last"], _timestamp(seed.get("last_seen_at"))) if x is not None)
        # Infrastructure scoring enriches a family; it must not be the object
        # that makes already-complete operating evidence routable.  Initialise
        # profiles whose persisted sessions and provisioning edges already
        # satisfy the existing singleton evidence package.  The normal loaders
        # below remain the single source of the profile's evidence and scores.
        for wallet in self._evidence_routable_wallets(conn, tables):
            profile(wallet)
        if "wt_provisioning_edges" in tables:
            for row in conn.execute("SELECT * FROM wt_provisioning_edges WHERE edge_type='SUBPROV_TO_CREATOR'"):
                edge = dict(row); wallet = edge["from_wallet"]
                if wallet not in profiles:
                    continue
                p = profiles[wallet]; p["sources"].add("wt_provisioning_edges"); p["creators"].add(edge["to_wallet"])
                if edge.get("funding_mechanism"): p["mechanisms"].add(edge["funding_mechanism"])
                if edge.get("funding_tx_signature"): p["signatures"].add(edge["funding_tx_signature"])
                if edge.get("source_mint"): p["launches"].add(edge["source_mint"])
                if edge.get("funding_amount_sol") is not None and float(edge["funding_amount_sol"]) <= 0:
                    p["zero_value_edges"] += 1; p["warnings"].append("zero-value funding edge requires independent corroboration")
                p["edge_times"].append(_timestamp(edge.get("last_observed_by_flex")) or 0)
                p["first"] = min(x for x in (p["first"], _timestamp(edge.get("first_observed_by_flex"))) if x is not None)
                p["last"] = max(x for x in (p["last"], _timestamp(edge.get("last_observed_by_flex"))) if x is not None)
                p["evidence"].append({"type": "FUNDING_EDGE", "source": "wt_provisioning_edges", "detail": edge})
        if "wt_active_subprov_sessions" in tables:
            for row in conn.execute("SELECT * FROM wt_active_subprov_sessions"):
                session = dict(row); wallet = session["subprov_wallet"]
                if wallet not in profiles:
                    continue
                p = profiles[wallet]; p["sources"].add("wt_active_subprov_sessions"); p["sessions"] += 1
                if session.get("state") == "ACTIVE": p["active_sessions"] += 1
                if session.get("treasury_wallet"): p["treasuries"].add(session["treasury_wallet"])
                if session.get("funding_mechanism"): p["mechanisms"].add(session["funding_mechanism"])
                if session.get("funding_signature"): p["signatures"].add(session["funding_signature"])
                if session.get("funding_time"): p["session_times"].add(int(session["funding_time"]))
                if session.get("funding_amount") is not None: p["session_amounts"].append(float(session["funding_amount"]))
        if seeds:
            for seed in seeds:
                wallet = seed["terminal_entity"]; p = profiles[wallet]
                outcomes = self._outcomes(conn, tables, wallet)
                p["outcomes"].extend(outcomes)
                p["launches"].update(r.get("mint") for r in outcomes if r.get("mint"))
                p["evidence"].extend(self._outcome_evidence(r) for r in outcomes)
                mints = sorted(p["launches"])
                if mints and "wt_walkback_queue" in tables:
                    cols = self._columns(conn, "wt_walkback_queue")
                    selected = [c for c in ("mint", "creator", "subprov", "treasury", "funding_mechanism", "funder_amount_sol") if c in cols]
                    marks = ",".join("?" for _ in mints)
                    for row in conn.execute(f"SELECT {','.join(selected)} FROM wt_walkback_queue WHERE mint IN ({marks})", mints):
                        data = dict(row)
                        if data.get("creator"): p["creators"].add(data["creator"])
                        if data.get("treasury"): p["treasuries"].add(data["treasury"])
                        if data.get("funding_mechanism"): p["mechanisms"].add(data["funding_mechanism"])
                        if data.get("funding_mechanism") or data.get("funder_amount_sol") is not None:
                            p["templates"][(data.get("funding_mechanism") or "Recorded funding", data.get("funder_amount_sol"))] += 1
                if mints and "wt_token_lifecycle" in tables and "operation_uuid" in self._columns(conn, "wt_token_lifecycle"):
                    marks = ",".join("?" for _ in mints)
                    p["campaigns"].update(r[0] for r in conn.execute(
                        f"SELECT DISTINCT operation_uuid FROM wt_token_lifecycle WHERE mint IN ({marks}) AND operation_uuid IS NOT NULL", mints
                    ))
        for p in profiles.values():
            label = get_funder_label(p["wallet"])
            if label: p["exclusions"].append({"type": label["kind"], "detail": label["name"], "source": "infra_mapping"})
            if self._dust_profile(p): p["exclusions"].append({"type": "DUST_PATTERN", "detail": "repeated low-variance micro-funding sessions", "source": "wt_active_subprov_sessions"})
            if p["zero_value_edges"] and len(p["signatures"]) < 2:
                p["exclusions"].append({"type": "UNSUPPORTED_ZERO_VALUE", "detail": "zero-value edges lack independent corroboration", "source": "wt_provisioning_edges"})
        return profiles

    def _evidence_routable_wallets(self, conn, tables) -> set[str]:
        """Return wallets independently routable from existing hard evidence.

        These are routing requirements, not new promotion gates.  They mirror
        the evidence already required by ``singleton_lane``: a reconstructable
        treasury-to-client-to-creator topology, five repeated creator
        relationships, a funding mechanism, and transaction provenance.
        Multi-member cohesion and every lifecycle/promotion threshold are still
        evaluated later by the unchanged family builder.
        """
        required = {"wt_provisioning_edges", "wt_active_subprov_sessions"}
        if not required.issubset(tables):
            return set()
        edge_rows = conn.execute(
            """
            SELECT from_wallet AS wallet,
                   COUNT(DISTINCT to_wallet) AS creator_count,
                   COUNT(DISTINCT CASE WHEN funding_tx_signature IS NOT NULL
                                       THEN funding_tx_signature END) AS signature_count,
                   COUNT(DISTINCT CASE WHEN funding_mechanism IS NOT NULL
                                       THEN funding_mechanism END) AS mechanism_count
              FROM wt_provisioning_edges
             WHERE edge_type='SUBPROV_TO_CREATOR'
             GROUP BY from_wallet
            HAVING creator_count >= 5
            """
        )
        wallets = set()
        # The session table is large, while its unique key begins with
        # subprov_wallet.  Narrowing by qualifying edge wallets first avoids a
        # full session-table aggregation on every page refresh.
        for edge in edge_rows:
            session = conn.execute(
                """
                SELECT COUNT(DISTINCT treasury_wallet) AS treasury_count,
                       COUNT(DISTINCT CASE WHEN funding_signature IS NOT NULL
                                           THEN funding_signature END) AS signature_count,
                       COUNT(DISTINCT CASE WHEN funding_mechanism IS NOT NULL
                                           THEN funding_mechanism END) AS mechanism_count
                  FROM wt_active_subprov_sessions
                 WHERE subprov_wallet=?
                """,
                (edge["wallet"],),
            ).fetchone()
            if (session["treasury_count"] >= 1
                    and edge["signature_count"] + session["signature_count"] >= 1
                    and edge["mechanism_count"] + session["mechanism_count"] >= 1):
                wallets.add(edge["wallet"])
        return wallets

    def _cluster_profiles(self, profiles, evaluation, proposals) -> list[dict[str, Any]]:
        # Complete-link clustering: every new member must independently match
        # every member already in the family, preventing transitive bridge merges.
        wallets = sorted(profiles)
        groups: list[list[dict[str, Any]]] = []
        for wallet in wallets:
            candidate = profiles[wallet]; placed = False
            for group in groups:
                if all(self._cohesion_pair(candidate, member)["valid"] for member in group):
                    group.append(candidate); placed = True; break
            if not placed: groups.append([candidate])
        return [self._profile_family(group, evaluation, proposals) for group in groups]

    @staticmethod
    def _cohesion_pair(left, right):
        shared_treasuries = left["treasuries"] & right["treasuries"]
        shared_mechanisms = left["mechanisms"] & right["mechanisms"]
        evidence = []
        if len(shared_treasuries) >= 2: evidence.append("repeated shared treasury relationships")
        if shared_mechanisms: evidence.append("consistent downstream funding pattern")
        if len(left["creators"]) >= 5 and len(right["creators"]) >= 5: evidence.append("independently repeated client topology")
        if left["signatures"] and right["signatures"]: evidence.append("transaction-provenance coverage")
        conflicts = []
        if left["exclusions"] or right["exclusions"]: conflicts.append("member exclusion evidence")
        repeated_clients = len(left["creators"]) >= 5 and len(right["creators"]) >= 5
        valid = len(evidence) >= 3 and len(shared_treasuries) >= 2 and repeated_clients and not conflicts
        return {"valid": valid, "strength": min(100, len(evidence) * 25), "evidence": evidence, "conflicts": conflicts}

    def _profile_family(self, group, evaluation, proposals) -> dict[str, Any]:
        members = sorted(p["wallet"] for p in group)
        treasuries = sorted(set().union(*(p["treasuries"] for p in group)))
        creators = sorted(set().union(*(p["creators"] for p in group)))
        mechanisms = sorted(set().union(*(p["mechanisms"] for p in group)))
        signatures = set().union(*(p["signatures"] for p in group))
        launches = sorted(set().union(*(p["launches"] for p in group)))
        launch_count = max(len(launches), len(creators), max((int(p["candidate"].get("distinct_launches") or 0) for p in group), default=0))
        firsts = [p["first"] for p in group if p["first"] is not None]
        lasts = [p["last"] for p in group if p["last"] is not None]
        first, last = min(firsts) if firsts else None, max(lasts) if lasts else None
        sessions = sum(p["sessions"] for p in group)
        outgoing = any(p["creators"] for p in group)
        complete_topology = bool(treasuries and outgoing and mechanisms)
        observation_count = sum(len(p["outcomes"]) for p in group)
        exclusions = [item for p in group for item in p["exclusions"]]
        warnings = sorted(set(item for p in group for item in p["warnings"]))
        persistent = sessions >= 2 or bool(first and last and last - first >= 86400 * 2)
        shared_treasury_count = 0
        if len(group) > 1:
            shared_treasury_count = len(set.intersection(*(p["treasuries"] for p in group)))
        sources = sorted(set().union(*(p["sources"] for p in group)))
        evidence_breakdown = {
            "topology_reconstruction": 20 if complete_topology else 10 if outgoing and mechanisms else 0,
            "transaction_provenance": 20 if len(signatures) >= 2 else 10 if signatures else 0,
            "relationship_coverage": 15 if shared_treasury_count >= 2 else 8 if treasuries and outgoing else 0,
            "identity_link_coverage": 15 if len(group) > 1 and shared_treasury_count >= 2 else 7 if treasuries else 0,
            "independent_evidence_diversity": min(15, 3 * len(sources)),
            "uncertainty_conflict_coverage": 15 if not exclusions and not warnings else 8 if not exclusions else 0,
        }
        completeness = self._metric(evidence_breakdown, EVIDENCE_WEIGHTS)
        now = int(time.time()); recent_cutoff = now - 14 * 86400
        recent_launches = sum(1 for p in group for ts in p["edge_times"] if ts >= recent_cutoff)
        recent = bool(last and last >= recent_cutoff)
        duration_days = max(0, ((last or 0) - (first or last or 0)) // 86400)
        significance_values = {
            "recent_launch_activity": min(30, recent_launches * 5 if recent_launches else (15 if recent and launch_count >= 5 else 5 if recent else 0)),
            "launch_velocity": min(15, recent_launches * 3),
            "active_session_breadth": min(15, sum(p["active_sessions"] for p in group) * 5 + min(10, sessions)),
            "coherent_member_breadth": 20 if len(group) > 1 else 0,
            "operational_persistence": 10 if duration_days >= 7 else 5 if duration_days >= 2 else 0,
            "topology_novelty": 10 if treasuries and complete_topology else 0,
        }
        significance = self._metric(significance_values, {k: v for k, v in (("recent_launch_activity",30),("launch_velocity",15),("active_session_breadth",15),("coherent_member_breadth",20),("operational_persistence",10),("topology_novelty",10))})
        maturity_values = {
            "observed_duration": min(30, duration_days),
            "independent_sessions": min(25, sessions * 3),
            "topology_stability": 20 if complete_topology and launch_count >= 5 else 10 if complete_topology else 0,
            "membership_stability": 15 if len(group) > 1 else 5,
            "conflict_survival": 10 if not exclusions else 0,
        }
        maturity = self._metric(maturity_values, {"observed_duration":30,"independent_sessions":25,"topology_stability":20,"membership_stability":15,"conflict_survival":10})
        membership = []
        for member in group:
            pair_results = [self._cohesion_pair(member, other) for other in group if other is not member]
            evidence = sorted(set(x for result in pair_results for x in result["evidence"]))
            conflicts = sorted(set(x for result in pair_results for x in result["conflicts"]))
            membership.append({"wallet": member["wallet"], "membership_status": "CORE" if len(group) > 1 else "SINGLETON_HYPOTHESIS",
                               "membership_strength": min((r["strength"] for r in pair_results), default=0),
                               "membership_evidence": evidence, "membership_conflicts": conflicts})
        cohesion_valid = len(group) > 1 and all(m["membership_strength"] >= 50 and not m["membership_conflicts"] for m in membership)
        singleton_lane = len(group) == 1 and complete_topology and len(creators) >= 5 and len(signatures) >= 1
        topology_reviewable = complete_topology and len(signatures) >= 1
        promotion_ready = not exclusions and topology_reviewable and (cohesion_valid or singleton_lane)
        blocking = []
        if exclusions: blocking.append("exclusion evidence must be resolved")
        if not complete_topology: blocking.append("complete operating topology not reconstructed")
        if not signatures: blocking.append("missing transaction provenance")
        if len(group) == 1: blocking.append("family breadth unresolved")
        if len(group) > 1 and not cohesion_valid: blocking.append("family cohesion review required")
        eligible = promotion_ready and recent and significance["score"] >= 55 and completeness["score"] >= 60
        stale = bool(last and last < now - self.dormancy_days * 86400)
        if exclusions: stage = "RETIRED"
        elif stale and (launch_count or sessions): stage = "DORMANT"
        elif eligible: stage = "SIGNIFICANT_ACTIVE"
        elif (complete_topology or (outgoing and mechanisms and len(sources) >= 2)) and (launch_count or sessions or observation_count):
            stage = "CANDIDATE"
        else: stage = "BACKGROUND"
        old_repeated = outgoing and launch_count >= self.minimum_launches and bool(mechanisms)
        old_persistent = sessions >= 2 or observation_count >= 2 or bool(first and last and last - first >= 86400)
        old_score = ((25 if old_repeated and treasuries else 15 if old_repeated else 0)
                     + (min(20, 5 * min(4, launch_count)) if old_repeated else 0)
                     + min(20, 5 * min(4, len(signatures))) + min(15, 3 * min(5, launch_count))
                     + (10 if shared_treasury_count >= 2 else 7 if sessions >= 2 and treasuries else 3 if treasuries else 0)
                     + (10 if old_persistent and (sessions >= 2 or observation_count >= 4) else 7 if old_persistent else 0))
        # X67.25 only routed sessions to its initially selected candidate roles.
        old_saw_dust = any(p["candidate"].get("candidate_role") in {"OPERATIONAL_TREASURY", "HUB"} and self._dust_profile(p) for p in group)
        old_stage = "EMERGING_OPERATION" if launch_count >= self.minimum_launches and old_repeated and old_persistent and old_score >= self.completeness_threshold and not old_saw_dust else "CANDIDATE_FAMILY"
        anchor = min(group, key=lambda p: (p["first"] if p["first"] is not None else 2**63, p["wallet"]))["wallet"]
        family_hash = hashlib.sha256(f"operation-family-anchor:{anchor}".encode()).hexdigest()[:16]
        absorbed_ids = [
            "family:" + hashlib.sha256(f"operation-family-anchor:{member}".encode()).hexdigest()[:16]
            for member in members if member != anchor
        ]
        name = f"{_short(members[0])} Family" if len(members) == 1 else f"{_short(members[0])} / {_short(members[1])} Family"
        evidence = []
        for p in group: evidence.extend(p["evidence"])
        timeline = self._evidence_timeline(group, first)
        topology = "Treasury → persistent client → fresh creator" if complete_topology else "Evidence accumulation incomplete"
        missing = []
        if not treasuries: missing.append("treasury relationships")
        if not signatures: missing.append("transaction provenance")
        if not outgoing: missing.append("downstream creator relationships")
        family = {
            "family_id": f"family:{family_hash}", "family_name": name, "stage": stage, "status": stage.replace("_", " ").title(),
            "family_anchor": anchor,
            "previous_stage": old_stage,
            "lifecycle_state": stage, "first_seen_at": first, "last_seen_at": last, "last_material_activity_at": last,
            "state_changed_at": last, "merged_into_family_id": None, "superseded_by_family_id": None,
            "absorbed_family_ids": absorbed_ids,
            "launches": launch_count, "observed_launches": launch_count, "launch_list": launches,
            "session_count": sessions, "active_sessions": sum(p["active_sessions"] for p in group),
            "member_wallets": members, "client_wallets": members, "member_treasuries": treasuries,
            "treasuries": treasuries, "primary_treasury_families": treasuries, "unique_creators": creators,
            "funding_mechanisms": mechanisms, "dominant_topology": topology, "observed_topology_variants": [topology],
            "evidence_completeness": completeness, "evidence_breakdown": completeness["dimensions"],
            "missing_evidence": missing, "evidence_sources": sources,
            "discovery_significance": significance, "significance_breakdown": significance["dimensions"],
            "material_change_reasons": self._material_reasons(recent_launches, len(group), recent, stage),
            "operational_maturity": maturity, "maturity_breakdown": maturity["dimensions"],
            "promotion_ready": promotion_ready, "promotion_status": "REVIEWABLE" if promotion_ready else "BLOCKED",
            "blocking_reasons": blocking, "reviewable_reasons": (["cohesion validated" if cohesion_valid else "significant unresolved operation lane", "topology and transaction evidence available"] if promotion_ready else []),
            "cohesion": {"status": "VALIDATED" if cohesion_valid else "SIGNIFICANT_UNRESOLVED" if singleton_lane else "UNPROVEN",
                         "score": min((m["membership_strength"] for m in membership), default=0),
                         "evidence": sorted(set(x for m in membership for x in m["membership_evidence"])),
                         "conflicts": sorted(set(x for m in membership for x in m["membership_conflicts"]))},
            "merge_split_decisions": ([{"decision": "MERGED", "members": members,
                                         "reason": "complete-link cohesion validated for every member"}]
                                      if len(group) > 1 else [{"decision": "RETAIN_SINGLETON",
                                                              "members": members,
                                                              "reason": "no complete-link family match"}]),
            "membership": membership, "exclusion_evidence": exclusions, "data_quality_warnings": warnings,
            "attention_eligible": eligible, "attention_rank": None,
            "why_surfaced": self._material_reasons(recent_launches, len(group), recent, stage),
            "dormancy_reason": "no material activity inside dormancy window" if stage == "DORMANT" else None,
            "supporting_evidence": evidence, "discovery_timeline": timeline, "evidence_timeline": timeline,
            "growth_timeline": timeline, "current_classification": "Monitoring",
            "terminal_entity": members[0], "observation_count": launch_count, "campaign_count": sessions,
            "identity_class_count": len(treasuries), "review_status": "MONITORING", "review_label": stage.replace("_", " ").title(),
            "promotion_handoff": None, "contradictions": [], "read_only": True,
            "promotion_gates": {"passed": {"cohesion_or_singleton_lane": cohesion_valid or singleton_lane,
                                               "promotion_ready": promotion_ready, "current_significance": significance["score"] >= 55,
                                               "recent_material_activity": recent, "exclusions_clear": not exclusions},
                                "failed": [reason for reason in blocking],
                                "reason_not_surfaced": None if eligible else "; ".join(blocking or ["insufficient current significance"])},
        }
        templates = defaultdict(int)
        for p in group:
            for key, count in p["templates"].items(): templates[key] += count
        family["funding_templates"] = [
            {"mechanism": key[0], "amount_sol": key[1], "observation_count": count}
            for key, count in sorted(templates.items(), key=lambda item: str(item[0]))
        ]
        family["campaigns"] = sorted(set().union(*(p["campaigns"] for p in group)))
        family["campaign_count"] = len(family["campaigns"])
        linked_identity = [] if evaluation is None else [item for item in evaluation.identity if any(w in item.entities for w in members)]
        linked_keys = {item.candidate_key for item in linked_identity}
        linked_proposals = [p for p in proposals if p.candidate_key in linked_keys]
        linked_proposals.sort(key=lambda p: (len(p.identity_classes), p.identity_confidence, p.candidate_key), reverse=True)
        primary = linked_proposals[0] if linked_proposals else None
        family["identity_classes"] = list(primary.identity_classes) if primary else []
        family["identity_class_count"] = len(family["identity_classes"])
        family["confidence"] = primary.identity_confidence if primary else None
        family["review_status"] = primary.decision if primary else "MONITORING"
        family["review_label"] = family["review_status"].replace("_", " ").title()
        family["current_attribution_outcome"] = "UNKNOWN_INFRASTRUCTURE"
        family["is_canonical_operator"] = False
        family["canonical_operator_id"] = None
        if primary:
            family["promotion_handoff"] = {
                "proposal_id": primary.proposal_id, "proposal_fingerprint": primary.proposal_fingerprint,
                "identity_fingerprint": primary.identity_fingerprint,
                "href": "/intelligence/operator-promotions", "requires_analyst_approval": True,
            }
            if primary.decision == "PROMOTION_ELIGIBLE":
                family["growth_timeline"].append({
                    "event_type": "PROMOTION_ELIGIBLE", "timestamp": last,
                    "day": self._day(first, last), "label": "Promotion eligible — analyst approval required",
                    "observation_count": launch_count,
                })
        return family

    @staticmethod
    def _metric(values, maxima):
        dimensions = [{"key": key, "label": key.replace("_", " ").title(), "score": int(values.get(key, 0)), "maximum": maximum}
                      for key, maximum in maxima.items()]
        return {"score": sum(x["score"] for x in dimensions), "maximum": sum(maxima.values()), "dimensions": dimensions}

    @staticmethod
    def _material_reasons(recent_launches, member_count, recent, stage):
        reasons = []
        if recent_launches >= 3: reasons.append("recent launch activity")
        if member_count > 1: reasons.append("multiple coherent clients")
        if recent and not reasons: reasons.append("recent material evidence")
        if stage == "DORMANT": reasons.append("inactive beyond dormancy window")
        return reasons

    @staticmethod
    def _dust_profile(profile: dict[str, Any]) -> bool:
        amounts = profile.get("session_amounts") or []
        return len(amounts) >= 10 and max(amounts) - min(amounts) <= 0.01 and max(amounts) <= 0.2

    @staticmethod
    def _evidence_timeline(group, first):
        events = []
        for profile in group:
            for i, outcome in enumerate(sorted(profile["outcomes"], key=lambda x: (_timestamp(x.get("completed_at")) or 0, x.get("mint") or "")), 1):
                ts = _timestamp(outcome.get("completed_at"))
                events.append({"event_type": "EVIDENCE_OBSERVED", "timestamp": ts,
                               "day": EmergingOperatorService._day(first, ts),
                               "label": f"Persisted operation evidence for {_short(profile['wallet'])}",
                               "observation_count": i, "source_mint": outcome.get("mint")})
            for ts in sorted(profile["session_times"]):
                events.append({"event_type": "SESSION_OBSERVED", "timestamp": ts,
                               "day": EmergingOperatorService._day(first, ts),
                               "label": f"Funding session observed for {_short(profile['wallet'])}",
                               "observation_count": profile["sessions"]})
        return sorted(events, key=lambda e: (e.get("timestamp") or 0, e["event_type"], e["label"]))

    @staticmethod
    def _day(first: int | None, current: int | None) -> int:
        if not first or not current: return 1
        return max(1, ((current - first) // 86400) + 1)

    @staticmethod
    def _outcome_evidence(row: dict[str, Any]) -> dict[str, Any]:
        return {"type": "ATTRIBUTION_OUTCOME", "source": "wt_attribution_outcomes",
                "mint": row.get("mint"), "completed_at": row.get("completed_at"),
                "detail": row.get("stop_reason"), "evidence": _json(row.get("evidence_json"), {}),
                "href": f"/discovery?entity={row.get('mint')}&type=token"}

    @staticmethod
    def _outcomes(conn, tables, entity):
        if "wt_attribution_outcomes" not in tables: return []
        return [dict(row) for row in conn.execute(
            "SELECT * FROM wt_attribution_outcomes WHERE terminal_entity=? "
            "AND outcome_type='UNKNOWN_INFRASTRUCTURE' AND should_seed_emerging_operator=1 ORDER BY completed_at,mint",
            (entity,),
        )]
