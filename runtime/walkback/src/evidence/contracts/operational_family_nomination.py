"""EB0.4A pure operational-family nomination evidence contract.

Behavioural similarity is nomination evidence only.  This module has no
operator identity, attribution, profile, rank, score, policy, or I/O path.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from typing import Iterable, Mapping, Optional, Tuple


CONTRACT_VERSION = "eb0.4a.v1"
AUTHORITY_CLASS = "NOMINATION_NON_AUTHORITATIVE"
QUALITY_STATES = frozenset({"OBSERVED", "CONFLICTING", "DEGRADED"})
COMPLETENESS_STATES = frozenset({"COMPLETE", "PARTIAL", "NOT_OBSERVED"})
NOMINATION_STATES = frozenset({"PROPOSED", "SUPPORTED"})
_FORBIDDEN_KEYS = (
    "operator", "owner", "identity", "attribution", "confidence", "score",
    "rank", "profit", "cashflow", "policy", "confirmed",
)


class OperationalFamilyNominationError(ValueError):
    """Named fail-closed EB0.4A contract error."""


@dataclass(frozen=True)
class OperationBehaviourFact:
    operation_id: str
    role: str
    edge_features: Tuple[str, ...]
    mechanism_features: Tuple[str, ...]
    temporal_features: Tuple[str, ...]
    source: str
    source_version: str
    source_record_digest: str
    quality_state: str
    completeness_state: str
    conflict_group_id: Optional[str]
    provenance_digest: str
    fact_id: str


@dataclass(frozen=True)
class OperationalFamilyNomination:
    contract_version: str
    authority_class: str
    nomination_state: str
    primary_role: str
    member_operation_ids: Tuple[str, ...]
    supporting_fact_ids: Tuple[str, ...]
    shared_edge_features: Tuple[str, ...]
    shared_mechanism_features: Tuple[str, ...]
    shared_temporal_features: Tuple[str, ...]
    supporting_sources: Tuple[str, ...]
    quality_state: str
    completeness_state: str
    conflict_group_ids: Tuple[str, ...]
    operator_identity_asserted: bool
    nomination_id: str


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _digest(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


def _fail(code: str) -> None:
    raise OperationalFamilyNominationError(f"EB0_4A_{code}")


def _text(record: Mapping[str, object], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        _fail(f"INVALID_{field.upper()}")
    return value.strip()


def _features(record: Mapping[str, object], field: str) -> Tuple[str, ...]:
    value = record.get(field)
    if not isinstance(value, (list, tuple)):
        _fail(f"INVALID_{field.upper()}")
    result = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            _fail(f"INVALID_{field.upper()}")
        normalized = item.strip()
        lowered = normalized.lower()
        if any(key in lowered for key in _FORBIDDEN_KEYS):
            _fail("FORBIDDEN_SEMANTICS")
        result.append(normalized)
    if len(result) != len(set(result)):
        _fail(f"DUPLICATE_{field.upper()}")
    return tuple(sorted(result))


def project_operation_behaviour_facts(
    records: Iterable[Mapping[str, object]],
) -> Tuple[OperationBehaviourFact, ...]:
    facts = {}
    for record in records:
        if not isinstance(record, Mapping):
            _fail("INVALID_FACT_RECORD")
        forbidden = [key for key in record if any(term in str(key).lower() for term in _FORBIDDEN_KEYS)]
        if forbidden:
            _fail("FORBIDDEN_FIELD")
        operation_id = _text(record, "operation_id")
        role = _text(record, "role")
        edges = _features(record, "edge_features")
        mechanisms = _features(record, "mechanism_features")
        temporal = _features(record, "temporal_features")
        if not mechanisms and not temporal:
            _fail("BEHAVIOUR_FEATURE_REQUIRED")
        source = _text(record, "source")
        source_version = _text(record, "source_version")
        source_digest = _text(record, "source_record_digest")
        quality = _text(record, "quality_state")
        completeness = _text(record, "completeness_state")
        conflict = record.get("conflict_group_id")
        if quality not in QUALITY_STATES:
            _fail("UNKNOWN_QUALITY_STATE")
        if completeness not in COMPLETENESS_STATES:
            _fail("UNKNOWN_COMPLETENESS_STATE")
        if quality == "CONFLICTING":
            if not isinstance(conflict, str) or not conflict.strip():
                _fail("CONFLICT_GROUP_REQUIRED")
            conflict = conflict.strip()
        elif conflict is not None:
            _fail("UNUSED_CONFLICT_GROUP")
        body = {
            "contract_version": CONTRACT_VERSION,
            "operation_id": operation_id, "role": role,
            "edge_features": edges, "mechanism_features": mechanisms,
            "temporal_features": temporal, "source": source,
            "source_version": source_version, "source_record_digest": source_digest,
            "quality_state": quality, "completeness_state": completeness,
            "conflict_group_id": conflict,
        }
        provenance = _digest({
            "contract_version": CONTRACT_VERSION, "operation_id": operation_id,
            "source": source, "source_version": source_version,
            "source_record_digest": source_digest,
        })
        fact = OperationBehaviourFact(
            operation_id, role, edges, mechanisms, temporal, source, source_version,
            source_digest, quality, completeness, conflict, provenance,
            _digest({**body, "provenance_digest": provenance}),
        )
        facts[fact.fact_id] = fact
    return tuple(facts[key] for key in sorted(facts))


def nominate_operational_family(
    facts: Iterable[OperationBehaviourFact], *, nomination_state: str,
) -> OperationalFamilyNomination:
    supplied = tuple(facts)
    material_list = []
    for fact in supplied:
        record = {
            "operation_id": fact.operation_id, "role": fact.role,
            "edge_features": list(fact.edge_features),
            "mechanism_features": list(fact.mechanism_features),
            "temporal_features": list(fact.temporal_features),
            "source": fact.source, "source_version": fact.source_version,
            "source_record_digest": fact.source_record_digest,
            "quality_state": fact.quality_state,
            "completeness_state": fact.completeness_state,
            "conflict_group_id": fact.conflict_group_id,
        }
        canonical = project_operation_behaviour_facts([record])[0]
        if canonical != fact:
            _fail("NONCANONICAL_FACT")
        material_list.append(canonical)
    material = tuple(sorted(material_list, key=lambda item: item.fact_id))
    if len(material) < 2 or len({item.operation_id for item in material}) < 2:
        _fail("MULTI_OPERATION_EVIDENCE_REQUIRED")
    if len({item.fact_id for item in material}) != len(material):
        _fail("DUPLICATE_FACT")
    if nomination_state not in NOMINATION_STATES:
        _fail("AUTHORITY_PROMOTION_REJECTED")
    roles = {item.role for item in material}
    if len(roles) != 1:
        _fail("ROLE_MISMATCH")
    primary_role = next(iter(roles))
    shared_edges = tuple(sorted(set.intersection(*(set(item.edge_features) for item in material))))
    shared_mechanisms = tuple(sorted(set.intersection(*(set(item.mechanism_features) for item in material))))
    shared_temporal = tuple(sorted(set.intersection(*(set(item.temporal_features) for item in material))))
    if not shared_mechanisms and not shared_temporal:
        _fail("SHARED_BEHAVIOUR_REQUIRED")
    sources = tuple(sorted({f"{item.source}:{item.source_version}" for item in material}))
    conflicts = tuple(sorted({item.conflict_group_id for item in material if item.conflict_group_id}))
    if nomination_state == "SUPPORTED":
        if not shared_mechanisms or not shared_temporal:
            _fail("SUPPORTED_REQUIRES_MECHANISM_AND_TEMPORAL")
        if len(sources) < 2:
            _fail("SUPPORTED_REQUIRES_TWO_SOURCES")
        if any(item.quality_state != "OBSERVED" for item in material) or any(
            item.completeness_state != "COMPLETE" for item in material
        ):
            _fail("SUPPORTED_REQUIRES_COMPLETE_NONCONFLICTING_EVIDENCE")
    quality = "CONFLICTING" if conflicts else (
        "DEGRADED" if any(item.quality_state == "DEGRADED" for item in material) else "OBSERVED"
    )
    completeness = "COMPLETE" if all(item.completeness_state == "COMPLETE" for item in material) else "PARTIAL"
    body = {
        "contract_version": CONTRACT_VERSION,
        "authority_class": AUTHORITY_CLASS,
        "nomination_state": nomination_state,
        "primary_role": primary_role,
        "member_operation_ids": tuple(sorted({item.operation_id for item in material})),
        "supporting_fact_ids": tuple(item.fact_id for item in material),
        "shared_edge_features": shared_edges,
        "shared_mechanism_features": shared_mechanisms,
        "shared_temporal_features": shared_temporal,
        "supporting_sources": sources,
        "quality_state": quality,
        "completeness_state": completeness,
        "conflict_group_ids": conflicts,
        "operator_identity_asserted": False,
    }
    return OperationalFamilyNomination(**body, nomination_id=_digest(body))


def nomination_digest(nominations: Iterable[OperationalFamilyNomination]) -> str:
    return _digest([asdict(item) for item in sorted(nominations, key=lambda item: item.nomination_id)])
