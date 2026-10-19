"""X24.1 Phase 4 — Detection vs Attribution reconciliation.

Classifies every walkback-confirmed launch (source population, per the X24
reconciliation report's explicit correction: NOT raw session counts, which are
dominated by candidate-churn noise) against wt_watchtower_launches to answer:
was this launch detected live, recovered by catch-up/reconciliation, or only
ever recovered retroactively by walkback?

Read-only. Never inserts into wt_watchtower_launches — a walkback-only launch
stays a walkback-only launch; this module only classifies and reports it. The
X24 report was explicit that presenting a walkback recovery as a live
detection would misrepresent coverage, so this boundary is intentional.

Two subsystems label the same real mechanism differently:
  - wt_active_subprov_sessions.funding_mechanism  uses 'PLAIN_TRANSFER'
  - wt_provisioning_edges/sessions.*_mechanism    use 'PLAIN_XFER'
Both are treated as equivalent here (_PLAIN_MECHANISM_LABELS) — this
inconsistency is itself a secondary finding worth normalising eventually, but
out of scope for this reconciliation pass.
"""
from __future__ import annotations

import os
import sqlite3
import time
from typing import Any, Optional

from src.ops.lineage_quarantine import eligible_session_relation

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
OPS_DB_PATH = os.environ.get("OPS_V2_DB_PATH", os.path.join(_REPO_ROOT, "database", "wt_ops_v2.db"))

_PLAIN_MECHANISM_LABELS = ("PLAIN_TRANSFER", "PLAIN_XFER")

# Detection-source strings already written by ws_cascade.py / launch_audit.py that
# count as a genuine live/near-live catch (never invented — these are the real
# values record_launch()/insert_pending_audit_row() persist today).
_LIVE_DETECTION_SOURCES = ("LIVE_STREAM", "ACTIVE_CATCHUP")
_RECONCILED_SOURCES = ("RECONCILE", "RECONCILER", "BACKFILL", "REPLAY")


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def _is_plain(mechanism: Optional[str]) -> bool:
    return mechanism in _PLAIN_MECHANISM_LABELS


_CONFIRMED_WALKBACK_OUTCOMES = ("WATCHTOWER_CONFIRMED",)


