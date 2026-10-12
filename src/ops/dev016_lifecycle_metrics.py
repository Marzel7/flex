"""Read-only DEV016 projection over compact DEV014 lifecycle journals.

This module never calls a provider or writes acquisition evidence. DEV014 retained
only aggregate facts from a bounded observed candle window, so a candle peak is
explicitly not represented as a verified historical ATH.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterator


COHORT_ROOT = Path(os.getenv("DEV014_LIFECYCLE_COHORT_ROOT", "/private/tmp/dev014-watchtower-lifecycle-cohort"))
OPS_DB_PATH = Path(os.getenv("WT_OPS_DB_PATH", "/Users/kevinkeaveney/Dev/claude/flex/database/wt_ops_v2.db"))
# Refuse unexpectedly large journals rather than treating this read path as raw retention.
MAX_COMPACT_JOURNAL_BYTES = 1_000_000


def _insufficient() -> dict[str, Any]:
    return {"value": None, "status": "INSUFFICIENT_EVIDENCE"}


def _observed(value: Any, provenance: str | None) -> dict[str, Any]:
    return {"value": value, "status": "OBSERVED_CANDLE" if value is not None else "INSUFFICIENT_EVIDENCE", "provenance": provenance}


def _journals() -> Iterator[dict[str, Any]]:
    """Yield valid, bounded compact journal records only."""
    for path in sorted((COHORT_ROOT / "journals").glob("*/journal.json")):
        try:
            if path.stat().st_size > MAX_COMPACT_JOURNAL_BYTES:
                continue
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(record, dict):
            yield record


def lifecycle_metrics(mint: str) -> dict[str, Any]:
    """Expose only facts derivable from the retained compact DEV014 evidence."""
    journal = next((row for row in _journals() if row.get("mint") == mint), None)
    if journal is None:
        return {"mint": mint, "status": "NOT_IN_DEV014_COHORT"}

    launch = journal.get("creation_timestamp")
    first_available = _dev013_first_available(mint)
    first_available_mc = first_available.get("entry_mc_usd") if first_available else None
    first_available_at = first_available.get("entry_timestamp") if first_available else None
    first_available_provenance = {
        "policy": "WATCHTOWER_FIRST_AVAILABLE_MC_V1",
        "semantic": "FIRST_AVAILABLE_TRADE_MARKET_CAP",
        "signature": first_available.get("first_available_signature") if first_available else None,
        "venue": first_available.get("first_available_venue") if first_available else None,
    }
    if journal.get("state") != "COMPLETED":
        return {
            "mint": mint, "status": journal.get("state", "INSUFFICIENT_EVIDENCE"),
            "launch_timestamp": _observed(launch, "WATCHTOWER_CREATION_EVENT"),
            "opening_market_cap": _observed(first_available_mc, first_available_provenance),
            "opening_timestamp": _observed(first_available_at, first_available_provenance),
            "opening_market_cap_semantic": "FIRST_AVAILABLE_TRADE_MARKET_CAP" if first_available else "INSUFFICIENT_EVIDENCE",
            "evidence_status": "NO_RETAINED_VALID_CANDLES",
        }

    facts = journal.get("lifecycle_metrics") or {}
    provenance = journal.get("provider_provenance")
    first_at = facts.get("first_observed_timestamp")
    peak_at = facts.get("observed_maximum_timestamp")
    latest_at = facts.get("last_observed_timestamp")
    trough = facts.get("peak_to_subsequent_trough") or {}

    def observed_seconds(start: Any, finish: Any) -> int | float | None:
        return finish - start if isinstance(start, (int, float)) and isinstance(finish, (int, float)) else None

    return {
        "mint": mint,
        "status": "OBSERVED_LIFECYCLE_AVAILABLE",
        "launch_timestamp": _observed(launch, "WATCHTOWER_CREATION_EVENT"),
        "opening_price": _insufficient(),
        "opening_market_cap": _observed(first_available_mc, first_available_provenance),
        "opening_timestamp": _observed(first_available_at, first_available_provenance),
        "opening_market_cap_semantic": "FIRST_AVAILABLE_TRADE_MARKET_CAP" if first_available else "INSUFFICIENT_EVIDENCE",
        "launch_time_market_cap": _insufficient(),
        "first_available_candle_market_cap": _insufficient(),
        "first_observed_candle_timestamp": _observed(first_at, provenance),
        "first_observed_trade_market_cap": _observed(first_available_mc, first_available_provenance),
        "first_observed_trade_timestamp": _observed(first_available_at, first_available_provenance),
        "observed_candle_peak_market_cap": _observed(facts.get("observed_maximum_mc_usd"), provenance),
        "observed_candle_peak_timestamp": _observed(peak_at, provenance),
        "verified_historical_ath_price": _insufficient(),
        "verified_historical_ath_market_cap": _insufficient(),
        "verified_historical_ath_timestamp": _insufficient(),
        "seconds_launch_to_observed_candle_peak": _observed(observed_seconds(launch, peak_at), provenance),
        "seconds_first_observed_candle_to_peak": _observed(observed_seconds(first_at, peak_at), provenance),
        "gain_from_opening_market_cap_percent": _observed(
            ((facts.get("observed_maximum_mc_usd") / first_available_mc) - 1) * 100
            if isinstance(first_available_mc, (int, float)) and first_available_mc > 0 and isinstance(facts.get("observed_maximum_mc_usd"), (int, float))
            else None,
            {"semantic": "FIRST_AVAILABLE_TRADE_TO_OBSERVED_CANDLE_PEAK", **first_available_provenance},
        ),
        "post_peak_low_market_cap": _observed(trough.get("observed_minimum_mc_usd"), provenance),
        "post_peak_low_timestamp": _observed(trough.get("observed_minimum_timestamp"), provenance),
        "max_observed_drawdown_percent": _observed(trough.get("drawdown_percent"), provenance),
        "latest_observed_price": _insufficient(),
        "latest_observed_market_cap": _observed(facts.get("last_observed_mc_usd"), provenance),
        "latest_observed_timestamp": _observed(latest_at, provenance),
        "observed_lifecycle_duration_seconds": _observed(observed_seconds(launch, latest_at), provenance),
        "evidence_status": facts.get("coverage_confidence"),
        "provider_provenance": provenance,
    }


def _assigned_mints() -> set[str] | None:
    """Read the current Watchtower assignment table without mutating it."""
    if not OPS_DB_PATH.is_file():
        return None
    try:
        connection = sqlite3.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True)
        try:
            return {row[0] for row in connection.execute("SELECT DISTINCT mint FROM watchtower_token_attribution")}
        finally:
            connection.close()
    except sqlite3.Error:
        return None


def _dev013_first_available(mint: str) -> dict[str, Any] | None:
    """Read a qualified DEV013 first-available *trade* fact, if retained.

    This is neither launch-time valuation nor a candle value. The narrow policy
    predicate protects DEV016 from silently consuming a different monitor entry.
    """
    if not OPS_DB_PATH.is_file():
        return None
    try:
        connection = sqlite3.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute(
                "SELECT entry_mc_usd, entry_timestamp, first_available_signature, first_available_venue "
                "FROM operation_monitor_entry_provenance "
                "WHERE operation_id='watchtower' AND mint=? "
                "AND entry_policy='WATCHTOWER_FIRST_AVAILABLE_MC_V1' "
                "AND entry_quality='FIRST_AVAILABLE_SLOT' AND entry_mc_usd > 0",
                (mint,),
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    return dict(row) if row else None


def _dev013_first_available_mints() -> set[str]:
    if not OPS_DB_PATH.is_file():
        return set()
    try:
        connection = sqlite3.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True)
        try:
            return {row[0] for row in connection.execute(
                "SELECT mint FROM operation_monitor_entry_provenance "
                "WHERE operation_id='watchtower' AND entry_policy='WATCHTOWER_FIRST_AVAILABLE_MC_V1' "
                "AND entry_quality='FIRST_AVAILABLE_SLOT' AND entry_mc_usd > 0"
            )}
        finally:
            connection.close()
    except sqlite3.Error:
        return set()


def coverage() -> dict[str, Any]:
    rows = list(_journals())
    cohort_mints = {row.get("mint") for row in rows if row.get("mint")}
    assigned = _assigned_mints()
    dev013_opening_mints = _dev013_first_available_mints()
    evidence_rows = [row for row in rows if row.get("state") == "COMPLETED"]
    result: dict[str, Any] = {
        "dev014_cohort_count": len(cohort_mints),
        "tokens_with_lifecycle_evidence": len(evidence_rows),
        "terminal_no_valid_candles_count": sum(row.get("state") == "NO_VALID_CANDLES" for row in rows),
        "observed_peak_coverage": len(evidence_rows),
        "drawdown_coverage": sum(bool((row.get("lifecycle_metrics") or {}).get("peak_to_subsequent_trough")) for row in evidence_rows),
        "opening_market_cap_coverage": len(cohort_mints & dev013_opening_mints),
        "latest_market_cap_coverage": sum((row.get("lifecycle_metrics") or {}).get("last_observed_mc_usd") is not None for row in evidence_rows),
    }
    if assigned is None:
        result.update({"watchtower_assigned_token_count": None, "tokens_outside_dev014_count": None, "coverage_status": "INSUFFICIENT_ASSIGNMENT_EVIDENCE"})
    else:
        result.update({
            "watchtower_assigned_token_count": len(assigned),
            "dev014_tokens_currently_assigned_count": len(cohort_mints & assigned),
            "dev014_tokens_not_currently_assigned_count": len(cohort_mints - assigned),
            "tokens_outside_dev014_count": len(assigned - cohort_mints),
            "coverage_status": "CURRENT_ASSIGNMENT_COMPARISON",
        })
    return result
