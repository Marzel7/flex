"""
WATCHTOWER CREATE Interceptor — Event-Driven ARMED Mode
=======================================================

Architecture:
  PASSIVE (always-on): TREASURY + SIGNALLER_1 + SIGNALLER_2 monitored via existing
  Helius webhook. No WebSocket cost.

  PENDING: TREASURY sends ≥10 SOL to unknown wallet → candidate recorded in memory.

  ARMED (confidence ≥ 0.75): TREASURY + SIGNALLER confirm same wallet within
  IGNITION_WINDOW_S → open accountSubscribe on wallet + logsSubscribe on pump.fun
  CREATE. Relay tracing starts in background thread.

  DISARM: CREATE intercepted, TTL expired, or wallet goes quiet → close WS
  subscriptions, record uptime metrics.

Ignition confidence model:
  TREASURY only              → 0.30  (PENDING, no WS)
  TREASURY + SIGNALLER_1     → 0.75  (ARMED, WS opens)
  TREASURY + SIGNALLER_2     → 0.75  (ARMED, WS opens)
  TREASURY + BOTH SIGNALLERS → 0.95  (ARMED, WS opens)
  +known WATCH wallet        → +0.05
  +known WATCH corridor      → +0.05

Enable via environment:
  ENABLE_CREATE_INTERCEPTOR=true
"""

import asyncio
import json
import logging
import os
import time
import threading
from dataclasses import dataclass, field
from typing import Optional, Dict, Set, List
import aiohttp
import requests

# ── Constants ─────────────────────────────────────────────────────────────────

PUMPFUN_PROGRAM  = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
SYSTEM_PROGRAM   = "11111111111111111111111111111111"
TOKEN_PROGRAM    = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
ASSOC_TOKEN_PROG = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"

_API_KEY  = os.getenv("HELIUS_API_KEY", "16f1a5fc-2592-466c-a5d4-b5799ae8da96")
_WSS_URL  = f"wss://mainnet.helius-rpc.com/?api-key={_API_KEY}"
_RPC_HTTP = f"https://mainnet.helius-rpc.com/?api-key={_API_KEY}"

JITO_ENDPOINTS = [
    "https://mainnet.block-engine.jito.co/api/v1/bundles",
]

_WALLET_KEYPAIR = None  # loaded from TRADING_KEYPAIR env var if present

def _load_keypair():
    global _WALLET_KEYPAIR
    raw = os.getenv("TRADING_KEYPAIR", "")
    if not raw:
        return
    try:
        from solders.keypair import Keypair
        _WALLET_KEYPAIR = Keypair.from_bytes(bytes(json.loads(raw)))
        log.warning(f"[INTERCEPTOR] wallet loaded pubkey={_WALLET_KEYPAIR.pubkey()}")
    except Exception as e:
        log.error(f"[INTERCEPTOR] keypair load failed: {e}")

_load_keypair()

# pump.fun program constants for dry-run signing
PUMPFUN_GLOBAL      = "4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5zDXmPu9sMbYSCa"
PUMPFUN_FEE_RECIP   = "CebN5WGQ4jvEPvsVU4EoHEpgznyZKIC643bkQo7WNMJ"
PUMPFUN_RENT_SYSVAR = "SysvarRent111111111111111111111111111111111"
PUMPFUN_EVENT_AUTH  = "Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1"
PUMPFUN_BUY_DISC    = b'\x66\x06\x3d\x12\x01\xda\xeb\xea'

# ── Ignition wallet set ────────────────────────────────────────────────────────

# Always-on ignition layer — these wallets drive PASSIVE→PENDING→ARMED transitions.
# Monitored via existing Helius webhook (zero additional WS cost).
_TREASURY_ADDR = "44orWS68MqXG198M3YXyZoNrYtsNhgnNhtUT5SavqJFM"
_SIGNALLER_ADDRS: Set[str] = {
    "44orA1BxQfFaX2iMjRbWstoqqWBE7ag8BD93ikxR4JFM",  # SIGNALLER_1
    "44o1Hecb4QUhqcRNYJBC6XZoeHWzkWAvenR5YYHRGbFM",  # SIGNALLER_2
}
_SIGNALLER_1 = "44orA1BxQfFaX2iMjRbWstoqqWBE7ag8BD93ikxR4JFM"
_SIGNALLER_2 = "44o1Hecb4QUhqcRNYJBC6XZoeHWzkWAvenR5YYHRGbFM"
_SUB_PROV = "N3TKf3wMBNu8XmZsTSnk2xWQ2LjiGvUJh1ae9Lc3dW7"

# Known WATCH infrastructure addresses (skipped as arm targets — they're already classified)
_KNOWN_INFRA: Set[str] = {
    _TREASURY_ADDR,
    "6jeT3WyrfwLxox3yAmchDg7ZQvS8XK8XXbkviPUudUW1",  # TREASURY_UP
    "N3TKf3wMBNu8XmZsTSnk2xWQ2LjiGvUJh1ae9Lc3dW7",   # SUB_PROV / PROFIT_RELAY 1
    "EYjGUZamSQ9vJBxZ4yj7pCK2XaZ99MAEQx9xRMrzyMx1",  # PROFIT_RELAY 2
    *_SIGNALLER_ADDRS,
}

# Optional: known WATCH wallet corridor (boosts confidence +0.05)
_KNOWN_WATCH_WALLETS: Set[str] = set()

# Configurable thresholds
IGNITION_WINDOW_S     = float(os.getenv("IGNITION_WINDOW_S", "60"))
TREASURY_MIN_SOL      = float(os.getenv("TREASURY_MIN_SOL", "10.0"))
ARMED_EXPIRY_S        = int(os.getenv("ARMED_EXPIRY_S", "7200"))
WS_CONFIDENCE_THRESH  = 0.75   # minimum confidence to open WebSocket subscriptions

# Relay tracing
RELAY_TINY_MAX_SOL = 1.0
FANOUT_MIN_SOL     = 10.0

# Benchmark RPC concurrency cap — limits concurrent getTransaction threads so
# Helius connection pool isn't saturated during high-volume CREATE bursts.
_BENCHMARK_RPC_SEM = threading.Semaphore(8)

# ── Benchmark mode config ─────────────────────────────────────────────────────
# INTERCEPTOR_CREATE_BENCHMARK=true  — monitor ALL pump.fun CREATEs for latency
# benchmarking. Never signs or submits. Auto-expires after TTL.
_BENCHMARK_ENABLED     = os.getenv("INTERCEPTOR_CREATE_BENCHMARK", "").lower() == "true"
_BENCHMARK_TTL_HOURS   = int(os.getenv("CREATE_BENCHMARK_TTL_HOURS", "24"))
_BENCHMARK_START_TS: Optional[float] = None   # set when benchmark activates
_BENCHMARK_TASK: Optional["asyncio.Task"] = None
_benchmark_lock = threading.Lock()

# Safety: benchmark mode hard-disables any submission path
# Allow non-zero INTERCEPTOR_BUY_SOL in benchmark mode IF SUBMIT_DISABLED=true (for DRY_RUN testing)
if _BENCHMARK_ENABLED:
    buy_sol = float(os.getenv("INTERCEPTOR_BUY_SOL", "0"))
    submit_disabled = os.getenv("SUBMIT_DISABLED", "").lower() == "true"
    assert buy_sol == 0 or submit_disabled, \
        "INTERCEPTOR_BUY_SOL must be 0 in benchmark mode unless SUBMIT_DISABLED=true"

log = logging.getLogger("wt.interceptor")
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(_h)
    log.setLevel(logging.WARNING)

# DB path
_DB_PATH = os.getenv("DB_PATH", "")
if not _DB_PATH:
    _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    _DB_PATH = os.path.join(_root, "database", "flex_complete_database.db")


# ── Operation size classification (analytics only) ────────────────────────────

def _operation_size(amount_sol: float) -> str:
    if amount_sol >= 500: return "MAJOR_OPERATOR"
    if amount_sol >= 100: return "OPERATOR_CANDIDATE"
    if amount_sol >= 50:  return "SWARM_CANDIDATE"
    return "SMALL_OPERATION"


# ── Pending Candidate (pre-ARMED state) ───────────────────────────────────────

@dataclass
class _PendingCandidate:
    wallet: str
    first_seen_ts: float = field(default_factory=time.time)
    treasury_ts: Optional[float] = None
    treasury_amount_sol: Optional[float] = None
    signaller1_ts: Optional[float] = None
    signaller2_ts: Optional[float] = None
    signaller_count: int = 0
    sub_prov_ts: Optional[float] = None
    sub_prov_amount_sol: Optional[float] = None

    def arm_confidence(self) -> float:
        if self.treasury_ts is None:
            return 0.0
        c = 0.30
        has_s1 = self.signaller1_ts is not None
        has_s2 = self.signaller2_ts is not None
        if has_s1 and has_s2:
            c = 0.95
        elif has_s1 or has_s2:
            c = 0.75
        if self.wallet in _KNOWN_WATCH_WALLETS:
            c = min(c + 0.05, 1.0)
        return c

    def trigger_source(self) -> str:
        has_s1 = self.signaller1_ts is not None
        has_s2 = self.signaller2_ts is not None
        if has_s1 and has_s2:
            return "treasury_signaller_both"
        if has_s1:
            return "treasury_signaller_1"
        if has_s2:
            return "treasury_signaller_2"
        return "treasury_only"

    def _within_window(self) -> bool:
        """All signals must fall within IGNITION_WINDOW_S of each other."""
        if self.treasury_ts is None:
            return False
        sig_ts = [t for t in (self.signaller1_ts, self.signaller2_ts) if t is not None]
        if not sig_ts:
            return False
        earliest = min(self.treasury_ts, min(sig_ts))
        latest   = max(self.treasury_ts, max(sig_ts))
        return (latest - earliest) <= IGNITION_WINDOW_S


_pending_candidates: Dict[str, _PendingCandidate] = {}
_pending_lock = threading.Lock()


# ── Armed Operation ────────────────────────────────────────────────────────────

@dataclass
class ArmedOperation:
    wallet:              str
    armed_ts:            float
    trigger_source:      str
    confidence:          float
    operation_size:      str
    treasury_ts:         Optional[float] = None
    treasury_amount_sol: Optional[float] = None
    signaller1_ts:       Optional[float] = None
    signaller2_ts:       Optional[float] = None
    # backward compat
    sub_prov:            Optional[str] = None
    relay_wallet:        Optional[str] = None
    creator_wallet:      Optional[str] = None
    expiry_ts:           float = field(default_factory=lambda: time.time() + ARMED_EXPIRY_S)
    ws_sub_ids:          List[int] = field(default_factory=list)
    # latency tracking
    relay_detected_at:    Optional[float] = None
    creator_funded_at:    Optional[float] = None
    first_outbound_ts:    Optional[float] = None
    create_seen_at:       Optional[float] = None
    buy_built_at:         Optional[float] = None
    buy_sent_at:          Optional[float] = None
    buy_landed_at:        Optional[float] = None
    websocket_armed_at:   Optional[float] = None
    websocket_disarmed_at: Optional[float] = None


