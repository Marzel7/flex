#!/usr/bin/env python3
"""
Phase 1.3 — Operation Scheduler / Continuous Watching.

Turns the two callable runners into a continuously-watching STANDALONE process:

  * INTAKE         (post-migration discovery)  — slow cadence, default 15 min
  * FORWARD_MONITOR(pre-migration expansion)   — fast cadence, default 3 min

Architecture: a single standalone process (NOT a gunicorn thread) launched via
supervisord. If this crashes the live app survives; if the live app restarts this
survives. Stays fully isolated — reads the live DB read-only (via the runners),
writes ONLY to wt_ops_v2.db, touches nothing in the live WATCH pipeline.

Independence: the two loops have independent next-run timestamps. The forward
monitor never waits on intake; a failure in one job never stops the other.

Run:
    python -m src.core.operation_scheduler --loop          # continuous
    python -m src.core.operation_scheduler --once-intake
    python -m src.core.operation_scheduler --once-forward
    python -m src.core.operation_scheduler --status
"""

from __future__ import annotations

import os
import sys
import time
import random
import signal
import traceback
from typing import Optional

from src.utils.db_locking import db_connect
from src.core.operation_store_v2 import run_intake, OPS_DB_PATH, ensure_schema as _ensure_store_schema
from src.core.operation_forward_monitor import operation_forward_monitor, ensure_schema as _ensure_fwd_schema

# ── cadence + budget config (env-overridable) ──────────────────────────────
INTAKE_INTERVAL = int(os.environ.get("OPS_INTAKE_INTERVAL_SEC", "900"))    # 15 min
FORWARD_INTERVAL = int(os.environ.get("OPS_FORWARD_INTERVAL_SEC", "180"))  # 3 min
INTAKE_MAX_RPC = int(os.environ.get("OPS_INTAKE_MAX_RPC", "120"))
FORWARD_MAX_RPC = int(os.environ.get("OPS_FORWARD_MAX_RPC", "180"))
DAILY_MAX_RPC = int(os.environ.get("OPS_DAILY_MAX_RPC", "0")) or None       # 0 = unlimited
INTAKE_DAYS = int(os.environ.get("OPS_INTAKE_DAYS", "7"))
INTAKE_LIMIT = int(os.environ.get("OPS_INTAKE_LIMIT", "50"))
FORWARD_LIMIT_WALLETS = int(os.environ.get("OPS_FORWARD_LIMIT_WALLETS", "0")) or None
DEGRADE_AFTER_FAILURES = 3        # consecutive failures -> skip that job one interval

LOCK_FILE = os.environ.get(
    "OPS_SCHEDULER_LOCK",
    os.path.join(os.path.dirname(__file__), "..", "..", "operation_scheduler.lock"),
)

_STOP = False


