"""
WATCHTOWER Critical Path Budget Policy
======================================

Core rule:
    fast path succeeds → process
    fast path uncertain → defer
    fast path slow → abort

Every real-time path must be covered by one of these buckets.
Constants here are the SINGLE source of truth — wired into each module
via import. Supervisord env-var overrides still work (each module reads
os.environ before calling into this module, or this module reads them).

Priority tiers
--------------
CRITICAL  — on the asyncio event loop, directly affects migration/birth/launch
NEAR_RT   — in a thread-pool worker spawned from the loop; stalls goroutine but
            not the socket reader
DEFERRED  — background task / offline worker; can be slow without system impact
"""

import os

# ── RPC timeouts (seconds) ────────────────────────────────────────────────────
# Applied to every aiohttp / urllib call on the CRITICAL path.
# sock_read is the per-chunk read timeout; without it a drip-feeding server
# can hold the connection open well past `total`.
CRITICAL_RPC_CONNECT_S  = int(os.environ.get("BUDGET_RPC_CONNECT_S",  "3"))
CRITICAL_RPC_READ_S     = int(os.environ.get("BUDGET_RPC_READ_S",     "5"))
CRITICAL_RPC_TOTAL_S    = int(os.environ.get("BUDGET_RPC_TOTAL_S",    "6"))

# Hard outer guard wrapping an entire path (e.g. asyncio.wait_for / asyncio.timeout).
# If the path hasn't resolved by this point, abort and defer.
CRITICAL_OUTER_TIMEOUT_S = int(os.environ.get("BUDGET_OUTER_TIMEOUT_S", "30"))

# Same for near-real-time (thread-pool) paths — looser, but still bounded.
NEARRT_RPC_CONNECT_S    = int(os.environ.get("BUDGET_NEARRT_CONNECT_S",  "5"))
NEARRT_RPC_READ_S       = int(os.environ.get("BUDGET_NEARRT_READ_S",    "10"))
NEARRT_RPC_TOTAL_S      = int(os.environ.get("BUDGET_NEARRT_TOTAL_S",   "12"))

# urllib sync timeout (used in ws_cascade _rpc, already off-loop via run_in_executor)
SYNC_RPC_TIMEOUT_S      = int(os.environ.get("BUDGET_SYNC_RPC_TIMEOUT_S", "10"))

# ── Public RPC policy ─────────────────────────────────────────────────────────
# api.mainnet-beta.solana.com connects fast but drip-feeds responses → stalls.
# Permitted only on DEFERRED paths (backfill, reconciler, offline worker).
PUBLIC_RPC_URL = "https://api.mainnet-beta.solana.com"
ALLOWED_ON_DEFERRED_ONLY: tuple = (PUBLIC_RPC_URL,)

# ── Pool / migration discovery ─────────────────────────────────────────────────
POOL_DISCOVERY_TOTAL_S  = int(os.environ.get("BUDGET_POOL_DISCOVERY_S",   "30"))
POOL_VALIDATE_TIMEOUT_S = int(os.environ.get("BUDGET_POOL_VALIDATE_S",     "6"))
# Outer hard limit on the full migration handler (includes pool discovery + all DB)
MIGRATION_OUTER_TIMEOUT_S = int(os.environ.get("BUDGET_MIGRATION_OUTER_S", "45"))

# ── Program CREATE watcher ─────────────────────────────────────────────────────
CREATE_FETCH_CONCURRENCY = int(os.environ.get("WS_CREATE_FETCH_CONCURRENCY", "4"))
CREATE_FETCH_TIMEOUT_S   = int(os.environ.get("WS_CREATE_FETCH_TIMEOUT_S",   "4"))
CREATE_FETCH_QUEUE_MAX   = int(os.environ.get("WS_CREATE_FETCH_MAX_QUEUE",  "20"))

