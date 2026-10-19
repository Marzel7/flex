"""EB0.2G bounded query-only extraction from normalized frozen evidence."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import time
from typing import Callable, Mapping, Optional, Tuple
from urllib.parse import quote

from .birth_valuation_corpus import assemble_birth_valuation_corpora
from .creator_historical_outcome_adapters import (
    adapt_creator_outcome,
    build_creator_identity_fact,
    build_observation_window_fact,
)
from .creator_historical_outcome_corpus import (
    CreatorHistoricalOutcomeCorpus,
    assemble_creator_historical_outcome_corpora,
)
from .creator_historical_outcome_manifest import (
    CreatorHistoricalOutcomeManifest,
    build_creator_historical_outcome_manifest,
)


EXTRACTOR_SCHEMA_VERSION = "eb0.2g.v1"
MAX_COHORT_MINTS = 5_000
MAX_POLICIES = 32
MAX_QUERY_SECONDS = 30.0


_SCHEMA = {
    "cohort_mints": {"position", "mint"},
    "eb0_1_canonical_observations": {
        "mint", "event_kind", "event_time_utc_ns", "source", "source_version",
        "observed_at_utc_ns", "price_or_market_cap_value", "valuation_semantics",
        "quality_state", "completeness_state", "source_record_digest",
    },
    "creator_identity_facts": {
        "mint", "creator", "resolution_method", "source", "source_version",
        "source_record_digest",
    },
    "observation_window_facts": {
        "mint", "observed_through_utc_ns", "full_horizon_complete", "source",
        "source_version", "source_record_digest",
    },
}


class CreatorHistoricalOutcomeExtractorError(RuntimeError):
    """Named fail-closed error for unsafe or malformed EB0.2G sources."""


@dataclass(frozen=True)
class OutcomePolicy:
    cohort_event_kind: str
    outcome_kind: str
    horizon_utc_ns: int
    threshold_value: Optional[str] = None


@dataclass(frozen=True)
class CreatorHistoricalOutcomeExtraction:
    schema_version: str
    selected_mints: Tuple[str, ...]
    qualified_mints: Tuple[str, ...]
    excluded_mints: Mapping[str, str]
    policy_count: int
    fact_count: int
    eligible_denominator_count: int
    unknown_count: int
    conflicting_fact_count: int
    manifests: Tuple[CreatorHistoricalOutcomeManifest, ...]
    corpora: Tuple[CreatorHistoricalOutcomeCorpus, ...]
    input_fingerprint: str
    result_digest: str


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode("utf-8")).hexdigest()


def _open_query_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise CreatorHistoricalOutcomeExtractorError("EB0_2G_SOURCE_NOT_FOUND")
    uri = f"file:{quote(str(path.resolve()), safe='/')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=0.25)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    if connection.execute("PRAGMA query_only").fetchone()[0] != 1:
        connection.close()
        raise CreatorHistoricalOutcomeExtractorError("EB0_2G_QUERY_ONLY_NOT_ENFORCED")
    return connection


def _validate_schema(connection: sqlite3.Connection) -> None:
    tables = {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if tables != set(_SCHEMA):
        raise CreatorHistoricalOutcomeExtractorError("EB0_2G_SCHEMA_TABLE_MISMATCH")
    for table, expected in _SCHEMA.items():
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        if columns != expected:
            raise CreatorHistoricalOutcomeExtractorError(
                f"EB0_2G_SCHEMA_COLUMN_MISMATCH_{table.upper()}"
            )


def _timed(
    connection: sqlite3.Connection,
    sql: str,
    parameters: tuple[object, ...],
    *,
    clock: Callable[[], float],
    max_query_seconds: float,
) -> list[sqlite3.Row]:
    deadline = clock() + max_query_seconds
    deadline_reached = False

    def interrupt() -> int:
        nonlocal deadline_reached
        if clock() >= deadline:
            deadline_reached = True
            return 1
        return 0

    connection.set_progress_handler(interrupt, 1_000)
    try:
        rows = connection.execute(sql, parameters).fetchall()
        if clock() >= deadline:
            raise CreatorHistoricalOutcomeExtractorError("EB0_2G_QUERY_TIMEOUT")
        return rows
    except sqlite3.OperationalError as exc:
        if deadline_reached and "interrupted" in str(exc).lower():
            raise CreatorHistoricalOutcomeExtractorError("EB0_2G_QUERY_TIMEOUT") from exc
        raise
    finally:
        connection.set_progress_handler(None, 0)


def _validate_policies(policies: Tuple[OutcomePolicy, ...]) -> None:
    if not policies or len(policies) > MAX_POLICIES:
        raise CreatorHistoricalOutcomeExtractorError("EB0_2G_INVALID_POLICY_COUNT")
    encoded = []
    for policy in policies:
        if (
            isinstance(policy.horizon_utc_ns, bool)
            or not isinstance(policy.horizon_utc_ns, int)
            or policy.horizon_utc_ns <= 0
        ):
            raise CreatorHistoricalOutcomeExtractorError("EB0_2G_INVALID_POLICY_HORIZON")
        encoded.append(json.dumps(policy.__dict__, sort_keys=True))
    if len(set(encoded)) != len(encoded):
        raise CreatorHistoricalOutcomeExtractorError("EB0_2G_DUPLICATE_POLICY")


def extract_creator_historical_outcomes(
    source_path: Path,
    *,
    policies: Tuple[OutcomePolicy, ...],
    max_query_seconds: float = MAX_QUERY_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> CreatorHistoricalOutcomeExtraction:
    """Read one injected normalized source and emit deterministic EB0.2C-E outputs."""

    _validate_policies(policies)
    if max_query_seconds <= 0 or max_query_seconds > MAX_QUERY_SECONDS:
        raise CreatorHistoricalOutcomeExtractorError("EB0_2G_INVALID_QUERY_BOUND")
    path = Path(source_path)
    connection = _open_query_only(path)
    try:
        _validate_schema(connection)
        cohort_rows = _timed(
            connection,
            "SELECT position,mint FROM cohort_mints ORDER BY position,mint LIMIT ?",
            (MAX_COHORT_MINTS + 1,), clock=clock, max_query_seconds=max_query_seconds,
        )
        if not cohort_rows or len(cohort_rows) > MAX_COHORT_MINTS:
            raise CreatorHistoricalOutcomeExtractorError("EB0_2G_INVALID_COHORT_SIZE")
        positions = [row["position"] for row in cohort_rows]
        mints = tuple(str(row["mint"] or "").strip() for row in cohort_rows)
        if (
            any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in positions)
            or positions != list(range(len(positions)))
            or any(not mint for mint in mints)
            or len(set(mints)) != len(mints)
        ):
            raise CreatorHistoricalOutcomeExtractorError("EB0_2G_INVALID_COHORT")

        placeholders = ",".join("?" for _ in mints)
        observation_rows = _timed(
            connection,
            f"SELECT * FROM eb0_1_canonical_observations WHERE mint IN ({placeholders}) "
            "ORDER BY mint,event_kind,event_time_utc_ns,source_record_digest",
            mints, clock=clock, max_query_seconds=max_query_seconds,
        )
        identity_rows = _timed(
            connection,
            f"SELECT * FROM creator_identity_facts WHERE mint IN ({placeholders}) "
            "ORDER BY mint,source_record_digest",
            mints, clock=clock, max_query_seconds=max_query_seconds,
        )
        window_rows = _timed(
            connection,
            f"SELECT * FROM observation_window_facts WHERE mint IN ({placeholders}) "
            "ORDER BY mint,source_record_digest",
            mints, clock=clock, max_query_seconds=max_query_seconds,
        )

        observations: dict[str, list[dict[str, object]]] = {mint: [] for mint in mints}
        identities: dict[str, list[sqlite3.Row]] = {mint: [] for mint in mints}
        windows: dict[str, list[sqlite3.Row]] = {mint: [] for mint in mints}
        for row in observation_rows:
            observations[str(row["mint"])].append(dict(row))
        for row in identity_rows:
            identities[str(row["mint"])].append(row)
        for row in window_rows:
            windows[str(row["mint"])].append(row)

        excluded: dict[str, str] = {}
        manifests = []
        qualified = []
        for mint in mints:
            if not observations[mint]:
                excluded[mint] = "NO_CANONICAL_CORPUS_EVIDENCE"
                continue
            if len(identities[mint]) != 1:
                excluded[mint] = "MISSING_OR_AMBIGUOUS_CREATOR_IDENTITY"
                continue
            if len(windows[mint]) != 1:
                excluded[mint] = "MISSING_OR_AMBIGUOUS_OBSERVATION_WINDOW"
                continue
            try:
                corpus = assemble_birth_valuation_corpora(observations[mint])[0]
                identity_row = identities[mint][0]
                identity = build_creator_identity_fact(**dict(identity_row))
                window_row = dict(windows[mint][0])
                raw_complete = window_row.pop("full_horizon_complete")
                if raw_complete not in (0, 1):
                    raise ValueError("invalid completeness")
                window = build_observation_window_fact(
                    **window_row, full_horizon_complete=bool(raw_complete)
                )
                facts = []
                for policy in policies:
                    facts.extend(adapt_creator_outcome(
                        corpus=corpus,
                        creator_identity=identity,
                        observation_window=window,
                        cohort_event_kind=policy.cohort_event_kind,
                        outcome_kind=policy.outcome_kind,
                        horizon_utc_ns=policy.horizon_utc_ns,
                        threshold_value=policy.threshold_value,
                    ))
                manifests.append(build_creator_historical_outcome_manifest(facts))
                qualified.append(mint)
            except Exception as exc:
                excluded[mint] = f"UNQUALIFIED_INPUT:{type(exc).__name__}:{exc}"

        manifest_tuple = tuple(sorted(manifests, key=lambda item: item.manifest_digest))
        corpora = (
            assemble_creator_historical_outcome_corpora(manifest_tuple)
            if manifest_tuple else ()
        )
        all_facts = [fact for manifest in manifest_tuple for fact in manifest.facts]
        policy_payload = [policy.__dict__ for policy in policies]
        fingerprint = _digest({
            "source_name": path.name,
            "selected_mints": mints,
            "policies": policy_payload,
            "observation_rows": [dict(row) for row in observation_rows],
            "identity_rows": [dict(row) for row in identity_rows],
            "window_rows": [dict(row) for row in window_rows],
        })
        body = {
            "schema_version": EXTRACTOR_SCHEMA_VERSION,
            "selected_mints": mints,
            "qualified_mints": tuple(qualified),
            "excluded_mints": dict(sorted(excluded.items())),
            "policies": policy_payload,
            "manifest_digests": [item.manifest_digest for item in manifest_tuple],
            "corpus_digests": [item.corpus_digest for item in corpora],
            "input_fingerprint": fingerprint,
        }
        return CreatorHistoricalOutcomeExtraction(
            schema_version=EXTRACTOR_SCHEMA_VERSION,
            selected_mints=mints,
            qualified_mints=tuple(qualified),
            excluded_mints=dict(sorted(excluded.items())),
            policy_count=len(policies),
            fact_count=len(all_facts),
            eligible_denominator_count=sum(item.denominator_eligible for item in all_facts),
            unknown_count=sum(item.outcome_state == "UNKNOWN" for item in all_facts),
            conflicting_fact_count=sum(item.quality_state == "CONFLICTING" for item in all_facts),
            manifests=manifest_tuple,
            corpora=corpora,
            input_fingerprint=fingerprint,
            result_digest=_digest(body),
        )
    except sqlite3.Error as exc:
        raise CreatorHistoricalOutcomeExtractorError("EB0_2G_SQLITE_READ_FAILED") from exc
    finally:
        connection.close()
