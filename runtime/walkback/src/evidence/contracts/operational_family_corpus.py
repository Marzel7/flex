"""EB0.4E deterministic per-role operational-family corpora."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from typing import Iterable, Mapping, Tuple

from .operational_family_manifest import OperationalFamilyManifest, verify_operational_family_manifest
from .operational_family_nomination import OperationBehaviourFact, OperationalFamilyNomination


CORPUS_SCHEMA_VERSION = "eb0.4e.v1"


class OperationalFamilyCorpusError(ValueError):
    """Named fail-closed EB0.4E error."""


@dataclass(frozen=True)
class OperationalFamilyCorpus:
    schema_version: str
    primary_role: str
    source_manifest_digests: Tuple[str, ...]
    operation_count: int
    fact_count: int
    nomination_count: int
    source_count: int
    nomination_state_counts: Mapping[str, int]
    quality_counts: Mapping[str, int]
    completeness_counts: Mapping[str, int]
    conflict_group_count: int
    facts: Tuple[OperationBehaviourFact, ...]
    nominations: Tuple[OperationalFamilyNomination, ...]
    corpus_digest: str


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256(payload.encode()).hexdigest()


def _counts(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def assemble_operational_family_corpora(
    manifests: Iterable[OperationalFamilyManifest],
) -> Tuple[OperationalFamilyCorpus, ...]:
    material = tuple(manifests)
    if not material:
        raise OperationalFamilyCorpusError("EB0_4E_EMPTY_INPUT")
    facts_by_role: dict[str, dict[str, OperationBehaviourFact]] = {}
    nominations_by_role: dict[str, dict[str, OperationalFamilyNomination]] = {}
    lineage: dict[str, set[str]] = {}
    for manifest in material:
        try:
            verify_operational_family_manifest(manifest, manifest.facts, manifest.nominations)
        except Exception as exc:
            raise OperationalFamilyCorpusError("EB0_4E_UNVERIFIED_MANIFEST") from exc
        for fact in manifest.facts:
            prior = facts_by_role.setdefault(fact.role, {}).get(fact.fact_id)
            if prior is not None and prior != fact:
                raise OperationalFamilyCorpusError("EB0_4E_FACT_ID_COLLISION")
            facts_by_role[fact.role][fact.fact_id] = fact
        for nomination in manifest.nominations:
            role = nomination.primary_role
            prior = nominations_by_role.setdefault(role, {}).get(nomination.nomination_id)
            if prior is not None and prior != nomination:
                raise OperationalFamilyCorpusError("EB0_4E_NOMINATION_ID_COLLISION")
            nominations_by_role[role][nomination.nomination_id] = nomination
            lineage.setdefault(role, set()).add(manifest.manifest_digest)
    if set(facts_by_role) != set(nominations_by_role):
        raise OperationalFamilyCorpusError("EB0_4E_ORPHAN_ROLE_EVIDENCE")
    result = []
    for role in sorted(nominations_by_role):
        facts = tuple(facts_by_role[role][key] for key in sorted(facts_by_role[role]))
        nominations = tuple(nominations_by_role[role][key] for key in sorted(nominations_by_role[role]))
        body = {
            "schema_version": CORPUS_SCHEMA_VERSION,
            "primary_role": role,
            "source_manifest_digests": tuple(sorted(lineage[role])),
            "operation_count": len({item.operation_id for item in facts}),
            "fact_count": len(facts),
            "nomination_count": len(nominations),
            "source_count": len({f"{item.source}:{item.source_version}" for item in facts}),
            "nomination_state_counts": _counts(item.nomination_state for item in nominations),
            "quality_counts": _counts(item.quality_state for item in facts),
            "completeness_counts": _counts(item.completeness_state for item in facts),
            "conflict_group_count": len({item.conflict_group_id for item in facts if item.conflict_group_id}),
            "facts": [asdict(item) for item in facts],
            "nominations": [asdict(item) for item in nominations],
        }
        result.append(OperationalFamilyCorpus(
            **{key: body[key] for key in body if key not in {"facts", "nominations"}},
            facts=facts, nominations=nominations, corpus_digest=_digest(body),
        ))
    return tuple(result)


def verify_operational_family_corpora(
    corpora: Iterable[OperationalFamilyCorpus],
    manifests: Iterable[OperationalFamilyManifest],
) -> bool:
    if tuple(corpora) != assemble_operational_family_corpora(manifests):
        raise OperationalFamilyCorpusError("EB0_4E_REPLAY_MISMATCH")
    return True