@dataclass
class DetectedCreate:
    mint:          str
    creator:       str
    bonding_curve: str
    slot:          int
    signature:     str
    detected_at:   float
    armed_op:      Optional[ArmedOperation] = None
    create_seen_at: Optional[float] = None
    buy_built_at:   Optional[float] = None
    buy_sent_at:    Optional[float] = None
    buy_landed_at:  Optional[float] = None
    build_start_ts:        Optional[float] = None
    instruction_built_at:  Optional[float] = None
    tx_signed_at:          Optional[float] = None
    tx_serialized_at:      Optional[float] = None


# In-memory state — keyed by wallet (the unknown destination)
_armed_ops:      Dict[str, ArmedOperation] = {}
_armed_lock      = threading.Lock()
_known_creators: Set[str] = set()

# Fanout detection: track outbound transfers from potential fanout wallets
# Maps fanout_wallet -> { recipient_wallet -> [(amount_sol, ts), ...] }
_fanout_txs: Dict[str, Dict[str, list]] = {}
_fanout_lock = threading.Lock()

# Event loop reference (set on start())
_interceptor_loop: Optional[asyncio.AbstractEventLoop] = None

# Ignition WS delivery metrics (Phase 1 instrumentation). The ignition logsSubscribe
# fires on `processed` commitment; getTransaction needs the tx to reach `confirmed`
# RPC visibility (~3-4s), so a fast immediate fetch returns null and the event is lost.
# These counters measure received → fetched → null → retry_success → dispatched so the
# real miss rate can be compared before/after the confirmed-commitment + retry fix.
_ignition_metrics: Dict[str, int] = {
    "ignition_ws_subscribes":    0,  # logsSubscribe/accountSubscribe acks (sub_id returned)
    "ignition_ws_received":      0,  # WS notification with a signature handed to _handle_ignition_tx
    "ignition_ws_errors":        0,  # WS connection/loop errors
    "ignition_poller_seen":      0,  # NEW sigs surfaced by the 15s getSignaturesForAddress poller
    "ignition_webhook_received": 0,  # ignition transfers arriving via the webhook path
    "ignition_tx_fetched":       0,  # getTransaction returned a non-null tx (first attempt OR retry)
    "ignition_tx_null":          0,  # first getTransaction returned null (not yet RPC-visible / missing)
    "ignition_tx_retry_success": 0,  # retry after null returned a tx (recovered a would-be drop)
    "ignition_tx_dispatched":    0,  # _dispatch_ignition_check actually called (outbound transfer found)
    "ignition_arm_count":        0,  # _arm() fired
}
_ignition_metrics_lock = threading.Lock()

def _ign_metric(key: str, n: int = 1):
    with _ignition_metrics_lock:
        _ignition_metrics[key] = _ignition_metrics.get(key, 0) + n


# ── DB Schema ─────────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS wt_armed_operations (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet               TEXT    NOT NULL DEFAULT '',
    armed_ts             REAL    NOT NULL,
    expiry_ts            REAL    NOT NULL,
    trigger_source       TEXT    NOT NULL,
    confidence           REAL    DEFAULT 0.0,
    operation_size       TEXT,
    treasury_ts          REAL,
    treasury_amount_sol  REAL,
    signaller1_ts        REAL,
    signaller2_ts        REAL,
    sub_prov             TEXT,
    relay_wallet         TEXT,
    creator_wallet       TEXT,
    state                TEXT    NOT NULL DEFAULT 'ARMED',
    disarmed_ts          REAL,
    disarm_reason        TEXT,
    websocket_armed_at   REAL,
    websocket_disarmed_at REAL,
    websocket_uptime_s   REAL,
    first_outbound_ts    REAL,
    creator_detected_ts  REAL,
    create_after_arm_s   REAL,
    created_at           INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_wt_armed_state  ON wt_armed_operations(state, expiry_ts);
CREATE INDEX IF NOT EXISTS idx_wt_armed_wallet ON wt_armed_operations(wallet, state);

