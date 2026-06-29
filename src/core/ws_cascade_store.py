"""WATCHTOWER real-time WebSocket cascade — storage layer.

The DB is the handoff boundary between the webhook/API layer (which WRITES active
sub-prov sessions) and the standalone `ws_cascade` daemon (which CONSUMES sessions,
opens/tears-down websocket watches, and records launches). No direct calls cross the
boundary — only these tables.

Three tables, all in wt_ops_v2.db:
  wt_active_subprov_sessions     — a confirmed treasury funded a confirmed SUB_PROV
  wt_candidate_websocket_watches — every closeAccount.destination we're temporarily watching
  wt_watchtower_launches         — the AUTHORITATIVE launch ledger (creator confirmed by CREATE)

States:
  session:   ACTIVE | COMPLETED | EXPIRED | ERROR
  candidate: WATCHING | FIRED_CREATE | BUY_SWARM | EXPIRED | EXPIRED_SIBLING | ERROR

Events (→ watchtower_events in the LIVE db, same sink the forward-walk uses):
  SUBPROV_SESSION_STARTED, SUBPROV_WEBSOCKET_OPENED, WRAP_CLOSE_FANOUT_DETECTED,
  CANDIDATE_WEBSOCKET_OPENED, WATCHTOWER_LAUNCH_DETECTED, CANDIDATE_CLASSIFIED_BUY_SWARM,
  CANDIDATE_WATCH_EXPIRED, SUBPROV_SESSION_EXPIRED, WEBSOCKET_CLEANUP_COMPLETED
"""

from __future__ import annotations

import os
import json
import time
from typing import Optional

try:
    from src.utils.db_locking import db_connect
except Exception:  # pragma: no cover - fallback for isolated runs
    import sqlite3

    def db_connect(path, timeout=30, row_factory=None):
        c = sqlite3.connect(path, timeout=timeout)
        if row_factory:
            c.row_factory = row_factory
        return c

# RAW connect for best-effort telemetry writes (treasury hits + events). These must NOT go
# through db_connect's process-wide write SERIALIZER: the cascade has multiple writer threads
# (event-writer + per-hit threads), and the serializer turned their concurrency into immediate
# SQLITE_BUSY failures (the "database is locked" storm after moving to the ops DB). A plain
# connection with busy_timeout correctly WAITS out the brief scheduler write-bursts on the ops
# DB (measured: 20/20 writes succeed under a hammering concurrent writer). Cross-process
# coordination is busy_timeout's job, not the in-process serializer's.
def _telemetry_conn(path, busy_ms=15000):
    """Raw connection bypassing the write serializer (see note above)."""
    try:
        from src.utils.db_locking import _sqlite3_connect_orig as _orig
    except Exception:
        import sqlite3 as _s
        _orig = _s.connect
    c = _orig(path, timeout=max(30, busy_ms // 1000))
    c.execute(f"PRAGMA busy_timeout={int(busy_ms)}")
    return c


OPS_DB_PATH = os.environ.get(
    "OPS_V2_DB_PATH",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "database", "wt_ops_v2.db")),
)
LIVE_DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "database", "flex_complete_database.db")),
)

# state vocab (single source of truth)
SESSION_STATES = ("ACTIVE", "COMPLETED", "EXPIRED", "ERROR")
CANDIDATE_STATES = ("WATCHING", "FIRED_CREATE", "BUY_SWARM", "EXPIRED", "EXPIRED_SIBLING", "ERROR")

FUNDING_MECHANISM = "WSOL_WRAP_CLOSE"
EXTRACTION_METHOD = "CLOSE_ACCOUNT_DESTINATION"


