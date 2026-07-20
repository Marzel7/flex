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
import threading
import time
import json as _json

from flask import Blueprint, render_template, jsonify, request, redirect
from src.utils.db_locking import db_connect

_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "../.."))
OPS_DB_PATH    = os.environ.get("OPS_V2_DB_PATH",  os.path.join(_REPO_ROOT, "database", "wt_ops_v2.db"))
ALERTS_DB_PATH = os.environ.get("ALERTS_DB_PATH", os.path.join(_REPO_ROOT, "database", "wt_alerts.db"))

# Live views show classified operations (WATCHTOWER + MICRO_DEPLOYER — both real operator
# activity). EXCLUDE: legacy rejected rows, and UNTEMPLATED ops (no …039280 template =
# not part of the operator-template mechanism, so not a tracked operation).
_LIVE_OPS_EXCLUDE = ("(o.status IS NULL OR o.status != 'REJECTED_SERIAL_DEPLOYER') "
                     "AND (o.op_type IS NULL OR o.op_type != 'UNTEMPLATED')")
_LIVE_OPS_EXCLUDE_NOALIAS = ("(status IS NULL OR status != 'REJECTED_SERIAL_DEPLOYER') "
                             "AND (op_type IS NULL OR op_type != 'UNTEMPLATED')")

ops_dashboard_bp = Blueprint("ops_dashboard", __name__)


def _conn():
    # Read-only URI connection over wt_ops_v2.db — see _live_conn rationale.
    # Dashboard reads only; bypass the write lane so the operation_scheduler's
    # writes can't block the page. Handlers that WRITE must use _conn_rw().
    c = sqlite3.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True, timeout=10)
    c.execute("PRAGMA busy_timeout=10000")
    c.row_factory = sqlite3.Row
    return c


def _conn_rw():
    # Writable, serialized connection over wt_ops_v2.db for the few action
    # handlers that mutate (subprov-funder, treasury-promote, treasury_stats).
    c = db_connect(OPS_DB_PATH, timeout=60)
    c.execute("PRAGMA busy_timeout=60000")  # 60s C-level wait — survives cross-process lock contention
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
                   CASE WHEN cr.creator_wallet IS NOT NULL THEN 1 END AS migrated
            FROM wt_operation_candidates c
            JOIN wt_ops_v2 o ON o.operation_uuid=c.operation_uuid
            LEFT JOIN wt_ops_v2_creators cr ON cr.creator_wallet=c.wallet
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
    # Read-only URI connection: this dashboard NEVER writes the live DB, and a
    # read-only WAL connection reads the last committed snapshot WITHOUT taking
    # the file write lock — so it can't be blocked by the listener's write storm.
    c = sqlite3.connect(f"file:{LIVE_DB_PATH}?mode=ro", uri=True, timeout=2)
    c.execute("PRAGMA busy_timeout=1500")
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
        # 69SNcR… is a known noisy infrastructure wallet — suppress from the live feed
        _FEED_SUPPRESSED = {"69SNcRC8NqjHBSXEcugCN5oFKRQoKmddmWzZYc3tqtxk"}
        rows = live.execute(
            "SELECT event_type, wallet_address, related_wallet, payload_json, created_at "
            "FROM watchtower_events WHERE created_at > strftime('%s','now')-86400 "
            "AND wallet_address NOT IN ({}) "
            "ORDER BY created_at DESC LIMIT 200".format(
                ",".join("?" * len(_FEED_SUPPRESSED))
            ), list(_FEED_SUPPRESSED)).fetchall()
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
        wt_detection_by_mint = {}
        try:
            for r in ov.execute(
                "SELECT mint, detection_source, detection_delay_seconds "
                "FROM wt_watchtower_launches WHERE mint IS NOT NULL"
            ).fetchall():
                wt_launch_mints.add(r[0])
                wt_detection_by_mint[r[0]] = {
                    "source": r[1],
                    "delay_seconds": r[2],
                }
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
            # close_destination holds the token mint for BUY_SWARM candidates
            swarm_mints = {r[0] for r in ov.execute(
                "SELECT DISTINCT close_destination FROM wt_candidate_websocket_watches "
                "WHERE state='BUY_SWARM' AND close_destination IS NOT NULL").fetchall()}
        except Exception:
            pass
        # UNCONFIRMED_WATCHTOWER_LIKE — wrap-close lineage confirmed, root unknown
        uwl_by_mint = {}
        try:
            live_conn_uwl = __import__("sqlite3").connect(LIVE_DB_PATH, timeout=5)
            live_conn_uwl.row_factory = __import__("sqlite3").Row
            for r in live_conn_uwl.execute(
                "SELECT mint, subprov_wallet, unknown_root_wallet, root_hop, amount_sol "
                "FROM wt_unconfirmed_watchtower_like WHERE status='REVIEW'"
            ).fetchall():
                uwl_by_mint[r["mint"]] = {
                    "subprov": r["subprov_wallet"], "root": r["unknown_root_wallet"],
                    "hop": r["root_hop"], "amount": r["amount_sol"],
                }
            live_conn_uwl.close()
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
        # STRONG attribution mints (backfill lineage-walk confirmed) also get the WATCHTOWER tag
        attribution_mints = set()
        try:
            attribution_mints = {r[0] for r in ov.execute(
                "SELECT mint FROM watchtower_token_attribution WHERE tier='STRONG'").fetchall()}
        except Exception:
            pass

        # treat discovery mints as WATCHTOWER for tagging even if they also have a watch_candidate row
        wt_launch_mints = set(wt_launch_mints)   # cascade-confirmed (gets the ✓)
        # candidate-lineage: resolve creator→mint via bulk attach (zero per-row RPC)
        candidate_lineage_mints = {}  # mint → subprov
        try:
            # Attach live DB to ov (ops conn) — NOT to live (read-only URI conn can't ATTACH r/w DBs)
            _live_db_path = LIVE_DB_PATH
            ov.execute(f"ATTACH DATABASE 'file:{_live_db_path}?mode=ro' AS _live")
            for r in ov.execute(
                "SELECT t.mint, c.subprov_wallet FROM _live.token_analysis t "
                "JOIN wt_candidate_websocket_watches c "
                "  ON t.earliest_tx_creator=c.candidate_wallet OR t.pf_ws_creator=c.candidate_wallet "
                "WHERE c.candidate_wallet IS NOT NULL"
            ).fetchall():
                candidate_lineage_mints[r[0]] = r[1]
            ov.execute("DETACH DATABASE _live")
        except Exception:
            try: ov.execute("DETACH DATABASE _live")
            except Exception: pass
        _wt_tag_mints = wt_launch_mints | discovery_mints | attribution_mints | set(candidate_lineage_mints.keys())

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
        # Note: FARM mints already in seen_mints (from watch_candidate_tokens) are tagged UNKNOWN
        # there; we still need to add the farm metadata so the tag override fires at render time.
        for m, fm in farm_by_mint.items():
            if m in seen_mints:
                # Already in rows from base query — inject farm metadata so tagging applies
                for row in rows:
                    if row.get("mint") == m and not row.get("_farm"):
                        row["_farm_funder"] = fm["funder"]
                        row["_farm_mechanism"] = fm["mechanism"]
                        row["_farm_creators"] = fm["farm_creators"]
                        row["_farm_funder_type"] = fm.get("funder_type", "OPERATOR")
                        row["_farm_cex_label"] = fm.get("cex_label")
                        row["_farm"] = True
                        break
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
            _now = int(time.time())
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
                _age_s = _now - (tr.get("migrated_at") or _now)
                is_fresh = (ctok == 1)  # single-use creator — fresh wallet, one launch
                rows.append({"mint": m,
                             "classified_as": "FRESH" if is_fresh else "UNKNOWN",
                             "classification_conf": None,
                             "classification_reason": ("single_use_creator_migrated" if is_fresh
                                                       else "recent_migration"),
                             "prediction_score": None,
                             "creator_address": tr.pop("earliest_tx_creator", None), **tr})
        except Exception:
            pass
        # ── FUNDING CYCLE (batch, once for the whole page) ────────────────────────────────
        # For each token's creator → its subprov (wrap_close_candidates) → the treasury→subprov
        # capital lifecycle (wt_webhook_hits): INITIAL seed, N TOP-UPs, and whether it's SWEEPING
        # back. Summarized to a compact per-row badge. Bounded + zero RPC.
        cycle_by_creator = {}
        try:
            page_creators = [r.get("creator_address") for r in rows if r.get("creator_address")]
            if page_creators:
                cph = ",".join("?" * len(page_creators))
                # creator → subprov + treasury
                cre_sub = {}
                for cr, sp, tre in ov.execute(
                    f"SELECT creator, subprov_wallet, lineage_source_treasury "
                    f"FROM wt_wrap_close_candidates WHERE creator IN ({cph})", page_creators).fetchall():
                    if cr and sp and cr not in cre_sub:
                        cre_sub[cr] = (sp, tre)
                # gather treasury↔subprov events for all involved (subprov, treasury) pairs
                pairs = {(sp, tre) for sp, tre in cre_sub.values() if tre}
                ev_by_sub = {}
                _lc = _live_conn()
                try:
                    for (sp, tre) in pairs:
                        evs = _lc.execute(
                            "SELECT amount_sol, direction, block_time FROM wt_webhook_hits "
                            "WHERE ((wallet_address=? AND counterparty=?) OR (wallet_address=? AND counterparty=?)) "
                            "ORDER BY block_time ASC", (tre, sp, sp, tre)).fetchall()
                        outs = [e for e in evs if e["direction"] == "outbound" and (e["amount_sol"] or 0) >= 0.5]
                        backs = [e for e in evs if e["direction"] == "inbound" and (e["amount_sol"] or 0) >= 0.5]
                        ev_by_sub[(sp, tre)] = {
                            "n_out": len(outs), "n_back": len(backs),
                            "initial_sol": (outs[0]["amount_sol"] if outs else None),
                            "total_out": round(sum(e["amount_sol"] or 0 for e in outs), 1),
                            "total_back": round(sum(e["amount_sol"] or 0 for e in backs), 1),
                            "first_at": (outs[0]["block_time"] if outs else None),
                            "last_at": (evs[-1]["block_time"] if evs else None),
                            "sweeping": bool(backs and evs and evs[-1]["direction"] == "inbound"),
                        }
                finally:
                    _lc.close()
                for cr, (sp, tre) in cre_sub.items():
                    c = ev_by_sub.get((sp, tre))
                    if c and c["n_out"]:
                        cycle_by_creator[cr] = {**c, "subprov": sp, "treasury": tre}
        except Exception:
            pass

        # CEX funder per creator — batch lookup from creator_funders for FRESH label
        cex_by_creator = {}
        try:
            all_creators = [r.get("creator_address") for r in rows if r.get("creator_address")]
            if all_creators:
                cph = ",".join("?" * len(all_creators))
                for cr, faddr, cex_ex, cex_ty in live.execute(
                    f"SELECT creator_address, funder_address, cex_exchange, cex_type "
                    f"FROM creator_funders WHERE is_cex=1 AND creator_address IN ({cph}) "
                    f"AND cex_exchange IS NOT NULL AND cex_exchange NOT LIKE 'Unknown%' "
                    f"GROUP BY creator_address", all_creators).fetchall():
                    if cr not in cex_by_creator:
                        cex_by_creator[cr] = {"funder": faddr, "cex_label": cex_ex, "cex_type": cex_ty}
        except Exception:
            pass

        out = []
        out_mints: set = set()
        for r in rows:
            mint = r["mint"]
            if mint in out_mints:
                continue
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
            farm = farm_by_mint.get(mint) or (
                {"funder": r.get("_farm_funder"), "mechanism": r.get("_farm_mechanism"),
                 "farm_creators": r.get("_farm_creators"),
                 "funder_type": r.get("_farm_funder_type"), "cex_label": r.get("_farm_cex_label")}
                if r.get("_farm") else None)
            uwl = uwl_by_mint.get(mint)                   # wrap-close lineage, unknown root
            # tag precedence: WATCHTOWER > UNCONFIRMED_WT_LIKE > FARM > base
            if is_wt or base == "WATCHTOWER":
                tag = "WATCHTOWER"
            elif uwl:
                tag = "WT_LIKE"
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
                "wt_detection_source": (wt_detection_by_mint.get(mint) or {}).get("source"),
                "wt_detection_delay_seconds": (wt_detection_by_mint.get(mint) or {}).get("delay_seconds"),
                "uwl_subprov": (uwl or {}).get("subprov"),
                "uwl_root": (uwl or {}).get("root"),
                "uwl_hop": (uwl or {}).get("hop"),
                "farm_funder": (farm or {}).get("funder"),
                "farm_mechanism": (farm or {}).get("mechanism"),
                "farm_creators": (farm or {}).get("farm_creators"),
                "farm_funder_type": (farm or {}).get("funder_type"),
                "farm_cex_label": (farm or {}).get("cex_label"),
                "creator_cex_label": (cex_by_creator.get(r.get("creator_address")) or {}).get("cex_label"),
                "creator_cex_funder": (cex_by_creator.get(r.get("creator_address")) or {}).get("funder"),
                "classification_conf": r.get("classification_conf"),
                "classification_reason": r.get("classification_reason"),
                "prediction_score": r.get("prediction_score"),
                "creator": r.get("creator_address"),
                "funding_cycle": cycle_by_creator.get(r.get("creator_address")),
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
            out_mints.add(mint)
        # Deduplicate by mint (multiple sources can produce the same mint; keep highest-priority tag)
        _tag_rank = {"WATCHTOWER": 0, "WT_LIKE": 1, "FARM": 2, "SWARM": 3, "FRESH": 4, "UNKNOWN": 5}
        seen_out: dict = {}
        for rec in out:
            m = rec["mint"]
            if m not in seen_out or _tag_rank.get(rec["tag"], 9) < _tag_rank.get(seen_out[m]["tag"], 9):
                seen_out[m] = rec
        out = list(seen_out.values())
        # Sort by recency (migrated_at → created_at) then cap at limit.
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


@ops_dashboard_bp.route("/api/ops-v2/intel/token-tree/<mint>")
def api_intel_token_tree(mint):
    """The full PROVISIONING TREE for one launched token: treasury → subprov(s) → creator
    (CREATE) + swarm wallets (BUY/SELL), so 'everything related to this token' is one view.
    A single subprov fan-out often bootstraps BOTH the creator AND its initial buy/sell swarm
    in one wrap-close burst (e.g. 642MWKJD seeded 4fmaor→CREATE + 22 dust swarm wallets for
    OILMAXXING). We classify each child CREATOR vs SWARM by whether it ever created a token.
    Zero RPC — all from wt_wrap_close_candidates ⋈ live token_analysis."""
    ov = _conn(); live = _live_conn()
    try:
        # 1) mint → creator (the wallet that CREATEd it)
        row = live.execute("SELECT pf_ws_creator FROM token_analysis WHERE mint=?", (mint,)).fetchone()
        creator = row[0] if row else None
        if not creator:
            return jsonify({"mint": mint, "creator": None, "treasuries": [],
                            "note": "no creator on record for this mint"})
        # 2) creator → its provisioning subprov(s) + lineage treasury
        prov = ov.execute(
            "SELECT subprov_wallet, lineage_source_treasury, base_amount_sol "
            "FROM wt_wrap_close_candidates WHERE creator=?", (creator,)).fetchall()
        # 3) for each provisioning subprov, pull its FULL child fan-out and split creator vs swarm
        # cache: which child wallets created a token? (one bounded query over all children)
        all_subprovs = {p[0] for p in prov if p[0]}
        children = {}   # subprov -> list of (child_creator, state, base_sol)
        for sp in all_subprovs:
            children[sp] = ov.execute(
                "SELECT creator, state, base_amount_sol FROM wt_wrap_close_candidates "
                "WHERE subprov_wallet=?", (sp,)).fetchall()
        child_wallets = list({c[0] for kids in children.values() for c in kids if c[0]})
        created_set = set()
        if child_wallets:
            cph = ",".join("?" * len(child_wallets))
            created_set = {r[0] for r in live.execute(
                f"SELECT DISTINCT pf_ws_creator FROM token_analysis WHERE pf_ws_creator IN ({cph})",
                child_wallets).fetchall()}
        # build the tree grouped by treasury → subprov
        treas_map = {}
        for sp, lineage_treas, base in prov:
            t = lineage_treas or "UNKNOWN_TREASURY"
            tnode = treas_map.setdefault(t, {"treasury": t, "subprovs": {}})
            if sp in tnode["subprovs"]:
                continue
            kids = children.get(sp, [])
            creators_k, swarm_k = [], []
            for ck, state, csol in kids:
                node = {"wallet": ck, "sol": csol, "state": state,
                        "created": ck in created_set}
                (creators_k if ck in created_set else swarm_k).append(node)
            tnode["subprovs"][sp] = {
                "subprov": sp,
                "seed_to_this_creator": base,
                "role": ("LAUNCH+SWARM" if creators_k and swarm_k
                         else "CREATOR" if creators_k else "SWARM"),
                "n_creators": len(creators_k), "n_swarm": len(swarm_k),
                "creators": sorted(creators_k, key=lambda x: -(x["sol"] or 0)),
                "swarm": sorted(swarm_k, key=lambda x: -(x["sol"] or 0)),
            }
        treasuries = [{"treasury": t, "subprovs": list(v["subprovs"].values())}
                      for t, v in treas_map.items()]
        # LATER swarm WAVES: wallets that BOUGHT this mint, captured reverse-direction by the
        # cascade (wt_swarm_buys) — zero-RPC, populated from swap txs already fetched. Group by the
        # subprov that funded each buyer. Excludes buyers already shown in the provisioning tree.
        in_tree = {c["wallet"] for t in treasuries for sp in t["subprovs"]
                   for c in (sp["creators"] + sp["swarm"])}
        swarm_waves = {}
        try:
            for r in ov.execute(
                "SELECT swarm_wallet, subprov_wallet, treasury_wallet, observed_at "
                "FROM wt_swarm_buys WHERE mint=? ORDER BY observed_at", (mint,)).fetchall():
                if r[0] in in_tree:
                    continue
                sp = r[1] or "UNKNOWN_SUBPROV"
                wv = swarm_waves.setdefault(sp, {"subprov": sp, "treasury": r[2], "buyers": []})
                wv["buyers"].append({"wallet": r[0], "observed_at": r[3]})
        except Exception:
            pass
        return jsonify({
            "mint": mint, "creator": creator, "treasuries": treasuries,
            "swarm_waves": list(swarm_waves.values()),
            "note": ("direct provisioning fan-out + reverse-attributed later swarm waves "
                     "(captured going forward; historical waves before this ran are absent)"),
        })
    finally:
        ov.close(); live.close()


@ops_dashboard_bp.route("/api/ops-v2/intel/launch-metrics")
def api_intel_launch_metrics():
    """Pre-launch counts + average lead time (template funding → migration)."""
    import statistics
    ov = _conn_rw(); live = _live_conn()  # writes treasury_stats below
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
    """Apex Funders panel — read-only aggregation over wt_treasury_funders (ops DB only).

    Zero RPC. No writes. No live-DB access. No lock risk.
    wt_treasury_funders is a pre-aggregated summary written by the cascade as it observes
    inbound treasury funding events; this endpoint only reads and reshapes it.

    Query param: window = 24h | 7d | 30d | all  (default: 30d)
    """
    import sqlite3 as _sq, time as _t, collections as _col
    from src.core.ws_cascade_store import OPS_DB_PATH

    _WINDOWS = {"24h": 86400, "7d": 604800, "30d": 2592000}
    window = request.args.get("window", "30d")
    cutoff = int(_t.time()) - _WINDOWS.get(window, _WINDOWS["30d"]) if window != "all" else 0

    conn = _sq.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True, timeout=5,
                       check_same_thread=False)
    conn.row_factory = _sq.Row
    try:
        conn.execute("PRAGMA busy_timeout=5000")

        # Ensure indexes exist (create-if-missing, safe on read-only connection via attached check)
        # Note: can't CREATE INDEX on a mode=ro connection — indexes must exist already.
        # We verify via the writable path at startup; see ensure_cascade_schema.

        # Confirmed treasury set for TREASURY_MESH classification
        confirmed = {r[0] for r in conn.execute(
            "SELECT treasury FROM wt_confirmed_treasuries").fetchall()}

        # Time-window filter on last_seen
        time_clause = "AND last_seen >= ?" if cutoff else ""
        params = (cutoff,) if cutoff else ()

        # One row per (funder, treasury) — aggregate across treasuries per funder
        sql = f"""
            SELECT
                funder,
                COUNT(DISTINCT treasury)   AS treasuries_funded,
                SUM(fund_count)            AS fund_count,
                ROUND(SUM(total_sol), 1)   AS total_sol,
                ROUND(MAX(max_sol), 1)     AS max_sol,
                MAX(last_seen)             AS last_seen,
                MAX(is_subprov_sweep)      AS is_subprov_sweep
            FROM wt_treasury_funders
            WHERE 1=1 {time_clause}
            GROUP BY funder
            ORDER BY treasuries_funded DESC, total_sol DESC
            LIMIT 100
        """
        rows = conn.execute(sql, params).fetchall()

        _RANK = {"TREASURY_MESH": 0, "HUB": 1, "EXTERNAL": 2, "SWEEP": 3}
        funders = []
        for r in rows:
            funder = r["funder"]
            tf = r["treasuries_funded"] or 0
            is_sweep = bool(r["is_subprov_sweep"])
            is_mesh = funder in confirmed

            if is_mesh:
                cls = "TREASURY_MESH"
            elif tf > 1:
                cls = "HUB"
            elif is_sweep:
                cls = "SWEEP"
            else:
                cls = "EXTERNAL"

            funders.append({
                "funder":           funder,
                "treasuries_funded": tf,
                "max_sol":          r["max_sol"] or 0,
                "total_sol":        r["total_sol"] or 0,
                "fund_count":       r["fund_count"] or 0,
                "expansion_class":  cls,
                "last_seen":        r["last_seen"],
                "is_shared_apex":   tf > 1 and not is_sweep,
                "is_subprov_sweep": is_sweep,
                "is_known_treasury": is_mesh,
            })

        funders.sort(key=lambda x: (_RANK.get(x["expansion_class"], 9), -(x["last_seen"] or 0)))
        cc = dict(_col.Counter(f["expansion_class"] for f in funders))
        return jsonify({
            "funders":       funders,
            "count":         len(funders),
            "shared_apexes": [f["funder"] for f in funders if f["is_shared_apex"]],
            "class_counts":  cc,
            "window":        window,
        })
    finally:
        conn.close()


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
        try:
            from src.utils.infra_mapping import is_known_account as _subprov_is_known
        except Exception:
            _subprov_is_known = lambda _: False
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
        # immediate_funder/funder_is_subprov added by the subprov-mesh model — the
        # DISTRIBUTION tier (a subprov funded by another subprov). Tolerate older DBs.
        _have_mesh = _column_exists(ov, "wt_discovered_subprovs", "immediate_funder")
        _mesh_cols = ", immediate_funder, funder_is_subprov" if _have_mesh else ""
        rows = ov.execute(
            "SELECT subprov, first_creator, creator_count, treasury, treasury_known, last_seen"
            + _mesh_cols + " FROM wt_discovered_subprovs"
            " WHERE COALESCE(state,'active') != 'dismissed' ORDER BY last_seen DESC").fetchall()
        # Build a map: immediate_funder → treasury for distribution nodes
        funder_treasury: dict = {}
        if _have_mesh:
            try:
                for fr in ov.execute(
                    "SELECT subprov, treasury FROM wt_discovered_subprovs "
                    "WHERE immediate_funder IS NOT NULL OR funder_is_subprov=1"
                ).fetchall():
                    if fr["treasury"]:
                        funder_treasury[fr["subprov"]] = fr["treasury"]
            except Exception:
                pass
        out = []
        known_count = 0
        # Distribution hubs that need surfacing: immediate_funder with unknown treasury.
        # We surface the HUB as the single lead rather than all its downstream rows.
        unresolved_hubs: dict = {}  # hub_addr → {last_seen, creator_count}
        for r in rows:
            sp = r["subprov"]
            if _subprov_is_known(sp):
                known_count += 1
                continue
            treasury = r["treasury"] or lineage.get(sp)
            funder_is_subprov = bool(r["funder_is_subprov"]) if _have_mesh else False
            immediate_funder = (r["immediate_funder"] if _have_mesh else None)
            # funder_is_subprov only counts as "resolved" if the distribution subprov's
            # OWN treasury is also confirmed — otherwise the chain is still unknown.
            funder_resolved = False
            if funder_is_subprov and immediate_funder:
                funder_t = funder_treasury.get(immediate_funder)
                funder_resolved = bool(funder_t and (funder_t in confirmed or funder_t in reviewing))
            # resolved if the treasury is known (confirmed OR in review OR linked via lineage),
            # OR if the immediate funder distribution subprov has a confirmed treasury.
            if (treasury and (treasury in confirmed or treasury in reviewing)) or funder_resolved:
                known_count += 1
                continue
            # If funded via an unresolved distribution hub, suppress this downstream row
            # and accumulate the hub itself as the single lead to investigate.
            if funder_is_subprov and immediate_funder and not funder_resolved:
                known_count += 1  # downstream row is accounted for
                hub = unresolved_hubs.setdefault(immediate_funder, {"last_seen": None, "creator_count": 0})
                hub["creator_count"] += r["creator_count"] or 0
                if r["last_seen"] and (hub["last_seen"] is None or r["last_seen"] > hub["last_seen"]):
                    hub["last_seen"] = r["last_seen"]
                continue
            # a treasury we have but haven't confirmed/reviewed = still a lead, show the addr
            out.append({
                "subprov": sp,
                "creators": r["creator_count"],
                "treasury": treasury,
                "treasury_status": ("pending" if treasury in reviewing else "unknown") if treasury else "unknown",
                "treasury_known": False,
                "total_sol": None,
                "last_seen": r["last_seen"],
                "first_creator": r["first_creator"],
                "immediate_funder": immediate_funder,
                "funder_is_subprov": funder_is_subprov,
            })
        # Surface each unresolved distribution hub as a single lead with ＋ set funder.
        # If the hub's lineage treasury is already confirmed, skip it — it's resolved.
        for hub_addr, hub_info in unresolved_hubs.items():
            hub_treasury = lineage.get(hub_addr)
            if hub_treasury and (hub_treasury in confirmed or hub_treasury in reviewing):
                continue
            out.append({
                "subprov": hub_addr,
                "creators": hub_info["creator_count"],
                "treasury": hub_treasury,
                "treasury_status": "unknown",
                "treasury_known": False,
                "total_sol": None,
                "last_seen": hub_info["last_seen"],
                "first_creator": None,
                "immediate_funder": None,
                "funder_is_subprov": False,
                "is_distribution_hub": True,
            })
        # DISTRIBUTION NODES: real subprovs that fund OTHER real subprovs (read-only
        # derivation, mechanism-guarded — never raw mid-chain nodes). Surfaced so the UI
        # can render root treasury → distribution subprov → subprov → creator.
        distribution_nodes = []
        try:
            from src.core.subprov_distribution import mid_tier_subprovs
            distribution_nodes = mid_tier_subprovs()
        except Exception:
            distribution_nodes = []
        return jsonify({"subprovs": out, "count": len(out),
                        "unknown_treasury": len(out), "known_resolved": known_count,
                        "distribution_nodes": distribution_nodes,
                        "distribution_count": len(distribution_nodes)})
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
        # Deduplicate by subprov: one row per active subprov, most-recent session wins for
        # treasury/TTL; funding_amount = sum across all active sessions for that subprov.
        _scols = {r[1] for r in ov.execute("PRAGMA table_info(wt_active_subprov_sessions)").fetchall()}
        _topup_cols = "initial_funding_amount, topup_count, topup_amount_total, last_topup_at" \
            if "initial_funding_amount" in _scols else \
            "NULL AS initial_funding_amount, 0 AS topup_count, 0.0 AS topup_amount_total, NULL AS last_topup_at"
        _sess_raw = ov.execute(
            f"SELECT subprov_wallet, treasury_wallet, funding_amount, funding_time, "
            f"detected_at, expires_at, open_reason, COALESCE(monitoring_state,'INTEL_ONLY') as monitoring_state, {_topup_cols} "
            "FROM wt_active_subprov_sessions WHERE state='ACTIVE' "
            "ORDER BY detected_at DESC").fetchall()
        _WS_CASCADE_SUPPRESSED = {"69SNcRC8NqjHBSXEcugCN5oFKRQoKmddmWzZYc3tqtxk"}
        _by_subprov: dict = {}
        for r in _sess_raw:
            sp = r["subprov_wallet"]
            if r["treasury_wallet"] in _WS_CASCADE_SUPPRESSED:
                continue
            if sp not in _by_subprov:
                initial = r["initial_funding_amount"] or r["funding_amount"] or 0
                topup_total = r["topup_amount_total"] or 0
                _by_subprov[sp] = {
                    "subprov": sp, "treasury": r["treasury_wallet"],
                    "funding_amount": r["funding_amount"] or 0,
                    "funding_time": r["funding_time"],
                    "ttl_remaining": max(0, (r["expires_at"] or now) - now),
                    "open_reason": r["open_reason"] or "PROVISION_CANDIDATE",
                    "monitoring_state": r["monitoring_state"],
                    "ws_subscribed": r["monitoring_state"] in ("LIVE_ARMED", "POST_CREATE_ACTIVE"),
                    "post_create_active": r["monitoring_state"] == "POST_CREATE_ACTIVE",
                    "initial_funding_sol": initial,
                    "topup_count": r["topup_count"] or 0,
                    "topup_amount_total": topup_total,
                    "session_total_sol": initial + topup_total,
                    "last_topup_at": r["last_topup_at"],
                }
            else:
                _by_subprov[sp]["funding_amount"] = (_by_subprov[sp]["funding_amount"] or 0) + (r["funding_amount"] or 0)
        # attach candidate count once per subprov
        _cand_counts = {
            row[0]: row[1] for row in ov.execute(
                "SELECT subprov_wallet, COUNT(DISTINCT candidate_wallet) FROM wt_candidate_websocket_watches "
                "WHERE state='WATCHING' AND expires_at > ? "
                "GROUP BY subprov_wallet", (now,)).fetchall()
        }
        # Enrich each session with treasury identity from confirmed_treasuries + vanity_families
        _treasury_set = {s["treasury"] for s in _by_subprov.values() if s.get("treasury")}
        _treasury_meta: dict = {}
        if _treasury_set:
            placeholders = ",".join("?" * len(_treasury_set))
            t_list = list(_treasury_set)
            for row in ov.execute(
                f"SELECT treasury, confidence, method, provenance, out_sol, recipients "
                f"FROM wt_confirmed_treasuries WHERE treasury IN ({placeholders})", t_list
            ).fetchall():
                _treasury_meta[row["treasury"]] = {
                    "confidence": row["confidence"], "method": row["method"],
                    "provenance": row["provenance"], "out_sol": row["out_sol"],
                    "recipients": row["recipients"],
                }
            # Vanity family lookup — family_label and role for each treasury
            for row in ov.execute(
                f"SELECT family_label, confirmed_wallets_json, roles_json "
                f"FROM wt_vanity_families"
            ).fetchall():
                try:
                    wallets = _json.loads(row["confirmed_wallets_json"] or "[]")
                    roles   = _json.loads(row["roles_json"] or "{}")
                    for w in wallets:
                        if w in _treasury_meta:
                            _treasury_meta[w]["family"] = row["family_label"]
                            _treasury_meta[w]["role"]   = roles.get(w)
                except Exception:
                    pass
        sessions = []
        for sp, s in _by_subprov.items():
            s["candidates"] = _cand_counts.get(sp, 0)
            tm = _treasury_meta.get(s.get("treasury") or "")
            if tm:
                s["treasury_confidence"] = tm.get("confidence")
                s["treasury_method"]     = tm.get("method")
                s["treasury_family"]     = tm.get("family")
                s["treasury_role"]       = tm.get("role")
                s["treasury_out_sol"]    = tm.get("out_sol")
                s["treasury_recipients"] = tm.get("recipients")
            sessions.append(s)
        sessions.sort(key=lambda s: s.get("funding_time") or 0, reverse=True)
        # candidate watches grouped by subprov — one entry per unique (candidate, subprov)
        watches = {}
        total_watching = 0
        for r in ov.execute(
            "SELECT candidate_wallet, subprov_wallet, "
            "  MAX(funding_amount) as funding_amount, MAX(expires_at) as expires_at "
            "FROM wt_candidate_websocket_watches WHERE state='WATCHING' AND subprov_wallet IS NOT NULL "
            "  AND expires_at > ? "
            "GROUP BY candidate_wallet, subprov_wallet "
            "ORDER BY MAX(expires_at) DESC", (now,)).fetchall():
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
        pw_metrics = {"pw_enabled": False, "pw_stream_state": None, "pw_active_candidates": 0,
                      "pw_matches": 0, "pw_fetch_timeout": 0, "pw_fetch_dropped": 0,
                      "pw_persist_queue_depth": 0, "pw_candidates_expired": 0}
        if _table_exists(ov, "wt_worker_heartbeat"):
            hb = ov.execute(
                "SELECT last_seen, meta_json FROM wt_worker_heartbeat WHERE worker_name='ws_cascade'").fetchone()
            if hb:
                hb_age = int(time.time()) - (hb["last_seen"] or 0)
                try:
                    _hb_meta = _json.loads(hb["meta_json"] or "{}")
                    cleanup_count = int(_hb_meta.get("cleanups", 0))
                    # Use explicit lifecycle state when available; fall back to age-based heuristic.
                    cascade_state = _hb_meta.get("cascade_state")
                    if cascade_state:
                        ws_health = cascade_state  # CONNECTING/SUBSCRIBING/RECONCILING/LIVE/DEGRADED/FAILED
                        if hb_age >= 90:
                            ws_health = "STALE"    # heartbeat too old regardless of last state
                    else:
                        ws_health = "LIVE" if hb_age < 90 else "STALE"
                    pw_metrics = {
                        "pw_enabled":       bool(_hb_meta.get("pw_stream_state")),
                        "pw_stream_state":  _hb_meta.get("pw_stream_state"),
                        "pw_active_candidates": _hb_meta.get("pw_active_candidates", 0),
                        "pw_matches":       _hb_meta.get("pw_matches", 0),
                        "pw_fetch_timeout": _hb_meta.get("pw_fetch_timeout", 0),
                        "pw_fetch_dropped": _hb_meta.get("pw_fetch_dropped", 0),
                        "pw_persist_queue_depth": _hb_meta.get("pw_persist_queue_depth", 0),
                        "pw_candidates_expired": _hb_meta.get("pw_candidates_expired", 0),
                    }
                except Exception:
                    ws_health = "LIVE" if hb_age < 90 else "STALE"
    finally:
        ov.close()
    # Phase E: 6-way classification breakdown from wt_discovered_subprovs
    subprov_type_counts = {}
    open_reason_counts = {}
    try:
        ov2 = _conn()
        try:
            if _table_exists(ov2, "wt_discovered_subprovs"):
                for row in ov2.execute(
                    "SELECT COALESCE(subprov_type,'UNKNOWN') as t, COUNT(*) FROM wt_discovered_subprovs GROUP BY t"
                ).fetchall():
                    subprov_type_counts[row[0]] = row[1]
            if _table_exists(ov2, "wt_active_subprov_sessions"):
                for row in ov2.execute(
                    "SELECT COALESCE(open_reason,'PROVISION_CANDIDATE') as r, COUNT(*) "
                    "FROM wt_active_subprov_sessions WHERE state='ACTIVE' GROUP BY r"
                ).fetchall():
                    open_reason_counts[row[0]] = row[1]
        finally:
            ov2.close()
    except Exception:
        pass
    # Pass subscription instrumentation fields from heartbeat meta_json to the dashboard
    sub_meta = {}
    try:
        _hb_meta_ref = _hb_meta if 'hb' in dir() and hb else {}
        for k in ("reconnect_gen", "subs_sent_total", "subs_conf_total", "sub_rate",
                  "sub_ack_count", "sub_avg_ack_ms", "sub_p95_ack_ms", "sub_max_ack_ms",
                  "sub_p0_count",
                  "sub_p0_avg_send_delay_ms", "sub_p0_max_send_delay_ms", "sub_p0_p95_send_delay_ms",
                  "sub_p0_avg_ack_ms", "sub_p0_max_ack_ms", "sub_p0_p95_ack_ms",
                  "sub_p0_recent",
                  "pending", "pending_hot", "pending_subprov", "pending_treasury",
                  "pending_candidate",
                  # X27.7 — cold-subscription starvation visibility
                  "subprov_ws_sig_seen", "cold_sub_stale_sec",
                  "cold_retry_active", "cold_retry_exhausted",
                  # X24.8 — per-kind sent/confirmed/exhausted breakdown
                  "sub_kind_breakdown",
                  # X24.9 — subscription target validation
                  "invalid_subscription_targets", "invalid_targets_by_source",
                  "startup_validation_failures", "startup_validation_by_source",
                  "runtime_validation_failures"):
            if k in _hb_meta:
                sub_meta[k] = _hb_meta[k]
    except Exception:
        pass
    return jsonify({
        "sessions": sessions, "watches_by_subprov": watches,
        "candidate_count": total_watching, "active_subprovs": len(sessions),
        "latest_launch": ll, "launches_total": launches_total,
        "ws_health": ws_health, "heartbeat_age_s": hb_age, "cleanup_count": cleanup_count,
        "last_wrap_close": last_wrap, "last_create": last_create,
        "pw": pw_metrics,
        "subprov_type_counts": subprov_type_counts,
        "open_reason_counts": open_reason_counts,
        **sub_meta,
    })


