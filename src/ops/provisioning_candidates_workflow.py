"""X67.3 — Provisioning Candidates investigation workflow.

Restores the WATCHTOWER Provisioning Candidates panel as an operational
investigation queue (X67.1), replacing the transient, wall-clock-decaying
`campaign == WATCHTOWER` admission gate (X65.93) with a persisted workflow
store and a verification methodology that runs as a background transition,
never as an admission gate.

Architectural invariant (unchanged by this module):
  - `wt_watchtower_launches`, `wt_confirmed_treasuries`, the walkback
    confirmation pipeline (`walkback_worker.py`), and the existing human-gated
    promotion route (`operation_dashboard_routes.py`'s
    `POST /api/ops-v2/intel/subprov-funder`) are Model 1. This module never
    writes to any of them. It only ever READS `wt_watchtower_launches` (to
    exclude already-confirmed launches) and `wt_confirmed_treasuries` (as the
    fixed, external ground truth a verified treasury must belong to).
  - Promotion into Model 1 remains a separate, human-gated action performed
    through the existing route. This module never performs that promotion
    itself -- `TREASURY_VERIFIED` is as far as this module's own writes go.

Workflow states (X67.1):
    CANDIDATE_DISCOVERED -> PENDING_VERIFICATION
        -> TREASURY_VERIFIED -> (existing human review) -> PROMOTED_TO_MODEL_1
        -> INVESTIGATION_CLOSED

Admission predicate (X66.0's reconstructed structural discovery predicate,
unchanged): fresh creator + session-cross-checked account-close provisioning
evidence + not already confirmed in Model 1. Verification (X65.97/98/99's
methodology, unchanged) is a TRANSITION between PENDING_VERIFICATION and
TREASURY_VERIFIED/INVESTIGATION_CLOSED, never an admission gate -- a launch
becomes visible in the panel the moment it is discovered, before any RPC call
is made.
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

DDL = """
CREATE TABLE IF NOT EXISTS wt_provisioning_candidate_workflow (
    mint                       TEXT PRIMARY KEY,
    workflow_state             TEXT NOT NULL CHECK(workflow_state IN (
                                   'PENDING_VERIFICATION',
                                   'TREASURY_VERIFIED',
                                   'INVESTIGATION_CLOSED',
                                   'PROMOTED_TO_MODEL_1'
                               )),
    discovered_at               INTEGER NOT NULL,
    updated_at                  INTEGER NOT NULL,
    creator                     TEXT,
    subprov_wallet              TEXT,
    funding_mechanism           TEXT CHECK(funding_mechanism IS NULL OR funding_mechanism IN (
                                   'WSOL_WRAP_CLOSE','SEEDED_ACCOUNT_CLOSE'
                               )),
    session_treasury            TEXT,
    verified_treasury           TEXT,
    selected_session_id         INTEGER,
    lineage_gap_seconds         INTEGER,
    verification_attempted_at   INTEGER,
    verification_outcome        TEXT CHECK(verification_outcome IS NULL OR verification_outcome IN (
                                   'PASS','FAIL','SKIPPED','ERROR'
                               )),
    closure_reason              TEXT CHECK(closure_reason IS NULL OR closure_reason IN (
                                   'EXCHANGE_BOUNDARY','MULTI_SOURCE_RELAY','TREASURY_MISMATCH',
                                   'WRONG_MECHANISM','INSUFFICIENT_EVIDENCE','UPSTREAM_NOT_RESOLVED',
                                   'OTHER_OPERATOR','MANUAL_REJECTION'
                               )),
    closure_note                TEXT,
    closure_actor                TEXT,
    evidence_json                TEXT,
    promoted_at                  INTEGER,
    attribution_source           TEXT,
    reconstructed                INTEGER NOT NULL DEFAULT 0 CHECK(reconstructed IN (0,1))
);
CREATE INDEX IF NOT EXISTS ix_wpcw_state ON wt_provisioning_candidate_workflow(workflow_state);
CREATE INDEX IF NOT EXISTS ix_wpcw_subprov ON wt_provisioning_candidate_workflow(subprov_wallet);
CREATE INDEX IF NOT EXISTS ix_wpcw_discovered ON wt_provisioning_candidate_workflow(discovered_at);
"""

VALID_STATES = frozenset({
    "PENDING_VERIFICATION", "TREASURY_VERIFIED", "INVESTIGATION_CLOSED", "PROMOTED_TO_MODEL_1",
})
VALID_CLOSURE_REASONS = frozenset({
    "EXCHANGE_BOUNDARY", "MULTI_SOURCE_RELAY", "TREASURY_MISMATCH", "WRONG_MECHANISM",
    "INSUFFICIENT_EVIDENCE", "UPSTREAM_NOT_RESOLVED", "OTHER_OPERATOR", "MANUAL_REJECTION",
})
VALID_MECHANISMS = frozenset({"WSOL_WRAP_CLOSE", "SEEDED_ACCOUNT_CLOSE"})

# Default maximum gap (seconds) between a session's own funding_time and the
# launch's wrap-close time for that session to be admissible corroboration.
# Empirically justified (X65.98): true positives observed at 64-123s; negative
# controls observed at 37-44 HOURS -- three orders of magnitude of separation,
# so this bound has wide safety margin without needing to be tuned per-wallet.
DEFAULT_MAX_LINEAGE_GAP_SECONDS = 6 * 3600


def ensure_schema(conn) -> None:
    conn.executescript(DDL)
    conn.commit()


def _table_exists(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


# ── Admission (X66.0's structural discovery predicate, unchanged) ───────────

def is_confirmed_in_model1(conn, mint: str) -> bool:
    """Model 1 read-only check. Never written to by this module."""
    if not _table_exists(conn, "wt_watchtower_launches"):
        return False
    return conn.execute(
        "SELECT 1 FROM wt_watchtower_launches WHERE mint=? LIMIT 1", (mint,)
    ).fetchone() is not None


def discover_candidate(
    conn, *, mint: str, creator: Optional[str], subprov_wallet: Optional[str],
    funding_mechanism: Optional[str], session_treasury: Optional[str] = None,
    now: Optional[int] = None,
) -> str:
    """Idempotent upsert. Admits `mint` into PENDING_VERIFICATION the moment
    the structural discovery predicate is satisfied -- verification is never a
    precondition for this call (X67.1). Returns the resulting workflow_state.

    A launch already confirmed in Model 1 (`wt_watchtower_launches`) or
    already present as a PROMOTED_TO_MODEL_1/TREASURY_VERIFIED/
    INVESTIGATION_CLOSED row is never reset back to PENDING_VERIFICATION by a
    repeated discovery call -- rediscovery is a no-op once a launch has left
    PENDING_VERIFICATION, satisfying the "previously closed rows are not
    automatically reopened" requirement without any extra flag.
    """
    ensure_schema(conn)
    now = now or int(time.time())

    if is_confirmed_in_model1(conn, mint):
        return "EXCLUDED_ALREADY_CONFIRMED"

    if funding_mechanism not in VALID_MECHANISMS:
        return "EXCLUDED_WRONG_MECHANISM"

    existing = conn.execute(
        "SELECT workflow_state FROM wt_provisioning_candidate_workflow WHERE mint=?", (mint,)
    ).fetchone()
    if existing:
        # Idempotent: already-discovered rows are never regressed to PENDING_VERIFICATION,
        # regardless of how many times the discovery predicate fires again.
        return existing[0] if not isinstance(existing, dict) else existing["workflow_state"]

    conn.execute(
        """INSERT INTO wt_provisioning_candidate_workflow
             (mint, workflow_state, discovered_at, updated_at, creator, subprov_wallet,
              funding_mechanism, session_treasury, reconstructed)
           VALUES (?, 'PENDING_VERIFICATION', ?, ?, ?, ?, ?, ?, 0)""",
        (mint, now, now, creator, subprov_wallet, funding_mechanism, session_treasury),
    )
    conn.commit()
    return "PENDING_VERIFICATION"


# ── Verification transition (X65.97/98/99 methodology, unchanged) ───────────

def select_nearest_eligible_session(conn, *, subprov_wallet: str, wrap_close_time: int):
    """X65.98's selection algorithm: the nearest session whose own funding_time
    strictly precedes the launch's wrap-close time. Returns the session row
    (as a dict) or None if no eligible session exists."""
    if not _table_exists(conn, "wt_active_subprov_sessions"):
        return None
    rows = conn.execute(
        "SELECT id, treasury_wallet, funding_signature, funding_amount, funding_time "
        "FROM wt_active_subprov_sessions WHERE subprov_wallet=? AND funding_time < ? "
        "ORDER BY funding_time DESC LIMIT 1",
        (subprov_wallet, wrap_close_time),
    ).fetchone()
    if rows is None:
        return None
    return dict(rows) if not isinstance(rows, dict) else rows


def verify_candidate(
    conn, *, mint: str, wrap_close_time: int, wrap_close_signature: Optional[str] = None,
    rpc_get_transaction=None, max_lineage_gap_seconds: int = DEFAULT_MAX_LINEAGE_GAP_SECONDS,
    now: Optional[int] = None, dry_run: bool = False,
) -> dict[str, Any]:
    """Runs the X65.97/98/99 corroboration predicate for one candidate and
    writes the resulting transition (TREASURY_VERIFIED or INVESTIGATION_CLOSED)
    -- never PROMOTED_TO_MODEL_1, which remains a separate, human-gated action
    performed through the existing promotion route.

    `rpc_get_transaction(signature) -> dict | None` is injected so this
    function makes zero RPC calls of its own when not supplied (safe for
    tests and dry runs) -- callers wire the live Helius RPC client in
    production. Fails closed (INVESTIGATION_CLOSED / INSUFFICIENT_EVIDENCE)
    on any missing, malformed, ambiguous, or contradictory evidence, and never
    raises out of this function for ordinary evidence gaps (only genuine
    programming errors propagate).
    """
    ensure_schema(conn)
    now = now or int(time.time())

    row = conn.execute(
        "SELECT * FROM wt_provisioning_candidate_workflow WHERE mint=?", (mint,)
    ).fetchone()
    if row is None:
        return {"mint": mint, "outcome": "ERROR", "reason": "NOT_FOUND"}
    row = dict(row)
    if row["workflow_state"] != "PENDING_VERIFICATION":
        return {"mint": mint, "outcome": "SKIPPED", "reason": "NOT_PENDING",
                "workflow_state": row["workflow_state"]}

    subprov_wallet = row.get("subprov_wallet")
    if not subprov_wallet:
        return _close(conn, mint, now, "INSUFFICIENT_EVIDENCE",
                       "no subprovider wallet resolved", dry_run=dry_run)

    selected = select_nearest_eligible_session(
        conn, subprov_wallet=subprov_wallet, wrap_close_time=wrap_close_time)
    if selected is None:
        return _close(conn, mint, now, "UPSTREAM_NOT_RESOLVED",
                       "no eligible preceding session found for this subprovider", dry_run=dry_run)

    gap = wrap_close_time - selected["funding_time"]
    if gap < 0 or gap > max_lineage_gap_seconds:
        return _close(conn, mint, now, "TREASURY_MISMATCH",
                       f"nearest session gap {gap}s exceeds bound {max_lineage_gap_seconds}s",
                       dry_run=dry_run, selected=selected, gap=gap)

    treasury = selected.get("treasury_wallet")
    if not treasury or not _table_exists(conn, "wt_confirmed_treasuries"):
        return _close(conn, mint, now, "TREASURY_MISMATCH",
                       "session treasury not present or wt_confirmed_treasuries missing",
                       dry_run=dry_run, selected=selected, gap=gap)
    is_confirmed_treasury = conn.execute(
        "SELECT 1 FROM wt_confirmed_treasuries WHERE treasury=? LIMIT 1", (treasury,)
    ).fetchone() is not None
    if not is_confirmed_treasury:
        return _close(conn, mint, now, "TREASURY_MISMATCH",
                       f"session treasury {treasury} not in wt_confirmed_treasuries",
                       dry_run=dry_run, selected=selected, gap=gap)

    funding_sig = selected.get("funding_signature")
    if not funding_sig:
        return _close(conn, mint, now, "INSUFFICIENT_EVIDENCE",
                       "session has no funding_signature to verify", dry_run=dry_run,
                       selected=selected, gap=gap)

    if rpc_get_transaction is None:
        # No verifier wired -- fail closed rather than assume. This is the
        # correct behaviour for a dry run and for any caller that has not
        # explicitly supplied live RPC access.
        return _close(conn, mint, now, "INSUFFICIENT_EVIDENCE",
                       "no RPC verifier available for this run", dry_run=dry_run,
                       selected=selected, gap=gap)

    tx = rpc_get_transaction(funding_sig)
    balance_delta_ok = _verify_balance_delta(tx, treasury=treasury, subprov=subprov_wallet)
    if balance_delta_ok is None:
        return _close(conn, mint, now, "INSUFFICIENT_EVIDENCE",
                       "transaction missing, malformed, or accounts not found in tx",
                       dry_run=dry_run, selected=selected, gap=gap)
    if balance_delta_ok is False:
        return _close(conn, mint, now, "TREASURY_MISMATCH",
                       "transaction does not confirm treasury as economic source / subprov as recipient",
                       dry_run=dry_run, selected=selected, gap=gap)

    evidence = {
        "treasury": treasury,
        "subprov_wallet": subprov_wallet,
        "treasury_to_subprov_signature": funding_sig,
        "funding_amount": selected.get("funding_amount"),
        "wrap_close_signature": wrap_close_signature,
        "wrap_close_time": wrap_close_time,
        "session_id": selected.get("id"),
        "lineage_gap_seconds": gap,
        "verified_at": now,
        "verification_method": "SESSION_HINT_RPC_VERIFIED",
    }
    if dry_run:
        return {"mint": mint, "outcome": "PASS", "would_transition_to": "TREASURY_VERIFIED",
                "evidence": evidence}

    conn.execute(
        "UPDATE wt_provisioning_candidate_workflow SET "
        "workflow_state='TREASURY_VERIFIED', updated_at=?, verified_treasury=?, "
        "selected_session_id=?, lineage_gap_seconds=?, verification_attempted_at=?, "
        "verification_outcome='PASS', evidence_json=?, attribution_source='SESSION_HINT_RPC_VERIFIED' "
        "WHERE mint=?",
        (now, treasury, selected.get("id"), gap, now, json.dumps(evidence), mint),
    )
    conn.commit()
    return {"mint": mint, "outcome": "PASS", "workflow_state": "TREASURY_VERIFIED", "evidence": evidence}


def _verify_balance_delta(tx: Optional[dict], *, treasury: str, subprov: str) -> Optional[bool]:
    """Confirms `treasury` shows a net balance decrease and `subprov` shows a
    net balance increase in `tx`. Returns None only when `tx` itself is
    missing/malformed (INSUFFICIENT_EVIDENCE) -- never assumes a pass on
    absent data. Returns False (TREASURY_MISMATCH) when the transaction is
    well-formed but does not involve the expected treasury/subprov pair at
    all, or the balance deltas run the wrong direction -- this is the case
    that catches a session-recorded treasury whose real funding transaction
    actually came from a different, unrelated wallet (e.g. an exchange)."""
    if not tx:
        return None
    meta = tx.get("meta") or {}
    pre, post = meta.get("preBalances"), meta.get("postBalances")
    accounts = (tx.get("transaction", {}).get("message", {}).get("accountKeys") or [])
    if not pre or not post or not accounts:
        return None
    keys = [a["pubkey"] if isinstance(a, dict) else a for a in accounts]
    if subprov not in keys:
        return None  # can't even locate the recipient -- malformed/unusable evidence
    if treasury not in keys:
        return False  # well-formed tx, but the expected treasury never appears in it
    t_idx = keys.index(treasury)
    s_idx = keys.index(subprov)
    treasury_delta = post[t_idx] - pre[t_idx]
    subprov_delta = post[s_idx] - pre[s_idx]
    return treasury_delta < 0 and subprov_delta > 0


def _close(conn, mint: str, now: int, reason: str, note: str, *, dry_run: bool,
           selected: Optional[dict] = None, gap: Optional[int] = None) -> dict[str, Any]:
    if reason not in VALID_CLOSURE_REASONS:
        reason = "OTHER_OPERATOR"
    evidence = {"note": note}
    if selected is not None:
        evidence["selected_session_id"] = selected.get("id")
        evidence["session_treasury"] = selected.get("treasury_wallet")
    if gap is not None:
        evidence["lineage_gap_seconds"] = gap
    if dry_run:
        return {"mint": mint, "outcome": "FAIL", "would_transition_to": "INVESTIGATION_CLOSED",
                "closure_reason": reason, "note": note}
    conn.execute(
        "UPDATE wt_provisioning_candidate_workflow SET "
        "workflow_state='INVESTIGATION_CLOSED', updated_at=?, verification_attempted_at=?, "
        "verification_outcome='FAIL', closure_reason=?, closure_note=?, evidence_json=? "
        "WHERE mint=?",
        (now, now, reason, note, json.dumps(evidence), mint),
    )
    conn.commit()
    return {"mint": mint, "outcome": "FAIL", "workflow_state": "INVESTIGATION_CLOSED",
            "closure_reason": reason, "note": note}


def close_manually(conn, *, mint: str, reason: str, actor: str, note: Optional[str] = None,
                    now: Optional[int] = None) -> dict[str, Any]:
    """Analyst-triggered closure -- requires an explicit reason and actor,
    distinct from the automated verification path above."""
    ensure_schema(conn)
    now = now or int(time.time())
    if reason not in VALID_CLOSURE_REASONS:
        return {"mint": mint, "outcome": "ERROR", "reason": "INVALID_CLOSURE_REASON"}
    row = conn.execute(
        "SELECT workflow_state FROM wt_provisioning_candidate_workflow WHERE mint=?", (mint,)
    ).fetchone()
    if row is None:
        return {"mint": mint, "outcome": "ERROR", "reason": "NOT_FOUND"}
    conn.execute(
        "UPDATE wt_provisioning_candidate_workflow SET "
        "workflow_state='INVESTIGATION_CLOSED', updated_at=?, closure_reason=?, "
        "closure_note=?, closure_actor=? WHERE mint=?",
        (now, reason, note, actor, mint),
    )
    conn.commit()
    return {"mint": mint, "outcome": "CLOSED", "workflow_state": "INVESTIGATION_CLOSED"}


# ── Model 1 promotion observation (never writes Model 1) ────────────────────

def sync_promoted_state(conn) -> int:
    """Read-only-with-respect-to-Model-1: for every TREASURY_VERIFIED row,
    checks whether Model 1's own promotion route (external to this module)
    has since confirmed the mint in `wt_watchtower_launches`, and if so marks
    this module's own row PROMOTED_TO_MODEL_1. Never writes
    `wt_watchtower_launches` itself -- only observes it. Returns the number of
    rows updated."""
    ensure_schema(conn)
    if not _table_exists(conn, "wt_watchtower_launches"):
        return 0
    now = int(time.time())
    rows = conn.execute(
        "SELECT mint FROM wt_provisioning_candidate_workflow WHERE workflow_state='TREASURY_VERIFIED'"
    ).fetchall()
    updated = 0
    for r in rows:
        mint = r[0] if not isinstance(r, dict) else r["mint"]
        if is_confirmed_in_model1(conn, mint):
            conn.execute(
                "UPDATE wt_provisioning_candidate_workflow SET "
                "workflow_state='PROMOTED_TO_MODEL_1', updated_at=?, promoted_at=? WHERE mint=?",
                (now, now, mint),
            )
            updated += 1
    if updated:
        conn.commit()
    return updated


# ── Query surface for the API layer ──────────────────────────────────────────

def list_candidates(conn, *, states: Optional[list[str]] = None,
                     funding_mechanism: Optional[str] = None) -> list[dict[str, Any]]:
    ensure_schema(conn)
    sync_promoted_state(conn)
    where = []
    params: list[Any] = []
    if states:
        where.append(f"workflow_state IN ({','.join('?' for _ in states)})")
        params.extend(states)
    if funding_mechanism:
        where.append("funding_mechanism=?")
        params.append(funding_mechanism)
    sql = "SELECT * FROM wt_provisioning_candidate_workflow"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY discovered_at DESC"
    rows = conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("evidence_json"):
            try:
                d["evidence"] = json.loads(d["evidence_json"])
            except (TypeError, ValueError):
                d["evidence"] = None
        out.append(d)
    return out
