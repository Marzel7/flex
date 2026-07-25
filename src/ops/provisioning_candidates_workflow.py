"""X67.3/X67.7 — Provisioning Candidates investigation workflow.

Restores the WATCHTOWER Provisioning Candidates panel as an operational
investigation queue (X67.1), replacing the transient, wall-clock-decaying
`campaign == WATCHTOWER` admission gate (X65.93) with a persisted workflow
store and a verification methodology that runs as a background transition,
never as an admission gate.

Architectural invariant (unchanged by this module):
  - `wt_watchtower_launches`, `wt_confirmed_treasuries`, the walkback
    confirmation pipeline (`walkback_worker.py`), and the existing canonical
    promotion writer (`watchtower_registry_promotion.promote_walkback_
    confirmed_watchtower`, invoked directly by this module -- see
    `evaluate_candidate_for_canonical_promotion`/`promote_eligible_candidate`
    below) are Model 1. This module never redefines Model 1's own
    confirmation predicate or writes `wt_watchtower_launches` through any
    path other than that existing writer. It only ever READS
    `wt_watchtower_launches` (to exclude already-confirmed launches) and
    `wt_confirmed_treasuries` (as the fixed, external ground truth a verified
    treasury must belong to).

X67.7 architectural change (per X67.5/X67.6's finding that TREASURY_VERIFIED
carries no evidential content beyond what verification itself already
proves): `TREASURY_VERIFIED` is REMOVED as a durable destination state.
Verification is now an action that, on success, immediately evaluates the
full canonical promotion predicate (identity/role separation, treasury
corroboration, canonical mechanism, canonical topology, no conflicting
evidence, required registry fields) and promotes through the EXISTING
canonical writer when-and-only-when every condition holds. A verified
treasury edge alone is necessary but never sufficient for promotion -- see
`evaluate_candidate_for_canonical_promotion`, the one authoritative decision
path (Gate: "never promote on treasury verification alone").

Workflow states (X67.7, superseding X67.1):
    CANDIDATE_DISCOVERED -> PENDING_VERIFICATION
        -> PROMOTED_TO_MODEL_1   (verification succeeds AND full predicate passes)
        -> INVESTIGATION_CLOSED  (verification fails, OR predicate rejects with
                                   a structural/evidence-conflict reason)

`TREASURY_VERIFIED` remains a valid value in the `workflow_state` CHECK
constraint and in `VALID_STATES` SOLELY so pre-X67.7 rows (this module's own
prior backfill) remain readable -- no code path in this module writes that
value for a new transition after this change. `reconcile_legacy_treasury_verified`
migrates any surviving legacy rows through the exact same evaluator new
candidates use, per the task's explicit "do not special-case by mint" rule.

Admission predicate (X66.0's reconstructed structural discovery predicate,
unchanged): fresh creator + session-cross-checked account-close provisioning
evidence + not already confirmed in Model 1. A launch becomes visible in the
panel the moment it is discovered, before any RPC call is made.
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
                                   'PROMOTED_TO_MODEL_1',
                                   'SHARED_PROVISIONING_INTELLIGENCE'
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
                                   'OTHER_OPERATOR','MANUAL_REJECTION','ROLE_SEPARATION_FAILED',
                                   'TREASURY_NOT_CONFIRMED','TREASURY_EDGE_MISSING',
                                   'TREASURY_EDGE_UNDECODABLE','TREASURY_BALANCE_DELTA_FAILED',
                                   'TEMPORAL_ORDER_FAILED','NON_CANONICAL_FUNDING_MECHANISM',
                                   'MECHANISM_EVIDENCE_CONFLICT','CLOSE_DESTINATION_UNVERIFIED',
                                   'SHARED_RELAY_TOPOLOGY','SUBPROVIDER_REUSE_CONFLICT',
                                   'LINEAGE_CONFLICT','NON_CANONICAL_TOPOLOGY','EVIDENCE_CONFLICT',
                                   'MISSING_REQUIRED_FIELD'
                               )),
    closure_note                TEXT,
    closure_actor                TEXT,
    evidence_json                TEXT,
    promoted_at                  INTEGER,
    attribution_source           TEXT,
    reconstructed                INTEGER NOT NULL DEFAULT 0 CHECK(reconstructed IN (0,1)),
    -- X67.7 promotion provenance -- additive; never overwrites the fields above.
    treasury_verified_at         INTEGER,
    treasury_verified_by         TEXT,
    treasury_verification_method TEXT,
    treasury_funding_signature   TEXT,
    promotion_source             TEXT,
    promoted_from_candidate      INTEGER CHECK(promoted_from_candidate IS NULL OR promoted_from_candidate IN (0,1)),
    promotion_reason             TEXT,
    promotion_evidence_version   TEXT,
    candidate_workflow_id        TEXT
);
CREATE INDEX IF NOT EXISTS ix_wpcw_state ON wt_provisioning_candidate_workflow(workflow_state);
CREATE INDEX IF NOT EXISTS ix_wpcw_subprov ON wt_provisioning_candidate_workflow(subprov_wallet);
CREATE INDEX IF NOT EXISTS ix_wpcw_discovered ON wt_provisioning_candidate_workflow(discovered_at);
"""