_SUPERVISORD_CONF = os.path.join(_REPO_ROOT, "config", "supervisor", "supervisord.conf")
_ARMED_FLAGS = {
    "WS_PROGRAM_CREATE_WATCHER_ENABLED": "1",
    "WS_SAVE_CANDIDATE_FANOUT": "1",
}

def _armed_enabled() -> bool:
    try:
        return open(_ARMED_STATE_FILE).read().strip() == "1"
    except Exception:
        return False

_ARMED_STATE_FILE = os.path.join(_REPO_ROOT, "database", "armed_mode.txt")

def _set_armed(enabled: bool):
    """Write armed state to a file the cascade reads on startup, then SIGTERM the cascade.
    Supervisord restarts it automatically; the new process reads the file — no supervisord
    reload needed, so gunicorn stays up and the HTTP response returns normally."""
    import subprocess
    open(_ARMED_STATE_FILE, "w").write("1" if enabled else "0")
    subprocess.run(["pkill", "-TERM", "-f", "ws_cascade"], capture_output=True)


@ops_dashboard_bp.route("/api/ops/armed-mode", methods=["GET"])
def api_armed_mode_get():
    return jsonify({"armed": _armed_enabled()})


@ops_dashboard_bp.route("/api/ops/armed-mode", methods=["POST"])
def api_armed_mode_set():
    data = request.get_json(silent=True) or {}
    if "armed" not in data:
        return jsonify({"ok": False, "error": "armed field required"}), 400
    _set_armed(bool(data["armed"]))
    return jsonify({"ok": True, "armed": bool(data["armed"])})


def _ops_dismiss_write(sql, params=()):
    """Submit one operations mutation to the shared transaction owner."""
    from src.core.ws_cascade_store import OPS_DB_PATH
    from src.core.database_write_service import database_write_service
    import os as _os
    database = f"operations:{_os.path.realpath(OPS_DB_PATH)}"
    database_write_service.register_database(database, OPS_DB_PATH)
    def transaction(conn):
        cur = conn.execute(sql, params)
        return cur.rowcount
    return database_write_service.submit(database, "operations-dashboard-dismiss", transaction)


@ops_dashboard_bp.route("/api/ops-v2/intel/dismiss-all-sessions", methods=["POST"])
def api_dismiss_all_sessions():
    """Expire ALL active sessions + their candidates immediately."""
    import time as _t
    try:
        now = int(_t.time())
        n = _ops_dismiss_write(
            "UPDATE wt_active_subprov_sessions SET expires_at=0, closed_at=? WHERE state='ACTIVE'",
            (now,))
        # Expire all WATCHING candidates so ProgramWatcher closes the CREATE stream
        _ops_dismiss_write(
            "UPDATE wt_wrap_close_candidates SET state='EXPIRED' WHERE state='WATCHING'")
        _ops_dismiss_write(
            "UPDATE wt_candidate_websocket_watches SET state='EXPIRED', close_reason='dismissed', closed_at=? WHERE state='WATCHING'",
            (now,))
        return jsonify({"ok": True, "dismissed": n})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@ops_dashboard_bp.route("/api/ops-v2/intel/dismiss-session", methods=["POST"])
