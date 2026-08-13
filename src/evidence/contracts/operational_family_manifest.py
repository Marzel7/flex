"""EB0.4D immutable manifests for operational-family nominations."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from typing import Iterable, Mapping, Tuple

from .operational_family_adapters import ADAPTER_VERSION
from .operational_family_nomination import (
    CONTRACT_VERSION,
    OperationBehaviourFact,
    OperationalFamilyNomination,
    nominate_operational_family,
    project_operation_behaviour_facts,
)


MANIFEST_SCHEMA_VERSION = "eb0.4d.v1"


class OperationalFamilyManifestError(ValueError):
    """Named fail-closed EB0.4D error."""


@dataclass(frozen=True)
class OperationalFamilyManifest:
    schema_version: str
    contract_version: str
    adapter_version: str
    input_digest: str
    fact_digest: str
    nomination_digest: str
    fact_count: int
    nomination_count: int
    operation_count: int
    role_counts: Mapping[str, int]
    nomination_state_counts: Mapping[str, int]
    quality_counts: Mapping[str, int]
    completeness_counts: Mapping[str, int]
    conflicting_fact_count: int
    facts: Tuple[OperationBehaviourFact, ...]
    nominations: Tuple[OperationalFamilyNomination, ...]
    manifest_digest: str


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _digest(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


def _counts(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def _canonical_fact(fact: OperationBehaviourFact) -> OperationBehaviourFact:
    record = asdict(fact)
    record.pop("provenance_digest")
    record.pop("fact_id")
    projected = project_operation_behaviour_facts([record])[0]
    if projected != fact:
        raise OperationalFamilyManifestError("EB0_4D_NONCANONICAL_FACT")
    return projected


def _canonical_nomination(
    nomination: OperationalFamilyNomination,
    facts: Mapping[str, OperationBehaviourFact],
) -> OperationalFamilyNomination:
    try:
        material = [facts[fact_id] for fact_id in nomination.supporting_fact_ids]
    except KeyError as exc:
        raise OperationalFamilyManifestError("EB0_4D_MISSING_SUPPORTING_FACT") from exc
    rebuilt = nominate_operational_family(material, nomination_state=nomination.nomination_state)
    if rebuilt != nomination:
        raise OperationalFamilyManifestError("EB0_4D_NONCANONICAL_NOMINATION")
    return rebuilt


def build_operational_family_manifest(
    facts: Iterable[OperationBehaviourFact],
    nominations: Iterable[OperationalFamilyNomination],
) -> OperationalFamilyManifest:
    canonical_facts = [_canonical_fact(item) for item in facts]
    if not canonical_facts:
        raise OperationalFamilyManifestError("EB0_4D_EMPTY_FACTS")
    if len({item.fact_id for item in canonical_facts}) != len(canonical_facts):
        raise OperationalFamilyManifestError("EB0_4D_DUPLICATE_FACT")
    ordered_facts = tuple(sorted(canonical_facts, key=lambda item: item.fact_id))
    by_id = {item.fact_id: item for item in ordered_facts}
    canonical_nominations = [_canonical_nomination(item, by_id) for item in nominations]
    if not canonical_nominations:
        raise OperationalFamilyManifestError("EB0_4D_EMPTY_NOMINATIONS")
    if len({item.nomination_id for item in canonical_nominations}) != len(canonical_nominations):
        raise OperationalFamilyManifestError("EB0_4D_DUPLICATE_NOMINATION")
    ordered_nominations = tuple(sorted(canonical_nominations, key=lambda item: item.nomination_id))
    body = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "input_digest": _digest({"facts": [asdict(x) for x in ordered_facts], "nominations": [asdict(x) for x in ordered_nominations]}),
        "fact_digest": _digest([asdict(x) for x in ordered_facts]),
        "nomination_digest": _digest([asdict(x) for x in ordered_nominations]),
        "fact_count": len(ordered_facts),
        "nomination_count": len(ordered_nominations),
        "operation_count": len({x.operation_id for x in ordered_facts}),
        "role_counts": _counts(x.role for x in ordered_facts),
        "nomination_state_counts": _counts(x.nomination_state for x in ordered_nominations),
        "quality_counts": _counts(x.quality_state for x in ordered_facts),
        "completeness_counts": _counts(x.completeness_state for x in ordered_facts),
        "conflicting_fact_count": sum(x.quality_state == "CONFLICTING" for x in ordered_facts),
        "facts": [asdict(x) for x in ordered_facts],
        "nominations": [asdict(x) for x in ordered_nominations],
    }
    return OperationalFamilyManifest(
        **{key: body[key] for key in body if key not in {"facts", "nominations"}},
        facts=ordered_facts,
        nominations=ordered_nominations,
        manifest_digest=_digest(body),
    )


def verify_operational_family_manifest(
    manifest: OperationalFamilyManifest,
    facts: Iterable[OperationBehaviourFact],
    nominations: Iterable[OperationalFamilyNomination],
) -> bool:
    if manifest.schema_version != MANIFEST_SCHEMA_VERSION:
        raise OperationalFamilyManifestError("EB0_4D_SCHEMA_VERSION_MISMATCH")
    if manifest.contract_version != CONTRACT_VERSION or manifest.adapter_version != ADAPTER_VERSION:
        raise OperationalFamilyManifestError("EB0_4D_VERSION_MISMATCH")
    if build_operational_family_manifest(facts, nominations) != manifest:
        raise OperationalFamilyManifestError("EB0_4D_REPLAY_MISMATCH")
    return True
