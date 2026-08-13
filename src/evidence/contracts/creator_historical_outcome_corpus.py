"""Deterministic EB0.2E per-creator corpus assembly."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from typing import Iterable, Mapping, Tuple

from .creator_historical_outcome import CreatorHistoricalOutcomeFact
from .creator_historical_outcome_manifest import (
    CreatorHistoricalOutcomeManifest,
    verify_creator_historical_outcome_manifest,
)


CORPUS_SCHEMA_VERSION = "eb0.2e.v1"


class CreatorHistoricalOutcomeCorpusError(ValueError):
    """Named fail-closed error for invalid EB0.2E corpus inputs."""


@dataclass(frozen=True)
class CreatorHistoricalOutcomeCorpus:
    schema_version: str
    creator: str
    source_manifest_digests: Tuple[str, ...]
    fact_count: int
    mint_count: int
    eligible_denominator_count: int
    unknown_count: int
    not_observed_count: int
    conflicting_fact_count: int
    outcome_kind_counts: Mapping[str, int]
    outcome_state_counts: Mapping[str, int]
    quality_counts: Mapping[str, int]
    completeness_counts: Mapping[str, int]
    facts: Tuple[CreatorHistoricalOutcomeFact, ...]
    corpus_digest: str

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["facts"] = [asdict(item) for item in self.facts]
        return payload


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _digest(value: object) -> str:
    return sha256(_canonical_json(value)).hexdigest()


def _counts(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def assemble_creator_historical_outcome_corpora(
    manifests: Iterable[CreatorHistoricalOutcomeManifest],
) -> Tuple[CreatorHistoricalOutcomeCorpus, ...]:
    material = list(manifests)
    if not material:
        raise CreatorHistoricalOutcomeCorpusError("EB0_2E_EMPTY_INPUT")

    grouped: dict[str, dict[str, CreatorHistoricalOutcomeFact]] = {}
    lineage: dict[str, set[str]] = {}
    for manifest in material:
        try:
            verify_creator_historical_outcome_manifest(manifest, manifest.facts)
        except Exception as exc:
            raise CreatorHistoricalOutcomeCorpusError("EB0_2E_UNVERIFIED_MANIFEST") from exc
        for fact in manifest.facts:
            creator = fact.creator.strip()
            if not creator:
                raise CreatorHistoricalOutcomeCorpusError("EB0_2E_INVALID_CREATOR")
            prior = grouped.setdefault(creator, {}).get(fact.fact_id)
            if prior is not None and prior != fact:
                raise CreatorHistoricalOutcomeCorpusError("EB0_2E_FACT_ID_COLLISION")
            grouped[creator][fact.fact_id] = fact
            lineage.setdefault(creator, set()).add(manifest.manifest_digest)

    corpora = []
    for creator in sorted(grouped):
        facts = tuple(grouped[creator][key] for key in sorted(grouped[creator]))
        manifest_digests = tuple(sorted(lineage[creator]))
        body = {
            "schema_version": CORPUS_SCHEMA_VERSION,
            "creator": creator,
            "source_manifest_digests": manifest_digests,
            "fact_count": len(facts),
            "mint_count": len({item.mint for item in facts}),
            "eligible_denominator_count": sum(item.denominator_eligible for item in facts),
            "unknown_count": sum(item.outcome_state == "UNKNOWN" for item in facts),
            "not_observed_count": sum(
                item.completeness_state == "NOT_OBSERVED" for item in facts
            ),
            "conflicting_fact_count": sum(item.quality_state == "CONFLICTING" for item in facts),
            "outcome_kind_counts": _counts(item.outcome_kind for item in facts),
            "outcome_state_counts": _counts(item.outcome_state for item in facts),
            "quality_counts": _counts(item.quality_state for item in facts),
            "completeness_counts": _counts(item.completeness_state for item in facts),
            "facts": [asdict(item) for item in facts],
        }
        corpora.append(
            CreatorHistoricalOutcomeCorpus(
                schema_version=CORPUS_SCHEMA_VERSION,
                creator=creator,
                source_manifest_digests=manifest_digests,
                fact_count=body["fact_count"],
                mint_count=body["mint_count"],
                eligible_denominator_count=body["eligible_denominator_count"],
                unknown_count=body["unknown_count"],
                not_observed_count=body["not_observed_count"],
                conflicting_fact_count=body["conflicting_fact_count"],
                outcome_kind_counts=body["outcome_kind_counts"],
                outcome_state_counts=body["outcome_state_counts"],
                quality_counts=body["quality_counts"],
                completeness_counts=body["completeness_counts"],
                facts=facts,
                corpus_digest=_digest(body),
            )
        )
    return tuple(corpora)


def verify_creator_historical_outcome_corpora(
    corpora: Iterable[CreatorHistoricalOutcomeCorpus],
    manifests: Iterable[CreatorHistoricalOutcomeManifest],
) -> bool:
    expected = assemble_creator_historical_outcome_corpora(manifests)
    actual = tuple(corpora)
    if actual != expected:
        raise CreatorHistoricalOutcomeCorpusError("EB0_2E_REPLAY_MISMATCH")
    return True
