"""
Launcher Observatory — Capability Provider Endpoints.

Provides data for the Launcher Observatory operation registered in Operations OS.
All queries are read-only, retrospective, DB-only (no RPC, no websocket).

Qualification threshold:
  A persistent operator is a funder wallet with:
    >= 3 launches in wt_farm_launches
    AND NOT already classified as WATCHTOWER treasury or subprov

  This conservative threshold avoids counting single-session burst
  operators (common on pump.fun) as persistent identities.

DB sources:
  OPS_DB (wt_ops_v2.db):
    - wt_farm_launches       — independent ground truth (1,359 rows)
    - wt_confirmed_treasuries — WATCHTOWER exclusion list
    - wt_discovered_subprovs  — WATCHTOWER exclusion list

  CREATOR_DB (pumpswap_tokens.db):
    - creator_profile         — operator scan profiles and launch counts
    - creator_tokens          — per-token launch records with timestamps

No Flask imports at module level — Blueprint registered via register function.
"""

from __future__ import annotations

import os
import sqlite3
import time

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OPS_DB_PATH = os.environ.get(
    "OPS_V2_DB_PATH",
    os.path.join(_REPO, "database", "wt_ops_v2.db"),
)
CREATOR_DB_PATH = os.environ.get(
    "PUMPSWAP_TOKENS_DB_PATH",
    os.path.join(_REPO, "pumpswap_tokens.db"),
)

# Persistence qualification threshold.
# Funder must have >= this many launches in wt_farm_launches to qualify.
PERSISTENCE_MIN_LAUNCHES = 3


def _ops_conn() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True, timeout=5)


def _creator_conn() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{CREATOR_DB_PATH}?mode=ro", uri=True, timeout=5)


def _unknown_funder_launches_cte() -> str:
    """SQL fragment: farm launches where the funder is NOT classified by WATCHTOWER."""
    return """
    WITH unknown_launches AS (
        SELECT fl.mint, fl.creator, fl.funder, fl.peak_mc, fl.migrated_at, fl.seed_sol
        FROM wt_farm_launches fl
        LEFT JOIN wt_confirmed_treasuries t ON fl.funder = t.treasury
        LEFT JOIN wt_discovered_subprovs sp ON fl.funder = sp.subprov
        WHERE t.treasury IS NULL AND sp.subprov IS NULL
    ),
    persistent_funders AS (
        SELECT funder, COUNT(*) as launch_count
        FROM unknown_launches
        GROUP BY funder
        HAVING COUNT(*) >= {min_launches}
    )
    """.format(min_launches=PERSISTENCE_MIN_LAUNCHES)


def _get_health() -> dict:
    """Health: is the Launcher Observatory data source accessible?"""
    t0 = time.time()
    try:
        with _ops_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM wt_farm_launches"
            ).fetchone()
            farm_count = row[0] if row else 0

        with _creator_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM creator_profile"
            ).fetchone()
            profile_count = row[0] if row else 0

        latency_ms = int((time.time() - t0) * 1000)
        active = farm_count > 0 and profile_count > 0
        return {
            "ok": True,
            "status": "HEALTHY" if active else "DEGRADED",
            "pipeline_active": active,
            "last_event_detected_at": None,
            "active_alert_count": 0,
            "worker_states": {
                "ops_db_farm_launches": str(farm_count),
                "creator_db_profiles": str(profile_count),
                "query_latency_ms": str(latency_ms),
            },
            "generated_at": int(time.time()),
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "OFFLINE",
            "pipeline_active": False,
            "last_event_detected_at": None,
            "active_alert_count": 1,
            "generated_at": int(time.time()),
            "_error": str(exc),
        }