# 'TREASURY_VERIFIED' retained ONLY for legacy-row readability (X67.7) -- no
# code path in this module writes it for a new transition. New terminal
# states are PROMOTED_TO_MODEL_1, INVESTIGATION_CLOSED, and
# SHARED_PROVISIONING_INTELLIGENCE.
VALID_STATES = frozenset({
    "PENDING_VERIFICATION", "TREASURY_VERIFIED", "INVESTIGATION_CLOSED",
    "PROMOTED_TO_MODEL_1", "SHARED_PROVISIONING_INTELLIGENCE",
})
VALID_CLOSURE_REASONS = frozenset({
    "EXCHANGE_BOUNDARY", "MULTI_SOURCE_RELAY", "TREASURY_MISMATCH", "WRONG_MECHANISM",
    "INSUFFICIENT_EVIDENCE", "UPSTREAM_NOT_RESOLVED", "OTHER_OPERATOR", "MANUAL_REJECTION",
    "ROLE_SEPARATION_FAILED", "TREASURY_NOT_CONFIRMED", "TREASURY_EDGE_MISSING",
    "TREASURY_EDGE_UNDECODABLE", "TREASURY_BALANCE_DELTA_FAILED", "TEMPORAL_ORDER_FAILED",
    "NON_CANONICAL_FUNDING_MECHANISM", "MECHANISM_EVIDENCE_CONFLICT", "CLOSE_DESTINATION_UNVERIFIED",
    "SHARED_RELAY_TOPOLOGY", "SUBPROVIDER_REUSE_CONFLICT", "LINEAGE_CONFLICT",
    "NON_CANONICAL_TOPOLOGY", "EVIDENCE_CONFLICT", "MISSING_REQUIRED_FIELD",
})
VALID_MECHANISMS = frozenset({"WSOL_WRAP_CLOSE", "SEEDED_ACCOUNT_CLOSE"})

# X67.4: canonical population (n=121 distinct confirmed subprovs) shows the
# session-count distribution is heavily bimodal (82/121 single-session,
# 95/121 <=2 sessions) but has a real, CONFIRMED long tail (up to 288
# sessions on wallets Model 1 already accepts, always paired with a
# consistent, non-contradictory mechanism record). A bare session-count
# cutoff would therefore reject genuinely canonical launches -- so reuse
# count alone is a SOFT signal (surfaced in the decision payload) and never
# a hard rejection threshold on its own; see EVIDENCE_CONFLICT/
# MECHANISM_EVIDENCE_CONFLICT for the actual hard gates.
SHARED_RELAY_SESSION_THRESHOLD = 50

EVIDENCE_VERSION = "X67.7-v1"

# Default maximum gap (seconds) between a session's own funding_time and the
# launch's wrap-close time for that session to be admissible corroboration.
# Empirically justified (X65.98): true positives observed at 64-123s; negative
# controls observed at 37-44 HOURS -- three orders of magnitude of separation,
# so this bound has wide safety margin without needing to be tuned per-wallet.
DEFAULT_MAX_LINEAGE_GAP_SECONDS = 6 * 3600