# ── Creator resolution ────────────────────────────────────────────────────────
# Fast path: used in listener/webhook context. Abort and mark budget_exceeded, not failed.
CREATOR_RESOLUTION_FAST_S   = int(os.environ.get("CREATOR_RESOLUTION_MAX_RUNTIME_SECS", "15"))
CREATOR_RESOLUTION_FAST_PAGES = int(os.environ.get("CREATOR_RESOLUTION_MAX_PAGES",        "1"))
# Deep path: standalone worker only. Still bounded.
CREATOR_RESOLUTION_DEEP_S   = int(os.environ.get("CREATOR_RESOLUTION_HIGH_PRIORITY_MAX_RUNTIME_SECS", "120"))
CREATOR_RESOLUTION_DEEP_PAGES = int(os.environ.get("CREATOR_RESOLUTION_HIGH_PRIORITY_MAX_PAGES", "5000"))

# ── DB write discipline ───────────────────────────────────────────────────────
# Maximum inline writes (INSERT/UPDATE) on the hot migration/birth path.
# Anything above this must go to a background queue.
MAX_DB_INLINE_WRITES_PER_EVENT = int(os.environ.get("BUDGET_MAX_INLINE_WRITES", "1"))

# DB connection / busy-timeout for critical-path connections (milliseconds).
# Must not spin indefinitely waiting for a WAL lock on the hot path.
CRITICAL_DB_BUSY_TIMEOUT_MS = int(os.environ.get("BUDGET_DB_BUSY_MS", "3000"))
NEARRT_DB_BUSY_TIMEOUT_MS   = int(os.environ.get("BUDGET_NEARRT_DB_BUSY_MS", "10000"))

# ── WS subscription budget ────────────────────────────────────────────────────
# Bounds on per-wallet logsSubscribe (candidate watches).
# If above these thresholds, prefer deferring over adding subscriptions.
MAX_ACTIVE_SUBPROV_SESSIONS = int(os.environ.get("WS_MAX_ACTIVE_SUBPROVS",   "10"))
MAX_CANDIDATE_WATCHES       = int(os.environ.get("WS_MAX_CANDIDATES",        "500"))
MAX_PROMOTED_SUBPROVS       = int(os.environ.get("WS_MAX_PROMOTED_SUBPROVS",  "40"))

# ── Degrade / skip policy labels (used in log lines and DB status columns) ───
DEGRADE_ABORT   = "abort"        # fast-fail, do not retry on this path
DEGRADE_DEFER   = "defer"        # enqueue for offline / background worker
DEGRADE_DROP    = "drop"         # discard silently (metrics only)
DEGRADE_SKIP    = "skip"         # record as skipped, reprocessable later
DEGRADE_RESTART = "restart"      # signal process-level restart

# ── Metric key names (for heartbeat meta_json + dashboard) ───────────────────
# Importable so every module emits the same key string.
METRIC_BUDGET_EXCEEDED      = "budget_exceeded_count"
METRIC_CRITICAL_RPC_TIMEOUT = "critical_rpc_timeout_count"
METRIC_DEFERRED_JOBS        = "deferred_jobs"
METRIC_DROPPED_CREATES      = "dropped_create_fetches"
METRIC_DB_INLINE_WRITES     = "db_inline_write_count"
METRIC_QUEUE_DEPTH          = "queue_depth"
METRIC_OLDEST_PENDING_AGE   = "oldest_pending_age_s"
METRIC_WORKER_RUNTIME_P95   = "worker_runtime_p95_ms"
METRIC_LOOP_LAG             = "loop_lag_s"
METRIC_DB_P99               = "db_p99_ms"

