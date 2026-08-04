"""Immutable, factual evidence reconciliation packages for shadow validation.

X69.1 deliberately makes no reconciliation decision.  The service reads an
immutable :class:`InvestigationPopulation` revision and organises evidence
already present in that revision or in existing read-only registries.  Nothing
in production imports or consumes this module.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Mapping

from src.ops.investigation_population import InvestigationPopulation
from src.utils.infra_mapping import get_funder_label


UNRESOLVED = "UNRESOLVED"


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted((_freeze(item) for item in value), key=str))
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _identifier(prefix: str, facts: Any) -> str:
    encoded = json.dumps(
        _plain(facts), sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, default=str,
    ).encode()
    return prefix + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class EvidenceProvenance:
    """Named origin and observation bounds for one factual evidence item."""

    source: str
    table: str | None
    registry: str | None
    rpc: bool
    transaction_signature: str | None
    observation_window: tuple[int | None, int | None]
    dependency_group: str
    completeness: str

    def __post_init__(self) -> None:
        if not self.source or not self.dependency_group or not self.completeness:
            raise ValueError("Evidence provenance must name its source, dependency group, and completeness")
        if not (self.table or self.registry or self.rpc):
            raise ValueError("Evidence provenance must identify a table, Registry, or RPC source")


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    """One observed fact; role organises facts but never scores or ranks them."""

    evidence_type: str
    role: str
    statement: str
    entities: tuple[str, ...]
    launches: tuple[str, ...]
    provenance: EvidenceProvenance
    details: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    evidence_id: str = field(init=False)

    def __post_init__(self) -> None:
        if self.role not in {"SUPPORTING", "CONTRADICTORY", "CONTEXT", "MISSING"}:
            raise ValueError(f"Unsupported factual evidence role: {self.role}")
        if not self.statement:
            raise ValueError("Evidence statement is required")
        frozen = _freeze(self.details)
        object.__setattr__(self, "details", frozen)
        facts = {
            "evidence_type": self.evidence_type, "role": self.role,
            "statement": self.statement, "entities": self.entities,
            "launches": self.launches, "provenance": self.provenance,
            "details": frozen,
        }
        object.__setattr__(self, "evidence_id", _identifier("eri:", facts))


@dataclass(frozen=True, slots=True)
class DependencyGroup:
    name: str
    evidence_ids: tuple[str, ...]
    sources: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExplainabilityStatement:
    statement: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.statement or not self.evidence_ids:
            raise ValueError("Explainability statements require text and evidence references")


@dataclass(frozen=True, slots=True)
class ReconciliationExplainability:
    why_population_exists: tuple[ExplainabilityStatement, ...]
    why_members_belong: Mapping[str, tuple[ExplainabilityStatement, ...]]
    supporting_facts: tuple[ExplainabilityStatement, ...]
    contradictory_facts: tuple[ExplainabilityStatement, ...]
    unknown_facts: tuple[ExplainabilityStatement, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "why_members_belong", _freeze(self.why_members_belong))


@dataclass(frozen=True, slots=True)
class ReconciliationPopulation:
    population_id: str
    revision_id: str
    anchor: str
    members: tuple[str, ...]
    launches: tuple[str, ...]
    first_seen_at: int | None
    last_seen_at: int | None


@dataclass(frozen=True, slots=True)
class CreatorReuseMintEvidence:
    """Persisted provisioning observations tying one creator to one mint."""

    mint: str
    supporting_edge_ids: tuple[str, ...]
    funding_transaction_signatures: tuple[str, ...]
    first_observed_at: int | None
    last_observed_at: int | None


@dataclass(frozen=True, slots=True)
class CreatorReuseEvidence:
    """One non-inferential creator-to-multiple-mints behavioural fact."""

    creator_wallet: str
    mint_evidence: tuple[CreatorReuseMintEvidence, ...]
    supporting_edge_ids: tuple[str, ...]
    funding_transaction_signatures: tuple[str, ...]
    first_observed_at: int | None
    last_observed_at: int | None
    population_revision_ids: tuple[str, ...]
    provenance: EvidenceProvenance
    evidence_id: str = field(init=False)

    def __post_init__(self) -> None:
        if len({item.mint for item in self.mint_evidence}) < 2:
            raise ValueError("Creator reuse requires at least two distinct persisted mints")
        if not self.supporting_edge_ids:
            raise ValueError("Creator reuse requires supporting provisioning edge IDs")
        if not self.population_revision_ids:
            raise ValueError("Creator reuse must identify affected population revisions")
        object.__setattr__(self, "evidence_id", _identifier("cre:", {
            "creator_wallet": self.creator_wallet,
            "mint_evidence": self.mint_evidence,
            "supporting_edge_ids": self.supporting_edge_ids,
            "funding_transaction_signatures": self.funding_transaction_signatures,
            "first_observed_at": self.first_observed_at,
            "last_observed_at": self.last_observed_at,
            "population_revision_ids": self.population_revision_ids,
            "provenance": self.provenance,
        }))


@dataclass(frozen=True, slots=True)
class EvidenceReconciliationPackage:
    """Shadow-only factual package bound to one population revision."""

    population: ReconciliationPopulation
    supporting_evidence: tuple[EvidenceItem, ...]
    contradictory_evidence: tuple[EvidenceItem, ...]
    context: tuple[EvidenceItem, ...]
    missing_evidence: tuple[EvidenceItem, ...]
    dependency_groups: tuple[DependencyGroup, ...]
    explainability: ReconciliationExplainability
    provenance: tuple[EvidenceProvenance, ...]
    creator_reuse_evidence: tuple[CreatorReuseEvidence, ...] = ()
    disposition: str = UNRESOLVED
    package_id: str = field(init=False)

    def __post_init__(self) -> None:
        if self.disposition != UNRESOLVED:
            raise ValueError("X69.1 reconciliation disposition must remain UNRESOLVED")
        items = (
            self.supporting_evidence + self.contradictory_evidence
            + self.context + self.missing_evidence
        )
        ids = {item.evidence_id for item in items}
        if len(ids) != len(items):
            raise ValueError("Evidence package contains duplicate factual items")
        statements = (
            self.explainability.why_population_exists
            + tuple(item for values in self.explainability.why_members_belong.values() for item in values)
            + self.explainability.supporting_facts
            + self.explainability.contradictory_facts
            + self.explainability.unknown_facts
        )
        if any(ref not in ids for statement in statements for ref in statement.evidence_ids):
            raise ValueError("Explainability contains an anonymous evidence reference")
        grouped_ids = {ref for group in self.dependency_groups for ref in group.evidence_ids}
        if grouped_ids != ids:
            raise ValueError("Every evidence item must belong to one dependency group")
        object.__setattr__(self, "package_id", _identifier("erp:", {
            "population_revision": self.population.revision_id,
            "evidence_ids": tuple(sorted(ids)), "disposition": self.disposition,
        }))


class EvidenceReconciliationService:
    """Build factual reconciliation packages without exposing them to consumers."""

    def __init__(
        self,
        ops_db_path: str | None = None,
        infrastructure_lookup: Callable[[str], Mapping[str, Any] | None] = get_funder_label,
    ) -> None:
        self.ops_db_path = ops_db_path
        self.infrastructure_lookup = infrastructure_lookup
        self._creator_reuse_index: Mapping[str, tuple[sqlite3.Row, ...]] | None = None

    def build(self, population: InvestigationPopulation) -> EvidenceReconciliationPackage:
        m = population.metadata
        supporting: list[EvidenceItem] = []
        contradictory: list[EvidenceItem] = []
        context: list[EvidenceItem] = []
        missing: list[EvidenceItem] = []
        window = (m.get("first_seen_at"), m.get("last_seen_at"))
        members = tuple(population.members)
        launches = tuple(population.launches)

        def provenance(
            source: str, group: str, *, table: str | None = None,
            registry: str | None = None, rpc: bool = False,
            signature: str | None = None, completeness: str = "OBSERVED",
        ) -> EvidenceProvenance:
            return EvidenceProvenance(
                source=source, table=table, registry=registry, rpc=rpc,
                transaction_signature=signature, observation_window=window,
                dependency_group=group, completeness=completeness,
            )

        sources = set(m.get("sources") or ())
        treasuries = tuple(m.get("treasuries") or ())
        creators = tuple(m.get("creators") or ())
        mechanisms = tuple(m.get("mechanisms") or ())
        signatures = tuple(m.get("signatures") or ())
        creator_reuse = self._creator_reuse_for_population(population, creators, window)

        if treasuries:
            source = (
                "wt_active_subprov_sessions" if "wt_active_subprov_sessions" in sources
                else "operator_entities" if "operator_entities" in sources
                else "wt_walkback_queue"
            )
            supporting.append(EvidenceItem(
                "TREASURY_RELATIONSHIPS", "SUPPORTING",
                f"Persisted evidence records {len(treasuries)} treasury relationship(s) for this population.",
                tuple(sorted(set(members + treasuries))), launches,
                provenance(source, "treasury_provisioning", table=source),
                {"treasuries": treasuries},
            ))
        else:
            missing.append(self._missing_item(
                "TREASURY_UNAVAILABLE", "No treasury relationship is present in the population evidence.",
                members, launches, window, "treasury_provisioning", "wt_active_subprov_sessions",
            ))

        if creators:
            supporting.append(EvidenceItem(
                "PROVISIONING_LINEAGE", "SUPPORTING",
                f"Persisted provisioning edges connect the population to {len(creators)} creator(s).",
                tuple(sorted(set(members + creators))), launches,
                provenance("wt_provisioning_edges", "treasury_provisioning", table="wt_provisioning_edges"),
                {"creators": creators},
            ))
        else:
            missing.append(self._missing_item(
                "CREATOR_LINEAGE_UNAVAILABLE", "No downstream creator relationship is present in the population evidence.",
                members, launches, window, "treasury_provisioning", "wt_provisioning_edges",
            ))

        session_count = int(m.get("session_count") or 0)
        if session_count:
            supporting.append(EvidenceItem(
                "PROVISIONING_SESSIONS", "SUPPORTING",
                f"The population contains {session_count} persisted provisioning session(s).",
                members, launches,
                provenance("wt_active_subprov_sessions", "treasury_provisioning", table="wt_active_subprov_sessions"),
                {"session_count": session_count, "active_session_count": int(m.get("active_session_count") or 0)},
            ))
        else:
            missing.append(self._missing_item(
                "SESSIONS_UNAVAILABLE", "No provisioning session is present in the population evidence.",
                members, launches, window, "treasury_provisioning", "wt_active_subprov_sessions",
            ))

        if mechanisms:
            context.append(EvidenceItem(
                "FUNDING_TOPOLOGY", "CONTEXT",
                "Persisted funding mechanisms describe the observed population topology.",
                members, launches,
                provenance("wt_provisioning_edges", "topology_funding_mechanism", table="wt_provisioning_edges"),
                {"funding_mechanisms": mechanisms},
            ))

        for signature in signatures:
            supporting.append(EvidenceItem(
                "TRANSACTION_PROVENANCE", "SUPPORTING",
                "A persisted transaction signature records provenance for the observed relationship.",
                members, launches,
                provenance("persisted_transaction_signature", "transaction_provenance", table="wt_provisioning_edges", signature=signature),
                {"signature": signature},
            ))
        if not signatures:
            missing.append(self._missing_item(
                "RPC_PROVENANCE_UNAVAILABLE", "No transaction signature is present for RPC provenance verification.",
                members, launches, window, "transaction_provenance", "wt_provisioning_edges",
            ))
        missing.append(self._missing_item(
            "RPC_CONFIRMATION_UNAVAILABLE",
            "The Investigation Population revision does not record an explicit RPC-confirmation result.",
            members, launches, window, "transaction_provenance", "wt_provisioning_edges",
        ))

        for evidence in m.get("evidence") or ():
            data = dict(evidence)
            detail = data.get("detail") if isinstance(data.get("detail"), Mapping) else {}
            evidence_type = str(data.get("type") or "PERSISTED_EVIDENCE")
            source = str(data.get("source") or "population_metadata")
            group = (
                "attribution_outcome" if evidence_type == "ATTRIBUTION_OUTCOME"
                else "treasury_provisioning" if evidence_type == "FUNDING_EDGE"
                else "operation_history"
            )
            context.append(EvidenceItem(
                evidence_type, "CONTEXT",
                f"The population retains a persisted {evidence_type.lower().replace('_', ' ')} fact.",
                members, launches,
                provenance(
                    source, group, table=source,
                    signature=detail.get("funding_tx_signature"),
                    rpc=bool(detail.get("funding_tx_signature")),
                ),
                data,
            ))

        for exclusion in m.get("exclusions") or ():
            data = dict(exclusion)
            source = str(data.get("source") or "infra_mapping")
            contradictory.append(EvidenceItem(
                str(data.get("type") or "KNOWN_INFRASTRUCTURE"), "CONTRADICTORY",
                f"Existing infrastructure evidence records: {data.get('detail') or data.get('type') or 'known infrastructure'}.",
                members, launches,
                provenance(source, "infrastructure_exclusion", registry=source),
                data,
            ))

        labelled = set()
        for entity in tuple(sorted(set(members + treasuries))):
            label = self.infrastructure_lookup(entity)
            if label and entity not in labelled:
                labelled.add(entity)
                contradictory.append(EvidenceItem(
                    str(label.get("kind") or "KNOWN_INFRASTRUCTURE"), "CONTRADICTORY",
                    f"Infrastructure Registry identifies {entity} as {label.get('name') or 'known infrastructure'}.",
                    (entity,), launches,
                    provenance("infra_mapping", "infrastructure_exclusion", registry="Infrastructure Registry"),
                    dict(label),
                ))

        for warning in m.get("warnings") or ():
            contradictory.append(EvidenceItem(
                "PERSISTED_EVIDENCE_WARNING", "CONTRADICTORY", str(warning),
                members, launches,
                provenance("population_data_quality", "evidence_dependency_conflict", table="wt_provisioning_edges"),
                {"warning": warning},
            ))

        context.extend(self._registry_context(population, window))
        missing.append(self._missing_item(
            "SETTLEMENT_UNAVAILABLE",
            "No post-launch settlement evidence is present in the Investigation Population revision.",
            members, launches, window, "settlement", "creator_outgoing_transfers",
        ))
        if creator_reuse:
            for reuse in creator_reuse:
                supporting.append(EvidenceItem(
                    "CREATOR_REUSE_CONTROL", "SUPPORTING",
                    f"Persisted provisioning edges record creator {reuse.creator_wallet} across "
                    f"{len(reuse.mint_evidence)} distinct mints.",
                    tuple(sorted(set(members + (reuse.creator_wallet,)))),
                    tuple(item.mint for item in reuse.mint_evidence),
                    reuse.provenance,
                    {
                        "creator_reuse_evidence_id": reuse.evidence_id,
                        "creator_wallet": reuse.creator_wallet,
                        "mints": tuple(item.mint for item in reuse.mint_evidence),
                        "mint_evidence": tuple({
                            "mint": item.mint,
                            "supporting_edge_ids": item.supporting_edge_ids,
                            "funding_transaction_signatures": item.funding_transaction_signatures,
                            "first_observed_at": item.first_observed_at,
                            "last_observed_at": item.last_observed_at,
                        } for item in reuse.mint_evidence),
                        "supporting_edge_ids": reuse.supporting_edge_ids,
                        "funding_transaction_signatures": reuse.funding_transaction_signatures,
                        "first_observed_at": reuse.first_observed_at,
                        "last_observed_at": reuse.last_observed_at,
                        "population_revision_ids": reuse.population_revision_ids,
                    },
                ))
        else:
            missing.append(self._missing_item(
                "CREATOR_REUSE_UNAVAILABLE",
                "No creator-reuse evidence is present in the Investigation Population revision.",
                members, launches, window, "creator_reuse", "wt_provisioning_edges",
            ))

        all_items = supporting + contradictory + context + missing
        groups = self._dependency_groups(all_items)
        explainability = self._explain(population, supporting, contradictory, context, missing)
        unique_provenance = tuple(dict.fromkeys(item.provenance for item in all_items))
        return EvidenceReconciliationPackage(
            population=ReconciliationPopulation(
                population.population_id, population.revision_id, population.anchor,
                members, launches, window[0], window[1],
            ),
            supporting_evidence=tuple(supporting),
            contradictory_evidence=tuple(contradictory), context=tuple(context),
            missing_evidence=tuple(missing), dependency_groups=groups,
            explainability=explainability, provenance=unique_provenance,
            creator_reuse_evidence=creator_reuse,
        )

    def build_all(
        self, populations: tuple[InvestigationPopulation, ...] | list[InvestigationPopulation]
    ) -> tuple[EvidenceReconciliationPackage, ...]:
        return tuple(self.build(population) for population in populations)

    def _creator_reuse_for_population(
        self,
        population: InvestigationPopulation,
        creators: tuple[str, ...],
        window: tuple[int | None, int | None],
    ) -> tuple[CreatorReuseEvidence, ...]:
        """Project one factual reuse observation per creator from persisted edges."""
        if not creators:
            return ()
        index = self._load_creator_reuse_index()
        result: list[CreatorReuseEvidence] = []
        for creator in sorted(set(creators)):
            rows = index.get(creator, ())
            by_mint: dict[str, list[sqlite3.Row]] = {}
            for row in rows:
                mint = str(row["source_mint"] or "").strip()
                if mint:
                    by_mint.setdefault(mint, []).append(row)
            if len(by_mint) < 2:
                continue
            mint_evidence = tuple(
                CreatorReuseMintEvidence(
                    mint=mint,
                    supporting_edge_ids=tuple(sorted({
                        str(row["edge_id"]) for row in mint_rows if row["edge_id"]
                    })),
                    funding_transaction_signatures=tuple(sorted({
                        str(row["funding_tx_signature"])
                        for row in mint_rows if row["funding_tx_signature"]
                    })),
                    first_observed_at=min(
                        (int(row["first_observed_by_flex"])
                         for row in mint_rows if row["first_observed_by_flex"] is not None),
                        default=None,
                    ),
                    last_observed_at=max(
                        (int(row["last_observed_by_flex"])
                         for row in mint_rows if row["last_observed_by_flex"] is not None),
                        default=None,
                    ),
                )
                for mint, mint_rows in sorted(by_mint.items())
            )
            edge_ids = tuple(sorted({
                edge_id for item in mint_evidence for edge_id in item.supporting_edge_ids
            }))
            signatures = tuple(sorted({
                signature
                for item in mint_evidence
                for signature in item.funding_transaction_signatures
            }))
            first_values = tuple(
                item.first_observed_at for item in mint_evidence
                if item.first_observed_at is not None
            )
            last_values = tuple(
                item.last_observed_at for item in mint_evidence
                if item.last_observed_at is not None
            )
            result.append(CreatorReuseEvidence(
                creator_wallet=creator,
                mint_evidence=mint_evidence,
                supporting_edge_ids=edge_ids,
                funding_transaction_signatures=signatures,
                first_observed_at=min(first_values, default=None),
                last_observed_at=max(last_values, default=None),
                population_revision_ids=(population.revision_id,),
                provenance=EvidenceProvenance(
                    source="wt_provisioning_edges",
                    table="wt_provisioning_edges",
                    registry=None,
                    rpc=False,
                    transaction_signature=None,
                    observation_window=window,
                    dependency_group="creator_reuse",
                    completeness="OBSERVED",
                ),
            ))
        return tuple(result)

    def _load_creator_reuse_index(self) -> Mapping[str, tuple[sqlite3.Row, ...]]:
        if self._creator_reuse_index is not None:
            return self._creator_reuse_index
        grouped: dict[str, list[sqlite3.Row]] = {}
        if self.ops_db_path and os.path.exists(self.ops_db_path):
            try:
                conn = sqlite3.connect(
                    f"file:{self.ops_db_path}?mode=ro", uri=True, timeout=5
                )
                conn.row_factory = sqlite3.Row
                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='wt_provisioning_edges'"
                ).fetchone()
                if exists:
                    rows = conn.execute(
                        "SELECT edge_id, to_wallet, source_mint, funding_tx_signature, "
                        "first_observed_by_flex, last_observed_by_flex "
                        "FROM wt_provisioning_edges "
                        "WHERE to_wallet IS NOT NULL AND source_mint IS NOT NULL "
                        "ORDER BY to_wallet, source_mint, edge_id"
                    ).fetchall()
                    for row in rows:
                        grouped.setdefault(str(row["to_wallet"]), []).append(row)
                conn.close()
            except (OSError, sqlite3.Error, ValueError):
                grouped = {}
        self._creator_reuse_index = {
            creator: tuple(rows) for creator, rows in grouped.items()
        }
        return self._creator_reuse_index

    @staticmethod
    def population_from_canonical_registry(
        family: Mapping[str, Any],
    ) -> InvestigationPopulation:
        """Create a shadow-only population revision from persisted Registry facts."""
        population_id = str(family["family_id"])
        members = tuple(sorted(family.get("member_wallets") or ()))
        launches = tuple(sorted(family.get("launch_list") or ()))
        anchor = str(family.get("terminal_entity") or population_id)
        evidence = tuple(family.get("supporting_evidence") or ())
        timeline = tuple(family.get("evidence_timeline") or ())
        return InvestigationPopulation(
            population_id=population_id,
            anchor=anchor,
            population_basis=(),
            members=members,
            launches=launches,
            timeline=timeline,
            metadata={
                "treasuries": tuple(sorted(family.get("treasuries") or ())),
                "member_treasuries": tuple((member, ()) for member in members),
                "creators": tuple(sorted(family.get("unique_creators") or ())),
                "mechanisms": tuple(sorted(family.get("funding_mechanisms") or ())),
                "signatures": (),
                "first_seen_at": family.get("first_seen_at"),
                "last_seen_at": family.get("last_seen_at"),
                "session_count": int(family.get("session_count") or 0),
                "active_session_count": int(family.get("active_sessions") or 0),
                "observation_count": int(family.get("observation_count") or 0),
                "launch_count_hint": int(family.get("observed_launches") or 0),
                "sources": tuple(family.get("evidence_sources") or ()),
                "exclusions": tuple(family.get("exclusion_evidence") or ()),
                "warnings": tuple(family.get("data_quality_warnings") or ()),
                "edge_times": (), "evidence": evidence, "templates": (),
                "campaigns": tuple(family.get("campaigns") or ()),
                "infrastructure_roles": (), "session_amounts": (),
            },
        )

    @staticmethod
    def _missing_item(
        evidence_type: str, statement: str, entities: tuple[str, ...],
        launches: tuple[str, ...], window: tuple[int | None, int | None],
        group: str, table: str,
    ) -> EvidenceItem:
        return EvidenceItem(
            evidence_type, "MISSING", statement, entities, launches,
            EvidenceProvenance(
                source="population_evidence_inventory", table=table, registry=None,
                rpc=False, transaction_signature=None, observation_window=window,
                dependency_group=group, completeness="NOT_OBSERVED",
            ),
        )

    def _registry_context(
        self, population: InvestigationPopulation,
        window: tuple[int | None, int | None],
    ) -> list[EvidenceItem]:
        if not self.ops_db_path or not os.path.exists(self.ops_db_path):
            return []
        result: list[EvidenceItem] = []
        try:
            conn = sqlite3.connect(f"file:{self.ops_db_path}?mode=ro", uri=True, timeout=5)
            conn.row_factory = sqlite3.Row
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "wt_operation_family_confirmations" in tables:
                row = conn.execute(
                    "SELECT * FROM wt_operation_family_confirmations WHERE family_id=?",
                    (population.population_id,),
                ).fetchone()
                if row:
                    confirmation_fact = {
                        key: row[key] for key in (
                            "family_id", "confirmed", "confirmed_at", "confirmed_by",
                            "confirmation_reason", "confirmation_notes", "reversed_at",
                            "reversed_by", "reversal_reason", "updated_at",
                        ) if key in row.keys()
                    }
                    result.append(EvidenceItem(
                        "CONFIRMATION_HISTORY", "CONTEXT",
                        "An existing confirmation-history record is attached to this population identifier.",
                        population.members, population.launches,
                        EvidenceProvenance(
                            "wt_operation_family_confirmations",
                            "wt_operation_family_confirmations", "Canonical Registry", False,
                            None, window, "operation_history", "OBSERVED",
                        ),
                        confirmation_fact,
                    ))
            conn.close()
        except (OSError, sqlite3.Error):
            return result
        return result

    @staticmethod
    def _dependency_groups(items: list[EvidenceItem]) -> tuple[DependencyGroup, ...]:
        grouped: dict[str, list[EvidenceItem]] = {}
        for item in items:
            grouped.setdefault(item.provenance.dependency_group, []).append(item)
        return tuple(
            DependencyGroup(
                name, tuple(item.evidence_id for item in values),
                tuple(sorted({item.provenance.source for item in values})),
            )
            for name, values in sorted(grouped.items())
        )

    @staticmethod
    def _explain(
        population: InvestigationPopulation, supporting: list[EvidenceItem],
        contradictory: list[EvidenceItem], context: list[EvidenceItem],
        missing: list[EvidenceItem],
    ) -> ReconciliationExplainability:
        population_refs = tuple(item.evidence_id for item in supporting if item.evidence_type in {
            "TREASURY_RELATIONSHIPS", "PROVISIONING_LINEAGE", "PROVISIONING_SESSIONS",
        })
        if not population_refs:
            population_refs = (missing[0].evidence_id,)
        why_population = (ExplainabilityStatement(
            "The population exists because the Family Builder grouped persisted membership and lineage facts.",
            population_refs,
        ),)
        member_map = {}
        for member in population.members:
            refs = tuple(
                item.evidence_id for item in supporting
                if member in item.entities and item.evidence_type in {
                    "TREASURY_RELATIONSHIPS", "PROVISIONING_LINEAGE", "PROVISIONING_SESSIONS",
                }
            )
            member_map[member] = (ExplainabilityStatement(
                f"{member} belongs to this population because it is present in persisted population membership evidence.",
                refs or population_refs,
            ),)
        return ReconciliationExplainability(
            why_population_exists=why_population,
            why_members_belong=member_map,
            supporting_facts=tuple(ExplainabilityStatement(item.statement, (item.evidence_id,)) for item in supporting),
            contradictory_facts=tuple(ExplainabilityStatement(item.statement, (item.evidence_id,)) for item in contradictory),
            unknown_facts=tuple(ExplainabilityStatement(item.statement, (item.evidence_id,)) for item in missing),
        )