_X67_7_NEW_COLUMNS = (
    ("treasury_verified_at", "INTEGER"),
    ("treasury_verified_by", "TEXT"),
    ("treasury_verification_method", "TEXT"),
    ("treasury_funding_signature", "TEXT"),
    ("promotion_source", "TEXT"),
    ("promoted_from_candidate", "INTEGER"),
    ("promotion_reason", "TEXT"),
    ("promotion_evidence_version", "TEXT"),
    ("candidate_workflow_id", "TEXT"),
)


def ensure_schema(conn) -> None:
    conn.executescript(DDL)
    # Additive column migration for tables created before X67.7 (CREATE TABLE
    # IF NOT EXISTS above does not add columns to an already-existing table).
    existing_cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(wt_provisioning_candidate_workflow)").fetchall()}
    for col, coltype in _X67_7_NEW_COLUMNS:
        if col not in existing_cols:
            conn.execute(
                f"ALTER TABLE wt_provisioning_candidate_workflow ADD COLUMN {col} {coltype}")
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
        decision = evaluate_candidate_for_canonical_promotion(
            conn, mint=mint, treasury=treasury, subprov_wallet=subprov_wallet,
            wrap_close_signature=wrap_close_signature, lineage_gap_seconds=gap,
            verification_evidence=evidence)
        return {"mint": mint, "outcome": "PASS", "would_transition_to":
                ("PROMOTED_TO_MODEL_1" if decision["eligible"] else "INVESTIGATION_CLOSED"),
                "evidence": evidence, "promotion_decision": decision}

    # X67.7 -- treasury verification succeeding is necessary but never
    # sufficient. Record the verified treasury edge as provenance FIRST
    # (so it survives regardless of the promotion outcome), then run the
    # one authoritative promotion predicate.
    conn.execute(
        "UPDATE wt_provisioning_candidate_workflow SET "
        "verified_treasury=?, selected_session_id=?, lineage_gap_seconds=?, "
        "verification_attempted_at=?, verification_outcome='PASS', evidence_json=?, "
        "attribution_source='SESSION_HINT_RPC_VERIFIED', treasury_verified_at=?, "
        "treasury_verified_by=?, treasury_verification_method=?, treasury_funding_signature=? "
        "WHERE mint=?",
        (treasury, selected.get("id"), gap, now, json.dumps(evidence),
         now, "AUTOMATED_VERIFIER", "SESSION_HINT_RPC_VERIFIED", funding_sig, mint),
    )
    conn.commit()

    return promote_eligible_candidate(
        conn, mint=mint, treasury=treasury, subprov_wallet=subprov_wallet,
        wrap_close_signature=wrap_close_signature, lineage_gap_seconds=gap,
        verification_evidence=evidence, now=now,
    )


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


# ── X67.7 — the one authoritative promotion decision path ──────────────────
#
# "Never promote on treasury verification alone" (task's Critical Safety
# Rule). This function is the SOLE place promotion eligibility is decided --
# verify_candidate() (live path) and the migration/reconciliation entry
# points (reconcile_legacy_treasury_verified, reevaluate_pending_candidates)
# all call this same function rather than duplicating any condition.