CREATE TABLE IF NOT EXISTS wt_detected_creates (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    mint                TEXT    NOT NULL UNIQUE,
    creator             TEXT    NOT NULL,
    bonding_curve       TEXT,
    slot                INTEGER,
    signature           TEXT,
    armed_op_id         INTEGER REFERENCES wt_armed_operations(id),
    detected_at         REAL    NOT NULL,
    relay_detected_at   REAL,
    creator_funded_at   REAL,
    create_seen_at      REAL,
    buy_built_at        REAL,
    buy_sent_at         REAL,
    buy_landed_at       REAL,
    create_to_build_ms  REAL,
    create_to_submit_ms REAL,
    create_to_land_ms   REAL,
    slot_delta          INTEGER,
    created_at          INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_wt_creates_mint ON wt_detected_creates(mint);
CREATE INDEX IF NOT EXISTS idx_wt_creates_ts   ON wt_detected_creates(detected_at DESC);

CREATE TABLE IF NOT EXISTS wt_swarm_recipients (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    swarm_id            TEXT    NOT NULL,
    armed_op_id         INTEGER NOT NULL REFERENCES wt_armed_operations(id),
    sub_prov_wallet     TEXT    NOT NULL,
    fanout_wallet       TEXT    NOT NULL,
    recipient_wallet    TEXT    NOT NULL UNIQUE,
    funded_ts           REAL    NOT NULL,
    confidence          REAL    DEFAULT 0.75,
    created_at          INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_wt_swarm_recipient_wallet ON wt_swarm_recipients(recipient_wallet);
CREATE INDEX IF NOT EXISTS idx_wt_swarm_id ON wt_swarm_recipients(swarm_id);
CREATE INDEX IF NOT EXISTS idx_wt_swarm_armed_op ON wt_swarm_recipients(armed_op_id);

-- Ignition feed health (persisted so a dead feed is detectable; the in-memory
-- counters reset every restart and hid the 431-subscribes/0-received outage).
CREATE TABLE IF NOT EXISTS wt_ignition_metrics (
    ts                       INTEGER PRIMARY KEY,   -- hour bucket (epoch)
    ignition_ws_subscribes   INTEGER NOT NULL DEFAULT 0,
    ignition_ws_received     INTEGER NOT NULL DEFAULT 0,
    ignition_ws_errors       INTEGER NOT NULL DEFAULT 0,
    ignition_poller_seen     INTEGER NOT NULL DEFAULT 0,
    ignition_webhook_received INTEGER NOT NULL DEFAULT 0,
    ignition_dispatch_count  INTEGER NOT NULL DEFAULT 0,
    ignition_arm_count       INTEGER NOT NULL DEFAULT 0,
    wallet_activity_seen     INTEGER NOT NULL DEFAULT 0,  -- 1 if any ignition wallet had on-chain activity this window
    alert                    TEXT                          -- e.g. 'FEED_DEAD' when active but 0 received/seen
);
"""


_MIGRATION_SQL = """
ALTER TABLE wt_armed_operations ADD COLUMN wallet                TEXT    NOT NULL DEFAULT '';
ALTER TABLE wt_armed_operations ADD COLUMN operation_size        TEXT;
ALTER TABLE wt_armed_operations ADD COLUMN treasury_ts           REAL;
ALTER TABLE wt_armed_operations ADD COLUMN treasury_amount_sol   REAL;
ALTER TABLE wt_armed_operations ADD COLUMN signaller1_ts         REAL;
ALTER TABLE wt_armed_operations ADD COLUMN signaller2_ts         REAL;
ALTER TABLE wt_armed_operations ADD COLUMN websocket_armed_at    REAL;
ALTER TABLE wt_armed_operations ADD COLUMN websocket_disarmed_at REAL;
ALTER TABLE wt_armed_operations ADD COLUMN websocket_uptime_s    REAL;
ALTER TABLE wt_armed_operations ADD COLUMN first_outbound_ts     REAL;
ALTER TABLE wt_armed_operations ADD COLUMN creator_detected_ts   REAL;
ALTER TABLE wt_armed_operations ADD COLUMN create_after_arm_s    REAL;
ALTER TABLE wt_detected_creates ADD COLUMN build_start_ts        REAL;
ALTER TABLE wt_detected_creates ADD COLUMN instruction_built_at  REAL;
ALTER TABLE wt_detected_creates ADD COLUMN tx_signed_at          REAL;
ALTER TABLE wt_detected_creates ADD COLUMN tx_serialized_at      REAL;
ALTER TABLE wt_detected_creates ADD COLUMN sign_ms               REAL;
ALTER TABLE wt_detected_creates ADD COLUMN serialize_ms          REAL;
ALTER TABLE wt_detected_creates ADD COLUMN total_build_sign_ms   REAL;
"""


def _ensure_schema():
    try:
        import sqlite3
        conn = sqlite3.connect(_DB_PATH, timeout=10)
        try:
            conn.executescript(_SCHEMA_SQL)
        except Exception:
            pass  # table already exists — that's fine
        for stmt in (list(_MIGRATION_SQL.strip().splitlines()) +
                     ["CREATE INDEX IF NOT EXISTS idx_wt_armed_wallet ON wt_armed_operations(wallet, state)"]):
            stmt = stmt.strip()
            if not stmt:
                continue
            try:
                conn.execute(stmt)
            except Exception:
                pass  # column/index already exists
        try:
            conn.commit()
        except Exception:
            pass
        conn.close()
    except Exception as e:
        log.warning(f"[INTERCEPTOR] schema init error (non-fatal, continuing): {e}")


# ── DB persistence ─────────────────────────────────────────────────────────────

def _persist_armed(op: ArmedOperation) -> Optional[int]:
    try:
        import sqlite3
        conn = sqlite3.connect(_DB_PATH, timeout=10)
        cur = conn.execute("""
            INSERT INTO wt_armed_operations
                (wallet, armed_ts, expiry_ts, trigger_source, confidence, operation_size,
                 treasury_ts, treasury_amount_sol, signaller1_ts, signaller2_ts,
                 sub_prov, relay_wallet, creator_wallet, state)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ARMED')
        """, (op.wallet, op.armed_ts, op.expiry_ts, op.trigger_source, op.confidence,
              op.operation_size, op.treasury_ts, op.treasury_amount_sol,
              op.signaller1_ts, op.signaller2_ts,
              op.sub_prov, op.relay_wallet, op.creator_wallet))
        conn.commit()
        row_id = cur.lastrowid
        conn.close()
        return row_id
    except Exception as e:
        log.warning(f"[INTERCEPTOR] persist_armed error: {e}")
        return None


def _disarm_armed_db(wallet: str, reason: str, disarmed_ts: float,
                     create_after_arm_s: Optional[float] = None,
                     creator_detected_ts: Optional[float] = None,
                     websocket_disarmed_at: Optional[float] = None,
                     websocket_armed_at: Optional[float] = None):
    try:
        import sqlite3
        conn = sqlite3.connect(_DB_PATH, timeout=10)
        ws_uptime = None
        if websocket_armed_at and websocket_disarmed_at:
            ws_uptime = websocket_disarmed_at - websocket_armed_at
        conn.execute("""
            UPDATE wt_armed_operations
            SET state = 'DISARMED',
                disarmed_ts = ?,
                disarm_reason = ?,
                websocket_disarmed_at = ?,
                websocket_uptime_s = ?,
                create_after_arm_s = ?,
                creator_detected_ts = ?
            WHERE wallet = ? AND state = 'ARMED'
        """, (disarmed_ts, reason, websocket_disarmed_at, ws_uptime,
              create_after_arm_s, creator_detected_ts, wallet))
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning(f"[INTERCEPTOR] disarm_db error: {e}")


def _persist_create(create: DetectedCreate, op: Optional[ArmedOperation], armed_id: Optional[int]):
    try:
        import sqlite3
        conn = sqlite3.connect(_DB_PATH, timeout=10)
        ctb = (create.buy_built_at - create.create_seen_at) * 1000  if create.buy_built_at  and create.create_seen_at else None
        cts = (create.buy_sent_at  - create.create_seen_at) * 1000  if create.buy_sent_at   and create.create_seen_at else None
        ctl = (create.buy_landed_at - create.create_seen_at) * 1000 if create.buy_landed_at and create.create_seen_at else None
        sign_ms = (create.tx_signed_at - create.build_start_ts) * 1000 if create.tx_signed_at and create.build_start_ts else None
        serialize_ms = (create.tx_serialized_at - create.tx_signed_at) * 1000 if create.tx_serialized_at and create.tx_signed_at else None
        total_build_sign = (create.tx_serialized_at - create.build_start_ts) * 1000 if create.tx_serialized_at and create.build_start_ts else None
        conn.execute("""
            INSERT OR IGNORE INTO wt_detected_creates
                (mint, creator, bonding_curve, slot, signature, armed_op_id,
                 detected_at, relay_detected_at, creator_funded_at, create_seen_at,
                 buy_built_at, buy_sent_at, buy_landed_at,
                 create_to_build_ms, create_to_submit_ms, create_to_land_ms,
                 build_start_ts, instruction_built_at, tx_signed_at, tx_serialized_at,
                 sign_ms, serialize_ms, total_build_sign_ms)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (create.mint, create.creator, create.bonding_curve, create.slot,
              create.signature, armed_id, create.detected_at,
              op.relay_detected_at if op else None,
              op.creator_funded_at if op else None,
              create.create_seen_at,
              create.buy_built_at, create.buy_sent_at, create.buy_landed_at,
              ctb, cts, ctl,
              create.build_start_ts, create.instruction_built_at, create.tx_signed_at, create.tx_serialized_at,
              sign_ms, serialize_ms, total_build_sign))
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning(f"[INTERCEPTOR] persist_create error: {e}")


# ── Armed state management ─────────────────────────────────────────────────────

def _arm(candidate: _PendingCandidate):
    """Transition a pending candidate to ARMED state."""
    now = time.time()
    confidence = candidate.arm_confidence()
    op = ArmedOperation(
        wallet=candidate.wallet,
        armed_ts=now,
        trigger_source=candidate.trigger_source(),
        confidence=confidence,
        operation_size=_operation_size(candidate.treasury_amount_sol or 0),
        treasury_ts=candidate.treasury_ts,
        treasury_amount_sol=candidate.treasury_amount_sol,
        signaller1_ts=candidate.signaller1_ts,
        signaller2_ts=candidate.signaller2_ts,
        expiry_ts=now + ARMED_EXPIRY_S,
        relay_detected_at=now,
    )
    with _armed_lock:
        _armed_ops[candidate.wallet] = op

    armed_id = _persist_armed(op)
    _ign_metric("ignition_arm_count")
    # Observability for the 60s→120s window widen: stamp the actual signaller lag and
    # which window band armed it (≤60s = original, 60-120s = newly-recovered cohort).
    # Lets us later compare arms/false-positives by band. signaller_lag_s, arm_confidence,
    # armed_reason (=trigger), and window_used_s are all reconstructable from the persisted
    # treasury_ts/signaller*_ts columns, so no schema change is needed.
    _sig_ts = [t for t in (candidate.signaller1_ts, candidate.signaller2_ts) if t is not None]
    _lag = (max(candidate.treasury_ts, max(_sig_ts)) - min(candidate.treasury_ts, min(_sig_ts))) \
           if (_sig_ts and candidate.treasury_ts) else None
    _band = "≤60s" if (_lag is not None and _lag <= 60) else ("60-120s" if _lag is not None else "n/a")
    log.info(
        f"[INTERCEPTOR] ⚡ ARMED  wallet={candidate.wallet[:20]}  "
        f"trigger={op.trigger_source}  confidence={confidence:.2f}  "
        f"size={op.operation_size}  treasury={candidate.treasury_amount_sol} SOL  "
        f"signaller_lag_s={_lag if _lag is None else round(_lag,1)}  band={_band}  "
        f"window_used_s={IGNITION_WINDOW_S:.0f}  id={armed_id}"
    )

    # Open WebSocket subscriptions in event loop
    if _interceptor_loop and not _interceptor_loop.is_closed():
        asyncio.run_coroutine_threadsafe(_arm_websockets(candidate.wallet), _interceptor_loop)

    # Trace relay chain in background thread
    if candidate.treasury_amount_sol:
        threading.Thread(
            target=_trace_relay_to_creator,
            args=(candidate.wallet, candidate.wallet, now),
            daemon=True,
            name=f"wt-relay-{candidate.wallet[:8]}"
        ).start()

    return op, armed_id


def disarm(wallet: str, reason: str):
    """Disarm a wallet — closes WS subscriptions and records metrics."""
    now = time.time()
    with _armed_lock:
        op = _armed_ops.pop(wallet, None)

    if op is None:
        return

    create_after_arm_s = None
    if op.create_seen_at:
        create_after_arm_s = op.create_seen_at - op.armed_ts

    ws_disarmed_at = now
    if _interceptor_loop and not _interceptor_loop.is_closed():
        asyncio.run_coroutine_threadsafe(_disarm_websockets(wallet, op), _interceptor_loop)
        ws_disarmed_at = now

    _disarm_armed_db(
        wallet, reason, now,
        create_after_arm_s=create_after_arm_s,
        creator_detected_ts=op.creator_funded_at,
        websocket_disarmed_at=ws_disarmed_at,
        websocket_armed_at=op.websocket_armed_at,
    )
    log.info(
        f"[INTERCEPTOR] disarmed wallet={wallet[:20]}  reason={reason}  "
        f"ws_uptime_s={round(ws_disarmed_at - op.websocket_armed_at) if op.websocket_armed_at else 'n/a'}"
    )


def is_armed() -> bool:
    now = time.time()
    with _armed_lock:
        active = {k: v for k, v in _armed_ops.items() if v.expiry_ts > now}
        expired = set(_armed_ops.keys()) - set(active.keys())
        _armed_ops.clear()
        _armed_ops.update(active)
    for w in expired:
        log.info(f"[INTERCEPTOR] expired ARMED wallet={w[:20]}")
        _disarm_armed_db(w, "ttl_expired", now)
    return len(active) > 0


def get_armed_ops() -> Dict[str, ArmedOperation]:
    now = time.time()
    with _armed_lock:
        return {k: v for k, v in _armed_ops.items() if v.expiry_ts > now}


# ── Benchmark mode ─────────────────────────────────────────────────────────────

def _is_benchmark_active() -> bool:
    """True while benchmark mode is running and within TTL."""
    global _BENCHMARK_ENABLED, _BENCHMARK_START_TS
    with _benchmark_lock:
        if not _BENCHMARK_ENABLED or _BENCHMARK_START_TS is None:
            return False
        elapsed_h = (time.time() - _BENCHMARK_START_TS) / 3600
        if elapsed_h >= _BENCHMARK_TTL_HOURS:
            _expire_benchmark_unlocked()
            return False
        return True


def _expire_benchmark_unlocked():
    """Called with _benchmark_lock held. Disables benchmark mode."""
    global _BENCHMARK_ENABLED, _BENCHMARK_START_TS, _BENCHMARK_TASK
    _BENCHMARK_ENABLED = False
    elapsed = round((time.time() - _BENCHMARK_START_TS) / 3600, 1) if _BENCHMARK_START_TS else 0
    _BENCHMARK_START_TS = None
    _pumpfun_armed_wallets.discard("__benchmark__")
    log.info(f"[BENCHMARK] expired after {elapsed}h — pump.fun CREATE WS closed (if no armed wallets remain)")
    if _BENCHMARK_TASK and not _BENCHMARK_TASK.done():
        _BENCHMARK_TASK.cancel()
    _BENCHMARK_TASK = None


def benchmark_status() -> dict:
    """Return benchmark state for status endpoint and dashboard."""
    with _benchmark_lock:
        active = _BENCHMARK_ENABLED and _BENCHMARK_START_TS is not None
        elapsed_h = round((time.time() - _BENCHMARK_START_TS) / 3600, 2) if _BENCHMARK_START_TS else None
        remaining_h = round(_BENCHMARK_TTL_HOURS - elapsed_h, 2) if elapsed_h is not None else None
    try:
        import sqlite3
        conn = sqlite3.connect(_DB_PATH, timeout=5)
        cutoff = time.time() - 86400
        row = conn.execute("""
            SELECT COUNT(*) n,
                   AVG(build_ms) avg_build_ms,
                   MAX(build_ms) max_build_ms,
                   SUM(CASE WHEN build_ms IS NOT NULL THEN 1 ELSE 0 END) with_build,
                   SUM(CASE WHEN est_position_top5  THEN 1 ELSE 0 END) top5,
                   SUM(CASE WHEN est_position_top10 THEN 1 ELSE 0 END) top10,
                   SUM(CASE WHEN est_position_top25 THEN 1 ELSE 0 END) top25,
                   SUM(CASE WHEN build_ms > 0 THEN 1 ELSE 0 END) with_build_ms
            FROM wt_interceptor_validation
            WHERE launch_type='GENERAL_PUMPFUN' AND create_ts >= ?
        """, (cutoff,)).fetchone()
        conn.close()
        n        = row[0] or 0
        avg_bms  = round(row[1], 2) if row[1] else None
        max_bms  = round(row[2], 2) if row[2] else None
        top25_rate    = round(row[6] / n * 100, 1) if n > 0 else None
        with_build_ms = row[7] or 0
    except Exception:
        n = avg_bms = max_bms = top25_rate = missed = None
    return {
        "active":        active,
        "elapsed_h":     elapsed_h,
        "remaining_h":   remaining_h,
        "ttl_h":         _BENCHMARK_TTL_HOURS,
        "creates_24h":   n,
        "avg_build_ms":  avg_bms,
        "max_build_ms":  max_bms,
        "top25_rate":    top25_rate,
        "with_build_ms_24h": with_build_ms,
    }


# ── Ignition engine ────────────────────────────────────────────────────────────

def _dispatch_ignition_check(source: str, dest: str, amount_sol: float, ts: float = None):
    """
    Entry point called from webhook_handler for every transfer.
    Called BEFORE the dust filter so SIGNALLER dust (0.00001 SOL) is captured.

    TREASURY → dest ≥ TREASURY_MIN_SOL  → on_treasury_transfer()
    SIGNALLER → dest (any amount)        → on_signaller_transfer()
    SUB_PROV → dest                     → on_sub_prov_transfer()
    any source → dest                   → track_fanout_transfer() for pattern detection
    """
    if ts is None:
        ts = time.time()
    if source == _TREASURY_ADDR and amount_sol >= TREASURY_MIN_SOL:
        on_treasury_transfer(source, dest, amount_sol, ts)
    elif source in _SIGNALLER_ADDRS:
        on_signaller_transfer(source, dest, amount_sol, ts)
    elif source == _SUB_PROV:
        on_sub_prov_transfer(source, dest, amount_sol, ts)

    # Track all transfers for fan-out pattern detection
    # Lightweight: only stores transfers in a 120-second sliding window
    track_fanout_transfer(source, dest, amount_sol, ts)


def on_treasury_transfer(source: str, dest: str, amount_sol: float, ts: float = None):
    """
    TREASURY → dest ≥ TREASURY_MIN_SOL.
    Creates or updates a PENDING_CANDIDATE. Does NOT arm alone.
    """
    if ts is None:
        ts = time.time()
    if dest in _KNOWN_INFRA:
        return
    with _pending_lock:
        cand = _pending_candidates.get(dest)
        if cand is None:
            cand = _PendingCandidate(wallet=dest)
            _pending_candidates[dest] = cand
        cand.treasury_ts         = ts
        cand.treasury_amount_sol = amount_sol
    log.info(
        f"[INTERCEPTOR] TREASURY→{dest[:20]}  {amount_sol:.4f} SOL  "
        f"size={_operation_size(amount_sol)}  → PENDING"
    )
    _check_ignition(dest)


def on_signaller_transfer(source: str, dest: str, amount_sol: float, ts: float = None):
    """
    SIGNALLER_1 or SIGNALLER_2 → dest (any amount, including 0.00001 SOL dust).
    Creates or updates a PENDING_CANDIDATE. May trigger ARMED if treasury already fired.
    """
    if ts is None:
        ts = time.time()
    if dest in _KNOWN_INFRA:
        return
    with _pending_lock:
        cand = _pending_candidates.get(dest)
        if cand is None:
            cand = _PendingCandidate(wallet=dest)
            _pending_candidates[dest] = cand
        if source == _SIGNALLER_1:
            cand.signaller1_ts = ts
        elif source == _SIGNALLER_2:
            cand.signaller2_ts = ts
        cand.signaller_count += 1
    log.debug(
        f"[INTERCEPTOR] SIGNALLER({source[:8]})→{dest[:20]}  {amount_sol:.5f} SOL  "
        f"signaller_count={cand.signaller_count}"
    )
    _check_ignition(dest)


def on_sub_prov_transfer(source: str, dest: str, amount_sol: float, ts: float = None):
    """
    SUB_PROV → dest (distribution/fan-out signal).
    Creates or updates a PENDING_CANDIDATE with sub_prov signal.
    High amounts + rapid consecutive transfers indicate bot army funding.
    """
    if ts is None:
        ts = time.time()
    if dest in _KNOWN_INFRA:
        return
    with _pending_lock:
        cand = _pending_candidates.get(dest)
        if cand is None:
            cand = _PendingCandidate(wallet=dest)
            _pending_candidates[dest] = cand
        cand.sub_prov_ts = ts
        cand.sub_prov_amount_sol = amount_sol
    log.info(
        f"[INTERCEPTOR] SUB_PROV→{dest[:20]}  {amount_sol:.4f} SOL  "
        f"(fan-out/distribution signal)  → PENDING"
    )
    _check_ignition(dest)


def store_swarm_recipients(swarm_id: str, armed_op_id: int, sub_prov_wallet: str,
                           fanout_wallet: str, recipients: list, confidence: float = 0.75):
    """
    Store swarm recipient wallets for later CREATE matching.

    Args:
        swarm_id: unique identifier for this swarm (e.g. "SWARM_20260602_123340")
        armed_op_id: ID of the parent ARMED operation
        sub_prov_wallet: the SUB_PROV address that initiated
        fanout_wallet: the intermediate distributor wallet
        recipients: list of recipient wallet addresses
        confidence: confidence score to inherit (default 0.75)
    """
    try:
        conn = sqlite3.connect(_DB_PATH, timeout=5)
        cursor = conn.cursor()

        ts = time.time()
        inserted = 0

        for recipient in recipients:
            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO wt_swarm_recipients
                        (swarm_id, armed_op_id, sub_prov_wallet, fanout_wallet,
                         recipient_wallet, funded_ts, confidence)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (swarm_id, armed_op_id, sub_prov_wallet, fanout_wallet,
                      recipient, ts, confidence))
                inserted += 1
            except Exception as e:
                log.warning(f"[SWARM] recipient store failed {recipient[:20]}: {e}")
                continue

        conn.commit()
        conn.close()

        log.warning(f"[SWARM] stored {inserted}/{len(recipients)} recipients  "
                   f"swarm_id={swarm_id}  armed_op_id={armed_op_id}")
    except Exception as e:
        log.error(f"[SWARM] store_swarm_recipients error: {e}")


