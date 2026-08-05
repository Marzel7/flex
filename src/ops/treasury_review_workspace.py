"""X74.1 Treasury Review & Identity Expansion Workspace.

Treasury review is an analyst investigation workflow, not an approve/reject
queue. This module is the single service surface for it: it composes
analyst-readable evidence for each `wt_treasury_review` row, exposes the
review actions, and bridges an "Approve Treasury" decision into the existing
canonical write paths — `treasury_bank.promote_to_confirmed()` (the
authoritative confirmed-set writer) and, when the analyst is expanding an
already-CONFIRMED Operator Identity such as WATCHTOWER,
`OperatorIdentityGovernanceService.expand()` (the immutable identity-event
ledger). It does not replace either of those; it is presentation +
orchestration on top of what already exists.

Every mutating action is recorded in `wt_treasury_review_actions`, an
immutable (trigger-protected) append-only audit log, mirroring the
`operator_identity_events` convention.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

try:
    from src.utils.db_locking import db_connect
except Exception:                                        # pragma: no cover
    import sqlite3
    def db_connect(path, timeout=30):
        c = sqlite3.connect(path, timeout=timeout)
        c.row_factory = sqlite3.Row
        return c

from src.core import treasury_bank
from src.ops.watchtower_alignment import WATCHTOWER_OPERATOR_ID


AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS wt_treasury_review_actions (
    action_id TEXT PRIMARY KEY,
    treasury TEXT NOT NULL,
    action TEXT NOT NULL,
    analyst TEXT NOT NULL,
    reason TEXT NOT NULL,
    evidence_revision TEXT NOT NULL,
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_wtra_treasury
ON wt_treasury_review_actions(treasury, created_at);
CREATE TRIGGER IF NOT EXISTS wt_treasury_review_actions_immutable_update
BEFORE UPDATE ON wt_treasury_review_actions BEGIN
 SELECT RAISE(ABORT, 'treasury review action history is immutable');
END;
CREATE TRIGGER IF NOT EXISTS wt_treasury_review_actions_immutable_delete
BEFORE DELETE ON wt_treasury_review_actions BEGIN
 SELECT RAISE(ABORT, 'treasury review action history is immutable');
END;
"""

ACTIONS = frozenset({
    "APPROVE_TREASURY", "REJECT_TREASURY", "NEEDS_MORE_EVIDENCE",
    "LINK_TO_OPERATOR", "CREATE_INVESTIGATION", "CREATE_OPERATOR_CANDIDATE",
})


