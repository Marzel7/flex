"""
Buy Swarm Observatory — capability endpoint providers.

Operation archetype: COORDINATED_SWARM
Infrastructure model: MESH

Data source: wt_swarm_buys (ops DB — read-only)
No RPC. No websocket. No new worker. No schema changes.

Qualification rules (all three must be satisfied):
  RULE 1 — MIN_PARTICIPANTS   >= 3 distinct swarm_wallet per mint
  RULE 2 — COORDINATION_WINDOW <= 7200s between first and last observed buy
  RULE 3 — KNOWN_SUBPROV      subprov_wallet IS NOT NULL

See buy_swarm_observatory.yaml for full rationale.
"""

from __future__ import annotations

import os
import time

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OPS_DB_PATH = os.environ.get("OPS_V2_DB_PATH", os.path.join(_REPO, "database", "wt_ops_v2.db"))

# ── Qualification constants ────────────────────────────────────────────────────
MIN_PARTICIPANTS      = 3      # RULE 1: minimum distinct swarm wallets per mint
MAX_WINDOW_SECONDS    = 7200   # RULE 2: 2-hour coordination window
REQUIRE_KNOWN_SUBPROV = True   # RULE 3: subprov_wallet must not be NULL


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _ops_conn():
    import sqlite3
    if not os.path.exists(OPS_DB_PATH):
        raise FileNotFoundError(f"OPS DB not found: {OPS_DB_PATH}")
    conn = sqlite3.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


# ── Shared CTE ────────────────────────────────────────────────────────────────

def _qualified_swarms_cte() -> str:
    """
    CTE that selects mints satisfying all three qualification rules.
    Returns one row per mint with aggregate stats.
    """
    subprov_filter = "AND subprov_wallet IS NOT NULL" if REQUIRE_KNOWN_SUBPROV else ""
    return f"""
    WITH qualified_swarms AS (
        SELECT
            mint,
            subprov_wallet,
            treasury_wallet,
            COUNT(DISTINCT swarm_wallet)                        AS participant_count,
            COUNT(*)                                            AS total_buys,
            MIN(observed_at)                                    AS first_seen,
            MAX(observed_at)                                    AS last_seen,
            MAX(observed_at) - MIN(observed_at)                 AS window_seconds
        FROM wt_swarm_buys
        WHERE 1=1 {subprov_filter}
        GROUP BY mint, subprov_wallet, treasury_wallet
        HAVING participant_count >= {MIN_PARTICIPANTS}
           AND window_seconds    <= {MAX_WINDOW_SECONDS}
    )
    """


# ── Capability providers ───────────────────────────────────────────────────────

def _get_health() -> dict:
    try:
        with _ops_conn() as conn:
            total_obs     = conn.execute("SELECT COUNT(*) FROM wt_swarm_buys").fetchone()[0]
            total_targets = conn.execute("SELECT COUNT(DISTINCT mint) FROM wt_swarm_buys").fetchone()[0]
            total_wallets = conn.execute("SELECT COUNT(DISTINCT swarm_wallet) FROM wt_swarm_buys").fetchone()[0]
            cte = _qualified_swarms_cte()
            qualified     = conn.execute(cte + "SELECT COUNT(*) FROM qualified_swarms").fetchone()[0]
            last_obs_ts   = conn.execute("SELECT MAX(observed_at) FROM wt_swarm_buys").fetchone()[0]

        status = "HEALTHY" if total_obs > 0 else "NO_DATA"
        return {
            "ok": True,
            "status": status,
            "total_observations": total_obs,
            "total_target_mints": total_targets,
            "total_swarm_wallets": total_wallets,
            "qualified_swarms": qualified,
            "last_observation_at": last_obs_ts,
            "qualification_rules": {
                "min_participants": MIN_PARTICIPANTS,
                "max_window_seconds": MAX_WINDOW_SECONDS,
                "require_known_subprov": REQUIRE_KNOWN_SUBPROV,
            },
            "generated_at": int(time.time()),
        }
    except Exception as exc:
        return {"ok": False, "status": "ERROR", "_error": str(exc), "generated_at": int(time.time())}