def _get_failure_attribution() -> dict:
    """Failure attribution: breakdown of why launches remain unattributed."""
    try:
        with _ops_conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM wt_farm_launches").fetchone()[0]

            # WATCHTOWER classified (treasury or subprov)
            wt_known = conn.execute("""
                SELECT COUNT(*) FROM wt_farm_launches fl
                WHERE EXISTS (SELECT 1 FROM wt_confirmed_treasuries t WHERE fl.funder = t.treasury)
                   OR EXISTS (SELECT 1 FROM wt_discovered_subprovs sp WHERE fl.funder = sp.subprov)
            """).fetchone()[0]

            # Unknown: how many funders appear only once (single-event)
            single_event = conn.execute("""
                WITH unknown AS (
                    SELECT fl.funder
                    FROM wt_farm_launches fl
                    LEFT JOIN wt_confirmed_treasuries t ON fl.funder = t.treasury
                    LEFT JOIN wt_discovered_subprovs sp ON fl.funder = sp.subprov
                    WHERE t.treasury IS NULL AND sp.subprov IS NULL
                ),
                counts AS (SELECT funder, COUNT(*) as n FROM unknown GROUP BY funder)
                SELECT SUM(n) FROM counts WHERE n = 1
            """).fetchone()[0] or 0

            # Below threshold (2 launches, not yet persistent)
            below_threshold = conn.execute("""
                WITH unknown AS (
                    SELECT fl.funder
                    FROM wt_farm_launches fl
                    LEFT JOIN wt_confirmed_treasuries t ON fl.funder = t.treasury
                    LEFT JOIN wt_discovered_subprovs sp ON fl.funder = sp.subprov
                    WHERE t.treasury IS NULL AND sp.subprov IS NULL
                ),
                counts AS (SELECT funder, COUNT(*) as n FROM unknown GROUP BY funder)
                SELECT SUM(n) FROM counts WHERE n >= 2 AND n < {min_launches}
            """.format(min_launches=PERSISTENCE_MIN_LAUNCHES)).fetchone()[0] or 0

            # Persistent operators (>= threshold)
            persistent = conn.execute("""
                WITH unknown AS (
                    SELECT fl.funder
                    FROM wt_farm_launches fl
                    LEFT JOIN wt_confirmed_treasuries t ON fl.funder = t.treasury
                    LEFT JOIN wt_discovered_subprovs sp ON fl.funder = sp.subprov
                    WHERE t.treasury IS NULL AND sp.subprov IS NULL
                ),
                counts AS (SELECT funder, COUNT(*) as n FROM unknown GROUP BY funder)
                SELECT SUM(n) FROM counts WHERE n >= {min_launches}
            """.format(min_launches=PERSISTENCE_MIN_LAUNCHES)).fetchone()[0] or 0

        return {
            "ok": True,
            "failure_breakdown": {
                "DISC_WATCHTOWER_KNOWN":  wt_known,
                "DISC_SINGLE_EVENT":      single_event,
                "DISC_BELOW_THRESHOLD":   below_threshold,
                # Persistent operators are NOT a failure — they are attributed
                # by this operation. Listed for completeness.
                "ATTRIBUTED_BY_OPS":      persistent,
            },
            "worst_nodes": {},
            "generated_at": int(time.time()),
        }
    except Exception as exc:
        return {"ok": False, "failure_breakdown": {}, "_error": str(exc),
                "generated_at": int(time.time())}


def _get_behaviour() -> dict:
    """Behaviour summary: operator cadence, activity state, campaign patterns."""
    try:
        with _ops_conn() as conn:
            cte = _unknown_funder_launches_cte()

            # Total persistent operators and their aggregate launches
            row = conn.execute(cte + """
                SELECT
                    COUNT(DISTINCT pf.funder)                         as op_count,
                    SUM(pf.launch_count)                              as total_launches,
                    ROUND(1.0 * SUM(pf.launch_count) / MAX(COUNT(DISTINCT pf.funder), 1), 1)
                                                                      as avg_per_op,
                    SUM(CASE WHEN pf.funder IN (
                        SELECT DISTINCT funder FROM unknown_launches
                        WHERE funder IN (SELECT funder FROM persistent_funders)
                          AND mint IN (
                              SELECT mint FROM wt_farm_launches
                              WHERE detected_at >= strftime('%s','now') - 2592000
                          )
                    ) THEN 1 ELSE 0 END)                              as active_30d
                FROM persistent_funders pf
            """).fetchone()

            op_count, total_launches, avg_per_op, active_30d = row or (0, 0, 0.0, 0)
            active_30d = active_30d or 0

        # Campaign interval from creator_db
        try:
            with _creator_conn() as cconn:
                # Median days between first and last launch for multi-launch creators
                interval_row = cconn.execute("""
                    SELECT AVG(span_days) FROM (
                        SELECT
                            CAST((MAX(created_at) - MIN(created_at)) / 86400.0 AS INTEGER) as span_days
                        FROM creator_tokens
                        GROUP BY creator_address
                        HAVING COUNT(*) >= {min_launches} AND span_days > 0
                    )
                """.format(min_launches=PERSISTENCE_MIN_LAUNCHES)).fetchone()
                avg_campaign_interval = round(interval_row[0], 1) if interval_row and interval_row[0] else None
        except Exception:
            avg_campaign_interval = None

        return {
            "ok": True,
            "operator_count":            op_count or 0,
            "total_launches":            total_launches or 0,
            "avg_launches_per_operator": avg_per_op or 0,
            "active_operators":          active_30d,
            "dormant_operators":         max(0, (op_count or 0) - active_30d),
            "new_operators_30d":         0,  # no timestamp on first-seen in farm_launches
            "avg_campaign_interval_days": avg_campaign_interval,
            "generated_at":              int(time.time()),
        }
    except Exception as exc:
        return {
            "ok": False,
            "operator_count": 0,
            "total_launches": 0,
            "avg_launches_per_operator": 0,
            "_error": str(exc),
            "generated_at": int(time.time()),
        }


