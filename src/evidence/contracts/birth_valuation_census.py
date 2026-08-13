"""EB0.1I bounded multi-source query-only extraction into EB0.1A-E corpora."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import time
from typing import Callable, Iterable, Mapping, Tuple
from urllib.parse import quote

from .birth_valuation_adapters import (
    adapt_launch_fact,
    adapt_market_observation,
    adapt_observed_migration,
    adapt_platform_receive,
)
from .birth_valuation_corpus import MintCorpus, assemble_birth_valuation_corpora


CENSUS_SCHEMA_VERSION = "eb0.1l.v1"
DEFAULT_MINT_LIMIT = 5_000
MAX_QUERY_SECONDS = 30.0
MAX_LAUNCH_FACTS_PER_MINT = 2
MAX_SELECTED_LAUNCH_FACTS = DEFAULT_MINT_LIMIT * MAX_LAUNCH_FACTS_PER_MINT


class BirthValuationCensusError(RuntimeError):
    """Named fail-closed error for an unsafe or malformed census source."""


@dataclass(frozen=True)
class CensusResult:
    schema_version: str
    high_water_migrated_at: int
    mint_limit: int
    selected_mints: Tuple[str, ...]
    eligible_mint_count: int
    excluded_by_cohort_bound_count: int
    corpora: Tuple[MintCorpus, ...]
    observation_count: int
    excluded_observation_count: int
    missing_event_kind_counts: Mapping[str, int]
    mints_without_canonical_evidence: Tuple[str, ...]
    ignored_explicit_record_count: int
    input_fingerprint: str
    result_digest: str


_PRIMARY_REQUIRED_COLUMNS = {
    "token_analysis": {
        "mint", "migrated_at", "first_observed_mc", "first_observed_price",
        "first_observed_at", "first_observed_source", "first_observed_confidence",
    },
    "token_price_snapshots": {
        "snapshot_id", "mint", "price_usd", "market_cap", "source", "captured_at",
        "created_at",
    },
}
_EVIDENCE_REQUIRED_COLUMNS = {
    "normalized_evidence_records": {
        "fact_family", "payload_json", "raw_artifact_digest", "acquired_at",
        "source_id", "source_version", "verification_state",
    },
}
_EVENT_KINDS = ("CHAIN_BIRTH", "PLATFORM_FIRST_SEEN", "MIGRATION", "MARKET_FIRST_OBSERVED")
_MAX_EXPLICIT_RECORDS = 10_000


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode("utf-8")).hexdigest()


def _decimal_text(value: object) -> str:
    normalised = format(Decimal(str(value)).normalize(), "f")
    return normalised.rstrip("0").rstrip(".") if "." in normalised else normalised


def _open_query_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise BirthValuationCensusError("EB0_1G_SOURCE_NOT_FOUND")
    uri = f"file:{quote(str(path.resolve()), safe='/')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=0.25)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    if connection.execute("PRAGMA query_only").fetchone()[0] != 1:
        connection.close()
        raise BirthValuationCensusError("EB0_1G_QUERY_ONLY_NOT_ENFORCED")
    return connection


def _validate_schema(
    connection: sqlite3.Connection,
    required_columns: Mapping[str, set[str]],
) -> None:
    tables = {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='table'"
        )
    }
    for table, required in required_columns.items():
        if table not in tables:
            raise BirthValuationCensusError(f"EB0_1G_MISSING_TABLE_{table.upper()}")
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        if not required <= columns:
            raise BirthValuationCensusError(f"EB0_1G_SCHEMA_MISMATCH_{table.upper()}")


def _timed(
    connection: sqlite3.Connection,
    sql: str,
    parameters: tuple[object, ...],
    *,
    clock: Callable[[], float],
    max_query_seconds: float,
) -> list[sqlite3.Row]:
    started = clock()
    rows = connection.execute(sql, parameters).fetchall()
    if clock() - started > max_query_seconds:
        raise BirthValuationCensusError("EB0_1G_QUERY_TIMEOUT")
    return rows


def _market_records(row: Mapping[str, object]) -> list[dict[str, object]]:
    captured = row["first_observed_at"]
    source = row["first_observed_source"]
    if captured is None:
        return []
    if isinstance(captured, bool) or not isinstance(captured, int) or captured < 0:
        raise BirthValuationCensusError("EB0_1G_INVALID_MARKET_TIME")
    if not isinstance(source, str) or not source.strip():
        raise BirthValuationCensusError("EB0_1G_INVALID_MARKET_SOURCE")
    records = []
    for column, kind in (("first_observed_mc", "MARKET_CAP"), ("first_observed_price", "PRICE")):
        value = row[column]
        if value is None:
            continue
        raw = {
            "mint": row["mint"],
            "captured_at": captured,
            "observed_at": captured,
            "value_kind": kind,
            "value": str(value),
            "source": source,
            "source_schema_version": "token-analysis-first-observed-v1",
            "source_record_digest": _digest({
                "mint": row["mint"], "column": column, "captured_at": captured,
                "value": str(value), "source": source,
                "confidence": row["first_observed_confidence"],
            }),
        }
        records.append(adapt_market_observation(raw))
    return records


def _bounded_records(
    records: Iterable[Mapping[str, object]],
) -> list[dict[str, object]]:
    material = []
    for record in records:
        if len(material) >= _MAX_EXPLICIT_RECORDS:
            raise BirthValuationCensusError("EB0_1I_EXPLICIT_RECORD_LIMIT")
        material.append(dict(record))
    return material


def extract_birth_valuation_census(
    primary_source_path: Path,
    *,
    evidence_source_path: Path | None = None,
    high_water_migrated_at: int,
    platform_receive_records: Iterable[Mapping[str, object]] = (),
    migration_receive_records: Iterable[Mapping[str, object]] = (),
    mint_limit: int = DEFAULT_MINT_LIMIT,
    max_query_seconds: float = MAX_QUERY_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> CensusResult:
    """Read independently injected sources and emit deterministic EB0.1E corpora."""

    if isinstance(high_water_migrated_at, bool) or not isinstance(high_water_migrated_at, int):
        raise BirthValuationCensusError("EB0_1G_INVALID_HIGH_WATER")
    if mint_limit != DEFAULT_MINT_LIMIT:
        raise BirthValuationCensusError("EB0_1G_MINT_LIMIT_MUST_BE_5000")
    if max_query_seconds <= 0 or max_query_seconds > MAX_QUERY_SECONDS:
        raise BirthValuationCensusError("EB0_1G_INVALID_QUERY_BOUND")

    platform_material = _bounded_records(platform_receive_records)
    migration_material = _bounded_records(migration_receive_records)
    if len(platform_material) + len(migration_material) > _MAX_EXPLICIT_RECORDS:
        raise BirthValuationCensusError("EB0_1I_EXPLICIT_RECORD_LIMIT")

    primary_path = Path(primary_source_path)
    evidence_path = Path(evidence_source_path) if evidence_source_path is not None else primary_path
    primary = _open_query_only(primary_path)
    evidence = None
    try:
        evidence = _open_query_only(evidence_path)
        _validate_schema(primary, _PRIMARY_REQUIRED_COLUMNS)
        _validate_schema(evidence, _EVIDENCE_REQUIRED_COLUMNS)
        eligible_rows = _timed(
            primary,
            "SELECT COUNT(DISTINCT mint) AS eligible_count FROM token_analysis "
            "WHERE migrated_at IS NOT NULL AND migrated_at<=?",
            (high_water_migrated_at,), clock=clock,
            max_query_seconds=max_query_seconds,
        )
        eligible_mint_count = int(eligible_rows[0]["eligible_count"])
        cohort = _timed(
            primary,
            "WITH ranked AS (SELECT mint,migrated_at,first_observed_mc,first_observed_price,"
            "first_observed_at,first_observed_source,first_observed_confidence,"
            "ROW_NUMBER() OVER (PARTITION BY mint ORDER BY migrated_at DESC,rowid DESC) AS rn "
            "FROM token_analysis WHERE migrated_at IS NOT NULL AND migrated_at<=?) "
            "SELECT mint,migrated_at,first_observed_mc,first_observed_price,first_observed_at,"
            "first_observed_source,first_observed_confidence FROM ranked WHERE rn=1 "
            "ORDER BY migrated_at DESC,mint ASC LIMIT ?",
            (high_water_migrated_at, mint_limit), clock=clock,
            max_query_seconds=max_query_seconds,
        )
        mints = tuple(str(row["mint"]).strip() for row in cohort)
        if not mints or any(not mint for mint in mints) or len(set(mints)) != len(mints):
            raise BirthValuationCensusError("EB0_1G_INVALID_COHORT")

        placeholders = ",".join("?" for _ in mints)
        observations: list[dict[str, object]] = []
        for row in cohort:
            observations.extend(_market_records(row))

        snapshot_rows = _timed(
            primary,
            f"SELECT snapshot_id,mint,price_usd,market_cap,source,captured_at,created_at "
            f"FROM token_price_snapshots WHERE mint IN ({placeholders}) AND captured_at<=? "
            "ORDER BY mint,captured_at,snapshot_id",
            (*mints, high_water_migrated_at), clock=clock,
            max_query_seconds=max_query_seconds,
        )
        for row in snapshot_rows:
            for column, kind in (("market_cap", "MARKET_CAP"), ("price_usd", "PRICE")):
                value = row[column]
                if value is None or float(value) <= 0:
                    continue
                observations.append(adapt_market_observation({
                    "mint": row["mint"], "captured_at": row["captured_at"],
                    "observed_at": row["created_at"], "value_kind": kind,
                    "value": _decimal_text(value), "source": row["source"],
                    "source_schema_version": "token-price-snapshot-v1",
                    "source_record_digest": _digest({
                        "snapshot_id": row["snapshot_id"], "mint": row["mint"],
                        "column": column, "captured_at": row["captured_at"],
                        "value": str(value), "source": row["source"],
                    }),
                }))

        mint_set = set(mints)
        receive_high_water = high_water_migrated_at * 1_000_000_000
        ignored_explicit = 0
        for record in platform_material:
            adapted = adapt_platform_receive(record)
            if adapted["mint"] not in mint_set or adapted["event_time_utc_ns"] > receive_high_water:
                ignored_explicit += 1
                continue
            observations.append(adapted)
        for record in migration_material:
            adapted = adapt_observed_migration(record)
            if adapted["mint"] not in mint_set or adapted["event_time_utc_ns"] > receive_high_water:
                ignored_explicit += 1
                continue
            observations.append(adapted)

        malformed_launch = _timed(
            evidence,
            "SELECT 1 FROM normalized_evidence_records WHERE fact_family='LaunchFact' "
            "AND NOT json_valid(payload_json) LIMIT 1",
            (), clock=clock, max_query_seconds=max_query_seconds,
        )
        if malformed_launch:
            raise BirthValuationCensusError("EB0_1G_INVALID_LAUNCH_PAYLOAD")
        launch_rows = _timed(
            evidence,
            "WITH selected(value) AS (SELECT value FROM json_each(?)), ranked AS ("
            "SELECT payload_json,raw_artifact_digest,acquired_at,source_id,source_version,"
            "verification_state,json_extract(payload_json,'$.mint') AS payload_mint,"
            "ROW_NUMBER() OVER (PARTITION BY json_extract(payload_json,'$.mint') "
            "ORDER BY raw_artifact_digest) AS mint_rank FROM normalized_evidence_records "
            "WHERE fact_family='LaunchFact' AND json_valid(payload_json) "
            "AND json_extract(payload_json,'$.mint') IN (SELECT value FROM selected)) "
            "SELECT * FROM ranked WHERE mint_rank<=? ORDER BY payload_mint,raw_artifact_digest "
            "LIMIT ?",
            (json.dumps(mints), MAX_LAUNCH_FACTS_PER_MINT + 1,
             MAX_SELECTED_LAUNCH_FACTS + 1),
            clock=clock, max_query_seconds=max_query_seconds,
        )
        if (len(launch_rows) > MAX_SELECTED_LAUNCH_FACTS
                or any(row["mint_rank"] > MAX_LAUNCH_FACTS_PER_MINT for row in launch_rows)):
            raise BirthValuationCensusError("EB0_1L_LAUNCH_FACT_OVERFLOW")
        for row in launch_rows:
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise BirthValuationCensusError("EB0_1G_INVALID_LAUNCH_PAYLOAD") from exc
            if not isinstance(payload, dict) or payload.get("mint") not in mint_set:
                raise BirthValuationCensusError("EB0_1L_LAUNCH_FACT_PREDICATE_MISMATCH")
            source_row = {key: row[key] for key in (
                "payload_json", "raw_artifact_digest", "acquired_at", "source_id",
                "source_version", "verification_state")}
            observations.append(adapt_launch_fact({**source_row, "fact_family": "LaunchFact", "payload": payload}))

        observed_pairs = {(str(item["mint"]), str(item["event_kind"])) for item in observations}
        missing_counts = {
            kind: sum((mint, kind) not in observed_pairs for mint in mints)
            for kind in _EVENT_KINDS
        }
        covered = {mint for mint, _ in observed_pairs}
        mints_without_evidence = tuple(mint for mint in mints if mint not in covered)
        corpora = assemble_birth_valuation_corpora(observations) if observations else ()
        fingerprint = _digest({
            "primary_path_name": primary_path.name,
            "evidence_path_name": evidence_path.name,
            "high_water": high_water_migrated_at,
            "mint_limit": mint_limit,
            "mints": mints,
            "platform_receive_digest": _digest(platform_material),
            "migration_receive_digest": _digest(migration_material),
        })
        body = {
            "schema_version": CENSUS_SCHEMA_VERSION,
            "high_water_migrated_at": high_water_migrated_at,
            "mint_limit": mint_limit,
            "selected_mints": mints,
            "eligible_mint_count": eligible_mint_count,
            "excluded_by_cohort_bound_count": eligible_mint_count - len(mints),
            "corpus_digests": [item.corpus_digest for item in corpora],
            "missing_event_kind_counts": missing_counts,
            "mints_without_canonical_evidence": mints_without_evidence,
            "ignored_explicit_record_count": ignored_explicit,
            "input_fingerprint": fingerprint,
        }
        return CensusResult(
            schema_version=CENSUS_SCHEMA_VERSION,
            high_water_migrated_at=high_water_migrated_at,
            mint_limit=mint_limit,
            selected_mints=mints,
            eligible_mint_count=eligible_mint_count,
            excluded_by_cohort_bound_count=eligible_mint_count - len(mints),
            corpora=corpora,
            observation_count=len(observations),
            excluded_observation_count=sum(len(item.excluded) for item in corpora),
            missing_event_kind_counts=missing_counts,
            mints_without_canonical_evidence=mints_without_evidence,
            ignored_explicit_record_count=ignored_explicit,
            input_fingerprint=fingerprint,
            result_digest=_digest(body),
        )
    except sqlite3.Error as exc:
        raise BirthValuationCensusError("EB0_1G_SQLITE_READ_FAILED") from exc
    finally:
        if evidence is not None:
            evidence.close()
        primary.close()
