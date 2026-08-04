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
from collections import defaultdict
from typing import Any, Callable

from src.ops.attribution_outcome import emerging_operator_seeds
from src.ops.identity_framework import PromotionDecisionEngine
from src.ops.operator_resolver import OperatorResolver


EVIDENCE_WEIGHTS = {
    "topology": 25,
    "internal_consistency": 20,
    "transaction_evidence": 20,
    "operational_scale": 15,
    "identity_consistency": 10,
    "behavioural_stability": 10,
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
        visible = [f for f in all_families if f["stage"] in {"EMERGING_OPERATION", "CONFIRMED_OPERATION"}]
        visible = visible[:limit]
        result = {
            "families": visible, "candidates": visible,
            "count": len(visible), "total": len(visible),
            "funnel": {
                stage: sum(f["stage"] == stage for f in all_families)
                for stage in ("BACKGROUND_CLUSTER", "CANDIDATE_FAMILY", "EMERGING_OPERATION", "CONFIRMED_OPERATION")
            },
            "promotion_config": {
                "minimum_launches": self.minimum_launches,
                "evidence_completeness_threshold": self.completeness_threshold,
                "confirmed_operation": "manual promotion only",
            },
            "intake_contract": "/api/ops-v2/emerging-operator-seeds",
            "read_only": True,
        }
        if debug:
            hidden = [f for f in all_families if f["stage"] not in {"EMERGING_OPERATION", "CONFIRMED_OPERATION"}]
            result["debug"] = {
                "enabled": True,
                "background_clusters": [f for f in hidden if f["stage"] == "BACKGROUND_CLUSTER"],
                "candidate_families": [f for f in hidden if f["stage"] == "CANDIDATE_FAMILY"],
                "rejected_families": [f for f in hidden if f["promotion_gates"]["failed"]],
            }
        return result

    def get(self, entity: str) -> dict[str, Any] | None:
        entity = (entity or "").strip()
        for family in self._compose():
            if entity == family["family_id"] or entity in family["member_wallets"]:
                return family
        return None

    def recent_events(self, limit: int = 20) -> list[dict[str, Any]]:
        events = []
        for family in self._compose():
            if family["stage"] not in {"EMERGING_OPERATION", "CONFIRMED_OPERATION"}:
                continue
            events.append({
                "timestamp": family["last_seen_at"],
                "state": "CONFIRMED" if family["stage"] == "CONFIRMED_OPERATION" else "EMERGING",
                "kind": "OPERATION_FAMILY_CONFIRMED" if family["stage"] == "CONFIRMED_OPERATION" else "EMERGING_CANDIDATE_STRENGTHENED",
                "message": f"{family['family_name']}: {family['observed_launches']} launches, {family['evidence_completeness']['score']}% evidence complete",
                "entity": {"id": family["family_id"], "type": "operation_family" if len(family["member_wallets"]) > 1 else "emerging_candidate"},
                "href": f"/intelligence/emerging-operators?entity={family['family_id']}",
            })
        return sorted(events, key=lambda e: (e["timestamp"] or 0, e["kind"]), reverse=True)[:max(1, min(int(limit), 50))]

    def _compose(self) -> list[dict[str, Any]]:
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
        families.sort(key=lambda f: (
            {"CONFIRMED_OPERATION": 3, "EMERGING_OPERATION": 2, "CANDIDATE_FAMILY": 1, "BACKGROUND_CLUSTER": 0}[f["stage"]],
            f["evidence_completeness"]["score"], f["last_seen_at"] or 0, f["family_id"],
        ), reverse=True)
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
            score = {k: v for k, v in EVIDENCE_WEIGHTS.items()}
            name = op.get("display_name") or op["operator_id"]
            result.append(self._family_record(
                family_id=f"canonical:{op['operator_id']}", name=name, stage="CONFIRMED_OPERATION",
                members=clients, treasuries=treasuries, creators=[], launches=launches,
                launch_list=launch_list, mechanisms=[], first=_timestamp(op.get("first_seen")),
                last=_timestamp(op.get("last_seen")), sessions=0, score=score,
                topology="Canonical treasury → client → fresh creator",
                evidence=[{"type": "MANUAL_PROMOTION", "source": "operators", "detail": "Canonical operator confirmed through the existing review workflow."}],
                timeline=[], gate_context={"canonical": True},
            ))
        return result

    def _discovery_profiles(self, conn, tables) -> dict[str, dict[str, Any]]:
        profiles: dict[str, dict[str, Any]] = {}
        if "wt_infrastructure_candidates" in tables:
            for row in conn.execute(
                "SELECT * FROM wt_infrastructure_candidates WHERE candidate_role IN ('OPERATIONAL_TREASURY','HUB')"
            ):
                data = dict(row); wallet = data["wallet"]
                profiles[wallet] = {
                    "wallet": wallet, "source": "infrastructure_candidate", "candidate": data,
                    "creators": set(), "treasuries": set(), "mechanisms": set(), "signatures": set(),
                    "launches": set(), "first": _timestamp(data.get("first_seen_at")),
                    "last": _timestamp(data.get("last_seen_at")), "sessions": 0, "session_times": set(),
                    "evidence": [], "outcomes": [], "templates": defaultdict(int), "campaigns": set(),
                    "session_amounts": [],
                }
        if "wt_provisioning_edges" in tables:
            for row in conn.execute("SELECT * FROM wt_provisioning_edges WHERE edge_type='SUBPROV_TO_CREATOR'"):
                edge = dict(row); wallet = edge["from_wallet"]
                if wallet not in profiles:
                    continue
                p = profiles[wallet]; p["creators"].add(edge["to_wallet"])
                if edge.get("funding_mechanism"): p["mechanisms"].add(edge["funding_mechanism"])
                if edge.get("funding_tx_signature"): p["signatures"].add(edge["funding_tx_signature"])
                if edge.get("source_mint"): p["launches"].add(edge["source_mint"])
                p["first"] = min(x for x in (p["first"], _timestamp(edge.get("first_observed_by_flex"))) if x is not None)
                p["last"] = max(x for x in (p["last"], _timestamp(edge.get("last_observed_by_flex"))) if x is not None)
                p["evidence"].append({"type": "FUNDING_EDGE", "source": "wt_provisioning_edges", "detail": edge})
        if "wt_active_subprov_sessions" in tables:
            for row in conn.execute("SELECT * FROM wt_active_subprov_sessions"):
                session = dict(row); wallet = session["subprov_wallet"]
                if wallet not in profiles:
                    continue
                p = profiles[wallet]; p["sessions"] += 1
                if session.get("treasury_wallet"): p["treasuries"].add(session["treasury_wallet"])
                if session.get("funding_mechanism"): p["mechanisms"].add(session["funding_mechanism"])
                if session.get("funding_signature"): p["signatures"].add(session["funding_signature"])
                if session.get("funding_time"): p["session_times"].add(int(session["funding_time"]))
                if session.get("funding_amount") is not None: p["session_amounts"].append(float(session["funding_amount"]))
        if "wt_unknown_infrastructure_registry" in tables:
            for seed in emerging_operator_seeds(conn):
                wallet = seed["terminal_entity"]
                p = profiles.setdefault(wallet, {
                    "wallet": wallet, "source": "attribution_seed", "candidate": {}, "creators": set(),
                    "treasuries": set(), "mechanisms": set(), "signatures": set(), "launches": set(),
                    "first": _timestamp(seed.get("first_seen_at")), "last": _timestamp(seed.get("last_seen_at")),
                    "sessions": 0, "session_times": set(), "evidence": [], "outcomes": [],
                    "templates": defaultdict(int), "campaigns": set(),
                    "session_amounts": [],
                })
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
        return profiles

    def _cluster_profiles(self, profiles, evaluation, proposals) -> list[dict[str, Any]]:
        # Union only on independently repeated family evidence: two shared
        # treasuries plus a shared downstream funding mechanism.  One omnibus
        # treasury alone is deliberately insufficient.
        wallets = sorted(profiles)
        parent = {w: w for w in wallets}
        def find(w):
            while parent[w] != w:
                parent[w] = parent[parent[w]]; w = parent[w]
            return w
        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb: parent[rb] = ra
        for i, left in enumerate(wallets):
            a = profiles[left]
            for right in wallets[i + 1:]:
                b = profiles[right]
                shared_treasuries = a["treasuries"] & b["treasuries"]
                shared_mechanisms = a["mechanisms"] & b["mechanisms"]
                # Thin profiles remain candidate families until each side has
                # enough independent downstream observations to support a merge.
                demonstrated = len(a["creators"]) >= 5 and len(b["creators"]) >= 5
                if len(shared_treasuries) >= 2 and shared_mechanisms and demonstrated and not self._dust_profile(a) and not self._dust_profile(b):
                    union(left, right)
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for wallet in wallets: groups[find(wallet)].append(profiles[wallet])
        return [self._profile_family(group, evaluation, proposals) for group in groups.values()]

    def _profile_family(self, group, evaluation, proposals) -> dict[str, Any]:
        members = sorted(p["wallet"] for p in group)
        treasuries = sorted(set().union(*(p["treasuries"] for p in group)))
        creators = sorted(set().union(*(p["creators"] for p in group)))
        mechanisms = sorted(set().union(*(p["mechanisms"] for p in group)))
        signatures = set().union(*(p["signatures"] for p in group))
        launches = sorted(set().union(*(p["launches"] for p in group)))
        # Some edge rows predate source-mint population; distinct funded creators
        # are the persisted launch observations in that case.
        launch_count = max(len(launches), len(creators), max((int(p["candidate"].get("distinct_launches") or 0) for p in group), default=0))
        firsts = [p["first"] for p in group if p["first"] is not None]
        lasts = [p["last"] for p in group if p["last"] is not None]
        first, last = min(firsts) if firsts else None, max(lasts) if lasts else None
        sessions = sum(p["sessions"] for p in group)
        outgoing = any(p["creators"] for p in group)
        repeated_topology = outgoing and launch_count >= 2 and bool(mechanisms)
        observation_count = sum(len(p["outcomes"]) for p in group)
        dust_pattern = any(self._dust_profile(p) for p in group)
        persistent = sessions >= 2 or observation_count >= 2 or bool(first and last and last - first >= 86400)
        rpc_confirmed = len(signatures)
        shared_treasury_count = 0
        if len(group) > 1:
            shared_treasury_count = len(set.intersection(*(p["treasuries"] for p in group)))
        breakdown = {
            "topology": 25 if repeated_topology and treasuries else 15 if repeated_topology else 0,
            "internal_consistency": min(20, 5 * min(4, launch_count)) if repeated_topology else 0,
            "transaction_evidence": min(20, 5 * min(4, rpc_confirmed)),
            "operational_scale": min(15, 3 * min(5, launch_count)),
            "identity_consistency": 10 if shared_treasury_count >= 2 else 7 if sessions >= 2 and treasuries else 3 if treasuries else 0,
            "behavioural_stability": 10 if persistent and (sessions >= 2 or observation_count >= 4) else 7 if persistent else 0,
        }
        score = sum(breakdown.values())
        gates = {
            "minimum_launch_count": launch_count >= self.minimum_launches,
            "repeated_topology": repeated_topology,
            "multiple_sessions_or_persistent_client": persistent,
            "evidence_completeness_threshold": score >= self.completeness_threshold,
            "non_dust_funding_pattern": not dust_pattern,
        }
        if all(gates.values()): stage = "EMERGING_OPERATION"
        elif launch_count or sessions: stage = "CANDIDATE_FAMILY"
        else: stage = "BACKGROUND_CLUSTER"
        failures = [key for key, passed in gates.items() if not passed]
        family_hash = hashlib.sha256("\0".join(members).encode()).hexdigest()[:16]
        name = f"{_short(members[0])} Family" if len(members) == 1 else f"{_short(members[0])} / {_short(members[1])} Family"
        evidence = []
        for p in group: evidence.extend(p["evidence"])
        timeline = self._evidence_timeline(group, first)
        family = self._family_record(
            family_id=f"family:{family_hash}", name=name, stage=stage, members=members,
            treasuries=treasuries, creators=creators, launches=launch_count,
            launch_list=launches, mechanisms=mechanisms, first=first, last=last,
            sessions=sessions, score=breakdown,
            topology="Treasury → persistent client → fresh creator" if treasuries and outgoing else "Evidence accumulation incomplete",
            evidence=evidence, timeline=timeline,
            gate_context={"gates": gates, "failures": failures},
        )
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
    def _dust_profile(profile: dict[str, Any]) -> bool:
        amounts = profile.get("session_amounts") or []
        return len(amounts) >= 10 and max(amounts) - min(amounts) <= 0.01 and max(amounts) <= 0.2

    def _family_record(self, *, family_id, name, stage, members, treasuries, creators,
                       launches, launch_list, mechanisms, first, last, sessions,
                       score, topology, evidence, timeline, gate_context):
        gates = gate_context.get("gates", {
            "minimum_launch_count": True, "repeated_topology": True,
            "multiple_sessions_or_persistent_client": True,
            "evidence_completeness_threshold": True,
        })
        failed = gate_context.get("failures", [])
        status = {"CONFIRMED_OPERATION": "Confirmed", "EMERGING_OPERATION": "Emerging", "CANDIDATE_FAMILY": "Candidate", "BACKGROUND_CLUSTER": "Background"}[stage]
        completeness = {
            "score": sum(score.values()), "maximum": 100,
            "dimensions": [
                {"key": key, "label": label, "score": score[key], "maximum": EVIDENCE_WEIGHTS[key]}
                for key, label in (
                    ("topology", "Topology"), ("internal_consistency", "Consistency"),
                    ("transaction_evidence", "Transaction Evidence"), ("operational_scale", "Scale"),
                    ("identity_consistency", "Identity"), ("behavioural_stability", "Behaviour"),
                )
            ],
        }
        return {
            "family_id": family_id, "family_name": name, "status": status, "stage": stage,
            "observed_launches": launches, "launches": launches, "first_seen_at": first,
            "last_seen_at": last, "dominant_topology": topology,
            "primary_treasury_families": treasuries, "treasuries": treasuries,
            "client_wallets": members, "member_wallets": members,
            "member_treasuries": treasuries, "unique_creators": creators,
            "funding_mechanisms": mechanisms, "observed_topology_variants": [topology],
            "evidence_completeness": completeness, "supporting_evidence": evidence,
            "current_classification": "Canonical" if stage == "CONFIRMED_OPERATION" else "Monitoring",
            "promotion_gates": {
                "passed": gates, "failed": failed,
                "reason_not_surfaced": None if not failed else "; ".join(x.replace("_", " ") for x in failed),
            },
            "discovery_timeline": timeline, "evidence_timeline": timeline,
            "launch_list": launch_list, "session_count": sessions,
            # Compatibility fields used by existing Mission Control consumers.
            "terminal_entity": members[0] if members else family_id,
            "observation_count": launches, "campaign_count": sessions,
            "identity_class_count": len(treasuries), "review_status": "MONITORING",
            "review_label": status, "growth_timeline": timeline,
            "promotion_handoff": None, "contradictions": [], "read_only": True,
        }

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