def _get_intelligence() -> dict:
    """Intelligence: ranked operator list, notable operators, knowledge enrichments."""
    try:
        with _ops_conn() as conn:
            cte = _unknown_funder_launches_cte()

            top_rows = conn.execute(cte + """
                SELECT pf.funder, pf.launch_count
                FROM persistent_funders pf
                ORDER BY pf.launch_count DESC
                LIMIT 15
            """).fetchall()

            # Format as abbreviated address → count (display-friendly)
            # full_address included so the UI can build /intelligence/entity/<addr> links
            top_ops = {
                r[0][:8] + "…" + r[0][-4:]: {"launch_count": r[1], "full_address": r[0]}
                for r in top_rows
            }

            # Most active in last 30 days: which persistent funders funded recently?
            recent_rows = conn.execute(cte + """
                SELECT ul.funder, COUNT(*) as recent_count
                FROM unknown_launches ul
                JOIN persistent_funders pf ON ul.funder = pf.funder
                WHERE ul.mint IN (
                    SELECT mint FROM wt_farm_launches
                    WHERE detected_at >= strftime('%s','now') - 2592000
                )
                GROUP BY ul.funder
                ORDER BY recent_count DESC
                LIMIT 10
            """).fetchall()

            most_active_30d = {
                r[0][:8] + "…" + r[0][-4:]: {"recent_count": r[1], "full_address": r[0]}
                for r in recent_rows
            }

        # Enrich top operators with Knowledge Layer annotations (read-only, no logic change).
        operator_enrichments: dict[str, list[dict]] = {}
        try:
            from src.knowledge.engine import enrich_batch
            full_addresses = [r[0] for r in top_rows]
            batch = enrich_batch(full_addresses)
            for address, items in batch.items():
                abbr = address[:8] + "…" + address[-4:]
                if items:
                    operator_enrichments[abbr] = [item.to_dict() for item in items]
        except Exception:
            pass   # enrichment is best-effort; intelligence still returns without it

        return {
            "ok": True,
            "top_operators": top_ops,
            "most_active_30d": most_active_30d,
            "returning_operators": [],
            "new_operators": [],
            "operator_enrichments": operator_enrichments,
            "generated_at": int(time.time()),
        }
    except Exception as exc:
        return {
            "ok": False,
            "top_operators": {},
            "_error": str(exc),
            "generated_at": int(time.time()),
        }


def _get_outcome_intelligence() -> dict:
    """Primary KPI: how much of WATCHTOWER's Unknown Scope does this operation explain?"""
    try:
        with _ops_conn() as conn:
            # D1: total unknown scope (funder not WATCHTOWER-known)
            unknown_total = conn.execute("""
                SELECT COUNT(*)
                FROM wt_farm_launches fl
                LEFT JOIN wt_confirmed_treasuries t ON fl.funder = t.treasury
                LEFT JOIN wt_discovered_subprovs sp ON fl.funder = sp.subprov
                WHERE t.treasury IS NULL AND sp.subprov IS NULL
            """).fetchone()[0] or 0

            # Total farm launches (for context)
            total_farm = conn.execute("SELECT COUNT(*) FROM wt_farm_launches").fetchone()[0] or 0

            # Attributed: unknown-scope launches from persistent funders
            cte = _unknown_funder_launches_cte()
            row = conn.execute(cte + """
                SELECT
                    COUNT(DISTINCT pf.funder)   as persistent_funder_count,
                    SUM(pf.launch_count)         as attributed_launches
                FROM persistent_funders pf
            """).fetchone()
            persistent_funders = row[0] or 0
            attributed = row[1] or 0

        remaining = unknown_total - attributed
        rate_pct = round(100.0 * attributed / unknown_total, 1) if unknown_total > 0 else 0.0

        return {
            "ok": True,
            "unknown_scope_total":    unknown_total,
            "explained_by_operators": attributed,
            "remaining_unknown":      remaining,
            "explanation_rate_pct":   rate_pct,
            "watchtower_scope_total": total_farm,
            "persistent_funders":     persistent_funders,
            "threshold_description":  f">= {PERSISTENCE_MIN_LAUNCHES} launches in wt_farm_launches",
            "generated_at": int(time.time()),
        }
    except Exception as exc:
        return {
            "ok": False,
            "unknown_scope_total":    0,
            "explained_by_operators": 0,
            "remaining_unknown":      0,
            "explanation_rate_pct":   0.0,
            "_error": str(exc),
            "generated_at": int(time.time()),
        }


# ── Blueprint registration ────────────────────────────────────────────────────

def register_launcher_observatory_routes(app) -> None:
    """Register Launcher Observatory provider endpoints with the Flask app."""
    from flask import Blueprint, jsonify

    bp = Blueprint("launcher_observatory", __name__)

    @bp.route("/api/ops/launcher-observatory/health")
    def lo_health():
        return jsonify(_get_health())

    @bp.route("/api/ops/launcher-observatory/failure-attribution")
    def lo_failure_attribution():
        return jsonify(_get_failure_attribution())

    @bp.route("/api/ops/launcher-observatory/behaviour")
    def lo_behaviour():
        return jsonify(_get_behaviour())

    @bp.route("/api/ops/launcher-observatory/intelligence")
    def lo_intelligence():
        return jsonify(_get_intelligence())

    @bp.route("/api/ops/launcher-observatory/outcome-intelligence")
    def lo_outcome_intelligence():
        return jsonify(_get_outcome_intelligence())

    app.register_blueprint(bp)