# ── run-log schema ─────────────────────────────────────────────────────────
def ensure_run_log(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS wt_ops_v2_runs (
            id                       INTEGER PRIMARY KEY AUTOINCREMENT,
            job_type                 TEXT NOT NULL,        -- INTAKE | FORWARD_MONITOR
            started_at               INTEGER NOT NULL,
            finished_at              INTEGER,
            status                   TEXT,                 -- OK | ERROR | BUDGET | DEGRADED
            runtime_sec              REAL,
            candidates_seen          INTEGER DEFAULT 0,
            traced                   INTEGER DEFAULT 0,
            skipped                  INTEGER DEFAULT 0,
            operations_discovered    INTEGER DEFAULT 0,
            operations_merged        INTEGER DEFAULT 0,
            operations_expanded      INTEGER DEFAULT 0,
            families_linked          INTEGER DEFAULT 0,
            wallets_added            INTEGER DEFAULT 0,
            edges_added              INTEGER DEFAULT 0,
            creators_added           INTEGER DEFAULT 0,
            activity_events_added    INTEGER DEFAULT 0,
            candidate_creators_added INTEGER DEFAULT 0,
            rpc_used                 INTEGER DEFAULT 0,
            rpc_budget               INTEGER DEFAULT 0,
            stopped_due_to_budget    INTEGER DEFAULT 0,
            error_message            TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_runs_job ON wt_ops_v2_runs(job_type, started_at);
        """
    )
    conn.commit()


def _log_run(job_type: str, started: int, s: dict, status: str, error: Optional[str],
             rpc_budget: int, stopped_budget: bool):
    """Write one run-log row. Short transaction, after the job (never during RPC)."""
    conn = db_connect(OPS_DB_PATH, timeout=30)
    try:
        ensure_run_log(conn)
        conn.execute(
            """INSERT INTO wt_ops_v2_runs
               (job_type, started_at, finished_at, status, runtime_sec, candidates_seen,
                traced, skipped, operations_discovered, operations_merged, operations_expanded,
                families_linked, wallets_added, edges_added, creators_added,
                activity_events_added, candidate_creators_added, rpc_used, rpc_budget,
                stopped_due_to_budget, error_message)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (job_type, started, int(time.time()), status, (s or {}).get("runtime_s"),
             (s or {}).get("candidates", 0),
             (s or {}).get("traced_ok", 0) or (s or {}).get("wallets_polled", 0),
             (s or {}).get("skipped_known", 0),
             (s or {}).get("discovered", 0), (s or {}).get("merged", 0), (s or {}).get("expanded", 0),
             (s or {}).get("family_linked", 0),
             (s or {}).get("wallets_added", 0), (s or {}).get("edges_added", 0),
             (s or {}).get("creators_added", 0),
             (s or {}).get("new_children", 0),
             (s or {}).get("new_candidates", 0),
             (s or {}).get("rpc_calls", 0), rpc_budget, 1 if stopped_budget else 0, error),
        )
        conn.commit()
    finally:
        conn.close()


# ── single-instance lock ────────────────────────────────────────────────────
def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def acquire_lock(path: str) -> bool:
    """Acquire the single-instance lock. Returns False if a live owner exists.
    A stale lock (dead owner) is removed and re-acquired."""
    if os.path.exists(path):
        try:
            with open(path) as f:
                owner = int((f.read().strip() or "0"))
        except (ValueError, OSError):
            owner = 0
        if owner and _pid_alive(owner) and owner != os.getpid():
            print(f"[SCHED] another scheduler is running (pid {owner}); exiting.")
            return False
        # stale -> reclaim
        try:
            os.remove(path)
        except OSError:
            pass
    try:
        with open(path, "w") as f:
            f.write(str(os.getpid()))
        return True
    except OSError as e:
        print(f"[SCHED] could not write lock file {path}: {e}")
        return False


def release_lock(path: str):
    try:
        if os.path.exists(path):
            with open(path) as f:
                if (f.read().strip() or "0") == str(os.getpid()):
                    os.remove(path)
    except OSError:
        pass


# ── daily RPC accounting ────────────────────────────────────────────────────
def _rpc_today() -> int:
    conn = db_connect(OPS_DB_PATH, timeout=30)
    try:
        ensure_run_log(conn)
        row = conn.execute(
            "SELECT COALESCE(SUM(rpc_used),0) FROM wt_ops_v2_runs "
            "WHERE started_at >= strftime('%s','now','start of day')").fetchone()
        return int(row[0] or 0)
    finally:
        conn.close()


def _daily_budget_left() -> Optional[int]:
    if DAILY_MAX_RPC is None:
        return None
    return max(0, DAILY_MAX_RPC - _rpc_today())


# ── job wrappers (with backoff + degraded mode) ─────────────────────────────
_fail_counts = {"INTAKE": 0, "FORWARD_MONITOR": 0}
_degraded_until = {"INTAKE": 0, "FORWARD_MONITOR": 0}


def _effective_budget(base: int) -> tuple[int, bool]:
    """Clamp a job's RPC budget to whatever the daily cap allows."""
    left = _daily_budget_left()
    if left is None:
        return base, False
    if left <= 0:
        return 0, True
    return min(base, left), left < base


def run_treasury_fingerprint_job(quiet=False) -> dict:
    """POST-MIGRATION treasury auto-promotion. For recent migrations, walk the creator's
    lineage to its root and run the 3-signal treasury fingerprint. Strict pass →
    auto-promote into wt_confirmed_treasuries + webhook. Near-miss → review. No human gate
    (the fingerprint is well-defined). Cheap raw RPC only."""
    import urllib.request as _u, json as _j
    started = int(time.time())
    key = os.environ.get("HELIUS_API_KEY", "")
    rpc_url = os.environ.get("HELIUS_RPC_URL", f"https://mainnet.helius-rpc.com/?api-key={key}")
    calls = [0]

    def _rpc(method, params):
        calls[0] += 1
        body = _j.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        try:
            req = _u.Request(rpc_url, data=body, headers={"Content-Type": "application/json"})
            return _j.loads(_u.urlopen(req, timeout=15).read()).get("result")
        except Exception:
            return None

    AMM = {"pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA", "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"}
    PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

    def _raw_txs(addr):
        sigs = _rpc("getSignaturesForAddress", [addr, {"limit": 40}]) or []
        out = []
        for s in sigs[:40]:
            if s.get("err"):
                continue
            tx = _rpc("getTransaction", [s["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
            if not tx:
                continue
            meta = tx.get("meta") or {}
            msg = tx.get("transaction", {}).get("message", {})
            keys = [k.get("pubkey") if isinstance(k, dict) else k for k in msg.get("accountKeys", [])]
            progs = {ix.get("programId") for ix in msg.get("instructions", []) if ix.get("programId")}
            ttype = "SWAP" if (progs & AMM or PUMP in progs) else "TRANSFER"
            pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
            nts = []
            if addr in keys and len(pre) == len(keys):
                ai = keys.index(addr); fee = meta.get("fee", 0) or 0
                if pre[ai] - post[ai] - (fee if ai == 0 else 0) > 0:
                    for j, k in enumerate(keys):
                        if j != ai and post[j] - pre[j] > 0:
                            nts.append({"fromUserAccount": addr, "toUserAccount": k, "amount": post[j] - pre[j]})
            out.append({"type": ttype, "nativeTransfers": nts,
                        "timestamp": s.get("blockTime") or tx.get("blockTime", 0)})
        return out

    def _funders(addr):
        """WRAP-CLOSE-AWARE funder walk. The naive 'biggest inbound transfer' FAILS on the
        WATCHTOWER structure: a creator/subprov is funded via a WSOL wrap→close cycle through a
        single-use wrap wallet, so the biggest plain transfer is the dev-buy/trading inflow,
        NOT the provisioning edge. This wandered off and never reached the treasury (0
        ALREADY_CONFIRMED). FIX: if the funding tx is a wrap-close, the REAL funder is the
        subprov (the closeAccount lineage source), not the wrap wallet. Prefer wrap-close
        funders; fall back to plain-transfer only if no wrap-close found."""
        from src.core.wrap_close_detector import detect_wrap_close
        from collections import defaultdict as _dd
        # PAGINATE deep — a treasury→subprov funding is a one-time LARGE plain transfer that's
        # often OLD (busy subprovs bury it under newer wrap-closes/sweeps). Verified: 43PKjr →
        # 8aBvMmr was 600 SOL plain transfer, outside a 40-sig window. Page back up to ~150 txs.
        all_sigs = []
        before = None
        for _ in range(4):
            params = [addr, {"limit": 50}]
            if before:
                params[1]["before"] = before
            batch = _rpc("getSignaturesForAddress", params) or []
            if not batch:
                break
            all_sigs += batch
            before = batch[-1].get("signature")
            if len(batch) < 50:
                break
        wrap_funders = []                 # (subprov, amount) — TRUE creator-provisioning edge
        plain_totals = _dd(float)         # sender -> TOTAL SOL into addr (treasury = biggest)
        for s in all_sigs:
            if s.get("err"):
                continue
            tx = _rpc("getTransaction", [s["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
            if not tx:
                continue
            # 1. WRAP-CLOSE funding (addr = closeAccount destination) → subprov is the funder
            try:
                ext = detect_wrap_close(tx)
            except Exception:
                ext = None
            if ext and ext.get("creator") == addr and ext.get("subprov"):
                wrap_funders.append((ext["subprov"], ext.get("base_amount_sol") or 0.0))
                continue
            # 2. plain transfer INTO addr — accumulate per sender (treasury→subprov is the LARGEST)
            meta = tx.get("meta") or {}; msg = tx.get("transaction", {}).get("message", {})
            keys = [k.get("pubkey") if isinstance(k, dict) else k for k in msg.get("accountKeys", [])]
            pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
            if addr not in keys or len(pre) != len(keys):
                continue
            ai = keys.index(addr)
            if post[ai] - pre[ai] > 0.05:
                debs = [(keys[j], pre[j] - post[j]) for j in range(len(keys)) if j != ai and pre[j] - post[j] > 0.05]
                if debs:
                    f = max(debs, key=lambda x: x[1])
                    plain_totals[f[0]] += f[1] / 1e9
        # PREFER wrap-close funders. Else the biggest plain-transfer funder (treasury sends
        # one large lump → it dominates the total, beating noise/sweeps).
        if wrap_funders:
            return sorted(wrap_funders, key=lambda x: -x[1])
        return sorted(plain_totals.items(), key=lambda x: -x[1])

    promoted, reviewed, rejected = [], 0, 0
    # Validation: track total DB-open time vs total wall time to measure lock-hold reduction.
    _db_open_intervals = []   # list of (open_ts, close_ts) pairs
    def _open_conn():
        """Open a fresh short-lived ops DB connection. raw sqlite3 (isolation_level=None =
        autocommit) so each statement is its own transaction — no read snapshot held between
        statements. busy_timeout=30s absorbs brief contention from other writers."""
        import sqlite3 as _sq_sched
        _t = time.time()
        c = _sq_sched.connect(OPS_DB_PATH, timeout=30, isolation_level=None)
        c.execute("PRAGMA busy_timeout=30000")
        _db_open_intervals.append([_t, None])   # None = still open
        return c
    def _close_conn(c):
        """Close and record hold duration."""
        if c:
            try:
                c.close()
            except Exception:
                pass
        if _db_open_intervals and _db_open_intervals[-1][1] is None:
            _db_open_intervals[-1][1] = time.time()

    try:
        from src.core import treasury_bank
        from src.utils.db_locking import db_connect
        live = db_connect(os.path.join(os.path.dirname(OPS_DB_PATH), "flex_complete_database.db"), timeout=20)

        # ── PHASE A: reads only (no RPC) — short-lived conn, close before any RPC work ──
        # already-fingerprinted — don't redo. A creator is "done" if it (or its lineage) was
        # logged either as the decision wallet OR as the source_migration of a decision.
        conn = _open_conn()
        treasury_bank.ensure_schema(conn)
        done = set()
        for r in conn.execute(
            "SELECT wallet, source_migration FROM wt_treasury_fingerprint_decisions").fetchall():
            if r[0]: done.add(r[0])
            if r[1]: done.add(r[1])
        # recently-migrated creators with known funding (live DB), newest first.
        # EXCLUDE serial deployers: the WATCHTOWER pattern uses FRESH single-use creators
        # (one creator → one token → discarded). A creator with >1 token in token_analysis
        # is a serial deployer, NOT a WATCHTOWER creator — the exact false-positive class.
        # This is computed from LOCAL token_analysis only (zero RPC) and cuts the candidate
        # set ~45% while removing noise.
        cand = [r[0] for r in live.execute(
            "SELECT cw FROM ("
            "  SELECT COALESCE(ta.earliest_tx_creator, ta.pf_ws_creator) cw, MAX(ta.migrated_at) last_mig "
            "  FROM token_analysis ta "
            "  WHERE COALESCE(ta.earliest_tx_creator, ta.pf_ws_creator) IS NOT NULL "
            "  GROUP BY cw "
            "  HAVING COUNT(*) <= 1 "                                    # single-token creators only
            "    AND MAX(ta.migrated_at) IS NOT NULL "
            "    AND MAX(ta.migrated_at) > strftime('%s','now')-259200 "  # migrated in last 3 days
            "    AND cw IN (SELECT creator_address FROM creator_funders) "
            ") ORDER BY last_mig DESC LIMIT 200").fetchall()]
        # process the first 25 NEW (not-yet-fingerprinted) ones this cycle; the rest next cycle
        creators = [c for c in cand if c and c not in done][:25]
        _close_conn(conn); conn = None   # ← release before any RPC

        # ── PHASE B: ONE-SHOT rescore (sentinel-gated — runs at most once ever) ──
        # Re-scores prior fingerprint candidates with the fixed raw-tx ping logic. The
        # webhook-blind ping bug forced every near-miss to ping=0, so strong treasuries were stuck
        # at 2/3 (some human-rejected on the broken signal).
        _resc_sentinel = os.path.join(os.path.dirname(__file__), "../../logs/.treasury_rescore_done")
        if not os.path.exists(_resc_sentinel):
            try:
                # rescore_decided_candidates internally opens the conn it receives for reads+writes.
                # Open a fresh conn for the entire rescore pass (it's bounded to 40 candidates,
                # each requiring RPC + a write — the hold is short relative to the 25-creator loop).
                conn = _open_conn()
                rs = treasury_bank.rescore_decided_candidates(conn, _raw_txs, max_candidates=40)
                _close_conn(conn); conn = None
                print(f"[SCHED] treasury rescore: checked={rs['checked']} now_ready_3of3={rs['now_ready_3of3']} "
                      f"still_near={rs['still_near_miss']}", flush=True)
                for addr, out_sol, pings in rs.get("ready", []):
                    print(f"[SCHED]   READY 3/3: {addr[:12]}… out={out_sol}◎ pings={pings}", flush=True)
                with open(_resc_sentinel, "w") as _f:
                    _f.write(str(int(time.time())))
            except Exception as e:
                if conn is not None:
                    _close_conn(conn); conn = None
                print(f"[SCHED] treasury rescore failed: {e}", flush=True)

        # ── PHASE C: per-creator evaluation — fresh conn per creator, closed between creators ──
        # evaluate_lineage_root interleaves DB reads with RPC calls then writes a single decision
        # at the end. A per-creator conn means the max hold per conn is one creator's evaluation
        # (~5–20s of RPC), not the full 25-creator cycle (~230s). The ops DB is free between
        # creators so the cascade and forward-monitor can write without contention.
        for cw in creators:
            if calls[0] > 800:               # RPC safety cap for one cycle
                break
            conn = _open_conn()
            try:
                res = treasury_bank.evaluate_lineage_root(conn, live, cw, _funders, _raw_txs)
            finally:
                _close_conn(conn); conn = None   # ← always release after each creator
            # evaluate_lineage_root now ALWAYS logs a decision against the creator
            # (CONFIRMED/REVIEW/REJECT/NO_ROOT/ALREADY_CONFIRMED), so cw is marked done and
            # won't be re-checked next cycle — the loop processes NEW creators each run.
            if not res:
                continue
            v = res.get("verdict")
            if v == "CONFIRMED" and res.get("needs_webhook"):
                promoted.append(res["treasury"])
            elif v == "REVIEW":
                reviewed += 1
            elif v == "REJECT":
                rejected += 1

        live.close()

        # Validation: compute total DB hold time vs wall time for this cycle.
        _wall = time.time() - started
        _hold = sum((iv[1] or time.time()) - iv[0] for iv in _db_open_intervals)
        _pct = round(100 * _hold / _wall, 1) if _wall > 0 else 0
        print(f"[SCHED][TREASURY_FP] conn_hold={round(_hold,1)}s wall={round(_wall,1)}s "
              f"hold_pct={_pct}% opens={len(_db_open_intervals)}", flush=True)

        # POST-MIGRATION LAUNCH BACKFILL: walk recent migrations backward; any whose lineage
        # reaches a known WATCHTOWER treasury is RECORDED as a launch (wt_watchtower_launches is
        # otherwise live-cascade-only, so a token missed live never surfaces on /ops/tokens). This
        # is ATTRIBUTION from the proven migration, distinct from real-time DETECTION.
        try:
            from src.core import watchtower_backfill as _bf
            def _get_sigs(addr, limit=20):
                return _rpc("getSignaturesForAddress", [addr, {"limit": limit}])
            def _get_tx(sig):
                return _rpc("getTransaction", [sig, {"encoding": "jsonParsed",
                                                     "maxSupportedTransactionVersion": 0}])
            _bres = _bf.backfill_recent_migrations(
                raw_txs_fn=_raw_txs, funder_walk_fn=_funders,
                get_tx_fn=_get_tx, get_sigs_fn=_get_sigs)
            if not quiet and (_bres.get("recorded") or _bres.get("checked")):
                print(f"[SCHED][BACKFILL] checked={_bres['checked']} recorded={_bres['recorded']}"
                      + (f" → {[m[:10] for m,_ in _bres.get('details',[])]}" if _bres.get('recorded') else ""))
        except Exception as _be:
            if not quiet:
                print(f"[SCHED][BACKFILL] error: {_be}")
        # auto-webhook the newly promoted treasuries (live-DB key, in this process)
        if promoted:
            try:
                import asyncio as _a
                from src.analysis.webhook_manager import WebhookManager, INFRA_ROLE
                loop = _a.new_event_loop()
                mgr = WebhookManager(os.path.join(os.path.dirname(OPS_DB_PATH), "flex_complete_database.db"))
                loop.run_until_complete(mgr.enroll_batch(promoted, role=INFRA_ROLE, notes="auto-fingerprint confirmed treasury"))
                loop.close()
                # update the audit ledger's webhook_status for each promoted treasury
                from src.utils.db_locking import db_connect as _dbc
                _c = _dbc(OPS_DB_PATH, timeout=20)
                for _t in promoted:
                    treasury_bank.mark_webhooked(_c, _t, ok=True)
                _c.close()
            except Exception as exc:
                if not quiet:
                    print(f"[SCHED][TREASURY_FP] webhook of promoted failed: {exc}")
        s = {"promoted": len(promoted), "reviewed": reviewed, "rejected": rejected, "rpc": calls[0],
             "runtime_s": round(time.time() - started, 1)}
        _log_run("TREASURY_FP", started, s, "OK", None, 800, False)
        if not quiet and (promoted or reviewed):
            print(f"[SCHED][TREASURY_FP] OK promoted={len(promoted)} review={reviewed} reject={rejected} rpc={calls[0]}"
                  + (f" → {[p[:10] for p in promoted]}" if promoted else ""))
        return {"status": "OK", "summary": s}
    except Exception as exc:
        if not quiet:
            print(f"[SCHED][TREASURY_FP] error: {type(exc).__name__}: {exc}")
        return {"status": "ERROR", "error": str(exc)}


def run_subprov_discovery_job(quiet=False) -> dict:
    """SUBPROV DISCOVERY — surfaces sub-provisioners (and thus UNKNOWN treasuries) from EVERY
    recent migration, bypassing the creator_funders extraction dependency (which lags/gaps on
    ~30% of migrations). Starting point = the CREATE signer (token_analysis.earliest_tx_creator),
    available the moment a token migrates — no funding extraction needed.

    Per creator (cheap, ~1-2 raw RPC): look at its funding tx — is it a WRAP-CLOSE? If so,
    extract the subprov (closeAccount lineage). Record the subprov + flag whether its treasury
    is known. Unknown-treasury subprovs are leads (surfaced in the Sub-Provisioners panel for
    manual on-chain tracing — backward-walking to the treasury is impractical on busy subprovs).
    Tracks checked creators so each cycle only processes NEW migrations. Raw RPC only."""
    import urllib.request as _u, json as _j
    started = int(time.time())
    key = os.environ.get("HELIUS_API_KEY", "")
    rpc_url = os.environ.get("HELIUS_RPC_URL", f"https://mainnet.helius-rpc.com/?api-key={key}")
    calls = [0]

    def _rpc(method, params):
        try:
            body = _j.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
            calls[0] += 1
            return _j.loads(_u.urlopen(_u.Request(rpc_url, data=body,
                headers={"Content-Type": "application/json"}), timeout=15).read()).get("result")
        except Exception:
            return None

    found = new_subprovs = unknown = 0
    try:
        from src.core.wrap_close_detector import detect_wrap_close
        # autocommit (isolation_level=None): each statement is its own transaction so NO read
        # snapshot is held across the RPC-heavy loop. A held snapshot pinned the ops-db WAL for the
        # whole cycle, blocking ALL other ops-db writers — it FROZE the ws_cascade heartbeat loop
        # (2.5h hang) and starved the farm scan. (lock-storm remaining cause #1.)
        import sqlite3 as _sq_sched
        conn = _sq_sched.connect(OPS_DB_PATH, timeout=30, isolation_level=None)
        conn.execute("PRAGMA busy_timeout=30000")
        live = db_connect(os.path.join(os.path.dirname(OPS_DB_PATH), "flex_complete_database.db"), timeout=20)
        conn.execute("""CREATE TABLE IF NOT EXISTS wt_subprov_discovery_checked (
            creator TEXT PRIMARY KEY, result TEXT, subprov TEXT, checked_at INTEGER)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS wt_discovered_subprovs (
            subprov TEXT PRIMARY KEY, first_creator TEXT, creator_count INTEGER DEFAULT 1,
            treasury TEXT, treasury_known INTEGER DEFAULT 0, first_seen INTEGER, last_seen INTEGER,
            immediate_funder TEXT, funder_is_subprov INTEGER DEFAULT 0)""")
        # Additive migration for pre-existing tables (immediate_funder = the wallet that
        # directly seeded this subprov; may itself be a subprov → the distribution tier).
        # `treasury` (root) is left UNTOUCHED — we only ADD the immediate-funder tier.
        for _col, _ddl in (("immediate_funder", "immediate_funder TEXT"),
                           ("funder_is_subprov", "funder_is_subprov INTEGER DEFAULT 0")):
            try:
                _have = {r[1] for r in conn.execute("PRAGMA table_info(wt_discovered_subprovs)").fetchall()}
                if _col not in _have:
                    conn.execute(f"ALTER TABLE wt_discovered_subprovs ADD COLUMN {_ddl}")
            except Exception:
                pass
        confirmed = {r[0] for r in conn.execute("SELECT treasury FROM wt_confirmed_treasuries").fetchall()}
        done = {r[0] for r in conn.execute("SELECT creator FROM wt_subprov_discovery_checked").fetchall()}
        # EVERY recent migration's creator (NO creator_funders filter, NO single-token filter)
        cands = [r[0] for r in live.execute(
            "SELECT DISTINCT COALESCE(earliest_tx_creator, pf_ws_creator) cw FROM token_analysis "
            "WHERE migrated_at > strftime('%s','now')-259200 "
            "AND COALESCE(earliest_tx_creator, pf_ws_creator) IS NOT NULL "
            "ORDER BY migrated_at DESC LIMIT 300").fetchall()]
        todo = [c for c in cands if c and c not in done][:30]   # 30 new creators/cycle
        _t_to_webhook = []   # treasuries auto-confirmed via launch-chain this cycle → webhook+WS
        for creator in todo:
            if calls[0] > 400:
                break
            # The funding wrap-close is the creator's FIRST tx (fresh single-use wallet seeded
            # then it CREATEs). Recent sigs are post-launch noise — page to the OLDEST tx.
            all_sigs = []
            before = None
            for _ in range(3):                      # up to ~150 sigs back to creation
                p = [creator, {"limit": 50}]
                if before:
                    p[1]["before"] = before
                batch = _rpc("getSignaturesForAddress", p) or []
                if not batch:
                    break
                all_sigs += batch
                before = batch[-1].get("signature")
                if len(batch) < 50:
                    break                            # reached the end (oldest)
            sigs = [s for s in all_sigs if not s.get("err")]
            sigs.sort(key=lambda x: x.get("blockTime") or 0)   # oldest first = the funding tx
            result, subprov = "NOT_WRAP_CLOSE", None
            for s in sigs[:6]:
                tx = _rpc("getTransaction", [s["signature"], {"encoding": "jsonParsed",
                          "maxSupportedTransactionVersion": 0}])
                if not tx:
                    continue
                try:
                    ext = detect_wrap_close(tx)
                except Exception:
                    ext = None
                if ext and ext.get("creator") == creator and ext.get("subprov"):
                    result, subprov = "WRAP_CLOSE", ext["subprov"]
                    break
            if not sigs:
                result = "NO_TX"
            conn.execute(
                "INSERT OR REPLACE INTO wt_subprov_discovery_checked (creator, result, subprov, checked_at) "
                "VALUES (?,?,?,?)", (creator, result, subprov, int(time.time())))
            if subprov:
                found += 1
                # who funds the subprov? cross-reference (zero RPC), in priority order:
                #   1. existing wrap-close lineage (we already linked this subprov to a treasury)
                #   2. treasury-outbound log (a known treasury sent to it)
                # If neither → UNKNOWN treasury (a genuine lead to investigate).
                t_known = None
                t_unconfirmed_funder = None   # the provisioning funder when it's NOT yet confirmed
                try:
                    lin = conn.execute(
                        "SELECT lineage_source_treasury FROM wt_wrap_close_candidates "
                        "WHERE subprov_wallet=? AND lineage_source_treasury IS NOT NULL LIMIT 1",
                        (subprov,)).fetchone()
                    if lin and lin[0] in confirmed:
                        t_known = lin[0]
                except Exception:
                    pass
                if not t_known and confirmed:
                    try:
                        row = live.execute(
                            "SELECT wallet_address FROM wt_webhook_hits WHERE direction='outbound' "
                            "AND counterparty=? AND wallet_address IN ({}) LIMIT 1".format(
                                ",".join("?" * len(confirmed))),
                            [subprov] + list(confirmed)).fetchone()
                        t_known = row[0] if row else None
                    except Exception:
                        pass
                # FINAL verify (1-2 RPC): if still unknown, check the subprov's top inbound funder
                # directly — subprov→treasury is ONE hop (a large lump near the subprov's creation,
                # findable unlike the deep creator-walk). If the funder is a confirmed treasury,
                # auto-resolve (don't surface a false 'unknown' lead for a known chain).
                if not t_known and confirmed and calls[0] < 380:
                    try:
                        ss = _rpc("getSignaturesForAddress", [subprov, {"limit": 50}]) or []
                        ss = [x for x in ss if not x.get("err")]
                        for sg in sorted(ss, key=lambda x: x.get("blockTime") or 0)[:20]:
                            stx = _rpc("getTransaction", [sg["signature"], {"encoding": "jsonParsed",
                                       "maxSupportedTransactionVersion": 0}])
                            if not stx:
                                continue
                            sm = stx.get("meta") or {}
                            sk = [k.get("pubkey") if isinstance(k, dict) else k
                                  for k in stx.get("transaction", {}).get("message", {}).get("accountKeys", [])]
                            spre, spost = sm.get("preBalances") or [], sm.get("postBalances") or []
                            if subprov in sk and len(spre) == len(sk):
                                si = sk.index(subprov)
                                if spost[si] - spre[si] > 1e9:   # received >1 SOL
                                    senders = [sk[j] for j in range(len(sk))
                                               if j != si and spre[j] - spost[j] > 1e9 and sk[j] in confirmed]
                                    if senders:
                                        t_known = senders[0]; break
                                    # NO confirmed sender, but the chain is REAL (this subprov did
                                    # wrap-close → a creator). The largest non-system inbound sender
                                    # is the provisioning treasury — capture it as a launch-chain
                                    # auto-confirm CANDIDATE (behavioral proof, not fingerprint).
                                    if not t_unconfirmed_funder:
                                        cand = sorted(
                                            [(sk[j], spre[j] - spost[j]) for j in range(len(sk))
                                             if j != si and spre[j] - spost[j] > 1e9],
                                            key=lambda x: x[1], reverse=True)
                                        if cand:
                                            t_unconfirmed_funder = cand[0][0]
                    except Exception:
                        pass
                # The IMMEDIATE funder = the wallet that directly seeded this subprov
                # (confirmed sender if known, else the largest inbound non-system sender).
                # This is the missing tier: it may itself be a subprov (distribution node).
                _imm_funder = t_known or t_unconfirmed_funder
                _funder_is_sp = 0
                if _imm_funder:
                    try:
                        # Mechanism guardrail: the funder is a SUBPROV-DISTRIBUTOR only if it
                        # PRODUCES creator-directed wrap-closes (present as a subprov_wallet).
                        _funder_is_sp = 1 if conn.execute(
                            "SELECT 1 FROM wt_wrap_close_candidates WHERE subprov_wallet=? LIMIT 1",
                            (_imm_funder,)).fetchone() else 0
                    except Exception:
                        _funder_is_sp = 0
                exists = conn.execute("SELECT creator_count FROM wt_discovered_subprovs WHERE subprov=?", (subprov,)).fetchone()
                if exists:
                    conn.execute("UPDATE wt_discovered_subprovs SET creator_count=creator_count+1, last_seen=?, "
                                 "treasury=COALESCE(treasury,?), treasury_known=MAX(treasury_known,?), "
                                 "immediate_funder=COALESCE(immediate_funder,?), "
                                 "funder_is_subprov=MAX(funder_is_subprov,?) WHERE subprov=?",
                                 (int(time.time()), t_known, 1 if t_known else 0,
                                  _imm_funder, _funder_is_sp, subprov))
                else:
                    new_subprovs += 1
                    if not t_known:
                        unknown += 1
                    conn.execute("INSERT INTO wt_discovered_subprovs (subprov, first_creator, treasury, "
                                 "treasury_known, first_seen, last_seen, immediate_funder, funder_is_subprov) "
                                 "VALUES (?,?,?,?,?,?,?,?)",
                                 (subprov, creator, t_known, 1 if t_known else 0,
                                  int(time.time()), int(time.time()), _imm_funder, _funder_is_sp))

                # LAUNCH-CHAIN AUTO-CONFIRM: we just observed a COMPLETED chain (this subprov
                # wrap-closed → a real creator). If its provisioning funder is NOT yet confirmed,
                # the chain itself is proof it's a WATCHTOWER treasury — confirm it now so its
                # FUTURE provisioning arms in real time, instead of waiting on a manual funder
                # trace (the lag that missed DchJqu's 14:07 wrap-close: discovered 12:55,
                # manually confirmed 16:56). Behavioral evidence, immune to the trading-desk
                # false positive (a recycling desk never produces a CREATE). Webhook off-thread.
                if t_unconfirmed_funder and not t_known and creator:
                    try:
                        import src.core.treasury_bank as _tb
                        res = _tb.auto_confirm_from_launch_chain(
                            conn, t_unconfirmed_funder, subprov=subprov, creator=creator,
                            mint=(result if isinstance(result, str) else ""),
                            create_sig=None)
                        if res.get("needs_webhook"):
                            print(f"[SCHED][SUBPROV] ⚡ LAUNCH-CHAIN auto-confirmed treasury "
                                  f"{t_unconfirmed_funder[:12]}… (subprov {subprov[:10]}… → creator "
                                  f"{creator[:10]}…) — webhooking", flush=True)
                            _t_to_webhook.append(t_unconfirmed_funder)
                    except Exception as _ace:
                        print(f"[SCHED][SUBPROV] auto-confirm error: {_ace}", flush=True)

                # COMMIT PER CREATOR — do NOT hold one transaction open across the whole
                # RPC-heavy loop. An open read+write txn pins the ops-db WAL for the loop's full
                # duration (minutes of getSignatures/getTransaction), which BLOCKED the WAL
                # checkpoint (1|167|167) and starved the ws_cascade's connections so it could
                # never subscribe treasuries. A short per-iteration txn releases the WAL pin
                # between RPC calls. (lock-storm remaining cause #1 — see DB_LOCK_STORM_DIAGNOSIS.)
                try:
                    conn.commit()
                except Exception:
                    pass
        conn.commit()
        # WEBHOOK + WS the launch-chain auto-confirmed treasuries. WS pickup is automatic (the
        # cascade's treasury tier reads wt_confirmed_treasuries on its next resync); here we just
        # enrol them on the Helius webhook (retry on lock — same as the funder-set flow).
        for _tw in dict.fromkeys(_t_to_webhook):
            try:
                import asyncio as _aio
                from src.analysis.webhook_manager import WebhookManager, INFRA_ROLE
                _wh_db = os.path.join(os.path.dirname(OPS_DB_PATH), "flex_complete_database.db")
                for _att in range(3):
                    try:
                        _loop = _aio.new_event_loop()
                        _mgr = WebhookManager(_wh_db)
                        _loop.run_until_complete(_mgr.enroll_batch([_tw], role=INFRA_ROLE,
                                                 notes="launch-chain auto-confirmed treasury"))
                        _loop.close()
                        import src.core.treasury_bank as _tb
                        _tb.mark_webhooked(conn, _tw, True)
                        break
                    except Exception as _we:
                        if "locked" in str(_we).lower() and _att < 2:
                            time.sleep(1.5 * (_att + 1)); continue
                        print(f"[SCHED][SUBPROV] webhook enrol failed for {_tw[:12]}…: {_we}", flush=True)
                        break
            except Exception as _oe:
                print(f"[SCHED][SUBPROV] webhook path error: {_oe}", flush=True)
        s = {"checked": len(todo), "wrap_close_found": found, "new_subprovs": new_subprovs,
             "new_unknown_treasury": unknown, "rpc": calls[0],
             "launch_chain_confirmed": len(set(_t_to_webhook)),
             "runtime_s": round(time.time() - started, 1)}
        _log_run("SUBPROV_DISCOVERY", started, s, "OK", None, 400, False)
        if not quiet and (found or new_subprovs):
            print(f"[SCHED][SUBPROV] checked={len(todo)} wrapclose={found} new_subprovs={new_subprovs} "
                  f"unknown_treasury={unknown} rpc={calls[0]}")
        conn.close(); live.close()
        return {"status": "OK", "summary": s}
    except Exception as exc:
        if not quiet:
            print(f"[SCHED][SUBPROV] error: {type(exc).__name__}: {exc}")
        return {"status": "ERROR", "error": str(exc)}


def run_intake_job(dry_run=False, quiet=False) -> dict:
    started = int(time.time())
    budget, capped = _effective_budget(INTAKE_MAX_RPC)
    if budget <= 0:
        _log_run("INTAKE", started, {}, "BUDGET", "daily RPC budget exhausted",
                 INTAKE_MAX_RPC, True)
        return {"status": "BUDGET"}
    try:
        s = run_intake(days=INTAKE_DAYS, limit=INTAKE_LIMIT, max_rpc=budget,
                       verbose=not quiet, dry_run=dry_run)
        stopped = bool(s.get("rpc_calls", 0) >= budget)
        status = "BUDGET" if (stopped and not capped) else "OK"
        if dry_run:
            status = "OK"
        else:
            _log_run("INTAKE", started, s, status, None, budget, stopped)
        _fail_counts["INTAKE"] = 0
        return {"status": status, "summary": s}
    except Exception as exc:                       # never crash the scheduler
        _fail_counts["INTAKE"] += 1
        err = f"{type(exc).__name__}: {exc}"
        if not dry_run:
            _log_run("INTAKE", started, {}, "ERROR", err[:300], budget, False)
        if not quiet:
            print(f"[SCHED][INTAKE] error: {err}\n{traceback.format_exc()[:500]}")
        if _fail_counts["INTAKE"] >= DEGRADE_AFTER_FAILURES:
            _degraded_until["INTAKE"] = int(time.time()) + INTAKE_INTERVAL
            if not quiet:
                print("[SCHED][INTAKE] entering degraded mode for one interval")
        return {"status": "ERROR", "error": err}


def run_forward_job(quiet=False) -> dict:
    started = int(time.time())
    budget, capped = _effective_budget(FORWARD_MAX_RPC)
    if budget <= 0:
        _log_run("FORWARD_MONITOR", started, {}, "BUDGET", "daily RPC budget exhausted",
                 FORWARD_MAX_RPC, True)
        return {"status": "BUDGET"}
    try:
        s = operation_forward_monitor(limit_wallets=FORWARD_LIMIT_WALLETS, max_rpc=budget,
                                      follow_children=True, verbose=not quiet)
        stopped = bool(s.get("rpc_calls", 0) >= budget)
        status = "BUDGET" if (stopped and not capped) else "OK"
        _log_run("FORWARD_MONITOR", started, s, status, None, budget, stopped)
        _fail_counts["FORWARD_MONITOR"] = 0
        return {"status": status, "summary": s}
    except Exception as exc:
        _fail_counts["FORWARD_MONITOR"] += 1
        err = f"{type(exc).__name__}: {exc}"
        _log_run("FORWARD_MONITOR", started, {}, "ERROR", err[:300], budget, False)
        if not quiet:
            print(f"[SCHED][FORWARD] error: {err}\n{traceback.format_exc()[:500]}")
        if _fail_counts["FORWARD_MONITOR"] >= DEGRADE_AFTER_FAILURES:
            _degraded_until["FORWARD_MONITOR"] = int(time.time()) + FORWARD_INTERVAL
            if not quiet:
                print("[SCHED][FORWARD] entering degraded mode for one interval")
        return {"status": "ERROR", "error": err}


# ── the loop ────────────────────────────────────────────────────────────────
def _handle_signal(signum, frame):
    global _STOP
    _STOP = True
    print(f"\n[SCHED] signal {signum} received — finishing current cycle then exiting.")


def loop(quiet=False):
    global _STOP
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    print(f"[SCHED] continuous watching started (pid {os.getpid()}). "
          f"intake every {INTAKE_INTERVAL}s, forward every {FORWARD_INTERVAL}s.")
    next_intake = 0.0       # run both immediately on start
    next_forward = 0.0
    while not _STOP:
        now = time.time()
        # forward monitor (fast cadence) — independent of intake
        if now >= next_forward:
            if now >= _degraded_until["FORWARD_MONITOR"]:
                r = run_forward_job(quiet=quiet)
                if not quiet:
                    su = (r.get("summary") or {})
                    print(f"[SCHED][FORWARD] {r['status']} "
                          f"children={su.get('new_children',0)} cand={su.get('new_candidates',0)} "
                          f"armed={su.get('armed',0)} rpc={su.get('rpc_calls',0)}")
                # ARM reconcile: disarm creators that migrated (remove webhook, mark
                # FIRED), expire stale armed creators, retry failed enrolments.
                try:
                    from src.core.operation_armed import reconcile_armed
                    ar = reconcile_armed()
                    if not quiet and (ar.get("fired") or ar.get("expired")):
                        print(f"[SCHED][ARMED] fired={ar['fired']} expired={ar['expired']} "
                              f"still_armed={ar['still_armed']}")
                except Exception as _re:
                    if not quiet:
                        print(f"[SCHED][ARMED] reconcile error: {_re}")
            elif not quiet:
                print("[SCHED][FORWARD] skipped (degraded)")
            next_forward = time.time() + FORWARD_INTERVAL
        # intake (slow cadence)
        now = time.time()
        if now >= next_intake:
            if now >= _degraded_until["INTAKE"]:
                r = run_intake_job(quiet=quiet)
                if not quiet:
                    su = (r.get("summary") or {})
                    print(f"[SCHED][INTAKE] {r['status']} "
                          f"disc={su.get('discovered',0)} merge={su.get('merged',0)} "
                          f"rpc={su.get('rpc_calls',0)}")
                # POST-MIGRATION treasury auto-promotion (fingerprint → auto-confirm+webhook)
                try:
                    if not quiet:
                        print(f"[SCHED][TREASURY_FP] START {int(time.time())}", flush=True)
                    run_treasury_fingerprint_job(quiet=quiet)
                    if not quiet:
                        print(f"[SCHED][TREASURY_FP] END {int(time.time())}", flush=True)
                except Exception as _tf:
                    if not quiet:
                        print(f"[SCHED][TREASURY_FP] error: {_tf}")
                # SUBPROV DISCOVERY — every migration's creator → wrap-close → subprov (bypasses
                # the creator_funders extraction gap; feeds the unknown-treasury investigation panel)
                try:
                    if not quiet:
                        print(f"[SCHED][SUBPROV] START {int(time.time())}", flush=True)
                    run_subprov_discovery_job(quiet=quiet)
                    if not quiet:
                        print(f"[SCHED][SUBPROV] END {int(time.time())}", flush=True)
                except Exception as _sp:
                    if not quiet:
                        print(f"[SCHED][SUBPROV] error: {_sp}")
                # FARM DETECTION — cluster recent single-use-creator migrated launches by funder
                # (mechanism-agnostic; catches the plain-transfer farms WATCHTOWER is blind to).
                # Bounded RPC; only traces creators not already in wt_farm_checked. Feeds the
                # Token Performance page's FARM tag.
                try:
                    from src.core.farm_detector import run_farm_scan
                    _fr = run_farm_scan(lookback_days=3, max_rpc=120, quiet=quiet)
                    if not quiet:
                        _fs = (_fr.get("summary") or {})
                        print(f"[SCHED][FARM] {_fr['status']} scanned={_fs.get('scanned',0)} "
                              f"farms={_fs.get('farms',0)} rpc={_fs.get('rpc_used',0)}")
                except Exception as _fe:
                    if not quiet:
                        print(f"[SCHED][FARM] error: {_fe}")
            elif not quiet:
                print("[SCHED][INTAKE] skipped (degraded)")
            next_intake = time.time() + INTAKE_INTERVAL
        # brief sleep with jitter (avoids tight spin + thundering herd)
        time.sleep(min(5.0, 1.0 + random.random()))
    print("[SCHED] stopped cleanly.")


# ── status ──────────────────────────────────────────────────────────────────
def print_status():
    conn = db_connect(OPS_DB_PATH, timeout=30)
    ensure_run_log(conn)
    print("================= OPERATION SCHEDULER STATUS =================")
    # lock
    if os.path.exists(LOCK_FILE):
        try:
            owner = int(open(LOCK_FILE).read().strip() or "0")
        except (ValueError, OSError):
            owner = 0
        alive = owner and _pid_alive(owner)
        print(f"  lock: {'HELD by pid '+str(owner)+(' (alive)' if alive else ' (STALE)') if owner else 'present (unreadable)'}")
    else:
        print("  lock: free (no scheduler running)")

    def _last(job, status=None):
        q = "SELECT started_at, status, rpc_used, runtime_sec FROM wt_ops_v2_runs WHERE job_type=?"
        p = [job]
        if status:
            q += " AND status=?"; p.append(status)
        q += " ORDER BY started_at DESC LIMIT 1"
        return conn.execute(q, p).fetchone()

    for job in ("INTAKE", "FORWARD_MONITOR"):
        last = _last(job)
        ok = _last(job, "OK")
        err = _last(job, "ERROR")
        def _fmt(r):
            return (f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(r[0]))} "
                    f"[{r[1]}] rpc={r[2]} {r[3]}s") if r else "—"
        print(f"  {job}:")
        print(f"     last run    : {_fmt(last)}")
        print(f"     last success: {_fmt(ok)}")
        print(f"     last failure: {_fmt(err)}")

    ops = conn.execute("SELECT COUNT(*) FROM wt_ops_v2").fetchone()[0]
    fams = conn.execute("SELECT COUNT(*) FROM wt_ops_v2_families").fetchone()[0]
    try:
        pend = conn.execute("SELECT COUNT(*) FROM wt_operation_candidates WHERE status='PENDING'").fetchone()[0]
    except Exception:
        pend = 0
    print(f"  operations: {ops}   families: {fams}   pending candidates: {pend}")
    print(f"  rpc used today: {_rpc_today()}" + (f" / {DAILY_MAX_RPC}" if DAILY_MAX_RPC else ""))
    conn.close()


def main():
    import argparse
    global LOCK_FILE
    ap = argparse.ArgumentParser(description="Phase 1.3 — operation scheduler / continuous watching")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--once-intake", action="store_true")
    ap.add_argument("--once-forward", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--lock-file", type=str, default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    if args.lock_file:
        LOCK_FILE = args.lock_file

    # make sure the v2 schemas exist before anything runs
    _c = db_connect(OPS_DB_PATH, timeout=30)
    _ensure_store_schema(_c); _ensure_fwd_schema(_c); ensure_run_log(_c); _c.close()

    if args.status:
        print_status(); return
    if args.once_intake:
        r = run_intake_job(dry_run=args.dry_run, quiet=args.quiet)
        print(f"[once-intake] {r['status']}"); return
    if args.once_forward:
        r = run_forward_job(quiet=args.quiet)
        print(f"[once-forward] {r['status']}"); return
    if args.loop:
        if not acquire_lock(LOCK_FILE):
            sys.exit(1)
        try:
            loop(quiet=args.quiet)
        finally:
            release_lock(LOCK_FILE)
        return
    ap.print_help()


if __name__ == "__main__":
    main()
