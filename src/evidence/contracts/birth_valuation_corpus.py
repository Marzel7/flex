"""Pure EB0.1E per-mint canonical corpus assembly."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Iterable, Mapping, Tuple

from .birth_valuation_manifest import (
    BirthValuationManifest,
    build_birth_valuation_manifest,
)


CORPUS_SCHEMA_VERSION = "eb0.1e.v1"


class BirthValuationCorpusError(ValueError):
    """Named fail-closed corpus assembly error."""


@dataclass(frozen=True)
class ExcludedObservation:
    mint: str
    event_kind: str
    observation_id: str
    event_time_utc_ns: int
    selected_boundary_utc_ns: int
    reason: str


@dataclass(frozen=True)
class MintCorpus:
    schema_version: str
    mint: str
    manifest: BirthValuationManifest
    excluded: Tuple[ExcludedObservation, ...]
    excluded_reason_counts: Mapping[str, int]
    corpus_digest: str


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: object) -> str:
    return sha256(_canonical_json(value)).hexdigest()


def assemble_birth_valuation_corpora(
    records: Iterable[Mapping[str, object]],
) -> Tuple[MintCorpus, ...]:
    """Select earliest per-kind boundaries and preserve all facts tied there."""

    material = [dict(item) for item in records]
    if not material:
        raise BirthValuationCorpusError("EB0_1E_EMPTY_INPUT")
    grouped: dict[str, list[dict[str, object]]] = {}
    for record in material:
        mint = record.get("mint")
        if not isinstance(mint, str) or not mint.strip():
            raise BirthValuationCorpusError("EB0_1E_INVALID_MINT")
        grouped.setdefault(mint.strip(), []).append(record)

    corpora = []
    for mint in sorted(grouped):
        by_kind: dict[str, list[dict[str, object]]] = {}
        for record in grouped[mint]:
            kind = record.get("event_kind")
            if not isinstance(kind, str) or not kind:
                raise BirthValuationCorpusError("EB0_1E_INVALID_EVENT_KIND")
            by_kind.setdefault(kind, []).append(record)

        selected: list[dict[str, object]] = []
        excluded: list[ExcludedObservation] = []
        for kind in sorted(by_kind):
            candidates = by_kind[kind]
            times = [item.get("event_time_utc_ns") for item in candidates]
            if any(isinstance(value, bool) or not isinstance(value, int) for value in times):
                raise BirthValuationCorpusError("EB0_1E_INVALID_EVENT_TIME")
            boundary = min(times)
            for item in candidates:
                if item["event_time_utc_ns"] == boundary:
                    selected.append(item)
                else:
                    observation = build_birth_valuation_manifest([item]).observations[0]
                    excluded.append(
                        ExcludedObservation(
                            mint=mint,
                            event_kind=kind,
                            observation_id=observation.observation_id,
                            event_time_utc_ns=item["event_time_utc_ns"],
                            selected_boundary_utc_ns=boundary,
                            reason="LATER_THAN_EARLIEST_EVENT_KIND_BOUNDARY",
                        )
                    )
        manifest = build_birth_valuation_manifest(selected)
        excluded.sort(key=lambda item: item.observation_id)
        counts = {"LATER_THAN_EARLIEST_EVENT_KIND_BOUNDARY": len(excluded)} if excluded else {}
        body = {
            "schema_version": CORPUS_SCHEMA_VERSION,
            "mint": mint,
            "manifest_digest": manifest.manifest_digest,
            "excluded": [item.__dict__ for item in excluded],
            "excluded_reason_counts": counts,
        }
        corpora.append(
            MintCorpus(
                schema_version=CORPUS_SCHEMA_VERSION,
                mint=mint,
                manifest=manifest,
                excluded=tuple(excluded),
                excluded_reason_counts=counts,
                corpus_digest=_digest(body),
            )
        )
    return tuple(corpora)
