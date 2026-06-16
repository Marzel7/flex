"""
Operation-Centric Intelligence Dashboard (Phase 1.4 — UI).

Read-only dashboard over the wt_ops_v2 store built by the operation-centric system
(Phases 1/1.1/1.2/2/1.3). Surfaces what was previously headless: operations, families,
lifecycle states, live expansion candidates, activity events, and scheduler run logs.

ISOLATION: reads ONLY database/wt_ops_v2.db. Touches nothing in the live WATCH
pipeline, wt_operations, attribution, or classification. Registered as a Flask
blueprint via register_operation_dashboard_routes(app) — one line in main.py,
mirroring the existing register_dashboard_routes pattern.

Routes:
  GET /operations-v2                       → the dashboard page
  GET /api/ops-v2/summary                  → top-line counts + scheduler health
  GET /api/ops-v2/operations               → operation list with lifecycle + rollups
  GET /api/ops-v2/operation/<uuid>         → one operation: wallets, edges, candidates
  GET /api/ops-v2/candidates               → live pre-launch creator candidates
  GET /api/ops-v2/activity                 → recent expansion events
  GET /api/ops-v2/runs                     → scheduler run log
"""

import os
import sqlite3
import time
import json as _json

from flask import Blueprint, render_template, jsonify, request, redirect
from src.utils.db_locking import db_connect

_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "../.."))
OPS_DB_PATH = os.environ.get("OPS_V2_DB_PATH", os.path.join(_REPO_ROOT, "database", "wt_ops_v2.db"))

# Live views show classified operations (WATCHTOWER + MICRO_DEPLOYER — both real operator
# activity). EXCLUDE: legacy rejected rows, and UNTEMPLATED ops (no …039280 template =
# not part of the operator-template mechanism, so not a tracked operation).
_LIVE_OPS_EXCLUDE = ("(o.status IS NULL OR o.status != 'REJECTED_SERIAL_DEPLOYER') "
                     "AND (o.op_type IS NULL OR o.op_type != 'UNTEMPLATED')")
_LIVE_OPS_EXCLUDE_NOALIAS = ("(status IS NULL OR status != 'REJECTED_SERIAL_DEPLOYER') "
                             "AND (op_type IS NULL OR op_type != 'UNTEMPLATED')")

ops_dashboard_bp = Blueprint("ops_dashboard", __name__)


