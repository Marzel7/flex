"""X24.2 Phase 5 — detection-path health reporting.

Read-only classification of HOW each wt_watchtower_launches row was armed and
detected, distinguishing the intended primary live path from the fallback
paths currently doing most of the real work (per the X24.1/X24.2
reconciliation: the RPC sweep is the actual primary detector today, not the
disabled WS subprov tier).

Categories (all real, persisted detection_source values — never invented):
  LIVE_SUBPROV_WS      — armed via a live WS notification for the subprov
                          itself (kind="subprov"/"subprov_account" in
                          ws_cascade.py's _on_message dispatch). Not yet
                          observed in production while WS_SUBPROV_WATCH_ENABLED=0.
  ACTIVE_CATCHUP       — armed via the immediate 0s/2s/5s/10s catch-up burst
                          fired right after a candidate is registered.
  OPENING_CATCHUP      — armed via the one-time catch-up scan when the WS
                          stream first opens.
  PROGRAM_LOGS         — the pump.fun CREATE log matched an already-armed
                          candidate directly off the shared program stream.
  PENDING_CREATE_RETRY — a program log seen before the candidate was armed,
                          recovered from the pending/replay buffer.
  RECONCILE / BACKFILL / REPLAY — offline reconciliation-sourced launches.
  MANUAL_USER_ATTESTATION — a human-attested launch (not an automated detector).
  <other/unrecognised> — reported honestly, not silently bucketed.

This module does not distinguish whether a launch's underlying candidate was
armed via subprov_sweep_pass() (RPC polling) vs a genuine live WS notification
for cases OTHER than LIVE_SUBPROV_WS, because wt_watchtower_launches does not
persist which arming mechanism registered the candidate — only the CREATE-
side detection_source. This is a real reporting limitation, not an omission;
see the module docstring in detection_reconciliation.py for the walkback-only
classification, which is the complementary read for launches that were never
armed at all.
"""
from __future__ import annotations

import os
import sqlite3
from typing import Any

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
OPS_DB_PATH = os.environ.get("OPS_V2_DB_PATH", os.path.join(_REPO_ROOT, "database", "wt_ops_v2.db"))

# Grouping into the sprint's four health-summary buckets. Every real
# detection_source value must land in exactly one bucket; an unrecognised
# value is reported under "OTHER_UNCLASSIFIED" rather than silently dropped.
_PRIMARY_LIVE = {"LIVE_SUBPROV_WS"}
_CATCH_UP = {"ACTIVE_CATCHUP", "OPENING_CATCHUP", "PROGRAM_LOGS"}
_RETRY_RECOVERY = {"PENDING_CREATE_RETRY", "RECONCILE", "RECONCILER", "BACKFILL", "REPLAY"}
_MANUAL = {"MANUAL_USER_ATTESTATION", "MANUAL"}


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _bucket(detection_source: str) -> str:
    if detection_source in _PRIMARY_LIVE:
        return "primary_live_path"
    if detection_source in _CATCH_UP:
        return "catch_up_path"
    if detection_source in _RETRY_RECOVERY:
        return "retry_recovery_path"
    if detection_source in _MANUAL:
        return "manual"
    return "other_unclassified"


def detection_path_health(ops_db_path: str = OPS_DB_PATH, *, window_days: int = 30) -> dict[str, Any]:
    """Measured baseline only — no target percentages invented. Returns per-
    detection_source counts, the four-bucket summary, and a walkback-only
    (never-armed) count for the same window, so the UI can show what fraction
    of recent WATCHTOWER attribution is surviving on fallback paths versus the
    intended primary live path."""
    conn = _connect(ops_db_path)
    try:
        rows = conn.execute(
            "SELECT detection_source, COUNT(*) as n FROM wt_watchtower_launches "
            "WHERE create_time > strftime('%s','now') - ? "
            "GROUP BY detection_source", (window_days * 86400,)
        ).fetchall()
        by_source = {r["detection_source"] or "NULL": r["n"] for r in rows}
        total = sum(by_source.values())

        bucket_counts: dict[str, int] = {
            "primary_live_path": 0, "catch_up_path": 0,
            "retry_recovery_path": 0, "manual": 0, "other_unclassified": 0,
        }
        for src, n in by_source.items():
            bucket_counts[_bucket(src)] += n

        # Deployment-readiness fix: classify_walkback_confirmed_launches previously had
        # no time-window parameter and always scanned all-time data, while the
        # bucket_summary above is windowed by window_days — the two headline numbers
        # were being compared on different time bases. Passing the SAME window_days
        # here makes them directly comparable (both "in the last N days").
        walkback_only = 0
        try:
            from src.ops.detection_reconciliation import classify_walkback_confirmed_launches
            recon = classify_walkback_confirmed_launches(ops_db_path, window_days=window_days)
            walkback_only = sum(
                1 for r in recon.get("rows", [])
                if r["classification"] in ("WALKBACK_RECOVERED", "PIPELINE_INCONSISTENCY")
            )
        except (ImportError, OSError, sqlite3.Error):
            walkback_only = None  # honestly report "not available" rather than 0

        return {
            "window_days": window_days,
            "total_live_detected_launches": total,
            "by_detection_source": by_source,
            "bucket_summary": bucket_counts,
            "walkback_only_or_pipeline_gap_count": walkback_only,
            "note": (
                "primary_live_path is the intended architecture (live WS notification for "
                "the subprov itself). If it reads 0 while catch_up_path/retry_recovery_path "
                "carry the volume, WATCHTOWER is currently surviving on fallback paths, not "
                "operating through its primary path. No target percentage is asserted here — "
                "this is a measured baseline only."
            ),
        }
    finally:
        conn.close()