# ─────────────────────────────── schema ─────────────────────────────────────
def ensure_cascade_schema(conn) -> None:
    """Idempotent. Creates the three cascade tables + indexes in wt_ops_v2.db."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS wt_active_subprov_sessions (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            subprov_wallet    TEXT NOT NULL,
            treasury_wallet   TEXT,
            funding_signature TEXT,
            funding_amount    REAL,
            funding_time      INTEGER,
            subprov_known     INTEGER DEFAULT 0,   -- 1 = already in wt_discovered_subprovs (confidence, NOT a gate)
            state             TEXT NOT NULL DEFAULT 'ACTIVE',
            detected_at       INTEGER NOT NULL,
            expires_at        INTEGER,
            closed_at         INTEGER,
            UNIQUE(subprov_wallet, funding_signature)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS wt_candidate_websocket_watches (
            id                       INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_wallet         TEXT NOT NULL,
            subprov_wallet           TEXT,
            treasury_wallet          TEXT,
            wrap_close_signature     TEXT,
            wrap_close_time          INTEGER,    -- on-chain blockTime of the wrap-close = the creator's BIRTH
            wrap_wallet              TEXT,
            temp_wsol_account        TEXT,
            close_destination        TEXT,
            funding_amount           REAL,
            state                    TEXT NOT NULL DEFAULT 'WATCHING',
            websocket_subscription_id TEXT,
            detected_at              INTEGER NOT NULL,
            expires_at               INTEGER,
            closed_at                INTEGER,
            close_reason             TEXT,
            UNIQUE(candidate_wallet, wrap_close_signature)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS wt_watchtower_launches (
            id                        INTEGER PRIMARY KEY AUTOINCREMENT,
            mint                      TEXT,
            creator_wallet            TEXT NOT NULL,
            create_signature          TEXT,
            create_time               INTEGER,
            create_slot               INTEGER,
            treasury_wallet           TEXT,
            subprov_wallet            TEXT,
            subprov_funding_sol       REAL,    -- treasury → subprov load (the big provisioning capital)
            wrap_close_sol            REAL,    -- subprov → creator wrap-close seed (the creator's birth amount)
            wrap_close_signature      TEXT,
            birth_to_launch_seconds   INTEGER,
            funding_mechanism         TEXT DEFAULT 'WSOL_WRAP_CLOSE',
            creator_extraction_method TEXT DEFAULT 'CLOSE_ACCOUNT_DESTINATION',
            confidence                TEXT DEFAULT 'STRICT',
            state                     TEXT DEFAULT 'FIRED_CREATE',
            recorded_at               INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            UNIQUE(creator_wallet, create_signature)
        )"""
    )
    # Per-treasury WS usage meter — one row per treasury, hit counters so the UI can
    # spot a treasury that turns into a high-volume swarm hub BEFORE it bloats the daemon.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS wt_treasury_ws_usage (
            treasury_wallet           TEXT PRIMARY KEY,
            subscribed_at             INTEGER,
            notif_count               INTEGER DEFAULT 0,   -- total WS notifications seen
            sessions_opened           INTEGER DEFAULT 0,   -- provisioning outbounds → sessions
            last_notif_at             INTEGER,
            last_notif_sig            TEXT,
            notif_count_1h            INTEGER DEFAULT 0,    -- rolling-hour count (reset by reader)
            hour_bucket               INTEGER DEFAULT 0     -- epoch//3600 the 1h count belongs to
        )"""
    )
    # REVERSE-DIRECTION swarm attribution: a BUY_SWARM candidate (a wrap-close-seeded wallet that
    # SWAPped instead of CREATEd) recorded against the mint it bought + its subprov. Lets a later
    # swarm WAVE attach to its launch in the token tree. Populated zero-extra-RPC from the swap tx
    # the cascade already fetched. UNIQUE(swarm_wallet, mint) dedupes repeat buys.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS wt_swarm_buys (
            swarm_wallet              TEXT,
            mint                      TEXT,
            subprov_wallet            TEXT,
            treasury_wallet           TEXT,
            swap_signature            TEXT,
            observed_at               INTEGER,
            UNIQUE(swarm_wallet, mint)
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_swarm_buys_mint ON wt_swarm_buys(mint)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_subprov_sessions_state ON wt_active_subprov_sessions(state)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_cand_watch_state ON wt_candidate_websocket_watches(state)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_cand_watch_subprov ON wt_candidate_websocket_watches(subprov_wallet)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_launches_creator ON wt_watchtower_launches(creator_wallet)")
    # migrate: add create_slot to a pre-existing launches table (audit needs it for tx-position)
    try:
        _cols = {r[1] for r in conn.execute("PRAGMA table_info(wt_watchtower_launches)").fetchall()}
        if "create_slot" not in _cols:
            conn.execute("ALTER TABLE wt_watchtower_launches ADD COLUMN create_slot INTEGER")
    except Exception:
        pass
    # migrate: add subprov_known to a pre-existing sessions table
    try:
        _scols = {r[1] for r in conn.execute("PRAGMA table_info(wt_active_subprov_sessions)").fetchall()}
        if "subprov_known" not in _scols:
            conn.execute("ALTER TABLE wt_active_subprov_sessions ADD COLUMN subprov_known INTEGER DEFAULT 0")
    except Exception:
        pass
    # migrate: add wrap_close_time to a pre-existing watches table (true creator birth)
    try:
        _wcols = {r[1] for r in conn.execute("PRAGMA table_info(wt_candidate_websocket_watches)").fetchall()}
        if "wrap_close_time" not in _wcols:
            conn.execute("ALTER TABLE wt_candidate_websocket_watches ADD COLUMN wrap_close_time INTEGER")
    except Exception:
        pass
    # migrate: add the two funding amounts to a pre-existing launches table
    try:
        _lcols = {r[1] for r in conn.execute("PRAGMA table_info(wt_watchtower_launches)").fetchall()}
        if "subprov_funding_sol" not in _lcols:
            conn.execute("ALTER TABLE wt_watchtower_launches ADD COLUMN subprov_funding_sol REAL")
        if "wrap_close_sol" not in _lcols:
            conn.execute("ALTER TABLE wt_watchtower_launches ADD COLUMN wrap_close_sol REAL")
    except Exception:
        pass
    # ── Phase A: subprov classification instrumentation ──────────────────────
    # open_reason on sessions — records what classification fired when the session was opened
    try:
        _scols2 = {r[1] for r in conn.execute("PRAGMA table_info(wt_active_subprov_sessions)").fetchall()}
        if "open_reason" not in _scols2:
            conn.execute(
                "ALTER TABLE wt_active_subprov_sessions ADD COLUMN "
                "open_reason TEXT DEFAULT 'PROVISION_CANDIDATE'"
            )
        if "initial_funding_amount" not in _scols2:
            conn.execute("ALTER TABLE wt_active_subprov_sessions ADD COLUMN initial_funding_amount REAL")
        if "topup_count" not in _scols2:
            conn.execute("ALTER TABLE wt_active_subprov_sessions ADD COLUMN topup_count INTEGER DEFAULT 0")
        if "topup_amount_total" not in _scols2:
            conn.execute("ALTER TABLE wt_active_subprov_sessions ADD COLUMN topup_amount_total REAL DEFAULT 0.0")
        if "last_topup_at" not in _scols2:
            conn.execute("ALTER TABLE wt_active_subprov_sessions ADD COLUMN last_topup_at INTEGER")
    except Exception:
        pass
    # confidence + state + wrap_close_count + topup_count on discovered subprovs
    try:
        _dcols = {r[1] for r in conn.execute("PRAGMA table_info(wt_discovered_subprovs)").fetchall()}
        if "confidence" not in _dcols:
            conn.execute("ALTER TABLE wt_discovered_subprovs ADD COLUMN confidence REAL DEFAULT 0.20")
        if "state" not in _dcols:
            conn.execute(
                "ALTER TABLE wt_discovered_subprovs ADD COLUMN "
                "state TEXT DEFAULT 'PROVISION_CANDIDATE'"
            )
        if "wrap_close_count" not in _dcols:
            conn.execute("ALTER TABLE wt_discovered_subprovs ADD COLUMN wrap_close_count INTEGER DEFAULT 0")
        if "topup_count" not in _dcols:
            conn.execute("ALTER TABLE wt_discovered_subprovs ADD COLUMN topup_count INTEGER DEFAULT 0")
        if "rejected_reason" not in _dcols:
            conn.execute("ALTER TABLE wt_discovered_subprovs ADD COLUMN rejected_reason TEXT")
    except Exception:
        pass
    # wt_subprov_evidence — one row per observed wrap-close, links subprov→creator
    conn.execute(
        """CREATE TABLE IF NOT EXISTS wt_subprov_evidence (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            subprov         TEXT NOT NULL,
            wrap_close_sig  TEXT NOT NULL UNIQUE,
            creator_wallet  TEXT NOT NULL,
            amount_sol      REAL,
            observed_at     INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            create_fired    INTEGER DEFAULT 0
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_se_subprov ON wt_subprov_evidence(subprov)")
    # wt_subprov_topups — top-up history (treasury seeds same subprov again)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS wt_subprov_topups (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            subprov     TEXT NOT NULL,
            treasury    TEXT NOT NULL,
            sig         TEXT NOT NULL UNIQUE,
            amount_sol  REAL,
            recorded_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_st_subprov ON wt_subprov_topups(subprov)")
    # ── Phase E Pass 1: subprov behaviour typing ──────────────────────────────
    try:
        _dcols = {r[1] for r in conn.execute("PRAGMA table_info(wt_discovered_subprovs)").fetchall()}
        if "buy_swarm_count" not in _dcols:
            conn.execute("ALTER TABLE wt_discovered_subprovs ADD COLUMN buy_swarm_count INTEGER DEFAULT 0")
        if "create_count" not in _dcols:
            conn.execute("ALTER TABLE wt_discovered_subprovs ADD COLUMN create_count INTEGER DEFAULT 0")
        if "buy_swarm_ratio" not in _dcols:
            conn.execute("ALTER TABLE wt_discovered_subprovs ADD COLUMN buy_swarm_ratio REAL DEFAULT 0.0")
        if "subprov_type" not in _dcols:
            conn.execute(
                "ALTER TABLE wt_discovered_subprovs ADD COLUMN "
                "subprov_type TEXT DEFAULT 'UNKNOWN'"
                # UNKNOWN | CREATOR_PROVISIONER | BUY_SWARM_PROVISIONER | MIXED | HISTORICAL
            )
    except Exception:
        pass
    # Cascade telemetry now lives in the OPS DB (not the contended hot DB): the cascade is its
    # own service, so its WS hits + events persist here where there's no listener write storm.
    # (The hot DB keeps its OWN copies for the legacy listener/main.py writers — untouched.)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS wt_webhook_hits (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            webhook_id       TEXT NOT NULL, wallet_address TEXT NOT NULL,
            tx_signature     TEXT, tx_type TEXT, source TEXT, counterparty TEXT,
            slot INTEGER, block_time INTEGER, amount_sol REAL,
            is_fee_touch INTEGER NOT NULL DEFAULT 0, is_pamm_interaction INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')), direction TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wh_created ON wt_webhook_hits(created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wh_wallet_time ON wt_webhook_hits(wallet_address, created_at DESC)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_wh_sig_wallet ON wt_webhook_hits(tx_signature, wallet_address)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS watchtower_events (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            event_sequence   INTEGER NOT NULL DEFAULT 0,
            event_type TEXT NOT NULL, wallet_address TEXT, related_wallet TEXT,
            token_mint TEXT, payload_json TEXT, source TEXT,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')))""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wt_events_wallet ON watchtower_events(wallet_address, created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wt_events_type ON watchtower_events(event_type, created_at DESC)")
    # ── Behaviour-first subprov gating (Pass F) ──────────────────────────────
    # NEW_SUBPROV wallets park here until they demonstrate a wrap-close event.
    # Only then does start_session() open a WS subscription (CONFIRMED_SUBPROV).
    conn.execute(
        """CREATE TABLE IF NOT EXISTS wt_temp_provision_candidates (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            wallet          TEXT NOT NULL UNIQUE,
            treasury        TEXT,
            funding_sig     TEXT,
            funding_amount  REAL,
            funding_time    INTEGER,
            detected_at     INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            expires_at      INTEGER NOT NULL,
            state           TEXT NOT NULL DEFAULT 'PENDING',
            -- PENDING | PROMOTED | EXPIRED | SCANNED
            promoted_at     INTEGER,
            scanned_at      INTEGER,
            scan_result     TEXT
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_temp_prov_state ON wt_temp_provision_candidates(state, expires_at)"
    )
    # ── Durable session retry queue ───────────────────────────────────────────
    # When start_session() fails with database locked, the detection context is
    # preserved here and retried by the drain loop. High-value events (≥10 SOL)
    # are flagged CRITICAL so the health dashboard surfaces them immediately.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS wt_pending_session_writes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            treasury        TEXT NOT NULL,
            subprov         TEXT NOT NULL,
            funding_sig     TEXT NOT NULL,
            funding_amount  REAL,
            funding_time    INTEGER,
            open_reason     TEXT NOT NULL DEFAULT 'PROVISION_CANDIDATE',
            subprov_known   INTEGER NOT NULL DEFAULT 0,
            ttl_seconds     INTEGER NOT NULL,
            enqueued_at     INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            retry_count     INTEGER NOT NULL DEFAULT 0,
            last_retry_at   INTEGER,
            state           TEXT NOT NULL DEFAULT 'PENDING',
            -- PENDING | WRITTEN | SUPERSEDED | FAILED
            priority        TEXT NOT NULL DEFAULT 'NORMAL',
            -- NORMAL | CRITICAL (≥HIGH_VALUE_PROVISION_SOL)
            failure_reason  TEXT
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_psw_state ON wt_pending_session_writes(state, enqueued_at)"
    )
    conn.commit()


# ─────────────────────────────── events ─────────────────────────────────────
# CRITICAL: emit_event is called INLINE from the cascade's async processor task. It MUST NOT
# block the event loop — a blocking live-DB write under the lock storm (time.sleep retry +
# 30s busy_timeout) was stalling the processor for seconds per event, which timed out WS
# subscription confirmations (30s) and broke the treasury tier entirely (0 sessions opened).
# Fix: emit_event just enqueues; a single background writer thread drains + writes the live DB
# (with the lock-retry happening THERE, off the event loop). Fire-and-forget, never blocks.
import queue as _queue_mod
import threading as _threading_mod

_event_q: "_queue_mod.Queue" = _queue_mod.Queue(maxsize=5000)
_writer_started = False
_writer_lock = _threading_mod.Lock()

# Item types on _event_q:
#   ('event', et, wallet, related, mint, payload, ts)   — watchtower_events row
#   ('hit',   treasury, counterparty, sig, amount_sol, block_time, ts)  — wt_webhook_hits row


def _event_writer_loop():
    """Single background thread draining all ops-DB writes. One writer = no lock contention."""
    wh_id = os.environ.get("WATCHTOWER_INFRA_WEBHOOK_ID", "106e20f6-f542-42b0-83d5-ca8c7b1a7162")
    while True:
        item = _event_q.get()
        if item is None:
            continue
        kind = item[0]
        for _attempt in range(3):
            try:
                c = _telemetry_conn(OPS_DB_PATH, busy_ms=60000)
                try:
                    if kind == 'event':
                        _, et, wallet, related, mint, payload, ts = item
                        c.execute(
                            "INSERT INTO watchtower_events (event_type, wallet_address, related_wallet, "
                            "token_mint, payload_json, source, created_at) VALUES (?,?,?,?,?,?,?)",
                            (et, wallet, related, mint, json.dumps(payload or {}), "ws_cascade", ts))
                    elif kind == 'hit':
                        _, treasury, counterparty, sig, amount_sol, block_time, ts = item[:7]
                        hit_tx_type = item[7] if len(item) > 7 else 'TRANSFER'
                        c.execute(
                            """INSERT OR IGNORE INTO wt_webhook_hits
                                 (webhook_id, wallet_address, tx_signature, tx_type, source,
                                  counterparty, block_time, amount_sol, is_fee_touch, created_at, direction)
                               VALUES (?, ?, ?, ?, 'treasury_ws', ?, ?, ?, 0, ?, 'outbound')""",
                            (wh_id, treasury, sig, hit_tx_type, counterparty, block_time, amount_sol, ts))
                    c.commit()
                    break
                finally:
                    c.close()
            except Exception as e:
                if _attempt < 2:
                    time.sleep(2 ** _attempt)
                    continue
                print(f"[WS_CASCADE] ops-db write failed {kind}: {e}", flush=True)
                break


def _ensure_writer():
    global _writer_started
    if _writer_started:
        return
    with _writer_lock:
        if _writer_started:
            return
        _threading_mod.Thread(target=_event_writer_loop, daemon=True,
                              name="ws-cascade-event-writer").start()
        _writer_started = True


def emit_event(event_type: str, wallet: Optional[str] = None,
               related: Optional[str] = None, token_mint: Optional[str] = None,
               payload: Optional[dict] = None) -> None:
    """Enqueue a cascade event for the background writer (NON-BLOCKING — never touches the DB
    on the caller's thread). Drops silently if the queue is full (telemetry, not critical)."""
    _ensure_writer()
    try:
        _event_q.put_nowait(('event', event_type, wallet, related, token_mint, payload, int(time.time())))
    except _queue_mod.Full:
        pass   # event log is best-effort; never block the cascade for a breadcrumb


def lookup_subprov(conn, wallet: str) -> Optional[dict]:
    """Return the wt_discovered_subprovs row for wallet, or None if unknown.
    Used by _classify_recipient to distinguish known subprovs from fresh recipients.
    Includes Phase E fields (buy_swarm_count, create_count, buy_swarm_ratio, subprov_type)
    with safe defaults when columns don't exist on older DB versions.
    """
    row = conn.execute(
        "SELECT subprov, creator_count, treasury, treasury_known, "
        "wrap_close_count, topup_count, confidence, state, "
        "COALESCE(buy_swarm_count,0), COALESCE(create_count,0), "
        "COALESCE(buy_swarm_ratio,0.0), COALESCE(subprov_type,'UNKNOWN') "
        "FROM wt_discovered_subprovs WHERE subprov=?", (wallet,)
    ).fetchone()
    if row is None:
        return None
    cols = ["subprov", "creator_count", "treasury", "treasury_known",
            "wrap_close_count", "topup_count", "confidence", "state",
            "buy_swarm_count", "create_count", "buy_swarm_ratio", "subprov_type"]
    return dict(zip(cols, row))


def is_historical_subprov(conn, wallet: str) -> bool:
    """Phase E Pass 1: return True if a wallet shows evidence of pre-existing subprov
    activity that WATCHTOWER didn't track at the time. Zero-cost — DB only, no RPC.

    A wallet is 'historical' if any of these are true:
      - Already in wt_discovered_subprovs with wrap-close evidence (backfill or prior session)
      - Appears as subprov_wallet in wt_candidate_websocket_watches (was active before)
      - Has a prior EXPIRED/COMPLETED session (we saw it before, it ran its course)
    """
    if conn.execute(
        "SELECT 1 FROM wt_discovered_subprovs "
        "WHERE subprov=? AND wrap_close_count > 0 LIMIT 1", (wallet,)
    ).fetchone():
        return True
    if conn.execute(
        "SELECT 1 FROM wt_candidate_websocket_watches "
        "WHERE subprov_wallet=? LIMIT 1", (wallet,)
    ).fetchone():
        return True
    if conn.execute(
        "SELECT 1 FROM wt_active_subprov_sessions "
        "WHERE subprov_wallet=? AND state IN ('EXPIRED','COMPLETED') LIMIT 1", (wallet,)
    ).fetchone():
        return True
    return False


def record_candidate_outcome(conn, *, subprov: str, outcome: str) -> None:
    """Phase E Pass 1: record that a candidate from this subprov resolved as CREATE or BUY_SWARM.
    Updates buy_swarm_count / create_count / buy_swarm_ratio / subprov_type on
    wt_discovered_subprovs. Called from _handle_candidate_tx after classification.
    outcome must be 'CREATE' or 'BUY_SWARM'.
    """
    if outcome not in ("CREATE", "BUY_SWARM"):
        return
    col = "buy_swarm_count" if outcome == "BUY_SWARM" else "create_count"
    now = int(time.time())
    try:
        conn.execute(
            f"UPDATE wt_discovered_subprovs SET {col} = {col} + 1, last_seen = ? "
            "WHERE subprov = ?", (now, subprov))
        # recompute ratio + reclassify type in same pass
        conn.execute(
            """UPDATE wt_discovered_subprovs SET
                 buy_swarm_ratio = CASE
                   WHEN (buy_swarm_count + create_count) = 0 THEN 0.0
                   ELSE CAST(buy_swarm_count AS REAL) / (buy_swarm_count + create_count)
                 END,
                 subprov_type = CASE
                   WHEN (buy_swarm_count + create_count) < 5 THEN 'UNKNOWN'
                   WHEN CAST(buy_swarm_count AS REAL) / (buy_swarm_count + create_count) > 0.7
                     THEN 'BUY_SWARM_PROVISIONER'
                   WHEN CAST(create_count AS REAL) / (buy_swarm_count + create_count) > 0.7
                     THEN 'CREATOR_PROVISIONER'
                   ELSE 'MIXED'
                 END
               WHERE subprov = ?""", (subprov,))
        conn.commit()
    except Exception:
        pass


def mark_non_provisioning_recipients(conn) -> int:
    """Maintenance sweep: find treasury recipients with ≥3 expired sessions, 0 wrap-close,
    0 create — mark subprov_type='NON_PROVISIONING_RECIPIENT' so _classify_recipient skips
    them on future fundings.

    Reversible: record_candidate_outcome() overwrites subprov_type when wrap-close/CREATE
    is observed, promoting the wallet back into the active pipeline.

    Returns count of newly marked wallets.
    """
    try:
        # Upsert into wt_discovered_subprovs for wallets not already there
        conn.execute("""
            INSERT INTO wt_discovered_subprovs (subprov, treasury, first_seen, last_seen,
                wrap_close_count, create_count, buy_swarm_count, subprov_type,
                treasury_known, confidence)
            SELECT
                s.subprov_wallet,
                s.treasury_wallet,
                MIN(s.funding_time),
                MAX(s.funding_time),
                0, 0, 0,
                'NON_PROVISIONING_RECIPIENT',
                1,
                'LOW'
            FROM wt_active_subprov_sessions s
            LEFT JOIN wt_discovered_subprovs d ON d.subprov = s.subprov_wallet
            WHERE d.subprov IS NULL
            GROUP BY s.subprov_wallet, s.treasury_wallet
            HAVING COUNT(*) >= 3
               AND SUM(CASE WHEN s.state='EXPIRED' THEN 1 ELSE 0 END) >= 3
            ON CONFLICT(subprov) DO NOTHING
        """)
        # Update existing rows that still have no provisioning evidence
        conn.execute("""
            UPDATE wt_discovered_subprovs
            SET subprov_type = 'NON_PROVISIONING_RECIPIENT'
            WHERE subprov_type IN ('UNKNOWN', 'NON_PROVISIONING_RECIPIENT')
              AND COALESCE(wrap_close_count, 0) = 0
              AND COALESCE(create_count, 0) = 0
              AND subprov IN (
                  SELECT subprov_wallet FROM wt_active_subprov_sessions
                  GROUP BY subprov_wallet
                  HAVING COUNT(*) >= 3
                     AND SUM(CASE WHEN state='EXPIRED' THEN 1 ELSE 0 END) >= 3
              )
        """)
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) FROM wt_discovered_subprovs "
            "WHERE subprov_type='NON_PROVISIONING_RECIPIENT'"
        ).fetchone()[0]
        return count
    except Exception:
        return 0


def promote_to_subprov(conn, *, subprov: str, treasury: str,
                       wrap_close_sig: str, creator: str,
                       amount_sol: Optional[float]) -> None:
    """Record wrap-close evidence and promote a PROVISION_CANDIDATE to PROVISIONAL_SUBPROV.

    Called once per wrap-close fan-out observed in _handle_subprov_tx. Idempotent on
    wrap_close_sig (UNIQUE constraint on wt_subprov_evidence). Updates confidence and
    state on wt_discovered_subprovs so _classify_recipient returns REACTIVATED next time
    this wallet receives treasury funding.
    """
    now = int(time.time())
    # 1. Record the evidence row (idempotent)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO wt_subprov_evidence "
            "(subprov, wrap_close_sig, creator_wallet, amount_sol, observed_at) "
            "VALUES (?,?,?,?,?)",
            (subprov, wrap_close_sig, creator, amount_sol, now))
    except Exception:
        pass

    # 2. Upsert wt_discovered_subprovs — recount from evidence table (idempotent).
    conn.execute(
        """INSERT OR IGNORE INTO wt_discovered_subprovs
             (subprov, first_creator, creator_count, treasury, treasury_known,
              first_seen, last_seen, wrap_close_count, state, confidence)
           VALUES (?,?,1,?,0,?,?,1,'PROVISIONAL_SUBPROV',0.45)""",
        (subprov, creator, treasury, now, now))
    conn.execute(
        """UPDATE wt_discovered_subprovs SET
             wrap_close_count = (SELECT COUNT(*)                    FROM wt_subprov_evidence WHERE subprov=?),
             creator_count    = (SELECT COUNT(DISTINCT creator_wallet) FROM wt_subprov_evidence WHERE subprov=?),
             last_seen        = ?,
             state            = CASE
               WHEN state = 'PROVISION_CANDIDATE' THEN 'PROVISIONAL_SUBPROV'
               ELSE state END,
             confidence       = MIN(0.74, 0.20 +
               (SELECT COUNT(*) FROM wt_subprov_evidence WHERE subprov=?) * 0.08)
           WHERE subprov = ?""",
        (subprov, subprov, now, subprov, subprov))
    conn.commit()