def _conn():
    c = db_connect(OPS_DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def _table_exists(c, name) -> bool:
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _column_exists(c, table, col) -> bool:
    try:
        return any(r[1] == col for r in c.execute(f"PRAGMA table_info({table})").fetchall())
    except Exception:
        return False


# ════════════════════════════════════════════════════════════════════════════
#  OPERATIONS OS — reuse of the campaigns dashboard shell, powered by wt_ops_v2.
#  These endpoints return the EXACT JSON shapes watchtower_dashboard.html's JS
#  expects, so the existing SVG graph / tempo bar / panels / feed render the
#  operation-centric model with no render-layer changes.
# ════════════════════════════════════════════════════════════════════════════

# map wt_ops_v2 wallet roles -> the dashboard's existing node_type styling enum
_ROLE_TO_NODE = {
    "TREASURY": "TREASURY",         # anchor, r22, gold
    "COLLECTOR": "COORDINATOR",     # r16, orange
    "PASS_THROUGH": "RELAY",        # r11
    "DIRECT_FUNDER": "SIGNALLER",   # r9
}


@ops_dashboard_bp.route("/api/ops-v2/_empty")
def api_v2_empty():
    """Harmless empty payload for dashboard fetches that have no v2 analog
    (legacy hit-activity / signals fallbacks). Keeps the reused JS happy."""
    return jsonify({"wallets": [], "events": [], "signals": [], "bursts": []})


@ops_dashboard_bp.route("/api/ops-v2/graph")
def api_v2_graph():
    """Treasury-rooted operation topology as graph nodes/links, mapped onto the
    dashboard's existing node_type styling. Treasury center → collectors →
    pass-throughs → candidate creators / migrated creators."""
    c = _conn()
    try:
        nodes, links, seen = [], [], set()

        def add_node(nid, node_type, last_at=None, extra=None):
            if not nid or nid in seen:
                return
            seen.add(nid)
            n = {"id": nid, "node_type": node_type}
            if last_at:
                n["last_activity_at"] = last_at
            if extra:
                n.update(extra)
            nodes.append(n)

        # infrastructure wallets per operation
        wallets = c.execute(
            "SELECT operation_uuid, wallet, role, last_seen FROM wt_ops_v2_wallets").fetchall()
        op_treasury = {r["operation_uuid"]: r["treasury_root"] for r in
                       c.execute("SELECT operation_uuid, treasury_root FROM wt_ops_v2").fetchall()}
        for r in wallets:
            add_node(r["wallet"], _ROLE_TO_NODE.get(r["role"], "UNKNOWN"), r["last_seen"])

        # edges (real on-chain value flow) -> classify by endpoint roles
        role_of = {r["wallet"]: r["role"] for r in wallets}
        for e in c.execute("SELECT from_wallet, to_wallet FROM wt_ops_v2_edges").fetchall():
            fr, to = e["from_wallet"], e["to_wallet"]
            if fr not in seen or to not in seen:
                continue
            rf = role_of.get(fr)
            et = ("treasury_to_subprov" if rf == "TREASURY"
                  else "subprov_to_wallet" if rf == "COLLECTOR" else "creator_link")
            links.append({"source": fr, "target": to, "edge_type": et})

        # candidate creators (cyan) + migrated creators (orange), linked to their source wallet
        if _table_exists(c, "wt_operation_candidates"):
            for r in c.execute(
                "SELECT wallet, source_wallet, first_seen, template_base FROM wt_operation_candidates "
                "ORDER BY first_seen DESC LIMIT 400").fetchall():
                add_node(r["wallet"], "CREATOR", r["first_seen"])
                if r["source_wallet"] in seen:
                    links.append({"source": r["source_wallet"], "target": r["wallet"], "edge_type": "creator_link"})
        # migrated creators promote to CREATOR_HC
        for r in c.execute(
            "SELECT creator_wallet, migration_time FROM wt_ops_v2_creators WHERE migration_time IS NOT NULL "
            "ORDER BY migration_time DESC LIMIT 200").fetchall():
            # upgrade node if present, else add
            existing = next((n for n in nodes if n["id"] == r["creator_wallet"]), None)
            if existing:
                existing["node_type"] = "CREATOR_HC"
                existing["last_activity_at"] = r["migration_time"]
            else:
                add_node(r["creator_wallet"], "CREATOR_HC", r["migration_time"])
        return jsonify({"nodes": nodes, "links": links})
    finally:
        c.close()


def _v2_op_state():
    """Derive the global op_state (dashboard enum) from the lifecycle distribution."""
    c = _conn()
    try:
        states = {r["state"]: r["n"] for r in c.execute(
            "SELECT state, COUNT(*) n FROM wt_operation_lifecycle GROUP BY state").fetchall()} \
            if _table_exists(c, "wt_operation_lifecycle") else {}
        cand24 = c.execute("SELECT COUNT(*) FROM wt_operation_candidates WHERE first_seen > strftime('%s','now')-86400").fetchone()[0] \
            if _table_exists(c, "wt_operation_candidates") else 0
        prov = states.get("PROVISIONING", 0)
        seen = states.get("CREATORS_SEEN", 0) + states.get("REACTIVATED", 0)
        if prov >= 3 or seen > 0:
            st, detail = "ESCALATING", f"{prov} provisioning · {seen} creators-seen · {cand24} candidates/24h"
        elif prov > 0:
            st, detail = "ACTIVE", f"{prov} operation(s) provisioning · {cand24} candidates/24h"
        elif cand24 > 0:
            st, detail = "FORMING", f"{cand24} candidate creators in 24h"
        elif sum(states.values()) and states.get("DORMANT", 0) == sum(states.values()):
            st, detail = "DORMANT", "all tracked operations dormant"
        else:
            st, detail = "QUIET", "monitor watching"
        return st, detail, states, cand24
    finally:
        c.close()


@ops_dashboard_bp.route("/api/ops-v2/ops-overview")
def api_v2_ops_overview():
    """Drives the graph's global state class + overlays (op_state enum)."""
    st, detail, states, cand24 = _v2_op_state()
    return jsonify({"ok": True, "op_state": st, "op_detail": detail,
                    "active_corridors": states.get("PROVISIONING", 0),
                    "active_corridor_wallets": [], "extraction": None,
                    "treasury": None, "swarms": []})


@ops_dashboard_bp.route("/api/ops-v2/campaigns")
def api_v2_campaigns():
    """LEFT 'LIVE OPERATIONS' panel — operations mapped onto the campaign card shape.
    trader_count←candidates, corridor_count/active_corridors←collectors, state←lifecycle."""
    c = _conn()
    try:
        st_map = {r["operation_uuid"]: (r["state"], r["last_activity"]) for r in c.execute(
            "SELECT operation_uuid, state, last_activity FROM wt_operation_lifecycle").fetchall()} \
            if _table_exists(c, "wt_operation_lifecycle") else {}
        cd_map = {r["operation_uuid"]: r["n"] for r in c.execute(
            "SELECT operation_uuid, COUNT(*) n FROM wt_operation_candidates GROUP BY operation_uuid").fetchall()} \
            if _table_exists(c, "wt_operation_candidates") else {}
        # map v2 lifecycle -> the dashboard's campaign state vocabulary
        STATE = {"PROVISIONING": "ACTIVE", "CREATORS_SEEN": "ESCALATING", "REACTIVATED": "ESCALATING",
                 "ACTIVE": "ACTIVE", "MIGRATED": "RECYCLING", "DORMANT": "DORMANT", "DISCOVERED": "FORMING"}
        out = []
        _has_optype = _column_exists(c, "wt_ops_v2", "op_type")
        _optype_col = "o.op_type" if _has_optype else "'WATCHTOWER' AS op_type"
        for r in c.execute(f"""
            SELECT o.operation_uuid, o.treasury_root, {_optype_col},
                   (SELECT COUNT(*) FROM wt_ops_v2_wallets w WHERE w.operation_uuid=o.operation_uuid AND role='COLLECTOR') collectors,
                   (SELECT COUNT(*) FROM wt_ops_v2_creators cr WHERE cr.operation_uuid=o.operation_uuid AND cr.migration_time IS NOT NULL) migrated
            FROM wt_ops_v2 o WHERE """ + _LIVE_OPS_EXCLUDE).fetchall():
            s = st_map.get(r["operation_uuid"], (None, None))
            out.append({
                "id": r["operation_uuid"][:8], "operation_uuid": r["operation_uuid"],
                "provisioner_address": r["treasury_root"],
                "op_type": r["op_type"] or "WATCHTOWER",
                "state": STATE.get(s[0] or "DISCOVERED", "FORMING"),
                "last_activity_at": s[1],
                "trader_count": cd_map.get(r["operation_uuid"], 0),       # candidates
                "corridor_count": r["collectors"], "active_corridors": r["collectors"],
                "sweep_count": r["migrated"], "total_sol_provisioned": 0,
            })
        _pri = {"ACTIVE": 0, "ESCALATING": 1, "FORMING": 3, "RECYCLING": 4, "DORMANT": 5}
        out.sort(key=lambda x: (_pri.get(x["state"], 9), -(x["last_activity_at"] or 0)))
        return jsonify({"campaigns": out})
    finally:
        c.close()


@ops_dashboard_bp.route("/api/ops-v2/creators")
def api_v2_creators():
    """'CREATOR CANDIDATES' panel — wt_operation_candidates mapped onto the candidate
    card shape. score←confidence*100, evidence carries template/funding."""
    import json as _json
    c = _conn()
    try:
        if not _table_exists(c, "wt_operation_candidates"):
            return jsonify({"candidates": []})
        rows = c.execute("""
            SELECT cc.wallet, cc.source_wallet, cc.source_role, cc.amount, cc.template_base,
                   cc.confidence, cc.status, cc.first_seen, o.treasury_root, o.operation_uuid,
                   (SELECT 1 FROM wt_ops_v2_creators cr WHERE cr.creator_wallet=cc.wallet) migrated
            FROM wt_operation_candidates cc JOIN wt_ops_v2 o ON o.operation_uuid=cc.operation_uuid
            ORDER BY cc.template_base IS NOT NULL DESC, cc.confidence DESC, cc.first_seen DESC
            LIMIT 60""").fetchall()
        cands = []
        for r in rows:
            ev = {"funding_sol": r["amount"], "provisioner": r["source_wallet"],
                  "template_base": r["template_base"], "fingerprint_match": r["template_base"] is not None,
                  "operation_uuid": r["operation_uuid"]}
            cands.append({
                "address": r["wallet"], "wallet_address": r["wallet"],
                "score": round((r["confidence"] or 0) * 100),
                "state": "MIGRATED" if r["migrated"] else (r["status"] or "PENDING"),
                "first_signal_at": r["first_seen"], "provisioned_at": r["first_seen"],
                "candidate_reason": "template_funded" if r["template_base"] is not None else "funded_child",
                "evidence_json": _json.dumps(ev), "evidence": ev,
                "evidence_grade": "STRONG" if r["template_base"] is not None else "MEDIUM",
                "source_operator": r["operation_uuid"][:8],
            })
        return jsonify({"candidates": cands, "results": cands, "wallets": cands})
    finally:
        c.close()


@ops_dashboard_bp.route("/api/ops-v2/tempo")
def api_v2_tempo():
    """TOP TEMPO BAR — operation states mapped onto the tempo metric keys."""
    st, detail, states, cand24 = _v2_op_state()
    c = _conn()
    try:
        migrated_today = c.execute("SELECT COUNT(*) FROM wt_ops_v2_creators WHERE migration_time > strftime('%s','now','start of day')").fetchone()[0]
        new_ops_24h = c.execute("SELECT COUNT(*) FROM wt_ops_v2 WHERE first_seen > strftime('%s','now')-86400 AND "+_LIVE_OPS_EXCLUDE_NOALIAS).fetchone()[0]
    finally:
        c.close()
    return jsonify({
        "launch_confirmed": states.get("PROVISIONING", 0),       # ACTIVE OPERATIONS proxy
        "launch_relay_linked": states.get("CREATORS_SEEN", 0),
        "relay_funded_dormant": states.get("DORMANT", 0),
        "profit_extraction": states.get("MIGRATED", 0),
        "collector_side": cand24,
        "related_total": sum(states.values()),
        "confirmed_hubs": new_ops_24h,
        "armed_operations": migrated_today,
        "daily_confirmed_launches": [],
    })


@ops_dashboard_bp.route("/api/ops-v2/metrics")
def api_v2_metrics():
    """Top command-bar metrics (#cm-*) sourced from wt_ops_v2."""
    c = _conn()
    try:
        states = {r["state"]: r["n"] for r in c.execute(
            "SELECT state, COUNT(*) n FROM wt_operation_lifecycle GROUP BY state").fetchall()} \
            if _table_exists(c, "wt_operation_lifecycle") else {}
        cand = c.execute("SELECT COUNT(*) FROM wt_operation_candidates").fetchone()[0] if _table_exists(c, "wt_operation_candidates") else 0
        cand24 = c.execute("SELECT COUNT(*) FROM wt_operation_candidates WHERE first_seen > strftime('%s','now')-86400").fetchone()[0] if _table_exists(c, "wt_operation_candidates") else 0
        migr = c.execute("SELECT COUNT(*) FROM wt_ops_v2_creators WHERE migration_time IS NOT NULL").fetchone()[0]
        rpc_today = int(c.execute("SELECT COALESCE(SUM(rpc_used),0) FROM wt_ops_v2_runs WHERE started_at >= strftime('%s','now','start of day')").fetchone()[0] or 0) if _table_exists(c, "wt_ops_v2_runs") else 0
        active = sum(states.get(s, 0) for s in ("PROVISIONING", "CREATORS_SEEN", "REACTIVATED", "ACTIVE"))
        fams = c.execute("SELECT COUNT(*) FROM wt_ops_v2_families").fetchone()[0]
        return jsonify({
            "active_campaigns": active,
            "coordinated_count": cand24, "active_trader_wallets": cand24,
            "corridors_resolved_24h": fams,
            "sweep_epochs_24h": migr,
            "total_swept_24h_sol": rpc_today,
            "reload_cycles_24h": states.get("REACTIVATED", 0),
            "confirmed_launches_24h": c.execute("SELECT COUNT(*) FROM wt_ops_v2_creators WHERE migration_time > strftime('%s','now')-86400").fetchone()[0],
            "new_subprov_24h": c.execute("SELECT COUNT(*) FROM wt_ops_v2 WHERE first_seen > strftime('%s','now')-86400 AND "+_LIVE_OPS_EXCLUDE_NOALIAS).fetchone()[0],
            "treasury_out_24h_sol": rpc_today,
            "hc_creator_count": migr, "creator_candidate_count": cand,
            "active_pamm_campaigns": fams,
        })
    finally:
        c.close()


@ops_dashboard_bp.route("/api/ops-v2/events-feed")
def api_v2_events_feed():
    """RIGHT event-feed panel — typed operation events from wt_operation_activity +
    migrations + lifecycle transitions."""
    c = _conn()
    try:
        limit = int(request.args.get("limit", 60))
        since = request.args.get("since")
        events = []
        if _table_exists(c, "wt_operation_activity"):
            q = ("SELECT operation_uuid, wallet, counterparty, event_type, amount, block_time "
                 "FROM wt_operation_activity")
            params = []
            if since:
                q += " WHERE block_time > ?"; params.append(int(since))
            q += " ORDER BY block_time DESC LIMIT ?"; params.append(limit)
            for r in c.execute(q, params).fetchall():
                et = {"NEW_CREATOR_CANDIDATE": "CREATOR_CANDIDATE", "NEW_CHILD": "TREASURY_FANOUT",
                      "FUNDING": "treasury_funded"}.get(r["event_type"], r["event_type"])
                events.append({
                    "type": et, "event_type": et, "address": r["counterparty"],
                    "wallet_address": r["counterparty"], "campaign_id": r["operation_uuid"][:8],
                    "ts": r["block_time"], "created_at": r["block_time"],
                    "label": et.replace("_", " ").title(),
                    "detail": (f"{round(r['amount'],3)} SOL" if r["amount"] else ""),
                    "id": f"{r['operation_uuid'][:8]}:{r['block_time']}:{(r['counterparty'] or '')[:8]}",
                    "payload": {"operation_uuid": r["operation_uuid"], "href": f"/ops/operation/{r['operation_uuid']}"},
                })
        # creator migrations as high-priority events
        for r in c.execute("SELECT operation_uuid, creator_wallet, token_mint, migration_time FROM wt_ops_v2_creators WHERE migration_time IS NOT NULL ORDER BY migration_time DESC LIMIT 20").fetchall():
            events.append({"type": "PUMPFUN_CREATE_CONFIRMED", "event_type": "CREATOR_MIGRATED",
                           "address": r["creator_wallet"], "campaign_id": r["operation_uuid"][:8],
                           "ts": r["migration_time"], "label": "Creator migrated",
                           "detail": (r["token_mint"] or "")[:16], "mint": r["token_mint"],
                           "id": f"mig:{r['creator_wallet'][:8]}:{r['migration_time']}",
                           "payload": {"href": f"/ops/operation/{r['operation_uuid']}"}})
        events.sort(key=lambda x: x["ts"] or 0, reverse=True)
        return jsonify({"events": events[:limit]})
    finally:
        c.close()


@ops_dashboard_bp.route("/api/ops-v2/scheduler")
def api_v2_scheduler():
    """Scheduler health widget — running?, last intake/forward, rpc today, budget."""
    import os as _os
    c = _conn()
    try:
        out = {"running": False, "last_intake": None, "last_forward": None,
               "rpc_today": 0, "budget_status": "ok"}
        if _table_exists(c, "wt_ops_v2_runs"):
            for job, key in (("INTAKE", "last_intake"), ("FORWARD_MONITOR", "last_forward")):
                r = c.execute("SELECT started_at, status, rpc_used FROM wt_ops_v2_runs WHERE job_type=? ORDER BY started_at DESC LIMIT 1", (job,)).fetchone()
                out[key] = dict(r) if r else None
            out["rpc_today"] = int(c.execute("SELECT COALESCE(SUM(rpc_used),0) FROM wt_ops_v2_runs WHERE started_at >= strftime('%s','now','start of day')").fetchone()[0] or 0)
            budgeted = c.execute("SELECT COUNT(*) FROM wt_ops_v2_runs WHERE stopped_due_to_budget=1 AND started_at > strftime('%s','now')-3600").fetchone()[0]
            out["budget_status"] = "throttled" if budgeted else "ok"
        # liveness via the scheduler lock file PID
        lock = _os.environ.get("OPS_SCHEDULER_LOCK",
                               _os.path.join(_REPO_ROOT, "operation_scheduler.lock"))
        if _os.path.exists(lock):
            try:
                pid = int(open(lock).read().strip() or "0")
                _os.kill(pid, 0)
                out["running"] = True
                out["pid"] = pid
            except (OSError, ValueError):
                out["running"] = False
        return jsonify(out)
    finally:
        c.close()


# ── operation-centric pages (the new product surface, source of truth = wt_ops_v2) ──
@ops_dashboard_bp.route("/ops")
@ops_dashboard_bp.route("/ops/live")
def ops_live_page():
    # OPERATIONS OS — the reused campaigns dashboard shell, powered by wt_ops_v2.
    # This is the single operations home (graph dashboard). The older card page
    # (ops_live.html) is retired from nav; kept on disk for reference.
    return render_template("watchtower_dashboard.html", active_page="ops_live")


@ops_dashboard_bp.route("/ops/cards")
def ops_live_cards_page():
    # Retired card view — kept reachable for reference, not in nav.
    return render_template("ops_live.html", active_page="ops_live_cards")


@ops_dashboard_bp.route("/ops/webhook-coverage")
def ops_webhook_intel_page():
    # Webhook Intelligence — operation account coverage + enrolment (wt_ops_v2 ⋈ webhooks).
    return render_template("ops_webhook_intelligence.html", active_page="ops_webhook")


@ops_dashboard_bp.route("/ops/operations")
def ops_operations_page():
    return render_template("ops_operations.html", active_page="ops_operations")


@ops_dashboard_bp.route("/ops/operation/<uuid>")
def ops_operation_detail_page(uuid):
    return render_template("ops_detail.html", active_page="ops_operations", operation_uuid=uuid)


@ops_dashboard_bp.route("/operations-v2")
def operations_v2_page():
    # Retired: the operation-centric view now lives at /ops.
    return redirect("/ops")


@ops_dashboard_bp.route("/api/ops-v2/summary")
def api_summary():
    c = _conn()
    try:
        out = {
            "operations": c.execute("SELECT COUNT(*) FROM wt_ops_v2 WHERE "+_LIVE_OPS_EXCLUDE_NOALIAS).fetchone()[0],
            "families": c.execute("SELECT COUNT(*) FROM wt_ops_v2_families").fetchone()[0],
            "wallets": c.execute("SELECT COUNT(DISTINCT wallet) FROM wt_ops_v2_wallets").fetchone()[0],
            "creators": c.execute("SELECT COUNT(*) FROM wt_ops_v2_creators").fetchone()[0],
            "treasuries": c.execute("SELECT COUNT(*) FROM wt_confirmed_treasuries").fetchone()[0],
            "collectors": c.execute("SELECT COUNT(*) FROM wt_ops_v2_wallets WHERE role='COLLECTOR'").fetchone()[0],
        }
        # forward-monitor objects (may not exist if Phase 2 never ran)
        out["candidates"] = c.execute("SELECT COUNT(*) FROM wt_operation_candidates").fetchone()[0] if _table_exists(c, "wt_operation_candidates") else 0
        out["template_leads"] = c.execute("SELECT COUNT(*) FROM wt_operation_candidates WHERE template_base IS NOT NULL").fetchone()[0] if _table_exists(c, "wt_operation_candidates") else 0
        out["activity_events"] = c.execute("SELECT COUNT(*) FROM wt_operation_activity").fetchone()[0] if _table_exists(c, "wt_operation_activity") else 0
        # lifecycle state distribution
        out["states"] = {r["state"]: r["n"] for r in c.execute(
            "SELECT state, COUNT(*) n FROM wt_operation_lifecycle GROUP BY state").fetchall()} if _table_exists(c, "wt_operation_lifecycle") else {}
        # scheduler health (last run per job)
        sched = {}
        if _table_exists(c, "wt_ops_v2_runs"):
            for job in ("INTAKE", "FORWARD_MONITOR"):
                r = c.execute("""SELECT started_at, status, rpc_used, runtime_sec
                                 FROM wt_ops_v2_runs WHERE job_type=? ORDER BY started_at DESC LIMIT 1""",
                              (job,)).fetchone()
                sched[job] = dict(r) if r else None
            r = c.execute("SELECT COALESCE(SUM(rpc_used),0) FROM wt_ops_v2_runs "
                          "WHERE started_at >= strftime('%s','now','start of day')").fetchone()
            sched["rpc_today"] = int(r[0] or 0)
        out["scheduler"] = sched
        return jsonify(out)
    finally:
        c.close()


@ops_dashboard_bp.route("/api/ops-v2/live")
def api_live():
    """Live Operations landing payload: 8 top metrics + a typed activity feed.

    Source of truth is wt_ops_v2 — operations / lifecycle / candidates / creators /
    activity / scheduler runs. The feed is a unified, reverse-chronological stream
    synthesised from those tables into the operation-centric event vocabulary.
    """
    c = _conn()
    try:
        now = int(time.time())
        d1 = now - 86400
        has_life = _table_exists(c, "wt_operation_lifecycle")
        has_cand = _table_exists(c, "wt_operation_candidates")
        has_act = _table_exists(c, "wt_operation_activity")
        has_runs = _table_exists(c, "wt_ops_v2_runs")

        states = {r["state"]: r["n"] for r in c.execute(
            "SELECT state, COUNT(*) n FROM wt_operation_lifecycle WHERE operation_uuid IN "
            "(SELECT operation_uuid FROM wt_ops_v2 WHERE treasury_root IN (SELECT treasury FROM wt_confirmed_treasuries)) "
            "GROUP BY state").fetchall()} if has_life else {}
        # SCOPE everything to confirmed-treasury-rooted operations (authoritative set).
        _conf_op_filter = "operation_uuid IN (SELECT operation_uuid FROM wt_ops_v2 WHERE treasury_root IN (SELECT treasury FROM wt_confirmed_treasuries))"
        metrics = {
            "active_operations": sum(states.get(s, 0) for s in
                                     ("ACTIVE", "PROVISIONING", "CREATORS_SEEN", "REACTIVATED")),
            "active_families": c.execute("SELECT COUNT(*) FROM wt_ops_v2_families").fetchone()[0],
            "new_operations_24h": c.execute(
                "SELECT COUNT(*) FROM wt_ops_v2 WHERE first_seen > ? AND treasury_root IN "
                "(SELECT treasury FROM wt_confirmed_treasuries)", (d1,)).fetchone()[0],
            "new_candidates_24h": c.execute(f"SELECT COUNT(*) FROM wt_operation_candidates WHERE first_seen > ? AND {_conf_op_filter}", (d1,)).fetchone()[0] if has_cand else 0,
            "new_migrations_24h": c.execute(f"SELECT COUNT(*) FROM wt_ops_v2_creators WHERE migration_time > ? AND {_conf_op_filter}", (d1,)).fetchone()[0],
            "reactivated": states.get("REACTIVATED", 0),
            "dormant": states.get("DORMANT", 0),
            "rpc_today": int(c.execute("SELECT COALESCE(SUM(rpc_used),0) FROM wt_ops_v2_runs WHERE started_at >= strftime('%s','now','start of day')").fetchone()[0] or 0) if has_runs else 0,
            "total_operations": c.execute("SELECT COUNT(*) FROM wt_ops_v2 WHERE treasury_root IN (SELECT treasury FROM wt_confirmed_treasuries)").fetchone()[0],
        }
        # creator pipeline (the hero number: candidates -> migrated -> attributed) — confirmed only
        pipeline = {
            "candidates": c.execute(f"SELECT COUNT(*) FROM wt_operation_candidates WHERE {_conf_op_filter}").fetchone()[0] if has_cand else 0,
            "candidates_24h": metrics["new_candidates_24h"],
            "template_leads": c.execute(f"SELECT COUNT(*) FROM wt_operation_candidates WHERE template_base IS NOT NULL AND {_conf_op_filter}").fetchone()[0] if has_cand else 0,
            "migrated": c.execute(f"SELECT COUNT(*) FROM wt_ops_v2_creators WHERE migration_time IS NOT NULL AND {_conf_op_filter}").fetchone()[0],
            "attributed": 0,  # reserved — attribution is a downstream enrichment, not yet wired
        }
        # per-operation cards (state/treasury/collectors/candidates/migrated/last activity)
        op_cards = []
        st_map = {r["operation_uuid"]: (r["state"], r["last_activity"]) for r in c.execute(
            "SELECT operation_uuid, state, last_activity FROM wt_operation_lifecycle").fetchall()} if has_life else {}
        cd_map = {r["operation_uuid"]: r["n"] for r in c.execute(
            "SELECT operation_uuid, COUNT(*) n FROM wt_operation_candidates GROUP BY operation_uuid").fetchall()} if has_cand else {}
        _otc = "o.op_type" if _column_exists(c, "wt_ops_v2", "op_type") else "'WATCHTOWER' AS op_type"
        for r in c.execute(f"""
            SELECT o.operation_uuid, o.treasury_root, {_otc},
                   (SELECT COUNT(*) FROM wt_ops_v2_wallets w WHERE w.operation_uuid=o.operation_uuid AND role='COLLECTOR') collectors,
                   (SELECT COUNT(*) FROM wt_ops_v2_creators cr WHERE cr.operation_uuid=o.operation_uuid AND cr.migration_time IS NOT NULL) migrated
            FROM wt_ops_v2 o WHERE """ + _LIVE_OPS_EXCLUDE +
            " AND o.treasury_root IN (SELECT treasury FROM wt_confirmed_treasuries)").fetchall():
            s = st_map.get(r["operation_uuid"], (None, None))
            op_cards.append({"operation_uuid": r["operation_uuid"], "treasury_root": r["treasury_root"],
                             "op_type": r["op_type"] or "WATCHTOWER",
                             "state": s[0] or "DISCOVERED", "last_activity": s[1],
                             "collectors": r["collectors"], "migrated": r["migrated"],
                             "candidates": cd_map.get(r["operation_uuid"], 0)})
        # rank by state priority (live first) then candidates
        _pri = {"PROVISIONING": 0, "CREATORS_SEEN": 1, "REACTIVATED": 2, "ACTIVE": 3,
                "DISCOVERED": 4, "MIGRATED": 5, "DORMANT": 6}
        op_cards.sort(key=lambda x: (_pri.get(x["state"], 9), -x["candidates"]))

        # ── unified typed feed ────────────────────────────────────────────
        # SCOPE TO CONFIRMED TREASURIES: the feed/metrics only reflect operations rooted
        # on a confirmed treasury (the authoritative set), not the contaminated ops-graph.
        confirmed_ops = {r["operation_uuid"] for r in c.execute(
            "SELECT operation_uuid FROM wt_ops_v2 WHERE treasury_root IN (SELECT treasury FROM wt_confirmed_treasuries)").fetchall()}
        feed = []
        # NEW_OPERATION (operation first_seen) + treasury label
        for r in c.execute("SELECT operation_uuid, treasury_root, first_seen FROM wt_ops_v2 WHERE treasury_root IN (SELECT treasury FROM wt_confirmed_treasuries) ORDER BY first_seen DESC LIMIT 40").fetchall():
            feed.append({"ts": r["first_seen"], "type": "NEW_OPERATION",
                         "operation": r["operation_uuid"], "treasury": r["treasury_root"],
                         "detail": "treasury-rooted operation discovered"})
        # CREATOR_MIGRATED (creator migration_time) — confirmed-rooted ops only
        for r in c.execute("SELECT operation_uuid, creator_wallet, token_mint, migration_time FROM wt_ops_v2_creators WHERE migration_time IS NOT NULL ORDER BY migration_time DESC LIMIT 80").fetchall():
            if r["operation_uuid"] not in confirmed_ops:
                continue
            feed.append({"ts": r["migration_time"], "type": "CREATOR_MIGRATED",
                         "operation": r["operation_uuid"], "wallet": r["creator_wallet"],
                         "detail": r["token_mint"]})
        # WATCHTOWER_LAUNCH — the real-time CREATEs the cascade caught (the highest-value event;
        # this is what the feed exists to surface). Includes the treasury/subprov lineage.
        try:
            for r in c.execute(
                "SELECT mint, creator_wallet, treasury_wallet, subprov_wallet, create_time, "
                "subprov_funding_sol, birth_to_launch_seconds FROM wt_watchtower_launches "
                "WHERE create_time IS NOT NULL ORDER BY create_time DESC LIMIT 60").fetchall():
                btl = r["birth_to_launch_seconds"]
                feed.append({"ts": r["create_time"], "type": "WATCHTOWER_LAUNCH",
                             "wallet": r["creator_wallet"],
                             "treasury": r["treasury_wallet"], "subprov": r["subprov_wallet"],
                             "detail": (f"{r['mint']} · {r['subprov_funding_sol'] or '?'}◎"
                                        + (f" · {btl}s birth→launch" if btl is not None else ""))})
        except Exception:
            pass
        if has_act:
            # NEW_COLLECTOR / NEW_CANDIDATE / OPERATION_EXPANDED — confirmed-rooted ops only
            for r in c.execute("""SELECT operation_uuid, wallet, counterparty, event_type, amount, block_time
                                  FROM wt_operation_activity ORDER BY block_time DESC LIMIT 200""").fetchall():
                if r["operation_uuid"] not in confirmed_ops:
                    continue
                et = {"NEW_CREATOR_CANDIDATE": "NEW_CANDIDATE",
                      "NEW_CHILD": "OPERATION_EXPANDED",
                      "FUNDING": "OPERATION_EXPANDED"}.get(r["event_type"], r["event_type"])
                feed.append({"ts": r["block_time"], "type": et,
                             "operation": r["operation_uuid"], "wallet": r["counterparty"],
                             "detail": (f"{round(r['amount'],3)} SOL from {r['wallet'][:8]}…" if r["amount"] else "")})
        # REACTIVATED / DORMANT lifecycle transitions (by last_changed) — confirmed only
        if has_life:
            for r in c.execute("""SELECT operation_uuid, state, last_changed FROM wt_operation_lifecycle
                                  WHERE state IN ('REACTIVATED','DORMANT') ORDER BY last_changed DESC LIMIT 40""").fetchall():
                if r["operation_uuid"] not in confirmed_ops:
                    continue
                feed.append({"ts": r["last_changed"],
                             "type": "OPERATION_REACTIVATED" if r["state"] == "REACTIVATED" else "OPERATION_DORMANT",
                             "operation": r["operation_uuid"], "detail": ""})
        # LIVE CASCADE STREAM — the real-time WATCHTOWER pulse (watchtower_events, live DB):
        # treasury provisioning outbounds, wrap-close fan-outs, launch detections, swarm rejects.
        # This is the high-frequency activity that makes the feed actually live; the ops-graph
        # sources above are slow/post-hoc by comparison.
        # MEANINGFUL cascade events only — NOT raw TREASURY_OUTBOUND (345/day of mostly
        # dust/top-ups would flood the feed and bury the launches; treasury moves already
        # have the dedicated webhook feed). "Changes only on real events": a launch, a
        # wrap-close fan-out (a creator was just provisioned), or a buy-swarm classification.
        _CASCADE_FEED_TYPES = {
            "WATCHTOWER_LAUNCH_DETECTED": "LAUNCH_DETECTED",
            "WRAP_CLOSE_FANOUT_DETECTED": "WRAP_CLOSE_FANOUT",
            "CANDIDATE_CLASSIFIED_BUY_SWARM": "BUY_SWARM",
        }
        try:
            _lc = _live_conn()
            try:
                ph = ",".join("?" * len(_CASCADE_FEED_TYPES))
                # DEDUPE bursty fan-outs: one subprov firing produces many per-candidate
                # WRAP_CLOSE_FANOUT rows in the same minute — collapse to one feed entry per
                # (type, wallet, minute) so a single provisioning event reads as one line, not 10.
                _seen_fanout = set()
                for r in _lc.execute(
                    f"SELECT event_type, wallet_address, related_wallet, token_mint, created_at "
                    f"FROM watchtower_events WHERE event_type IN ({ph}) "
                    f"ORDER BY created_at DESC LIMIT 300", list(_CASCADE_FEED_TYPES)).fetchall():
                    et = _CASCADE_FEED_TYPES.get(r["event_type"], r["event_type"])
                    if et in ("WRAP_CLOSE_FANOUT", "BUY_SWARM"):
                        key = (et, r["wallet_address"], (r["created_at"] or 0) // 60)
                        if key in _seen_fanout:
                            continue
                        _seen_fanout.add(key)
                    feed.append({"ts": r["created_at"], "type": et,
                                 "wallet": r["wallet_address"], "related": r["related_wallet"],
                                 "detail": r["token_mint"] or (r["related_wallet"] or "")})
            finally:
                _lc.close()
        except Exception:
            pass
        feed.sort(key=lambda x: x["ts"] or 0, reverse=True)
        feed = feed[:60]

        return jsonify({"ts": now, "metrics": metrics, "states": states,
                        "pipeline": pipeline, "operations": op_cards, "feed": feed})
    finally:
        c.close()


@ops_dashboard_bp.route("/api/ops-v2/command-center")
def api_command_center():
    """One composed payload for the replatformed Command Center page.

    Bundles: top-line counts + lifecycle state distribution (banner state),
    the operation list, top pre-launch template candidates, recent activity
    events, and the last scheduler runs. Single round-trip; reads only wt_ops_v2.
    """
    c = _conn()
    try:
        now = int(time.time())
        has_life = _table_exists(c, "wt_operation_lifecycle")
        has_cand = _table_exists(c, "wt_operation_candidates")
        has_act = _table_exists(c, "wt_operation_activity")
        has_runs = _table_exists(c, "wt_ops_v2_runs")

        # ── summary + state distribution ──────────────────────────────────
        states = {r["state"]: r["n"] for r in c.execute(
            "SELECT state, COUNT(*) n FROM wt_operation_lifecycle WHERE operation_uuid IN "
            "(SELECT operation_uuid FROM wt_ops_v2 WHERE treasury_root IN (SELECT treasury FROM wt_confirmed_treasuries)) "
            "GROUP BY state").fetchall()} if has_life else {}
        cand24 = c.execute("SELECT COUNT(*) FROM wt_operation_candidates WHERE first_seen > ?",
                           (now - 86400,)).fetchone()[0] if has_cand else 0
        template_leads = c.execute(
            "SELECT COUNT(*) FROM wt_operation_candidates WHERE template_base IS NOT NULL").fetchone()[0] if has_cand else 0
        summary = {
            "operations": c.execute("SELECT COUNT(*) FROM wt_ops_v2 WHERE "+_LIVE_OPS_EXCLUDE_NOALIAS).fetchone()[0],
            "families": c.execute("SELECT COUNT(*) FROM wt_ops_v2_families").fetchone()[0],
            "treasuries": c.execute("SELECT COUNT(*) FROM wt_confirmed_treasuries").fetchone()[0],
            "collectors": c.execute("SELECT COUNT(*) FROM wt_ops_v2_wallets WHERE role='COLLECTOR'").fetchone()[0],
            "creators": c.execute("SELECT COUNT(*) FROM wt_ops_v2_creators").fetchone()[0],
            "candidates": c.execute("SELECT COUNT(*) FROM wt_operation_candidates").fetchone()[0] if has_cand else 0,
            "candidates_24h": cand24,
            "template_leads": template_leads,
            "activity_events": c.execute("SELECT COUNT(*) FROM wt_operation_activity").fetchone()[0] if has_act else 0,
            "states": states,
        }

        # ── banner state (operation-centric) ──────────────────────────────
        provisioning = states.get("PROVISIONING", 0)
        creators_seen = states.get("CREATORS_SEEN", 0) + states.get("REACTIVATED", 0)
        if provisioning > 0 or creators_seen > 0:
            banner = {"state": "PROVISIONING", "live": True,
                      "detail": f"{provisioning} PROVISIONING · {creators_seen} creators-seen · {cand24} candidate creators in 24h"}
        elif cand24 > 0:
            banner = {"state": "WATCHING", "live": True,
                      "detail": f"{cand24} candidate creators detected in last 24h across {summary['operations']} tracked operations"}
        else:
            banner = {"state": "DORMANT", "live": False,
                      "detail": "No operation expansion in window — monitor watching"}

        # ── operations list (ranked by candidates, then size) ─────────────
        op_rows = c.execute("""
            SELECT o.operation_uuid, o.treasury_root, o.confidence, o.last_seen,
                   (SELECT COUNT(*) FROM wt_ops_v2_wallets w WHERE w.operation_uuid=o.operation_uuid) wallets,
                   (SELECT COUNT(*) FROM wt_ops_v2_wallets w WHERE w.operation_uuid=o.operation_uuid AND role='COLLECTOR') collectors,
                   (SELECT COUNT(*) FROM wt_ops_v2_creators cr WHERE cr.operation_uuid=o.operation_uuid) creators
            FROM wt_ops_v2 o WHERE """ + _LIVE_OPS_EXCLUDE).fetchall()
        st_map = {r["operation_uuid"]: (r["state"], r["last_activity"]) for r in
                  c.execute("SELECT operation_uuid, state, last_activity FROM wt_operation_lifecycle").fetchall()} if has_life else {}
        cd_map = {r["operation_uuid"]: r["n"] for r in
                  c.execute("SELECT operation_uuid, COUNT(*) n FROM wt_operation_candidates GROUP BY operation_uuid").fetchall()} if has_cand else {}
        operations = []
        for r in op_rows:
            d = dict(r)
            s = st_map.get(r["operation_uuid"], (None, None))
            d["state"] = s[0] or "DISCOVERED"
            d["last_activity"] = s[1]
            d["candidates"] = cd_map.get(r["operation_uuid"], 0)
            operations.append(d)
        operations.sort(key=lambda x: (x["candidates"], x["wallets"]), reverse=True)

        # ── top pre-launch template candidates ────────────────────────────
        candidates = []
        if has_cand:
            candidates = [dict(r) for r in c.execute("""
                SELECT cc.wallet, cc.source_wallet, cc.source_role, cc.amount, cc.template_base,
                       cc.confidence, cc.status, cc.first_seen, o.treasury_root,
                       (SELECT 1 FROM wt_ops_v2_creators cr WHERE cr.creator_wallet=cc.wallet) migrated
                FROM wt_operation_candidates cc JOIN wt_ops_v2 o ON o.operation_uuid=cc.operation_uuid
                ORDER BY cc.template_base IS NOT NULL DESC, cc.confidence DESC, cc.first_seen DESC
                LIMIT 40""").fetchall()]

        # ── recent activity ───────────────────────────────────────────────
        activity = []
        if has_act:
            activity = [dict(r) for r in c.execute("""
                SELECT a.wallet, a.counterparty, a.event_type, a.amount, a.block_time, o.treasury_root
                FROM wt_operation_activity a JOIN wt_ops_v2 o ON o.operation_uuid=a.operation_uuid
                ORDER BY a.block_time DESC LIMIT 40""").fetchall()]

        # ── scheduler health + recent runs ────────────────────────────────
        sched = {"runs": [], "intake": None, "forward": None, "rpc_today": 0}
        if has_runs:
            for job, key in (("INTAKE", "intake"), ("FORWARD_MONITOR", "forward")):
                r = c.execute("""SELECT started_at, status, rpc_used, runtime_sec
                                 FROM wt_ops_v2_runs WHERE job_type=? ORDER BY started_at DESC LIMIT 1""",
                              (job,)).fetchone()
                sched[key] = dict(r) if r else None
            sched["rpc_today"] = int(c.execute(
                "SELECT COALESCE(SUM(rpc_used),0) FROM wt_ops_v2_runs WHERE started_at >= strftime('%s','now','start of day')"
            ).fetchone()[0] or 0)
            sched["runs"] = [dict(r) for r in c.execute("""
                SELECT job_type, started_at, status, runtime_sec, operations_discovered, operations_merged,
                       activity_events_added, candidate_creators_added, rpc_used, rpc_budget, stopped_due_to_budget
                FROM wt_ops_v2_runs ORDER BY started_at DESC LIMIT 12""").fetchall()]

        return jsonify({"ts": now, "banner": banner, "summary": summary,
                        "operations": operations, "candidates": candidates,
                        "activity": activity, "scheduler": sched})
    finally:
        c.close()


@ops_dashboard_bp.route("/api/ops-v2/operations")
def api_operations():
    c = _conn()
    try:
        rows = c.execute("""
            SELECT o.operation_uuid, o.treasury_root, o.family_uuid, o.confidence,
                   o.first_seen, o.last_seen,
                   (SELECT COUNT(*) FROM wt_ops_v2_wallets w WHERE w.operation_uuid=o.operation_uuid) wallets,
                   (SELECT COUNT(*) FROM wt_ops_v2_wallets w WHERE w.operation_uuid=o.operation_uuid AND role='COLLECTOR') collectors,
                   (SELECT COUNT(*) FROM wt_ops_v2_creators cr WHERE cr.operation_uuid=o.operation_uuid) creators,
                   (SELECT COUNT(*) FROM wt_ops_v2_creators cr WHERE cr.operation_uuid=o.operation_uuid AND cr.migration_time IS NOT NULL) migrated
            FROM wt_ops_v2 o WHERE """ + _LIVE_OPS_EXCLUDE + """
              AND o.treasury_root IN (SELECT treasury FROM wt_confirmed_treasuries)
        """).fetchall()
        # family label lookup
        fam = {r["family_uuid"]: r["family_label"] for r in
               c.execute("SELECT family_uuid, family_label FROM wt_ops_v2_families").fetchall()}
        # lifecycle + candidate counts (Phase 2 tables, optional)
        states = {}
        cand = {}
        if _table_exists(c, "wt_operation_lifecycle"):
            states = {r["operation_uuid"]: (r["state"], r["last_activity"]) for r in
                      c.execute("SELECT operation_uuid, state, last_activity FROM wt_operation_lifecycle").fetchall()}
        if _table_exists(c, "wt_operation_candidates"):
            cand = {r["operation_uuid"]: r["n"] for r in
                    c.execute("SELECT operation_uuid, COUNT(*) n FROM wt_operation_candidates GROUP BY operation_uuid").fetchall()}
        ops = []
        for r in rows:
            d = dict(r)
            st = states.get(r["operation_uuid"], (None, None))
            d["state"] = st[0] or "DISCOVERED"
            d["last_activity"] = st[1]
            d["candidates"] = cand.get(r["operation_uuid"], 0)
            d["family"] = fam.get(r["family_uuid"]) or "—"
            ops.append(d)
        # rank: most candidates, then most wallets
        ops.sort(key=lambda x: (x["candidates"], x["wallets"]), reverse=True)
        return jsonify({"operations": ops})
    finally:
        c.close()


@ops_dashboard_bp.route("/api/ops-v2/operation/<uuid>")
def api_operation_detail(uuid):
    c = _conn()
    try:
        op = c.execute("SELECT * FROM wt_ops_v2 WHERE operation_uuid=?", (uuid,)).fetchone()
        if not op:
            return jsonify({"error": "not found"}), 404
        wallets = [dict(r) for r in c.execute(
            "SELECT wallet, role, first_seen, last_seen FROM wt_ops_v2_wallets WHERE operation_uuid=? ORDER BY "
            "CASE role WHEN 'TREASURY' THEN 0 WHEN 'COLLECTOR' THEN 1 WHEN 'PASS_THROUGH' THEN 2 ELSE 3 END",
            (uuid,)).fetchall()]
        edges = [dict(r) for r in c.execute(
            "SELECT from_wallet, to_wallet, amount_sol, block_time, signature, edge_type, hop_depth "
            "FROM wt_ops_v2_edges WHERE operation_uuid=? ORDER BY hop_depth", (uuid,)).fetchall()]
        creators = [dict(r) for r in c.execute(
            "SELECT creator_wallet, token_mint, migration_time, template_base, funding_amount_sol "
            "FROM wt_ops_v2_creators WHERE operation_uuid=?", (uuid,)).fetchall()]
        cands = []
        if _table_exists(c, "wt_operation_candidates"):
            cands = [dict(r) for r in c.execute(
                "SELECT wallet, source_wallet, source_role, amount, template_base, confidence, status, first_seen "
                "FROM wt_operation_candidates WHERE operation_uuid=? ORDER BY confidence DESC, first_seen DESC LIMIT 200",
                (uuid,)).fetchall()]
        state = None
        if _table_exists(c, "wt_operation_lifecycle"):
            r = c.execute("SELECT state, last_changed, last_activity FROM wt_operation_lifecycle WHERE operation_uuid=?",
                          (uuid,)).fetchone()
            state = dict(r) if r else None
        # family label
        family = None
        if op["family_uuid"]:
            fr = c.execute("SELECT family_label FROM wt_ops_v2_families WHERE family_uuid=?", (op["family_uuid"],)).fetchone()
            family = fr["family_label"] if fr else None
        # recent activity for this operation
        activity = []
        if _table_exists(c, "wt_operation_activity"):
            activity = [dict(r) for r in c.execute(
                "SELECT wallet, counterparty, event_type, amount, block_time, signature "
                "FROM wt_operation_activity WHERE operation_uuid=? ORDER BY block_time DESC LIMIT 80", (uuid,)).fetchall()]
        # synthesised timeline (discovered -> wallets added -> candidates -> migrations -> state)
        tl = []
        tl.append({"ts": op["first_seen"], "kind": "DISCOVERED", "label": "Operation discovered (treasury-rooted)"})
        first_coll = c.execute("SELECT MIN(first_seen) m FROM wt_ops_v2_wallets WHERE operation_uuid=? AND role='COLLECTOR'", (uuid,)).fetchone()["m"]
        if first_coll:
            tl.append({"ts": first_coll, "kind": "COLLECTOR_ADDED", "label": "First collector attached"})
        if cands:
            ft = min((x["first_seen"] for x in cands if x["first_seen"]), default=None)
            if ft:
                tl.append({"ts": ft, "kind": "CANDIDATE_DETECTED", "label": "First creator candidate detected (pre-migration)"})
        migs = [x for x in creators if x["migration_time"]]
        if migs:
            tl.append({"ts": min(x["migration_time"] for x in migs), "kind": "MIGRATION", "label": "First creator migration"})
        if state and state.get("state") in ("DORMANT", "REACTIVATED"):
            tl.append({"ts": state.get("last_changed"), "kind": state["state"],
                       "label": f"Operation {state['state'].lower()}"})
        tl = [t for t in tl if t["ts"]]
        tl.sort(key=lambda x: x["ts"])

        # Creator Launch Path: for each migrated creator, the template-funding time +
        # funder (pass-through) + lead time (funding → migration). From the live DB.
        launch_path = []
        live = _live_conn()
        try:
            amt_ph = ",".join("?" * len(CREATOR_TEMPLATE_AMOUNTS))
            for cr in creators:
                if not cr["migration_time"]:
                    continue
                f = live.execute(
                    f"SELECT funder_address, amount_sol, first_detected_at FROM creator_funders "
                    f"WHERE creator_address=? AND amount_sol IN ({amt_ph}) "
                    f"ORDER BY first_detected_at LIMIT 1",
                    [cr["creator_wallet"]] + list(CREATOR_TEMPLATE_AMOUNTS)).fetchone()
                ft = _parse_ts(f["first_detected_at"]) if f else None
                lead = round((cr["migration_time"] - ft) / 60) if (ft and cr["migration_time"] > ft) else None
                launch_path.append({
                    "creator": cr["creator_wallet"], "token_mint": cr["token_mint"],
                    "template": _template_base_of(f["amount_sol"]) if f else cr["template_base"],
                    "funded_by": f["funder_address"] if f else None,
                    "funded_at": ft, "migration_time": cr["migration_time"],
                    "lead_time_min": lead, "treasury": op["treasury_root"],
                })
            launch_path.sort(key=lambda x: x["migration_time"] or 0, reverse=True)
        finally:
            live.close()

        return jsonify({"operation": dict(op), "family": family, "state": state, "wallets": wallets,
                        "edges": edges, "creators": creators, "candidates": cands,
                        "activity": activity, "timeline": tl, "launch_path": launch_path})
    finally:
        c.close()


@ops_dashboard_bp.route("/api/ops-v2/candidates")
def api_candidates():
    """Live pre-launch creator candidates — the Phase 2 payoff. Template-bearing first."""
    c = _conn()
    try:
        if not _table_exists(c, "wt_operation_candidates"):
            return jsonify({"candidates": []})
        only_template = request.args.get("template") == "1"
        where = "WHERE c.template_base IS NOT NULL" if only_template else ""
        rows = c.execute(f"""
            SELECT c.wallet, c.source_wallet, c.source_role, c.amount, c.template_base,
                   c.confidence, c.status, c.first_seen,
                   o.treasury_root,
                   (SELECT 1 FROM wt_ops_v2_creators cr WHERE cr.creator_wallet=c.wallet) AS migrated
            FROM wt_operation_candidates c
            JOIN wt_ops_v2 o ON o.operation_uuid=c.operation_uuid
            {where}
            ORDER BY c.template_base IS NOT NULL DESC, c.confidence DESC, c.first_seen DESC
            LIMIT 300
        """).fetchall()
        return jsonify({"candidates": [dict(r) for r in rows]})
    finally:
        c.close()


@ops_dashboard_bp.route("/api/ops-v2/activity")
def api_activity():
    c = _conn()
    try:
        if not _table_exists(c, "wt_operation_activity"):
            return jsonify({"activity": []})
        rows = c.execute("""
            SELECT a.wallet, a.counterparty, a.event_type, a.amount, a.block_time, a.signature,
                   o.treasury_root
            FROM wt_operation_activity a JOIN wt_ops_v2 o ON o.operation_uuid=a.operation_uuid
            ORDER BY a.block_time DESC LIMIT 200
        """).fetchall()
        return jsonify({"activity": [dict(r) for r in rows]})
    finally:
        c.close()


@ops_dashboard_bp.route("/api/ops-v2/runs")
def api_runs():
    c = _conn()
    try:
        if not _table_exists(c, "wt_ops_v2_runs"):
            return jsonify({"runs": []})
        rows = c.execute("""
            SELECT id, job_type, started_at, finished_at, status, runtime_sec,
                   candidates_seen, operations_discovered, operations_merged,
                   activity_events_added, candidate_creators_added, rpc_used, rpc_budget,
                   stopped_due_to_budget, error_message
            FROM wt_ops_v2_runs ORDER BY started_at DESC LIMIT 50
        """).fetchall()
        return jsonify({"runs": [dict(r) for r in rows]})
    finally:
        c.close()


# ════════════════════════════════════════════════════════════════════════════
#  WEBHOOK INTELLIGENCE — coverage of wt_ops_v2 operation accounts against the
#  webhook enrolment tables (live DB). The page answers: "are the right operation
#  accounts webhooked, and what have they revealed?"
# ════════════════════════════════════════════════════════════════════════════

LIVE_DB_PATH = os.environ.get(
    "FLEX_DB_PATH", os.path.join(_REPO_ROOT, "database", "flex_complete_database.db"))

# how stale the latest webhook hit can be before we call the listener DOWN
WEBHOOK_STALE_S = 3600          # 1h
ENROLL_BATCH_CAP = 50           # max wallets per enrol call (runaway-Helius guard)


def _live_conn():
    c = db_connect(LIVE_DB_PATH, timeout=8)
    c.row_factory = sqlite3.Row
    return c


# ── pre-launch creator classification ───────────────────────────────────────
# A wallet is a PRE_LAUNCH_CREATOR iff funded with a known creator template (small
# sub-~6-SOL amount carrying the ATA-rent tail) AND not yet migrated. Everything else
# the treasury funds (large/round amounts) is INFRASTRUCTURE, excluded from launch panels.
_ATA_RENT = 2_039_280
CREATOR_TEMPLATE_AMOUNTS = (
    1.11203928, 1.10203928, 2.10203928, 0.60703928, 5.10203928,
    1.11103928, 1.11003928, 1.21203928, 0.14203928, 0.20303928,
)
CREATOR_TEMPLATE_BASES = {1.11, 1.10, 2.10, 0.605, 5.10, 0.14, 0.203, 1.21}
LEAD_TIME_MIN = 58          # observed avg template-funding → migration (N=25, avg 58)


def _is_creator_template(amount_sol) -> bool:
    """True if a funding amount is a creator-launch template (ATA-rent tail + small base)."""
    if amount_sol is None or amount_sol <= 0 or amount_sol > 6.5:
        return False
    lamports = round(amount_sol * 1e9)
    if lamports % 1_000_000 != _ATA_RENT % 1_000_000:   # must carry the …039280 tail
        return False
    base = round((lamports - _ATA_RENT) / 1e9, 4)
    return 0.05 <= base <= 6.0


def _template_base_of(amount_sol):
    lamports = round((amount_sol or 0) * 1e9)
    return round((lamports - _ATA_RENT) / 1e9, 4) if _is_creator_template(amount_sol) else None


def _parse_ts(s):
    """creator_funders.first_detected_at is mostly 'YYYY-MM-DD HH:MM:SS' text."""
    import datetime as _dt
    if isinstance(s, int):
        return s
    try:
        return int(_dt.datetime.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S").timestamp())
    except Exception:
        return None


def _webhooked_set(live_c) -> set:
    """All currently-webhooked wallets (active ENROLLED/CONFIRMED)."""
    try:
        return {r[0] for r in live_c.execute(
            "SELECT DISTINCT wallet_address FROM wt_webhook_enrollments "
            "WHERE is_active=1 AND state IN ('ENROLLED','CONFIRMED','PENDING')").fetchall()}
    except Exception:
        return set()


def _is_valid_op_account(ov_c, wallet) -> str | None:
    """Server-side guard for enrol. WEBHOOKS ARE TREASURY-ONLY.

    Only confirmed treasuries (wt_confirmed_treasuries — the authoritative live-watch set)
    may be webhooked. Sub-provisioners, candidates, creators, and ops-graph infra are NOT
    enrolled — creators are armed transiently by the wrap-close detector, not webhooked.
    This is the single gate that keeps the webhook set = the treasury set."""
    try:
        r = ov_c.execute("SELECT 1 FROM wt_confirmed_treasuries WHERE treasury=? LIMIT 1", (wallet,)).fetchone()
        if r:
            return "confirmed_treasury"
    except Exception:
        pass
    return None                                    # not a confirmed treasury → reject


def _webhook_hits_24h(live_c) -> dict:
    """wallet -> webhook hit count in last 24h (for per-op event totals)."""
    try:
        rows = live_c.execute(
            "SELECT wallet_address, COUNT(*) n FROM wt_webhook_hits "
            "WHERE block_time > strftime('%s','now')-86400 GROUP BY wallet_address").fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception:
        return {}


def _coverage_status(cand_cov: float, has_treasury: bool, candidates: int) -> str:
    """GOOD / PARTIAL / WEAK / UNCOVERED from candidate coverage %."""
    if candidates == 0:
        return "GOOD" if has_treasury else "PARTIAL"   # nothing to cover yet
    if cand_cov >= 0.8:
        return "GOOD"
    if cand_cov >= 0.4:
        return "PARTIAL"
    if cand_cov > 0:
        return "WEAK"
    return "UNCOVERED"


def _pre_launch_creators(ov, live, limit=200):
    """The hero signal: template-funded, not-yet-migrated, fresh wallets — the wallets
    likely to launch in the next ~60 min. Source: creator_funders template amounts,
    joined to wt_ops_v2 operations + migration status."""
    now = int(time.time())
    migrated = {r[0] for r in ov.execute(
        "SELECT creator_wallet FROM wt_ops_v2_creators WHERE migration_time IS NOT NULL").fetchall()}
    wh = _webhooked_set(live)
    # link wallet -> operation (via candidates or wallets table)
    wallet_op = {r["wallet"]: r["operation_uuid"] for r in
                 ov.execute("SELECT wallet, operation_uuid FROM wt_operation_candidates").fetchall()} \
                 if _table_exists(ov, "wt_operation_candidates") else {}
    for r in ov.execute("SELECT wallet, operation_uuid FROM wt_ops_v2_wallets WHERE role='CREATOR'").fetchall():
        wallet_op.setdefault(r["wallet"], r["operation_uuid"])
    op_fam = {r["operation_uuid"]: r["family_uuid"] for r in ov.execute("SELECT operation_uuid, family_uuid FROM wt_ops_v2").fetchall()}
    op_tr = {r["operation_uuid"]: r["treasury_root"] for r in ov.execute("SELECT operation_uuid, treasury_root FROM wt_ops_v2").fetchall()}
    fam = {r["family_uuid"]: r["family_label"] for r in ov.execute("SELECT family_uuid, family_label FROM wt_ops_v2_families").fetchall()}

    # SOURCE: wt_operation_activity (forward-monitor-fresh, REAL on-chain block_time) —
    # NOT creator_funders.first_detected_at (FLEX detection stamp that lags weeks). A
    # wallet is a pre-launch creator iff it received an exact creator-template transfer
    # (ATA-rent …039280 tail + small base) within the last ~3h and hasn't migrated.
    rows = ov.execute(
        "SELECT counterparty, amount, block_time, wallet AS funder, operation_uuid "
        "FROM wt_operation_activity "
        "WHERE block_time > strftime('%s','now')-10800 AND amount BETWEEN 0.05 AND 6.5 "
        "ORDER BY block_time DESC") if _table_exists(ov, "wt_operation_activity") else []
    out = []
    seen = set()
    for r in rows:
        w = r["counterparty"]
        if not w or w in seen or w in migrated:
            continue
        tb = _template_base_of(r["amount"])     # exact ATA-rent-tail template only
        if tb is None:
            continue
        seen.add(w)
        ts = r["block_time"]
        mins = max(0, (now - ts) // 60)
        op = r["operation_uuid"] or wallet_op.get(w)
        window = max(0, LEAD_TIME_MIN - mins)
        out.append({
            "wallet": w, "template": tb,
            "funded_by": r["funder"], "funded_at": ts,
            "minutes_since_funding": mins,
            "expected_launch_window_min": window,
            "countdown_min": window,                    # alias used by the console
            "expected_launch_ts": ts + LEAD_TIME_MIN * 60,
            "migration_status": "PENDING",
            "operation": op[:8] if op else None, "operation_uuid": op,
            "treasury": op_tr.get(op), "family": fam.get(op_fam.get(op)) if op else None,
            "webhooked": w in wh,
            "confidence": 0.9 if mins <= LEAD_TIME_MIN else 0.5,  # within the window = high
        })
    # closest to launch FIRST: in-window leads with the smallest countdown at top,
    # then past-window (countdown 0) after. Within-window before stale.
    out.sort(key=lambda x: (x["countdown_min"] == 0, x["countdown_min"]))
    return [x for x in out if x["minutes_since_funding"] <= 180][:limit]


# Operational event priority — CREATOR_CANDIDATE_ARMED is the single most important
# event (a creator is about to launch). It outranks sweeps, relay activity, normal infra.
_EVENT_PRIORITY = {
    "CREATOR_CANDIDATE_ARMED":     0,   # 🔴 TOP — a creator is armed, ~58m to launch
    "CREATOR_FUNDING_DETECTED":    1,   # creator funded, resolving
    "PRE_LAUNCH_CREATOR_DETECTED": 1,
    "FORWARD_HOP_DETECTED":        2,   # walk progressing
    "FORWARD_WALK_STARTED":        3,   # provisioning chain detected
    "SIGNAL_ACTIVATION":           4,
    "FORWARD_WALK_EXPIRED":        6,
    # everything else (sweeps, relay, infra movement) ranks below
}
_DEFAULT_EVENT_PRIORITY = 5


@ops_dashboard_bp.route("/api/ops-v2/intel/operational-events")
def api_intel_operational_events():
    """Priority-ranked operational event feed. CREATOR_CANDIDATE_ARMED is the top
    operational event — above sweeps, relay activity, or normal infra movement."""
    live = _live_conn()
    try:
        rows = live.execute(
            "SELECT event_type, wallet_address, related_wallet, payload_json, created_at "
            "FROM watchtower_events WHERE created_at > strftime('%s','now')-86400 "
            "ORDER BY created_at DESC LIMIT 200").fetchall()
        events = []
        for r in rows:
            try:
                pl = _json.loads(r["payload_json"]) if r["payload_json"] else {}
            except Exception:
                pl = {}
            et = r["event_type"]
            events.append({
                "type": et, "wallet": r["wallet_address"], "related": r["related_wallet"],
                "ts": r["created_at"], "priority": _EVENT_PRIORITY.get(et, _DEFAULT_EVENT_PRIORITY),
                "is_top": et == "CREATOR_CANDIDATE_ARMED",
                "payload": pl,
            })
        # sort by priority (lower = higher), then recency
        events.sort(key=lambda e: (e["priority"], -e["ts"]))
        return jsonify({
            "events": events[:60],
            "armed_count_24h": sum(1 for e in events if e["type"] == "CREATOR_CANDIDATE_ARMED"),
            "top_event": next((e for e in events if e["is_top"]), None),
        })
    finally:
        live.close()


@ops_dashboard_bp.route("/api/ops-v2/intel/treasury-resolution")
def api_intel_treasury_resolution():
    """PRELIMINARY positional treasury resolver — read-only review layer. Shows where the
    assigned treasury may differ from the upstream convergence root. Changes NO roles;
    flagged rows are candidates for human review, not ground truth (see module docstring)."""
    try:
        from src.core.treasury_positional_resolver import resolutions
        rows = resolutions()
        return jsonify({
            "resolutions": rows,
            "mismatch_review": sum(1 for r in rows if r["status"] == "ROLE_MISMATCH_REVIEW"),
            "unresolved": sum(1 for r in rows if r["status"] == "UNRESOLVED"),
            "ok": sum(1 for r in rows if r["status"] == "OK"),
            "note": "PRELIMINARY — review-only, non-destructive. Roles unchanged.",
        })
    except Exception as e:
        return jsonify({"resolutions": [], "error": str(e)})


@ops_dashboard_bp.route("/api/ops-v2/intel/role-scores/<wallet>")
def api_intel_role_scores(wallet):
    """Behavioural role SCORES for one wallet (treasury / sub_prov / collector /
    pass_through) with reasons. NON-DESTRUCTIVE — scores + evidence only, never re-roots.
    Feeds the Treasury Attribution Review panel for human confirmation. There is no single
    walk rule that identifies the treasury (direct-fanout vs sub-provisioned structures), so
    we score behaviour and a human confirms."""
    try:
        from src.core.operation_discovery_poc import score_wallet_role
        return jsonify({"wallet": wallet, **score_wallet_role(wallet)})
    except Exception as e:
        return jsonify({"wallet": wallet, "error": str(e)}), 500


@ops_dashboard_bp.route("/api/ops-v2/intel/anchor-health")
def api_intel_anchor_health():
    """Anchor health — are the treasury/collector webhooks actually delivering, and is
    the forward-walk → arm pipeline live? Answers: last webhook hit (per anchor), last
    walk started, last armed creator."""
    now = int(time.time())
    ov = _conn(); live = _live_conn()
    try:
        # webhooked anchors (treasuries + collectors that are enrolled)
        enrolled = _webhooked_set(live)
        anchors = []
        # TREASURY only — the walker anchors on verified treasuries (collectors include
        # reclassified sweep hubs that don't provision creators).
        for r in ov.execute(
            "SELECT DISTINCT wallet, role FROM wt_ops_v2_wallets WHERE role='TREASURY'").fetchall():
            w = r["wallet"]
            if w not in enrolled:
                continue
            last_hit = (live.execute(
                "SELECT MAX(block_time) FROM wt_webhook_hits WHERE wallet_address=?", (w,)).fetchone() or [None])[0]
            hits_1h = live.execute(
                "SELECT COUNT(*) FROM wt_webhook_hits WHERE wallet_address=? AND block_time > strftime('%s','now')-3600", (w,)).fetchone()[0]
            anchors.append({
                "wallet": w, "role": r["role"], "last_hit": last_hit,
                "last_hit_age_s": (now - last_hit) if last_hit else None,
                "hits_1h": hits_1h,
                "delivering": last_hit is not None and (now - last_hit) < 3600,
            })
        anchors.sort(key=lambda a: (not a["delivering"], a["last_hit_age_s"] or 1e12))

        # pipeline freshness
        last_walk = last_walk_arm = None
        if _table_exists(ov, "wt_ops_v2_forward_walks"):
            last_walk = (ov.execute("SELECT MAX(started_at) FROM wt_ops_v2_forward_walks").fetchone() or [None])[0]
        last_armed = None
        if _table_exists(ov, "wt_ops_v2_armed"):
            row = ov.execute(
                "SELECT creator_wallet, armed_at, state FROM wt_ops_v2_armed ORDER BY armed_at DESC LIMIT 1").fetchone()
            if row:
                last_armed = {"creator": row["creator_wallet"], "armed_at": row["armed_at"], "state": row["state"]}

        any_delivering = any(a["delivering"] for a in anchors)
        return jsonify({
            "anchors": anchors,
            "anchors_total": len(anchors),
            "anchors_delivering": sum(1 for a in anchors if a["delivering"]),
            "any_delivering": any_delivering,
            "last_webhook_hit": (live.execute("SELECT MAX(block_time) FROM wt_webhook_hits").fetchone() or [None])[0],
            "last_walk_started": last_walk,
            "last_armed_creator": last_armed,
            "status": "DELIVERING" if any_delivering else "SILENT",
        })
    finally:
        ov.close(); live.close()


@ops_dashboard_bp.route("/api/ops-v2/intel/active-walks")
def api_intel_active_walks():
    """Live forward-walk sessions — the early-warning chain-follower in flight."""
    try:
        from src.core.operation_forward_walk import active_walks
        ov = _conn()
        try:
            walks = active_walks(ov)
        finally:
            ov.close()
        return jsonify({
            "walks": walks,
            "walking": sum(1 for w in walks if w["state"] == "WALKING"),
            "armed_from_walk": sum(1 for w in walks if w["state"] == "ARMED"),
        })
    except Exception as e:
        return jsonify({"walks": [], "walking": 0, "armed_from_walk": 0, "error": str(e)})


@ops_dashboard_bp.route("/api/ops-v2/intel/treasury-ws-usage")
def api_intel_treasury_ws_usage():
    """Per-treasury WebSocket usage. Treasuries are permanently WS-subscribed (real-time
    provisioning trigger). This surfaces each treasury's notification volume so a treasury
    that turns into a high-volume swarm hub is visible BEFORE it bloats the daemon. Zero RPC.

    flagged=True when events/hr exceeds WS_TREASURY_BUSY_PER_HR (default 200) — the 'getting
    heavy' signal."""
    BUSY_PER_HR = int(os.environ.get("WS_TREASURY_BUSY_PER_HR", "200"))
    ov = _conn()
    try:
        if not _table_exists(ov, "wt_treasury_ws_usage"):
            return jsonify({"treasuries": [], "subscribed": 0, "flagged": 0,
                            "note": "treasury WS tier not yet initialised"})
        now = int(time.time()); hb = now // 3600
        confirmed = set()
        if _table_exists(ov, "wt_confirmed_treasuries"):
            confirmed = {r[0] for r in ov.execute("SELECT treasury FROM wt_confirmed_treasuries").fetchall()}
        rows = ov.execute(
            "SELECT treasury_wallet, subscribed_at, notif_count, sessions_opened, "
            "last_notif_at, last_notif_sig, notif_count_1h, hour_bucket "
            "FROM wt_treasury_ws_usage ORDER BY notif_count DESC").fetchall()
        out, flagged = [], 0
        for r in rows:
            # 1h count only valid if it's the current hour bucket; otherwise it's stale → 0
            per_hr = r["notif_count_1h"] if r["hour_bucket"] == hb else 0
            is_flagged = per_hr >= BUSY_PER_HR
            if is_flagged:
                flagged += 1
            out.append({
                "treasury": r["treasury_wallet"],
                "confirmed": r["treasury_wallet"] in confirmed,
                "notif_total": r["notif_count"] or 0,
                "sessions_opened": r["sessions_opened"] or 0,
                "events_per_hr": per_hr,
                "last_notif_at": r["last_notif_at"],
                "last_notif_ago_s": (now - r["last_notif_at"]) if r["last_notif_at"] else None,
                "subscribed_at": r["subscribed_at"],
                "flagged_busy": is_flagged,
            })
        return jsonify({"treasuries": out, "subscribed": len(out), "flagged": flagged,
                        "busy_threshold_per_hr": BUSY_PER_HR})
    except Exception as e:
        return jsonify({"treasuries": [], "subscribed": 0, "flagged": 0, "error": str(e)})
    finally:
        ov.close()


@ops_dashboard_bp.route("/ops/tokens")
def ops_tokens_page():
    return render_template("ops_tokens.html", active_page="ops_tokens")


@ops_dashboard_bp.route("/api/ops-v2/intel/token-performance")
def api_intel_token_performance():
    """Recent WATCH tokens with performance + classification tags. Joins the WATCH pipeline's
    classification (watch_candidate_tokens.classified_as) with on-chain performance
    (token_analysis: peak/current MC, migration, risk). Tags: WATCHTOWER (✓ if a confirmed
    cascade launch), SWARM (linked to a buy-swarm subprov), WATCH_LIKE_NEW_OP, UNKNOWN.
    Zero RPC. Query: ?limit=, ?tag=WATCHTOWER|SWARM|... , ?migrated_only=1."""
    limit = min(int(request.args.get("limit", 100)), 500)
    tag_filter = (request.args.get("tag") or "").upper().strip()
    migrated_only = request.args.get("migrated_only") == "1"
    live = _live_conn(); ov = _conn()
    try:
        # confirmed WATCHTOWER launch mints (strongest tag) + buy-swarm subprov→mint links
        wt_launch_mints, swarm_mints = set(), set()
        try:
            wt_launch_mints = {r[0] for r in ov.execute(
                "SELECT mint FROM wt_watchtower_launches WHERE mint IS NOT NULL").fetchall()}
        except Exception:
            pass
        # FARM clustering (operator detection beyond WATCHTOWER) — mint → {funder, mechanism,...}.
        # Persisted by farm_detector.run_farm_scan; mechanism-agnostic (catches the plain-transfer
        # farms WATCHTOWER's wrap-close detection is blind to). Zero RPC here (pure DB read).
        farm_by_mint = {}
        try:
            for r in ov.execute(
                "SELECT l.mint, l.funder, f.mechanism, f.creator_count, f.is_known_treasury, "
                "f.funder_type, f.cex_label "
                "FROM wt_farm_launches l JOIN wt_farms f ON f.funder=l.funder").fetchall():
                farm_by_mint[r[0]] = {"funder": r[1], "mechanism": r[2],
                                      "farm_creators": r[3], "is_known": bool(r[4]),
                                      "funder_type": r[5] or "OPERATOR", "cex_label": r[6]}
        except Exception:
            pass
        try:
            swarm_mints = {r[0] for r in ov.execute(
                "SELECT DISTINCT token_mint FROM wt_candidate_websocket_watches "
                "WHERE state='BUY_SWARM' AND token_mint IS NOT NULL").fetchall()}
        except Exception:
            pass

        where = "WHERE 1=1"
        if migrated_only:
            where += " AND t.migrated_at IS NOT NULL"
        rows = live.execute(
            f"""SELECT w.mint, w.classified_as, w.classification_conf, w.classification_reason,
                       w.prediction_score, w.creator_address,
                       t.market_cap_highest, t.market_cap_current, t.first_observed_mc,
                       t.migrated_at, t.lifecycle_stage, t.risk_level, t.rug_probability,
                       t.market_cap_highest_at_ts, t.created_at
                FROM watch_candidate_tokens w
                LEFT JOIN token_analysis t ON t.mint = w.mint
                {where}
                ORDER BY COALESCE(t.migrated_at, t.created_at, w.updated_at) DESC
                LIMIT ?""", (limit * 3,)).fetchall()
        rows = [dict(r) for r in rows]
        seen_mints = {r["mint"] for r in rows}

        # UNION the DISCOVERY-confirmed WATCHTOWER launches. The cascade ledger
        # (wt_watchtower_launches, 4 rows) only captures the tiny fraction caught in real time;
        # the discovery job (wt_discovered_subprovs, 32 treasury→subprov→creator chains) is the
        # AUTHORITATIVE recent-launch source. Resolve each chain's creator → mint via
        # token_analysis (zero RPC), so the page reflects ALL recent WATCHTOWER launches, not the
        # 18h-stale ledger view. discovery_mints get the WATCHTOWER tag (✓ only if also in ledger).
        discovery_mints = set()
        try:
            disc_creators = [r[0] for r in ov.execute(
                "SELECT DISTINCT first_creator FROM wt_discovered_subprovs "
                "WHERE first_creator IS NOT NULL AND treasury_known=1").fetchall()]
            for _cr in disc_creators:
                tr = live.execute(
                    "SELECT mint, market_cap_highest, market_cap_current, first_observed_mc, "
                    "migrated_at, lifecycle_stage, risk_level, rug_probability, "
                    "market_cap_highest_at_ts, created_at FROM token_analysis "
                    "WHERE earliest_tx_creator=? OR pf_ws_creator=? LIMIT 1", (_cr, _cr)).fetchone()
                if not tr:
                    continue
                tr = dict(tr); m = tr["mint"]
                discovery_mints.add(m)
                if m in seen_mints:
                    continue
                if migrated_only and not tr.get("migrated_at"):
                    continue
                rows.append({"mint": m, "classified_as": "WATCHTOWER", "classification_conf": 1.0,
                             "classification_reason": "discovery_launch_chain", "prediction_score": None,
                             "creator_address": _cr, **{k: v for k, v in tr.items() if k != "mint"}})
                seen_mints.add(m)
        except Exception:
            pass
        # treat discovery mints as WATCHTOWER for tagging even if they also have a watch_candidate row
        wt_launch_mints = set(wt_launch_mints)   # cascade-confirmed (gets the ✓)
        _wt_tag_mints = wt_launch_mints | discovery_mints

        # UNION the confirmed cascade launches not already present (they get the ✓ badge).
        # cascade launch create_times (so a just-caught launch not yet in token_analysis still
        # has a date to sort by — and is NOT hidden by migrated_only).
        wt_create_time = {}
        try:
            for r in ov.execute("SELECT mint, create_time FROM wt_watchtower_launches "
                                "WHERE mint IS NOT NULL").fetchall():
                wt_create_time[r[0]] = r[1]
        except Exception:
            pass
        for m in (wt_launch_mints - seen_mints):
            tr = live.execute(
                "SELECT market_cap_highest, market_cap_current, first_observed_mc, migrated_at, "
                "lifecycle_stage, risk_level, rug_probability, market_cap_highest_at_ts, created_at "
                "FROM token_analysis WHERE mint=?", (m,)).fetchone()
            tr = dict(tr) if tr else {}
            # A cascade-CONFIRMED launch (STRICT, caught at CREATE) is real regardless of whether
            # token_analysis has migrated it yet — do NOT drop it on migrated_only. Fall back to
            # the launch's create_time for sorting when token_analysis hasn't caught up.
            if not tr.get("created_at") and not tr.get("migrated_at"):
                tr["created_at"] = wt_create_time.get(m)
            rows.append({"mint": m, "classified_as": "WATCHTOWER", "classification_conf": 1.0,
                         "classification_reason": "confirmed_cascade_launch",
                         "prediction_score": None, "creator_address": None, **tr})
        # UNION the FARM launches not already present (plain-transfer farms that aren't in the
        # WATCH pipeline OR the WATCHTOWER set — the larger ecosystem we were blind to).
        for m, fm in farm_by_mint.items():
            if m in seen_mints:
                continue
            tr = live.execute(
                "SELECT market_cap_highest, market_cap_current, first_observed_mc, migrated_at, "
                "lifecycle_stage, risk_level, rug_probability, market_cap_highest_at_ts, created_at "
                "FROM token_analysis WHERE mint=?", (m,)).fetchone()
            tr = dict(tr) if tr else {}
            if migrated_only and not tr.get("migrated_at"):
                continue
            rows.append({"mint": m, "classified_as": "FARM", "classification_conf": None,
                         "classification_reason": f"farm:{fm['funder'][:8]} ({fm['mechanism']})",
                         "prediction_score": None, "creator_address": fm.get("funder"), **tr})
            seen_mints.add(m)
        # UNION RECENT GENERAL MIGRATIONS from token_analysis. The page's original base
        # (watch_candidate_tokens) froze ~2 weeks ago, so recent migrated tokens that aren't
        # WATCHTOWER/FARM were invisible. token_analysis IS current (migrations flow live), so
        # pull recent migrated tokens directly — tagged UNKNOWN unless a tag source matches.
        # This makes the page show ALL recent migrated tokens with classification overlaid.
        # FRESH vs UNKNOWN split: a migrated token whose creator is SINGLE-USE (made exactly ONE
        # token ever) carries the WATCHTOWER-style fingerprint — fresh wallet, one launch, migrated.
        # That's high-signal and must NOT be buried in generic UNKNOWN. A serial creator (n>1) is
        # noise → stays UNKNOWN. The creator-count subquery does the split.
        try:
            for r in live.execute(
                "SELECT ta.mint, ta.market_cap_highest, ta.market_cap_current, ta.first_observed_mc, "
                "ta.migrated_at, ta.lifecycle_stage, ta.risk_level, ta.rug_probability, "
                "ta.market_cap_highest_at_ts, ta.created_at, ta.earliest_tx_creator, cc.n AS creator_tokens "
                "FROM token_analysis ta "
                "LEFT JOIN (SELECT earliest_tx_creator cr, COUNT(*) n FROM token_analysis "
                "           WHERE earliest_tx_creator IS NOT NULL GROUP BY earliest_tx_creator) cc "
                "  ON cc.cr = ta.earliest_tx_creator "
                "WHERE ta.migrated_at > strftime('%s','now','-14 days') AND ta.migrated_at IS NOT NULL "
                "ORDER BY ta.migrated_at DESC LIMIT 400").fetchall():
                tr = dict(r); m = tr["mint"]
                if m in seen_mints:
                    continue
                seen_mints.add(m)
                ctok = tr.pop("creator_tokens", None)
                is_fresh = (ctok == 1)   # single-use creator = FRESH (WATCHTOWER-style signature)
                rows.append({"mint": m,
                             "classified_as": "FRESH" if is_fresh else "UNKNOWN",
                             "classification_conf": None,
                             "classification_reason": ("single_use_creator_migrated" if is_fresh
                                                       else "recent_migration"),
                             "prediction_score": None,
                             "creator_address": tr.pop("earliest_tx_creator", None), **tr})
        except Exception:
            pass
        out = []
        for r in rows:
            mint = r["mint"]
            # tag precedence: confirmed launch > pipeline classification > swarm overlay
            base = r.get("classified_as") or "UNKNOWN"
            # WATCH_LIKE_NEW_OP is DROPPED — the WATCH prediction pipeline that produced it froze
            # ~2 weeks ago (all NEW-OP tokens are 3+ weeks old, nothing recent surfaces), and its
            # signals (shared_funder/dormant) are now covered by the live FARM/FRESH tags. Collapse
            # it to UNKNOWN so those stale rows fall back to FRESH/UNKNOWN by their creator instead.
            if base == "WATCH_LIKE_NEW_OP":
                base = "UNKNOWN"
            is_wt_confirmed = mint in wt_launch_mints     # cascade ledger → ✓
            is_wt = mint in _wt_tag_mints                 # cascade OR discovery → WATCHTOWER tag
            is_swarm = mint in swarm_mints
            farm = farm_by_mint.get(mint)                 # operator cluster (any mechanism)
            # tag precedence: WATCHTOWER (wrap-close, confirmed) > FARM (operator cluster) > base
            if is_wt or base == "WATCHTOWER":
                tag = "WATCHTOWER"
            elif farm:
                tag = "FARM"
            else:
                tag = base
            peak = r.get("market_cap_highest"); cur = r.get("market_cap_current")
            entry = r.get("first_observed_mc")
            mult = (peak / entry) if (peak and entry and entry > 0) else None
            retrace = (1 - cur / peak) if (peak and cur and peak > 0) else None
            rec = {
                "mint": mint, "tag": tag,
                "wt_confirmed": is_wt_confirmed, "swarm": is_swarm,
                "farm_funder": (farm or {}).get("funder"),
                "farm_mechanism": (farm or {}).get("mechanism"),
                "farm_creators": (farm or {}).get("farm_creators"),
                "farm_funder_type": (farm or {}).get("funder_type"),
                "farm_cex_label": (farm or {}).get("cex_label"),
                "classification_conf": r.get("classification_conf"),
                "classification_reason": r.get("classification_reason"),
                "prediction_score": r.get("prediction_score"),
                "creator": r.get("creator_address"),
                "peak_mc": peak, "current_mc": cur, "entry_mc": entry,
                "peak_multiple": round(mult, 1) if mult else None,
                "retrace_pct": round(retrace * 100, 0) if retrace is not None else None,
                "migrated_at": r.get("migrated_at"), "stage": r.get("lifecycle_stage"),
                "risk_level": r.get("risk_level"), "rug_probability": r.get("rug_probability"),
                "peak_at": r.get("market_cap_highest_at_ts"), "created_at": r.get("created_at"),
            }
            if tag_filter:
                if tag_filter == "SWARM" and not is_swarm:
                    continue
                if tag_filter != "SWARM" and tag != tag_filter:
                    continue
            out.append(rec)
        # SORT BY RECENCY ACROSS ALL SOURCES, THEN truncate. The watch_candidate rows and the
        # UNION'd discovery/cascade launches are merged out of order — the discovery launches are
        # the freshest but were appended last, so a source-order cut buried them (the page started
        # at 13-day-old tokens). Order by migrated_at → created_at → 0 so the newest float up.
        out.sort(key=lambda r: (r.get("migrated_at") or r.get("created_at") or 0), reverse=True)
        out = out[:limit]
        # summary counts (over the returned window)
        from collections import Counter as _Ct
        counts = dict(_Ct(r["tag"] for r in out))
        counts["SWARM"] = sum(1 for r in out if r["swarm"])
        counts["WATCHTOWER_confirmed"] = sum(1 for r in out if r["wt_confirmed"])
        return jsonify({"tokens": out, "count": len(out), "counts": counts,
                        "farms_total": len({r["farm_funder"] for r in out if r.get("farm_funder")})})
    except Exception as e:
        return jsonify({"tokens": [], "count": 0, "error": str(e)})
    finally:
        live.close(); ov.close()


@ops_dashboard_bp.route("/api/ops-v2/intel/active-ws-sessions")
def api_intel_active_ws_sessions():
    """The TEMPORARY WS subscriptions the cascade currently holds beyond the permanent
    treasuries — one per ACTIVE subprov session, opened only on a ≥WS_TREASURY_MIN_SOL
    provisioning load. Lets you SEE that only large-amount launch candidates are subscribed
    and no buy-swarm/dust account slipped in (any row below the floor = a swarm leak). Zero RPC."""
    FLOOR = float(os.environ.get("WS_TREASURY_MIN_SOL", "50"))
    ov = _conn()
    try:
        if not _table_exists(ov, "wt_active_subprov_sessions"):
            return jsonify({"sessions": [], "active": 0, "floor_sol": FLOOR})
        now = int(time.time())
        # candidate counts per ACTIVE subprov (watching vs total)
        cand_total, cand_watching = {}, {}
        if _table_exists(ov, "wt_candidate_websocket_watches"):
            for r in ov.execute(
                "SELECT subprov_wallet, COUNT(*) n, SUM(state='WATCHING') w "
                "FROM wt_candidate_websocket_watches GROUP BY subprov_wallet").fetchall():
                cand_total[r[0]] = r[1]; cand_watching[r[0]] = r[2] or 0
        rows = ov.execute(
            "SELECT subprov_wallet, treasury_wallet, funding_amount, detected_at, expires_at "
            "FROM wt_active_subprov_sessions WHERE state='ACTIVE' ORDER BY detected_at DESC").fetchall()
        out, below_floor = [], 0
        for r in rows:
            amt = r["funding_amount"]
            leak = amt is not None and amt < FLOOR
            if leak:
                below_floor += 1
            ttl = (r["expires_at"] - now) if r["expires_at"] else None
            sp = r["subprov_wallet"]
            out.append({
                "subprov": sp, "treasury": r["treasury_wallet"],
                "funding_sol": amt,
                "candidates_total": cand_total.get(sp, 0),
                "candidates_watching": cand_watching.get(sp, 0),
                "ttl_remaining_s": ttl if (ttl is None or ttl > 0) else 0,
                "age_s": (now - r["detected_at"]) if r["detected_at"] else None,
                "below_floor": leak,   # True = a swarm/dust account that shouldn't be subscribed
            })
        return jsonify({"sessions": out, "active": len(out), "floor_sol": FLOOR,
                        "below_floor": below_floor})
    except Exception as e:
        return jsonify({"sessions": [], "active": 0, "floor_sol": FLOOR, "error": str(e)})
    finally:
        ov.close()


@ops_dashboard_bp.route("/api/ops-v2/intel/armed")
def api_intel_armed():
    """Currently-ARMED creators: template-funded, webhooked, ~58-min countdown running.
    Plus recently FIRED (launched) with their actual lead time."""
    now = int(time.time())
    ov = _conn()
    try:
        if not _table_exists(ov, "wt_ops_v2_armed"):
            return jsonify({"armed": [], "recently_fired": [], "stats": {}})
        armed = []
        for r in ov.execute(
            "SELECT creator_wallet, operation_uuid, treasury, template_base, funded_at, "
            "expected_launch, webhooked FROM wt_ops_v2_armed WHERE state='ARMED' "
            "ORDER BY expected_launch ASC").fetchall():
            armed.append({
                "creator": r["creator_wallet"], "operation": (r["operation_uuid"] or "")[:8],
                "operation_uuid": r["operation_uuid"], "treasury": r["treasury"],
                "template": r["template_base"], "funded_at": r["funded_at"],
                "expected_launch": r["expected_launch"],
                "countdown_min": max(0, (r["expected_launch"] - now) // 60),
                "webhooked": bool(r["webhooked"]),
            })
        fired = [{"creator": r["creator_wallet"], "operation": (r["operation_uuid"] or "")[:8],
                  "lead_time_min": r["lead_time_min"], "migration_time": r["migration_time"]}
                 for r in ov.execute(
                     "SELECT creator_wallet, operation_uuid, lead_time_min, migration_time "
                     "FROM wt_ops_v2_armed WHERE state='FIRED' ORDER BY migration_time DESC LIMIT 20").fetchall()]
        st = {row[0]: row[1] for row in ov.execute(
            "SELECT state, COUNT(*) FROM wt_ops_v2_armed GROUP BY state").fetchall()}
        return jsonify({"armed": armed, "recently_fired": fired, "stats": st})
    finally:
        ov.close()


@ops_dashboard_bp.route("/api/ops-v2/intel/pre-launch-creators")
def api_intel_pre_launch():
    ov = _conn(); live = _live_conn()
    try:
        return jsonify({"creators": _pre_launch_creators(ov, live)})
    finally:
        ov.close(); live.close()


@ops_dashboard_bp.route("/api/ops-v2/intel/launch-metrics")
def api_intel_launch_metrics():
    """Pre-launch counts + average lead time (template funding → migration)."""
    import statistics
    ov = _conn(); live = _live_conn()
    try:
        plc = _pre_launch_creators(ov, live, limit=1000)
        within60 = sum(1 for c in plc if c["minutes_since_funding"] <= 60)
        within30 = sum(1 for c in plc if c["minutes_since_funding"] <= 30)
        # avg lead time over recently migrated creators with template funding
        leads = []
        amt_ph = ",".join("?" * len(CREATOR_TEMPLATE_AMOUNTS))
        for cw, mt in ov.execute(
            "SELECT creator_wallet, migration_time FROM wt_ops_v2_creators "
            "WHERE migration_time IS NOT NULL ORDER BY migration_time DESC LIMIT 80").fetchall():
            f = live.execute(
                f"SELECT first_detected_at FROM creator_funders WHERE creator_address=? "
                f"AND amount_sol IN ({amt_ph}) LIMIT 1", [cw] + list(CREATOR_TEMPLATE_AMOUNTS)).fetchone()
            if f and f[0]:
                ft = _parse_ts(f[0])
                if ft and mt > ft and (mt - ft) < 86400:
                    leads.append((mt - ft) / 60)
        launches24 = ov.execute(
            "SELECT COUNT(*) FROM wt_ops_v2_creators WHERE migration_time > strftime('%s','now')-86400").fetchone()[0]
        return jsonify({
            "pre_launch_creators": len(plc),
            "within_60m": within60, "within_30m": within30,
            "launches_24h": launches24,
            "avg_lead_time_min": round(statistics.mean(leads)) if leads else None,
            "median_lead_time_min": round(statistics.median(leads)) if leads else None,
            "lead_sample_n": len(leads),
        })
    finally:
        ov.close(); live.close()


TREASURY_STATS_TTL = 1800   # refresh a treasury's tx/24h at most every 30 min


def _treasury_tx_24h(treasury: str, ov) -> int:
    """Cached tx/24h for a treasury. RPC is expensive (firehoses), so cache in
    wt_ops_v2_treasury_stats with a 30-min TTL and refresh only stale rows."""
    import os as _os, json as _json, urllib.request, urllib.error
    ov.execute("""CREATE TABLE IF NOT EXISTS wt_ops_v2_treasury_stats (
        treasury TEXT PRIMARY KEY, tx_24h INTEGER, computed_at INTEGER)""")
    now = int(time.time())
    row = ov.execute("SELECT tx_24h, computed_at FROM wt_ops_v2_treasury_stats WHERE treasury=?", (treasury,)).fetchone()
    if row and row["computed_at"] and (now - row["computed_at"]) < TREASURY_STATS_TTL:
        return row["tx_24h"]
    # refresh via paginated getSignaturesForAddress (bounded to keep it cheap)
    key = _os.environ.get("HELIUS_API_KEY", "")
    if not key:
        return row["tx_24h"] if row else None
    cutoff = now - 86400
    total, before, pages = 0, None, 0
    try:
        while pages < 6:
            body = _json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getSignaturesForAddress",
                                "params": [treasury, {"limit": 1000, **({"before": before} if before else {})}]}).encode()
            req = urllib.request.Request(f"https://mainnet.helius-rpc.com/?api-key={key}", data=body,
                                         headers={"Content-Type": "application/json", "User-Agent": "flex-intel/0.1"})
            r = _json.loads(urllib.request.urlopen(req, timeout=15).read()).get("result", [])
            if not r:
                break
            in24 = [x for x in r if (x.get("blockTime") or 0) >= cutoff]
            total += len(in24)
            if len(in24) < len(r) or len(r) < 1000:
                break
            before = r[-1]["signature"]; pages += 1
        if pages >= 6:
            total = f"{total}+"  # hit the cap
    except (urllib.error.URLError, Exception):
        return row["tx_24h"] if row else None
    tx_val = int(str(total).rstrip("+")) if total else 0
    capped = isinstance(total, str)
    ov.execute("INSERT INTO wt_ops_v2_treasury_stats (treasury, tx_24h, computed_at) VALUES (?,?,?) "
               "ON CONFLICT(treasury) DO UPDATE SET tx_24h=excluded.tx_24h, computed_at=excluded.computed_at",
               (treasury, tx_val, now))
    ov.commit()
    return f"{tx_val}+" if capped else tx_val


@ops_dashboard_bp.route("/api/ops-v2/intel/treasury-funders")
def api_intel_treasury_funders():
    """The APEX layer: wallets that FUND the confirmed treasuries (the layer above).
    Computed LIVE from the inbound-hit log every call (zero RPC, always current — no stale
    snapshot table). A funder that funds MULTIPLE treasuries is a shared apex (links the
    operation); subprov-sweeps (a subprov recycling capital back up) are flagged separately.
    These are the predicted next-rotation sources."""
    ov = _conn(); live = _live_conn()
    try:
        confirmed = {r[0] for r in ov.execute("SELECT treasury FROM wt_confirmed_treasuries").fetchall()}
        if not confirmed:
            return jsonify({"funders": [], "shared_apexes": [], "count": 0})
        subprovs = set()
        buyswarm_wallets = set()       # wallets the system ALREADY classified BUY_SWARM (local, zero RPC)
        try:
            # known subprovs = BOTH sources (wrap-close candidates AND the discovered-subprov table).
            # A known subprov sending into a treasury is SWEEP (recycling up), NOT external capital —
            # reading only wt_wrap_close_candidates missed subprovs that live only in
            # wt_discovered_subprovs (e.g. DZ81n7cc, 8oackoLD → were false EXTERNAL leads).
            subprovs = {r[0] for r in ov.execute(
                "SELECT DISTINCT subprov_wallet FROM wt_wrap_close_candidates WHERE subprov_wallet IS NOT NULL").fetchall()}
            try:
                subprovs |= {r[0] for r in ov.execute(
                    "SELECT subprov FROM wt_discovered_subprovs WHERE subprov IS NOT NULL").fetchall()}
            except Exception:
                pass
            # a wallet is buy-swarm infra if it (or its wrap-close children) were marked BUY_SWARM —
            # the subprov runs a trading/fan-out op (wallets SWAP many tokens, never CREATE).
            for r in ov.execute(
                "SELECT subprov_wallet, creator FROM wt_wrap_close_candidates WHERE state='BUY_SWARM'").fetchall():
                if r[0]:
                    buyswarm_wallets.add(r[0])
                if r[1]:
                    buyswarm_wallets.add(r[1])
        except Exception:
            pass
        ph = ",".join("?" * len(confirmed))
        # inbound capital to confirmed treasuries, grouped by (treasury, funder)
        rows = live.execute(
            f"SELECT wallet_address treasury, counterparty funder, COUNT(*) n, "
            f"SUM(amount_sol) tot, MAX(amount_sol) mx, MAX(block_time) ls "
            f"FROM wt_webhook_hits WHERE direction='inbound' AND amount_sol > 1 "
            f"AND counterparty IS NOT NULL AND wallet_address IN ({ph}) "
            f"GROUP BY wallet_address, counterparty", list(confirmed)).fetchall()
        # RECYCLING detection: a (treasury,funder) pair where the treasury ALSO sent OUT to the
        # funder = the funder is recycling treasury money back, NOT genuine external capital.
        # Needs the bidirectional log (treasury outbounds — fixed via composite-PK storage).
        paid_back = set()
        out_to_funder = {}             # funder → total SOL the treasuries sent OUT to it (for net-flow)
        try:
            for r in live.execute(
                f"SELECT wallet_address t, counterparty f, SUM(amount_sol) o FROM wt_webhook_hits "
                f"WHERE direction='outbound' AND counterparty IS NOT NULL AND wallet_address IN ({ph}) "
                f"GROUP BY wallet_address, counterparty",
                list(confirmed)).fetchall():
                paid_back.add((r["t"], r["f"]))
                out_to_funder[r["f"]] = (out_to_funder.get(r["f"], 0.0) + (r["o"] or 0.0))
        except Exception:
            pass
        agg = {}
        for r in rows:
            f = r["funder"]
            d = agg.setdefault(f, {"funder": f, "treasuries": set(), "recycled_to": set(),
                                   "fund_count": 0, "total_sol": 0.0, "max_sol": 0.0, "last_seen": 0,
                                   "is_subprov_sweep": 1 if f in subprovs else 0})
            d["treasuries"].add(r["treasury"])
            if (r["treasury"], f) in paid_back:
                d["recycled_to"].add(r["treasury"])
            d["fund_count"] += r["n"] or 0
            d["total_sol"] += r["tot"] or 0
            d["max_sol"] = max(d["max_sol"], r["mx"] or 0)
            d["last_seen"] = max(d["last_seen"], r["ls"] or 0)
        funders = []
        for d in agg.values():
            tf = len(d["treasuries"])
            d["treasuries_funded"] = tf
            d["is_known_treasury"] = d["funder"] in confirmed
            # recycling = treasury paid this funder back for at least one of the pairs
            d["is_recycling"] = len(d["recycled_to"]) > 0
            d.pop("treasuries"); d.pop("recycled_to")
            d["is_shared_apex"] = tf > 1 and not d["is_subprov_sweep"]
            d["total_sol"] = round(d["total_sol"], 1)
            # BUY-SWARM signals (local, zero RPC):
            #  (a) the wallet is already BUY_SWARM-classified (it/its wrap-close children SWAP
            #      many tokens, never CREATE), OR
            #  (b) net-NEGATIVE treasury flow: the treasury sent it MORE than came back (out > in
            #      by a margin) — the fingerprint of a TRADING op (capital spent on tokens),
            #      distinct from RECYCLING (a wash, net ≈ 0).
            _out = out_to_funder.get(d["funder"], 0.0)
            _in = d["total_sol"]
            d["treasury_out_sol"] = round(_out, 1)
            d["net_to_treasury_sol"] = round(_in - _out, 1)
            _net_negative_trade = _out > 0 and (_in < _out * 0.9)   # returned <90% → spent on trades
            d["is_buy_swarm"] = (d["funder"] in buyswarm_wallets) or _net_negative_trade
            # ── EXPANSION CLASS (priority order) — the network-growth classifier ──
            # MESH (known treasury funds another) and HUB (funds multiple treasuries) are
            # structural network signals that OUTRANK everything — a wallet linking treasuries
            # is a hub even if it also trades/recycles. BUY_SWARM/recycling/sweep are
            # "not-an-expansion-lead" categories for single-treasury funders.
            if d["is_known_treasury"]:
                d["expansion_class"] = "TREASURY_MESH"    # known treasury funds another = mesh growth ★★★
            elif tf > 1:
                d["expansion_class"] = "HUB"              # funds multiple treasuries = network hub ★★
            elif d["is_buy_swarm"]:
                d["expansion_class"] = "BUY_SWARM"        # treasury-funded TRADING op (swaps many tokens, net-negative) — NOT a lead
            elif d["is_subprov_sweep"]:
                d["expansion_class"] = "SWEEP"            # subprov recycling up
            elif d["is_recycling"]:
                d["expansion_class"] = "RECYCLING"        # treasury funded it first, pays back (wash)
            else:
                d["expansion_class"] = "EXTERNAL"         # genuine external capital → expansion candidate ★
            funders.append(d)
        # expansion-priority first: mesh > hub > external, then the non-lead categories, then recency
        _rank = {"TREASURY_MESH": 0, "HUB": 1, "EXTERNAL": 2, "BUY_SWARM": 3, "RECYCLING": 4, "SWEEP": 5}
        funders.sort(key=lambda x: (_rank.get(x["expansion_class"], 5), -(x["last_seen"] or 0)))
        from collections import Counter as _C
        return jsonify({"funders": funders, "count": len(funders),
                        "shared_apexes": [f["funder"] for f in funders if f["is_shared_apex"]],
                        "class_counts": dict(_C(f["expansion_class"] for f in funders))})
    finally:
        ov.close(); live.close()


@ops_dashboard_bp.route("/api/ops-v2/intel/subprovs")
def api_intel_subprovs():
    """Sub-provisioners — the wallets that do the wrap-close creator provisioning. Each was
    funded by a TREASURY (the layer above). When a subprov's treasury is UNKNOWN, it's a lead
    to a NEW treasury: backward-walking to it is impractical (busy subprovs bury the funding
    under 10,000s of txs), so these are surfaced for MANUAL on-chain tracing — check who funded
    the subprov to find the (possibly unknown) treasury."""
    ov = _conn()
    try:
        # Source = wt_discovered_subprovs (full migration coverage, fed by the subprov-discovery
        # job — every migration's creator, no creator_funders dependency). Only surface
        # UNKNOWN-treasury subprovs (the leads to investigate); known ones are resolved/hidden.
        if not _table_exists(ov, "wt_discovered_subprovs"):
            return jsonify({"subprovs": [], "count": 0, "unknown_treasury": 0, "known_resolved": 0})
        # A subprov is RESOLVED (removed from leads) once we know its treasury — that means the
        # treasury is CONFIRMED *or* already in REVIEW (we've identified it, awaiting promotion).
        # Plus the existing wrap-close lineage data. Only subprovs whose treasury is genuinely
        # UNKNOWN remain as leads to investigate.
        confirmed = {r[0] for r in ov.execute("SELECT treasury FROM wt_confirmed_treasuries").fetchall()}
        reviewing = set()
        try:
            reviewing = {r[0] for r in ov.execute(
                "SELECT treasury FROM wt_treasury_review WHERE status='PENDING_REVIEW'").fetchall()}
        except Exception:
            pass
        # subprov → treasury from existing wrap-close lineage (a second resolution source)
        lineage = {}
        try:
            for r in ov.execute("SELECT subprov_wallet, MAX(lineage_source_treasury) t "
                                "FROM wt_wrap_close_candidates WHERE subprov_wallet IS NOT NULL "
                                "GROUP BY subprov_wallet").fetchall():
                if r[1]:
                    lineage[r[0]] = r[1]
        except Exception:
            pass
        rows = ov.execute(
            "SELECT subprov, first_creator, creator_count, treasury, treasury_known, last_seen "
            "FROM wt_discovered_subprovs ORDER BY last_seen DESC").fetchall()
        out = []
        known_count = 0
        for r in rows:
            sp = r["subprov"]
            treasury = r["treasury"] or lineage.get(sp)
            # resolved if the treasury is known (confirmed OR in review OR linked via lineage)
            if treasury and (treasury in confirmed or treasury in reviewing):
                known_count += 1
                continue
            # a treasury we have but haven't confirmed/reviewed = still a lead, but show the addr
            out.append({
                "subprov": sp,
                "creators": r["creator_count"],
                "treasury": treasury,
                "treasury_status": ("pending" if treasury in reviewing else "unknown") if treasury else "unknown",
                "treasury_known": False,
                "total_sol": None,
                "last_seen": r["last_seen"],
                "first_creator": r["first_creator"],
            })
        return jsonify({"subprovs": out, "count": len(out),
                        "unknown_treasury": len(out), "known_resolved": known_count})
    finally:
        ov.close()


@ops_dashboard_bp.route("/api/ops-v2/intel/ws-cascade")
def api_intel_ws_cascade():
    """Active Websocket Cascade panel. Real-time SUB_PROV sessions + candidate watches +
    the latest WATCHTOWER launch. Read-only from wt_ops_v2.db (cascade tables) + the
    ws_cascade worker heartbeat (live db) for WS health."""
    ov = _conn()
    try:
        if not _table_exists(ov, "wt_active_subprov_sessions"):
            return jsonify({"sessions": [], "watches_by_subprov": {}, "candidate_count": 0,
                            "latest_launch": None, "ws_health": "DOWN", "cleanup_count": 0,
                            "last_wrap_close": None, "last_create": None})
        now = int(time.time())
        sessions = []
        for r in ov.execute(
            "SELECT id, subprov_wallet, treasury_wallet, funding_amount, funding_time, "
            "detected_at, expires_at FROM wt_active_subprov_sessions WHERE state='ACTIVE' "
            "ORDER BY detected_at DESC").fetchall():
            sessions.append({
                "subprov": r["subprov_wallet"], "treasury": r["treasury_wallet"],
                "funding_amount": r["funding_amount"], "funding_time": r["funding_time"],
                "ttl_remaining": max(0, (r["expires_at"] or now) - now),
                "candidates": ov.execute(
                    "SELECT COUNT(*) FROM wt_candidate_websocket_watches "
                    "WHERE subprov_wallet=? AND state='WATCHING'", (r["subprov_wallet"],)).fetchone()[0],
            })
        # candidate watches grouped by subprov (WATCHING only)
        watches = {}
        total_watching = 0
        for r in ov.execute(
            "SELECT candidate_wallet, subprov_wallet, funding_amount, detected_at, expires_at "
            "FROM wt_candidate_websocket_watches WHERE state='WATCHING' "
            "ORDER BY detected_at DESC").fetchall():
            total_watching += 1
            watches.setdefault(r["subprov_wallet"], []).append({
                "candidate": r["candidate_wallet"], "amount": r["funding_amount"],
                "ttl_remaining": max(0, (r["expires_at"] or now) - now),
            })
        last_wrap = ov.execute(
            "SELECT MAX(detected_at) FROM wt_candidate_websocket_watches").fetchone()[0]
        last_create = ov.execute("SELECT MAX(create_time) FROM wt_watchtower_launches").fetchone()[0]
        ll = None
        lr = ov.execute(
            "SELECT mint, creator_wallet, create_signature, create_time, treasury_wallet, "
            "subprov_wallet, birth_to_launch_seconds, confidence FROM wt_watchtower_launches "
            "ORDER BY id DESC LIMIT 1").fetchone()
        if lr:
            btl = lr["birth_to_launch_seconds"]
            ll = {"mint": lr["mint"], "creator": lr["creator_wallet"],
                  "create_sig": lr["create_signature"], "create_time": lr["create_time"],
                  "treasury": lr["treasury_wallet"], "subprov": lr["subprov_wallet"],
                  "birth_to_launch_s": btl, "confidence": lr["confidence"],
                  "mode": ("INSTANT" if btl is not None and btl < 60 else
                           ("STAGED" if btl is not None else None))}
        launches_total = ov.execute("SELECT COUNT(*) FROM wt_watchtower_launches").fetchone()[0]
        # WS health + cleanup count from the ws_cascade heartbeat (lives in the ops db —
        # quiet, no lock contention with the hot live db).
        ws_health, cleanup_count, hb_age = "DOWN", 0, None
        if _table_exists(ov, "wt_worker_heartbeat"):
            hb = ov.execute(
                "SELECT last_seen, meta_json FROM wt_worker_heartbeat WHERE worker_name='ws_cascade'").fetchone()
            if hb:
                hb_age = int(time.time()) - (hb["last_seen"] or 0)
                ws_health = "LIVE" if hb_age < 90 else "STALE"
                try:
                    cleanup_count = int((_json.loads(hb["meta_json"] or "{}")).get("cleanups", 0))
                except Exception:
                    pass
    finally:
        ov.close()
    return jsonify({
        "sessions": sessions, "watches_by_subprov": watches,
        "candidate_count": total_watching, "active_subprovs": len(sessions),
        "latest_launch": ll, "launches_total": launches_total,
        "ws_health": ws_health, "heartbeat_age_s": hb_age, "cleanup_count": cleanup_count,
        "last_wrap_close": last_wrap, "last_create": last_create,
    })


@ops_dashboard_bp.route("/api/ops-v2/intel/launch-audit")
def api_intel_launch_audit():
    """Launch Audit panel — is WATCHTOWER detection ACTIONABLE? Per detected launch: detection
    latency, our entry position, MC at detection, peak MC, the headline actionable_multiple
    (peak_mc / mc_at_detection), time-to-peak, outcome. Plus the aggregate report (medians +
    multiple buckets). Read-only over wt_launch_audit (the audit pipeline owns the writes)."""
    ov = _conn()
    try:
        if not _table_exists(ov, "wt_launch_audit"):
            return jsonify({"launches": [], "report": None})
        rows = ov.execute(
            "SELECT mint, creator, treasury, create_time, detection_latency_ms, "
            "our_possible_buy_index_estimate, first_external_buy_slot, mc_at_create, "
            "mc_at_detection, mc_at_first_external_buy, peak_mc, current_mc, "
            "actionable_multiple, time_to_peak_s, migrated, dumped_before_migration, "
            "final_state, audit_state, mc_at_detection_source, peak_mc_source, source "
            "FROM wt_launch_audit ORDER BY created_at DESC LIMIT 50").fetchall()
        # the FULL funding profile per launch (treasury→subprov load + wrap-close seed) lives on
        # the cascade's launch ledger — join it by mint so each audit row shows provisioning cost.
        funding = {}
        try:
            for fr in ov.execute(
                "SELECT mint, subprov_funding_sol, wrap_close_sol FROM wt_watchtower_launches "
                "WHERE mint IS NOT NULL").fetchall():
                funding[fr["mint"]] = (fr["subprov_funding_sol"], fr["wrap_close_sol"])
        except Exception:
            pass
        launches = [{
            "mint": r["mint"], "creator": r["creator"], "treasury": r["treasury"],
            "create_time": r["create_time"], "detection_latency_ms": r["detection_latency_ms"],
            "position": r["our_possible_buy_index_estimate"],
            "first_external_buy_slot": r["first_external_buy_slot"],
            "mc_at_create": r["mc_at_create"], "mc_at_detection": r["mc_at_detection"],
            "mc_at_first_external_buy": r["mc_at_first_external_buy"],
            "peak_mc": r["peak_mc"], "current_mc": r["current_mc"],
            "actionable_multiple": r["actionable_multiple"], "time_to_peak_s": r["time_to_peak_s"],
            "migrated": r["migrated"], "dumped": r["dumped_before_migration"],
            "final_state": r["final_state"], "audit_state": r["audit_state"],
            "mc_detection_source": r["mc_at_detection_source"], "peak_source": r["peak_mc_source"],
            "source": r["source"],
            "subprov_funding_sol": funding.get(r["mint"], (None, None))[0],
            "wrap_close_sol": funding.get(r["mint"], (None, None))[1],
        } for r in rows]
    finally:
        ov.close()
    # aggregate report (reuse the module's own logic so it stays one source of truth)
    report = None
    try:
        from src.core import launch_audit
        report = launch_audit.outcome_report()
    except Exception:
        report = None
    return jsonify({"launches": launches, "report": report})


@ops_dashboard_bp.route("/api/ops-v2/intel/vanity-families")
def api_intel_vanity_families():
    """Vanity-family evidence — wallets sharing a deliberate vanity prefix with known
    WATCHTOWER infra (e.g. the 44or family: treasury + dual signallers). EVIDENCE of same
    operator, never a role/treasury assignment. Read-only over wt_vanity_families +
    wt_vanity_matches; full addresses throughout."""
    try:
        from src.core import vanity_family
        return jsonify(vanity_family.families_overview())
    except Exception as e:
        return jsonify({"families": [], "matches": [], "match_count": 0, "error": str(e)})


def _subprov_oldest_funder(subprov):
    """RPC verify: find the subprov's PROVISIONING funder — the SOL sender in its
    OLDEST transaction. A provisioned wallet's first-ever tx is the treasury seeding it
    (confirmed: BZeKsV's oldest tx is yUpm7rKX → 700 SOL, exactly Solscan's "Funded by";
    matches the 700/800-SOL provisioning signature). This holds for single-use subprovs
    AND busy fan-out wallets alike — the seed precedes all activity. Returns
    (funder|None, note, amount_sol|None)."""
    import urllib.request, urllib.error
    key = os.environ.get("HELIUS_API_KEY", "")
    if not key:
        return None, "no HELIUS_API_KEY — cannot verify", None
    rpc = f"https://mainnet.helius-rpc.com/?api-key={key}"

    def _post(method, params):
        body = _json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        req = urllib.request.Request(rpc, data=body,
                                     headers={"Content-Type": "application/json", "User-Agent": "flex-intel/0.1"})
        return _json.loads(urllib.request.urlopen(req, timeout=20).read()).get("result")

    try:
        # page (oldest-first via repeated `before=`) to the very oldest signature — the
        # seeding tx. Bounded by MAX_PAGES; busy wallets (14k+ sigs) still terminate.
        MAX_PAGES = 30
        oldest, before, pages = None, None, 0
        while pages < MAX_PAGES:
            r = _post("getSignaturesForAddress",
                      [subprov, {"limit": 1000, **({"before": before} if before else {})}])
            if not r:
                break
            oldest = r[-1]["signature"]
            if len(r) < 1000:
                break
            before = oldest; pages += 1
        if not oldest:
            return None, "no transactions found for subprov", None
        tx = _post("getTransaction", [oldest, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
        if not tx:
            return None, "could not fetch oldest tx", None
        meta = tx.get("meta") or {}
        keys = [k.get("pubkey") if isinstance(k, dict) else k
                for k in (tx.get("transaction", {}).get("message", {}).get("accountKeys") or [])]
        pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
        try:
            sp_idx = keys.index(subprov)
        except ValueError:
            return None, "subprov not in oldest tx account keys", None
        if sp_idx >= min(len(pre), len(post)) or post[sp_idx] <= pre[sp_idx]:
            return None, "oldest tx is not an inbound funding to subprov", None
        gain = post[sp_idx] - pre[sp_idx]
        deltas = [(keys[i], (post[i] - pre[i])) for i in range(min(len(pre), len(post), len(keys)))]
        senders = sorted([d for d in deltas if d[1] < 0], key=lambda x: x[1])
        if not senders:
            return None, "no net sender in oldest tx", None
        amt = gain / 1e9
        return senders[0][0], f"seed {amt:.3f} SOL, oldest tx {oldest[:16]}…", amt
    except (urllib.error.URLError, Exception) as e:
        return None, f"rpc error: {e}", None


@ops_dashboard_bp.route("/api/ops-v2/intel/subprov-funder", methods=["POST"])
def api_intel_subprov_funder():
    """Human action on a SUB_PROV's funding (treasury) address.

    POST { confirm:true, subprov:..., action:'set'|'remove', treasury:... }

    action='set'    → verify on-chain (1 RPC: oldest-tx funder of the subprov must
                      match `treasury`), write the subprov→treasury link
                      (treasury_known=1), AUTO-CONFIRM the treasury into
                      wt_confirmed_treasuries, and webhook it. The subprov leaves the
                      UNKNOWN-leads list.
    action='remove' → clear the funder off the subprov (treasury=NULL,
                      treasury_known=0) → back to an UNKNOWN lead. Does NOT touch the
                      treasury itself (it may fund other subprovs)."""
    body = request.get_json(silent=True) or {}
    if body.get("confirm") is not True or not body.get("subprov"):
        return jsonify({"error": "confirmation + subprov required"}), 400
    sp = body["subprov"].strip()
    action = body.get("action", "set")
    ov = _conn()
    try:
        if not _table_exists(ov, "wt_discovered_subprovs"):
            return jsonify({"error": "wt_discovered_subprovs missing"}), 400
        exists = ov.execute("SELECT 1 FROM wt_discovered_subprovs WHERE subprov=?", (sp,)).fetchone()
        if not exists:
            return jsonify({"error": f"subprov {sp[:12]}… not found"}), 404

        if action == "remove":
            ov.execute("UPDATE wt_discovered_subprovs SET treasury=NULL, treasury_known=0 WHERE subprov=?", (sp,))
            ov.commit()
            return jsonify({"ok": True, "action": "remove", "subprov": sp,
                            "message": "funder cleared — subprov back to UNKNOWN lead"})

        # action == 'set'
        treasury = (body.get("treasury") or "").strip()
        if not treasury:
            return jsonify({"error": "treasury (funder) address required for set"}), 400

        # RPC verify: the subprov's PROVISIONING funder (largest inbound transfer) must
        # match the typed treasury. If it doesn't match (typo, or a multi-hop funding the
        # simple check can't see), block — UNLESS the caller passes override:true.
        funder, note, _amt = _subprov_oldest_funder(sp)
        verified = bool(funder) and funder == treasury
        if not verified and not body.get("override"):
            return jsonify({"error": "on-chain verification failed",
                            "typed_treasury": treasury,
                            "onchain_funder": funder,
                            "note": note,
                            "hint": "the address you typed does not match the subprov's largest-inbound "
                                    "(provisioning) funder; pass override:true to write it anyway"}), 409

        # write the subprov→treasury link
        ov.execute("UPDATE wt_discovered_subprovs SET treasury=?, treasury_known=1 WHERE subprov=?",
                   (treasury, sp))
        # AUTO-CONFIRM the treasury (idempotent) — provenance marks the source
        ov.execute(
            "INSERT INTO wt_confirmed_treasuries (treasury, method, confidence, confirmed_at, provenance) "
            "VALUES (?, 'subprov_funder_trace', 'MANUAL', ?, 'CONFIRMED_SUBPROV_TRACE') "
            "ON CONFLICT(treasury) DO NOTHING",
            (treasury, int(time.time())))
        ov.commit()
        # CLOSE the ops connection BEFORE the webhook enroll — the link + confirm are
        # durable now, and holding ov open while WebhookManager opens its own writer
        # caused "database is locked" on the enroll (treasury confirmed but not webhooked
        # → "✗ blind"). Decouple them: storage first, enroll on a clean slate.
        ov.close()

        # webhook the newly-confirmed treasury + sync the coverage table (same as promote).
        # RETRY ON LOCK: the enroll opens its own live-DB writer and can lose the race to the
        # lock storm — leaving a confirmed-but-blind treasury ("✗ blind"). The confirm+link
        # above are already durable; enroll_batch is idempotent, so retry it a few times with
        # backoff before reporting failure.
        webhooked = False; webhook_error = None
        try:
            import asyncio as _asyncio, time as _t
            from src.analysis.webhook_manager import WebhookManager, INFRA_ROLE
            for _attempt in range(3):
                try:
                    loop = _asyncio.new_event_loop()
                    mgr = WebhookManager(LIVE_DB_PATH)
                    loop.run_until_complete(mgr.enroll_batch([treasury], role=INFRA_ROLE,
                                                             notes="confirmed via subprov funder trace"))
                    loop.close()
                    webhooked = True
                    break
                except Exception as _enr_e:
                    webhook_error = str(_enr_e)
                    if "locked" in webhook_error.lower() and _attempt < 2:
                        _t.sleep(1.5 * (_attempt + 1))
                        continue
                    raise
            oc = _conn()
            try:
                oc.execute(
                    "INSERT INTO wt_confirmed_treasury_webhooks (treasury, source, enrolled_at, webhook_active) "
                    "VALUES (?, 'CONFIRMED_TREASURY', ?, 1) "
                    "ON CONFLICT(treasury) DO UPDATE SET webhook_active=1",
                    (treasury, int(time.time())))
                oc.commit()
            finally:
                oc.close()
        except Exception as exc:
            webhook_error = str(exc)

        return jsonify({"ok": True, "action": "set", "subprov": sp, "treasury": treasury,
                        "verified": verified, "onchain_funder": funder, "note": note,
                        "treasury_confirmed": True, "webhooked": webhooked,
                        "webhook_error": webhook_error})
    finally:
        try:
            ov.close()  # idempotent; already closed on the success path
        except Exception:
            pass


@ops_dashboard_bp.route("/api/ops-v2/intel/creator-modes")
def api_intel_creator_modes():
    """THE strategic metric: birth_to_launch distribution → STAGED vs INSTANT split.
    Determines whether the endgame is pre-launch PREDICTION (staged-dominant) or real-time
    ATTRIBUTION (instant-dominant). Reads wt_creator_birth_launch (populated by the
    measurement job, raw RPC). Returns the histogram + per-treasury mode breakdown."""
    ov = _conn()
    try:
        if not _table_exists(ov, "wt_creator_birth_launch"):
            return jsonify({"measured": 0, "histogram": {}, "by_treasury": [], "summary": {}})
        rows = ov.execute(
            "SELECT creator, treasury, birth_to_launch_s, creator_mode FROM wt_creator_birth_launch").fetchall()
        measured = [r for r in rows if r["birth_to_launch_s"] is not None]
        buckets = [("0-10s", 0, 10), ("10-60s", 10, 60), ("1-10m", 60, 600),
                   ("10-60m", 600, 3600), ("60m+", 3600, 10**12)]
        histogram = {lbl: sum(1 for r in measured if lo <= r["birth_to_launch_s"] < hi)
                     for lbl, lo, hi in buckets}
        instant = sum(1 for r in measured if r["creator_mode"] == "INSTANT")
        staged = sum(1 for r in measured if r["creator_mode"] == "STAGED")
        unknown = sum(1 for r in rows if r["birth_to_launch_s"] is None)
        tot = instant + staged
        gaps = sorted(r["birth_to_launch_s"] for r in measured)
        # per-treasury split — which treasuries run INSTANT vs STAGED
        by_t = {}
        for r in measured:
            t = r["treasury"] or "?"
            d = by_t.setdefault(t, {"treasury": t, "instant": 0, "staged": 0, "min_s": None, "max_s": None})
            d["instant" if r["creator_mode"] == "INSTANT" else "staged"] += 1
            g = r["birth_to_launch_s"]
            d["min_s"] = g if d["min_s"] is None else min(d["min_s"], g)
            d["max_s"] = g if d["max_s"] is None else max(d["max_s"], g)
        for d in by_t.values():
            n = d["instant"] + d["staged"]
            d["mode"] = "INSTANT" if d["instant"] > d["staged"] else ("STAGED" if d["staged"] > d["instant"] else "MIXED")
            d["n"] = n
        by_treasury = sorted(by_t.values(), key=lambda x: -x["n"])
        return jsonify({
            "measured": len(measured), "unknown": unknown,
            "histogram": histogram,
            "summary": {
                "instant": instant, "staged": staged,
                "instant_pct": (100 * instant // tot) if tot else 0,
                "staged_pct": (100 * staged // tot) if tot else 0,
                "median_s": gaps[len(gaps) // 2] if gaps else None,
                "min_s": gaps[0] if gaps else None, "max_s": gaps[-1] if gaps else None,
                "dominant": "INSTANT" if instant > staged else "STAGED",
                "endgame": "real-time ATTRIBUTION" if instant > staged else "pre-launch PREDICTION",
            },
            "by_treasury": by_treasury,
        })
    finally:
        ov.close()


@ops_dashboard_bp.route("/api/ops-v2/intel/kpi")
def api_intel_kpi():
    """The system KPIs (the new model's health): treasury auto-promotions, wrap-close strict
    arms, arm→fired conversion, false promotions (reverted), expired arms. Plus the
    seed/auto provenance split and the fingerprint decision ledger summary."""
    ov = _conn()
    try:
        def _count(sql, *a):
            try:
                return ov.execute(sql, a).fetchone()[0]
            except Exception:
                return 0
        seed = _count("SELECT COUNT(*) FROM wt_confirmed_treasuries WHERE provenance='CONFIRMED_SEED'")
        auto = _count("SELECT COUNT(*) FROM wt_confirmed_treasuries WHERE provenance='CONFIRMED_AUTO'")
        # fingerprint decisions ledger
        dec = {r[0]: r[1] for r in ov.execute(
            "SELECT decision, COUNT(*) FROM wt_treasury_fingerprint_decisions GROUP BY decision").fetchall()} \
            if _table_exists(ov, "wt_treasury_fingerprint_decisions") else {}
        # wrap-close arms + conversion
        wc_detected = _count("SELECT COUNT(*) FROM wt_wrap_close_candidates")
        wc_armed = _count("SELECT COUNT(*) FROM wt_wrap_close_candidates WHERE state='ARMED'")
        wc_fired = _count("SELECT COUNT(*) FROM wt_wrap_close_candidates WHERE state='FIRED'")
        wc_expired = _count("SELECT COUNT(*) FROM wt_wrap_close_candidates WHERE state='EXPIRED'")
        # strict arms (the armed table) — arm→fired conversion
        armed_total = _count("SELECT COUNT(*) FROM wt_ops_v2_armed WHERE arm_grade='STRICT'")
        armed_fired = _count("SELECT COUNT(*) FROM wt_ops_v2_armed WHERE arm_grade='STRICT' AND state='FIRED'")
        armed_expired = _count("SELECT COUNT(*) FROM wt_ops_v2_armed WHERE arm_grade='STRICT' AND state='EXPIRED'")
        reverted = dec.get("REVERTED", 0)
        conv = round(100 * armed_fired / armed_total) if armed_total else None
        return jsonify({
            "treasury": {
                "confirmed_seed": seed, "confirmed_auto": auto, "total": seed + auto,
                "auto_promotions": dec.get("CONFIRMED", 0),
                "near_misses": dec.get("NEAR_MISS", 0),
                "rejects": dec.get("REJECT", 0),
                "false_promotions_reverted": reverted,
            },
            "wrap_close": {
                "detected": wc_detected, "armed": wc_armed, "fired": wc_fired, "expired": wc_expired,
            },
            "strict_arms": {
                "total": armed_total, "fired": armed_fired, "expired": armed_expired,
                "arm_to_fired_pct": conv,
            },
            "decisions_ledger_total": sum(dec.values()),
        })
    finally:
        ov.close()


@ops_dashboard_bp.route("/api/ops-v2/intel/confirmed-treasuries")
def api_intel_confirmed_treasuries():
    """The CONFIRMED TREASURY BANK — authoritative live-watch set + the dashboard card.
    Source of truth = wt_confirmed_treasuries (NOT ops-graph roles)."""
    from src.core import treasury_bank
    ov = _conn(); live = _live_conn()
    try:
        rows = treasury_bank.confirmed_treasuries(ov)
        # enrich with live last-hit / last-fanout from webhook hits
        addrs = [r["treasury"] for r in rows]
        last_hit = {}
        if addrs:
            ph = ",".join("?" * len(addrs))
            for w, mx in live.execute(
                f"SELECT wallet_address, MAX(block_time) FROM wt_webhook_hits "
                f"WHERE wallet_address IN ({ph}) GROUP BY wallet_address", addrs).fetchall():
                last_hit[w] = mx
        for r in rows:
            r["last_hit"] = last_hit.get(r["treasury"]) or r.get("last_hit")
        webhooked = sum(1 for r in rows if r["webhooked"])
        # card aggregates
        all_hits = [r["last_hit"] for r in rows if r["last_hit"]]
        fired = [r for r in rows if r.get("last_fired_at")]
        return jsonify({
            "treasuries": rows,
            "card": {
                "confirmed": len(rows),
                "webhooked": webhooked,
                "last_hit": max(all_hits) if all_hits else None,
                "last_fanout": max([r["last_fanout"] for r in rows if r.get("last_fanout")] or [0]) or None,
                "last_strict_candidate": max([r["last_strict_candidate"] for r in rows if r.get("last_strict_candidate")] or [0]) or None,
                "last_fired_token": (sorted(fired, key=lambda x: x["last_fired_at"])[-1]["last_fired_token"] if fired else None),
                "last_fired_at": (max([r["last_fired_at"] for r in fired]) if fired else None),
            },
        })
    finally:
        ov.close(); live.close()


def _best_source_token(src_creators: dict) -> dict:
    """ZERO-RPC: for each candidate treasury → its source creator wallets → resolve each creator's
    migrated token + peak MC from token_analysis, and return the BEST (highest-peak) source token
    per candidate. {candidate: {mint, peak_mc, creator, migrated_at, count}}."""
    if not src_creators:
        return {}
    all_creators = set()
    for s in src_creators.values():
        all_creators |= s
    if not all_creators:
        return {}
    # creator → best (highest-peak) token (local token_analysis)
    creator_tok = {}
    try:
        live = _live_conn()
        try:
            ph = ",".join("?" * len(all_creators))
            for r in live.execute(
                f"SELECT COALESCE(pf_ws_creator, earliest_tx_creator) creator, mint, "
                f"market_cap_highest peak, migrated_at "
                f"FROM token_analysis WHERE COALESCE(pf_ws_creator, earliest_tx_creator) IN ({ph}) "
                f"AND market_cap_highest IS NOT NULL",
                list(all_creators)).fetchall():
                c = r["creator"]
                cur = creator_tok.get(c)
                if cur is None or (r["peak"] or 0) > (cur["peak_mc"] or 0):
                    creator_tok[c] = {"mint": r["mint"], "peak_mc": r["peak"],
                                      "creator": c, "migrated_at": r["migrated_at"]}
        finally:
            live.close()
    except Exception:
        return {}
    # per candidate: pick the highest-peak token among its source creators
    out = {}
    for cand, creators in src_creators.items():
        toks = [creator_tok[c] for c in creators if c in creator_tok]
        if not toks:
            continue
        best = max(toks, key=lambda x: x["peak_mc"] or 0)
        best = dict(best); best["count"] = len(toks)
        out[cand] = best
    return out


@ops_dashboard_bp.route("/api/ops-v2/intel/treasury-review")
def api_intel_treasury_review():
    """The treasury DISCOVERY candidate queue (review-only). Human promotes from here.
    Each candidate is enriched with `occurrences` = how many DISTINCT migrations traced
    their lineage back to this wallet (from the fingerprint-decision audit log). A repeat
    candidate (occurrences > 1) is far stronger evidence than a one-off — it has provisioned
    multiple migrated creators over time."""
    from src.core import treasury_bank
    ov = _conn()
    try:
        cands = treasury_bank.review_queue(ov)
        # occurrence count + the source CREATORS per candidate from the decision ledger
        # (source_migration is the CREATOR wallet the fingerprint walked back FROM).
        occ = {}
        src_creators = {}      # candidate wallet → set of source creator wallets
        try:
            for r in ov.execute(
                "SELECT wallet, COUNT(DISTINCT source_migration) n "
                "FROM wt_treasury_fingerprint_decisions WHERE source_migration IS NOT NULL "
                "GROUP BY wallet").fetchall():
                occ[r["wallet"]] = r["n"]
            for r in ov.execute(
                "SELECT DISTINCT wallet, source_migration FROM wt_treasury_fingerprint_decisions "
                "WHERE source_migration IS NOT NULL").fetchall():
                src_creators.setdefault(r["wallet"], set()).add(r["source_migration"])
        except Exception:
            pass
        # ZERO-RPC source-token enrichment: each candidate was discovered by walking back from a
        # token's CREATOR. Resolve creator → its token + peak MC (local token_analysis) and attach
        # the BEST (highest-peak) source token, so "near-miss treasury #34" reads as "treasury
        # behind a $980k launch". The peak magnitude is the human's strongest review signal.
        _best_token = _best_source_token(src_creators)
        for c in cands:
            t = c.get("treasury")
            c["occurrences"] = occ.get(t, 0)
            c["status"] = "PENDING_REVIEW"
            bt = _best_token.get(t)
            if bt:
                c["source_token"] = bt["mint"]; c["source_token_peak_mc"] = bt["peak_mc"]
                c["source_creator"] = bt["creator"]; c["source_token_migrated"] = bt["migrated_at"]
                c["source_token_count"] = bt["count"]
        # strongest-evidence first: most occurrences, then capital scale
        cands.sort(key=lambda c: (-(c.get("occurrences") or 0), -(c.get("out_sol") or 0)))
        # RECENTLY-DECIDED candidates (confirmed/rejected) — shown dimmed so the panel reflects
        # that discovery IS active even when the pending queue is empty (0 pending = worked, not dead).
        recent = []
        try:
            for r in ov.execute(
                "SELECT treasury, transfer_pct, out_sol, recipients, micro_pings, detected_via, "
                "status, reviewed_at, reviewed_by FROM wt_treasury_review "
                "WHERE status != 'PENDING_REVIEW' ORDER BY COALESCE(reviewed_at, detected_at) DESC LIMIT 12").fetchall():
                d = dict(r)
                d["occurrences"] = occ.get(r["treasury"], 0)
                bt = _best_token.get(r["treasury"])
                if bt:
                    d["source_token"] = bt["mint"]; d["source_token_peak_mc"] = bt["peak_mc"]
                recent.append(d)
        except Exception:
            pass
        return jsonify({"candidates": cands, "recent_decided": recent,
                        "decided_counts": _decided_counts(ov)})
    finally:
        ov.close()


def _decided_counts(ov):
    try:
        return {r[0]: r[1] for r in ov.execute(
            "SELECT status, COUNT(*) FROM wt_treasury_review GROUP BY status").fetchall()}
    except Exception:
        return {}


@ops_dashboard_bp.route("/api/ops-v2/intel/treasury-promote", methods=["POST"])
def api_intel_treasury_promote():
    """Human action: promote a reviewed candidate → confirmed set, then webhook it.
    POST {confirm:true, treasury:..., action:'promote'|'reject'}."""
    from src.core import treasury_bank
    import asyncio as _asyncio
    body = request.get_json(silent=True) or {}
    if body.get("confirm") is not True or not body.get("treasury"):
        return jsonify({"error": "confirmation + treasury required"}), 400
    t = body["treasury"]
    ov = _conn()
    try:
        if body.get("action") == "reject":
            return jsonify(treasury_bank.reject_candidate(ov, t))
        res = treasury_bank.promote_to_confirmed(ov, t)
        if not res.get("ok"):
            return jsonify(res), 400
    finally:
        ov.close()
    # webhook the newly-confirmed treasury (live-DB key)
    try:
        from src.analysis.webhook_manager import WebhookManager, INFRA_ROLE
        loop = _asyncio.new_event_loop()
        mgr = WebhookManager(LIVE_DB_PATH)
        loop.run_until_complete(mgr.enroll_batch([t], role=INFRA_ROLE, notes="promoted confirmed treasury"))
        loop.close()
        res["webhooked"] = True
        # SYNC the bank's coverage table — the Confirmed Treasury Bank panel reads
        # `webhooked` from wt_confirmed_treasury_webhooks (NOT wt_webhook_enrollments). Without
        # this, a promoted treasury shows "✗ blind" even though it IS webhooked.
        try:
            oc = _conn()
            oc.execute(
                "INSERT INTO wt_confirmed_treasury_webhooks (treasury, source, enrolled_at, webhook_active) "
                "VALUES (?, 'CONFIRMED_TREASURY', ?, 1) "
                "ON CONFLICT(treasury) DO UPDATE SET webhook_active=1",
                (t, int(time.time())))
            oc.commit(); oc.close()
        except Exception:
            pass
    except Exception as exc:
        res["webhooked"] = False; res["webhook_error"] = str(exc)
    return jsonify(res)


@ops_dashboard_bp.route("/api/ops-v2/intel/treasury-coverage")
def api_intel_treasury_coverage():
    """Treasury trigger coverage — the persistent webhook targets. Per treasury:
    op count, tx/24h (cached), webhooked?, last event, template events, GREEN/RED."""
    refresh = request.args.get("refresh") == "1"
    ov = _conn(); live = _live_conn()
    try:
        ov.execute("""CREATE TABLE IF NOT EXISTS wt_ops_v2_treasury_stats (
            treasury TEXT PRIMARY KEY, tx_24h INTEGER, computed_at INTEGER)""")
        wh_states = {}
        try:
            for r in live.execute("SELECT wallet_address, state FROM wt_webhook_enrollments WHERE is_active=1").fetchall():
                wh_states[r["wallet_address"]] = r["state"]
        except Exception:
            pass
        # AUTHORITATIVE source = wt_confirmed_treasuries (not ops-graph roles)
        treasuries = [r["treasury"] for r in ov.execute(
            "SELECT treasury FROM wt_confirmed_treasuries ORDER BY out_sol DESC").fetchall()]
        # wrap-close detections attributed to each confirmed treasury (the NEW signal)
        wc_by_treasury = {}
        try:
            for r in ov.execute(
                "SELECT lineage_source_treasury, COUNT(*) n, MAX(funded_at) last_at, "
                "SUM(state='ARMED') armed FROM wt_wrap_close_candidates GROUP BY lineage_source_treasury").fetchall():
                wc_by_treasury[r[0]] = {"detections": r[1], "last_at": r[2], "armed": r[3] or 0}
        except Exception:
            pass
        rows = []
        for t in treasuries:
            opcount = ov.execute("SELECT COUNT(DISTINCT operation_uuid) FROM wt_ops_v2_wallets WHERE wallet=?", (t,)).fetchone()[0]
            wc = wc_by_treasury.get(t, {})
            # confirmed-treasury stats: wrap-close detections are the authoritative signal
            tmpl_events = wc.get("detections", 0)          # wrap-close creator detections
            launches = wc.get("armed", 0)                   # armed (strict) from this treasury
            last_ev = wc.get("last_at")
            operation = (ov.execute("SELECT operation_uuid FROM wt_ops_v2_wallets WHERE wallet=? AND role='TREASURY' LIMIT 1", (t,)).fetchone() or [None])[0]
            wh = wh_states.get(t)
            tx24 = _treasury_tx_24h(t, ov) if refresh else (
                ov.execute("SELECT tx_24h FROM wt_ops_v2_treasury_stats WHERE treasury=?", (t,)).fetchone() or [None])[0]
            rows.append({
                "treasury": t, "operation": operation[:8] if operation else None, "operation_uuid": operation,
                "operation_count": opcount, "launches": launches, "tx_24h": tx24,
                "webhooked": wh is not None, "webhook_state": wh,
                "last_event": last_ev, "template_events": tmpl_events,
                "status": "MONITORED" if wh else "BLIND",
                # high-value = repeat producer AND low-volume (cheap, clean webhook target)
                "high_value": launches >= 3 and isinstance(tx24, int) and tx24 < 500 if tx24 is not None else launches >= 3,
            })
        # sort: unwebhooked high-value first, then by launches/template activity
        rows.sort(key=lambda x: (x["webhooked"], -x["launches"], -(x["template_events"] or 0)))
        return jsonify({
            "treasuries": rows,
            "covered": sum(1 for r in rows if r["webhooked"]),
            "missing": sum(1 for r in rows if not r["webhooked"]),
            "total": len(rows),
        })
    finally:
        ov.close(); live.close()


@ops_dashboard_bp.route("/api/ops-v2/intel/detection-health")
def api_intel_detection_health():
    """Real-time detection health — the listener is the biggest risk right now."""
    now = int(time.time())
    live = _live_conn()
    try:
        last = (live.execute("SELECT MAX(block_time) FROM wt_webhook_hits").fetchone() or [None])[0]
        hits24 = live.execute("SELECT COUNT(*) FROM wt_webhook_hits WHERE block_time > strftime('%s','now')-86400").fetchone()[0]
        tmpl24 = live.execute(
            "SELECT COUNT(*) FROM wt_webhook_hits WHERE block_time > strftime('%s','now')-86400 "
            "AND CAST(ROUND(amount_sol*1e9) AS INT)%1000000=39280").fetchone()[0]
        age = (now - last) if last else None
        listener = "DOWN" if last is None else ("LIVE" if age < 3600 else "STALE")
        return jsonify({
            "listener_status": listener, "last_webhook_hit": last, "last_hit_age_s": age,
            "hits_24h": hits24, "template_hits_24h": tmpl24,
            "rpc_follow_success_pct": None, "failed_follows": None,
            "warning": "NO_WEBHOOK_EVENTS" if (last is None or (age and age > 3600)) else None,
        })
    finally:
        live.close()


@ops_dashboard_bp.route("/api/ops-v2/intel/status")
def api_intel_status():
    """Header status: scheduler LIVE/DOWN, webhook listener LIVE/DOWN/STALE,
    last webhook event, last scheduler run."""
    import os as _os
    now = int(time.time())
    out = {"scheduler_running": False, "webhook_listener": "DOWN",
           "last_webhook_event": None, "last_scheduler_run": None, "candidate_webhook_ok": None}
    # scheduler liveness via lock PID (reuse the scheduler widget logic)
    c = _conn()
    try:
        if _table_exists(c, "wt_ops_v2_runs"):
            r = c.execute("SELECT job_type, started_at FROM wt_ops_v2_runs ORDER BY started_at DESC LIMIT 1").fetchone()
            out["last_scheduler_run"] = dict(r) if r else None
    finally:
        c.close()
    lock = _os.environ.get("OPS_SCHEDULER_LOCK", _os.path.join(_REPO_ROOT, "operation_scheduler.lock"))
    if _os.path.exists(lock):
        try:
            _os.kill(int(open(lock).read().strip() or "0"), 0)
            out["scheduler_running"] = True
        except (OSError, ValueError):
            pass
    # webhook listener freshness
    live = _live_conn()
    try:
        r = live.execute("SELECT MAX(block_time) FROM wt_webhook_hits").fetchone()
        last = r[0] if r else None
        out["last_webhook_event"] = last
        if last:
            age = now - last
            out["webhook_listener"] = "LIVE" if age < WEBHOOK_STALE_S else "STALE"
            out["webhook_event_age_s"] = age
    finally:
        live.close()
    return jsonify(out)


@ops_dashboard_bp.route("/api/ops-v2/intel/coverage")
def api_intel_coverage():
    """Headline coverage summary + per-operation coverage table. The page's core:
    creator-candidate coverage is the headline metric."""
    ov = _conn()
    live = _live_conn()
    try:
        wh = _webhooked_set(live)
        hits24 = _webhook_hits_24h(live)

        # families + lifecycle
        fam = {r["family_uuid"]: r["family_label"] for r in
               ov.execute("SELECT family_uuid, family_label FROM wt_ops_v2_families").fetchall()}
        life = {r["operation_uuid"]: (r["state"], r["last_activity"]) for r in
                ov.execute("SELECT operation_uuid, state, last_activity FROM wt_operation_lifecycle").fetchall()} \
                if _table_exists(ov, "wt_operation_lifecycle") else {}

        # per-operation wallets by role + candidates
        ops = []
        all_cand = all_cand_cov = 0
        tot_pt = cov_pt = tot_co = cov_co = tot_tr = cov_tr = 0
        weak_ops = 0
        # AUTHORITATIVE: only operations rooted on a CONFIRMED treasury (the live bank).
        # The ops-graph holds many operations whose roots are contaminated/unconfirmed
        # trading wallets — those are not part of the confirmed-treasury watch set.
        for o in ov.execute(
                "SELECT operation_uuid, treasury_root, family_uuid FROM wt_ops_v2 "
                "WHERE treasury_root IN (SELECT treasury FROM wt_confirmed_treasuries)").fetchall():
            uuid = o["operation_uuid"]
            # role wallets
            roles = {"TREASURY": [], "COLLECTOR": [], "PASS_THROUGH": []}
            for w in ov.execute("SELECT wallet, role FROM wt_ops_v2_wallets WHERE operation_uuid=?", (uuid,)).fetchall():
                if w["role"] in roles:
                    roles[w["role"]].append(w["wallet"])
            # template-funded candidates only (real pre-launch creators) — infra excluded
            cands = [r["wallet"] for r in ov.execute(
                "SELECT DISTINCT wallet FROM wt_operation_candidates WHERE operation_uuid=? "
                "AND template_base IS NOT NULL AND template_base BETWEEN 0.05 AND 6.0", (uuid,)).fetchall()] \
                if _table_exists(ov, "wt_operation_candidates") else []

            def cov(lst):
                return (sum(1 for w in lst if w in wh), len(lst))
            cc, ct = cov(cands)
            pc, pt = cov(roles["PASS_THROUGH"])
            oc, ot = cov(roles["COLLECTOR"])
            tc, tt = cov(roles["TREASURY"])
            cand_cov = (cc / ct) if ct else 1.0
            ev24 = sum(hits24.get(w, 0) for grp in roles.values() for w in grp) + sum(hits24.get(w, 0) for w in cands)
            launches = ov.execute(
                "SELECT COUNT(*) FROM wt_ops_v2_creators WHERE operation_uuid=? AND migration_time IS NOT NULL", (uuid,)).fetchone()[0]
            state = (life.get(uuid) or ("DISCOVERED", None))[0]
            status = _coverage_status(cand_cov, tc > 0, ct)
            if status in ("WEAK", "UNCOVERED"):
                weak_ops += 1
            all_cand += ct; all_cand_cov += cc
            tot_pt += pt; cov_pt += pc; tot_co += ot; cov_co += oc; tot_tr += tt; cov_tr += tc
            ops.append({
                "operation_uuid": uuid, "treasury_root": o["treasury_root"],
                "family": fam.get(o["family_uuid"]) or "—", "state": state,
                "candidates": ct, "candidate_cov": cc, "candidate_cov_pct": round(cand_cov*100),
                "passthrough_cov_pct": round((pc/pt*100) if pt else 100),
                "collector_cov_pct": round((oc/ot*100) if ot else 100),
                "treasury_webhooked": tc > 0 and tt > 0,
                "events_24h": ev24, "launches": launches,
                "coverage_status": status,
                "last_activity": (life.get(uuid) or (None, None))[1],
            })
        # sort: provisioning/creators_seen first → lowest candidate coverage → recent
        _pri = {"PROVISIONING": 0, "CREATORS_SEEN": 1, "REACTIVATED": 2, "ACTIVE": 3, "DISCOVERED": 4, "MIGRATED": 5, "DORMANT": 6}
        ops.sort(key=lambda x: (_pri.get(x["state"], 9), x["candidate_cov_pct"], -(x["last_activity"] or 0)))

        # headline summary
        uncovered_hp = ov.execute(
            "SELECT COUNT(DISTINCT cc.wallet) FROM wt_operation_candidates cc "
            "JOIN wt_operation_lifecycle l ON l.operation_uuid=cc.operation_uuid "
            "WHERE l.state IN ('PROVISIONING','CREATORS_SEEN','REACTIVATED')").fetchone()[0] \
            if _table_exists(ov, "wt_operation_candidates") else 0
        # subtract those already covered
        hp_wallets = {r[0] for r in ov.execute(
            "SELECT DISTINCT cc.wallet FROM wt_operation_candidates cc "
            "JOIN wt_operation_lifecycle l ON l.operation_uuid=cc.operation_uuid "
            "WHERE l.state IN ('PROVISIONING','CREATORS_SEEN','REACTIVATED') "
            "AND cc.template_base IS NOT NULL AND cc.template_base BETWEEN 0.05 AND 6.0").fetchall()} \
            if _table_exists(ov, "wt_operation_candidates") else set()
        uncovered_hp = len(hp_wallets - wh)
        events24 = sum(hits24.values())
        launches24 = live.execute(
            "SELECT COUNT(*) FROM wt_webhook_hits WHERE tx_type LIKE '%CREATE%' AND block_time > strftime('%s','now')-86400").fetchone()[0] \
            if True else 0

        summary = {
            "candidates_covered": all_cand_cov, "candidates_total": all_cand,
            "candidate_cov_pct": round((all_cand_cov/all_cand*100) if all_cand else 0),
            "uncovered_high_priority": uncovered_hp,
            "webhooked_passthroughs": cov_pt, "passthroughs_total": tot_pt,
            "webhooked_collectors": cov_co, "collectors_total": tot_co,
            "webhooked_treasuries": cov_tr, "treasuries_total": tot_tr,
            "weak_coverage_ops": weak_ops,
            "events_24h": events24, "launches_24h": launches24,
        }
        return jsonify({"summary": summary, "operations": ops})
    finally:
        ov.close(); live.close()


@ops_dashboard_bp.route("/api/ops-v2/intel/enrollment-queue")
def api_intel_enrollment_queue():
    """DEPRECATED for treasuries — treasuries now AUTO-enroll on the 3-signal fingerprint
    (post-migration → wt_confirmed_treasuries → auto-webhook), no human queue. This endpoint
    now returns the AUTO-PIPELINE STATUS (confirmed/review/recent) for transparency, not
    manual 'next to webhook' suggestions. Creators are armed automatically by the wrap-close
    detector. Nothing here requires a human action except optional review of near-misses."""
    from src.core import treasury_bank
    ov = _conn()
    live = _live_conn()
    try:
        treasury_bank.ensure_schema(ov)
        confirmed = treasury_bank.confirmed_treasuries(ov)
        review = treasury_bank.review_queue(ov)
        return jsonify({
            "mode": "AUTO",
            "note": "Treasuries auto-enroll on fingerprint. No manual queue. Review = near-misses only.",
            "confirmed_count": len(confirmed),
            "webhooked_count": sum(1 for t in confirmed if t.get("webhooked")),
            "review_candidates": review,        # near-misses (passed 2 of 3 signals)
            "queue": [],                         # empty — nothing requires manual enrolment
        })
    finally:
        ov.close(); live.close()


def _legacy_enrollment_queue_DEPRECATED():
    """The old manual priority queue — kept for reference, no longer routed."""
    ov = _conn()
    live = _live_conn()
    try:
        wh = _webhooked_set(live)
        hits = _webhook_hits_24h(live)
        life = {r["operation_uuid"]: r["state"] for r in
                ov.execute("SELECT operation_uuid, state FROM wt_operation_lifecycle").fetchall()} \
                if _table_exists(ov, "wt_operation_lifecycle") else {}
        fam = {r["family_uuid"]: r["family_label"] for r in
               ov.execute("SELECT family_uuid, family_label FROM wt_ops_v2_families").fetchall()}
        op_fam = {r["operation_uuid"]: r["family_uuid"] for r in
                  ov.execute("SELECT operation_uuid, family_uuid FROM wt_ops_v2").fetchall()}
        migrated = {r[0] for r in ov.execute(
            "SELECT creator_wallet FROM wt_ops_v2_creators WHERE migration_time IS NOT NULL").fetchall()}
        rows = []

        # Tier 0 — PRE-LAUNCH CREATORS (template-funded, not migrated, ~60-min window).
        # The highest-priority webhook target. Surfaced above all other tiers.
        plc_wallets = set()
        for plc in _pre_launch_creators(ov, live, limit=200):
            if plc["webhooked"]:
                continue
            plc_wallets.add(plc["wallet"])
            rows.append({"tier": 0, "wallet": plc["wallet"], "role": "PRE_LAUNCH_CREATOR",
                         "operation": plc["operation"], "operation_uuid": plc["operation_uuid"],
                         "family": plc["family"] or "—",
                         "reason": f"template {plc['template']} · funded {plc['minutes_since_funding']}m ago · "
                                   f"launch window ~{plc['expected_launch_window_min']}m",
                         "last_seen": plc["funded_at"], "already_webhooked": False})

        # Tier 1/2 — TEMPLATE-FUNDED candidate creators only (the real pre-launch
        # leads). Infrastructure fan-out wallets (large/round amounts, no template
        # tail) are NOT enrolment targets and are excluded — they flooded the queue
        # with thousands of non-creators. Tier 1 if op is live, Tier 2 otherwise.
        if _table_exists(ov, "wt_operation_candidates"):
            for r in ov.execute(
                "SELECT wallet, operation_uuid, first_seen, template_base, confidence FROM wt_operation_candidates "
                "WHERE template_base IS NOT NULL AND template_base BETWEEN 0.05 AND 6.0 "
                "ORDER BY first_seen DESC").fetchall():
                w = r["wallet"]
                if w in wh or w in migrated or w in plc_wallets:
                    continue
                age = int(time.time()) - (r["first_seen"] or 0)
                # STALE GUARD: a "pre-launch creator" is only meaningful inside the launch
                # window. Past ~2× the lead time it has either already launched (and we
                # missed it) or it's a dead lead — either way it's NOT a live webhook target.
                # (DxBXmA at 4.9h was shown as PRE_LAUNCH long after its 58-min window.)
                if age > LEAD_TIME_MIN * 60 * 2:
                    continue
                st = life.get(r["operation_uuid"], "DISCOVERED")
                live_op = st in ("PROVISIONING", "CREATORS_SEEN", "REACTIVATED")
                tier = 1 if live_op else 2
                reason = (f"template creator · op {st}" if live_op else "template-funded creator")
                reason += f" · template {r['template_base']}"
                rows.append({"tier": tier, "wallet": w, "role": "PRE_LAUNCH_CREATOR",
                             "operation": r["operation_uuid"][:8], "operation_uuid": r["operation_uuid"],
                             "family": fam.get(op_fam.get(r["operation_uuid"])) or "—",
                             "reason": reason, "last_seen": r["first_seen"],
                             "already_webhooked": False})

        # Tier 3 — pass-throughs that produced candidates (in live ops)
        seen_wallets = {r["wallet"] for r in rows}
        for r in ov.execute(
            "SELECT DISTINCT w.wallet, w.operation_uuid FROM wt_ops_v2_wallets w WHERE w.role='PASS_THROUGH'").fetchall():
            w = r["wallet"]
            if w in wh or w in seen_wallets:
                continue
            st = life.get(r["operation_uuid"], "DISCOVERED")
            if st not in ("PROVISIONING", "CREATORS_SEEN", "REACTIVATED"):
                continue
            rows.append({"tier": 3, "wallet": w, "role": "PASS_THROUGH",
                         "operation": r["operation_uuid"][:8], "operation_uuid": r["operation_uuid"],
                         "family": fam.get(op_fam.get(r["operation_uuid"])) or "—",
                         "reason": f"pass-through in {st} operation", "last_seen": None,
                         "already_webhooked": False})

        # Tier 4 — collectors in PROVISIONING; Tier 5 — treasuries w/ recent activity
        for role, tier, label in (("COLLECTOR", 4, "collector"), ("TREASURY", 5, "treasury")):
            for r in ov.execute(
                "SELECT DISTINCT wallet, operation_uuid FROM wt_ops_v2_wallets WHERE role=?", (role,)).fetchall():
                w = r["wallet"]
                if w in wh or w in {x["wallet"] for x in rows}:
                    continue
                st = life.get(r["operation_uuid"], "DISCOVERED")
                if role == "COLLECTOR" and st != "PROVISIONING":
                    continue
                rows.append({"tier": tier, "wallet": w, "role": role,
                             "operation": r["operation_uuid"][:8], "operation_uuid": r["operation_uuid"],
                             "family": fam.get(op_fam.get(r["operation_uuid"])) or "—",
                             "reason": f"{label} · op {st}", "last_seen": None,
                             "already_webhooked": False})

        rows.sort(key=lambda x: (x["tier"], -(x["last_seen"] or 0)))
        return jsonify({"queue": rows[:300],
                        "tier0_count": sum(1 for r in rows if r["tier"] == 0),
                        "tier1_count": sum(1 for r in rows if r["tier"] == 1),
                        "total_uncovered": len(rows)})
    finally:
        ov.close(); live.close()


@ops_dashboard_bp.route("/api/ops-v2/intel/ops-overview")
def api_intel_ops_overview():
    """Full ops-overview shape for the restored operational-intelligence page, but
    sourced from wt_ops_v2 (operations as 'campaigns'/'provisioners', operation
    activity as 'events', candidates as the creator pipeline). Drop-in compatible
    with that page's render functions."""
    ov = _conn()
    live = _live_conn()
    now = int(time.time())
    try:
        st, detail, states, cand24 = _v2_op_state()
        # map v2 banner state -> the page's op_state enum
        OPSTATE = {"PROVISIONING": "ESCALATING", "CREATORS_SEEN": "ACTIVE", "ACTIVE": "ACTIVE",
                   "FORMING": "FORMING", "DORMANT": "DORMANT", "QUIET": "QUIET"}
        op_state = OPSTATE.get(st, "FORMING")

        life = {r["operation_uuid"]: (r["state"], r["last_activity"]) for r in
                ov.execute("SELECT operation_uuid, state, last_activity FROM wt_operation_lifecycle").fetchall()} \
                if _table_exists(ov, "wt_operation_lifecycle") else {}
        CAMP_STATE = {"PROVISIONING": "ACTIVE", "CREATORS_SEEN": "ESCALATING", "REACTIVATED": "ESCALATING",
                      "ACTIVE": "ACTIVE", "MIGRATED": "DORMANT", "DORMANT": "DORMANT", "DISCOVERED": "FORMING"}

        # campaigns = operations
        campaigns, provisioners = [], []
        for o in ov.execute("SELECT operation_uuid, treasury_root, first_seen FROM wt_ops_v2").fetchall():
            uuid = o["operation_uuid"]
            s, la = life.get(uuid, ("DISCOVERED", None))
            creators = ov.execute("SELECT COUNT(*) FROM wt_ops_v2_creators WHERE operation_uuid=?", (uuid,)).fetchone()[0]
            cands = ov.execute("SELECT COUNT(*) FROM wt_operation_candidates WHERE operation_uuid=?", (uuid,)).fetchone()[0] if _table_exists(ov, "wt_operation_candidates") else 0
            evs = ov.execute("SELECT COUNT(*) FROM wt_operation_activity WHERE operation_uuid=?", (uuid,)).fetchone()[0] if _table_exists(ov, "wt_operation_activity") else 0
            age = (now - la) if la else None
            campaigns.append({"id": uuid[:8], "state": CAMP_STATE.get(s, "FORMING"),
                              "age_s": age, "creator_count": creators, "candidate_count": cands,
                              "event_count": evs, "total_sol_provisioned": 0})
            provisioners.append({"address": o["treasury_root"], "age_s": age, "role": "TREASURY"})

        # candidates pipeline (counts) — enrich with webhook coverage
        wh = _webhooked_set(live)
        all_cands = [r["wallet"] for r in ov.execute("SELECT DISTINCT wallet FROM wt_operation_candidates").fetchall()] \
            if _table_exists(ov, "wt_operation_candidates") else []
        migrated = ov.execute("SELECT COUNT(*) FROM wt_ops_v2_creators WHERE migration_time IS NOT NULL").fetchone()[0]
        candidates = {
            "enrolled": sum(1 for w in all_cands if w in wh),
            "scored_": len(all_cands),
            "high_conf_": ov.execute("SELECT COUNT(*) FROM wt_operation_candidates WHERE template_base IS NOT NULL").fetchone()[0] if _table_exists(ov, "wt_operation_candidates") else 0,
            "fee_touches_": migrated,
            "last_hit_age_s": None,
            "migrated": migrated,
        }

        # events = operation activity (newest first) in the page's event shape
        events = []
        if _table_exists(ov, "wt_operation_activity"):
            for r in ov.execute(
                "SELECT wallet, counterparty, event_type, amount, block_time, operation_uuid "
                "FROM wt_operation_activity ORDER BY block_time DESC LIMIT 60").fetchall():
                sem = {"NEW_CREATOR_CANDIDATE": "CREATOR_CANDIDATE", "NEW_CHILD": "TREASURY_FANOUT",
                       "FUNDING": "FUNDING"}.get(r["event_type"], r["event_type"])
                events.append({"ts": r["block_time"], "age_s": (now - r["block_time"]) if r["block_time"] else None,
                               "wallet": r["counterparty"], "raw_type": r["event_type"], "semantic": sem,
                               "mint": None, "payload": {"amount_sol": r["amount"], "operation": r["operation_uuid"][:8],
                                                          "href": f"/ops/operation/{r['operation_uuid']}"}})

        # treasury block
        last_op_act = max((p["age_s"] for p in provisioners if p["age_s"] is not None), default=None)
        treasury = {"provisioners": provisioners, "age_s": (min((p["age_s"] for p in provisioners if p["age_s"] is not None), default=None)),
                    "signals_": len(events), "sol_": 0, "state": op_state}

        extraction = {"sweeps": migrated, "relays": [],
                      "epoch": states.get("MIGRATED", 0)} if states.get("MIGRATED", 0) else {}

        return jsonify({
            "ok": True, "computed_at": now, "op_state": op_state, "op_detail": detail,
            "campaigns": campaigns, "candidates": candidates, "events": events,
            "treasury": treasury, "extraction": extraction,
            "infra_wallets": {"collectors": ov.execute("SELECT COUNT(*) FROM wt_ops_v2_wallets WHERE role='COLLECTOR'").fetchone()[0],
                              "passthroughs": ov.execute("SELECT COUNT(*) FROM wt_ops_v2_wallets WHERE role='PASS_THROUGH'").fetchone()[0]},
            "interceptor": {},
        })
    finally:
        ov.close(); live.close()


@ops_dashboard_bp.route("/api/ops-v2/intel/webhook-events")
def api_intel_webhook_events():
    """Webhook hits enriched with operation context (operation_uuid, family, wallet role,
    candidate status, related mint). Marks LAUNCH_DETECTED on a candidate CREATE."""
    ov = _conn()
    live = _live_conn()
    try:
        limit = int(request.args.get("limit", 80))
        # SCOPE the feed to the CURRENT watch set: confirmed treasuries + currently-armed
        # creators. Old hits from de-webhooked wallets (e.g. sub-provs like 8sUKF9 that were
        # webhooked before the treasury-only rule) must not appear — they're stale noise.
        watch = {r[0] for r in ov.execute("SELECT treasury FROM wt_confirmed_treasuries").fetchall()}
        try:
            watch |= {r[0] for r in ov.execute(
                "SELECT creator_wallet FROM wt_ops_v2_armed WHERE state='ARMED'").fetchall()}
        except Exception:
            pass
        if watch:
            ph = ",".join("?" * len(watch))
            hits = live.execute(
                f"SELECT wallet_address, counterparty, tx_type, amount_sol, block_time, tx_signature, direction, source "
                f"FROM wt_webhook_hits WHERE wallet_address IN ({ph}) "
                f"ORDER BY block_time DESC LIMIT ?", list(watch) + [limit]).fetchall()
        else:
            hits = []
        # ── treasury-outbound CLASSIFICATION: BEHAVIORAL OUTCOME (not funding-frequency) ──
        # A funding transfer to a subprov is INDISTINGUISHABLE at funding time from a refill
        # vs a launch-trigger — both are "capital to a recipient we've funded before". The
        # ONLY thing that knows the difference is what the recipient DOES with the capital
        # (wrap-close → CREATE = launch; fan-out → SWAP = buy-swarm; nothing = dormant). So we
        # label by the cascade's downstream OUTCOME for that recipient, derived live from the
        # cascade state tables (zero RPC, always current — the cascade updates these as it
        # observes CREATE/SWAP/expiry, and the feed re-reads them every refresh). The old
        # INITIAL/TOP_UP timing label was misleading (TOP_UP looked uninteresting even when the
        # recipient was an active launch-producer). SIGNAL (sub-20 SOL dust probe) is kept.
        SIGNAL_MAX_SOL = 20.0
        CAPITAL_MIN_SOL = 50.0
        launched_subprovs = set()    # recipient → produced a CREATE (LAUNCHED)
        buyswarm_subprovs = set()    # recipient → fan-out wallets SWAPped (BUY_SWARM)
        watching_subprovs = set()    # recipient → cascade actively watching (outcome pending)
        launched_subprov_first_ts = {}   # subprov → earliest CREATE time it produced
        already_launched_subprovs = set()  # subprov has ALREADY produced a creator (single-use → spent)
        try:
            launched_subprovs = {r[0] for r in ov.execute(
                "SELECT subprov_wallet FROM wt_watchtower_launches WHERE subprov_wallet IS NOT NULL").fetchall()}
            # earliest launch time per subprov — splits LAUNCHED (the funding that CAUSED the
            # launch) from TOP_UP (capital to a subprov that ALREADY launched). Subprovs are
            # single-use (30/31 produce exactly 1 token), so post-launch capital = refill.
            for r in ov.execute(
                "SELECT subprov_wallet, MIN(create_time) ct FROM wt_watchtower_launches "
                "WHERE subprov_wallet IS NOT NULL AND create_time IS NOT NULL "
                "GROUP BY subprov_wallet").fetchall():
                launched_subprov_first_ts[r[0]] = r[1]
            # AUTHORITATIVE has-launched signal: wt_discovered_subprovs.creator_count ≥ 1 is set by
            # the discovery job for EVERY subprov that produced a creator — independent of whether
            # the cascade captured the real-time CREATE (the launches ledger is incomplete). A
            # single-use subprov that already has a creator will NOT relaunch, so any capital-sized
            # transfer to it is a TOP_UP, not a new launch-trigger — see single-token-creator-filter.
            already_launched_subprovs = {r[0] for r in ov.execute(
                "SELECT subprov FROM wt_discovered_subprovs WHERE creator_count >= 1").fetchall()}
            # a subprov whose wrap-close children are BUY_SWARM (and none fired) = buy-swarm op
            buyswarm_subprovs = {r[0] for r in ov.execute(
                "SELECT DISTINCT subprov_wallet FROM wt_candidate_websocket_watches "
                "WHERE state='BUY_SWARM' AND subprov_wallet IS NOT NULL").fetchall()}
            # also fold in the wrap-close-candidate BUY_SWARM verdicts (broader source)
            buyswarm_subprovs |= {r[0] for r in ov.execute(
                "SELECT DISTINCT subprov_wallet FROM wt_wrap_close_candidates "
                "WHERE state='BUY_SWARM' AND subprov_wallet IS NOT NULL").fetchall()}
            watching_subprovs = {r[0] for r in ov.execute(
                "SELECT subprov_wallet FROM wt_active_subprov_sessions WHERE state='ACTIVE'").fetchall()}
            watching_subprovs |= {r[0] for r in ov.execute(
                "SELECT DISTINCT subprov_wallet FROM wt_candidate_websocket_watches "
                "WHERE state='WATCHING' AND subprov_wallet IS NOT NULL").fetchall()}
        except Exception:
            pass

        def classify_outbound(treasury, recipient, amount, block_time):
            """BEHAVIORAL OUTCOME of a treasury outbound: SIGNAL (dust probe) | LAUNCHED |
            TOP_UP | BUY_SWARM | WATCHING | DORMANT | None. Derived from cascade state + the
            single-use-subprov invariant (zero RPC)."""
            a = amount or 0
            if a <= 0 or not recipient:
                return None
            if a < SIGNAL_MAX_SOL:
                return "SIGNAL"                   # sub-20 SOL dust probe (signaller)
            if a < CAPITAL_MIN_SOL:
                return None                       # mid-band, unclassified
            # capital-sized → label by the recipient's downstream OUTCOME (cascade state).
            if recipient in launched_subprovs:
                # SINGLE-USE INVARIANT: subprovs launch exactly once (30/31 in our data).
                # Distinguish the funding that CAUSED the launch from a later refill:
                #   • this transfer is AT/BEFORE the launch's CREATE → it's the provisioning → LAUNCHED
                #   • this transfer is AFTER the launch → the subprov already fired → TOP_UP
                lt = launched_subprov_first_ts.get(recipient)
                if lt is not None and block_time is not None and block_time > lt + 60:
                    return "TOP_UP"
                return "LAUNCHED"
            if recipient in already_launched_subprovs:
                # known to have launched (discovery job), but no precise CREATE time to compare.
                # Single-use → it won't relaunch → this capital is a refill.
                return "TOP_UP"
            if recipient in buyswarm_subprovs:
                return "BUY_SWARM"
            if recipient in watching_subprovs:
                return "WATCHING"
            return "DORMANT"                      # funded, but no wrap-close/CREATE/swarm observed

        # build wallet -> (operation_uuid, role) maps.
        # PRECEDENCE: confirmed treasury (authoritative) > wrap-close subprov/creator >
        # ops-graph role > stale candidate. The confirmed bank is the source of truth, so
        # a confirmed treasury is labeled CONFIRMED_TREASURY regardless of any ops-graph row.
        wallet_op = {}
        for r in ov.execute("SELECT wallet, operation_uuid, role FROM wt_ops_v2_wallets").fetchall():
            wallet_op[r["wallet"]] = (r["operation_uuid"], r["role"])
        if _table_exists(ov, "wt_operation_candidates"):
            for r in ov.execute("SELECT DISTINCT wallet, operation_uuid FROM wt_operation_candidates").fetchall():
                wallet_op.setdefault(r["wallet"], (r["operation_uuid"], "CANDIDATE"))
        # wrap-close: subprov + creator roles (the validated WATCHTOWER mechanism)
        try:
            for r in ov.execute("SELECT subprov_wallet, creator FROM wt_wrap_close_candidates").fetchall():
                if r[0]:
                    wallet_op[r[0]] = (None, "SUB_PROV")
                if r[1]:
                    wallet_op[r[1]] = (None, "WRAP_CLOSE_CREATOR")
        except Exception:
            pass
        # confirmed treasuries — HIGHEST precedence, overrides any stale ops-graph label
        try:
            for r in ov.execute("SELECT treasury FROM wt_confirmed_treasuries").fetchall():
                wallet_op[r[0]] = (None, "CONFIRMED_TREASURY")
        except Exception:
            pass
        op_fam = {r["operation_uuid"]: r["family_uuid"] for r in ov.execute("SELECT operation_uuid, family_uuid FROM wt_ops_v2").fetchall()}
        fam = {r["family_uuid"]: r["family_label"] for r in ov.execute("SELECT family_uuid, family_label FROM wt_ops_v2_families").fetchall()}
        migrated = {r[0] for r in ov.execute("SELECT creator_wallet FROM wt_ops_v2_creators WHERE migration_time IS NOT NULL").fetchall()}

        def etype(tx):
            tx = (tx or "").upper()
            if "CREATE" in tx: return "PUMP_CREATE"
            if "TRANSFER" in tx: return "FUNDING_EDGE"
            if "SWAP" in tx: return "SWEEP"
            return "UNKNOWN"

        events = []
        for h in hits:
            w = h["wallet_address"]
            op, role = wallet_op.get(w, (None, None))
            et = etype(h["tx_type"])
            is_cand = role == "CANDIDATE"
            launch = is_cand and et == "PUMP_CREATE"
            _dir = (h["direction"] if "direction" in h.keys() else None)
            # classify treasury OUTBOUNDS: SIGNAL / INITIAL / TOP_UP
            funding_type = None
            if _dir == "outbound" and role == "CONFIRMED_TREASURY":
                funding_type = classify_outbound(w, h["counterparty"], h["amount_sol"], h["block_time"])
            _src = (h["source"] if "source" in h.keys() else None)
            _via = "WS" if _src == "treasury_ws" else ("webhook" if _src else None)
            events.append({
                "wallet": w, "counterparty": h["counterparty"], "type": et,
                "amount": h["amount_sol"], "ts": h["block_time"], "signature": h["tx_signature"],
                "direction": _dir, "funding_type": funding_type, "via": _via,
                "operation_uuid": op, "operation": op[:8] if op else None,
                "family": fam.get(op_fam.get(op)) if op else None,
                "role": role, "candidate": is_cand,
                "candidate_status": ("MIGRATED" if w in migrated else "PENDING") if is_cand else None,
                "launch_detected": launch,
                "from_operation": op is not None,
            })
        # PRE_LAUNCH_CREATOR_DETECTED — synthetic high-priority events from the
        # template-funding signal (the ~60-min pre-launch window). Prepended so they
        # surface even while the webhook listener is quiet.
        for plc in _pre_launch_creators(ov, live, limit=40):
            events.append({
                "wallet": plc["wallet"], "counterparty": plc["funded_by"],
                "type": "PRE_LAUNCH_CREATOR_DETECTED", "amount": None,
                "ts": plc["funded_at"], "signature": None,
                "operation_uuid": plc["operation_uuid"], "operation": plc["operation"],
                "family": plc["family"], "role": "PRE_LAUNCH_CREATOR", "candidate": True,
                "candidate_status": "PENDING", "launch_detected": False, "from_operation": True,
                "template": plc["template"], "expected_launch_min": plc["expected_launch_window_min"],
            })
        events.sort(key=lambda x: x["ts"] or 0, reverse=True)
        return jsonify({"events": events})
    finally:
        ov.close(); live.close()


@ops_dashboard_bp.route("/api/ops-v2/intel/operation/<uuid>")
def api_intel_operation(uuid):
    """Drilldown: webhook coverage by role + recent events + uncovered high-priority
    wallets + candidates pending launch, for one operation."""
    ov = _conn()
    live = _live_conn()
    try:
        wh = _webhooked_set(live)
        roles = {"TREASURY": [], "COLLECTOR": [], "PASS_THROUGH": []}
        for w in ov.execute("SELECT wallet, role FROM wt_ops_v2_wallets WHERE operation_uuid=?", (uuid,)).fetchall():
            if w["role"] in roles:
                roles[w["role"]].append(w["wallet"])
        cands = [r["wallet"] for r in ov.execute(
            "SELECT DISTINCT wallet FROM wt_operation_candidates WHERE operation_uuid=?", (uuid,)).fetchall()] \
            if _table_exists(ov, "wt_operation_candidates") else []
        # TEMPLATE-funded candidates only = real pre-launch creator seeds. The rest of
        # `cands` are fan-out spray recipients (dust the op touched), NOT pending creators.
        template_cands = {r[0] for r in ov.execute(
            "SELECT DISTINCT wallet FROM wt_operation_candidates WHERE operation_uuid=? AND template_base IS NOT NULL", (uuid,)).fetchall()} \
            if _table_exists(ov, "wt_operation_candidates") else set()
        migrated = {r[0] for r in ov.execute(
            "SELECT creator_wallet FROM wt_ops_v2_creators WHERE operation_uuid=? AND migration_time IS NOT NULL", (uuid,)).fetchall()}
        confirmed_treasuries = {r[0] for r in ov.execute("SELECT treasury FROM wt_confirmed_treasuries").fetchall()}

        def cov(lst): return {"covered": sum(1 for w in lst if w in wh), "total": len(lst)}
        coverage = {"treasury": cov(roles["TREASURY"]), "collectors": cov(roles["COLLECTOR"]),
                    "passthroughs": cov(roles["PASS_THROUGH"]), "candidates": cov(list(template_cands))}
        uncovered = [w for w in (list(template_cands) + roles["COLLECTOR"] + roles["PASS_THROUGH"] + roles["TREASURY"]) if w not in wh]
        # pending launch = TEMPLATE-funded, NOT migrated, NOT a confirmed treasury (those
        # aren't pre-launch creators). This is the genuine "about to launch" set.
        pending = [w for w in template_cands if w not in migrated and w not in confirmed_treasuries]
        # recent webhook events for this op's wallets
        op_wallets = set(cands) | {w for g in roles.values() for w in g}
        events = []
        if op_wallets:
            ph = ",".join("?" * len(op_wallets))
            for h in live.execute(
                f"SELECT wallet_address, tx_type, amount_sol, block_time FROM wt_webhook_hits "
                f"WHERE wallet_address IN ({ph}) ORDER BY block_time DESC LIMIT 30", list(op_wallets)).fetchall():
                events.append(dict(h))
        return jsonify({"operation_uuid": uuid, "coverage": coverage,
                        "uncovered_high_priority": uncovered[:50], "pending_launch": pending[:50],
                        "recent_events": events})
    finally:
        ov.close(); live.close()


@ops_dashboard_bp.route("/api/ops-v2/intel/enroll", methods=["POST"])
def api_intel_enroll():
    """Enrol operation accounts into the candidate webhook. WRITE endpoint —
    POST + {confirm:true} required. Wallets are re-validated server-side against
    wt_ops_v2 (won't enroll arbitrary input). Wired to the proven, idempotent
    webhook_manager.enroll_batch (skips already-active, guards infra addresses)."""
    import asyncio as _asyncio
    body = request.get_json(silent=True) or {}
    if body.get("confirm") is not True:
        return jsonify({"error": "confirmation required", "hint": "POST {confirm:true, wallets:[...]} or {confirm:true, tier1:true}"}), 400

    # ── OFF direction: remove wallets from the webhook (the ON/OFF toggle). ──────
    if body.get("action") == "off":
        wallets = list(dict.fromkeys(body.get("wallets") or []))
        if not wallets:
            return jsonify({"error": "no wallets to remove"}), 400
        try:
            from src.analysis.webhook_manager import WebhookManager, CANDIDATE_ROLE
            loop = _asyncio.new_event_loop()
            mgr = WebhookManager(LIVE_DB_PATH)
            removed = 0
            for w in wallets[:ENROLL_BATCH_CAP]:
                loop.run_until_complete(mgr.remove(w, role=CANDIDATE_ROLE, reason="ops-os toggle off"))
                removed += 1
            loop.close()
            return jsonify({"ok": True, "action": "off", "removed": removed})
        except Exception as exc:
            return jsonify({"error": f"remove failed: {type(exc).__name__}: {exc}"}), 500

    ov = _conn()
    try:
        # build the target list: explicit wallets, or all uncovered Tier-1 candidates
        if body.get("tier1"):
            live = _live_conn()
            try:
                wh = _webhooked_set(live)
            finally:
                live.close()
            targets = [r["wallet"] for r in ov.execute(
                "SELECT DISTINCT cc.wallet FROM wt_operation_candidates cc "
                "JOIN wt_operation_lifecycle l ON l.operation_uuid=cc.operation_uuid "
                "WHERE l.state IN ('PROVISIONING','CREATORS_SEEN','REACTIVATED') "
                "  AND cc.wallet NOT IN (SELECT creator_wallet FROM wt_ops_v2_creators WHERE migration_time IS NOT NULL) "
                "ORDER BY cc.confidence DESC, cc.first_seen DESC LIMIT 500").fetchall()
                if r["wallet"] not in wh]
        else:
            targets = list(dict.fromkeys(body.get("wallets") or []))

        # server-side validation: every target must be a current op account
        valid = [w for w in targets if _is_valid_op_account(ov, w)]
        rejected = [w for w in targets if w not in valid]
        if not valid:
            return jsonify({"error": "no valid operation accounts to enroll",
                            "rejected": rejected[:20]}), 400
        if len(valid) > ENROLL_BATCH_CAP:
            valid = valid[:ENROLL_BATCH_CAP]
            capped = True
        else:
            capped = False
    finally:
        ov.close()

    # run the proven async enrol path (fresh loop per call — matches existing call sites)
    try:
        from src.analysis.webhook_manager import WebhookManager, CANDIDATE_ROLE
        loop = _asyncio.new_event_loop()
        mgr = WebhookManager(LIVE_DB_PATH)
        enrolled = loop.run_until_complete(
            mgr.enroll_batch(valid, role=CANDIDATE_ROLE, notes="operations-os intel page"))
        loop.close()
    except Exception as exc:
        return jsonify({"error": f"enroll failed: {type(exc).__name__}: {exc}"}), 500

    return jsonify({"ok": True, "requested": len(targets), "validated": len(valid),
                    "newly_enrolled": enrolled, "capped": capped,
                    "rejected_non_op_accounts": len(rejected)})


def register_operation_dashboard_routes(app):
    app.register_blueprint(ops_dashboard_bp)