def api_dismiss_session():
    """Expire a LIVE_ARMED session immediately. The cascade cleanup loop (every 5s) will
    unsubscribe the wallet and, if no sessions remain, drain the ProgramWatcher stream."""
    from src.core.ws_cascade_store import OPS_DB_PATH
    import sqlite3 as _sq, time as _t
    data = request.get_json(silent=True) or {}
    subprov = (data.get("subprov") or "").strip()
    if not subprov:
        return jsonify({"ok": False, "error": "subprov required"}), 400
    # Read the session id first (read-only, never blocked)
    conn = _sq.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True, timeout=10)
    try:
        row = conn.execute(
            "SELECT id FROM wt_active_subprov_sessions "
            "WHERE subprov_wallet=? AND state='ACTIVE' LIMIT 1", (subprov,)).fetchone()
    finally:
        conn.close()
    if not row:
        return jsonify({"ok": False, "error": "no active session found"}), 404
    try:
        now = int(_t.time())
        _ops_dismiss_write(
            "UPDATE wt_active_subprov_sessions SET expires_at=0, closed_at=? WHERE id=?",
            (now, row[0]))
        # Expire candidates from this subprov so ProgramWatcher removes them
        _ops_dismiss_write(
            "UPDATE wt_wrap_close_candidates SET state='EXPIRED' WHERE subprov_wallet=? AND state='WATCHING'",
            (subprov,))
        _ops_dismiss_write(
            "UPDATE wt_candidate_websocket_watches SET state='EXPIRED', close_reason='dismissed', closed_at=? WHERE subprov_wallet=? AND state='WATCHING'",
            (now, subprov))
        return jsonify({"ok": True, "subprov": subprov, "session_id": row[0]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@ops_dashboard_bp.route("/api/ops-v2/intel/token-lifecycle")
def api_intel_token_lifecycle():
    """Operations Ledger — merged session + token lifecycle view.

    Returns ARMED (LIVE_ARMED sessions without a mint), EXPIRED (closed sessions
    without a launch), and LAUNCHED/MIGRATED/RECYCLED token rows in a single
    chronological stream. The full lifecycle: ARMED → LAUNCHED → MIGRATED → RECYCLED.
    """
    ov = _conn()
    try:
        from src.core.ws_cascade_store import get_lifecycle_rows, ensure_cascade_schema
        ensure_cascade_schema(ov)
        rows = get_lifecycle_rows(ov, limit=100)
        state_counts: dict = {}
        for r in rows:
            state_counts[r["lifecycle_state"]] = state_counts.get(r["lifecycle_state"], 0) + 1
        armed_count   = state_counts.get("ARMED", 0)
        expired_count = state_counts.get("EXPIRED", 0)
        launched = sum(state_counts.get(s, 0) for s in ("LAUNCHED", "MIGRATED", "RECYCLED"))
        migrated  = state_counts.get("MIGRATED", 0) + state_counts.get("RECYCLED", 0)
        return jsonify({
            "lifecycle": rows,
            "summary": state_counts,
            "total": len(rows),
            "kpis": {
                "armed": armed_count,
                "expired": expired_count,
                "launched": launched,
                "migrated": migrated,
                "conversion_pct": round(launched / (armed_count + launched) * 100)
                                  if (armed_count + launched) else None,
            },
        })
    except Exception as e:
        return jsonify({"lifecycle": [], "summary": {}, "total": 0, "error": str(e)})
    finally:
        ov.close()


@ops_dashboard_bp.route("/api/ops-v2/intel/detection-reconciliation")
def api_intel_detection_reconciliation():
    """X24.1 Phase 4/5 — classifies every walkback-confirmed launch as
    LIVE_DETECTED / RECONCILED / WALKBACK_RECOVERED / PIPELINE_INCONSISTENCY.
    Read-only, never writes wt_watchtower_launches. See
    src/ops/detection_reconciliation.py for the classification rules."""
    try:
        from src.ops.detection_reconciliation import classify_walkback_confirmed_launches
        return jsonify(classify_walkback_confirmed_launches())
    except Exception as e:
        return jsonify({"rows": [], "summary": {}, "total": 0, "error": str(e)}), 500


@ops_dashboard_bp.route("/api/ops-v2/intel/detection-path-health")
def api_intel_detection_path_health():
    """X24.2 Phase 5 — how each recent WATCHTOWER launch was armed/detected,
    bucketed into primary-live / catch-up / retry-recovery / manual, plus the
    walkback-only-or-pipeline-gap count for the same window. Read-only,
    measured baseline only — no target percentages. See
    src/ops/detection_path_health.py."""
    try:
        from src.ops.detection_path_health import detection_path_health
        return jsonify(detection_path_health())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@ops_dashboard_bp.route("/api/ops-v2/intel/sweep-coverage")
def api_intel_sweep_coverage():
    """X24.2 Phase 1 — live snapshot of subprov_sweep_pass() fairness/coverage:
    eligible sessions, never-swept count, sessions expiring soon that have
    never been swept, and how many were swept within the last 30s. Read-only,
    computed from the durable last_swept_at/sweep_count columns so it reflects
    real accumulated state, not just the last in-memory cycle."""
    try:
        from src.core import ws_cascade_store as store
        conn = store.db_connect(store.OPS_DB_PATH, timeout=10)
        try:
            cap = int(os.environ.get("WS_MAX_ACTIVE_SUBPROVS", "10"))
            return jsonify(store.sweep_coverage_snapshot(conn, cap=cap))
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@ops_dashboard_bp.route("/api/ops-v2/intel/launch-audit")
def api_intel_launch_audit():
    """Launch Audit panel — is WATCHTOWER detection ACTIONABLE? Per detected launch: detection
    latency, our entry position, MC at detection, peak MC, the headline actionable_multiple
    (peak_mc / mc_at_detection), time-to-peak, outcome. Plus the aggregate report (medians +
    multiple buckets). Read-only over wt_launch_audit (the audit pipeline owns the writes)."""
    ov = _conn()
    try:
        if not _table_exists(ov, "wt_launch_audit"):
            return jsonify({"launches": [], "report": None, "latency_breakdown": None})
        try:
            from src.core import launch_audit as _launch_audit
            _launch_audit.ensure_audit_schema(ov)
        except Exception:
            pass
        rows = ov.execute(
            "SELECT mint, creator, treasury, subprov, create_time, detection_latency_ms, "
            "program_log_context_slot, tx_slot, slot_lag, estimated_slot_to_ws_ms, "
            "program_fetch_duration_ms, handoff_to_canonical_ms, canonical_fetch_skipped, "
            "duplicate_fetch_count, "
            "block_time_to_ws_seen_ms, ws_seen_to_fetch_start_ms, fetch_duration_ms, "
            "fetch_to_mint_extract_ms, mint_extract_to_db_start_ms, db_commit_duration_ms, "
            "db_commit_to_alert_ms, total_ws_to_commit_ms, total_block_time_to_commit_ms, "
            "our_possible_buy_index_estimate, first_external_buy_slot, mc_at_create, "
            "mc_at_detection, mc_at_first_external_buy, peak_mc, current_mc, "
            "actionable_multiple, time_to_peak_s, migrated, dumped_before_migration, "
            "final_state, audit_state, mc_at_detection_source, peak_mc_source, source, "
            "create_signature "
            "FROM wt_launch_audit ORDER BY created_at DESC LIMIT 50").fetchall()
        # the FULL funding profile per launch: launch ledger + aggregated session data.
        # Session data is aggregated (SUM/MAX) so multi-session subprovs are handled correctly —
        # a GROUP BY wl.mint with no ORDER picks arbitrary rows for non-aggregated columns.
        funding = {}
        try:
            for fr in ov.execute(
                "SELECT wl.mint, wl.subprov_wallet, wl.subprov_funding_sol, wl.wrap_close_sol, "
                "wl.detection_source, "
                "SUM(s.topup_count) AS topup_count, "
                "SUM(s.topup_amount_total) AS topup_amount_total, "
                "MAX(s.last_topup_at) AS last_topup_at, "
                "MIN(s.funding_time) AS session_funded_at "
                "FROM wt_watchtower_launches wl "
                "LEFT JOIN wt_active_subprov_sessions s ON s.subprov_wallet = wl.subprov_wallet "
                "WHERE wl.mint IS NOT NULL "
                "GROUP BY wl.mint").fetchall():
                funding[fr["mint"]] = {
                    "subprov": fr["subprov_wallet"],
                    "subprov_funding_sol": fr["subprov_funding_sol"],
                    "wrap_close_sol": fr["wrap_close_sol"],
                    "detection_source": fr["detection_source"],
                    "topup_count": fr["topup_count"] or 0,
                    "topup_amount_total": fr["topup_amount_total"] or 0,
                    "last_topup_at": fr["last_topup_at"],
                    "session_funded_at": fr["session_funded_at"],
                }
        except Exception:
            pass
        include_events = request.args.get("include_events") == "1"
        # per-mint events index — only built when requested (zero cost otherwise)
        events_by_mint: dict = {}
        if include_events:
            # build a unified event stream per mint from three tables, sorted by timestamp
            try:
                # 1. Creator seed: the wrap-close that funded each creator
                for ev in ov.execute(
                    "SELECT se.creator_wallet, se.amount_sol, se.observed_at, "
                    "       se.wrap_close_sig, se.funding_mechanism, "
                    "       wl.mint, wl.subprov_wallet, wl.treasury_wallet "
                    "FROM wt_subprov_evidence se "
                    "JOIN wt_watchtower_launches wl ON wl.creator_wallet = se.creator_wallet "
                    "WHERE se.create_fired = 1"
                ).fetchall():
                    events_by_mint.setdefault(ev["mint"], []).append({
                        "type": "CREATOR_SEED",
                        "timestamp": ev["observed_at"],
                        "amount_sol": ev["amount_sol"],
                        "mechanism": ev["funding_mechanism"] or "WSOL_WRAP_CLOSE",
                        "from": ev["subprov_wallet"],
                        "to": ev["creator_wallet"],
                        "sig": ev["wrap_close_sig"],
                    })
                # 2a. Initial session funding transfer (first treasury→subprov transfer that
                #     opened the session — lives only in wt_active_subprov_sessions)
                for ev in ov.execute(
                    "SELECT s.subprov_wallet, s.treasury_wallet, s.funding_amount, "
                    "       s.funding_time, s.funding_signature, wl.mint "
                    "FROM wt_active_subprov_sessions s "
                    "JOIN wt_watchtower_launches wl ON wl.subprov_wallet = s.subprov_wallet "
                    "WHERE s.funding_signature IS NOT NULL"
                ).fetchall():
                    events_by_mint.setdefault(ev["mint"], []).append({
                        "type": "TREASURY_INITIAL",
                        "timestamp": ev["funding_time"],
                        "amount_sol": ev["funding_amount"],
                        "mechanism": "PLAIN_TRANSFER",
                        "from": ev["treasury_wallet"],
                        "to": ev["subprov_wallet"],
                        "sig": ev["funding_signature"],
                    })
                # 2b. Treasury top-ups recorded in wt_subprov_topups
                for ev in ov.execute(
                    "SELECT st.subprov, st.treasury, st.amount_sol, st.recorded_at, st.sig, "
                    "       wl.mint "
                    "FROM wt_subprov_topups st "
                    "JOIN wt_watchtower_launches wl ON wl.subprov_wallet = st.subprov"
                ).fetchall():
                    events_by_mint.setdefault(ev["mint"], []).append({
                        "type": "TREASURY_TOPUP",
                        "timestamp": ev["recorded_at"],
                        "amount_sol": ev["amount_sol"],
                        "mechanism": "PLAIN_TRANSFER",
                        "from": ev["treasury"],
                        "to": ev["subprov"],
                        "sig": ev["sig"],
                    })
                # 3. Capital reloads — include initial provisioning transfers
                #    (PLAIN_TRANSFER_NEW_SUBPROV) as well as reloads; exclude only
                #    internal wrap-close activity that isn't a treasury→subprov transfer
                for ev in ov.execute(
                    "SELECT cr.subprov, cr.treasury, cr.amount_sol, cr.block_time, cr.sig, "
                    "       cr.enrolment_reason, cr.linked_mint, wl.mint "
                    "FROM wt_capital_reloads cr "
                    "JOIN wt_watchtower_launches wl ON wl.subprov_wallet = cr.subprov "
                    "WHERE cr.enrolment_reason NOT IN ('WRAP_CLOSE_SEED','WSOL_WRAP_CLOSE') "
                    "   OR cr.enrolment_reason IS NULL"
                ).fetchall():
                    target_mint = ev["linked_mint"] or ev["mint"]
                    reason = ev["enrolment_reason"] or "UNKNOWN"
                    ev_type = "TREASURY_INITIAL" if "NEW_SUBPROV" in reason else "TREASURY_RELOAD"
                    events_by_mint.setdefault(target_mint, []).append({
                        "type": ev_type,
                        "timestamp": ev["block_time"],
                        "amount_sol": ev["amount_sol"],
                        "mechanism": reason,
                        "from": ev["treasury"],
                        "to": ev["subprov"],
                        "sig": ev["sig"],
                    })
                # deduplicate by sig (same transfer may appear in sessions + capital_reloads)
                # then sort each mint's events by timestamp ascending
                for mint_key in events_by_mint:
                    seen_sigs: set = set()
                    deduped = []
                    for e in events_by_mint[mint_key]:
                        sig = e.get("sig")
                        if sig and sig in seen_sigs:
                            continue
                        if sig:
                            seen_sigs.add(sig)
                        deduped.append(e)
                    events_by_mint[mint_key] = sorted(deduped, key=lambda e: e["timestamp"] or 0)
            except Exception as _ev_e:
                events_by_mint = {}

        # sibling wallets: other wallets funded by the same treasury within ±120s of the
        # launch subprov's initial funding — reveals concurrent buy-swarm + payment legs
        siblings_by_mint: dict = {}
        if include_events:
            try:
                for fr in ov.execute(
                    "SELECT wl.mint, wl.subprov_wallet, wl.treasury_wallet, "
                    "       s.funding_time "
                    "FROM wt_watchtower_launches wl "
                    "JOIN wt_active_subprov_sessions s ON s.subprov_wallet = wl.subprov_wallet "
                    "WHERE wl.mint IS NOT NULL AND s.funding_time IS NOT NULL"
                ).fetchall():
                    mint = fr["mint"]
                    treasury = fr["treasury_wallet"]
                    t0 = fr["funding_time"]
                    subprov = fr["subprov_wallet"]
                    if not treasury or not t0:
                        continue
                    # all other wallets funded by same treasury in ±120s window
                    sibs = ov.execute(
                        "SELECT h.counterparty AS wallet, SUM(h.amount_sol) AS total_sol, "
                        "       MIN(h.block_time) AS first_seen, "
                        "       (SELECT COUNT(*) FROM wt_subprov_evidence se "
                        "        WHERE se.subprov=h.counterparty) AS fan_out, "
                        "       (SELECT COUNT(*) FROM wt_active_subprov_sessions ss "
                        "        WHERE ss.subprov_wallet=h.counterparty) AS has_session "
                        "FROM wt_webhook_hits h "
                        "WHERE h.wallet_address=? AND h.direction IN ('OUT','outbound') "
                        "  AND h.block_time BETWEEN ? AND ? "
                        "  AND h.counterparty != ? "
                        "GROUP BY h.counterparty "
                        "ORDER BY MIN(h.block_time) ASC",
                        (treasury, t0 - 120, t0 + 120, subprov)
                    ).fetchall()
                    siblings_by_mint[mint] = []
                    for s in sibs:
                        fan_out = s["fan_out"] or 0
                        role = ("BUY_SWARM" if fan_out > 10
                                else "PAYMENT" if s["total_sol"] and s["total_sol"] < 15
                                else "SUBPROV" if s["has_session"] else "UNKNOWN")
                        siblings_by_mint[mint].append({
                            "wallet": s["wallet"],
                            "sol": s["total_sol"],
                            "first_seen": s["first_seen"],
                            "fan_out": fan_out,
                            "role": role,
                        })
                        # inject individual sibling transfers into the timeline
                        sib_xfers = ov.execute(
                            "SELECT h.amount_sol, h.block_time, h.tx_signature AS sig "
                            "FROM wt_webhook_hits h "
                            "WHERE h.wallet_address=? AND h.direction IN ('OUT','outbound') "
                            "  AND h.block_time BETWEEN ? AND ? "
                            "  AND h.counterparty=? "
                            "ORDER BY h.block_time ASC",
                            (treasury, t0 - 120, t0 + 120, s["wallet"])
                        ).fetchall()
                        for xf in sib_xfers:
                            events_by_mint.setdefault(mint, []).append({
                                "type": "TREASURY_SIBLING_XFER",
                                "timestamp": xf["block_time"],
                                "amount_sol": xf["amount_sol"],
                                "from": treasury,
                                "to": s["wallet"],
                                "sibling_role": role,
                                "sig": xf["sig"],
                            })
            except Exception as _sib_e:
                siblings_by_mint = {}

        # operation_uuid + op_status + live count per operation_uuid
        op_uuid_by_mint: dict = {}
        op_meta: dict = {}   # operation_uuid -> {status, live_count}
        try:
            lc_rows = ov.execute(
                "SELECT mint, operation_uuid, lifecycle_state "
                "FROM wt_token_lifecycle WHERE operation_uuid IS NOT NULL"
            ).fetchall()
            for lc in lc_rows:
                op_uuid_by_mint[lc["mint"]] = lc["operation_uuid"]
                m = op_meta.setdefault(lc["operation_uuid"], {"status": None, "live_count": 0})
                if lc["lifecycle_state"] == "LAUNCHED":
                    m["live_count"] += 1
        except Exception:
            pass
        # pull operation lifecycle status for each known operation_uuid
        if op_meta:
            try:
                placeholders = ",".join("?" * len(op_meta))
                for ol in ov.execute(
                    f"SELECT operation_uuid, state FROM wt_operation_lifecycle "
                    f"WHERE operation_uuid IN ({placeholders})",
                    list(op_meta.keys())
                ).fetchall():
                    op_meta[ol["operation_uuid"]]["status"] = ol["state"]
            except Exception:
                pass

        launches = [{
            "mint": r["mint"], "creator": r["creator"], "treasury": r["treasury"],
            "subprov": r["subprov"] or (funding.get(r["mint"]) or {}).get("subprov"),
            "subprov_wallet": r["subprov"] or (funding.get(r["mint"]) or {}).get("subprov"),
            "operation_uuid": op_uuid_by_mint.get(r["mint"]),
            "op_status": op_meta.get(op_uuid_by_mint.get(r["mint"]), {}).get("status"),
            "op_live_count": op_meta.get(op_uuid_by_mint.get(r["mint"]), {}).get("live_count", 0),
            "create_time": r["create_time"], "detection_latency_ms": r["detection_latency_ms"],
            "program_log_context_slot": r["program_log_context_slot"],
            "tx_slot": r["tx_slot"],
            "slot_lag": r["slot_lag"],
            "estimated_slot_to_ws_ms": r["estimated_slot_to_ws_ms"],
            "program_fetch_duration_ms": r["program_fetch_duration_ms"],
            "handoff_to_canonical_ms": r["handoff_to_canonical_ms"],
            "canonical_fetch_skipped": bool(r["canonical_fetch_skipped"]) if r["canonical_fetch_skipped"] is not None else None,
            "duplicate_fetch_count": r["duplicate_fetch_count"],
            "block_time_to_ws_seen_ms": r["block_time_to_ws_seen_ms"],
            "ws_seen_to_fetch_start_ms": r["ws_seen_to_fetch_start_ms"],
            "fetch_duration_ms": r["fetch_duration_ms"],
            "fetch_to_mint_extract_ms": r["fetch_to_mint_extract_ms"],
            "mint_extract_to_db_start_ms": r["mint_extract_to_db_start_ms"],
            "db_commit_duration_ms": r["db_commit_duration_ms"],
            "db_commit_to_alert_ms": r["db_commit_to_alert_ms"],
            "total_ws_to_commit_ms": r["total_ws_to_commit_ms"],
            "total_block_time_to_commit_ms": r["total_block_time_to_commit_ms"],
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
            "create_signature": r["create_signature"],
            "detection_source": (funding.get(r["mint"]) or {}).get("detection_source"),
            "subprov_funding_sol": (funding.get(r["mint"]) or {}).get("subprov_funding_sol"),
            "wrap_close_sol": (funding.get(r["mint"]) or {}).get("wrap_close_sol"),
            "topup_count": (funding.get(r["mint"]) or {}).get("topup_count", 0),
            "topup_amount_total": (funding.get(r["mint"]) or {}).get("topup_amount_total", 0),
            "last_topup_at": (funding.get(r["mint"]) or {}).get("last_topup_at"),
            "session_funded_at": (funding.get(r["mint"]) or {}).get("session_funded_at"),
            **({"events": events_by_mint.get(r["mint"], [])} if include_events else {}),
            **({"siblings": siblings_by_mint.get(r["mint"], [])} if include_events else {}),
        } for r in rows]
        def _median(vals):
            vals = sorted(v for v in vals if v is not None)
            if not vals:
                return None
            mid = len(vals) // 2
            return vals[mid] if len(vals) % 2 else int((vals[mid - 1] + vals[mid]) / 2)
        def _p95(vals):
            vals = sorted(v for v in vals if v is not None)
            if not vals:
                return None
            return vals[min(len(vals) - 1, int((len(vals) - 1) * 0.95))]
        slowest = None
        with_total = [x for x in launches if x.get("total_ws_to_commit_ms") is not None]
        if with_total:
            slowest = max(with_total, key=lambda x: x.get("total_ws_to_commit_ms") or 0)
            slowest = {
                "mint": slowest.get("mint"),
                "creator": slowest.get("creator"),
                "total_ws_to_commit_ms": slowest.get("total_ws_to_commit_ms"),
                "fetch_duration_ms": slowest.get("program_fetch_duration_ms") or slowest.get("fetch_duration_ms"),
                "db_commit_duration_ms": slowest.get("db_commit_duration_ms"),
            }
        _fetch_ms = [
            (x.get("program_fetch_duration_ms")
             if x.get("program_fetch_duration_ms") is not None
             else x.get("fetch_duration_ms"))
            for x in launches
        ]
        latency_breakdown = {
            "median_ws_to_commit_ms": _median([x.get("total_ws_to_commit_ms") for x in launches]),
            "median_rpc_fetch_ms": _median(_fetch_ms),
            "median_db_commit_ms": _median([x.get("db_commit_duration_ms") for x in launches]),
            "p95_ws_to_commit_ms": _p95([x.get("total_ws_to_commit_ms") for x in launches]),
            "slowest_recent_launch": slowest,
        }
    finally:
        ov.close()
    # aggregate report (reuse the module's own logic so it stays one source of truth)
    report = None
    try:
        from src.core import launch_audit
        report = launch_audit.outcome_report()
    except Exception:
        report = None
    return jsonify({"launches": launches, "report": report,
                    "latency_breakdown": latency_breakdown})


@ops_dashboard_bp.route("/api/ops-v2/intel/launch-audit-reconcile", methods=["POST"])
def api_intel_launch_audit_reconcile():
    """In-process reconcile: finds wt_watchtower_launches rows missing from wt_launch_audit
    (or in FAILED/stale-PENDING state) and re-runs audit capture for each. Runs in a background
    thread so the HTTP response returns immediately (reconcile can take 60-90s under write-lane
    contention — longer than the gunicorn worker timeout if run synchronously)."""
    import threading
    def _run():
        try:
            from src.core import launch_audit as _la
            result = _la.reconcile_missing()
            print(f"[LAUNCH_AUDIT] in-process reconcile complete: {result}", flush=True)
        except Exception as e:
            import traceback
            print(f"[LAUNCH_AUDIT] in-process reconcile error: {e}\n{traceback.format_exc()}", flush=True)
    threading.Thread(target=_run, daemon=True, name="launch-audit-reconcile").start()
    return jsonify({"status": "reconcile started in background — check server logs for result"})


@ops_dashboard_bp.route("/api/ops-v2/intel/capital-reloads")
def api_intel_capital_reloads():
    """Mission 2: Capital Deployment panel — UNRESOLVED capital movements only.
    Rows with operation_uuid are attributed to a campaign and appear in the Operations Ledger
    timeline instead. This endpoint returns only orphaned / pre-campaign capital intelligence.
    ?all=1 returns everything (attributed + unresolved) for debugging."""
    import time as _time
    _now_cr = int(_time.time())
    _show_all = request.args.get("all") == "1"
    def _cr_ago(ts):
        if not ts: return None
        s = _now_cr - int(ts)
        if s < 60: return f"{s}s"
        if s < 3600: return f"{s//60}m"
        if s < 86400: return f"{s//3600}h"
        return f"{s//86400}d"
    ov = _conn()
    try:
        if not _table_exists(ov, "wt_capital_reloads"):
            return jsonify({"reloads": [], "total_sol": 0})
        _op_filter = "" if _show_all else "AND cr.operation_uuid IS NULL"
        rows = ov.execute(
            f"SELECT cr.subprov, cr.treasury, cr.sig, cr.amount_sol, cr.wrap_close_count, "
            "       cr.first_creator, cr.linked_mint, cr.recorded_at, "
            "       COALESCE(cr.enrolment_reason,'WRAP_CLOSE_RELOAD') AS enrolment_reason, "
            "       COALESCE(cr.block_time, cr.recorded_at) AS block_time, "
            "       COALESCE(cr.session_opened,0) AS session_opened, "
            "       cr.operation_uuid, "
            "       s.monitoring_state, s.state AS session_state, "
            "       d.wrap_close_count AS live_wcc, d.create_count AS live_creators "
            "FROM wt_capital_reloads cr "
            "LEFT JOIN wt_active_subprov_sessions s "
            "       ON s.subprov_wallet=cr.subprov AND s.state='ACTIVE' "
            "LEFT JOIN wt_discovered_subprovs d ON d.subprov=cr.subprov "
            f"WHERE 1=1 {_op_filter} "
            "ORDER BY cr.recorded_at DESC LIMIT 100"
        ).fetchall()
        # Totals across ALL rows (not just UNRESOLVED) for the headline stats
        all_rows = ov.execute(
            "SELECT amount_sol, enrolment_reason, operation_uuid "
            "FROM wt_capital_reloads ORDER BY recorded_at DESC LIMIT 500"
        ).fetchall()
        reloads = []
        for r in rows:
            (subprov, treasury, sig, amount_sol, wcc, first_creator, linked_mint, recorded_at,
             enrolment_reason, block_time, session_opened, operation_uuid,
             monitoring_state, session_state, live_wcc, live_creators) = r
            reloads.append({
                "subprov": subprov,
                "subprov_short": subprov[:10] + "…" if subprov else "",
                "treasury": treasury,
                "treasury_short": treasury[:10] + "…" if treasury else "",
                "sig": sig,
                "amount_sol": round(amount_sol or 0, 2),
                "wrap_close_count": wcc or 0,
                "live_wcc": live_wcc or 0,
                "live_creators": live_creators or 0,
                "first_creator": first_creator,
                "linked_mint": linked_mint,
                "recorded_at": recorded_at,
                "ago": _cr_ago(recorded_at),
                "enrolment_reason": enrolment_reason or "WRAP_CLOSE_RELOAD",
                "is_plain_transfer": (enrolment_reason or "").startswith("PLAIN_TRANSFER"),
                "session_opened": bool(session_opened),
                "operation_uuid": operation_uuid,
                "monitoring_state": monitoring_state,
                "session_state": session_state,
            })
        total_sol = sum((r[0] or 0) for r in all_rows)
        plain_count = sum(1 for r in all_rows if (r[1] or "").startswith("PLAIN_TRANSFER"))
        attributed_count = sum(1 for r in all_rows if r[2] is not None)
        unresolved_count = sum(1 for r in all_rows if r[2] is None)
        return jsonify({
            "reloads": reloads,
            "total_sol": round(total_sol, 2),
            "plain_transfer_count": plain_count,
            "reload_count": len(reloads),
            "attributed_count": attributed_count,
            "unresolved_count": unresolved_count,
            "total_event_count": len(all_rows),
        })
    except Exception as e:
        return jsonify({"reloads": [], "total_sol": 0, "error": str(e)})
    finally:
        ov.close()


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
    action='remove'  → clear the funder off the subprov (treasury=NULL,
                       treasury_known=0) → back to an UNKNOWN lead. Does NOT touch the
                       treasury itself (it may fund other subprovs).
    action='dismiss' → mark state='dismissed' so the subprov no longer appears in the
                       UNKNOWN leads list. Reversible (state can be reset)."""
    body = request.get_json(silent=True) or {}
    if body.get("confirm") is not True or not body.get("subprov"):
        return jsonify({"error": "confirmation + subprov required"}), 400
    sp = body["subprov"].strip()
    action = body.get("action", "set")
    ov = _conn_rw()  # POST handler: writes wt_discovered_subprovs / confirmed_treasuries
    try:
        if not _table_exists(ov, "wt_discovered_subprovs"):
            return jsonify({"error": "wt_discovered_subprovs missing"}), 400
        exists = ov.execute("SELECT 1 FROM wt_discovered_subprovs WHERE subprov=?", (sp,)).fetchone()
        if not exists:
            return jsonify({"error": f"subprov {sp[:12]}… not found"}), 404

        if action == "dismiss":
            ov.execute("UPDATE wt_discovered_subprovs SET state='dismissed' WHERE subprov=?", (sp,))
            ov.commit()
            ov.close()
            return jsonify({"ok": True, "action": "dismiss", "subprov": sp})

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
        from src.ops.watchtower_alignment import reconcile_confirmed_treasury
        reconcile_confirmed_treasury(ov, treasury)
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
                    if _attempt < 2:
                        _t.sleep(1.5 * (_attempt + 1))
                        continue
                    break  # exhausted retries — webhook_error set, webhooked=False, return ok anyway
            oc = _conn_rw()  # writes wt_confirmed_treasury_webhooks
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
    ov = _conn()
    try:
        rows = treasury_bank.confirmed_treasuries(ov)
        # enrich with last-hit / last-fanout from webhook hits (ops DB only)
        addrs = [r["treasury"] for r in rows]
        last_hit = {}
        last_outbound = {}
        last_outbound_type: dict = {}
        last_outbound_sig: dict = {}
        last_outbound_sol: dict = {}
        if addrs:
            ph = ",".join("?" * len(addrs))
            # wt_webhook_hits lives only in the ops DB (cascade WS hits) — skip live DB entirely.
            try:
                for w, mx in ov.execute(
                    f"SELECT wallet_address, MAX(block_time) FROM wt_webhook_hits "
                    f"WHERE wallet_address IN ({ph}) GROUP BY wallet_address", addrs).fetchall():
                    if mx:
                        last_hit[w] = max(last_hit.get(w) or 0, mx)
                for w, mx, tx_type, sig, sol in ov.execute(
                    f"SELECT wallet_address, block_time, tx_type, tx_signature, amount_sol FROM wt_webhook_hits h1 "
                    f"WHERE direction='outbound' AND wallet_address IN ({ph}) "
                    f"  AND block_time = (SELECT MAX(h2.block_time) FROM wt_webhook_hits h2 "
                    f"                    WHERE h2.wallet_address=h1.wallet_address AND h2.direction='outbound') "
                    f"GROUP BY wallet_address",
                    addrs).fetchall():
                    if mx and mx > (last_outbound.get(w) or 0):
                        last_outbound[w] = mx
                        last_outbound_type[w] = tx_type
                        last_outbound_sig[w] = sig
                        last_outbound_sol[w] = sol
            except Exception:
                pass
        # Best downstream subprov activity per treasury (fills the gap for CONFIRMED_SUBPROV_TRACE
        # treasuries that were enrolled yesterday and have 0 webhook hits yet).
        max_subprov_ts: dict = {}
        try:
            if addrs:
                ph2 = ",".join("?" * len(addrs))
                for w, mx in ov.execute(
                    f"SELECT treasury, MAX(last_seen) FROM wt_discovered_subprovs "
                    f"WHERE treasury IN ({ph2}) GROUP BY treasury", addrs).fetchall():
                    if mx:
                        max_subprov_ts[w] = mx
        except Exception:
            pass
        # Last confirmed launch per treasury
        last_launch_ts: dict = {}
        last_launch_mint: dict = {}
        last_launch_sig: dict = {}
        try:
            if addrs and _table_exists(ov, "wt_watchtower_launches"):
                ph3 = ",".join("?" * len(addrs))
                for w, mx, mint, sig in ov.execute(
                    f"SELECT treasury_wallet, MAX(create_time), "
                    f"  (SELECT mint FROM wt_watchtower_launches l2 "
                    f"   WHERE l2.treasury_wallet=l.treasury_wallet "
                    f"   ORDER BY create_time DESC LIMIT 1), "
                    f"  (SELECT create_signature FROM wt_watchtower_launches l3 "
                    f"   WHERE l3.treasury_wallet=l.treasury_wallet "
                    f"   ORDER BY create_time DESC LIMIT 1) "
                    f"FROM wt_watchtower_launches l "
                    f"WHERE treasury_wallet IN ({ph3}) GROUP BY treasury_wallet", addrs).fetchall():
                    if mx:
                        last_launch_ts[w] = mx
                        last_launch_mint[w] = mint
                        last_launch_sig[w] = sig
        except Exception:
            pass
        _TREASURY_SUPPRESSED = {"69SNcRC8NqjHBSXEcugCN5oFKRQoKmddmWzZYc3tqtxk"}
        rows = [r for r in rows if r["treasury"] not in _TREASURY_SUPPRESSED]
        for r in rows:
            t = r["treasury"]
            wh_ts   = last_hit.get(t) or r.get("last_hit")
            ob_ts   = last_outbound.get(t)
            sp_ts   = max_subprov_ts.get(t)
            ln_ts   = last_launch_ts.get(t)
            r["last_hit"] = wh_ts
            r["last_outbound"] = ob_ts
            r["last_outbound_sol"] = last_outbound_sol.get(t)
            # last_action = most recent outbound activity: confirmed launch or outbound WS hit.
            # Inbound hits and subprov scheduler timestamps excluded — they don't indicate
            # the treasury is provisioning.
            direct = [ts for ts in [ob_ts, ln_ts] if ts]
            r["last_action"]      = max(direct) if direct else None
            ob_type = last_outbound_type.get(t)
            is_launch = ln_ts and ln_ts == r["last_action"]
            is_mesh   = ob_ts and ob_ts == r["last_action"] and ob_type == "TREASURY_MESH"
            r["last_action_src"] = (
                "launch"   if is_launch else
                "mesh"     if is_mesh   else
                "outbound" if ob_ts and ob_ts == r["last_action"] else None
            )
            r["last_action_sig"] = (
                last_launch_sig.get(t) if is_launch else
                last_outbound_sig.get(t)
            )
            # Fallback display field: show subprov activity when no direct signal exists
            r["last_subprov_activity"] = sp_ts
        # Enrich with launches + tx_24h from coverage stats tables
        wc_by_treasury: dict = {}
        tx24_by_treasury: dict = {}
        try:
            if addrs:
                ph4 = ",".join("?" * len(addrs))
                for w, armed, det, lat in ov.execute(
                    f"SELECT treasury_wallet, SUM(armed), COUNT(*), MAX(last_seen) "
                    f"FROM wt_wrap_close_candidates WHERE treasury_wallet IN ({ph4}) GROUP BY treasury_wallet",
                    addrs).fetchall():
                    wc_by_treasury[w] = {"armed": armed or 0, "detections": det or 0, "last_at": lat}
        except Exception:
            pass
        try:
            if addrs:
                ph5 = ",".join("?" * len(addrs))
                for w, tx24 in ov.execute(
                    f"SELECT treasury, tx_24h FROM wt_ops_v2_treasury_stats WHERE treasury IN ({ph5})",
                    addrs).fetchall():
                    tx24_by_treasury[w] = tx24
        except Exception:
            pass
        # Active subprov session counts per treasury
        active_subprovs: dict = {}
        try:
            if addrs:
                ph6 = ",".join("?" * len(addrs))
                for w, cnt in ov.execute(
                    f"SELECT treasury_wallet, COUNT(*) FROM wt_active_subprov_sessions "
                    f"WHERE state='ACTIVE' AND treasury_wallet IN ({ph6}) GROUP BY treasury_wallet",
                    addrs).fetchall():
                    active_subprovs[w] = cnt
        except Exception:
            pass
        for r in rows:
            t = r["treasury"]
            wc = wc_by_treasury.get(t, {})
            r["launches"]  = wc.get("armed", 0)
            r["tx_24h"]    = tx24_by_treasury.get(t)
            r["active_subprovs"] = active_subprovs.get(t, 0)
        # Sort by most recent outbound action descending (nulls last).
        rows.sort(key=lambda r: (r["last_action"] or 0, r["last_outbound"] or 0), reverse=True)
        webhooked = sum(1 for r in rows if r["webhooked"])
        # card aggregates — use last_action (outbound, both DBs) for recency, fall back to any hit
        all_hits = [r["last_action"] or r["last_hit"] for r in rows if (r["last_action"] or r["last_hit"])]
        # Best launch across all treasuries (from wt_watchtower_launches — authoritative)
        best_launch_ts = max(last_launch_ts.values()) if last_launch_ts else None
        best_launch_mint = None
        if best_launch_ts:
            for tw, ts in last_launch_ts.items():
                if ts == best_launch_ts:
                    best_launch_mint = last_launch_mint.get(tw)
                    break
        # Fall back to webhook table fields if cascade hasn't written any launches yet
        fired = [r for r in rows if r.get("last_fired_at")]
        wh_last_fired_at = max([r["last_fired_at"] for r in fired]) if fired else None
        wh_last_fired_token = (sorted(fired, key=lambda x: x["last_fired_at"])[-1]["last_fired_token"] if fired else None)
        return jsonify({
            "treasuries": rows,
            "card": {
                "confirmed": len(rows),
                "webhooked": webhooked,
                "last_hit": max(all_hits) if all_hits else None,
                "last_fanout": max([r["last_fanout"] for r in rows if r.get("last_fanout")] or [0]) or None,
                "last_strict_candidate": max([r["last_strict_candidate"] for r in rows if r.get("last_strict_candidate")] or [0]) or None,
                "last_fired_token": best_launch_mint or wh_last_fired_token,
                "last_fired_at": best_launch_ts or wh_last_fired_at,
            },
        })
    finally:
        ov.close()


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
            oc = _conn_rw()  # writes wt_confirmed_treasury_webhooks
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


# ════════════════════════════════════════════════════════════════════════════
#  RECOVERY-SAFE APPROVAL — registry-only, zero activation side-effects.
#
#  treasury-approve  → wt_confirmed_treasuries + audit trail ONLY.
#                      NO webhook, NO op reassignment, NO subprov changes.
#  treasury-webhook-enroll → explicit second step, separate button.
#
#  The existing treasury-promote route is kept as-is (full promote+webhook
#  in one call) for use once recovery is complete. During recovery the UI
#  renders the split buttons instead.
# ════════════════════════════════════════════════════════════════════════════

def _ensure_approval_audit_table(ov):
    ov.execute("""CREATE TABLE IF NOT EXISTS wt_treasury_approval_audit (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        treasury    TEXT NOT NULL,
        action      TEXT NOT NULL,          -- APPROVED | REJECTED | WEBHOOK_ENROLLED
        reviewer    TEXT,
        confidence  TEXT,                   -- HIGH | MEDIUM | LOW
        notes       TEXT,
        evidence_json TEXT,                 -- snapshot of review evidence at approval time
        created_at  INTEGER NOT NULL
    )""")
    # Add reviewer/confidence/notes columns to wt_treasury_review if absent (migration-safe)
    for col, defn in [("reviewer", "TEXT"), ("confidence", "TEXT"), ("notes", "TEXT"),
                      ("evidence_json", "TEXT")]:
        try:
            ov.execute(f"ALTER TABLE wt_treasury_review ADD COLUMN {col} {defn}")
        except Exception:
            pass
    ov.commit()


def _compute_confidence(c: dict) -> str:
    """Derive HIGH/MEDIUM/LOW confidence from a review candidate dict."""
    score = 0
    if c.get("detected_via") == "auto_fingerprint_3of3":
        score += 3
    if (c.get("occurrences") or 0) >= 2:
        score += 2
    elif (c.get("occurrences") or 0) == 1:
        score += 1
    if (c.get("micro_pings") or 0) >= 5:
        score += 2
    elif (c.get("micro_pings") or 0) > 0:
        score += 1
    if (c.get("source_token_peak_mc") or 0) >= 100_000:
        score += 2
    elif (c.get("source_token_peak_mc") or 0) >= 25_000:
        score += 1
    if (c.get("recipients") or 0) >= 20:
        score += 1
    if (c.get("out_sol") or 0) >= 500:
        score += 1
    if score >= 6:
        return "HIGH"
    if score >= 3:
        return "MEDIUM"
    return "LOW"


@ops_dashboard_bp.route("/api/ops-v2/intel/treasury-approve", methods=["POST"])
def api_intel_treasury_approve():
    """Recovery-safe treasury approval.

    Adds the treasury to wt_confirmed_treasuries and records the decision in the
    permanent audit log.  NOTHING ELSE HAPPENS:
      - no webhook enrolment
      - no operation reassignment
      - no subprov record changes
      - no graph rebuilds
      - no background workers started

    POST { confirm:true, treasury:<addr>, action:'approve'|'reject',
           confidence:'HIGH'|'MEDIUM'|'LOW', notes:<str>, reviewer:<str> }
    """
    from src.core import treasury_bank
    import json as _json
    body = request.get_json(silent=True) or {}
    if body.get("confirm") is not True or not body.get("treasury"):
        return jsonify({"error": "confirm:true + treasury required"}), 400
    t = body["treasury"].strip()
    action = body.get("action", "approve")
    confidence = body.get("confidence", "MEDIUM")
    notes = body.get("notes", "")
    reviewer = body.get("reviewer", "human")
    now = int(time.time())

    ov = _conn_rw()
    try:
        _ensure_approval_audit_table(ov)

        # Fetch candidate evidence snapshot for the audit record
        evidence = {}
        try:
            row = ov.execute(
                "SELECT transfer_pct, out_sol, recipients, micro_pings, detected_via, "
                "detected_at FROM wt_treasury_review WHERE treasury=?", (t,)).fetchone()
            if row:
                evidence = dict(row)
        except Exception:
            pass
        evidence_json = _json.dumps(evidence)

        if action == "reject":
            # Mark rejected in review table
            ov.execute(
                "UPDATE wt_treasury_review SET status='REJECTED', reviewed_at=?, "
                "reviewed_by=?, confidence=?, notes=? WHERE treasury=?",
                (now, reviewer, confidence, notes, t))
            # Audit trail
            ov.execute(
                "INSERT INTO wt_treasury_approval_audit "
                "(treasury, action, reviewer, confidence, notes, evidence_json, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (t, "REJECTED", reviewer, confidence, notes, evidence_json, now))
            ov.commit()
            return jsonify({"ok": True, "action": "reject", "treasury": t,
                            "message": "marked REJECTED — registry unchanged, no activation"})

        # APPROVE — registry only
        # 1. Update wt_treasury_review status
        ov.execute(
            "UPDATE wt_treasury_review SET status='APPROVED', reviewed_at=?, "
            "reviewed_by=?, confidence=?, notes=? WHERE treasury=?",
            (now, reviewer, confidence, notes, t))

        # 2. Insert into wt_confirmed_treasuries (idempotent)
        ov.execute(
            "INSERT INTO wt_confirmed_treasuries "
            "(treasury, method, confidence, confirmed_at, provenance) "
            "VALUES (?, 'human_review_recovery_safe', ?, ?, 'APPROVED_NO_WEBHOOK') "
            "ON CONFLICT(treasury) DO UPDATE SET "
            "confidence=excluded.confidence, confirmed_at=excluded.confirmed_at, "
            "provenance=excluded.provenance",
            (t, confidence, now))

        # 3. Permanent audit record
        ov.execute(
            "INSERT INTO wt_treasury_approval_audit "
            "(treasury, action, reviewer, confidence, notes, evidence_json, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (t, "APPROVED", reviewer, confidence, notes, evidence_json, now))
        from src.ops.watchtower_alignment import reconcile_confirmed_treasury
        reconcile_confirmed_treasury(ov, t)
        ov.commit()

        return jsonify({
            "ok": True, "action": "approve", "treasury": t,
            "confidence": confidence, "reviewer": reviewer,
            "registry_updated": True,
            "webhooked": False,
            "message": (
                "Treasury added to confirmed registry. "
                "NO webhook enrolled — use Enroll Webhook button when ready to activate."
            ),
            "activation_needed": True,
        })
    finally:
        ov.close()


@ops_dashboard_bp.route("/api/ops-v2/intel/treasury-webhook-enroll", methods=["POST"])
def api_intel_treasury_webhook_enroll():
    """Explicit webhook enrolment — a SEPARATE step from approval.

    Only allowed for treasuries already in wt_confirmed_treasuries.
    POST { confirm:true, treasury:<addr>, reviewer:<str> }
    """
    import asyncio as _asyncio, json as _json
    body = request.get_json(silent=True) or {}
    if body.get("confirm") is not True or not body.get("treasury"):
        return jsonify({"error": "confirm:true + treasury required"}), 400
    t = body["treasury"].strip()
    reviewer = body.get("reviewer", "human")
    now = int(time.time())

    ov = _conn_rw()
    try:
        # Guard: must already be confirmed
        row = ov.execute(
            "SELECT treasury FROM wt_confirmed_treasuries WHERE treasury=?", (t,)).fetchone()
        if not row:
            return jsonify({"error": "treasury not in confirmed registry — approve first"}), 400

        _ensure_approval_audit_table(ov)

        # Enroll via WebhookManager
        webhooked = False; webhook_error = None
        try:
            from src.analysis.webhook_manager import WebhookManager, INFRA_ROLE
            for attempt in range(3):
                try:
                    loop = _asyncio.new_event_loop()
                    mgr = WebhookManager(LIVE_DB_PATH)
                    loop.run_until_complete(mgr.enroll_batch(
                        [t], role=INFRA_ROLE, notes="explicit enroll post recovery-safe approval"))
                    loop.close()
                    webhooked = True
                    break
                except Exception as _e:
                    webhook_error = str(_e)
                    if "locked" in webhook_error.lower() and attempt < 2:
                        import time as _t2; _t2.sleep(1.5 * (attempt + 1))
                    else:
                        raise
        except Exception as exc:
            webhook_error = str(exc)

        if webhooked:
            # Sync coverage table
            try:
                ov.execute(
                    "INSERT INTO wt_confirmed_treasury_webhooks "
                    "(treasury, source, enrolled_at, webhook_active) "
                    "VALUES (?, 'EXPLICIT_ENROLL', ?, 1) "
                    "ON CONFLICT(treasury) DO UPDATE SET webhook_active=1, enrolled_at=?",
                    (t, now, now))
            except Exception:
                pass
            # Audit trail
            ov.execute(
                "INSERT INTO wt_treasury_approval_audit "
                "(treasury, action, reviewer, confidence, notes, evidence_json, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (t, "WEBHOOK_ENROLLED", reviewer, None,
                 "explicit enroll — separate from approval", "{}", now))
            ov.commit()

        return jsonify({
            "ok": webhooked, "treasury": t,
            "webhooked": webhooked, "webhook_error": webhook_error,
            "message": "Webhook enrolled — treasury is now live in the detection pipeline." if webhooked
                       else f"Enrolment failed: {webhook_error}",
        })
    finally:
        ov.close()


@ops_dashboard_bp.route("/api/ops-v2/intel/treasury-approval-audit")
def api_intel_treasury_approval_audit():
    """Permanent audit trail of all approval actions."""
    ov = _conn()
    try:
        _ensure_approval_audit_table(_conn_rw())
        rows = ov.execute(
            "SELECT treasury, action, reviewer, confidence, notes, created_at "
            "FROM wt_treasury_approval_audit ORDER BY created_at DESC LIMIT 200"
        ).fetchall()
        return jsonify({"audit": [dict(r) for r in rows], "count": len(rows)})
    finally:
        ov.close()


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
    # WS liveness is driven by TREASURY WS NOTIFICATIONS (wt_treasury_ws_usage.last_notif_at),
    # NOT by wt_webhook_hits. A treasury fires a WS notification on EVERY tx, but a HIT row is
    # only written on a provisioning-sized outbound (rare) — so the old check called the WS
    # "BLIND" whenever provisioning was quiet even though 1000s of notifications were flowing.
    # last_webhook_hit / hits24 stay as the PROVISIONING signal; status reflects WS receipt.
    live = _live_conn(); ov = _conn()
    try:
        def _q1(conn, sql):
            try:
                return (conn.execute(sql).fetchone() or [None])[0]
            except Exception:
                return None
        # true WS liveness: most recent treasury WS notification (ops DB)
        last_notif = _q1(ov, "SELECT MAX(last_notif_at) FROM wt_treasury_ws_usage")
        notif_total = _q1(ov, "SELECT SUM(notif_count) FROM wt_treasury_ws_usage") or 0
        # provisioning hits (separate, rarer signal) — union ops (cascade) + live (legacy)
        last_hit = max([x for x in (_q1(live, "SELECT MAX(block_time) FROM wt_webhook_hits"),
                                    _q1(ov,   "SELECT MAX(block_time) FROM wt_webhook_hits"))
                        if x is not None], default=None)
        hits24 = (_q1(live, "SELECT COUNT(*) FROM wt_webhook_hits WHERE block_time > strftime('%s','now')-86400") or 0) \
               + (_q1(ov,   "SELECT COUNT(*) FROM wt_webhook_hits WHERE block_time > strftime('%s','now')-86400") or 0)
        _tmpl_sql = ("SELECT COUNT(*) FROM wt_webhook_hits WHERE block_time > strftime('%s','now')-86400 "
                     "AND CAST(ROUND(amount_sol*1e9) AS INT)%1000000=39280")
        tmpl24 = (_q1(live, _tmpl_sql) or 0) + (_q1(ov, _tmpl_sql) or 0)
        notif_age = (now - last_notif) if last_notif else None
        # LIVE if WS notifications are recent (the WS is receiving); STALE/DOWN only if notifs dried up.
        listener = "DOWN" if last_notif is None else ("LIVE" if notif_age < 3600 else "STALE")
        return jsonify({
            "listener_status": listener,
            "last_ws_notif": last_notif, "last_ws_notif_age_s": notif_age, "ws_notif_total": notif_total,
            "last_webhook_hit": last_hit, "last_hit_age_s": (now - last_hit) if last_hit else None,
            "hits_24h": hits24, "template_hits_24h": tmpl24,
            "rpc_follow_success_pct": None, "failed_follows": None,
            "warning": "NO_WS_NOTIFS" if (last_notif is None or (notif_age and notif_age > 3600)) else None,
        })
    finally:
        live.close(); ov.close()


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
    # WS liveness = treasury WS NOTIFICATIONS (wt_treasury_ws_usage), not provisioning hits.
    ovh = _conn()
    try:
        def _q(conn, sql):
            try:
                return (conn.execute(sql).fetchone() or [None])[0]
            except Exception:
                return None
        last = _q(ovh, "SELECT MAX(last_notif_at) FROM wt_treasury_ws_usage")
        try:
            ovh.close()
        except Exception:
            pass
        out["last_webhook_event"] = last
        if last:
            age = now - last
            out["webhook_listener"] = "LIVE" if age < WEBHOOK_STALE_S else "STALE"
            out["webhook_event_age_s"] = age
    finally:
        pass  # ovh already closed above
    return jsonify(out)


@ops_dashboard_bp.route("/api/ops-v2/intel/coverage-opportunity")
def api_intel_coverage_opportunity():
    """Coverage Opportunity ranking — read-only, zero RPC, zero schema changes.

    Returns two ranked tiers of unsubscribed wallets ordered by Expected Detection
    Yield (EDY): blind confirmed treasuries first (structurally more efficient —
    one subscription exposes all downstream subprovs), then unsubscribed known
    subprovs second.

    Scoring (0-100 per component, weighted sum → EDY 0-100):
      recency  40%  exp(-days_inactive / 7)  half-life ≈ 5 days
      cadence  30%  launches-per-30-days × 5, capped 100
      quality  20%  median peak MC USD / 1000, capped 100 ($100k median → 100)
      network  10%  downstream wallets × 10, capped 100

    Role multipliers applied after sum:
      TREASURY (blind)            × 1.5  (one sub covers all downstream)
      SUBPROV (treasury webhooked) × 0.5  (treasury already provides partial coverage)
      SUBPROV (unknown treasury)   × 0.7
    """
    import math
    import json as _json

    ov = _conn()
    try:
        now = int(time.time())

        # ── helpers ───────────────────────────────────────────────────────────
        confirmed_set = {r[0] for r in ov.execute(
            "SELECT treasury FROM wt_confirmed_treasuries").fetchall()}
        webhooked_set = {r[0] for r in ov.execute(
            "SELECT treasury FROM wt_confirmed_treasury_webhooks WHERE webhook_active=1"
        ).fetchall()} if _table_exists(ov, "wt_confirmed_treasury_webhooks") else set()
        active_session_set = {r[0] for r in ov.execute(
            "SELECT DISTINCT subprov_wallet FROM wt_active_subprov_sessions WHERE state='ACTIVE'"
        ).fetchall()} if _table_exists(ov, "wt_active_subprov_sessions") else set()

        # ── Attach live DB for peak MC data (fail-open) ───────────────────────
        live_attached = False
        try:
            ov.execute(f"ATTACH DATABASE 'file:{LIVE_DB_PATH}?mode=ro' AS live")
            live_attached = True
        except Exception:
            pass

        # ── Median peak MC per subprov / treasury via confirmed launches ───────
        # Builds a dict: wallet → median_peak_mc_usd (None if no data)
        mc_by_subprov: dict = {}
        mc_by_treasury: dict = {}
        if live_attached and _table_exists(ov, "wt_watchtower_launches"):
            try:
                rows = ov.execute("""
                    SELECT wl.subprov_wallet, wl.treasury_wallet,
                           mp.peak_market_cap
                    FROM wt_watchtower_launches wl
                    JOIN live.token_market_cap_peaks mp ON mp.mint = wl.mint
                    WHERE mp.peak_market_cap > 0
                """).fetchall()
                from collections import defaultdict
                sp_mc: dict = defaultdict(list)
                tr_mc: dict = defaultdict(list)
                for r in rows:
                    if r[0]:
                        sp_mc[r[0]].append(r[2])
                    if r[1]:
                        tr_mc[r[1]].append(r[2])
                def _median(lst):
                    s = sorted(lst)
                    n = len(s)
                    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
                mc_by_subprov  = {k: _median(v) for k, v in sp_mc.items()}
                mc_by_treasury = {k: _median(v) for k, v in tr_mc.items()}
            except Exception:
                pass

        # ── Launch counts per subprov / treasury ─────────────────────────────
        launches_by_subprov: dict  = {}
        launches_by_treasury: dict = {}
        first_seen_by_treasury: dict = {}
        if _table_exists(ov, "wt_watchtower_launches"):
            try:
                for r in ov.execute("""
                    SELECT subprov_wallet, COUNT(*) as n, MIN(create_time) as first
                    FROM wt_watchtower_launches GROUP BY subprov_wallet
                """).fetchall():
                    launches_by_subprov[r[0]] = (r[1], r[2])
                for r in ov.execute("""
                    SELECT treasury_wallet, COUNT(*) as n, MIN(create_time) as first
                    FROM wt_watchtower_launches GROUP BY treasury_wallet
                """).fetchall():
                    launches_by_treasury[r[0]] = (r[1], r[2])
            except Exception:
                pass

        # Downstream subprov count per treasury (network importance)
        downstream: dict = {}
        try:
            for r in ov.execute("""
                SELECT treasury, COUNT(*) as n FROM wt_discovered_subprovs
                WHERE treasury IS NOT NULL GROUP BY treasury
            """).fetchall():
                downstream[r[0]] = r[1]
        except Exception:
            pass

        # Last webhook-hit per treasury (recency signal for treasuries)
        last_hit: dict = {}
        if _table_exists(ov, "wt_webhook_hits"):
            try:
                for r in ov.execute(
                    "SELECT wallet_address, MAX(block_time) as last FROM wt_webhook_hits GROUP BY wallet_address"
                ).fetchall():
                    last_hit[r[0]] = r[1]
            except Exception:
                pass

        # Max downstream subprov activity per treasury — best recency proxy for
        # treasuries that are confirmed but have never had a webhook hit (blind).
        # When the treasury fired a subprov last week, that IS treasury activity.
        max_subprov_last_seen: dict = {}
        try:
            for r in ov.execute(
                "SELECT treasury, MAX(last_seen) as last FROM wt_discovered_subprovs "
                "WHERE treasury IS NOT NULL GROUP BY treasury"
            ).fetchall():
                if r[1]:
                    max_subprov_last_seen[r[0]] = r[1]
        except Exception:
            pass

        # ── Scoring helper ───────────────────────────────────────────────────
        def _edy(wallet, role, last_active_ts, creator_count, first_active_ts,
                 launch_count, first_launch_ts, treasury_wallet=None):
            # Recency (40%) — exponential decay, λ=7 days
            days_inactive = (now - (last_active_ts or 0)) / 86400 if last_active_ts else 999
            recency = 100 * math.exp(-days_inactive / 7)

            # Cadence (30%) — launches per 30 days when active
            # Use creator_count as a proxy when confirmed launches are few
            span_days = max(1, (now - (first_active_ts or now)) / 86400)
            events = max(creator_count or 0, launch_count or 0)
            cadence = min(100, 5 * (events / span_days * 30))

            # Quality (20%) — median peak MC USD, capped at $100k → 100
            mc = mc_by_subprov.get(wallet) or mc_by_treasury.get(wallet or "") or \
                 (mc_by_treasury.get(treasury_wallet) if treasury_wallet else None)
            quality = min(100, (mc / 1000)) if mc else 0

            # Network (10%) — downstream wallets (for treasuries) or 1 for leaf subprovs
            net_count = downstream.get(wallet, 1 if role == "SUBPROV" else 0)
            network = min(100, 10 * net_count)

            edy = 0.40 * recency + 0.30 * cadence + 0.20 * quality + 0.10 * network

            # Role multiplier
            if role == "TREASURY":
                edy *= 1.5
            elif role == "SUBPROV":
                # Single-use decay: creator_count==1 means fired once and spent.
                # After 48h with no reuse, value collapses — floor at 5%.
                if (creator_count or 0) == 1 and days_inactive > 2:
                    edy *= max(0.05, math.exp(-(days_inactive - 2) / 1.5))
                if treasury_wallet and treasury_wallet in webhooked_set:
                    edy *= 0.5   # treasury already provides partial coverage
                elif not treasury_wallet:
                    edy *= 0.7   # unknown treasury — unconfirmed lead

            return round(min(100, edy), 1)

        def _ago_label(ts):
            if not ts:
                return None
            delta = now - ts
            if delta < 3600:
                return f"{delta // 60}m"
            if delta < 86400:
                return f"{delta // 3600}h"
            return f"{delta // 86400}d"

        # ── TIER 1: Blind confirmed treasuries ───────────────────────────────
        # confirmed_at keyed by treasury for first_ts fallback
        confirmed_at_map: dict = {}
        try:
            for r in ov.execute("SELECT treasury, confirmed_at FROM wt_confirmed_treasuries").fetchall():
                confirmed_at_map[r[0]] = r[1]
        except Exception:
            pass

        tier1 = []
        for t in confirmed_set - webhooked_set:
            # Recency: best of webhook-hit, max-downstream-subprov-activity, confirmed_at
            last_ts = max(
                filter(None, [
                    last_hit.get(t),
                    max_subprov_last_seen.get(t),
                    confirmed_at_map.get(t),
                ]),
                default=None,
            )
            ln, fl   = launches_by_treasury.get(t, (0, None))
            first_ts = fl or confirmed_at_map.get(t)
            creators = downstream.get(t, 0)
            edy = _edy(t, "TREASURY", last_ts, creators, first_ts, ln, fl)
            days_inactive = round((now - last_ts) / 86400, 1) if last_ts else None
            tier1.append({
                "wallet":            t,
                "role":              "TREASURY",
                "edy_score":         edy,
                "days_since_active": days_inactive,
                "last_active_label": _ago_label(last_ts),
                "creator_count":     creators,
                "confirmed_launches": ln,
                "median_peak_mc_usd": round(mc_by_treasury.get(t, 0)) or None,
                "downstream_subprovs": downstream.get(t, 0),
                "already_subscribed": False,
                "note": f"1 subscription covers {downstream.get(t, 0)} known downstream subprovs" if downstream.get(t) else None,
            })
        tier1.sort(key=lambda x: -x["edy_score"])

        # ── TIER 2: Unsubscribed known subprovs ──────────────────────────────
        tier2 = []
        if _table_exists(ov, "wt_discovered_subprovs"):
            _have_mesh = _column_exists(ov, "wt_discovered_subprovs", "immediate_funder")
            _mesh_sel  = ", immediate_funder, funder_is_subprov" if _have_mesh else ""
            rows = ov.execute(
                "SELECT subprov, creator_count, treasury, treasury_known, "
                "first_seen, last_seen" + _mesh_sel +
                " FROM wt_discovered_subprovs WHERE treasury_known=1"
            ).fetchall()
            for r in rows:
                sp = r["subprov"]
                if sp in active_session_set:
                    continue   # already subscribed — skip
                treasury = r["treasury"]
                if treasury and treasury not in confirmed_set:
                    continue   # treasury not confirmed — goes in TIER 3 (unknown-treasury leads)
                ln, fl = launches_by_subprov.get(sp, (0, None))
                edy = _edy(sp, "SUBPROV", r["last_seen"], r["creator_count"],
                           r["first_seen"], ln, fl, treasury_wallet=treasury)
                days_inactive = round((now - r["last_seen"]) / 86400, 1) if r["last_seen"] else None
                tier2.append({
                    "wallet":            sp,
                    "role":              "SUBPROV",
                    "treasury":          treasury,
                    "treasury_webhooked": treasury in webhooked_set,
                    "edy_score":         edy,
                    "days_since_active": days_inactive,
                    "last_active_label": _ago_label(r["last_seen"]),
                    "creator_count":     r["creator_count"] or 0,
                    "confirmed_launches": ln,
                    "median_peak_mc_usd": round(mc_by_subprov.get(sp) or mc_by_treasury.get(treasury or "") or 0) or None,
                    "already_subscribed": False,
                })
        tier2.sort(key=lambda x: -x["edy_score"])
        # Drop spent single-use subprovs — EDY<5 after decay means no forward value
        tier2 = [x for x in tier2 if x["edy_score"] >= 5]

        # ── Summary ──────────────────────────────────────────────────────────
        all_scores = [x["edy_score"] for x in tier1 + tier2]
        top10 = sorted(all_scores, reverse=True)[:10]
        active_7d = sum(
            1 for x in tier1 + tier2
            if x["days_since_active"] is not None and x["days_since_active"] <= 7
        )
        dormant_90d = sum(
            1 for x in tier1 + tier2
            if x["days_since_active"] is None or x["days_since_active"] > 90
        )

        return jsonify({
            "tier1_blind_treasuries":    tier1,
            "tier2_unsubscribed_subprovs": tier2[:50],
            "summary": {
                "tier1_count":        len(tier1),
                "tier2_count":        len(tier2),
                "total_candidates":   len(tier1) + len(tier2),
                "top10_avg_edy":      round(sum(top10) / len(top10), 1) if top10 else 0,
                "highest_edy":        top10[0] if top10 else 0,
                "active_7d_count":    active_7d,
                "dormant_90d_count":  dormant_90d,
            },
        })
    finally:
        try:
            ov.execute("DETACH DATABASE live")
        except Exception:
            pass
        ov.close()


@ops_dashboard_bp.route("/api/ops-v2/intel/coverage-summary")
def api_intel_coverage_summary():
    """Coverage-first hero summary. Read-only ops DB only — no live DB, no RPC, no mutations.
    Answers: how much WATCHTOWER launch infrastructure are we actually watching right now?
    All tables are small (≤262 rows max); this replaces the 5-call parallel hero fetch."""
    ov = _conn()
    try:
        now = int(time.time())

        def _c(sql):
            try:
                return (ov.execute(sql).fetchone() or [0])[0] or 0
            except Exception:
                return 0

        confirmed    = _c("SELECT COUNT(*) FROM wt_confirmed_treasuries")
        webhooked    = _c("SELECT COUNT(*) FROM wt_confirmed_treasury_webhooks WHERE webhook_active=1")
        subprov_total= _c("SELECT COUNT(*) FROM wt_discovered_subprovs")
        subprov_known= _c("SELECT COUNT(*) FROM wt_discovered_subprovs WHERE treasury_known=1")
        active_sess  = _c("SELECT COUNT(*) FROM wt_active_subprov_sessions WHERE state='ACTIVE'")
        launches_7d  = _c("SELECT COUNT(*) FROM wt_watchtower_launches WHERE create_time > strftime('%s','now')-604800")
        farm_7d      = _c("SELECT COUNT(*) FROM wt_farm_launches WHERE created_at > strftime('%s','now')-604800")
        last_wrap    = (ov.execute("SELECT MAX(detected_at) FROM wt_candidate_websocket_watches").fetchone() or [None])[0]
        last_create  = (ov.execute("SELECT MAX(create_time) FROM wt_watchtower_launches").fetchone() or [None])[0]

        # cascade daemon health — ws_cascade writes its heartbeat here (ops DB, not live)
        hb = None
        try:
            hb = ov.execute(
                "SELECT last_seen, status, meta_json FROM wt_worker_heartbeat WHERE worker_name='ws_cascade'").fetchone()
        except Exception:
            pass
        hb_age = (now - hb["last_seen"]) if (hb and hb["last_seen"]) else None

        # Parse per-tier WS subscription counts + lifecycle state from heartbeat meta
        hb_subs = hb_treasury_subs = hb_subprov_subs = hb_candidate_subs = 0
        hb_lifecycle = None
        hb_reconnect_gen = None
        try:
            import json as _json
            if hb and hb["meta_json"]:
                _m = _json.loads(hb["meta_json"])
                hb_subs           = _m.get("subs", 0)
                hb_treasury_subs  = _m.get("treasury_subs", 0)
                hb_subprov_subs   = _m.get("subprov_subs", 0)
                hb_candidate_subs = _m.get("candidate_subs", 0)
                hb_lifecycle      = _m.get("cascade_state")  # CONNECTING/SUBSCRIBING/RECONCILING/LIVE/DEGRADED/FAILED
                hb_reconnect_gen  = _m.get("reconnect_gen")
        except Exception:
            pass

        # State model: prefer explicit lifecycle state from heartbeat; fall back to heuristic.
        if hb_age is None:
            cascade_status = "DOWN"
        elif hb_age >= 90:
            cascade_status = "STALE"
        elif hb_lifecycle in ("CONNECTING", "SUBSCRIBING", "RECONCILING", "DEGRADED", "FAILED"):
            cascade_status = hb_lifecycle   # transient startup or error state — not yet LIVE
        elif hb_subs == 0:
            cascade_status = "DEGRADED"
        elif active_sess == 0:
            cascade_status = "LIVE_IDLE"
        else:
            cascade_status = "ACTIVE"

        coverage_pct = round(100 * launches_7d / farm_7d) if farm_7d else 0

        return jsonify({
            "confirmed_treasuries": confirmed,
            "webhooked_treasuries": webhooked,
            "blind_treasuries": max(0, confirmed - webhooked),
            "subprovs_total": subprov_total,
            "subprovs_known": subprov_known,
            "subprovs_gap": max(0, subprov_known - hb_subprov_subs),
            "cascade_status": cascade_status,
            "cascade_ws_state": hb_lifecycle,
            "cascade_hb_age_s": hb_age,
            "cascade_reconnect_gen": hb_reconnect_gen,
            "cascade_ws_subs": hb_subs,
            "cascade_treasury_subs": hb_treasury_subs,
            "cascade_subprov_subs": hb_subprov_subs,
            "cascade_candidate_subs": hb_candidate_subs,
            "cascade_session_subs": active_sess,
            "launches_detected_7d": launches_7d,
            "launches_total_7d": farm_7d,
            "coverage_pct": coverage_pct,
            "last_wrap_close": last_wrap,
            "last_create": last_create,
        })
    finally:
        ov.close()


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
            _cols = ("SELECT wallet_address, counterparty, tx_type, amount_sol, block_time, "
                     "tx_signature, direction, source FROM wt_webhook_hits "
                     f"WHERE wallet_address IN ({ph}) ORDER BY block_time DESC LIMIT ?")
            # The cascade (WS) now writes its treasury_ws hits to the OPS DB; the legacy HTTP
            # webhook still writes the HOT DB. UNION both so the feed reflects the LIVE WS, not
            # just the (often stale) webhook. Merge + sort + cap.
            hits = list(live.execute(_cols, list(watch) + [limit]).fetchall())
            try:
                hits += list(ov.execute(_cols, list(watch) + [limit]).fetchall())
            except Exception:
                pass
            # dedupe on (tx_signature, wallet_address); keep newest; re-sort; cap
            _seen = set(); _merged = []
            for h in sorted(hits, key=lambda r: (r[4] or 0), reverse=True):
                k = (h[5], h[0])
                if k in _seen:
                    continue
                _seen.add(k); _merged.append(h)
            hits = _merged[:limit]
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

        # wallet → token (mint, symbol). The feed edges are treasury→SUBPROV, but the token lives
        # one hop further down (subprov → creator → token). So map BOTH the creator AND its subprov
        # to the token: the subprov→creator link is in wt_wrap_close_candidates, creator→mint via
        # live token_analysis.pf_ws_creator, symbol via metadata_cache. Zero RPC.
        wallet_token = {}
        try:
            sub_creator = ov.execute(
                "SELECT subprov_wallet, creator FROM wt_wrap_close_candidates "
                "WHERE creator IS NOT NULL").fetchall()
            creators = list({r[1] for r in sub_creator})
            creator_token = {}
            if creators:
                lc2 = _live_conn()
                try:
                    cph = ",".join("?" * len(creators))
                    for r in lc2.execute(
                        f"SELECT ta.pf_ws_creator c, ta.mint m, mc.symbol s "
                        f"FROM token_analysis ta LEFT JOIN metadata_cache mc ON mc.mint = ta.mint "
                        f"WHERE ta.pf_ws_creator IN ({cph})", creators).fetchall():
                        if r["c"]:
                            creator_token[r["c"]] = {"mint": r["m"], "symbol": r["s"]}
                finally:
                    lc2.close()
            for sub, cre in sub_creator:
                tok = creator_token.get(cre)
                if not tok:
                    continue
                wallet_token.setdefault(cre, tok)   # recipient IS the creator
                if sub:
                    wallet_token.setdefault(sub, tok)   # recipient is the subprov one hop up
        except Exception:
            pass

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
            cp = h["counterparty"]
            # token the recipient produced — check BOTH endpoints of the edge (the stored
            # `direction` is unreliable: the same treasury→subprov edge appears as both 'inbound'
            # and 'outbound' rows), so resolve by whichever side is a known token wallet.
            _tok = wallet_token.get(cp) or wallet_token.get(w)
            events.append({
                "wallet": w, "counterparty": cp, "type": et,
                "amount": h["amount_sol"], "ts": h["block_time"], "signature": h["tx_signature"],
                "direction": _dir, "funding_type": funding_type, "via": _via,
                "operation_uuid": op, "operation": op[:8] if op else None,
                "family": fam.get(op_fam.get(op)) if op else None,
                "role": role, "candidate": is_cand,
                "candidate_status": ("MIGRATED" if w in migrated else "PENDING") if is_cand else None,
                "launch_detected": launch,
                "from_operation": op is not None,
                "token_mint": (_tok or {}).get("mint"),
                "token_symbol": (_tok or {}).get("symbol"),
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
        # LIVE FEED = newest-first (a feed must read as a time-ordered stream — hoisting older
        # token edges to the top made it look stalled). Token/launch edges are surfaced by an
        # in-place ROW HIGHLIGHT in the UI, not by reordering. Exception: a true launch_detected
        # is rare + worth pinning, so it still floats to the top.
        events.sort(key=lambda x: (0 if x.get("launch_detected") else 1, -(x["ts"] or 0)))
        # WS ACTIVITY SUMMARY: hit rows are only written on ≥1◎ provisioning outbounds, so the
        # feed looks empty while a treasury is busy with sub-1◎ dust. Surface the live WS metering
        # (wt_treasury_ws_usage) so the feed shows the WS IS active even with no hit rows.
        ws_activity = []
        try:
            for r in ov.execute(
                "SELECT treasury_wallet, notif_count, notif_count_1h, sessions_opened, last_notif_at "
                "FROM wt_treasury_ws_usage WHERE last_notif_at > strftime('%s','now')-3600 "
                "ORDER BY last_notif_at DESC LIMIT 20").fetchall():
                ws_activity.append({
                    "treasury": r[0], "notifs_total": r[1], "notifs_1h": r[2],
                    "sessions_opened": r[3], "last_notif_at": r[4]})
        except Exception:
            pass
        return jsonify({"events": events, "ws_activity": ws_activity,
                        "ws_active": bool(ws_activity)})
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


@ops_dashboard_bp.route("/api/ops-v2/intel/discovery-leads")
def api_intel_discovery_leads():
    """
    Discovery Lead Scoring Engine — read-only, ops DB only, no RPC.

    Returns unconfirmed funders ranked by evidence of operator infrastructure behaviour.
    Evidence badges use tri-state (PRESENT / ABSENT / UNKNOWN) so conditional signals
    (WRAP_CLOSE, TREASURY_PROXIMITY, SUBPROV_PROXIMITY) never show as falsely negative.
    """
    from src.core.discovery_evidence import build_leads
    c = _conn()
    try:
        leads = build_leads(c)
        by_tier = {"HIGH": [], "MEDIUM": [], "LOW": []}
        for lead in leads:
            if lead.tier in by_tier:
                by_tier[lead.tier].append(lead.to_dict())
        return jsonify({
            "leads": [l.to_dict() for l in leads],
            "by_tier": by_tier,
            "counts": {
                "HIGH": len(by_tier["HIGH"]),
                "MEDIUM": len(by_tier["MEDIUM"]),
                "LOW": len(by_tier["LOW"]),
                "total": len(leads),
            },
        })
    finally:
        c.close()


@ops_dashboard_bp.route("/api/ops-v2/intel/discovery-review-add", methods=["POST"])
def api_intel_discovery_review_add():
    """
    Add a discovery lead to wt_treasury_review. Human-triggered only.

    POST { treasury: <addr>, score: int, evidence: [...badges], reasons: [...str] }

    Stores discovery evidence alongside the review entry so the review card can
    show why the lead was surfaced. Idempotent — safe to call if already pending.
    Does NOT approve, webhook, or activate anything.
    """
    import json as _json
    body = request.get_json(silent=True) or {}
    treasury = (body.get("treasury") or "").strip()
    if not treasury:
        return jsonify({"error": "treasury required"}), 400

    score = body.get("score")
    evidence = body.get("evidence", [])
    reasons = body.get("reasons", [])
    now = int(time.time())

    ov = _conn_rw()
    try:
        # Ensure evidence columns exist (idempotent ALTER TABLE)
        for col, coltype in [("evidence_json", "TEXT"), ("discovery_score", "INTEGER"),
                             ("discovery_reasons", "TEXT")]:
            if not _column_exists(ov, "wt_treasury_review", col):
                try:
                    ov.execute(f"ALTER TABLE wt_treasury_review ADD COLUMN {col} {coltype}")
                except Exception:
                    pass

        existing = ov.execute(
            "SELECT status FROM wt_treasury_review WHERE treasury=?", (treasury,)
        ).fetchone()

        if existing:
            if existing["status"] in ("CONFIRMED", "REJECTED"):
                return jsonify({
                    "ok": False,
                    "message": f"Already {existing['status']} — no change made",
                    "status": existing["status"],
                })
            # Already pending — update evidence (it may have improved)
            ov.execute(
                "UPDATE wt_treasury_review SET evidence_json=?, discovery_score=?, "
                "discovery_reasons=? WHERE treasury=?",
                (_json.dumps(evidence), score, _json.dumps(reasons), treasury)
            )
            ov.commit()
            return jsonify({
                "ok": True, "action": "updated",
                "message": "Already in review — evidence refreshed",
                "status": "PENDING_REVIEW",
            })

        ov.execute(
            "INSERT INTO wt_treasury_review "
            "(treasury, detected_via, status, detected_at, evidence_json, "
            " discovery_score, discovery_reasons) "
            "VALUES (?, 'discovery_lead', 'PENDING_REVIEW', ?, ?, ?, ?)",
            (treasury, now, _json.dumps(evidence), score, _json.dumps(reasons))
        )
        ov.commit()
        return jsonify({
            "ok": True, "action": "added",
            "message": "Added to Treasury Review — no activation. Use Approve to confirm.",
            "status": "PENDING_REVIEW",
        })
    finally:
        ov.close()


@ops_dashboard_bp.route("/api/ops-v2/intel/runtime-budget")
def api_intel_runtime_budget():
    """Runtime Budget Health — is WATCHTOWER operating within its design budgets?
    Returns both configuration (from runtime_budget.py) and live runtime counters
    (degrade events, timeouts, DB pressure) so the UI can show health vs degradation."""
    from src.core import runtime_budget as rb

    # ── Budget configuration ──────────────────────────────────────────────────
    config = {
        "critical_rpc_total_s":       rb.CRITICAL_RPC_TOTAL_S,
        "critical_outer_timeout_s":   rb.CRITICAL_OUTER_TIMEOUT_S,
        "nearrt_rpc_total_s":         rb.NEARRT_RPC_TOTAL_S,
        "pool_validate_timeout_s":    rb.POOL_VALIDATE_TIMEOUT_S,
        "pool_discovery_total_s":     rb.POOL_DISCOVERY_TOTAL_S,
        "migration_outer_timeout_s":  rb.MIGRATION_OUTER_TIMEOUT_S,
        "create_fetch_concurrency":   rb.CREATE_FETCH_CONCURRENCY,
        "create_fetch_timeout_s":     rb.CREATE_FETCH_TIMEOUT_S,
        "create_fetch_queue_max":     rb.CREATE_FETCH_QUEUE_MAX,
        "creator_resolution_fast_s":  rb.CREATOR_RESOLUTION_FAST_S,
        "creator_resolution_deep_s":  rb.CREATOR_RESOLUTION_DEEP_S,
        "max_db_inline_writes":       rb.MAX_DB_INLINE_WRITES_PER_EVENT,
        "critical_db_busy_ms":        rb.CRITICAL_DB_BUSY_TIMEOUT_MS,
        "max_active_subprov_sessions": rb.MAX_ACTIVE_SUBPROV_SESSIONS,
        "max_candidate_watches":      rb.MAX_CANDIDATE_WATCHES,
        "public_rpc_policy":          "DEFERRED_ONLY",
    }

    # ── Runtime degrade counters (from live process memory) ──────────────────
    # ws_cascade counters live in its heartbeat (ops DB). Listener counters from its module.
    runtime = {
        "treasury_timeout":       0,
        "catchup_timeout":        0,
        "validation_timeout":     0,
        "pool_discovery_deferred": 0,
        "migration_deferred":     0,
        "create_fetch_dropped":   0,
        "create_fetch_timeout":   0,
        "create_fetch_queue":     0,
        "db_lock_errors_5m":      0,
        "db_lock_errors_1h":      0,
        "db_p99_wait_ms":         0,
        "db_queue_depth":         0,
        "loop_lag_s":             None,   # from listener heartbeat if available
        "cascade_state":          None,
        "listener_birth_stale_s": None,
    }

    # Cascade degrade counters from heartbeat meta_json (ops DB)
    try:
        ov = _conn()
        try:
            if _table_exists(ov, "wt_worker_heartbeat"):
                hb = ov.execute(
                    "SELECT last_seen, meta_json FROM wt_worker_heartbeat "
                    "WHERE worker_name='ws_cascade'"
                ).fetchone()
                if hb and hb["meta_json"]:
                    _m = _json.loads(hb["meta_json"] or "{}")
                    runtime["treasury_timeout"]     = _m.get("budget_treasury_timeout", 0)
                    runtime["catchup_timeout"]      = _m.get("budget_catchup_timeout", 0)
                    runtime["create_fetch_dropped"] = _m.get("pw_fetch_dropped", 0)
                    runtime["create_fetch_timeout"] = _m.get("pw_fetch_timeout", 0)
                    runtime["create_fetch_queue"]   = _m.get("pw_fetch_queue", 0)
                    runtime["cascade_state"]        = _m.get("cascade_state")
        finally:
            ov.close()
    except Exception:
        pass

    # Listener budget counters — read directly from the live module if in-process,
    # fall back gracefully when called from the API process (different interpreter).
    try:
        import importlib
        _lcm = importlib.import_module("src.core.pumpfun_curve_listener")
        with _lcm._BUDGET_COUNTERS_LOCK:
            _lc = dict(_lcm._BUDGET_COUNTERS)
        runtime["validation_timeout"]      = _lc.get("validation_timeout", 0)
        runtime["pool_discovery_deferred"] = _lc.get("pool_discovery_deferred", 0)
        runtime["migration_deferred"]      = _lc.get("migration_deferred", 0)
    except Exception:
        pass

    # DB health — serializer metrics (in-process, no DB read)
    try:
        from src.utils.db_locking import serializer_metrics as _sm, get_lock_error_metrics as _glm
        _s = _sm()
        _g = _glm()
        runtime["db_p99_wait_ms"]    = _s.get("p99_wait_ms", 0)
        runtime["db_queue_depth"]    = _s.get("queue_depth", 0)
        runtime["db_lock_errors_5m"] = _g.get("lock_errors_5m", 0)
        runtime["db_lock_errors_1h"] = _g.get("lock_errors_1h", 0)
    except Exception:
        pass

    # Creator resolution skip count from the resolution queue table
    try:
        lv = _live_conn()
        try:
            row = lv.execute(
                "SELECT COUNT(*) FROM creator_resolution_queue WHERE status='skipped'"
            ).fetchone()
            runtime["creator_resolution_skipped"] = row[0] if row else 0
            # Also check budget_exceeded status
            row2 = lv.execute(
                "SELECT COUNT(*) FROM creator_resolution_queue WHERE status='budget_exceeded'"
            ).fetchone()
            runtime["creator_resolution_budget_exceeded"] = row2[0] if row2 else 0
        finally:
            lv.close()
    except Exception:
        runtime["creator_resolution_skipped"] = 0
        runtime["creator_resolution_budget_exceeded"] = 0

    # ── Derive overall health status ──────────────────────────────────────────
    # RED: critical path threatened (loop lag, critical RPC storm, DB p99 spike)
    # YELLOW: optional work being skipped/deferred (healthy behaviour)
    # GREEN: everything within budget
    _critical_failures = (
        runtime["treasury_timeout"] +
        runtime["create_fetch_timeout"]
    )
    _optional_deferred = (
        runtime.get("creator_resolution_skipped", 0) +
        runtime["pool_discovery_deferred"] +
        runtime["validation_timeout"] +
        runtime["catchup_timeout"]
    )
    _db_pressure = runtime["db_lock_errors_5m"]
    _db_p99      = runtime["db_p99_wait_ms"] or 0
    _cascade_ok  = runtime["cascade_state"] in ("LIVE", None)

    if not _cascade_ok or _db_p99 > 5000 or _db_pressure > 10:
        health = "RED"
        health_label = "Critical path at risk"
        health_note  = ("DB pressure or cascade down" if not _cascade_ok
                        else f"DB p99={_db_p99}ms, lock errors={_db_pressure}/5m")
    elif _critical_failures > 0 or _db_p99 > 1000:
        health = "YELLOW"
        health_label = "Near budget"
        health_note  = f"{_critical_failures} critical timeout(s) — monitor"
    elif _optional_deferred > 0:
        health = "GREEN"
        health_label = "Degrading gracefully"
        health_note  = f"{_optional_deferred} optional operation(s) deferred — critical path protected"
    else:
        health = "GREEN"
        health_label = "Healthy"
        health_note  = "All paths within budget"

    budget_table = [
        {"path": p, "file": f, "tier": t, "budget_s": b, "degrade": d, "risk": r}
        for p, f, t, b, d, r in rb.BUDGET_TABLE
    ]

    return jsonify({
        "health": health,
        "health_label": health_label,
        "health_note": health_note,
        "runtime": runtime,
        "config": config,
        "budget_table": budget_table,
    })


@ops_dashboard_bp.route("/api/ops-v2/intel/scheduler-pressure")
def api_intel_scheduler_pressure():
    """Scheduler runtime pressure state — read directly from ops DB (cross-process safe).
    Returns current pressure score/level, defer counts, starvation state, and last success."""
    import sqlite3, json as _json
    now = int(time.time())
    ov = _conn()
    try:
        active = ov.execute(
            "SELECT COUNT(*) FROM wt_active_subprov_sessions WHERE state='ACTIVE'"
        ).fetchone()[0]
        active_pressure = ov.execute(
            "SELECT COUNT(*) FROM wt_active_subprov_sessions "
            "WHERE state='ACTIVE' AND open_reason NOT IN "
            "('HISTORICAL_SUBPROV_DISCOVERED','CREATOR')"
        ).fetchone()[0]
        pending = critical = 0
        try:
            for row in ov.execute(
                "SELECT priority, COUNT(*) n FROM wt_pending_session_writes "
                "WHERE state='PENDING' GROUP BY priority"
            ).fetchall():
                pending += row["n"]
                if row["priority"] == "CRITICAL":
                    critical += row["n"]
        except Exception:
            pass
        # last run log entries
        runs = []
        try:
            for r in ov.execute(
                "SELECT job_type, started_at, status, runtime_sec, rpc_used "
                "FROM wt_ops_v2_runs ORDER BY started_at DESC LIMIT 10"
            ).fetchall():
                runs.append({
                    "job": r["job_type"], "ago_s": now - (r["started_at"] or now),
                    "status": r["status"], "runtime_s": r["runtime_sec"], "rpc": r["rpc_used"],
                })
        except Exception:
            pass
        # cascade heartbeat
        hb_age = None
        cascade_state = None
        try:
            hb = ov.execute(
                "SELECT last_seen, meta_json FROM wt_worker_heartbeat "
                "WHERE worker_name='ws_cascade' ORDER BY last_seen DESC LIMIT 1"
            ).fetchone()
            if hb:
                hb_age = now - (hb["last_seen"] or now)
                meta = _json.loads(hb["meta_json"] or "{}")
                cascade_state = meta.get("cascade_state")
        except Exception:
            pass
        # derive pressure score (mirrors calculate_runtime_pressure logic)
        score = 0
        if critical > 0: score += 25
        if pending > 0: score += 10
        if active > 0: score += 3
        if active > 10: score += 6
        if active > 25: score += 10
        if cascade_state in ("ACTIVE", "LIVE"): score += 2
        level = "CRITICAL" if score >= 30 else "HIGH" if score >= 20 else "MEDIUM" if score >= 10 else "LOW"
        # pull scheduler state flushed by the scheduler process into wt_scheduler_state
        sched_extra = {}
        try:
            import json as _json2
            ss_row = ov.execute(
                "SELECT value_json, updated_at FROM wt_scheduler_state WHERE key='main' LIMIT 1"
            ).fetchone()
            if ss_row:
                ss = _json2.loads(ss_row["value_json"] or "{}")
                # prefer the scheduler's authoritative score/level (it has more signals)
                sched_age = now - (ss_row["updated_at"] or now)
                if sched_age < 60:  # only trust if flushed within last minute
                    score = ss.get("pressure_score", score)
                    level = ss.get("pressure_level", level)
                sched_extra = {
                    "current_batch_size": ss.get("scheduler_batch_size"),
                    "last_defer_reason": ss.get("last_defer_reason"),
                    "deferred_cycles": ss.get("deferred_cycles", 0),
                    "starvation_override_count": ss.get("starvation_override_count", 0),
                    "sched_state_age_s": sched_age,
                }
        except Exception:
            pass
        return jsonify({
            "pressure_score": score,
            "pressure_level": level,
            "active_sessions": active,
            "active_pressure_sessions": active_pressure,
            "pending_writes": pending,
            "critical_pending": critical,
            "cascade_state": cascade_state,
            "heartbeat_age_s": hb_age,
            "recent_runs": runs,
            **sched_extra,
        })
    except Exception as e:
        return jsonify({"error": str(e), "pressure_level": "UNKNOWN"})
    finally:
        ov.close()


@ops_dashboard_bp.route("/api/ops-v2/intel/unconfirmed-watchtower-like")
def api_intel_unconfirmed_watchtower_like():
    try:
        from src.utils.db_locking import db_connect as _dbc
        conn = _dbc(LIVE_DB_PATH, timeout=10)
        conn.row_factory = __import__("sqlite3").Row
        try:
            if not _table_exists(conn, "wt_unconfirmed_watchtower_like"):
                return __import__("flask").jsonify({"leads": [], "total": 0})
            rows = conn.execute(
                """SELECT mint, creator_wallet, subprov_wallet, unknown_root_wallet,
                          root_hop, amount_sol, first_seen, last_seen,
                          occurrence_count, status
                   FROM wt_unconfirmed_watchtower_like
                   WHERE status='REVIEW'
                   ORDER BY last_seen DESC LIMIT 50""").fetchall()
            leads = [dict(r) for r in rows]
            total = conn.execute(
                "SELECT COUNT(*) FROM wt_unconfirmed_watchtower_like WHERE status='REVIEW'"
            ).fetchone()[0]
            # Group by unknown_root to show how many tokens share the same root
            root_counts = {}
            for r in conn.execute(
                "SELECT unknown_root_wallet, COUNT(*) n FROM wt_unconfirmed_watchtower_like "
                "WHERE status='REVIEW' GROUP BY unknown_root_wallet ORDER BY n DESC LIMIT 20"
            ).fetchall():
                root_counts[r[0]] = r[1]
            return __import__("flask").jsonify({
                "leads": leads, "total": total, "root_counts": root_counts
            })
        finally:
            conn.close()
    except Exception as e:
        return __import__("flask").jsonify({"error": str(e), "leads": [], "total": 0})


# ── /watchtower/operations API endpoints ─────────────────────────────────────

@ops_dashboard_bp.route("/api/ops/active-operations")
def api_ops_active_operations():
    """One row per active subprov session, enriched with fanout/create/swarm counts.
    Groups multiple sessions for the same treasury together."""
    ov = _conn()
    try:
        now = int(time.time())
        if not _table_exists(ov, "wt_active_subprov_sessions"):
            return jsonify({"operations": [], "total": 0})

        _scols = {r[1] for r in ov.execute("PRAGMA table_info(wt_active_subprov_sessions)").fetchall()}
        _topup_sql = "initial_funding_amount, topup_count, topup_amount_total" \
            if "initial_funding_amount" in _scols else \
            "NULL AS initial_funding_amount, 0 AS topup_count, 0.0 AS topup_amount_total"
        _seq_sql = "funding_sequence_number, COALESCE(treasury_rotated,0) as treasury_rotated" \
            if "funding_sequence_number" in _scols else \
            "NULL AS funding_sequence_number, 0 AS treasury_rotated"

        sessions = ov.execute(
            f"SELECT subprov_wallet, treasury_wallet, funding_amount, detected_at, expires_at, "
            f"open_reason, COALESCE(monitoring_state,'INTEL_ONLY') as monitoring_state, "
            f"{_topup_sql}, {_seq_sql} "
            "FROM wt_active_subprov_sessions WHERE state='ACTIVE' ORDER BY detected_at DESC"
        ).fetchall()

        # fanout stats per subprov
        fanout_stats: dict = {}
        if _table_exists(ov, "wt_fanout_events"):
            for r in ov.execute(
                "SELECT subprov_wallet, COUNT(*) as bursts, SUM(fanout_count) as recipients, "
                "SUM(creates_fired) as creates, SUM(buy_swarms) as swarms, "
                "MAX(fanout_time) as last_fanout "
                "FROM wt_fanout_events GROUP BY subprov_wallet"
            ).fetchall():
                fanout_stats[r["subprov_wallet"]] = {
                    "bursts": r["bursts"], "recipients": r["recipients"] or 0,
                    "creates": r["creates"] or 0, "swarms": r["swarms"] or 0,
                    "last_fanout": r["last_fanout"],
                }

        # launch count per subprov
        launch_counts: dict = {}
        if _table_exists(ov, "wt_watchtower_launches"):
            for r in ov.execute(
                "SELECT subprov_wallet, COUNT(*) n FROM wt_watchtower_launches GROUP BY subprov_wallet"
            ).fetchall():
                launch_counts[r["subprov_wallet"]] = r["n"]

        # swarm count per subprov (from wt_swarm_buys distinct mints)
        swarm_counts: dict = {}
        if _table_exists(ov, "wt_swarm_buys"):
            for r in ov.execute(
                "SELECT subprov_wallet, COUNT(DISTINCT mint) n FROM wt_swarm_buys GROUP BY subprov_wallet"
            ).fetchall():
                swarm_counts[r["subprov_wallet"]] = r["n"]

        ops = []
        for s in sessions:
            sp = s["subprov_wallet"]
            fs = fanout_stats.get(sp, {})
            last_activity = max(
                s["detected_at"] or 0,
                fs.get("last_fanout") or 0,
            )
            initial = s["initial_funding_amount"] or s["funding_amount"] or 0
            topup = s["topup_amount_total"] or 0
            ops.append({
                "subprov": sp,
                "treasury": s["treasury_wallet"],
                "monitoring_state": s["monitoring_state"],
                "open_reason": s["open_reason"] or "PROVISION_CANDIDATE",
                "funding_sequence_number": s["funding_sequence_number"],
                "treasury_rotated": bool(s["treasury_rotated"]),
                "detected_at": s["detected_at"],
                "ttl_remaining": max(0, (s["expires_at"] or now) - now),
                "age_s": now - (s["detected_at"] or now),
                "initial_sol": initial,
                "topup_count": s["topup_count"] or 0,
                "topup_sol": topup,
                "total_sol": initial + topup,
                "fanout_bursts": fs.get("bursts", 0),
                "fanout_recipients": fs.get("recipients", 0),
                "creates": launch_counts.get(sp, fs.get("creates", 0)),
                "swarms": swarm_counts.get(sp, fs.get("swarms", 0)),
                "last_activity": last_activity,
                "last_activity_ago": now - last_activity if last_activity else None,
                # Fanout batch key: same treasury funded within a 10s window = one batch
                "fanout_batch_id": f"{s['treasury_wallet']}_{(s['detected_at'] or 0) // 10}",
            })

        # Mark batch_size so the frontend knows which rows are part of a multi-subprov fanout
        from collections import Counter as _Counter
        batch_sizes = _Counter(o["fanout_batch_id"] for o in ops)
        for o in ops:
            o["fanout_batch_size"] = batch_sizes[o["fanout_batch_id"]]

        # sort by last_activity desc
        ops.sort(key=lambda x: x["last_activity"], reverse=True)
        return jsonify({"operations": ops, "total": len(ops), "now": now})
    except Exception as e:
        return jsonify({"operations": [], "total": 0, "error": str(e)})
    finally:
        ov.close()


@ops_dashboard_bp.route("/api/ops/operation-board")
def api_ops_operation_board():
    """Operation-centric board view: one row per treasury, enriched with phase/lifecycle."""
    ov = _conn()
    try:
        now = int(time.time())
        if not _table_exists(ov, "wt_ops_v2"):
            return jsonify({"operations": [], "phase_counts": {}, "now": now})

        # Pull all ops with a treasury_root — one row per operation_uuid
        _ops_cols = {r[1] for r in ov.execute("PRAGMA table_info(wt_ops_v2)").fetchall()}
        _src_sel = ", source" if "source" in _ops_cols else ", NULL as source"
        _ot_sel  = ", op_type" if "op_type" in _ops_cols else ", NULL as op_type"
        # Only show operations that have at least one currently ACTIVE subprov session.
        # UNION with active-session treasuries that have no wt_ops_v2 record yet (newly seen).
        if _table_exists(ov, "wt_active_subprov_sessions"):
            ops_rows = ov.execute(
                # Known ops with active sessions
                f"SELECT operation_uuid, treasury_root, status, confidence, created_at, updated_at{_src_sel}{_ot_sel} "
                "FROM wt_ops_v2 WHERE treasury_root IS NOT NULL "
                "AND EXISTS ("
                "  SELECT 1 FROM wt_active_subprov_sessions s "
                "  WHERE s.treasury_wallet = treasury_root AND s.state = 'ACTIVE'"
                ") "
                "UNION ALL "
                # Active-session treasuries not yet in wt_ops_v2 (synthesise a minimal row)
                "SELECT NULL as operation_uuid, s.treasury_wallet as treasury_root, "
                "  'ACTIVE' as status, NULL as confidence, MIN(s.detected_at) as created_at, "
                f"  MAX(s.detected_at) as updated_at, NULL as source, NULL as op_type "
                "FROM wt_active_subprov_sessions s "
                "WHERE s.state = 'ACTIVE' AND s.treasury_wallet IS NOT NULL "
                "AND NOT EXISTS (SELECT 1 FROM wt_ops_v2 o WHERE o.treasury_root = s.treasury_wallet) "
                "GROUP BY s.treasury_wallet"
            ).fetchall()
        else:
            ops_rows = ov.execute(
                f"SELECT operation_uuid, treasury_root, status, confidence, created_at, updated_at{_src_sel}{_ot_sel} "
                "FROM wt_ops_v2 WHERE treasury_root IS NOT NULL"
            ).fetchall()

        # lifecycle state per operation_uuid
        lifecycle_map: dict = {}  # operation_uuid -> state
        if _table_exists(ov, "wt_operation_lifecycle"):
            for r in ov.execute(
                "SELECT operation_uuid, state FROM wt_operation_lifecycle"
            ).fetchall():
                lifecycle_map[r["operation_uuid"]] = r["state"]

        # live/armed subprov counts per treasury_wallet (the treasury_root)
        live_map: dict = {}
        armed_map: dict = {}
        cand_map: dict = {}           # treasury -> WATCHING candidate count (real PW input)
        last_session_map: dict = {}
        subprov_total_map: dict = {}  # all-time distinct subprovs
        active_focus_map: dict = {}   # treasury -> {subprov, opened_ago, last_sol, state}
        if _table_exists(ov, "wt_candidate_websocket_watches"):
            for r in ov.execute(
                "SELECT c.treasury_wallet, COUNT(*) n "
                "FROM wt_candidate_websocket_watches c "
                "WHERE c.state='WATCHING' AND c.treasury_wallet IS NOT NULL "
                "GROUP BY c.treasury_wallet"
            ).fetchall():
                cand_map[r["treasury_wallet"]] = r["n"] or 0
        if _table_exists(ov, "wt_active_subprov_sessions"):
            sess_cols = {r[1] for r in ov.execute("PRAGMA table_info(wt_active_subprov_sessions)").fetchall()}
            # column name variations
            sprov_col = "subprov_wallet" if "subprov_wallet" in sess_cols else "subprov"
            sol_col = ("funding_amount" if "funding_amount" in sess_cols
                       else "total_sol" if "total_sol" in sess_cols else "funding_sol")
            for r in ov.execute(
                f"SELECT treasury_wallet, "
                f"SUM(CASE WHEN state='ACTIVE' THEN 1 ELSE 0 END) as live_count, "
                f"SUM(CASE WHEN state='ACTIVE' AND monitoring_state='LIVE_ARMED' THEN 1 ELSE 0 END) as armed_count, "
                f"MAX(detected_at) as last_session, "
                f"COUNT(DISTINCT {sprov_col}) as total_subprovs "
                "FROM wt_active_subprov_sessions GROUP BY treasury_wallet"
            ).fetchall():
                tw = r["treasury_wallet"]
                live_map[tw] = r["live_count"] or 0
                armed_map[tw] = r["armed_count"] or 0
                last_session_map[tw] = r["last_session"] or 0
                subprov_total_map[tw] = r["total_subprovs"] or 0
            # active focus: most recently opened ACTIVE subprov per treasury
            for r in ov.execute(
                f"SELECT treasury_wallet, {sprov_col} as sprov, detected_at, {sol_col} as sol, monitoring_state "
                "FROM wt_active_subprov_sessions WHERE state='ACTIVE' "
                "ORDER BY detected_at DESC"
            ).fetchall():
                tw = r["treasury_wallet"]
                if tw not in active_focus_map:
                    active_focus_map[tw] = {
                        "subprov": r["sprov"],
                        "opened_at": r["detected_at"] or 0,
                        "sol": r["sol"] or 0,
                        "state": r["monitoring_state"] or "",
                    }

        # fanout stats per treasury_wallet
        fanouts_24h_map: dict = {}
        recipients_24h_map: dict = {}
        last_fanout_map: dict = {}
        funding_rounds_map: dict = {}  # distinct fanout events = funding rounds
        # historical behavioural profile per treasury (for predictive intelligence)
        hist_profile_map: dict = {}  # tw -> {avg_recipients, avg_sol, swarm_rate, create_rate, sample_n}
        if _table_exists(ov, "wt_fanout_events"):
            cutoff = now - 86400
            for r in ov.execute(
                "SELECT treasury_wallet, "
                "SUM(CASE WHEN fanout_time > ? THEN 1 ELSE 0 END) as fanouts_24h, "
                "SUM(CASE WHEN fanout_time > ? THEN fanout_count ELSE 0 END) as recipients_24h, "
                "MAX(fanout_time) as last_fanout, "
                "COUNT(*) as total_rounds "
                "FROM wt_fanout_events GROUP BY treasury_wallet",
                (cutoff, cutoff)
            ).fetchall():
                tw = r["treasury_wallet"]
                fanouts_24h_map[tw] = r["fanouts_24h"] or 0
                recipients_24h_map[tw] = r["recipients_24h"] or 0
                last_fanout_map[tw] = r["last_fanout"] or 0
                funding_rounds_map[tw] = r["total_rounds"] or 0
            # historical profile (all-time, excludes swarm-only events)
            for r in ov.execute(
                "SELECT treasury_wallet, "
                "AVG(fanout_count) as avg_recipients, "
                "AVG(total_sol) as avg_sol, "
                "AVG(CASE WHEN buy_swarms > 0 THEN 1.0 ELSE 0.0 END) as swarm_rate, "
                "AVG(CASE WHEN creates_fired > 0 THEN 1.0 ELSE 0.0 END) as create_rate, "
                "COUNT(*) as sample_n "
                "FROM wt_fanout_events GROUP BY treasury_wallet"
            ).fetchall():
                hist_profile_map[r["treasury_wallet"]] = {
                    "avg_recipients": r["avg_recipients"] or 0,
                    "avg_sol": r["avg_sol"] or 0,
                    "swarm_rate": r["swarm_rate"] or 0,
                    "create_rate": r["create_rate"] or 0,
                    "sample_n": r["sample_n"] or 0,
                }

        # launch timing history per treasury (fanout-to-create latency)
        launch_timing_map: dict = {}  # tw -> {avg_s, min_s, max_s, n}
        if _table_exists(ov, "wt_watchtower_launches"):
            _lc2 = {r[1] for r in ov.execute("PRAGMA table_info(wt_watchtower_launches)").fetchall()}
            if "fanout_to_create_secs" in _lc2:
                for r in ov.execute(
                    "SELECT treasury_wallet, "
                    "AVG(fanout_to_create_secs) as avg_s, "
                    "MIN(fanout_to_create_secs) as min_s, "
                    "MAX(fanout_to_create_secs) as max_s, "
                    "COUNT(*) as n "
                    "FROM wt_watchtower_launches "
                    "WHERE fanout_to_create_secs IS NOT NULL "
                    "GROUP BY treasury_wallet"
                ).fetchall():
                    launch_timing_map[r["treasury_wallet"]] = {
                        "avg_s": r["avg_s"] or 0,
                        "min_s": r["min_s"] or 0,
                        "max_s": r["max_s"] or 0,
                        "n": r["n"] or 0,
                    }

        # launch counts per treasury_wallet
        creates_total_map: dict = {}
        creates_24h_map: dict = {}
        if _table_exists(ov, "wt_watchtower_launches"):
            cutoff = now - 86400
            # check if create_time column exists
            _lc = {r[1] for r in ov.execute("PRAGMA table_info(wt_watchtower_launches)").fetchall()}
            _create_time_col = "create_time" if "create_time" in _lc else "created_at"
            for r in ov.execute(
                f"SELECT treasury_wallet, COUNT(*) as total, "
                f"SUM(CASE WHEN {_create_time_col} > ? THEN 1 ELSE 0 END) as c24h "
                "FROM wt_watchtower_launches GROUP BY treasury_wallet",
                (cutoff,)
            ).fetchall():
                tw = r["treasury_wallet"]
                creates_total_map[tw] = r["total"] or 0
                creates_24h_map[tw] = r["c24h"] or 0

        # buy swarm counts per treasury (from wt_swarm_buys if available)
        swarm_map: dict = {}
        if _table_exists(ov, "wt_swarm_buys"):
            _sb_cols = {r[1] for r in ov.execute("PRAGMA table_info(wt_swarm_buys)").fetchall()}
            _tw_col = "treasury_wallet" if "treasury_wallet" in _sb_cols else None
            if _tw_col:
                for r in ov.execute(
                    f"SELECT {_tw_col}, COUNT(DISTINCT mint) as cnt FROM wt_swarm_buys GROUP BY {_tw_col}"
                ).fetchall():
                    swarm_map[r[_tw_col]] = r["cnt"] or 0

        # ── Treasury WS open status ───────────────────────────────────────────
        # webhook_active=1 means the cascade has this treasury subscribed
        treasury_ws_map: dict = {}   # treasury -> bool (WS open)
        treasury_ws_age_map: dict = {}  # treasury -> seconds since last hit
        if _table_exists(ov, "wt_confirmed_treasury_webhooks"):
            for r in ov.execute(
                "SELECT treasury, webhook_active, last_hit FROM wt_confirmed_treasury_webhooks"
            ).fetchall():
                treasury_ws_map[r["treasury"]] = bool(r["webhook_active"])
                lh = r["last_hit"] or 0
                treasury_ws_age_map[r["treasury"]] = now - lh if lh else None
        # also check wt_treasury_ws_usage for recent activity (supplement)
        if _table_exists(ov, "wt_treasury_ws_usage"):
            for r in ov.execute(
                "SELECT treasury_wallet, last_notif_at FROM wt_treasury_ws_usage"
            ).fetchall():
                tw2 = r["treasury_wallet"]
                if tw2 not in treasury_ws_map:
                    treasury_ws_map[tw2] = False
                lna = r["last_notif_at"] or 0
                if lna:
                    treasury_ws_age_map[tw2] = now - lna

        # ── Intel-only subprov counts ─────────────────────────────────────────
        intel_map: dict = {}
        if _table_exists(ov, "wt_active_subprov_sessions"):
            # sprov_col / sol_col already resolved in the live/armed block above
            for r in ov.execute(
                "SELECT treasury_wallet, "
                "SUM(CASE WHEN state='ACTIVE' AND monitoring_state='INTEL_ONLY' THEN 1 ELSE 0 END) as intel_count "
                "FROM wt_active_subprov_sessions GROUP BY treasury_wallet"
            ).fetchall():
                intel_map[r["treasury_wallet"]] = r["intel_count"] or 0

        # ── Assessment timeline per treasury ─────────────────────────────────
        # Reconstructed from watchtower_events (treasury-keyed events, recent 8)
        # Event types we care about, in display priority order
        _WE_LABELS = {
            "TREASURY_WEBSOCKET_OPENED":    ("TREASURY SUBSCRIBED",  "Treasury WS opened"),
            "SUBPROV_SESSION_OPENED_WS":    ("SUBPROV FUNDED",       "SubProv session opened"),
            "SUBPROV_SESSION_STARTED":      ("SUBPROV STARTED",      "SubProv session started"),
            "WRAP_CLOSE_FANOUT_DETECTED":   ("FAN-OUT DETECTED",     "Wrap-close fan-out observed"),
            "CANDIDATE_WEBSOCKET_OPENED":   ("CANDIDATE ARMED",      "ProgramWatcher armed on candidate"),
            "CANDIDATE_WATCH_EXPIRED":      ("CANDIDATE EXPIRED",    "Candidate watch expired without CREATE"),
            "CANDIDATE_CLASSIFIED_BUY_SWARM":("BUY-SWARM",          "Candidate classified as buy-swarm"),
            "SUBPROV_SESSION_EXPIRED":      ("SESSION EXPIRED",      "SubProv session expired"),
            "SUBPROV_SESSION_INTEL_ONLY":   ("INTEL ONLY",           "Session downgraded to intel-only"),
            "WATCHTOWER_LAUNCH_DETECTED":   ("CREATE DETECTED",      "Pump.fun CREATE detected"),
        }
        assessment_timeline_map: dict = {}  # tw -> list of {ts, label, detail}
        if _table_exists(ov, "watchtower_events"):
            # fetch the most recent meaningful events per treasury
            # wallet_address = the subprov/treasury; related_wallet = treasury context
            we_cols = {r[1] for r in ov.execute("PRAGMA table_info(watchtower_events)").fetchall()}
            if "created_at" in we_cols:
                _we_types = "','".join(_WE_LABELS.keys())
                for r in ov.execute(
                    f"SELECT wallet_address, related_wallet, event_type, created_at "
                    f"FROM watchtower_events "
                    f"WHERE event_type IN ('{_we_types}') "
                    f"ORDER BY created_at DESC LIMIT 2000"
                ).fetchall():
                    # treasury-keyed: TREASURY events use wallet_address; SUBPROV/CANDIDATE use related_wallet
                    et = r["event_type"]
                    if et in ("TREASURY_WEBSOCKET_OPENED",):
                        tw_key = r["wallet_address"]
                    else:
                        tw_key = r["related_wallet"]
                    if not tw_key:
                        continue
                    if tw_key not in assessment_timeline_map:
                        assessment_timeline_map[tw_key] = []
                    if len(assessment_timeline_map[tw_key]) < 8:
                        lbl, detail = _WE_LABELS.get(et, (et, et))
                        assessment_timeline_map[tw_key].append({
                            "ts": r["created_at"],
                            "label": lbl,
                            "detail": detail,
                            "event_type": et,
                        })

        # ── Similar campaign profiles (cross-treasury behavioural distance) ────
        # Uses hist_profile_map already built. Compute pairwise similarity at render time.
        # We'll pass the full profile map to the per-op payload so the frontend can display matches.
        # Pre-compute a compact profile list for comparison.
        _all_profiles: list[dict] = []
        for _tw, _p in hist_profile_map.items():
            if _p["sample_n"] >= 3:
                _all_profiles.append({
                    "treasury": _tw,
                    "avg_recipients": _p["avg_recipients"],
                    "avg_sol": _p["avg_sol"],
                    "create_rate": _p["create_rate"],
                    "swarm_rate": _p["swarm_rate"],
                    "sample_n": _p["sample_n"],
                })

        def _similar_campaigns(tw: str, top_n: int = 3) -> list[dict]:
            """Return top_n most behaviourally similar treasuries (excluding self)."""
            import math
            p = hist_profile_map.get(tw)
            if not p or p["sample_n"] < 3:
                return []
            results = []
            for other in _all_profiles:
                if other["treasury"] == tw:
                    continue
                # Normalised Euclidean distance on 4 dimensions
                dr = (p["avg_recipients"] - other["avg_recipients"]) / max(p["avg_recipients"], 1)
                ds = (p["avg_sol"] - other["avg_sol"]) / max(p["avg_sol"], 1)
                dc = p["create_rate"] - other["create_rate"]
                dw = p["swarm_rate"] - other["swarm_rate"]
                dist = math.sqrt(dr**2 + ds**2 + dc**2 + dw**2)
                # Convert distance to match % (0 dist → 100%, dist≥2 → ~0%)
                match_pct = max(0, round(100 * (1 - dist / 2)))
                if match_pct >= 40:
                    results.append({
                        "treasury": other["treasury"],
                        "match_pct": match_pct,
                        "sample_n": other["sample_n"],
                    })
            results.sort(key=lambda x: -x["match_pct"])
            return results[:top_n]

        # ── Behaviour model maturity per treasury ─────────────────────────────
        # Combines launch count + fanout sample count + last-launch age
        last_launch_map: dict = {}
        if _table_exists(ov, "wt_watchtower_launches"):
            _lc3 = {r[1] for r in ov.execute("PRAGMA table_info(wt_watchtower_launches)").fetchall()}
            _ct_col = "create_time" if "create_time" in _lc3 else "recorded_at"
            for r in ov.execute(
                f"SELECT treasury_wallet, MAX({_ct_col}) as last_launch "
                "FROM wt_watchtower_launches GROUP BY treasury_wallet"
            ).fetchall():
                last_launch_map[r["treasury_wallet"]] = r["last_launch"] or 0

        def _model_maturity(tw: str) -> dict:
            """Describe how mature the behavioural model is for this treasury."""
            t = launch_timing_map.get(tw, {})
            h = hist_profile_map.get(tw, {})
            launches = t.get("n", 0)
            fanout_n = int(h.get("sample_n", 0))
            last_launch = last_launch_map.get(tw)
            last_launch_age = (now - last_launch) if last_launch else None

            if launches >= 5 and fanout_n >= 20:
                stage = "MATURE"
                confidence = "HIGH"
                desc = f"{launches} confirmed launches · {fanout_n} fan-out observations"
            elif launches >= 2 and fanout_n >= 5:
                stage = "DEVELOPING"
                confidence = "MEDIUM"
                desc = f"{launches} confirmed launches · {fanout_n} fan-out observations"
            elif launches >= 1:
                stage = "EARLY"
                confidence = "LOW"
                desc = f"{launches} launch on record · needs {2 - launches} more for timing baseline"
            else:
                stage = "LEARNING"
                confidence = "INSUFFICIENT"
                desc = f"No confirmed launches yet · {fanout_n} fan-out observations"

            return {
                "stage": stage,
                "confidence": confidence,
                "desc": desc,
                "launches": launches,
                "fanout_n": fanout_n,
                "last_launch_age": last_launch_age,
            }

        # ── Origin (historical metadata only) ────────────────────────────────
        def _origin(src: str, ot: str) -> str:
            if ot == "MICRO_DEPLOYER":
                return "MICRO_DEPLOYER"
            if src == "watch_migration":
                return "DISCOVERED_VIA_MIGRATION"
            if src == "wrap_close_forward":
                return "LIVE_DETECTION"
            return src.upper() if src else "UNKNOWN"

        # ── Phase + monitoring state derivation ──────────────────────────────
        # op_phase      = lifecycle position (ACTIVE / POST_CREATE / DORMANT)
        # monitoring    = what WATCHTOWER is ACTUALLY doing RIGHT NOW
        #                 derived from live component states, not DB labels
        # Both are independent. A card with no live subprovs is never "WATCHING CREATE".

        _PHASE_ORDER = ["ACTIVE", "POST_CREATE", "DORMANT"]

        # assign sequential op numbers (sorted by created_at)
        sorted_rows = sorted(ops_rows, key=lambda r: r["created_at"] or 0)
        op_number_map = {r["operation_uuid"]: i + 1 for i, r in enumerate(sorted_rows)}

        operations = []
        for row in ops_rows:
            op_uuid = row["operation_uuid"]
            tw = row["treasury_root"]
            lc_state = lifecycle_map.get(op_uuid, "")
            live_sub = live_map.get(tw, 0)
            armed_sub = armed_map.get(tw, 0)
            intel_sub = intel_map.get(tw, 0)
            fanouts_24h = fanouts_24h_map.get(tw, 0)
            recipients_24h = recipients_24h_map.get(tw, 0)
            creates_total = creates_total_map.get(tw, 0)
            creates_24h = creates_24h_map.get(tw, 0)
            last_fanout = last_fanout_map.get(tw, 0)
            last_session = last_session_map.get(tw, 0)
            funding_rounds = funding_rounds_map.get(tw, 0)
            subprov_count = subprov_total_map.get(tw, 0)
            buy_swarms = swarm_map.get(tw, 0)
            active_focus = active_focus_map.get(tw)

            # ── Live component states ─────────────────────────────────────────
            treasury_ws_open = treasury_ws_map.get(tw, False)
            treasury_ws_age  = treasury_ws_age_map.get(tw)
            subprov_ws_open  = live_sub > intel_sub   # ACTIVE non-intel subprovs
            pw_open          = cand_map.get(tw, 0) > 0  # ProgramWatcher has actual candidates to match

            # ── Operation Phase (lifecycle position) ─────────────────────────
            # Simple 3-state: ACTIVE (something live), POST_CREATE (ran before), DORMANT
            if live_sub > 0 or (treasury_ws_open and lc_state in ("PROVISIONING", "")):
                op_phase = "ACTIVE"
            elif lc_state in ("MIGRATED", "REACTIVATED", "PROVISIONING"):
                op_phase = "POST_CREATE"
            else:
                op_phase = "DORMANT"

            # ── Monitoring State (what WATCHTOWER is doing NOW) ───────────────
            # Derived strictly from live component states.
            if pw_open:
                monitoring = "WATCHING CREATE"
            elif subprov_ws_open:
                monitoring = "WAITING FOR FAN-OUT"
            elif intel_sub > 0:
                monitoring = "INTEL ONLY"
            elif treasury_ws_open and lc_state in ("MIGRATED", "REACTIVATED"):
                monitoring = "MONITORING CONTINUATION"
            elif treasury_ws_open:
                monitoring = "WATCHING TREASURY"
            elif lc_state in ("MIGRATED", "REACTIVATED"):
                monitoring = "MONITORING CONTINUATION"
            elif lc_state == "PROVISIONING":
                monitoring = "WAITING FOR FUNDING"
            else:
                monitoring = "IDLE"

            # ── Origin (historical, metadata only) ───────────────────────────
            origin = _origin(row["source"] or "", row["op_type"] or "")

            # ── Confidence label ─────────────────────────────────────────────
            conf = row["confidence"] or 0
            conf_label = ("CERTAIN" if conf >= 1.0 else "HIGH" if conf >= 0.8
                          else "PROBABLE" if conf >= 0.6 else "POSSIBLE")

            # ── Next expected event (driven by monitoring state) ──────────────
            next_event_map = {
                "WATCHING CREATE":         "CREATE on pump.fun",
                "WAITING FOR FAN-OUT":     "SubProv wrap-close fan-out",
                "INTEL ONLY":              "Escalation to armed watch",
                "MONITORING CONTINUATION": "Treasury reactivation or new funding",
                "WATCHING TREASURY":       "New SubProv funding",
                "WAITING FOR FUNDING":     "Treasury to fund SubProv",
                "IDLE":                    "New treasury activity",
            }
            next_event = next_event_map.get(monitoring, "Unknown")

            # ── Lifecycle steps (operational journey, active stage highlighted) ─
            has_launched = creates_total > 0
            lc_steps = [
                {"step": "FUNDING",
                 "done": True,
                 "active": monitoring == "WAITING FOR FUNDING"},
                {"step": "FAN-OUT",
                 "done": fanouts_24h > 0 or has_launched,
                 "active": monitoring in ("WAITING FOR FAN-OUT", "WATCHING TREASURY")},
                {"step": "CREATE",
                 "done": has_launched,
                 "active": monitoring == "WATCHING CREATE"},
                {"step": "CONTINUATION",
                 "done": lc_state in ("MIGRATED", "REACTIVATED") and not (live_sub > 0),
                 "active": monitoring == "MONITORING CONTINUATION"},
            ]

            last_activity = max(last_fanout, last_session, row["updated_at"] or 0)

            # ── WATCHTOWER Assessment ─────────────────────────────────────────
            # Produces structured explanation fields — not just labels.
            # All derived from existing DB data. Zero RPC.
            #
            # Fields:
            #   health          ON_PATTERN / SLOW / UNUSUAL / ANOMALOUS / UNKNOWN / IDLE_WATCH
            #   timing_status   same scale, specifically for fanout→create timing
            #   timing_elapsed_s  seconds since last fanout (WATCHING CREATE) or session open
            #   timing_window   human range from historical data e.g. "1–6s"
            #   timing_samples  number of historical launches used
            #   score_value     0-100 operation score
            #   score_checks    list of {label, pass} for score breakdown
            #   belief          one-sentence "what do we believe"
            #   why             one-sentence "why"
            #   assessment      one-sentence "what does this imply"
            #   prediction_level  HIGH / MEDIUM / LOW
            #   prediction_text   "CREATE likely · Expected 1–6 seconds"
            #   action          recommended operator action
            #   hist_*          historical profile values for comparison
            #   unknown_reason  if UNKNOWN, explains the limitation

            def _fmt_elapsed(s: int) -> str:
                if s < 60:   return f"{s}s"
                if s < 3600: return f"{s//60}m {s%60}s"
                h = s // 3600; m = (s % 3600) // 60
                return f"{h}h {m}m" if m else f"{h}h"

            predictive: dict | None = None
            hist = hist_profile_map.get(tw)
            timing = launch_timing_map.get(tw)

            if monitoring == "WATCHING CREATE" and last_fanout:
                elapsed_s = int(now - last_fanout)
                has_history = timing and timing["n"] >= 2

                if has_history:
                    max_expected = max(timing["max_s"] * 3, 30)
                    if elapsed_s <= timing["max_s"] * 1.5:
                        timing_status = health = "ON_PATTERN"
                    elif elapsed_s <= max_expected:
                        timing_status = health = "SLOW"
                    elif elapsed_s <= max_expected * 10:
                        timing_status = health = "UNUSUAL"
                    else:
                        timing_status = health = "ANOMALOUS"

                    tw_str = f"{int(timing['min_s'])}–{int(timing['max_s'])}s"
                    elapsed_fmt = _fmt_elapsed(elapsed_s)

                    if health == "ON_PATTERN":
                        belief = "CREATE is imminent."
                        why    = f"Elapsed time ({elapsed_fmt}) is within the historical window of {tw_str}."
                        assessment = f"This treasury has completed {timing['n']} previous launches in this timing range."
                        prediction_level = "HIGH"
                        prediction_text = f"CREATE likely · Expected within {tw_str}"
                        action = "Continue CREATE monitoring. No action required."
                    elif health == "SLOW":
                        belief = "CREATE is still possible but delayed."
                        why    = f"Elapsed {elapsed_fmt} exceeds the typical window of {tw_str}."
                        assessment = "This is outside normal range but within an acceptable outer bound."
                        prediction_level = "MEDIUM"
                        prediction_text = f"CREATE possible · Timing slow vs historical {tw_str}"
                        action = "Monitor. If no CREATE within the next window, assess for abort."
                    elif health == "UNUSUAL":
                        belief = "CREATE probability is reduced."
                        why    = f"Elapsed {elapsed_fmt} is far outside the historical window of {tw_str}."
                        assessment = "This treasury may have aborted or delayed this campaign wave."
                        prediction_level = "LOW"
                        prediction_text = "Delayed · Immediate CREATE unlikely"
                        action = "Reduce attention. Monitor for new fan-out or treasury funding instead."
                    else:  # ANOMALOUS
                        belief = "This operation has broken from its historical creator pattern."
                        why    = f"Elapsed {elapsed_fmt} — historical maximum was {int(timing['max_s'])}s across {timing['n']} launches."
                        assessment = "The armed ProgramWatcher is still open but CREATE is very unlikely now."
                        prediction_level = "LOW"
                        prediction_text = "Likely abandoned · ProgramWatcher may be stale"
                        action = "Switch attention. Monitor treasury for new funding or reactivation signal."

                    score_checks = [
                        {"label": "Treasury fingerprint confirmed", "pass": True},
                        {"label": "Fan-out pattern observed",       "pass": (fanouts_24h > 0 or funding_rounds > 0)},
                        {"label": "Timing within historical window","pass": health == "ON_PATTERN"},
                        {"label": "ProgramWatcher armed",           "pass": pw_open},
                        {"label": f"Historical launches ≥ 2 ({timing['n']} found)", "pass": True},
                    ]
                    score_value = (100 if health == "ON_PATTERN"
                                   else 65 if health == "SLOW"
                                   else 30 if health == "UNUSUAL"
                                   else 10)
                else:
                    timing_status = health = "UNKNOWN"
                    tw_str = None
                    elapsed_fmt = _fmt_elapsed(elapsed_s)
                    belief = "Insufficient historical data to assess timing."
                    why    = f"Only {timing['n'] if timing else 0} confirmed launches recorded; minimum 2 required."
                    assessment = "WATCHTOWER cannot evaluate timing deviation without a baseline."
                    prediction_level = "MEDIUM"
                    prediction_text = "ProgramWatcher armed · timing unverifiable"
                    action = "Continue monitoring. Score will improve after the next confirmed launch."
                    score_checks = [
                        {"label": "Treasury fingerprint confirmed", "pass": True},
                        {"label": "ProgramWatcher armed",           "pass": pw_open},
                        {"label": "Historical launches ≥ 2",        "pass": False},
                        {"label": "Timing within historical window", "pass": False},
                    ]
                    score_value = 50
                    unknown_reason = (
                        f"Only {timing['n'] if timing else 0} confirmed launch{'es' if (timing['n'] if timing else 0)!=1 else ''} "
                        f"on record. Minimum 2 required for timing baseline."
                    )

                predictive = {
                    "health": health,
                    "timing_status": timing_status,
                    "timing_elapsed_s": elapsed_s,
                    "timing_elapsed_fmt": elapsed_fmt,
                    "timing_window": tw_str,
                    "timing_samples": timing["n"] if timing else 0,
                    "score_value": score_value,
                    "score_checks": score_checks,
                    "belief": belief,
                    "why": why,
                    "assessment": assessment,
                    "prediction_level": prediction_level,
                    "prediction_text": prediction_text,
                    "action": action,
                    "hist_avg_recipients": round(hist["avg_recipients"], 1) if hist else None,
                    "hist_avg_sol": round(hist["avg_sol"], 2) if hist else None,
                    "hist_create_rate": round(hist["create_rate"] * 100) if hist else None,
                    "unknown_reason": unknown_reason if health == "UNKNOWN" else None,
                }

            elif monitoring == "WAITING FOR FAN-OUT":
                session_age = (now - active_focus["opened_at"]
                               if active_focus and active_focus.get("opened_at") else None)
                create_rate_pct = round(hist["create_rate"] * 100) if hist else None
                elapsed_fmt = _fmt_elapsed(int(session_age)) if session_age else "unknown"

                if session_age is not None and session_age > 3600:
                    health = "SLOW"
                    belief = "Fan-out is delayed beyond the expected window."
                    why    = f"SubProv session open for {elapsed_fmt} without a wrap-close fan-out."
                    assessment = "Most funded subprovs produce a fan-out within 30 minutes."
                    prediction_level = "MEDIUM"
                    prediction_text = "Fan-out delayed · May still occur"
                    action = "Continue monitoring. Treasury may be staging or waiting for market conditions."
                elif session_age is not None and session_age > 900:
                    health = "UNUSUAL"
                    belief = "Fan-out is taking longer than typical."
                    why    = f"Session open {elapsed_fmt}."
                    assessment = "Delayed but not yet anomalous."
                    prediction_level = "MEDIUM"
                    prediction_text = "Waiting · Fan-out expected soon"
                    action = "Monitor. No action required yet."
                else:
                    health = "ON_PATTERN"
                    belief = "SubProv is active and expected to fan-out imminently."
                    why    = "Session was opened recently and is within the normal pre-fanout window."
                    assessment = f"Historical create rate for this treasury is {create_rate_pct}%." if create_rate_pct else "Awaiting fan-out."
                    prediction_level = "HIGH"
                    prediction_text = "Fan-out expected · Watching for wrap-close"
                    action = "ProgramWatcher will arm automatically on fan-out. No action required."

                score_checks = [
                    {"label": "Treasury fingerprint confirmed", "pass": True},
                    {"label": "SubProv session active",         "pass": True},
                    {"label": "Session within timing window",   "pass": health == "ON_PATTERN"},
                    {"label": f"Historical create rate {create_rate_pct}%+", "pass": (create_rate_pct or 0) >= 50},
                ]
                predictive = {
                    "health": health,
                    "timing_elapsed_s": int(session_age) if session_age else None,
                    "timing_elapsed_fmt": elapsed_fmt,
                    "timing_samples": timing["n"] if timing else 0,
                    "score_value": 80 if health == "ON_PATTERN" else 55,
                    "score_checks": score_checks,
                    "belief": belief,
                    "why": why,
                    "assessment": assessment,
                    "prediction_level": prediction_level,
                    "prediction_text": prediction_text,
                    "action": action,
                    "hist_create_rate": create_rate_pct,
                    "hist_avg_recipients": round(hist["avg_recipients"], 1) if hist else None,
                }

            elif monitoring in ("WATCHING TREASURY", "MONITORING CONTINUATION"):
                last_activity_age = now - last_activity if last_activity else None
                elapsed_fmt = _fmt_elapsed(int(last_activity_age)) if last_activity_age else "unknown"
                has_history = creates_total > 0

                if has_history:
                    health = "ON_PATTERN"
                    belief = f"Treasury is established with {creates_total} confirmed launch{'es' if creates_total!=1 else ''}."
                    why    = "Continuation monitoring follows a completed campaign wave."
                    assessment = "Treasury may reactivate with new funding or a fresh SubProv."
                    prediction_level = "MEDIUM"
                    prediction_text = f"Reactivation possible · {creates_total} prior launches"
                    action = "Monitor treasury for new funding events. No immediate action required."
                else:
                    health = "IDLE_WATCH"
                    belief = "Treasury is subscribed but no launches have been recorded."
                    why    = "No confirmed creator fan-outs or pump.fun CREATEs observed."
                    assessment = "This treasury may be in a pre-campaign state or may not be a launch operator."
                    prediction_level = "LOW"
                    prediction_text = "No launch history · Watching for first activity"
                    action = "Monitor. If no activity within 7 days, consider rotating to a higher-signal target."

                score_checks = [
                    {"label": "Treasury fingerprint confirmed", "pass": True},
                    {"label": "Treasury WS subscribed",         "pass": treasury_ws_open},
                    {"label": f"Prior launches observed ({creates_total})", "pass": has_history},
                    {"label": "Recent activity (< 24h)",        "pass": (last_activity_age or 99999) < 86400},
                ]
                predictive = {
                    "health": health,
                    "timing_elapsed_s": int(last_activity_age) if last_activity_age else None,
                    "timing_elapsed_fmt": elapsed_fmt,
                    "timing_samples": timing["n"] if timing else 0,
                    "score_value": 65 if has_history else 35,
                    "score_checks": score_checks,
                    "belief": belief,
                    "why": why,
                    "assessment": assessment,
                    "prediction_level": prediction_level,
                    "prediction_text": prediction_text,
                    "action": action,
                    "hist_create_rate": round(hist["create_rate"] * 100) if hist else None,
                    "hist_avg_recipients": round(hist["avg_recipients"], 1) if hist else None,
                }

            elif monitoring == "INTEL ONLY":
                health = "ON_PATTERN"
                predictive = {
                    "health": health,
                    "timing_samples": timing["n"] if timing else 0,
                    "score_value": 60,
                    "score_checks": [
                        {"label": "Treasury fingerprint confirmed", "pass": True},
                        {"label": "SubProv under observation",      "pass": True},
                        {"label": "Armed session active",           "pass": False},
                    ],
                    "belief": "SubProv is being tracked in intelligence-only mode.",
                    "why": "Session exists but monitoring state is INTEL_ONLY — not yet escalated to armed watch.",
                    "assessment": "Waiting for criteria to escalate to ProgramWatcher.",
                    "prediction_level": "MEDIUM",
                    "prediction_text": "Watching for escalation trigger",
                    "action": "No action required. WATCHTOWER will escalate automatically on qualifying fan-out.",
                    "hist_create_rate": round(hist["create_rate"] * 100) if hist else None,
                }

            elif monitoring == "WAITING FOR FUNDING":
                health = "ON_PATTERN"
                predictive = {
                    "health": health,
                    "timing_samples": timing["n"] if timing else 0,
                    "score_value": 50,
                    "score_checks": [
                        {"label": "Treasury fingerprint confirmed", "pass": True},
                        {"label": "SubProv funding observed",       "pass": False},
                    ],
                    "belief": "Treasury is confirmed but no SubProv has been funded yet.",
                    "why": "No wrap-close events or active sessions detected.",
                    "assessment": "Campaign may be in a pre-launch preparation stage.",
                    "prediction_level": "LOW",
                    "prediction_text": "Waiting for treasury to fund a SubProv",
                    "action": "Monitor treasury outbound transfers. No action required.",
                }

            # ── Enrich predictive with timeline, similar campaigns, model maturity ─
            model = _model_maturity(tw)
            if predictive is not None:
                predictive["model"] = model
                predictive["similar_campaigns"] = _similar_campaigns(tw)
                # Enrich score_checks with model maturity check
                if "score_checks" in predictive:
                    predictive["score_checks"].append({
                        "label": f"Behaviour model {model['stage'].lower()} ({model['launches']} launches, n={model['fanout_n']})",
                        "pass": model["stage"] in ("MATURE", "DEVELOPING"),
                    })

            # ── Active focus (current subprov being monitored) ────────────────
            focus = None
            if active_focus and (subprov_ws_open or pw_open or intel_sub > 0):
                ms = active_focus["state"]
                focus = {
                    "subprov": active_focus["subprov"],
                    "opened_ago": now - active_focus["opened_at"] if active_focus["opened_at"] else None,
                    "sol": active_focus["sol"],
                    "monitoring_state": ms,
                    "pw_open": pw_open,
                    "subprov_ws_open": subprov_ws_open,
                    "treasury_ws_open": treasury_ws_open,
                }

            operations.append({
                "treasury": tw,
                "treasury_short": tw[-4:] if tw else "????",
                "op_id": op_uuid,
                "op_number": op_number_map.get(op_uuid, 0),
                "op_phase": op_phase,
                "monitoring": monitoring,
                "origin": origin,
                "confidence": conf_label,
                "lifecycle_state": lc_state,
                "next_event": next_event,
                "treasury_ws_open": treasury_ws_open,
                "treasury_ws_age": treasury_ws_age,
                "subprov_ws_open": subprov_ws_open,
                "pw_open": pw_open,
                "live_subprovs": live_sub,
                "armed_subprovs": armed_sub,
                "intel_subprovs": intel_sub,
                "fanouts_24h": fanouts_24h,
                "recipients_24h": recipients_24h,
                "creates_total": creates_total,
                "creates_24h": creates_24h,
                "funding_rounds": funding_rounds,
                "subprov_count": subprov_count,
                "buy_swarms": buy_swarms,
                "last_fanout": last_fanout,
                "last_session": last_session,
                "last_activity": last_activity,
                "last_activity_ago": now - last_activity if last_activity else None,
                "lifecycle_steps": lc_steps,
                "active_focus": focus,
                "predictive": predictive,
                "assessment_timeline": assessment_timeline_map.get(tw, []),
            })

        # sort by monitoring state urgency, then last_activity desc
        _MON_ORDER = ["WATCHING CREATE", "WAITING FOR FAN-OUT", "INTEL ONLY",
                      "WATCHING TREASURY", "MONITORING CONTINUATION",
                      "WAITING FOR FUNDING", "IDLE"]
        mon_idx = {m: i for i, m in enumerate(_MON_ORDER)}
        operations.sort(key=lambda x: (mon_idx.get(x["monitoring"], 99), -(x["last_activity"] or 0)))

        # phase_counts keyed by monitoring state (what UI phase pills show)
        phase_counts: dict = {m: 0 for m in _MON_ORDER}
        for o in operations:
            mon = o["monitoring"]
            phase_counts[mon] = phase_counts.get(mon, 0) + 1

        return jsonify({"operations": operations, "phase_counts": phase_counts, "now": now})
    except Exception as e:
        return jsonify({"operations": [], "phase_counts": {}, "now": now, "error": str(e)})
    finally:
        ov.close()


@ops_dashboard_bp.route("/api/ops/timeline")
def api_ops_timeline():
    """Unified event timeline for all active subprovs (or a specific one via ?subprov=).
    Returns FUNDING / FANOUT / CREATE / SWARM events sorted by time desc. Max 200 events."""
    subprov_filter = request.args.get("subprov")
    limit = min(int(request.args.get("limit", 200)), 500)
    ov = _conn()
    try:
        now = int(time.time())
        events = []

        # FUNDING events from active sessions
        if _table_exists(ov, "wt_active_subprov_sessions"):
            q = ("SELECT 'FUNDING' as etype, detected_at as etime, subprov_wallet as subprov, "
                 "treasury_wallet as treasury, funding_amount as amount, NULL as count, "
                 "open_reason as detail, NULL as mint "
                 "FROM wt_active_subprov_sessions WHERE state='ACTIVE'")
            params: list = []
            if subprov_filter:
                q += " AND subprov_wallet=?"; params.append(subprov_filter)
            for r in ov.execute(q, params).fetchall():
                events.append(dict(r))

        # FANOUT events
        if _table_exists(ov, "wt_fanout_events"):
            q = ("SELECT 'FANOUT' as etype, fanout_time as etime, subprov_wallet as subprov, "
                 "treasury_wallet as treasury, total_sol as amount, fanout_count as count, "
                 "CASE WHEN creates_fired>0 THEN 'CREATOR_BURST' "
                 "     WHEN buy_swarms>0 THEN 'SWARM_BURST' "
                 "     ELSE 'UNKNOWN' END as detail, NULL as mint "
                 "FROM wt_fanout_events")
            params = []
            if subprov_filter:
                q += " WHERE subprov_wallet=?"; params.append(subprov_filter)
            for r in ov.execute(q, params).fetchall():
                events.append(dict(r))

        # CREATE events from watchtower_launches
        if _table_exists(ov, "wt_watchtower_launches"):
            q = ("SELECT 'CREATE' as etype, create_time as etime, subprov_wallet as subprov, "
                 "treasury_wallet as treasury, wrap_close_sol as amount, NULL as count, "
                 "launch_mode as detail, mint "
                 "FROM wt_watchtower_launches")
            params = []
            if subprov_filter:
                q += " WHERE subprov_wallet=?"; params.append(subprov_filter)
            for r in ov.execute(q, params).fetchall():
                events.append(dict(r))

        # SWARM events (one per distinct mint, most recent observed_at)
        if _table_exists(ov, "wt_swarm_buys"):
            q = ("SELECT 'SWARM' as etype, MAX(observed_at) as etime, subprov_wallet as subprov, "
                 "treasury_wallet as treasury, NULL as amount, COUNT(*) as count, "
                 "NULL as detail, mint "
                 "FROM wt_swarm_buys")
            params = []
            if subprov_filter:
                q += " WHERE subprov_wallet=?"; params.append(subprov_filter)
            q += " GROUP BY mint, subprov_wallet"
            for r in ov.execute(q, params).fetchall():
                events.append(dict(r))

        events.sort(key=lambda x: x.get("etime") or 0, reverse=True)
        events = events[:limit]
        for e in events:
            t = e.get("etime")
            e["ago_s"] = (now - t) if t else None
        return jsonify({"events": events, "total": len(events), "now": now})
    except Exception as e:
        return jsonify({"events": [], "total": 0, "error": str(e)})
    finally:
        ov.close()


@ops_dashboard_bp.route("/api/ops/fanout-intelligence")
def api_ops_fanout_intelligence():
    """Fanout burst table.
    Default (?grouped=1, the default): one row per subprov — total bursts, recipients, creates, swarms.
    Raw (?grouped=0): one row per fanout event, ordered by time desc (original behaviour)."""
    limit = min(int(request.args.get("limit", 200)), 1000)
    grouped = request.args.get("grouped", "1") != "0"
    ov = _conn()
    try:
        now = int(time.time())
        if not _table_exists(ov, "wt_fanout_events"):
            return jsonify({"bursts": [], "rolling": {}, "total": 0, "grouped": grouped})

        bursts = []
        if grouped:
            for r in ov.execute(
                "SELECT subprov_wallet, treasury_wallet, "
                "COUNT(*) as burst_count, "
                "SUM(fanout_count) as total_recipients, "
                "SUM(total_sol) as total_sol, "
                "AVG(avg_sol) as avg_sol, "
                "MAX(fanout_count) as max_burst_size, "
                "MIN(fanout_count) as min_burst_size, "
                "SUM(creates_fired) as creates, "
                "SUM(buy_swarms) as swarms, "
                "MAX(fanout_time) as last_fanout, "
                "MIN(fanout_time) as first_fanout, "
                "SUM(CASE WHEN has_identical_amounts THEN 1 ELSE 0 END) as identical_count "
                "FROM wt_fanout_events GROUP BY subprov_wallet "
                "ORDER BY last_fanout DESC LIMIT ?", (limit,)
            ).fetchall():
                creates = r["creates"] or 0
                swarms = r["swarms"] or 0
                burst_type = ("CREATOR" if creates > 0 else "SWARM" if swarms > 0 else "UNKNOWN")
                bursts.append({
                    "subprov": r["subprov_wallet"],
                    "treasury": r["treasury_wallet"],
                    "burst_count": r["burst_count"],
                    "total_recipients": r["total_recipients"] or 0,
                    "total_sol": r["total_sol"],
                    "avg_sol": r["avg_sol"],
                    "max_burst_size": r["max_burst_size"],
                    "min_burst_size": r["min_burst_size"],
                    "creates": creates,
                    "swarms": swarms,
                    "last_fanout": r["last_fanout"],
                    "first_fanout": r["first_fanout"],
                    "last_fanout_ago": now - (r["last_fanout"] or now),
                    "identical_bursts": r["identical_count"] or 0,
                    "burst_type": burst_type,
                    "grouped": True,
                })
        else:
            for r in ov.execute(
                "SELECT subprov_wallet, treasury_wallet, fanout_time, fanout_count, total_sol, "
                "largest_sol, smallest_sol, avg_sol, has_identical_amounts, "
                "creates_fired, buy_swarms "
                "FROM wt_fanout_events ORDER BY fanout_time DESC LIMIT ?", (limit,)
            ).fetchall():
                burst_type = ("CREATOR" if r["creates_fired"] > 0
                              else "SWARM" if r["buy_swarms"] > 0
                              else "UNKNOWN")
                bursts.append({
                    "subprov": r["subprov_wallet"],
                    "treasury": r["treasury_wallet"],
                    "fanout_time": r["fanout_time"],
                    "ago_s": now - (r["fanout_time"] or now),
                    "recipients": r["fanout_count"],
                    "total_sol": r["total_sol"],
                    "largest_sol": r["largest_sol"],
                    "smallest_sol": r["smallest_sol"],
                    "avg_sol": r["avg_sol"],
                    "identical_amounts": bool(r["has_identical_amounts"]),
                    "creates": r["creates_fired"] or 0,
                    "swarms": r["buy_swarms"] or 0,
                    "burst_type": burst_type,
                    "grouped": False,
                })

        # rolling 30s / 60s / 120s aggregates
        rolling = {}
        for window_s, label in [(30, "30s"), (60, "60s"), (120, "120s")]:
            cutoff = now - window_s
            row = ov.execute(
                "SELECT COUNT(*) bursts, SUM(fanout_count) recipients, "
                "SUM(creates_fired) creates, SUM(buy_swarms) swarms, SUM(total_sol) sol "
                "FROM wt_fanout_events WHERE fanout_time >= ?", (cutoff,)
            ).fetchone()
            rolling[label] = {
                "bursts": row["bursts"] or 0,
                "recipients": row["recipients"] or 0,
                "creates": row["creates"] or 0,
                "swarms": row["swarms"] or 0,
                "sol": round(row["sol"] or 0, 3),
            }

        # summary stats
        stats_row = ov.execute(
            "SELECT COUNT(*) total, SUM(fanout_count) total_recipients, "
            "SUM(creates_fired) total_creates, SUM(buy_swarms) total_swarms, "
            "AVG(fanout_count) avg_burst_size "
            "FROM wt_fanout_events"
        ).fetchone()

        return jsonify({
            "bursts": bursts,
            "rolling": rolling,
            "total": stats_row["total"] or 0,
            "total_recipients": stats_row["total_recipients"] or 0,
            "total_creates": stats_row["total_creates"] or 0,
            "total_swarms": stats_row["total_swarms"] or 0,
            "avg_burst_size": round(stats_row["avg_burst_size"] or 0, 1),
            "now": now,
        })
    except Exception as e:
        return jsonify({"bursts": [], "rolling": {}, "total": 0, "error": str(e)})
    finally:
        ov.close()


@ops_dashboard_bp.route("/api/ops/subprov-intelligence")
def api_ops_subprov_intelligence():
    """SubProv dossier: per-subprov behavioural profile from wt_discovered_subprovs
    enriched with live session + fanout + launch stats."""
    limit = min(int(request.args.get("limit", 200)), 1000)
    ov = _conn()
    try:
        now = int(time.time())
        rows = []

        if not _table_exists(ov, "wt_discovered_subprovs"):
            return jsonify({"subprovs": [], "total": 0})

        discovered = {
            r["subprov"]: dict(r)
            for r in ov.execute("SELECT * FROM wt_discovered_subprovs ORDER BY last_seen DESC").fetchall()
        }

        # live session map
        sessions: dict = {}
        if _table_exists(ov, "wt_active_subprov_sessions"):
            for r in ov.execute(
                "SELECT subprov_wallet, treasury_wallet, funding_amount, initial_funding_amount, "
                "topup_count, topup_amount_total, monitoring_state, open_reason, detected_at, "
                "funding_sequence_number, COALESCE(treasury_rotated,0) as treasury_rotated "
                "FROM wt_active_subprov_sessions WHERE state='ACTIVE'"
            ).fetchall():
                sessions[r["subprov_wallet"]] = dict(r)

        # fanout stats
        fanout: dict = {}
        if _table_exists(ov, "wt_fanout_events"):
            for r in ov.execute(
                "SELECT subprov_wallet, COUNT(*) bursts, SUM(fanout_count) recipients, "
                "SUM(creates_fired) creates, SUM(buy_swarms) swarms, "
                "MIN(fanout_time) first_fanout, MAX(fanout_time) last_fanout, "
                "AVG(fanout_count) avg_burst_size "
                "FROM wt_fanout_events GROUP BY subprov_wallet"
            ).fetchall():
                fanout[r["subprov_wallet"]] = dict(r)

        # launch stats
        launches: dict = {}
        if _table_exists(ov, "wt_watchtower_launches"):
            for r in ov.execute(
                "SELECT subprov_wallet, COUNT(*) n, MIN(create_time) first_create, "
                "MAX(create_time) last_create, AVG(birth_to_launch_seconds) avg_birth_to_launch "
                "FROM wt_watchtower_launches GROUP BY subprov_wallet"
            ).fetchall():
                launches[r["subprov_wallet"]] = dict(r)

        # candidate watch counts
        cand_counts: dict = {}
        if _table_exists(ov, "wt_candidate_websocket_watches"):
            for r in ov.execute(
                "SELECT subprov_wallet, COUNT(*) total, "
                "SUM(CASE WHEN state='WATCHING' THEN 1 ELSE 0 END) watching, "
                "SUM(CASE WHEN state='RESOLVED_CREATE' THEN 1 ELSE 0 END) resolved_create "
                "FROM wt_candidate_websocket_watches GROUP BY subprov_wallet"
            ).fetchall():
                cand_counts[r["subprov_wallet"]] = dict(r)

        for sp, d in list(discovered.items())[:limit]:
            sess = sessions.get(sp)
            fo = fanout.get(sp, {})
            la = launches.get(sp, {})
            cc = cand_counts.get(sp, {})

            # data quality flags
            dq = []
            if not fo:
                dq.append("NO_FANOUT_HISTORY")
            if not la:
                dq.append("NO_CONFIRMED_CREATES")
            if d.get("subprov_type") == "UNKNOWN":
                dq.append("TYPE_UNCLASSIFIED")
            if not sess:
                dq.append("NOT_LIVE")

            rows.append({
                "subprov": sp,
                "treasury": d.get("treasury"),
                "treasury_known": bool(d.get("treasury_known")),
                "state": d.get("state"),
                "confidence": d.get("confidence"),
                "subprov_type": d.get("subprov_type") or "UNKNOWN",
                "rejected_reason": d.get("rejected_reason"),
                # lifetime stats from discovered table
                "creator_count": d.get("creator_count") or 0,
                "wrap_close_count": d.get("wrap_close_count") or 0,
                "buy_swarm_count": d.get("buy_swarm_count") or 0,
                "create_count": d.get("create_count") or 0,
                "buy_swarm_ratio": round(d.get("buy_swarm_ratio") or 0, 3),
                "topup_count_hist": d.get("topup_count") or 0,
                "first_seen": d.get("first_seen"),
                "last_seen": d.get("last_seen"),
                "first_seen_ago": (now - d["first_seen"]) if d.get("first_seen") else None,
                "last_seen_ago": (now - d["last_seen"]) if d.get("last_seen") else None,
                # live session (LIVE)
                "live": bool(sess),
                "monitoring_state": sess["monitoring_state"] if sess else None,
                "open_reason": sess["open_reason"] if sess else None,
                "funding_sequence_number": sess["funding_sequence_number"] if sess else None,
                "treasury_rotated": bool(sess["treasury_rotated"]) if sess else False,
                "live_sol": ((sess["initial_funding_amount"] or sess["funding_amount"] or 0) + (sess["topup_amount_total"] or 0)) if sess else None,
                # fanout intel (DERIVED from wt_fanout_events)
                "fanout_bursts": fo.get("bursts") or 0,
                "fanout_recipients": fo.get("recipients") or 0,
                "fanout_creates": fo.get("creates") or 0,
                "fanout_swarms": fo.get("swarms") or 0,
                "avg_burst_size": round(fo.get("avg_burst_size") or 0, 1),
                "first_fanout": fo.get("first_fanout"),
                "last_fanout": fo.get("last_fanout"),
                # confirmed launches (LIVE)
                "confirmed_creates": la.get("n") or 0,
                "first_create": la.get("first_create"),
                "last_create": la.get("last_create"),
                "avg_birth_to_launch": round(la.get("avg_birth_to_launch") or 0, 1),
                # candidate watches (DERIVED)
                "candidates_total": cc.get("total") or 0,
                "candidates_watching": cc.get("watching") or 0,
                "candidates_resolved_create": cc.get("resolved_create") or 0,
                "data_quality": dq,
            })

        rows.sort(key=lambda x: x["last_seen"] or 0, reverse=True)
        return jsonify({"subprovs": rows, "total": len(rows), "now": now})
    except Exception as e:
        return jsonify({"subprovs": [], "total": 0, "error": str(e)})
    finally:
        ov.close()


@ops_dashboard_bp.route("/api/ops/launch-intelligence")
def api_ops_launch_intelligence():
    """Launch Intelligence: all confirmed WATCHTOWER launches with audit data where available."""
    ov = _conn()
    try:
        now = int(time.time())
        if not _table_exists(ov, "wt_watchtower_launches"):
            return jsonify({"launches": [], "total": 0})

        launches_raw = ov.execute(
            "SELECT * FROM wt_watchtower_launches ORDER BY create_time DESC"
        ).fetchall()

        # audit data keyed by mint
        audit_map: dict = {}
        if _table_exists(ov, "wt_launch_audit"):
            for r in ov.execute(
                "SELECT mint, detection_latency_ms, fetch_latency_ms, alert_latency_ms, "
                "mc_at_create, mc_at_detection, mc_at_first_external_buy, "
                "peak_mc, current_mc, time_to_peak_s, retrace_from_peak_pct, "
                "actionable_multiple, peak_multiple_from_first_external, "
                "seconds_from_detection_to_peak, final_state, audit_state, "
                "buys_before_first_external_buy, buys_before_detection, "
                "mc_at_create_source, mc_at_detection_source, migrated, migration_time "
                "FROM wt_launch_audit"
            ).fetchall():
                audit_map[r["mint"]] = dict(r)

        launches = []
        for r in launches_raw:
            mint = r["mint"]
            audit = audit_map.get(mint, {})
            dq = []
            if not audit:
                dq.append("NO_AUDIT")
            elif audit.get("audit_state") not in ("FINALIZED", "PEAK_CAPTURED"):
                dq.append(f"AUDIT_{audit.get('audit_state','UNKNOWN')}")
            if not r["wrap_close_signature"]:
                dq.append("NO_WRAP_SIG")
            if r["fanout_time"] is None:
                dq.append("NO_FANOUT_TIME")

            am = audit.get("actionable_multiple")
            launches.append({
                "mint": mint,
                "creator": r["creator_wallet"],
                "subprov": r["subprov_wallet"],
                "treasury": r["treasury_wallet"],
                "create_time": r["create_time"],
                "create_ago": (now - r["create_time"]) if r["create_time"] else None,
                "launch_mode": r["launch_mode"] or "UNKNOWN",
                "confidence": r["confidence"],
                "birth_to_launch_s": r["birth_to_launch_seconds"],
                "fanout_count": r["fanout_count"],
                "fanout_to_create_s": r["fanout_to_create_secs"],
                "create_to_migration_s": r["create_to_migration_secs"],
                # audit fields (FORWARD-ONLY — only present if audit ran)
                "detection_latency_ms": audit.get("detection_latency_ms"),
                "mc_at_create": audit.get("mc_at_create"),
                "mc_at_detection": audit.get("mc_at_detection"),
                "peak_mc": audit.get("peak_mc"),
                "actionable_multiple": am,
                "actionable_bucket": (
                    ">10x" if am and am >= 10 else
                    "5-10x" if am and am >= 5 else
                    "2-5x" if am and am >= 2 else
                    "1.5-2x" if am and am >= 1.5 else
                    "<1.5x" if am else None
                ),
                "time_to_peak_s": audit.get("time_to_peak_s"),
                "final_state": audit.get("final_state") or "UNKNOWN",
                "audit_state": audit.get("audit_state"),
                "migrated": bool(audit.get("migrated")),
                "data_quality": dq,
            })

        # summary stats
        audited = [l for l in launches if l["actionable_multiple"] is not None]
        migrated = [l for l in launches if l["migrated"]]
        buckets = {}
        for b in (">10x", "5-10x", "2-5x", "1.5-2x", "<1.5x"):
            buckets[b] = len([l for l in audited if l["actionable_bucket"] == b])

        return jsonify({
            "launches": launches,
            "total": len(launches),
            "audited": len(audited),
            "migrated": len(migrated),
            "actionable_buckets": buckets,
            "median_multiple": sorted([l["actionable_multiple"] for l in audited])[len(audited)//2] if audited else None,
            "now": now,
        })
    except Exception as e:
        return jsonify({"launches": [], "total": 0, "error": str(e)})
    finally:
        ov.close()


@ops_dashboard_bp.route("/api/ops/post-create-activity")
def api_ops_post_create_activity():
    """Post-CREATE activity: subprovs that have at least one confirmed create,
    showing subsequent fanout/swarm/reactivation behaviour."""
    ov = _conn()
    try:
        now = int(time.time())
        if not _table_exists(ov, "wt_watchtower_launches"):
            return jsonify({"subprovs": [], "total": 0})

        # subprovs with at least one confirmed create
        launched = {
            r["subprov_wallet"]: dict(r)
            for r in ov.execute(
                "SELECT subprov_wallet, COUNT(*) creates, MIN(create_time) first_create, "
                "MAX(create_time) last_create, treasury_wallet "
                "FROM wt_watchtower_launches GROUP BY subprov_wallet"
            ).fetchall()
        }

        # post-create fanout activity (fanout events AFTER last create)
        post_fanout: dict = {}
        if _table_exists(ov, "wt_fanout_events"):
            for sp, la in launched.items():
                rows = ov.execute(
                    "SELECT COUNT(*) bursts, SUM(fanout_count) recipients, SUM(creates_fired) creates "
                    "FROM wt_fanout_events WHERE subprov_wallet=? AND fanout_time > ?",
                    (sp, la["last_create"])
                ).fetchone()
                if rows and rows["bursts"]:
                    post_fanout[sp] = dict(rows)

        # live session info
        sessions: dict = {}
        if _table_exists(ov, "wt_active_subprov_sessions"):
            for r in ov.execute(
                "SELECT subprov_wallet, monitoring_state, open_reason, detected_at, expires_at "
                "FROM wt_active_subprov_sessions WHERE state='ACTIVE'"
            ).fetchall():
                sessions[r["subprov_wallet"]] = dict(r)

        # swarm activity per subprov
        swarms: dict = {}
        if _table_exists(ov, "wt_swarm_buys"):
            for r in ov.execute(
                "SELECT subprov_wallet, COUNT(DISTINCT mint) tokens, COUNT(*) total_buys "
                "FROM wt_swarm_buys GROUP BY subprov_wallet"
            ).fetchall():
                swarms[r["subprov_wallet"]] = dict(r)

        result = []
        for sp, la in launched.items():
            sess = sessions.get(sp)
            pf = post_fanout.get(sp, {})
            sw = swarms.get(sp, {})

            # op_phase derived from whether we're live
            is_live = bool(sess)
            has_post_fanout = bool(pf)
            op_phase = "POST_CREATE_ACTIVE" if (is_live and sess.get("monitoring_state") == "POST_CREATE_ACTIVE") else (
                "POST_CREATE_LIVE" if is_live else "DORMANT"
            )

            result.append({
                "subprov": sp,
                "treasury": la["treasury_wallet"],
                "creates": la["creates"],
                "first_create": la["first_create"],
                "last_create": la["last_create"],
                "last_create_ago": (now - la["last_create"]) if la["last_create"] else None,
                "op_phase": op_phase,
                # live session (LIVE)
                "live": is_live,
                "monitoring_state": sess["monitoring_state"] if sess else None,
                "open_reason": sess["open_reason"] if sess else None,
                "ttl_remaining": max(0, (sess["expires_at"] or now) - now) if sess else None,
                # post-create fanout (DERIVED — fanout events AFTER last create)
                "post_create_bursts": pf.get("bursts") or 0,
                "post_create_recipients": pf.get("recipients") or 0,
                "post_create_further_creates": pf.get("creates") or 0,
                # swarm activity (DERIVED)
                "swarm_tokens": sw.get("tokens") or 0,
                "swarm_total_buys": sw.get("total_buys") or 0,
                # data quality
                "data_quality": (
                    (["POST_FANOUT_ACTIVE"] if has_post_fanout else ["NO_POST_FANOUT"]) +
                    ([] if is_live else ["SESSION_EXPIRED"])
                ),
            })

        result.sort(key=lambda x: x["last_create"] or 0, reverse=True)
        return jsonify({"subprovs": result, "total": len(result), "now": now})
    except Exception as e:
        return jsonify({"subprovs": [], "total": 0, "error": str(e)})
    finally:
        ov.close()


@ops_dashboard_bp.route("/api/ops/miss-analysis")
def api_ops_miss_analysis():
    """Miss Analysis: subprovs where creates fired without a confirmed WATCHTOWER detection.
    Sources: wt_discovered_subprovs create_count vs wt_watchtower_launches,
    wt_unconfirmed_watchtower_like, candidate watches resolved outside pipeline."""
    ov = _conn()
    try:
        now = int(time.time())

        # confirmed launches per subprov
        confirmed: dict = {}
        if _table_exists(ov, "wt_watchtower_launches"):
            for r in ov.execute(
                "SELECT subprov_wallet, COUNT(*) n FROM wt_watchtower_launches GROUP BY subprov_wallet"
            ).fetchall():
                confirmed[r["subprov_wallet"]] = r["n"]

        # discovered subprovs with create activity
        discovered_creates = []
        if _table_exists(ov, "wt_discovered_subprovs"):
            for r in ov.execute(
                "SELECT subprov, treasury, creator_count, create_count, wrap_close_count, "
                "buy_swarm_count, first_seen, last_seen, state, confidence "
                "FROM wt_discovered_subprovs WHERE creator_count > 0 ORDER BY creator_count DESC"
            ).fetchall():
                sp = r["subprov"]
                conf = confirmed.get(sp, 0)
                gap = (r["creator_count"] or 0) - conf
                discovered_creates.append({
                    "subprov": sp,
                    "treasury": r["treasury"],
                    "creator_count": r["creator_count"] or 0,
                    "confirmed_launches": conf,
                    "detection_gap": max(0, gap),
                    "detection_rate": round(conf / r["creator_count"], 3) if r["creator_count"] else None,
                    "wrap_close_count": r["wrap_close_count"] or 0,
                    "buy_swarm_count": r["buy_swarm_count"] or 0,
                    "first_seen": r["first_seen"],
                    "last_seen": r["last_seen"],
                    "last_seen_ago": (now - r["last_seen"]) if r["last_seen"] else None,
                    "state": r["state"],
                    "confidence": r["confidence"],
                    "miss_type": (
                        "FULL_MISS" if conf == 0 and gap > 0 else
                        "PARTIAL_MISS" if gap > 0 else
                        "COVERED"
                    ),
                })

        # unconfirmed watchtower-like patterns (FORWARD-ONLY)
        unconfirmed = []
        if _table_exists(ov, "wt_unconfirmed_watchtower_like"):
            for r in ov.execute(
                "SELECT * FROM wt_unconfirmed_watchtower_like ORDER BY first_seen DESC LIMIT 100"
            ).fetchall():
                unconfirmed.append(dict(r))

        # candidate watches that resolved to CREATE but have no matching launch record (pipeline gap)
        pipeline_gaps = []
        if _table_exists(ov, "wt_candidate_websocket_watches"):
            confirmed_sigs = set()
            if _table_exists(ov, "wt_watchtower_launches"):
                for r in ov.execute("SELECT create_signature FROM wt_watchtower_launches").fetchall():
                    if r["create_signature"]:
                        confirmed_sigs.add(r["create_signature"])
            # RESOLVED_CREATE candidates without a matching launch — data gap
            gap_rows = ov.execute(
                "SELECT candidate_wallet, subprov_wallet, treasury_wallet, wrap_close_signature, "
                "wrap_close_time, close_reason "
                "FROM wt_candidate_websocket_watches WHERE state='RESOLVED_CREATE' LIMIT 200"
            ).fetchall()
            for r in gap_rows:
                # We can't match by sig directly (candidate watch stores wrap_close_sig not create_sig)
                # Surface as potential pipeline gap for human review
                pipeline_gaps.append({
                    "candidate": r["candidate_wallet"],
                    "subprov": r["subprov_wallet"],
                    "treasury": r["treasury_wallet"],
                    "wrap_close_sig": r["wrap_close_signature"],
                    "wrap_close_time": r["wrap_close_time"],
                    "close_reason": r["close_reason"],
                    "gap_type": "FORWARD_ONLY",
                })

        total_creators = sum(d["creator_count"] for d in discovered_creates)
        total_confirmed = sum(d["confirmed_launches"] for d in discovered_creates)
        total_gap = sum(d["detection_gap"] for d in discovered_creates)
        full_misses = [d for d in discovered_creates if d["miss_type"] == "FULL_MISS"]
        partial_misses = [d for d in discovered_creates if d["miss_type"] == "PARTIAL_MISS"]

        return jsonify({
            "discovered_creates": discovered_creates,
            "unconfirmed_patterns": unconfirmed,
            "pipeline_gaps": pipeline_gaps,
            "summary": {
                "total_subprovs_with_creates": len(discovered_creates),
                "total_creator_count": total_creators,
                "total_confirmed_launches": total_confirmed,
                "total_detection_gap": total_gap,
                "full_miss_subprovs": len(full_misses),
                "partial_miss_subprovs": len(partial_misses),
                "covered_subprovs": len(discovered_creates) - len(full_misses) - len(partial_misses),
                "overall_detection_rate": round(total_confirmed / total_creators, 3) if total_creators else None,
            },
            "data_quality_note": (
                "creator_count in wt_discovered_subprovs is FORWARD-ONLY (only populated from "
                "wrap-close events observed during active WS session). Historical creates before "
                "pipeline deployment are not counted. detection_gap is a lower bound on misses."
            ),
            "now": now,
        })
    except Exception as e:
        return jsonify({"discovered_creates": [], "summary": {}, "error": str(e)})
    finally:
        ov.close()


@ops_dashboard_bp.route("/api/ops/behaviour-fingerprints")
def api_ops_behaviour_fingerprints():
    """Behaviour fingerprints: per-subprov operational signatures derived from fanout patterns,
    timing, and launch outcomes. Read-only derived from existing tables."""
    ov = _conn()
    try:
        now = int(time.time())

        fingerprints = []

        if not _table_exists(ov, "wt_fanout_events"):
            return jsonify({"fingerprints": [], "total": 0})

        # Per-subprov fanout distribution stats
        subprov_fanout = {}
        for r in ov.execute(
            "SELECT subprov_wallet, treasury_wallet, "
            "COUNT(*) bursts, "
            "AVG(fanout_count) avg_size, MIN(fanout_count) min_size, MAX(fanout_count) max_size, "
            "SUM(CASE WHEN fanout_count <= 6 THEN 1 ELSE 0 END) small_bursts, "
            "SUM(CASE WHEN fanout_count >= 11 THEN 1 ELSE 0 END) large_bursts, "
            "AVG(total_sol) avg_total_sol, AVG(avg_sol) avg_per_recipient, "
            "SUM(CASE WHEN has_identical_amounts THEN 1 ELSE 0 END) identical_bursts, "
            "SUM(creates_fired) total_creates, SUM(buy_swarms) total_swarms, "
            "MIN(fanout_time) first_burst, MAX(fanout_time) last_burst "
            "FROM wt_fanout_events GROUP BY subprov_wallet"
        ).fetchall():
            subprov_fanout[r["subprov_wallet"]] = dict(r)

        # launch timing from watchtower_launches
        launch_timing: dict = {}
        if _table_exists(ov, "wt_watchtower_launches"):
            for r in ov.execute(
                "SELECT subprov_wallet, "
                "COUNT(*) n, AVG(birth_to_launch_seconds) avg_birth_s, "
                "AVG(fanout_to_create_secs) avg_fanout_to_create, "
                "SUM(CASE WHEN launch_mode='INSTANT' THEN 1 ELSE 0 END) instant_count, "
                "SUM(CASE WHEN launch_mode='STAGED' THEN 1 ELSE 0 END) staged_count "
                "FROM wt_watchtower_launches GROUP BY subprov_wallet"
            ).fetchall():
                launch_timing[r["subprov_wallet"]] = dict(r)

        for sp, fo in subprov_fanout.items():
            la = launch_timing.get(sp, {})
            bursts = fo["bursts"] or 1
            small = fo["small_bursts"] or 0
            large = fo["large_bursts"] or 0
            identical = fo["identical_bursts"] or 0
            creates = fo["total_creates"] or 0
            swarms = fo["total_swarms"] or 0

            # Derive behaviour signature
            creator_ratio = creates / bursts if bursts else 0
            swarm_ratio = swarms / bursts if bursts else 0
            small_ratio = small / bursts if bursts else 0
            identical_ratio = identical / bursts if bursts else 0

            sig_parts = []
            if creator_ratio > 0.5:
                sig_parts.append("CREATOR_DOMINANT")
            elif swarm_ratio > 0.5:
                sig_parts.append("SWARM_DOMINANT")
            else:
                sig_parts.append("MIXED")

            if small_ratio > 0.8:
                sig_parts.append("PRECISION_BURSTS")
            elif (large / bursts if bursts else 0) > 0.5:
                sig_parts.append("MASS_BURSTS")

            if identical_ratio > 0.7:
                sig_parts.append("UNIFORM_AMOUNTS")

            instant = la.get("instant_count") or 0
            staged = la.get("staged_count") or 0
            if instant + staged > 0:
                if instant / (instant + staged) > 0.8:
                    sig_parts.append("INSTANT_MODE")
                elif staged / (instant + staged) > 0.8:
                    sig_parts.append("STAGED_MODE")

            # operational lifespan
            first = fo.get("first_burst")
            last = fo.get("last_burst")
            lifespan_s = (last - first) if first and last else None

            fingerprints.append({
                "subprov": sp,
                "treasury": fo.get("treasury_wallet"),
                # raw stats
                "total_bursts": bursts,
                "avg_burst_size": round(fo.get("avg_size") or 0, 1),
                "min_burst_size": fo.get("min_size"),
                "max_burst_size": fo.get("max_size"),
                "small_burst_pct": round(small_ratio * 100, 1),
                "large_burst_pct": round((large / bursts) * 100 if bursts else 0, 1),
                "identical_amount_pct": round(identical_ratio * 100, 1),
                "avg_per_recipient_sol": round(fo.get("avg_per_recipient") or 0, 4),
                "avg_total_sol": round(fo.get("avg_total_sol") or 0, 3),
                "total_creates": creates,
                "total_swarms": swarms,
                "creator_ratio": round(creator_ratio, 3),
                "swarm_ratio": round(swarm_ratio, 3),
                # launch timing (FORWARD-ONLY — only from pipeline-detected launches)
                "confirmed_launches": la.get("n") or 0,
                "avg_birth_to_launch_s": round(la.get("avg_birth_s") or 0, 1),
                "avg_fanout_to_create_s": round(la.get("avg_fanout_to_create") or 0, 1) if la.get("avg_fanout_to_create") else None,
                "instant_launches": instant,
                "staged_launches": staged,
                # derived signature
                "behaviour_signature": "+".join(sig_parts) if sig_parts else "UNKNOWN",
                "first_burst": first,
                "last_burst": last,
                "lifespan_s": lifespan_s,
                "data_quality": (
                    (["FORWARD_ONLY_TIMING"] if la else ["NO_CONFIRMED_LAUNCHES"]) +
                    (["LOW_SAMPLE"] if bursts < 5 else [])
                ),
            })

        fingerprints.sort(key=lambda x: x["total_bursts"], reverse=True)
        return jsonify({"fingerprints": fingerprints, "total": len(fingerprints), "now": now})
    except Exception as e:
        return jsonify({"fingerprints": [], "total": 0, "error": str(e)})
    finally:
        ov.close()


@ops_dashboard_bp.route("/api/ops/rolling-activity")
def api_ops_rolling_activity():
    """Rolling activity heatmap: 24h of fanout + create + swarm events binned by hour,
    plus per-hour burst counts for the heatmap grid."""
    ov = _conn()
    try:
        now = int(time.time())
        cutoff_24h = now - 86400
        cutoff_7d = now - 7 * 86400

        hourly_bins: dict = {}

        if _table_exists(ov, "wt_fanout_events"):
            for r in ov.execute(
                "SELECT fanout_time, fanout_count, creates_fired, buy_swarms, total_sol "
                "FROM wt_fanout_events WHERE fanout_time >= ? ORDER BY fanout_time",
                (cutoff_24h,)
            ).fetchall():
                t = r["fanout_time"] or 0
                bucket = (t // 3600) * 3600  # floor to hour
                b = hourly_bins.setdefault(bucket, {"ts": bucket, "bursts": 0, "recipients": 0,
                                                     "creates": 0, "swarms": 0, "sol": 0.0})
                b["bursts"] += 1
                b["recipients"] += r["fanout_count"] or 0
                b["creates"] += r["creates_fired"] or 0
                b["swarms"] += r["buy_swarms"] or 0
                b["sol"] += r["total_sol"] or 0

        if _table_exists(ov, "wt_watchtower_launches"):
            for r in ov.execute(
                "SELECT create_time FROM wt_watchtower_launches WHERE create_time >= ?",
                (cutoff_24h,)
            ).fetchall():
                t = r["create_time"] or 0
                bucket = (t // 3600) * 3600
                b = hourly_bins.setdefault(bucket, {"ts": bucket, "bursts": 0, "recipients": 0,
                                                     "creates": 0, "swarms": 0, "sol": 0.0})
                b["creates"] = b.get("creates", 0) + 1

        bins = sorted(hourly_bins.values(), key=lambda x: x["ts"])
        for b in bins:
            b["ago_h"] = round((now - b["ts"]) / 3600, 1)
            b["sol"] = round(b["sol"], 2)

        # 7d daily summary
        daily_bins: dict = {}
        if _table_exists(ov, "wt_fanout_events"):
            for r in ov.execute(
                "SELECT fanout_time, fanout_count, creates_fired, buy_swarms "
                "FROM wt_fanout_events WHERE fanout_time >= ?", (cutoff_7d,)
            ).fetchall():
                t = r["fanout_time"] or 0
                bucket = (t // 86400) * 86400
                b = daily_bins.setdefault(bucket, {"ts": bucket, "bursts": 0,
                                                    "recipients": 0, "creates": 0, "swarms": 0})
                b["bursts"] += 1
                b["recipients"] += r["fanout_count"] or 0
                b["creates"] += r["creates_fired"] or 0
                b["swarms"] += r["buy_swarms"] or 0

        daily = sorted(daily_bins.values(), key=lambda x: x["ts"])

        # rolling windows (same logic as fanout-intelligence endpoint)
        rolling = {}
        if _table_exists(ov, "wt_fanout_events"):
            for window_s, label in [(300, "5m"), (1800, "30m"), (3600, "1h"), (21600, "6h"), (86400, "24h")]:
                cutoff = now - window_s
                row = ov.execute(
                    "SELECT COUNT(*) bursts, SUM(fanout_count) recipients, "
                    "SUM(creates_fired) creates, SUM(buy_swarms) swarms, SUM(total_sol) sol "
                    "FROM wt_fanout_events WHERE fanout_time >= ?", (cutoff,)
                ).fetchone()
                rolling[label] = {
                    "bursts": row["bursts"] or 0,
                    "recipients": row["recipients"] or 0,
                    "creates": row["creates"] or 0,
                    "swarms": row["swarms"] or 0,
                    "sol": round(row["sol"] or 0, 2),
                }

        return jsonify({
            "hourly_bins": bins,
            "daily_bins": daily,
            "rolling": rolling,
            "now": now,
        })
    except Exception as e:
        return jsonify({"hourly_bins": [], "daily_bins": [], "rolling": {}, "error": str(e)})
    finally:
        ov.close()


@ops_dashboard_bp.route("/api/ops-v2/intel/mission3")
def api_intel_mission3():
    """Mission 3 — Attribution Intelligence.

    Unified investigation queue with type badge + status.

    Type derivation (highest-confidence wins):
      WT          → any walkback outcome WATCHTOWER_CONFIRMED/WATCHTOWER_ATTRIBUTED or LINK_ONLY class
      NON-WT      → all walkback entries completed, best outcome = NON_WATCHTOWER
      DISMISSED   → ds.state = 'dismissed'
      WT-LIKE     → everything else (wrap-close confirmed, not yet attributed)

    Status derivation per subprov (precedence order):
      CANDIDATE_FOUND   → any walkback entry has TREASURY_CANDIDATE outcome
      WALKBACK_PENDING  → any walkback entry is PENDING or IN_PROGRESS, none CANDIDATE_FOUND
      NON_WATCHTOWER    → all walkback entries completed, best outcome = NON_WATCHTOWER
      NEW               → no walkback entries at all
      DISMISSED         → ds.state = 'dismissed'

    Sort: (STATUS_PRIORITY, TYPE_PRIORITY, age_seconds)
      STATUS: CANDIDATE_FOUND=0, NEW=1, WALKBACK_PENDING=2, NON_WATCHTOWER=3, DISMISSED=9
      TYPE within same status: WT=0, WT-LIKE=1, NON-WT=2, DISMISSED=3

    Coverage KPIs:
      Attribution coverage = attributed / total walkback entries (WT rows contribute)
      Discovery queue = count of WT-LIKE rows (primary new-operator pipeline)
    """
    ov = _conn()
    try:
        now = int(time.time())

        # ── Coverage: total migrations attributed vs total walkback queue entries ──
        total_migrations = 0
        attributed = 0
        try:
            from src.core.walkback_queue import queue_stats as _wq_stats
            wq = _wq_stats(ov)
            by_outcome = wq.get("by_outcome", {})
            by_class = wq.get("by_class", {})
            total_migrations = sum(by_outcome.values())
            # WATCHTOWER_CONFIRMED = walkback traced lineage to confirmed treasury
            # LINK_ONLY class = token directly linked to confirmed treasury (no walkback needed)
            # Both count as attributed
            attributed = (by_outcome.get("WATCHTOWER_CONFIRMED", 0)
                          + by_outcome.get("WATCHTOWER_ATTRIBUTED", 0)
                          + by_class.get("LINK_ONLY", 0))
            wq_diagnostics = wq
        except Exception:
            wq = {}
            wq_diagnostics = {}
            by_outcome = {}

        coverage_pct = round(attributed / total_migrations * 100, 1) if total_migrations else 0

        # ── Walkback status per subprov (aggregate across all queue entries) ──
        # Also track LINK_ONLY class hits per subprov for type derivation.
        subprov_walkback: dict = {}  # subprov → {status, best_outcome, candidate_wallet, last_at, has_link_only}
        try:
            for r in ov.execute(
                "SELECT subprov, status, intelligence_outcome, walkback_class, funder_wallet, "
                "funder_amount_sol, funder_sig, funder_block_time, completed_at "
                "FROM wt_walkback_queue ORDER BY enqueued_at ASC"
            ).fetchall():
                sp = r["subprov"]
                if sp not in subprov_walkback:
                    subprov_walkback[sp] = {
                        "status": r["status"], "best_outcome": r["intelligence_outcome"],
                        "candidate_wallet": None, "last_at": r["completed_at"],
                        "has_link_only": False, "hops": []
                    }
                entry = subprov_walkback[sp]
                # Update status: COMPLETED > IN_PROGRESS > PENDING
                if r["status"] == "COMPLETED":
                    entry["status"] = "COMPLETED"
                elif r["status"] == "IN_PROGRESS" and entry["status"] != "COMPLETED":
                    entry["status"] = "IN_PROGRESS"
                # Update best outcome using precedence
                outcome_rank = {"TREASURY_CANDIDATE": 4, "WATCHTOWER_CONFIRMED": 3,
                                "WATCHTOWER_ATTRIBUTED": 3, "LINEAGE_GAP": 2,
                                "NON_WATCHTOWER": 1, "NO_ATTRIBUTION_FOUND": 0}
                cur_rank = outcome_rank.get(entry["best_outcome"] or "", -1)
                new_rank = outcome_rank.get(r["intelligence_outcome"] or "", -1)
                if new_rank > cur_rank:
                    entry["best_outcome"] = r["intelligence_outcome"]
                if r["intelligence_outcome"] == "TREASURY_CANDIDATE" and r["funder_wallet"]:
                    entry["candidate_wallet"] = r["funder_wallet"]
                if r["completed_at"] and (not entry["last_at"] or r["completed_at"] > entry["last_at"]):
                    entry["last_at"] = r["completed_at"]
                # Track LINK_ONLY class — direct treasury linkage, no walkback needed
                if (r["walkback_class"] if "walkback_class" in r.keys() else None) == "LINK_ONLY":
                    entry["has_link_only"] = True
                # Collect hop evidence
                if r["funder_wallet"]:
                    entry["hops"].append({
                        "wallet": r["funder_wallet"],
                        "amount_sol": r["funder_amount_sol"],
                        "sig": r["funder_sig"],
                        "block_time": r["funder_block_time"],
                        "outcome": r["intelligence_outcome"],
                    })
        except Exception:
            pass

        # Derive UI type badge: WT | WT-LIKE | NON-WT | DISMISSED
        def _derive_type(sp_addr: str, ds_state: str) -> str:
            if ds_state == "dismissed":
                return "DISMISSED"
            wb = subprov_walkback.get(sp_addr)
            if not wb:
                return "WT-LIKE"
            if wb.get("has_link_only") or wb.get("best_outcome") in (
                    "WATCHTOWER_CONFIRMED", "WATCHTOWER_ATTRIBUTED"):
                return "WT"
            if wb.get("best_outcome") == "NON_WATCHTOWER" and wb.get("status") == "COMPLETED":
                return "NON-WT"
            return "WT-LIKE"

        # Derive UI status from walkback aggregation
        def _derive_status(sp_addr: str, ds_state: str) -> str:
            if ds_state == "dismissed":
                return "DISMISSED"
            wb = subprov_walkback.get(sp_addr)
            if not wb:
                return "NEW"
            if wb["best_outcome"] == "TREASURY_CANDIDATE":
                return "CANDIDATE_FOUND"
            if wb["status"] in ("PENDING", "IN_PROGRESS") and wb["status"] != "COMPLETED":
                return "WALKBACK_PENDING"
            if wb["best_outcome"] in ("NON_WATCHTOWER", "NO_ATTRIBUTION_FOUND") and wb["status"] == "COMPLETED":
                return "NON_WATCHTOWER"
            if wb["status"] == "COMPLETED":
                return "NON_WATCHTOWER"
            return "WALKBACK_PENDING"

        # ── CEX/INFRA filter — skip known exchanges and infrastructure accounts ──
        try:
            from src.utils.infra_mapping import is_known_account as _is_known
        except Exception:
            _is_known = lambda _: False

        # ── Load treasury review candidates and confirmed set ──
        confirmed = {r[0] for r in ov.execute("SELECT treasury FROM wt_confirmed_treasuries").fetchall()}
        reviewing = set()
        try:
            reviewing = {r[0] for r in ov.execute(
                "SELECT treasury FROM wt_treasury_review WHERE status='PENDING_REVIEW'").fetchall()}
        except Exception:
            pass

        # ── Build per-subprov launch count from wt_watchtower_launches ──
        launch_counts: dict = {}
        try:
            for r in ov.execute(
                "SELECT subprov_wallet, COUNT(*) n FROM wt_watchtower_launches "
                "GROUP BY subprov_wallet"
            ).fetchall():
                launch_counts[r["subprov_wallet"]] = r["n"]
        except Exception:
            pass

        # ── Unknown subprovs: treasury IS NULL, exclude already-confirmed ──
        _have_mesh = _column_exists(ov, "wt_discovered_subprovs", "immediate_funder")
        _mesh_cols = ", immediate_funder, funder_is_subprov" if _have_mesh else ""
        ds_rows = ov.execute(
            "SELECT subprov, first_creator, creator_count, treasury, state, "
            "first_seen, last_seen, discovery_source" + _mesh_cols
            + " FROM wt_discovered_subprovs "
            "WHERE treasury IS NULL OR (treasury NOT IN "
            "(SELECT treasury FROM wt_confirmed_treasuries)) "
            "ORDER BY first_seen ASC"
        ).fetchall()

        subprovs = []
        for r in ds_rows:
            sp = r["subprov"]
            treasury = r["treasury"]
            ds_state = r["state"] or "active"
            # Skip already-confirmed or in-review (resolved by another path)
            if treasury and (treasury in confirmed or treasury in reviewing):
                continue
            # Skip known CEX / infrastructure accounts — they are not WATCHTOWER subprovs
            if _is_known(sp):
                continue
            # Skip zero-launch subprovs — no confirmed token means no actionable attribution case
            if launch_counts.get(sp, 0) == 0:
                continue
            wb = subprov_walkback.get(sp, {})
            ui_type = _derive_type(sp, ds_state)
            ui_status = _derive_status(sp, ds_state)
            first_seen = r["first_seen"] or now
            age_s = now - int(first_seen)

            subprovs.append({
                "subprov": sp,
                "type": ui_type,
                "creators": r["creator_count"] or 0,
                "launches": launch_counts.get(sp, 0),
                "first_seen": first_seen,
                "last_seen": r["last_seen"],
                "age_seconds": age_s,
                "status": ui_status,
                "walkback_status": wb.get("status"),
                "walkback_outcome": wb.get("best_outcome"),
                "candidate_wallet": wb.get("candidate_wallet"),
                "walkback_hops": wb.get("hops", []),
                "discovery_source": r["discovery_source"],
                "treasury": treasury,
            })

        # Sort: (status_priority, type_priority, age) — CANDIDATE_FOUND/WT floats to top
        STATUS_PRIORITY = {"CANDIDATE_FOUND": 0, "NEW": 1, "WALKBACK_PENDING": 2,
                           "NON_WATCHTOWER": 3, "DISMISSED": 9}
        TYPE_PRIORITY = {"WT": 0, "WT-LIKE": 1, "NON-WT": 2, "DISMISSED": 3}
        subprovs.sort(key=lambda r: (
            STATUS_PRIORITY.get(r["status"], 9),
            TYPE_PRIORITY.get(r["type"], 9),
            r["age_seconds"]
        ))

        wt_like_count = sum(1 for s in subprovs if s["type"] == "WT-LIKE")
        wt_count = sum(1 for s in subprovs if s["type"] == "WT")
        dismissed_count = sum(1 for s in subprovs if s["type"] == "DISMISSED")
        return jsonify({
            "coverage": {
                "attributed": attributed,
                "total": total_migrations,
                "pct": coverage_pct,
                "wt_like_count": wt_like_count,
                "wt_count": wt_count,
                "dismissed_count": dismissed_count,
                "non_watchtower": by_outcome.get("NON_WATCHTOWER", 0),
            },
            "subprovs": subprovs,
            "walkback_diagnostics": {
                "by_class": wq_diagnostics.get("by_class", {}),
                "by_status": wq_diagnostics.get("by_status", {}),
                "by_outcome": by_outcome,
                "rpc_total": wq_diagnostics.get("rpc_total", 0),
                "total_entries": total_migrations,
            },
        })
    except Exception as e:
        return jsonify({"error": str(e), "coverage": {}, "subprovs": [],
                        "walkback_diagnostics": {}})
    finally:
        ov.close()


@ops_dashboard_bp.route("/api/ops-v2/intel/operator-pattern-report")
def api_intel_operator_pattern_report():
    """Read-only operator pattern discovery report.

    Clusters confirmed WATCHTOWER launches by behavioural fingerprint.
    Generated on demand — no writes, no background processing, no schema changes.
    Shared logic with scripts/report_operator_patterns.py via src.core.pattern_discovery.
    """
    try:
        min_members = int(request.args.get("min_members", 2))
    except (TypeError, ValueError):
        min_members = 2

    ov = _conn()
    try:
        from src.core.pattern_discovery import build_report
        report = build_report(ov, min_members=min_members)
        return jsonify(report)
    except Exception as e:
        return jsonify({"error": str(e), "launches_total": 0,
                        "primary_clusters": [], "secondary_clusters": [],
                        "singletons": [], "summary": {}})
    finally:
        ov.close()


@ops_dashboard_bp.route("/api/ops/walkback-queue")
def api_ops_walkback_queue():
    """Walkback queue stats: lineage completeness split + trend."""
    ov = _conn()
    try:
        if not _table_exists(ov, "wt_walkback_queue"):
            return jsonify({"by_class": {}, "by_status": {}, "rpc_total": 0, "trend_24h": {},
                            "recent": []})
        from src.core.walkback_queue import queue_stats
        stats = queue_stats(ov)

        # recent entries (last 50, newest first)
        recent = []
        for r in ov.execute(
            "SELECT mint, creator, subprov, treasury, walkback_class, attribution_source, "
            "intelligence_outcome, status, rpc_used, enqueued_at, completed_at "
            "FROM wt_walkback_queue ORDER BY enqueued_at DESC LIMIT 50"
        ).fetchall():
            recent.append({
                "mint": r["mint"],
                "creator": r["creator"],
                "subprov": r["subprov"],
                "treasury": r["treasury"],
                "walkback_class": r["walkback_class"],
                "attribution_source": r["attribution_source"],
                "intelligence_outcome": r["intelligence_outcome"],
                "status": r["status"],
                "rpc_used": r["rpc_used"],
                "enqueued_at": r["enqueued_at"],
                "completed_at": r["completed_at"],
            })

        stats["recent"] = recent
        return jsonify(stats)
    except Exception as e:
        return jsonify({"error": str(e), "by_class": {}, "by_status": {}, "trend_24h": {}})
    finally:
        ov.close()


@ops_dashboard_bp.route("/api/ops/cdc-intelligence")
def api_ops_cdc_intelligence():
    """Capital Distributor Candidates — infrastructure observation only, no creator detection."""
    ov = _conn()
    try:
        if not _table_exists(ov, "wt_capital_distributor_candidates"):
            return jsonify({"cdcs": [], "total": 0})

        cdcs_raw = ov.execute(
            "SELECT wallet, source_treasury, funding_amount_sol, first_seen, "
            "observation_state, last_activity, total_outbound_sol, recipient_count, "
            "fanout_count, largest_fanout, derived_role, role_confidence "
            "FROM wt_capital_distributor_candidates ORDER BY first_seen DESC LIMIT 100"
        ).fetchall()

        now = int(time.time())
        cdcs = []
        for r in cdcs_raw:
            cdcs.append({
                "wallet": r["wallet"],
                "source_treasury": r["source_treasury"],
                "funding_sol": round(r["funding_amount_sol"] or 0, 2),
                "first_seen": r["first_seen"],
                "first_seen_ago": now - (r["first_seen"] or now),
                "state": r["observation_state"],
                "last_activity": r["last_activity"],
                "last_activity_ago": (now - r["last_activity"]) if r["last_activity"] else None,
                "total_outbound_sol": round(r["total_outbound_sol"] or 0, 2),
                "recipient_count": r["recipient_count"] or 0,
                "fanout_count": r["fanout_count"] or 0,
                "largest_fanout": r["largest_fanout"] or 0,
                "derived_role": r["derived_role"] or "UNKNOWN",
                "role_confidence": r["role_confidence"] or "NONE",
            })

        # recent outbound events (last 50)
        recent_outbounds = []
        if _table_exists(ov, "wt_cdc_outbound_events"):
            for r in ov.execute(
                "SELECT cdc_wallet, sig, block_time, recipient, amount_sol, fanout_size "
                "FROM wt_cdc_outbound_events ORDER BY block_time DESC LIMIT 50"
            ).fetchall():
                recent_outbounds.append({
                    "cdc_wallet": r["cdc_wallet"],
                    "sig": r["sig"],
                    "block_time": r["block_time"],
                    "block_time_ago": now - (r["block_time"] or now),
                    "recipient": r["recipient"],
                    "amount_sol": round(r["amount_sol"] or 0, 3),
                    "fanout_size": r["fanout_size"],
                })

        counts = {
            "observing":   sum(1 for c in cdcs if c["state"] == "OBSERVING"),
            "subscribed":  sum(1 for c in cdcs if c["state"] == "SUBSCRIBED"),
            "inactive":    sum(1 for c in cdcs if c["state"] == "INACTIVE"),
        }

        return jsonify({
            "cdcs": cdcs,
            "total": len(cdcs),
            "counts": counts,
            "recent_outbounds": recent_outbounds,
            "now": now,
        })
    except Exception as e:
        return jsonify({"cdcs": [], "total": 0, "error": str(e)})
    finally:
        ov.close()


@ops_dashboard_bp.route("/api/ops-v2/dust-observatory")
def dust_observatory_summary():
    """Dust Observatory: marker summary + role transition stats + intelligence overview."""
    try:
        from src.core import dust_observatory as dobs
        markers    = dobs.get_dust_marker_summary()
        stats      = dobs.get_role_transition_stats()
        intel      = dobs.get_intelligence_summary()
        recipients = dobs.get_all_recipients()
        return jsonify({
            "ok": True,
            "generated_at": time.time(),
            "markers": markers,
            "stats": stats,
            "intel": intel,
            "recipients": recipients,
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "generated_at": time.time(),
            "markers": [],
            "stats": {},
            "intel": {},
            "error": str(e),
        })


@ops_dashboard_bp.route("/api/ops-v2/dust-observatory/recipient/<wallet>")
def dust_observatory_recipient(wallet):
    """Return the full lifecycle record for one recipient wallet."""
    try:
        from src.core import dust_observatory as dobs
        data = dobs.get_recipient_lifecycle(wallet)
        if data is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@ops_dashboard_bp.route("/ops/dust-observatory")
def dust_observatory_page():
    return render_template("watchtower_dust_observatory.html", active_page="dust_observatory")


@ops_dashboard_bp.route("/ops/detection-health")
def detection_health_page():
    return render_template("watchtower_detection_health.html", active_page="detection_health")


@ops_dashboard_bp.route("/api/ops-v2/detection-health")
def api_detection_health():
    """Sprint O1.3 — Detection health with finalised confidence model.

    Separates two independent questions:
      operational_status — can WATCHTOWER currently be trusted to detect launches?
      historical_status  — how has WATCHTOWER been performing recently?

    Confidence values:
      LIVE       — freshly observed, value reflects current state
      STALE      — last known value, system may have changed (heartbeat 120–300s)
      UNKNOWN    — telemetry too old to trust (heartbeat >300s) or no row exists
      HISTORICAL — permanent DB record; freshness concept does not apply

    operational_status decision tree (O1.3):
      No heartbeat row         → UNKNOWN
      Heartbeat age > 300s     → NO_TELEMETRY
      PW stream_state ≠ ACTIVE → DEGRADED
      UNMONITORED treasury > 0 → DEGRADED  (known blind spot)
      Heartbeat STALE (120–300s) → DEGRADED  (telemetry becoming stale)
      Otherwise                → LIVE

    Treasury STALE (previously monitored, now quiet) does NOT degrade
    operational_status — quiet treasuries are expected; no evidence of
    subscription failure. Only UNMONITORED (never seen a notification)
    represents a genuine capability gap.

    Read-only, ops DB only, no schema changes, no detection impact.
    """
    import time
    import json
    import statistics

    # Heartbeat thresholds (seconds)
    HB_LIVE  = 120
    HB_STALE = 300

    # Treasury active threshold (seconds) — WS notification within this window = ACTIVE
    TREASURY_ACTIVE_S = 86400

    now = int(time.time())
    window_7d = now - 86400 * 7

    try:
        db = _conn()

        # ── Cascade Heartbeat ────────────────────────────────────────────────
        hb_row = db.execute(
            "SELECT last_seen, status, meta_json FROM wt_worker_heartbeat WHERE worker_name='ws_cascade'"
        ).fetchone()

        if hb_row is None:
            # No row: process has never written a heartbeat since DB creation
            hb_confidence = "UNKNOWN"
            hb_age_s = None
            hb_last_seen = None
            meta = {}
        else:
            hb_age_s = now - hb_row["last_seen"]
            hb_last_seen = hb_row["last_seen"]
            meta = json.loads(hb_row["meta_json"]) if hb_row["meta_json"] else {}
            if hb_age_s < HB_LIVE:
                hb_confidence = "LIVE"
            elif hb_age_s < HB_STALE:
                hb_confidence = "STALE"
            else:
                hb_confidence = "UNKNOWN"

        # cascade_heartbeat status: GREEN/AMBER/RED only when LIVE or STALE
        if hb_confidence == "LIVE":
            cascade_status = "GREEN"
        elif hb_confidence == "STALE":
            cascade_status = "AMBER"
        else:
            cascade_status = "UNKNOWN"

        # Runtime fields — null them when heartbeat is UNKNOWN (too stale to trust)
        if hb_confidence in ("LIVE", "STALE"):
            cascade_runtime = {
                "cascade_state": meta.get("cascade_state"),
                "reconnect_generation": meta.get("reconnect_gen"),
                "subs": meta.get("subs"),
                "treasury_subs": meta.get("treasury_subs"),
                "subprov_subs": meta.get("subprov_subs"),
            }
        else:
            cascade_runtime = {
                "cascade_state": None,
                "reconnect_generation": None,
                "subs": None,
                "treasury_subs": None,
                "subprov_subs": None,
            }

        cascade_heartbeat = {
            "confidence": hb_confidence,
            "status": cascade_status,
            "last_updated": hb_last_seen,
            "last_updated_age_s": hb_age_s,
            **cascade_runtime,
        }

        # ── ProgramWatcher (inherits heartbeat confidence, no independent source) ──
        if hb_confidence == "UNKNOWN":
            program_watcher = {
                "confidence": "UNKNOWN",
                "status": "UNKNOWN",
                "last_updated": hb_last_seen,
                "last_updated_age_s": hb_age_s,
                "stream_state": None,
                "active_candidates": None,
                "fetch_queue": None,
                "fetch_timeout": None,
                "fetch_dropped": None,
                "active_catchup_hits": None,
                "matches": None,
            }
        else:
            pw_state = meta.get("pw_stream_state", "")
            pw_status = "GREEN" if pw_state == "ACTIVE" else "RED"
            program_watcher = {
                "confidence": hb_confidence,
                "status": pw_status,
                "last_updated": hb_last_seen,
                "last_updated_age_s": hb_age_s,
                "stream_state": pw_state or None,
                "active_candidates": meta.get("pw_active_candidates"),
                "fetch_queue": meta.get("pw_fetch_queue"),
                "fetch_timeout": meta.get("pw_fetch_timeout"),
                "fetch_dropped": meta.get("pw_fetch_dropped"),
                "active_catchup_hits": meta.get("pw_active_catchup_hits"),
                "matches": meta.get("pw_matches"),
            }

        # ── Treasury Coverage (independent of heartbeat — reads DB directly) ──
        treasury_rows = db.execute(
            "SELECT treasury, no_subscribe, confidence, confirmed_at FROM wt_confirmed_treasuries"
        ).fetchall()
        ws_usage = {
            r["treasury_wallet"]: r for r in db.execute(
                "SELECT treasury_wallet, last_notif_at, notif_count, sessions_opened FROM wt_treasury_ws_usage"
            ).fetchall()
        }

        coverage_counts = {"ACTIVE": 0, "STALE": 0, "UNMONITORED": 0, "EXCLUDED": 0}
        treasury_detail = []
        for t in treasury_rows:
            addr = t["treasury"]
            if t["no_subscribe"]:
                state = "EXCLUDED"
            else:
                usage = ws_usage.get(addr)
                if usage and usage["last_notif_at"]:
                    age = now - int(usage["last_notif_at"])
                    state = "ACTIVE" if age < TREASURY_ACTIVE_S else "STALE"
                else:
                    # Row in ws_usage with no last_notif_at, or no row at all:
                    # this treasury has never produced a WS notification
                    state = "UNMONITORED"
            coverage_counts[state] += 1
            usage = ws_usage.get(addr)
            last_notif_at = usage["last_notif_at"] if usage and usage["last_notif_at"] else None
            treasury_detail.append({
                "treasury": addr,
                "coverage_state": state,
                "confidence": t["confidence"],
                "notif_count": usage["notif_count"] if usage else 0,
                "last_notif_at": last_notif_at,
                "last_notif_age_s": (now - int(last_notif_at)) if last_notif_at else None,
            })

        # Coverage status: driven by UNMONITORED (genuine blind spots), not ACTIVE %.
        # STALE = previously monitored and now quiet — expected, not a failure signal.
        # ACTIVE % retained as an informational field but does not set tc_status.
        eligible = coverage_counts["ACTIVE"] + coverage_counts["STALE"] + coverage_counts["UNMONITORED"]
        active_pct = round(100 * coverage_counts["ACTIVE"] / eligible, 1) if eligible else 0
        unmonitored_n = coverage_counts["UNMONITORED"]
        if eligible == 0:
            tc_status = "UNKNOWN"
        elif unmonitored_n == 0:
            tc_status = "GREEN"
        elif unmonitored_n <= 2:
            tc_status = "AMBER"
        else:
            tc_status = "RED"

        # last_updated = most recent treasury notification across all non-excluded treasuries
        notif_timestamps = [
            int(t["last_notif_at"])
            for t in treasury_detail
            if t.get("last_notif_at") and t.get("coverage_state") != "EXCLUDED"
        ]
        tc_last_updated = max(notif_timestamps) if notif_timestamps else None
        tc_last_updated_age = (now - tc_last_updated) if tc_last_updated else None

        treasury_coverage = {
            "confidence": "LIVE",
            "status": tc_status,
            "last_updated": tc_last_updated,
            "last_updated_age_s": tc_last_updated_age,
            "total": len(treasury_rows),
            "counts": coverage_counts,
            "active_pct": active_pct,
            "treasuries": treasury_detail,
        }

        # ── Detection Sources — 7d historical ───────────────────────────────
        source_rows = db.execute(
            """SELECT detection_source, COUNT(*) n FROM wt_watchtower_launches
               WHERE recorded_at > ? AND detection_source IS NOT NULL
               GROUP BY detection_source""",
            (window_7d,)
        ).fetchall()
        source_map = {r["detection_source"]: r["n"] for r in source_rows}
        total_launches = sum(source_map.values())
        pl_n = source_map.get("PROGRAM_LOGS", 0)
        pl_pct = round(100 * pl_n / total_launches, 1) if total_launches else 0
        ds_status = "GREEN" if pl_pct >= 50 else "AMBER" if pl_pct >= 25 else "RED"
        detection_sources = {
            "confidence": "HISTORICAL",
            "status": ds_status,
            "last_updated": None,
            "last_updated_age_s": None,
            "window_days": 7,
            "total_launches": total_launches,
            "by_source": dict(source_map),
            "program_logs_pct": pl_pct,
        }

        # ── Detection Latency — 7d historical ───────────────────────────────
        lat_rows = db.execute(
            """SELECT a.detection_latency_ms, l.detection_source
               FROM wt_launch_audit a
               LEFT JOIN wt_watchtower_launches l ON l.mint = a.mint
               WHERE a.detection_latency_ms IS NOT NULL
                 AND a.create_time > ?""",
            (window_7d,)
        ).fetchall()

        by_source_lat = {}
        all_vals = []
        for r in lat_rows:
            ms = r["detection_latency_ms"]
            src = r["detection_source"] or "UNKNOWN"
            all_vals.append(ms)
            by_source_lat.setdefault(src, []).append(ms)

        def _percentiles(vals):
            if not vals:
                return {}
            s = sorted(vals)
            n = len(s)
            return {
                "count": n,
                "p50_ms": round(statistics.median(s)),
                "p95_ms": round(s[min(int(n * 0.95), n - 1)]),
                "p99_ms": round(s[min(int(n * 0.99), n - 1)]),
                "min_ms": round(s[0]),
                "max_ms": round(s[-1]),
            }

        p50_all = statistics.median(all_vals) if all_vals else None
        lat_status = (
            "GREEN" if p50_all and p50_all < 5000 else
            "AMBER" if p50_all and p50_all < 30000 else
            "RED" if p50_all else "UNKNOWN"
        )
        detection_latency = {
            "confidence": "HISTORICAL",
            "status": lat_status,
            "last_updated": None,
            "last_updated_age_s": None,
            "window_days": 7,
            "overall": _percentiles(all_vals),
            "by_source": {src: _percentiles(vals) for src, vals in by_source_lat.items()},
        }

        db.close()

        # ── Operational Status ───────────────────────────────────────────────
        # Derived from live metrics only: heartbeat + PW + UNMONITORED treasury count.
        # active_pct (ACTIVE %) is NOT used — it measures treasury activity, not
        # WATCHTOWER subscription capability. STALE treasuries are quiet but monitored.
        # Never derived from historical metrics.
        if hb_row is None:
            op_status = "UNKNOWN"
        elif hb_confidence == "UNKNOWN":
            op_status = "NO_TELEMETRY"
        elif pw_state != "ACTIVE":
            op_status = "DEGRADED"
        elif unmonitored_n > 0:
            op_status = "DEGRADED"
        elif hb_confidence == "STALE":
            op_status = "DEGRADED"
        else:
            op_status = "LIVE"

        # ── Historical Status ────────────────────────────────────────────────
        # Derived from historical metrics only: sources + latency.
        # Independent of heartbeat state — remains valid when detector is offline.
        if total_launches == 0 and not all_vals:
            hist_status = "UNKNOWN"
        else:
            # Worst of the two historical metric statuses
            rank = {"GREEN": 0, "AMBER": 1, "RED": 2, "UNKNOWN": 3}
            worst = max(ds_status, lat_status, key=lambda s: rank.get(s, 0))
            hist_status = worst

        # ── Operational Reasons ──────────────────────────────────────────────
        op_reasons = []
        if hb_row is None:
            op_reasons.append("No cascade heartbeat has ever been recorded")
        elif hb_confidence == "UNKNOWN":
            op_reasons.append(
                f"Cascade heartbeat has not updated for {hb_age_s} seconds "
                f"(threshold: {HB_STALE}s) — current runtime state cannot be trusted"
            )
        elif hb_confidence == "STALE":
            op_reasons.append(
                f"Cascade heartbeat is {hb_age_s}s old — telemetry becoming stale "
                f"(STALE threshold: {HB_LIVE}s)"
            )
        if hb_confidence in ("LIVE", "STALE") and meta.get("pw_stream_state") != "ACTIVE":
            op_reasons.append(
                f"ProgramWatcher stream state is '{meta.get('pw_stream_state', 'unknown')}' — "
                f"not ACTIVE"
            )
        if hb_confidence == "UNKNOWN":
            op_reasons.append(
                "ProgramWatcher state unknown because cascade telemetry is stale"
            )
        if unmonitored_n:
            op_reasons.append(
                f"{unmonitored_n} confirmed {'treasury has' if unmonitored_n == 1 else 'treasuries have'} "
                f"never produced a WS notification — monitoring blind spot; cascade restart may be required"
            )

        # ── Historical Reasons ───────────────────────────────────────────────
        hist_reasons = []
        if total_launches:
            hist_reasons.append(
                f"PROGRAM_LOGS produced {pl_pct}% of {total_launches} detections in the last 7 days"
            )
        else:
            hist_reasons.append("No detections recorded in the last 7 days")
        if p50_all is not None:
            hist_reasons.append(
                f"Median detection latency {round(p50_all / 1000, 1)}s "
                f"(p95: {round((sorted(all_vals)[min(int(len(all_vals) * 0.95), len(all_vals) - 1)]) / 1000, 1)}s)"
            )

        # ── Deprecated overall_status (backwards compatibility) ──────────────
        # Derived from operational_status only. Retained temporarily so any
        # existing consumer does not silently break. Do not use in new consumers.
        _op_rank = {"LIVE": 0, "DEGRADED": 1, "NO_TELEMETRY": 2, "UNKNOWN": 3}
        overall_status = {
            0: "GREEN",
            1: "AMBER",
            2: "RED",
            3: "RED",
        }[_op_rank.get(op_status, 3)]

        return jsonify({
            "ok": True,
            "generated_at": now,
            # Primary status fields
            "operational_status": op_status,
            "historical_status": hist_status,
            "operational_reasons": op_reasons,
            "historical_reasons": hist_reasons,
            # Live metrics
            "cascade_heartbeat": cascade_heartbeat,
            "program_watcher": program_watcher,
            "treasury_coverage": treasury_coverage,
            # Historical metrics
            "detection_sources": detection_sources,
            "detection_latency": detection_latency,
            # Deprecated — derived from operational_status only
            "overall_status": overall_status,
            "_overall_status_deprecated": True,
        })

    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 500


# ════════════════════════════════════════════════════════════════════════════
# Sprint O3.1 — Standalone Alert Evaluator
#
# All evaluation + persistence logic lives in src/core/alert_evaluator.py.
# The standalone process (scripts/run_alert_evaluator.py, supervised) polls
# every 30s and writes wt_alerts.db.  This endpoint is now READ-ONLY.
#
# Alert lifecycle:  RAISED → ACTIVE → RECOVERED
# Raised_at is set once per incident by the evaluator, never by this endpoint.
# ════════════════════════════════════════════════════════════════════════════

from src.core.alert_evaluator import read_alerts as _ae_read_alerts, SEV_RANK as _AE_SEV_RANK


@ops_dashboard_bp.route("/api/ops-v2/alerts")
def api_alerts():
    """Sprint O3.1 — Read-only alert endpoint.

    Alert state is maintained by the standalone alert_evaluator process
    (scripts/run_alert_evaluator.py, managed by supervisord). This endpoint
    reads the current state from wt_alerts.db and returns it — no evaluation,
    no writes. raised_at timestamps reflect the first evaluator observation,
    not the first dashboard view.
    """
    import time as _time
    now = int(_time.time())

    try:
        active, recovered = _ae_read_alerts()

        return jsonify({
            "ok":           True,
            "generated_at": now,
            "active_count": len(active),
            "active":       active,
            "recovered":    recovered,
        })

    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 500


# ════════════════════════════════════════════════════════════════════════════
# Sprint O4.1 — Discovery Assurance
# ════════════════════════════════════════════════════════════════════════════

@ops_dashboard_bp.route("/api/ops-v2/walkback-health")
def api_walkback_progress_health():
    """Progress health; a live PID without completed work is not healthy."""
    conn = _conn()
    try:
        from src.ops.walkback_health import build_walkback_health
        return jsonify({"ok": True, **build_walkback_health(conn)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    finally:
        conn.close()


@ops_dashboard_bp.route("/api/ops-v2/watchtower-attribution-funnel")
def api_watchtower_attribution_funnel():
    """Rolling persisted-data launch-to-Mission-Control control funnel."""
    try:
        hours = max(1, min(int(request.args.get("hours", 72)), 24 * 30))
        from src.ops.watchtower_funnel import build_watchtower_funnel
        return jsonify(build_watchtower_funnel(
            OPS_DB_PATH, LIVE_DB_PATH, window_seconds=hours * 3600
        ))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@ops_dashboard_bp.route("/api/ops-v2/watchtower-control")
def api_watchtower_control():
    """Known-good reference operator status for deployment and NOC use."""
    conn = _conn()
    try:
        from src.ops.watchtower_alignment import audit_alignment
        from src.ops.walkback_health import build_walkback_health
        return jsonify({
            "ok": True,
            "generated_at": int(time.time()),
            "identity": audit_alignment(conn),
            "walkback": build_walkback_health(conn),
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    finally:
        conn.close()


@ops_dashboard_bp.route("/api/ops-v2/attribution-outcomes")
def api_attribution_outcomes():
    """Canonical terminal conclusions; legacy queue wording is not an API contract."""
    conn = _conn()
    try:
        limit = max(1, min(int(request.args.get("limit", 100)), 500))
        outcome_type = (request.args.get("outcome_type") or "").strip().upper()
        where, args = "1=1", []
        if outcome_type:
            from src.ops.attribution_outcome import OUTCOME_TYPES
            if outcome_type not in OUTCOME_TYPES:
                return jsonify({"ok": False, "error": "invalid outcome_type"}), 400
            where, args = "outcome_type=?", [outcome_type]
        rows = [dict(row) for row in conn.execute(
            "SELECT mint,outcome_type,stop_reason,terminal_entity,terminal_entity_type,confidence,"
            "operator_id,should_seed_emerging_operator,should_retry,completed_at,evidence_json "
            f"FROM wt_attribution_outcomes WHERE {where} ORDER BY completed_at DESC LIMIT ?",
            (*args, limit),
        )]
        for row in rows:
            try:
                row["evidence"] = _json.loads(row.pop("evidence_json") or "{}")
            except (TypeError, ValueError):
                row["evidence"] = {}
                row.pop("evidence_json", None)
        return jsonify({"ok": True, "outcomes": rows, "count": len(rows)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    finally:
        conn.close()


@ops_dashboard_bp.route("/api/ops-v2/attribution-outcomes/summary")
def api_attribution_outcomes_summary():
    """X26.5.1 — exact, uncapped, SQL-grouped counts of wt_attribution_outcomes
    by outcome_type within an explicit window. Built to replace the landing
    Attribution Health panel's prior client-side pattern (fetch 500 mixed
    rows -> filter to 24h in JS -> group in JS), which silently under-counted
    once combined outcome volume across all types exceeded the shared
    500-row cap within the window. This endpoint never fetches individual
    rows for the count -- COUNT(*)/GROUP BY runs entirely in SQL, so it
    cannot be truncated by any row limit regardless of volume.

    window=24h|7d|30d|all (X29.6.1: default 24h). completed_after (unix
    seconds) may be supplied instead of window for a custom cutoff.
    """
    conn = _conn()
    try:
        from src.ops.discovery_window import parse_window_param, window_seconds_for
        completed_after_param = request.args.get("completed_after")
        if completed_after_param is not None:
            completed_after = int(completed_after_param)
            window = "custom"
        else:
            window = parse_window_param(request.args.get("window"))
            completed_after = None if window == "all" else int(time.time()) - window_seconds_for(window)

        if completed_after is None:
            rows = conn.execute(
                "SELECT outcome_type, COUNT(*) AS n FROM wt_attribution_outcomes "
                "GROUP BY outcome_type"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT outcome_type, COUNT(*) AS n FROM wt_attribution_outcomes "
                "WHERE completed_at >= ? GROUP BY outcome_type",
                (completed_after,),
            ).fetchall()
        counts = {row["outcome_type"]: row["n"] for row in rows}

        # X26.11 — presentation-only grouping. KNOWN_CEX_REACHED/
        # KNOWN_BRIDGE_REACHED/KNOWN_RELAY_REACHED are the canonical
        # attribution outcomes for a reviewed terminal-infrastructure
        # boundary being legitimately reached (see src/ops/attribution_outcome
        # .py's OUTCOME_TYPES and _boundary()) -- LINEAGE_GAP/
        # UNKNOWN_INFRASTRUCTURE are deliberately excluded: their
        # terminal_entity_type may also read "INFRASTRUCTURE", but they mean
        # "walkback ran out of evidence" / "not yet a reviewed boundary",
        # the opposite concept. This never changes wt_attribution_outcomes
        # or OUTCOME_TYPES -- it's a second, additive aggregation computed
        # from the same already-fetched counts, with a further breakdown by
        # terminal_entity_type queried only for those three outcome types so
        # the drill-down subtypes (Exchange/Relay/Bridge/Automation/Custody)
        # are available without a second round-trip. Adding a new reviewed
        # infrastructure registry category later requires no code change
        # here -- it already gets its own terminal_entity_type row.
        _REVIEWED_TERMINAL_OUTCOME_TYPES = (
            "KNOWN_CEX_REACHED", "KNOWN_BRIDGE_REACHED", "KNOWN_RELAY_REACHED",
        )
        _SUBTYPE_LABELS = {
            "CEX": "Exchange (CEX)", "AUTOMATION": "Automation", "BRIDGE": "Bridge",
            "RELAY": "Relay", "CUSTODY": "Custody", "PLATFORM": "Platform",
            "PROTOCOL": "Protocol", "SYSTEM": "System", "INFRASTRUCTURE": "Infrastructure",
        }
        reviewed_total = sum(counts.get(t, 0) for t in _REVIEWED_TERMINAL_OUTCOME_TYPES)
        subtype_placeholders = ",".join("?" for _ in _REVIEWED_TERMINAL_OUTCOME_TYPES)
        if completed_after is None:
            subtype_rows = conn.execute(
                "SELECT terminal_entity_type, COUNT(*) AS n FROM wt_attribution_outcomes "
                f"WHERE outcome_type IN ({subtype_placeholders}) GROUP BY terminal_entity_type",
                _REVIEWED_TERMINAL_OUTCOME_TYPES,
            ).fetchall()
        else:
            subtype_rows = conn.execute(
                "SELECT terminal_entity_type, COUNT(*) AS n FROM wt_attribution_outcomes "
                f"WHERE outcome_type IN ({subtype_placeholders}) AND completed_at >= ? "
                "GROUP BY terminal_entity_type",
                (*_REVIEWED_TERMINAL_OUTCOME_TYPES, completed_after),
            ).fetchall()
        subtypes = [
            {
                "terminal_entity_type": row["terminal_entity_type"],
                "label": _SUBTYPE_LABELS.get(str(row["terminal_entity_type"] or "").upper(), str(row["terminal_entity_type"] or "Unknown")),
                "count": row["n"],
            }
            for row in subtype_rows
        ]
        subtypes.sort(key=lambda x: -x["count"])

        return jsonify({
            "ok": True,
            "window": window,
            "completed_after": completed_after,
            "counts": counts,
            "reviewed_infrastructure": {
                "total": reviewed_total,
                "subtypes": subtypes,
            },
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    finally:
        conn.close()


from src.ops.swr_cache import SWRCache as _SWRCache

# X29.1.4 — the legacy Investigation Queue endpoint shares the SAME
# evaluate_launcher_profile()-per-creator cost as Operational Intelligence
# (~31s measured live) but had no cache at all, and its fetch sits inside
# discovery.html's main BLOCKING Promise.all — so every page load was
# stalling on this endpoint specifically, not the (already-cached,
# already-async) Operational Intelligence panel. Fixed with the same
# SWRCache primitive X29.1.2 built.
#
# Kept as a SEPARATE cache instance from _OPERATIONAL_INTELLIGENCE_CACHE
# (below) rather than one shared cache: build_pipeline_health() (bucket
# assignments) and build_operational_intelligence() (topology/behaviour/
# mechanism records) are genuinely different functions returning different
# response shapes, even though both internally call
# evaluate_launcher_profile() per creator — a single cache keyed only by
# window_seconds would have to hold two incompatible value shapes under one
# key, which is more confusing than two clearly-named caches. If a future
# sprint wants to eliminate the duplicated evaluate_launcher_profile() work
# itself, that belongs in a shared per-creator profile cache underneath
# both functions (the wt_launcher_profile_cache table X27.9.1 already
# flagged as a follow-up candidate), not at this route-caching layer.
_INVESTIGATION_PIPELINE_CACHE_TTL_SEC = 300
_INVESTIGATION_PIPELINE_CACHE = _SWRCache(ttl_seconds=_INVESTIGATION_PIPELINE_CACHE_TTL_SEC)


def _get_pipeline_health(window_seconds: int):
    """Returns (pipeline, meta) — meta = {state, age_seconds, generated_at},
    same optional-metadata shape X29.1.2 established for consistency."""
    from src.ops.investigation_pipeline import build_pipeline_health

    def compute():
        return build_pipeline_health(OPS_DB_PATH, LIVE_DB_PATH, window_seconds=window_seconds)

    return _INVESTIGATION_PIPELINE_CACHE.get(window_seconds, compute)


@ops_dashboard_bp.route("/api/ops-v2/investigation-pipeline")
def api_investigation_pipeline():
    """X27.2/X27.5 — Investigation Reduction Pipeline. Reduces every migrated
    launch (wt_attribution_outcomes row) in the window into EXACTLY ONE
    mutually-exclusive investigative bucket (src/ops/investigation_pipeline
    .py), re-ranked by investigative usefulness rather than attribution
    -confidence order. As of X27.5, this includes the two behavioural
    archetypes (Rapid Birth → Launch, Burst Launches) formerly split into a
    separate Behaviour Queue dashboard -- every migrated launch now appears
    in exactly one bucket across the whole platform, not per-panel. Read
    -only, zero writes; does not alter attribution, detection, walkback, or
    classification. bucket=<BUCKET_ID> filters the assignment map to a
    drill-down list for one bucket only; group_by=creator groups that
    bucket's mints by creator wallet (launch count desc) instead of
    returning a flat token list -- most useful for REPEAT_CREATOR, where
    the creator identity is the natural drill-down unit and individual
    tokens are one click further down from there.
    """
    try:
        from src.ops.discovery_window import parse_window_param, window_seconds_for, empty_state_message
        window_param = parse_window_param(request.args.get("window"))
        window_seconds = window_seconds_for(window_param)
        from src.ops.investigation_pipeline import (
            launches_in_bucket, creators_in_bucket, BUCKET_ORDER,
        )
        pipeline, cache_meta = _get_pipeline_health(window_seconds)

        bucket_filter = request.args.get("bucket")
        group_by = (request.args.get("group_by") or "").strip().lower()
        response = {
            "ok": True,
            "window": window_param,
            "generated_at": pipeline["generated_at"],
            "total_launches": pipeline["total_launches"],
            "conserved": pipeline["conserved"],
            "buckets": pipeline["buckets"],
            # X29.1.4 — additive only, same convention as
            # /api/ops-v2/operational-intelligence (X29.1.2); every field
            # above this line is unchanged from X27.2/X27.5.
            "cache_state": cache_meta["state"],
            "cache_age_seconds": cache_meta["age_seconds"],
        }
        # X29.6.1 — informative empty state: a confirmed, correctly-attributed
        # launch older than the selected window must never read as "Discovery
        # has no data" (the exact X29.6 root cause).
        if pipeline["total_launches"] == 0:
            response["empty_state_message"] = empty_state_message(window_param)
        if bucket_filter:
            if bucket_filter not in BUCKET_ORDER:
                return jsonify({"ok": False, "error": f"Unknown bucket '{bucket_filter}'"}), 400
            response["bucket"] = bucket_filter
            if group_by == "creator":
                response["group_by"] = "creator"
                response["creators"] = creators_in_bucket(pipeline, bucket_filter)
            else:
                response["mints"] = launches_in_bucket(pipeline, bucket_filter)
        # X27.9.1 Phase 7 — single-launch lookup: exclusive bucket + supplementary
        # behavioural evidence, so a launch's exclusive classification and its
        # secondary evidence can both be verified/consumed without a second endpoint.
        mint_filter = request.args.get("mint")
        if mint_filter:
            assignment = pipeline["assignments"].get(mint_filter)
            response["mint"] = mint_filter
            response["assignment"] = assignment
            # X29.3 — Funding Boundary (renamed from X29.2's Capital Origin):
            # additive, read-only, zero RPC. Never overrides outcome_type/
            # assignment above; null when no record exists.
            from src.ops.funding_boundary import get_funding_boundary, serialize_funding_boundary
            from src.ops.wallet_quality import get_wallet_quality, serialize_wallet_quality
            fb_conn = sqlite3.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True, timeout=10)
            try:
                fb_record = get_funding_boundary(fb_conn, mint_filter)
                # X29.4 — Wallet Quality: a SEPARATE, orthogonal annotation
                # dimension, never folded into funding_boundary/assignment.
                # Keyed on the creator wallet when a funding-boundary row
                # exists (subject_wallet); otherwise not looked up.
                wq_record = None
                if fb_record and fb_record.get("subject_wallet"):
                    wq_record = get_wallet_quality(fb_conn, fb_record["subject_wallet"])
            finally:
                fb_conn.close()
            response["funding_boundary"] = serialize_funding_boundary(fb_record)
            response["wallet_quality"] = serialize_wallet_quality(wq_record)
        return jsonify(response)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


from src.ops.swr_cache import SWRCache as _SWRCache

_OPERATIONAL_INTELLIGENCE_CACHE_TTL_SEC = 300
# X29.1.2 — replaces X29.1's plain TTL cache (which blocked the FIRST request
# after every 5-minute expiry on the full ~31s evaluate_launcher_profile()
# recomputation) with stale-while-revalidate + single-flight refresh: once a
# key has been populated once, no later request ever blocks on recomputation
# again -- a stale request gets the previous result immediately and schedules
# exactly one background refresh. See src/ops/swr_cache.py for the full
# state machine (FRESH/STALE/REFRESHING) and atomic-swap guarantee.
_OPERATIONAL_INTELLIGENCE_CACHE = _SWRCache(ttl_seconds=_OPERATIONAL_INTELLIGENCE_CACHE_TTL_SEC)


def _get_operational_intelligence(window_seconds: int):
    """Returns (intel, meta) — meta = {state, age_seconds, generated_at},
    optional response metadata per X29.1.2 (existing consumers that only
    read the `intel`-derived response fields are unaffected)."""
    from src.ops.operational_intelligence import build_operational_intelligence

    def compute():
        return build_operational_intelligence(OPS_DB_PATH, LIVE_DB_PATH, window_seconds=window_seconds)

    return _OPERATIONAL_INTELLIGENCE_CACHE.get(window_seconds, compute)


def prewarm_operational_intelligence_cache() -> None:
    """X29.1.2/X29.1.4/X29.6.1 optional startup prewarm — populates all four
    Discovery window values (24h/7d/30d/all) for BOTH the Operational
    Intelligence and the legacy Investigation Queue caches once at process
    startup, so the very first analyst request at any window never pays
    either endpoint's cold-compute cost. Each prewarm runs off-thread and
    independently best-effort; a failure in one does not affect the others
    or prevent the route's own cold-compute fallback from working."""
    import threading
    from src.ops.discovery_window import WINDOW_ORDER, window_seconds_for

    def _prewarm(label, get_fn, window_seconds):
        try:
            get_fn(window_seconds)
        except Exception as exc:
            try:
                import logging
                logging.getLogger(__name__).warning(
                    "%s prewarm failed for window_seconds=%s: %s", label, window_seconds, exc)
            except Exception:
                pass

    for window_param in WINDOW_ORDER:
        window_seconds = window_seconds_for(window_param)
        threading.Thread(
            target=_prewarm, args=("Operational Intelligence", _get_operational_intelligence, window_seconds),
            daemon=True).start()
        threading.Thread(
            target=_prewarm, args=("Investigation Queue", _get_pipeline_health, window_seconds),
            daemon=True).start()


@ops_dashboard_bp.route("/api/ops-v2/operational-intelligence")
def api_operational_intelligence():
    """X29.1 — Operational Topology Intelligence Framework. Classifies every
    launch in the window by three INDEPENDENT dimensions (src/ops/
    funding_topology.py, operational_behaviour_tags.py, funding_mechanism.py):
    Funding Topology (exactly one of FAN_OUT/LINEAR/MULTI_LEVEL_FAN_OUT/
    MESH/UNKNOWN), Operational Behaviour (zero or more additive tags), and
    Funding Mechanism (zero or more additive tags). Read-only, zero writes;
    does not alter detection, attribution, or the existing investigation
    -pipeline bucket model (src/ops/investigation_pipeline.py — retained
    separately, not replaced by this route).

    Query params:
      window=24h|7d|30d|all         -- X29.6.1: same convention as
                                        investigation-pipeline; defaults to 24h
      view=hierarchy                -- returns the Topology->Behaviour->Mechanism
                                        drill-down tree (computed fresh from the
                                        flat per-mint records every call; the
                                        tree itself is never stored)
      topology=<FAN_OUT|...>         -- filter mints to this topology
      behaviour=<RAPID_BIRTH_LAUNCH|...> -- filter mints carrying this tag
      mechanism=<WSOL_WRAP_CLOSE|...>    -- filter mints carrying this tag
      Any combination of topology/behaviour/mechanism may be supplied
      together (cross-dimensional query, e.g.
      ?topology=FAN_OUT&mechanism=PLAIN_TRANSFER) -- the brief's explicit
      requirement that no hierarchy prevent cross-dimensional searching.
      mint=<MINT>                    -- single-launch lookup
    """
    try:
        from src.ops.discovery_window import parse_window_param, window_seconds_for, empty_state_message
        window_param = parse_window_param(request.args.get("window"))
        window_seconds = window_seconds_for(window_param)
        intel, cache_meta = _get_operational_intelligence(window_seconds)

        response = {
            "ok": True,
            "window": window_param,
            "generated_at": intel["generated_at"],
            "total_launches": intel["total_launches"],
            "conserved": intel["conserved"],
            "topology_summary": intel["topology_summary"],
            "behaviour_summary": intel["behaviour_summary"],
            "mechanism_summary": intel["mechanism_summary"],
            # X29.1.2 — optional metadata, additive only; every field above
            # this line is unchanged from X29.1, so existing consumers keep
            # working without modification.
            "cache_state": cache_meta["state"],
            "cache_age_seconds": cache_meta["age_seconds"],
        }
        # X29.6.1 — informative empty state, same convention as
        # investigation-pipeline.
        if intel["total_launches"] == 0:
            response["empty_state_message"] = empty_state_message(window_param)

        if (request.args.get("view") or "").strip().lower() == "hierarchy":
            from src.ops.operational_intelligence import build_hierarchy
            response["hierarchy"] = build_hierarchy(intel)["tree"]

        topology_filter = request.args.get("topology")
        behaviour_filter = request.args.get("behaviour")
        mechanism_filter = request.args.get("mechanism")
        if topology_filter or behaviour_filter or mechanism_filter:
            from src.ops.operational_intelligence import query as oi_query
            response["filter"] = {
                "topology": topology_filter, "behaviour": behaviour_filter, "mechanism": mechanism_filter,
            }
            mints = oi_query(
                intel, topology=topology_filter, behaviour=behaviour_filter, mechanism=mechanism_filter,
            )
            response["mints"] = mints
            # X29.1.3 — optional presentation grouping of the matched launches
            # by attribution outcome (e.g. "separate by CEX-reached vs Repeat
            # Creator"). Additive: existing consumers reading `mints` are
            # unaffected; `groups` only appears when explicitly requested.
            # Reuses wt_attribution_outcomes.outcome_type — no new detection.
            if (request.args.get("group_by") or "").strip().lower() == "outcome":
                from src.ops.operational_intelligence import group_mints_by_outcome
                response["group_by"] = "outcome"
                response["groups"] = group_mints_by_outcome(OPS_DB_PATH, mints)["groups"]

        mint_filter = request.args.get("mint")
        if mint_filter:
            response["mint"] = mint_filter
            response["record"] = intel["records"].get(mint_filter)

        return jsonify(response)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@ops_dashboard_bp.route("/api/ops-v2/operational-intelligence/cache-metrics")
def api_operational_intelligence_cache_metrics():
    """X29.1.2 deliverable — runtime metrics for the Operational Intelligence
    SWR cache: cache_hits, stale_serves, refreshes_started/succeeded/failed,
    refreshes_suppressed (single-flight collisions), cold_computes. Also
    reports each currently-populated window's live state (fresh/stale/
    refreshing) for direct observability during a cache-expiry event."""
    try:
        from src.ops.discovery_window import WINDOW_ORDER, window_seconds_for
        states = {}
        for window_param in WINDOW_ORDER:
            window_seconds = window_seconds_for(window_param)
            states[str(window_seconds)] = _OPERATIONAL_INTELLIGENCE_CACHE.state_of(window_seconds)
        return jsonify({"ok": True, "metrics": _OPERATIONAL_INTELLIGENCE_CACHE.metrics, "window_states": states})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@ops_dashboard_bp.route("/api/ops-v2/operations/<operation_id>")
def api_operation_identity_detail(operation_id):
    """X25.4 Phase 9 — read-only Operation Identity detail. No mutation, no
    merge/split controls, no analyst override. Reuses
    src/ops/operation_identity.py's already-built read-only resolver."""
    try:
        from src.ops.operation_identity import build_operations
        result = build_operations()
        op = result["operations"].get(operation_id)
        if not op:
            return jsonify({"ok": False, "error": "operation not found"}), 404
        return jsonify({"ok": True, "operation": op})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@ops_dashboard_bp.route("/api/ops-v2/known-operations")
def api_operations_list():
    """X29.7 — Operations-first Discovery landing list. Named distinctly
    from the pre-existing /api/ops-v2/operations route (a DIFFERENT, older
    wt_ops_v2/operation_uuid concept -- unrelated to this sprint) to avoid
    a route collision. Every known operation (src/ops/operation_identity
    .py's existing treasury-mesh resolver, UNCHANGED) plus a summary (role
    counts + funding-mechanism/-boundary distributions, all derived from
    already-persisted evidence -- src/ops/operations_summary.py). Zero new
    intelligence calculation; zero writes."""
    try:
        from src.ops.operations_summary import build_operations_summary
        result = build_operations_summary(OPS_DB_PATH)
        return jsonify({"ok": True, **result})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@ops_dashboard_bp.route("/api/ops-v2/lineage/<path:wallet>")
def api_operational_lineage(wallet):
    """X29.7 — Operational Lineage for one wallet: a VARIABLE-DEPTH chain
    (Treasury -> ... -> Creator, however many hops actually exist in
    wt_provisioning_edges/wt_watchtower_launches), not a fixed four-role
    model. See src/ops/operational_lineage.py. Read-only, zero writes, zero
    new intelligence -- reads the same facts funding_topology.py already
    reads."""
    try:
        from src.ops.operational_lineage import build_lineage
        conn = sqlite3.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True, timeout=10)
        try:
            lineage = build_lineage(conn, wallet)
        finally:
            conn.close()
        return jsonify({"ok": True, **lineage})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@ops_dashboard_bp.route("/api/ops-v2/roles/<role_type>")