def _get_failure_attribution() -> dict:
    try:
        with _ops_conn() as conn:
            total = conn.execute("SELECT COUNT(DISTINCT mint) FROM wt_swarm_buys").fetchone()[0]

            # DISC_SINGLE_BUY: only 1 unique wallet
            single = conn.execute("""
                SELECT COUNT(*) FROM (
                    SELECT mint FROM wt_swarm_buys
                    GROUP BY mint HAVING COUNT(DISTINCT swarm_wallet) = 1
                )
            """).fetchone()[0]

            # DISC_BELOW_THRESHOLD: exactly 2 participants
            below = conn.execute("""
                SELECT COUNT(*) FROM (
                    SELECT mint FROM wt_swarm_buys
                    GROUP BY mint HAVING COUNT(DISTINCT swarm_wallet) = 2
                )
            """).fetchone()[0]

            # DISC_WINDOW_TOO_WIDE: >= 3 participants but window > 7200s
            wide = conn.execute(f"""
                SELECT COUNT(*) FROM (
                    SELECT mint FROM wt_swarm_buys
                    WHERE subprov_wallet IS NOT NULL
                    GROUP BY mint
                    HAVING COUNT(DISTINCT swarm_wallet) >= {MIN_PARTICIPANTS}
                       AND MAX(observed_at) - MIN(observed_at) > {MAX_WINDOW_SECONDS}
                )
            """).fetchone()[0]

            # DISC_NO_PROVISIONER: >= 3 participants + good window but no subprov
            no_prov = conn.execute(f"""
                SELECT COUNT(*) FROM (
                    SELECT mint FROM wt_swarm_buys
                    WHERE subprov_wallet IS NULL
                    GROUP BY mint
                    HAVING COUNT(DISTINCT swarm_wallet) >= {MIN_PARTICIPANTS}
                       AND MAX(observed_at) - MIN(observed_at) <= {MAX_WINDOW_SECONDS}
                )
            """).fetchone()[0]

            # QUALIFIED: all rules satisfied
            cte = _qualified_swarms_cte()
            qualified = conn.execute(cte + "SELECT COUNT(*) FROM qualified_swarms").fetchone()[0]

        return {
            "ok": True,
            "total_targets_observed": total,
            "qualified": qualified,
            "failure_breakdown": {
                "DISC_SINGLE_BUY":      single,
                "DISC_BELOW_THRESHOLD": below,
                "DISC_WINDOW_TOO_WIDE": wide,
                "DISC_NO_PROVISIONER":  no_prov,
            },
            "generated_at": int(time.time()),
        }
    except Exception as exc:
        return {"ok": False, "_error": str(exc), "generated_at": int(time.time())}


def _get_behaviour() -> dict:
    try:
        with _ops_conn() as conn:
            cte = _qualified_swarms_cte()

            stats = conn.execute(cte + """
                SELECT
                    COUNT(*)                        AS swarm_count,
                    SUM(participant_count)          AS total_participants,
                    AVG(participant_count)          AS avg_participants,
                    MAX(participant_count)          AS max_participants,
                    AVG(window_seconds)             AS avg_window_seconds,
                    MIN(window_seconds)             AS min_window_seconds,
                    MAX(window_seconds)             AS max_window_seconds,
                    COUNT(DISTINCT treasury_wallet) AS distinct_operators
                FROM qualified_swarms
            """).fetchone()

            # Campaigns: distinct (treasury_wallet, subprov_wallet) combos with >=1 swarm
            campaign_count = conn.execute(cte + """
                SELECT COUNT(*) FROM (
                    SELECT treasury_wallet, subprov_wallet
                    FROM qualified_swarms
                    WHERE treasury_wallet IS NOT NULL
                    GROUP BY treasury_wallet, subprov_wallet
                )
            """).fetchone()[0]

        n = stats["swarm_count"] or 0
        total_p = stats["total_participants"] or 0
        avg_p   = round(stats["avg_participants"] or 0, 1)
        ops     = stats["distinct_operators"] or 0

        return {
            "ok": True,
            "swarm_count": n,
            "total_participants": total_p,
            "avg_participants_per_swarm": avg_p,
            "max_participants_in_swarm": stats["max_participants"] or 0,
            "avg_window_seconds": round(stats["avg_window_seconds"] or 0, 0),
            "campaign_count": campaign_count,
            "distinct_operators": ops,
            "generated_at": int(time.time()),
        }
    except Exception as exc:
        return {"ok": False, "swarm_count": 0, "_error": str(exc), "generated_at": int(time.time())}


