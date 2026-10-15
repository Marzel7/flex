"""Read-only DEV-013 evidence projection for Watchtower UI consumers."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable


FIRST_AVAILABLE_POLICY = "WATCHTOWER_FIRST_AVAILABLE_MC_V1"
LIFECYCLE_POLICY = "WATCHTOWER_RECENT50_ADAPTIVE_LIFECYCLE_V1"
ROLLING_POLICY = "WATCHTOWER_CENSORED_ROLLING_LIFECYCLE_V1"
RIGHT_CENSORED_MINTS = {
    "4uTHA7hUfZD1PKWQBVtuCEWnBjgB6xaL7GfJrPRDpump",
    "6RufsEXSv9Nd4YUmUGah8MeMXZT2rxn3kriGiBcSpump",
}
ROOT = Path(__file__).resolve().parents[2]
LIFECYCLE_PILOT_PATH = ROOT / "docs/audits/dev013_watchtower_recent50_adaptive_lifecycle_pilot.v1.json"
_LIFECYCLE_FIELDS = (
    "ath_mc_usd", "ath_timestamp", "max_multiple", "time_to_ath_seconds",
    "max_drawdown_percent", "terminal_reason", "terminal_timestamp",
    "terminal_mc_usd", "terminal_multiple", "lifecycle_horizon",
)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _json(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _empty_lifecycle(status: str, source: str | None = None) -> dict[str, Any]:
    return {"lifecycle_status": status, **{field: None for field in _LIFECYCLE_FIELDS}, "lifecycle_evidence_source": source}


def _retained(payload: dict[str, Any], canonical: str, alias: str | None = None) -> Any:
    return payload.get(canonical) if canonical in payload else payload.get(alias) if alias else None


def _lifecycle(payload: dict[str, Any], *, source: str, right_censored: bool = False) -> dict[str, Any]:
    if right_censored:
        return _empty_lifecycle("RIGHT_CENSORED", "RIGHT_CENSORED_STAGE2_PENDING")
    if payload.get("terminal_reason") not in {"DRAWDOWN_85", "HORIZON_7D"}:
        return _empty_lifecycle("LIFECYCLE_EVIDENCE_MISSING")
    return {
        "lifecycle_status": "QUALIFIED_TERMINAL",
        "ath_mc_usd": _retained(payload, "ath_mc_usd", "observed_ath_mc_usd"),
        "ath_timestamp": _retained(payload, "ath_timestamp", "observed_ath_timestamp"),
        "max_multiple": _retained(payload, "max_multiple", "observed_max_multiple"),
        "time_to_ath_seconds": payload.get("time_to_ath_seconds"),
        "max_drawdown_percent": payload.get("max_drawdown_percent"),
        "terminal_reason": payload.get("terminal_reason"), "terminal_timestamp": payload.get("terminal_timestamp"),
        "terminal_mc_usd": payload.get("terminal_mc_usd"), "terminal_multiple": payload.get("terminal_multiple"),
        "lifecycle_horizon": payload.get("lifecycle_horizon_actually_acquired"),
        "lifecycle_evidence_source": source,
    }


def _pilot_terminal_facts() -> dict[str, dict[str, Any]]:
    try:
        rows = _json(LIFECYCLE_PILOT_PATH.read_text()).get("tokens", [])
    except OSError:
        return {}
    return {str(row["mint"]): row for row in rows if isinstance(row, dict) and row.get("mint") and row.get("terminal_reason") in {"DRAWDOWN_85", "HORIZON_7D"}}


def dev013_watchtower_ui_facts(conn: sqlite3.Connection, mints: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Return retained Watchtower facts only; never writes or acquires."""
    wanted = sorted({str(mint) for mint in mints if mint})
    if not wanted:
        return {}
    placeholders = ",".join("?" for _ in wanted)
    facts = {mint: {} for mint in wanted}
    if _table_exists(conn, "operation_monitor_entry_provenance"):
        rows = conn.execute(
            "SELECT p.* FROM operation_monitor_entry_provenance p JOIN ("
            " SELECT mint,MAX(acquired_at) AS acquired_at FROM operation_monitor_entry_provenance"
            " WHERE operation_id='watchtower' AND entry_quality='FIRST_AVAILABLE_SLOT' AND entry_policy=?"
            f" AND mint IN ({placeholders}) GROUP BY mint) latest"
            " ON latest.mint=p.mint AND latest.acquired_at=p.acquired_at"
            " WHERE p.operation_id='watchtower' AND p.entry_quality='FIRST_AVAILABLE_SLOT' AND p.entry_policy=?",
            [FIRST_AVAILABLE_POLICY, *wanted, FIRST_AVAILABLE_POLICY],
        ).fetchall()
        for row in rows:
            record = dict(row)
            facts[record["mint"]].update({
                "entry_mc_usd": record["entry_mc_usd"], "entry_timestamp": record["entry_timestamp"],
                "entry_quality": "FIRST_AVAILABLE_SLOT", "entry_policy": FIRST_AVAILABLE_POLICY,
                "entry_label": "First Available", "entry_slot": record["entry_slot"],
                "entry_slot_offset": record["reference_slot_offset"], "entry_venue": record["first_available_venue"],
                "entry_provenance_available": True,
            })
    rolling: dict[str, dict[str, Any]] = {}
    if _table_exists(conn, "dev013_watchtower_censored_rolling_final"):
        for row in conn.execute(f"SELECT mint,result_json FROM dev013_watchtower_censored_rolling_final WHERE policy=? AND mint IN ({placeholders})", [ROLLING_POLICY, *wanted]).fetchall():
            rolling[row["mint"]] = _json(row["result_json"])
    if _table_exists(conn, "dev013_watchtower_recent50_lifecycle_provenance"):
        for row in conn.execute(f"SELECT mint,outcome,result_json FROM dev013_watchtower_recent50_lifecycle_provenance WHERE lifecycle_policy=? AND mint IN ({placeholders})", [LIFECYCLE_POLICY, *wanted]).fetchall():
            mint = row["mint"]
            if mint not in rolling and (mint in RIGHT_CENSORED_MINTS or row["outcome"] in {"QUALIFIED", "QUALIFIED_LIFECYCLE"}):
                facts[mint].update(_lifecycle(_json(row["result_json"]), source=LIFECYCLE_POLICY, right_censored=mint in RIGHT_CENSORED_MINTS))
    for mint, payload in rolling.items():
        facts[mint].update(_lifecycle(payload, source=ROLLING_POLICY))
    for mint, payload in _pilot_terminal_facts().items():
        if mint in facts and not facts[mint].get("lifecycle_status"):
            facts[mint].update(_lifecycle(payload, source="WATCHTOWER_RECENT50_ADAPTIVE_LIFECYCLE_PILOT_V1"))
    return facts