def evaluate_candidate_for_canonical_promotion(
    conn, *, mint: str, treasury: str, subprov_wallet: str,
    wrap_close_signature: Optional[str], lineage_gap_seconds: int,
    verification_evidence: dict[str, Any],
) -> dict[str, Any]:
    """Pure decision function (no writes). Returns:
        {eligible: bool, reason_code: str|None, missing_requirements: [...],
         conflicting_evidence: [...], verified_evidence: {...},
         evidence_version: str}
    `reason_code` is None when eligible=True; otherwise one of the failure
    reasons enumerated in VALID_CLOSURE_REASONS (X67.7 additions)."""
    missing: list[str] = []
    conflicts: list[str] = []

    row = conn.execute(
        "SELECT * FROM wt_provisioning_candidate_workflow WHERE mint=?", (mint,)
    ).fetchone()
    if row is None:
        return {"eligible": False, "reason_code": "MISSING_REQUIRED_FIELD",
                "missing_requirements": ["candidate_row"], "conflicting_evidence": [],
                "verified_evidence": {}, "evidence_version": EVIDENCE_VERSION}
    row = dict(row)
    creator = row.get("creator")

    # 1. Required identities + role separation.
    for field_name, value in (("mint", mint), ("creator", creator),
                               ("treasury", treasury), ("subprov_wallet", subprov_wallet)):
        if not value:
            missing.append(field_name)
    if missing:
        return {"eligible": False, "reason_code": "MISSING_REQUIRED_FIELD",
                "missing_requirements": missing, "conflicting_evidence": [],
                "verified_evidence": {}, "evidence_version": EVIDENCE_VERSION}
    if treasury == subprov_wallet or treasury == creator or subprov_wallet == creator:
        return {"eligible": False, "reason_code": "ROLE_SEPARATION_FAILED",
                "missing_requirements": [], "conflicting_evidence": ["role_collision"],
                "verified_evidence": {}, "evidence_version": EVIDENCE_VERSION}

    # 2. Treasury verification (already run by the caller -- re-assert the
    # hard facts here so this function is a complete, self-checking gate
    # even if called from a different entry point, e.g. migration).
    if not _table_exists(conn, "wt_confirmed_treasuries") or conn.execute(
        "SELECT 1 FROM wt_confirmed_treasuries WHERE treasury=? LIMIT 1", (treasury,)
    ).fetchone() is None:
        return {"eligible": False, "reason_code": "TREASURY_NOT_CONFIRMED",
                "missing_requirements": [], "conflicting_evidence": [],
                "verified_evidence": {}, "evidence_version": EVIDENCE_VERSION}
    if not verification_evidence.get("treasury_to_subprov_signature"):
        return {"eligible": False, "reason_code": "TREASURY_EDGE_MISSING",
                "missing_requirements": ["treasury_to_subprov_signature"], "conflicting_evidence": [],
                "verified_evidence": {}, "evidence_version": EVIDENCE_VERSION}
    if lineage_gap_seconds is None or lineage_gap_seconds < 0:
        return {"eligible": False, "reason_code": "TEMPORAL_ORDER_FAILED",
                "missing_requirements": [], "conflicting_evidence": ["temporal_order"],
                "verified_evidence": {}, "evidence_version": EVIDENCE_VERSION}

    # 3. Canonical provisioning mechanism -- validated against the authoritative
    # Model 1 enum (VALID_MECHANISMS, the same enum walkback_worker.py itself
    # uses), and cross-checked for a stored-vs-raw evidence conflict rather
    # than trusting either single source.
    workflow_mechanism = row.get("funding_mechanism")
    raw_mechanism = None
    if _table_exists(conn, "wt_walkback_queue"):
        wq = conn.execute(
            "SELECT funding_mechanism FROM wt_walkback_queue WHERE mint=?", (mint,)
        ).fetchone()
        if wq is not None:
            raw_mechanism = wq["funding_mechanism"] if not isinstance(wq, dict) else wq.get("funding_mechanism")
    if workflow_mechanism not in VALID_MECHANISMS:
        return {"eligible": False, "reason_code": "NON_CANONICAL_FUNDING_MECHANISM",
                "missing_requirements": [], "conflicting_evidence": [],
                "verified_evidence": {}, "evidence_version": EVIDENCE_VERSION}
    if raw_mechanism is not None and raw_mechanism != workflow_mechanism:
        # This is the exact discrepancy class X67.4 flagged for
        # HqDzBCPHMNKu/9Pp8MeVxT5ku: recorded workflow evidence disagrees
        # with the raw walkback-decoded mechanism. Fail closed rather than
        # trust either source silently.
        conflicts.append(f"mechanism: workflow={workflow_mechanism} raw={raw_mechanism}")
        return {"eligible": False, "reason_code": "MECHANISM_EVIDENCE_CONFLICT",
                "missing_requirements": [], "conflicting_evidence": conflicts,
                "verified_evidence": {}, "evidence_version": EVIDENCE_VERSION}
    if not verification_evidence.get("wrap_close_signature") and not wrap_close_signature:
        return {"eligible": False, "reason_code": "CLOSE_DESTINATION_UNVERIFIED",
                "missing_requirements": ["wrap_close_signature"], "conflicting_evidence": [],
                "verified_evidence": {}, "evidence_version": EVIDENCE_VERSION}

    # 4. Canonical topology. Session-reuse count alone is a SOFT signal
    # (X67.4: even confirmed Model 1 subprovs range up to 288 sessions) --
    # only surfaced as a soft flag, never a standalone hard rejection, per
    # the task's explicit "do not convert every observed canonical
    # characteristic into an inflexible rule." The hard check remains the
    # mechanism-conflict gate above, which is what actually distinguished
    # the known non-canonical candidate in X67.4's audit.
    session_count = 0
    if _table_exists(conn, "wt_active_subprov_sessions"):
        r = conn.execute(
            "SELECT COUNT(*) c FROM wt_active_subprov_sessions WHERE subprov_wallet=?",
            (subprov_wallet,)
        ).fetchone()
        session_count = r["c"] if not isinstance(r, dict) else r.get("c", 0)
    soft_relay_flag = session_count >= SHARED_RELAY_SESSION_THRESHOLD

    # Lineage conflict: creator already resolved to a DIFFERENT subprov by
    # the ordinary walkback pipeline than the one this evaluation is
    # promoting under.
    if _table_exists(conn, "wt_walkback_queue"):
        wq2 = conn.execute(
            "SELECT subprov FROM wt_walkback_queue WHERE mint=?", (mint,)
        ).fetchone()
        if wq2 is not None:
            wq2_subprov = wq2["subprov"] if not isinstance(wq2, dict) else wq2.get("subprov")
            if wq2_subprov and wq2_subprov != subprov_wallet:
                conflicts.append(f"subprov: evaluating={subprov_wallet} walkback_queue={wq2_subprov}")
                return {"eligible": False, "reason_code": "LINEAGE_CONFLICT",
                        "missing_requirements": [], "conflicting_evidence": conflicts,
                        "verified_evidence": {}, "evidence_version": EVIDENCE_VERSION}

    verified_evidence = {
        "treasury": treasury, "subprov_wallet": subprov_wallet, "creator": creator,
        "treasury_to_subprov_signature": verification_evidence.get("treasury_to_subprov_signature"),
        "wrap_close_signature": verification_evidence.get("wrap_close_signature") or wrap_close_signature,
        "funding_amount": verification_evidence.get("funding_amount"),
        "lineage_gap_seconds": lineage_gap_seconds,
        "subprov_session_count": session_count,
        "soft_relay_flag": soft_relay_flag,
        "verification_method": verification_evidence.get("verification_method", "SESSION_HINT_RPC_VERIFIED"),
    }
    return {"eligible": True, "reason_code": None, "missing_requirements": [],
            "conflicting_evidence": [], "verified_evidence": verified_evidence,
            "evidence_version": EVIDENCE_VERSION}


