"""EB0.2J bounded normalization of split frozen SQLite source replicas."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import time
from typing import Callable, Mapping, Tuple
from urllib.parse import quote


NORMALIZER_VERSION = "eb0.2j.v1"
MAX_COHORT_MINTS = 5_000
MAX_QUERY_SECONDS = 30.0

_SOURCE_SCHEMA = {
    "main": {"token_analysis": {
        "mint", "pf_ws_creator", "creator_mismatch", "first_observed_at",
        "first_observed_mc", "first_observed_price", "first_observed_source",
    }},
    "ops": {"wt_watchtower_launches": {
        "mint", "creator_wallet", "create_signature", "create_time", "create_slot",
        "creator_extraction_method", "confidence", "recorded_at",
    }},
    "creator": {"creator_tokens": {"creator_address", "mint", "created_at"}},
}


class CreatorOutcomeNormalizationError(RuntimeError):
    """Named fail-closed error for unsafe EB0.2J normalization."""


@dataclass(frozen=True)
class SourceHighWaters:
    main_token_analysis_rowid: int
    ops_watchtower_launches_rowid: int
    creator_tokens_rowid: int
    observed_through_utc_ns: int


@dataclass(frozen=True)
class NormalizedCreatorOutcomeInputs:
    cohort_mints: Tuple[Tuple[int, str], ...]
    canonical_observations: Tuple[Mapping[str, object], ...]
    creator_identity_facts: Tuple[Mapping[str, object], ...]
    observation_window_facts: Tuple[Mapping[str, object], ...]
    excluded_mints: Mapping[str, str]
    source_fingerprint: str


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _decimal_text(value: object) -> str:
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise CreatorOutcomeNormalizationError("EB0_2J_INVALID_MARKET_VALUE") from exc
    if not decimal.is_finite() or decimal <= 0:
        raise CreatorOutcomeNormalizationError("EB0_2J_INVALID_MARKET_VALUE")
    normalized = format(decimal.normalize(), "f")
    return normalized.rstrip("0").rstrip(".") if "." in normalized else normalized


def _open(path: Path) -> sqlite3.Connection:
    if not Path(path).is_file():
        raise CreatorOutcomeNormalizationError("EB0_2J_SOURCE_NOT_FOUND")
    uri = f"file:{quote(str(Path(path).resolve()), safe='/')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=0.25)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    if connection.execute("PRAGMA query_only").fetchone()[0] != 1:
        connection.close()
        raise CreatorOutcomeNormalizationError("EB0_2J_QUERY_ONLY_NOT_ENFORCED")
    return connection


def _schema(connection: sqlite3.Connection, source: str) -> None:
    tables = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )}
    for table, required in _SOURCE_SCHEMA[source].items():
        if table not in tables:
            raise CreatorOutcomeNormalizationError(f"EB0_2J_MISSING_TABLE_{source.upper()}_{table.upper()}")
        columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
        if not required <= columns:
            raise CreatorOutcomeNormalizationError(f"EB0_2J_SCHEMA_MISMATCH_{source.upper()}_{table.upper()}")


def _query(connection: sqlite3.Connection, sql: str, params: tuple[object, ...], *, clock: Callable[[], float], seconds: float) -> list[sqlite3.Row]:
    deadline = clock() + seconds
    reached = False
    def stop() -> int:
        nonlocal reached
        reached = clock() >= deadline
        return int(reached)
    connection.set_progress_handler(stop, 1_000)
    try:
        rows = connection.execute(sql, params).fetchall()
        if clock() >= deadline:
            raise CreatorOutcomeNormalizationError("EB0_2J_QUERY_TIMEOUT")
        return rows
    except sqlite3.OperationalError as exc:
        if reached and "interrupted" in str(exc).lower():
            raise CreatorOutcomeNormalizationError("EB0_2J_QUERY_TIMEOUT") from exc
        raise CreatorOutcomeNormalizationError("EB0_2J_SQLITE_READ_FAILED") from exc
    finally:
        connection.set_progress_handler(None, 0)


def _positive_integer(value: object, error: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CreatorOutcomeNormalizationError(error)
    return value


def normalize_creator_outcome_sources(
    main_path: Path,
    ops_path: Path,
    creator_path: Path,
    *,
    cohort_mints: Tuple[str, ...],
    high_waters: SourceHighWaters,
    max_query_seconds: float = MAX_QUERY_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> NormalizedCreatorOutcomeInputs:
    """Normalize only selected frozen rows; never infer complete negative coverage."""

    if not cohort_mints or len(cohort_mints) > MAX_COHORT_MINTS or len(set(cohort_mints)) != len(cohort_mints) or any(not isinstance(mint, str) or not mint.strip() for mint in cohort_mints):
        raise CreatorOutcomeNormalizationError("EB0_2J_INVALID_COHORT")
    for value in (
        high_waters.main_token_analysis_rowid,
        high_waters.ops_watchtower_launches_rowid,
        high_waters.creator_tokens_rowid,
        high_waters.observed_through_utc_ns,
    ):
        _positive_integer(value, "EB0_2J_INVALID_HIGH_WATER")
    if max_query_seconds <= 0 or max_query_seconds > MAX_QUERY_SECONDS:
        raise CreatorOutcomeNormalizationError("EB0_2J_INVALID_QUERY_BOUND")

    connections = {"main": _open(main_path), "ops": _open(ops_path), "creator": _open(creator_path)}
    try:
        for source, connection in connections.items():
            _schema(connection, source)
        marks = ",".join("?" for _ in cohort_mints)
        main = _query(connections["main"], f"SELECT rowid,mint,pf_ws_creator,creator_mismatch,first_observed_at,first_observed_mc,first_observed_price,first_observed_source FROM token_analysis WHERE rowid<=? AND mint IN ({marks}) ORDER BY mint,rowid", (high_waters.main_token_analysis_rowid, *cohort_mints), clock=clock, seconds=max_query_seconds)
        ops = _query(connections["ops"], f"SELECT rowid,mint,creator_wallet,create_signature,create_time,create_slot,creator_extraction_method,confidence,recorded_at FROM wt_watchtower_launches WHERE rowid<=? AND mint IN ({marks}) ORDER BY mint,rowid", (high_waters.ops_watchtower_launches_rowid, *cohort_mints), clock=clock, seconds=max_query_seconds)
        creators = _query(connections["creator"], f"SELECT rowid,creator_address,mint,created_at FROM creator_tokens WHERE rowid<=? AND mint IN ({marks}) ORDER BY mint,rowid", (high_waters.creator_tokens_rowid, *cohort_mints), clock=clock, seconds=max_query_seconds)
    finally:
        for connection in connections.values():
            connection.close()

    by_main = {str(row["mint"]): row for row in main}
    by_ops: dict[str, list[sqlite3.Row]] = {}
    by_creator: dict[str, set[str]] = {}
    for row in ops: by_ops.setdefault(str(row["mint"]), []).append(row)
    for row in creators: by_creator.setdefault(str(row["mint"]), set()).add(str(row["creator_address"]))

    observations: list[dict[str, object]] = []
    identities: list[dict[str, object]] = []
    windows: list[dict[str, object]] = []
    excluded: dict[str, str] = {}
    source_rows = {"main": [dict(row) for row in main], "ops": [dict(row) for row in ops], "creator": [dict(row) for row in creators]}
    for mint in cohort_mints:
        row = by_main.get(mint)
        verified_launches = [
            launch for launch in by_ops.get(mint, [])
            if launch["confidence"] == "VERIFIED"
            and launch["create_signature"] and launch["create_slot"] is not None
            and launch["create_time"] is not None
            and launch["recorded_at"] is not None
            and int(launch["recorded_at"]) >= int(launch["create_time"])
        ]
        if len(verified_launches) == 1:
            launch = verified_launches[0]
            launch_source = {"source": "ops.wt_watchtower_launches", "high_water": high_waters.ops_watchtower_launches_rowid, "row": dict(launch)}
            observations.append({
                "mint": mint, "event_kind": "CHAIN_BIRTH",
                "event_time_utc_ns": int(launch["create_time"]) * 1_000_000_000,
                "source": "ops.wt_watchtower_launches:verified_create_proof",
                "source_version": NORMALIZER_VERSION,
                "observed_at_utc_ns": int(launch["recorded_at"]) * 1_000_000_000,
                "price_or_market_cap_value": None, "valuation_semantics": "UNKNOWN",
                "quality_state": "VERIFIED", "completeness_state": "NOT_OBSERVED",
                "source_record_digest": _digest(launch_source),
            })
        if row is not None and row["first_observed_at"] is not None:
            value_kind = "MARKET_CAP" if row["first_observed_mc"] is not None else "PRICE" if row["first_observed_price"] is not None else None
            if value_kind:
                value = row["first_observed_mc"] if value_kind == "MARKET_CAP" else row["first_observed_price"]
                source_record = {"source": "main.token_analysis", "high_water": high_waters.main_token_analysis_rowid, "row": dict(row)}
                observations.append({
                    "mint": mint, "event_kind": "MARKET_FIRST_OBSERVED",
                    "event_time_utc_ns": int(float(row["first_observed_at"]) * 1_000_000_000),
                    "source": f"main.token_analysis:{row['first_observed_source'] or 'unknown'}",
                    "source_version": NORMALIZER_VERSION,
                    "observed_at_utc_ns": int(float(row["first_observed_at"]) * 1_000_000_000),
                    "price_or_market_cap_value": _decimal_text(value),
                    "valuation_semantics": "MARKET_CAP_AT_EVENT" if value_kind == "MARKET_CAP" else "PRICE_AT_EVENT",
                    "quality_state": "OBSERVED", "completeness_state": "COMPLETE",
                    "source_record_digest": _digest(source_record),
                })

        candidates = []
        if row is not None and row["pf_ws_creator"] and row["creator_mismatch"] in (None, 0):
            for launch in by_ops.get(mint, []):
                if launch["creator_wallet"] == row["pf_ws_creator"] and launch["creator_extraction_method"] == "PF_WS_CREATOR_VERIFIED" and launch["confidence"] == "VERIFIED":
                    if not by_creator.get(mint) or by_creator[mint] == {row["pf_ws_creator"]}:
                        candidates.append((row, launch))
        if len(candidates) == 1:
            identity_source = {"main": dict(candidates[0][0]), "ops": dict(candidates[0][1]), "creator_membership": sorted(by_creator.get(mint, set())), "high_waters": high_waters.__dict__}
            identities.append({"mint": mint, "creator": str(row["pf_ws_creator"]), "resolution_method": "PF_WS_CREATOR_VERIFIED", "source": "main.token_analysis+ops.wt_watchtower_launches", "source_version": NORMALIZER_VERSION, "source_record_digest": _digest(identity_source)})
        else:
            excluded[mint] = "MISSING_AMBIGUOUS_OR_MISMATCHED_CREATOR_IDENTITY"

        windows.append({"mint": mint, "observed_through_utc_ns": high_waters.observed_through_utc_ns, "full_horizon_complete": 0, "source": "caller_bound_split_source_high_waters", "source_version": NORMALIZER_VERSION, "source_record_digest": _digest({"mint": mint, "high_waters": high_waters.__dict__})})

    body = {"version": NORMALIZER_VERSION, "cohort": cohort_mints, "high_waters": high_waters.__dict__, "source_rows": source_rows, "observations": observations, "identities": identities, "windows": windows, "excluded": excluded}
    return NormalizedCreatorOutcomeInputs(tuple(enumerate(cohort_mints)), tuple(observations), tuple(identities), tuple(windows), dict(sorted(excluded.items())), _digest(body))


def materialize_normalized_fixture(inputs: NormalizedCreatorOutcomeInputs, output_path: Path) -> None:
    """Materialize the exact EB0.2G schema only at a caller-supplied new path."""
    path = Path(output_path)
    if path.exists():
        raise CreatorOutcomeNormalizationError("EB0_2J_OUTPUT_EXISTS")
    connection = sqlite3.connect(path)
    try:
        connection.executescript("""
          CREATE TABLE cohort_mints(position INTEGER,mint TEXT);
          CREATE TABLE eb0_1_canonical_observations(mint TEXT,event_kind TEXT,event_time_utc_ns INTEGER,source TEXT,source_version TEXT,observed_at_utc_ns INTEGER,price_or_market_cap_value TEXT,valuation_semantics TEXT,quality_state TEXT,completeness_state TEXT,source_record_digest TEXT);
          CREATE TABLE creator_identity_facts(mint TEXT,creator TEXT,resolution_method TEXT,source TEXT,source_version TEXT,source_record_digest TEXT);
          CREATE TABLE observation_window_facts(mint TEXT,observed_through_utc_ns INTEGER,full_horizon_complete INTEGER,source TEXT,source_version TEXT,source_record_digest TEXT);
        """)
        connection.executemany("INSERT INTO cohort_mints VALUES (?,?)", inputs.cohort_mints)
        connection.executemany("INSERT INTO eb0_1_canonical_observations VALUES (?,?,?,?,?,?,?,?,?,?,?)", [tuple(row.values()) for row in inputs.canonical_observations])
        connection.executemany("INSERT INTO creator_identity_facts VALUES (?,?,?,?,?,?)", [tuple(row.values()) for row in inputs.creator_identity_facts])
        connection.executemany("INSERT INTO observation_window_facts VALUES (?,?,?,?,?,?)", [tuple(row.values()) for row in inputs.observation_window_facts])
        connection.commit()
    except Exception:
        connection.close()
        path.unlink(missing_ok=True)
        raise
    finally:
        connection.close()
