"""X67.18 -- Evidence-gathering adapters for the shared canonical predicate
(watchtower_canonical_predicate.py). Each adapter reads its OWN path's
existing data source and translates it into a CanonicalEvidenceInput; all
I/O lives here, never inside the predicate itself (X67.17 S3/S6).

These adapters are NOT yet wired into either promotion path
(evaluate_candidate_for_canonical_promotion / promote_walkback_confirmed_
watchtower) -- per X67.18's mandatory backtest-before-deploy gate, they are
first exercised only by the backtest/shadow-evaluation tooling
(scripts/x67_18_backtest.py) against real, read-only database connections.
Wiring either promotion path to call these adapters + the shared predicate
is a SEPARATE, later, explicitly-approved change.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Optional

from src.ops.watchtower_canonical_predicate import (
    CanonicalEvidenceInput,
    ConflictSignal,
    MechanismEvidence,
    SessionEvidence,
    TreasuryConfirmationEvidence,
)
from src.ops.lineage_quarantine import eligible_session_relation

# Mirrors provisioning_candidates_workflow.SHARED_RELAY_SESSION_THRESHOLD --
# kept as its own constant here (not imported) so this module has no import
# dependency on either promotion-path module, consistent with X67.17 S6's
# explicit instruction not to make Path B depend on Path A's internals.
SHARED_RELAY_SESSION_THRESHOLD = 50


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    try:
        value = row[key]
        return default if value is None else value
    except (IndexError, KeyError):
        return default


def _gather_common_evidence(
    ops_conn: sqlite3.Connection, *,
    mint: str, treasury: Optional[str], subprov: Optional[str], creator: Optional[str],
    stored_mechanism: Optional[str],
    stored_signature: Optional[str] = None,
    evidence_tier_if_no_conflict: str = "WALKBACK_RECOVERED",
) -> CanonicalEvidenceInput:
    """Shared evidence-gathering logic used by BOTH path adapters below --
    every fact here comes from tables already read by one or both existing
    promotion paths today (wt_confirmed_treasuries, wt_walkback_queue,
    wt_active_subprov_sessions). No new data source is introduced."""

    # --- Identity ---
    treasury_row = None
    if treasury and _table_exists(ops_conn, "wt_confirmed_treasuries"):
        treasury_row = ops_conn.execute(
            "SELECT confidence, provenance FROM wt_confirmed_treasuries WHERE treasury=?",
            (treasury,),
        ).fetchone()
    treasury_confirmation = TreasuryConfirmationEvidence(
        confirmed=treasury_row is not None,
        confidence_tier=_row_get(treasury_row, "provenance"),
    )

    # --- Session + topology (X67.16's core new axis) ---
    session_row = None
    if subprov and _table_exists(ops_conn, "wt_active_subprov_sessions"):
        session_relation = eligible_session_relation(ops_conn)
        session_row = ops_conn.execute(
            f"SELECT state, funding_signature FROM {session_relation} "
            "WHERE subprov_wallet=? ORDER BY detected_at DESC LIMIT 1",
            (subprov,),
        ).fetchone()

    walkback_row = None
    if _table_exists(ops_conn, "wt_walkback_queue"):
        walkback_row = ops_conn.execute(
            "SELECT subprov, funder_wallet, funding_mechanism, funder_sig "
            "FROM wt_walkback_queue WHERE mint=?",
            (mint,),
        ).fetchone()

    funder_wallet = _row_get(walkback_row, "funder_wallet")
    # X67.16 Phase 3's finding, operationalized: a session is RELAY_ASSISTED
    # exactly when the wallet that signed the creator-funding transaction
    # (funder_wallet) differs from the labelled subprovider itself.
    topology = "UNKNOWN"
    relay_wallet = None
    if subprov and funder_wallet:
        topology = "DIRECT" if funder_wallet == subprov else "RELAY_ASSISTED"
        relay_wallet = funder_wallet if topology == "RELAY_ASSISTED" else None

    if session_row is not None:
        session_state = {
            "ACTIVE": "ACTIVE", "EXPIRED": "EXPIRED", "REJECTED": "REJECTED",
        }.get(_row_get(session_row, "state"), "EXPIRED")
        session_exists = True
    elif walkback_row is not None:
        session_state = "RECONSTRUCTED"
        session_exists = True
    else:
        session_state = "ABSENT"
        session_exists = False

    # Relay classification is deliberately left UNCLASSIFIED here rather
    # than inferred -- classifying a relay wallet as exchange-pattern
    # requires an RPC trace of ITS OWN upstream parent (X67.16 Phase 3),
    # which neither adapter performs (no RPC in this module, per the
    # pure-evidence-gathering-only constraint). A future, explicitly
    # RPC-driven adapter step could populate this field; until then it is
    # reported honestly as unclassified rather than guessed.
    session_evidence = SessionEvidence(
        exists=session_exists,
        state=session_state,
        topology=topology,
        relay_wallet=relay_wallet,
        relay_status="EXTERNAL_UNCLASSIFIED" if relay_wallet else "NONE",
    )

    # --- Mechanism ---
    raw_mechanism = _row_get(walkback_row, "funding_mechanism")
    raw_signature = _row_get(walkback_row, "funder_sig")
    conflicts: list[ConflictSignal] = []

    if stored_mechanism and raw_mechanism and stored_mechanism != raw_mechanism:
        # X67.20 -- the SAME real transaction, decoded once by the live
        # capture path and once by the walkback pipeline, can legitimately
        # receive two different taxonomy labels for one undisputed event
        # (X67.19's AvLiJBdtb4omCymE... finding: identical signature,
        # identical sender/recipient/amount/timing, only the label differs).
        # This is fundamentally different from a genuine lineage/mechanism
        # dispute where the two sources cite DIFFERENT signatures entirely
        # (X67.19's CPtvQTf8bXKPx4wQ... finding). Distinguish by comparing
        # the signatures, when both are available -- only fall back to the
        # stricter MECHANISM_CONFLICT when a same-signature comparison
        # cannot be made (no stored_signature supplied by the caller) or
        # when the signatures are genuinely different.
        if stored_signature and raw_signature and stored_signature == raw_signature:
            mechanism_value = stored_mechanism
            conflicts.append(ConflictSignal(
                code="MECHANISM_LABEL_VARIATION",
                detail=f"same signature={stored_signature[:16]}... stored={stored_mechanism} raw={raw_mechanism}",
            ))
        else:
            mechanism_value = "CONFLICTING"
            conflicts.append(ConflictSignal(
                code="MECHANISM_CONFLICT",
                detail=f"stored={stored_mechanism} raw={raw_mechanism} "
                       f"stored_sig={stored_signature!r} raw_sig={raw_signature!r}",
                redecode_attempted=False,
            ))
    else:
        mechanism_value = stored_mechanism or raw_mechanism or "UNVERIFIED"
        if mechanism_value not in ("WSOL_WRAP_CLOSE", "SEEDED_ACCOUNT_CLOSE", "PLAIN_XFER"):
            mechanism_value = "UNVERIFIED"

    mechanism_evidence = MechanismEvidence(
        value=mechanism_value,
        evidence_tier=evidence_tier_if_no_conflict,
    )

    # --- Conflicts: role collision, lineage conflict, session-volume soft flag ---
    if treasury and subprov and creator:
        if treasury == subprov or treasury == creator or subprov == creator:
            conflicts.append(ConflictSignal(code="ROLE_COLLISION", detail="same-mint role overlap"))

    walkback_subprov = _row_get(walkback_row, "subprov")
    if walkback_subprov and subprov and walkback_subprov != subprov:
        conflicts.append(ConflictSignal(
            code="LINEAGE_CONFLICT",
            detail=f"evaluating={subprov} walkback_queue={walkback_subprov}",
        ))

    session_count = 0
    if subprov and _table_exists(ops_conn, "wt_active_subprov_sessions"):
        session_relation = eligible_session_relation(ops_conn)
        r = ops_conn.execute(
            f"SELECT COUNT(*) c FROM {session_relation} WHERE subprov_wallet=?",
            (subprov,),
        ).fetchone()
        session_count = _row_get(r, "c", 0) or 0
    if session_count >= SHARED_RELAY_SESSION_THRESHOLD:
        conflicts.append(ConflictSignal(
            code="SHARED_RELAY_SESSION_VOLUME", detail=f"session_count={session_count}",
        ))

    return CanonicalEvidenceInput(
        mint=mint,
        treasury_wallet=treasury,
        subprov_wallet=subprov,
        creator_wallet=creator,
        treasury_confirmation=treasury_confirmation,
        session_evidence=session_evidence,
        mechanism_evidence=mechanism_evidence,
        conflict_evidence=conflicts,
    )


def build_evidence_from_path_a_candidate(
    ops_conn: sqlite3.Connection, *, mint: str,
) -> CanonicalEvidenceInput:
    """Path A adapter -- reads wt_provisioning_candidate_workflow (the
    candidate-workflow row) plus the same shared tables Path B's adapter
    also reads. Mirrors the fields evaluate_candidate_for_canonical_
    promotion() already reads from this exact table."""
    row = ops_conn.execute(
        "SELECT creator, subprov_wallet, session_treasury, verified_treasury, funding_mechanism "
        "FROM wt_provisioning_candidate_workflow WHERE mint=?", (mint,),
    ).fetchone()
    creator = _row_get(row, "creator")
    subprov = _row_get(row, "subprov_wallet")
    treasury = _row_get(row, "verified_treasury") or _row_get(row, "session_treasury")
    stored_mechanism = _row_get(row, "funding_mechanism")

    # X67.18 backtest finding: candidates closed BEFORE treasury verification
    # ever ran (e.g. investigation-closed on identity/topology grounds) never
    # had session_treasury/verified_treasury populated on the workflow row
    # itself, even though the treasury is fully knowable from
    # wt_active_subprov_sessions for that subprov. Without this fallback,
    # every such row falsely reports IDENTITY_UNCONFIRMED regardless of its
    # true treasury lineage -- a false negative, not a genuine identity gap.
    if not treasury and subprov and _table_exists(ops_conn, "wt_active_subprov_sessions"):
        session_relation = eligible_session_relation(ops_conn)
        sess = ops_conn.execute(
            f"SELECT treasury_wallet FROM {session_relation} "
            "WHERE subprov_wallet=? AND treasury_wallet IS NOT NULL "
            "ORDER BY detected_at DESC LIMIT 1", (subprov,),
        ).fetchone()
        treasury = _row_get(sess, "treasury_wallet")

    return _gather_common_evidence(
        ops_conn, mint=mint, treasury=treasury, subprov=subprov, creator=creator,
        stored_mechanism=stored_mechanism,
        evidence_tier_if_no_conflict="WALKBACK_RECOVERED",
    )


def build_evidence_from_path_b_outcome(
    ops_conn: sqlite3.Connection, *, mint: str,
) -> CanonicalEvidenceInput:
    """Path B adapter -- reads wt_walkback_queue directly (the same table
    promote_walkback_confirmed_watchtower() already reads for creator/
    treasury/subprov/mechanism fields), rather than the candidate-workflow
    table Path A uses. This is the NEW evidence-gathering step Path B
    currently lacks entirely (X67.14's core finding)."""
    row = ops_conn.execute(
        "SELECT creator, subprov, treasury, funding_mechanism, funder_sig "
        "FROM wt_walkback_queue WHERE mint=?",
        (mint,),
    ).fetchone()
    creator = _row_get(row, "creator")
    subprov = _row_get(row, "subprov")
    treasury = _row_get(row, "treasury")
    stored_mechanism = _row_get(row, "funding_mechanism")
    # X67.21 -- pass the walkback queue's own funder_sig as the "stored"
    # signature so _gather_common_evidence's same-signature-vs-different-
    # signature comparison (X67.20) has something to compare against. For
    # Path B specifically both "stored" and "raw" mechanism/signature would
    # otherwise come from the exact same wt_walkback_queue row (there is no
    # second source at THIS mint's pre-registry stage) -- so this call can
    # never itself produce a MECHANISM_CONFLICT/MECHANISM_LABEL_VARIATION
    # signal; that distinction only becomes possible once a registry row
    # exists to compare against (build_evidence_for_registry_row).
    stored_signature = _row_get(row, "funder_sig")

    return _gather_common_evidence(
        ops_conn, mint=mint, treasury=treasury, subprov=subprov, creator=creator,
        stored_mechanism=stored_mechanism, stored_signature=stored_signature,
        evidence_tier_if_no_conflict="WALKBACK_RECOVERED",
    )


# X67.21 -- stable public names matching the integration-layer design doc
# (X67.21's own interface spec), aliasing the existing X67.18 adapters
# rather than renaming them (avoids breaking scripts/x67_18_backtest.py and
# the X67.18/X67.20 test suites, which import the original names).
build_evidence_for_candidate_workflow = build_evidence_from_path_a_candidate
build_evidence_for_walkback_queue = build_evidence_from_path_b_outcome


def build_evidence_for_registry_row(
    ops_conn: sqlite3.Connection, *, mint: str,
) -> CanonicalEvidenceInput:
    """Backtest-only adapter -- reads an EXISTING wt_watchtower_launches row
    directly (used only to replay already-canonical rows through the shared
    predicate for the X67.18 backtest; not used by either live promotion
    path, which always evaluate a mint BEFORE it is in the registry)."""
    row = ops_conn.execute(
        "SELECT creator_wallet, treasury_wallet, subprov_wallet, funding_mechanism, "
        "confidence, creator_extraction_method, wrap_close_signature "
        "FROM wt_watchtower_launches WHERE mint=?",
        (mint,),
    ).fetchone()
    if row is None:
        raise ValueError(f"mint not in registry: {mint}")
    creator = _row_get(row, "creator_wallet")
    treasury = _row_get(row, "treasury_wallet")
    subprov = _row_get(row, "subprov_wallet")
    stored_mechanism = _row_get(row, "funding_mechanism")
    stored_signature = _row_get(row, "wrap_close_signature")
    confidence = _row_get(row, "confidence")

    # X67.17 S8's recommended additive resolution for the 5 already-audited
    # PLAIN_XFER rows: X67.15 RPC-verified these exact 5 signatures by exact
    # signature match. Rather than silently regress them to REVIEW_REQUIRED
    # for lack of a persisted per-row field that does not exist in today's
    # schema, this backtest adapter recognizes them by mint (the precise,
    # already-established X67.15 result set) and reports RPC_VERIFIED
    # evidence for exactly these five -- everything else defaults to
    # WALKBACK_RECOVERED/HISTORICAL_RECONSTRUCTION as appropriate. This is
    # a backtest-only convenience, NOT a schema or registry change, and is
    # explicitly called out in the backtest report rather than hidden.
    x67_15_verified_plain_xfer_mints = frozenset({
        "5Rg9Ay22nwhhgE3adzvwsGxMCKTyrPn3joYhiLZEpump",
        "gQcrSg6acMHon1RHfMAwGtdVFvW2mJNF1T6dkgmpump",
        "8XaVic8H3Rr8jiWhnrEdpVWoSakTygj3R5NdQtr9pump",
        "E7AAwze6ch19cmexjsNHgz7tT27yzzvjqZ79AD8Zpump",
        "CVdByCD7SLsj2Kv7UAqGyNJgSVc4Nvd8qdL2U1shpump",
    })
    if mint in x67_15_verified_plain_xfer_mints:
        evidence_tier = "RPC_VERIFIED"
    elif stored_mechanism in ("WSOL_WRAP_CLOSE", "SEEDED_ACCOUNT_CLOSE"):
        # X67.10's 13 legacy rows and the general STRICT/WALKBACK account-close
        # population both qualify for HISTORICAL_RECONSTRUCTION/WALKBACK_
        # RECOVERED, which the predicate treats as sufficient for
        # self-verifying mechanisms regardless of which of the two applies --
        # collapse to the lower (stricter-named but numerically higher-rank,
        # i.e. lower-priority) of the two so this adapter never OVER-claims
        # evidence strength it cannot actually distinguish from the confidence
        # column alone.
        evidence_tier = "WALKBACK_RECOVERED" if confidence == "WALKBACK" else "HISTORICAL_RECONSTRUCTION"
    else:
        evidence_tier = "WALKBACK_RECOVERED"

    return _gather_common_evidence(
        ops_conn, mint=mint, treasury=treasury, subprov=subprov, creator=creator,
        stored_mechanism=stored_mechanism, stored_signature=stored_signature,
        evidence_tier_if_no_conflict=evidence_tier,
    )