def promote_eligible_candidate(
    conn, *, mint: str, treasury: str, subprov_wallet: str,
    wrap_close_signature: Optional[str], lineage_gap_seconds: int,
    verification_evidence: dict[str, Any], now: Optional[int] = None,
) -> dict[str, Any]:
    """Runs the one authoritative predicate and, on eligibility, promotes
    through the EXISTING canonical registry writer
    (watchtower_registry_promotion.promote_walkback_confirmed_watchtower) --
    never a bespoke direct INSERT. Idempotent: if the mint is already
    canonical, no-ops and reconciles the candidate's own workflow_state
    rather than attempting a duplicate write."""
    now = now or int(time.time())
    ensure_schema(conn)

    if is_confirmed_in_model1(conn, mint):
        conn.execute(
            "UPDATE wt_provisioning_candidate_workflow SET workflow_state='PROMOTED_TO_MODEL_1', "
            "updated_at=?, promoted_at=COALESCE(promoted_at,?) WHERE mint=?",
            (now, now, mint),
        )
        conn.commit()
        return {"mint": mint, "action": "already_canonical", "eligible": True,
                "workflow_state": "PROMOTED_TO_MODEL_1"}

    decision = evaluate_candidate_for_canonical_promotion(
        conn, mint=mint, treasury=treasury, subprov_wallet=subprov_wallet,
        wrap_close_signature=wrap_close_signature, lineage_gap_seconds=lineage_gap_seconds,
        verification_evidence=verification_evidence,
    )
    if not decision["eligible"]:
        note = f"promotion predicate failed: {decision['reason_code']}"
        if decision["conflicting_evidence"]:
            note += " | conflicts: " + "; ".join(decision["conflicting_evidence"])
        if decision["missing_requirements"]:
            note += " | missing: " + ", ".join(decision["missing_requirements"])
        result = _close(conn, mint, now, decision["reason_code"] or "EVIDENCE_CONFLICT", note, dry_run=False)
        result["decision"] = decision
        return {**result, "action": "not_eligible", "eligible": False}

    try:
        from src.core.watchtower_registry_promotion import promote_walkback_confirmed_watchtower
    except Exception as exc:  # noqa: BLE001 -- registry writer import must never crash this call
        return {"mint": mint, "action": "failed", "eligible": True,
                "error": f"could not import canonical registry writer: {exc}"}

    row = conn.execute(
        "SELECT creator FROM wt_provisioning_candidate_workflow WHERE mint=?", (mint,)
    ).fetchone()
    creator = row["creator"] if not isinstance(row, dict) else row.get("creator")
    evidence_payload = dict(decision["verified_evidence"])
    evidence_payload.setdefault("creator", creator)
    evidence_payload["treasuries"] = [treasury]
    evidence_payload["subprovisioners"] = [subprov_wallet]

    try:
        from src.ops.watchtower_alignment import WATCHTOWER_OPERATOR_ID
    except Exception as exc:  # noqa: BLE001
        return {"mint": mint, "action": "failed", "eligible": True,
                "error": f"could not import WATCHTOWER_OPERATOR_ID: {exc}"}

    try:
        result = promote_walkback_confirmed_watchtower(
            conn, mint,
            outcome_type="CANONICAL_OPERATOR_REACHED",
            operator_id=WATCHTOWER_OPERATOR_ID,
            evidence=evidence_payload,
            completed_at=now,
        )
    except Exception as exc:  # noqa: BLE001 -- must never crash the caller
        return {"mint": mint, "action": "failed", "eligible": True, "error": str(exc)}

    if result.get("action") == "failed":
        return {"mint": mint, "action": "failed", "eligible": True, "error": result.get("error")}
    if result.get("action") not in ("promoted", "already_present"):
        # Defensive: e.g. "not_eligible" from the registry writer's own
        # outcome_type/operator_id check -- should be unreachable given the
        # fixed literals passed above, but never assume; fail closed.
        return {"mint": mint, "action": result.get("action", "failed"), "eligible": True,
                "error": "canonical registry writer declined promotion unexpectedly"}

    conn.execute(
        "UPDATE wt_provisioning_candidate_workflow SET "
        "workflow_state='PROMOTED_TO_MODEL_1', updated_at=?, promoted_at=?, "
        "promotion_source='X67_7_CANDIDATE_EVALUATOR', promoted_from_candidate=1, "
        "promotion_reason=?, promotion_evidence_version=?, evidence_json=? "
        "WHERE mint=?",
        (now, now, decision["reason_code"] or "FULL_MODEL1_PREDICATE_SATISFIED",
         decision["evidence_version"], json.dumps(evidence_payload), mint),
    )
    conn.commit()
    return {"mint": mint, "action": "promoted", "eligible": True,
            "workflow_state": "PROMOTED_TO_MODEL_1", "decision": decision}


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


