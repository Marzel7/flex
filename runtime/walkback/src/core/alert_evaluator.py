"""
WATCHTOWER Detection Health Alert Evaluator  (Sprint O3.1)

Single source of truth for alert evaluation and state persistence.

Consumed by:
  • scripts/run_alert_evaluator.py — standalone supervised process (writes)
  • operation_dashboard_routes.api_alerts — read-only dashboard endpoint

Design rules
  • Reads ops DB (wt_ops_v2.db) via the same read-only _conn()-equivalent.
  • Writes ONLY to wt_alerts.db — no touch to detection pipeline.
  • Does NOT duplicate heartbeat / PW / treasury logic from api_detection_health.
    Instead it re-queries the same underlying tables with identical thresholds.
  • PW_DEGRADED only fires when heartbeat confidence is LIVE (Task 7).
    A STALE heartbeat already means NO_TELEMETRY explains the uncertainty;
    reporting PW state from an aged snapshot would be misleading.
  • No executable SQL in operator_action fields (Task 8).  Actions carry a
    runbook_id and plain-English guidance only.
"""

import os
import sqlite3
import json
import time

# ── DB paths (mirrors operation_dashboard_routes.py) ──────────────────────────
_REPO_ROOT     = os.path.normpath(os.path.join(os.path.dirname(__file__), "../.."))
OPS_DB_PATH    = os.environ.get("OPS_V2_DB_PATH",  os.path.join(_REPO_ROOT, "database", "wt_ops_v2.db"))
ALERTS_DB_PATH = os.environ.get("ALERTS_DB_PATH",  os.path.join(_REPO_ROOT, "database", "wt_alerts.db"))

# ── Alert thresholds ──────────────────────────────────────────────────────────
HB_LIVE_S          = 120    # heartbeat younger than this → LIVE
HB_UNKNOWN_S       = 300    # heartbeat older than this   → UNKNOWN
RETRY_STUCK_AGE_S  = 6 * 3600   # PENDING sig-retry row age threshold
RETRY_STUCK_MIN    = 3           # minimum stuck rows to fire

# Severity ordering for sort
SEV_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


# ── wt_alerts.db helpers ──────────────────────────────────────────────────────

