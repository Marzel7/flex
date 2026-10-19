"""Analyst-governed dismissal and reopening for Investigation Populations."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from typing import Any


REASONS = {
    "SPAM_DUSTING": "Spam / Dusting", "KNOWN_BENIGN": "Known benign behaviour",
    "FALSE_POSITIVE": "False positive", "DUPLICATE": "Duplicate Investigation",
    "SHARED_INFRASTRUCTURE": "Shared Infrastructure only",
    "ANALYST_DECISION": "Analyst decision", "OTHER": "Other",
}

DDL = """
CREATE TABLE IF NOT EXISTS wt_investigation_lifecycle (
 family_id TEXT PRIMARY KEY, state TEXT NOT NULL CHECK(state IN ('DISMISSED','REOPENED')),
 original_disposition TEXT NOT NULL, reason_code TEXT, reason_label TEXT, notes TEXT,
 dismissed_by TEXT, dismissed_at INTEGER, reopened_by TEXT, reopened_at INTEGER,
 dismissal_fingerprint TEXT, dismissal_snapshot TEXT, updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS wt_investigation_lifecycle_audit (
 event_id TEXT PRIMARY KEY, family_id TEXT NOT NULL, event_type TEXT NOT NULL,
 analyst TEXT NOT NULL, reason TEXT NOT NULL, notes TEXT, timestamp INTEGER NOT NULL,
 previous_state TEXT NOT NULL, resulting_state TEXT NOT NULL,
 evidence_fingerprint TEXT NOT NULL, evidence_snapshot TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_wil_state ON wt_investigation_lifecycle(state, updated_at DESC);
CREATE INDEX IF NOT EXISTS ix_wila_family ON wt_investigation_lifecycle_audit(family_id, timestamp DESC);
"""


class InvestigationLifecycleError(Exception):
    def __init__(self, message: str, code: str = "INVESTIGATION_LIFECYCLE_ERROR", status: int = 400):
        super().__init__(message); self.code = code; self.status = status


def behaviour_snapshot(family: dict[str, Any]) -> dict[str, Any]:
    rec = family.get("reconciliation") or {}
    return {
        "launches": sorted(family.get("launch_list") or []),
        "treasuries": sorted(family.get("treasuries") or []),
        "clients": sorted(family.get("client_wallets") or []),
        "mechanisms": sorted(family.get("funding_mechanisms") or []),
        "topologies": sorted(family.get("observed_topology_variants") or []),
        "walkback_descendants": int(family.get("walkback_descendant_count") or 0),
        "supporting_types": sorted(str(x.get("evidence_type") or "") for x in rec.get("supporting_evidence") or []),
    }


def fingerprint(snapshot: dict[str, Any]) -> str:
    raw = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _change_reasons(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    labels = {"treasuries": "New Treasury", "topologies": "New Topology",
              "mechanisms": "New Funding Mechanism", "clients": "New Provisioning Pattern",
              "walkback_descendants": "New Walkback Evidence", "supporting_types": "New Evidence"}
    reasons = []
    for key, label in labels.items():
        left, right = before.get(key), after.get(key)
        if isinstance(right, list):
            if set(right) - set(left or []): reasons.append(label)
        elif int(right or 0) > int(left or 0): reasons.append(label)
    return reasons


def read_lifecycle(path: str, family_id: str) -> dict[str, Any] | None:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True); conn.row_factory = sqlite3.Row
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='wt_investigation_lifecycle'").fetchone():
            conn.close(); return None
        row = conn.execute("SELECT * FROM wt_investigation_lifecycle WHERE family_id=?", (family_id,)).fetchone()
        history = conn.execute("SELECT * FROM wt_investigation_lifecycle_audit WHERE family_id=? ORDER BY timestamp DESC,rowid DESC", (family_id,)).fetchall()
        conn.close()
        if not row: return None
        result = dict(row); result["history"] = [dict(x) for x in history]
        result["dismissal_snapshot"] = json.loads(result.get("dismissal_snapshot") or "{}")
        return result
    except (sqlite3.Error, OSError, json.JSONDecodeError):
        return None


def apply_lifecycle_overlay(family: dict[str, Any], path: str) -> dict[str, Any]:
    lifecycle = read_lifecycle(path, str(family.get("family_id") or ""))
    if not lifecycle: return family
    current = behaviour_snapshot(family); current_fp = fingerprint(current)
    reasons = _change_reasons(lifecycle.get("dismissal_snapshot") or {}, current) if lifecycle["state"] == "DISMISSED" else []
    lifecycle["current_fingerprint"] = current_fp
    lifecycle["reopen_recommended"] = bool(reasons)
    lifecycle["material_changes"] = reasons
    family["investigation_lifecycle"] = lifecycle
    family["analyst_lifecycle"] = lifecycle["state"]
    if lifecycle["state"] == "DISMISSED":
        presentation = dict(family.get("presentation") or {})
        presentation["reconciled_disposition"] = presentation.get("disposition") or (family.get("reconciliation") or {}).get("disposition")
        presentation["disposition"] = "DISMISSED"
        presentation["label"] = "Dismissed Investigation"
        family["presentation"] = presentation
    return family


class InvestigationLifecycleService:
    def __init__(self, path: str, registry: Any): self.path, self.registry = path, registry

    def dismiss(self, family_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        analyst = str(payload.get("analyst") or "").strip(); code = str(payload.get("reason_code") or "").strip().upper()
        notes = str(payload.get("notes") or "").strip()
        if not analyst or code not in REASONS: raise InvestigationLifecycleError("Analyst and a valid dismissal reason are required.", "DISMISSAL_METADATA_REQUIRED")
        family = self.registry.get(family_id)
        if not family: raise InvestigationLifecycleError("Investigation not found.", "FAMILY_NOT_FOUND", 404)
        disposition = str((family.get("reconciliation") or {}).get("disposition") or "")
        if disposition not in {"UNRESOLVED", "REVIEW", "OPERATOR_CANDIDATE"}:
            raise InvestigationLifecycleError("Only Investigation or Review populations can be dismissed.", "DISMISSAL_NOT_PERMITTED", 409)
        snap = behaviour_snapshot(family); fp = fingerprint(snap); now = int(time.time())
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            conn.executescript(DDL)
            old = conn.execute("SELECT state FROM wt_investigation_lifecycle WHERE family_id=?", (family_id,)).fetchone()
            if old and old[0] == "DISMISSED": raise InvestigationLifecycleError("Investigation is already dismissed.", "ALREADY_DISMISSED", 409)
            conn.execute("INSERT INTO wt_investigation_lifecycle VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(family_id) DO UPDATE SET state='DISMISSED',original_disposition=excluded.original_disposition,reason_code=excluded.reason_code,reason_label=excluded.reason_label,notes=excluded.notes,dismissed_by=excluded.dismissed_by,dismissed_at=excluded.dismissed_at,reopened_by=NULL,reopened_at=NULL,dismissal_fingerprint=excluded.dismissal_fingerprint,dismissal_snapshot=excluded.dismissal_snapshot,updated_at=excluded.updated_at",
                         (family_id,"DISMISSED",disposition,code,REASONS[code],notes or None,analyst,now,None,None,fp,json.dumps(snap,sort_keys=True),now))
            conn.execute("INSERT INTO wt_investigation_lifecycle_audit VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (str(uuid.uuid4()),family_id,"INVESTIGATION_DISMISSED",analyst,REASONS[code],notes or None,now,old[0] if old else disposition,"DISMISSED",fp,json.dumps(snap,sort_keys=True)))
            conn.commit()
        finally: conn.close()
        return apply_lifecycle_overlay(dict(family), self.path)

    def reopen(self, family_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        analyst = str(payload.get("analyst") or "").strip(); reason = str(payload.get("reason") or "").strip(); notes = str(payload.get("notes") or "").strip()
        if not analyst or not reason: raise InvestigationLifecycleError("Analyst and reopening reason are required.", "REOPEN_METADATA_REQUIRED")
        family = self.registry.get(family_id)
        lifecycle = read_lifecycle(self.path, family_id)
        if not family or not lifecycle or lifecycle["state"] != "DISMISSED": raise InvestigationLifecycleError("Investigation is not dismissed.", "NOT_DISMISSED", 409)
        snap = behaviour_snapshot(family); fp = fingerprint(snap); now = int(time.time())
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            conn.executescript(DDL)
            conn.execute("UPDATE wt_investigation_lifecycle SET state='REOPENED',reopened_by=?,reopened_at=?,updated_at=? WHERE family_id=?", (analyst,now,now,family_id))
            conn.execute("INSERT INTO wt_investigation_lifecycle_audit VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (str(uuid.uuid4()),family_id,"INVESTIGATION_REOPENED",analyst,reason,notes or None,now,"DISMISSED","REOPENED",fp,json.dumps(snap,sort_keys=True)))
            conn.commit()
        finally: conn.close()
        return apply_lifecycle_overlay(dict(family), self.path)
