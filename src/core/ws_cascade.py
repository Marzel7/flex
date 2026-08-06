"""WATCHTOWER real-time WebSocket cascade — standalone daemon.

    python -m src.core.ws_cascade --loop      # continuous (supervised)
    python -m src.core.ws_cascade --once       # single tick (debug)

The creator CANNOT be identified pre-launch (proven on-chain: same-instant wrap-close
siblings are byte-for-byte identical and all carry the …039280 tail). So this service does
NOT predict the creator. Instead:

  confirmed TREASURY → SUB_PROV funding  (webhook writes wt_active_subprov_sessions)
      → WS-watch the SUB_PROV
      → on each wrap-close, extract EVERY closeAccount.destination → WS-watch each candidate
      → the candidate that emits a Pump.fun CREATE is the creator → attribute + record launch
      → tear down all watches (creator + siblings)

Single Helius WS connection; one logsSubscribe per wallet. The DB is the source of truth, so
a reconnect/restart rebuilds every subscription from wt_active_subprov_sessions +
wt_candidate_websocket_watches. Reconcile (operation_armed) stays a fallback only.
"""

from __future__ import annotations

import os
import sys
import json
import time
import signal
import asyncio
import threading
import traceback
import concurrent.futures
from collections import deque
from typing import Optional

try:
    import websockets
except Exception:                                    # pragma: no cover
    websockets = None

from src.utils.db_locking import db_connect
from src.utils.pubkey_validation import is_valid_pubkey, invalid_reason
from src.core import ws_cascade_store as store
from src.core.ws_cascade_store import OPS_DB_PATH, LIVE_DB_PATH, emit_event
from src.core.wrap_close_detector import extract_close_destinations, detect_seeded_account_close
from src.core import runtime_budget as _budget
from src.core import rpc_deadline

# ── config (env, conservative defaults) ──────────────────────────────────────
# TTLs sized from data: real subprovs stay actively provisioning for a MEDIAN of ~2h (p75 ~10h,
# p90 16h) — 16/19 multi-funding subprovs exceeded the old 10-min session TTL, so the subscription
# died mid-campaign and had to be re-opened on the next funding (losing the catch-up window). 2h
# covers the median; refresh_session() extends it on each new funding so an active subprov stays
# subscribed as long as it keeps provisioning. Candidate TTL stays short (a wrap-close→CREATE is
# seconds-to-minutes), but bumped 3→10min for the occasional STAGED launch.
SESSION_TTL_SEC        = int(os.environ.get("WS_SESSION_TTL_SEC", "1800"))         # 30m default
SESSION_TTL_HIGH_SOL   = int(os.environ.get("WS_SESSION_TTL_HIGH_SOL_SEC", "1800"))  # same as standard — avg fund→launch is ~12m
SESSION_HIGH_SOL_FLOOR = float(os.environ.get("WS_SESSION_HIGH_SOL_FLOOR", "100"))   # ≥100◎ triggers 6h TTL
CAPITAL_RELOAD_MIN_SOL = float(os.environ.get("WS_CAPITAL_RELOAD_MIN_SOL", "50"))    # threshold for CAPITAL_RELOAD event
CDC_MIN_SOL            = float(os.environ.get("WS_CDC_MIN_SOL", "50"))               # Capital Distributor Candidate threshold
CDC_INACTIVITY_TTL_SEC = int(os.environ.get("WS_CDC_INACTIVITY_TTL_SEC", "3600"))   # unsubscribe after 60min quiet
CANDIDATE_TTL_SEC      = int(os.environ.get("WS_CANDIDATE_TTL_SEC", "1800"))         # 30m (matches session TTL)
MAX_CANDIDATES    = int(os.environ.get("WS_MAX_CANDIDATES", "0"))        # 0 = no cap
MAX_ACTIVE_SUBPROVS = int(os.environ.get("WS_MAX_ACTIVE_SUBPROVS", "10"))
# PROMOTED SUBPROV TIER (Phase 1 subscription-promotion) — a STANDING watchlist of subprovs we
# already discovered (treasury_known + wrap-close producer), subscribed directly so their NEXT
# launch is caught in real time without waiting for a treasury-funded session. This is a SEPARATE
# pool from the session subprovs (live treasury-funded sessions) with its own budget. Measured
# baseline before this: 2.8% real-time detection (9/322); the 196 unwatched launch funders are
# the gap. Gated behind WS_PROMOTE_DISCOVERED so it can be toggled cleanly during measurement.
WS_PROMOTE_DISCOVERED   = os.environ.get("WS_PROMOTE_DISCOVERED", "0") == "1"
MAX_PROMOTED_SUBPROVS   = int(os.environ.get("WS_MAX_PROMOTED_SUBPROVS", "40"))
POLL_SEC          = float(os.environ.get("WS_POLL_SEC", "2"))
CLEANUP_SEC       = float(os.environ.get("WS_CLEANUP_SEC", "5"))
HEARTBEAT_SEC     = 30
WATCHDOG_STALE_SEC = int(os.environ.get("WS_WATCHDOG_STALE_SEC", "90"))  # alert if heartbeat this old
# catch-up scans the candidate's most-recent sigs immediately after subscribing. Live, the
# CREATE is the newest tx (just funded), so a small window suffices; bump if INSTANT creators
# do >1 action before catch-up runs.
CATCHUP_SIG_LIMIT = int(os.environ.get("WS_CATCHUP_SIG_LIMIT", "8"))
SUBPROV_DURABLE_CATCHUP_LIMIT = int(os.environ.get("WS_SUBPROV_DURABLE_CATCHUP_LIMIT", "50"))
SUBPROV_SIG_RETRY_LIMIT = int(os.environ.get("WS_SUBPROV_SIG_RETRY_LIMIT", "25"))
SUBPROV_SIG_MAX_ATTEMPTS = int(os.environ.get("WS_SUBPROV_SIG_MAX_ATTEMPTS", "8"))
# How often to sweep ACTIVE subprovs for wrap-closes whose WS notification dropped/stalled.
# This is the reliability backstop for the ~100s-miss case (a dropped subprov notification).
# RPC-bounded: one getSignatures per active subprov per sweep, deduped so it doesn't refetch.
SUBPROV_SWEEP_SEC = float(os.environ.get("WS_SUBPROV_SWEEP_SEC", "6"))
ACTIVE_CATCHUP0_WORKERS = int(os.environ.get("WS_ACTIVE_CATCHUP0_WORKERS", "4"))
# X24.2.1 Phase 3 — bounded sweep concurrency. Measured root cause: sequential
# execution of MAX_ACTIVE_SUBPROVS (10) catch_up_subprov() calls, each costing
# ~8-16s (dominated by up to SUBPROV_DURABLE_CATCHUP_LIMIT=50 sequential
# getTransaction calls per session, NOT executor contention — the default
# executor's queue depth was measured at 0 throughout). Bounding concurrency at
# 4 (capacity-calculated: inspections/min must exceed measured arrivals/min of
# ~19; 4 gives ~29/min, a real margin) keeps RPC concurrency explicit and
# bounded while cutting cycle time roughly in proportion.
SWEEP_CONCURRENCY = int(os.environ.get("WS_SWEEP_CONCURRENCY", "4"))
# X65.29 — bounded concurrency for the RPC-FETCH stage only, inside a single
# catch_up_subprov() call's per-signature loop. Independent of SWEEP_CONCURRENCY
# (which bounds concurrent SESSIONS/subprovs, not signatures within one
# session). Root-cause audit (X65.28) found the per-signature loop was fully
# serial even within one session, and that the dominant cost per signature is
# the getTransaction RPC round-trip (~3200-3488ms observed live) rather than
# the durable DB-write/classification work that follows it (tens of ms) — so
# only the RPC fetch is parallelized here; every stateful read/write
# (_handle_subprov_tx's session lookup, PRE_CREATE/POST_CREATE phase
# detection, promote_to_subprov, candidate-watch persistence) still runs
# strictly serially, in the existing chronological order, via
# _process_subprov_sig_durable(prefetched_tx=...). Default of 4 matches
# SWEEP_CONCURRENCY's own conservative default; do not raise above 4 without
# re-measuring RPC latency/timeout-rate at each step (see X65.28/X65.29
# rollout guardrails).
SUBPROV_SIGNATURE_CONCURRENCY = int(os.environ.get("WS_SUBPROV_SIGNATURE_CONCURRENCY", "4"))
# X65.31 — dedicated executor for the X65.29 prefetch stage, isolated from
# Python's shared asyncio default executor (max_workers=12, shared by
# durable processing, CDC handlers, websocket work, and every other
# _ato_thread()/_arpc() call in the process). Root-cause audit (X65.30)
# measured a representative slow batch: rpc_fetch_ms=337,488ms of which
# only ~15,864ms (4.7%) was actual logged RPC round-trip time; the
# remaining ~321,624ms (95.3%) was executor ADMISSION delay -- signatures
# whose _ato_thread(...) submission sat queued behind unrelated work on the
# shared pool (measured live: active_threads=12/12, fully saturated, across
# every sample taken). Isolating prefetch submission onto its own pool
# removes that specific contention without touching the shared pool's other
# consumers at all.
#
# NOT reused: RpcDeadlineGuard's own executor (_get_tx_guard()). Audited
# (X65.31) and rejected: _get_subprov_tx_fast_retry() already calls
# _get_tx_with_outcome() -> RpcDeadlineGuard.call_with_deadline(), which
# submits the ACTUAL getTransaction call onto the guard's own dedicated,
# capacity-bounded executor (max_capacity, circuit breaker, late-result
# cache) and then blocks the CALLING thread on fut.result(timeout=...)
# until that resolves. Today's prefetch code (_ato_thread) therefore
# already pays a double-indirection cost: a shared-pool thread sits idle,
# purely blocked waiting on the guard's own separate executor. Reusing the
# guard's executor DIRECTLY for prefetch submission would make prefetch
# calls compete for the SAME max_capacity slots as every other _get_tx
# caller in the process (durable processing included), changing the
# guard's admission/capacity/circuit-breaker semantics for callers that
# have nothing to do with this prefetch stage -- exactly the outcome this
# task's constraints prohibit. A dedicated pool avoids that: it only ever
# holds prefetch-submission threads blocked waiting on the (unchanged)
# guard, never competing with anything else for guard capacity.
SUBPROV_PREFETCH_EXECUTOR_WORKERS = int(os.environ.get("WS_SUBPROV_PREFETCH_EXECUTOR_WORKERS", "4"))
_subprov_prefetch_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=SUBPROV_PREFETCH_EXECUTOR_WORKERS,
    thread_name_prefix="ws-subprov-prefetch",
)
SUBPROV_FAST_RETRY_OFFSETS = tuple(
    float(x.strip()) for x in os.environ.get("WS_SUBPROV_FAST_RETRY_OFFSETS", "0,0.25,0.75,1.5").split(",")
    if x.strip()
)

# X24.7 — pluggable signature-processing-order policy for catch_up_subprov()'s
# per-signature loop. Evidence (X24.6/X24.7 full-population replay, n=39 — the
# complete reconstructable confirmed-launch history, not a sample of a larger
# set): the eventual creator is disproportionately the NEWEST wrap-close event
# in a subprov's session (median reverse-position 4; 69.2% within the last 10
# events; statistically significant vs a same-session random-recipient control,
# z=2.71). Pure newest-first improves median inspections-to-creator (25->4) but
# worsens the tail (P95 139->207); ALTERNATING newest/oldest improves BOTH
# median (25->7) and tail (P95 139->78) against the current oldest-first order,
# while remaining a strict permutation of the same signature set — every
# signature is still processed exactly once per cycle, never fewer.
#
# Caveat, kept honest rather than presented as a uniform win: the improvement
# is NOT uniform across the historical population. 2 of 5 treasuries, and the
# single earliest observed campaign week (both likely overlapping the same
# "instant-launch" sub-population), show alternating performing WORSE than the
# current order. This is a measured, real trade-off, not a clean universal
# improvement — hence a pluggable policy (swappable, not a silent hard
# replacement) rather than deleting the old order outright.
SUBPROV_SIG_ORDER_POLICY = os.environ.get("WS_SUBPROV_SIG_ORDER_POLICY", "ALTERNATING").upper()


def _order_signature_indices(n: int, policy: str = None) -> list[int]:
    """Returns a permutation of range(n) — indices into a newest-first list
    (index 0 = newest signature) — in the order signatures should be
    processed under `policy`. Always a strict permutation: len(output) == n,
    every index 0..n-1 appears exactly once, regardless of policy. This is
    what guarantees X24.7's "prioritisation only, never a filter" requirement
    structurally, not just by convention.

    Policies:
      FIFO / OLDEST_FIRST — oldest signature first (index n-1, ..., 0).
                            This is the pre-X24.7 default behaviour.
      NEWEST_FIRST        — newest signature first (index 0, ..., n-1).
                            Best median in replay, worst tail — not the
                            recommended default; available for comparison/
                            experimentation via the env var.
      ALTERNATING         — newest, oldest, 2nd-newest, 2nd-oldest, ... .
                            The X24.7-recommended default: best median AND
                            tail against the pre-X24.7 order in the full
                            replay.
    """
    policy = (policy or SUBPROV_SIG_ORDER_POLICY).upper()
    if n <= 0:
        return []
    if policy in ("FIFO", "OLDEST_FIRST"):
        return list(reversed(range(n)))
    if policy == "NEWEST_FIRST":
        return list(range(n))
    if policy == "ALTERNATING":
        order: list[int] = []
        lo, hi = 0, n - 1
        take_newest = True
        while lo <= hi:
            if take_newest:
                order.append(lo); lo += 1
            else:
                order.append(hi); hi -= 1
            take_newest = not take_newest
        return order
    # Unrecognised policy value: fail safe to the pre-X24.7 default rather
    # than silently doing something unvalidated.
    _log(f"⚠ unrecognised WS_SUBPROV_SIG_ORDER_POLICY={policy!r}, falling back to FIFO")
    return list(reversed(range(n)))

# Treasury WS tier: permanently logsSubscribe the (small, stable) confirmed-treasury set so a
# provisioning outbound opens a SUB_PROV session in real-time (~3s) instead of waiting on the
# enhanced webhook (5–390s + ngrok jitter). WS-first; the webhook path remains as a fallback
# (start_session is idempotent on (subprov, funding_sig), so whichever fires first wins).
# Floor for what counts as a launch-PROVISIONING outbound (→ open a SUB_PROV session).
# SOURCE-AWARE: the WS tier only subscribes to CONFIRMED treasuries, so the sender is already
# authoritative — a known treasury seeding ANY provisioning-sized amount is signal regardless of
# size. Real subprovs can start small: GnaMKX's FIRST seed from 5JWii73 was 1◎ before later 100s◎
# top-ups, and 2/22 confirmed-fan-out subprovs were funded ENTIRELY below 50◎. So from a known
# treasury we use a LOW floor (1◎); the 50◎ figure (below) is kept only as the documented
# noise-vs-provision boundary for any future UNATTRIBUTED-source path.
#   Bimodal evidence (154 historical sessions): sub-13◎ cluster = peer-treasury top-ups + dust
#   (mesh noise), 60–990◎ cluster = real provisions. 50◎ sits in the empty 13–60◎ gap.
TREASURY_PROVISION_MIN_SOL       = float(os.environ.get("WS_TREASURY_MIN_SOL", "0.05")) # known-treasury floor (sub-1◎ provisions confirmed in data: 0.132–0.780◎)
TREASURY_PROVISION_NOISE_SOL     = float(os.environ.get("WS_TREASURY_NOISE_SOL", "50")) # unattributed-source floor (ref)
TREASURY_PROVISION_MAX_SOL = float(os.environ.get("WS_TREASURY_MAX_SOL", "999999"))
TREASURY_REFRESH_SEC       = float(os.environ.get("WS_TREASURY_REFRESH_SEC", "60"))

# ── HOT subprov subscription priority ────────────────────────────────────────
# A HOT_SUBPROV is a wallet just funded by a confirmed treasury — it has a
# 119s median window before wrap-close. We cannot afford to wait 44–60 pending
# subscription confirmations. Two-pronged response:
#   1. Priority kind "hot_subprov" in pending_req — stale timeout 2s vs 90s cold
#   2. Immediate RPC burst fallback: poll getSignaturesForAddress at 0/1/2/4/8/15/30/60s
# The RPC burst runs regardless of subscription confirmation state.
HOT_SUB_STALE_SEC   = float(os.environ.get("WS_HOT_SUB_STALE_SEC",  "2"))   # retry HOT if unconfirmed this long
# X27.7 — measured live sub_avg_ack_ms=11660 / sub_p0_avg_ack_ms=32829 (Helius ack
# latency drifted well past the old 10s default), so COLD pending subs were being
# dropped by sweep_stale_pending() before Helius ever acked them — the confirmed
# root cause of primary_live_path=0 for 30 days. Raised past observed p95 with headroom.
COLD_SUB_STALE_SEC  = float(os.environ.get("WS_COLD_SUB_STALE_SEC", "45"))  # drop COLD pending after this
COLD_SUB_RETRY_MAX  = int(os.environ.get("WS_COLD_SUB_RETRY_MAX", "3"))     # resubscribe attempts before giving up
HOT_BURST_SCHEDULE  = [0, 1, 2, 4, 8, 15, 30, 60]                           # RPC poll offsets (seconds)
# Watchdog thresholds
PENDING_WARN_1  = int(os.environ.get("WS_PENDING_WARN_1",  "10"))
PENDING_WARN_2  = int(os.environ.get("WS_PENDING_WARN_2",  "25"))

# ── Subscription priority levels ──────────────────────────────────────────────
# P0: new LIVE_ARMED subprov (must subscribe before fan-out, never waits in queue)
# P1: confirmed treasuries (permanent, small set)
# P2: previously active LIVE_ARMED sessions (reconnect replay)
# P3: everything else (INTEL_ONLY, promoted discovered, candidates)
# INTEL_ONLY sessions are never subscribed.
SUB_PRIORITY_LIVE_ARMED  = 0   # new session, just funded
SUB_PRIORITY_TREASURY    = 1   # confirmed treasury
SUB_PRIORITY_SESSION     = 2   # reconnect replay of existing sessions
SUB_PRIORITY_OTHER       = 3   # promoted/candidates

# Rate-limit reconnect replay: max accountSubscribe sends per second during resync.
# At 10/s, 52 subscriptions take 5.2s — well within Helius keepalive.
# P0 (new LIVE_ARMED) bypasses this rate limit entirely.
RECONNECT_SUBSCRIBE_RATE = float(os.environ.get("WS_RECONNECT_SUBSCRIBE_RATE", "10"))

# ── Classification enforcement ────────────────────────────────────────────────
# ENFORCE=0: classify + log only (Pass 1 audit).
# ENFORCE=1: BUY_SWARM_PROVISIONER sessions are rejected at enrollment; live sessions
#            that trip the burst threshold are closed and their candidates expired.
CLASSIFICATION_ENFORCE = os.environ.get("WS_SUBPROV_CLASSIFICATION_ENFORCE", "0") == "1"

# ── Candidate / ARMED flags ───────────────────────────────────────────────────
# SAVE_CANDIDATE_FANOUT  — persist fan-out destinations to DB + add to ProgramWatcher.
#                          Should always be 1 in production.
# CANDIDATE_WALLET_WS_ENABLED — open a per-candidate logsSubscribe. Always 0: the
#                          correct model is one pump.fun program stream (ProgramWatcher),
#                          not N per-wallet streams.
# CANDIDATE_WATCH_ENABLED kept as alias for SAVE_CANDIDATE_FANOUT (backward compat).
# ARMED state can be overridden by a file so the toggle works without restarting supervisord.
# The file contains "1" or "0"; absent = fall back to env var.
_ARMED_STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "database", "armed_mode.txt")
def _read_armed_file():
    try:
        return open(_ARMED_STATE_FILE).read().strip()
    except Exception:
        return None
_armed_file_val = _read_armed_file()
SAVE_CANDIDATE_FANOUT       = (_armed_file_val == "1") if _armed_file_val is not None \
                              else os.environ.get("WS_SAVE_CANDIDATE_FANOUT", "1") == "1"
PROGRAM_WATCHER_ENABLED_FILE = (_armed_file_val == "1") if _armed_file_val is not None else None
CANDIDATE_WALLET_WS_ENABLED = os.environ.get("WS_CANDIDATE_WALLET_WS_ENABLED", "0") == "1"
CANDIDATE_WATCH_ENABLED     = SAVE_CANDIDATE_FANOUT   # alias used in older code paths
SUBPROV_WATCH_ENABLED       = os.environ.get("WS_SUBPROV_WATCH_ENABLED", "1") == "1"

# Live burst-detection thresholds (applied per subprov, real-time in _handle_subprov_tx)
BURST_RECIPIENT_FLOOR   = int(os.environ.get("WS_BURST_RECIPIENT_FLOOR", "20"))   # min recipients in burst window
BURST_WINDOW_SEC        = float(os.environ.get("WS_BURST_WINDOW_SEC", "300"))     # 5min burst window
BURST_MEDIAN_SOL_LO     = float(os.environ.get("WS_BURST_MEDIAN_SOL_LO", "0.03"))
BURST_MEDIAN_SOL_HI     = float(os.environ.get("WS_BURST_MEDIAN_SOL_HI", "0.20"))
PENDING_CRITICAL = int(os.environ.get("WS_PENDING_CRITICAL", "40"))

# ── Behaviour-first subprov gating (Pass F) ──────────────────────────────────
# ENFORCE=0 (audit mode): NEW_SUBPROV still calls start_session as before, but also
#   writes a TEMP row and logs what WOULD have been skipped. No behaviour change.
# ENFORCE=1 (live): NEW_SUBPROV writes TEMP row only — NO start_session, NO WS sub.
#   Sub-subprov plain-xfer recursion also blocked for unconfirmed parent.
#   Offline reconciler sweeps TEMP candidates for wrap-close evidence and promotes hits.
TEMP_SUBPROV_ENFORCE = os.environ.get("WS_TEMP_SUBPROV_ENFORCE", "0") == "1"
# TTL for TEMP candidates before they expire unconfirmed (seconds). Default 30min.
TEMP_SUBPROV_TTL_SEC = int(os.environ.get("WS_TEMP_SUBPROV_TTL_SEC", "1800"))
# Offline reconciler: max RPC calls per sweep tick. Keep tight.
TEMP_SWEEP_RPC_BUDGET = int(os.environ.get("WS_TEMP_SWEEP_RPC_BUDGET", "15"))
# Seconds between offline sweep ticks.
TEMP_SWEEP_INTERVAL_SEC = float(os.environ.get("WS_TEMP_SWEEP_INTERVAL_SEC", "300"))

PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

# ── Plain-transfer subprov enrolment ─────────────────────────────────────────
# Confirmed treasuries sometimes capitalise subprovs via a plain SOL transfer
# (system:transfer) rather than via a wrap-close. 8/8 observed recipients became
# active subprovs within 46s median. Transfers ≥ this threshold from a confirmed
# treasury are treated as a subprov enrolment signal, exactly like a wrap-close-
# funded subprov, using the existing session + subscription path.
PLAIN_TRANSFER_MIN_SOL = float(os.environ.get("WS_PLAIN_TRANSFER_MIN_SOL", "50"))

_SYSTEM_PROGRAM = "11111111111111111111111111111111"
_TOKEN_PROGRAM  = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
_ATA_PROGRAM    = "ATokenGPvbdGVxr1b2uviiaZnivfpAtrQwkFQ4HTuufP"
_WSOL_MINT      = "So11111111111111111111111111111111111111112"


def _is_plain_transfer(tx: dict, sender: str, recipient: str, amount_sol: float) -> bool:
    """Return True iff this tx looks like a plain SOL transfer from sender → recipient.

    Required conditions (all must hold):
      1. sender lost SOL (checked by caller via preBalances/postBalances delta)
      2. recipient gained a single large SOL amount (≥ PLAIN_TRANSFER_MIN_SOL, checked by caller)
      3. at least one System Program 'transfer' or 'transferWithSeed' instruction targets recipient
      4. no WSOL wrap-close destinations (no closeAccount on WSOL ATA → recipient)
      5. no SEEDED_ACCOUNT_CLOSE pattern
      6. no Token Program instructions (rules out ATA-close / token transfers)
    """
    from src.core.wrap_close_detector import extract_close_destinations, detect_seeded_account_close
    if amount_sol < PLAIN_TRANSFER_MIN_SOL:
        return False
    if extract_close_destinations(tx):
        return False
    if detect_seeded_account_close(tx):
        return False
    msg = tx.get("transaction", {}).get("message", {}) or {}
    instructions = msg.get("instructions") or []
    has_system_transfer = False
    for ix in instructions:
        prog = ix.get("programId", "")
        parsed = ix.get("parsed") or {}
        ix_type = parsed.get("type", "") if isinstance(parsed, dict) else ""
        info = parsed.get("info", {}) if isinstance(parsed, dict) else {}
        if prog == _TOKEN_PROGRAM or prog == _ATA_PROGRAM:
            return False   # any token instruction = not a plain SOL transfer
        if prog == _SYSTEM_PROGRAM and ix_type in ("transfer", "transferWithSeed"):
            dest = info.get("destination") or info.get("newAccount") or ""
            if dest == recipient:
                has_system_transfer = True
    return has_system_transfer


# ── Known infrastructure wallets — never session these ───────────────────────
# High-volume AMM pools, system programs, and other non-subprov wallets that
# can receive SOL from treasuries (e.g. via swap fees / liquidity) but are
# categorically not WATCHTOWER sub-provisioners.
_SUBPROV_BLOCKLIST: frozenset = frozenset({
    "Ayj7LBHBDJFDEEb3NJn8isUwjtSjxzonDQW7gsAW52jA",  # Pump.fun AMM (Hayes-WSOL) Pool 2
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",   # PumpSwap AMM pool authority
    "39azUYFW61WEBz9cPousBkosYxjZazaF29Y6un2Kp5bL",  # PumpFun migration authority
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",   # PumpFun program
    "11111111111111111111111111111111",                 # System program
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",   # Token program
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJe1bRS",  # Associated token program
})

# ── Wallet profile cache ──────────────────────────────────────────────────────
# Refresh interval for the in-process wallet profile dict. Short enough to pick up
# newly confirmed treasuries/subprovs; long enough to avoid WAL pin risk.
WALLET_PROFILE_REFRESH_SEC = float(os.environ.get("WS_WALLET_PROFILE_REFRESH_SEC", "900"))  # 15 min

# ── program-CREATE watcher (Phase 1: shadow mode, gated) ─────────────────────
PROGRAM_WATCHER_ENABLED  = PROGRAM_WATCHER_ENABLED_FILE if PROGRAM_WATCHER_ENABLED_FILE is not None \
                          else os.environ.get("WS_PROGRAM_CREATE_WATCHER_ENABLED", "1") == "1"
CREATE_FETCH_CONCURRENCY = int(os.environ.get("WS_CREATE_FETCH_CONCURRENCY", "4"))
CREATE_FETCH_TIMEOUT_S   = int(os.environ.get("WS_CREATE_FETCH_TIMEOUT_S", "4"))
CREATE_FETCH_MAX_QUEUE   = int(os.environ.get("WS_CREATE_FETCH_MAX_QUEUE", "20"))
CREATE_REPLAY_TTL_SEC    = int(os.environ.get("WS_CREATE_REPLAY_TTL_SEC", "60"))
CREATE_REPLAY_MAX        = int(os.environ.get("WS_CREATE_REPLAY_MAX", "500"))
PROGRAM_DRAIN_GRACE_S    = int(os.environ.get("WS_PROGRAM_DRAIN_GRACE_S", "90"))


def _confirmed_treasuries(conn) -> set:
    """The authoritative confirmed-treasury set (wt_confirmed_treasuries, ops DB) — the wallets
    we WS-subscribe permanently. Small + stable (≈12)."""
    try:
        return {r[0] for r in conn.execute("SELECT treasury FROM wt_confirmed_treasuries").fetchall()}
    except Exception:
        return set()


def _no_subscribe_treasuries(conn) -> set:
    """Treasuries flagged no_subscribe=1 — their subprovs are recorded INTEL_ONLY, never websocketed."""
    try:
        return {r[0] for r in conn.execute(
            "SELECT treasury FROM wt_confirmed_treasuries WHERE no_subscribe=1").fetchall()}
    except Exception:
        return set()


def _treasury_no_subscribe(conn, treasury: Optional[str]) -> bool:
    """Authoritative per-session no-subscribe check; do not rely on stale process cache."""
    if not treasury:
        return False
    try:
        return conn.execute(
            "SELECT 1 FROM wt_confirmed_treasuries "
            "WHERE treasury=? AND COALESCE(no_subscribe,0)=1 LIMIT 1",
            (treasury,),
        ).fetchone() is not None
    except Exception:
        return False


def _resolve_linked_mint(conn, subprov: str) -> Optional[str]:
    """Return the most recent mint this subprov launched, if known. Zero RPC — DB only.
    Checks wt_watchtower_launches first (confirmed WATCHTOWER launches), then falls back
    to wt_candidate_websocket_watches for any resolved candidate."""
    try:
        row = conn.execute(
            "SELECT mint FROM wt_watchtower_launches "
            "WHERE subprov_wallet=? ORDER BY create_time DESC LIMIT 1", (subprov,)
        ).fetchone()
        if row:
            return row[0]
    except Exception:
        pass
    return None


def _promotable_subprovs(conn) -> list:
    """Phase 1 subscription-promotion set: discovered subprovs we should put on a STANDING
    watchlist so their NEXT launch is caught in real time (not only after a treasury-funded
    session opens). EXACTLY scoped — treasury is known AND the wallet is a confirmed wrap-close
    producer (the mechanism guardrail; never a raw mid-chain/collector). Bounded + newest-first."""
    try:
        return [r[0] for r in conn.execute(
            "SELECT s.subprov FROM wt_discovered_subprovs s "
            "WHERE s.treasury_known=1 "
            "  AND s.subprov IN (SELECT subprov_wallet FROM wt_wrap_close_candidates) "
            "ORDER BY s.last_seen DESC "
            "LIMIT ?", (MAX_PROMOTED_SUBPROVS,)).fetchall()]
    except Exception:
        return []


def _build_wallet_profile() -> dict:
    """Build the in-memory wallet identity dict from a pair of short read-only snapshots.

    Priority (highest wins, lower roles never overwrite higher):
        TREASURY > BUY_SWARM > SUBPROV > CREATOR > HISTORICAL

    Opens and CLOSES both DB connections before building the dict so no read
    transaction is held during the (cheap) Python construction pass.
    """
    t0 = time.time()
    try:
        ops_conn = db_connect(OPS_DB_PATH, timeout=2)
        try:
            raw_treasuries  = ops_conn.execute("SELECT treasury FROM wt_confirmed_treasuries").fetchall()
            raw_buy_swarm   = ops_conn.execute(
                "SELECT subprov FROM wt_discovered_subprovs WHERE subprov_type='BUY_SWARM_PROVISIONER'"
            ).fetchall()
            raw_subprovs    = ops_conn.execute(
                "SELECT subprov FROM wt_discovered_subprovs WHERE wrap_close_count > 0"
            ).fetchall()
            raw_non_prov    = ops_conn.execute(
                "SELECT subprov FROM wt_discovered_subprovs WHERE subprov_type='NON_PROVISIONING_RECIPIENT'"
            ).fetchall()
            raw_hist_sess   = ops_conn.execute(
                "SELECT DISTINCT subprov_wallet FROM wt_active_subprov_sessions "
                "WHERE state IN ('EXPIRED','COMPLETED')"
            ).fetchall()
            raw_hist_cands  = ops_conn.execute(
                "SELECT DISTINCT subprov_wallet FROM wt_candidate_websocket_watches"
            ).fetchall()
        finally:
            ops_conn.close()

        hot_conn = db_connect(LIVE_DB_PATH, timeout=2)
        try:
            raw_creators = hot_conn.execute(
                "SELECT DISTINCT pf_ws_creator FROM token_analysis WHERE pf_ws_creator IS NOT NULL"
            ).fetchall()
        finally:
            hot_conn.close()

    except Exception as e:
        _log(f"⚠ wallet_profile build failed (keeping old): {e}")
        return {}

    # Build dict after both connections are closed — priority order low→high
    # NON_PROVISIONING_RECIPIENT is lowest (overridden by any confirmed identity)
    profile: dict[str, str] = {}
    for (w,) in raw_non_prov:
        if w: profile[w] = "NON_PROVISIONING"
    for (w,) in raw_hist_sess:
        if w: profile[w] = "HISTORICAL"
    for (w,) in raw_hist_cands:
        if w: profile.setdefault(w, "HISTORICAL")
    for (w,) in raw_creators:
        if w and w not in profile: profile[w] = "CREATOR"
    for (w,) in raw_subprovs:
        if w: profile[w] = "SUBPROV"
    for (w,) in raw_buy_swarm:
        if w: profile[w] = "BUY_SWARM"
    for (w,) in raw_treasuries:
        if w: profile[w] = "TREASURY"

    build_ms = int((time.time() - t0) * 1000)
    _log(f"📋 wallet_profile built: {len(profile):,} wallets in {build_ms}ms "
         f"(T={len(raw_treasuries)} BS={len(raw_buy_swarm)} SP={len(raw_subprovs)} "
         f"CR={len(raw_creators):,} HI={len(raw_hist_sess)+len(raw_hist_cands)} "
         f"NPR={len(raw_non_prov)})")
    return profile


# pump.fun instruction discriminators (first 8 bytes of the instruction data). The CREATE ix
# carries the mint at accounts[0] — verified stable across fixtures (xmaxxing + Donald80):
#   CREATE = d6904cec5f8b31b4  (16 accounts: [0]=mint [2]=bonding_curve [3]=assoc_bonding_curve)
#   BUY    = 66063d1201daebea  (18 accounts: mint at [2])  — NOT a CREATE
PUMP_CREATE_DISCRIMINATOR = "d6904cec5f8b31b4"
HELIUS_API_KEY = os.environ.get("HELIUS_API_KEY", "")
WS_URL = os.environ.get("HELIUS_WS_URL", f"wss://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}")
RPC_URL = os.environ.get("HELIUS_RPC_URL", f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}")

_STOP = False
_CASCADE_STATE = "STARTING"   # lifecycle state written to heartbeat
_ACTIVE_CATCHUP0_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=max(1, ACTIVE_CATCHUP0_WORKERS),
    thread_name_prefix="wt-catchup0",
)


def _set_state(state: str):
    global _CASCADE_STATE
    _CASCADE_STATE = state
    _log(f"state → {state}")
_CLEANUP_COUNT = 0
# Budget degrade counters — incremented whenever a path hits its budget ceiling.
# These feed the runtime-budget health panel; never reset (lifetime totals since start).
_TREASURY_TIMEOUT_COUNT  = 0   # treasury_tx handler timed out (outer wait_for)
_CATCHUP_TIMEOUT_COUNT   = 0   # catch_up_candidate / catch_up_subprov RPC timeout
_VALIDATION_TIMEOUT_COUNT = 0  # batch_validate outer timeout in pumpfun_curve_listener
_COLD_RETRY_EXHAUSTED_COUNT = 0  # X27.7 — cold subscriptions that exhausted COLD_SUB_RETRY_MAX


def _handle_signal(*_a):
    global _STOP
    _STOP = True


def _log(msg):
    print(f"[WS_CASCADE] {msg}", flush=True)


def _fetch_candidate_sigs_catchup0(creator: str, limit: int = 5) -> dict:
    """Dedicated first-pass catch-up fetch. Runs outside the event loop/default executor."""
    rpc_started_at = time.time()
    sigs = _rpc(
        "getSignaturesForAddress",
        [creator, {"limit": limit, "commitment": "confirmed"}],
        _budget.NEARRT_RPC_TOTAL_S,
    )
    rpc_done_at = time.time()
    return {
        "creator": creator,
        "sigs": sigs if isinstance(sigs, list) else [],
        "rpc_error": sigs is None,
        "catchup_0_rpc_started_at": rpc_started_at,
        "catchup_0_rpc_done_at": rpc_done_at,
    }


def _lat_ms(a, b):
    return int((a - b) * 1000) if (a is not None and b is not None) else None