def record_treasury_hit(*, treasury: str, counterparty: str, sig: str,
                        amount_sol: float, block_time: Optional[int],
                        tx_type: str = "TRANSFER") -> None:
    """Enqueue a treasury outbound hit for the single background writer (non-blocking).
    tx_type='TREASURY_MESH' for treasury→treasury transfers (recorded but no session opened)."""
    _ensure_writer()
    try:
        _event_q.put_nowait(('hit', treasury, counterparty, sig, amount_sol, block_time, int(time.time()), tx_type))
    except _queue_mod.Full:
        pass  # best-effort telemetry


# ──────────────────────────── session helpers ───────────────────────────────
def start_session(conn, *, subprov: str, treasury: Optional[str], funding_sig: Optional[str],
                  funding_amount: Optional[float], funding_time: Optional[int],
                  ttl_seconds: int, subprov_known: int = 0,
                  open_reason: str = "PROVISION_CANDIDATE",
                  monitoring_state: str = "LIVE_ARMED") -> bool:
    """Record a confirmed treasury→SUB_PROV funding as an ACTIVE session. Idempotent on
    (subprov, funding_sig). Returns True if a NEW session row was created.

    The active SUB_PROV is DISCOVERED from this funding — it need NOT already be in
    wt_discovered_subprovs. `subprov_known` records whether it happened to be known (a
    confidence signal), but membership is never a gate for session creation.

    `open_reason` is the Phase-A classification label:
      PROVISION_CANDIDATE  — unknown recipient, unproven
      SUBPROV_TOP_UP       — known subprov, extending/re-opening
      SUBPROV_REACTIVATED  — historical subprov (wrap-close seen), re-opened
    """
    now = int(time.time())

    # ── Active-session dedup ─────────────────────────────────────────────────
    # If a session is already ACTIVE for this subprov (any prior funding), extend its
    # TTL and skip the INSERT — we're already subscribed, a new row would be an orphan.
    # This is broader than the old SUBPROV_TOP_UP-only dedup: any re-funding of an
    # already-watched subprov (regardless of classification) extends rather than duplicates.
    active = conn.execute(
        "SELECT id FROM wt_active_subprov_sessions "
        "WHERE subprov_wallet=? AND state='ACTIVE' LIMIT 1", (subprov,)).fetchone()
    if active:
        # only count as a top-up if a real amount was transferred (zero = internal bookkeeping call)
        _amt = funding_amount or 0.0
        if _amt > 0:
            conn.execute(
                "UPDATE wt_active_subprov_sessions "
                "SET expires_at=MAX(expires_at,?), "
                "    topup_count=COALESCE(topup_count,0)+1, "
                "    topup_amount_total=COALESCE(topup_amount_total,0)+?, "
                "    last_topup_at=? "
                "WHERE subprov_wallet=? AND state='ACTIVE'",
                (now + ttl_seconds, _amt, now, subprov))
        else:
            conn.execute(
                "UPDATE wt_active_subprov_sessions SET expires_at=MAX(expires_at,?) "
                "WHERE subprov_wallet=? AND state='ACTIVE'",
                (now + ttl_seconds, subprov))
        if open_reason == "SUBPROV_TOP_UP":
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO wt_subprov_topups "
                    "(subprov, treasury, sig, amount_sol, recorded_at) VALUES (?,?,?,?,?)",
                    (subprov, treasury or "", funding_sig or "", funding_amount, now))
            except Exception:
                pass
        conn.commit()
        return False   # no new session row — caller should not re-subscribe

    cur = conn.execute(
        """INSERT OR IGNORE INTO wt_active_subprov_sessions
             (subprov_wallet, treasury_wallet, funding_signature, funding_amount,
              initial_funding_amount, funding_time, subprov_known, open_reason,
              monitoring_state, state, detected_at, expires_at)
           VALUES (?,?,?,?,?,?,?,?,?, 'ACTIVE', ?, ?)""",
        (subprov, treasury, funding_sig, funding_amount, funding_amount,
         funding_time or now, int(subprov_known), open_reason, monitoring_state, now, now + ttl_seconds))
    conn.commit()
    if cur.rowcount == 0:
        # duplicate funding_sig for a non-TOP_UP (e.g. webhook + WS both fire) →
        # extend TTL so an active subprov stays subscribed through a long campaign.
        conn.execute(
            "UPDATE wt_active_subprov_sessions SET expires_at=MAX(expires_at,?) "
            "WHERE subprov_wallet=? AND state='ACTIVE'",
            (now + ttl_seconds, subprov))
        conn.commit()
    return cur.rowcount > 0


