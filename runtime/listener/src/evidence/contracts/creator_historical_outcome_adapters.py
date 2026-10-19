"""Frozen EB0.2C adapters from canonical EB0.1 corpus evidence.

The caller must supply provenance-bound creator identity and observation-window
facts. This module performs no discovery, I/O, ranking, scoring, or attribution.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from hashlib import sha256
import json
from typing import Optional, Tuple

from .birth_valuation_corpus import MintCorpus
from .creator_historical_outcome import (
    CreatorHistoricalOutcomeFact,
    project_creator_historical_outcomes,
)


ADAPTER_VERSION = "eb0.2c.v1"
QUALIFIED_CREATOR_METHODS = frozenset({"PF_WS_CREATOR_VERIFIED", "CANONICAL_CREATE_PROOF"})
COHORT_EVENT_KINDS = frozenset({"CHAIN_BIRTH", "PLATFORM_FIRST_SEEN", "MIGRATION"})


class CreatorHistoricalOutcomeAdapterError(ValueError):
    """Named fail-closed error for invalid EB0.2C adapter inputs."""


@dataclass(frozen=True)
class CreatorIdentityFact:
    mint: str
    creator: str
    resolution_method: str
    source: str
    source_version: str
    source_record_digest: str
    provenance_digest: str


@dataclass(frozen=True)
class ObservationWindowFact:
    mint: str
    observed_through_utc_ns: int
    full_horizon_complete: bool
    source: str
    source_version: str
    source_record_digest: str
    provenance_digest: str


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256(payload.encode("utf-8")).hexdigest()


def _validate_text(value: object, error: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CreatorHistoricalOutcomeAdapterError(error)
    return value.strip()


def build_creator_identity_fact(
    *,
    mint: str,
    creator: str,
    resolution_method: str,
    source: str,
    source_version: str,
    source_record_digest: str,
) -> CreatorIdentityFact:
    mint = _validate_text(mint, "EB0_2C_INVALID_CREATOR_MINT")
    creator = _validate_text(creator, "EB0_2C_INVALID_CREATOR")
    method = _validate_text(resolution_method, "EB0_2C_INVALID_CREATOR_METHOD")
    source = _validate_text(source, "EB0_2C_INVALID_CREATOR_SOURCE")
    source_version = _validate_text(source_version, "EB0_2C_INVALID_CREATOR_SOURCE_VERSION")
    source_record_digest = _validate_text(
        source_record_digest, "EB0_2C_INVALID_CREATOR_SOURCE_RECORD_DIGEST"
    )
    if method not in QUALIFIED_CREATOR_METHODS:
        raise CreatorHistoricalOutcomeAdapterError("EB0_2C_UNQUALIFIED_CREATOR_IDENTITY")
    body = {
        "mint": mint,
        "creator": creator,
        "resolution_method": method,
        "source": source,
        "source_version": source_version,
        "source_record_digest": source_record_digest,
    }
    return CreatorIdentityFact(**body, provenance_digest=_digest(body))


def build_observation_window_fact(
    *,
    mint: str,
    observed_through_utc_ns: int,
    full_horizon_complete: bool,
    source: str,
    source_version: str,
    source_record_digest: str,
) -> ObservationWindowFact:
    mint = _validate_text(mint, "EB0_2C_INVALID_WINDOW_MINT")
    if (
        isinstance(observed_through_utc_ns, bool)
        or not isinstance(observed_through_utc_ns, int)
        or observed_through_utc_ns < 0
    ):
        raise CreatorHistoricalOutcomeAdapterError("EB0_2C_INVALID_WINDOW_CUTOFF")
    if not isinstance(full_horizon_complete, bool):
        raise CreatorHistoricalOutcomeAdapterError("EB0_2C_INVALID_WINDOW_COMPLETENESS")
    source = _validate_text(source, "EB0_2C_INVALID_WINDOW_SOURCE")
    source_version = _validate_text(source_version, "EB0_2C_INVALID_WINDOW_SOURCE_VERSION")
    source_record_digest = _validate_text(
        source_record_digest, "EB0_2C_INVALID_WINDOW_SOURCE_RECORD_DIGEST"
    )
    body = {
        "mint": mint,
        "observed_through_utc_ns": observed_through_utc_ns,
        "full_horizon_complete": full_horizon_complete,
        "source": source,
        "source_version": source_version,
        "source_record_digest": source_record_digest,
    }
    return ObservationWindowFact(**body, provenance_digest=_digest(body))


def _cohort_time(corpus: MintCorpus, event_kind: str) -> int:
    if event_kind not in COHORT_EVENT_KINDS:
        raise CreatorHistoricalOutcomeAdapterError("EB0_2C_UNQUALIFIED_COHORT_EVENT_KIND")
    times = {
        item.event_time_utc_ns
        for item in corpus.manifest.observations
        if item.event_kind == event_kind
    }
    if not times:
        raise CreatorHistoricalOutcomeAdapterError("EB0_2C_COHORT_EVENT_NOT_OBSERVED")
    if len(times) != 1:
        raise CreatorHistoricalOutcomeAdapterError("EB0_2C_AMBIGUOUS_COHORT_BOUNDARY")
    return next(iter(times))


def adapt_creator_outcome(
    *,
    corpus: MintCorpus,
    creator_identity: CreatorIdentityFact,
    observation_window: ObservationWindowFact,
    cohort_event_kind: str,
    outcome_kind: str,
    horizon_utc_ns: int,
    threshold_value: Optional[str] = None,
) -> Tuple[CreatorHistoricalOutcomeFact, ...]:
    """Adapt one frozen corpus/window into one or more provenance-preserving facts."""

    if not (corpus.mint == creator_identity.mint == observation_window.mint):
        raise CreatorHistoricalOutcomeAdapterError("EB0_2C_MINT_MISMATCH")
    if isinstance(horizon_utc_ns, bool) or not isinstance(horizon_utc_ns, int) or horizon_utc_ns <= 0:
        raise CreatorHistoricalOutcomeAdapterError("EB0_2C_INVALID_HORIZON")
    cohort_time = _cohort_time(corpus, cohort_event_kind)
    horizon_end = cohort_time + horizon_utc_ns
    if observation_window.observed_through_utc_ns < cohort_time:
        raise CreatorHistoricalOutcomeAdapterError("EB0_2C_WINDOW_PRECEDES_COHORT")

    if outcome_kind == "MIGRATION_BY_HORIZON":
        if threshold_value is not None:
            raise CreatorHistoricalOutcomeAdapterError("EB0_2C_UNUSED_THRESHOLD")
        candidates = [
            item
            for item in corpus.manifest.observations
            if item.event_kind == "MIGRATION"
            and cohort_time <= item.event_time_utc_ns <= horizon_end
            and item.event_time_utc_ns <= observation_window.observed_through_utc_ns
        ]
    elif outcome_kind == "MARKET_CAP_AT_LEAST_BY_HORIZON":
        if not isinstance(threshold_value, str):
            raise CreatorHistoricalOutcomeAdapterError("EB0_2C_MISSING_THRESHOLD")
        try:
            threshold = Decimal(threshold_value)
        except Exception as exc:
            raise CreatorHistoricalOutcomeAdapterError("EB0_2C_INVALID_THRESHOLD") from exc
        if not threshold.is_finite() or threshold <= 0:
            raise CreatorHistoricalOutcomeAdapterError("EB0_2C_INVALID_THRESHOLD")
        candidates = [
            item
            for item in corpus.manifest.observations
            if item.event_kind == "MARKET_FIRST_OBSERVED"
            and item.price_or_market_cap_value is not None
            and item.valuation_semantics in {"MARKET_CAP_AT_EVENT", "BIRTH_MARKET_CAP"}
            and Decimal(item.price_or_market_cap_value) >= threshold
            and cohort_time <= item.event_time_utc_ns <= horizon_end
            and item.event_time_utc_ns <= observation_window.observed_through_utc_ns
        ]
    else:
        raise CreatorHistoricalOutcomeAdapterError("EB0_2C_UNKNOWN_OUTCOME_KIND")

    complete = (
        observation_window.full_horizon_complete
        and observation_window.observed_through_utc_ns >= horizon_end
    )
    if candidates:
        state = "OBSERVED_TRUE"
        event_time = min(item.event_time_utc_ns for item in candidates)
        completeness = "COMPLETE" if complete else "PARTIAL"
        quality = "CONFLICTING" if any(item.quality_state == "CONFLICTING" for item in candidates) else candidates[0].quality_state
    elif complete:
        state = "OBSERVED_FALSE"
        event_time = None
        completeness = "COMPLETE"
        quality = "VERIFIED"
    else:
        state = "UNKNOWN"
        event_time = None
        completeness = "NOT_OBSERVED"
        quality = "UNKNOWN"

    lineage = {
        "adapter_version": ADAPTER_VERSION,
        "corpus_digest": corpus.corpus_digest,
        "manifest_digest": corpus.manifest.manifest_digest,
        "creator_identity": asdict(creator_identity),
        "observation_window": asdict(observation_window),
        "cohort_event_kind": cohort_event_kind,
        "outcome_kind": outcome_kind,
        "candidate_observation_ids": sorted(item.observation_id for item in candidates),
    }
    record = {
        "creator": creator_identity.creator,
        "mint": corpus.mint,
        "cohort_event_time_utc_ns": cohort_time,
        "outcome_kind": outcome_kind,
        "horizon_utc_ns": horizon_utc_ns,
        "observed_through_utc_ns": observation_window.observed_through_utc_ns,
        "outcome_state": state,
        "outcome_event_time_utc_ns": event_time,
        "threshold_value": threshold_value,
        "source": "EB0_2C_FROZEN_CANONICAL_ADAPTER",
        "source_version": ADAPTER_VERSION,
        "quality_state": quality,
        "completeness_state": completeness,
        "source_record_digest": _digest(lineage),
    }
    return project_creator_historical_outcomes([record])