def _get_intelligence() -> dict:
    try:
        with _ops_conn() as conn:
            cte = _qualified_swarms_cte()

            # Top swarms by participant count
            top_rows = conn.execute(cte + """
                SELECT mint, participant_count, window_seconds,
                       subprov_wallet, treasury_wallet,
                       first_seen, last_seen
                FROM qualified_swarms
                ORDER BY participant_count DESC
                LIMIT 10
            """).fetchall()

            # Top operators (treasury_wallet) by swarm count
            top_ops = conn.execute(cte + """
                SELECT COALESCE(treasury_wallet, 'UNKNOWN') AS operator,
                       COUNT(*) AS swarm_count,
                       SUM(participant_count) AS total_participants,
                       COUNT(DISTINCT subprov_wallet) AS subprov_count
                FROM qualified_swarms
                GROUP BY operator
                ORDER BY swarm_count DESC
                LIMIT 8
            """).fetchall()

            # Campaign breakdown
            campaigns = conn.execute(cte + """
                SELECT COALESCE(treasury_wallet,'UNKNOWN') AS treasury,
                       subprov_wallet,
                       COUNT(*) AS targets,
                       SUM(participant_count) AS participants,
                       MIN(first_seen) AS first_ts,
                       MAX(last_seen) AS last_ts
                FROM qualified_swarms
                GROUP BY treasury, subprov_wallet
                ORDER BY targets DESC
                LIMIT 10
            """).fetchall()

        # Enrich top swarms with Knowledge Layer annotations
        top_swarms_out = []
        subprov_addresses = [r["subprov_wallet"] for r in top_rows if r["subprov_wallet"]]
        enrichments: dict[str, list[dict]] = {}
        try:
            from src.knowledge.engine import enrich_batch
            batch = enrich_batch(subprov_addresses)
            for addr, items in batch.items():
                if items:
                    enrichments[addr] = [item.to_dict() for item in items]
        except Exception:
            pass

        for r in top_rows:
            abbr = r["mint"][:8] + "…" + r["mint"][-4:]
            entry: dict = {
                "mint_abbr":         abbr,
                "mint":              r["mint"],
                "participant_count": r["participant_count"],
                "window_seconds":    r["window_seconds"],
                "first_seen":        r["first_seen"],
                "last_seen":         r["last_seen"],
                "subprov_wallet":    r["subprov_wallet"],
                "treasury_wallet":   r["treasury_wallet"],
            }
            if r["subprov_wallet"] and r["subprov_wallet"] in enrichments:
                entry["subprov_knowledge"] = enrichments[r["subprov_wallet"]]
            top_swarms_out.append(entry)

        top_operators_out = {
            r["operator"][:8] + "…" + r["operator"][-4:] if len(r["operator"]) > 16 else r["operator"]: {
                "swarm_count":        r["swarm_count"],
                "total_participants": r["total_participants"],
                "subprov_count":      r["subprov_count"],
                "full_address":       r["operator"] if r["operator"] != "UNKNOWN" else None,
            }
            for r in top_ops
        }

        campaigns_out = [
            {
                "treasury":           (r["treasury"][:8] + "…" + r["treasury"][-4:]) if len(r["treasury"]) > 16 else r["treasury"],
                "treasury_full":      r["treasury"] if r["treasury"] != "UNKNOWN" else None,
                "subprov":            (r["subprov_wallet"][:8] + "…" + r["subprov_wallet"][-4:]) if r["subprov_wallet"] else "—",
                "subprov_full":       r["subprov_wallet"],
                "target_count":       r["targets"],
                "participants":       r["participants"],
                "first_seen":         r["first_ts"],
                "last_seen":          r["last_ts"],
            }
            for r in campaigns
        ]

        return {
            "ok": True,
            "top_swarms": top_swarms_out,
            "top_operators": top_operators_out,
            "campaigns": campaigns_out,
            "generated_at": int(time.time()),
        }
    except Exception as exc:
        return {"ok": False, "top_swarms": [], "_error": str(exc), "generated_at": int(time.time())}


