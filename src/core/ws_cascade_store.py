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


def _event_writer_loop():
    while True:
        ev = _event_q.get()
        if ev is None:
            continue
        et, wallet, related, mint, payload, ts = ev
        for _attempt in range(4):
            try:
                c = db_connect(LIVE_DB_PATH, timeout=30)
                try:
                    c.execute("PRAGMA busy_timeout=15000")
                    c.execute(
                        "INSERT INTO watchtower_events (event_type, wallet_address, related_wallet, "
                        "token_mint, payload_json, source, created_at) VALUES (?,?,?,?,?,?,?)",
                        (et, wallet, related, mint, json.dumps(payload or {}), "ws_cascade", ts))
                    c.commit()
                    break
                finally:
                    c.close()
            except Exception as e:
                if "locked" in str(e).lower() and _attempt < 3:
                    time.sleep(1.0 * (_attempt + 1))
                    continue
                print(f"[WS_CASCADE] event write failed {et}: {e}", flush=True)
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
        _event_q.put_nowait((event_type, wallet, related, token_mint, payload, int(time.time())))
    except _queue_mod.Full:
        pass   # event log is best-effort; never block the cascade for a breadcrumb


def record_treasury_hit(*, treasury: str, counterparty: str, sig: str,
                        amount_sol: float, block_time: Optional[int]) -> None:
    """Write a treasury outbound into wt_webhook_hits (LIVE db) tagged source='treasury_ws'
    so the Webhook Event Feed becomes real-time. Deduped against the webhook path by the
    UNIQUE(tx_signature, wallet_address) index — whichever path (WS ~3s vs webhook 5–390s)
    arrives first wins; the other INSERT OR IGNOREs. Best-effort/off the cascade hot path:
    a locked live DB degrades to 'the webhook backfills it', never blocks detection."""
    wh_id = os.environ.get("WATCHTOWER_INFRA_WEBHOOK_ID", "106e20f6-f542-42b0-83d5-ca8c7b1a7162")
    for _attempt in range(3):
        try:
            c = db_connect(LIVE_DB_PATH, timeout=30)
            try:
                c.execute("PRAGMA busy_timeout=30000")
                c.execute(
                    """INSERT OR IGNORE INTO wt_webhook_hits
                         (webhook_id, wallet_address, tx_signature, tx_type, source,
                          counterparty, block_time, amount_sol, is_fee_touch, created_at, direction)
                       VALUES (?, ?, ?, 'TRANSFER', 'treasury_ws', ?, ?, ?, 0, ?, 'outbound')""",
                    (wh_id, treasury, sig, counterparty, block_time, amount_sol, int(time.time())))
                c.commit()
                return
            finally:
                c.close()
        except Exception as e:
            if "locked" in str(e).lower() and _attempt < 2:
                time.sleep(1.0)
                continue
            print(f"[WS_CASCADE] treasury hit write failed {sig[:12]}…: {e}", flush=True)
            return


# ──────────────────────────── session helpers ───────────────────────────────
def start_session(conn, *, subprov: str, treasury: Optional[str], funding_sig: Optional[str],
                  funding_amount: Optional[float], funding_time: Optional[int],
                  ttl_seconds: int, subprov_known: int = 0) -> bool:
    """Record a confirmed treasury→SUB_PROV funding as an ACTIVE session. Idempotent on
    (subprov, funding_sig). Returns True if a NEW session row was created.

    The active SUB_PROV is DISCOVERED from this funding — it need NOT already be in
    wt_discovered_subprovs. `subprov_known` records whether it happened to be known (a
    confidence signal), but membership is never a gate for session creation."""
    now = int(time.time())
    cur = conn.execute(
        """INSERT OR IGNORE INTO wt_active_subprov_sessions
             (subprov_wallet, treasury_wallet, funding_signature, funding_amount,
              funding_time, subprov_known, state, detected_at, expires_at)
           VALUES (?,?,?,?,?,?, 'ACTIVE', ?, ?)""",
        (subprov, treasury, funding_sig, funding_amount, funding_time or now,
         int(subprov_known), now, now + ttl_seconds))
    conn.commit()
    if cur.rowcount == 0:
        # repeat funding of an already-active subprov → EXTEND its TTL so the subscription
        # survives a multi-hour provisioning campaign instead of expiring mid-stream.
        conn.execute(
            "UPDATE wt_active_subprov_sessions SET expires_at=? "
            "WHERE subprov_wallet=? AND state='ACTIVE' AND expires_at < ?",
            (now + ttl_seconds, subprov, now + ttl_seconds))
        conn.commit()
    return cur.rowcount > 0


def active_sessions(conn) -> list:
    return conn.execute(
        "SELECT id, subprov_wallet, treasury_wallet, funding_signature, funding_amount, "
        "funding_time, expires_at FROM wt_active_subprov_sessions WHERE state='ACTIVE'").fetchall()


# ───────────────────── treasury WS usage metering ───────────────────────────
def treasury_ws_register(conn, treasury: str) -> None:
    """Ensure a usage row exists for a treasury we're WS-subscribing (idempotent)."""
    now = int(time.time())
    conn.execute(
        "INSERT OR IGNORE INTO wt_treasury_ws_usage (treasury_wallet, subscribed_at) VALUES (?, ?)",
        (treasury, now))
    conn.commit()


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


def latest_launch(conn):
    return conn.execute(
        "SELECT mint, creator_wallet, create_signature, create_time, treasury_wallet, "
        "subprov_wallet, birth_to_launch_seconds, confidence, recorded_at "
        "FROM wt_watchtower_launches ORDER BY id DESC LIMIT 1").fetchone()