def alerts_conn():
    """Writable connection to wt_alerts.db.

    Isolated file — never contends with wt_ops_v2.db WAL.
    Plain sqlite3 (no TrackedConnection) — not part of the detection pipeline.
    """
    c = sqlite3.connect(ALERTS_DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=10000")
    return c


def alerts_conn_ro():
    """Read-only connection to wt_alerts.db for the dashboard endpoint."""
    c = sqlite3.connect(f"file:{ALERTS_DB_PATH}?mode=ro", uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=5000")
    return c


def ensure_alert_table():
    """Create wt_alert_state in wt_alerts.db if absent. Idempotent. Safe to call at startup.

    Also runs ADD COLUMN migrations for pre-O3.1 tables that have `action` but
    not `runbook_id` / `operator_action`.  sqlite3 ADD COLUMN is safe to run
    repeatedly (we swallow the 'duplicate column' error).
    """
    c = alerts_conn()
    try:
        c.execute("""
            CREATE TABLE IF NOT EXISTS wt_alert_state (
                alert_id        TEXT PRIMARY KEY,
                severity        TEXT NOT NULL,
                title           TEXT NOT NULL,
                summary         TEXT NOT NULL,
                detail          TEXT,
                runbook_id      TEXT,
                operator_action TEXT,
                state           TEXT NOT NULL DEFAULT 'RAISED',
                raised_at       INTEGER NOT NULL,
                last_seen_at    INTEGER NOT NULL,
                resolved_at     INTEGER
            )
        """)
        # Migrate pre-O3.1 tables — ADD COLUMN is idempotent via exception swallow
        for col in ("runbook_id TEXT", "operator_action TEXT"):
            try:
                c.execute(f"ALTER TABLE wt_alert_state ADD COLUMN {col}")
            except Exception:
                pass
        c.commit()
    finally:
        c.close()


# ── Evaluation ────────────────────────────────────────────────────────────────

def _ops_conn_ro():
    """Read-only connection to wt_ops_v2.db for evaluation queries."""
    c = sqlite3.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=10000")
    return c


def evaluate_alerts(now: int) -> list:
    """
    Evaluate all four alert conditions against live DB state.

    Returns a list of dicts, one per alert class:
        { alert_id, firing: bool, ...fields when firing... }

    Uses the same tables and thresholds as api_detection_health so the two
    implementations remain in sync. Does NOT call the HTTP endpoint to avoid
    loopback dependency.

    Task 7 — PW_DEGRADED fires only when heartbeat confidence is LIVE.
    A STALE heartbeat means the PW value is already of uncertain age;
    NO_TELEMETRY (which fires at >300s) already explains the situation.
    Firing both from stale data would be redundant and potentially misleading.
    """
    db = _ops_conn_ro()
    try:
        hb_row = db.execute(
            "SELECT last_seen, meta_json FROM wt_worker_heartbeat WHERE worker_name='ws_cascade'"
        ).fetchone()

        meta     = json.loads(hb_row["meta_json"]) if hb_row and hb_row["meta_json"] else {}
        hb_age_s = (now - hb_row["last_seen"]) if hb_row else None
        hb_conf  = (
            "LIVE"    if hb_age_s is not None and hb_age_s < HB_LIVE_S else
            "STALE"   if hb_age_s is not None and hb_age_s < HB_UNKNOWN_S else
            "UNKNOWN"
        )
        pw_state = meta.get("pw_stream_state", "")

        treasury_rows = db.execute(
            "SELECT treasury, no_subscribe, confidence FROM wt_confirmed_treasuries"
        ).fetchall()
        ws_usage = {
            r["treasury_wallet"]: r for r in db.execute(
                "SELECT treasury_wallet, last_notif_at FROM wt_treasury_ws_usage"
            ).fetchall()
        }

        stuck = db.execute("""
            SELECT COUNT(*) n, MIN(first_seen_at) oldest_at
            FROM wt_subprov_sig_retry
            WHERE status='PENDING' AND first_seen_at < ?
        """, (now - RETRY_STUCK_AGE_S,)).fetchone()
        stuck_n      = stuck["n"]
        stuck_oldest = stuck["oldest_at"]

    finally:
        db.close()

    results = []

    # ── Alert 1: NO_TELEMETRY ─────────────────────────────────────────────────
    # Fires when a heartbeat row EXISTS (process has run before) but is too old.
    # If no row exists at all it's a fresh deployment — different condition, not alerted.
    if hb_row is not None and hb_conf == "UNKNOWN":
        results.append({
            "alert_id": "NO_TELEMETRY",
            "firing":   True,
            "severity": "CRITICAL",
            "title":    "WATCHTOWER telemetry lost",
            "summary":  (
                f"Cascade heartbeat has not updated for {hb_age_s}s "
                f"(threshold: {HB_UNKNOWN_S}s). Current detection state is unknown."
            ),
            "detail": (
                f"The ws_cascade process last wrote a heartbeat {hb_age_s}s ago. "
                f"All runtime state — ProgramWatcher, subscriptions, candidate counts — "
                f"cannot be trusted. WATCHTOWER may or may not be detecting launches."
            ),
            "runbook_id":      "RB-001",
            "operator_action": (
                "1. Run: supervisorctl status ws_cascade\n"
                "2. If STOPPED: supervisorctl start ws_cascade\n"
                "3. If RUNNING: check logs/supervisor/ws_cascade.log for reconnect errors or panics\n"
                "4. If process crashes repeatedly, check Helius WS connectivity and API key validity"
            ),
            "detail_data": {"heartbeat_age_s": hb_age_s, "last_seen_at": hb_row["last_seen"]},
        })
    else:
        results.append({"alert_id": "NO_TELEMETRY", "firing": False})

    # ── Alert 2: PW_DEGRADED ─────────────────────────────────────────────────
    # Task 7: Only fire when heartbeat confidence is LIVE.
    # STALE → NO_TELEMETRY already explains the uncertainty; don't report
    # a stale PW snapshot as a current degradation.
    if hb_conf == "LIVE" and pw_state != "ACTIVE":
        results.append({
            "alert_id": "PW_DEGRADED",
            "firing":   True,
            "severity": "HIGH",
            "title":    "ProgramWatcher not ACTIVE",
            "summary":  (
                f"Cascade heartbeat is current but ProgramWatcher stream state "
                f"is '{pw_state or 'unknown'}'. Real-time CREATE detection may be impaired."
            ),
            "detail": (
                f"ProgramWatcher subscribes to pump.fun program logs for sub-second CREATE detection. "
                f"When not ACTIVE, launches may only be caught by ACTIVE_CATCHUP "
                f"(backup polling path, median latency 3–46s vs 1–6s for PROGRAM_LOGS). "
                f"Heartbeat age: {hb_age_s}s."
            ),
            "runbook_id":      "RB-002",
            "operator_action": (
                "1. Check ws_cascade logs for ProgramWatcher subscription errors:\n"
                "   tail -100 logs/supervisor/ws_cascade.log | grep -i 'programwatcher\\|pw_stream'\n"
                "2. A ws_cascade restart typically restores ACTIVE state within 30s:\n"
                "   supervisorctl restart ws_cascade\n"
                "3. If state does not return to ACTIVE after restart, check Helius program-log subscription limits"
            ),
            "detail_data": {"pw_stream_state": pw_state, "heartbeat_age_s": hb_age_s},
        })
    else:
        results.append({"alert_id": "PW_DEGRADED", "firing": False})

    # ── Alert 3: UNMONITORED_TREASURY ────────────────────────────────────────
    unmonitored = []
    for t in treasury_rows:
        if t["no_subscribe"]:
            continue
        usage = ws_usage.get(t["treasury"])
        if usage is None or not usage["last_notif_at"]:
            unmonitored.append({"treasury": t["treasury"], "confidence": t["confidence"]})

    if unmonitored:
        n = len(unmonitored)
        results.append({
            "alert_id": "UNMONITORED_TREASURY",
            "firing":   True,
            "severity": "HIGH",
            "title":    f"{n} confirmed {'treasury' if n==1 else 'treasuries'} never monitored",
            "summary":  (
                f"{n} confirmed {'treasury has' if n==1 else 'treasuries have'} "
                f"no WS monitoring evidence. Launches from "
                f"{'it' if n==1 else 'them'} will not be detected in real time."
            ),
            "detail": (
                f"{n} {'wallet' if n==1 else 'wallets'} present in wt_confirmed_treasuries "
                f"but absent from wt_treasury_ws_usage (or last_notif_at is NULL). "
                f"This typically occurs when a treasury is confirmed after the last ws_cascade restart, "
                f"so no subscription has been opened yet. "
                f"Affected: {', '.join(u['treasury'][:12]+'…' for u in unmonitored[:5])}"
                f"{'…' if n>5 else ''}"
            ),
            "runbook_id":      "RB-003",
            "operator_action": (
                "1. Restart ws_cascade to pick up newly confirmed treasuries:\n"
                "   supervisorctl restart ws_cascade\n"
                "2. Allow 60s for subscription confirmation after restart\n"
                "3. If alert persists after restart, verify the treasury wallet is enrolled "
                "in wt_confirmed_treasuries with no_subscribe=0"
            ),
            "detail_data": {"unmonitored": unmonitored},
        })
    else:
        results.append({"alert_id": "UNMONITORED_TREASURY", "firing": False})

    # ── Alert 4: RETRY_BACKLOG ────────────────────────────────────────────────
    if stuck_n >= RETRY_STUCK_MIN:
        age_h = round((now - stuck_oldest) / 3600, 1) if stuck_oldest else None
        results.append({
            "alert_id": "RETRY_BACKLOG",
            "firing":   True,
            "severity": "LOW",
            "title":    f"{stuck_n} sig-retry rows stuck",
            "summary":  (
                f"{stuck_n} signature retry rows have been PENDING for more than "
                f"{RETRY_STUCK_AGE_S // 3600}h. These are permanently unresolvable "
                f"(getTransaction returned None — transactions pruned by the RPC)."
            ),
            "detail": (
                f"{stuck_n} rows in wt_subprov_sig_retry with status=PENDING, "
                f"age >{RETRY_STUCK_AGE_S // 3600}h. Oldest: {age_h}h ago. "
                f"These do not block detection but accumulate as dead weight. "
                f"The RPC pruned these transactions; they will never resolve."
            ),
            "runbook_id":      "RB-004",
            "operator_action": (
                f"Clear the backlog using the maintenance script:\n"
                f"  python3 scripts/clear_stuck_sig_retries.py\n"
                f"The script marks all PENDING rows older than {RETRY_STUCK_AGE_S // 3600}h as FAILED. "
                f"This is safe — these transactions are permanently unavailable from the RPC."
            ),
            "detail_data": {
                "stuck_count":        stuck_n,
                "stuck_oldest_age_h": age_h,
                "threshold_h":        RETRY_STUCK_AGE_S // 3600,
            },
        })
    else:
        results.append({"alert_id": "RETRY_BACKLOG", "firing": False})

    return results


# ── Persistence ───────────────────────────────────────────────────────────────

def persist_alerts(evaluated: list, now: int) -> list:
    """
    Upsert evaluated alert results into wt_alert_state.

      firing=True  + no existing row       → INSERT state=RAISED
      firing=True  + RAISED/ACTIVE         → UPDATE state=ACTIVE, last_seen_at
      firing=True  + RECOVERED             → re-raise with new raised_at
      firing=False + RAISED/ACTIVE         → UPDATE state=RECOVERED, resolved_at
      firing=False + no row / RECOVERED    → no-op

    raised_at is set exactly once per incident (never overwritten while RAISED/ACTIVE).
    resolved_at is set once when condition first clears.

    Returns all rows ordered by recency (for dashboard consumption).
    """
    c = alerts_conn()
    try:
        ensure_alert_table()   # idempotent — cheap if table exists

        for ev in evaluated:
            aid = ev["alert_id"]
            existing = c.execute(
                "SELECT state, raised_at FROM wt_alert_state WHERE alert_id=?", (aid,)
            ).fetchone()

            if ev["firing"]:
                fields = (
                    ev.get("severity", ""),
                    ev.get("title", ""),
                    ev.get("summary", ""),
                    ev.get("detail", ""),
                    ev.get("runbook_id"),
                    ev.get("operator_action", ""),
                )
                if existing is None:
                    c.execute("""
                        INSERT INTO wt_alert_state
                          (alert_id, severity, title, summary, detail, runbook_id, operator_action,
                           state, raised_at, last_seen_at, resolved_at)
                        VALUES (?,?,?,?,?,?,?, 'RAISED',?,?,NULL)
                    """, (aid, *fields, now, now))
                elif existing["state"] in ("RAISED", "ACTIVE"):
                    c.execute("""
                        UPDATE wt_alert_state
                        SET state='ACTIVE', last_seen_at=?,
                            severity=?, title=?, summary=?, detail=?,
                            runbook_id=?, operator_action=?, resolved_at=NULL
                        WHERE alert_id=?
                    """, (now, *fields, aid))
                else:  # RECOVERED — re-raise
                    c.execute("""
                        UPDATE wt_alert_state
                        SET state='RAISED', raised_at=?, last_seen_at=?,
                            severity=?, title=?, summary=?, detail=?,
                            runbook_id=?, operator_action=?, resolved_at=NULL
                        WHERE alert_id=?
                    """, (now, now, *fields, aid))
            else:
                if existing and existing["state"] in ("RAISED", "ACTIVE"):
                    c.execute("""
                        UPDATE wt_alert_state
                        SET state='RECOVERED', resolved_at=?, last_seen_at=?
                        WHERE alert_id=?
                    """, (now, now, aid))

        c.commit()

        rows = c.execute("""
            SELECT alert_id, severity, title, summary, detail, runbook_id, operator_action,
                   state, raised_at, last_seen_at, resolved_at
            FROM wt_alert_state
            ORDER BY COALESCE(resolved_at, last_seen_at) DESC
        """).fetchall()
        return [dict(r) for r in rows]

    finally:
        c.close()


def read_alerts() -> tuple:
    """
    Read current alert state for the dashboard — no writes.
    Returns (active_list, recovered_list) sorted by severity then recency.
    """
    try:
        c = alerts_conn_ro()
    except Exception:
        return [], []

    try:
        rows = c.execute("""
            SELECT alert_id, severity, title, summary, detail, runbook_id, operator_action,
                   state, raised_at, last_seen_at, resolved_at
            FROM wt_alert_state
            ORDER BY COALESCE(resolved_at, last_seen_at) DESC
        """).fetchall()
    finally:
        c.close()

    all_rows = [dict(r) for r in rows]
    active    = [r for r in all_rows if r["state"] in ("RAISED", "ACTIVE")]
    recovered = [r for r in all_rows if r["state"] == "RECOVERED"]
    active.sort(key=lambda r: SEV_RANK.get(r.get("severity", "LOW"), 99))
    return active, recovered


# ── Standalone evaluator loop (called by scripts/run_alert_evaluator.py) ──────

def run_evaluator(interval_s: int = 30):
    """
    Evaluate → persist loop. Runs forever; designed for supervisor management.
    On startup, ensures wt_alert_state table exists before entering the loop.
    """
    print(f"[alert_evaluator] Starting — interval={interval_s}s, "
          f"ops_db={OPS_DB_PATH}, alerts_db={ALERTS_DB_PATH}", flush=True)

    ensure_alert_table()
    print("[alert_evaluator] wt_alert_state table ready", flush=True)

    while True:
        t0 = time.monotonic()
        try:
            now       = int(time.time())
            evaluated = evaluate_alerts(now)
            rows      = persist_alerts(evaluated, now)

            firing    = [e for e in evaluated if e["firing"]]
            recovered = [r for r in rows if r["state"] == "RECOVERED"]

            elapsed_ms = round((time.monotonic() - t0) * 1000)
            print(
                f"[alert_evaluator] evaluated {len(evaluated)} alerts — "
                f"{len(firing)} firing, {len(recovered)} recovered ({elapsed_ms}ms)",
                flush=True,
            )
        except Exception as exc:
            import traceback
            print(f"[alert_evaluator] ERROR: {exc}", flush=True)
            traceback.print_exc()

        time.sleep(interval_s)
