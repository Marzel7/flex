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
from collections import Counter, defaultdict
from typing import Any, Callable

from src.ops.attribution_outcome import emerging_operator_seeds
from src.ops.identity_framework import PromotionDecisionEngine
from src.ops.investigation_population import (
    InvestigationPopulationBuilder,
    LegacyFamilyProjectionAdapter,
)
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

    def _snapshot_is_trustworthy(self) -> bool:
        """X72.0: the on-disk snapshot store (src.ops.intelligence_snapshots)
        is a single GLOBAL file location, keyed only by (function,
        window_seconds) -- it has no concept of which (ops_db_path,
        live_db_path) pair produced it, because operational_intelligence/
        pipeline_health (its original consumers) only ever run against one
        fixed production DB pair. EmergingOperatorService, unlike those, is
        explicitly instantiable against ARBITRARY db paths (every unit test
        does this with tmp_path fixtures). Reading the global snapshot for
        an instance pointed at a different DB pair would silently return
        unrelated (in tests: real production) data instead of computing
        fresh from the instance's own databases -- exactly the bug this
        guard exists to prevent. Only the real production service (the
        module-level singleton in operator_routes.py, constructed with
        src.core.db.OPS_DB_PATH/DB_PATH) is allowed to trust the snapshot;
        every other instantiation always computes live."""
        try:
            from src.core.db import OPS_DB_PATH, DB_PATH
        except Exception:
            return False
        return (
            os.path.realpath(self.ops_db_path) == os.path.realpath(str(OPS_DB_PATH))
            and os.path.realpath(self.live_db_path) == os.path.realpath(str(DB_PATH))
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

    @staticmethod
    def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}

    def _list_uncached(self, limit: int = 200, debug: bool = False) -> dict[str, Any]:
        """The full, expensive computation (_compose() + the reconciliation
        pass) -- unchanged logic, previously named list(). X72.0: this now
        runs ONLY inside the standalone snapshot scheduler process (see
        src.ops.emerging_operators_snapshot.build_emerging_operators_
        snapshot), never on a request thread. list() below is the
        request-facing method and only reads the published snapshot."""
        limit = max(1, min(int(limit), 500))
        all_families = self._compose()
        try:
            from src.ops.reconciliation_metadata import build_reconciliation_metadata
            reconciliation_by_family = build_reconciliation_metadata(self, all_families)
        except Exception:
            reconciliation_by_family = {}
        from src.ops.reconciliation_presentation import reconciliation_presentation

        # Promoted source populations remain in the immutable family snapshot
        # and are directly addressable by profile/API ID, but the canonical
        # child Operation owns launch accounting and analyst-queue placement.
        # This preserves the population without double-counting its launches.
        surface_families = [
            family for family in all_families
            if not family.get("promoted_to_operation_id")
        ]

        def reconciled_card(family):
            card = self._list_card(family)
            metadata = reconciliation_by_family.get(str(family.get("family_id")))
            if metadata is not None:
                card["reconciliation"] = metadata
            card["presentation"] = reconciliation_presentation(family, metadata)
            return card

        visible_full = [f for f in surface_families if f["stage"] in {"EMERGING", "ESTABLISHED", "CONFIRMED"}][:limit]
        visible = [self._list_card(f) for f in visible_full]
        candidate_summary_full = sorted(
            (f for f in surface_families if f["stage"] in {"CANDIDATE", "SIGNIFICANT_ACTIVE"}),
            key=lambda f: (f["discovery_significance"]["score"], f["last_material_activity_at"] or 0, f["family_id"]),
            reverse=True,
        )[:self.candidate_summary_limit]
        if not candidate_summary_full:
            candidate_summary_full = sorted(
                (f for f in surface_families if f["stage"] not in {"CONFIRMED", "RETIRED", "BACKGROUND"}),
                key=lambda f: (f["last_material_activity_at"] or 0, f["family_id"]), reverse=True,
            )[:self.candidate_summary_limit]
        candidate_summary = ([self._list_card(f) for f in candidate_summary_full]
                             if visible_full else candidate_summary_full)
        compatibility_candidates = visible if visible else candidate_summary
        disposition_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for family in surface_families:
            metadata = reconciliation_by_family.get(str(family.get("family_id")))
            if metadata:
                disposition_groups[str(metadata.get("disposition") or "UNRESOLVED")].append(family)
        material_key = lambda f: (
            int(f.get("launches") or 0),
            int((f.get("discovery_significance") or {}).get("score") or 0),
            int(f.get("last_material_activity_at") or 0),
            str(f.get("family_id") or ""),
        )
        confirmed_reconciled = [reconciled_card(f) for f in sorted(
            disposition_groups["CONFIRMED_OPERATION"], key=material_key, reverse=True
        )]
        active_investigations = [reconciled_card(f) for f in sorted(
            disposition_groups["UNRESOLVED"], key=material_key, reverse=True
        )[:5]]
        operator_candidates = [reconciled_card(f) for f in sorted(
            disposition_groups["OPERATOR_CANDIDATE"], key=material_key, reverse=True
        )[:5]]
        review_cases = [reconciled_card(f) for f in sorted(
            disposition_groups["REVIEW"], key=material_key, reverse=True
        )[:5]]
        infrastructure_alerts = [reconciled_card(f) for f in sorted(
            disposition_groups["INFRASTRUCTURE"], key=material_key, reverse=True
        )[:5]]
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
            "confirmed_operations_reconciled": confirmed_reconciled,
            "active_investigations_reconciled": active_investigations,
            "operator_candidates_reconciled": operator_candidates,
            "review_cases_reconciled": review_cases,
            "infrastructure_alerts_reconciled": infrastructure_alerts,
            "attention_budget_reconciled": {
                "operator_candidates_visible": len(operator_candidates),
                "operator_candidates_maximum": 5,
                "review_visible": len(review_cases),
                "infrastructure_visible": len(infrastructure_alerts),
                "review_infrastructure_maximum": 10,
            },
            "reconciled_counts": self._reconcile_disposition_states(
                surface_families, reconciliation_by_family
            ),
        }
        if debug:
            result["debug"] = self._debug_block(all_families)
        return result

    @staticmethod
    def _debug_block(all_families: list[dict[str, Any]]) -> dict[str, Any]:
        """X72.0: extracted so list() (the snapshot-reading, request-facing
        method) can compute this cheaply, in memory, from the snapshot's
        already-loaded `families` list only when a caller actually passes
        debug=1 -- previously this ~5.7MB block was baked into EVERY
        snapshot write regardless of whether any request ever asked for it,
        roughly doubling the on-disk/in-memory snapshot size and adding
        60-85ms of pure JSON parse time to every single warm request."""
        hidden = [f for f in all_families if f["stage"] not in {"EMERGING", "ESTABLISHED", "CONFIRMED"}]
        return {
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

    def list(self, limit: int = 200, debug: bool = False) -> dict[str, Any]:
        """X72.0 request-facing entry point. Reads the last published
        snapshot (built entirely off the request thread by the standalone
        intelligence_snapshot_scheduler process) and slices it in memory --
        never invokes _list_uncached()/_compose() here. `limit` is a pure
        truncation of the snapshot's already-deterministically-ordered
        `families` list (verified: slicing a limit=500 build to N gives an
        identical result to building at limit=N directly), so one snapshot,
        always built at the maximum limit, serves every limit a caller asks
        for. `debug` only controls whether the (already-computed) debug
        block is included in the response, never a rebuild.

        Startup fallback: if no snapshot has ever been published yet (fresh
        deploy, scheduler not yet run once), falls back to the original
        synchronous computation exactly once per cold key -- this matches
        Phase 4's explicit "existing startup behaviour only until the first
        snapshot has been published" requirement. After that first
        publication, this branch is never taken again."""
        snapshot = None
        if self._snapshot_is_trustworthy():
            from src.ops.emerging_operators_snapshot import read_emerging_operators_snapshot
            snapshot = read_emerging_operators_snapshot()
        if snapshot is None:
            return self._list_uncached(limit=limit, debug=debug)

        limit = max(1, min(int(limit), 500))
        payload = snapshot.payload
        full = payload.get("list_max") or {}
        result = dict(full)
        families = result.get("families") or []
        visible = families[:limit]
        result["families"] = visible
        result["count"] = len(visible)
        result["total"] = len(visible)
        result["confirmed_operations"] = [f for f in visible if f["stage"] == "CONFIRMED"]
        result["emerging_operations"] = [f for f in visible if f["stage"] == "EMERGING"]
        result["established_changes"] = [
            f for f in visible if f["stage"] == "ESTABLISHED" and f["material_change_reasons"]
        ]
        if debug:
            raw_families = payload.get("families") or []
            result["debug"] = self._debug_block(raw_families)
        else:
            result.pop("debug", None)
        result["snapshot_meta"] = {
            "generated_at": payload.get("generated_at"),
            "build_duration_ms": payload.get("build_duration_ms"),
            "snapshot_version": snapshot.snapshot_version,
            "age_seconds": round(time.time() - snapshot.computed_at, 1),
        }
        return result

    @staticmethod
    def _list_card(family: dict[str, Any]) -> dict[str, Any]:
        card = dict(family)
        from src.ops.operational_role import derive_operational_role
        card["operational_role"] = derive_operational_role(family)
        # Preserve the aggregate needed by the compact dashboard before the
        # potentially large creator-address list is removed from list payloads.
        card["creator_count"] = len(family.get("unique_creators") or ())
        for key in ("supporting_evidence", "discovery_timeline", "evidence_timeline",
                    "growth_timeline", "launch_list", "unique_creators"):
            card[key] = []
        card["evidence_detail_href"] = f"/api/ops/emerging-operators/{family['family_id']}"
        card["profile_href"] = f"/intelligence/operations/{family['family_id']}"
        card["operational_intelligence_href"] = f"{card['profile_href']}?tab=operational"
        return card

    def get(self, entity: str) -> dict[str, Any] | None:
        """X72.0: all_families and the reconciliation-by-family map now come
        from the published snapshot (never _compose()/build_reconciliation_
        metadata on the request thread -- the latter alone measured ~7.4s
        standalone). _reconcile_token_states() and OperationIntelligence
        Assembler.build() remain live per-request: both are genuinely
        entity/request-shaped and measured fast (~55-106ms combined with a
        warm snapshot) -- well inside the <100ms warm-request target, so
        there is no reliability upside to snapshotting them and a real cost
        (staleness, memory) to doing so."""
        entity = (entity or "").strip()
        all_families, reconciliation_by_family = self._snapshot_families_and_reconciliation()
        for family in all_families:
            if entity == family["family_id"] or entity in family["member_wallets"]:
                result = dict(family)
                result["profile_href"] = f"/intelligence/operations/{family['family_id']}"
                result["operational_intelligence_href"] = f"{result['profile_href']}?tab=operational"
                result["ecosystem_intelligence_href"] = f"{result['profile_href']}?tab=related"
                metadata = reconciliation_by_family.get(str(result.get("family_id")))
                from src.ops.reconciliation_presentation import reconciliation_presentation
                if metadata is not None:
                    result["reconciliation"] = metadata
                result["presentation"] = reconciliation_presentation(result, metadata)
                reconciliation = self._reconcile_token_states(all_families)
                result["intelligence"] = OperationIntelligenceAssembler(
                    self.ops_db_path, self.live_db_path
                ).build(result, all_families, reconciliation)
                result["operational_role"] = result["intelligence"]["operational_role"]
                return result
        return None

    def _snapshot_families_and_reconciliation(
        self,
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        """Shared snapshot read for get()/recent_events(). Falls back to the
        original synchronous computation when no snapshot has EVER been
        published yet (cold start, Phase 4's explicit startup exception) OR
        when this instance isn't pointed at the production DB pair the
        snapshot was built from (see _snapshot_is_trustworthy) -- every
        production request after the first successful scheduler build
        reads the snapshot exclusively."""
        snapshot = None
        if self._snapshot_is_trustworthy():
            from src.ops.emerging_operators_snapshot import read_emerging_operators_snapshot
            snapshot = read_emerging_operators_snapshot()
        if snapshot is None:
            all_families = self._compose()
            try:
                from src.ops.reconciliation_metadata import build_reconciliation_metadata
                reconciliation_by_family = build_reconciliation_metadata(self, all_families)
            except Exception:
                reconciliation_by_family = {}
            return all_families, reconciliation_by_family
        payload = snapshot.payload
        return payload.get("families") or [], payload.get("reconciliation_by_family") or {}

    def _reconcile_disposition_states(
        self,
        families: list[dict[str, Any]],
        reconciliation_by_family: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Exclusive launch ledger and separately-unitised population counts."""
        universe: set[str] = set()
        try:
            with self._connect(self.ops_db_path) as conn:
                tables = self._tables(conn)
                for table, column in (
                    ("wt_watchtower_launches", "mint"),
                    ("wt_provisioning_edges", "source_mint"),
                    ("wt_attribution_outcomes", "mint"),
                ):
                    if table in tables and column in self._columns(conn, table):
                        universe.update(row[0] for row in conn.execute(
                            f"SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL"
                        ) if row[0])
        except (OSError, sqlite3.Error):
            pass
        priority = {
            "UNKNOWN": 0, "REJECTED": 1, "UNRESOLVED": 2,
            "INFRASTRUCTURE": 3, "REVIEW": 4, "OPERATOR_CANDIDATE": 5,
            "CONFIRMED_OPERATION": 6,
        }
        claims: dict[str, list[tuple[str, str]]] = defaultdict(list)
        population_counts = Counter()
        for family in families:
            metadata = reconciliation_by_family.get(str(family.get("family_id")))
            disposition = str((metadata or {}).get("disposition") or "UNKNOWN")
            population_counts[disposition] += 1
            for mint in family.get("launch_list") or ():
                if mint:
                    universe.add(mint)
                    claims[str(mint)].append((disposition, str(family.get("family_id"))))
        launch_sets: dict[str, set[str]] = defaultdict(set)
        overlaps = 0
        for mint in universe:
            mint_claims = claims.get(mint, ())
            disposition = max(
                (claim[0] for claim in mint_claims),
                key=lambda value: priority.get(value, 0), default="UNKNOWN",
            )
            launch_sets[disposition].add(mint)
            if len({claim[1] for claim in mint_claims}) > 1:
                overlaps += 1
        return {
            "population_counts": dict(sorted(population_counts.items())),
            "population_unit": "populations",
            "launch_counts": {
                key: len(launch_sets.get(key, set())) for key in (
                    "CONFIRMED_OPERATION", "OPERATOR_CANDIDATE", "REVIEW",
                    "INFRASTRUCTURE", "UNRESOLVED", "REJECTED", "UNKNOWN",
                )
            },
            "launch_unit": "launches",
            "total_launches": len(universe),
            "assigned_launches": sum(len(values) for values in launch_sets.values()),
            "exclusive": True,
            "source_overlap_count": overlaps,
            "contract": "Each persisted launch is displayed once using the highest reconciled disposition; population counts are reported separately.",
        }

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
        """X72.0: reads the snapshot's families (never _compose() on the
        request thread once a snapshot exists)."""
        events = []
        composed, _ = self._snapshot_families_and_reconciliation()
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
            populations = self._population_builder().build(profiles)
            adapter = self._legacy_adapter(evaluation, proposals)
            projected = [adapter.project(population) for population in populations]
            # A canonical operator and its pre-promotion investigation are one
            # analyst identity, not two registry cards.  Carry the observed
            # population metrics onto the canonical record and suppress the
            # superseded investigation projection.  The canonical tables stay
            # authoritative for disposition and membership.
            absorbed: set[str] = set()
            for canonical in families:
                canonical_entities = set(canonical.get("member_wallets") or ())
                canonical_entities.update(canonical.get("treasuries") or ())
                matches = [
                    family for family in projected
                    if canonical_entities.intersection(family.get("member_wallets") or ())
                ]
                if not matches:
                    continue
                source = max(
                    matches,
                    key=lambda family: (
                        int(family.get("launches") or 0),
                        int(family.get("last_material_activity_at") or 0),
                    ),
                )
                matched_ids = sorted(str(family["family_id"]) for family in matches)
                absorbed.update(matched_ids)
                for key in (
                    "launches", "observed_launches", "launch_list", "session_count",
                    "active_sessions", "unique_creators", "last_seen_at",
                    "last_material_activity_at",
                ):
                    canonical[key] = source.get(key)
                canonical["observation_count"] = source.get("observed_launches") or 0
                canonical["source_population_id"] = source.get("family_id")
                canonical["absorbed_family_ids"] = matched_ids
                for population in matches:
                    population["promoted_to_operation_id"] = canonical["family_id"]
                    population["child_operations"] = [canonical["family_id"]]
                canonical["evidence_sources"] = sorted(set(
                    (canonical.get("evidence_sources") or ())
                    + (source.get("evidence_sources") or ())
                ))
            # Promotion creates a separate canonical Operation.  Its source
            # Investigation Population remains a permanent, directly
            # addressable evidence record and is never replaced.
            families.extend(projected)
        from src.ops.operation_confirmation import apply_confirmation_overlay
        apply_confirmation_overlay(families, self.ops_db_path)
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
            unique_creators = []
            if "wt_watchtower_launches" in tables and str(op.get("display_name") or "").upper() == "WATCHTOWER":
                cols = self._columns(conn, "wt_watchtower_launches")
                mint_col = "mint" if "mint" in cols else "token_mint" if "token_mint" in cols else None
                creator_col = "creator_wallet" if "creator_wallet" in cols else "creator" if "creator" in cols else None
                if mint_col:
                    launch_list = [r[0] for r in conn.execute(f"SELECT DISTINCT {mint_col} FROM wt_watchtower_launches ORDER BY {mint_col}")]
                    launches = len(launch_list)
                if creator_col:
                    unique_creators = [r[0] for r in conn.execute(
                        f"SELECT DISTINCT {creator_col} FROM wt_watchtower_launches "
                        f"WHERE {creator_col} IS NOT NULL ORDER BY {creator_col}"
                    )]
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
                "unique_creators": unique_creators, "funding_mechanisms": [],
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
        selected_descendants_by_parent: dict[str, set[str]] = defaultdict(set)
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
                "walkback_descendants": set(), "provisioning_clients": set(),
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
        # Infrastructure candidates carry a denormalized distinct-launch
        # count, but population membership must be reconstructed from the
        # validated identity-bearing evidence. Only SELECTED descendants are
        # canonical members; ALTERNATIVE/REJECTED edges remain evidence and
        # cannot inflate Registry or Profile launch counts.
        if "wt_walkback_edge_candidates" in tables:
            columns = self._columns(conn, "wt_walkback_edge_candidates")
            if {"candidate_parent", "mint", "selection_status"}.issubset(columns):
                for row in conn.execute(
                    "SELECT DISTINCT candidate_parent,mint "
                    "FROM wt_walkback_edge_candidates "
                    "WHERE selection_status='SELECTED' "
                    "AND candidate_parent IS NOT NULL AND mint IS NOT NULL"
                ):
                    wallet, mint = row["candidate_parent"], row["mint"]
                    selected_descendants_by_parent[str(wallet)].add(str(mint))
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
        # Apply descendant membership only to infrastructure-only profiles.
        # Families already routed by attribution outcomes or provisioning
        # edges retain those established membership sources; mixing ancestry
        # descendants into them would silently broaden existing populations.
        infrastructure_only_wallets = set()
        for wallet, p in profiles.items():
            descendants = selected_descendants_by_parent.get(wallet, set())
            if descendants and p["sources"] == {"wt_infrastructure_candidates"}:
                p["launches"].update(descendants)
                p["walkback_descendants"].update(descendants)
                p["sources"].add("wt_walkback_edge_candidates")
                infrastructure_only_wallets.add(wallet)
        # Project participants from the same selected launch membership.
        # Walkback queue rows are keyed by mint and carry observed creators
        # and subproviders. An OPERATIONAL_TREASURY candidate_parent is itself
        # direct treasury evidence for each selected descendant; no scalar or
        # inferred identity is introduced here.
        if infrastructure_only_wallets and "wt_walkback_queue" in tables:
            for row in conn.execute(
                "SELECT DISTINCT e.candidate_parent,q.creator,q.subprov,q.treasury "
                "FROM wt_walkback_edge_candidates e "
                "JOIN wt_walkback_queue q ON q.mint=e.mint "
                "WHERE e.selection_status='SELECTED'"
            ):
                wallet = str(row["candidate_parent"] or "")
                if wallet not in infrastructure_only_wallets:
                    continue
                p = profiles[wallet]
                if row["creator"]:
                    p["creators"].add(str(row["creator"]))
                if row["subprov"]:
                    p["provisioning_clients"].add(str(row["subprov"]))
                if row["treasury"]:
                    p["treasuries"].add(str(row["treasury"]))
                p["sources"].add("wt_walkback_queue")
        for wallet in infrastructure_only_wallets:
            p = profiles[wallet]
            if p["candidate"].get("candidate_role") == "OPERATIONAL_TREASURY":
                p["treasuries"].add(wallet)
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
        """Compatibility wrapper over the population builder and legacy adapter."""
        adapter = self._legacy_adapter(evaluation, proposals)
        return [
            adapter.project(population)
            for population in self._population_builder().build(profiles)
        ]

    def _population_builder(self) -> InvestigationPopulationBuilder:
        return InvestigationPopulationBuilder(
            self._cohesion_pair, self._evidence_timeline
        )

    def _legacy_adapter(self, evaluation, proposals) -> LegacyFamilyProjectionAdapter:
        return LegacyFamilyProjectionAdapter(
            minimum_launches=self.minimum_launches,
            completeness_threshold=self.completeness_threshold,
            dormancy_days=self.dormancy_days,
            metric=self._metric,
            material_reasons=self._material_reasons,
            evidence_weights=EVIDENCE_WEIGHTS,
            evaluation=evaluation,
            proposals=proposals,
        )

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
        """Compatibility wrapper retained for focused legacy tests."""
        population = self._population_builder().build_group(group)
        return self._legacy_adapter(evaluation, proposals).project(population)

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
