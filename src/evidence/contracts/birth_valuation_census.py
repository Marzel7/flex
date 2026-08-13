"""EB0.1G bounded query-only SQLite extraction into EB0.1A-E corpora."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import time
from typing import Callable, Mapping, Tuple
from urllib.parse import quote

from .birth_valuation_adapters import (
    adapt_launch_fact,
    adapt_market_observation,
    adapt_observed_migration,
    adapt_platform_receive,
)
from .birth_valuation_corpus import MintCorpus, assemble_birth_valuation_corpora


CENSUS_SCHEMA_VERSION = "eb0.1g.v1"
DEFAULT_MINT_LIMIT = 5_000
MAX_QUERY_SECONDS = 30.0


class BirthValuationCensusError(RuntimeError):
    """Named fail-closed error for an unsafe or malformed census source."""


@dataclass(frozen=True)
class CensusResult:
    schema_version: str
    high_water_migrated_at: int
    mint_limit: int
    selected_mints: Tuple[str, ...]
    corpora: Tuple[MintCorpus, ...]
    observation_count: int
    excluded_observation_count: int
    input_fingerprint: str
    result_digest: str


_REQUIRED_COLUMNS = {
    "token_analysis": {
        "mint", "migrated_at", "first_observed_mc", "first_observed_price",
        "first_observed_at", "first_observed_source", "first_observed_confidence",
    },
    "normalized_evidence_records": {
        "fact_family", "payload_json", "raw_artifact_digest", "acquired_at",
        "source_id", "source_version", "verification_state",
    },
    "token_price_snapshots": {
        "snapshot_id", "mint", "price_usd", "market_cap", "source", "captured_at",
        "created_at",
    },
    "eb0_platform_receive_evidence": {
        "mint", "receive_utc_ns", "source", "source_schema_version",
        "source_record_digest",
    },
    "eb0_migration_receive_evidence": {
        "mint", "receive_utc_ns", "signature", "source", "source_schema_version",
        "source_record_digest",
    },
}


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


def _validate_schema(connection: sqlite3.Connection) -> None:
    tables = {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='table'"
        )
    }
    for table, required in _REQUIRED_COLUMNS.items():
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


def extract_birth_valuation_census(
    source_path: Path,
    *,
    high_water_migrated_at: int,
    mint_limit: int = DEFAULT_MINT_LIMIT,
    max_query_seconds: float = MAX_QUERY_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> CensusResult:
    """Read one immutable high-water cohort and emit deterministic EB0.1E corpora."""

    if isinstance(high_water_migrated_at, bool) or not isinstance(high_water_migrated_at, int):
        raise BirthValuationCensusError("EB0_1G_INVALID_HIGH_WATER")
    if mint_limit != DEFAULT_MINT_LIMIT:
        raise BirthValuationCensusError("EB0_1G_MINT_LIMIT_MUST_BE_5000")
    if max_query_seconds <= 0 or max_query_seconds > MAX_QUERY_SECONDS:
        raise BirthValuationCensusError("EB0_1G_INVALID_QUERY_BOUND")

    connection = _open_query_only(Path(source_path))
    try:
        _validate_schema(connection)
        cohort = _timed(
            connection,
            "SELECT mint,migrated_at,first_observed_mc,first_observed_price,"
            "first_observed_at,first_observed_source,first_observed_confidence "
            "FROM token_analysis WHERE migrated_at IS NOT NULL AND migrated_at<=? "
            "ORDER BY migrated_at DESC,mint ASC LIMIT ?",
            (high_water_migrated_at, mint_limit + 1), clock=clock,
            max_query_seconds=max_query_seconds,
        )
        if len(cohort) > mint_limit:
            raise BirthValuationCensusError("EB0_1G_COHORT_EXCEEDS_5000")
        mints = tuple(str(row["mint"]).strip() for row in cohort)
        if not mints or any(not mint for mint in mints) or len(set(mints)) != len(mints):
            raise BirthValuationCensusError("EB0_1G_INVALID_COHORT")

        placeholders = ",".join("?" for _ in mints)
        observations: list[dict[str, object]] = []
        for row in cohort:
            observations.extend(_market_records(row))

        snapshot_rows = _timed(
            connection,
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

        platform_rows = _timed(
            connection,
            f"SELECT * FROM eb0_platform_receive_evidence WHERE mint IN ({placeholders}) "
            "AND receive_utc_ns<=? ORDER BY mint,receive_utc_ns,source_record_digest",
            (*mints, high_water_migrated_at * 1_000_000_000), clock=clock,
            max_query_seconds=max_query_seconds,
        )
        observations.extend(adapt_platform_receive(dict(row)) for row in platform_rows)

        migration_rows = _timed(
            connection,
            f"SELECT * FROM eb0_migration_receive_evidence WHERE mint IN ({placeholders}) "
            "AND receive_utc_ns<=? ORDER BY mint,receive_utc_ns,source_record_digest",
            (*mints, high_water_migrated_at * 1_000_000_000), clock=clock,
            max_query_seconds=max_query_seconds,
        )
        observations.extend(adapt_observed_migration(dict(row)) for row in migration_rows)

        launch_rows = _timed(
            connection,
            "SELECT payload_json,raw_artifact_digest,acquired_at,source_id,source_version,"
            "verification_state FROM normalized_evidence_records "
            "WHERE fact_family='LaunchFact' ORDER BY raw_artifact_digest",
            (), clock=clock, max_query_seconds=max_query_seconds,
        )
        mint_set = set(mints)
        for row in launch_rows:
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise BirthValuationCensusError("EB0_1G_INVALID_LAUNCH_PAYLOAD") from exc
            if isinstance(payload, dict) and payload.get("mint") in mint_set:
                observations.append(adapt_launch_fact({**dict(row), "fact_family": "LaunchFact", "payload": payload}))

        covered = {str(item["mint"]) for item in observations}
        for mint in mints:
            if mint not in covered:
                raise BirthValuationCensusError("EB0_1G_MINT_WITHOUT_CANONICAL_EVIDENCE")
        corpora = assemble_birth_valuation_corpora(observations)
        fingerprint = _digest({"path_name": Path(source_path).name, "high_water": high_water_migrated_at,
                               "mint_limit": mint_limit, "mints": mints})
        body = {
            "schema_version": CENSUS_SCHEMA_VERSION,
            "high_water_migrated_at": high_water_migrated_at,
            "mint_limit": mint_limit,
            "selected_mints": mints,
            "corpus_digests": [item.corpus_digest for item in corpora],
            "input_fingerprint": fingerprint,
        }
        return CensusResult(
            schema_version=CENSUS_SCHEMA_VERSION,
            high_water_migrated_at=high_water_migrated_at,
            mint_limit=mint_limit,
            selected_mints=mints,
            corpora=corpora,
            observation_count=len(observations),
            excluded_observation_count=sum(len(item.excluded) for item in corpora),
            input_fingerprint=fingerprint,
            result_digest=_digest(body),
        )
    except sqlite3.Error as exc:
        raise BirthValuationCensusError("EB0_1G_SQLITE_READ_FAILED") from exc
    finally:
        connection.close()
