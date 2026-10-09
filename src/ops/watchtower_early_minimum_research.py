"""Read-only presentation of the bounded DEV-014 Watchtower pilot.

This deliberately models a different evidence class from the append-only
observed-minimum store.  The pilot predates deterministic request/evidence
identities, so it cannot be promoted or imported into that authority.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from statistics import median
from typing import Any, Mapping


EVIDENCE_CLASS = "RESEARCH_PILOT_OBSERVATION"
AUTHORITATIVE_EVIDENCE_CLASS = "AUTHORITATIVE_OBSERVED_MINIMUM"
WINDOWS = {"5m": 300, "15m": 900, "60m": 3600}
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACT = ROOT / "docs/audits/dev014_watchtower_early_minimum_mcap_pilot_20261009.v1.json"


def _artifact_identity(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _observation(mint: str, entry: Mapping[str, Any], label: str, value: list[Any], artifact_sha256: str) -> dict[str, Any]:
    if len(value) != 4:
        raise ValueError("INVALID_PILOT_MINIMUM")
    minimum, timestamp, offset, coverage = value
    entry_mc = float(entry["mc_usd"])
    minimum_mc = float(minimum)
    if not mint or entry_mc <= 0 or minimum_mc <= 0 or int(timestamp) <= 0 or int(offset) < 0:
        raise ValueError("INVALID_PILOT_OBSERVATION")
    return {
        "evidence_class": EVIDENCE_CLASS,
        "mint": mint,
        "entry_timestamp": int(entry["timestamp"]),
        "entry_mc_usd": entry_mc,
        "window_label": label,
        "window_seconds": WINDOWS[label],
        "observed_minimum_mc_usd": minimum_mc,
        "observed_minimum_timestamp": int(timestamp),
        "observed_minimum_offset_seconds": int(offset),
        "observed_drawdown_percent": (entry_mc - minimum_mc) * 100.0 / entry_mc,
        "coverage_status": str(coverage),
        "request_identity": None,
        "evidence_identity": None,
        "chronological_recovery": None,
        "source_artifact_sha256": artifact_sha256,
    }


def read_pilot(path: str | Path = DEFAULT_ARTIFACT) -> dict[str, Any]:
    """Parse the retained artifact without deriving missing evidence."""
    artifact = Path(path).resolve()
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "DEV014_WATCHTOWER_PARTIAL_OBSERVED_MINIMUM_PILOT_V1":
        raise ValueError("UNRECOGNIZED_PILOT_ARTIFACT")
    sha256 = _artifact_identity(artifact)
    rows = []
    for record in payload.get("records") or ():
        mint = str(record.get("mint") or "")
        entry = record.get("entry") or {}
        minima = record.get("minima") or {}
        observations = {label: _observation(mint, entry, label, value, sha256)
                        for label, value in minima.items() if label in WINDOWS}
        rows.append({"mint": mint, "entry_mc_usd": entry.get("mc_usd"), "entry_timestamp": entry.get("timestamp"),
                     "observations": observations, "evidence_class": EVIDENCE_CLASS,
                     "source_artifact_sha256": sha256, "thirty_minute": None, "recovery": None})
    if len(rows) != 10 or len({row["mint"] for row in rows}) != len(rows):
        raise ValueError("PILOT_SAMPLE_IDENTITY_INVALID")
    return {"evidence_class": EVIDENCE_CLASS, "source_artifact_sha256": sha256,
            "sample_size": len(rows), "selected_terminal_collapsed_sample": True, "rows": rows}


def statistics(pilot: Mapping[str, Any]) -> dict[str, Any]:
    """Return sample-only statistics; missing data never enters a denominator."""
    result = {}
    for label, seconds in WINDOWS.items():
        measured = [row["observations"][label] for row in pilot["rows"] if label in row["observations"]]
        declines = [float(row["observed_drawdown_percent"]) for row in measured]
        result[str(seconds)] = {
            "window_label": label, "sample_size": int(pilot["sample_size"]), "measured_count": len(measured),
            "missing_count": int(pilot["sample_size"]) - len(measured),
            "complete_returned_buckets": sum(row["coverage_status"] == "COMPLETE_OBSERVED_WINDOW" for row in measured),
            "partial_coverage": sum(row["coverage_status"] == "PARTIAL_OBSERVED_MINIMUM" for row in measured),
            "declines": {str(threshold): {"numerator": sum(value >= threshold for value in declines),
                                             "denominator": len(measured),
                                             "percent": (sum(value >= threshold for value in declines) * 100.0 / len(measured)) if measured else None}
                         for threshold in (20, 30, 40, 50)},
            "median_observed_drawdown_percent": median(declines) if declines else None,
            "median_time_to_observed_minimum_seconds": median([int(row["observed_minimum_offset_seconds"]) for row in measured]) if measured else None,
        }
    return result


def projection(path: str | Path = DEFAULT_ARTIFACT) -> dict[str, Any]:
    pilot = read_pilot(path)
    return {**pilot, "statistics": statistics(pilot), "thirty_minute_availability": "UNAVAILABLE_NOT_RETAINED",
            "recovery_metric_availability": "UNAVAILABLE_NOT_RETAINED"}