class WorkspaceError(ValueError):
    def __init__(self, message: str, code: str = "INVALID_ACTION", status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


def ensure_schema(conn) -> None:
    conn.executescript(AUDIT_DDL)
    conn.commit()


def _load_json(value) -> list:
    try:
        return json.loads(value) if value else []
    except Exception:
        return []


def _related_identity(conn, treasury: str) -> dict[str, Any]:
    """Determine what this treasury is already associated with, if anything."""
    row = conn.execute(
        "SELECT oe.operator_id, o.display_name, o.status "
        "FROM operator_entities oe JOIN operators o ON o.operator_id=oe.operator_id "
        "WHERE oe.entity_address=? AND o.status<>'REJECTED' "
        "ORDER BY (oe.operator_id=?) DESC LIMIT 1",
        (treasury, WATCHTOWER_OPERATOR_ID),
    ).fetchone()
    if row:
        return {
            "kind": "WATCHTOWER" if row["operator_id"] == WATCHTOWER_OPERATOR_ID else "OPERATION",
            "operator_id": row["operator_id"],
            "display_name": row["display_name"],
            "status": row["status"],
        }
    if conn.execute("SELECT 1 FROM wt_discovered_subprovs WHERE treasury=? LIMIT 1", (treasury,)).fetchone():
        return {"kind": "INFRASTRUCTURE", "operator_id": None, "display_name": None, "status": None}
    return {"kind": "UNKNOWN", "operator_id": None, "display_name": None, "status": None}


def _evidence_summary(row: dict[str, Any], counts: dict[str, int]) -> dict[str, Any]:
    """Analyst-readable — no raw SQL/column dumps."""
    supporting: list[str] = []
    contradictions: list[str] = []
    missing: list[str] = []

    transfer_pct = row.get("transfer_pct")
    out_sol = row.get("out_sol") or 0
    recipients = row.get("recipients") or 0
    micro_pings = row.get("micro_pings") or 0
    detected_via = row.get("detected_via") or "unknown"

    why = f"Surfaced via {detected_via.replace('_', ' ')}"
    if row.get("has_walkback_evidence"):
        why += "; confirmed walkback lineage reached this wallet as an unresolved upstream hop."
    else:
        why += "."

    if transfer_pct is not None:
        if transfer_pct >= 95:
            supporting.append(f"{transfer_pct}% of observed activity is plain transfers (treasury-shaped, not a trading wallet).")
        else:
            contradictions.append(f"Only {transfer_pct}% transfer-pure — some swap/trade activity observed, atypical for a treasury.")
    else:
        missing.append("No transfer-purity fingerprint recorded yet.")

    if out_sol >= 50 and recipients >= 3:
        supporting.append(f"Distributed {out_sol:.0f} SOL to {recipients} recipients — matches treasury capital scale.")
    elif recipients:
        missing.append(f"Capital scale below the usual treasury threshold ({out_sol:.0f} SOL to {recipients} recipients).")
    elif out_sol:
        missing.append(f"{out_sol:.0f} SOL of funding observed, but no distributed-recipient breadth recorded yet (recipient count not measured for walkback-sourced leads).")
    else:
        missing.append("No capital-scale signal recorded.")

    if micro_pings:
        supporting.append(f"Carries the coordination-ping signature ({micro_pings} observed).")
    else:
        missing.append("No coordination-ping signature observed yet.")

    if counts.get("wrap_close", 0):
        supporting.append(f"{counts['wrap_close']} wrap-close observation(s) trace lineage back to this wallet.")
    if counts.get("subprovs", 0):
        supporting.append(f"{counts['subprovs']} distinct sub-provisioner(s) reachable downstream of this wallet.")
    if counts.get("launches", 0):
        supporting.append(f"{counts['launches']} token launch(es) attributable through this lineage.")
    else:
        missing.append("No completed launch has yet traced back to this wallet.")

    return {
        "why_proposed": why,
        "supporting_evidence": supporting,
        "contradictions": contradictions,
        "missing_evidence": missing,
    }


def _counts_for_treasury(conn, treasury: str, evidence_subprovs: list[str], evidence_mints: list[str]) -> dict[str, int]:
    wrap_close = 0
    try:
        wrap_close = conn.execute(
            "SELECT COUNT(*) FROM wt_wrap_close_candidates WHERE lineage_source_treasury=?",
            (treasury,),
        ).fetchone()[0]
    except Exception:
        pass
    subprovs = len(evidence_subprovs) if evidence_subprovs else 0
    try:
        row = conn.execute(
            "SELECT COUNT(DISTINCT subprov) FROM wt_discovered_subprovs WHERE treasury=?",
            (treasury,),
        ).fetchone()
        if row and row[0]:
            subprovs = max(subprovs, row[0])
    except Exception:
        pass
    return {"wrap_close": wrap_close, "subprovs": subprovs, "launches": len(evidence_mints)}


def _priority_score(row: dict[str, Any], counts: dict[str, int]) -> float:
    """Analyst-value ranking, not age. Weighted toward what would most likely
    expand WATCHTOWER: launch evidence > wrap-close observations > subprov
    breadth > walkback presence > raw capital scale > recency."""
    score = 0.0
    score += counts.get("launches", 0) * 100
    score += counts.get("wrap_close", 0) * 20
    score += counts.get("subprovs", 0) * 10
    score += 15 if row.get("has_walkback_evidence") else 0
    score += min((row.get("out_sol") or 0) / 10, 20)
    score += 5 if (row.get("distinct_creators") or 1) > 1 else 0
    return score


def compose_review_item(conn, row: dict[str, Any]) -> dict[str, Any]:
    treasury = row["treasury"]
    evidence_subprovs = _load_json(row.get("evidence_subprovs"))
    evidence_creators = _load_json(row.get("evidence_creators"))
    evidence_mints = _load_json(row.get("evidence_mints"))
    counts = _counts_for_treasury(conn, treasury, evidence_subprovs, evidence_mints)
    identity = _related_identity(conn, treasury)
    evidence = _evidence_summary(row, counts)
    recent_actions = [dict(r) for r in conn.execute(
        "SELECT action, analyst, reason, created_at FROM wt_treasury_review_actions "
        "WHERE treasury=? ORDER BY created_at DESC LIMIT 5", (treasury,),
    ).fetchall()] if _table_exists(conn, "wt_treasury_review_actions") else []
    return {
        "treasury": treasury,
        "status": row.get("status"),
        "evidence_summary": evidence,
        "walkback_depth": max(len(evidence_subprovs), 1 if row.get("has_walkback_evidence") else 0),
        "subprovider_count": counts["subprovs"],
        "wrap_close_observations": counts["wrap_close"],
        "launch_count": counts["launches"],
        "investigation_population": {
            "subprovs": evidence_subprovs,
            "creators": evidence_creators,
            "mints": evidence_mints,
        },
        "related_identity": identity,
        "first_observed": row.get("detected_at"),
        "last_observed": row.get("last_walkback_at") or row.get("detected_at"),
        "recent_actions": recent_actions,
        "priority_score": _priority_score(row, counts),
        "watchtower_candidate": counts["wrap_close"] > 0 or counts["launches"] > 0 or bool(row.get("has_walkback_evidence")),
        # X75.3A PART 6 -- navigation: every review item links forward to
        # Discovery's per-entity view for this wallet, so an analyst never
        # has to leave the review workspace to see the same wallet's
        # canonical identity / structural-population context.
        "discovery_href": f"/discovery?entity={treasury}&type=treasury",
    }


def _table_exists(conn, table: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone())


def list_review_workspace(conn, *, status: str = "PENDING_REVIEW", sort: str = "priority", limit: int = 200) -> dict[str, Any]:
    ensure_schema(conn)
    treasury_bank._ensure_schema_once(conn)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM wt_treasury_review WHERE status=? ORDER BY detected_at DESC LIMIT ?",
        (status, max(limit, 1) * 3),
    ).fetchall()]
    items = [compose_review_item(conn, row) for row in rows]
    if sort == "newest":
        items.sort(key=lambda i: i["first_observed"] or 0, reverse=True)
    elif sort == "oldest":
        items.sort(key=lambda i: i["first_observed"] or 0)
    else:
        items.sort(key=lambda i: i["priority_score"], reverse=True)
    items = items[:limit]

    counts_row = conn.execute(
        "SELECT status, COUNT(*) FROM wt_treasury_review GROUP BY status"
    ).fetchall()
    status_counts = {r[0]: r[1] for r in counts_row}
    ages = [r[0] for r in conn.execute(
        "SELECT detected_at FROM wt_treasury_review WHERE status='PENDING_REVIEW' AND detected_at IS NOT NULL"
    ).fetchall()]
    now = int(time.time())
    watchtower_candidates = sum(1 for i in items if i["watchtower_candidate"])

    return {
        "items": items,
        "status_counts": status_counts,
        "pending_total": status_counts.get("PENDING_REVIEW", 0),
        "newest_pending_age_secs": (now - max(ages)) if ages else None,
        "oldest_pending_age_secs": (now - min(ages)) if ages else None,
        "watchtower_candidates": watchtower_candidates,
        "requires_attention": status_counts.get("PENDING_REVIEW", 0) > 0,
        "generated_at": now,
    }