def _log_program_create_latency(launch: dict, creator: str, alert_emitted_at: float) -> None:
    """Structured one-line latency split for matched pump.fun CREATEs."""
    if (launch or {}).get("detection_source") != "PROGRAM_LOGS":
        return
    block_time = launch.get("create_time")
    ws_seen = launch.get("program_log_seen_at") or launch.get("ws_seen_at")
    tx_slot = launch.get("tx_slot") or launch.get("create_slot")
    ws_context_slot = launch.get("program_log_context_slot")
    slot_lag = (ws_context_slot - tx_slot) if (ws_context_slot is not None and tx_slot is not None) else None
    fetch_start = launch.get("get_transaction_started_at")
    tx_fetched = launch.get("tx_fetched_at")
    mint_extracted = launch.get("mint_extracted_at")
    db_start = launch.get("record_launch_started_at")
    db_commit = launch.get("record_launch_committed_at")
    _log(
        "PROGRAM_CREATE_LATENCY "
        f"mint={launch.get('mint')} creator={creator} "
        f"tx_slot={tx_slot} "
        f"ws_context_slot={ws_context_slot} "
        f"slot_lag={slot_lag} "
        f"program_fetch={_lat_ms(launch.get('program_tx_fetched_at'), launch.get('program_fetch_started_at'))}ms "
        f"canonical_fetch_skipped={launch.get('canonical_fetch_skipped')} "
        f"duplicate_fetch_count={launch.get('duplicate_fetch_count')} "
        f"block_to_ws={_lat_ms(ws_seen, block_time)}ms "
        f"ws_to_fetch={_lat_ms(fetch_start, ws_seen)}ms "
        f"rpc_fetch={_lat_ms(tx_fetched, fetch_start)}ms "
        f"fetch_to_mint={_lat_ms(mint_extracted, tx_fetched)}ms "
        f"mint_to_db={_lat_ms(db_start, mint_extracted)}ms "
        f"db={_lat_ms(db_commit, db_start)}ms "
        f"db_to_alert={_lat_ms(alert_emitted_at, db_commit)}ms "
        f"total_ws_to_commit={_lat_ms(db_commit, ws_seen)}ms "
        f"total_block_to_commit={_lat_ms(db_commit, block_time)}ms"
    )


# ── raw RPC (1 credit each — never the enhanced-tx endpoint) ─────────────────
# IMPORTANT: _rpc uses BLOCKING urllib. It must NEVER be called directly on the asyncio event
# loop — a slow/stalled RPC would freeze the WHOLE loop (ws.recv stops reading, keepalive pings
# stop → "keepalive ping timeout", and live logsNotifications back up unread in the socket
# buffer). All loop-context callers go through `_arpc`/`_aget_tx` (run_in_executor → thread pool),
# so blocking I/O happens off the loop and recv keeps reading. Direct _rpc is fine only in code
# already running in a worker thread (audit phase1, backfill).
def _rpc(method, params, timeout=None):
    import urllib.request
    # Default to the NEAR_RT budget (called from thread-pool workers, never inline on the loop).
    # Callers that are purely deferred (reconcile, backfill) may pass a looser timeout.
    _t = timeout if timeout is not None else _budget.NEARRT_RPC_TOTAL_S
    try:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        req = urllib.request.Request(RPC_URL, data=body,
                                     headers={"Content-Type": "application/json", "User-Agent": "ws-cascade/0.1"})
        return json.loads(urllib.request.urlopen(req, timeout=_t).read()).get("result")
    except Exception as e:
        _log(f"rpc {method} failed: {e}")
        return None


def _get_tx_raw(sig):
    # 'confirmed' commitment — Helius no longer accepts 'processed' for getTransaction.
    return _rpc("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0,
                                         "commitment": "confirmed"}])


# X24.3 — bounded RPC tail protection. _rpc()'s own timeout=_budget.NEARRT_RPC_TOTAL_S
# bounds each individual blocking socket operation (connect/send/recv), not the
# logical request as a whole (proven in the X24.2.3 evidence report: observed
# ~23.6s calls against a configured 12s budget). _get_tx_guard enforces a TRUE
# cumulative wall-clock deadline around the whole _get_tx_raw() call via a
# dedicated, capacity-bounded executor + circuit breaker + in-flight dedup +
# late-result reuse (src/core/rpc_deadline.py). Lazily constructed so importing
# this module never spins up threads as a side effect (tests, tooling, etc.).
_get_tx_guard_instance: "rpc_deadline.RpcDeadlineGuard | None" = None
_get_tx_guard_lock = threading.Lock()


def _get_tx_guard() -> "rpc_deadline.RpcDeadlineGuard":
    global _get_tx_guard_instance
    if _get_tx_guard_instance is None:
        with _get_tx_guard_lock:
            if _get_tx_guard_instance is None:
                _get_tx_guard_instance = rpc_deadline.RpcDeadlineGuard()
    return _get_tx_guard_instance


def _get_tx_with_outcome(sig) -> "rpc_deadline.DeadlineResult":
    """Deadline-guarded getTransaction fetch returning the full explicit outcome
    (design requirement 4) — used by the fast-retry path, which needs to
    distinguish DEADLINE_EXCEEDED_RUNNING / CANCELLED_BEFORE_START /
    CAPACITY_REJECTED / CIRCUIT_OPEN_REJECTED / RPC_ERROR / NOT_FOUND / SUCCESS
    for its own telemetry, rather than the collapsed dict-or-None every other
    _get_tx() call site still uses (and continues to use unchanged)."""
    return _get_tx_guard().call_with_deadline(sig, lambda: _get_tx_raw(sig))


def _get_tx(sig):
    """Unchanged call contract (dict | None) for every existing call site
    (treasury tx, CDC tx, sibling classification, launch audit, etc.) — now
    tail-protected by the same deadline guard as the fast-retry path, without
    those call sites needing to change at all."""
    result = _get_tx_with_outcome(sig)
    return result.value


# ── async, off-loop wrappers — run blocking RPC + DB work in the default thread-pool executor
# so the asyncio event loop (ws.recv + keepalive) is NEVER frozen by I/O. ───────────────────
async def _arpc(method, params, timeout=None):
    _t = timeout if timeout is not None else _budget.NEARRT_RPC_TOTAL_S
    return await asyncio.get_event_loop().run_in_executor(None, lambda: _rpc(method, params, _t))


async def _aget_tx(sig):
    return await asyncio.get_event_loop().run_in_executor(None, _get_tx, sig)


async def _ato_thread(fn, *args, **kwargs):
    """Run a blocking function (DB writes, sync handlers) off the event loop."""
    return await asyncio.get_event_loop().run_in_executor(None, lambda: fn(*args, **kwargs))


def _prefetch_executor_stats() -> dict:
    """X65.31 — read-only introspection of the dedicated prefetch executor.
    Field names duplicate under two conventions on purpose: max_workers/
    active_threads/queue_depth mirror _default_executor_stats()'s existing
    shape (so the two pools' occupancy can be diffed directly in logs);
    active_workers/pending_jobs are aliases matching this task's own
    requested field names. Wrapped so it can never raise (private-attribute
    access, same caveat as _default_executor_stats)."""
    try:
        ex = _subprov_prefetch_executor
        threads = getattr(ex, "_threads", None)
        work_queue = getattr(ex, "_work_queue", None)
        max_workers = getattr(ex, "_max_workers", None)
        active_threads = len(threads) if threads is not None else None
        queue_depth = work_queue.qsize() if work_queue is not None else None
        return {
            "max_workers": max_workers,
            "active_threads": active_threads,
            "queue_depth": queue_depth,
            "active_workers": active_threads,
            "pending_jobs": queue_depth,
        }
    except Exception:
        return {"max_workers": None, "active_threads": None, "queue_depth": None,
                "active_workers": None, "pending_jobs": None}


async def _ato_prefetch_thread(fn, *args, **kwargs):
    """X65.31 — run a blocking prefetch call on the DEDICATED
    _subprov_prefetch_executor, never the shared asyncio default executor.
    Returns (result, queue_wait_ms, exec_ms): queue_wait_ms is the time spent
    waiting for a free worker thread on THIS pool (admission delay);
    exec_ms is the time the call itself took once it started running. This
    is the split X65.30 identified as missing -- without it, "the call took
    337 seconds" cannot be distinguished from "the call waited 337 seconds
    for a thread.\""""
    loop = asyncio.get_event_loop()
    _submitted_at = time.time()
    _started_holder = {}

    def _wrapped(*a, **kw):
        _started_holder["t"] = time.time()
        return fn(*a, **kw)

    result = await loop.run_in_executor(_subprov_prefetch_executor, lambda: _wrapped(*args, **kwargs))
    _started_at = _started_holder.get("t", _submitted_at)
    queue_wait_ms = round((_started_at - _submitted_at) * 1000, 1)
    exec_ms = round((time.time() - _started_at) * 1000, 1)
    return result, queue_wait_ms, exec_ms


def _default_executor_stats() -> dict:
    """X24.2.1 Phase 1 — read-only introspection of asyncio's default
    ThreadPoolExecutor (shared by every _arpc/_ato_thread call across the WHOLE
    cascade, not just the sweep). Uses private attributes (no public API exists
    for this); wrapped so it can never raise in production even if the
    attributes change across Python versions — diagnostic only, never gates
    behaviour."""
    try:
        loop = asyncio.get_event_loop()
        ex = getattr(loop, "_default_executor", None)
        if ex is None:
            return {"max_workers": None, "queue_depth": None, "active_threads": None, "note": "executor not yet created"}
        return {
            "max_workers": getattr(ex, "_max_workers", None),
            "queue_depth": ex._work_queue.qsize() if hasattr(ex, "_work_queue") else None,
            "active_threads": len(getattr(ex, "_threads", []) or []),
        }
    except Exception as exc:
        return {"error": repr(exc)}


_B58_ALPHABET = b'123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'


