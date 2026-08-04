"""Internal investigation populations and the legacy family projection.

An :class:`InvestigationPopulation` is the evidence-derived output of the
Family Builder.  It deliberately has no lifecycle, candidate state,
confirmation, operator identity, attention state, or analyst presentation.

``LegacyFamilyProjectionAdapter`` is the compatibility boundary for X69.0. It
recreates the pre-X69 family dictionaries from a population so existing
consumers continue to observe exactly the same contract and behaviour.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence


def _freeze(value: Any) -> Any:
    """Recursively freeze evidence facts without changing their meaning."""
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted((_freeze(item) for item in value), key=str))
    return value


def _plain(value: Any) -> Any:
    """Return a deterministic JSON-compatible representation of frozen facts."""
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _frozen_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return _freeze(value)


@dataclass(frozen=True, slots=True)
class InvestigationPopulation:
    """A lifecycle-free population hypothesis produced from persisted facts."""

    population_id: str
    anchor: str
    population_basis: tuple[Mapping[str, Any], ...]
    members: tuple[str, ...]
    launches: tuple[str, ...]
    timeline: tuple[Mapping[str, Any], ...]
    metadata: Mapping[str, Any]
    revision_id: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "population_basis", tuple(_freeze(item) for item in self.population_basis))
        object.__setattr__(self, "members", tuple(self.members))
        object.__setattr__(self, "launches", tuple(self.launches))
        object.__setattr__(self, "timeline", tuple(_freeze(item) for item in self.timeline))
        object.__setattr__(self, "metadata", _freeze(self.metadata))
        forbidden = {
            "stage", "status", "lifecycle", "lifecycle_state", "candidate_state",
            "confirmation", "operator_id", "operation_id", "attention_eligible",
            "attention_rank", "reconciliation", "promotion_status",
        }
        overlap = forbidden & set(self.metadata)
        if overlap:
            raise ValueError(
                "Investigation population metadata contains presentation state: "
                + ", ".join(sorted(overlap))
            )
        revision_facts = {
            "population_id": self.population_id,
            "anchor": self.anchor,
            "population_basis": self.population_basis,
            "members": self.members,
            "launches": self.launches,
            "timeline": self.timeline,
            "metadata": self.metadata,
        }
        encoded = json.dumps(
            _plain(revision_facts), sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, default=str,
        ).encode()
        object.__setattr__(self, "revision_id", "ipr:" + hashlib.sha256(encoded).hexdigest())


class InvestigationPopulationBuilder:
    """Construct lifecycle-free populations from Family Builder profiles."""

    def __init__(
        self,
        cohesion_pair: Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]],
        timeline_factory: Callable[[Sequence[Mapping[str, Any]], int | None], list[dict[str, Any]]],
    ) -> None:
        self._cohesion_pair = cohesion_pair
        self._timeline_factory = timeline_factory

    def build(self, profiles: Mapping[str, dict[str, Any]]) -> list[InvestigationPopulation]:
        groups: list[list[dict[str, Any]]] = []
        for wallet in sorted(profiles):
            profile = profiles[wallet]
            for group in groups:
                if all(self._cohesion_pair(profile, member)["valid"] for member in group):
                    group.append(profile)
                    break
            else:
                groups.append([profile])
        return [self.build_group(group) for group in groups]

    def build_group(self, group: Sequence[dict[str, Any]]) -> InvestigationPopulation:
        members = tuple(sorted(p["wallet"] for p in group))
        treasuries = tuple(sorted(set().union(*(p["treasuries"] for p in group))))
        creators = tuple(sorted(set().union(*(p["creators"] for p in group))))
        mechanisms = tuple(sorted(set().union(*(p["mechanisms"] for p in group))))
        signatures = tuple(sorted(set().union(*(p["signatures"] for p in group))))
        launches = tuple(sorted(set().union(*(p["launches"] for p in group))))
        firsts = [p["first"] for p in group if p["first"] is not None]
        lasts = [p["last"] for p in group if p["last"] is not None]
        first = min(firsts) if firsts else None
        last = max(lasts) if lasts else None
        anchor_profile = min(
            group,
            key=lambda p: (p["first"] if p["first"] is not None else 2**63, p["wallet"]),
        )
        anchor = anchor_profile["wallet"]
        population_id = "family:" + hashlib.sha256(
            f"operation-family-anchor:{anchor}".encode()
        ).hexdigest()[:16]

        basis: list[Mapping[str, Any]] = []
        for left_index, left in enumerate(group):
            for right in group[left_index + 1:]:
                result = dict(self._cohesion_pair(left, right))
                basis.append(_frozen_mapping({
                    "left": left["wallet"], "right": right["wallet"],
                    "valid": result["valid"], "strength": result["strength"],
                    "evidence": tuple(result["evidence"]),
                    "conflicts": tuple(result["conflicts"]),
                }))

        evidence: list[dict[str, Any]] = []
        for profile in group:
            evidence.extend(profile["evidence"])
        timeline = self._timeline_factory(group, first)

        metadata = _frozen_mapping({
            "treasuries": treasuries,
            "member_treasuries": tuple(
                (p["wallet"], tuple(sorted(p["treasuries"]))) for p in group
            ),
            "creators": creators,
            "mechanisms": mechanisms,
            "signatures": signatures,
            "first_seen_at": first,
            "last_seen_at": last,
            "session_count": sum(p["sessions"] for p in group),
            "active_session_count": sum(p["active_sessions"] for p in group),
            "observation_count": sum(len(p["outcomes"]) for p in group),
            "launch_count_hint": max(
                (int(p["candidate"].get("distinct_launches") or 0) for p in group),
                default=0,
            ),
            "sources": tuple(sorted(set().union(*(p["sources"] for p in group)))),
            "exclusions": tuple(item for p in group for item in p["exclusions"]),
            "warnings": tuple(sorted(set(item for p in group for item in p["warnings"]))),
            "edge_times": tuple(ts for p in group for ts in p["edge_times"]),
            "evidence": tuple(evidence),
            "templates": tuple(
                (key, count) for p in group for key, count in p["templates"].items()
            ),
            "campaigns": tuple(sorted(set().union(*(p["campaigns"] for p in group)))),
            "infrastructure_roles": tuple(
                p["candidate"].get("candidate_role") for p in group
            ),
            "session_amounts": tuple(
                tuple(p.get("session_amounts") or ()) for p in group
            ),
        })
        return InvestigationPopulation(
            population_id=population_id,
            anchor=anchor,
            population_basis=tuple(basis),
            members=members,
            launches=launches,
            timeline=tuple(_frozen_mapping(event) for event in timeline),
            metadata=metadata,
        )


class LegacyFamilyProjectionAdapter:
    """Reproduce the legacy analyst-facing family dictionary unchanged."""

    def __init__(
        self,
        *,
        minimum_launches: int,
        completeness_threshold: int,
        dormancy_days: int,
        metric: Callable[[Mapping[str, int], Mapping[str, int]], dict[str, Any]],
        material_reasons: Callable[[int, int, bool, str], list[str]],
        evidence_weights: Mapping[str, int],
        evaluation: Any = None,
        proposals: Sequence[Any] = (),
    ) -> None:
        self.minimum_launches = minimum_launches
        self.completeness_threshold = completeness_threshold
        self.dormancy_days = dormancy_days
        self._metric = metric
        self._material_reasons = material_reasons
        self.evidence_weights = evidence_weights
        self.evaluation = evaluation
        self.proposals = proposals

    def project(self, population: InvestigationPopulation) -> dict[str, Any]:
        m = population.metadata
        members = list(population.members)
        treasuries = list(m["treasuries"])
        creators = list(m["creators"])
        mechanisms = list(m["mechanisms"])
        signatures = set(m["signatures"])
        launches = list(population.launches)
        launch_count = max(len(launches), len(creators), int(m["launch_count_hint"]))
        first, last = m["first_seen_at"], m["last_seen_at"]
        sessions = int(m["session_count"])
        outgoing = bool(creators)
        complete_topology = bool(treasuries and outgoing and mechanisms)
        observation_count = int(m["observation_count"])
        exclusions = [_plain(item) for item in m["exclusions"]]
        warnings = list(m["warnings"])
        persistent = sessions >= 2 or bool(first and last and last - first >= 86400 * 2)
        shared_treasury_count = 0
        if len(members) > 1:
            treasury_sets = [set(values) for _, values in m["member_treasuries"]]
            shared_treasury_count = len(set.intersection(*treasury_sets))

        sources = list(m["sources"])
        evidence_breakdown = {
            "topology_reconstruction": 20 if complete_topology else 10 if outgoing and mechanisms else 0,
            "transaction_provenance": 20 if len(signatures) >= 2 else 10 if signatures else 0,
            "relationship_coverage": 15 if shared_treasury_count >= 2 else 8 if treasuries and outgoing else 0,
            "identity_link_coverage": 15 if len(members) > 1 and shared_treasury_count >= 2 else 7 if treasuries else 0,
            "independent_evidence_diversity": min(15, 3 * len(sources)),
            "uncertainty_conflict_coverage": 15 if not exclusions and not warnings else 8 if not exclusions else 0,
        }
        completeness = self._metric(evidence_breakdown, self.evidence_weights)
        now = int(time.time())
        recent_cutoff = now - 14 * 86400
        recent_launches = sum(ts >= recent_cutoff for ts in m["edge_times"])
        recent = bool(last and last >= recent_cutoff)
        duration_days = max(0, ((last or 0) - (first or last or 0)) // 86400)
        significance_values = {
            "recent_launch_activity": min(30, recent_launches * 5 if recent_launches else (15 if recent and launch_count >= 5 else 5 if recent else 0)),
            "launch_velocity": min(15, recent_launches * 3),
            "active_session_breadth": min(15, int(m["active_session_count"]) * 5 + min(10, sessions)),
            "coherent_member_breadth": 20 if len(members) > 1 else 0,
            "operational_persistence": 10 if duration_days >= 7 else 5 if duration_days >= 2 else 0,
            "topology_novelty": 10 if treasuries and complete_topology else 0,
        }
        significance_maxima = {
            "recent_launch_activity": 30, "launch_velocity": 15,
            "active_session_breadth": 15, "coherent_member_breadth": 20,
            "operational_persistence": 10, "topology_novelty": 10,
        }
        significance = self._metric(significance_values, significance_maxima)
        maturity_values = {
            "observed_duration": min(30, duration_days),
            "independent_sessions": min(25, sessions * 3),
            "topology_stability": 20 if complete_topology and launch_count >= 5 else 10 if complete_topology else 0,
            "membership_stability": 15 if len(members) > 1 else 5,
            "conflict_survival": 10 if not exclusions else 0,
        }
        maturity = self._metric(maturity_values, {
            "observed_duration": 30, "independent_sessions": 25,
            "topology_stability": 20, "membership_stability": 15,
            "conflict_survival": 10,
        })

        membership = []
        for member in members:
            pairs = [p for p in population.population_basis if member in (p["left"], p["right"])]
            evidence = sorted(set(x for pair in pairs for x in pair["evidence"]))
            conflicts = sorted(set(x for pair in pairs for x in pair["conflicts"]))
            membership.append({
                "wallet": member,
                "membership_status": "CORE" if len(members) > 1 else "SINGLETON_HYPOTHESIS",
                "membership_strength": min((int(pair["strength"]) for pair in pairs), default=0),
                "membership_evidence": evidence,
                "membership_conflicts": conflicts,
            })
        cohesion_valid = len(members) > 1 and all(
            item["membership_strength"] >= 50 and not item["membership_conflicts"]
            for item in membership
        )
        singleton_lane = len(members) == 1 and complete_topology and len(creators) >= 5 and len(signatures) >= 1
        topology_reviewable = complete_topology and len(signatures) >= 1
        promotion_ready = not exclusions and topology_reviewable and (cohesion_valid or singleton_lane)
        blocking = []
        if exclusions:
            blocking.append("exclusion evidence must be resolved")
        if not complete_topology:
            blocking.append("complete operating topology not reconstructed")
        if not signatures:
            blocking.append("missing transaction provenance")
        if len(members) == 1:
            blocking.append("family breadth unresolved")
        if len(members) > 1 and not cohesion_valid:
            blocking.append("family cohesion review required")
        eligible = promotion_ready and recent and significance["score"] >= 55 and completeness["score"] >= 60
        stale = bool(last and last < now - self.dormancy_days * 86400)
        if exclusions:
            stage = "RETIRED"
        elif stale and (launch_count or sessions):
            stage = "DORMANT"
        elif eligible:
            stage = "SIGNIFICANT_ACTIVE"
        elif (complete_topology or (outgoing and mechanisms and len(sources) >= 2)) and (launch_count or sessions or observation_count):
            stage = "CANDIDATE"
        else:
            stage = "BACKGROUND"

        old_repeated = outgoing and launch_count >= self.minimum_launches and bool(mechanisms)
        old_persistent = sessions >= 2 or observation_count >= 2 or bool(first and last and last - first >= 86400)
        old_score = (
            (25 if old_repeated and treasuries else 15 if old_repeated else 0)
            + (min(20, 5 * min(4, launch_count)) if old_repeated else 0)
            + min(20, 5 * min(4, len(signatures)))
            + min(15, 3 * min(5, launch_count))
            + (10 if shared_treasury_count >= 2 else 7 if sessions >= 2 and treasuries else 3 if treasuries else 0)
            + (10 if old_persistent and (sessions >= 2 or observation_count >= 4) else 7 if old_persistent else 0)
        )
        old_saw_dust = any(
            role in {"OPERATIONAL_TREASURY", "HUB"}
            and len(amounts) >= 10
            and max(amounts) - min(amounts) <= 0.01
            and max(amounts) <= 0.2
            for role, amounts in zip(m["infrastructure_roles"], m["session_amounts"])
        )
        old_stage = "EMERGING_OPERATION" if (
            launch_count >= self.minimum_launches and old_repeated and old_persistent
            and old_score >= self.completeness_threshold and not old_saw_dust
        ) else "CANDIDATE_FAMILY"

        absorbed_ids = [
            "family:" + hashlib.sha256(f"operation-family-anchor:{member}".encode()).hexdigest()[:16]
            for member in members if member != population.anchor
        ]
        name = f"{self._short(members[0])} Family" if len(members) == 1 else f"{self._short(members[0])} / {self._short(members[1])} Family"
        timeline = [dict(event) for event in population.timeline]
        topology = "Treasury → persistent client → fresh creator" if complete_topology else "Evidence accumulation incomplete"
        missing = []
        if not treasuries:
            missing.append("treasury relationships")
        if not signatures:
            missing.append("transaction provenance")
        if not outgoing:
            missing.append("downstream creator relationships")
        reasons = self._material_reasons(recent_launches, len(members), recent, stage)

        family = {
            "family_id": population.population_id, "family_name": name,
            "stage": stage, "status": stage.replace("_", " ").title(),
            "family_anchor": population.anchor, "previous_stage": old_stage,
            "lifecycle_state": stage, "first_seen_at": first, "last_seen_at": last,
            "last_material_activity_at": last, "state_changed_at": last,
            "merged_into_family_id": None, "superseded_by_family_id": None,
            "absorbed_family_ids": absorbed_ids,
            "launches": launch_count, "observed_launches": launch_count, "launch_list": launches,
            "session_count": sessions, "active_sessions": int(m["active_session_count"]),
            "member_wallets": members, "client_wallets": members,
            "member_treasuries": treasuries, "treasuries": treasuries,
            "primary_treasury_families": treasuries, "unique_creators": creators,
            "funding_mechanisms": mechanisms, "dominant_topology": topology,
            "observed_topology_variants": [topology],
            "evidence_completeness": completeness,
            "evidence_breakdown": completeness["dimensions"],
            "missing_evidence": missing, "evidence_sources": sources,
            "discovery_significance": significance,
            "significance_breakdown": significance["dimensions"],
            "material_change_reasons": reasons,
            "operational_maturity": maturity,
            "maturity_breakdown": maturity["dimensions"],
            "promotion_ready": promotion_ready,
            "promotion_status": "REVIEWABLE" if promotion_ready else "BLOCKED",
            "blocking_reasons": blocking,
            "reviewable_reasons": ([
                "cohesion validated" if cohesion_valid else "significant unresolved operation lane",
                "topology and transaction evidence available",
            ] if promotion_ready else []),
            "cohesion": {
                "status": "VALIDATED" if cohesion_valid else "SIGNIFICANT_UNRESOLVED" if singleton_lane else "UNPROVEN",
                "score": min((item["membership_strength"] for item in membership), default=0),
                "evidence": sorted(set(x for item in membership for x in item["membership_evidence"])),
                "conflicts": sorted(set(x for item in membership for x in item["membership_conflicts"])),
            },
            "merge_split_decisions": ([{
                "decision": "MERGED", "members": members,
                "reason": "complete-link cohesion validated for every member",
            }] if len(members) > 1 else [{
                "decision": "RETAIN_SINGLETON", "members": members,
                "reason": "no complete-link family match",
            }]),
            "membership": membership, "exclusion_evidence": exclusions,
            "data_quality_warnings": warnings, "attention_eligible": eligible,
            "attention_rank": None, "why_surfaced": reasons,
            "dormancy_reason": "no material activity inside dormancy window" if stage == "DORMANT" else None,
            "supporting_evidence": [_plain(item) for item in m["evidence"]],
            "discovery_timeline": timeline, "evidence_timeline": list(timeline),
            "growth_timeline": list(timeline), "current_classification": "Monitoring",
            "terminal_entity": members[0], "observation_count": launch_count,
            "campaign_count": sessions, "identity_class_count": len(treasuries),
            "review_status": "MONITORING", "review_label": stage.replace("_", " ").title(),
            "promotion_handoff": None, "contradictions": [], "read_only": True,
            "promotion_gates": {
                "passed": {
                    "cohesion_or_singleton_lane": cohesion_valid or singleton_lane,
                    "promotion_ready": promotion_ready,
                    "current_significance": significance["score"] >= 55,
                    "recent_material_activity": recent,
                    "exclusions_clear": not exclusions,
                },
                "failed": [reason for reason in blocking],
                "reason_not_surfaced": None if eligible else "; ".join(blocking or ["insufficient current significance"]),
            },
        }
        template_counts: dict[tuple[Any, Any], int] = {}
        for key, count in m["templates"]:
            template_counts[key] = template_counts.get(key, 0) + count
        family["funding_templates"] = [
            {"mechanism": key[0], "amount_sol": key[1], "observation_count": count}
            for key, count in sorted(template_counts.items(), key=lambda item: str(item[0]))
        ]
        family["campaigns"] = list(m["campaigns"])
        family["campaign_count"] = len(family["campaigns"])

        linked_identity = [] if self.evaluation is None else [
            item for item in self.evaluation.identity
            if any(wallet in item.entities for wallet in members)
        ]
        linked_keys = {item.candidate_key for item in linked_identity}
        linked_proposals = [proposal for proposal in self.proposals if proposal.candidate_key in linked_keys]
        linked_proposals.sort(
            key=lambda proposal: (
                len(proposal.identity_classes), proposal.identity_confidence,
                proposal.candidate_key,
            ),
            reverse=True,
        )
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
                "proposal_id": primary.proposal_id,
                "proposal_fingerprint": primary.proposal_fingerprint,
                "identity_fingerprint": primary.identity_fingerprint,
                "href": "/intelligence/operator-promotions",
                "requires_analyst_approval": True,
            }
            if primary.decision == "PROMOTION_ELIGIBLE":
                family["growth_timeline"].append({
                    "event_type": "PROMOTION_ELIGIBLE", "timestamp": last,
                    "day": self._day(first, last),
                    "label": "Promotion eligible — analyst approval required",
                    "observation_count": launch_count,
                })
        return family

    @staticmethod
    def _short(value: str) -> str:
        return value if len(value) <= 8 else value[:4]

    @staticmethod
    def _day(first: int | None, current: int | None) -> int:
        if first is None or current is None:
            return 1
        return max(1, int((current - first) // 86400) + 1)
