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
from typing import Optional

try:
    import websockets
except Exception:                                    # pragma: no cover
    websockets = None

from src.utils.db_locking import db_connect
from src.core import ws_cascade_store as store
from src.core.ws_cascade_store import OPS_DB_PATH, LIVE_DB_PATH, emit_event
from src.core.wrap_close_detector import extract_close_destinations
from src.core import runtime_budget as _budget

# ── config (env, conservative defaults) ──────────────────────────────────────
# TTLs sized from data: real subprovs stay actively provisioning for a MEDIAN of ~2h (p75 ~10h,
# p90 16h) — 16/19 multi-funding subprovs exceeded the old 10-min session TTL, so the subscription
# died mid-campaign and had to be re-opened on the next funding (losing the catch-up window). 2h
# covers the median; refresh_session() extends it on each new funding so an active subprov stays
# subscribed as long as it keeps provisioning. Candidate TTL stays short (a wrap-close→CREATE is
# seconds-to-minutes), but bumped 3→10min for the occasional STAGED launch.
SESSION_TTL_SEC   = int(os.environ.get("WS_SESSION_TTL_SEC", "7200"))    # 2h (was 10min — too short)
CANDIDATE_TTL_SEC = int(os.environ.get("WS_CANDIDATE_TTL_SEC", "600"))   # 10 min (was 3min)
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
# How often to sweep ACTIVE subprovs for wrap-closes whose WS notification dropped/stalled.
# This is the reliability backstop for the ~100s-miss case (a dropped subprov notification).
# RPC-bounded: one getSignatures per active subprov per sweep, deduped so it doesn't refetch.
SUBPROV_SWEEP_SEC = float(os.environ.get("WS_SUBPROV_SWEEP_SEC", "6"))

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
COLD_SUB_STALE_SEC  = float(os.environ.get("WS_COLD_SUB_STALE_SEC", "10"))  # drop COLD pending after this
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
SAVE_CANDIDATE_FANOUT       = os.environ.get("WS_SAVE_CANDIDATE_FANOUT",       "1") == "1"
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
PROGRAM_WATCHER_ENABLED  = os.environ.get("WS_PROGRAM_CREATE_WATCHER_ENABLED", "1") == "1"
CREATE_FETCH_CONCURRENCY = int(os.environ.get("WS_CREATE_FETCH_CONCURRENCY", "4"))
CREATE_FETCH_TIMEOUT_S   = int(os.environ.get("WS_CREATE_FETCH_TIMEOUT_S", "4"))
CREATE_FETCH_MAX_QUEUE   = int(os.environ.get("WS_CREATE_FETCH_MAX_QUEUE", "20"))
PROGRAM_DRAIN_GRACE_S    = int(os.environ.get("WS_PROGRAM_DRAIN_GRACE_S", "90"))


def _confirmed_treasuries(conn) -> set:
    """The authoritative confirmed-treasury set (wt_confirmed_treasuries, ops DB) — the wallets
    we WS-subscribe permanently. Small + stable (≈12)."""
    try:
        return {r[0] for r in conn.execute("SELECT treasury FROM wt_confirmed_treasuries").fetchall()}
    except Exception:
        return set()


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


def _handle_signal(*_a):
    global _STOP
    _STOP = True


def _log(msg):
    print(f"[WS_CASCADE] {msg}", flush=True)


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


def _get_tx(sig):
    # 'confirmed' commitment — Helius no longer accepts 'processed' for getTransaction.
    return _rpc("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0,
                                         "commitment": "confirmed"}])


# ── async, off-loop wrappers — run blocking RPC + DB work in the default thread-pool executor
# so the asyncio event loop (ws.recv + keepalive) is NEVER frozen by I/O. ───────────────────
async def _arpc(method, params, timeout=None):
    _t = timeout if timeout is not None else _budget.NEARRT_RPC_TOTAL_S
    return await asyncio.get_event_loop().run_in_executor(None, lambda: _rpc(method, params, _t))


async def _aget_tx(sig):
    return await asyncio.get_event_loop().run_in_executor(None, _get_tx, sig)