def _record_action(conn, treasury: str, action: str, analyst: str, reason: str,
                   evidence_revision: str, result: dict[str, Any]) -> str:
    action_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO wt_treasury_review_actions "
        "(action_id, treasury, action, analyst, reason, evidence_revision, result_json, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (action_id, treasury, action, analyst, reason, evidence_revision, json.dumps(result), int(time.time())),
    )
    return action_id


def _metadata(payload: dict[str, Any]) -> tuple[str, str, str]:
    analyst = str(payload.get("analyst") or "").strip()
    reason = str(payload.get("reason") or "").strip()
    revision = str(payload.get("evidence_revision") or f"treasury-review:{int(time.time())}").strip()
    if not analyst or not reason:
        raise WorkspaceError("analyst and reason are required", "ACTION_METADATA_REQUIRED")
    return analyst, reason, revision


def approve_treasury(conn, treasury: str, payload: dict[str, Any], *,
                     governance_service=None) -> dict[str, Any]:
    """Approve a treasury. If the analyst names a target Operator Identity
    (payload['operator_id'], defaulting to WATCHTOWER), the treasury is both
    (a) promoted into the authoritative confirmed set via
    treasury_bank.promote_to_confirmed — which already reconciles ownership
    into operator_entities for WATCHTOWER — and (b) recorded as an explicit
    identity-expansion event via OperatorIdentityGovernanceService.expand,
    so the immutable identity ledger carries this decision too. Never
    recreates or duplicates the Operator; expand() is itself idempotent
    (INSERT OR IGNORE on the asset, always appends a new event)."""
    ensure_schema(conn)
    analyst, reason, revision = _metadata(payload)
    operator_id = str(payload.get("operator_id") or WATCHTOWER_OPERATOR_ID).strip()

    promotion = treasury_bank.promote_to_confirmed(conn, treasury, reviewed_by=analyst)
    if not promotion.get("ok"):
        raise WorkspaceError(promotion.get("error", "promotion failed"), "PROMOTION_FAILED", 409)

    expansion = None
    if operator_id:
        gov = governance_service
        if gov is None:
            from src.core.db import OPS_DB_PATH
            from src.ops.operator_identity_governance import OperatorIdentityGovernanceService
            gov = OperatorIdentityGovernanceService(str(OPS_DB_PATH))
        try:
            expansion = gov.expand(operator_id, {
                "analyst": analyst, "reason": reason, "evidence_revision": revision,
                "asset_type": "TREASURY", "asset_value": treasury,
            })
        except Exception as exc:
            # Promotion (authoritative) already succeeded; identity-ledger
            # expansion is additive and must never roll that back.
            expansion = {"error": str(exc)}

    result = {"promotion": promotion, "identity_expansion": expansion}
    _record_action(conn, treasury, "APPROVE_TREASURY", analyst, reason, revision, result)
    conn.commit()
    return {"ok": True, "treasury": treasury, **result}