def api_roles_browse(role_type):
    """X29.7 — Browse By Role: list distinct wallets holding a given role
    (TREASURY/SUBPROVIDER/CREATOR), derived on-the-fly from
    wt_provisioning_edges + wt_watchtower_launches (the same source of
    truth src/ops/operational_lineage.py's roles_for_wallet() uses) --
    no new wt_wallet_roles table, so this can never drift from the edges
    themselves. limit param bounds the result set (default 100, max 500)."""
    try:
        role = (role_type or "").strip().upper()
        from src.ops.operational_lineage import ROLE_TREASURY, ROLE_SUBPROVIDER, ROLE_CREATOR
        if role not in (ROLE_TREASURY, ROLE_SUBPROVIDER, ROLE_CREATOR):
            return jsonify({"ok": False, "error": f"unknown role '{role_type}'"}), 400
        limit = max(1, min(int(request.args.get("limit", 100)), 500))
        conn = sqlite3.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True, timeout=10)
        try:
            wallets: set[str] = set()
            if role == ROLE_TREASURY:
                for r in conn.execute("SELECT treasury FROM wt_confirmed_treasuries"):
                    wallets.add(r[0])
                for r in conn.execute(
                    "SELECT DISTINCT from_wallet FROM wt_provisioning_edges WHERE edge_type='TREASURY_TO_SUBPROV'"
                ):
                    wallets.add(r[0])
                for r in conn.execute(
                    "SELECT DISTINCT treasury_wallet FROM wt_watchtower_launches WHERE treasury_wallet IS NOT NULL"
                ):
                    wallets.add(r[0])
            elif role == ROLE_SUBPROVIDER:
                for r in conn.execute(
                    "SELECT DISTINCT to_wallet FROM wt_provisioning_edges WHERE edge_type='TREASURY_TO_SUBPROV'"
                ):
                    wallets.add(r[0])
                for r in conn.execute(
                    "SELECT DISTINCT from_wallet FROM wt_provisioning_edges WHERE edge_type='SUBPROV_TO_CREATOR'"
                ):
                    wallets.add(r[0])
                for r in conn.execute(
                    "SELECT DISTINCT subprov_wallet FROM wt_watchtower_launches WHERE subprov_wallet IS NOT NULL"
                ):
                    wallets.add(r[0])
            else:  # CREATOR
                for r in conn.execute(
                    "SELECT DISTINCT to_wallet FROM wt_provisioning_edges WHERE edge_type='SUBPROV_TO_CREATOR'"
                ):
                    wallets.add(r[0])
                for r in conn.execute(
                    "SELECT DISTINCT creator_wallet FROM wt_watchtower_launches WHERE creator_wallet IS NOT NULL"
                ):
                    wallets.add(r[0])
        finally:
            conn.close()
        wallet_list = sorted(wallets)
        return jsonify({
            "ok": True, "role": role, "total": len(wallet_list),
            "wallets": wallet_list[:limit],
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@ops_dashboard_bp.route("/api/ops-v2/discovery-triage/summary")
def api_discovery_triage_summary():
    """X21C — Level-1 operational summary + pattern buckets for the Discovery
    Triage Workspace. Read-only aggregation over wt_attribution_outcomes
    (INSUFFICIENT_EVIDENCE, LINEAGE_GAP only); no attribution/walkback/resolver
    logic is touched."""
    conn = _conn()
    try:
        from src.ops.discovery_triage import build_triage_summary
        return jsonify(build_triage_summary(conn))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    finally:
        conn.close()


@ops_dashboard_bp.route("/api/ops-v2/discovery-triage/queue")
def api_discovery_triage_queue():
    """X21C — Ranked, grouped investigation queue. Ranking is a deterministic
    function of persisted signal presence/count, never an opaque score."""
    conn = _conn()
    try:
        from src.ops.discovery_triage import build_investigation_queue
        limit = max(1, min(int(request.args.get("limit", 100)), 500))
        filter_key = (request.args.get("filter") or "").strip() or None
        return jsonify(build_investigation_queue(conn, limit=limit, filter_key=filter_key))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    finally:
        conn.close()


@ops_dashboard_bp.route("/api/ops-v2/emerging-operator-seeds")
def api_emerging_operator_seeds():
    """Strict X20 intake: only current UNKNOWN_INFRASTRUCTURE conclusions."""
    conn = _conn()
    try:
        from src.ops.attribution_outcome import emerging_operator_seeds
        rows = emerging_operator_seeds(conn)
        return jsonify({"ok": True, "required_outcome_type": "UNKNOWN_INFRASTRUCTURE",
                        "seeds": rows, "count": len(rows)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    finally:
        conn.close()

@ops_dashboard_bp.route("/api/ops-v2/discovery-assurance")
def api_discovery_assurance():
    """Discovery Assurance — DB-only, no RPC, no serializer writes.

    Measures graph completeness: what fraction of WATCHTOWER operator
    infrastructure is visible and producing WS observations. Ground truth
    is wt_farm_launches (populated by farm_detector.py from migrated tokens,
    independent of WATCHTOWER detection decisions).

    Denominator: wt_farm_launches rows with a known-subprov funder (619).
    Numerator: those where a wrap-close candidate was observed (6).
    The 739 launches with completely unknown funders are NOT in the
    denominator — they represent scope we cannot even attribute.
    """
    import time as _time
    now = int(_time.time())
    try:
        conn = sqlite3.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # ── universe ──────────────────────────────────────────────────────
        cur.execute("SELECT COUNT(*) FROM wt_farm_launches")
        total_launches = cur.fetchone()[0]

        # ── known graph infrastructure ─────────────────────────────────────
        cur.execute("SELECT COUNT(*) FROM wt_confirmed_treasuries WHERE no_subscribe IS NULL OR no_subscribe = 0")
        active_treasuries = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM wt_confirmed_treasuries")
        total_treasuries = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM wt_confirmed_treasuries WHERE no_subscribe = 1")
        excluded_treasuries = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM wt_discovered_subprovs")
        total_subprovs = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM wt_discovered_subprovs WHERE treasury IS NOT NULL")
        linked_subprovs = cur.fetchone()[0]

        # ── D1: unknown scope (funder not in any known graph node) ─────────
        cur.execute("""
            SELECT COUNT(*) FROM wt_farm_launches fl
            WHERE NOT EXISTS (SELECT 1 FROM wt_confirmed_treasuries ct WHERE ct.treasury = fl.funder)
            AND   NOT EXISTS (SELECT 1 FROM wt_discovered_subprovs  ds WHERE ds.subprov  = fl.funder)
        """)
        d1_unknown = cur.fetchone()[0]

        # ── D2: excluded treasury lineage ──────────────────────────────────
        cur.execute("""
            SELECT COUNT(*) FROM wt_farm_launches fl
            JOIN wt_discovered_subprovs ds ON ds.subprov = fl.funder
            JOIN wt_confirmed_treasuries ct ON ct.treasury = ds.treasury
            WHERE ct.no_subscribe = 1
        """)
        d2_excluded = cur.fetchone()[0]

        # ── D6: topology gap (known subprov, treasury unknown) ─────────────
        cur.execute("""
            SELECT COUNT(*) FROM wt_farm_launches fl
            JOIN wt_discovered_subprovs ds ON ds.subprov = fl.funder
            WHERE ds.treasury IS NULL
        """)
        d6_topology_gap = cur.fetchone()[0]

        # ── D3: known subprov, no wrap-close observed ──────────────────────
        cur.execute("""
            SELECT COUNT(*) FROM wt_farm_launches fl
            JOIN wt_discovered_subprovs ds ON ds.subprov = fl.funder
            WHERE ds.treasury IS NOT NULL
            AND   NOT EXISTS (
                SELECT 1 FROM wt_wrap_close_candidates wcc WHERE wcc.creator = fl.creator
            )
        """)
        d3_no_wcc = cur.fetchone()[0]

        # ── denominator: known-subprov launches ────────────────────────────
        cur.execute("""
            SELECT COUNT(*) FROM wt_farm_launches fl
            JOIN wt_discovered_subprovs ds ON ds.subprov = fl.funder
        """)
        known_subprov_launches = cur.fetchone()[0]

        # ── numerator: wrap-close observed for those launches ──────────────
        cur.execute("""
            SELECT COUNT(DISTINCT fl.mint)
            FROM wt_farm_launches fl
            JOIN wt_discovered_subprovs ds ON ds.subprov = fl.funder
            WHERE EXISTS (
                SELECT 1 FROM wt_wrap_close_candidates wcc WHERE wcc.creator = fl.creator
            )
        """)
        wcc_observed = cur.fetchone()[0]

        # ── worst treasuries by discovery gap ──────────────────────────────
        cur.execute("""
            SELECT ct.treasury,
                   COUNT(*) AS total_launches,
                   SUM(CASE WHEN NOT EXISTS (
                       SELECT 1 FROM wt_wrap_close_candidates wcc WHERE wcc.creator = fl.creator
                   ) THEN 1 ELSE 0 END) AS gap
            FROM wt_farm_launches fl
            JOIN wt_discovered_subprovs ds ON ds.subprov = fl.funder
            JOIN wt_confirmed_treasuries ct ON ct.treasury = ds.treasury
            GROUP BY ct.treasury
            HAVING gap > 0
            ORDER BY gap DESC
            LIMIT 5
        """)
        worst_treasuries = [dict(r) for r in cur.fetchall()]

        # ── worst subprovs by discovery gap ────────────────────────────────
        cur.execute("""
            SELECT ds.subprov,
                   COUNT(*) AS total_launches,
                   SUM(CASE WHEN NOT EXISTS (
                       SELECT 1 FROM wt_wrap_close_candidates wcc WHERE wcc.creator = fl.creator
                   ) THEN 1 ELSE 0 END) AS gap
            FROM wt_farm_launches fl
            JOIN wt_discovered_subprovs ds ON ds.subprov = fl.funder
            GROUP BY ds.subprov
            HAVING gap > 0
            ORDER BY gap DESC
            LIMIT 10
        """)
        worst_subprovs = [dict(r) for r in cur.fetchall()]

        conn.close()

        discovery_rate_pct = round(100.0 * wcc_observed / known_subprov_launches, 1) if known_subprov_launches else 0.0
        unknown_pct = round(100.0 * d1_unknown / total_launches, 1) if total_launches else 0.0

        return jsonify({
            "ok":            True,
            "generated_at":  now,
            "summary": {
                "total_launches":         total_launches,
                "unknown_scope":          d1_unknown,
                "unknown_scope_pct":      unknown_pct,
                "known_subprov_launches": known_subprov_launches,
                "wcc_observed":           wcc_observed,
                "discovery_rate_pct":     discovery_rate_pct,
                "discovery_rate_label":   "Known Graph Only",
            },
            "infrastructure": {
                "total_treasuries":  total_treasuries,
                "active_treasuries": active_treasuries,
                "excluded_treasuries": excluded_treasuries,
                "total_subprovs":    total_subprovs,
                "linked_subprovs":   linked_subprovs,
            },
            "failure_attribution": {
                "D1_unknown_funder":     d1_unknown,
                "D2_excluded_treasury":  d2_excluded,
                "D3_no_wcc_observed":    d3_no_wcc,
                "D6_topology_gap":       d6_topology_gap,
            },
            "worst_treasuries": worst_treasuries,
            "worst_subprovs":   worst_subprovs,
        })

    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 500


@ops_dashboard_bp.route("/ops/discovery-assurance")
def page_discovery_assurance():
    return render_template("watchtower_discovery_assurance.html")


def register_operation_dashboard_routes(app):
    app.register_blueprint(ops_dashboard_bp)
