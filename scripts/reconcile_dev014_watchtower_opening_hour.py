#!/usr/bin/env python3
"""Produce a compact, provider-free DEV-014 opening-hour research dataset."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from statistics import median
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ops.watchtower_historical_execution_binding import HistoricalExecutionBinding


AUDITS = ROOT / "docs" / "audits"
CHRONOLOGY = AUDITS / "dev014_watchtower_full_chronology_projection_20261010.v1.json"
POPULATION = AUDITS / "dev014_watchtower_forensic_population_v2_20261009.v1.json"
COHORT_AUTHORIZATION = AUDITS / "dev014_creation_time_anchored_cohort_authorization_20261010.v1.json"
OUTPUT = AUDITS / "dev014_watchtower_opening_hour_reconciliation_20261011.v1.json"
HISTORICAL_JOURNALS = (
    Path("/private/tmp/dev014-evidence/journal.json"),
    Path("/private/tmp/dev014-r51-70-evidence/journal.json"),
    Path("/private/tmp/dev014-r71-90-evidence/journal.json"),
    Path("/private/tmp/dev014-final-four-evidence/journal.json"),
)
COHORT_JOURNAL = Path("/private/tmp/dev014-creation-time-cohort-20261010/evidence/journal.json")
MAX_ARTIFACT_BYTES = 1_000_000
WINDOWS = (300, 900, 1800, 3600)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _quantiles(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "p25": None, "median": None, "p75": None, "max": None}
    ordered = sorted(values)
    def pick(fraction: float) -> float:
        return ordered[round((len(ordered) - 1) * fraction)]
    return {"count": len(ordered), "min": ordered[0], "p25": pick(.25),
            "median": median(ordered), "p75": pick(.75), "max": ordered[-1]}


def _journal_records(paths: tuple[Path, ...], cohort: Path) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    records: dict[str, dict[str, Any]] = {}
    hashes: dict[str, str] = {}
    # Binding discovery from journal.json includes successor segments exactly once.
    for path in (*paths, cohort):
        if not path.is_file() or path.is_symlink():
            raise ValueError("AUTHORITATIVE_JOURNAL_UNAVAILABLE")
        binding = HistoricalExecutionBinding(path)
        binding.read()
        for segment, document in binding._segments.items():
            hashes[str(segment)] = _sha256(segment)
            for record in document["records"]:
                identity = record.get("request_identity")
                if not isinstance(identity, str) or not identity or identity in records:
                    raise ValueError("DUPLICATE_OR_INVALID_REQUEST_IDENTITY")
                records[identity] = {**record, "_journal": str(segment), "_journal_sha256": hashes[str(segment)]}
    return records, hashes


def _observation(record: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    if isinstance(record.get("creation_time_anchored_observation"), dict):
        return "CREATION_TIME_ANCHORED_OBSERVATION", record["creation_time_anchored_observation"]
    if isinstance(record.get("entry_relative_observed_minima"), dict):
        return "QUALIFIED_ENTRY_ANCHOR", {"results": record["entry_relative_observed_minima"]}
    if isinstance(record.get("observed_price_relative_extrema"), dict):
        return "OBSERVED_PRICE_ANCHOR", {"results": record["observed_price_relative_extrema"]}
    return None, None


def _windows(observation: dict[str, Any] | None) -> dict[str, dict[str, Any] | None]:
    values = observation.get("results", {}) if observation else {}
    out: dict[str, dict[str, Any] | None] = {}
    for seconds in WINDOWS:
        item = values.get(str(seconds), values.get(seconds))
        if not isinstance(item, dict):
            out[str(seconds)] = None
            continue
        out[str(seconds)] = {
            "coverage": item.get("coverage_status"),
            "minimum_status": item.get("minimum_status"),
            "valid_candles": item.get("valid_bucket_count"),
            "missing_candles": len(item.get("missing_bucket_timestamps", [])),
            "minimum_mc_usd": item.get("observed_minimum_mc_usd"),
            "maximum_mc_usd": item.get("observed_maximum_mc_usd"),
            "maximum_timestamp": item.get("observed_maximum_timestamp"),
        }
    return out


def _status(record: dict[str, Any] | None, observation: dict[str, Any] | None) -> str:
    if record is None:
        return "UNACQUIRED_NO_QUALIFIED_ANCHOR"
    state = record.get("state")
    if state == "ATTEMPTED":
        return "PROTECTED_ATTEMPTED"
    if state == "OUTCOME_UNKNOWN":
        return "PROTECTED_OUTCOME_UNKNOWN"
    if state != "COMPLETED":
        return "UNRESOLVED_NONTERMINAL"
    if record.get("normalization_status") == "NO_VALID_CANDLES":
        return "COMPLETED_NO_VALID_CANDLES"
    if record.get("normalization_status") != "NORMALIZED" or observation is None:
        return "COMPLETED_EVIDENCE_UNQUALIFIED"
    return "COMPLETED_QUALIFIED_OBSERVATION"


def build(*, chronology: dict[str, Any], population: dict[str, Any], authorization: dict[str, Any],
          records: dict[str, dict[str, Any]], journal_hashes: dict[str, str]) -> dict[str, Any]:
    ranked = chronology["ranked"]
    if len(ranked) != 597 or len(chronology["unranked"]) != 42:
        raise ValueError("IMMUTABLE_CHRONOLOGY_COUNTS_INVALID")
    by_rank: dict[int, dict[str, Any]] = {}
    for record in records.values():
        rank = record.get("chronological_rank")
        if not isinstance(rank, int) or rank in by_rank:
            raise ValueError("DUPLICATE_OR_INVALID_CHRONOLOGICAL_RANK")
        by_rank[rank] = record
    rows: list[dict[str, Any]] = []
    coverage: dict[str, int] = {}
    missing_candles = 0
    first_observed: list[float] = []
    first_hour_minimum: list[float] = []
    first_hour_maximum: list[float] = []
    drawdowns: list[float] = []
    time_to_high: list[float] = []
    qualified_records = partial_hour_records = no_valid = normalization_failures = 0
    anchor_counts: dict[str, int] = {}
    for item in ranked:
        rank = item["rank"]
        record = by_rank.get(rank)
        anchor_type, observation = _observation(record or {})
        status = _status(record, observation)
        windows = _windows(observation)
        if anchor_type:
            anchor_counts[anchor_type] = anchor_counts.get(anchor_type, 0) + 1
        row: dict[str, Any] = {
            "rank": rank, "mint": item["mint"], "creation_timestamp": item["creation_timestamp"],
            "acquisition_status": status, "anchor_type": anchor_type,
            "first_observed_mc_usd": observation.get("first_observed_mc_usd") if observation else None,
            "first_observed_timestamp": observation.get("first_observed_timestamp") if observation else None,
            "windows": windows,
            "provider_provenance": observation.get("provider_provenance") if observation else None,
            "evidence_identity": record.get("evidence_identity") if record else None,
            "source_journal_sha256": record.get("_journal_sha256") if record else None,
            "normalization_status": record.get("normalization_status") if record else None,
        }
        if record and record.get("state") == "COMPLETED" and not row["evidence_identity"]:
            raise ValueError("COMPLETED_EVIDENCE_IDENTITY_MISSING")
        if status == "COMPLETED_QUALIFIED_OBSERVATION":
            qualified_records += 1
        elif status == "COMPLETED_NO_VALID_CANDLES":
            no_valid += 1
        elif status == "COMPLETED_EVIDENCE_UNQUALIFIED":
            normalization_failures += 1
        hour = windows["3600"]
        if hour:
            state = str(hour.get("coverage") or "UNAVAILABLE")
            coverage[state] = coverage.get(state, 0) + 1
            missing_candles += int(hour.get("missing_candles") or 0)
            if state != "COMPLETE_OBSERVED_WINDOW":
                partial_hour_records += 1
        if anchor_type == "CREATION_TIME_ANCHORED_OBSERVATION" and status == "COMPLETED_QUALIFIED_OBSERVATION":
            first = row["first_observed_mc_usd"]
            if isinstance(first, (int, float)) and first > 0:
                first_observed.append(float(first))
                if hour and hour.get("coverage") == "COMPLETE_OBSERVED_WINDOW":
                    low, high = hour.get("minimum_mc_usd"), hour.get("maximum_mc_usd")
                    high_ts = hour.get("maximum_timestamp")
                    if isinstance(low, (int, float)) and isinstance(high, (int, float)):
                        first_hour_minimum.append(float(low)); first_hour_maximum.append(float(high))
                        drawdowns.append((float(first) - float(low)) * 100.0 / float(first))
                    if isinstance(high_ts, int):
                        time_to_high.append(float(high_ts - item["creation_timestamp"]))
        rows.append(row)
    protected = sum(row["acquisition_status"].startswith("PROTECTED_") for row in rows)
    completed = sum(row["acquisition_status"].startswith("COMPLETED_") for row in rows)
    return {
        "artifact_type": "DEV014_WATCHTOWER_OPENING_HOUR_RESEARCH_DATASET",
        "version": "v1", "frozen_population": chronology["population_identity"],
        "chronology_content_hash": chronology["content_hash"], "cohort_authorization_content_hash": authorization["content_hash"],
        "inputs": {"journal_sha256": journal_hashes},
        "storage_contract": {"raw_provider_payload_retention": False, "max_artifact_bytes": MAX_ARTIFACT_BYTES,
                             "single_file_500mb_or_more_allowed": False, "unbounded_growth_paths": 0},
        "coverage": {"frozen_launches": 639, "ranked_launches": 597, "unranked_launches": 42,
                     "completed_provider_identities": completed, "qualified_price_observations": qualified_records,
                     "first_hour_partial_coverage_records": partial_hour_records,
                     "first_hour_coverage_by_status": coverage, "first_hour_missing_candles": missing_candles,
                     "no_valid_candles": no_valid, "normalization_failures": normalization_failures,
                     "protected_uncertain_outcomes": protected,
                     "remaining_missing_price_evidence": 597 - qualified_records,
                     "anchor_type_counts": anchor_counts},
        "research_statistics": {"creation_time_observations_only": True,
                                "first_observed_mc_usd": _quantiles(first_observed),
                                "first_hour_minimum_mc_usd": _quantiles(first_hour_minimum),
                                "first_hour_maximum_mc_usd": _quantiles(first_hour_maximum),
                                "first_hour_drawdown_percent": _quantiles(drawdowns),
                                "time_to_observed_high_seconds": _quantiles(time_to_high),
                                "entry_relative_measurements_excluded_from_creation_time_statistics": anchor_counts.get("QUALIFIED_ENTRY_ANCHOR", 0)},
        "records": rows,
    }


def write(path: Path, value: dict[str, Any]) -> None:
    rendered = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    if len(rendered.encode()) >= MAX_ARTIFACT_BYTES:
        raise ValueError("COMPACT_RESEARCH_ARTIFACT_BOUND_EXCEEDED")
    temporary = path.with_suffix(path.suffix + ".tmp")
    if temporary.exists() or path.is_symlink():
        raise ValueError("OUTPUT_PATH_UNSAFE")
    temporary.write_text(rendered, encoding="utf-8")
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--chronology", type=Path, default=CHRONOLOGY)
    parser.add_argument("--population", type=Path, default=POPULATION)
    parser.add_argument("--authorization", type=Path, default=COHORT_AUTHORIZATION)
    parser.add_argument("--cohort-journal", type=Path, default=COHORT_JOURNAL)
    parser.add_argument("--historical-journal", type=Path, action="append")
    args = parser.parse_args()
    historical = tuple(Path(path) for path in args.historical_journal) if args.historical_journal else HISTORICAL_JOURNALS
    records, hashes = _journal_records(historical, args.cohort_journal)
    value = build(chronology=_load(args.chronology), population=_load(args.population), authorization=_load(args.authorization),
                  records=records, journal_hashes=hashes)
    write(args.output, value)


if __name__ == "__main__":
    main()
