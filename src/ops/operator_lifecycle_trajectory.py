"""Offline, manifest-driven lifecycle trajectory reads.

The database stores only window references and coverage metadata.  Candle bytes
remain in the retained evidence files named by those references.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from src.ops.birdeye_price_lifecycle import parse_ohlc

MODEL = "BYZANTINE_LIFECYCLE_MODEL_V1"
CONTRACT = {
    "lifecycle_model_version": MODEL,
    "tiers": (
        ("TIER_1", "1s", 0, 300), ("TIER_2", "15s", 300, 1800),
        ("TIER_3", "1m", 1800, 21600), ("TIER_4", "5m", 21600, 86400),
        ("TIER_5", "15m", 86400, 172800), ("TIER_6", "30m", 172800, 259200),
        ("TIER_7", "1H", 259200, 604800),
    ),
    "interval_precision_seconds": {"1s": 1, "15s": 15, "1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1H": 3600},
    "collapse_model_version": "BYZANTINE_COLLAPSE_SEVERITY_V1",
    "primary_collapse_threshold_percent": 92.5,
    "terminal_rug_threshold_percent": 95,
    "maximum_horizon_seconds": 604800,
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def contract_digest() -> str:
    return hashlib.sha256(canonical_json(CONTRACT).encode()).hexdigest()


def coverage(windows: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(windows, key=lambda x: (x["start_timestamp"], x["end_timestamp"], x["window_identity"]))
    gaps, overlaps = [], []
    for left, right in zip(ordered, ordered[1:]):
        if right["start_timestamp"] > left["end_timestamp"]:
            gaps.append({"start": left["end_timestamp"], "end": right["start_timestamp"]})
        elif right["start_timestamp"] < left["end_timestamp"]:
            overlaps.append({"start": right["start_timestamp"], "end": left["end_timestamp"]})
    return {
        "qualified_tiers": [x["tier"] for x in ordered],
        "coverage_start": ordered[0]["start_timestamp"] if ordered else None,
        "coverage_end": ordered[-1]["end_timestamp"] if ordered else None,
        "gaps": gaps, "overlaps": overlaps,
        "missing_windows": [],
        "interval_transitions": [{"from": a["interval"], "to": b["interval"], "at": b["start_timestamp"]} for a, b in zip(ordered, ordered[1:]) if a["interval"] != b["interval"]],
        "maximum_qualified_horizon_seconds": (ordered[-1]["end_timestamp"] - ordered[0]["start_timestamp"]) if ordered else 0,
    }


def indexed_windows(facts: dict[str, Any]) -> list[dict[str, Any]]:
    return list((facts.get("trajectory_index") or {}).get("windows") or [])


def read_candles(conn: sqlite3.Connection, operator_id: str, mint: str, model: str = MODEL) -> list[dict[str, Any]]:
    """Read only explicitly indexed evidence paths; never scans a directory."""
    row = conn.execute("SELECT facts_json FROM operator_lifecycle_projection WHERE operator_id=? AND mint=? AND lifecycle_model_version=?", (operator_id, mint, model)).fetchone()
    if not row:
        raise ValueError("UNKNOWN_TRAJECTORY")
    facts = json.loads(row[0]); index = facts.get("trajectory_index") or {}
    if index.get("lifecycle_model_version") != model:
        raise ValueError("UNQUALIFIED_TRAJECTORY_INDEX")
    output = []
    for window in indexed_windows(facts):
        if window.get("qualification_status") != "QUALIFIED":
            continue
        payload = Path(window["raw_artifact_reference"]).read_bytes()
        if hashlib.sha256(payload).hexdigest() != window["raw_artifact_digest"]:
            raise ValueError("RAW_ARTIFACT_DIGEST_MISMATCH")
        for candle in parse_ohlc(json.loads(payload)):
            output.append({**candle, "tier": window["tier"], "interval": window["interval"], "window_identity": window["window_identity"], "window_provenance": window["raw_artifact_reference"]})
    # Boundary candles may be present in both adjacent responses.  The logical
    # manifest is chronological, so retain one deterministic observation.
    return [x for _, x in sorted({x["timestamp"]: x for x in output}.items())]