def lookup_swarm_recipient(creator_wallet: str) -> Optional[dict]:
    """
    O(1) indexed lookup: check if creator_wallet is a known swarm recipient.

    Returns:
        dict with: {
            'swarm_id': str,
            'armed_op_id': int,
            'sub_prov_wallet': str,
            'fanout_wallet': str,
            'confidence': float,
            'funded_ts': float,
        }
        or None if not found
    """
    try:
        conn = sqlite3.connect(_DB_PATH, timeout=2)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT swarm_id, armed_op_id, sub_prov_wallet, fanout_wallet, confidence, funded_ts
            FROM wt_swarm_recipients
            WHERE recipient_wallet = ?
            LIMIT 1
        """, (creator_wallet,))

        row = cursor.fetchone()
        conn.close()

        if row:
            return {
                'swarm_id': row['swarm_id'],
                'armed_op_id': row['armed_op_id'],
                'sub_prov_wallet': row['sub_prov_wallet'],
                'fanout_wallet': row['fanout_wallet'],
                'confidence': row['confidence'],
                'funded_ts': row['funded_ts'],
            }
        return None
    except Exception as e:
        log.warning(f"[SWARM] lookup error for {creator_wallet[:20]}: {e}")
        return None


def track_fanout_transfer(fanout_wallet: str, recipient: str, amount_sol: float, ts: float):
    """
    Track outbound transfer from a potential fanout wallet.
    Checks if it forms a fan-out pattern and fires store_swarm_recipients if detected.
    """
    now = time.time()

    with _fanout_lock:
        if fanout_wallet not in _fanout_txs:
            _fanout_txs[fanout_wallet] = {}

        if recipient not in _fanout_txs[fanout_wallet]:
            _fanout_txs[fanout_wallet][recipient] = []

        _fanout_txs[fanout_wallet][recipient].append((amount_sol, ts))

        # Trim old transfers (>120 second window)
        for r in list(_fanout_txs[fanout_wallet].keys()):
            txs = _fanout_txs[fanout_wallet][r]
            _fanout_txs[fanout_wallet][r] = [(a, t) for a, t in txs if now - t < 120]
            if not _fanout_txs[fanout_wallet][r]:
                del _fanout_txs[fanout_wallet][r]

        # Check for fan-out pattern
        tx_history = _fanout_txs[fanout_wallet]

    pattern = detect_fan_out_pattern(fanout_wallet, tx_history)
    if not pattern:
        return

    # Pattern detected! Store recipients
    armed_ops = get_armed_ops()
    if not armed_ops:
        log.warning(f"[SWARM] fan-out detected but no ARMED operations active")
        return

    # Use first ARMED op (assuming single coordinator)
    op = next(iter(armed_ops.values()))
    swarm_id = f"SWARM_{int(now)}_{fanout_wallet[:8]}"

    try:
        recipients_list = list(pattern['recipients'])
        store_swarm_recipients(
            swarm_id=swarm_id,
            armed_op_id=op.id,
            sub_prov_wallet=_SUB_PROV,
            fanout_wallet=fanout_wallet,
            recipients=recipients_list,
            confidence=pattern['pattern_confidence'],
        )
        log.warning(
            f"[SWARM] fan-out pattern confirmed  swarm_id={swarm_id}  "
            f"recipients={len(recipients_list)}  confidence={pattern['pattern_confidence']:.2f}  "
            f"total_sol={pattern['total_distributed']:.1f}  window_s={pattern['time_window_seconds']:.0f}"
        )
    except Exception as e:
        log.error(f"[SWARM] fan-out store failed: {e}")


def detect_fan_out_pattern(source_wallet: str, tx_history: dict) -> Optional[dict]:
    """
    Detect if source_wallet distributed funds to 10+ recipients in a <120 second window.

    Args:
        source_wallet: the wallet distributing funds (e.g. SUB_PROV)
        tx_history: dict mapping recipient -> list of [amount_sol, ts] transfers

    Returns:
        dict with: {
            'pattern_detected': bool,
            'recipients': set of recipient wallets,
            'total_distributed': float,
            'time_window_seconds': float,
            'tx_count': int,
            'pattern_confidence': float,  # 0.0 - 1.0
            'fanout_wallet': str or None,  # intermediate distributor if multi-hop
        }
        or None if no pattern
    """
    if not tx_history or len(tx_history) < 10:
        return None

    recipients = list(tx_history.keys())
    all_txs = []
    total_sol = 0.0

    for recipient, transfers in tx_history.items():
        for amount_sol, ts in transfers:
            all_txs.append((ts, amount_sol, recipient))
            total_sol += amount_sol

    if not all_txs:
        return None

    all_txs.sort(key=lambda x: x[0])
    first_ts, last_ts = all_txs[0][0], all_txs[-1][0]
    time_window = last_ts - first_ts

    # Confidence scoring
    confidence = 0.0

    # +0.30 for 10+ recipients (coordinated distribution)
    if len(recipients) >= 10:
        confidence += 0.30
    if len(recipients) >= 50:
        confidence += 0.15

    # +0.25 for rapid burst (10+ txs in < 60 seconds)
    if len(all_txs) >= 10 and time_window < 60:
        confidence += 0.25
    if len(all_txs) >= 50 and time_window < 120:
        confidence += 0.15

    # +0.20 for high volume (100+ SOL distributed)
    if total_sol >= 100:
        confidence += 0.20
    if total_sol >= 500:
        confidence += 0.15

    # Pattern is positive if confidence >= 0.70
    pattern_detected = confidence >= 0.70

    return {
        'pattern_detected': pattern_detected,
        'recipients': set(recipients),
        'total_distributed': total_sol,
        'time_window_seconds': time_window,
        'tx_count': len(all_txs),
        'pattern_confidence': min(confidence, 1.0),
        'source_wallet': source_wallet,
    } if pattern_detected else None


def _check_ignition(wallet: str):
    """
    Evaluate whether a pending candidate meets the ARMED threshold.
    Prunes stale candidates. Fires _arm() when confidence >= WS_CONFIDENCE_THRESH.
    """
    now = time.time()
    # Prune stale candidates first
    with _pending_lock:
        stale = [w for w, c in _pending_candidates.items()
                 if now - c.first_seen_ts > IGNITION_WINDOW_S * 3]
        for w in stale:
            _pending_candidates.pop(w, None)
        cand = _pending_candidates.get(wallet)

    if cand is None:
        return

    # Already armed
    with _armed_lock:
        if wallet in _armed_ops:
            return

    # Check window — all signals must be within IGNITION_WINDOW_S of each other
    if not cand._within_window():
        return

    confidence = cand.arm_confidence()
    if confidence < WS_CONFIDENCE_THRESH:
        log.debug(
            f"[INTERCEPTOR] PENDING wallet={wallet[:20]}  confidence={confidence:.2f}  "
            f"(below threshold {WS_CONFIDENCE_THRESH}) — waiting for SIGNALLER confirmation"
        )
        return

    # Remove from pending, arm
    with _pending_lock:
        _pending_candidates.pop(wallet, None)

    _arm(cand)


# ── WebSocket management (runs in interceptor event loop) ─────────────────────

# Per-wallet WS connections tracked here so _disarm_websockets can close them
_armed_ws_tasks: Dict[str, asyncio.Task] = {}
_pumpfun_ws_task: Optional[asyncio.Task] = None
_pumpfun_ws_lock = asyncio.Lock() if False else threading.Lock()  # real asyncio.Lock set in start()
_pumpfun_armed_wallets: Set[str] = set()


async def _arm_websockets(wallet: str):
    """
    Open accountSubscribe on wallet + ensure pump.fun logsSubscribe is running.
    Called in the interceptor event loop when confidence >= 0.75.
    """
    global _pumpfun_ws_task

    with _armed_lock:
        op = _armed_ops.get(wallet)
    if op is None:
        return

    op.websocket_armed_at = time.time()
    _pumpfun_armed_wallets.add(wallet)

    # Start per-wallet account monitor
    task = asyncio.create_task(_monitor_wallet(wallet), name=f"wt-ws-{wallet[:8]}")
    _armed_ws_tasks[wallet] = task

    # Start pump.fun CREATE monitor if not already running
    if _pumpfun_ws_task is None or _pumpfun_ws_task.done():
        _pumpfun_ws_task = asyncio.create_task(_monitor_pumpfun_creates(), name="wt-pumpfun-create")
        log.info(f"[INTERCEPTOR] pump.fun CREATE monitor started (triggered by wallet={wallet[:20]})")

    log.info(f"[INTERCEPTOR] WS subscriptions opened for wallet={wallet[:20]}")


async def _disarm_websockets(wallet: str, op: ArmedOperation):
    """
    Cancel per-wallet WS task. Stop pump.fun monitor if no wallets remain armed.
    """
    global _pumpfun_ws_task

    _pumpfun_armed_wallets.discard(wallet)
    op.websocket_disarmed_at = time.time()

    task = _armed_ws_tasks.pop(wallet, None)
    if task and not task.done():
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=2)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

    # Stop pump.fun monitor when no wallets remain armed
    if not _pumpfun_armed_wallets and _pumpfun_ws_task and not _pumpfun_ws_task.done():
        _pumpfun_ws_task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(_pumpfun_ws_task), timeout=2)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        _pumpfun_ws_task = None
        log.info("[INTERCEPTOR] pump.fun CREATE monitor stopped — no armed wallets")

    log.info(f"[INTERCEPTOR] WS subscriptions closed for wallet={wallet[:20]}")


async def _monitor_wallet(wallet: str):
    """
    logsSubscribe on armed wallet via aiohttp.
    Cancelled by _disarm_websockets().
    """
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(_WSS_URL, heartbeat=30) as ws:
                    await ws.send_str(json.dumps({
                        "jsonrpc": "2.0", "id": 1,
                        "method": "logsSubscribe",
                        "params": [{"mentions": [wallet]}, {"commitment": "processed"}]
                    }))
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            if "result" in data and data.get("id") == 1:
                                sub_id = data["result"]
                                with _armed_lock:
                                    op = _armed_ops.get(wallet)
                                if op is not None:
                                    op.ws_sub_ids.append(sub_id)
                                log.info(f"[INTERCEPTOR] logsSubscribe wallet={wallet[:20]} sub_id={sub_id}")
                                continue
                            if data.get("method") != "logsNotification":
                                continue
                            value = data.get("params", {}).get("result", {}).get("value", {})
                            sig   = value.get("signature", "")
                            logs  = value.get("logs", [])
                            if any("transfer" in l.lower() for l in logs):
                                with _armed_lock:
                                    op = _armed_ops.get(wallet)
                                if op and op.first_outbound_ts is None:
                                    op.first_outbound_ts = time.time()
                                threading.Thread(
                                    target=_handle_wallet_tx,
                                    args=(wallet, sig, time.time()),
                                    daemon=True
                                ).start()
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break

        except asyncio.CancelledError:
            return
        except Exception as e:
            with _armed_lock:
                if wallet not in _armed_ops:
                    return
            log.debug(f"[INTERCEPTOR] wallet monitor {wallet[:20]} error: {e} — reconnecting")
            await asyncio.sleep(3)


async def _monitor_pumpfun_creates():
    """
    logsSubscribe on pump.fun program via aiohttp (compatible with gthread workers).
    Runs while at least one wallet is ARMED, OR while benchmark mode is active.
    """
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(_WSS_URL, heartbeat=30) as ws:
                    await ws.send_str(json.dumps({
                        "jsonrpc": "2.0", "id": 1,
                        "method": "logsSubscribe",
                        "params": [{"mentions": [PUMPFUN_PROGRAM]}, {"commitment": "processed"}]
                    }))
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            # Subscription confirmation
                            if "result" in data and data.get("id") == 1:
                                log.warning(f"[INTERCEPTOR] pump.fun logsSubscribe sub_id={data['result']}")
                                continue

                            if data.get("method") != "logsNotification":
                                continue

                            benchmark = _is_benchmark_active()
                            if not _pumpfun_armed_wallets and not benchmark:
                                continue

                            params = data.get("params", {})
                            result = params.get("result", {})
                            value  = result.get("value", {})
                            logs   = value.get("logs", [])
                            sig    = value.get("signature", "")
                            slot   = result.get("context", {}).get("slot", 0)

                            if not any("Instruction: Create" in l for l in logs):
                                continue

                            detected_at = time.time()
                            mode = "PASSIVE" if benchmark and not _pumpfun_armed_wallets \
                                   else os.getenv("INTERCEPTOR_MODE", "PASSIVE").upper()
                            asyncio.create_task(
                                _handle_create_async(sig, slot, detected_at, logs, mode, benchmark)
                            )

                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break

        except asyncio.CancelledError:
            return
        except Exception as e:
            still_needed = _pumpfun_armed_wallets or _is_benchmark_active()
            if not still_needed:
                return
            log.warning(f"[INTERCEPTOR] pumpfun logsSubscribe error: {e} — reconnecting")
            await asyncio.sleep(3)


# ── Wallet TX handler (relay tracing) ─────────────────────────────────────────

def _handle_wallet_tx(wallet: str, sig: str, detected_at: float):
    """Fetch tx from armed wallet, trace relay chain if outbound transfer found."""
    try:
        resp = requests.post(_RPC_HTTP, json={
            "jsonrpc": "2.0", "id": 1,
            "method": "getTransaction",
            "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
        }, timeout=5)
        tx = resp.json().get("result")
        if not tx:
            return

        accs = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])
        pre  = tx.get("meta", {}).get("preBalances", [])
        post = tx.get("meta", {}).get("postBalances", [])

        w_idx = next((i for i, a in enumerate(accs)
                      if (a if isinstance(a, str) else a.get("pubkey", "")) == wallet), None)
        if w_idx is None:
            return

        w_loss_sol = ((pre[w_idx] if w_idx < len(pre) else 0) -
                      (post[w_idx] if w_idx < len(post) else 0)) / 1e9
        if w_loss_sol <= 0:
            return

        best_gain, relay = 0, None
        for i, acc in enumerate(accs):
            addr = acc if isinstance(acc, str) else acc.get("pubkey", "")
            if addr == wallet or addr in (PUMPFUN_PROGRAM, SYSTEM_PROGRAM, TOKEN_PROGRAM, ASSOC_TOKEN_PROG):
                continue
            gain = (post[i] if i < len(post) else 0) - (pre[i] if i < len(pre) else 0)
            if gain > best_gain:
                best_gain, relay = gain, addr

        if not relay:
            return

        relay_sol = best_gain / 1e9
        bt = tx.get("blockTime", 0)

        if relay_sol <= RELAY_TINY_MAX_SOL:
            creator = _trace_relay_to_creator(relay, wallet, bt)
            if creator:
                with _armed_lock:
                    op = _armed_ops.get(wallet)
                if op:
                    op.relay_wallet   = relay
                    op.creator_wallet = creator
                    op.creator_funded_at = time.time()
                with _armed_lock:
                    _known_creators.add(creator)
                log.info(f"[INTERCEPTOR] creator traced  wallet={wallet[:20]}  creator={creator[:20]}")

    except Exception as e:
        log.debug(f"[INTERCEPTOR] handle_wallet_tx error: {e}")


# ── Relay tracing ──────────────────────────────────────────────────────────────

def _trace_relay_to_creator(relay_wallet: str, sub_prov: str,
                             funding_ts: float, max_hops: int = 3) -> Optional[str]:
    current = relay_wallet
    for hop in range(max_hops):
        try:
            resp = requests.post(_RPC_HTTP, json={
                "jsonrpc": "2.0", "id": 1,
                "method": "getSignaturesForAddress",
                "params": [current, {"limit": 5}]
            }, timeout=5)
            sigs = resp.json().get("result", [])

            for s in sigs:
                if s.get("err") is not None:
                    continue
                tx_resp = requests.post(_RPC_HTTP, json={
                    "jsonrpc": "2.0", "id": 1,
                    "method": "getTransaction",
                    "params": [s["signature"], {
                        "encoding": "jsonParsed",
                        "maxSupportedTransactionVersion": 0
                    }]
                }, timeout=5)
                tx = tx_resp.json().get("result")
                if not tx:
                    continue

                bt = tx.get("blockTime", 0)
                if abs(bt - funding_ts) > 300:
                    continue

                accs = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])
                pre  = tx.get("meta", {}).get("preBalances", [])
                post = tx.get("meta", {}).get("postBalances", [])

                cur_idx = next((i for i, a in enumerate(accs)
                                if (a if isinstance(a, str) else a.get("pubkey", "")) == current), None)
                if cur_idx is None:
                    continue

                cur_loss = (pre[cur_idx] if cur_idx < len(pre) else 0) - \
                           (post[cur_idx] if cur_idx < len(post) else 0)
                if cur_loss < 1_000:
                    continue

                best_gain, best_addr = 0, None
                for i, acc in enumerate(accs):
                    addr = acc if isinstance(acc, str) else acc.get("pubkey", "")
                    if addr == current or addr in (PUMPFUN_PROGRAM, SYSTEM_PROGRAM,
                                                   TOKEN_PROGRAM, ASSOC_TOKEN_PROG):
                        continue
                    gain = (post[i] if i < len(post) else 0) - (pre[i] if i < len(pre) else 0)
                    if gain > best_gain:
                        best_gain, best_addr = gain, addr

                if not best_addr:
                    continue

                check = requests.post(_RPC_HTTP, json={
                    "jsonrpc": "2.0", "id": 1,
                    "method": "getSignaturesForAddress",
                    "params": [best_addr, {"limit": 3}]
                }, timeout=5)
                prior_sigs = check.json().get("result", [])
                if len(prior_sigs) <= 2:
                    log.info(f"[INTERCEPTOR] creator found hop={hop+1} "
                             f"{current[:20]}→{best_addr[:20]} prior_txs={len(prior_sigs)}")
                    return best_addr

                current = best_addr
                break
        except Exception as e:
            log.debug(f"[INTERCEPTOR] relay trace error hop={hop}: {e}")
            break

    return None


# ── pump.fun CREATE handler ───────────────────────────────────────────────────

_rpc_session: Optional[aiohttp.ClientSession] = None


async def _get_rpc_session() -> aiohttp.ClientSession:
    global _rpc_session
    try:
        if _rpc_session is None or _rpc_session.closed:
            _rpc_session = aiohttp.ClientSession()
    except Exception:
        _rpc_session = aiohttp.ClientSession()
    return _rpc_session


async def _handle_create_async(sig: str, slot: int, detected_at: float, logs: list,
                                mode: str = "PASSIVE", is_benchmark: bool = False):
    """Async entry point: semaphore-gated, fetch tx via shared aiohttp session."""
    # Semaphore check is the first thing — if full, drop immediately (no sleep yet)
    if not _BENCHMARK_RPC_SEM.acquire(blocking=False):
        return
    try:
        await asyncio.sleep(4)  # let tx propagate — processed commitment, need ~3-4s for RPC visibility
        payload = json.dumps({
            "jsonrpc": "2.0", "id": 1,
            "method": "getTransaction",
            "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0,
                             "commitment": "confirmed"}]
        })
        session = await _get_rpc_session()
        async with session.post(_RPC_HTTP, data=payload,
                                headers={"Content-Type": "application/json"},
                                timeout=aiohttp.ClientTimeout(total=5)) as resp:
            result = await resp.json(content_type=None)
            tx = result.get("result")
        if not tx:
            return
        threading.Thread(
            target=_process_create_tx,
            args=(tx, sig, slot, detected_at, mode, is_benchmark),
            daemon=True
        ).start()
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.warning(f"[INTERCEPTOR] handle_create_async error: {type(e).__name__}: {e}")
    finally:
        _BENCHMARK_RPC_SEM.release()


def _process_create_tx(tx: dict, sig: str, slot: int, detected_at: float,
                       mode: str = "PASSIVE", is_benchmark: bool = False):
    """Process a fetched CREATE tx dict. Called from _handle_create_async after RPC fetch."""
    try:
        accs    = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])
        addrs   = [a if isinstance(a, str) else a.get("pubkey", "") for a in accs]
        creator = addrs[0] if addrs else None
        mint    = next((a for a in addrs[1:] if a.endswith("pump")), None)

        if not mint or not creator:
            return

        bonding_curve = _derive_bonding_curve(mint)

        create = DetectedCreate(
            mint=mint, creator=creator,
            bonding_curve=bonding_curve or "",
            slot=slot, signature=sig, detected_at=detected_at,
        )
        create.create_seen_at = detected_at

        armed_ops  = get_armed_ops()
        matched_op = None
        matched_wallet = None
        swarm_match = None

        # Match by traced creator_wallet first
        for wallet, op in armed_ops.items():
            if op.creator_wallet and op.creator_wallet == creator:
                matched_op, matched_wallet = op, wallet
                break

        # Check swarm recipients (O(1) indexed lookup)
        if matched_op is None:
            swarm_match = lookup_swarm_recipient(creator)
            if swarm_match:
                # Creator is a known swarm recipient
                # Find the ARMED op by armed_op_id
                for wallet, op in armed_ops.items():
                    if op.id == swarm_match['armed_op_id']:
                        matched_op, matched_wallet = op, wallet
                        break
                if matched_op:
                    log.warning(
                        f"[SWARM] CREATE matched recipient  creator={creator[:20]}  "
                        f"swarm_id={swarm_match['swarm_id']}  armed_op_id={swarm_match['armed_op_id']}"
                    )

        # Fallback: use first armed op if any exist and we haven't matched yet
        if matched_op is None and armed_ops:
            matched_wallet, matched_op = next(iter(armed_ops.items()))

        # Benchmark path: record every CREATE then ALWAYS return — never reaches buy logic
        if is_benchmark:
            block_time = float(tx.get("blockTime") or detected_at)
            estimated_delta = 0
            try:
                from src.core.watchtower.passive_validator import validate_create as passive_validate
                rec = passive_validate(
                    mint=mint, creator=creator, create_slot=slot,
                    create_ts=block_time, create_detected_at=detected_at,
                    armed_source=matched_op.trigger_source if matched_op else None,
                    armed_op_id=None,
                    launch_type="WATCH" if matched_op else "GENERAL_PUMPFUN",
                    watch_confidence=matched_op.confidence if matched_op else 0.0,
                )
                if rec:
                    estimated_delta = rec.estimated_slot_delta or 0
                log.warning(f"[BENCHMARK] recorded mint={mint[:20]}")
            except Exception as e:
                log.warning(f"[BENCHMARK] validate_create failed mint={mint[:20]}: {e}")
            # Schedule buyer position analysis in 45s
            try:
                from src.core.watchtower.buyer_position_analyzer import schedule_analysis
                schedule_analysis(
                    mint=mint, create_slot=slot,
                    create_ts=block_time, detected_ts=detected_at,
                    estimated_slot_delta=estimated_delta,
                )
            except Exception as e:
                log.warning(f"[BENCHMARK] schedule_analysis failed: {e}")
            return  # unconditional — benchmark mode never reaches buy logic below

        if matched_op is None:
            return

        matched_op.create_seen_at = detected_at
        log.info(
            f"[INTERCEPTOR] 🎯 CREATE  mint={mint[:20]}  creator={creator[:20]}  "
            f"matched={'creator' if matched_op.creator_wallet == creator else 'armed'}  "
            f"mode={mode}  benchmark={is_benchmark}  lag_ms={(time.time()-detected_at)*1000:.1f}"
        )

        if mode.upper() == "PASSIVE":
            if not is_benchmark:
                # Only call validate_create for ARMED path if we haven't already done it above
                try:
                    from src.core.watchtower.passive_validator import validate_create as passive_validate
                    passive_validate(
                        mint=mint, creator=creator, create_slot=slot, create_ts=detected_at,
                        create_detected_at=detected_at,
                        armed_source=matched_op.trigger_source if matched_op else None,
                        armed_op_id=None, launch_type="WATCH" if matched_op else "general",
                        watch_confidence=matched_op.confidence if matched_op else 0.0,
                    )
                except ImportError:
                    pass
            log.info("[INTERCEPTOR] passive validation recorded")
            if matched_wallet:
                disarm(matched_wallet, f"create_validated_passive:{mint[:20]}")
            return

        buy_amount_sol = float(os.getenv("INTERCEPTOR_BUY_SOL", "0"))
        if bonding_curve:
            _build_and_submit_buy(create, matched_op, buy_amount_sol, mode=mode)

        # Persist
        armed_id = None
        if matched_wallet:
            try:
                import sqlite3
                conn = sqlite3.connect(_DB_PATH, timeout=5)
                row = conn.execute(
                    "SELECT id FROM wt_armed_operations WHERE wallet=? AND state='ARMED' "
                    "ORDER BY armed_ts DESC LIMIT 1", (matched_wallet,)
                ).fetchone()
                armed_id = row[0] if row else None
                conn.close()
            except Exception:
                pass

        _persist_create(create, matched_op, armed_id)

        if matched_wallet:
            disarm(matched_wallet, f"create_detected:{mint[:20]}")

    except Exception as e:
        log.warning(f"[INTERCEPTOR] process_create_tx error: {e}")


def _derive_bonding_curve(mint: str) -> Optional[str]:
    try:
        from solders.pubkey import Pubkey  # type: ignore
        mint_pk    = Pubkey.from_string(mint)
        prog_pk    = Pubkey.from_string(PUMPFUN_PROGRAM)
        pda, _bump = Pubkey.find_program_address([b"bonding-curve", bytes(mint_pk)], prog_pk)
        return str(pda)
    except ImportError:
        try:
            resp = requests.post(_RPC_HTTP, json={
                "jsonrpc": "2.0", "id": 1, "method": "getProgramAccounts",
                "params": [PUMPFUN_PROGRAM, {
                    "filters": [{"memcmp": {"offset": 8, "bytes": mint}}],
                    "encoding": "base64"
                }]
            }, timeout=3)
            accounts = resp.json().get("result", [])
            if accounts:
                return accounts[0].get("pubkey")
        except Exception:
            pass
        return None
    except Exception as e:
        log.debug(f"[INTERCEPTOR] bonding curve derivation error: {e}")
        return None


# ── Buy execution ──────────────────────────────────────────────────────────────

def _build_pump_buy_tx(create: DetectedCreate, amount_sol: float, payer_pubkey):
    """
    Build real pump.fun buy instruction with real PDAs. No RPC calls.
    Returns the Instruction (ready for transaction assembly).
    """
    import struct
    from solders.pubkey import Pubkey
    from solders.instruction import Instruction, AccountMeta

    try:
        mint    = Pubkey.from_string(create.mint)
        bonding = Pubkey.from_string(create.bonding_curve)
        payer   = payer_pubkey

        # Derive associated bonding curve (token account for bonding curve)
        assoc_bonding = Pubkey.find_program_address(
            [bytes(bonding), bytes(Pubkey.from_string(TOKEN_PROGRAM)), bytes(mint)],
            Pubkey.from_string(ASSOC_TOKEN_PROG)
        )[0]

        # Derive ATA for buyer
        ata = Pubkey.find_program_address(
            [bytes(payer), bytes(Pubkey.from_string(TOKEN_PROGRAM)), bytes(mint)],
            Pubkey.from_string(ASSOC_TOKEN_PROG)
        )[0]

        # Instruction data: discriminator + amount_lamports (u64 LE) + max_sol_cost (u64 LE)
        lamports = int(amount_sol * 1e9)
        max_cost = int(lamports * 1.1)  # 10% slippage
        data = PUMPFUN_BUY_DISC + struct.pack('<Q', lamports) + struct.pack('<Q', max_cost)

        accounts = [
            AccountMeta(Pubkey.from_string(PUMPFUN_GLOBAL),      False, False),
            AccountMeta(Pubkey.from_string(PUMPFUN_FEE_RECIP),   False, True),
            AccountMeta(mint,                                     False, False),
            AccountMeta(bonding,                                  False, True),
            AccountMeta(assoc_bonding,                            False, True),
            AccountMeta(ata,                                      False, True),
            AccountMeta(payer,                                    True,  True),
            AccountMeta(Pubkey.from_string(SYSTEM_PROGRAM),      False, False),
            AccountMeta(Pubkey.from_string(TOKEN_PROGRAM),       False, False),
            AccountMeta(Pubkey.from_string(PUMPFUN_RENT_SYSVAR), False, False),
            AccountMeta(Pubkey.from_string(PUMPFUN_EVENT_AUTH),  False, False),
            AccountMeta(Pubkey.from_string(PUMPFUN_PROGRAM),     False, False),
        ]
        ix = Instruction(Pubkey.from_string(PUMPFUN_PROGRAM), data, accounts)
        log.debug(
            f"[DRY_RUN] built instruction: {len(accounts)} accts, {len(data)} bytes data, "
            f"amount={lamports/1e9:.3f}SOL, ata={str(ata)[:20]}..."
        )
        return ix
    except Exception as e:
        log.error(f"[INTERCEPTOR] _build_pump_buy_tx failed: {e}")
        raise


def _submit_jito(tx_base64: str):
    if _BENCHMARK_ENABLED:
        log.error("[BENCHMARK] _submit_jito called in benchmark mode — blocked")
        return None
    if os.getenv("SUBMIT_DISABLED", "").lower() == "true":
        log.error("[SUBMIT] _submit_jito blocked by SUBMIT_DISABLED=true")
        return None
    try:
        for endpoint in JITO_ENDPOINTS:
            try:
                resp = requests.post(
                    endpoint,
                    json={"jsonrpc": "2.0", "id": 1, "method": "sendBundle", "params": [[tx_base64]]},
                    timeout=5
                )
                if resp.status_code == 200:
                    result = resp.json().get("result")
                    log.info(f"[JITO] bundle submitted: {result}")
                    return result
            except Exception as e:
                log.warning(f"[JITO] {endpoint} failed: {e}")
    except Exception as e:
        log.error(f"[JITO] submission error: {e}")
    return None


def _submit_rpc(tx_base64: str, rpc_url: str):
    if _BENCHMARK_ENABLED:
        log.error("[BENCHMARK] _submit_rpc called in benchmark mode — blocked")
        return None
    if os.getenv("SUBMIT_DISABLED", "").lower() == "true":
        log.error("[SUBMIT] _submit_rpc blocked by SUBMIT_DISABLED=true")
        return None
    try:
        resp = requests.post(
            rpc_url,
            json={"jsonrpc": "2.0", "id": 1, "method": "sendTransaction",
                  "params": [tx_base64, {"encoding": "base64"}]},
            timeout=5
        )
        if resp.status_code == 200:
            sig = resp.json().get("result")
            log.info(f"[RPC] tx submitted: {sig}")
            return sig
    except Exception as e:
        log.error(f"[RPC] submission error: {e}")
    return None


def _build_and_submit_buy(create: DetectedCreate, op: Optional[ArmedOperation],
                          amount_sol: float, mode: str = "PASSIVE"):
    # Hard block: benchmark mode must never reach here
    if _BENCHMARK_ENABLED:
        log.error("[BENCHMARK] _build_and_submit_buy called in benchmark mode — blocked")
        return

    create.buy_built_at = time.time()
    build_ms = (create.buy_built_at - create.create_seen_at) * 1000

    if mode == "PASSIVE":
        log.info(
            f"[INTERCEPTOR] PASSIVE: would build buy in ~{build_ms:.1f}ms  "
            f"mint={create.mint[:20]}  amount={amount_sol} SOL"
        )
        create.buy_sent_at = create.buy_built_at
        return

    if not _WALLET_KEYPAIR:
        log.warning("[INTERCEPTOR] LIVE mode but no wallet configured (TRADING_KEYPAIR env var)")
        return

    try:
        # ─────────────────────────────────────────────────────────────────────────────
        # DRY_RUN_SIGNING + LIVE both use this path; hard return at the end if DRY_RUN
        # ─────────────────────────────────────────────────────────────────────────────
        create.build_start_ts = time.time()

        # Step 1: Build instruction (PDA derivation, no RPC)
        ix = _build_pump_buy_tx(create, amount_sol, _WALLET_KEYPAIR.pubkey())
        create.instruction_built_at = time.time()

        # Step 2: Assemble + sign transaction
        from solders.message import MessageV0
        from solders.transaction import VersionedTransaction
        from solders.hash import Hash
        import base64

        dummy_hash = Hash.default()  # No RPC needed for dry-run timing
        msg = MessageV0.try_compile(
            payer=_WALLET_KEYPAIR.pubkey(),
            instructions=[ix],
            address_lookup_table_accounts=[],
            recent_blockhash=dummy_hash,
        )
        tx = VersionedTransaction(message=msg, keypairs=[_WALLET_KEYPAIR])
        create.tx_signed_at = time.time()

        # Step 3: Serialize to wire format
        tx_bytes = bytes(tx)
        tx_base64 = base64.b64encode(tx_bytes).decode()
        create.tx_serialized_at = time.time()

        # Compute timing metrics
        build_ms     = (create.instruction_built_at - create.build_start_ts) * 1000
        sign_ms      = (create.tx_signed_at - create.build_start_ts) * 1000
        serialize_ms = (create.tx_serialized_at - create.tx_signed_at) * 1000
        total_ms     = (create.tx_serialized_at - create.create_seen_at) * 1000
        ws_to_ready  = (create.tx_serialized_at - create.create_seen_at) * 1000

        log.warning(
            f"[DRY_RUN] mint={create.mint[:20]}  "
            f"build={build_ms:.2f}ms  sign={sign_ms:.2f}ms  serialize={serialize_ms:.2f}ms  "
            f"ws→ready={ws_to_ready:.2f}ms  bytes={len(tx_bytes)}"
        )

        create.buy_sent_at = create.tx_serialized_at

        # Hard stop if dry-run mode — prevent any submission
        if mode == "DRY_RUN_SIGNING":
            return

        # ─────────────────────────────────────────────────────────────────────────────
        # LIVE only below
        # ─────────────────────────────────────────────────────────────────────────────
        submit_ms = (create.buy_sent_at - create.create_seen_at) * 1000
        log.info(
            f"[INTERCEPTOR] LIVE: built in {build_ms:.1f}ms, signed in {sign_ms:.2f}ms, "
            f"submitting in {submit_ms:.1f}ms  mint={create.mint[:20]}"
        )
        threads = [
            threading.Thread(target=_submit_jito, args=(tx_base64,), daemon=True),
            threading.Thread(target=_submit_rpc, args=(tx_base64, _RPC_HTTP), daemon=True),
        ]
        for t in threads:
            t.start()
    except Exception as e:
        log.error(f"[INTERCEPTOR] tx build/submit error: {e}")


# ── Always-on ignition monitors (TREASURY + SIGNALLER_1 + SIGNALLER_2) ────────

async def _monitor_ignition_wallet(address: str, role: str):
    """
    Permanent logsSubscribe on a single ignition wallet (TREASURY or SIGNALLER) via aiohttp.
    Runs 24/7. ~20 events/day for TREASURY, ~10/day per SIGNALLER — negligible cost.
    """
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(_WSS_URL, heartbeat=30) as ws:
                    # accountSubscribe — the CORRECT primitive for plain wallets.
                    # logsSubscribe({"mentions":[wallet]}) returns a sub_id but never
                    # delivers for non-program accounts (proven: 0 notifications vs
                    # 787 for a program control). accountSubscribe fires on every
                    # balance change. It carries no signature, so on each notification
                    # we fetch the wallet's latest signature and route it through the
                    # existing _handle_ignition_tx pipeline.
                    await ws.send_str(json.dumps({
                        "jsonrpc": "2.0", "id": 1,
                        "method": "accountSubscribe",
                        "params": [address, {"commitment": "processed", "encoding": "jsonParsed"}]
                    }))
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            if "result" in data and data.get("id") == 1:
                                _ign_metric("ignition_ws_subscribes")
                                log.warning(f"[INTERCEPTOR] ignition WS {role} accountSubscribe sub_id={data['result']}")
                                continue
                            if data.get("method") != "accountNotification":
                                continue
                            # Balance changed — fetch the most recent signature for this wallet.
                            _ign_metric("ignition_ws_received")
                            sigs = _poll_get_signatures(address, limit=1)
                            if not sigs:
                                continue
                            sig = sigs[0].get("signature", "")
                            if not sig:
                                continue
                            threading.Thread(
                                target=_handle_ignition_tx,
                                args=(address, role, sig, time.time()),
                                daemon=True,
                                name=f"wt-ign-{role[:4]}-{sig[:8]}"
                            ).start()
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break

        except asyncio.CancelledError:
            return
        except Exception as e:
            _ign_metric("ignition_ws_errors")
            log.warning(f"[INTERCEPTOR] ignition WS {role} error: {e} — reconnecting in 5s")
            await asyncio.sleep(5)


def _fetch_ignition_tx(sig: str):
    """getTransaction at `confirmed` commitment (matches the CREATE path which already
    solved the processed→null race). Returns the tx dict or None."""
    resp = requests.post(_RPC_HTTP, json={
        "jsonrpc": "2.0", "id": 1,
        "method": "getTransaction",
        "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0,
                         "commitment": "confirmed"}]
    }, timeout=5)
    return resp.json().get("result")


def _handle_ignition_tx(address: str, role: str, sig: str, detected_at: float):
    """
    Fetch a transaction seen on an ignition wallet, extract the SOL transfer,
    and dispatch into the ignition engine. Runs in a background thread.

    The ignition logsSubscribe fires on `processed` commitment, but a just-processed
    tx is not yet visible to getTransaction at finalized — so we fetch at `confirmed`
    and retry once after a short delay if the first read is null. This mirrors the
    CREATE path (sleep(4) + confirmed) which already handles this race.
    """
    _ign_metric("ignition_ws_received")
    try:
        tx = _fetch_ignition_tx(sig)
        if not tx:
            # processed→confirmed propagation race: wait and retry once before dropping.
            _ign_metric("ignition_tx_null")
            time.sleep(2)
            tx = _fetch_ignition_tx(sig)
            if not tx:
                log.warning(f"[INTERCEPTOR] ignition tx null after retry ({role} {sig[:16]}) — dropped")
                return
            _ign_metric("ignition_tx_retry_success")
        _ign_metric("ignition_tx_fetched")

        accs = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])
        pre  = tx.get("meta", {}).get("preBalances", [])
        post = tx.get("meta", {}).get("postBalances", [])
        bt   = float(tx.get("blockTime") or detected_at)

        addrs = [a if isinstance(a, str) else a.get("pubkey", "") for a in accs]

        addr_idx = next((i for i, a in enumerate(addrs) if a == address), None)
        if addr_idx is None:
            return

        net = (pre[addr_idx] if addr_idx < len(pre) else 0) - \
              (post[addr_idx] if addr_idx < len(post) else 0)
        if net <= 0:
            return  # inbound or no change — not an outbound transfer

        # Find the primary receiver (biggest gainer, excluding system programs)
        best_gain, dest = 0, None
        for i, addr in enumerate(addrs):
            if addr == address or addr in (SYSTEM_PROGRAM, TOKEN_PROGRAM, ASSOC_TOKEN_PROG, PUMPFUN_PROGRAM):
                continue
            gain = (post[i] if i < len(post) else 0) - (pre[i] if i < len(pre) else 0)
            if gain > best_gain:
                best_gain, dest = gain, addr

        if not dest:
            return

        amount_sol = best_gain / 1e9
        _dispatch_ignition_check(address, dest, amount_sol, bt)
        _ign_metric("ignition_tx_dispatched")

    except Exception as e:
        # Visible (was .debug, invisible under WARNING log level) so fetch errors are
        # measurable rather than silently lost.
        log.warning(f"[INTERCEPTOR] ignition tx fetch error ({role} {sig[:16]}): {e}")


# ── Ignition poller (feed repair) ─────────────────────────────────────────────
# Root cause (see WATCHTOWER_Ignition_Feed_Repair.md): logsSubscribe({"mentions":[wallet]})
# returns a sub_id but never delivers for plain wallets — Solana only surfaces those logs
# for PROGRAM accounts. This poller is the reliable, Helius-config-independent feed: every
# IGNITION_POLL_INTERVAL_S it lists each ignition wallet's recent signatures and hands any
# NEW ones to the same _handle_ignition_tx → _dispatch_ignition_check path. ≤15s latency
# fits inside the 53s minimum ARMED→creator-seed window.

IGNITION_POLL_INTERVAL_S = float(os.getenv("IGNITION_POLL_INTERVAL_S", "15"))
_IGNITION_POLL_WALLETS = [
    (_TREASURY_ADDR, "TREASURY"),
    (_SIGNALLER_1,   "SIGNALLER_1"),
    (_SIGNALLER_2,   "SIGNALLER_2"),
]
_ignition_poll_cursor: Dict[str, str] = {}   # wallet -> last-seen signature
_ignition_poll_stop = threading.Event()


IGNITION_METRICS_FLUSH_S = float(os.getenv("IGNITION_METRICS_FLUSH_S", "3600"))


def _ignition_metrics_loop():
    """
    Periodically persist _ignition_metrics into wt_ignition_metrics and raise a
    FEED_DEAD alert when the ignition wallets had on-chain activity but the feed
    saw nothing. This is the exact condition that went undetected before
    (431 subscribes / 0 received). Counters are snapshotted-and-reset per window.
    """
    import sqlite3
    while not _ignition_poll_stop.is_set():
        _ignition_poll_stop.wait(IGNITION_METRICS_FLUSH_S)
        if _ignition_poll_stop.is_set():
            break
        with _ignition_metrics_lock:
            snap = dict(_ignition_metrics)
            for k in _ignition_metrics:
                _ignition_metrics[k] = 0
        # Did the wallets actually have activity this window? (cheap: 1 getSignatures each)
        activity = 0
        for address, _role in _IGNITION_POLL_WALLETS:
            sigs = _poll_get_signatures(address, limit=1)
            if sigs and sigs[0].get("blockTime", 0) >= time.time() - IGNITION_METRICS_FLUSH_S:
                activity = 1
                break
        received = snap.get("ignition_ws_received", 0) + snap.get("ignition_poller_seen", 0) \
                   + snap.get("ignition_webhook_received", 0)
        alert = "FEED_DEAD" if (activity and received == 0) else None
        if alert:
            log.error(
                "[INTERCEPTOR] 🚨 IGNITION FEED_DEAD — ignition wallets had on-chain "
                "activity but 0 events were received this window (WS+poller+webhook all silent)"
            )
        try:
            conn = sqlite3.connect(_DB_PATH, timeout=10)
            conn.execute(
                "INSERT OR REPLACE INTO wt_ignition_metrics "
                "(ts, ignition_ws_subscribes, ignition_ws_received, ignition_ws_errors, "
                " ignition_poller_seen, ignition_webhook_received, ignition_dispatch_count, "
                " ignition_arm_count, wallet_activity_seen, alert) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (int(time.time() // 3600 * 3600),
                 snap.get("ignition_ws_subscribes", 0), snap.get("ignition_ws_received", 0),
                 snap.get("ignition_ws_errors", 0), snap.get("ignition_poller_seen", 0),
                 snap.get("ignition_webhook_received", 0), snap.get("ignition_tx_dispatched", 0),
                 snap.get("ignition_arm_count", 0), activity, alert),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            log.warning(f"[INTERCEPTOR] metrics flush error: {e}")


def _poll_get_signatures(address: str, limit: int = 10):
    """getSignaturesForAddress newest-first. Returns list of {signature, blockTime}."""
    try:
        resp = requests.post(_RPC_HTTP, json={
            "jsonrpc": "2.0", "id": 1,
            "method": "getSignaturesForAddress",
            "params": [address, {"limit": limit}],
        }, timeout=8)
        return resp.json().get("result") or []
    except Exception as e:
        _ign_metric("ignition_ws_errors")
        log.debug(f"[INTERCEPTOR] poll getSignatures error ({address[:8]}): {e}")
        return []


def _ignition_poll_loop():
    """
    Background thread. Polls TREASURY/S1/S2 every IGNITION_POLL_INTERVAL_S for new
    signatures and routes them through the existing _handle_ignition_tx pipeline.

    Cursor seeding: on first poll per wallet we record the newest sig WITHOUT
    dispatching (avoids replaying history on startup); subsequent polls dispatch
    only sigs newer than the cursor.
    """
    log.warning(
        f"[INTERCEPTOR] ignition poller started  interval={IGNITION_POLL_INTERVAL_S:.0f}s  "
        f"wallets=TREASURY+SIGNALLER_1+SIGNALLER_2"
    )
    while not _ignition_poll_stop.is_set():
        for address, role in _IGNITION_POLL_WALLETS:
            sigs = _poll_get_signatures(address, limit=10)
            if not sigs:
                continue
            newest = sigs[0].get("signature")
            cursor = _ignition_poll_cursor.get(address)
            if cursor is None:
                # First sight: seed cursor, don't replay history.
                _ignition_poll_cursor[address] = newest
                continue
            # Collect sigs newer than the cursor (list is newest-first).
            fresh = []
            for s in sigs:
                if s.get("signature") == cursor:
                    break
                fresh.append(s)
            if fresh:
                _ignition_poll_cursor[address] = newest
                # Dispatch oldest→newest so timing/PENDING ordering is natural.
                for s in reversed(fresh):
                    _ign_metric("ignition_poller_seen")
                    threading.Thread(
                        target=_handle_ignition_tx,
                        args=(address, role, s["signature"], time.time()),
                        daemon=True,
                        name=f"wt-poll-{role[:4]}-{s['signature'][:8]}",
                    ).start()
        _ignition_poll_stop.wait(IGNITION_POLL_INTERVAL_S)


# ── Startup ───────────────────────────────────────────────────────────────────

def _run_event_loop(loop: asyncio.AbstractEventLoop):
    asyncio.set_event_loop(loop)
    loop.run_forever()


def start(db_path: str = None):
    """
    Start the CREATE interceptor background workers.
    Called from main.py at startup if ENABLE_CREATE_INTERCEPTOR=true.

    Always-on WebSocket layer (tiny cost, ~40 events/day total):
      - TREASURY     logsSubscribe
      - SIGNALLER_1  logsSubscribe
      - SIGNALLER_2  logsSubscribe

    Dynamic WebSocket layer (only while ARMED):
      - armed wallet logsSubscribe
      - pump.fun CREATE logsSubscribe

    Webhook handler remains as fallback for ignition events.
    """
    global _DB_PATH, _interceptor_loop

    if db_path:
        _DB_PATH = db_path

    _ensure_schema()

    try:
        from src.core.watchtower.buyer_position_analyzer import ensure_schema as _bpv_schema
        _bpv_schema()
    except Exception as e:
        log.warning(f"[INTERCEPTOR] buyer_position_analyzer schema error (non-fatal): {e}")

    try:
        from src.core.watchtower.curve_impact_analyzer import ensure_schema as _cia_schema
        _cia_schema()
    except Exception as e:
        log.warning(f"[INTERCEPTOR] curve_impact_analyzer schema error (non-fatal): {e}")

    loop = asyncio.new_event_loop()
    _interceptor_loop = loop
    t = threading.Thread(target=_run_event_loop, args=(loop,), daemon=True, name="wt-interceptor")
    t.start()

    # Schedule always-on ignition monitors
    ignition_wallets = [
        (_TREASURY_ADDR, "TREASURY"),
        (_SIGNALLER_1,   "SIGNALLER_1"),
        (_SIGNALLER_2,   "SIGNALLER_2"),
        (_SUB_PROV,      "SUB_PROV"),
    ]
    for addr, role in ignition_wallets:
        asyncio.run_coroutine_threadsafe(_monitor_ignition_wallet(addr, role), loop)

    # Start the ignition POLLER — the reliable feed (WS logsSubscribe(mentions=wallet)
    # is a silent no-op for plain wallets; see WATCHTOWER_Ignition_Feed_Repair.md).
    _ignition_poll_stop.clear()
    threading.Thread(target=_ignition_poll_loop, daemon=True, name="wt-ignition-poller").start()

    # Start the metrics flusher (persists _ignition_metrics so a dead feed is detectable).
    threading.Thread(target=_ignition_metrics_loop, daemon=True, name="wt-ignition-metrics").start()

    # Start benchmark mode if configured
    if _BENCHMARK_ENABLED:
        _start_benchmark(loop)

    log.warning(
        f"[INTERCEPTOR] started  "
        f"always-on-ws=TREASURY+SIGNALLER_1+SIGNALLER_2  "
        f"benchmark={'ON ttl=' + str(_BENCHMARK_TTL_HOURS) + 'h' if _BENCHMARK_ENABLED else 'OFF'}  "
        f"db={_DB_PATH}"
    )


def _start_benchmark(loop: asyncio.AbstractEventLoop):
    """Activate benchmark mode: open pump.fun CREATE WS, schedule expiry."""
    global _BENCHMARK_START_TS
    with _benchmark_lock:
        _BENCHMARK_START_TS = time.time()

    # Add sentinel so _monitor_pumpfun_creates keeps running after it starts
    _pumpfun_armed_wallets.add("__benchmark__")

    # Schedule the coroutine directly in the interceptor event loop
    asyncio.run_coroutine_threadsafe(_monitor_pumpfun_creates(), loop)

    # Schedule auto-expiry
    expiry_s = _BENCHMARK_TTL_HOURS * 3600
    def _expiry_callback():
        time.sleep(expiry_s)
        with _benchmark_lock:
            _expire_benchmark_unlocked()
        log.warning(f"[BENCHMARK] TTL reached ({_BENCHMARK_TTL_HOURS}h) — mode disabled")

    threading.Thread(target=_expiry_callback, daemon=True, name="wt-benchmark-ttl").start()
    log.warning(f"[BENCHMARK] started — monitoring ALL pump.fun CREATEs  ttl={_BENCHMARK_TTL_HOURS}h")


# ── Status ────────────────────────────────────────────────────────────────────

def get_status() -> dict:
    ops    = get_armed_ops()
    now    = time.time()
    with _pending_lock:
        pending_count = len(_pending_candidates)

    ws_active = bool(_pumpfun_armed_wallets)

    return {
        "state":             "BENCHMARK" if _is_benchmark_active() and not ops
                             else "ARMED" if ops else "PASSIVE",
        "armed_count":       len(ops),
        "pending_count":     pending_count,
        "pumpfun_ws_active": ws_active,
        "ignition_ws_metrics": dict(_ignition_metrics),  # received→fetched→null→retry→dispatched
        "benchmark":         benchmark_status(),
        "operations": [
            {
                "wallet":          k,
                "trigger":         v.trigger_source,
                "confidence":      v.confidence,
                "operation_size":  v.operation_size,
                "treasury_sol":    v.treasury_amount_sol,
                "relay_wallet":    v.relay_wallet,
                "creator_wallet":  v.creator_wallet,
                "armed_age_s":     round(now - v.armed_ts),
                "expires_in_s":    round(v.expiry_ts - now),
                "ws_armed_at":     v.websocket_armed_at,
            }
            for k, v in ops.items()
        ],
        "pending_candidates": [
            {
                "wallet":       k,
                "confidence":   v.arm_confidence(),
                "has_treasury": v.treasury_ts is not None,
                "has_s1":       v.signaller1_ts is not None,
                "has_s2":       v.signaller2_ts is not None,
                "age_s":        round(now - v.first_seen_ts),
                "operation_size": _operation_size(v.treasury_amount_sol or 0),
            }
            for k, v in _pending_candidates.items()
        ],
        "websocket_cost_estimate": {
            "always_on_hours_per_month":  720,
            "armed_hours_per_month_est":  "≤8 (2–4 WATCH ops × 120min max)",
            "reduction_pct":              "~99%",
        },
    }