async def _ato_thread(fn, *args):
    """Run a blocking function (DB writes, sync handlers) off the event loop."""
    return await asyncio.get_event_loop().run_in_executor(None, lambda: fn(*args))


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
    active_candidates dict, logs match (Phase 1: shadow only — does NOT record
    launches and does NOT interfere with the existing per-wallet path).

    Lifecycle: CLOSED → OPENING → ACTIVE → DRAINING → CLOSED
    Opens on first candidate, closes after PROGRAM_DRAIN_GRACE_S with zero candidates.
    """

    def __init__(self):
        self.active_candidates: dict = {}        # wallet → {subprov, treasury, expires_at, ...}
        self._state: str = "CLOSED"              # CLOSED|OPENING|ACTIVE|DRAINING
        self._sub_id: int | None = None
        self._drain_task: asyncio.Task | None = None
        self._fetch_sem: asyncio.Semaphore = asyncio.Semaphore(CREATE_FETCH_CONCURRENCY)
        self._candidate_persist_queue: asyncio.Queue = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None  # set by run_cascade()

        # metrics (int/str, updated from the asyncio loop only)
        self.metric_active_candidates: int = 0
        self.metric_persist_queue_depth: int = 0
        self.metric_stream_state: str = "CLOSED"
        self.metric_create_fetch_queue_depth: int = 0
        self.metric_create_fetch_dropped: int = 0
        self.metric_create_fetch_timeout: int = 0
        self.metric_program_matches: int = 0
        self.metric_candidates_expired: int = 0
        self.metric_program_opens: int = 0
        self.metric_program_closes: int = 0
        self._stream_opened_at: float = 0.0
        self.metric_program_open_seconds: float = 0.0

        # background task handles
        self._expire_task: asyncio.Task | None = None
        self._persist_task: asyncio.Task | None = None

    def start_background_tasks(self):
        """Start the expire + persist background loops. Call once after the event loop is live."""
        if self._expire_task is None or self._expire_task.done():
            self._expire_task = asyncio.ensure_future(self._expire_loop())
        if self._persist_task is None or self._persist_task.done():
            self._persist_task = asyncio.ensure_future(self._persist_loop())

    def add_candidates(self, candidates: list, conn) -> None:
        """Called from _handle_subprov_tx after wrap-close. Each dict must contain
        'candidate', 'subprov', 'treasury', 'wrap_sig', 'wrap_time', 'amount'."""
        now = int(time.time())
        for meta in candidates:
            wallet = meta.get("candidate")
            if not wallet:
                continue
            expires_at = now + CANDIDATE_TTL_SEC
            self.active_candidates[wallet] = {**meta, "expires_at": expires_at}
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

        # if candidates present and stream is closed: trigger open (thread-safe)
        if self.active_candidates and self._state == "CLOSED":
            if self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(self._trigger_open(), self._loop)
            else:
                asyncio.ensure_future(self._trigger_open())

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
        self._state = "OPENING"
        self.metric_stream_state = "OPENING"
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
        self._state = "ACTIVE"
        self.metric_stream_state = "ACTIVE"
        self.metric_program_opens += 1
        self._stream_opened_at = time.time()
        _log(f"[ProgramWatcher] state → ACTIVE sub_id={sub_id}")

    async def _on_notification(self, data: dict, ws) -> None:
        """Called when a logsNotification arrives on the pump.fun subscription.
        Phase 1: fetch tx, check creator against active_candidates, log match only."""
        if self._state != "ACTIVE":
            return
        params = data.get("params") or {}
        result = (params.get("result") or {})
        value = result.get("value") or {}
        sig = value.get("signature")
        if value.get("err") or not sig:
            return

        # Rate-limit: if all fetch slots are busy AND too many are queued, drop.
        # asyncio.Semaphore._value is the number of available slots.
        if self._fetch_sem._value == 0 and self.metric_create_fetch_queue_depth >= CREATE_FETCH_MAX_QUEUE:
            self.metric_create_fetch_dropped += 1
            return

        self.metric_create_fetch_queue_depth += 1
        asyncio.ensure_future(self._fetch_and_check(sig))

    async def _fetch_and_check(self, sig: str) -> None:
        """Fetch one tx and check if its creator is a candidate. Phase 1: log only."""
        try:
            async with self._fetch_sem:
                try:
                    async with asyncio.timeout(CREATE_FETCH_TIMEOUT_S):
                        tx = await _arpc("getTransaction",
                                         [sig, {"encoding": "jsonParsed",
                                                "maxSupportedTransactionVersion": 0,
                                                "commitment": "processed"}])
                except TimeoutError:
                    self.metric_create_fetch_timeout += 1
                    return
                except asyncio.TimeoutError:
                    self.metric_create_fetch_timeout += 1
                    return
        finally:
            self.metric_create_fetch_queue_depth = max(0, self.metric_create_fetch_queue_depth - 1)

        if not tx:
            return
        is_create, mint, btime, extra = _tx_is_create(tx)
        if not is_create or not mint:
            return

        # The signer / fee payer is the creator wallet on a pump.fun CREATE.
        # accounts[1] in the CREATE ix is the creator/signer per the pump IDL.
        # We extract it from the tx account keys: the CREATE ix signer is accountKeys[1].
        acct_keys = []
        try:
            acct_keys = [
                k.get("pubkey") if isinstance(k, dict) else k
                for k in (tx.get("transaction", {}).get("message", {}).get("accountKeys") or [])
            ]
        except Exception:
            pass

        # Pump CREATE ix accounts[1] is the creator (user who signed the CREATE).
        # We look in the ix account list for a wallet that's in active_candidates.
        create_accts = _find_pump_create_ix(tx)
        creator = create_accts[1] if len(create_accts) > 1 else None

        # Cross-check with our candidates dict (O(1) — no DB)
        if creator and creator in self.active_candidates:
            meta = self.active_candidates[creator]
            self.metric_program_matches += 1
            _log(f"[ProgramWatcher] SHADOW MATCH creator={creator[:12]}… "
                 f"mint={mint} subprov={str(meta.get('subprov','?'))[:12]}… "
                 f"sig={sig[:16]}… (Phase 1: log only)")

    async def _expire_loop(self) -> None:
        """Runs every 30s: expire stale candidates, enqueue expire records, maybe drain."""
        while not _STOP:
            await asyncio.sleep(30)
            try:
                now = int(time.time())
                expired = [w for w, m in self.active_candidates.items()
                           if m.get("expires_at", 0) < now]
                for w in expired:
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
                    _log("[ProgramWatcher] zero candidates — starting drain timer")
                    self._state = "DRAINING"
                    self.metric_stream_state = "DRAINING"
                    if self._drain_task and not self._drain_task.done():
                        self._drain_task.cancel()
                    self._drain_task = asyncio.ensure_future(self._drain_timer())
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
            _log("[ProgramWatcher] drain timer expired — closing stream")
            self._state = "CLOSED"
            self.metric_stream_state = "CLOSED"
            self._sub_id = None
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
            "pw_matches":             self.metric_program_matches,
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
        # P0 timing ring buffer (last 50 P0 events)
        # each entry: {wallet, requested_at, sent_at, ack_at, send_delay_ms, ack_latency_ms, gen}
        self._p0_events: list = []
        self._p0_ring_size = 50

    async def subscribe(self, wallet, kind, priority: int = SUB_PRIORITY_OTHER,
                        requested_at: float | None = None):
        if wallet in self.wallet_sub or wallet in self.wallet_kind:
            return                      # already (being) subscribed
        rid = self.next_req; self.next_req += 1
        queued_at = requested_at or time.time()
        self._queue_pos_counter += 1
        self._subs_sent_total += 1
        self.wallet_kind[wallet] = kind
        if kind == "treasury":
            # TREASURIES move SOL via plain system:transfer, which emits no program logs that
            # logsSubscribe's `mentions` filter matches — so logsSubscribe NEVER fired for them.
            # accountSubscribe fires on every balance change (a plain transfer always changes the
            # balance), so it's the correct primitive for the treasury tier. (Subprovs/candidates
            # keep logsSubscribe — their wrap-close emits token-program logs that DO mention them.)
            # Treasury stays at 'confirmed' — provisioning isn't sub-second-critical and a treasury
            # balance read at 'processed' has higher reorg exposure.
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
        Cold kinds (subprov/candidate/treasury) are dropped after COLD_SUB_STALE_SEC (10s).
        Returns (dropped_wallets, stale_hot_wallets) — callers can retry stale HOT ones."""
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
                    dropped.append(wallet)
        return dropped, stale_hot

    def pending_count_by_kind(self):
        """Return {kind: count} for all pending (unconfirmed) subscribe requests."""
        counts: dict = {}
        for ent in self.pending_req.values():
            k = ent[1]
            counts[k] = counts.get(k, 0) + 1
        return counts

    async def unsubscribe(self, wallet):
        sub_id = self.wallet_sub.pop(wallet, None)
        self.wallet_kind.pop(wallet, None)
        if sub_id is not None:
            self.sub_wallet.pop(sub_id, None)
            try:
                rid = self.next_req; self.next_req += 1
                await self.ws.send(json.dumps(
                    {"jsonrpc": "2.0", "id": rid, "method": "logsUnsubscribe", "params": [sub_id]}))
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
        # Ensure the cascade schema ONCE at startup — NOT on every _ops() call. The schema
        # ensure is a WRITE (CREATE TABLE/INDEX); running it on the hot _ops() path (called
        # from resync_subscriptions + cleanup on the async WS loop) blocked the event loop
        # under write contention, so subscription-confirmation acks were never processed and
        # ALL subscriptions were reaped as "never-confirmed" (419 dropped / 0 confirmed). One
        # short startup write fixes it; _ops() is now a pure read-path connection.
        try:
            c = db_connect(OPS_DB_PATH, timeout=20)
            try:
                store.ensure_cascade_schema(c)
            finally:
                c.close()
        except Exception as _e:
            _log(f"⚠ startup schema ensure failed (will retry lazily): {_e}")

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
                return t, s, c, p
            finally:
                conn.close()

        treasuries, sessions, candidates, promoted = await loop.run_in_executor(None, _db_load)
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
                        "wrap_close_signature, wrap_close_time, funding_amount "
                        "FROM wt_candidate_websocket_watches "
                        "WHERE state='WATCHING' AND wrap_close_time > ?", (cutoff,)
                    ).fetchall()
                finally:
                    conn2.close()
            recent = await loop.run_in_executor(None, _recent_candidates)
            if recent:
                metas = [{"candidate": r[0], "subprov": r[1], "treasury": r[2],
                          "wrap_sig": r[3], "wrap_time": r[4], "amount": r[5]}
                         for r in recent]
                prog_watcher.add_candidates(metas, None)
                _log(f"[ProgramWatcher] reloaded {len(recent)} recent candidate(s) from DB on (re)connect")

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
        for t in treasuries:
            await _rate_send(t, "treasury", SUB_PRIORITY_TREASURY)
            if t not in self.mgr.wallet_kind or _sent_this_resync == 1:
                emit_event("TREASURY_WEBSOCKET_OPENED", wallet=t)

        # P2: SESSION SUBPROV TIER — existing LIVE_ARMED sessions (reconnect replay)
        if SUBPROV_WATCH_ENABLED:
            for s in sessions:
                subprov = s[1]
                monitoring_state = s[9] if len(s) > 9 else "LIVE_ARMED"
                if monitoring_state != "LIVE_ARMED":
                    continue   # never subscribe INTEL_ONLY
                await _rate_send(subprov, "subprov", SUB_PRIORITY_SESSION, catchup_kind="subprov")
                emit_event("SUBPROV_WEBSOCKET_OPENED", wallet=subprov)

        # P3: PROMOTED SUBPROV TIER (standing watchlist, lower priority than sessions)
        if SUBPROV_WATCH_ENABLED:
            for subprov in promoted:
                await _rate_send(subprov, "subprov", SUB_PRIORITY_OTHER, catchup_kind="subprov")
                emit_event("SUBPROV_WEBSOCKET_OPENED", wallet=subprov,
                           payload={"source": "discovered_promotion"})

        # Candidate wallets are NOT subscribed — CREATE detection is via ProgramWatcher (one stream).

        _log(f"resync complete: sent={_sent_this_resync} gen={self.mgr._reconnect_gen} "
             f"pending={len(self.mgr.pending_req)} rate={RECONNECT_SUBSCRIBE_RATE}/s")

        # Schedule catch-ups as fire-and-forget tasks — they do RPC and must not block the
        # reader. A small initial delay lets the subscription confirmations arrive first.
        async def _deferred_catchups():
            await asyncio.sleep(30)  # let all subscription confirmations arrive before scanning
            for kind, wallet in catchup_tasks:
                if kind == "subprov":
                    await self.catch_up_subprov(wallet)
                else:
                    await self.catch_up_candidate(wallet)
        if catchup_tasks:
            asyncio.ensure_future(_deferred_catchups())

    async def subscribe_live_armed(self, wallet: str) -> None:
        """P0 subscribe — new LIVE_ARMED subprov, bypasses all rate limiting.
        Called immediately when a session is opened from _handle_treasury_tx so the
        subscription races ahead of any pending reconnect-replay queue."""
        await self.mgr.subscribe(wallet, "subprov", priority=SUB_PRIORITY_LIVE_ARMED)

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

    def _classify_recipient(self, conn, recipient: str, amount_sol: float = 0.0) -> tuple:
        """Return (classification, subprov_meta) for a treasury outbound recipient.

        6-way classification (Pass 1 — instrumentation only, WS_SUBPROV_CLASSIFICATION_ENFORCE=0):
          TREASURY_MESH              — recipient is a confirmed treasury (mesh capital routing)
          BUY_SWARM_PROVISIONER      — known subprov whose destinations are predominantly swaps
          KNOWN_SUBPROV_TOPUP        — known subprov, active session exists (mid-campaign top-up)
          SUBPROV_REACTIVATED        — known subprov, wrap-close history, no active session
          HISTORICAL_SUBPROV_DISCOVERED — wallet existed/operated before WATCHTOWER found it
          NEW_SUBPROV                — fresh wallet, no prior evidence (open_reason=PROVISION_CANDIDATE)

        When ENFORCE=0 (default): all non-mesh recipients still open sessions as before.
        When ENFORCE=1 (Pass 2): BUY_SWARM_PROVISIONER and KNOWN_SUBPROV_TOPUP skip the creator pipeline.

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
            # Large funding → REARMED label so you can see re-activations in WATCHING
            if amount_sol >= 1.0:
                expired_count = (conn.execute(
                    "SELECT COUNT(*) FROM wt_active_subprov_sessions "
                    "WHERE subprov_wallet=? AND state='EXPIRED'", (recipient,)
                ).fetchone() or (0,))[0]
                if expired_count >= 5:
                    return "REARMED_SUBPROV_CANDIDATE", {}
            return "HISTORICAL_SUBPROV_DISCOVERED", {}

        # SUBPROV / BUY_SWARM: fetch the row for the fine-grained sub-classification
        if role in ("SUBPROV", "BUY_SWARM"):
            known = store.lookup_subprov(conn, recipient) or {}
            bsr   = known.get("buy_swarm_ratio") or 0.0
            n_obs = (known.get("buy_swarm_count") or 0) + (known.get("create_count") or 0)
            # creator_count (discovery-time) overrides the real-time ratio if it shows
            # substantial creator provisioning — guards against divergence between the two counters
            has_creators = (known.get("creator_count") or 0) >= 5
            if bsr > 0.7 and n_obs >= 10 and not has_creators:
                return "BUY_SWARM_PROVISIONER", known
            if (known.get("wrap_close_count") or 0) >= 1:
                active = conn.execute(
                    "SELECT 1 FROM wt_active_subprov_sessions "
                    "WHERE subprov_wallet=? AND state='ACTIVE' LIMIT 1", (recipient,)
                ).fetchone()
                return ("KNOWN_SUBPROV_TOPUP" if active else "SUBPROV_REACTIVATED"), known
            # Profile said SUBPROV but DB row missing wrap_close — fall through to historical check
            if store.is_historical_subprov(conn, recipient):
                return "HISTORICAL_SUBPROV_DISCOVERED", known
            return "NEW_SUBPROV", {}

        # Cache miss — full DB lookup (cold path, drives incremental updates below)
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
            if (known.get("wrap_close_count") or 0) >= 1:
                self._profile_set(recipient, "SUBPROV")
                active = conn.execute(
                    "SELECT 1 FROM wt_active_subprov_sessions "
                    "WHERE subprov_wallet=? AND state='ACTIVE' LIMIT 1", (recipient,)
                ).fetchone()
                return ("KNOWN_SUBPROV_TOPUP" if active else "SUBPROV_REACTIVATED"), known

        # CREATOR check before historical — mirrors profile priority (CREATOR > HISTORICAL)
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
            if amount_sol >= 1.0:
                expired_count = (conn.execute(
                    "SELECT COUNT(*) FROM wt_active_subprov_sessions "
                    "WHERE subprov_wallet=? AND state='EXPIRED'", (recipient,)
                ).fetchone() or (0,))[0]
                if expired_count >= 5:
                    return "REARMED_SUBPROV_CANDIDATE", known or {}
            return "HISTORICAL_SUBPROV_DISCOVERED", known or {}

        return "NEW_SUBPROV", {}

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
        # Historical create evidence from wt_discovered_subprovs
        known = store.lookup_subprov(conn, subprov)
        if known and (known.get("create_count") or 0) > 0:
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
                            # Promote: write evidence + open a real session
                            try:
                                store.promote_to_subprov(
                                    conn, subprov=wallet, treasury=treasury_addr or "",
                                    wrap_close_sig=sig_str,
                                    creator=real_dests[0]["candidate"],
                                    amount_sol=real_dests[0].get("base_amount_sol"))
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
                                                "creator": real_dests[0]["candidate"]})
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
                classification, _meta = self._classify_recipient(conn, w, amount_sol=gain)
                _log(
                    f"CLASSIFY treasury={treasury[:10]}… recipient={w[:12]}… "
                    f"amount={gain:.4f}◎ result={classification}"
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
                    "KNOWN_SUBPROV_TOPUP", "SUBPROV_REACTIVATED",
                    "BUY_SWARM_PROVISIONER", "HISTORICAL_SUBPROV_DISCOVERED",
                    "REARMED_SUBPROV_CANDIDATE",
                ) else 0
                open_reason = "PROVISION_CANDIDATE" if classification == "NEW_SUBPROV" else classification

                # ── Monitoring state: LIVE_ARMED vs INTEL_ONLY ────────────────
                # LIVE_ARMED: subscribe to WS, open candidate pipeline, spend RPC budget
                # INTEL_ONLY: record funding intelligence only — no WS, no candidate watch
                _LIVE_ARMED_CLASSIFICATIONS = {
                    "NEW_SUBPROV", "SUBPROV_REACTIVATED", "REARMED_SUBPROV_CANDIDATE",
                }
                monitoring_state = "LIVE_ARMED" if classification in _LIVE_ARMED_CLASSIFICATIONS else "INTEL_ONLY"

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

                _session_opened = False
                try:
                    _session_opened = store.start_session(
                        conn, subprov=w, treasury=treasury, funding_sig=sig,
                        funding_amount=gain, funding_time=btime,
                        ttl_seconds=SESSION_TTL_SEC, subprov_known=subprov_known,
                        open_reason=open_reason, monitoring_state=monitoring_state)
                except Exception as _lock_err:
                    is_hv = gain >= store.HIGH_VALUE_PROVISION_SOL
                    _log(f"{'🚨' if is_hv else '⚠️'} SESSION_WRITE_{'DROPPED_HIGH_VALUE' if is_hv else 'FAILED'} "
                         f"{treasury[:10]}… → {w[:12]}… {gain:.2f}◎ ({_lock_err}) — enqueuing retry")
                    try:
                        store.enqueue_pending_session(
                            conn, treasury=treasury, subprov=w, funding_sig=sig,
                            funding_amount=gain, funding_time=btime,
                            open_reason=open_reason, subprov_known=subprov_known,
                            ttl_seconds=SESSION_TTL_SEC)
                    except Exception as _eq:
                        _log(f"🚨 ENQUEUE_FAILED {w[:12]}… {gain:.2f}◎ — session write permanently lost: {_eq}")
                if _session_opened:
                    icon = {
                        "KNOWN_SUBPROV_TOPUP":           "🔄",
                        "SUBPROV_REACTIVATED":           "♻️",
                        "BUY_SWARM_PROVISIONER":         "⚠️",
                        "HISTORICAL_SUBPROV_DISCOVERED": "🔍",
                        "REARMED_SUBPROV_CANDIDATE":     "🔁",
                        "NEW_SUBPROV":                   "⚡",
                    }.get(classification, "⚡")
                    if monitoring_state == "LIVE_ARMED":
                        opened.append(w)
                        emit_event("SUBPROV_SESSION_OPENED_WS", wallet=w, related=treasury,
                                   payload={"funding_sol": gain, "sig": sig, "via": "treasury_ws",
                                            "classification": classification})
                        _log(f"{icon} {classification} {treasury[:10]}… → {w[:12]}… {gain:.2f} ◎ (LIVE_ARMED)")
                        # P0 subscribe: fire immediately, ahead of any reconnect-replay queue.
                        # Use run_coroutine_threadsafe when called from a thread (RECONCILE),
                        # ensure_future when already on the event loop (WS notification path).
                        if self._loop and self._loop.is_running():
                            asyncio.run_coroutine_threadsafe(
                                self.subscribe_live_armed(w), self._loop)
                        else:
                            asyncio.ensure_future(self.subscribe_live_armed(w))
                    else:
                        emit_event("SUBPROV_SESSION_INTEL_ONLY", wallet=w, related=treasury,
                                   payload={"funding_sol": gain, "sig": sig,
                                            "classification": classification})
                        _log(f"{icon} {classification} {treasury[:10]}… → {w[:12]}… {gain:.2f} ◎ (INTEL_ONLY — no WS)")
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
                try:
                    store.promote_to_subprov(
                        conn, subprov=treasury, treasury=treasury,
                        wrap_close_sig=sig, creator=direct_dests[0]["candidate"],
                        amount_sol=direct_dests[0].get("base_amount_sol"),
                    )
                except Exception as _e:
                    _log(f"[treasury-as-subprov] promote_to_subprov failed: {_e}")
                emit_event("WRAP_CLOSE_FANOUT_DETECTED", wallet=treasury,
                           related=direct_dests[0]["candidate"],
                           payload={"wrap_close_sig": sig, "via": "treasury_direct",
                                    "dest_count": len(direct_dests)})
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
                                wrap_close_time=wrap_close_time):
                            new_direct.append(cand)
                        direct_metas.append({
                            "candidate": cand, "subprov": treasury, "treasury": treasury,
                            "wrap_sig": sig, "wrap_time": wrap_close_time,
                            "amount": d.get("base_amount_sol"),
                        })
                    if new_direct:
                        _log(f"🎯 treasury-direct wrap-close {treasury[:10]}… → {len(new_direct)} candidate(s) saved")
                    # Feed ProgramWatcher — one program stream, no per-wallet subscriptions
                    prog_watcher = getattr(self, "_prog_watcher", None)
                    if prog_watcher and direct_metas:
                        prog_watcher.add_candidates(direct_metas, conn)
                        _log(f"🎯 ProgramWatcher armed with {len(direct_metas)} candidate(s) from treasury-direct fanout")

            store.treasury_ws_record_notif(conn, treasury, sig, opened_session=bool(opened))
            return opened
        finally:
            conn.close()

    # ---- handle a SUB_PROV log notification (wrap-close fan-out) ------------
    def _handle_subprov_tx(self, subprov, sig):
        conn = self._ops()
        try:
            sess = store.session_for_subprov(conn, subprov)
            if not sess:
                return []                              # session gone/expired
            treasury, funding_time = sess[1], sess[2]
            tx = _get_tx(sig)
            wrap_close_time = (tx or {}).get("blockTime")   # on-chain creator BIRTH time
            _known_treasuries = getattr(self, "_treasuries", None)
            if _known_treasuries is None:
                _known_treasuries = _confirmed_treasuries(conn)
                self._treasuries = _known_treasuries
            raw_dests = extract_close_destinations(tx)
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
                        classification, _cmeta = self._classify_recipient(conn, w, amount_sol=gain)
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
                        if store.start_session(conn, subprov=w, treasury=treasury,
                                               funding_sig=sig, funding_amount=gain,
                                               funding_time=wrap_close_time,
                                               ttl_seconds=SESSION_TTL_SEC,
                                               subprov_known=subprov_known,
                                               open_reason=open_reason):
                            child_sessions.append(w)
                            emit_event("SUBPROV_SESSION_OPENED_WS", wallet=w, related=subprov,
                                       payload={"funding_sol": gain, "sig": sig,
                                                "via": "subprov_plain_xfer",
                                                "parent_subprov": subprov})
                            _log(f"🔀 sub-subprov {subprov[:10]}… → {w[:12]}… {gain:.2f}◎ session opened")
                    return child_sessions
                return []
            # ── Phase B: record wrap-close evidence + promote to PROVISIONAL_SUBPROV ──
            # Always runs — preserves classification data even when candidate watching is off.
            try:
                store.promote_to_subprov(
                    conn,
                    subprov=subprov,
                    treasury=treasury or "",
                    wrap_close_sig=sig,
                    creator=dests[0]["candidate"],
                    amount_sol=dests[0].get("base_amount_sol"),
                )
            except Exception as _e:
                _log(f"[Phase B] promote_to_subprov failed: {_e}")
            emit_event("WRAP_CLOSE_FANOUT_DETECTED", wallet=subprov,
                       related=dests[0]["candidate"],
                       payload={"wrap_close_sig": sig, "base": dests[0].get("base_amount_sol"),
                                "dest_count": len(dests)})
            # Persist fan-out destinations + feed ProgramWatcher (one program stream, not per-wallet WS)
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
                            wrap_close_time=wrap_close_time):
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
            # Feed ProgramWatcher in-memory set → one pump.fun program stream, no per-wallet WS
            prog_watcher = getattr(self, "_prog_watcher", None)
            if prog_watcher and watcher_metas:
                prog_watcher.add_candidates(watcher_metas, conn)
                _log(f"🎯 ProgramWatcher armed with {len(watcher_metas)} candidate(s) from fanout")
            # ── Live burst-detection (BUY_SWARM safety valve) ─────────────────
            if CLASSIFICATION_ENFORCE and new_watches and self._is_buy_swarm_burst(conn, subprov):
                _log(f"⚡ burst threshold hit for {subprov[:14]}… — triggering BUY_SWARM gate")
                expired = self._gate_buy_swarm(conn, subprov, source="live_burst")
                return [("UNSUBSCRIBE", w) for w in expired]
            # Return empty: callers must not subscribe candidate wallets to WS
            return []
        finally:
            conn.close()

    # ---- handle a CANDIDATE log notification (CREATE vs SWAP) ---------------
    def _handle_candidate_tx(self, candidate, sig, ws_seen_at=None):
        """Returns ('CREATE', launch_dict) | ('SWAP', None) | (None, None).
        Captures the detection-latency timestamps (ws_seen / tx_fetched / mint_extracted) for
        the launch audit as it goes."""
        ws_seen_at = ws_seen_at or time.time()
        tx = _get_tx(sig)
        tx_fetched_at = time.time()
        is_create, mint, btime, extra = _tx_is_create(tx)
        mint_extracted_at = time.time()
        if is_create:
            create_slot = (tx or {}).get("slot")
            conn = self._ops()
            try:
                row = conn.execute(
                    "SELECT subprov_wallet, treasury_wallet, wrap_close_signature, wrap_close_time, "
                    "funding_amount "
                    "FROM wt_candidate_websocket_watches WHERE candidate_wallet=? "
                    "ORDER BY detected_at DESC LIMIT 1", (candidate,)).fetchone()
                subprov = row[0] if row else None
                treasury = row[1] if row else None
                wrap_sig = row[2] if row else None
                wrap_close_time = row[3] if row else None
                wrap_close_sol = row[4] if row else None    # subprov→creator wrap-close seed
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
                newly = store.record_launch(
                    conn, mint=mint, creator=candidate, create_sig=sig, create_time=btime,
                    treasury=treasury, subprov=subprov, wrap_close_sig=wrap_sig,
                    birth_to_launch_s=btl, create_slot=create_slot,
                    subprov_funding_sol=subprov_funding_sol, wrap_close_sol=wrap_close_sol)
                if newly:
                    self._reconcile_bridge(conn, candidate, mint, btime, birth_time, subprov, treasury)
                return "CREATE", {"mint": mint, "subprov": subprov, "treasury": treasury,
                                  "create_time": btime, "btl": btl, "wrap_sig": wrap_sig,
                                  "create_sig": sig, "newly": newly, "create_slot": create_slot,
                                  "bonding_curve": extra.get("bonding_curve"),
                                  "associated_bonding_curve": extra.get("associated_bonding_curve"),
                                  "mint_source": extra.get("mint_source"),
                                  "ws_seen_at": ws_seen_at, "tx_fetched_at": tx_fetched_at,
                                  "mint_extracted_at": mint_extracted_at}
            finally:
                conn.close()
        if _tx_is_swap(tx):
            # reverse-direction swarm attribution: capture the mint this swarm wallet BOUGHT,
            # from the tx we already have (zero extra RPC), so it can be linked to its launch.
            return "SWAP", {"swap_mint": _swap_target_mint(tx)}
        return None, None

    # ---- shared candidate-sig processor (WS notification AND catch-up) ------
    async def process_candidate_sig(self, candidate, sig):
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
        verdict, launch = await _ato_thread(self._handle_candidate_tx, candidate, sig, ws_seen_at)
        if verdict == "CREATE":
            btl = launch.get("btl")
            mode = "INSTANT" if (btl is not None and btl < 60) else ("STAGED" if btl is not None else "?")
            # only emit + teardown when THIS call newly recorded the launch (idempotent)
            if launch.get("newly"):
                _log(f"🚀 WATCHTOWER LAUNCH creator={candidate[:12]}… mint={launch.get('mint')} "
                     f"btl={btl}s [{mode}] (src={launch.get('mint_source')})")
                emit_event("WATCHTOWER_LAUNCH_DETECTED", wallet=candidate, related=launch.get("subprov"),
                           token_mint=launch.get("mint"),
                           payload={"create_sig": sig, "treasury": launch.get("treasury"),
                                    "birth_to_launch_s": btl, "mode": mode,
                                    "bonding_curve": launch.get("bonding_curve"),
                                    "mint_source": launch.get("mint_source")})
                alert_emitted_at = time.time()         # T4
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
            sigs = await asyncio.wait_for(
                _arpc("getSignaturesForAddress",
                      [candidate, {"limit": limit, "commitment": "processed"}]),
                timeout=_budget.NEARRT_RPC_TOTAL_S,
            ) or []
        except asyncio.TimeoutError:
            global _CATCHUP_TIMEOUT_COUNT
            _CATCHUP_TIMEOUT_COUNT += 1
            return
        except Exception:
            return
        # oldest → newest so a CREATE is recorded before any later swap is seen
        for s in sorted([x for x in sigs if not x.get("err")],
                        key=lambda x: x.get("blockTime") or 0):
            sig = s.get("signature")
            if not sig:
                continue
            verdict = await self.process_candidate_sig(candidate, sig)
            if verdict == "CREATE":
                break                                  # creator found; watch torn down

    # ---- subprov-side catch-up: recover a DROPPED/LATE wrap-close notification ----
    #      The subprov's wrap-close logsNotification can be dropped or arrive ~100s late
    #      (WS drop / receive-loop stall). Because the creator wallet is UNKNOWN until we see
    #      the wrap-close, a missed subprov notification delays discovering the creator at all.
    #      This scans an ACTIVE subprov's recent sigs for wrap-closes we haven't processed and
    #      runs the same discover→subscribe→candidate-catch-up flow — turning a ~100s miss into
    #      a few seconds. Polling can't beat a 1s atomic launch, but it makes recovery RELIABLE.
    async def catch_up_subprov(self, subprov, limit=CATCHUP_SIG_LIMIT):
        try:
            sigs = await asyncio.wait_for(
                _arpc("getSignaturesForAddress",
                      [subprov, {"limit": limit, "commitment": "processed"}]),
                timeout=_budget.NEARRT_RPC_TOTAL_S,
            ) or []
        except asyncio.TimeoutError:
            global _CATCHUP_TIMEOUT_COUNT
            _CATCHUP_TIMEOUT_COUNT += 1
            return
        except Exception:
            return
        for s in sorted([x for x in sigs if not x.get("err")],
                        key=lambda x: x.get("blockTime") or 0):
            sig = s.get("signature")
            if not sig or self._subprov_sig_seen(subprov, sig):
                continue
            # process the wrap-close exactly like a live notification would (idempotent:
            # open_candidate_watch is INSERT OR IGNORE on (candidate, wrap_close_sig)) — off-loop.
            new_watches = await _ato_thread(self._handle_subprov_tx, subprov, sig)
            for item in new_watches:
                if isinstance(item, tuple) and item[0] == "UNSUBSCRIBE":
                    await self.mgr.unsubscribe(item[1])
                    # new_watches is always [] now (handle_subprov_tx returns [] after fanout)
                    # UNSUBSCRIBE tuples are only for legacy BUY_SWARM gate; candidates never subscribed

    # ---- launch audit phase 1 (off-thread) ---------------------------------
    def _trigger_audit_phase1(self, launch, creator, create_sig, ws_seen_at, alert_emitted_at):
        """Fire the immediate audit capture in a daemon thread. Bounded, best-effort — never
        blocks or breaks the realtime detection path."""
        def _run():
            try:
                from src.core import launch_audit
                launch_audit.capture_phase1(
                    mint=launch.get("mint"), creator=creator, treasury=launch.get("treasury"),
                    subprov=launch.get("subprov"), create_signature=create_sig,
                    create_slot=launch.get("create_slot"), create_time=launch.get("create_time"),
                    ws_seen_at=ws_seen_at, tx_fetched_at=launch.get("tx_fetched_at"),
                    mint_extracted_at=launch.get("mint_extracted_at"),
                    alert_emitted_at=alert_emitted_at)
            except Exception as e:
                print(f"[WS_CASCADE] audit phase1 failed: {e}", flush=True)
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
        global _CLEANUP_COUNT
        await self.mgr.unsubscribe(creator)
        conn = self._ops()
        try:
            if subprov:
                for (sib,) in store.siblings_of(conn, subprov, creator):
                    # classify sibling OFF-LOOP (blocking RPC): SWAP → BUY_SWARM, idle → EXPIRED_SIBLING
                    state, reason = await _ato_thread(_classify_sibling, sib)
                    store.close_candidate(conn, sib, state, reason)
                    await self.mgr.unsubscribe(sib)
                    if state == "BUY_SWARM":
                        emit_event("CANDIDATE_CLASSIFIED_BUY_SWARM", wallet=sib, related=subprov)
                # close the sub-prov session if no live candidates remain
                if not store.subprov_has_live_candidates(conn, subprov):
                    sess = store.session_for_subprov(conn, subprov)
                    if sess:
                        store.close_session(conn, sess[0], "COMPLETED")
                    await self.mgr.unsubscribe(subprov)
            _CLEANUP_COUNT += 1
            emit_event("WEBSOCKET_CLEANUP_COMPLETED", wallet=creator, related=subprov,
                       payload={"cleanup_count": _CLEANUP_COUNT})
        finally:
            conn.close()

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

    async def cleanup_pass(self):
        dropped, stale_hot = self.mgr.sweep_stale_pending()
        for w in dropped:
            _log(f"⚠ dropped cold pending subscription {w[:14]}… (unconfirmed >{COLD_SUB_STALE_SEC}s)")
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
            for sid, subprov in store.expire_stale_sessions(conn):
                await self.mgr.unsubscribe(subprov)
                _log(f"🗑 session expired/dismissed {subprov[:12]}…")
                emit_event("SUBPROV_SESSION_EXPIRED", wallet=subprov)
            # ── Phase D: reject unproven PROVISION_CANDIDATEs after 2h ──────
            for sid, subprov in store.reject_unproven_sessions(conn):
                await self.mgr.unsubscribe(subprov)
                _log(f"🚫 REJECTED {subprov[:12]}… — PROVISION_CANDIDATE, no wrap-close in 2h")
                emit_event("SUBPROV_CANDIDATE_REJECTED", wallet=subprov)
        finally:
            conn.close()

    # ---- subprov sweep: catch-up every ACTIVE subprov (reliability backstop) ----
    async def subprov_sweep_pass(self):
        """Run catch_up_subprov over all ACTIVE subprovs to recover any wrap-close whose WS
        notification dropped/stalled. Bounded (MAX_ACTIVE_SUBPROVS, deduped sigs)."""
        conn = self._ops()
        try:
            subprovs = [s[1] for s in store.active_sessions(conn)[:MAX_ACTIVE_SUBPROVS]]
        finally:
            conn.close()
        for subprov in subprovs:
            await self.catch_up_subprov(subprov)


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
                new_watches = await _ato_thread(casc._handle_subprov_tx, subprov, sig)
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
            c = db_connect(OPS_DB_PATH, timeout=60)
            c.execute("PRAGMA busy_timeout=60000")
            c.execute(
                """CREATE TABLE IF NOT EXISTS wt_worker_heartbeat (
                    worker_name TEXT PRIMARY KEY, last_seen INTEGER, status TEXT, meta_json TEXT)""")
            c.execute(
                """INSERT INTO wt_worker_heartbeat (worker_name, last_seen, status, meta_json)
                   VALUES ('ws_cascade', strftime('%s','now'), 'ok', ?)
                   ON CONFLICT(worker_name) DO UPDATE SET
                     last_seen=excluded.last_seen, status=excluded.status, meta_json=excluded.meta_json""",
                (json.dumps(meta),))
            c.commit(); c.close()
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
                due = launch_audit.due_for_checkpoint(limit=20)
                for mint in due:
                    try:
                        launch_audit.run_phase2(mint)
                    except Exception:
                        pass
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
        _log("[ProgramWatcher] enabled (Phase 1 shadow mode)")
    casc._prog_watcher = prog_watcher   # attach so _handle_subprov_tx can reach it

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
        }
        if prog_watcher:
            base.update(prog_watcher.get_metrics())
        base.update({
            "wallet_profile_size":   len(casc._wallet_profile),
            "profile_hits":          casc._profile_hits,
            "profile_misses":        casc._profile_misses,
            "classify_counts":       dict(casc._classify_counts),
        })
        try:
            _hconn = casc._ops()
            base.update(store.pending_session_counts(_hconn))
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
    while not _STOP:
        try:
            _set_state("CONNECTING")
            async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=60,
                                          open_timeout=30, close_timeout=10,
                                          max_size=10 * 1024 * 1024) as ws:
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
                    last_poll = last_cleanup = last_sweep = last_temp_sweep = last_drain = 0.0
                    while not _STOP:
                        now = time.time()
                        try:
                            if now - last_poll >= POLL_SEC:
                                await casc.resync_subscriptions(); last_poll = now
                            if now - last_cleanup >= CLEANUP_SEC:
                                await casc.cleanup_pass(); last_cleanup = now
                            if now - last_sweep >= SUBPROV_SWEEP_SEC:
                                await casc.subprov_sweep_pass(); last_sweep = now
                            if now - last_temp_sweep >= TEMP_SWEEP_INTERVAL_SEC:
                                await _ato_thread(casc._temp_candidate_sweep)
                                last_temp_sweep = now
                            if now - last_drain >= 30:
                                await _ato_thread(casc._drain_pending_sessions)
                                last_drain = now
                            await _ato_thread(casc._refresh_wallet_profile_if_due)
                            # Open program-CREATE stream if candidates arrived after connect
                            if prog_watcher and prog_watcher._state == "OPENING":
                                await prog_watcher._open_stream(ws)
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
                    _log(f"WS loop error: {e} — reconnecting in {reconnect_delay}s")
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, 60)
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
        for subprov in any_opened:
            if SUBPROV_WATCH_ENABLED and subprov not in casc.mgr.wallet_kind:
                await casc.mgr.subscribe(subprov, "subprov")
                emit_event("SUBPROV_WEBSOCKET_OPENED", wallet=subprov, related=wallet)
                await casc.catch_up_subprov(subprov)
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
        for subprov in opened:
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
        raw_result = await _ato_thread(casc._handle_subprov_tx, wallet, sig)
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