def _get_outcome_intelligence() -> dict:
    try:
        with _ops_conn() as conn:
            total_targets = conn.execute(
                "SELECT COUNT(DISTINCT mint) FROM wt_swarm_buys"
            ).fetchone()[0] or 0

            cte = _qualified_swarms_cte()
            row = conn.execute(cte + """
                SELECT
                    COUNT(*) AS qualified_count,
                    SUM(participant_count) AS attributed_participants,
                    COUNT(DISTINCT treasury_wallet) AS operators
                FROM qualified_swarms
            """).fetchone()

            qualified   = row["qualified_count"] or 0
            remaining   = total_targets - qualified
            rate_pct    = round(100.0 * qualified / total_targets, 1) if total_targets else 0.0

        return {
            "ok": True,
            "total_targets_observed": total_targets,
            "qualified_swarms":       qualified,
            "remaining_unqualified":  remaining,
            "qualification_rate_pct": rate_pct,
            "attributed_participants": row["attributed_participants"] or 0,
            "distinct_operators":      row["operators"] or 0,
            "threshold_description": (
                f">= {MIN_PARTICIPANTS} participants, "
                f"<= {MAX_WINDOW_SECONDS}s window, "
                f"known subprov"
            ),
            "generated_at": int(time.time()),
        }
    except Exception as exc:
        return {
            "ok": False,
            "total_targets_observed": 0,
            "qualified_swarms": 0,
            "remaining_unqualified": 0,
            "qualification_rate_pct": 0.0,
            "_error": str(exc),
            "generated_at": int(time.time()),
        }


# ── Swarm detail helper (used by Entity Intelligence adapter) ─────────────────

def get_swarm_buys_for_wallet(wallet_address: str) -> list[dict]:
    """
    Return all wt_swarm_buys rows where swarm_wallet = wallet_address.
    Used by the Entity Intelligence aggregator to enrich participant observations.
    Read-only. No RPC.
    """
    try:
        with _ops_conn() as conn:
            rows = conn.execute(
                """
                SELECT mint, subprov_wallet, treasury_wallet, swap_signature, observed_at
                FROM wt_swarm_buys
                WHERE swarm_wallet = ?
                ORDER BY observed_at ASC
                """,
                (wallet_address,),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def get_swarm_buys_for_subprov(subprov_address: str) -> list[dict]:
    """
    Return aggregate swarm stats where subprov_wallet = subprov_address.
    Used by the Entity Intelligence aggregator for subprov entities.
    """
    try:
        with _ops_conn() as conn:
            cte = _qualified_swarms_cte()
            rows = conn.execute(
                cte + """
                SELECT mint, participant_count, window_seconds, first_seen, last_seen, treasury_wallet
                FROM qualified_swarms
                WHERE subprov_wallet = ?
                ORDER BY participant_count DESC
                """,
                (subprov_address,),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


# ── Route registration ────────────────────────────────────────────────────────

def register_buy_swarm_observatory_routes(app) -> None:
    """Register Buy Swarm Observatory provider endpoints with the Flask app."""
    from flask import Blueprint, jsonify

    bp = Blueprint("buy_swarm_observatory", __name__)

    @bp.route("/api/ops/buy-swarm-observatory/health")
    def bso_health():
        return jsonify(_get_health())

    @bp.route("/api/ops/buy-swarm-observatory/failure-attribution")
    def bso_failure_attribution():
        return jsonify(_get_failure_attribution())

    @bp.route("/api/ops/buy-swarm-observatory/behaviour")
    def bso_behaviour():
        return jsonify(_get_behaviour())

    @bp.route("/api/ops/buy-swarm-observatory/intelligence")
    def bso_intelligence():
        return jsonify(_get_intelligence())

    @bp.route("/api/ops/buy-swarm-observatory/outcome-intelligence")
    def bso_outcome_intelligence():
        return jsonify(_get_outcome_intelligence())

    app.register_blueprint(bp)