# ── Audit table (used by the dashboard and the budget-exceeded audit log) ─────
# fmt: off
BUDGET_TABLE = [
    # Path                           File                              Budget tier  Timeout-S    Degrade        Risk if exceeded
    ("pool_discovery",               "pumpfun_curve_listener.py",     "CRITICAL",  POOL_DISCOVERY_TOTAL_S,   DEGRADE_DEFER,   "stalled migration, backed-up birth queue"),
    ("pool_validate_getMultipleAcc", "pumpfun_curve_listener.py",     "CRITICAL",  POOL_VALIDATE_TIMEOUT_S,  DEGRADE_ABORT,   "event-loop stall (91s seen in production)"),
    ("migration_handler_outer",      "pumpfun_curve_listener.py",     "CRITICAL",  MIGRATION_OUTER_TIMEOUT_S,DEGRADE_DEFER,   "birth queue backup, DB lock cascade"),
    ("birth_parsing",                "pumpfun_curve_listener.py",     "CRITICAL",  CRITICAL_OUTER_TIMEOUT_S, DEGRADE_SKIP,    "missed birth signal"),
    ("pumpportal_reconnect",         "pumpfun_curve_listener.py",     "CRITICAL",  30,                       DEGRADE_RESTART, "complete birth blindness"),
    ("pumpswap_reconnect",           "pumpfun_curve_listener.py",     "CRITICAL",  30,                       DEGRADE_RESTART, "migration blindness"),
    ("program_create_fetch",         "ws_cascade.py",                 "CRITICAL",  CREATE_FETCH_TIMEOUT_S,   DEGRADE_DROP,    "CREATE missed, metrics++"),
    ("candidate_persistence",        "ws_cascade.py",                 "NEAR_RT",   NEARRT_RPC_TOTAL_S,       DEGRADE_DEFER,   "subprov watch gap"),
    ("treasury_tx_handler",          "ws_cascade.py",                 "NEAR_RT",   SYNC_RPC_TIMEOUT_S,       DEGRADE_ABORT,   "subprov session not opened"),
    ("subprov_catchup_sigs",         "ws_cascade.py",                 "NEAR_RT",   SYNC_RPC_TIMEOUT_S,       DEGRADE_SKIP,    "missed pre-subscription CREATE"),
    ("cascade_reconcile",            "ws_cascade.py",                 "DEFERRED",  60,                       DEGRADE_SKIP,    "session gap (acceptable)"),
    ("creator_resolution_fast",      "creator_resolution_worker.py",  "NEAR_RT",   CREATOR_RESOLUTION_FAST_S,DEGRADE_SKIP,    "creator unresolved, retry-queued"),
    ("creator_resolution_deep",      "creator_resolution_worker.py",  "DEFERRED",  CREATOR_RESOLUTION_DEEP_S,DEGRADE_SKIP,    "budget_exceeded status, reprocessable"),
    ("creator_funding",              "pumpfun_curve_listener.py",     "NEAR_RT",   30,                       DEGRADE_DEFER,   "funding chain unrecorded, retry ok"),
    ("price_worker_peak_update",     "price_worker.py",               "DEFERRED",  30,                       DEGRADE_SKIP,    "stale peak, not critical"),
    ("telemetry_write",              "pumpfun_curve_listener.py",     "DEFERRED",  0,                        DEGRADE_DROP,    "missing telemetry row (acceptable)"),
    ("symbol_fetch",                 "pumpfun_curve_listener.py",     "DEFERRED",  0,                        DEGRADE_DROP,    "missing symbol (cached on next read)"),
    ("prediction_scoring",           "token_prediction_builder.py",   "DEFERRED",  0,                        DEGRADE_SKIP,    "stale score, recomputed on next tick"),
    ("launch_audit_phase1",          "ws_cascade.py",                 "DEFERRED",  0,                        DEGRADE_DEFER,   "audit row delayed, not lost"),
]
# fmt: on


def budget_table_markdown() -> str:
    """Return the budget table as a markdown string for the dashboard or CLI."""
    header = (
        "| Path | File | Tier | Budget (s) | Degrade | Risk |\n"
        "|------|------|------|------------|---------|------|\n"
    )
    rows = "\n".join(
        f"| {p} | {f} | {t} | {b} | {d} | {r} |"
        for p, f, t, b, d, r in BUDGET_TABLE
    )
    return header + rows