# ── Model 1 promotion observation (never writes Model 1 directly) ──────────

def sync_promoted_state(conn) -> int:
    """Read-only-with-respect-to-Model-1 observation pass: for any row this
    module's own workflow_state has NOT yet caught up to Model 1's own
    registry (e.g. promoted through an external path), reconcile the local
    state. Never writes `wt_watchtower_launches` itself -- only observes it.
    Returns the number of rows updated."""
    ensure_schema(conn)
    if not _table_exists(conn, "wt_watchtower_launches"):
        return 0
    now = int(time.time())
    rows = conn.execute(
        "SELECT mint FROM wt_provisioning_candidate_workflow "
        "WHERE workflow_state NOT IN ('PROMOTED_TO_MODEL_1', 'INVESTIGATION_CLOSED', "
        "'SHARED_PROVISIONING_INTELLIGENCE')"
    ).fetchall()
    updated = 0
    for r in rows:
        mint = r[0] if not isinstance(r, dict) else r["mint"]
        if is_confirmed_in_model1(conn, mint):
            conn.execute(
                "UPDATE wt_provisioning_candidate_workflow SET "
                "workflow_state='PROMOTED_TO_MODEL_1', updated_at=?, promoted_at=COALESCE(promoted_at,?) "
                "WHERE mint=?",
                (now, now, mint),
            )
            updated += 1
    if updated:
        conn.commit()
    return updated