def reject_treasury(conn, treasury: str, payload: dict[str, Any]) -> dict[str, Any]:
    ensure_schema(conn)
    analyst, reason, revision = _metadata(payload)
    result = treasury_bank.reject_candidate(conn, treasury, reviewed_by=analyst)
    _record_action(conn, treasury, "REJECT_TREASURY", analyst, reason, revision, result)
    conn.commit()
    return {"ok": True, "treasury": treasury, **result}


def needs_more_evidence(conn, treasury: str, payload: dict[str, Any]) -> dict[str, Any]:
    ensure_schema(conn)
    analyst, reason, revision = _metadata(payload)
    if not conn.execute("SELECT 1 FROM wt_treasury_review WHERE treasury=?", (treasury,)).fetchone():
        raise WorkspaceError("not in review queue", "NOT_FOUND", 404)
    # Stays PENDING_REVIEW — this is an annotation, not a status transition.
    result = {"noted": True}
    _record_action(conn, treasury, "NEEDS_MORE_EVIDENCE", analyst, reason, revision, result)
    conn.commit()
    return {"ok": True, "treasury": treasury, **result}


def link_to_existing_operator(conn, treasury: str, payload: dict[str, Any], *,
                              governance_service=None) -> dict[str, Any]:
    """Attach this treasury as evidence for a NON-WATCHTOWER confirmed
    Operator — i.e. this treasury belongs to a different, already-known
    Operation, not a rejection and not WATCHTOWER."""
    ensure_schema(conn)
    analyst, reason, revision = _metadata(payload)
    operator_id = str(payload.get("operator_id") or "").strip()
    if not operator_id:
        raise WorkspaceError("operator_id is required", "OPERATOR_ID_REQUIRED")
    gov = governance_service
    if gov is None:
        from src.core.db import OPS_DB_PATH
        from src.ops.operator_identity_governance import OperatorIdentityGovernanceService
        gov = OperatorIdentityGovernanceService(str(OPS_DB_PATH))
    expansion = gov.expand(operator_id, {
        "analyst": analyst, "reason": reason, "evidence_revision": revision,
        "asset_type": "TREASURY", "asset_value": treasury,
    })
    conn.execute(
        "UPDATE wt_treasury_review SET status='LINKED', reviewed_by=?, reviewed_at=? WHERE treasury=?",
        (analyst, int(time.time()), treasury),
    )
    result = {"identity_expansion": expansion}
    _record_action(conn, treasury, "LINK_TO_OPERATOR", analyst, reason, revision, result)
    conn.commit()
    return {"ok": True, "treasury": treasury, **result}