def classify_walkback_confirmed_launches(
    ops_db_path: str = OPS_DB_PATH, *, window_days: Optional[int] = None
) -> dict[str, Any]:
    """Returns the full classification for every launch with a persisted
    wt_provisioning_sessions row (a non-null source_mint).

    window_days: if given, only rows whose best-available event timestamp
    (launch.create_time, else creator_launch_time, else
    subprov_to_creator_block_time, else recorded_at, in that preference order)
    falls within the last window_days are included. Deployment-readiness fix
    (X24.2 review): detection_path_health() previously compared a windowed
    live-detection count against an always-all-time walkback-only count,
    silently mismatching time bases. Default remains None (all-time) so
    existing callers (X24.1's route, tests) are unaffected.

    Classification taxonomy (per X24.1 Phase 5's UI vocabulary, corrected by
    X25.5.1):
      LIVE_DETECTED          — wt_watchtower_launches row exists,
                               detection_source in LIVE_STREAM/ACTIVE_CATCHUP.
      RECONCILED             — wt_watchtower_launches row exists via a
                               reconciliation/backfill/replay source.
      WALKBACK_RECOVERED     — no wt_watchtower_launches row, the session that
                               should have covered it was never LIVE_ARMED at
                               the relevant time, AND this mint's authoritative
                               walkback outcome (wt_walkback_queue.intelligence_outcome)
                               is WATCHTOWER_CONFIRMED — a genuinely confirmed
                               operation launch recovered retrospectively.
      PIPELINE_INCONSISTENCY — same as above but a LIVE_ARMED session DID
                               cover the CREATE-time window and still missed
                               it (the AWiaGsus-class defect) — ALSO requires
                               the confirmed walkback outcome.
      WALKBACK_OBSERVED      — a wt_provisioning_sessions row exists (a
                               partial funding fragment was captured during a
                               walk) but the mint's authoritative outcome is
                               NOT WATCHTOWER_CONFIRMED — e.g. LINEAGE_GAP,
                               NON_WATCHTOWER, or a known-infrastructure
                               attribution outcome (KNOWN_RELAY_REACHED etc.).
                               This is evidence a fragment was observed; it is
                               NOT a membership claim. Replaces the pre-X25.5.1
                               defect where such rows were misclassified
                               WALKBACK_RECOVERED and rendered as confirmed
                               WATCHTOWER-tracked-operation membership.
      WALKBACK_INCONCLUSIVE  — a wt_provisioning_sessions row exists but no
                               wt_walkback_queue row can be found for this
                               mint at all, so membership cannot be
                               determined either way from persisted data.

    X25.5.1 fix: wt_provisioning_sessions row existence alone is NEVER used as
    a membership gate. wt_provisioning_sessions is deliberately an
    operation-agnostic, append-only evidence table (see
    src/ops/provisioning_edges.py's own docstring) — its mere existence proves
    only that a funding fragment was observed during a walk, never that the
    walk confirmed a WATCHTOWER operation. Membership is established solely
    by joining to wt_walkback_queue.intelligence_outcome.
    """
    conn = _connect(ops_db_path)
    try:
        if not _table_exists(conn, "wt_provisioning_sessions"):
            return {"rows": [], "summary": {}}

        sessions = conn.execute(
            "SELECT source_mint, treasury, subprov, creator, "
            "treasury_to_subprov_mechanism, subprov_to_creator_mechanism, "
            "treasury_to_subprov_block_time, subprov_to_creator_block_time, "
            "creator_launch_time, recorded_at "
            "FROM wt_provisioning_sessions "
            "WHERE source_mint IS NOT NULL AND source_mint != ''"
        ).fetchall()

        rows: list[dict[str, Any]] = []
        for s in sessions:
            mint = s["source_mint"]
            launch = conn.execute(
                "SELECT detection_source, create_time, subprov_wallet, treasury_wallet "
                "FROM wt_watchtower_launches WHERE mint=?", (mint,)
            ).fetchone()

            # treasury->subprov mechanism may only be recorded on the edge table
            # (wt_provisioning_sessions.treasury_to_subprov_mechanism is often blank —
            # confirmed empirically in the X24 investigation), so fall back to it, and
            # further fall back to the live-side session table (which is the only place
            # AWiaGsus's own PLAIN_TRANSFER mechanism is actually recorded — walkback
            # never produced a TREASURY_TO_SUBPROV edge for that pair at all).
            t2s_mech = s["treasury_to_subprov_mechanism"]
            if not t2s_mech and s["subprov"]:
                edge = conn.execute(
                    "SELECT funding_mechanism FROM wt_provisioning_edges "
                    "WHERE edge_type='TREASURY_TO_SUBPROV' AND to_wallet=? LIMIT 1",
                    (s["subprov"],)
                ).fetchone()
                t2s_mech = edge["funding_mechanism"] if edge else None
            if not t2s_mech and s["subprov"]:
                session_relation = eligible_session_relation(conn)
                live_sess = conn.execute(
                    f"SELECT funding_mechanism FROM {session_relation} "
                    "WHERE subprov_wallet=? ORDER BY detected_at DESC LIMIT 1",
                    (s["subprov"],)
                ).fetchone()
                t2s_mech = live_sess["funding_mechanism"] if live_sess else None
            plain_transfer_associated = _is_plain(t2s_mech) or _is_plain(s["subprov_to_creator_mechanism"])

            if launch is not None:
                src = launch["detection_source"] or ""
                if src in _LIVE_DETECTION_SOURCES:
                    classification = "LIVE_DETECTED"
                elif src in _RECONCILED_SOURCES:
                    classification = "RECONCILED"
                else:
                    # A launch row exists but under an unrecognised source — still a
                    # real detection, just not one of the two named-source buckets.
                    # Report faithfully rather than forcing it into LIVE_DETECTED.
                    classification = "RECONCILED" if src else "LIVE_DETECTED"
                event_time = launch["create_time"]
            else:
                # No live launch row. Was there ever a LIVE_ARMED session covering the
                # relevant window? Check by subprov + whether the session state implies
                # the system believed it was watching at CREATE time.
                session_row = None
                if s["subprov"]:
                    session_relation = eligible_session_relation(conn)
                    session_row = conn.execute(
                        "SELECT monitoring_state, funding_time, expires_at "
                        f"FROM {session_relation} WHERE subprov_wallet=? "
                        "ORDER BY detected_at DESC LIMIT 1", (s["subprov"],)
                    ).fetchone()
                create_time = s["creator_launch_time"] or s["subprov_to_creator_block_time"]
                was_live_armed_at_create = False
                if session_row and session_row["monitoring_state"] == "LIVE_ARMED":
                    ft, exp = session_row["funding_time"], session_row["expires_at"]
                    if create_time and ft and exp and ft <= create_time <= exp:
                        was_live_armed_at_create = True

                # X25.5.1 fix: wt_provisioning_sessions row existence is NOT a
                # membership gate — join to the mint's own authoritative
                # walkback verdict before ever claiming WATCHTOWER_RECOVERED/
                # PIPELINE_INCONSISTENCY (both of which the frontend renders
                # as confirmed operation membership). A partial funding
                # fragment with no confirmed treasury (LINEAGE_GAP,
                # NON_WATCHTOWER, NO_ATTRIBUTION_FOUND, or any
                # known-infrastructure attribution outcome) must never imply
                # membership — it is downgraded to a neutral provenance state.
                queue_row = None
                if _table_exists(conn, "wt_walkback_queue"):
                    queue_row = conn.execute(
                        "SELECT intelligence_outcome FROM wt_walkback_queue WHERE mint=?", (mint,)
                    ).fetchone()
                if queue_row is None:
                    classification = "WALKBACK_INCONCLUSIVE"
                elif queue_row["intelligence_outcome"] not in _CONFIRMED_WALKBACK_OUTCOMES:
                    classification = "WALKBACK_OBSERVED"
                else:
                    classification = "PIPELINE_INCONSISTENCY" if was_live_armed_at_create else "WALKBACK_RECOVERED"
                event_time = create_time

            # Deployment-readiness fix: best-available event timestamp, falling back to
            # walkback's own recorded_at only if no on-chain timestamp is known at all —
            # this is what window_days filters against, so a windowed caller (e.g.
            # detection_path_health) compares like-for-like against wt_watchtower_launches'
            # own create_time-based window rather than an always-all-time count.
            event_time = event_time or s["recorded_at"]
            if window_days is not None:
                cutoff = time.time() - (window_days * 86400)
                if not event_time or event_time < cutoff:
                    continue

            rows.append({
                "mint": mint,
                "treasury": s["treasury"],
                "subprov": s["subprov"],
                "creator": s["creator"],
                "treasury_to_subprov_mechanism": t2s_mech,
                "subprov_to_creator_mechanism": s["subprov_to_creator_mechanism"],
                "plain_transfer_associated": plain_transfer_associated,
                "walkback_recorded_at": s["recorded_at"],
                "live_detection_source": launch["detection_source"] if launch else None,
                "classification": classification,
                "event_time": event_time,
            })

        summary: dict[str, int] = {}
        for r in rows:
            summary[r["classification"]] = summary.get(r["classification"], 0) + 1

        return {"rows": rows, "summary": summary, "total": len(rows)}
    finally:
        conn.close()