def _b58_first8_hex(s):
    """Decode the first 8 bytes of a base58 string to hex (the instruction discriminator).
    Decodes the whole string (cheap — pump ix data is short) and slices."""
    try:
        num = 0
        for ch in s.encode():
            num = num * 58 + _B58_ALPHABET.index(ch)
        full = num.to_bytes((num.bit_length() + 7) // 8, 'big') if num else b''
        full = b'\x00' * (len(s) - len(s.lstrip('1'))) + full
        return full[:8].hex()
    except Exception:
        return ""


def _find_pump_create_ix(tx):
    """Locate the pump.fun CREATE instruction (top-level OR inner) by its discriminator and
    return its account list. The CREATE ix has the mint at accounts[0]. Returns [] if none."""
    msg = tx.get("transaction", {}).get("message", {}) or {}
    meta = tx.get("meta") or {}
    all_ix = list(msg.get("instructions", []) or [])
    for grp in (meta.get("innerInstructions") or []):
        all_ix += grp.get("instructions", []) or []
    for ix in all_ix:
        if ix.get("programId") != PUMP_PROGRAM or "data" not in ix:
            continue
        if _b58_first8_hex(ix.get("data", "")) == PUMP_CREATE_DISCRIMINATOR:
            return ix.get("accounts", []) or []
    return []


def _tx_is_create(tx):
    """Return (is_create, mint, block_time, extra) from a decoded tx.

    A tx is a CREATE only if it contains the actual pump CREATE INSTRUCTION (matched by its
    discriminator) — NOT merely a log line "Instruction: Create", which also appears in later
    swap txs that CPI into pump (the false positive that caused duplicate launches + wrong
    oldest-first selection). Mint is taken DIRECTLY from create_ix.accounts[0] (design item 8;
    [2]=bonding_curve, [3]=assoc_bonding_curve). postTokenBalances is fallback only (item 9)."""
    if not tx:
        return False, None, None, {}
    accts = _find_pump_create_ix(tx)
    if not accts:                                      # no real CREATE instruction → not a CREATE
        return False, None, tx.get("blockTime"), {}

    extra = {"mint_source": "create_instruction"}
    mint = accts[0] if len(accts) > 0 else None
    if len(accts) > 2:
        extra["bonding_curve"] = accts[2]
    if len(accts) > 3:
        extra["associated_bonding_curve"] = accts[3]
    if not mint:                                       # FALLBACK ONLY
        for tb in ((tx.get("meta") or {}).get("postTokenBalances") or []):
            if tb.get("mint") and "So111" not in tb["mint"]:
                mint = tb["mint"]; extra["mint_source"] = "post_token_balances"; break
    return True, mint, tx.get("blockTime"), extra


def _tx_is_swap(tx):
    if not tx:
        return False
    logs = " ".join((tx.get("meta") or {}).get("logMessages", []) or [])
    return ("Instruction: Buy" in logs) or ("Instruction: Sell" in logs)


def _swap_target_mint(tx):
    """The non-WSOL mint a swap tx traded — extracted from the tx WE ALREADY HOLD (zero extra
    RPC). This is how the reverse-direction swarm-attribution links a BUY_SWARM candidate to the
    token it bought: a pump.fun swap touches WSOL + the target mint, so the target is the lone
    non-WSOL mint in the token balances. Returns the mint or None."""
    if not tx:
        return None
    meta = tx.get("meta") or {}
    for tb in ((meta.get("postTokenBalances") or []) + (meta.get("preTokenBalances") or [])):
        m = tb.get("mint")
        if m and "So111" not in m:          # skip WSOL
            return m
    return None


def _classify_sibling(sib):
    """BLOCKING (RPC) — classify a teardown sibling: did it SWAP (→BUY_SWARM) or stay idle
    (→EXPIRED_SIBLING)? Runs in a worker thread via _ato_thread, never on the event loop."""
    state, reason = "EXPIRED_SIBLING", "sibling_idle"
    try:
        tx_sigs = _rpc("getSignaturesForAddress", [sib, {"limit": 10}]) or []
        for s in tx_sigs:
            if s.get("err"):
                continue
            if _tx_is_swap(_get_tx(s["signature"])):
                state, reason = "BUY_SWARM", "sibling_swapped"; break
    except Exception:
        pass
    return state, reason


# ── program CREATE watcher ───────────────────────────────────────────────────
class ProgramCreateWatcher:
    """Subscribes ONCE to the pump.fun program via logsSubscribe.
    On each CREATE notification: fetches tx, checks creator against
    active_candidates dict, and on match delegates to Cascade.process_candidate_sig
    for durable launch recording (wt_watchtower_launches, wt_detected_creates,
    token_analysis.create_tx_signature) + emit + audit phase 1.

    Lifecycle: CLOSED → OPENING → ACTIVE → DRAINING → CLOSED
    Opens on first candidate, closes after PROGRAM_DRAIN_GRACE_S with zero candidates.
    On OPENING → ACTIVE transition runs a tiny per-candidate catch-up scan to cover
    instant launches that land before the subscription is confirmed.
    """

    def __init__(self):
        self.active_candidates: dict = {}        # wallet → {subprov, treasury, expires_at, ...}
        self._state: str = "CLOSED"              # CLOSED|OPENING|ACTIVE|DRAINING
        self._sub_id: int | None = None
        self._ws = None
        self._next_req_id: int = 99001
        self._drain_task: asyncio.Task | None = None
        self._fetch_sem: asyncio.Semaphore = asyncio.Semaphore(CREATE_FETCH_CONCURRENCY)
        self._candidate_persist_queue: asyncio.Queue = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None  # set by run_cascade()
        self._cascade_ref = None                 # set by run_cascade() after Cascade is created
        self._create_replay_order = deque()       # unmatched pump.fun CREATE sigs, oldest → newest
        self._create_replay_by_sig: dict = {}     # sig → compact decoded CREATE entry
        self._invalid_rejected_total: int = 0     # X24.9 — malformed candidate wallets rejected

        # metrics (int/str, updated from the asyncio loop only)
        self.metric_active_candidates: int = 0
        self.metric_persist_queue_depth: int = 0
        self.metric_stream_state: str = "CLOSED"
        self.metric_create_fetch_queue_depth: int = 0
        self.metric_create_fetch_dropped: int = 0
        self.metric_create_fetch_timeout: int = 0
        self.metric_active_catchup_runs: int = 0
        self.metric_active_catchup_checked: int = 0
        self.metric_active_catchup_hits: int = 0
        self.metric_active_catchup_errors: int = 0
        self.metric_expire_probe_runs: int = 0
        self.metric_expire_probe_hits: int = 0
        self.metric_expire_probe_errors: int = 0
        self.metric_program_matches: int = 0
        self.metric_create_replay_stored: int = 0
        self.metric_create_replay_hits: int = 0
        self.metric_create_replay_evicted: int = 0
        self.metric_candidates_expired: int = 0
        self.metric_program_opens: int = 0
        self.metric_program_closes: int = 0
        self._stream_opened_at: float = 0.0
        self.metric_program_open_seconds: float = 0.0

        # background task handles
        self._expire_task: asyncio.Task | None = None
        self._persist_task: asyncio.Task | None = None
        self._pending_fetch_task: asyncio.Task | None = None

        # Pending-CREATE-fetch retry queue: sigs whose getTransaction returned None on the
        # first PROGRAM_LOGS pass (indexer boundary). Each entry: {sig, queued_at, attempts,
        # next_retry_at, program_log_seen_at, program_log_context_slot}. Bounded by age and
        # count; entries that match active_candidates on a later retry fire process_candidate_sig
        # as if they had been caught live. Entries that exceed max retries are dropped.
        self._pending_create_sigs: dict = {}   # sig → entry dict
        self.metric_pending_create_queued: int = 0
        self.metric_pending_create_resolved: int = 0
        self.metric_pending_create_dropped: int = 0

    def start_background_tasks(self):
        """Start the expire + persist background loops. Call once after the event loop is live."""
        if self._expire_task is None or self._expire_task.done():
            self._expire_task = asyncio.ensure_future(self._expire_loop())
        if self._persist_task is None or self._persist_task.done():
            self._persist_task = asyncio.ensure_future(self._persist_loop())
        if self._pending_fetch_task is None or self._pending_fetch_task.done():
            self._pending_fetch_task = asyncio.ensure_future(self._pending_create_fetch_loop())

    def _ops(self):
        return db_connect(OPS_DB_PATH, timeout=5)

    # ── Pending-CREATE-fetch retry ────────────────────────────────────────────
    # When _fetch_and_check's getTransaction returns None (indexer boundary), the sig is
    # placed here instead of being silently dropped. A background loop retries with bounded
    # delays; on success the tx is checked against active_candidates (same as live path) and
    # stored in the replay buffer if still unmatched. This closes the gap where a PROGRAM_LOGS
    # notification fires, the indexer hasn't made the tx available yet, and neither the replay
    # buffer nor ACTIVE_CATCHUP0 can recover it.

    _PENDING_FETCH_RETRIES = [0.5, 1.5, 4.0]  # seconds between attempts (3 retries max)
    _PENDING_FETCH_MAX     = 200               # cap the queue to bound memory
    _PENDING_FETCH_TTL     = 30.0              # drop entries older than this

    def _enqueue_pending_create(self, sig: str, program_log_seen_at: float | None,
                                program_log_context_slot: int | None) -> None:
        """Queue a CREATE sig whose getTransaction returned None for later retry."""
        if sig in self._pending_create_sigs:
            return
        if len(self._pending_create_sigs) >= self._PENDING_FETCH_MAX:
            return
        now = time.time()
        self._pending_create_sigs[sig] = {
            "sig": sig,
            "queued_at": now,
            "attempts": 0,
            "next_retry_at": now + self._PENDING_FETCH_RETRIES[0],
            "program_log_seen_at": program_log_seen_at,
            "program_log_context_slot": program_log_context_slot,
        }
        self.metric_pending_create_queued += 1
        _log(f"[ProgramWatcher] PENDING_CREATE_QUEUED sig={sig[:16]}… retry_in={self._PENDING_FETCH_RETRIES[0]}s")

    async def _pending_create_fetch_loop(self) -> None:
        """Background task: retry fetching pending CREATE txs whose first fetch returned None."""
        while True:
            try:
                await asyncio.sleep(0.25)
                if not self._pending_create_sigs or self._cascade_ref is None:
                    continue
                now = time.time()
                to_drop = [s for s, e in self._pending_create_sigs.items()
                           if now - e["queued_at"] > self._PENDING_FETCH_TTL]
                for s in to_drop:
                    self._pending_create_sigs.pop(s, None)
                    self.metric_pending_create_dropped += 1
                    _log(f"[ProgramWatcher] PENDING_CREATE_EXPIRED sig={s[:16]}…")

                due = [e for e in self._pending_create_sigs.values() if e["next_retry_at"] <= now]
                for entry in due:
                    sig = entry["sig"]
                    attempt = entry["attempts"]
                    try:
                        async with asyncio.timeout(CREATE_FETCH_TIMEOUT_S):
                            tx = await _arpc("getTransaction",
                                             [sig, {"encoding": "jsonParsed",
                                                    "maxSupportedTransactionVersion": 0,
                                                    "commitment": "confirmed"}])
                    except Exception:
                        tx = None

                    if tx is None:
                        next_attempt = attempt + 1
                        if next_attempt >= len(self._PENDING_FETCH_RETRIES):
                            self._pending_create_sigs.pop(sig, None)
                            self.metric_pending_create_dropped += 1
                            _log(f"[ProgramWatcher] PENDING_CREATE_EXHAUSTED sig={sig[:16]}… attempts={next_attempt}")
                        else:
                            entry["attempts"] = next_attempt
                            entry["next_retry_at"] = now + self._PENDING_FETCH_RETRIES[next_attempt]
                        continue

                    # Fetch succeeded — process same as _fetch_and_check
                    self._pending_create_sigs.pop(sig, None)
                    is_create, mint, _btime, _extra = _tx_is_create(tx)
                    if not is_create or not mint:
                        continue

                    signer_keys = []
                    try:
                        for k in (tx.get("transaction", {}).get("message", {}).get("accountKeys") or []):
                            if isinstance(k, dict) and k.get("signer"):
                                signer_keys.append(k.get("pubkey"))
                    except Exception:
                        pass

                    create_accts = _find_pump_create_ix(tx)
                    creator = None
                    for acct in create_accts:
                        if acct in self.active_candidates:
                            creator = acct
                            break
                    if not creator:
                        for acct in signer_keys:
                            if acct in self.active_candidates:
                                creator = acct
                                break

                    program_log_seen_at = entry.get("program_log_seen_at")
                    program_log_context_slot = entry.get("program_log_context_slot")

                    if creator and creator in self.active_candidates:
                        self.metric_pending_create_resolved += 1
                        _log(
                            f"[ProgramWatcher] PENDING_CREATE_MATCHED sig={sig[:16]}… "
                            f"creator={creator[:12]}… mint={mint} "
                            f"age={int((now - entry['queued_at']) * 1000)}ms attempt={attempt+1}"
                        )
                        asyncio.ensure_future(
                            self._cascade_ref.process_candidate_sig(
                                creator, sig, tx_data=tx,
                                detection_source="PENDING_CREATE_RETRY",
                                timing={
                                    "program_log_seen_at": program_log_seen_at,
                                    "program_log_context_slot": program_log_context_slot,
                                    "handoff_to_canonical_at": time.time(),
                                }))
                    else:
                        # No current match — store in replay buffer so add_candidates() can find it
                        self._store_create_replay(
                            sig=sig, tx=tx, mint=mint,
                            create_accts=create_accts, signer_keys=signer_keys,
                            program_log_seen_at=program_log_seen_at,
                            program_log_context_slot=program_log_context_slot,
                            program_fetch_started_at=None,
                            program_tx_fetched_at=time.time(),
                        )
                        _log(
                            f"[ProgramWatcher] PENDING_CREATE_BUFFERED sig={sig[:16]}… "
                            f"mint={mint} age={int((now - entry['queued_at']) * 1000)}ms attempt={attempt+1}"
                        )
            except Exception as exc:
                _log(f"[ProgramWatcher] pending_create_fetch_loop error: {exc}")

    def _prune_create_replay(self) -> None:
        """Keep the unmatched CREATE replay buffer bounded by age and count."""
        cutoff = time.time() - max(1, CREATE_REPLAY_TTL_SEC)
        while self._create_replay_order:
            sig = self._create_replay_order[0]
            entry = self._create_replay_by_sig.get(sig)
            if entry and entry.get("seen_at", 0) >= cutoff and len(self._create_replay_order) <= CREATE_REPLAY_MAX:
                break
            self._create_replay_order.popleft()
            if self._create_replay_by_sig.pop(sig, None) is not None:
                self.metric_create_replay_evicted += 1

    def _store_create_replay(self, *, sig: str, tx: dict, mint: str, create_accts: list,
                             signer_keys: list, program_log_seen_at: float | None,
                             program_log_context_slot: int | None,
                             program_fetch_started_at: float | None,
                             program_tx_fetched_at: float | None) -> None:
        """Retain an unmatched CREATE briefly so newly armed creators can replay it."""
        if not sig:
            return
        self._prune_create_replay()
        keys = {x for x in list(create_accts or []) + list(signer_keys or []) if x}
        if not keys:
            return
        now = time.time()
        if sig not in self._create_replay_by_sig:
            self._create_replay_order.append(sig)
            self.metric_create_replay_stored += 1
        self._create_replay_by_sig[sig] = {
            "sig": sig,
            "tx": tx,
            "mint": mint,
            "keys": keys,
            "seen_at": now,
            "program_log_seen_at": program_log_seen_at,
            "program_log_context_slot": program_log_context_slot,
            "program_fetch_started_at": program_fetch_started_at,
            "program_tx_fetched_at": program_tx_fetched_at,
        }
        self._prune_create_replay()

    def _matching_replay_entries(self, creator: str) -> list:
        self._prune_create_replay()
        matches = []
        for sig in list(self._create_replay_order):
            entry = self._create_replay_by_sig.get(sig)
            if entry and creator in (entry.get("keys") or set()):
                matches.append(entry)
        return matches

    def _replay_buffer_for_candidates(self, wallets: list[str]) -> None:
        """Replay unmatched CREATEs before falling back to creator-index catch-up."""
        if self._cascade_ref is None:
            return
        for creator in wallets:
            if creator not in self.active_candidates:
                continue
            matches = self._matching_replay_entries(creator)
            for entry in matches:
                sig = entry.get("sig")
                if not sig:
                    continue
                self.metric_create_replay_hits += 1
                _log(
                    f"[ProgramWatcher] replay-buffer CREATE creator={creator[:12]}… "
                    f"mint={entry.get('mint')} sig={sig[:12]}… "
                    f"age={_lat_ms(time.time(), entry.get('seen_at'))}ms"
                )
                asyncio.ensure_future(
                    self._cascade_ref.process_candidate_sig(
                        creator, sig, tx_data=entry.get("tx"), detection_source="PROGRAM_REPLAY_BUFFER",
                        timing={
                            "program_log_seen_at": entry.get("program_log_seen_at"),
                            "program_log_context_slot": entry.get("program_log_context_slot"),
                            "program_fetch_started_at": entry.get("program_fetch_started_at"),
                            "program_tx_fetched_at": entry.get("program_tx_fetched_at"),
                            "handoff_to_canonical_at": time.time(),
                        }))

    def _dispatch_immediate_catchup0(self, creator: str, registered_at: float) -> None:
        """Start the first candidate catch-up RPC immediately on a dedicated executor."""
        if self._cascade_ref is None:
            return
        submit_at = time.time()

        future = _ACTIVE_CATCHUP0_EXECUTOR.submit(_fetch_candidate_sigs_catchup0, creator, 5)

        def _done(fut):
            try:
                result = fut.result()
            except Exception as exc:
                _log(f"[ProgramWatcher] catch-up +0s submit error {creator[:12]}…: {exc}")
                return
            loop = self._loop
            if not loop or not loop.is_running() or self._cascade_ref is None:
                return

            async def _process_result():
                self.metric_active_catchup_runs += 1
                self.metric_active_catchup_checked += 1
                if creator not in self.active_candidates:
                    return
                if result.get("rpc_error"):
                    self.metric_active_catchup_errors += 1
                    _log(
                        f"[ProgramWatcher] catch-up +0s RPC_ERROR {creator[:12]}… "
                        f"commitment=confirmed "
                        f"reg_to_submit={_lat_ms(submit_at, registered_at)}ms "
                        f"submit_to_rpc={_lat_ms(result.get('catchup_0_rpc_started_at'), submit_at)}ms "
                        f"rpc={_lat_ms(result.get('catchup_0_rpc_done_at'), result.get('catchup_0_rpc_started_at'))}ms"
                    )
                    return
                sigs = result.get("sigs") or []
                found = False
                process_started_at = None
                for s in sorted([x for x in sigs if not x.get("err")],
                                key=lambda x: x.get("blockTime") or 0):
                    sig = s.get("signature")
                    if not sig:
                        continue
                    process_started_at = time.time()
                    timing = {
                        "candidate_registered_at": registered_at,
                        "catchup_0_submit_at": submit_at,
                        "catchup_0_rpc_started_at": result.get("catchup_0_rpc_started_at"),
                        "catchup_0_rpc_done_at": result.get("catchup_0_rpc_done_at"),
                        "catchup_0_process_started_at": process_started_at,
                    }
                    verdict = await self._cascade_ref.process_candidate_sig(
                        creator, sig, detection_source="ACTIVE_CATCHUP", timing=timing)
                    if verdict == "CREATE":
                        self.metric_active_catchup_hits += 1
                        found = True
                        _log(
                            f"[ProgramWatcher] catch-up +0s CREATE_FOUND {creator[:12]}… sig={sig[:12]}… "
                            f"reg_to_submit={_lat_ms(submit_at, registered_at)}ms "
                            f"submit_to_rpc={_lat_ms(result.get('catchup_0_rpc_started_at'), submit_at)}ms "
                            f"rpc={_lat_ms(result.get('catchup_0_rpc_done_at'), result.get('catchup_0_rpc_started_at'))}ms "
                            f"rpc_to_process={_lat_ms(process_started_at, result.get('catchup_0_rpc_done_at'))}ms"
                        )
                        break
                if not found:
                    first_process = process_started_at or time.time()
                    _log(
                        f"[ProgramWatcher] catch-up +0s NO_SIGNATURES {creator[:12]}… sigs={len(sigs)} "
                        f"reg_to_submit={_lat_ms(submit_at, registered_at)}ms "
                        f"submit_to_rpc={_lat_ms(result.get('catchup_0_rpc_started_at'), submit_at)}ms "
                        f"rpc={_lat_ms(result.get('catchup_0_rpc_done_at'), result.get('catchup_0_rpc_started_at'))}ms "
                        f"rpc_to_process={_lat_ms(first_process, result.get('catchup_0_rpc_done_at'))}ms"
                    )

            asyncio.run_coroutine_threadsafe(_process_result(), loop)

        future.add_done_callback(_done)

    async def _delayed_catchup_sequence(self, wallets: list[str]) -> None:
        """Bounded delayed catch-up for creators added while the program stream is active."""
        if self._cascade_ref is None:
            return
        start_at = time.time()
        remaining = list(wallets)
        self.metric_active_catchup_runs += 1
        for offset in (2, 5, 10):
            if not remaining:
                break
            sleep_for = start_at + offset - time.time()
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
            still_needed = []
            for creator in remaining:
                if creator not in self.active_candidates:
                    _log(f"[ProgramWatcher] catch-up +{offset}s skip {creator[:12]}… inactive")
                    continue
                conn_chk = self._ops()
                try:
                    already = conn_chk.execute(
                        "SELECT 1 FROM wt_watchtower_launches WHERE creator_wallet=? LIMIT 1",
                        (creator,)).fetchone()
                finally:
                    conn_chk.close()
                if already:
                    self.metric_active_catchup_hits += 1
                    _log(f"[ProgramWatcher] catch-up +{offset}s skip {creator[:12]}… already fired")
                    continue

                self.metric_active_catchup_checked += 1
                rpc_started_at = time.time()
                try:
                    sigs_raw = await asyncio.wait_for(
                        _arpc("getSignaturesForAddress",
                              [creator, {"limit": 5, "commitment": "confirmed"}]),
                        timeout=5.0,
                    )
                except Exception as exc:
                    self.metric_active_catchup_errors += 1
                    _log(f"[ProgramWatcher] catch-up error {creator[:12]}… +{offset}s: {exc}")
                    still_needed.append(creator)
                    continue
                if sigs_raw is None:
                    self.metric_active_catchup_errors += 1
                    _log(
                        f"[ProgramWatcher] catch-up +{offset}s RPC_ERROR {creator[:12]}… "
                        f"commitment=confirmed rpc={_lat_ms(time.time(), rpc_started_at)}ms"
                    )
                    still_needed.append(creator)
                    continue
                sigs = sigs_raw if isinstance(sigs_raw, list) else []

                found = False
                clean_sigs = [x for x in sigs if not x.get("err")]
                for s in sorted(clean_sigs, key=lambda x: x.get("blockTime") or 0):
                    sig = s.get("signature")
                    if not sig:
                        continue
                    verdict = await self._cascade_ref.process_candidate_sig(
                        creator, sig, detection_source="ACTIVE_CATCHUP")
                    if verdict == "CREATE":
                        self.metric_active_catchup_hits += 1
                        _log(
                            f"[ProgramWatcher] catch-up +{offset}s CREATE_FOUND {creator[:12]}… "
                            f"sig={sig[:12]}…"
                        )
                        found = True
                        break
                if not found:
                    _log(
                        f"[ProgramWatcher] catch-up +{offset}s NO_SIGNATURES {creator[:12]}… "
                        f"sigs={len(sigs)} rpc={_lat_ms(time.time(), rpc_started_at)}ms"
                    )
                    still_needed.append(creator)
            remaining = still_needed
            if remaining:
                _log(f"[ProgramWatcher] catch-up +{offset}s: {len(remaining)} creator(s) still unfired")

    def _schedule_delayed_catchups(self, wallets: list[str]) -> None:
        if not wallets or self._cascade_ref is None:
            return
        loop = self._loop
        if not loop or not loop.is_running():
            _log(f"[ProgramWatcher] catch-up delayed scheduling skipped — loop unavailable wallets={len(wallets)}")
            return

        async def _runner():
            await self._delayed_catchup_sequence(wallets)

        def _on_loop():
            task = asyncio.create_task(_runner())

            def _done(task):
                try:
                    task.result()
                except Exception as exc:
                    _log(f"[ProgramWatcher] catch-up delayed task failed: {exc}")

            task.add_done_callback(_done)

        loop.call_soon_threadsafe(_on_loop)
        _log(f"[ProgramWatcher] catch-up delayed scheduled wallets={len(wallets)} offsets=2,5,10")

    def add_candidates(self, candidates: list, conn) -> None:
        """Called from _handle_subprov_tx after wrap-close. Each dict must contain
        'candidate', 'subprov', 'treasury', 'wrap_sig', 'wrap_time', 'amount'."""
        now = int(time.time())
        registered_at_by_wallet = {}
        for meta in candidates:
            wallet = meta.get("candidate")
            if not wallet:
                continue
            # X24.9 — same boundary validation as SubscriptionManager.subscribe():
            # a malformed candidate wallet must never occupy an in-memory ProgramWatcher
            # slot or a DB persist-queue entry.
            if not is_valid_pubkey(wallet):
                self._invalid_rejected_total += 1
                _log(f"⛔ REJECTED invalid candidate wallet reason={invalid_reason(wallet)} "
                     f"wallet={str(wallet)[:20]}…")
                continue
            expires_at = meta.get("expires_at") or (now + CANDIDATE_TTL_SEC)
            # added_at=0 for DB-restored candidates (reconnect reload) so catch-up skips them.
            # If the wallet is already in active_candidates with a live added_at (>0), preserve it:
            # a DB reload must not overwrite a freshly-armed candidate's added_at with 0, which
            # would make _catchup_on_active's age filter exclude it (root cause of 4p7tnfwPED5 miss).
            incoming_added_at = meta.get("added_at", now)
            existing_added_at = (self.active_candidates.get(wallet) or {}).get("added_at", 0) or 0
            added_at = existing_added_at if (incoming_added_at == 0 and existing_added_at > 0) else incoming_added_at
            registered_at = time.time()
            registered_at_by_wallet[wallet] = registered_at
            self.active_candidates[wallet] = {
                **meta, "expires_at": expires_at, "added_at": added_at,
                "candidate_registered_at": registered_at,
            }
            row = {
                "candidate":   wallet,
                "subprov":     meta.get("subprov"),
                "treasury":    meta.get("treasury"),
                "wrap_sig":    meta.get("wrap_sig"),
                "wrap_time":   meta.get("wrap_time"),
                "amount":      meta.get("amount"),
                "now":         now,
                "expires_at":  expires_at,
            }
            try:
                self._candidate_persist_queue.put_nowait({"action": "insert", **row})
            except asyncio.QueueFull:
                pass  # persist queue full; INSERT OR IGNORE means a later retry won't duplicate

        self.metric_active_candidates = len(self.active_candidates)

        # Only live, newly-discovered candidates should trigger the +0/+2/+5/+10 catch-up.
        # DB-restored candidates are reloaded so the live program stream can match them going
        # forward; scanning all restored rows on every reconnect creates an RPC storm.
        new_wallets = [
            m.get("candidate") for m in candidates
            if m.get("candidate") and (m.get("added_at", now) or 0) > 0
        ]

        # if candidates present and stream is closed: trigger open (thread-safe)
        if self.active_candidates and self._state == "CLOSED":
            if self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(self._trigger_open(), self._loop)
            else:
                asyncio.ensure_future(self._trigger_open())

        # If the pump.fun CREATE log already passed through ProgramWatcher milliseconds before
        # this creator was armed, recover it from the replay buffer before asking the creator
        # wallet index. This closes the same-second race where getSignaturesForAddress(creator)
        # can still return zero even though the program log was already observed.
        if self._state == "ACTIVE" and new_wallets and self._cascade_ref is not None:
            self._replay_buffer_for_candidates(new_wallets)

        # If the stream is already ACTIVE, the new candidate was not visible when the
        # logsSubscribe was opened. Start the first catch-up RPC immediately on a dedicated
        # executor, then keep bounded delayed retries for RPC-indexing lag. This avoids making
        # "0s" mean "whenever the event loop/default executor gets to it."
        if self._state == "ACTIVE" and new_wallets and self._cascade_ref is not None:
            for wallet in new_wallets:
                self._dispatch_immediate_catchup0(
                    wallet, registered_at_by_wallet.get(wallet, time.time()))

        if self._state == "ACTIVE" and new_wallets and self._cascade_ref is not None:
            self._schedule_delayed_catchups(new_wallets)

    def evict_by_subprov(self, subprov: str) -> int:
        """Remove all active_candidates belonging to a dismissed/expired subprov.

        X28.0: after Phase 1/2, this is called ONLY from (a) reject_unproven_sessions()'s
        cleanup path, where the underlying query already guarantees zero candidates exist for
        that subprov (a defensive no-op, not load-bearing), and (b) the post-CREATE watchdog,
        gated behind PW_POST_CREATE_EVICT_ENABLED (default off). It is deliberately NOT called
        from session-TTL expiry anymore — see cleanup_pass(). If this ever evicts a nonzero
        count outside those two known-safe cases, that's a regression: candidate_evicted_by_parent
        should read zero in steady state."""
        to_evict = [w for w, m in self.active_candidates.items() if m.get("subprov") == subprov]
        for w in to_evict:
            self.active_candidates.pop(w, None)
        if to_evict:
            self.metric_active_candidates = len(self.active_candidates)
            _log(f"[ProgramWatcher] evicted {len(to_evict)} candidates for dismissed subprov {subprov[:12]}…")
            if self._cascade_ref is not None:
                self._cascade_ref._metric("candidate_evicted_by_parent", len(to_evict))
            if len(self.active_candidates) == 0 and self._state == "ACTIVE":
                asyncio.ensure_future(self._close_stream(reason="evicted_zero_candidates"))
        return len(to_evict)

    async def _trigger_open(self):
        """Fire the stream open; called from add_candidates when CLOSED → should open."""
        if self._state != "CLOSED":
            return
        self._state = "OPENING"
        self.metric_stream_state = "OPENING"
        _log("[ProgramWatcher] state → OPENING (first candidate added)")
        # The actual logsSubscribe is sent via the cascade's shared WS in _open_stream,
        # which is called from run_cascade once it has a live ws handle.

    async def _open_stream(self, ws) -> None:
        """Send logsSubscribe for the pump.fun program on the shared WS connection."""
        if self._state not in ("OPENING", "CLOSED"):
            return
        if not self.active_candidates:
            self._state = "CLOSED"
            self.metric_stream_state = "CLOSED"
            return
        self._state = "OPENING"
        self.metric_stream_state = "OPENING"
        self._ws = ws
        # Cancel drain timer if one is running
        if self._drain_task and not self._drain_task.done():
            self._drain_task.cancel()
            self._drain_task = None
        # logsSubscribe with mentions=[PUMP_PROGRAM] — fires on every tx that mentions the program
        msg = {
            "jsonrpc": "2.0",
            "id": 99000,   # fixed id; the sub confirmation routes via _on_program_subscribe_confirmed
            "method": "logsSubscribe",
            "params": [
                {"mentions": [PUMP_PROGRAM]},
                {"commitment": os.environ.get("WS_LOGS_COMMITMENT", "processed")}
            ]
        }
        try:
            await ws.send(json.dumps(msg))
            _log("[ProgramWatcher] logsSubscribe sent for pump.fun program")
        except Exception as e:
            _log(f"[ProgramWatcher] open_stream send failed: {e}")
            self._state = "CLOSED"
            self.metric_stream_state = "CLOSED"

    def on_subscribe_confirmed(self, sub_id: int) -> None:
        """Called when the subscription confirmation for our req arrives."""
        self._sub_id = sub_id
        if not self.active_candidates:
            _log(f"[ProgramWatcher] sub_id={sub_id} confirmed with zero candidates — closing")
            if self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    self._close_stream(reason="confirmed_zero_candidates"),
                    self._loop,
                )
            else:
                asyncio.ensure_future(self._close_stream(reason="confirmed_zero_candidates"))
            return
        self._state = "ACTIVE"
        self.metric_stream_state = "ACTIVE"
        self.metric_program_opens += 1
        self._stream_opened_at = time.time()
        _log(f"[ProgramWatcher] state → ACTIVE sub_id={sub_id}")
        # Catch-up: a candidate that CREATEd during OPENING (before this confirm) is already
        # on-chain but never triggered a logsNotification for us. Check each active candidate's
        # most-recent sigs over a tiny window — bounded, best-effort, off the WS recv path.
        if self.active_candidates and self._cascade_ref is not None:
            asyncio.ensure_future(self._catchup_on_active())
        # Also schedule delayed catch-up retries (+2s/+5s/+10s) for any live candidate that was
        # registered during OPENING. Those candidates had add_candidates() called while state was
        # not ACTIVE, so the normal delayed-retry path was never triggered. Now that we are ACTIVE,
        # give them the same retry coverage as candidates registered during ACTIVE.
        # Filter: added_at > 0 (live, not DB-restored) and registered recently (within OPENING window).
        if self._cascade_ref is not None:
            _opening_cutoff = time.time() - 10  # OPENING handshake observed <2s; 10s is 5× that
            _opening_candidates = [
                w for w, m in self.active_candidates.items()
                if (m.get("added_at") or 0) >= _opening_cutoff
            ]
            if _opening_candidates:
                _log(f"[ProgramWatcher] OPENING_DELAYED_CATCHUP scheduling {len(_opening_candidates)} candidate(s) added during OPENING")
                self._schedule_delayed_catchups(_opening_candidates)

    async def _on_notification(self, data: dict, ws) -> None:
        """Called when a logsNotification arrives on the pump.fun subscription.
        Fetch only likely CREATE txs, then check creator against active_candidates."""
        if self._state != "ACTIVE":
            return
        params = data.get("params") or {}
        result = (params.get("result") or {})
        context = (result.get("context") or {})
        value = result.get("value") or {}
        sig = value.get("signature")
        if value.get("err") or not sig:
            return

        logs = value.get("logs") or []
        if not any("Instruction: Create" in str(line) for line in logs):
            return

        program_log_seen_at = time.time()
        program_log_context_slot = context.get("slot")

        # Rate-limit: if all fetch slots are busy AND too many are queued, drop.
        # asyncio.Semaphore._value is the number of available slots.
        if self._fetch_sem._value == 0 and self.metric_create_fetch_queue_depth >= CREATE_FETCH_MAX_QUEUE:
            self.metric_create_fetch_dropped += 1
            return

        self.metric_create_fetch_queue_depth += 1
        asyncio.ensure_future(self._fetch_and_check(
            sig,
            program_log_seen_at=program_log_seen_at,
            program_log_context_slot=program_log_context_slot,
        ))

    async def _fetch_and_check(self, sig: str, program_log_seen_at: float | None = None,
                               program_log_context_slot: int | None = None) -> None:
        """Fetch one tx and check if its creator is a candidate. Phase 1: log only."""
        program_fetch_started_at = None
        program_tx_fetched_at = None
        try:
            async with self._fetch_sem:
                program_fetch_started_at = time.time()
                try:
                    async with asyncio.timeout(CREATE_FETCH_TIMEOUT_S):
                        tx = await _arpc("getTransaction",
                                         [sig, {"encoding": "jsonParsed",
                                                "maxSupportedTransactionVersion": 0,
                                                "commitment": "confirmed"}])
                        program_tx_fetched_at = time.time()
                except TimeoutError:
                    self.metric_create_fetch_timeout += 1
                    return
                except asyncio.TimeoutError:
                    self.metric_create_fetch_timeout += 1
                    return
        finally:
            self.metric_create_fetch_queue_depth = max(0, self.metric_create_fetch_queue_depth - 1)

        if not tx:
            # Indexer boundary: tx not yet available at commitment=confirmed. Queue for retry
            # instead of silently dropping — the retry loop will match against active_candidates
            # once the tx becomes available, covering the gap where neither the replay buffer
            # nor ACTIVE_CATCHUP0 can recover a CREATE that indexed slower than the WS notification.
            self._enqueue_pending_create(sig, program_log_seen_at, program_log_context_slot)
            return
        is_create, mint, btime, extra = _tx_is_create(tx)
        if not is_create or not mint:
            return

        # The pump.fun CREATE account order can vary across parser versions. Do not
        # hardcode an index; match the decoded CREATE instruction/signers against the
        # armed candidate set.
        signer_keys = []
        try:
            for k in (tx.get("transaction", {}).get("message", {}).get("accountKeys") or []):
                if isinstance(k, dict) and k.get("signer"):
                    signer_keys.append(k.get("pubkey"))
        except Exception:
            pass

        create_accts = _find_pump_create_ix(tx)
        creator = None
        for acct in create_accts:
            if acct in self.active_candidates:
                creator = acct
                break
        if not creator:
            for acct in signer_keys:
                if acct in self.active_candidates:
                    creator = acct
                    break

        # Cross-check with our candidates dict (O(1) — no DB)
        if creator and creator in self.active_candidates:
            meta = self.active_candidates[creator]
            self.metric_program_matches += 1
            subprov_s = str(meta.get("subprov", "?"))[:12]
            _log(f"[ProgramWatcher] MATCH creator={creator[:12]}… "
                 f"mint={mint} subprov={subprov_s}… sig={sig[:16]}…")
            if self._cascade_ref is not None:
                # Delegate to the canonical durable path: record_launch + emit + audit.
                # process_candidate_sig is idempotent (INSERT OR IGNORE + _seen guard).
                asyncio.ensure_future(
                    self._cascade_ref.process_candidate_sig(
                        creator, sig, tx_data=tx, detection_source="PROGRAM_LOGS",
                        timing={
                            "program_log_seen_at": program_log_seen_at,
                            "program_log_context_slot": program_log_context_slot,
                            "program_fetch_started_at": program_fetch_started_at,
                            "program_tx_fetched_at": program_tx_fetched_at,
                            "handoff_to_canonical_at": time.time(),
                        }))
        else:
            self._store_create_replay(
                sig=sig,
                tx=tx,
                mint=mint,
                create_accts=create_accts,
                signer_keys=signer_keys,
                program_log_seen_at=program_log_seen_at,
                program_log_context_slot=program_log_context_slot,
                program_fetch_started_at=program_fetch_started_at,
                program_tx_fetched_at=program_tx_fetched_at,
            )

    async def _catchup_on_active(self) -> None:
        """Run immediately when the stream becomes ACTIVE: check only recently-added candidates
        for a CREATE that landed during the OPENING window (before subscription was confirmed).
        Scoped to WS_OPENING_CATCHUP_MAX_AGE_SEC (default 30s) and capped at 5 wallets so
        reconnect storms don't burn RPC across the full restored candidate set."""
        max_age = int(os.environ.get("WS_OPENING_CATCHUP_MAX_AGE_SEC", "30"))
        cap     = int(os.environ.get("WS_OPENING_CATCHUP_LIMIT", "5"))
        cutoff  = int(time.time()) - max_age
        recent  = [w for w, m in self.active_candidates.items()
                   if (m.get("added_at") or 0) >= cutoff][:cap]
        _log(f"[ProgramWatcher] ACTIVE catch-up: checking {len(recent)}/{len(self.active_candidates)} recent candidate(s) max_age={max_age}s")
        for creator in recent:
            if self._cascade_ref is None:
                break
            try:
                sigs_raw = await asyncio.wait_for(
                    _arpc("getSignaturesForAddress",
                          [creator, {"limit": 5, "commitment": "confirmed"}]),
                    timeout=5.0,
                )
            except Exception as exc:
                self.metric_active_catchup_errors += 1
                _log(f"[ProgramWatcher] OPENING_CATCHUP RPC_ERROR {creator[:12]}… commitment=confirmed err={exc}")
                continue
            if sigs_raw is None:
                self.metric_active_catchup_errors += 1
                _log(f"[ProgramWatcher] OPENING_CATCHUP RPC_ERROR {creator[:12]}… commitment=confirmed")
                continue
            sigs = sigs_raw if isinstance(sigs_raw, list) else []
            if not sigs:
                _log(f"[ProgramWatcher] OPENING_CATCHUP NO_SIGNATURES {creator[:12]}… sigs=0")
                continue
            for s in sorted([x for x in sigs if not x.get("err")],
                            key=lambda x: x.get("blockTime") or 0):
                sig = s.get("signature")
                if not sig:
                    continue
                verdict = await self._cascade_ref.process_candidate_sig(
                    creator, sig, detection_source="OPENING_CATCHUP")
                if verdict == "CREATE":
                    _log(f"[ProgramWatcher] OPENING_CATCHUP CREATE_FOUND {creator[:12]}… sig={sig[:12]}…")
                    break

    async def _expire_loop(self) -> None:
        """Runs every 30s: expire stale candidates, enqueue expire records, maybe drain."""
        while not _STOP:
            await asyncio.sleep(30)
            try:
                now = int(time.time())
                fresh_cutoff = now - SESSION_TTL_SEC  # only probe candidates added this session
                expired = [w for w, m in self.active_candidates.items()
                           if m.get("expires_at", 0) < now]
                for w in expired:
                    meta_e = self.active_candidates.get(w, {})
                    # skip probe for restored (added_at=0) or stale candidates — no missed
                    # CREATE is possible for wallets not freshly armed in this session
                    is_fresh = (meta_e.get("added_at") or 0) >= fresh_cutoff
                    if self._cascade_ref is not None and w in self.active_candidates and is_fresh:
                        self.metric_expire_probe_runs += 1
                        try:
                            sigs = await asyncio.wait_for(
                                _arpc("getSignaturesForAddress",
                                      [w, {"limit": 8, "commitment": "confirmed"}]),
                                timeout=8.0,
                            ) or []
                            checked = 0
                            found = False
                            for s in sorted([x for x in sigs if not x.get("err")],
                                            key=lambda x: x.get("blockTime") or 0):
                                sig = s.get("signature")
                                if not sig:
                                    continue
                                checked += 1
                                verdict = await self._cascade_ref.process_candidate_sig(
                                    w, sig, detection_source="EXPIRE_PROBE")
                                if verdict == "CREATE":
                                    found = True
                                    self.metric_expire_probe_hits += 1
                                    _log(f"[ProgramWatcher] expire-probe recovered CREATE {w[:12]}... sig={sig[:12]}...")
                                    break
                            if found or w not in self.active_candidates:
                                continue
                            _log(f"[ProgramWatcher] expire-probe no CREATE {w[:12]}... checked={checked}")
                        except Exception as e:
                            self.metric_expire_probe_errors += 1
                            _log(f"[ProgramWatcher] expire-probe error {w[:12]}...: {e}")
                    meta = self.active_candidates.pop(w, {})
                    self.metric_candidates_expired += 1
                    try:
                        self._candidate_persist_queue.put_nowait(
                            {"action": "expire", "candidate": w})
                    except asyncio.QueueFull:
                        pass
                self.metric_active_candidates = len(self.active_candidates)
                self.metric_persist_queue_depth = self._candidate_persist_queue.qsize()

                if len(self.active_candidates) == 0 and self._state == "ACTIVE":
                    asyncio.ensure_future(self._close_stream(reason="zero_candidates"))
            except Exception as e:
                _log(f"[ProgramWatcher] expire_loop error: {e}")

    async def _persist_loop(self) -> None:
        """Runs every 2s: drain the candidate persist queue into a batch DB write."""
        while not _STOP:
            await asyncio.sleep(2)
            if self._candidate_persist_queue.empty():
                continue
            batch = []
            while not self._candidate_persist_queue.empty():
                try:
                    batch.append(self._candidate_persist_queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            if not batch:
                continue
            try:
                from src.utils.db_locking import db_connect as _dbc
                await _ato_thread(_persist_batch, batch)
            except Exception as e:
                _log(f"[ProgramWatcher] persist_loop error: {e}")
            self.metric_persist_queue_depth = self._candidate_persist_queue.qsize()

    async def _drain_timer(self) -> None:
        """Wait PROGRAM_DRAIN_GRACE_S, then if still zero candidates: unsubscribe + CLOSED."""
        await asyncio.sleep(PROGRAM_DRAIN_GRACE_S)
        if len(self.active_candidates) == 0 and self._state == "DRAINING":
            await self._close_stream(reason="drain_timer")

    async def _close_stream(self, reason: str = "close") -> None:
        """Unsubscribe the pump.fun program stream and mark ProgramWatcher closed."""
        if self._state == "CLOSED" and self._sub_id is None:
            self.metric_stream_state = "CLOSED"
            return
        sub_id = self._sub_id
        ws = self._ws
        if sub_id is not None and ws is not None:
            try:
                req_id = self._next_req_id
                self._next_req_id += 1
                await ws.send(json.dumps({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "method": "logsUnsubscribe",
                    "params": [sub_id],
                }))
                _log(f"[ProgramWatcher] logsUnsubscribe sent sub_id={sub_id} reason={reason}")
            except Exception as e:
                _log(f"[ProgramWatcher] logsUnsubscribe failed sub_id={sub_id} reason={reason}: {e}")
        else:
            _log(f"[ProgramWatcher] closing stream reason={reason} sub_id={sub_id}")

        self._state = "CLOSED"
        self.metric_stream_state = "CLOSED"
        self._sub_id = None
        self._ws = None
        self.metric_program_closes += 1
        if self._stream_opened_at:
            self.metric_program_open_seconds += time.time() - self._stream_opened_at
            self._stream_opened_at = 0.0

    def get_metrics(self) -> dict:
        current_open_s = (time.time() - self._stream_opened_at) if self._stream_opened_at else 0.0
        return {
            "pw_stream_state":        self.metric_stream_state,
            "pw_active_candidates":   self.metric_active_candidates,
            "pw_persist_queue_depth": self.metric_persist_queue_depth,
            "pw_fetch_queue":         self.metric_create_fetch_queue_depth,
            "pw_fetch_dropped":       self.metric_create_fetch_dropped,
            "pw_fetch_timeout":       self.metric_create_fetch_timeout,
            "pw_active_catchup_runs": self.metric_active_catchup_runs,
            "pw_active_catchup_checked": self.metric_active_catchup_checked,
            "pw_active_catchup_hits":  self.metric_active_catchup_hits,
            "pw_active_catchup_errors": self.metric_active_catchup_errors,
            "pw_expire_probe_runs":   self.metric_expire_probe_runs,
            "pw_expire_probe_hits":   self.metric_expire_probe_hits,
            "pw_expire_probe_errors": self.metric_expire_probe_errors,
            "pw_matches":             self.metric_program_matches,
            "pw_replay_buffer_size":   len(self._create_replay_by_sig),
            "pw_replay_stored":        self.metric_create_replay_stored,
            "pw_replay_hits":          self.metric_create_replay_hits,
            "pw_replay_evicted":       self.metric_create_replay_evicted,
            "pw_pending_fetch_depth":  len(self._pending_create_sigs),
            "pw_pending_fetch_queued": self.metric_pending_create_queued,
            "pw_pending_fetch_resolved": self.metric_pending_create_resolved,
            "pw_pending_fetch_dropped": self.metric_pending_create_dropped,
            "pw_candidates_expired":  self.metric_candidates_expired,
            "pw_opens":               self.metric_program_opens,
            "pw_closes":              self.metric_program_closes,
            "pw_open_seconds_total":  round(self.metric_program_open_seconds + current_open_s, 1),
            "pw_current_open_s":      round(current_open_s, 1),
        }


def _persist_batch(batch: list) -> None:
    """Blocking: write a batch of candidate insert/expire records. Called via _ato_thread."""
    try:
        from src.utils.db_locking import db_connect as _dbc
        conn = _dbc(OPS_DB_PATH, timeout=30)
        try:
            from src.core import ws_cascade_store as _st
            _st.batch_upsert_candidates(conn, batch)
        finally:
            conn.close()
    except Exception as e:
        _log(f"[ProgramWatcher] _persist_batch failed: {e}")


# ── subscription manager ─────────────────────────────────────────────────────
class SubscriptionManager:
    """Tracks wallet ↔ helius subscription_id on one WS connection. logsSubscribe per wallet.
    pending_req maps the JSON-RPC request id → wallet until the subscription is confirmed."""

    def __init__(self):
        self.ws = None
        self.next_req = 1
        self.pending_req = {}          # req_id -> (wallet, kind, queued_at, state, priority, queue_pos, sent_at)
        self.wallet_sub = {}           # wallet -> subscription_id
        self.sub_wallet = {}           # subscription_id -> (wallet, kind)
        self.wallet_kind = {}          # wallet -> kind
        # Instrumentation: ACK latency ring buffer (last 200 confirmations)
        self._ack_latencies: list = []     # [(latency_ms, priority, kind), ...]
        self._ack_ring_size = 200
        self._reconnect_gen = 0            # increments on every resync_subscriptions call
        self._queue_pos_counter = 0        # monotonic queue position within a reconnect gen
        self._subs_confirmed_total = 0
        self._subs_sent_total = 0
        self._cold_retry_count = {}    # wallet -> number of stale-drop resubscribe attempts
        # X24.8 — per-kind lifetime counters (durable across ring-buffer eviction), to
        # distinguish "this whole tier never acks" from "this one wallet never acks".
        self._sent_by_kind: dict = {}
        self._confirmed_by_kind: dict = {}
        self._exhausted_by_kind: dict = {}
        # X24.9 — invalid subscription targets rejected at the subscribe() chokepoint,
        # before ever reaching pending_req/wallet_kind/Helius. Lifetime, per-kind.
        self._invalid_rejected_by_kind: dict = {}
        self._invalid_rejected_total = 0
        # P0 timing ring buffer (last 50 P0 events)
        # each entry: {wallet, requested_at, sent_at, ack_at, send_delay_ms, ack_latency_ms, gen}
        self._p0_events: list = []
        self._p0_ring_size = 50

    async def subscribe(self, wallet, kind, priority: int = SUB_PRIORITY_OTHER,
                        requested_at: float | None = None):
        # X24.9 — reject invalid pubkeys before any state is touched: no pending_req
        # entry, no wallet_kind claim, no retry cycle, no Helius call. This is the
        # single chokepoint every subscription source (treasury, session subprov,
        # promoted subprov, dust marker, CDC) funnels through, so validating here
        # covers all of them without duplicating the check at each call site.
        if not is_valid_pubkey(wallet):
            self._invalid_rejected_total += 1
            self._invalid_rejected_by_kind[kind] = self._invalid_rejected_by_kind.get(kind, 0) + 1
            _log(f"⛔ REJECTED invalid subscription target kind={kind} "
                 f"reason={invalid_reason(wallet)} wallet={str(wallet)[:20]}…")
            return
        if wallet in self.wallet_sub or wallet in self.wallet_kind:
            return                      # already (being) subscribed
        rid = self.next_req; self.next_req += 1
        queued_at = requested_at or time.time()
        self._queue_pos_counter += 1
        self._subs_sent_total += 1
        self._sent_by_kind[kind] = self._sent_by_kind.get(kind, 0) + 1
        self.wallet_kind[wallet] = kind
        # X24.1 — mechanism-aware primitive selection. Both "treasury" and
        # "subprov_account" (a PLAIN_TRANSFER-funded sub-provisioner) move SOL via
        # plain system::transfer, which emits no program logs that logsSubscribe's
        # `mentions` filter can match — so BOTH need accountSubscribe (balance-change
        # notifications), not just the treasury tier. Every other kind (subprov,
        # hot_subprov, candidate, cdc, dust) is only ever opened for a WSOL_WRAP_CLOSE
        # / SEEDED_ACCOUNT_CLOSE funding chain, whose terminal instruction is
        # spl-token::closeAccount — a real program instruction that DOES emit a
        # matching log, so logsSubscribe remains correct for them.
        if kind in ("treasury", "subprov_account"):
            # Treasury stays at 'confirmed' — provisioning isn't sub-second-critical and a
            # treasury balance read at 'processed' has higher reorg exposure. subprov_account
            # uses the same commitment for the same reason: this tier exists specifically to
            # catch capital movement, not to race the CREATE itself (that's still logsSubscribe
            # on the resulting candidate once a wrap-close/seeded-close is later observed).
            msg = {"jsonrpc": "2.0", "id": rid, "method": "accountSubscribe",
                   "params": [wallet, {"commitment": "confirmed", "encoding": "jsonParsed"}]}
        else:
            # SUBPROV + CANDIDATE → 'processed' commitment. 'confirmed' lags the chain tip by ~13s,
            # which is most of the create→detection latency on an INSTANT (1s) launch — by the time
            # a confirmed wrap-close/CREATE notification arrives the token has already moved. A
            # pump.fun CREATE / wrap-close essentially never reorgs, and we record the sig either
            # way, so 'processed' (~sub-second, near tip) is the right tradeoff for real-time catch.
            _commit = os.environ.get("WS_LOGS_COMMITMENT", "processed")
            msg = {"jsonrpc": "2.0", "id": rid, "method": "logsSubscribe",
                   "params": [{"mentions": [wallet]}, {"commitment": _commit}]}
        # Actually send — ws.send() resolves when the message is flushed to the socket buffer.
        # sent_at is recorded AFTER send() to capture any send-buffer backpressure delay.
        if self.ws is None:
            # No WS connection yet; drop silently — resync_subscriptions will re-send on connect.
            self.wallet_kind.pop(wallet, None)
            return
        try:
            await self.ws.send(json.dumps(msg))
        except Exception as _e:
            _log(f"⚠ subscribe send failed for {wallet[:12]}…: {_e}")
            self.wallet_kind.pop(wallet, None)
            return
        sent_at = time.time()
        send_delay_ms = round((sent_at - queued_at) * 1000, 1)
        # pending_req: (wallet, kind, queued_at, state, priority, queue_pos, sent_at, reconnect_gen)
        self.pending_req[rid] = (wallet, kind, queued_at, "SENT", priority,
                                 self._queue_pos_counter, sent_at, self._reconnect_gen)
        if priority == SUB_PRIORITY_LIVE_ARMED:
            # Record P0 event in ring buffer for dashboard
            self._p0_events.append({
                "wallet":         wallet[:12] + "…",
                "requested_at":   queued_at,
                "sent_at":        sent_at,
                "ack_at":         None,
                "send_delay_ms":  send_delay_ms,
                "ack_latency_ms": None,
                "gen":            self._reconnect_gen,
                "_rid":           rid,
            })
            if len(self._p0_events) > self._p0_ring_size:
                self._p0_events.pop(0)
            delay_flag = " ⚠️ SLOW SEND" if send_delay_ms > 100 else ""
            _log(f"📡 P0 subscribe sent {wallet[:12]}… send_delay={send_delay_ms}ms "
                 f"q_pos={self._queue_pos_counter} gen={self._reconnect_gen} "
                 f"pending={len(self.pending_req)}{delay_flag}")

    def sweep_stale_pending(self, max_age=90):
        """Clear pending subscribe requests that never confirmed (e.g. an invalid pubkey
        Helius silently rejects). Frees wallet_kind so a future valid retry can proceed and
        avoids a permanent leak.

        Priority-aware: HOT_SUBPROV is considered stale after HOT_SUB_STALE_SEC (2s).
        Cold kinds (subprov/candidate/treasury) are dropped after COLD_SUB_STALE_SEC.
        Returns (dropped, stale_hot) where dropped is a list of (wallet, kind, retries_left)
        so callers can resubscribe cold drops (X27.7 — previously cold drops had no retry
        at all, which combined with ack latency exceeding the old 10s timeout meant a
        subprov could permanently lose live coverage after a single slow ack)."""
        now = time.time()
        dropped = []
        stale_hot = []
        for rid, ent in list(self.pending_req.items()):
            wallet, kind, ts = ent[0], ent[1], ent[2]
            age = now - ts
            if kind == "hot_subprov":
                if age > HOT_SUB_STALE_SEC:
                    stale_hot.append(wallet)
                    self.pending_req.pop(rid, None)
                    self.wallet_kind.pop(wallet, None)
            else:
                if age > COLD_SUB_STALE_SEC:
                    self.pending_req.pop(rid, None)
                    self.wallet_kind.pop(wallet, None)
                    attempts = self._cold_retry_count.get(wallet, 0)
                    dropped.append((wallet, kind, attempts))
        return dropped, stale_hot

    def pending_count_by_kind(self):
        """Return {kind: count} for all pending (unconfirmed) subscribe requests."""
        counts: dict = {}
        for ent in self.pending_req.values():
            k = ent[1]
            counts[k] = counts.get(k, 0) + 1
        return counts

    def sub_kind_breakdown(self) -> dict:
        """X24.8 — per-kind lifetime sent/confirmed/exhausted, to distinguish a whole
        subscription tier never acking (e.g. every 'dust' send exhausts) from an
        isolated wallet failing within an otherwise-healthy tier. Read-only,
        no effect on subscribe/retry behaviour."""
        kinds = set(self._sent_by_kind) | set(self._confirmed_by_kind) | set(self._exhausted_by_kind)
        return {
            k: {
                "sent": self._sent_by_kind.get(k, 0),
                "confirmed": self._confirmed_by_kind.get(k, 0),
                "exhausted": self._exhausted_by_kind.get(k, 0),
            }
            for k in sorted(kinds)
        }

    async def unsubscribe(self, wallet):
        sub_id = self.wallet_sub.pop(wallet, None)
        kind = self.wallet_kind.pop(wallet, None)
        if sub_id is not None:
            self.sub_wallet.pop(sub_id, None)
            # X24.1 — the unsubscribe RPC method must match how the subscription was
            # opened: accountSubscribe → accountUnsubscribe, logsSubscribe →
            # logsUnsubscribe. Pre-existing bug found while adding "subprov_account":
            # this always sent logsUnsubscribe, which is also wrong for the existing
            # "treasury" kind (accountSubscribe) — fixed for both here, since Helius
            # silently no-ops an unsubscribe call for the wrong method, leaking the
            # server-side subscription slot indefinitely otherwise.
            method = "accountUnsubscribe" if kind in ("treasury", "subprov_account") else "logsUnsubscribe"
            try:
                rid = self.next_req; self.next_req += 1
                await self.ws.send(json.dumps(
                    {"jsonrpc": "2.0", "id": rid, "method": method, "params": [sub_id]}))
            except Exception:
                pass

    def on_subscribe_confirmed(self, rid, sub_id):
        ent = self.pending_req.pop(rid, None)
        if not ent:
            return None
        wallet, kind = ent[0], ent[1]
        queued_at = ent[2]
        sent_at = ent[6] if len(ent) > 6 else ent[2]
        priority = ent[4] if len(ent) > 4 else SUB_PRIORITY_OTHER
        queue_pos = ent[5] if len(ent) > 5 else 0
        reconnect_gen = ent[7] if len(ent) > 7 else 0
        latency_ms = round((time.time() - sent_at) * 1000, 1)
        # Record ACK latency in ring buffer
        self._ack_latencies.append((latency_ms, priority, kind))
        if len(self._ack_latencies) > self._ack_ring_size:
            self._ack_latencies.pop(0)
        self._subs_confirmed_total += 1
        self._confirmed_by_kind[kind] = self._confirmed_by_kind.get(kind, 0) + 1
        self._cold_retry_count.pop(wallet, None)
        # hot_subprov is promoted to subprov on confirmation — routing logic uses "subprov"
        resolved_kind = "subprov" if kind == "hot_subprov" else kind
        self.wallet_sub[wallet] = sub_id
        self.sub_wallet[sub_id] = (wallet, resolved_kind)
        self.wallet_kind[wallet] = resolved_kind
        if kind == "hot_subprov":
            _log(f"🔥 HOT subscribe confirmed {wallet[:12]}… sub_id={sub_id} ack={latency_ms}ms")
        elif priority == SUB_PRIORITY_LIVE_ARMED:
            # Fill in ack timing on the P0 ring buffer entry
            ack_now = time.time()
            for ev in reversed(self._p0_events):
                if ev.get("_rid") == rid:
                    ev["ack_at"] = ack_now
                    ev["ack_latency_ms"] = round((ack_now - ev["sent_at"]) * 1000, 1)
                    break
            ack_flag = " ⚠️ SLOW ACK" if latency_ms > 500 else ""
            _log(f"✅ P0 LIVE_ARMED confirmed {wallet[:12]}… sub_id={sub_id} "
                 f"send_delay={round((sent_at - queued_at)*1000,1)}ms ack={latency_ms}ms "
                 f"q_pos={queue_pos} gen={reconnect_gen} "
                 f"pending_remaining={len(self.pending_req)}{ack_flag}")
        return wallet, resolved_kind

    def ack_latency_stats(self) -> dict:
        """Return ACK latency stats over the ring buffer, broken down by priority."""
        out: dict = {
            "subs_sent_total": self._subs_sent_total,
            "subs_conf_total": self._subs_confirmed_total,
        }
        if self._ack_latencies:
            lats = sorted(x[0] for x in self._ack_latencies)
            n = len(lats)
            def _p95(lst): return lst[int(len(lst) * 0.95)] if lst else None
            out.update({
                "ack_count":   n,
                "avg_ack_ms":  round(sum(lats) / n, 1),
                "p95_ack_ms":  _p95(lats),
                "max_ack_ms":  lats[-1],
            })
        # P0-specific: send_delay + ack_latency (separate dimensions)
        p0_confirmed = [ev for ev in self._p0_events if ev.get("ack_at") is not None]
        p0_send_delays = sorted(ev["send_delay_ms"] for ev in self._p0_events)
        p0_ack_lats = sorted(ev["ack_latency_ms"] for ev in p0_confirmed)
        if p0_send_delays:
            out.update({
                "p0_count":            len(self._p0_events),
                "p0_avg_send_delay_ms": round(sum(p0_send_delays)/len(p0_send_delays), 1),
                "p0_max_send_delay_ms": p0_send_delays[-1],
                "p0_p95_send_delay_ms": p0_send_delays[int(len(p0_send_delays)*0.95)],
            })
        if p0_ack_lats:
            out.update({
                "p0_avg_ack_ms": round(sum(p0_ack_lats)/len(p0_ack_lats), 1),
                "p0_max_ack_ms": p0_ack_lats[-1],
                "p0_p95_ack_ms": p0_ack_lats[int(len(p0_ack_lats)*0.95)],
            })
        # Last 5 P0 events for the timeline panel
        out["p0_recent"] = [
            {k: v for k, v in ev.items() if not k.startswith("_")}
            for ev in self._p0_events[-5:]
        ]
        return out

    def lookup(self, sub_id):
        return self.sub_wallet.get(sub_id)

    def reset(self):
        self.pending_req.clear(); self.wallet_sub.clear()
        self.sub_wallet.clear(); self.wallet_kind.clear()
        self._reconnect_gen += 1
        self._queue_pos_counter = 0


# ── core cascade ─────────────────────────────────────────────────────────────
class Cascade:
    def __init__(self):
        self.mgr = SubscriptionManager()
        self._loop: asyncio.AbstractEventLoop | None = None  # set by run_cascade()
        # idempotency: (candidate, sig) already processed — guards against the WS notification
        # and the catch-up scan handling the same CREATE/SWAP twice. record_launch is also
        # INSERT OR IGNORE on (creator, create_sig), so the LEDGER is idempotent regardless;
        # this set additionally suppresses duplicate events + teardown. Bounded by eviction.
        self._processed = set()
        # subprov sigs already scanned by the subprov catch-up — avoids re-fetching the same
        # wrap-close txs every sweep. (open_candidate_watch is also INSERT OR IGNORE, so even a
        # re-scan can't double-open a candidate; this just saves the RPC.)
        self._subprov_seen = set()
        # POST_CREATE_ACTIVE tracking: maps subprov_wallet → unix timestamp of last observed
        # fan-out event. The _post_create_watchdog reads this to decide whether to extend the
        # 120s armed window. Updated whenever a new wrap-close is detected for a subprov that
        # is in POST_CREATE_ACTIVE monitoring state.
        self._post_create_last_fanout: dict[str, float] = {}

        # ── Wallet profile cache ──────────────────────────────────────────────
        # Single dict lookup replaces 1–4 DB queries on every treasury outbound.
        # Built at startup; refreshed periodically off-thread; incrementally
        # updated at the same callsites that already write to the underlying tables.
        self._wallet_profile: dict[str, str] = _build_wallet_profile()
        self._profile_last_refresh: float = time.time()
        # Metrics (lifetime totals since process start)
        self._profile_hits:   int = 0   # wallet found in profile
        self._profile_misses: int = 0   # wallet not in profile (→ NEW_SUBPROV path)
        self._classify_counts: dict[str, int] = {}  # classification → count
        self._subprov_sig_metrics: dict[str, int] = {
            "subprov_ws_sig_seen": 0,
            "subprov_sig_processed": 0,
            "subprov_sig_failed": 0,
            "subprov_sig_retry_enqueued": 0,
            "subprov_sig_catchup_recovered": 0,
            "subprov_sig_gap_detected": 0,
            "subprov_gettx_none_count": 0,
            "subprov_fast_retry_attempts": 0,
            "subprov_fast_retry_success": 0,
            "subprov_fast_retry_fallback": 0,
        }
        # X24.2.1 Phase 3 — sweep-overlap guard. subprov_sweep_pass() is now run
        # as its own task decoupled from _maintenance()'s sequential loop (so a
        # slow sweep no longer blocks resync_subscriptions/cleanup_pass/etc.).
        # This flag prevents a second sweep from starting while one is still
        # running — an overlapping sweep would risk selecting and concurrently
        # inspecting the SAME session twice (a real correctness risk, not just
        # a performance one), since fair_sweep_candidates() would return
        # unswept/least-recently-swept rows without knowing another cycle
        # already claimed them.
        self._sweep_in_progress: bool = False
        self._sweep_skipped_overlap_count: int = 0
        # Ensure the cascade schema ONCE at startup — NOT on every _ops() call. The schema
        # ensure is a WRITE (CREATE TABLE/INDEX); running it on the hot _ops() path (called
        # from resync_subscriptions + cleanup on the async WS loop) blocked the event loop
        # under write contention, so subscription-confirmation acks were never processed and
        # ALL subscriptions were reaped as "never-confirmed" (419 dropped / 0 confirmed). One
        # short startup write fixes it; _ops() is now a pure read-path connection.
        try:
            store.operations_write("ws-cascade-schema-startup", store.ensure_cascade_schema)
        except Exception as _e:
            _log(f"⚠ startup schema ensure failed (will retry lazily): {_e}")

        # X24.9 Phase 3 — startup integrity audit over every subscription source.
        # Read-only; reports totals (never silently drops a failure) via the
        # existing heartbeat rather than a new dashboard.
        self._startup_validation: dict = {}
        try:
            from src.ops.subscription_target_audit import startup_validation_summary
            _conn = self._ops()
            try:
                self._startup_validation = startup_validation_summary(_conn)
            finally:
                _conn.close()
            _sv = self._startup_validation
            _log(f"[X24.9] startup validation — valid={_sv['total_valid']} "
                 f"invalid={_sv['total_invalid']} duplicates={_sv['total_duplicates']} "
                 f"disabled={_sv['total_disabled']} by_source={_sv['invalid_by_source']}")
        except Exception as _e:
            _log(f"⚠ startup validation audit failed (non-fatal): {_e}")

    def _seen(self, candidate, sig):
        key = (candidate, sig)
        if key in self._processed:
            return True
        self._processed.add(key)
        if len(self._processed) > 5000:                # bound memory; evict oldest-ish
            for k in list(self._processed)[:1000]:
                self._processed.discard(k)
        return False

    def _subprov_sig_seen(self, subprov, sig):
        key = (subprov, sig)
        if key in self._subprov_seen:
            return True
        self._subprov_seen.add(key)
        if len(self._subprov_seen) > 5000:
            for k in list(self._subprov_seen)[:1000]:
                self._subprov_seen.discard(k)
        return False

    def _metric(self, name: str, inc: int = 1) -> None:
        self._subprov_sig_metrics[name] = self._subprov_sig_metrics.get(name, 0) + inc

    def _record_subprov_sig_dedupe(self, subprov: str, original_done_at, observed_at: float,
                                   source: str) -> None:
        """X64.9B1 — best-effort durable write of one duplicate observation
        AND the total_checked denominator increment, in one short-lived
        connection (opened and closed here, never reusing the caller's
        already-open `conn`) specifically to avoid any risk of a
        nested-write-lane conflict (see
        src.core.database_write_service.NestedDatabaseWriteError, observed
        elsewhere in this project's operational history for exactly this
        class of bug). Any failure here is swallowed — observability must
        never be able to disable or delay the existing dedup skip, which has
        already happened by the time this is called."""
        try:
            age_s = max(0, int(observed_at) - int(original_done_at or observed_at))
            dconn = self._ops()
            try:
                store.record_subprov_sig_checked(dconn, observed_at=int(observed_at))
                store.record_subprov_sig_duplicate(
                    dconn, subprov=subprov, age_s=age_s, source=source,
                    observed_at=int(observed_at))
            finally:
                dconn.close()
        except Exception as e:
            _log(f"⚠ X64.9B1 dedupe-stats write failed (non-fatal, dedup unaffected): {e}")

    def _record_subprov_sig_checked_only(self, observed_at: float) -> None:
        """X64.9B1 — best-effort durable increment of the total_checked
        denominator for the non-duplicate path (the duplicate path records
        this together with the duplicate itself, above, to save a connection
        open). Same failure discipline: never allowed to affect processing."""
        try:
            dconn = self._ops()
            try:
                store.record_subprov_sig_checked(dconn, observed_at=int(observed_at))
            finally:
                dconn.close()
        except Exception as e:
            _log(f"⚠ X64.9B1 checked-counter write failed (non-fatal): {e}")

    def _process_subprov_sig_durable(self, subprov: str, sig: str, *,
                                     slot: Optional[int] = None,
                                     source: str = "WS",
                                     advance_cursor: bool = True,
                                     prefetched_tx: Optional[tuple] = None) -> list:
        """Durably process one subprov signature through the existing handler.

        The retry row is written before getTransaction/parser/DB fanout work, so
        a process restart, RPC timeout, or DB lock cannot erase the fact that the
        signature was seen.

        advance_cursor: True (default, preserves all prior behaviour) — on
        success, marks the retry row DONE AND advances the durable cursor to
        this signature, matching the WS live-path contract of processing
        signatures one at a time in arrival order. Pass False when the caller
        processes a BATCH out of chronological order (X24.7's alternating
        signature-priority policy inside catch_up_subprov) — in that case the
        retry row is still marked DONE here (safe in any order, keyed by its
        own signature), but the cursor is NOT touched; the caller is
        responsible for advancing it exactly once, to the newest successfully
        processed signature in the batch, after the whole batch completes.

        prefetched_tx (X65.29): an optional (tx, tx_retry_info) tuple already
        fetched by the caller (catch_up_subprov's bounded-concurrency RPC
        prefetch stage) — passed straight through to _handle_subprov_tx so
        the RPC round-trip is not repeated. None (every pre-existing call
        site) preserves the exact prior behaviour of fetching inline.
        """
        if not subprov or not sig:
            return []
        # X24.2.2 — cut per-signature DB round-trips from 4 to 2. Each _ops() write
        # blocks on the single process-wide DB_WRITE_SERIALIZE lock
        # (src/utils/db_locking.py TrackedConnection._acquire_write_lane), shared by
        # every writer in the whole process. X24.2.1 Phase 3 measurement showed
        # RPC/network cost alone (~150-260ms/sig, isolated and under simulated
        # concurrency) does not explain the 1,100-2,600ms/sig observed live —
        # pointing at this lock as the amplified cost under concurrent sweep
        # sessions. subprov_sig_enqueue() (write to PENDING) immediately followed by
        # subprov_sig_mark_running() (write to RUNNING) wrote the SAME row twice in
        # two separate lock acquisitions; subprov_sig_enqueue_running() combines them
        # into one write with no change to crash-safety (see its docstring).
        _sig_t0 = time.time()
        notification_seen_at = time.time()
        conn = self._ops()
        try:
            _dedupe_t0 = time.time()
            row = conn.execute(
                "SELECT status, last_attempt_at FROM wt_subprov_sig_retry "
                "WHERE subprov_wallet=? AND signature=?",
                (subprov, sig)).fetchone()
            if row and row[0] == "DONE":
                # X24.2.3 Phase 3 — live counter for the redundant-work question:
                # how often does the durable dedupe boundary actually short-circuit
                # already-processed work, vs. every fetched signature being genuinely
                # new (as offline sampling found: 0/48 across 8 live subprovs).
                self._metric("subprov_sig_already_done_skipped")
                # X64.9B1 — durable redelivery measurement, entirely additive and
                # best-effort: must NEVER affect the skip decision above (already
                # returned in-memory data by this point) or block/slow it down.
                # See docs/design/x64_9/x64_9b1_observability_design.md.
                self._record_subprov_sig_dedupe(subprov, row[1], notification_seen_at, source)
                return []
            _dedupe_lookup_ms = round((time.time() - _dedupe_t0) * 1000, 1)
            _enqueue_t0 = time.time()
            is_new, first_seen_at = store.subprov_sig_enqueue_running(
                conn, subprov=subprov, signature=sig, slot=slot)
            _enqueue_ms = round((time.time() - _enqueue_t0) * 1000, 1)
            if is_new:
                self._metric("subprov_sig_retry_enqueued")
        finally:
            conn.close()

        # X64.9B1 — non-duplicate path: record the total_checked denominator only
        # (no duplicate to log). Fired after the dedupe-critical connection is
        # already closed, so this can never contend with or delay it.
        self._record_subprov_sig_checked_only(notification_seen_at)

        try:
            _handle_tx_t0 = time.time()
            result = self._handle_subprov_tx(subprov, sig, seen_at=first_seen_at,
                                             prefetched=prefetched_tx)
            _handle_tx_ms = round((time.time() - _handle_tx_t0) * 1000, 1)

            _mark_done_t0 = time.time()
            conn = self._ops()
            try:
                if advance_cursor:
                    row = conn.execute(
                        "SELECT wrap_close_time FROM wt_candidate_websocket_watches "
                        "WHERE subprov_wallet=? AND wrap_close_signature=? "
                        "ORDER BY detected_at DESC LIMIT 1",
                        (subprov, sig)).fetchone()
                    block_time = row[0] if row else None
                    store.subprov_sig_mark_done(
                        conn, subprov=subprov, signature=sig, slot=slot, block_time=block_time)
                else:
                    # X24.7 — batch/reordered path: mark this signature's retry row
                    # DONE only. The caller (catch_up_subprov) advances the cursor
                    # once, after the whole batch, to the newest successfully
                    # processed signature — never per-signature here, since under
                    # alternating order the "last call in the loop" is NOT
                    # guaranteed to be the newest signature (verified: for any
                    # batch size, the alternating sequence's final index is always
                    # somewhere in the middle of the range, never the newest end).
                    store.subprov_sig_mark_retry_done(conn, subprov=subprov, signature=sig)
            finally:
                conn.close()
            _mark_done_ms = round((time.time() - _mark_done_t0) * 1000, 1)
            _sig_total_ms = round((time.time() - _sig_t0) * 1000, 1)
            self._last_sig_stage_timing = {
                "subprov": subprov, "sig": sig, "dedupe_lookup_ms": _dedupe_lookup_ms,
                "enqueue_running_ms": _enqueue_ms, "handle_tx_ms": _handle_tx_ms,
                "mark_done_ms": _mark_done_ms, "total_ms": _sig_total_ms,
            }
            if _sig_total_ms > 500:  # only log the expensive ones to avoid flooding
                _log(f"⏲ sig_stage_timing {subprov[:12]}… dedupe={_dedupe_lookup_ms}ms "
                     f"enqueue_running={_enqueue_ms}ms "
                     f"handle_tx={_handle_tx_ms}ms mark_done={_mark_done_ms}ms "
                     f"total={_sig_total_ms}ms")
            self._metric("subprov_sig_processed")
            if source == "CATCHUP":
                self._metric("subprov_sig_catchup_recovered")
            return result or []
        except Exception as exc:
            conn = self._ops()
            try:
                store.subprov_sig_mark_failed(
                    conn, subprov=subprov, signature=sig, error=repr(exc),
                    max_attempts=SUBPROV_SIG_MAX_ATTEMPTS)
            finally:
                conn.close()
            self._metric("subprov_sig_failed")
            # Distinguish transient RPC propagation races (tx not yet visible) from real failures.
            # getTransaction=None on first attempt is normal — the durable retry loop recovers these
            # within seconds. Only warn once retries are exhausted.
            _is_rpc_race = "getTransaction returned None" in str(exc)
            conn2 = self._ops()
            try:
                _attempts = (conn2.execute(
                    "SELECT attempts FROM wt_subprov_sig_retry "
                    "WHERE subprov_wallet=? AND signature=?",
                    (subprov, sig)).fetchone() or [0])[0]
            finally:
                conn2.close()
            _exhausted = _attempts >= SUBPROV_SIG_MAX_ATTEMPTS
            if _exhausted:
                _log(f"⚠ subprov sig EXHAUSTED {subprov[:12]}… sig={sig[:12]}… attempts={_attempts} source={source}: {exc}")
            elif _is_rpc_race:
                _log(f"⏳ subprov sig pending RPC availability {subprov[:12]}… sig={sig[:12]}… queued for retry (attempt {_attempts})")
            else:
                _log(f"⚠ subprov sig failed {subprov[:12]}… sig={sig[:12]}… source={source}: {exc}")
            raise

    def _ops(self):
        # HOT PATH — pure connection, NO schema write. Schema is ensured once in __init__.
        # (Running ensure_cascade_schema here blocked the async WS loop under contention and
        # killed subscription confirmation — see __init__.)
        c = db_connect(OPS_DB_PATH, timeout=60)
        c.execute("PRAGMA busy_timeout=60000")
        return c

    # ---- (re)build subscriptions from DB (startup + reconnect) --------------
    async def resync_subscriptions(self):
        loop = asyncio.get_event_loop()

        def _db_load():
            conn = self._ops()
            try:
                t = _confirmed_treasuries(conn)
                for tr in t:
                    store.treasury_ws_register(conn, tr)
                s = store.active_sessions(conn)[:MAX_ACTIVE_SUBPROVS]
                c = []  # candidates no longer individually subscribed — reloaded via _recent_candidates
                p = _promotable_subprovs(conn) if WS_PROMOTE_DISCOVERED else []
                cdc = store.get_subscribed_cdcs(conn)  # rehydrate WS after restart
                return t, s, c, p, cdc
            finally:
                conn.close()

        treasuries, sessions, candidates, promoted, subscribed_cdcs = await loop.run_in_executor(None, _db_load)
        self._treasuries = treasuries     # cache for the mesh-skip gate in _handle_treasury_tx

        # Reload recent WATCHING candidates into ProgramWatcher on (re)connect.
        # Scoped to wrap_close_time within CANDIDATE_TTL_SEC — avoids loading 1M+ historical rows.
        prog_watcher = getattr(self, "_prog_watcher", None)
        if prog_watcher:
            def _recent_candidates():
                cutoff = int(time.time()) - CANDIDATE_TTL_SEC
                conn2 = self._ops()
                try:
                    return conn2.execute(
                        "SELECT candidate_wallet, subprov_wallet, treasury_wallet, "
                        "wrap_close_signature, wrap_close_time, funding_amount, expires_at "
                        "FROM wt_candidate_websocket_watches "
                        "WHERE state='WATCHING' AND wrap_close_time > ?", (cutoff,)
                    ).fetchall()
                finally:
                    conn2.close()
            recent = await loop.run_in_executor(None, _recent_candidates)
            if recent:
                now_t = int(time.time())
                metas = [{"candidate": r[0], "subprov": r[1], "treasury": r[2],
                          "wrap_sig": r[3], "wrap_time": r[4], "amount": r[5],
                          "added_at": 0, "expires_at": r[6]}
                         for r in recent
                         if r[6] and r[6] > now_t]   # skip already-expired candidates
                skipped = len(recent) - len(metas)
                if skipped:
                    _log(f"[ProgramWatcher] skipped {skipped} already-expired candidate(s) on (re)connect")
                if metas:
                    prog_watcher.add_candidates(metas, None)
                    _log(f"[ProgramWatcher] reloaded {len(metas)} recent candidate(s) from DB on (re)connect")

        # Startup session cleanup: expire ACTIVE sessions that are clearly orphaned.
        # A session is an orphan if its subprov is not in the resubscription set AND it
        # was opened more than SESSION_TTL_SEC ago (i.e. it would have expired on its own
        # if the cascade had stayed running). Sessions opened recently (< SESSION_TTL_SEC ago)
        # are kept — they may be from a brief reconnect and the subprov is still active.
        def _expire_orphans():
            live_set = {s[1] for s in sessions}
            conn = self._ops()
            try:
                now_t = int(time.time())
                # Only expire orphans older than SESSION_TTL_SEC — recent sessions survive reconnects
                cutoff = now_t - SESSION_TTL_SEC
                rows = conn.execute(
                    "SELECT id, subprov_wallet FROM wt_active_subprov_sessions "
                    "WHERE state='ACTIVE' AND detected_at < ?", (cutoff,)).fetchall()
                expired_n = 0
                for rid, rwallet in rows:
                    if rwallet not in live_set:
                        conn.execute(
                            "UPDATE wt_active_subprov_sessions SET state='EXPIRED', closed_at=? "
                            "WHERE id=?", (now_t, rid))
                        expired_n += 1
                if expired_n:
                    conn.commit()
                    _log(f"startup cleanup: expired {expired_n} orphaned session(s)")
            except Exception as _e:
                _log(f"startup cleanup error: {_e}")
            finally:
                conn.close()
        await loop.run_in_executor(None, _expire_orphans)

        # PRIORITY-ORDERED, RATE-LIMITED RECONNECT REPLAY
        # Task 3: subscribe in strict priority order so the WS reader confirms high-priority
        # wallets first and keeps the pending queue short.
        # Task 4: pace at RECONNECT_SUBSCRIBE_RATE req/s so pending queue stays <5 at steady state.
        # P0 (new LIVE_ARMED) is never rate-limited — it bypasses this path entirely via
        # subscribe_live_armed() called directly from _handle_treasury_tx.
        # Rate limit delay between sends (skip for the very first send to avoid initial latency).
        _rate_delay = 1.0 / RECONNECT_SUBSCRIBE_RATE if RECONNECT_SUBSCRIBE_RATE > 0 else 0
        catchup_tasks = []
        _sent_this_resync = 0

        async def _rate_send(wallet, kind, priority, catchup_kind=None):
            nonlocal _sent_this_resync
            if wallet in self.mgr.wallet_kind:
                return
            if _sent_this_resync > 0 and _rate_delay > 0:
                await asyncio.sleep(_rate_delay)
            await self.mgr.subscribe(wallet, kind, priority=priority)
            _sent_this_resync += 1
            if catchup_kind:
                catchup_tasks.append((catchup_kind, wallet))

        # P1: TREASURY TIER — confirmed-treasury set, permanent subscriptions
        _max_t = int(os.environ.get("WS_MAX_TREASURY_SUBSCRIBE", "0")) or len(treasuries)
        for t in list(treasuries)[:_max_t]:
            await _rate_send(t, "treasury", SUB_PRIORITY_TREASURY)
            if t not in self.mgr.wallet_kind or _sent_this_resync == 1:
                emit_event("TREASURY_WEBSOCKET_OPENED", wallet=t)

        # P2: SESSION SUBPROV TIER — existing LIVE_ARMED sessions (reconnect replay)
        # X24.1 — mechanism-aware kind: a PLAIN_TRANSFER-funded session must resubscribe
        # via accountSubscribe ("subprov_account"), not logsSubscribe ("subprov"), or it
        # would silently lose live detection capability again on every reconnect.
        if SUBPROV_WATCH_ENABLED:
            for s in sessions:
                subprov = s[1]
                monitoring_state = s[9] if len(s) > 9 else "LIVE_ARMED"
                funding_mechanism = s[10] if len(s) > 10 else "WSOL_WRAP_CLOSE"
                if monitoring_state != "LIVE_ARMED":
                    continue   # never subscribe INTEL_ONLY
                _kind = "subprov_account" if funding_mechanism == "PLAIN_TRANSFER" else "subprov"
                await _rate_send(subprov, _kind, SUB_PRIORITY_SESSION, catchup_kind=_kind)
                emit_event("SUBPROV_WEBSOCKET_OPENED", wallet=subprov,
                           payload={"funding_mechanism": funding_mechanism})

        # P3: PROMOTED SUBPROV TIER (standing watchlist, lower priority than sessions)
        if SUBPROV_WATCH_ENABLED:
            for subprov in promoted:
                await _rate_send(subprov, "subprov", SUB_PRIORITY_OTHER, catchup_kind="subprov")
                emit_event("SUBPROV_WEBSOCKET_OPENED", wallet=subprov,
                           payload={"source": "discovered_promotion"})

        # P4: CDC REHYDRATION — re-subscribe CDCs that were SUBSCRIBED before this restart
        for cdc_w in subscribed_cdcs:
            await _rate_send(cdc_w, "cdc", SUB_PRIORITY_OTHER)
            _log(f"🔵 CDC rehydrated {cdc_w[:12]}… (was SUBSCRIBED before restart)")

        # Candidate wallets are NOT subscribed — CREATE detection is via ProgramWatcher (one stream).

        # P5: DUST OBSERVATORY — subscribe known DUST_MARKER wallets (permanent, low-priority).
        # Purely observational: notifications are only enqueued for off-thread processing.
        _dust = getattr(self, "_dust_markers", set())
        for dm in _dust:
            await _rate_send(dm, "dust", SUB_PRIORITY_OTHER)

        _log(f"resync complete: sent={_sent_this_resync} gen={self.mgr._reconnect_gen} "
             f"pending={len(self.mgr.pending_req)} rate={RECONNECT_SUBSCRIBE_RATE}/s")

        # Schedule catch-ups as fire-and-forget tasks — they do RPC and must not block the
        # reader. A small initial delay lets the subscription confirmations arrive first.
        async def _deferred_catchups():
            await asyncio.sleep(30)  # let all subscription confirmations arrive before scanning
            for kind, wallet in catchup_tasks:
                # X24.1 — "subprov_account" (PLAIN_TRANSFER, accountSubscribe) reuses the
                # exact same signature-history catch-up as "subprov" (logsSubscribe):
                # catch_up_subprov scans getSignaturesForAddress for the subprov wallet,
                # which is identical regardless of which WS primitive is watching it live.
                if kind in ("subprov", "subprov_account"):
                    await self.catch_up_subprov(wallet)
                else:
                    await self.catch_up_candidate(wallet)
        if catchup_tasks:
            asyncio.ensure_future(_deferred_catchups())

    async def subscribe_live_armed(self, wallet: str, funding_mechanism: Optional[str] = None) -> None:
        """P0 subscribe — new LIVE_ARMED subprov, bypasses all rate limiting.
        Called immediately when a session is opened from _handle_treasury_tx so the
        subscription races ahead of any pending reconnect-replay queue.

        X24.1 — mechanism-aware kind selection. A PLAIN_TRANSFER-funded subprov must be
        subscribed via "subprov_account" (accountSubscribe) — logsSubscribe cannot see a
        plain system::transfer. Everything else (WSOL_WRAP_CLOSE, SEEDED_ACCOUNT_CLOSE,
        and any unrecognised/null mechanism) keeps the existing "subprov" kind
        (logsSubscribe), preserving current behaviour exactly for those cases."""
        kind = "subprov_account" if funding_mechanism == "PLAIN_TRANSFER" else "subprov"
        await self.mgr.subscribe(wallet, kind, priority=SUB_PRIORITY_LIVE_ARMED)

    # ---- offline reconciliation: recover treasury events missed during downtime ----
    async def reconcile_pass(self):
        """For each confirmed treasury, scan recent signatures and replay any provisioning
        txs that occurred while the cascade was offline.

        Uses last_notif_sig from wt_treasury_ws_usage as the 'until' cursor so only the
        gap window is scanned — not the full history.  First-boot (last_notif_sig IS NULL)
        uses limit=3 for a safe baseline without becoming a historical scanner.

        Bounded: limit=10 sigs per treasury, 1 RPC credit each.  Runs in thread executor
        so it never blocks the WS reader."""
        loop = asyncio.get_event_loop()

        def _load_cursors():
            conn = self._ops()
            try:
                rows = conn.execute(
                    "SELECT treasury_wallet, last_notif_sig FROM wt_treasury_ws_usage"
                ).fetchall()
                return {r[0]: r[1] for r in rows}
            finally:
                conn.close()

        def _reconcile_treasury(treasury, last_sig):
            """Scan one treasury for missed provisioning txs.  Runs in a worker thread."""
            if last_sig:
                params = [treasury, {"limit": 10, "until": last_sig,
                                     "commitment": "confirmed"}]
            else:
                params = [treasury, {"limit": 3, "commitment": "confirmed"}]
            sigs = _rpc("getSignaturesForAddress", params) or []
            if not sigs:
                return 0
            # Process oldest-first so sessions open in chronological order.
            opened = 0
            for entry in reversed(sigs):
                sig = entry.get("signature") if isinstance(entry, dict) else entry
                if not sig:
                    continue
                try:
                    new_subprovs = self._handle_treasury_tx(treasury, sig)
                    if new_subprovs:
                        opened += len(new_subprovs)
                        _log(f"RECONCILE {treasury[:10]}… → {len(new_subprovs)} missed session(s) "
                             f"from sig {sig[:12]}…")
                except Exception as _e:
                    _log(f"RECONCILE error {treasury[:10]}… sig {sig[:12]}…: {_e}")
            return opened

        cursors = await loop.run_in_executor(None, _load_cursors)
        # Run all treasuries concurrently in the thread pool (each is one RPC + optional tx fetch).
        tasks = [
            loop.run_in_executor(None, _reconcile_treasury, t, cursors.get(t))
            for t in (self._treasuries or set())
        ]
        if not tasks:
            return
        results = await asyncio.gather(*tasks, return_exceptions=True)
        total_opened = sum(r for r in results if isinstance(r, int))
        errors = sum(1 for r in results if isinstance(r, Exception))
        _log(f"RECONCILE complete — {len(tasks)} treasuries, "
             f"{total_opened} missed session(s) recovered"
             + (f", {errors} error(s)" if errors else ""))
        emit_event("RECONCILE_COMPLETE",
                   payload={"treasuries": len(tasks), "sessions_recovered": total_opened})

    # ---- Phase E Pass 1: 6-way recipient classification ---------------------
    def _refresh_wallet_profile_if_due(self):
        """Background-safe: called from the maintenance loop (off event loop via _ato_thread).
        Atomically swaps in a freshly built profile; keeps the old one on any build failure."""
        now = time.time()
        if now - self._profile_last_refresh < WALLET_PROFILE_REFRESH_SEC:
            return
        new_profile = _build_wallet_profile()
        if new_profile:  # keep old on failure (returns {} on exception)
            self._wallet_profile = new_profile
        self._profile_last_refresh = now

    def _profile_set(self, wallet: str, role: str):
        """Incremental update — called alongside existing DB writes so the cache
        stays current between full refreshes. Priority rules: never downgrade."""
        PRIORITY = {"TREASURY": 5, "BUY_SWARM": 4, "SUBPROV": 3, "CREATOR": 2, "HISTORICAL": 1}
        current = self._wallet_profile.get(wallet)
        if current is None or PRIORITY.get(role, 0) > PRIORITY.get(current, 0):
            self._wallet_profile[wallet] = role

    # Gap threshold separating "continuing operation" from "genuine reactivation" (seconds)
    _DORMANCY_THRESHOLD_S: int = int(os.environ.get("WS_DORMANCY_THRESHOLD_S", str(4 * 3600)))

    def _last_operational_activity(self, conn, subprov: str) -> int | None:
        """Return the most-recent operational event timestamp for subprov across all tables.
        Includes: session open, wrap-close fan-out, CREATE, swarm buy.
        Used to compute the dormancy gap for operation continuation logic."""
        row = conn.execute("""
            SELECT MAX(t) FROM (
                SELECT MAX(detected_at)    AS t FROM wt_active_subprov_sessions
                  WHERE subprov_wallet=?
                UNION ALL
                SELECT MAX(fanout_time)    AS t FROM wt_fanout_events
                  WHERE subprov_wallet=?
                UNION ALL
                SELECT MAX(create_time)    AS t FROM wt_watchtower_launches
                  WHERE subprov_wallet=?
                UNION ALL
                SELECT MAX(observed_at)    AS t FROM wt_swarm_buys
                  WHERE subprov_wallet=?
                UNION ALL
                SELECT MAX(wrap_close_time) AS t FROM wt_candidate_websocket_watches
                  WHERE subprov_wallet=?
            )
        """, (subprov, subprov, subprov, subprov, subprov)).fetchone()
        return row[0] if row else None

    def _funding_sequence_number(self, conn, subprov: str) -> int:
        """Return the funding sequence number within the current operation (1-based).
        Counts sessions since the last dormancy break (gap ≥ _DORMANCY_THRESHOLD_S between
        consecutive sessions). Rejected/expired sessions still count as fundings — the capital
        was deployed regardless of whether the pipeline accepted the session."""
        rows = conn.execute(
            "SELECT detected_at FROM wt_active_subprov_sessions "
            "WHERE subprov_wallet=? ORDER BY detected_at ASC", (subprov,)
        ).fetchall()
        if not rows:
            return 1
        # Walk backwards to find the start of the current operation
        times = [r[0] for r in rows]
        op_start_idx = 0
        for i in range(len(times) - 1, 0, -1):
            if times[i] - times[i - 1] >= self._DORMANCY_THRESHOLD_S:
                op_start_idx = i
                break
        # +1 because this call happens BEFORE the new row is inserted
        return len(times) - op_start_idx + 1
        # e.g. 4 existing rows in one operation → next funding = #5

    def _classify_recipient(self, conn, recipient: str, amount_sol: float = 0.0,
                            funding_treasury: str | None = None) -> tuple:
        """Return (classification, subprov_meta) for a treasury outbound recipient.

        Classification labels (operation-centric model):
          TREASURY_MESH              — recipient is a confirmed treasury (mesh capital routing)
          BUY_SWARM_PROVISIONER      — known subprov whose destinations are predominantly swaps
          CONTINUING_OPERATION       — known subprov, last activity < _DORMANCY_THRESHOLD_S ago
                                       (this is another funding tranche within the same operation;
                                        meta includes treasury_rotated=True if treasury changed)
          SUBPROV_REACTIVATED        — known subprov, last activity ≥ _DORMANCY_THRESHOLD_S ago
                                       (genuine dormant→active transition)
          HISTORICAL_SUBPROV_DISCOVERED — wallet operated before WATCHTOWER, no active op context
          NEW_SUBPROV                — fresh wallet, no prior evidence (open_reason=PROVISION_CANDIDATE)

        Replaces KNOWN_SUBPROV_TOPUP and REARMED_SUBPROV_CANDIDATE — both were imprecise.
        The dormancy gap is measured against last *operational* activity (fanout/create/swarm/
        wrap-close/session-open), not merely when the session record expired.

        When ENFORCE=0 (default): all non-mesh recipients still open sessions.
        When ENFORCE=1: BUY_SWARM_PROVISIONER skips the creator pipeline.

        Fast path: consults in-memory _wallet_profile (O(1)) before any DB query.
        """
        role = self._wallet_profile.get(recipient)
        if role:
            self._profile_hits += 1
        else:
            self._profile_misses += 1
        self._classify_counts[role or "NEW_SUBPROV"] = (
            self._classify_counts.get(role or "NEW_SUBPROV", 0) + 1
        )

        # Fast exits — no DB needed
        if role == "TREASURY":
            return "TREASURY_MESH", {}
        if role == "NON_PROVISIONING":
            return "NON_PROVISIONING_RECIPIENT", {}
        if role == "CREATOR":
            return "CREATOR", {}
        if role == "HISTORICAL":
            return self._classify_known_subprov(conn, recipient, {}, funding_treasury)

        # SUBPROV / BUY_SWARM: fetch the row for fine-grained sub-classification
        if role in ("SUBPROV", "BUY_SWARM"):
            known = store.lookup_subprov(conn, recipient) or {}
            bsr   = known.get("buy_swarm_ratio") or 0.0
            n_obs = (known.get("buy_swarm_count") or 0) + (known.get("create_count") or 0)
            has_creators = (known.get("creator_count") or 0) >= 5
            if bsr > 0.7 and n_obs >= 10 and not has_creators:
                return "BUY_SWARM_PROVISIONER", known
            if (known.get("wrap_close_count") or 0) >= 1:
                return self._classify_known_subprov(conn, recipient, known, funding_treasury)
            if store.is_historical_subprov(conn, recipient):
                return "HISTORICAL_SUBPROV_DISCOVERED", known
            return "NEW_SUBPROV", {}

        # Cache miss — full DB lookup (cold path)
        _known_treasuries = getattr(self, "_treasuries", None)
        if _known_treasuries is None:
            _known_treasuries = _confirmed_treasuries(conn)
            self._treasuries = _known_treasuries
        if recipient in _known_treasuries:
            self._profile_set(recipient, "TREASURY")
            return "TREASURY_MESH", {}

        known = store.lookup_subprov(conn, recipient)
        if known:
            if known.get("subprov_type") == "NON_PROVISIONING_RECIPIENT":
                self._profile_set(recipient, "NON_PROVISIONING")
                return "NON_PROVISIONING_RECIPIENT", known
            bsr   = known.get("buy_swarm_ratio") or 0.0
            n_obs = (known.get("buy_swarm_count") or 0) + (known.get("create_count") or 0)
            has_creators = (known.get("creator_count") or 0) >= 5
            if bsr > 0.7 and n_obs >= 10 and not has_creators:
                self._profile_set(recipient, "BUY_SWARM")
                return "BUY_SWARM_PROVISIONER", known
            if ((known.get("wrap_close_count") or 0) + (known.get("seeded_account_count") or 0)) >= 1:
                self._profile_set(recipient, "SUBPROV")
                return self._classify_known_subprov(conn, recipient, known, funding_treasury)

        # CREATOR check before historical
        hot_conn = db_connect(LIVE_DB_PATH, timeout=2)
        is_creator = hot_conn.execute(
            "SELECT 1 FROM token_analysis WHERE pf_ws_creator=? LIMIT 1", (recipient,)
        ).fetchone()
        hot_conn.close()
        if is_creator:
            self._profile_set(recipient, "CREATOR")
            return "CREATOR", {}

        if store.is_historical_subprov(conn, recipient):
            self._profile_set(recipient, "HISTORICAL")
            return self._classify_known_subprov(conn, recipient, known or {}, funding_treasury)

        return "NEW_SUBPROV", {}

    def _classify_known_subprov(self, conn, subprov: str, meta: dict,
                                 funding_treasury: str | None) -> tuple:
        """Determine CONTINUING_OPERATION vs SUBPROV_REACTIVATED based on dormancy gap.

        Uses last *operational* activity across all event tables — not just session close time.
        Attaches funding_sequence_number and treasury_rotated flag to meta.
        """
        now = int(time.time())
        last_activity = self._last_operational_activity(conn, subprov)
        gap_s = (now - last_activity) if last_activity else None
        seq   = self._funding_sequence_number(conn, subprov)

        # Detect treasury rotation: look at the most recent prior session's treasury
        treasury_rotated = False
        if funding_treasury and last_activity:
            prior_row = conn.execute(
                "SELECT treasury_wallet FROM wt_active_subprov_sessions "
                "WHERE subprov_wallet=? ORDER BY detected_at DESC LIMIT 1", (subprov,)
            ).fetchone()
            if prior_row and prior_row[0] and prior_row[0] != funding_treasury:
                treasury_rotated = True

        enriched = dict(meta)
        enriched["funding_sequence_number"] = seq
        enriched["last_activity_at"] = last_activity
        enriched["gap_s"] = gap_s
        enriched["treasury_rotated"] = treasury_rotated

        if gap_s is not None and gap_s < self._DORMANCY_THRESHOLD_S:
            return "CONTINUING_OPERATION", enriched
        return "SUBPROV_REACTIVATED", enriched

    def _is_buy_swarm_burst(self, conn, subprov: str) -> bool:
        """True if the subprov's WATCHING candidates look like a buy-swarm burst:
        ≥BURST_RECIPIENT_FLOOR distinct recipients in BURST_WINDOW_SEC, median funding in the
        dust band (BURST_MEDIAN_SOL_LO–HI), and zero CREATE confirmations on this subprov."""
        since = int(time.time()) - int(BURST_WINDOW_SEC)
        rows = conn.execute(
            "SELECT funding_amount FROM wt_candidate_websocket_watches "
            "WHERE subprov_wallet=? AND detected_at>=? AND candidate_wallet!=subprov_wallet",
            (subprov, since)).fetchall()
        if len(rows) < BURST_RECIPIENT_FLOOR:
            return False
        amounts = sorted(r[0] for r in rows if r[0] is not None)
        if not amounts:
            return False
        median = amounts[len(amounts) // 2]
        if not (BURST_MEDIAN_SOL_LO <= median <= BURST_MEDIAN_SOL_HI):
            return False
        # Historical create evidence from wt_discovered_subprovs.
        # creator_count = discovery-time tally of creator wallets funded (reliable).
        # create_count  = real-time pipeline counter (may be 0 for pre-pipeline subprovs).
        # Either ≥ 1 is sufficient to exempt from the swarm gate.
        known = store.lookup_subprov(conn, subprov)
        if known and ((known.get("create_count") or 0) > 0 or (known.get("creator_count") or 0) > 0):
            return False
        # In-flight create evidence: a candidate from this subprov already fired a CREATE
        # this session (state=FIRED_CREATE). wt_discovered_subprovs hasn't been updated yet
        # (that happens post-teardown), so this is the only real-time signal.
        in_flight = conn.execute(
            "SELECT 1 FROM wt_candidate_websocket_watches "
            "WHERE subprov_wallet=? AND state='FIRED_CREATE' LIMIT 1", (subprov,)).fetchone()
        if in_flight:
            return False
        # Also check the launch ledger directly — catches the case where record_launch ran
        # but candidate state update hasn't committed yet
        launched = conn.execute(
            "SELECT 1 FROM wt_watchtower_launches WHERE subprov_wallet=? LIMIT 1",
            (subprov,)).fetchone()
        if launched:
            return False
        return True

    def _gate_buy_swarm(self, conn, subprov: str, source: str) -> list:
        """Enforce BUY_SWARM gate: close session, expire candidates, return list of
        candidate wallets to unsubscribe (so caller can remove from WS manager)."""
        sess = store.session_for_subprov(conn, subprov)
        if sess:
            store.close_session(conn, sess[0], "BUY_SWARM_REJECTED")
        expired = store.expire_all_candidates_for_subprov(conn, subprov, "BUY_SWARM_GATE")
        emit_event("BUY_SWARM_SESSION_CLOSED", wallet=subprov,
                   payload={"source": source, "expired_candidates": len(expired)})
        _log(f"🚫 BUY_SWARM gate {subprov[:14]}… ({source}) — session closed, "
             f"{len(expired)} candidates expired")
        return expired

    # ---- offline temp-candidate reconciler (Pass F) -------------------------
    def _temp_candidate_sweep(self):
        """Offline RPC sweep: scan PENDING TEMP_PROVISION_CANDIDATEs for wrap-close evidence.
        Runs off the event loop on a thread. Budget-capped. Any wallet that shows a wrap-close
        in its recent signatures is promoted to CONFIRMED_SUBPROV (opens a real session).
        Wallets with no evidence keep their PENDING state until TTL expires."""
        conn = self._ops()
        try:
            # Expire stale candidates first (free DB op)
            expired_n = store.expire_temp_candidates(conn)
            if expired_n:
                _log(f"🅿 temp sweep: expired {expired_n} stale candidate(s)")

            candidates = store.get_temp_candidates_due(conn, limit=TEMP_SWEEP_RPC_BUDGET)
            if not candidates:
                return

            _log(f"🅿 temp sweep: scanning {len(candidates)} TEMP candidate(s) for wrap-close evidence")
            promoted = 0
            scanned = 0
            _known_treasuries = getattr(self, "_treasuries", None) or _confirmed_treasuries(conn)

            for row in candidates:
                wallet, treasury_addr = row[0], row[1]
                scanned += 1
                try:
                    # getSignaturesForAddress — 1cr, no enhanced endpoint
                    sigs = _rpc("getSignaturesForAddress", [
                        wallet, {"limit": 20, "commitment": "confirmed"}
                    ]) or []
                    found_wrap_close = False
                    for s in sigs:
                        sig_str = s.get("signature") if isinstance(s, dict) else s
                        if not sig_str:
                            continue
                        tx = _get_tx(sig_str)
                        if not tx:
                            continue
                        dests = extract_close_destinations(tx)
                        # Valid wrap-close: destination is not self, not a treasury
                        real_dests = [d for d in dests
                                      if d.get("candidate") != wallet
                                      and d.get("candidate") not in _known_treasuries]
                        if real_dests:
                            found_wrap_close = True
                            _mech_temp = real_dests[0].get("funding_mechanism", "WSOL_WRAP_CLOSE")
                            # Promote: write evidence + open a real session
                            try:
                                store.promote_to_subprov(
                                    conn, subprov=wallet, treasury=treasury_addr or "",
                                    wrap_close_sig=sig_str,
                                    creator=real_dests[0]["candidate"],
                                    amount_sol=real_dests[0].get("base_amount_sol"),
                                    funding_mechanism=_mech_temp)
                            except Exception:
                                pass
                            store.promote_temp_candidate(conn, wallet)
                            # pass funding_amount=0 — the session was already opened (or will be)
                            # by the NEW_SUBPROV path with the correct amount; passing the original
                            # amount here would double-count it in topup_amount_total
                            store.start_session(
                                conn, subprov=wallet, treasury=treasury_addr,
                                funding_sig=row[2],  # original funding_sig
                                funding_amount=0.0, funding_time=row[4],
                                ttl_seconds=SESSION_TTL_SEC, subprov_known=0,
                                open_reason="TEMP_PROMOTED")
                            emit_event("TEMP_CANDIDATE_PROMOTED", wallet=wallet,
                                       related=treasury_addr,
                                       payload={"wrap_close_sig": sig_str,
                                                "creator": real_dests[0]["candidate"],
                                                "funding_mechanism": _mech_temp})
                            _log(f"✅ TEMP_PROMOTED {wallet[:14]}… wrap-close confirmed → session opened")
                            promoted += 1
                            break

                    if not found_wrap_close:
                        store.mark_temp_candidate_scanned(conn, wallet, "no_evidence")

                except Exception as _e:
                    _log(f"🅿 temp sweep err {wallet[:14]}… {_e}")

            _log(f"🅿 temp sweep done: {scanned} scanned, {promoted} promoted")

            # Mark recycled recipients — only valid when subprov watching is live.
            # With SUBPROV_WATCH_ENABLED=0, wrap-close output is invisible so every
            # subprov looks like a non-provisioner; don't mark under those conditions.
            if SUBPROV_WATCH_ENABLED:
                npr_count = store.mark_non_provisioning_recipients(conn)
                if npr_count:
                    _log(f"🔇 non-provisioning recipients marked: {npr_count} total")
        finally:
            conn.close()

    # ---- handle a TREASURY log notification (provisioning outbound) ---------
    def _handle_treasury_tx(self, treasury, sig):
        """A confirmed treasury did something. If it's a provisioning-sized SOL outbound to
        another wallet, open a SUB_PROV session in real-time (the WS-first trigger). Always
        meter the notification so the UI can spot a treasury turning into a swarm hub.
        Returns the list of newly-opened subprov wallets (to subscribe on the loop)."""
        conn = self._ops()
        opened = []
        try:
            tx = _get_tx(sig)
            if not tx:
                store.treasury_ws_record_notif(conn, treasury, sig, opened_session=False)
                return []
            meta = tx.get("meta") or {}
            keys = [k.get("pubkey") if isinstance(k, dict) else k
                    for k in (tx.get("transaction", {}).get("message", {}).get("accountKeys") or [])]
            pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
            btime = tx.get("blockTime")
            try:
                ti = keys.index(treasury)
            except ValueError:
                store.treasury_ws_record_notif(conn, treasury, sig, opened_session=False)
                return []
            # treasury must have SENT SOL (lost lamports)
            if ti >= min(len(pre), len(post)) or post[ti] >= pre[ti]:
                store.treasury_ws_record_notif(conn, treasury, sig, opened_session=False)
                return []
            # recipient(s) = wallet(s) that GAINED a provisioning-sized amount.
            # Classification is handled by _classify_recipient (Phase A). TREASURY_MESH recipients
            # are filtered there; all others open a session. No behaviour change in Phase A.
            for i, w in enumerate(keys):
                if i >= min(len(pre), len(post)) or w == treasury:
                    continue
                gain = (post[i] - pre[i]) / 1e9
                if gain < TREASURY_PROVISION_MIN_SOL:
                    continue
                # ── Phase E Pass 1: 6-way classify, audit log, no behaviour gate ──
                classification, _meta = self._classify_recipient(conn, w, amount_sol=gain,
                                                                    funding_treasury=treasury)
                _log(
                    f"CLASSIFY treasury={treasury[:10]}… recipient={w[:12]}… "
                    f"amount={gain:.4f}◎ result={classification}"
                    + (f" seq=#{_meta.get('funding_sequence_number')} gap={_meta.get('gap_s')}s"
                       if _meta.get("funding_sequence_number") else "")
                    + (" treasury_rotated=True" if _meta.get("treasury_rotated") else "")
                )
                if classification == "TREASURY_MESH":
                    store.record_treasury_hit(treasury=treasury, counterparty=w, sig=sig,
                                              amount_sol=gain, block_time=btime,
                                              tx_type="TREASURY_MESH")
                    _log(f"🔗 treasury mesh {treasury[:10]}… → {w[:12]}… {gain:.2f} ◎ (recorded, no session)")
                    continue
                if classification == "NON_PROVISIONING_RECIPIENT":
                    store.record_treasury_hit(treasury=treasury, counterparty=w, sig=sig,
                                              amount_sol=gain, block_time=btime,
                                              tx_type="NON_PROVISIONING_RECIPIENT")
                    _log(f"🔇 non-provisioning {treasury[:10]}… → {w[:12]}… {gain:.2f} ◎ (skipped)")
                    continue
                if classification == "BUY_SWARM_PROVISIONER" and CLASSIFICATION_ENFORCE:
                    store.record_treasury_hit(treasury=treasury, counterparty=w, sig=sig,
                                              amount_sol=gain, block_time=btime)
                    emit_event("BUY_SWARM_ENROLLMENT_BLOCKED", wallet=w, related=treasury,
                               payload={"funding_sol": gain, "sig": sig})
                    _log(f"🚫 BUY_SWARM_PROVISIONER {treasury[:10]}… → {w[:12]}… {gain:.2f} ◎ (enrollment blocked)")
                    continue
                # Non-mesh, non-swarm recipients: confirmed subprovs open sessions immediately;
                # NEW_SUBPROV parks in the TEMP table and waits for behavioural confirmation.
                store.record_treasury_hit(treasury=treasury, counterparty=w, sig=sig,
                                          amount_sol=gain, block_time=btime)
                subprov_known = 1 if classification in (
                    "CONTINUING_OPERATION", "SUBPROV_REACTIVATED",
                    "BUY_SWARM_PROVISIONER", "HISTORICAL_SUBPROV_DISCOVERED",
                ) else 0
                open_reason = "PROVISION_CANDIDATE" if classification == "NEW_SUBPROV" else classification

                # ── Monitoring state: LIVE_ARMED vs INTEL_ONLY ────────────────
                # LIVE_ARMED: subscribe to WS, open candidate pipeline, spend RPC budget
                # LIVE_ARMED: proven subprovs (CONTINUING_OPERATION / SUBPROV_REACTIVATED)
                # get LIVE_ARMED on reload if they have confirmed wrap-close history — they
                # have already demonstrated the creator-funding pattern so a capital reload
                # is unambiguously a new operation round. NEW_SUBPROV also arms immediately.
                # INTEL_ONLY only for truly ambiguous recipients (BUY_SWARM_PROVISIONER etc).
                _m = _meta or {}
                # wrap_close_count = mechanism A (WSOL_WRAP_CLOSE)
                # seeded_account_count = mechanism B (SEEDED_ACCOUNT_CLOSE / PLAIN_TRANSFER subprovs)
                # either proves creator-funding behaviour
                _proven_fanout = (_m.get("wrap_close_count") or 0) + (_m.get("seeded_account_count") or 0)
                _proven_subprov = (classification in ("CONTINUING_OPERATION", "SUBPROV_REACTIVATED",
                                                      "HISTORICAL_SUBPROV_DISCOVERED")
                                   and _proven_fanout >= 1)
                _LIVE_ARMED_CLASSIFICATIONS = {"NEW_SUBPROV"}
                monitoring_state = ("LIVE_ARMED"
                                    if (classification in _LIVE_ARMED_CLASSIFICATIONS or _proven_subprov)
                                    else "INTEL_ONLY")
                # no_subscribe treasuries: record session intel but never websocket their subprovs.
                # Check the DB directly here; this gate is safety-critical and the flag can be
                # changed while the daemon is running.
                if monitoring_state == "LIVE_ARMED" and _treasury_no_subscribe(conn, treasury):
                    monitoring_state = "INTEL_ONLY"
                    _log(f"⊘ NO_SUBSCRIBE treasury {treasury[:10]}… → {w[:12]}… {gain:.2f} ◎ (INTEL_ONLY, no WS)")

                # ── Pass F: behaviour-first gate ──────────────────────────────
                if classification == "NEW_SUBPROV":
                    is_new = store.record_temp_candidate(
                        conn, wallet=w, treasury=treasury, funding_sig=sig,
                        funding_amount=gain, funding_time=btime,
                        ttl_seconds=TEMP_SUBPROV_TTL_SEC)
                    if TEMP_SUBPROV_ENFORCE:
                        # Hard gate: do NOT open session or subscribe. Offline reconciler
                        # will promote this wallet if it demonstrates wrap-close behaviour.
                        emit_event("TEMP_PROVISION_CANDIDATE_PARKED", wallet=w, related=treasury,
                                   payload={"funding_sol": gain, "sig": sig, "new": is_new})
                        _log(f"🅿 TEMP_PROVISION_CANDIDATE {treasury[:10]}… → {w[:12]}… "
                             f"{gain:.2f} ◎ (parked, no WS sub)")
                        continue
                    else:
                        # Audit mode: still open session as before, but log what would be skipped.
                        _log(f"🅿 [audit] NEW_SUBPROV {treasury[:10]}… → {w[:12]}… {gain:.2f} ◎ "
                             f"(would be parked under ENFORCE=1)")

                # Hard blocklist: known AMM pools / system programs — never session
                if w in _SUBPROV_BLOCKLIST:
                    _log(f"🚫 BLOCKLISTED {w[:12]}… from {treasury[:10]}… {gain:.2f}◎ — known infra, skipping")
                    emit_event("SUBPROV_BLOCKLISTED", wallet=w, related=treasury,
                               payload={"funding_sol": gain, "sig": sig})
                    continue
                # CEX / exchange hot wallets — never session.
                # X65.68: use is_known_account() (CEX_ACCOUNTS + INFRASTRUCTURE_ACCOUNTS +
                # CUSTOM_ACCOUNTS) directly rather than the local _cex_set cache, which only
                # ever covered the live cex_wallets table + INFRASTRUCTURE_ACCOUNTS — missing
                # CEX_ACCOUNTS entirely, which is where Coinbase/Binance/Kraken/etc. live.
                from src.utils.infra_mapping import is_known_account
                if is_known_account(w):
                    _log(f"🏦 CEX {w[:12]}… from {treasury[:10]}… {gain:.2f}◎ — exchange wallet, skipping")
                    emit_event("SUBPROV_BLOCKLISTED", wallet=w, related=treasury,
                               payload={"funding_sol": gain, "sig": sig, "reason": "CEX"})
                    continue

                _session_opened = False
                # ── Plain-transfer detection ──────────────────────────────────
                # Confirmed-treasury plain SOL transfers (no wrap-close, no token
                # instructions) above PLAIN_TRANSFER_MIN_SOL are a high-confidence
                # subprov enrolment signal: 8/8 observed recipients became active
                # subprovs within 46s. Label the mechanism so the session row and
                # capital_reload record are queryable by enrolment path.
                _funding_mechanism = "WSOL_WRAP_CLOSE"  # default; overridden below
                if _is_plain_transfer(tx, treasury, w, gain):
                    _funding_mechanism = "PLAIN_TRANSFER"
                    _log(f"💸 PLAIN_TRANSFER {treasury[:10]}… → {w[:12]}… {gain:.1f} ◎ "
                         f"(treasury→subprov capital injection, mechanism=PLAIN_TRANSFER)")

                # Brand-new subprovs funded with ≥SESSION_HIGH_SOL_FLOOR◎ get a longer TTL —
                # large capital deployments often stage their wrap-close fan-out minutes to
                # hours after provisioning; the default 30m window misses them.
                _ttl = (SESSION_TTL_HIGH_SOL
                        if subprov_known == 0 and gain >= SESSION_HIGH_SOL_FLOOR
                        else SESSION_TTL_SEC)
                if _ttl != SESSION_TTL_SEC:
                    _log(f"⏱ HIGH_SOL_TTL {w[:12]}… {gain:.1f}◎ → {_ttl//3600}h session (new subprov)")
                try:
                    _session_opened = store.start_session(
                        conn, subprov=w, treasury=treasury, funding_sig=sig,
                        funding_amount=gain, funding_time=btime,
                        ttl_seconds=_ttl, subprov_known=subprov_known,
                        open_reason=open_reason, monitoring_state=monitoring_state,
                        funding_sequence_number=_meta.get("funding_sequence_number"),
                        treasury_rotated=bool(_meta.get("treasury_rotated")),
                        last_activity_at=_meta.get("last_activity_at"),
                        funding_mechanism=_funding_mechanism)
                except Exception as _lock_err:
                    is_hv = gain >= store.HIGH_VALUE_PROVISION_SOL
                    _log(f"{'🚨' if is_hv else '⚠️'} SESSION_WRITE_{'DROPPED_HIGH_VALUE' if is_hv else 'FAILED'} "
                         f"{treasury[:10]}… → {w[:12]}… {gain:.2f}◎ ({_lock_err}) — enqueuing retry")
                    try:
                        store.enqueue_pending_session(
                            conn, treasury=treasury, subprov=w, funding_sig=sig,
                            funding_amount=gain, funding_time=btime,
                            open_reason=open_reason, subprov_known=subprov_known,
                            ttl_seconds=_ttl)
                    except Exception as _eq:
                        _log(f"🚨 ENQUEUE_FAILED {w[:12]}… {gain:.2f}◎ — session write permanently lost: {_eq}")
                icon = {
                    "CONTINUING_OPERATION":          "🔄",
                    "SUBPROV_REACTIVATED":           "♻️",
                    "BUY_SWARM_PROVISIONER":         "⚠️",
                    "HISTORICAL_SUBPROV_DISCOVERED": "🔍",
                    "NEW_SUBPROV":                   "⚡",
                }.get(classification, "⚡")
                if _session_opened:
                    if monitoring_state == "LIVE_ARMED":
                        opened.append(w)
                        emit_event("SUBPROV_SESSION_OPENED_WS", wallet=w, related=treasury,
                                   payload={"funding_sol": gain, "sig": sig, "via": "treasury_ws",
                                            "classification": classification})
                        _log(f"{icon} {classification} {treasury[:10]}… → {w[:12]}… {gain:.2f} ◎ (LIVE_ARMED)")
                        # P0 subscribe: fire immediately, ahead of any reconnect-replay queue.
                        # Use run_coroutine_threadsafe when called from a thread (RECONCILE),
                        # ensure_future when already on the event loop (WS notification path).
                        # X24.1 — pass the real funding mechanism so subscribe_live_armed can
                        # select accountSubscribe for PLAIN_TRANSFER sessions.
                        if self._loop and self._loop.is_running():
                            asyncio.run_coroutine_threadsafe(
                                self.subscribe_live_armed(w, funding_mechanism=_funding_mechanism), self._loop)
                        else:
                            asyncio.ensure_future(self.subscribe_live_armed(w, funding_mechanism=_funding_mechanism))
                    else:
                        emit_event("SUBPROV_SESSION_INTEL_ONLY", wallet=w, related=treasury,
                                   payload={"funding_sol": gain, "sig": sig,
                                            "classification": classification})
                        _log(f"{icon} {classification} {treasury[:10]}… → {w[:12]}… {gain:.2f} ◎ (INTEL_ONLY — no WS)")
                else:
                    # Top-up into existing active session — log the continuation
                    _log(f"{icon} {classification} {treasury[:10]}… → {w[:12]}… {gain:.2f} ◎ (TOP_UP — session extended)")

                # ── CAPITAL_RELOAD detection (new session OR top-up) ───────────
                # Fires for:
                #   a) known subprov receiving large injection (existing behaviour)
                #   b) NEW wallet receiving a plain-transfer ≥ threshold from a
                #      confirmed treasury (new enrolment path)
                # INVARIANT: never arms ProgramWatcher — purely an intel record.
                _is_plain = _funding_mechanism == "PLAIN_TRANSFER"
                _fire_reload = (
                    (subprov_known == 1 and gain >= CAPITAL_RELOAD_MIN_SOL)
                    or (_is_plain and gain >= PLAIN_TRANSFER_MIN_SOL)
                )
                if _fire_reload:
                    _discovered = store.lookup_subprov(conn, w) or {}
                    _wcc = _discovered.get("wrap_close_count", 0) or 0
                    _linked_mint = _resolve_linked_mint(conn, w) if _wcc > 0 else None
                    _first_creator = _discovered.get("first_creator")
                    _enrolment_reason = (
                        "PLAIN_TRANSFER_NEW_SUBPROV" if (_is_plain and subprov_known == 0)
                        else "PLAIN_TRANSFER_RELOAD" if _is_plain
                        else "WRAP_CLOSE_RELOAD"
                    )
                    # Resolve operation_uuid via treasury: ASSUMPTION single active op per treasury.
                    # NULL = UNRESOLVED (Mission 2). Linked = attributed (Mission 1 / Ledger timeline).
                    _reload_op_uuid = None
                    try:
                        _op_row = conn.execute(
                            "SELECT operation_uuid FROM wt_ops_v2 "
                            "WHERE treasury_root=? ORDER BY last_seen DESC LIMIT 1",
                            (treasury,)).fetchone()
                        _reload_op_uuid = _op_row["operation_uuid"] if _op_row else None
                    except Exception:
                        pass
                    store.record_capital_reload(
                        conn, subprov=w, treasury=treasury, sig=sig,
                        amount_sol=gain, wrap_close_count=_wcc,
                        first_creator=_first_creator, linked_mint=_linked_mint,
                        enrolment_reason=_enrolment_reason, block_time=btime,
                        session_opened=_session_opened, operation_uuid=_reload_op_uuid)
                    emit_event("CAPITAL_RELOAD", wallet=w, related=treasury,
                               payload={"funding_sol": gain, "sig": sig,
                                        "wrap_close_count": _wcc,
                                        "first_creator": _first_creator,
                                        "linked_mint": _linked_mint,
                                        "classification": classification,
                                        "enrolment_reason": _enrolment_reason,
                                        "funding_mechanism": _funding_mechanism,
                                        "via": "new_session" if _session_opened else "topup"})
                    _log(f"🚨 CAPITAL_RELOAD {treasury[:10]}… → {w[:12]}… "
                         f"{gain:.2f} ◎ wcc={_wcc} reason={_enrolment_reason} "
                         f"({'new session' if _session_opened else 'top-up'} — PW OFF)")
                # ── CDC gate (independent of subprov session logic) ────────────
                # Large recipient (≥ CDC_MIN_SOL) with no confirmed treasury record
                # and no wrap-close history → Capital Distributor Candidate.
                # INVARIANTS: never arms ProgramWatcher, never creates creator
                # candidates, never modifies the subprov session. Pure observation.
                if (gain >= CDC_MIN_SOL
                        and w not in (_confirmed_treasuries(conn) if not hasattr(self, "_treasuries")
                                      else self._treasuries)
                        and (store.lookup_subprov(conn, w) or {}).get("wrap_close_count", 0) == 0):
                    _is_new_cdc = store.register_cdc(
                        conn, wallet=w, source_treasury=treasury,
                        funding_sig=sig, funding_amount_sol=gain, block_time=btime or 0)
                    if _is_new_cdc:
                        emit_event("CDC_REGISTERED", wallet=w, related=treasury,
                                   payload={"funding_sol": gain, "sig": sig})
                        _log(f"🔵 CDC {treasury[:10]}… → {w[:12]}… {gain:.2f} ◎ "
                             f"(capital distributor candidate — observing, no WS yet)")
                        opened.append(("CDC", w))   # signal to caller: subscribe this wallet
                    elif w not in self.mgr.wallet_kind:
                        # Already registered but not subscribed (e.g. post-restart).
                        # Re-subscribe so _handle_cdc_tx receives its future txs.
                        opened.append(("CDC", w))
            # ── Treasury-as-subprov: direct wrap-close fan-out ──────────────────
            # Some treasuries (e.g. Dtwi1e…) ALSO do wrap-close→creator directly,
            # without routing through a child subprov. Detect that here by running
            # extract_close_destinations on the same tx. If close dests are found,
            # the treasury IS acting as its own subprov — open candidate watches
            # with treasury as both treasury and subprov.
            _known_treasuries = getattr(self, "_treasuries", None)
            if _known_treasuries is None:
                _known_treasuries = _confirmed_treasuries(conn)
                self._treasuries = _known_treasuries
            direct_dests = [d for d in extract_close_destinations(tx)
                            if d.get("candidate") != treasury
                            and d.get("candidate") not in _known_treasuries]
            if direct_dests:
                wrap_close_time = tx.get("blockTime")
                _mech_direct = direct_dests[0].get("funding_mechanism", "WSOL_WRAP_CLOSE")
                try:
                    store.promote_to_subprov(
                        conn, subprov=treasury, treasury=treasury,
                        wrap_close_sig=sig, creator=direct_dests[0]["candidate"],
                        amount_sol=direct_dests[0].get("base_amount_sol"),
                        funding_mechanism=_mech_direct,
                    )
                except Exception as _e:
                    _log(f"[treasury-as-subprov] promote_to_subprov failed: {_e}")
                emit_event("WRAP_CLOSE_FANOUT_DETECTED", wallet=treasury,
                           related=direct_dests[0]["candidate"],
                           payload={"wrap_close_sig": sig, "via": "treasury_direct",
                                    "dest_count": len(direct_dests),
                                    "funding_mechanism": _mech_direct})
                if treasury in self._post_create_last_fanout:
                    self._post_create_last_fanout[treasury] = time.time()
                    _log(f"⏱ POST_CREATE_ACTIVE {treasury[:12]}… — fanout heartbeat (treasury-direct), window extended")
                if SAVE_CANDIDATE_FANOUT:
                    new_direct = []
                    direct_metas = []
                    for d in direct_dests:
                        cand = d["candidate"]
                        if store.open_candidate_watch(
                                conn, candidate=cand, subprov=treasury, treasury=treasury,
                                wrap_close_sig=sig, wrap_wallet=d.get("wrap_wallet"),
                                temp_wsol=d.get("temp_wsol_account"),
                                funding_amount=d.get("base_amount_sol"), ttl_seconds=CANDIDATE_TTL_SEC,
                                wrap_close_time=wrap_close_time,
                                funding_mechanism=d.get("funding_mechanism", "WSOL_WRAP_CLOSE")):
                            new_direct.append(cand)
                        direct_metas.append({
                            "candidate": cand, "subprov": treasury, "treasury": treasury,
                            "wrap_sig": sig, "wrap_time": wrap_close_time,
                            "amount": d.get("base_amount_sol"),
                        })
                    if new_direct:
                        _log(f"🎯 treasury-direct wrap-close {treasury[:10]}… → {len(new_direct)} candidate(s) saved")
                    # Record fanout event for treasury-direct path
                    try:
                        store.record_fanout_event(
                            conn, subprov=treasury, treasury=treasury,
                            fanout_time=wrap_close_time or int(time.time()),
                            dests=direct_dests, sig=sig)
                    except Exception as _fe:
                        _log(f"[fanout_event/direct] write failed: {_fe}")
                    # Feed ProgramWatcher — one program stream, no per-wallet subscriptions
                    prog_watcher = getattr(self, "_prog_watcher", None)
                    if prog_watcher and direct_metas:
                        prior_creates = (conn.execute(
                            "SELECT COUNT(*) FROM wt_watchtower_launches WHERE subprov_wallet=?", (treasury,)
                        ).fetchone()[0] or 0)
                        burst_size = len(direct_dests)
                        op_phase = "PRE_CREATE" if prior_creates == 0 else "POST_CREATE"
                        if op_phase == "PRE_CREATE":
                            arm_pw = True
                        elif burst_size <= 6:
                            arm_pw = True
                        elif burst_size >= 11:
                            arm_pw = False
                        else:
                            arm_pw = burst_size <= 8
                        if arm_pw:
                            prog_watcher.add_candidates(direct_metas, conn)
                            _log(f"🎯 ProgramWatcher armed {len(direct_metas)} candidate(s) [treasury-direct {op_phase} burst={burst_size}]")
                        else:
                            _log(f"⏸ ProgramWatcher deferred [treasury-direct {op_phase} burst={burst_size} ≥ threshold — INTEL only]")

            store.treasury_ws_record_notif(conn, treasury, sig, opened_session=bool(opened))
            return opened
        finally:
            conn.close()

    # ---- handle a SUB_PROV log notification (wrap-close fan-out) ------------
    def _handle_cdc_tx(self, cdc_wallet, sig):
        """Account notification for a Capital Distributor Candidate.
        Record outbound transfers (intelligence). If this tx contains a wrap-close,
        the CDC has proven itself to be a subprov — promote it and process the
        wrap-close recipients as creator candidates."""
        conn = self._ops()
        try:
            tx = _get_tx(sig)
            if not tx:
                return
            btime = (tx or {}).get("blockTime") or 0

            # ── Wrap-close detection: CDC → SUBPROV promotion ─────────────────
            # If the CDC performs a wrap-close, it is confirmed as a subprov.
            # Promote it, open a session, then hand off to _handle_subprov_tx.
            close_dests = extract_close_destinations(tx)
            self_closes = [d for d in close_dests if d.get("candidate") == cdc_wallet]
            real_dests = [d for d in close_dests if d.get("candidate") != cdc_wallet]
            if real_dests:
                # Resolve the CDC's source treasury
                cdc_row = conn.execute(
                    "SELECT source_treasury, funding_amount_sol, funding_sig "
                    "FROM wt_capital_distributor_candidates WHERE wallet=? LIMIT 1",
                    (cdc_wallet,)).fetchone()
                treasury = cdc_row[0] if cdc_row else None
                funding_amount = cdc_row[1] if cdc_row else 0.0
                funding_sig = cdc_row[2] if cdc_row else sig

                # Open a subprov session (may already exist — start_session is idempotent)
                store.start_session(
                    conn, subprov=cdc_wallet, treasury=treasury or "",
                    funding_sig=funding_sig, funding_amount=funding_amount,
                    funding_time=btime, ttl_seconds=SESSION_TTL_SEC,
                    subprov_known=1, open_reason="CDC_WRAP_CLOSE_PROMOTED")
                # Update CDC state to PROMOTED so we stop routing future txs here
                conn.execute(
                    "UPDATE wt_capital_distributor_candidates "
                    "SET observation_state='PROMOTED', subscription_ended=? WHERE wallet=?",
                    (btime or int(time.time()), cdc_wallet))
                conn.commit()
                # Re-register WS subscription as 'subprov' so future txs route correctly
                try:
                    self.mgr.wallet_kind[cdc_wallet] = "subprov"
                except Exception:
                    pass
                emit_event("CDC_PROMOTED_TO_SUBPROV", wallet=cdc_wallet, related=treasury,
                           payload={"sig": sig, "wrap_close_dests": len(real_dests),
                                    "treasury": treasury})
                _log(f"🔵→🟡 CDC PROMOTED {cdc_wallet[:12]}… → subprov "
                     f"(wrap-close to {len(real_dests)} dest(s), treasury={treasury[:12] if treasury else 'unknown'}…)")
                # Now process this tx as a subprov tx (it already has the session)
                return self._handle_subprov_tx(cdc_wallet, sig)

            # ── Plain outbound recording (pure intelligence) ──────────────────
            meta = (tx or {}).get("meta") or {}
            keys = [k.get("pubkey") if isinstance(k, dict) else k
                    for k in ((tx or {}).get("transaction", {}).get("message", {})
                              .get("accountKeys") or [])]
            pre  = meta.get("preBalances") or []
            post = meta.get("postBalances") or []
            try:
                si = keys.index(cdc_wallet)
            except ValueError:
                return
            if si >= len(pre) or si >= len(post) or post[si] >= pre[si]:
                return  # not a sender in this tx
            recipients = []
            for i, w in enumerate(keys):
                if i == si or not w:
                    continue
                if i < len(pre) and i < len(post) and post[i] > pre[i]:
                    gain_lamports = post[i] - pre[i]
                    if gain_lamports >= 1_000_000:   # ≥ 0.001 SOL
                        recipients.append((w, gain_lamports / 1e9))
            if recipients:
                store.record_cdc_outbound(conn, cdc_wallet=cdc_wallet, sig=sig,
                                          block_time=btime, recipients=recipients)
                emit_event("CDC_OUTBOUND_RECORDED", wallet=cdc_wallet,
                           payload={"sig": sig, "recipients": len(recipients)})
                _log(f"🔵 CDC outbound {cdc_wallet[:12]}… → {len(recipients)} recipients (sig {sig[:16]}…)")
        except Exception as exc:
            _log(f"_handle_cdc_tx error {cdc_wallet[:12]}… {exc}")
        finally:
            conn.close()

    def _get_subprov_tx_fast_retry(self, subprov: str, sig: str, seen_at: Optional[float] = None):
        """Fetch a just-seen subprov tx, absorbing short RPC propagation races inline.

        The durable retry row already exists before this is called. If all quick attempts
        return None, the caller raises and the existing retry worker remains the safety net.

        X24.2.3 Phase 1/4 — per-attempt timing (additive only, no retry-logic change).
        Separates sleep-to-target time from actual RPC-call time per attempt, so the
        retry-behaviour audit can attribute cost to "waiting for the scheduled offset"
        vs "the RPC call itself was slow" rather than only seeing a combined total.
        """
        burst_started_at = time.time()
        first_rpc_started_at = None
        last_rpc_done_at = None
        none_count = 0
        offsets = SUBPROV_FAST_RETRY_OFFSETS or (0.0,)
        _attempt_timings: list[dict] = []
        for idx, offset in enumerate(offsets, start=1):
            target = burst_started_at + max(0.0, float(offset))
            _sleep_t0 = time.time()
            sleep_for = target - time.time()
            if sleep_for > 0:
                time.sleep(sleep_for)
            _sleep_ms = round((time.time() - _sleep_t0) * 1000, 1)
            rpc_started_at = time.time()
            if first_rpc_started_at is None:
                first_rpc_started_at = rpc_started_at
            self._metric("subprov_fast_retry_attempts")
            # X24.3 — deadline-guarded fetch. tx/outcome distinguish SUCCESS from
            # every tail-protection outcome (DEADLINE_EXCEEDED_RUNNING,
            # CANCELLED_BEFORE_START, CAPACITY_REJECTED, CIRCUIT_OPEN_REJECTED,
            # RPC_ERROR, NOT_FOUND) for telemetry (design requirement 4), while
            # this loop's own retry scheduling (offsets/sleep/attempt count) is
            # completely unchanged — a guarded-but-failed attempt is still just
            # "no tx this attempt" to the existing retry logic below.
            _deadline_result = _get_tx_with_outcome(sig)
            tx = _deadline_result.value
            _outcome = _deadline_result.outcome
            last_rpc_done_at = time.time()
            _rpc_call_ms = round((last_rpc_done_at - rpc_started_at) * 1000, 1)
            self._metric(f"subprov_gettx_outcome_{_outcome.value.lower()}")
            _attempt_timings.append({
                "attempt": idx, "sleep_ms": _sleep_ms, "rpc_call_ms": _rpc_call_ms,
                "result": "HIT" if tx else "NONE", "outcome": _outcome.value,
            })
            if tx:
                if idx > 1:
                    self._metric("subprov_fast_retry_success")
                    _log(
                        f"⚡ subprov fast retry recovered {subprov[:12]}… sig={sig[:12]}… "
                        f"attempt={idx} seen_to_available={_lat_ms(last_rpc_done_at, seen_at)}ms "
                        f"first_rpc_to_available={_lat_ms(last_rpc_done_at, first_rpc_started_at)}ms"
                    )
                _total_burst_ms = round((time.time() - burst_started_at) * 1000, 1)
                if _total_burst_ms > 500:
                    _log(f"⏲ retry_burst_timing {subprov[:12]}… sig={sig[:12]}… "
                         f"attempts={_attempt_timings} total_burst_ms={_total_burst_ms}")
                return tx, {
                    "first_rpc_started_at": first_rpc_started_at,
                    "tx_available_at": last_rpc_done_at,
                    "attempts": idx,
                    "none_count": none_count,
                    "fallback": False,
                    "attempt_timings": _attempt_timings,
                }
            none_count += 1
            self._metric("subprov_gettx_none_count")

        self._metric("subprov_fast_retry_fallback")
        _log(
            f"⏳ subprov fast retry fallback {subprov[:12]}… sig={sig[:12]}… "
            f"attempts={len(offsets)} none={none_count} "
            f"seen_to_fallback={_lat_ms(last_rpc_done_at, seen_at)}ms"
        )
        _total_burst_ms = round((time.time() - burst_started_at) * 1000, 1)
        _log(f"⏲ retry_burst_timing {subprov[:12]}… sig={sig[:12]}… "
             f"attempts={_attempt_timings} total_burst_ms={_total_burst_ms} FALLBACK")
        return None, {
            "first_rpc_started_at": first_rpc_started_at,
            "tx_available_at": None,
            "attempts": len(offsets),
            "none_count": none_count,
            "attempt_timings": _attempt_timings,
            "fallback": True,
        }

    def _handle_subprov_tx(self, subprov, sig, seen_at: Optional[float] = None,
                          prefetched: Optional[tuple] = None):
        # X24.2.3 Phase 1 — sub-stage timing inside handle_tx, the stage X24.2.2 left
        # unaddressed and confirmed dominant (median 531ms, mean 1158ms, max 8423ms
        # across the X24.2.2 validation window). Additive only; isolates RPC-fetch
        # (already partly covered by _get_subprov_tx_fast_retry's own tx_retry_info)
        # from decode/classification/candidate-extraction cost, to answer the sprint's
        # key question: why do some signatures cost 5-12s while others cost ms.
        #
        # X65.29 — `prefetched`, when supplied, is the (tx, tx_retry_info) tuple
        # already returned by a call to _get_subprov_tx_fast_retry() made BEFORE
        # this method runs (catch_up_subprov's concurrent-fetch stage). This
        # lets the RPC round-trip (the dominant cost, ~3200-3488ms observed)
        # happen concurrently across several signatures while every stateful
        # read/write below (session lookup, _op_phase's prior_creates count,
        # promote_to_subprov, candidate-watch writes) still runs strictly
        # serially, in original chronological order, exactly as before —
        # `prefetched=None` (every existing call site) is byte-for-byte
        # unchanged behaviour.
        _htx_t0 = time.time()
        conn = self._ops()
        try:
            _session_t0 = time.time()
            sess = store.session_for_subprov(conn, subprov)
            _session_lookup_ms = round((time.time() - _session_t0) * 1000, 1)
            if not sess:
                return []                              # session gone/expired
            treasury, funding_time = sess[1], sess[2]
            _rpc_t0 = time.time()
            if prefetched is not None:
                tx, tx_retry_info = prefetched
            else:
                tx, tx_retry_info = self._get_subprov_tx_fast_retry(subprov, sig, seen_at=seen_at)
            _rpc_fetch_ms = round((time.time() - _rpc_t0) * 1000, 1)
            if not tx:
                raise RuntimeError("getTransaction returned None")
            wrap_close_time = (tx or {}).get("blockTime")   # on-chain creator BIRTH time
            _treasuries_t0 = time.time()
            _known_treasuries = getattr(self, "_treasuries", None)
            if _known_treasuries is None:
                _known_treasuries = _confirmed_treasuries(conn)
                self._treasuries = _known_treasuries
            _treasuries_lookup_ms = round((time.time() - _treasuries_t0) * 1000, 1)
            _decode_t0 = time.time()
            raw_dests = extract_close_destinations(tx)
            _decode_ms = round((time.time() - _decode_t0) * 1000, 1)
            # Self-close guard: subprov closing its own WSOL ATA back to itself.
            # This is just WSOL round-tripping (buy-swarm trader), not creator seeding.
            self_closes = [d for d in raw_dests if d.get("candidate") == subprov]
            if self_closes:
                emit_event("SELF_CLOSE_IGNORED", wallet=subprov,
                           payload={"sig": sig, "count": len(self_closes)})
            dests = [d for d in raw_dests
                     if d.get("candidate") != subprov
                     and d.get("candidate") not in _known_treasuries]
            if not dests:
                # ── Sub-subprov plain-transfer detection ──────────────────────────
                # No wrap-close found. Check if this subprov plain-transferred SOL to
                # an unknown wallet (capital routing to a child subprov tier).
                # If so, open a session for that child so ITS wrap-closes get watched.
                # X24.2.3 Phase 1 — this branch (plain-transfer classification) is
                # hypothesised as a variance source: it calls _classify_recipient()
                # (a DB read on cache miss) once per qualifying account key in the tx,
                # an O(n) cost per signature that the earlier per-stage timing
                # (handle_tx as a single blob) could not isolate.
                _noclass_t0 = time.time()
                _classify_calls = 0
                meta = (tx or {}).get("meta") or {}
                keys = [k.get("pubkey") if isinstance(k, dict) else k
                        for k in ((tx or {}).get("transaction", {}).get("message", {})
                                  .get("accountKeys") or [])]
                pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
                try:
                    si = keys.index(subprov)
                except ValueError:
                    si = -1
                if si >= 0 and si < min(len(pre), len(post)) and post[si] < pre[si]:
                    child_sessions = []
                    for i, w in enumerate(keys):
                        if i >= min(len(pre), len(post)) or w == subprov or w in _known_treasuries:
                            continue
                        gain = (post[i] - pre[i]) / 1e9
                        if gain < TREASURY_PROVISION_MIN_SOL:
                            continue
                        # Only open session if w is not already known as a buy-swarm producer
                        _classify_calls += 1
                        classification, _cmeta = self._classify_recipient(conn, w, amount_sol=gain,
                                                                            funding_treasury=subprov)
                        if classification in ("TREASURY_MESH", "BUY_SWARM_PROVISIONER"):
                            _log(f"⏭ subprov plain-xfer skip {w[:12]}… classification={classification}")
                            continue
                        # Pass F: sub-subprov recursion blocked for unproven children under ENFORCE.
                        # The parent (subprov) IS confirmed (it has a live session + wrap-close
                        # evidence that triggered this path). The CHILD (w) must still earn its sub.
                        if classification == "NEW_SUBPROV" and TEMP_SUBPROV_ENFORCE:
                            is_new = store.record_temp_candidate(
                                conn, wallet=w, treasury=treasury, funding_sig=sig,
                                funding_amount=gain, funding_time=wrap_close_time,
                                ttl_seconds=TEMP_SUBPROV_TTL_SEC)
                            emit_event("TEMP_PROVISION_CANDIDATE_PARKED", wallet=w, related=subprov,
                                       payload={"funding_sol": gain, "sig": sig,
                                                "via": "subprov_plain_xfer", "new": is_new})
                            _log(f"🅿 TEMP_PROVISION_CANDIDATE {subprov[:10]}… → {w[:12]}… "
                                 f"{gain:.2f}◎ (parked, no WS sub)")
                            continue
                        open_reason = "PROVISION_CANDIDATE" if classification == "NEW_SUBPROV" else classification
                        subprov_known = 1 if classification not in ("NEW_SUBPROV",) else 0
                        child_monitoring_state = (
                            "INTEL_ONLY" if _treasury_no_subscribe(conn, treasury) else "LIVE_ARMED"
                        )
                        if store.start_session(conn, subprov=w, treasury=treasury,
                                               funding_sig=sig, funding_amount=gain,
                                               funding_time=wrap_close_time,
                                               ttl_seconds=SESSION_TTL_SEC,
                                               subprov_known=subprov_known,
                                               open_reason=open_reason,
                                               monitoring_state=child_monitoring_state):
                            if child_monitoring_state == "LIVE_ARMED":
                                child_sessions.append(w)
                                emit_event("SUBPROV_SESSION_OPENED_WS", wallet=w, related=subprov,
                                           payload={"funding_sol": gain, "sig": sig,
                                                    "via": "subprov_plain_xfer",
                                                    "parent_subprov": subprov})
                                _log(f"🔀 sub-subprov {subprov[:10]}… → {w[:12]}… {gain:.2f}◎ session opened")
                            else:
                                emit_event("SUBPROV_SESSION_INTEL_ONLY", wallet=w, related=subprov,
                                           payload={"funding_sol": gain, "sig": sig,
                                                    "via": "subprov_plain_xfer",
                                                    "parent_subprov": subprov})
                                _log(f"⊘ NO_SUBSCRIBE sub-subprov {subprov[:10]}… → {w[:12]}… "
                                     f"{gain:.2f}◎ (INTEL_ONLY, no WS)")
                    _noclass_ms = round((time.time() - _noclass_t0) * 1000, 1)
                    _htx_total_ms = round((time.time() - _htx_t0) * 1000, 1)
                    if _htx_total_ms > 500:
                        _log(f"⏲ handle_tx_stage_timing {subprov[:12]}… sig={sig[:12]}… "
                             f"session={_session_lookup_ms}ms rpc_fetch={_rpc_fetch_ms}ms "
                             f"treasuries={_treasuries_lookup_ms}ms decode={_decode_ms}ms "
                             f"noclass_branch={_noclass_ms}ms classify_calls={_classify_calls} "
                             f"branch=NO_DESTS_WITH_CHILDREN total={_htx_total_ms}ms")
                    return child_sessions
                _noclass_ms = round((time.time() - _noclass_t0) * 1000, 1)
                _htx_total_ms = round((time.time() - _htx_t0) * 1000, 1)
                if _htx_total_ms > 500:
                    _log(f"⏲ handle_tx_stage_timing {subprov[:12]}… sig={sig[:12]}… "
                         f"session={_session_lookup_ms}ms rpc_fetch={_rpc_fetch_ms}ms "
                         f"treasuries={_treasuries_lookup_ms}ms decode={_decode_ms}ms "
                         f"noclass_branch={_noclass_ms}ms classify_calls={_classify_calls} "
                         f"branch=NO_DESTS_NO_CHILDREN total={_htx_total_ms}ms")
                return []
            # ── PROTECT FIRST, CLASSIFY SECOND ────────────────────────────────────
            # Build watcher_metas from dests immediately — no DB writes precede this.
            # ProgramWatcher in-memory protection is the single most time-critical step;
            # every DB write below is trailing classification and must never delay it.
            _protect_first = os.environ.get("PW_PROTECT_BEFORE_CLASSIFY", "1") == "1"
            watcher_metas_all = [
                {
                    "candidate": d["candidate"], "subprov": subprov, "treasury": treasury,
                    "wrap_sig": sig, "wrap_time": wrap_close_time,
                    "amount": d.get("base_amount_sol"),
                }
                for d in dests
            ]
            prog_watcher = getattr(self, "_prog_watcher", None)
            _arm_pw = False
            _op_phase = "UNKNOWN"
            _burst_size = len(dests)
            if _protect_first and prog_watcher and watcher_metas_all:
                prior_creates = (conn.execute(
                    "SELECT COUNT(*) FROM wt_watchtower_launches WHERE subprov_wallet=?", (subprov,)
                ).fetchone()[0] or 0)
                _op_phase = "PRE_CREATE" if prior_creates == 0 else "POST_CREATE"
                if _op_phase == "PRE_CREATE":
                    _arm_pw = True
                elif _burst_size <= 6:
                    _arm_pw = True
                elif _burst_size >= 11:
                    _arm_pw = False
                else:
                    _arm_pw = _burst_size <= 8
                if _arm_pw:
                    _log(f"[CANDIDATE_PROTECT_START] subprov={subprov[:12]}… candidates={len(watcher_metas_all)} phase={_op_phase} burst={_burst_size}")
                    prog_watcher.add_candidates(watcher_metas_all, conn)
                    _armed_at = time.time()
                    _log(f"[CANDIDATE_IN_MEMORY] subprov={subprov[:12]}… candidates={len(watcher_metas_all)} armed in {_lat_ms(_armed_at, seen_at)}ms since seen")
                    _log(
                        f"⏱ SUBPROV_ARM_LATENCY subprov={subprov[:12]}… sig={sig[:12]}… "
                        f"candidates={len(watcher_metas_all)} "
                        f"seen_to_armed={_lat_ms(_armed_at, seen_at)}ms "
                        f"first_rpc_to_available={_lat_ms(tx_retry_info.get('tx_available_at'), tx_retry_info.get('first_rpc_started_at'))}ms "
                        f"attempts={tx_retry_info.get('attempts')} "
                        f"none_count={tx_retry_info.get('none_count')} "
                        f"fallback={tx_retry_info.get('fallback')}"
                    )
                else:
                    _log(f"⏸ ProgramWatcher deferred [{_op_phase} burst={_burst_size} ≥ threshold — INTEL only]")

            # ── Phase B: record wrap-close evidence + promote to PROVISIONAL_SUBPROV ──
            # Trailing classification — runs after in-memory protection is established.
            _classify_t0 = time.time()
            _mech = dests[0].get("funding_mechanism", "WSOL_WRAP_CLOSE")
            try:
                store.promote_to_subprov(
                    conn,
                    subprov=subprov,
                    treasury=treasury or "",
                    wrap_close_sig=sig,
                    creator=dests[0]["candidate"],
                    amount_sol=dests[0].get("base_amount_sol"),
                    funding_mechanism=_mech,
                )
            except Exception as _e:
                _log(f"[Phase B] promote_to_subprov failed: {_e}")
            _classify_ms = round((time.time() - _classify_t0) * 1000, 1)
            emit_event("WRAP_CLOSE_FANOUT_DETECTED", wallet=subprov,
                       related=dests[0]["candidate"],
                       payload={"wrap_close_sig": sig, "base": dests[0].get("base_amount_sol"),
                                "dest_count": len(dests), "funding_mechanism": _mech})
            # POST_CREATE_ACTIVE heartbeat: if this subprov is in the 120s continuation window,
            # update the last-fanout timestamp to extend the armed window.
            if subprov in self._post_create_last_fanout:
                self._post_create_last_fanout[subprov] = time.time()
                _log(f"⏱ POST_CREATE_ACTIVE {subprov[:12]}… — fanout heartbeat, window extended")
            # Persist fan-out destinations (DB classification — trailing, after in-memory protection)
            _candidate_extraction_t0 = time.time()
            new_watches = []
            watcher_metas = []
            if SAVE_CANDIDATE_FANOUT:
                for d in dests:
                    cand = d["candidate"]
                    if store.open_candidate_watch(
                            conn, candidate=cand, subprov=subprov, treasury=treasury,
                            wrap_close_sig=sig, wrap_wallet=d.get("wrap_wallet"),
                            temp_wsol=d.get("temp_wsol_account"),
                            funding_amount=d.get("base_amount_sol"), ttl_seconds=CANDIDATE_TTL_SEC,
                            wrap_close_time=wrap_close_time,
                            funding_mechanism=d.get("funding_mechanism", "WSOL_WRAP_CLOSE")):
                        new_watches.append(cand)
                        # VANITY-FAMILY EVIDENCE on the wrap-close participants
                        try:
                            from src.core.vanity_family import check_and_record as _vf_check
                            for _w in (cand, d.get("wrap_wallet"), subprov):
                                if _w:
                                    _vf_check(_w, source_event="wrap_close", source_sig=sig)
                        except Exception:
                            pass
                    watcher_metas.append({
                        "candidate": cand, "subprov": subprov, "treasury": treasury,
                        "wrap_sig": sig, "wrap_time": wrap_close_time,
                        "amount": d.get("base_amount_sol"),
                    })
                if new_watches:
                    _log(f"📋 wrap-close fanout {subprov[:12]}… → {len(new_watches)} candidate(s) saved")
                    _log(f"[CANDIDATE_CLASSIFIED] subprov={subprov[:12]}… db_saved={len(new_watches)} of {len(dests)}")
            _candidate_extraction_ms = round((time.time() - _candidate_extraction_t0) * 1000, 1)
            # Record fanout event (powers Bursts/Recipients columns in the ops dashboard)
            _durable_handler_t0 = time.time()
            try:
                store.record_fanout_event(
                    conn, subprov=subprov, treasury=treasury,
                    fanout_time=wrap_close_time or int(time.time()),
                    dests=dests, sig=sig)
            except Exception as _fe:
                _log(f"[fanout_event] write failed: {_fe}")
            _durable_handler_ms = round((time.time() - _durable_handler_t0) * 1000, 1)
            # Legacy path: feed ProgramWatcher if protect-first flag is off
            if not _protect_first and prog_watcher and watcher_metas:
                prior_creates = (conn.execute(
                    "SELECT COUNT(*) FROM wt_watchtower_launches WHERE subprov_wallet=?", (subprov,)
                ).fetchone()[0] or 0)
                burst_size = len(dests)
                op_phase = "PRE_CREATE" if prior_creates == 0 else "POST_CREATE"
                if op_phase == "PRE_CREATE":
                    arm_pw = True
                elif burst_size <= 6:
                    arm_pw = True
                elif burst_size >= 11:
                    arm_pw = False
                else:
                    arm_pw = burst_size <= 8
                if arm_pw:
                    prog_watcher.add_candidates(watcher_metas, conn)
                    _log(f"🎯 ProgramWatcher armed {len(watcher_metas)} candidate(s) [{op_phase} burst={burst_size}]")
                    armed_at = time.time()
                    _log(
                        f"⏱ SUBPROV_ARM_LATENCY subprov={subprov[:12]}… sig={sig[:12]}… "
                        f"candidates={len(watcher_metas)} "
                        f"seen_to_armed={_lat_ms(armed_at, seen_at)}ms "
                        f"first_rpc_to_available={_lat_ms(tx_retry_info.get('tx_available_at'), tx_retry_info.get('first_rpc_started_at'))}ms "
                        f"attempts={tx_retry_info.get('attempts')} "
                        f"none_count={tx_retry_info.get('none_count')} "
                        f"fallback={tx_retry_info.get('fallback')}"
                    )
                else:
                    _log(f"⏸ ProgramWatcher deferred [{op_phase} burst={burst_size} ≥ threshold — INTEL only]")
            # ── Live burst-detection (BUY_SWARM safety valve) ─────────────────
            # Retrospective: runs after candidates are already in-memory. Evicts if confirmed swarm.
            _htx_total_ms = round((time.time() - _htx_t0) * 1000, 1)
            if _htx_total_ms > 500:
                _log(f"⏲ handle_tx_stage_timing {subprov[:12]}… sig={sig[:12]}… "
                     f"session={_session_lookup_ms}ms rpc_fetch={_rpc_fetch_ms}ms "
                     f"treasuries={_treasuries_lookup_ms}ms decode={_decode_ms}ms "
                     f"classify={_classify_ms}ms candidate_extraction={_candidate_extraction_ms}ms "
                     f"durable_handler={_durable_handler_ms}ms dest_count={len(dests)} "
                     f"branch=WRAP_CLOSE_FANOUT total={_htx_total_ms}ms")
            if CLASSIFICATION_ENFORCE and new_watches and self._is_buy_swarm_burst(conn, subprov):
                _log(f"⚡ burst threshold hit for {subprov[:14]}… — triggering BUY_SWARM gate")
                expired = self._gate_buy_swarm(conn, subprov, source="live_burst")
                return [("UNSUBSCRIBE", w) for w in expired]
            # Return empty: callers must not subscribe candidate wallets to WS
            return []
        finally:
            conn.close()

    # ---- handle a CANDIDATE log notification (CREATE vs SWAP) ---------------
    def _handle_candidate_tx(self, candidate, sig, ws_seen_at=None, detection_source="LIVE_STREAM",
                             timing=None, tx_data=None):
        """Returns ('CREATE', launch_dict) | ('SWAP', None) | (None, None).
        Captures the detection-latency timestamps (ws_seen / tx_fetched / mint_extracted) for
        the launch audit as it goes."""
        timing = dict(timing or {})
        ws_seen_at = ws_seen_at or time.time()
        canonical_fetch_skipped = tx_data is not None
        duplicate_fetch_count = 0 if canonical_fetch_skipped else 1
        get_transaction_started_at = None
        if tx_data is None:
            get_transaction_started_at = time.time()
            tx = _get_tx(sig)
            tx_fetched_at = time.time()
        else:
            tx = tx_data
            tx_fetched_at = time.time()
        is_create, mint, btime, extra = _tx_is_create(tx)
        mint_extracted_at = time.time()
        if is_create:
            create_slot = (tx or {}).get("slot")
            conn = self._ops()
            try:
                row = conn.execute(
                    "SELECT subprov_wallet, treasury_wallet, wrap_close_signature, wrap_close_time, "
                    "funding_amount, COALESCE(funding_mechanism,'WSOL_WRAP_CLOSE') "
                    "FROM wt_candidate_websocket_watches WHERE candidate_wallet=? "
                    "ORDER BY detected_at DESC LIMIT 1", (candidate,)).fetchone()
                subprov = row[0] if row else None
                treasury = row[1] if row else None
                wrap_sig = row[2] if row else None
                wrap_close_time = row[3] if row else None
                wrap_close_sol = row[4] if row else None    # subprov→creator wrap-close seed
                _launch_mech = row[5] if row else "WSOL_WRAP_CLOSE"
                # birth_to_launch = CREATE time − the creator's BIRTH (the wrap-close that funded
                # it), NOT the treasury→subprov session funding (which adds the subprov pipeline
                # time and mislabels INSTANT launches as STAGED — e.g. Memeville read 125s vs the
                # true 1s). Fall back to the session funding_time only if wrap_close_time is absent.
                birth_time = wrap_close_time
                subprov_funding_sol = None                  # treasury→subprov load (from the session)
                if subprov:
                    sess = store.session_for_subprov(conn, subprov)
                    if sess:
                        if birth_time is None:
                            birth_time = sess[2]
                        # session row: (id, treasury, funding_time, funding_sig, [funding_amount?])
                        try:
                            # most-recent session for this subprov (any state — by launch time it
                            # may have COMPLETED/EXPIRED); the funding_amount is the treasury load.
                            sf = conn.execute(
                                "SELECT funding_amount FROM wt_active_subprov_sessions "
                                "WHERE subprov_wallet=? ORDER BY detected_at DESC LIMIT 1",
                                (subprov,)).fetchone()
                            subprov_funding_sol = sf[0] if sf else None
                        except Exception:
                            subprov_funding_sol = None
                btl = (btime - birth_time) if (btime and birth_time) else None
                detection_delay_s = (int(time.time()) - btime) if btime else None
                record_launch_started_at = time.time()
                newly = store.record_launch(
                    conn, mint=mint, creator=candidate, create_sig=sig, create_time=btime,
                    treasury=treasury, subprov=subprov, wrap_close_sig=wrap_sig,
                    birth_to_launch_s=btl, create_slot=create_slot,
                    subprov_funding_sol=subprov_funding_sol, wrap_close_sol=wrap_close_sol,
                    detection_source=detection_source,
                    detection_delay_seconds=detection_delay_s,
                    funding_mechanism=_launch_mech)
                record_launch_committed_at = time.time()
                # Guarantee a PENDING audit row exists before the detection conn closes.
                # This rides the same write-serializer slot as record_launch — the audit
                # worker that runs later only ever UPDATEs this pre-existing row, so it
                # can never race to INSERT against the detection transaction.
                try:
                    from src.core import launch_audit as _la
                    _la.insert_pending_audit_row(
                        conn, mint=mint, creator=candidate, treasury=treasury, subprov=subprov,
                        create_signature=sig, create_slot=create_slot, create_time=btime)
                    conn.commit()   # record_launch already committed; this commits the PENDING row
                except Exception as _audit_pending_err:
                    _log(f"[AUDIT] PENDING row insert failed mint={mint} err={_audit_pending_err}")
                # upsert_lifecycle_launched is idempotent (ON CONFLICT DO UPDATE) — run it
                # BEFORE _reconcile_bridge so it isn't affected by any aborted-txn state
                # that reconcile's nested ensure_operation_for_treasury import may leave.
                # Run regardless of newly (duplicate-detection paths still need a lifecycle row).
                try:
                    _op_row = conn.execute(
                        "SELECT operation_uuid FROM wt_ops_v2 "
                        "WHERE treasury_root=? ORDER BY last_seen DESC LIMIT 1",
                        (treasury,)).fetchone()
                    _op_uuid = _op_row["operation_uuid"] if _op_row else None
                    store.upsert_lifecycle_launched(
                        conn, mint=mint, treasury=treasury, subprov=subprov,
                        creator=candidate, create_sig=sig,
                        launched_at=btime or int(time.time()),
                        operation_uuid=_op_uuid)
                except Exception as _lc_e:
                    import traceback
                    _log(f"[LIFECYCLE] upsert_lifecycle_launched FAILED mint={mint} err={_lc_e}\n{traceback.format_exc()}")
                if newly:
                    self._reconcile_bridge(conn, candidate, mint, btime, birth_time, subprov, treasury)
                return "CREATE", {
                    "mint": mint, "subprov": subprov, "treasury": treasury,
                    "create_time": btime, "btl": btl, "wrap_sig": wrap_sig,
                    "create_sig": sig, "newly": newly, "create_slot": create_slot,
                    "bonding_curve": extra.get("bonding_curve"),
                    "associated_bonding_curve": extra.get("associated_bonding_curve"),
                    "mint_source": extra.get("mint_source"),
                    "program_log_seen_at": timing.get("program_log_seen_at") or ws_seen_at,
                    "program_log_context_slot": timing.get("program_log_context_slot"),
                    "tx_slot": create_slot,
                    "program_fetch_started_at": timing.get("program_fetch_started_at"),
                    "program_tx_fetched_at": timing.get("program_tx_fetched_at"),
                    "handoff_to_canonical_at": timing.get("handoff_to_canonical_at"),
                    "candidate_registered_at": timing.get("candidate_registered_at"),
                    "catchup_0_submit_at": timing.get("catchup_0_submit_at"),
                    "catchup_0_rpc_started_at": timing.get("catchup_0_rpc_started_at"),
                    "catchup_0_rpc_done_at": timing.get("catchup_0_rpc_done_at"),
                    "catchup_0_process_started_at": timing.get("catchup_0_process_started_at"),
                    "get_transaction_started_at": get_transaction_started_at,
                    "ws_seen_at": ws_seen_at, "tx_fetched_at": tx_fetched_at,
                    "mint_extracted_at": mint_extracted_at,
                    "record_launch_started_at": record_launch_started_at,
                    "record_launch_committed_at": record_launch_committed_at,
                    "canonical_fetch_skipped": canonical_fetch_skipped,
                    "duplicate_fetch_count": duplicate_fetch_count,
                    "detection_source": detection_source,
                    "detection_delay_s": detection_delay_s,
                }
            finally:
                conn.close()
        if _tx_is_swap(tx):
            # reverse-direction swarm attribution: capture the mint this swarm wallet BOUGHT,
            # from the tx we already have (zero extra RPC), so it can be linked to its launch.
            return "SWAP", {"swap_mint": _swap_target_mint(tx)}
        return None, None

    # ---- shared candidate-sig processor (WS notification AND catch-up) ------
    async def process_candidate_sig(self, candidate, sig, tx_data=None,
                                    detection_source="LIVE_STREAM", timing=None):
        """Process ONE candidate signature: CREATE → record launch + emit + teardown;
        SWAP → BUY_SWARM + unsubscribe. Idempotent via self._seen and record_launch's
        INSERT OR IGNORE. Used by both the live WS notification and the catch-up scan, so a
        CREATE that fired before the subscription went live is still caught."""
        if self._seen(candidate, sig):
            return None
        ws_seen_at = time.time()                       # T1 — the candidate sig in hand
        # belt-and-suspenders: if this candidate already fired, don't reprocess at all
        conn = self._ops()
        try:
            done = conn.execute(
                "SELECT 1 FROM wt_watchtower_launches WHERE creator_wallet=? LIMIT 1",
                (candidate,)).fetchone()
        finally:
            conn.close()
        if done:
            return "CREATE"
        # _handle_candidate_tx does the blocking getTransaction + DB writes → run it OFF the
        # event loop so a slow RPC can't freeze recv / keepalive.
        verdict, launch = await _ato_thread(
            self._handle_candidate_tx, candidate, sig, ws_seen_at, detection_source, timing, tx_data)
        if verdict == "CREATE":
            btl = launch.get("btl")
            mode = "INSTANT" if (btl is not None and btl < 60) else ("STAGED" if btl is not None else "?")
            # only emit + teardown when THIS call newly recorded the launch (idempotent)
            if launch.get("newly"):
                _log(f"🚀 WATCHTOWER LAUNCH creator={candidate[:12]}… mint={launch.get('mint')} "
                     f"btl={btl}s [{mode}] detection={launch.get('detection_source')} "
                     f"delay={launch.get('detection_delay_s')}s (src={launch.get('mint_source')})")
                emit_event("WATCHTOWER_LAUNCH_DETECTED", wallet=candidate, related=launch.get("subprov"),
                           token_mint=launch.get("mint"),
                           payload={"create_sig": sig, "treasury": launch.get("treasury"),
                                    "birth_to_launch_s": btl, "mode": mode,
                                    "detection_source": launch.get("detection_source"),
                                    "detection_delay_s": launch.get("detection_delay_s"),
                                    "bonding_curve": launch.get("bonding_curve"),
                                    "mint_source": launch.get("mint_source")})
                alert_emitted_at = time.time()         # T4
                _log_program_create_latency(launch, candidate, alert_emitted_at)
                # X28.0 Phase 8 — direct proof the decoupling works: did this CREATE land
                # after the parent subprov's own WS subscription was already gone? Purely
                # observational; does not affect recording/teardown either way.
                _launch_subprov = launch.get("subprov")
                if _launch_subprov and _launch_subprov not in self.mgr.wallet_kind:
                    self._metric("create_after_parent_unsubscribe")
                    _log(f"✅ CREATE_AFTER_PARENT_UNSUBSCRIBE creator={candidate[:12]}… "
                         f"subprov={_launch_subprov[:12]}… — candidate detection survived parent cleanup")
                # AUDIT phase 1 — off-thread so the realtime path isn't blocked by the
                # buyer-position + curve-replay RPC work.
                self._trigger_audit_phase1(launch, candidate, sig, ws_seen_at, alert_emitted_at)
                await self._teardown_after_create(candidate, launch.get("subprov"))
            # Phase E: feed the CREATE outcome back to the subprov stats (instrumentation only).
            _subprov = launch.get("subprov")
            if _subprov:
                conn = self._ops()
                try:
                    store.record_candidate_outcome(conn, subprov=_subprov, outcome="CREATE")
                finally:
                    conn.close()
            return "CREATE"
        elif verdict == "SWAP":
            swap_mint = (launch or {}).get("swap_mint")
            conn = self._ops()
            try:
                store.close_candidate(conn, candidate, "BUY_SWARM", "swapped")
                # REVERSE-DIRECTION swarm attribution: link this swarm wallet (and its subprov)
                # to the mint it bought, so a later swarm WAVE attaches to its launch in the UI.
                # Zero extra RPC — swap_mint came from the tx already fetched above.
                if swap_mint:
                    store.record_swarm_buy(conn, swarm_wallet=candidate, mint=swap_mint,
                                           swap_sig=sig, observed_at=int(ws_seen_at))
                # Phase E: feed the BUY_SWARM outcome to this candidate's subprov stats.
                cand_row = conn.execute(
                    "SELECT subprov_wallet FROM wt_candidate_websocket_watches "
                    "WHERE candidate_wallet=? ORDER BY detected_at DESC LIMIT 1",
                    (candidate,)).fetchone()
                if cand_row and cand_row[0]:
                    store.record_candidate_outcome(conn, subprov=cand_row[0], outcome="BUY_SWARM")
            finally:
                conn.close()
            await self.mgr.unsubscribe(candidate)
            emit_event("CANDIDATE_CLASSIFIED_BUY_SWARM", wallet=candidate,
                       token_mint=swap_mint, payload={"swap_mint": swap_mint, "swap_sig": sig})
            return "SWAP"
        return None

    # ---- candidate catch-up: an INSTANT launch can CREATE before the candidate
    #      subscription is even live. Immediately after opening the watch, scan the
    #      candidate's most-recent signatures and process any that already happened. ----
    async def catch_up_candidate(self, candidate, limit=CATCHUP_SIG_LIMIT):
        try:
            sigs_raw = await asyncio.wait_for(
                _arpc("getSignaturesForAddress",
                      [candidate, {"limit": limit, "commitment": "confirmed"}]),
                timeout=_budget.NEARRT_RPC_TOTAL_S,
            )
        except asyncio.TimeoutError:
            global _CATCHUP_TIMEOUT_COUNT
            _CATCHUP_TIMEOUT_COUNT += 1
            _log(f"[ProgramWatcher] CANDIDATE_CATCHUP RPC_ERROR {candidate[:12]}… timeout commitment=confirmed")
            return
        except Exception as exc:
            _log(f"[ProgramWatcher] CANDIDATE_CATCHUP RPC_ERROR {candidate[:12]}… commitment=confirmed err={exc}")
            return
        if sigs_raw is None:
            _log(f"[ProgramWatcher] CANDIDATE_CATCHUP RPC_ERROR {candidate[:12]}… commitment=confirmed")
            return
        sigs = sigs_raw if isinstance(sigs_raw, list) else []
        if not sigs:
            _log(f"[ProgramWatcher] CANDIDATE_CATCHUP NO_SIGNATURES {candidate[:12]}… sigs=0")
            return
        # oldest → newest so a CREATE is recorded before any later swap is seen
        for s in sorted([x for x in sigs if not x.get("err")],
                        key=lambda x: x.get("blockTime") or 0):
            sig = s.get("signature")
            if not sig:
                continue
            verdict = await self.process_candidate_sig(
                candidate, sig, detection_source="CANDIDATE_CATCHUP")
            if verdict == "CREATE":
                _log(f"[ProgramWatcher] CANDIDATE_CATCHUP CREATE_FOUND {candidate[:12]}… sig={sig[:12]}…")
                break                                  # creator found; watch torn down

    # ---- subprov-side catch-up: recover a DROPPED/LATE wrap-close notification ----
    #      The subprov's wrap-close logsNotification can be dropped or arrive ~100s late
    #      (WS drop / receive-loop stall). Because the creator wallet is UNKNOWN until we see
    #      the wrap-close, a missed subprov notification delays discovering the creator at all.
    #      This scans an ACTIVE subprov's recent sigs for wrap-closes we haven't processed and
    #      runs the same discover→subscribe→candidate-catch-up flow — turning a ~100s miss into
    #      a few seconds. Polling can't beat a 1s atomic launch, but it makes recovery RELIABLE.
    async def catch_up_subprov(self, subprov, limit=SUBPROV_DURABLE_CATCHUP_LIMIT) -> str:
        """Fetch and process recent signatures for one subprov.

        X24.2 deployment-readiness fix: returns an explicit outcome string
        instead of always returning None regardless of success or failure.
        Callers that need to know whether a genuine inspection happened
        (e.g. subprov_sweep_pass's fairness bookkeeping) MUST branch on this
        return value rather than assuming the call succeeded just because it
        didn't raise.

        Outcomes:
          "SUCCESS"      — the RPC call completed and returned a signature list
                           (possibly empty — an empty list is a genuine, successful
                           inspection that found nothing new, not a failure).
          "RPC_TIMEOUT"  — the RPC call did not complete within budget.
          "RPC_ERROR"    — the RPC call raised an exception.
          "NO_RESULT"    — the RPC call completed but returned None (Helius-side
                           failure surfaced as a null payload rather than an
                           exception/timeout).
        """
        # X24.2.1 Phase 1 — per-stage timing instrumentation (additive only, no
        # ordering/fairness change). Measures exactly where wall-clock time goes
        # inside one catch_up_subprov() call, to prove or disprove the "thread-
        # pool contention" hypothesis rather than assume it.
        _stage_t0 = time.time()
        conn = self._ops()
        try:
            last_seen = store.subprov_cursor(conn, subprov)
        finally:
            conn.close()
        _cursor_lookup_ms = round((time.time() - _stage_t0) * 1000, 1)
        params = {"limit": limit, "commitment": "confirmed"}
        if last_seen:
            params["until"] = last_seen
        _getsigs_t0 = time.time()
        try:
            sigs_raw = await asyncio.wait_for(
                _arpc("getSignaturesForAddress", [subprov, params]),
                timeout=_budget.NEARRT_RPC_TOTAL_S,
            )
        except asyncio.TimeoutError:
            global _CATCHUP_TIMEOUT_COUNT
            _CATCHUP_TIMEOUT_COUNT += 1
            _log(f"⚠ subprov catch-up sig fetch RPC_ERROR {subprov[:12]}… timeout commitment=confirmed")
            return "RPC_TIMEOUT"
        except Exception as exc:
            _log(f"⚠ subprov catch-up sig fetch RPC_ERROR {subprov[:12]}… commitment=confirmed: {exc}")
            return "RPC_ERROR"
        _getsigs_ms = round((time.time() - _getsigs_t0) * 1000, 1)
        if sigs_raw is None:
            _log(f"⚠ subprov catch-up sig fetch RPC_ERROR {subprov[:12]}… commitment=confirmed")
            return "NO_RESULT"
        sigs = sigs_raw if isinstance(sigs_raw, list) else []

        clean = [x for x in sigs if isinstance(x, dict) and x.get("signature") and not x.get("err")]
        if len(clean) >= limit:
            self._metric("subprov_sig_gap_detected")
            _log(f"⚠ subprov_sig_gap_detected {subprov[:12]}… fetched limit={limit}; cursor may lag")
        # `clean` is newest→oldest (Solana getSignaturesForAddress order); index 0 = newest.
        # X24.7 — processing order is now policy-driven (default ALTERNATING newest/oldest,
        # evidence: full-population replay of every reconstructable confirmed launch showed
        # this order improves both median AND P95 inspections-to-creator vs the prior
        # oldest-first order). This is a REORDERING only: _order_signature_indices always
        # returns a strict permutation of every index, so every signature in `clean` is still
        # processed exactly once per cycle regardless of policy — never fewer, never skipped.
        #
        # Cursor correctness: subprov_sig_mark_done() (the pre-X24.7 path) unconditionally
        # overwrites the durable cursor on every call, and was only correct because the old
        # oldest-first loop guaranteed the LAST call was for the newest signature. Under any
        # reordering that is no longer true (verified: the alternating sequence's last-visited
        # index is always somewhere in the middle of the range, never the newest end). So this
        # loop now calls _process_subprov_sig_durable(..., advance_cursor=False) — which still
        # marks each signature's retry row DONE on success, exactly as before, but does NOT
        # touch wt_subprov_sig_cursor — and the cursor is advanced explicitly, exactly once,
        # after the whole batch, to the newest signature that was ACTUALLY successfully
        # processed (not merely the newest signature in the fetched batch — if the newest
        # signature itself failed, the cursor must not skip past it, matching the pre-X24.7
        # behaviour where a failed signature never advances the cursor either).
        _sigproc_t0 = time.time()
        _sigs_processed = 0
        _newest_done_idx = None       # index into `clean` of the newest SUCCESSFULLY processed sig
        _newest_done_sig = None
        _newest_done_slot = None
        order = _order_signature_indices(len(clean))

        # X65.29/X65.31 — bounded-concurrency RPC PREFETCH stage. Fetches
        # getTransaction for every signature in `order` concurrently (capped
        # at SUBPROV_SIGNATURE_CONCURRENCY in flight), completely independent
        # of SWEEP_CONCURRENCY (which bounds SESSIONS, not signatures within
        # one session). This is pure I/O with no shared mutable state — safe
        # to run out of order. The DURABLE processing loop immediately below
        # still runs serially, in the original chronological `order`, exactly
        # as before X65.29 — only the RPC round-trip that _handle_subprov_tx
        # used to do inline is moved earlier and made concurrent. See
        # X65.28's ordering audit: _handle_subprov_tx's only order-sensitive
        # read (prior_creates -> PRE_CREATE/POST_CREATE phase, used solely for
        # a ProgramWatcher-arming latency heuristic) is preserved exactly
        # because the durable call itself is still made one at a time.
        #
        # X65.31 — submission now goes through _ato_prefetch_thread (the
        # DEDICATED _subprov_prefetch_executor), not _ato_thread (the shared
        # asyncio default executor). X65.30's root-cause audit measured a
        # representative slow batch where 95.3% of rpc_fetch_ms was executor
        # ADMISSION delay (shared pool saturated at 12/12 threads by
        # unrelated durable-processing/CDC/websocket work), not RPC
        # execution time itself -- this isolates prefetch from that
        # contention. executor_queue_ms/rpc_execution_ms are now tracked
        # per-signature and summed, so a future slow batch can be attributed
        # to one or the other directly from the log line, without needing
        # another manual audit.
        _batch_size = len(order)
        _fetch_started_at = time.time()
        _prefetch_semaphore = asyncio.Semaphore(max(1, SUBPROV_SIGNATURE_CONCURRENCY))
        _prefetch_timed_out = 0
        _prefetch_failed = 0
        _prefetch_late = 0
        _total_queue_wait_ms = 0.0
        _total_rpc_exec_ms = 0.0

        async def _prefetch_one(idx):
            nonlocal _prefetch_timed_out, _prefetch_failed, _prefetch_late
            nonlocal _total_queue_wait_ms, _total_rpc_exec_ms
            s = clean[idx]
            sig = s.get("signature")
            if not sig:
                return idx, None
            async with _prefetch_semaphore:
                try:
                    result, queue_wait_ms, exec_ms = await _ato_prefetch_thread(
                        lambda _sig=sig: self._get_subprov_tx_fast_retry(subprov, _sig))
                except asyncio.TimeoutError:
                    _prefetch_timed_out += 1
                    return idx, None
                except Exception:
                    _prefetch_failed += 1
                    return idx, None
                _total_queue_wait_ms += queue_wait_ms
                _total_rpc_exec_ms += exec_ms
                if (queue_wait_ms + exec_ms) > 5000:
                    _prefetch_late += 1
                return idx, result

        _prefetched = dict(await asyncio.gather(*(_prefetch_one(idx) for idx in order)))
        _fetch_ms = round((time.time() - _fetch_started_at) * 1000, 1)
        _successful_prefetch = sum(1 for v in _prefetched.values() if v is not None and v[0])
        if _batch_size:
            _prefetch_stats = _prefetch_executor_stats()
            _log(f"⏱ signature_batch subprov={subprov[:12]}… batch_size={_batch_size} "
                 f"concurrency={SUBPROV_SIGNATURE_CONCURRENCY} prefetch_total_ms={_fetch_ms} "
                 f"executor_queue_ms={round(_total_queue_wait_ms, 1)} "
                 f"rpc_execution_ms={round(_total_rpc_exec_ms, 1)} "
                 f"successful={_successful_prefetch} timed_out={_prefetch_timed_out} "
                 f"failed={_prefetch_failed} late_results={_prefetch_late} "
                 f"throughput_sig_per_s={round(_batch_size / (_fetch_ms / 1000.0), 2) if _fetch_ms else 0.0} "
                 f"prefetch_executor={_prefetch_stats}")

        _processing_started_at = time.time()
        for idx in order:
            s = clean[idx]
            sig = s.get("signature")
            if not sig:
                continue
            try:
                # X24.2.3 Phase 1 — executor-queue wait: the gap between submitting
                # this call to the shared default ThreadPoolExecutor and the worker
                # thread actually starting it. Separates "waiting for a free thread"
                # from "the work itself was slow" — a distinction the per-signature
                # timing in X24.2.1 could not make (it only measured wall-clock from
                # inside the call, after the thread had already started).
                _submit_at = time.time()
                _exec_wait_holder = {}
                _prefetched_tx = _prefetched.get(idx)
                def _timed_call(_subprov=subprov, _sig=sig, _slot=s.get("slot"), _pf=_prefetched_tx):
                    _exec_wait_holder["wait_ms"] = round((time.time() - _submit_at) * 1000, 1)
                    return self._process_subprov_sig_durable(
                        _subprov, _sig, slot=_slot, source="CATCHUP", advance_cursor=False,
                        prefetched_tx=_pf)
                new_watches = await _ato_thread(_timed_call)
                _exec_wait_ms = _exec_wait_holder.get("wait_ms", 0.0)
                if _exec_wait_ms > 100:
                    _log(f"⏲ executor_wait {subprov[:12]}… sig={sig[:12]}… wait_ms={_exec_wait_ms}")
            except Exception:
                continue
            _sigs_processed += 1
            # Track the newest (lowest `clean` index) signature seen so far among
            # those that completed without raising — this is what the cursor
            # advances to, regardless of the order they were actually visited in.
            if _newest_done_idx is None or idx < _newest_done_idx:
                _newest_done_idx = idx
                _newest_done_sig = sig
                _newest_done_slot = s.get("slot")
            for item in new_watches:
                if isinstance(item, tuple) and item[0] == "UNSUBSCRIBE":
                    await self.mgr.unsubscribe(item[1])
        _processing_ms = round((time.time() - _processing_started_at) * 1000, 1)
        _cursor_commit_ms = 0.0
        if _newest_done_sig is not None:
            _cursor_commit_t0 = time.time()
            _cursor_conn = self._ops()
            try:
                _bt_row = _cursor_conn.execute(
                    "SELECT wrap_close_time FROM wt_candidate_websocket_watches "
                    "WHERE subprov_wallet=? AND wrap_close_signature=? "
                    "ORDER BY detected_at DESC LIMIT 1",
                    (subprov, _newest_done_sig)).fetchone()
                _block_time = _bt_row[0] if _bt_row else None
                store.subprov_sig_advance_cursor(
                    _cursor_conn, subprov=subprov, signature=_newest_done_sig,
                    slot=_newest_done_slot, block_time=_block_time)
            finally:
                _cursor_conn.close()
            _cursor_commit_ms = round((time.time() - _cursor_commit_t0) * 1000, 1)
        _sigproc_ms = round((time.time() - _sigproc_t0) * 1000, 1)
        _total_ms = round((time.time() - _stage_t0) * 1000, 1)
        # X65.29 — throughput instrumentation, per the rollout-guardrail
        # requirement to PROVE concurrency improves throughput rather than
        # merely increasing RPC pressure: batch size/concurrency actually
        # used, RPC-fetch vs durable-processing time split out, per-outcome
        # counts, and signatures/second for this batch.
        _batch_throughput_sig_per_s = round(_batch_size / (_total_ms / 1000.0), 2) if _total_ms else 0.0
        self._last_catchup_timing = {
            "subprov": subprov, "cursor_lookup_ms": _cursor_lookup_ms,
            "getsigs_ms": _getsigs_ms, "sigs_fetched": len(clean),
            "sigs_processed": _sigs_processed, "sigproc_ms": _sigproc_ms,
            "sigproc_ms_per_sig": round(_sigproc_ms / _sigs_processed, 1) if _sigs_processed else 0.0,
            "total_ms": _total_ms,
            "signature_batch_size": _batch_size,
            "signature_concurrency": SUBPROV_SIGNATURE_CONCURRENCY,
            # X65.31 — split out of the old single rpc_fetch_ms: executor_queue_ms
            # (admission delay on the dedicated prefetch pool) vs
            # rpc_execution_ms (actual getTransaction wall-clock time), summed
            # across every signature in the batch. prefetch_total_ms is kept
            # as the overall wall-clock time for the whole concurrent stage
            # (>= max(executor_queue_ms, rpc_execution_ms) since work overlaps
            # across the SUBPROV_SIGNATURE_CONCURRENCY-bounded semaphore).
            "prefetch_total_ms": _fetch_ms,
            "executor_queue_ms": round(_total_queue_wait_ms, 1),
            "rpc_execution_ms": round(_total_rpc_exec_ms, 1),
            "prefetch_executor_stats": _prefetch_executor_stats(),
            "processing_ms": _processing_ms,
            "batch_duration_ms": _total_ms,
            "successful_signatures": _successful_prefetch,
            "timed_out_signatures": _prefetch_timed_out,
            "failed_signatures": _prefetch_failed,
            "cursor_commit_ms": _cursor_commit_ms,
            "throughput_sig_per_s": _batch_throughput_sig_per_s,
        }
        if _total_ms > 1000:  # only log the expensive ones to avoid flooding
            _log(f"⏱ catch_up_subprov timing {subprov[:12]}… cursor={_cursor_lookup_ms}ms "
                 f"getsigs={_getsigs_ms}ms sigs_fetched={len(clean)} sigs_processed={_sigs_processed} "
                 f"sigproc_total={_sigproc_ms}ms sigproc_per_sig={self._last_catchup_timing['sigproc_ms_per_sig']}ms "
                 f"prefetch_total_ms={_fetch_ms} executor_queue_ms={round(_total_queue_wait_ms, 1)} "
                 f"rpc_execution_ms={round(_total_rpc_exec_ms, 1)} processing_ms={_processing_ms} "
                 f"cursor_commit_ms={_cursor_commit_ms} throughput_sig_per_s={_batch_throughput_sig_per_s} "
                 f"total={_total_ms}ms")
                    # new_watches is usually [] now; sentinels are legacy BUY_SWARM gate.
        return "SUCCESS"

    # ---- launch audit phase 1 (off-thread) ---------------------------------
    def _trigger_audit_phase1(self, launch, creator, create_sig, ws_seen_at, alert_emitted_at):
        """Fire the immediate audit capture in a daemon thread.

        The PENDING sentinel row was already written by insert_pending_audit_row() on the
        detection conn before this is called — so this thread only ever UPDATEs.  On
        OperationalError (WAL contention) _upsert retries internally; if all retries
        exhaust, _mark_failed records the failure rather than losing the row silently.
        """
        mint = launch.get("mint")
        def _run():
            try:
                from src.core import launch_audit
                launch_audit.capture_phase1(
                    mint=mint, creator=creator, treasury=launch.get("treasury"),
                    subprov=launch.get("subprov"), create_signature=create_sig,
                    create_slot=launch.get("create_slot"), create_time=launch.get("create_time"),
                    ws_seen_at=ws_seen_at,
                    program_log_context_slot=launch.get("program_log_context_slot"),
                    program_log_seen_at=launch.get("program_log_seen_at"),
                    tx_slot=launch.get("tx_slot"),
                    program_fetch_started_at=launch.get("program_fetch_started_at"),
                    program_tx_fetched_at=launch.get("program_tx_fetched_at"),
                    handoff_to_canonical_at=launch.get("handoff_to_canonical_at"),
                    get_transaction_started_at=launch.get("get_transaction_started_at"),
                    tx_fetched_at=launch.get("tx_fetched_at"),
                    mint_extracted_at=launch.get("mint_extracted_at"),
                    record_launch_started_at=launch.get("record_launch_started_at"),
                    record_launch_committed_at=launch.get("record_launch_committed_at"),
                    canonical_fetch_skipped=launch.get("canonical_fetch_skipped"),
                    duplicate_fetch_count=launch.get("duplicate_fetch_count"),
                    alert_emitted_at=alert_emitted_at)
            except Exception as e:
                # PENDING row already exists — record failure visibly rather than losing it.
                import traceback
                err = traceback.format_exc()
                print(f"[WS_CASCADE] audit phase1 failed mint={mint}: {e}\n{err}", flush=True)
                try:
                    from src.core import launch_audit
                    launch_audit._mark_failed(mint, type(e).__name__, str(e))
                except Exception as _mf_e:
                    print(f"[WS_CASCADE] _mark_failed also failed mint={mint}: {_mf_e}", flush=True)
        import threading
        threading.Thread(target=_run, daemon=True, name="audit-phase1").start()

    # ---- reconcile bridge: keep existing OPS tables consistent -------------
    def _reconcile_bridge(self, conn, creator, mint, launched_at, funded_at, subprov, treasury):
        """One launch truth. Mark wt_wrap_close_candidates FIRED, upsert wt_ops_v2_creators,
        and classify the INSTANT/STAGED mode via the existing helper — so creator panels +
        KPIs reflect the cascade launch without a second ledger."""
        try:
            conn.execute("UPDATE wt_wrap_close_candidates SET state='FIRED' WHERE creator=?", (creator,))
        except Exception:
            pass
        try:
            real_mint = mint or f"launched:{creator}"
            # wt_ops_v2_creators PK = (operation_uuid, creator_wallet, token_mint) — resolve/create
            # the treasury's operation so the row is consistent with the OPS graph.
            op_uuid = None
            try:
                from src.core.wrap_close_detector import ensure_operation_for_treasury
                if treasury:
                    op_uuid = ensure_operation_for_treasury(conn, treasury, subprov=subprov)
            except Exception:
                op_uuid = None
            if not op_uuid:
                # FULL address in the fallback op key — a truncated prefix could merge two
                # distinct treasuries' creators under one operation_uuid.
                op_uuid = f"ws-cascade:{treasury or subprov or creator}"
            conn.execute(
                "INSERT OR IGNORE INTO wt_ops_v2_creators "
                "(operation_uuid, creator_wallet, token_mint, migration_time) VALUES (?,?,?,?)",
                (op_uuid, creator, real_mint, launched_at))
            conn.execute(
                "UPDATE wt_ops_v2_creators SET migration_time=COALESCE(migration_time,?) "
                "WHERE creator_wallet=? AND token_mint=?",
                (launched_at, creator, real_mint))
        except Exception:
            pass
        try:
            from src.core.operation_armed import _classify_creator_mode
            if funded_at and launched_at:
                _classify_creator_mode(conn, creator, funded_at, launched_at)
        except Exception:
            pass
        conn.commit()

    # ---- teardown after a CREATE -------------------------------------------
    async def _teardown_after_create(self, creator, subprov):
        """Post-CREATE teardown — three phases:
          1. Unsubscribe the creator (single-use wallet, done).
          2. Classify + close sibling candidates (co-provisioned wallets).
          3. Transition subprov session to POST_CREATE_ACTIVE for 120s continuation
             window instead of closing immediately. The subprov WS subscription stays
             live. After 120s (extended by any new fan-out), drop to INTEL_ONLY and
             unsubscribe. The session itself stays ACTIVE for 4h operation-grouping.
        """
        global _CLEANUP_COUNT
        await self.mgr.unsubscribe(creator)
        conn = self._ops()
        try:
            if subprov:
                for (sib,) in store.siblings_of(conn, subprov, creator):
                    state, reason = await _ato_thread(_classify_sibling, sib)
                    store.close_candidate(conn, sib, state, reason)
                    await self.mgr.unsubscribe(sib)
                    if state == "BUY_SWARM":
                        emit_event("CANDIDATE_CLASSIFIED_BUY_SWARM", wallet=sib, related=subprov)
                # Transition session to POST_CREATE_ACTIVE — do NOT close or unsubscribe.
                # 40% of observed launches had fan-out activity within 120s of CREATE.
                store.set_session_post_create(conn, subprov)
                _log(f"⏱ POST_CREATE_ACTIVE {subprov[:12]}… — keeping armed 120s")
                emit_event("SUBPROV_POST_CREATE_ACTIVE", wallet=subprov, related=creator)
                # Schedule the 120s watchdog off-loop so the event loop isn't blocked.
                asyncio.ensure_future(self._post_create_watchdog(subprov))
            _CLEANUP_COUNT += 1
            emit_event("WEBSOCKET_CLEANUP_COMPLETED", wallet=creator, related=subprov,
                       payload={"cleanup_count": _CLEANUP_COUNT})
        finally:
            conn.close()

    async def _post_create_watchdog(self, subprov: str):
        """120s armed continuation window after CREATE. If more fan-out is observed
        (tracked via self._post_create_last_fanout), the deadline resets. When the
        window expires with no new fan-out, drop to INTEL_ONLY + unsubscribe subprov.
        The session stays ACTIVE for 4h operation-grouping — only monitoring drops."""
        _POST_CREATE_ARMED_S = int(os.environ.get("WS_POST_CREATE_ARMED_S", "120"))
        # Track the last-fanout time for this subprov so fan-out events can extend the window.
        self._post_create_last_fanout[subprov] = time.time()
        while True:
            await asyncio.sleep(10)
            last = self._post_create_last_fanout.get(subprov, 0)
            if time.time() - last >= _POST_CREATE_ARMED_S:
                break
        # Window expired — drop to INTEL_ONLY
        conn = self._ops()
        try:
            store.set_session_intel_only(conn, subprov)
        finally:
            conn.close()
        self._post_create_last_fanout.pop(subprov, None)
        await self.mgr.unsubscribe(subprov)
        _log(f"🔕 POST_CREATE→INTEL_ONLY {subprov[:12]}… — armed window closed")
        # PW_LIFECYCLE_V2: evict candidates for this subprov now that the armed window has
        # closed. Historical proof (33 launches): zero second-creator wrap-closes ever
        # occurred after CREATE for the same subprov — post-CREATE activity is exclusively
        # BUY_SWARM, EXPIRED_SIBLING, or noise. Early eviction lets ProgramWatcher close
        # naturally (zero-candidate check) instead of waiting for the 30-min TTL.
        # Feature-flagged: PW_POST_CREATE_EVICT_ENABLED=0 dry-runs (logs only, no eviction).
        _pw = getattr(self, "_prog_watcher", None)
        if _pw is not None:
            _evict_enabled = os.environ.get("PW_POST_CREATE_EVICT_ENABLED", "0") == "1"
            _candidates_for_subprov = [
                w for w, m in _pw.active_candidates.items() if m.get("subprov") == subprov
            ]
            n = len(_candidates_for_subprov)
            if _evict_enabled:
                evicted = _pw.evict_by_subprov(subprov)
                _log(f"[PW_LIFECYCLE] PW_WATCHDOG_COMPLETE subprov={subprov[:12]}…")
                _log(f"[PW_LIFECYCLE] PW_EVICT_SUBPROV subprov={subprov[:12]}… wallets_removed={evicted}")
                remaining = len(_pw.active_candidates)
                if remaining == 0:
                    _log("[PW_LIFECYCLE] PW_ZERO_CANDIDATES closing_programwatcher")
                    # _close_stream is triggered automatically by evict_by_subprov when
                    # active_candidates reaches zero — no explicit call needed here.
            else:
                _log(f"[PW_LIFECYCLE] PW_WATCHDOG_COMPLETE subprov={subprov[:12]}… (dry-run)")
                _log(f"[PW_LIFECYCLE] PW_EVICT_SUBPROV_DRY_RUN subprov={subprov[:12]}… would_remove={n}")

    # ---- TTL cleanup pass --------------------------------------------------
    def _drain_pending_sessions(self):
        """Retry session writes that failed with DB lock. Runs every 30s in the maintenance loop.
        Replays original detection context — not subject to current TEMP_SUBPROV_ENFORCE flag."""
        conn = self._ops()
        try:
            written, remaining, superseded = store.drain_pending_sessions(conn)
            if written:
                _log(f"🔁 PENDING_SESSION_REPLAYED: {written} written, {remaining} remaining")
            if superseded:
                _log(f"↩ PENDING_SESSION_SUPERSEDED: {superseded} (session opened via another path)")
            if remaining:
                counts = store.pending_session_counts(conn)
                if counts["critical_pending"]:
                    _log(f"🚨 CRITICAL pending session writes: {counts['critical_pending']} (≥{store.HIGH_VALUE_PROVISION_SOL}◎)")
        except Exception as e:
            _log(f"⚠️ pending session drain error: {e}")
        finally:
            conn.close()

    # X77.2 — retry queue for watchtower_events / wt_webhook_hits writes that
    # failed with a transient (contention) error. Same cadence/pattern as
    # _drain_pending_sessions above, deliberately kept as a separate method
    # (separate table, separate write path, separate failure class) rather
    # than merged into it.
    def _drain_pending_cascade_events(self):
        conn = self._ops()
        try:
            result = store.drain_pending_cascade_events(conn)
            if result["written"]:
                _log(f"🔁 PENDING_CASCADE_EVENT_REPLAYED: {result['written']} written, "
                     f"{result['remaining']} remaining")
            if result["remaining"]:
                counts = store.pending_cascade_event_counts(conn)
                if counts["FAILED"]:
                    _log(f"⚠️ {counts['FAILED']} cascade event(s) permanently failed on retry "
                         f"(non-transient error) — see wt_pending_cascade_events.last_error")
        except Exception as e:
            _log(f"⚠️ pending cascade event drain error: {e}")
        finally:
            conn.close()

    async def cleanup_pass(self):
        dropped, stale_hot = self.mgr.sweep_stale_pending()
        for w, kind, attempts in dropped:
            if attempts < COLD_SUB_RETRY_MAX:
                self.mgr._cold_retry_count[w] = attempts + 1
                _log(f"⚠ cold pending subscription {w[:14]}… unconfirmed >{COLD_SUB_STALE_SEC}s "
                     f"— resubscribing (attempt {attempts + 1}/{COLD_SUB_RETRY_MAX})")
                await self.mgr.subscribe(w, kind)
            else:
                global _COLD_RETRY_EXHAUSTED_COUNT
                _COLD_RETRY_EXHAUSTED_COUNT += 1
                self.mgr._exhausted_by_kind[kind] = self.mgr._exhausted_by_kind.get(kind, 0) + 1
                self.mgr._cold_retry_count.pop(w, None)
                _log(f"⚠ dropped cold pending subscription {w[:14]}… "
                     f"(unconfirmed >{COLD_SUB_STALE_SEC}s, exhausted {COLD_SUB_RETRY_MAX} retries)")
        for w in stale_hot:
            # HOT subscribe not confirmed within 2s — resubscribe immediately
            # (Helius may have dropped it silently; the burst fallback is already running)
            _log(f"🔥 HOT subscribe stale {w[:14]}… — resubscribing")
            await self.mgr.subscribe(w, "hot_subprov")
        conn = self._ops()
        try:
            for (cand,) in store.expire_stale_candidates(conn):
                await self.mgr.unsubscribe(cand)
                emit_event("CANDIDATE_WATCH_EXPIRED", wallet=cand)
            _pw = getattr(self, "_prog_watcher", None)
            for sid, subprov in store.expire_stale_sessions(conn):
                await self.mgr.unsubscribe(subprov)
                _log(f"🗑 session expired/dismissed {subprov[:12]}…")
                emit_event("SUBPROV_SESSION_EXPIRED", wallet=subprov)
                # X28.0 Phase 1/2 — do NOT evict_by_subprov() here. Session TTL expiry means
                # only the PARENT's own WS subscription is dropped (mgr.unsubscribe above);
                # any candidates already armed in ProgramCreateWatcher.active_candidates must
                # survive — their CREATE-detection lifecycle is independent of the parent
                # session (X27.11 Phase 2: matching is purely `creator in active_candidates`
                # against the single global pump.fun stream, with no reference to subprov
                # state). Deleting them here was the confirmed defect: a subprov hitting its
                # 30-min TTL silently destroyed already-armed CREATE coverage. Candidates now
                # expire only via their own TTL (expire_stale_candidates, above) or an
                # explicit CREATE_DETECTED/invalidation outcome.
                if _pw:
                    _preserved = sum(1 for m in _pw.active_candidates.values() if m.get("subprov") == subprov)
                    if _preserved:
                        self._metric("parent_cleanup_candidates_preserved", _preserved)
                        _log(f"🛡 PARENT_CLEANUP_PRESERVED subprov={subprov[:12]}… candidates_kept={_preserved}")
            # ── Phase D: reject unproven PROVISION_CANDIDATEs after 2h ──────
            # NOTE (X28.0 Phase 1 audit): reject_unproven_sessions()'s own query already
            # NOT EXISTS-guards on wt_subprov_evidence and any WATCHING/FIRED_CREATE/BUY_SWARM
            # row in wt_candidate_websocket_watches — a session only reaches this branch with
            # ZERO legitimate candidates, so evict_by_subprov() here is a defensive no-op, not
            # a load-bearing eviction. Left in place; do not remove the underlying query's
            # NOT EXISTS guards without re-auditing this call.
            for sid, subprov in store.reject_unproven_sessions(conn):
                await self.mgr.unsubscribe(subprov)
                _log(f"🚫 REJECTED {subprov[:12]}… — PROVISION_CANDIDATE, no wrap-close in 2h")
                emit_event("SUBPROV_CANDIDATE_REJECTED", wallet=subprov)
                if _pw:
                    _pw.evict_by_subprov(subprov)
            # ── Soft-tag operational spend proxies (retrospective, zero pipeline impact) ─
            _tagged = store.tag_operational_spend_proxies(conn)
            if _tagged:
                _log(f"🏷 OPERATIONAL_SPEND_PROXY tagged {_tagged} expired zero-fanout session(s)")
            # ── CDC inactivity TTL ─────────────────────────────────────────────
            cutoff = int(time.time()) - CDC_INACTIVITY_TTL_SEC
            for cdc_w in store.expire_inactive_cdcs(conn, cutoff):
                await self.mgr.unsubscribe(cdc_w)
                _log(f"🔵 CDC inactivity unsubscribe {cdc_w[:12]}…")
                emit_event("CDC_INACTIVITY_EXPIRED", wallet=cdc_w)
        finally:
            conn.close()

    # ---- subprov sweep: catch-up every ACTIVE subprov (reliability backstop) ----
    async def subprov_sweep_pass(self):
        """Run catch_up_subprov over ACTIVE subprovs to recover any wrap-close whose WS
        notification dropped/stalled (or, while WS_SUBPROV_WATCH_ENABLED=0, whose WS
        notification never existed at all — this sweep is currently the PRIMARY
        detection path, not a backstop; see X24.1/X24.2 reconciliation).

        X24.2 Phase 2 — replaces the old unordered active_sessions()[:MAX_ACTIVE_SUBPROVS]
        slice (which could let a session outside the arbitrary top-N expire without ever
        being inspected — the proven AWiaGsus-class coverage defect) with a deterministic,
        durable fair scheduler: never-swept-first (soonest expiry), then
        least-recently-swept (soonest expiry), id as tie-breaker. The ordering survives a
        process restart because it is derived entirely from durable columns
        (last_swept_at/sweep_count/first_swept_at), not in-memory state.

        Still bounded to MAX_ACTIVE_SUBPROVS RPC calls per cycle — this is a rotation
        fix, not a cap increase. Given hundreds of eligible sessions can exist
        concurrently, full coverage is achieved over successive cycles (bounded by
        ceil(eligible / cap) * SUBPROV_SWEEP_SEC), not within a single cycle.

        Deployment-readiness fix (X24.2 review): mark_swept() is now called ONLY
        when catch_up_subprov() reports "SUCCESS" — i.e. the RPC call genuinely
        completed and was actually inspected, even if it found zero new signatures
        (an empty result is a real, successful inspection, not a failure). A
        session whose inspection failed (RPC_TIMEOUT / RPC_ERROR / NO_RESULT) is
        deliberately left un-swept so it stays at (or near) the front of the
        never-swept/least-recently-swept queue and is retried on a future cycle,
        rather than being falsely marked as inspected and pushed to the back of
        the fairness queue. This is the fix for the confirmed defect where
        mark_swept() previously fired unconditionally regardless of outcome.

        X24.2.1 Phase 3 — bounded concurrency. Phase 1 instrumentation PROVED
        (not assumed) the dominant cost is sequential per-signature processing
        inside catch_up_subprov itself (median ~8.3s/session, up to 50
        sequential getTransaction calls per session), NOT executor contention
        (measured queue_depth=0 throughout). Ten sessions run with at most
        SWEEP_CONCURRENCY (default 4) in flight at once via a semaphore — the
        SELECTION and PRIORITY ORDER from fair_sweep_candidates() is completely
        unchanged (still exactly the same rows, same order); only how many of
        them are inspected in parallel changes. mark_swept() is still called
        ONLY on a SUCCESS outcome, exactly as before, from each session's own
        independent branch — bounded concurrency does not change that
        contract, it just lets several independent branches run at once.
        No session can be double-selected within one cycle (rows come from a
        single SELECT), and this whole method is guarded against overlapping
        with a PRIOR cycle by the caller (_sweep_in_progress)."""
        conn = self._ops()
        try:
            rows = store.fair_sweep_candidates(conn, limit=MAX_ACTIVE_SUBPROVS)
            coverage = store.sweep_coverage_snapshot(conn, cap=MAX_ACTIVE_SUBPROVS)
        finally:
            conn.close()
        self._metric("sweep_cycles_run")
        self._subprov_sig_metrics["sweep_eligible_sessions_last_cycle"] = coverage["eligible_sessions"]
        self._subprov_sig_metrics["sweep_selected_last_cycle"] = len(rows)
        self._subprov_sig_metrics["sweep_never_swept_gauge"] = coverage["never_swept"]
        self._subprov_sig_metrics["sweep_expiring_60s_never_swept_gauge"] = coverage["expiring_within_60s_never_swept"]
        self._subprov_sig_metrics["sweep_swept_within_30s_gauge"] = coverage["swept_within_30s"]
        self._subprov_sig_metrics["sweep_duplicate_sweep_gauge"] = coverage["sessions_swept_more_than_once"]
        sweep_started_at = time.time()
        failed_outcomes = 0
        per_session_timings: list[dict] = []
        semaphore = asyncio.Semaphore(SWEEP_CONCURRENCY)

        async def _inspect_one(row):
            nonlocal failed_outcomes
            session_id, subprov = row[0], row[1]
            async with semaphore:
                _queue_wait_ms = round((time.time() - sweep_started_at) * 1000, 1)
                _session_t0 = time.time()
                outcome = await self.catch_up_subprov(subprov)
                _catchup_ms = round((time.time() - _session_t0) * 1000, 1)
            self._metric("sweep_rpc_requests_issued")
            _mark_swept_ms = 0.0
            if outcome == "SUCCESS":
                _mark_t0 = time.time()
                _mconn = self._ops()
                try:
                    store.mark_swept(_mconn, session_id)
                finally:
                    _mconn.close()
                _mark_swept_ms = round((time.time() - _mark_t0) * 1000, 1)
            else:
                failed_outcomes += 1
                self._metric(f"sweep_inspection_failed_{outcome.lower()}")
                _log(f"⚠ sweep inspection NOT counted as swept (session_id={session_id} "
                     f"subprov={subprov[:12]}… outcome={outcome}) — will retry on a future cycle")
            per_session_timings.append({
                "session_id": session_id, "subprov": subprov[:12], "outcome": outcome,
                "queue_wait_ms": _queue_wait_ms, "catchup_ms": _catchup_ms,
                "mark_swept_ms": _mark_swept_ms,
                "total_ms": round(_queue_wait_ms + _catchup_ms + _mark_swept_ms, 1),
            })

        # Bounded concurrency, not an unbounded gather over all `rows` — the
        # semaphore caps how many catch_up_subprov() calls (and therefore RPC
        # calls) are in flight simultaneously to exactly SWEEP_CONCURRENCY,
        # regardless of how many rows were selected this cycle.
        await asyncio.gather(*(_inspect_one(row) for row in rows))

        self._subprov_sig_metrics["sweep_last_cycle_duration_ms"] = round((time.time() - sweep_started_at) * 1000, 1)
        self._subprov_sig_metrics["sweep_failed_inspections_last_cycle"] = failed_outcomes
        self._last_sweep_per_session_timings = per_session_timings
        if rows:
            _cycle_ms = self._subprov_sig_metrics['sweep_last_cycle_duration_ms']
            _sum_individual_ms = round(sum(t["total_ms"] for t in per_session_timings), 1)
            _executor_stats = _default_executor_stats()
            _log(f"🧭 sweep cycle: eligible={coverage['eligible_sessions']} selected={len(rows)} "
                 f"failed={failed_outcomes} never_swept={coverage['never_swept']} "
                 f"expiring_60s_unswept={coverage['expiring_within_60s_never_swept']} "
                 f"duration_ms={_cycle_ms} sum_individual_ms={_sum_individual_ms} "
                 f"execution_mode=CONCURRENT(cap={SWEEP_CONCURRENCY}) executor={_executor_stats}")
            for t in sorted(per_session_timings, key=lambda x: -x["total_ms"])[:5]:
                _log(f"  ⏱ session={t['subprov']}… outcome={t['outcome']} "
                     f"queue_wait_ms={t['queue_wait_ms']} catchup_ms={t['catchup_ms']} "
                     f"mark_swept_ms={t['mark_swept_ms']} total_ms={t['total_ms']}")

    async def subprov_sweep_pass_guarded(self):
        """X24.2.1 Phase 3 — overlap guard + decoupling wrapper. Called from
        _maintenance() as a fire-and-forget task (asyncio.ensure_future), NOT
        awaited inline, so a slow sweep cycle can no longer block
        resync_subscriptions/cleanup_pass/subprov_retry_pass/etc. in the same
        loop iteration (the second proven contributor to the 53-90s cycle
        problem — even after bounding concurrency, a sweep that's still
        slower than SUBPROV_SWEEP_SEC would otherwise still stall the rest of
        maintenance every cycle).

        If a previous sweep is still running, this cycle is SKIPPED (not
        queued, not stacked) and counted — never starts a second concurrent
        sweep, which would risk fair_sweep_candidates() selecting/inspecting a
        session that a still-running prior cycle already claimed."""
        if self._sweep_in_progress:
            self._sweep_skipped_overlap_count += 1
            self._subprov_sig_metrics["sweep_skipped_overlap_count"] = self._sweep_skipped_overlap_count
            _log(f"⏭ sweep cycle skipped — previous cycle still running "
                 f"(skipped_overlap_count={self._sweep_skipped_overlap_count})")
            return
        self._sweep_in_progress = True
        try:
            await self.subprov_sweep_pass()
        finally:
            self._sweep_in_progress = False

    def sweep_health_report(self, *, arrival_window_seconds: int = 300) -> dict:
        """X24.2.1 — health semantics required by the sprint: report DEGRADED
        whenever measured throughput is below the measured arrival rate, even
        if every RPC call is individually succeeding (the exact failure mode
        that made X24.2 look correct in isolated tests while failing in live
        validation — 0 failed RPC outcomes, but the backlog still grew).

        Sizes are derived from real durable state (sweep_coverage_snapshot,
        sweep_arrival_rate) plus the in-process counters this cycle already
        maintains — nothing here gates or alters scheduling behaviour, it is
        read-only reporting."""
        conn = self._ops()
        try:
            coverage = store.sweep_coverage_snapshot(conn, cap=MAX_ACTIVE_SUBPROVS)
            arrivals = store.sweep_arrival_rate(conn, window_seconds=arrival_window_seconds)
        finally:
            conn.close()

        cycle_ms = self._subprov_sig_metrics.get("sweep_last_cycle_duration_ms")
        cycles_run = self._subprov_sig_metrics.get("sweep_cycles_run", 0)
        selected_last = self._subprov_sig_metrics.get("sweep_selected_last_cycle", 0)
        failed_last = self._subprov_sig_metrics.get("sweep_failed_inspections_last_cycle", 0)
        succeeded_last = max(selected_last - failed_last, 0)

        inspections_per_minute = None
        if cycle_ms and cycle_ms > 0 and succeeded_last:
            inspections_per_minute = round(succeeded_last / (cycle_ms / 1000.0 / 60.0), 2)

        arrivals_per_minute = arrivals["arrivals_per_minute"]
        backlog = coverage["never_swept"]
        backlog_growth_per_minute = None
        estimated_drain_minutes = None
        degraded = None
        if inspections_per_minute is not None:
            backlog_growth_per_minute = round(arrivals_per_minute - inspections_per_minute, 2)
            degraded = inspections_per_minute < arrivals_per_minute
            if inspections_per_minute > arrivals_per_minute:
                net_drain_per_minute = inspections_per_minute - arrivals_per_minute
                estimated_drain_minutes = round(backlog / net_drain_per_minute, 1) if net_drain_per_minute > 0 else None

        return {
            "measured_at": int(time.time()),
            "sweep_cycles_run": cycles_run,
            "sweep_last_cycle_duration_ms": cycle_ms,
            "inspections_per_minute": inspections_per_minute,
            "arrivals_per_minute": arrivals_per_minute,
            "backlog_never_swept": backlog,
            "backlog_growth_per_minute": backlog_growth_per_minute,
            "estimated_backlog_drain_minutes": estimated_drain_minutes,
            "sweep_concurrency": SWEEP_CONCURRENCY,
            "currently_running_sweep": self._sweep_in_progress,
            "sweep_skipped_overlap_count": self._sweep_skipped_overlap_count,
            "status": (
                "UNKNOWN" if degraded is None else ("DEGRADED" if degraded else "HEALTHY")
            ),
            "note": (
                "DEGRADED means measured inspection throughput is below measured "
                "arrival rate -- the backlog is growing -- even if RPC calls are "
                "succeeding. This is independent of RPC failure/timeout counts."
            ),
        }

    def cascade_write_health_report(self) -> dict:
        """X77.2 — required health surface: queued / retried / failed / dropped
        / succeeded for the watchtower_events / wt_webhook_hits write path.
        Combines the in-process rate counters (event_writer_stats — reset on
        restart) with durable state (wt_pending_cascade_events — survives
        restart) so a fresh process still reports a true PENDING backlog even
        before it has retried anything itself."""
        stats = store.event_writer_stats()
        conn = self._ops()
        try:
            durable_counts = store.pending_cascade_event_counts(conn)
        finally:
            conn.close()
        # DEGRADED (not STALLED/STOPPED — this is a volume signal, not a
        # liveness signal) whenever a durable backlog exists; the maintenance
        # loop drains it every 30s, so a nonzero PENDING count that persists
        # across reports means retries themselves are also failing.
        status = "DEGRADED" if durable_counts["PENDING"] or durable_counts["FAILED"] else "HEALTHY"
        return {
            "measured_at": int(time.time()),
            "succeeded": stats["succeeded"],
            "queued_for_retry": stats["queued_for_retry"],
            "retried_ok": stats["retried_ok"],
            "failed_permanent": stats["failed_permanent"],
            "dropped_queue_full": stats["dropped_queue_full"],
            "durable_pending": durable_counts["PENDING"],
            "durable_written_via_retry": durable_counts["WRITTEN"],
            "durable_superseded": durable_counts["SUPERSEDED"],
            "durable_failed": durable_counts["FAILED"],
            "status": status,
            "note": (
                "queued_for_retry/retried_ok/failed_permanent/dropped_queue_full "
                "are in-process counters (reset on restart). durable_* counts "
                "come from wt_pending_cascade_events and survive restart -- "
                "durable_pending is the number of events not yet successfully "
                "written, either originally or via retry."
            ),
        }

    async def subprov_retry_pass(self):
        conn = self._ops()
        try:
            rows = store.due_subprov_sig_retries(conn, limit=SUBPROV_SIG_RETRY_LIMIT)
        finally:
            conn.close()
        for row in rows:
            subprov, sig, slot = row[0], row[1], row[2]
            try:
                await _ato_thread(
                    self._process_subprov_sig_durable,
                    subprov, sig, slot=slot, source="RETRY")
            except Exception:
                continue


# ── HOT subprov RPC burst fallback ───────────────────────────────────────────
async def _hot_subprov_burst(casc: "Cascade", subprov: str) -> None:
    """RPC burst fallback for HOT_SUBPROV_PREARMED wallets.

    Runs getSignaturesForAddress at 0/1/2/4/8/15/30/60s offsets regardless of
    whether the logsSubscribe was ever confirmed by Helius.  This is the safety
    net that would have caught the SOLAI miss: even with 44–60 pending subscribes
    blocking confirmation, we still see the wrap-close and arm the creator.

    Each poll deduplicates against casc._subprov_seen so we never fetch the same
    sig twice.  Terminates early if the subprov session is no longer ACTIVE.
    """
    if not CANDIDATE_WATCH_ENABLED:
        return   # audit-only mode: no RPC burst, no candidate arming
    loop = asyncio.get_event_loop()
    start = time.time()
    seen_this_burst: set = set()

    def _session_active() -> bool:
        conn = casc._ops()
        try:
            row = conn.execute(
                "SELECT state FROM wt_active_subprov_sessions "
                "WHERE subprov_wallet=? AND state='ACTIVE' LIMIT 1", (subprov,)
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def _burst_poll() -> list:
        """One RPC poll: return new wrap-close sigs not yet seen."""
        sigs = _rpc_get_sigs(subprov, limit=20)
        new = [s for s in sigs if s not in casc._subprov_seen and s not in seen_this_burst]
        seen_this_burst.update(new)
        casc._subprov_seen.update(new)
        return new

    last_schedule = list(HOT_BURST_SCHEDULE)
    for delay in last_schedule:
        elapsed = time.time() - start
        wait = max(0.0, delay - elapsed)
        if wait > 0:
            await asyncio.sleep(wait)
        if _STOP:
            break
        # Stop if session expired or subprov is now confirmed (WS took over)
        active = await loop.run_in_executor(None, _session_active)
        if not active:
            _log(f"🔥 burst stop {subprov[:12]}… — session no longer active at +{delay}s")
            break
        # If subscription confirmed, WS is live — burst can stop early
        if subprov in casc.mgr.wallet_sub:
            _log(f"🔥 burst stop {subprov[:12]}… — WS confirmed at +{delay}s")
            break
        try:
            new_sigs = await asyncio.wait_for(
                loop.run_in_executor(None, _burst_poll),
                timeout=_budget.NEARRT_RPC_TOTAL_S,
            )
        except (asyncio.TimeoutError, Exception) as _e:
            _log(f"🔥 burst poll err {subprov[:12]}… +{delay}s: {_e}")
            continue
        if new_sigs:
            _log(f"🔥 burst hit {subprov[:12]}… +{delay}s — {len(new_sigs)} new sig(s)")
        for sig in new_sigs:
            try:
                new_watches = await _ato_thread(
                    casc._process_subprov_sig_durable, subprov, sig, source="HOT_BURST")
                for item in new_watches:
                    if isinstance(item, tuple) and item[0] == "UNSUBSCRIBE":
                        await casc.mgr.unsubscribe(item[1])
                        continue
                    cand = item
                    # Candidates are matched by ProgramWatcher, not per-wallet WS
                    _log(f"🔥 burst → candidate {cand[:12]}… saved (subprov {subprov[:10]}…)")
            except Exception as _e:
                _log(f"🔥 burst tx err {sig[:12]}… {_e}")


def _rpc_get_sigs(wallet: str, limit: int = 20) -> list:
    """Synchronous getSignaturesForAddress for use in executor threads."""
    result = _rpc("getSignaturesForAddress", [wallet, {"limit": limit, "commitment": "confirmed"}],
                  timeout=_budget.SYNC_RPC_TIMEOUT_S)
    return [e["signature"] for e in (result or []) if e.get("signature")]


# ── async runner ─────────────────────────────────────────────────────────────
async def _heartbeat_loop(get_meta):
    while not _STOP:
        try:
            # Heartbeat lives in wt_ops_v2.db (quiet) — NOT the live DB, which is hot with
            # webhook/API writes and was throwing DB_LOCK_ERROR on every heartbeat. The
            # cascade's tables are here anyway, so it's the natural home; the dashboard reads
            # it from the ops db too.
            meta = get_meta()
            meta["cascade_state"] = _CASCADE_STATE
            def write_heartbeat(c):
                c.execute(
                    """CREATE TABLE IF NOT EXISTS wt_worker_heartbeat (
                        worker_name TEXT PRIMARY KEY, last_seen INTEGER, status TEXT, meta_json TEXT)""")
                c.execute(
                    """INSERT INTO wt_worker_heartbeat (worker_name, last_seen, status, meta_json)
                       VALUES ('ws_cascade', strftime('%s','now'), 'ok', ?)
                       ON CONFLICT(worker_name) DO UPDATE SET
                         last_seen=excluded.last_seen, status=excluded.status, meta_json=excluded.meta_json""",
                    (json.dumps(meta),))
            store.operations_write("ws-cascade-heartbeat", write_heartbeat)
            # Self-watchdog: only fire if LIVE — during SUBSCRIBING/RECONCILING zero subs is expected.
            if _CASCADE_STATE == "LIVE" and meta.get("subs", 0) == 0:
                _log("WATCHDOG ⚠ zero active WS subscriptions — treasury monitoring is dark")
            pending = meta.get("pending", 0)
            if pending >= PENDING_CRITICAL:
                _log(f"WATCHDOG 🔴 CRITICAL {pending} pending subscribe requests — HOT wallets at risk")
            elif pending >= PENDING_WARN_2:
                _log(f"WATCHDOG 🟠 {pending} pending subscribe requests — queue saturated")
            elif pending >= PENDING_WARN_1:
                _log(f"WATCHDOG ⚠ {pending} pending subscribe requests stuck")
        except Exception:
            pass
        await asyncio.sleep(HEARTBEAT_SEC)


async def _deferred_audit_loop():
    """Phase 2: re-visit audited launches at their +5m/+30m/+2h/+24h checkpoints to fill
    peak/outcome/actionability from the snapshot tables. Bounded per tick, off the WS path.
    Reads DB only (no RPC), so it's cheap. Runs in a thread to avoid blocking the loop."""
    import asyncio as _a
    while not _STOP:
        try:
            from src.core import launch_audit

            def _tick():
                # Advance pending checkpoints
                due = launch_audit.due_for_checkpoint(limit=20)
                for mint in due:
                    try:
                        launch_audit.run_phase2(mint)
                    except Exception:
                        pass
                # Reconcile any launches missing an audit row (catches the missing-commit
                # edge case and any future detection-side failures)
                try:
                    r = launch_audit.reconcile_missing(limit=5)
                    if r["total"]:
                        print(f"[WS_CASCADE] audit reconcile: {r}", flush=True)
                except Exception as _re:
                    print(f"[WS_CASCADE] audit reconcile error: {_re}", flush=True)
                return len(due)
            n = await _a.get_event_loop().run_in_executor(None, _tick)
            if n:
                _log(f"audit phase2: advanced {n} launch(es)")
        except Exception as e:
            print(f"[WS_CASCADE] deferred audit error: {e}", flush=True)
        await _a.sleep(60)


async def run_cascade():
    if websockets is None:
        _log("FATAL: `websockets` not installed"); return
    casc = Cascade()
    casc._loop = asyncio.get_event_loop()

    # Phase 1: program-CREATE watcher (shadow mode, gated by env flag)
    prog_watcher: ProgramCreateWatcher | None = None
    if PROGRAM_WATCHER_ENABLED:
        prog_watcher = ProgramCreateWatcher()
        prog_watcher._loop = asyncio.get_event_loop()
        prog_watcher.start_background_tasks()
        _log("[ProgramWatcher] enabled (durable CREATE capture path)")
        # Reload WATCHING candidates from DB — they are lost when the process restarts
        # because active_candidates is in-memory only. Without this, the ProgramWatcher
        # stream fires correctly but no candidates match → every CREATE is silently missed.
        _pw_conn = casc._ops()
        try:
            _pw_rows = _pw_conn.execute(
                "SELECT c.candidate_wallet, c.subprov_wallet, c.treasury_wallet, "
                "c.wrap_close_signature, c.wrap_close_time, c.funding_amount "
                "FROM wt_candidate_websocket_watches c "
                "WHERE c.state='WATCHING' AND c.expires_at > strftime('%s','now') "
                "AND NOT EXISTS ("
                "  SELECT 1 FROM wt_confirmed_treasuries t "
                "  WHERE t.treasury = c.treasury_wallet AND t.no_subscribe = 1"
                ")"
            ).fetchall()
            if _pw_rows:
                _pw_metas = [{"candidate": r[0], "subprov": r[1], "treasury": r[2],
                              "wrap_sig": r[3], "wrap_time": r[4], "amount": r[5],
                              "added_at": 0}  # restored from DB — exclude from catch-up
                             for r in _pw_rows]
                prog_watcher.add_candidates(_pw_metas, _pw_conn)
                _log(f"[ProgramWatcher] reloaded {len(_pw_rows)} WATCHING candidates from DB")
        except Exception as _e:
            _log(f"[ProgramWatcher] candidate reload failed: {_e}")
        finally:
            _pw_conn.close()
    casc._prog_watcher = prog_watcher   # attach so _handle_subprov_tx can reach it
    if prog_watcher:
        prog_watcher._cascade_ref = casc  # back-ref so ProgramWatcher can call process_candidate_sig

    # Dust Observatory — subscribe to known DUST_MARKER wallets.
    # Purely observational: enqueues sigs for off-thread processing, zero influence on detection.
    _dust_markers: list = []
    try:
        from src.core import dust_observatory as _dobs
        _dust_markers = _dobs.init(start_enricher=True)
        casc._dust_markers = set(_dust_markers)
        _log(f"[DustObs] loaded {len(_dust_markers)} dust marker wallet(s)")
    except Exception as _de:
        _log(f"[DustObs] init failed (non-fatal): {_de}")
        casc._dust_markers = set()

    def _meta():
        kinds = casc.mgr.wallet_kind
        pending_by_kind = casc.mgr.pending_count_by_kind()
        ack_stats = casc.mgr.ack_latency_stats()
        # subs_per_sec: confirmed subs over last 60s (derived from ring buffer timestamps)
        base = {
            "subs":      len(casc.mgr.wallet_sub),
            "pending":   len(casc.mgr.pending_req),
            "pending_hot":       pending_by_kind.get("hot_subprov", 0),
            "pending_subprov":   pending_by_kind.get("subprov", 0),
            "pending_candidate": pending_by_kind.get("candidate", 0),
            "pending_treasury":  pending_by_kind.get("treasury", 0),
            "cleanups":  _CLEANUP_COUNT,
            "treasury_subs":   sum(1 for k in kinds.values() if k == "treasury"),
            "subprov_subs":    sum(1 for k in kinds.values() if k in ("subprov", "hot_subprov")),
            "candidate_subs":  sum(1 for k in kinds.values() if k == "candidate"),
            "hot_subprov_subs": sum(1 for k in kinds.values() if k == "hot_subprov"),
            # Budget degrade counters (lifetime since start, never reset)
            "budget_treasury_timeout":  _TREASURY_TIMEOUT_COUNT,
            "budget_catchup_timeout":   _CATCHUP_TIMEOUT_COUNT,
            # Subscription instrumentation (Task 1 / Task 5)
            "reconnect_gen":        casc.mgr._reconnect_gen,
            "subs_sent_total":      casc.mgr._subs_sent_total,
            "subs_conf_total":      casc.mgr._subs_confirmed_total,
            "sub_rate":             RECONNECT_SUBSCRIBE_RATE,
            **{f"sub_{k}": v for k, v in ack_stats.items() if k != "p0_recent"},
            "sub_p0_recent": ack_stats.get("p0_recent", []),
            # X27.7 — direct visibility into the confirmed root cause: cold subscriptions
            # stuck waiting past COLD_SUB_STALE_SEC, and how many are mid-retry vs
            # exhausted. If cold_retry_active stays high while subprov_ws_sig_seen stays
            # flat, live detection is starved even though the process looks healthy.
            "subprov_ws_sig_seen":  casc._subprov_sig_metrics.get("subprov_ws_sig_seen", 0),
            "cold_sub_stale_sec":   COLD_SUB_STALE_SEC,
            "cold_retry_active":    len(casc.mgr._cold_retry_count),
            "cold_retry_exhausted": _COLD_RETRY_EXHAUSTED_COUNT,
            # X24.8 — per-kind sent/confirmed/exhausted, to tell "this whole tier never
            # acks" apart from "this one wallet never acks" without singling out wallets.
            "sub_kind_breakdown":   casc.mgr.sub_kind_breakdown(),
            # X24.9 — invalid-target rejection (runtime, Phase 4) + startup audit (Phase 3).
            "invalid_subscription_targets":  casc.mgr._invalid_rejected_total,
            "invalid_targets_by_source":     dict(casc.mgr._invalid_rejected_by_kind),
            "startup_validation_failures":   (casc._startup_validation or {}).get("total_invalid", 0),
            "startup_validation_by_source":  (casc._startup_validation or {}).get("invalid_by_source", {}),
            "runtime_validation_failures":   (casc.mgr._invalid_rejected_total
                                               + getattr(prog_watcher, "_invalid_rejected_total", 0)),
        }
        if prog_watcher:
            base.update(prog_watcher.get_metrics())
        base.update({
            "wallet_profile_size":   len(casc._wallet_profile),
            "profile_hits":          casc._profile_hits,
            "profile_misses":        casc._profile_misses,
            "classify_counts":       dict(casc._classify_counts),
            **dict(casc._subprov_sig_metrics),
        })
        try:
            _hconn = casc._ops()
            try:
                base.update(store.pending_session_counts(_hconn))
            finally:
                _hconn.close()
        except Exception:
            pass
        return base

    asyncio.ensure_future(_heartbeat_loop(_meta))
    asyncio.ensure_future(_deferred_audit_loop())

    import errno as _errno
    import random as _random
    reconnect_delay = 5
    consecutive_errno49 = 0          # Phase 3: exit after 3 consecutive socket failures

    _set_state("CONNECTING")
    _cf_cookie: str = ""   # Cloudflare __cf_bm session cookie — persisted across reconnects
    while not _STOP:
        try:
            _set_state("CONNECTING")
            _extra = {"Cookie": _cf_cookie} if _cf_cookie else {}
            _log(f"WS attempting connect: {WS_URL[:60]}… key_len={len(WS_URL.split('api-key=')[-1]) if 'api-key=' in WS_URL else 0}")
            async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=60,
                                          open_timeout=30, close_timeout=10,
                                          max_size=10 * 1024 * 1024,
                                          extra_headers=_extra) as ws:
                # Capture Cloudflare __cf_bm cookie from response headers for reuse
                _set_cookie = ws.response_headers.get("set-cookie", "")
                if "__cf_bm=" in _set_cookie:
                    _cf_cookie = _set_cookie.split(";")[0].strip()
                consecutive_errno49 = 0          # successful connect resets the counter
                casc.mgr.ws = ws
                casc.mgr.reset()
                _log(f"✓ WS connected ({WS_URL.split('?')[0]})")

                # ── Phase 2: SUBSCRIBING ──────────────────────────────────────
                _set_state("SUBSCRIBING")
                await casc.resync_subscriptions()
                reconnect_delay = 5

                # Open the program-CREATE watcher stream only if there are active candidates
                if prog_watcher and prog_watcher._state in ("CLOSED", "OPENING") and len(prog_watcher.active_candidates) > 0:
                    await prog_watcher._open_stream(ws)

                # ── Phase 1: RECONCILING ─────────────────────────────────────
                # Recover any treasury provisioning txs that arrived while the
                # process was offline.  Runs in thread executor — reader task is
                # not yet started so the WS keepalive (ping_interval=20s) must
                # land before reconciliation finishes.  With 39 treasuries × 1
                # RPC each, this takes ~3–5s under normal RPC latency — well
                # within the 20s ping window.
                _set_state("RECONCILING")
                await casc.reconcile_pass()

                # ── LIVE ──────────────────────────────────────────────────────
                _set_state("LIVE")

                # READER / PROCESSOR / MAINTENANCE split — the whole point of the cascade is to
                # LISTEN for the create tx in real time. The reader does NOTHING but ws.recv() →
                # queue, so a slow message (RPC/DB) can never starve recv or stall keepalive. A
                # separate processor drains the queue; maintenance (poll/cleanup/sweep) is its own
                # task. All blocking RPC/DB inside processing runs off-loop (run_in_executor).
                inbox: asyncio.Queue = asyncio.Queue(maxsize=1000)

                async def _reader():
                    while not _STOP:
                        raw = await ws.recv()              # only job: read, fast
                        try:
                            inbox.put_nowait(raw)
                        except asyncio.QueueFull:
                            # drop oldest to stay live (catch-up/sweep will recover anything missed)
                            try:
                                inbox.get_nowait()
                            except Exception:
                                pass
                            try:
                                inbox.put_nowait(raw)
                            except Exception:
                                pass

                async def _processor():
                    while not _STOP:
                        raw = await inbox.get()
                        try:
                            await _on_message(casc, raw)
                        except Exception as _pe:
                            print(f"[WS_CASCADE] process error: {_pe} ts={int(time.time())}", flush=True)

                async def _maintenance():
                    last_poll = last_cleanup = last_sweep = last_temp_sweep = last_retry = last_drain = 0.0
                    last_fd_check = 0.0
                    _FD_WARN = int(os.environ.get("WS_CASCADE_DB_FD_WARN", "16"))
                    _FD_EXIT = int(os.environ.get("WS_CASCADE_DB_FD_EXIT", "28"))
                    while not _STOP:
                        now = time.time()
                        try:
                            if now - last_poll >= POLL_SEC:
                                await casc.resync_subscriptions(); last_poll = now
                            if now - last_cleanup >= CLEANUP_SEC:
                                await casc.cleanup_pass(); last_cleanup = now
                            if now - last_sweep >= SUBPROV_SWEEP_SEC:
                                # X24.2.1 Phase 3 — decoupled from this loop. A sweep
                                # cycle can take far longer than SUBPROV_SWEEP_SEC
                                # (measured 53-90s pre-fix); awaiting it inline here
                                # was the second proven contributor to the throughput
                                # problem, since it delayed resync_subscriptions/
                                # cleanup_pass/subprov_retry_pass/etc. every cycle it
                                # ran long. Fired as its own task; subprov_sweep_pass_guarded
                                # itself refuses to overlap with a still-running prior
                                # cycle (reports+skips rather than starting a second one).
                                asyncio.ensure_future(casc.subprov_sweep_pass_guarded())
                                last_sweep = now
                            if now - last_retry >= SUBPROV_SWEEP_SEC:
                                await casc.subprov_retry_pass(); last_retry = now
                            if now - last_temp_sweep >= TEMP_SWEEP_INTERVAL_SEC:
                                await _ato_thread(casc._temp_candidate_sweep)
                                last_temp_sweep = now
                            if now - last_drain >= 30:
                                await _ato_thread(casc._drain_pending_sessions)
                                await _ato_thread(casc._drain_pending_cascade_events)
                                last_drain = now
                            await _ato_thread(casc._refresh_wallet_profile_if_due)
                            # ProgramWatcher is intentionally expensive: keep it open only while
                            # there are candidate wallets to match against.
                            if (
                                prog_watcher
                                and prog_watcher._state in ("OPENING", "ACTIVE", "DRAINING")
                                and len(prog_watcher.active_candidates) == 0
                            ):
                                await prog_watcher._close_stream(reason="maintenance_zero_candidates")
                            # Open program-CREATE stream if candidates arrived after connect
                            if prog_watcher and prog_watcher._state == "OPENING" and len(prog_watcher.active_candidates) > 0:
                                await prog_watcher._open_stream(ws)
                            # FD watchdog: leaked connections pin the WAL → p99 spikes
                            if now - last_fd_check >= 120:
                                import subprocess as _sp
                                try:
                                    _fd_out = _sp.check_output(
                                        ["lsof", "-p", str(os.getpid())], stderr=_sp.DEVNULL)
                                    # Count only real DB connections (not -wal/-shm siblings)
                                    _db_fds = sum(
                                        1 for _ln in _fd_out.decode().splitlines()
                                        if ".db" in _ln and ".db-" not in _ln)
                                    if _db_fds >= _FD_EXIT:
                                        _log(f"🔴 FD watchdog: {_db_fds} DB FDs ≥ exit threshold {_FD_EXIT} — exiting for supervisord restart")
                                        os._exit(1)
                                    elif _db_fds >= _FD_WARN:
                                        _log(f"⚠ FD watchdog: {_db_fds} DB FDs ≥ warn threshold {_FD_WARN}")
                                except Exception:
                                    pass
                                last_fd_check = now
                        except Exception as _me:
                            _log(f"⚠ maintenance error (non-fatal): {_me}")
                        await asyncio.sleep(0.5)

                tasks = [asyncio.ensure_future(t()) for t in (_reader, _processor, _maintenance)]
                try:
                    # if any task exits (e.g. reader on ws close), tear them all down + reconnect
                    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for p in pending:
                        p.cancel()
                    for d in done:                          # surface the reason (ws closed, etc.)
                        exc = d.exception()
                        if exc:
                            raise exc
                finally:
                    for t in tasks:
                        t.cancel()
                    _set_state("CONNECTING")   # will re-enter SUBSCRIBING on next iteration

        except Exception as e:
            if not _STOP:
                is_errno49 = isinstance(e, OSError) and e.errno == _errno.EADDRNOTAVAIL

                if is_errno49:
                    consecutive_errno49 += 1
                    # Phase 3: after 3 consecutive socket failures let supervisord restart us
                    # cleanly rather than spinning in a tight reconnect loop that burns sockets.
                    if consecutive_errno49 >= 3:
                        _set_state("FAILED")
                        _log(f"RESOURCE_EXHAUSTION — {consecutive_errno49} consecutive Errno 49 "
                             f"(local address unavailable). Exiting for supervisord restart.")
                        import sys as _sys
                        _sys.exit(1)
                    jitter = _random.uniform(0, 5)
                    delay = 10 + jitter
                    _log(f"NETWORK_INTERFACE_LOST (Errno 49, attempt {consecutive_errno49}/3) — "
                         f"waiting {delay:.0f}s for interface to recover")
                    _set_state("DEGRADED")
                    await asyncio.sleep(delay)
                    reconnect_delay = 5
                else:
                    consecutive_errno49 = 0
                    _set_state("DEGRADED")
                    _extra_detail = ""
                    try:
                        import websockets.exceptions as _wse
                        if isinstance(e, _wse.InvalidStatusCode):
                            _extra_detail = f" status={e.status_code} headers={dict(e.headers)}"
                        elif hasattr(e, 'status_code'):
                            _extra_detail = f" status={e.status_code}"
                    except Exception:
                        pass
                    _log(f"WS loop error: {e}{_extra_detail} — reconnecting in {reconnect_delay}s")
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, 60)
    # X24.3 design requirement 7 — explicit executor lifecycle: shut down the
    # RPC deadline guard's dedicated pool on the normal stop path. Since this is
    # a fresh Python process per daemon restart, the interpreter tearing down
    # would reclaim these threads regardless — this call makes ownership
    # explicit and auditable rather than relying on that implicitly.
    if _get_tx_guard_instance is not None:
        _get_tx_guard_instance.shutdown(wait=False)
    _log("stopped")


async def _on_message(casc: Cascade, raw):
    try:
        data = json.loads(raw)
    except Exception:
        return
    # subscription confirmation
    if "result" in data and isinstance(data.get("id"), int):
        req_id = data["id"]
        sub_id = data["result"]
        # Route to program watcher if this is its subscription confirmation (req id 99000)
        prog_watcher = getattr(casc, "_prog_watcher", None)
        if prog_watcher and req_id == 99000 and isinstance(sub_id, int):
            prog_watcher.on_subscribe_confirmed(sub_id)
            return
        casc.mgr.on_subscribe_confirmed(req_id, sub_id)
        return

    # TREASURY tier uses accountSubscribe → accountNotification (balance change, NO signature).
    # Resolve new signatures off-loop since the last processed sig, then route each to the
    # treasury handler.  Using limit=1 was unreliable: accountNotification fires on EVERY
    # account change (fee touches, inbounds) so the limit=1 sig was often a fee-touch, and
    # the real provisioning transfer was one position back and never processed.  Fix: scan
    # back to last_notif_sig (stored in wt_treasury_ws_usage) and process ALL new sigs.
    if data.get("method") == "accountNotification":
        params = data.get("params") or {}
        ent = casc.mgr.lookup(params.get("subscription"))
        if not ent or ent[1] != "treasury":
            return
        wallet = ent[0]

        def _fetch_new_sigs():
            """Return all sigs newer than the last processed one, oldest-first."""
            conn = casc._ops()
            try:
                row = conn.execute(
                    "SELECT last_notif_sig FROM wt_treasury_ws_usage WHERE treasury_wallet=?",
                    (wallet,)).fetchone()
                last_sig = row[0] if row else None
            finally:
                conn.close()
            if last_sig:
                raw = _rpc("getSignaturesForAddress",
                           [wallet, {"limit": 5, "until": last_sig, "commitment": "confirmed"}]) or []
            else:
                raw = _rpc("getSignaturesForAddress",
                           [wallet, {"limit": 3, "commitment": "confirmed"}]) or []
            # oldest-first so sessions open in chronological order
            return list(reversed([e["signature"] for e in raw
                                   if isinstance(e, dict) and e.get("signature") and not e.get("err")]))

        new_sigs = await asyncio.get_event_loop().run_in_executor(None, _fetch_new_sigs)
        any_opened = []
        for sig in new_sigs:
            if sig in casc._processed:
                continue
            casc._processed.add(sig)
            opened = await _ato_thread(casc._handle_treasury_tx, wallet, sig)
            any_opened.extend(opened)
        for entry in any_opened:
            if isinstance(entry, tuple) and entry[0] == "CDC":
                _cdc_w = entry[1]
                if _cdc_w not in casc.mgr.wallet_kind:
                    await casc.mgr.subscribe(_cdc_w, "cdc")
                    _ops = casc._ops()
                    try:
                        store.cdc_mark_subscribed(_ops, wallet=_cdc_w)
                    finally:
                        _ops.close()
                    _log(f"🔵 CDC subscribed {_cdc_w[:12]}… (accountSubscribe, 60min TTL)")
                continue
            subprov = entry
            if SUBPROV_WATCH_ENABLED and subprov not in casc.mgr.wallet_kind:
                await casc.mgr.subscribe(subprov, "subprov")
                emit_event("SUBPROV_WEBSOCKET_OPENED", wallet=subprov, related=wallet)
                await casc.catch_up_subprov(subprov)
        return

    # X24.1 — SUBPROV_ACCOUNT tier: PLAIN_TRANSFER-funded sub-provisioners use
    # accountSubscribe → accountNotification (balance change, NO signature), mirroring
    # the treasury branch above exactly. Reuses _handle_subprov_tx (the same handler the
    # "subprov"/logsSubscribe path calls) so a qualifying event flows through the SAME
    # candidate-extraction → candidate-watch → CREATE-detection → record_launch() path —
    # no second launch pipeline.
    if data.get("method") == "accountNotification":
        params = data.get("params") or {}
        ent = casc.mgr.lookup(params.get("subscription"))
        if not ent or ent[1] != "subprov_account":
            return
        wallet = ent[0]

        def _fetch_new_subprov_sigs():
            conn = casc._ops()
            try:
                row = conn.execute(
                    "SELECT last_notif_sig FROM wt_subprov_account_ws_usage WHERE subprov_wallet=?",
                    (wallet,)).fetchone()
                last_sig = row[0] if row else None
            finally:
                conn.close()
            if last_sig:
                raw = _rpc("getSignaturesForAddress",
                           [wallet, {"limit": 5, "until": last_sig, "commitment": "confirmed"}]) or []
            else:
                raw = _rpc("getSignaturesForAddress",
                           [wallet, {"limit": 3, "commitment": "confirmed"}]) or []
            return list(reversed([e["signature"] for e in raw
                                   if isinstance(e, dict) and e.get("signature") and not e.get("err")]))

        new_sigs = await asyncio.get_event_loop().run_in_executor(None, _fetch_new_subprov_sigs)
        last_sig_seen = None
        for sig in new_sigs:
            last_sig_seen = sig
            if sig in casc._processed:
                continue
            casc._processed.add(sig)
            try:
                new_watches = await _ato_thread(casc._handle_subprov_tx, wallet, sig)
            except Exception:
                new_watches = []
            for item in new_watches:
                if isinstance(item, tuple) and item[0] == "UNSUBSCRIBE":
                    await casc.mgr.unsubscribe(item[1])
        if last_sig_seen:
            _ops = casc._ops()
            try:
                store.subprov_account_ws_record_notif(_ops, wallet, last_sig_seen)
            finally:
                _ops.close()
        return

    if data.get("method") != "logsNotification":
        return
    params = data.get("params") or {}
    sub_id = params.get("subscription")
    result = (params.get("result") or {})
    value = result.get("value") or {}
    sig = value.get("signature")
    if value.get("err") or not sig:
        return

    # Route to program-CREATE watcher if this notification is on its subscription
    prog_watcher = getattr(casc, "_prog_watcher", None)
    if prog_watcher and prog_watcher._sub_id is not None and sub_id == prog_watcher._sub_id:
        await prog_watcher._on_notification(data, None)
        return

    ent = casc.mgr.lookup(sub_id)
    if not ent:
        return
    wallet, kind = ent

    if kind == "treasury":
        # _handle_treasury_tx does blocking RPC + DB → off the loop. Hard outer timeout so a
        # slow Helius response can't pin a thread-pool slot for minutes (NEAR_RT budget).
        try:
            opened = await asyncio.wait_for(
                _ato_thread(casc._handle_treasury_tx, wallet, sig),
                timeout=_budget.CRITICAL_OUTER_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            global _TREASURY_TIMEOUT_COUNT
            _TREASURY_TIMEOUT_COUNT += 1
            _log(f"⚠ treasury_tx timeout ({_budget.CRITICAL_OUTER_TIMEOUT_S}s) sig={sig[:12]}… — deferred (total={_TREASURY_TIMEOUT_COUNT})")
            opened = []
        for entry in opened:
            if isinstance(entry, tuple) and entry[0] == "CDC":
                _cdc_w = entry[1]
                if _cdc_w not in casc.mgr.wallet_kind:
                    await casc.mgr.subscribe(_cdc_w, "cdc")
                    _ops = casc._ops()
                    try:
                        store.cdc_mark_subscribed(_ops, wallet=_cdc_w)
                    finally:
                        _ops.close()
                    _log(f"🔵 CDC subscribed {_cdc_w[:12]}… (accountSubscribe, 60min TTL)")
                continue
            subprov = entry
            if subprov not in casc.mgr.wallet_kind:
                # HOT path: newly-funded subprov from a confirmed treasury.
                # Use "hot_subprov" kind so sweep_stale_pending retries it at 2s
                # instead of dropping it at 10s like a cold wallet.
                await casc.mgr.subscribe(subprov, "hot_subprov")
                emit_event("SUBPROV_WEBSOCKET_OPENED", wallet=subprov, related=wallet,
                           payload={"priority": "HOT"})
                _log(f"🔥 HOT_SUBPROV_PREARMED {subprov[:12]}… — RPC burst + priority subscribe")
                # HOT RPC burst fallback: poll wrap-closes regardless of subscription state.
                # Even if Helius never confirms the subscribe, we catch the wrap-close via RPC.
                asyncio.ensure_future(_hot_subprov_burst(casc, subprov))
    elif kind == "subprov":
        # _handle_subprov_tx does blocking RPC + DB → run it OFF the event loop so recv keeps
        # reading the next notification while this one's tx is fetched/decoded.
        casc._metric("subprov_ws_sig_seen")
        raw_result = await _ato_thread(
            casc._process_subprov_sig_durable, wallet, sig, source="WS")
        # Separate real candidates from UNSUBSCRIBE sentinels (BUY_SWARM burst gate)
        new_watches = []
        for item in raw_result:
            if isinstance(item, tuple) and item[0] == "UNSUBSCRIBE":
                await casc.mgr.unsubscribe(item[1])
            else:
                new_watches.append(item)
        # Phase 1 shadow: inform the program-CREATE watcher of new candidates (memory-only, O(1))
        if CANDIDATE_WATCH_ENABLED and prog_watcher and new_watches:
            watcher_metas = [{"candidate": c, "subprov": wallet, "treasury": None,
                               "wrap_sig": sig, "wrap_time": None, "amount": None}
                             for c in new_watches]
            conn_pw = casc._ops()
            try:
                prog_watcher.add_candidates(watcher_metas, conn_pw)
            finally:
                conn_pw.close()
        # Candidates are saved to DB + ProgramWatcher by _handle_subprov_tx.
        # No per-candidate WS subscriptions — CREATE detection is via the program stream.
    elif kind == "cdc":
        cdc_result = await _ato_thread(casc._handle_cdc_tx, wallet, sig)
        # If the CDC was promoted to subprov during this tx, cdc_result contains
        # new candidate watches from _handle_subprov_tx — wire them into ProgramWatcher.
        if cdc_result and CANDIDATE_WATCH_ENABLED and prog_watcher:
            cdc_new_watches = [item for item in cdc_result
                               if not (isinstance(item, tuple) and item[0] == "UNSUBSCRIBE")]
            if cdc_new_watches:
                conn_pw = casc._ops()
                try:
                    watcher_metas = [{"candidate": c, "subprov": wallet, "treasury": None,
                                      "wrap_sig": sig, "wrap_time": None, "amount": None}
                                     for c in cdc_new_watches]
                    prog_watcher.add_candidates(watcher_metas, conn_pw)
                finally:
                    conn_pw.close()
    elif kind == "dust":
        # Dust Observatory: enqueue the sig for off-thread processing.
        # No RPC here — the writer thread fetches the tx and extracts recipients.
        try:
            from src.core import dust_observatory as _dobs
            _dobs.enqueue_sig(wallet, sig)
        except Exception:
            pass
    elif kind == "candidate":
        # Should not be reached: candidate wallets are no longer individually subscribed.
        # ProgramWatcher handles CREATE detection via the pump.fun program stream.
        pass


def loop():
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    _log(f"starting — session_ttl={SESSION_TTL_SEC}s candidate_ttl={CANDIDATE_TTL_SEC}s "
         f"max_candidates={MAX_CANDIDATES} poll={POLL_SEC}s")
    asyncio.run(run_cascade())


def once():
    """Single resync + cleanup tick against the DB (no live WS) — for tests/debug."""
    casc = Cascade()

    async def _tick():
        casc.mgr.ws = _NullWS()
        await casc.resync_subscriptions()
        await casc.cleanup_pass()
    asyncio.run(_tick())
    _log("once: tick complete")


class _NullWS:
    async def send(self, *_a):
        return None


if __name__ == "__main__":
    if "--once" in sys.argv:
        once()
    else:
        loop()