def create_investigation(conn, treasury: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Evidence supports something worth tracking, but not yet an Operator
    Identity confirmation or expansion. Leaves the treasury visibly flagged
    (status=INVESTIGATING) without forcing it into WATCHTOWER or any
    existing Operation."""
    ensure_schema(conn)
    analyst, reason, revision = _metadata(payload)
    if not conn.execute("SELECT 1 FROM wt_treasury_review WHERE treasury=?", (treasury,)).fetchone():
        raise WorkspaceError("not in review queue", "NOT_FOUND", 404)
    conn.execute(
        "UPDATE wt_treasury_review SET status='INVESTIGATING', reviewed_by=?, reviewed_at=? WHERE treasury=?",
        (analyst, int(time.time()), treasury),
    )
    result = {"status": "INVESTIGATING"}
    _record_action(conn, treasury, "CREATE_INVESTIGATION", analyst, reason, revision, result)
    conn.commit()
    return {"ok": True, "treasury": treasury, **result}


def create_operator_candidate(conn, treasury: str, payload: dict[str, Any], *,
                              governance_service=None) -> dict[str, Any]:
    """Evidence supports a NEW Operation rather than an existing one or
    WATCHTOWER. Creates a fresh CANDIDATE operator seeded with this treasury
    as its first asset, via the existing identity-governance machinery
    (ensure_state + expand), never duplicating an existing operator.

    The `operators` insert is committed on `conn` BEFORE the governance
    service touches it: OperatorIdentityGovernanceService writes through
    database_write_service, which opens its own dedicated connection to the
    ops DB file — it cannot see an uncommitted row from `conn`'s own
    transaction."""
    ensure_schema(conn)
    analyst, reason, revision = _metadata(payload)
    display_name = str(payload.get("display_name") or f"Candidate Operation — {treasury[:8]}").strip()
    now = int(time.time())
    operator_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"treasury-review-candidate:{treasury}"))
    existing = conn.execute("SELECT 1 FROM operators WHERE operator_id=?", (operator_id,)).fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO operators(operator_id,status,confidence,first_seen,last_seen,summary,"
            "review_state,display_name,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (operator_id, "CANDIDATE", "REVIEW", now, now,
             f"Candidate operation seeded from treasury review: {reason}",
             "NEEDS_REVIEW", display_name, now, now),
        )
        conn.commit()
    gov = governance_service
    if gov is None:
        from src.core.db import OPS_DB_PATH
        from src.ops.operator_identity_governance import OperatorIdentityGovernanceService
        gov = OperatorIdentityGovernanceService(str(OPS_DB_PATH))
    gov._mutate("operator-identity-ensure-candidate", lambda c: gov._ensure_state(c, operator_id))
    conn.execute(
        "UPDATE wt_treasury_review SET status='LINKED', reviewed_by=?, reviewed_at=? WHERE treasury=?",
        (analyst, now, treasury),
    )
    result = {"operator_id": operator_id, "display_name": display_name}
    _record_action(conn, treasury, "CREATE_OPERATOR_CANDIDATE", analyst, reason, revision, result)
    conn.commit()
    return {"ok": True, "treasury": treasury, **result}


DISPATCH = {
    "APPROVE_TREASURY": approve_treasury,
    "REJECT_TREASURY": reject_treasury,
    "NEEDS_MORE_EVIDENCE": needs_more_evidence,
    "LINK_TO_OPERATOR": link_to_existing_operator,
    "CREATE_INVESTIGATION": create_investigation,
    "CREATE_OPERATOR_CANDIDATE": create_operator_candidate,
}


def perform_action(conn, treasury: str, action: str, payload: dict[str, Any]) -> dict[str, Any]:
    action = str(action or "").upper()
    if action not in DISPATCH:
        raise WorkspaceError(f"Unsupported action: {action}", "UNSUPPORTED_ACTION")
    return DISPATCH[action](conn, treasury, payload)