def reconcile_legacy_treasury_verified(conn, *, now: Optional[int] = None) -> list[dict[str, Any]]:
    """X67.7 migration: runs every surviving legacy TREASURY_VERIFIED row
    through the EXACT SAME evaluator (`promote_eligible_candidate` ->
    `evaluate_candidate_for_canonical_promotion`) new candidates use --
    no per-mint special-casing, per the task's explicit constraint. Safe to
    call repeatedly (idempotent via `promote_eligible_candidate`'s own
    already_canonical short-circuit). Returns one result dict per row
    processed."""
    ensure_schema(conn)
    now = now or int(time.time())
    rows = conn.execute(
        "SELECT * FROM wt_provisioning_candidate_workflow WHERE workflow_state='TREASURY_VERIFIED'"
    ).fetchall()
    results = []
    for r in rows:
        row = dict(r)
        mint = row["mint"]
        evidence = {}
        if row.get("evidence_json"):
            try:
                evidence = json.loads(row["evidence_json"])
            except (TypeError, ValueError):
                evidence = {}
        treasury = row.get("verified_treasury") or evidence.get("treasury")
        subprov = row.get("subprov_wallet")
        gap = row.get("lineage_gap_seconds")
        wrap_close_sig = evidence.get("wrap_close_signature")
        result = promote_eligible_candidate(
            conn, mint=mint, treasury=treasury, subprov_wallet=subprov,
            wrap_close_signature=wrap_close_sig, lineage_gap_seconds=gap,
            verification_evidence=evidence, now=now,
        )
        results.append(result)
    return results


def reevaluate_pending_candidates(conn) -> list[dict[str, Any]]:
    """X67.7 Phase 7: classify (not auto-promote) every current
    PENDING_VERIFICATION row using the evaluator's own reason codes, WITHOUT
    running verification (no RPC call is made here) -- this is a read-only
    reconciliation report over already-known evidence, used to distinguish
    ELIGIBLE_FOR_PROMOTION / EVIDENCE_INCOMPLETE / EVIDENCE_CONFLICT /
    NON_CANONICAL_TOPOLOGY / SHARED_PROVISIONING_INTELLIGENCE / UNEVALUABLE
    without writing anything. Never promotes -- promotion still requires an
    explicit verify_candidate() call, which performs the real RPC check."""
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT * FROM wt_provisioning_candidate_workflow WHERE workflow_state='PENDING_VERIFICATION'"
    ).fetchall()
    out = []
    for r in rows:
        row = dict(r)
        mint = row["mint"]
        subprov = row.get("subprov_wallet")
        classification = "EVIDENCE_INCOMPLETE"
        detail = "treasury edge not yet independently verified"
        if not subprov:
            classification = "UNEVALUABLE"
            detail = "no subprovider resolved"
        else:
            workflow_mechanism = row.get("funding_mechanism")
            raw_mechanism = None
            if _table_exists(conn, "wt_walkback_queue"):
                wq = conn.execute(
                    "SELECT funding_mechanism FROM wt_walkback_queue WHERE mint=?", (mint,)
                ).fetchone()
                if wq is not None:
                    raw_mechanism = wq["funding_mechanism"] if not isinstance(wq, dict) else wq.get("funding_mechanism")
            if raw_mechanism is not None and raw_mechanism != workflow_mechanism:
                classification = "UNEVALUABLE"
                detail = f"mechanism conflict: workflow={workflow_mechanism} raw={raw_mechanism}"
            elif _table_exists(conn, "wt_active_subprov_sessions"):
                cnt_row = conn.execute(
                    "SELECT COUNT(*) c FROM wt_active_subprov_sessions WHERE subprov_wallet=?",
                    (subprov,)
                ).fetchone()
                session_count = cnt_row["c"] if not isinstance(cnt_row, dict) else cnt_row.get("c", 0)
                if session_count >= SHARED_RELAY_SESSION_THRESHOLD:
                    classification = "SHARED_PROVISIONING_INTELLIGENCE"
                    detail = f"subprovider has {session_count} sessions -- shared/relay topology"
        out.append({"mint": mint, "classification": classification, "detail": detail})
    return out


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