def active_sessions(conn) -> list:
    return conn.execute(
        "SELECT id, subprov_wallet, treasury_wallet, funding_signature, funding_amount, "
        "funding_time, expires_at, open_reason, subprov_known, "
        "COALESCE(monitoring_state,'LIVE_ARMED') as monitoring_state "
        "FROM wt_active_subprov_sessions WHERE state='ACTIVE'").fetchall()


# ───────────────── durable session retry queue ──────────────────────────────
HIGH_VALUE_PROVISION_SOL = float(os.environ.get("HIGH_VALUE_PROVISION_SOL", "10"))


def enqueue_pending_session(conn, *, treasury: str, subprov: str, funding_sig: str,
                             funding_amount: Optional[float], funding_time: Optional[int],
                             open_reason: str, subprov_known: int, ttl_seconds: int) -> None:
    """Persist a session write that failed due to DB lock. Preserves original detection
    context so retry replays the exact intended action, independent of runtime flags."""
    priority = "CRITICAL" if (funding_amount or 0) >= HIGH_VALUE_PROVISION_SOL else "NORMAL"
    try:
        conn.execute(
            """INSERT OR IGNORE INTO wt_pending_session_writes
                 (treasury, subprov, funding_sig, funding_amount, funding_time,
                  open_reason, subprov_known, ttl_seconds, priority)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (treasury, subprov, funding_sig, funding_amount, funding_time,
             open_reason, subprov_known, ttl_seconds, priority))
        conn.commit()
    except Exception:
        pass  # if even this fails, caller logs CRITICAL


def drain_pending_sessions(conn) -> tuple[int, int]:
    """Retry PENDING session writes. Returns (written, remaining).
    Replays original detection context — does NOT reclassify under current runtime flags."""
    now = int(time.time())
    rows = conn.execute(
        "SELECT id, treasury, subprov, funding_sig, funding_amount, funding_time, "
        "open_reason, subprov_known, ttl_seconds, priority "
        "FROM wt_pending_session_writes WHERE state='PENDING' "
        "ORDER BY priority DESC, enqueued_at ASC LIMIT 20"
    ).fetchall()
    written = 0
    superseded = 0
    for r in rows:
        try:
            # Check if already superseded by an active session opened another way
            already = conn.execute(
                "SELECT 1 FROM wt_active_subprov_sessions "
                "WHERE subprov_wallet=? AND (state='ACTIVE' OR funding_signature=?) LIMIT 1",
                (r["subprov"], r["funding_sig"])
            ).fetchone()
            if already:
                conn.execute(
                    "UPDATE wt_pending_session_writes SET state='SUPERSEDED' WHERE id=?", (r["id"],))
                conn.commit()
                superseded += 1
                continue
            start_session(conn,
                subprov=r["subprov"], treasury=r["treasury"],
                funding_sig=r["funding_sig"], funding_amount=r["funding_amount"],
                funding_time=r["funding_time"], ttl_seconds=r["ttl_seconds"],
                subprov_known=r["subprov_known"], open_reason=r["open_reason"])
            conn.execute(
                "UPDATE wt_pending_session_writes SET state='WRITTEN', last_retry_at=? WHERE id=?",
                (now, r["id"]))
            conn.commit()
            written += 1
        except Exception:
            conn.execute(
                "UPDATE wt_pending_session_writes "
                "SET retry_count=retry_count+1, last_retry_at=? WHERE id=?",
                (now, r["id"]))
            conn.commit()
    remaining = conn.execute(
        "SELECT COUNT(*) FROM wt_pending_session_writes WHERE state='PENDING'"
    ).fetchone()[0]
    return written, remaining, superseded


def pending_session_counts(conn) -> dict:
    """For health dashboard."""
    rows = conn.execute(
        "SELECT state, priority, COUNT(*) n FROM wt_pending_session_writes GROUP BY state, priority"
    ).fetchall()
    pending, critical = 0, 0
    for r in rows:
        if r["state"] == "PENDING":
            pending += r["n"]
            if r["priority"] == "CRITICAL":
                critical += r["n"]
    return {"pending_session_writes": pending, "critical_pending": critical}


# ───────────────────── treasury WS usage metering ───────────────────────────
def treasury_ws_register(conn, treasury: str) -> None:
    """Ensure a usage row exists for a treasury we're WS-subscribing (idempotent).

    Called from resync_subscriptions ON THE ASYNC WS LOOP. It MUST NOT raise a lock error —
    a "database is locked" here propagated up and crashed/reconnected the whole WS connection
    (the BLIND loop). Use a raw lock-tolerant connection and swallow contention (it's just usage
    metering; the row gets created on a later pass)."""
    now = int(time.time())
    try:
        c = _telemetry_conn(OPS_DB_PATH, busy_ms=8000)
        try:
            c.execute(
                "INSERT OR IGNORE INTO wt_treasury_ws_usage (treasury_wallet, subscribed_at) VALUES (?, ?)",
                (treasury, now))
            c.commit()
        finally:
            c.close()
    except Exception:
        pass  # best-effort metering — never crash the WS loop on a transient lock


def treasury_ws_record_notif(conn, treasury: str, sig: Optional[str], opened_session: bool) -> None:
    """Count one WS notification for a treasury. Maintains a rolling 1-hour bucket so the UI
    can show events/hr and flag a treasury that's turning into a swarm hub."""
    now = int(time.time())
    hb = now // 3600
    row = conn.execute(
        "SELECT hour_bucket, notif_count_1h FROM wt_treasury_ws_usage WHERE treasury_wallet=?",
        (treasury,)).fetchone()
    if row is None:
        conn.execute("INSERT OR IGNORE INTO wt_treasury_ws_usage (treasury_wallet, subscribed_at) "
                     "VALUES (?, ?)", (treasury, now))
        cur_bucket, cur_1h = hb, 0
    else:
        cur_bucket, cur_1h = row[0], row[1]
    # reset the 1h counter when we roll into a new hour bucket
    new_1h = (cur_1h + 1) if cur_bucket == hb else 1
    conn.execute(
        """UPDATE wt_treasury_ws_usage
              SET notif_count = notif_count + 1,
                  sessions_opened = sessions_opened + ?,
                  last_notif_at = ?, last_notif_sig = ?,
                  notif_count_1h = ?, hour_bucket = ?
            WHERE treasury_wallet = ?""",
        (1 if opened_session else 0, now, sig, new_1h, hb, treasury))
    conn.commit()


def session_for_subprov(conn, subprov: str):
    return conn.execute(
        "SELECT id, treasury_wallet, funding_time, funding_signature FROM wt_active_subprov_sessions "
        "WHERE subprov_wallet=? AND state='ACTIVE' ORDER BY detected_at DESC LIMIT 1",
        (subprov,)).fetchone()


def close_session(conn, session_id: int, state: str) -> None:
    conn.execute(
        "UPDATE wt_active_subprov_sessions SET state=?, closed_at=? WHERE id=?",
        (state, int(time.time()), session_id))
    conn.commit()


def expire_stale_sessions(conn) -> list:
    """Return + mark EXPIRED any ACTIVE session past its TTL. Returns the expired rows
    (id, subprov_wallet) so the caller can unsubscribe."""
    now = int(time.time())
    rows = conn.execute(
        "SELECT id, subprov_wallet FROM wt_active_subprov_sessions "
        "WHERE state='ACTIVE' AND expires_at IS NOT NULL AND expires_at < ?", (now,)).fetchall()
    for r in rows:
        conn.execute("UPDATE wt_active_subprov_sessions SET state='EXPIRED', closed_at=? WHERE id=?",
                     (now, r[0]))
    conn.commit()
    return rows


# Phase D window: PROVISION_CANDIDATE sessions older than this with no wrap-close → REJECTED
_CANDIDATE_REJECT_WINDOW_S = int(os.environ.get("WS_CANDIDATE_REJECT_WINDOW_S", str(2 * 3600)))


def reject_unproven_sessions(conn) -> list:
    """Phase D: expire PROVISION_CANDIDATE sessions that have been ACTIVE for longer than
    WS_CANDIDATE_REJECT_WINDOW_S (default 2h) without producing any wrap-close evidence.

    A wallet that receives treasury SOL but never wrap-closes within 2h is almost certainly
    not a sub-provisioner (AMM pool, CEX deposit, operational wallet, refund recipient, etc).
    Expiring it stops the daemon resubscribing it on reconnect and clears UI noise.

    Returns list of (id, subprov_wallet) expired so caller can unsubscribe.
    """
    now = int(time.time())
    cutoff = now - _CANDIDATE_REJECT_WINDOW_S
    # Find PROVISION_CANDIDATE sessions opened before the cutoff with no wrap-close evidence
    rows = conn.execute(
        """SELECT s.id, s.subprov_wallet
           FROM wt_active_subprov_sessions s
           WHERE s.state = 'ACTIVE'
             AND s.open_reason = 'PROVISION_CANDIDATE'
             AND s.detected_at < ?
             AND NOT EXISTS (
               SELECT 1 FROM wt_subprov_evidence e WHERE e.subprov = s.subprov_wallet
             )
             AND NOT EXISTS (
               SELECT 1 FROM wt_candidate_websocket_watches w
               WHERE w.subprov_wallet = s.subprov_wallet
                 AND w.state IN ('WATCHING','FIRED_CREATE','BUY_SWARM')
             )""",
        (cutoff,)).fetchall()
    for r in rows:
        conn.execute(
            "UPDATE wt_active_subprov_sessions "
            "SET state='EXPIRED', closed_at=?, open_reason='PROVISION_CANDIDATE_REJECTED' "
            "WHERE id=?",
            (now, r[0]))
    if rows:
        conn.commit()
    return rows


# ─────────────────────────── candidate helpers ──────────────────────────────
def open_candidate_watch(conn, *, candidate: str, subprov: str, treasury: Optional[str],
                         wrap_close_sig: Optional[str], wrap_wallet: Optional[str],
                         temp_wsol: Optional[str], funding_amount: Optional[float],
                         ttl_seconds: int, wrap_close_time: Optional[int] = None) -> bool:
    """Record a wrap-close destination as a WATCHING candidate. Idempotent on
    (candidate, wrap_close_sig). Returns True if newly inserted (caller should subscribe).

    wrap_close_time = the on-chain blockTime of the wrap-close tx = the creator's BIRTH. Used
    for an ACCURATE birth_to_launch (create_time − wrap_close_time), NOT the treasury→subprov
    session funding time (which over-counts the subprov pipeline and mislabels INSTANT as STAGED)."""
    if candidate == subprov:
        return False
    # one active watch per (candidate, subprov) is enough — multiple wrap-close txs to the
    # same candidate wallet are the same creator, don't open duplicate subscriptions.
    already = conn.execute(
        "SELECT 1 FROM wt_candidate_websocket_watches "
        "WHERE candidate_wallet=? AND subprov_wallet=? AND state='WATCHING' LIMIT 1",
        (candidate, subprov)).fetchone()
    if already:
        return False
    now = int(time.time())
    cur = conn.execute(
        """INSERT OR IGNORE INTO wt_candidate_websocket_watches
             (candidate_wallet, subprov_wallet, treasury_wallet, wrap_close_signature,
              wrap_close_time, wrap_wallet, temp_wsol_account, close_destination, funding_amount,
              state, detected_at, expires_at)
           VALUES (?,?,?,?,?,?,?,?,?, 'WATCHING', ?, ?)""",
        (candidate, subprov, treasury, wrap_close_sig, wrap_close_time, wrap_wallet, temp_wsol,
         candidate, funding_amount, now, now + ttl_seconds))
    conn.commit()
    return cur.rowcount > 0


def record_fanout_audit(conn, *, candidate: str, subprov: str, treasury: str,
                        wrap_close_sig: str, wrap_close_time: Optional[int],
                        wrap_wallet: Optional[str], temp_wsol: Optional[str],
                        funding_amount: Optional[float]) -> bool:
    """Persist a wrap-close fan-out destination without opening a live watch.
    state='AUDIT_ONLY', watch_mode='AUDIT' — evidence only, no WS subscription."""
    if candidate == subprov:
        return False
    already = conn.execute(
        "SELECT 1 FROM wt_candidate_websocket_watches "
        "WHERE candidate_wallet=? AND subprov_wallet=? LIMIT 1",
        (candidate, subprov)).fetchone()
    if already:
        return False
    now = int(time.time())
    cur = conn.execute(
        """INSERT OR IGNORE INTO wt_candidate_websocket_watches
             (candidate_wallet, subprov_wallet, treasury_wallet, wrap_close_signature,
              wrap_close_time, wrap_wallet, temp_wsol_account, close_destination, funding_amount,
              state, watch_mode, detected_at, expires_at)
           VALUES (?,?,?,?,?,?,?,?,?, 'AUDIT_ONLY', 'AUDIT', ?, 0)""",
        (candidate, subprov, treasury, wrap_close_sig, wrap_close_time, wrap_wallet, temp_wsol,
         candidate, funding_amount, now))
    conn.commit()
    return cur.rowcount > 0


def set_candidate_subscription(conn, candidate: str, sub_id) -> None:
    conn.execute(
        "UPDATE wt_candidate_websocket_watches SET websocket_subscription_id=? "
        "WHERE candidate_wallet=? AND state='WATCHING'", (str(sub_id), candidate))
    conn.commit()


def watching_candidates(conn) -> list:
    return conn.execute(
        "SELECT candidate_wallet, subprov_wallet, treasury_wallet, wrap_close_signature, "
        "websocket_subscription_id, expires_at FROM wt_candidate_websocket_watches "
        "WHERE state='WATCHING'").fetchall()


def siblings_of(conn, subprov: str, exclude: str) -> list:
    return conn.execute(
        "SELECT candidate_wallet FROM wt_candidate_websocket_watches "
        "WHERE subprov_wallet=? AND state='WATCHING' AND candidate_wallet!=?",
        (subprov, exclude)).fetchall()


def close_candidate(conn, candidate: str, state: str, reason: str = "") -> None:
    conn.execute(
        "UPDATE wt_candidate_websocket_watches SET state=?, close_reason=?, closed_at=? "
        "WHERE candidate_wallet=? AND state='WATCHING'",
        (state, reason, int(time.time()), candidate))
    conn.commit()


def expire_all_candidates_for_subprov(conn, subprov: str, reason: str = "BUY_SWARM_GATE") -> list:
    """Expire every WATCHING candidate for a subprov. Returns the list of candidate wallets
    so the caller can unsubscribe them from the WS manager."""
    rows = conn.execute(
        "SELECT candidate_wallet FROM wt_candidate_websocket_watches "
        "WHERE subprov_wallet=? AND state='WATCHING'", (subprov,)).fetchall()
    now = int(time.time())
    conn.execute(
        "UPDATE wt_candidate_websocket_watches SET state='EXPIRED', close_reason=?, closed_at=? "
        "WHERE subprov_wallet=? AND state='WATCHING'",
        (reason, now, subprov))
    conn.commit()
    return [r[0] for r in rows]


# ── Behaviour-first temp candidate store (Pass F) ────────────────────────────

def record_temp_candidate(conn, *, wallet: str, treasury: Optional[str],
                          funding_sig: Optional[str], funding_amount: Optional[float],
                          funding_time: Optional[int], ttl_seconds: int) -> bool:
    """Park a NEW_SUBPROV wallet as TEMP_PROVISION_CANDIDATE (no WS subscription).
    Idempotent on wallet — if already PENDING, extends expires_at. Returns True if
    this is the first time we've seen this wallet (new row inserted)."""
    now = int(time.time())
    expires = now + ttl_seconds
    cur = conn.execute(
        """INSERT INTO wt_temp_provision_candidates
             (wallet, treasury, funding_sig, funding_amount, funding_time, detected_at, expires_at, state)
           VALUES (?,?,?,?,?,?,?,'PENDING')
           ON CONFLICT(wallet) DO UPDATE SET
             expires_at = MAX(expires_at, excluded.expires_at),
             treasury   = COALESCE(treasury, excluded.treasury),
             funding_sig = COALESCE(funding_sig, excluded.funding_sig),
             funding_amount = COALESCE(funding_amount, excluded.funding_amount)
           WHERE state = 'PENDING'""",
        (wallet, treasury, funding_sig, funding_amount, funding_time or now, now, expires))
    conn.commit()
    return cur.rowcount > 0


def get_temp_candidates_due(conn, limit: int = 20) -> list:
    """Return PENDING temp candidates whose expires_at has not passed yet, oldest first.
    These are the wallets the offline reconciler should scan for wrap-close evidence."""
    now = int(time.time())
    return conn.execute(
        "SELECT wallet, treasury, funding_sig, funding_amount, funding_time, detected_at, expires_at "
        "FROM wt_temp_provision_candidates "
        "WHERE state='PENDING' AND expires_at > ? "
        "ORDER BY detected_at ASC LIMIT ?",
        (now, limit)).fetchall()


def mark_temp_candidate_scanned(conn, wallet: str, result: str) -> None:
    """Record that the offline sweep scanned this wallet (result = wrap_close_found | no_evidence)."""
    now = int(time.time())
    conn.execute(
        "UPDATE wt_temp_provision_candidates SET state='SCANNED', scanned_at=?, scan_result=? "
        "WHERE wallet=? AND state='PENDING'",
        (now, result, wallet))
    conn.commit()


def promote_temp_candidate(conn, wallet: str) -> None:
    """Mark temp candidate as PROMOTED (wrap-close confirmed — caller opens the real session)."""
    now = int(time.time())
    conn.execute(
        "UPDATE wt_temp_provision_candidates SET state='PROMOTED', promoted_at=? WHERE wallet=?",
        (now, wallet))
    conn.commit()


def expire_temp_candidates(conn) -> int:
    """Expire PENDING temp candidates whose TTL has passed. Returns count expired."""
    now = int(time.time())
    cur = conn.execute(
        "UPDATE wt_temp_provision_candidates SET state='EXPIRED' "
        "WHERE state='PENDING' AND expires_at <= ?", (now,))
    conn.commit()
    return cur.rowcount


def record_swarm_buy(conn, *, swarm_wallet: str, mint: str, swap_sig: Optional[str],
                     observed_at: Optional[int]) -> None:
    """Link a BUY_SWARM wallet to the mint it bought (reverse-direction swarm attribution).
    Resolves the wallet's subprov/treasury from its candidate watch so the token tree can group
    a later swarm wave under the launch's lineage. Idempotent on (swarm_wallet, mint)."""
    sub = treas = None
    try:
        row = conn.execute(
            "SELECT subprov_wallet, treasury_wallet FROM wt_candidate_websocket_watches "
            "WHERE candidate_wallet=? ORDER BY detected_at DESC LIMIT 1", (swarm_wallet,)).fetchone()
        if row:
            sub, treas = row[0], row[1]
    except Exception:
        pass
    conn.execute(
        "INSERT OR IGNORE INTO wt_swarm_buys "
        "(swarm_wallet, mint, subprov_wallet, treasury_wallet, swap_signature, observed_at) "
        "VALUES (?,?,?,?,?,?)",
        (swarm_wallet, mint, sub, treas, swap_sig, observed_at or int(time.time())))
    conn.commit()


def expire_stale_candidates(conn) -> list:
    """Mark EXPIRED any WATCHING candidate past TTL. Returns (candidate_wallet,) rows."""
    now = int(time.time())
    rows = conn.execute(
        "SELECT candidate_wallet FROM wt_candidate_websocket_watches "
        "WHERE state='WATCHING' AND expires_at IS NOT NULL AND expires_at < ?", (now,)).fetchall()
    for r in rows:
        conn.execute(
            "UPDATE wt_candidate_websocket_watches SET state='EXPIRED', close_reason='ttl', "
            "closed_at=? WHERE candidate_wallet=? AND state='WATCHING'", (now, r[0]))
    conn.commit()
    return rows


def subprov_has_live_candidates(conn, subprov: str) -> bool:
    n = conn.execute(
        "SELECT COUNT(*) FROM wt_candidate_websocket_watches "
        "WHERE subprov_wallet=? AND state='WATCHING'", (subprov,)).fetchone()[0]
    return n > 0


def candidate_count_for_subprov(conn, subprov: str) -> int:
    # Count only LIVE (WATCHING) candidates — the cap exists to bound concurrent WS subscriptions,
    # NOT lifetime fan-out. Counting EXPIRED/closed rows let a long-active subprov permanently hit
    # MAX_CANDIDATES and silently DROP every new wrap-close (the 595Xin→HXNyboe miss: 25 expired
    # candidates pinned the count at the cap, so HXNyboe's live wrap-close was discarded uncaught).
    return conn.execute(
        "SELECT COUNT(*) FROM wt_candidate_websocket_watches "
        "WHERE subprov_wallet=? AND state='WATCHING'",
        (subprov,)).fetchone()[0]


# ──────────────────────────── launch ledger ─────────────────────────────────
def record_launch(conn, *, mint: Optional[str], creator: str, create_sig: Optional[str],
                  create_time: Optional[int], treasury: Optional[str], subprov: Optional[str],
                  wrap_close_sig: Optional[str], birth_to_launch_s: Optional[int],
                  create_slot: Optional[int] = None, confidence: str = "STRICT",
                  subprov_funding_sol: Optional[float] = None,
                  wrap_close_sol: Optional[float] = None) -> bool:
    """Authoritative launch record. Idempotent on (creator, create_sig). Marks the
    candidate FIRED_CREATE. Returns True if newly recorded.

    subprov_funding_sol = treasury→subprov load (the big provisioning capital).
    wrap_close_sol       = subprov→creator wrap-close seed (the creator's birth amount).
    Together: the full provisioning-cost chain that produced this launch."""
    cur = conn.execute(
        """INSERT OR IGNORE INTO wt_watchtower_launches
             (mint, creator_wallet, create_signature, create_time, create_slot, treasury_wallet,
              subprov_wallet, subprov_funding_sol, wrap_close_sol, wrap_close_signature,
              birth_to_launch_seconds, funding_mechanism, creator_extraction_method, confidence, state)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'FIRED_CREATE')""",
        (mint, creator, create_sig, create_time, create_slot, treasury, subprov,
         subprov_funding_sol, wrap_close_sol, wrap_close_sig,
         birth_to_launch_s, FUNDING_MECHANISM, EXTRACTION_METHOD, confidence))
    conn.execute(
        "UPDATE wt_candidate_websocket_watches SET state='FIRED_CREATE', close_reason='create', "
        "closed_at=? WHERE candidate_wallet=?", (int(time.time()), creator))
    conn.commit()
    # ENROLL the launched mint into the live price monitor (tracked_tokens, LIVE db). The price
    # worker only snapshots MC for tokens in token_analysis OR tracked_tokens — a cascade-caught
    # launch is in NEITHER until now, so it had no MC anywhere (the 2PZAgP gap: caught live but no
    # peak/current MC on any page). Enrolling it makes the price worker start tracking it → MC
    # flows into token_market_cap_peaks → the migrated-tokens + token-performance pages fill in.
    # Best-effort/retry on lock; never block the cascade.
    # ENROLL the launched mint into the live price monitor — OFF-THREAD. This is a LIVE-db write
    # and the live db is frequently lock-contended (curve_listener); doing it synchronously here
    # blocked the detection hot path for up to 45s (busy_timeout 15s × 3 retries) BEFORE the alert
    # could emit — the 23–94s alert_latency_ms seen in the launch audit. The enroll is not
    # time-critical (the price worker picks the token up on its next cycle), so fire-and-forget.
    if mint and cur.rowcount > 0:
        threading.Thread(target=_enroll_tracked_token, args=(mint,),
                         daemon=True, name="wt-enroll").start()
    return cur.rowcount > 0


def _enroll_tracked_token(mint: str) -> None:
    """Best-effort live-db enroll of a launched mint into tracked_tokens so the price worker
    snapshots it. Runs in a daemon thread (off the cascade detection path). Resolves the
    pool/pair so it's actually priceable (NULL pair → 0 snapshots → 60min deactivation)."""
    for _attempt in range(3):
        try:
            lc = db_connect(LIVE_DB_PATH, timeout=20)
            try:
                lc.execute("PRAGMA busy_timeout=15000")
                _pair = None
                try:
                    _pr = lc.execute(
                        "SELECT pool_address FROM token_pool_accounts "
                        "WHERE mint=? AND pool_address IS NOT NULL ORDER BY is_active DESC LIMIT 1",
                        (mint,)).fetchone()
                    _pair = _pr[0] if _pr else None
                except Exception:
                    pass
                now_ = int(time.time())
                lc.execute(
                    "INSERT INTO tracked_tokens (mint, pair_address, priority_level, is_active, "
                    "created_at, updated_at) VALUES (?, ?, 'high', 1, ?, ?) "
                    "ON CONFLICT(mint) DO UPDATE SET "
                    "  pair_address=COALESCE(excluded.pair_address, tracked_tokens.pair_address), "
                    "  is_active=1, inactive_since=NULL, updated_at=excluded.updated_at",
                    (mint, _pair, now_, now_))
                lc.commit()
            finally:
                lc.close()
            return
        except Exception as e:
            if "locked" in str(e).lower() and _attempt < 2:
                time.sleep(1.0); continue
            print(f"[WS_CASCADE] tracked_tokens enroll failed for {mint[:10]}…: {e}", flush=True)
            return


def batch_upsert_candidates(conn, rows: list) -> None:
    """Batch INSERT OR IGNORE for candidate rows from the ProgramCreateWatcher persist queue.
    One commit for the whole batch. rows are dicts with action='insert' or action='expire'."""
    inserts = [r for r in rows if r.get("action") != "expire"]
    expires = [r["candidate"] for r in rows if r.get("action") == "expire" and r.get("candidate")]
    if inserts:
        conn.executemany(
            "INSERT OR IGNORE INTO wt_candidate_websocket_watches "
            "(candidate_wallet, subprov_wallet, treasury_wallet, wrap_close_signature, "
            "wrap_close_time, funding_amount, state, detected_at, expires_at) "
            "VALUES (:candidate,:subprov,:treasury,:wrap_sig,:wrap_time,:amount,'WATCHING',:now,:expires_at)",
            inserts)
    if expires:
        ph = ",".join("?" * len(expires))
        conn.execute(
            f"UPDATE wt_candidate_websocket_watches SET state='EXPIRED', closed_at=strftime('%s','now') "
            f"WHERE candidate_wallet IN ({ph}) AND state='WATCHING'", expires)
    conn.commit()


def latest_launch(conn):
    return conn.execute(
        "SELECT mint, creator_wallet, create_signature, create_time, treasury_wallet, "
        "subprov_wallet, birth_to_launch_seconds, confidence, recorded_at "
        "FROM wt_watchtower_launches ORDER BY id DESC LIMIT 1").fetchone()
