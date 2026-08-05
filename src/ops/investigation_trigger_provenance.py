"""Immutable provenance for why an Investigation Population was created."""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Mapping


DDL = """
CREATE TABLE IF NOT EXISTS wt_investigation_trigger_provenance (
 family_id TEXT PRIMARY KEY,
 created_at INTEGER,
 trigger_type TEXT NOT NULL,
 signals_json TEXT NOT NULL,
 initial_disposition TEXT NOT NULL,
 initial_population_size INTEGER NOT NULL,
 initial_topology TEXT,
 no_confirmed_operation_match INTEGER NOT NULL,
 captured_from_json TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS wt_trigger_provenance_no_update
BEFORE UPDATE ON wt_investigation_trigger_provenance BEGIN
 SELECT RAISE(ABORT, 'investigation trigger provenance is immutable');
END;
CREATE TRIGGER IF NOT EXISTS wt_trigger_provenance_no_delete
BEFORE DELETE ON wt_investigation_trigger_provenance BEGIN
 SELECT RAISE(ABORT, 'investigation trigger provenance is immutable');
END;
"""


def _trigger(family: Mapping[str, Any], reconciliation: Mapping[str, Any] | None) -> dict[str, Any]:
    treasuries = list(family.get("treasuries") or family.get("member_treasuries") or [])
    clients = list(family.get("provisioning_clients") or family.get("client_wallets") or [])
    creators = list(family.get("unique_creators") or [])
    mechanisms = list(family.get("funding_mechanisms") or [])
    members = list(family.get("member_wallets") or [])
    launches = list(family.get("launch_list") or [])
    walkback = int(family.get("walkback_descendant_count") or 0)
    canonical = str(family.get("family_id") or "").startswith("canonical:")
    if canonical:
        trigger_type = "Confirmed Operator"
    elif walkback and treasuries:
        trigger_type = "Operational Treasury"
    elif len(treasuries) > 1 or len(members) > 1:
        trigger_type = "Shared Infrastructure"
    elif clients and creators:
        trigger_type = "Provisioning Controller"
    elif int(family.get("session_count") or 0):
        trigger_type = "Session Cluster"
    else:
        trigger_type = "Investigation Population"

    signals: list[str] = []
    def add(label: str, present: bool) -> None:
        if present and label not in signals:
            signals.append(label)
    add("Operational Treasury discovered", walkback > 0 and bool(treasuries))
    add("Shared Treasury", len(treasuries) > 1)
    add("Provisioning Lineage", bool(clients))
    add("Creator Reuse", bool(creators) and len(creators) < len(launches))
    add("Funding Mechanism", bool(mechanisms))
    add("Fan-Out observed", len(launches) > 1 and (len(creators) > 1 or len(clients) > 1))
    add("Multi-Level Fan-Out", walkback > 0 and len(launches) > 1)
    add("Walkback convergence", walkback > 0)
    add("Topology", bool(family.get("dominant_topology")) and family.get("dominant_topology") != "Evidence accumulation incomplete")
    add("Unknown Treasury", any("unknown" in str(x).lower() for x in family.get("evidence_sources") or []))
    if canonical:
        add("Manual confirmation", "manual_confirmation" in (family.get("evidence_sources") or []))
    return {
        "family_id": str(family.get("family_id") or ""),
        "trigger_type": trigger_type,
        "signals": signals,
        "created_at": family.get("first_seen_at") or family.get("state_changed_at"),
        "initial_disposition": str((reconciliation or {}).get("disposition") or family.get("stage") or "UNRESOLVED"),
        "initial_population_size": len(launches) or int(family.get("launches") or 0),
        "initial_topology": family.get("dominant_topology"),
        "no_confirmed_operation_match": not canonical and not bool(family.get("canonical_operator_id")),
        "captured_from": sorted(str(x) for x in (family.get("evidence_sources") or [])),
    }


def capture_and_apply(path: str, families: list[dict[str, Any]], reconciliation_by_family: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Insert unseen triggers and return the immutable record for every family."""
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(DDL)
        for family in families:
            trigger = _trigger(family, reconciliation_by_family.get(str(family.get("family_id"))))
            if not trigger["family_id"]:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO wt_investigation_trigger_provenance VALUES (?,?,?,?,?,?,?,?,?)",
                (trigger["family_id"], trigger["created_at"], trigger["trigger_type"],
                 json.dumps(trigger["signals"]), trigger["initial_disposition"],
                 trigger["initial_population_size"], trigger["initial_topology"],
                 int(trigger["no_confirmed_operation_match"]),
                 json.dumps(trigger["captured_from"])),
            )
        conn.commit()
        rows = conn.execute("SELECT * FROM wt_investigation_trigger_provenance").fetchall()
    finally:
        conn.close()
    result = {}
    for row in rows:
        item = dict(row)
        item["signals"] = json.loads(item.pop("signals_json") or "[]")
        item["captured_from"] = json.loads(item.pop("captured_from_json") or "[]")
        item["no_confirmed_operation_match"] = bool(item["no_confirmed_operation_match"])
        result[item["family_id"]] = item
    for family in families:
        trigger = result.get(str(family.get("family_id")))
        if trigger:
            family["investigation_trigger"] = trigger
    return result


def apply_trigger_map(payload: dict[str, Any], triggers: Mapping[str, dict[str, Any]]) -> None:
    """Attach immutable trigger records to every family projection in a payload."""
    for value in payload.values():
        if not isinstance(value, list):
            continue
        for family in value:
            if isinstance(family, dict) and family.get("family_id") in triggers:
                family["investigation_trigger"] = triggers[family["family_id"]]
