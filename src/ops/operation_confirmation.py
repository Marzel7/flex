"""Durable analyst confirmation for Operation Registry families.

Confirmation is an overlay keyed by stable family_id.  It changes lifecycle
only: the discovery composer remains authoritative for identity, membership,
topology, evidence, statistics, and launch attribution.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS wt_operation_family_confirmations (
    family_id TEXT PRIMARY KEY,
    confirmed INTEGER NOT NULL DEFAULT 1 CHECK (confirmed IN (0,1)),
    confirmed_at INTEGER NOT NULL,
    confirmed_by TEXT NOT NULL,
    confirmation_reason TEXT NOT NULL,
    confirmation_notes TEXT,
    previous_lifecycle TEXT NOT NULL,
    reversed_at INTEGER,
    reversed_by TEXT,
    reversal_reason TEXT,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS wt_operation_family_confirmation_audit (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    family_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('CONFIRMED','REVERSED')),
    action_at INTEGER NOT NULL,
    action_by TEXT NOT NULL,
    reason TEXT NOT NULL,
    notes TEXT,
    previous_lifecycle TEXT NOT NULL,
    resulting_lifecycle TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_operation_confirmation_audit_family
ON wt_operation_family_confirmation_audit(family_id, action_at);
"""


class ConfirmationError(ValueError):
    def __init__(self, message: str, code: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def readiness_for(family: dict[str, Any]) -> dict[str, Any]:
    cohesion = family.get("cohesion") or {}
    evidence = family.get("evidence_completeness") or {}
    maturity = family.get("operational_maturity") or {}
    checks = [
        ("stable_family_identity", "Stable family identity", bool(family.get("family_id") and family.get("family_anchor"))),
        ("topology_reconstructed", "Topology reconstructed", family.get("dominant_topology") != "Evidence accumulation incomplete"),
        ("treasury_relationships", "Treasury relationships established", bool(family.get("treasuries"))),
        ("infrastructure_understood", "Infrastructure understood", bool(family.get("member_wallets") and family.get("funding_mechanisms"))),
        ("behaviour_consistent", "Behaviour consistent", not bool(family.get("contradictions"))),
        ("observation_history", "Sufficient observation history", int(family.get("observed_launches") or 0) >= 2),
        ("evidence_package", "Evidence package complete", int(evidence.get("score") or 0) >= 50),
        ("critical_conflicts", "No unresolved critical conflicts", not bool(
            family.get("exclusion_evidence") or cohesion.get("conflicts")
        )),
    ]
    checklist = [{"key": key, "label": label, "met": met} for key, label, met in checks]
    issues = [item["label"] for item in checklist if not item["met"]]
    issues.extend(str(x) for x in family.get("blocking_reasons") or [] if str(x) not in issues)
    return {
        "ready": all(item["met"] for item in checklist),
        "analyst_decision_required": True,
        "checklist": checklist,
        "outstanding_issues": issues,
        "evidence_coverage": int(evidence.get("score") or 0),
        "operational_maturity": int(maturity.get("score") or 0),
        "explanation": "Evidence coverage informs confirmation readiness but never confirms an operation automatically.",
    }


def _read_rows(path: str, family_id: str | None = None) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    confirmations: dict[str, dict[str, Any]] = {}
    audit: dict[str, list[dict[str, Any]]] = {}
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "wt_operation_family_confirmations" in tables:
            where, args = (" WHERE family_id=?", (family_id,)) if family_id else ("", ())
            for row in conn.execute("SELECT * FROM wt_operation_family_confirmations" + where, args):
                confirmations[row["family_id"]] = dict(row)
        if "wt_operation_family_confirmation_audit" in tables:
            where, args = (" WHERE family_id=?", (family_id,)) if family_id else ("", ())
            for row in conn.execute(
                "SELECT * FROM wt_operation_family_confirmation_audit" + where + " ORDER BY action_at, audit_id", args
            ):
                audit.setdefault(row["family_id"], []).append(dict(row))
        conn.close()
    except (OSError, sqlite3.Error):
        pass
    return confirmations, audit


def apply_confirmation_overlay(families: list[dict[str, Any]], path: str) -> None:
    confirmations, audits = _read_rows(path)
    for family in families:
        family_id = family.get("family_id")
        row = confirmations.get(family_id)
        history = audits.get(family_id, [])
        family["confirmation_readiness"] = readiness_for(family)
        family["confirmation"] = {
            "confirmed": bool(row and row.get("confirmed")),
            "confirmed_at": row.get("confirmed_at") if row else None,
            "confirmed_by": row.get("confirmed_by") if row else None,
            "confirmation_reason": row.get("confirmation_reason") if row else None,
            "confirmation_notes": row.get("confirmation_notes") if row else None,
            "previous_lifecycle": row.get("previous_lifecycle") if row else None,
            "reversed_at": row.get("reversed_at") if row else None,
            "reversed_by": row.get("reversed_by") if row else None,
            "reversal_reason": row.get("reversal_reason") if row else None,
            "history": history,
        }
        if row and row.get("confirmed"):
            previous = family["stage"]
            family["stage"] = family["lifecycle_state"] = "CONFIRMED"
            family["status"] = family["review_label"] = "Confirmed"
            family["review_status"] = family["promotion_status"] = "CONFIRMED"
            family["state_changed_at"] = row["confirmed_at"]
            family["previous_stage"] = row.get("previous_lifecycle") or previous
            event = {
                "event_type": "OPERATION_CONFIRMED", "type": "OPERATION_CONFIRMED",
                "timestamp": row["confirmed_at"], "day": 1,
                "label": f"Operation confirmed by {row['confirmed_by']}",
                "source": "wt_operation_family_confirmation_audit",
                "observation_count": family.get("observed_launches") or 0,
            }
            for key in ("discovery_timeline", "evidence_timeline", "growth_timeline"):
                if not any(x.get("event_type") == "OPERATION_CONFIRMED" for x in family.get(key) or []):
                    family.setdefault(key, []).append(dict(event))
                    family[key].sort(key=lambda x: (x.get("timestamp") or 0, x.get("event_type") or ""))


class OperationConfirmationService:
    def __init__(self, ops_db_path: str, registry: Any) -> None:
        self.ops_db_path = ops_db_path
        self.registry = registry

    def confirm(self, family_id: str, *, analyst: str, reason: str, notes: str = "") -> dict[str, Any]:
        analyst, reason = str(analyst or "").strip(), str(reason or "").strip()
        if not analyst or not reason:
            raise ConfirmationError("analyst and confirmation reason are required", "MISSING_CONFIRMATION_METADATA")
        family = self.registry.get(family_id)
        if not family:
            raise ConfirmationError("Operation family not found", "FAMILY_NOT_FOUND", 404)
        if family["lifecycle_state"] not in {"EMERGING", "CONFIRMED"}:
            raise ConfirmationError("Only an Emerging Operation can be confirmed", "INVALID_LIFECYCLE", 409)
        if family["lifecycle_state"] == "CONFIRMED":
            if (family.get("confirmation") or {}).get("confirmed"):
                return family
            raise ConfirmationError("Canonical confirmed operations do not require this workflow", "ALREADY_CONFIRMED", 409)
        now = int(time.time())
        conn = sqlite3.connect(self.ops_db_path, timeout=10)
        try:
            conn.executescript(SCHEMA)
            existing = conn.execute(
                "SELECT confirmed FROM wt_operation_family_confirmations WHERE family_id=?", (family_id,)
            ).fetchone()
            if existing and existing[0]:
                raise ConfirmationError("Operation is already confirmed", "ALREADY_CONFIRMED", 409)
            conn.execute(
                "INSERT INTO wt_operation_family_confirmations "
                "(family_id,confirmed,confirmed_at,confirmed_by,confirmation_reason,confirmation_notes,previous_lifecycle,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(family_id) DO UPDATE SET "
                "confirmed=1,confirmed_at=excluded.confirmed_at,confirmed_by=excluded.confirmed_by,"
                "confirmation_reason=excluded.confirmation_reason,confirmation_notes=excluded.confirmation_notes,"
                "previous_lifecycle=excluded.previous_lifecycle,reversed_at=NULL,reversed_by=NULL,reversal_reason=NULL,updated_at=excluded.updated_at",
                (family_id, 1, now, analyst, reason, notes or None, family["lifecycle_state"], now),
            )
            conn.execute(
                "INSERT INTO wt_operation_family_confirmation_audit "
                "(family_id,action,action_at,action_by,reason,notes,previous_lifecycle,resulting_lifecycle) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (family_id, "CONFIRMED", now, analyst, reason, notes or None, family["lifecycle_state"], "CONFIRMED"),
            )
            conn.commit()
        finally:
            conn.close()
        self.registry._cached_families = None
        from src.ops.operation_attribution import clear_operation_attribution_cache
        clear_operation_attribution_cache(self.ops_db_path)
        return self.registry.get(family_id)

    def reverse(self, family_id: str, *, analyst: str, reason: str, notes: str = "") -> dict[str, Any]:
        analyst, reason = str(analyst or "").strip(), str(reason or "").strip()
        if not analyst or not reason:
            raise ConfirmationError("analyst and reversal reason are required", "MISSING_REVERSAL_METADATA")
        confirmations, _ = _read_rows(self.ops_db_path, family_id)
        row = confirmations.get(family_id)
        if not row or not row.get("confirmed"):
            raise ConfirmationError("Operation is not analyst-confirmed", "NOT_CONFIRMED", 409)
        now = int(time.time())
        conn = sqlite3.connect(self.ops_db_path, timeout=10)
        try:
            conn.executescript(SCHEMA)
            conn.execute(
                "UPDATE wt_operation_family_confirmations SET confirmed=0,reversed_at=?,reversed_by=?,reversal_reason=?,updated_at=? WHERE family_id=?",
                (now, analyst, reason, now, family_id),
            )
            conn.execute(
                "INSERT INTO wt_operation_family_confirmation_audit "
                "(family_id,action,action_at,action_by,reason,notes,previous_lifecycle,resulting_lifecycle) VALUES (?,?,?,?,?,?,?,?)",
                (family_id, "REVERSED", now, analyst, reason, notes or None, "CONFIRMED", row["previous_lifecycle"]),
            )
            conn.commit()
        finally:
            conn.close()
        self.registry._cached_families = None
        from src.ops.operation_attribution import clear_operation_attribution_cache
        clear_operation_attribution_cache(self.ops_db_path)
        return self.registry.get(family_id)
