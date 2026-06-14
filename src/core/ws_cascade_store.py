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
    conn.commit()


# ─────────────────────────────── events ─────────────────────────────────────
def emit_event(event_type: str, wallet: Optional[str] = None,
               related: Optional[str] = None, token_mint: Optional[str] = None,
               payload: Optional[dict] = None) -> None:
    """Write a cascade event into watchtower_events (LIVE db) — fire-and-forget, retries
    on lock. Same sink the forward-walk uses, so events show in the existing feed."""
    for _attempt in range(3):
        try:
            c = db_connect(LIVE_DB_PATH, timeout=30)
            try:
                c.execute("PRAGMA busy_timeout=30000")
                c.execute(
                    "INSERT INTO watchtower_events (event_type, wallet_address, related_wallet, "
                    "token_mint, payload_json, source, created_at) VALUES (?,?,?,?,?,?,?)",
                    (event_type, wallet, related, token_mint, json.dumps(payload or {}),
                     "ws_cascade", int(time.time())))
                c.commit()
                return
            finally:
                c.close()
        except Exception as e:
            if "locked" in str(e).lower() and _attempt < 2:
                time.sleep(1.0)
                continue
            print(f"[WS_CASCADE] event emit failed {event_type}: {e}", flush=True)
            return


# ──────────────────────────── session helpers ───────────────────────────────
def start_session(conn, *, subprov: str, treasury: Optional[str], funding_sig: Optional[str],
                  funding_amount: Optional[float], funding_time: Optional[int],
                  ttl_seconds: int) -> bool:
    """Record a confirmed treasury→SUB_PROV funding as an ACTIVE session. Idempotent on
    (subprov, funding_sig). Returns True if a NEW session row was created."""
    now = int(time.time())
    cur = conn.execute(
        """INSERT OR IGNORE INTO wt_active_subprov_sessions
             (subprov_wallet, treasury_wallet, funding_signature, funding_amount,
              funding_time, state, detected_at, expires_at)
           VALUES (?,?,?,?,?, 'ACTIVE', ?, ?)""",
        (subprov, treasury, funding_sig, funding_amount, funding_time or now,
         now, now + ttl_seconds))
    conn.commit()
    return cur.rowcount > 0


def active_sessions(conn) -> list:
    return conn.execute(
        "SELECT id, subprov_wallet, treasury_wallet, funding_signature, funding_amount, "
        "funding_time, expires_at FROM wt_active_subprov_sessions WHERE state='ACTIVE'").fetchall()


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
                         ttl_seconds: int) -> bool:
    """Record a wrap-close destination as a WATCHING candidate. Idempotent on
    (candidate, wrap_close_sig). Returns True if newly inserted (caller should subscribe)."""
    now = int(time.time())
    cur = conn.execute(
        """INSERT OR IGNORE INTO wt_candidate_websocket_watches
             (candidate_wallet, subprov_wallet, treasury_wallet, wrap_close_signature,
              wrap_wallet, temp_wsol_account, close_destination, funding_amount,
              state, detected_at, expires_at)
           VALUES (?,?,?,?,?,?,?,?, 'WATCHING', ?, ?)""",
        (candidate, subprov, treasury, wrap_close_sig, wrap_wallet, temp_wsol,
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
    return conn.execute(
        "SELECT COUNT(*) FROM wt_candidate_websocket_watches WHERE subprov_wallet=?",
        (subprov,)).fetchone()[0]


# ──────────────────────────── launch ledger ─────────────────────────────────
def record_launch(conn, *, mint: Optional[str], creator: str, create_sig: Optional[str],
                  create_time: Optional[int], treasury: Optional[str], subprov: Optional[str],
                  wrap_close_sig: Optional[str], birth_to_launch_s: Optional[int],
                  create_slot: Optional[int] = None, confidence: str = "STRICT") -> bool:
    """Authoritative launch record. Idempotent on (creator, create_sig). Marks the
    candidate FIRED_CREATE. Returns True if newly recorded."""
    cur = conn.execute(
        """INSERT OR IGNORE INTO wt_watchtower_launches
             (mint, creator_wallet, create_signature, create_time, create_slot, treasury_wallet,
              subprov_wallet, wrap_close_signature, birth_to_launch_seconds,
              funding_mechanism, creator_extraction_method, confidence, state)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'FIRED_CREATE')""",
        (mint, creator, create_sig, create_time, create_slot, treasury, subprov, wrap_close_sig,
         birth_to_launch_s, FUNDING_MECHANISM, EXTRACTION_METHOD, confidence))
    conn.execute(
        "UPDATE wt_candidate_websocket_watches SET state='FIRED_CREATE', close_reason='create', "
        "closed_at=? WHERE candidate_wallet=?", (int(time.time()), creator))
    conn.commit()
    return cur.rowcount > 0


def latest_launch(conn):
    return conn.execute(
        "SELECT mint, creator_wallet, create_signature, create_time, treasury_wallet, "
        "subprov_wallet, birth_to_launch_seconds, confidence, recorded_at "
        "FROM wt_watchtower_launches ORDER BY id DESC LIMIT 1").fetchone()
