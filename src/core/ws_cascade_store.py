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
import threading
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


def operations_write(command: str, transaction):
    """The only wt_ops_v2 mutation boundary used by cascade infrastructure."""
    from src.core.database_write_service import database_write_service

    selector = f"operations:{os.path.realpath(OPS_DB_PATH)}"
    database_write_service.register_database(selector, OPS_DB_PATH)
    return database_write_service.submit(selector, command, transaction)

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
            detection_source           TEXT,
            detection_delay_seconds    INTEGER,
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
    # X24.1 — mirror of wt_treasury_ws_usage for PLAIN_TRANSFER-funded sub-provisioners,
    # which are observed via accountSubscribe (balance change) exactly like treasuries,
    # NOT logsSubscribe (a plain system::transfer emits no program logs the `mentions`
    # filter can match). Kept as its own table rather than reusing wt_treasury_ws_usage
    # so subprov-tier usage never conflates with treasury-tier usage under one PK space.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS wt_subprov_account_ws_usage (
            subprov_wallet            TEXT PRIMARY KEY,
            subscribed_at             INTEGER,
            notif_count               INTEGER DEFAULT 0,
            last_notif_at             INTEGER,
            last_notif_sig            TEXT,
            notif_count_1h            INTEGER DEFAULT 0,
            hour_bucket               INTEGER DEFAULT 0
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
    # X20.8 perf audit: /api/discovery/entity/<id> looks up funding_signature on every call
    # (src/discovery/service.py:_identify); with 90k+ rows and no index this was a full table
    # scan costing ~3.1s per request (measured). Read-only lookup, safe additive index.
    conn.execute("CREATE INDEX IF NOT EXISTS ix_subprov_sessions_funding_signature ON wt_active_subprov_sessions(funding_signature)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_cand_watch_state ON wt_candidate_websocket_watches(state)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_cand_watch_subprov ON wt_candidate_websocket_watches(subprov_wallet)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_launches_creator ON wt_watchtower_launches(creator_wallet)")
    # X67.31 -- treasury_wallet is filtered/joined on in confirmed-treasuries and
    # launch-audit's sibling-wallet lookup (WHERE treasury_wallet IN (...), JOIN ... ON
    # treasury_wallet=...); EXPLAIN QUERY PLAN showed a full-table SCAN here before this
    # index. Not the cause of the 30-100s X67.30 incident (that was the correlated
    # subquery pattern, fixed separately) -- independent hygiene, low write overhead at
    # this table's size (~160 rows).
    conn.execute("CREATE INDEX IF NOT EXISTS ix_launches_treasury ON wt_watchtower_launches(treasury_wallet)")
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
    # migrate: add wrap_close_time + funding_mechanism to a pre-existing watches table
    try:
        _wcols = {r[1] for r in conn.execute("PRAGMA table_info(wt_candidate_websocket_watches)").fetchall()}
        if "wrap_close_time" not in _wcols:
            conn.execute("ALTER TABLE wt_candidate_websocket_watches ADD COLUMN wrap_close_time INTEGER")
        if "funding_mechanism" not in _wcols:
            conn.execute("ALTER TABLE wt_candidate_websocket_watches ADD COLUMN funding_mechanism TEXT DEFAULT 'WSOL_WRAP_CLOSE'")
    except Exception:
        pass
    # migrate: add the two funding amounts to a pre-existing launches table
    try:
        _lcols = {r[1] for r in conn.execute("PRAGMA table_info(wt_watchtower_launches)").fetchall()}
        if "subprov_funding_sol" not in _lcols:
            conn.execute("ALTER TABLE wt_watchtower_launches ADD COLUMN subprov_funding_sol REAL")
        if "wrap_close_sol" not in _lcols:
            conn.execute("ALTER TABLE wt_watchtower_launches ADD COLUMN wrap_close_sol REAL")
        if "detection_source" not in _lcols:
            conn.execute("ALTER TABLE wt_watchtower_launches ADD COLUMN detection_source TEXT")
        if "detection_delay_seconds" not in _lcols:
            conn.execute("ALTER TABLE wt_watchtower_launches ADD COLUMN detection_delay_seconds INTEGER")
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
        if "funding_mechanism" not in _scols2:
            conn.execute(
                "ALTER TABLE wt_active_subprov_sessions ADD COLUMN "
                "funding_mechanism TEXT DEFAULT 'WSOL_WRAP_CLOSE'"
            )
        if "session_tag" not in _scols2:
            conn.execute(
                "ALTER TABLE wt_active_subprov_sessions ADD COLUMN "
                "session_tag TEXT DEFAULT NULL"
            )
    except Exception:
        pass
    # wt_capital_reloads: add enrolment_reason + block_time for plain-transfer enrolments
    try:
        _crcols = {r[1] for r in conn.execute("PRAGMA table_info(wt_capital_reloads)").fetchall()}
        if "enrolment_reason" not in _crcols:
            conn.execute("ALTER TABLE wt_capital_reloads ADD COLUMN enrolment_reason TEXT")
        if "block_time" not in _crcols:
            conn.execute("ALTER TABLE wt_capital_reloads ADD COLUMN block_time INTEGER")
        if "session_opened" not in _crcols:
            conn.execute("ALTER TABLE wt_capital_reloads ADD COLUMN session_opened INTEGER DEFAULT 0")
        if "linked_mint_basis" not in _crcols:
            conn.execute("ALTER TABLE wt_capital_reloads ADD COLUMN linked_mint_basis TEXT")
        if "operation_uuid" not in _crcols:
            conn.execute("ALTER TABLE wt_capital_reloads ADD COLUMN operation_uuid TEXT")
    except Exception:
        pass
    try:
        _stcols = {r[1] for r in conn.execute("PRAGMA table_info(wt_subprov_topups)").fetchall()}
        if "operation_uuid" not in _stcols:
            conn.execute("ALTER TABLE wt_subprov_topups ADD COLUMN operation_uuid TEXT")
    except Exception:
        pass
    # ── Operation Lifecycle v2: operation_state ──────────────────────────────
    # Additive interpretation column on PROVISION_CANDIDATE sessions.
    # Design rule: this is a trailing annotation, never a detection precondition.
    # Values: FUNDED | ARMED | POST_CREATE | ABORTED | COMPLETE | RECYCLED
    # NULL = pre-v2 row (treat as FUNDED for display).
    import logging as _opstate_log
    _opstate_logger = _opstate_log.getLogger(__name__)
    try:
        _oscols = {r[1] for r in conn.execute("PRAGMA table_info(wt_active_subprov_sessions)").fetchall()}
        if "operation_state" not in _oscols:
            conn.execute("ALTER TABLE wt_active_subprov_sessions ADD COLUMN operation_state TEXT")
            _opstate_logger.info("[op_state] Added operation_state column to wt_active_subprov_sessions")
    except Exception as _e:
        _opstate_logger.error("[op_state] Failed to add operation_state column: %s", _e)
    # Idempotent backfill: derive minimum guaranteed state for pre-v2 PROVISION_CANDIDATE rows.
    # Source of truth for CREATE: wt_watchtower_launches (immutable detection record).
    # Backfills to POST_CREATE (not CREATE) — we know CREATE happened, not exactly when.
    # Re-runnable: only touches rows where operation_state IS NULL.
    try:
        _bf_cur = conn.execute("""
            UPDATE wt_active_subprov_sessions
            SET operation_state = (
                SELECT CASE
                    WHEN l.subprov_wallet IS NOT NULL           THEN 'POST_CREATE'
                    WHEN wt_active_subprov_sessions.state = 'COMPLETED'   THEN 'POST_CREATE'
                    WHEN wt_active_subprov_sessions.monitoring_state = 'LIVE_ARMED'
                         AND wt_active_subprov_sessions.state = 'ACTIVE'  THEN 'ARMED'
                    WHEN wt_active_subprov_sessions.state IN ('BUY_SWARM_REJECTED', 'EXPIRED')
                                                                THEN 'ABORTED'
                    ELSE                                             'FUNDED'
                END
                FROM wt_active_subprov_sessions s2
                LEFT JOIN wt_watchtower_launches l
                    ON l.subprov_wallet = s2.subprov_wallet
                   AND l.treasury_wallet = s2.treasury_wallet
                WHERE s2.id = wt_active_subprov_sessions.id
            )
            WHERE open_reason = 'PROVISION_CANDIDATE'
              AND operation_state IS NULL
        """)
        conn.commit()
        _opstate_logger.info("[op_state] Backfill complete — %d rows updated", _bf_cur.rowcount)
    except Exception as _e:
        _opstate_logger.error("[op_state] Backfill failed: %s", _e)
    # Self-audit: verify no NULL rows remain after backfill.
    try:
        _audit = {r[0]: r[1] for r in conn.execute(
            "SELECT COALESCE(operation_state,'NULL') as s, COUNT(*) "
            "FROM wt_active_subprov_sessions "
            "WHERE open_reason='PROVISION_CANDIDATE' GROUP BY s"
        ).fetchall()}
        _null_count = _audit.get('NULL', 0)
        if _null_count > 0:
            _opstate_logger.warning(
                "[op_state] %d PROVISION_CANDIDATE rows still NULL after backfill. "
                "Distribution: %s", _null_count, _audit
            )
        else:
            _opstate_logger.info("[op_state] Self-audit passed. Distribution: %s", _audit)
    except Exception as _e:
        _opstate_logger.error("[op_state] Self-audit query failed: %s", _e)
    # wt_token_lifecycle — derived lifecycle aggregation (read-only view of confirmed launches)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS wt_token_lifecycle (
            mint                    TEXT PRIMARY KEY,
            treasury                TEXT,
            subprov                 TEXT,
            creator                 TEXT,
            create_sig              TEXT,
            lifecycle_state         TEXT NOT NULL DEFAULT 'LAUNCHED',
            funded_at               INTEGER,
            launched_at             INTEGER,
            migrated_at             INTEGER,
            recycled_at             INTEGER,
            campaign_end_reason     TEXT,
            migration_sig           TEXT,
            recycle_sig             TEXT,
            recycle_amount_sol      REAL,
            recycle_direction       TEXT,
            operation_uuid          TEXT,
            updated_at              INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_tl_subprov ON wt_token_lifecycle(subprov)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_tl_treasury ON wt_token_lifecycle(treasury)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_tl_state ON wt_token_lifecycle(lifecycle_state)")
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
        if "seeded_account_count" not in _dcols:
            conn.execute("ALTER TABLE wt_discovered_subprovs ADD COLUMN seeded_account_count INTEGER DEFAULT 0")
        if "topup_count" not in _dcols:
            conn.execute("ALTER TABLE wt_discovered_subprovs ADD COLUMN topup_count INTEGER DEFAULT 0")
        if "rejected_reason" not in _dcols:
            conn.execute("ALTER TABLE wt_discovered_subprovs ADD COLUMN rejected_reason TEXT")
    except Exception:
        pass
    # wt_subprov_evidence — one row per observed provisioning event, links subprov→creator
    conn.execute(
        """CREATE TABLE IF NOT EXISTS wt_subprov_evidence (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            subprov         TEXT NOT NULL,
            wrap_close_sig  TEXT NOT NULL UNIQUE,
            creator_wallet  TEXT NOT NULL,
            amount_sol      REAL,
            observed_at     INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            create_fired    INTEGER DEFAULT 0,
            funding_mechanism TEXT DEFAULT 'WSOL_WRAP_CLOSE'
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_se_subprov ON wt_subprov_evidence(subprov)")
    # Durable subprov signature intake. This is the backstop for a live subprov
    # logsSubscribe notification that drops, times out, or fails under DB/RPC
    # pressure. The retry table is intentionally keyed by (subprov, signature)
    # so every seen funding signature has a durable processing record.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS wt_subprov_sig_cursor (
            subprov_wallet  TEXT PRIMARY KEY,
            last_seen_sig   TEXT,
            last_seen_slot  INTEGER,
            last_seen_at    INTEGER,
            updated_at      INTEGER NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS wt_subprov_sig_retry (
            subprov_wallet  TEXT NOT NULL,
            signature       TEXT NOT NULL,
            slot            INTEGER,
            first_seen_at   INTEGER NOT NULL,
            last_attempt_at INTEGER,
            attempts        INTEGER DEFAULT 0,
            last_error      TEXT,
            status          TEXT NOT NULL DEFAULT 'PENDING',
            PRIMARY KEY (subprov_wallet, signature)
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_subprov_sig_retry_status ON wt_subprov_sig_retry(status, last_attempt_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_subprov_sig_retry_subprov ON wt_subprov_sig_retry(subprov_wallet)")
    # migrate: add funding_mechanism to pre-existing evidence table
    try:
        _evcols = {r[1] for r in conn.execute("PRAGMA table_info(wt_subprov_evidence)").fetchall()}
        if "funding_mechanism" not in _evcols:
            conn.execute("ALTER TABLE wt_subprov_evidence ADD COLUMN funding_mechanism TEXT DEFAULT 'WSOL_WRAP_CLOSE'")
    except Exception:
        pass
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
    # wt_capital_reloads — large treasury injections into known launched subprovs
    conn.execute(
        """CREATE TABLE IF NOT EXISTS wt_capital_reloads (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            subprov         TEXT NOT NULL,
            treasury        TEXT NOT NULL,
            sig             TEXT UNIQUE,
            amount_sol      REAL NOT NULL,
            wrap_close_count INTEGER DEFAULT 0,
            first_creator   TEXT,
            linked_mint     TEXT,
            recorded_at     INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_cr_subprov ON wt_capital_reloads(subprov)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_cr_recorded ON wt_capital_reloads(recorded_at)")
    # ── Capital Distributor Candidates ────────────────────────────────────────
    conn.execute(
        """CREATE TABLE IF NOT EXISTS wt_capital_distributor_candidates (
            wallet                TEXT PRIMARY KEY,
            source_treasury       TEXT NOT NULL,
            funding_sig           TEXT NOT NULL,
            funding_amount_sol    REAL NOT NULL,
            first_seen            INTEGER NOT NULL,
            observation_state     TEXT NOT NULL DEFAULT 'OBSERVING',
            subscription_started  INTEGER,
            subscription_ended    INTEGER,
            last_activity         INTEGER,
            total_outbound_sol    REAL DEFAULT 0,
            recipient_count       INTEGER DEFAULT 0,
            fanout_count          INTEGER DEFAULT 0,
            largest_fanout        INTEGER DEFAULT 0,
            wrap_close_count      INTEGER DEFAULT 0,
            creator_count         INTEGER DEFAULT 0,
            buy_swarm_count       INTEGER DEFAULT 0,
            migration_count       INTEGER DEFAULT 0,
            derived_role          TEXT DEFAULT 'UNKNOWN',
            role_confidence       TEXT DEFAULT 'NONE',
            role_evidence_json    TEXT,
            recorded_at           INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS wt_cdc_outbound_events (
            id                        INTEGER PRIMARY KEY AUTOINCREMENT,
            cdc_wallet                TEXT NOT NULL,
            sig                       TEXT NOT NULL,
            block_time                INTEGER,
            recipient                 TEXT NOT NULL,
            amount_sol                REAL,
            fanout_size               INTEGER DEFAULT 1,
            recipient_did_wrap_close  INTEGER DEFAULT 0,
            recipient_did_create      INTEGER DEFAULT 0,
            recipient_did_swap        INTEGER DEFAULT 0,
            recipient_did_migrate     INTEGER DEFAULT 0,
            recorded_at               INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            UNIQUE(sig, recipient)
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_cdc_wallet    ON wt_capital_distributor_candidates(source_treasury)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_cdc_state     ON wt_capital_distributor_candidates(observation_state)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_cdcoe_wallet  ON wt_cdc_outbound_events(cdc_wallet)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_cdcoe_sig     ON wt_cdc_outbound_events(sig)")
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
    # ── X77.2: durable retry queue for _event_writer_loop's watchtower_events /
    # wt_webhook_hits writes. Mirrors wt_pending_session_writes exactly (same
    # PENDING/WRITTEN/SUPERSEDED/FAILED vocabulary, same drain-loop cadence) --
    # only transient failures (SQLITE_BUSY / DatabaseWriteLockError) land here;
    # constraint/schema/malformed-data failures never retry (see
    # _is_transient_write_failure in this module).
    conn.execute(
        """CREATE TABLE IF NOT EXISTS wt_pending_cascade_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            kind            TEXT NOT NULL,       -- 'event' | 'hit'
            payload_json    TEXT NOT NULL,       -- the original _event_q item, minus kind, json-encoded
            dedupe_key      TEXT,                -- for 'hit': tx_signature||wallet_address; NULL for 'event' (no natural key)
            enqueued_at     INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            retry_count     INTEGER NOT NULL DEFAULT 0,
            last_retry_at   INTEGER,
            last_error      TEXT,
            state           TEXT NOT NULL DEFAULT 'PENDING'
            -- PENDING | WRITTEN | SUPERSEDED | FAILED
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_pce_state ON wt_pending_cascade_events(state, enqueued_at)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_pce_dedupe ON wt_pending_cascade_events(dedupe_key) "
        "WHERE dedupe_key IS NOT NULL"
    )
    # ── X24.2 Phase 2: durable sweep-fairness bookkeeping ────────────────────
    # subprov_sweep_pass() previously sliced active_sessions()[:MAX_ACTIVE_SUBPROVS]
    # with no ordering, so sessions outside the arbitrary top-N could expire without
    # ever being inspected (the proven AWiaGsus-class coverage defect). These three
    # additive columns let the fair scheduler order by "never swept first, then
    # least-recently-swept" and survive a process restart (in-memory-only fairness
    # state would reset to all-unswept on every restart, which is itself fine — but
    # a DURABLE cursor means genuine progress isn't lost on restart either).
    try:
        _swcols = {r[1] for r in conn.execute("PRAGMA table_info(wt_active_subprov_sessions)").fetchall()}
        if "last_swept_at" not in _swcols:
            conn.execute("ALTER TABLE wt_active_subprov_sessions ADD COLUMN last_swept_at INTEGER")
        if "sweep_count" not in _swcols:
            conn.execute("ALTER TABLE wt_active_subprov_sessions ADD COLUMN sweep_count INTEGER NOT NULL DEFAULT 0")
        if "first_swept_at" not in _swcols:
            conn.execute("ALTER TABLE wt_active_subprov_sessions ADD COLUMN first_swept_at INTEGER")
    except Exception:
        pass
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_subprov_sessions_sweep_order "
        "ON wt_active_subprov_sessions(state, last_swept_at)"
    )
    # X28.0 — candidate provenance snapshot, captured at open_candidate_watch() time so a
    # candidate's funding lineage survives independent of any later join back to
    # wt_active_subprov_sessions (that row is never deleted today, but a snapshot removes the
    # dependency entirely — see X28.0 Phase 3). Also carries forward capital-context fields
    # (Phase 4: preserve, do not act on, the parent subprov's own treasury funding + observed
    # fan-out totals) so a large treasury load (100+ SOL) is never silently lost once fan-out
    # begins. NOT used for any closure/eviction decision this sprint — data preservation only.
    try:
        _wcols2 = {r[1] for r in conn.execute("PRAGMA table_info(wt_candidate_websocket_watches)").fetchall()}
        if "initial_subprov_funding_sol" not in _wcols2:
            conn.execute("ALTER TABLE wt_candidate_websocket_watches ADD COLUMN initial_subprov_funding_sol REAL")
        if "initial_subprov_funding_signature" not in _wcols2:
            conn.execute("ALTER TABLE wt_candidate_websocket_watches ADD COLUMN initial_subprov_funding_signature TEXT")
        if "initial_subprov_funding_time" not in _wcols2:
            conn.execute("ALTER TABLE wt_candidate_websocket_watches ADD COLUMN initial_subprov_funding_time INTEGER")
        if "subprov_fanout_count_at_capture" not in _wcols2:
            conn.execute("ALTER TABLE wt_candidate_websocket_watches ADD COLUMN subprov_fanout_count_at_capture INTEGER")
        if "subprov_fanout_value_at_capture" not in _wcols2:
            conn.execute("ALTER TABLE wt_candidate_websocket_watches ADD COLUMN subprov_fanout_value_at_capture REAL")
    except Exception:
        pass
    # X64.9B1 — durable redelivery-dedupe measurement (see docs/design/x64_9/x64_9b1_observability_design.md).
    # Aggregated by (subprov_wallet, age_bucket), NOT one row per duplicate event — bounded growth
    # regardless of actual duplicate volume, which is the unknown this instrumentation measures.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS wt_subprov_sig_dedupe_stats (
            subprov_wallet      TEXT NOT NULL,
            age_bucket          TEXT NOT NULL,
            duplicate_count     INTEGER NOT NULL DEFAULT 0,
            max_duplicate_age_s INTEGER,
            first_observed_at   INTEGER,
            last_observed_at    INTEGER,
            source_ws           INTEGER NOT NULL DEFAULT 0,
            source_catchup      INTEGER NOT NULL DEFAULT 0,
            source_retry        INTEGER NOT NULL DEFAULT 0,
            source_hot_burst    INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (subprov_wallet, age_bucket)
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_subprov_sig_dedupe_stats_bucket "
        "ON wt_subprov_sig_dedupe_stats(age_bucket)"
    )
    # Single-row global rollup — total_checked is the denominator required to make a
    # zero-duplicate result meaningful (see x64_9b1_measurement_contract.md).
    conn.execute(
        """CREATE TABLE IF NOT EXISTS wt_subprov_sig_dedupe_summary (
            id                    INTEGER PRIMARY KEY CHECK (id = 1),
            total_checked         INTEGER NOT NULL DEFAULT 0,
            total_duplicates      INTEGER NOT NULL DEFAULT 0,
            max_duplicate_age_s   INTEGER,
            first_duplicate_at    INTEGER,
            last_duplicate_at     INTEGER,
            updated_at            INTEGER NOT NULL
        )"""
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

# X77.2 — in-process counters for the health dashboard (queued/retried/failed/
# dropped/succeeded). Not durable by design: they reset on restart, same as
# every other in-process counter this module already exposes
# (_subprov_sig_metrics in ws_cascade.py). Durable state (what actually
# survives a crash) lives in wt_pending_cascade_events; these counters are
# purely an operational rate/volume view on top of it.
_event_writer_stats = {
    "succeeded": 0, "queued_for_retry": 0, "retried_ok": 0,
    "failed_permanent": 0, "dropped_queue_full": 0,
}
_event_writer_stats_lock = _threading_mod.Lock()


def _bump_stat(name: str, n: int = 1) -> None:
    with _event_writer_stats_lock:
        _event_writer_stats[name] = _event_writer_stats.get(name, 0) + n


def event_writer_stats() -> dict:
    """Snapshot of this process's in-memory write-outcome counters."""
    with _event_writer_stats_lock:
        return dict(_event_writer_stats)


def _is_transient_write_failure(exc: BaseException) -> bool:
    """True for failures worth retrying (contention only): SQLITE_BUSY /
    'database is locked' (raised here as DatabaseWriteLockError by
    database_write_service, or occasionally a raw sqlite3.OperationalError
    if a caller bypasses the service) and NestedDatabaseWriteError (a
    same-thread reentrancy trip that a later, differently-scheduled retry
    will not hit again). NEVER transient: IntegrityError (constraint
    violation), schema errors, or any failure whose cause is the DATA
    itself, not contention -- retrying those would fail identically forever
    and just hide a real bug behind a growing retry queue."""
    import sqlite3 as _sq3
    try:
        from src.core.database_write_service import (
            DatabaseWriteLockError, NestedDatabaseWriteError,
        )
    except Exception:
        DatabaseWriteLockError = ()  # type: ignore[assignment]
        NestedDatabaseWriteError = ()  # type: ignore[assignment]
    if isinstance(exc, (DatabaseWriteLockError, NestedDatabaseWriteError)):
        return True
    if isinstance(exc, _sq3.OperationalError) and "locked" in str(exc).lower():
        return True
    if isinstance(exc, (_sq3.IntegrityError, _sq3.ProgrammingError)):
        return False
    return False  # unknown failure classes default to non-retryable (fail loud, don't mask)


def _item_dedupe_key(item: tuple) -> Optional[str]:
    """'hit' rows have a natural key (tx_signature, wallet_address) already
    enforced by idx_wh_sig_wallet's UNIQUE INSERT OR IGNORE -- reuse it here
    so a retried 'hit' can never double-enqueue. 'event' rows have no natural
    key (watchtower_events is an append-only log by design), so dedupe_key
    stays NULL and duplicates are prevented only by not re-enqueueing an
    already-PENDING item (see enqueue_pending_cascade_event)."""
    if item[0] == 'hit':
        _, treasury, _counterparty, sig, *_rest = item
        return f"hit:{sig}:{treasury}"
    return None


def enqueue_pending_cascade_event(conn, item: tuple, error: BaseException) -> None:
    """Persist a watchtower_events/wt_webhook_hits write that failed with a
    transient (contention) error, so drain_pending_cascade_events can retry
    it later instead of losing it. Idempotent on dedupe_key for 'hit' rows."""
    kind = item[0]
    payload = json.dumps(list(item))
    dedupe_key = _item_dedupe_key(item)
    try:
        conn.execute(
            """INSERT OR IGNORE INTO wt_pending_cascade_events
                 (kind, payload_json, dedupe_key, last_error)
               VALUES (?,?,?,?)""",
            (kind, payload, dedupe_key, str(error)[:500]))
        conn.commit()
    except Exception:
        pass  # best-effort durability; if even this fails, the write is genuinely lost


def _write_cascade_item(c, item: tuple, wh_id: str) -> None:
    """The single write path shared by the live queue drain and the retry
    drain -- one implementation, so retried writes are byte-identical to
    first-attempt writes."""
    kind = item[0]
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


def drain_pending_cascade_events(conn, *, limit: int = 50) -> dict:
    """Retry PENDING watchtower_events/wt_webhook_hits writes. Mirrors
    drain_pending_sessions exactly. Called every 30s from the same
    maintenance loop, off the event loop (via _ato_thread), so a retry
    round-trip -- even a slow one under contention -- never blocks WS
    processing."""
    wh_id = os.environ.get("WATCHTOWER_INFRA_WEBHOOK_ID", "106e20f6-f542-42b0-83d5-ca8c7b1a7162")
    now = int(time.time())
    import sqlite3 as _sq3
    _prev_rf = conn.row_factory
    conn.row_factory = _sq3.Row
    rows = conn.execute(
        "SELECT id, kind, payload_json FROM wt_pending_cascade_events "
        "WHERE state='PENDING' ORDER BY enqueued_at ASC LIMIT ?", (limit,)
    ).fetchall()
    conn.row_factory = _prev_rf
    written = 0
    failed = 0
    for r in rows:
        item = tuple(json.loads(r["payload_json"]))
        try:
            def write(c, _item=item):
                _write_cascade_item(c, _item, wh_id)
            operations_write(f"ws-cascade-retry-{r['kind']}", write)
            conn.execute(
                "UPDATE wt_pending_cascade_events SET state='WRITTEN', last_retry_at=? WHERE id=?",
                (now, r["id"]))
            conn.commit()
            written += 1
            _bump_stat("retried_ok")
        except Exception as e:
            if _is_transient_write_failure(e):
                conn.execute(
                    "UPDATE wt_pending_cascade_events "
                    "SET retry_count=retry_count+1, last_retry_at=?, last_error=? WHERE id=?",
                    (now, str(e)[:500], r["id"]))
                conn.commit()
                failed += 1
            else:
                # A non-transient failure surfacing on retry means the original
                # classification was wrong (or the data itself is bad) -- stop
                # retrying rather than loop forever on a permanent failure.
                conn.execute(
                    "UPDATE wt_pending_cascade_events SET state='FAILED', last_retry_at=?, last_error=? WHERE id=?",
                    (now, str(e)[:500], r["id"]))
                conn.commit()
                failed += 1
                _bump_stat("failed_permanent")
    remaining = conn.execute(
        "SELECT COUNT(*) FROM wt_pending_cascade_events WHERE state='PENDING'"
    ).fetchone()[0]
    return {"written": written, "failed": failed, "remaining": remaining}


def pending_cascade_event_counts(conn) -> dict:
    """For the health dashboard."""
    rows = conn.execute(
        "SELECT state, COUNT(*) n FROM wt_pending_cascade_events GROUP BY state"
    ).fetchall()
    counts = {"PENDING": 0, "WRITTEN": 0, "SUPERSEDED": 0, "FAILED": 0}
    for r in rows:
        counts[r[0]] = r[1]
    return counts


def _event_writer_loop():
    """Single background thread draining all ops-DB writes. One writer = no lock contention.
    X77.2: a transient (contention) failure no longer silently drops the event -- it's
    persisted to wt_pending_cascade_events and retried by drain_pending_cascade_events on
    the maintenance loop. A non-transient failure (constraint/schema/malformed data) is
    never retried -- retrying it would fail identically forever."""
    wh_id = os.environ.get("WATCHTOWER_INFRA_WEBHOOK_ID", "106e20f6-f542-42b0-83d5-ca8c7b1a7162")
    while True:
        item = _event_q.get()
        if item is None:
            continue
        kind = item[0]
        try:
            def write(c, _item=item):
                _write_cascade_item(c, _item, wh_id)
            operations_write(f"ws-cascade-{kind}", write)
            _bump_stat("succeeded")
        except Exception as e:
            print(f"[WS_CASCADE] ops-db write failed {kind}: {e}", flush=True)
            if _is_transient_write_failure(e):
                try:
                    conn = db_connect(OPS_DB_PATH, timeout=5)
                    try:
                        ensure_cascade_schema(conn)
                        enqueue_pending_cascade_event(conn, item, e)
                        _bump_stat("queued_for_retry")
                    finally:
                        conn.close()
                except Exception as enqueue_exc:
                    print(f"[WS_CASCADE] pending-event enqueue failed (event genuinely lost): "
                          f"{enqueue_exc}", flush=True)
            else:
                _bump_stat("failed_permanent")


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
        _bump_stat("dropped_queue_full")   # queue-full is a genuine loss; counted, not silent


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
        "COALESCE(buy_swarm_ratio,0.0), COALESCE(subprov_type,'UNKNOWN'), "
        "first_creator, COALESCE(seeded_account_count,0) "
        "FROM wt_discovered_subprovs WHERE subprov=?", (wallet,)
    ).fetchone()
    if row is None:
        return None
    cols = ["subprov", "creator_count", "treasury", "treasury_known",
            "wrap_close_count", "topup_count", "confidence", "state",
            "buy_swarm_count", "create_count", "buy_swarm_ratio", "subprov_type",
            "first_creator", "seeded_account_count"]
    return dict(zip(cols, row))


def is_historical_subprov(conn, wallet: str) -> bool:
    """Phase E Pass 1: return True if a wallet shows evidence of pre-existing subprov
    activity that WATCHTOWER didn't track at the time. Zero-cost — DB only, no RPC.

    A wallet is 'historical' if any of these are true:
      - Already in wt_discovered_subprovs with wrap-close evidence (backfill or prior session)
      - Appears as subprov_wallet in wt_candidate_websocket_watches (was active before)
      - Has a prior EXPIRED/COMPLETED session (we saw it before, it ran its course)
    """
    # X26.3.1: wt_candidate_websocket_watches and wt_active_subprov_sessions
    # both record raw session/watch activity independent of
    # wt_discovered_subprovs.state — a known-infrastructure wallet (CEX hot
    # wallet, relay solver, pool authority) can accumulate hundreds of
    # EXPIRED sessions or watch rows purely because creators transacted
    # with it, without ever being a real sub-provisioner. Since
    # wt_discovered_subprovs is the canonical classification record, a
    # REJECTED_INFRASTRUCTURE row there overrides all raw activity checks
    # below — this wallet must never be reported as historical
    # sub-provisioner evidence, regardless of session/watch volume.
    if conn.execute(
        "SELECT 1 FROM wt_discovered_subprovs "
        "WHERE subprov=? AND COALESCE(state,'') LIKE 'REJECTED%' LIMIT 1", (wallet,)
    ).fetchone():
        return False
    # X26.3: a REJECTED_INFRASTRUCTURE row can still carry a nonzero
    # wrap_close_count (confirmed live false-positive: several CEX hot
    # wallets produced a wrap-close-shaped funding tx purely because a
    # creator's first transaction happened to be a CEX withdrawal) — exclude
    # rejected rows so known infrastructure is never reported as historical
    # sub-provisioner evidence.
    if conn.execute(
        "SELECT 1 FROM wt_discovered_subprovs "
        "WHERE subprov=? AND (wrap_close_count + COALESCE(seeded_account_count,0)) > 0 "
        "AND COALESCE(state,'') NOT LIKE 'REJECTED%' LIMIT 1", (wallet,)
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


def record_capital_reload(conn, *, subprov: str, treasury: str, sig: str,
                          amount_sol: float, wrap_close_count: int = 0,
                          first_creator: Optional[str] = None,
                          linked_mint: Optional[str] = None,
                          enrolment_reason: Optional[str] = None,
                          block_time: Optional[int] = None,
                          session_opened: bool = False,
                          operation_uuid: Optional[str] = None) -> None:
    """Persist a CAPITAL_RELOAD event: a large treasury injection into a known or new subprov.
    Idempotent on sig (UNIQUE constraint). Does NOT arm ProgramWatcher — purely an intel record.
    enrolment_reason: PLAIN_TRANSFER_NEW_SUBPROV | PLAIN_TRANSFER_RELOAD | WRAP_CLOSE_RELOAD etc.
    operation_uuid: linked campaign if resolvable at write time (NULL = UNRESOLVED / Mission 2).
    """
    now = int(time.time())
    try:
        conn.execute(
            "INSERT OR IGNORE INTO wt_capital_reloads "
            "(subprov, treasury, sig, amount_sol, wrap_close_count, first_creator, linked_mint, "
            " enrolment_reason, block_time, session_opened, operation_uuid, recorded_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (subprov, treasury, sig, amount_sol, wrap_close_count, first_creator, linked_mint,
             enrolment_reason, block_time, int(session_opened), operation_uuid, now))
        conn.commit()
    except Exception:
        pass


def register_cdc(conn, *, wallet: str, source_treasury: str, funding_sig: str,
                 funding_amount_sol: float, block_time: int) -> bool:
    """Insert a new Capital Distributor Candidate. Returns True if newly inserted."""
    now = int(time.time())
    try:
        conn.execute(
            "INSERT OR IGNORE INTO wt_capital_distributor_candidates "
            "(wallet, source_treasury, funding_sig, funding_amount_sol, first_seen, "
            " observation_state, recorded_at) "
            "VALUES (?,?,?,?,?,'OBSERVING',?)",
            (wallet, source_treasury, funding_sig, funding_amount_sol, block_time or now, now))
        conn.commit()
        return conn.execute("SELECT changes()").fetchone()[0] == 1
    except Exception:
        return False


def cdc_mark_subscribed(conn, *, wallet: str) -> None:
    conn.execute(
        "UPDATE wt_capital_distributor_candidates "
        "SET observation_state='SUBSCRIBED', subscription_started=? WHERE wallet=?",
        (int(time.time()), wallet))
    conn.commit()


def cdc_mark_inactive(conn, *, wallet: str) -> None:
    conn.execute(
        "UPDATE wt_capital_distributor_candidates "
        "SET observation_state='INACTIVE', subscription_ended=? WHERE wallet=?",
        (int(time.time()), wallet))
    conn.commit()


def record_cdc_outbound(conn, *, cdc_wallet: str, sig: str, block_time: int,
                        recipients: list) -> None:
    """Record outbound tx from a CDC wallet. recipients = list of (address, amount_sol)."""
    now = int(time.time())
    fanout_size = len(recipients)
    rows = [(cdc_wallet, sig, block_time, addr, amt, fanout_size, now)
            for addr, amt in recipients]
    conn.executemany(
        "INSERT OR IGNORE INTO wt_cdc_outbound_events "
        "(cdc_wallet, sig, block_time, recipient, amount_sol, fanout_size, recorded_at) "
        "VALUES (?,?,?,?,?,?,?)", rows)
    total_sol = sum(amt for _, amt in recipients if amt)
    conn.execute(
        "UPDATE wt_capital_distributor_candidates SET "
        "last_activity=?, "
        "recipient_count = recipient_count + ?, "
        "total_outbound_sol = total_outbound_sol + ?, "
        "fanout_count = fanout_count + CASE WHEN ? > 3 THEN 1 ELSE 0 END, "
        "largest_fanout = MAX(largest_fanout, ?) "
        "WHERE wallet=?",
        (block_time or now, fanout_size, total_sol, fanout_size, fanout_size, cdc_wallet))
    conn.commit()


def get_active_cdcs(conn) -> list:
    """All CDC wallets currently in OBSERVING state."""
    return [r[0] for r in conn.execute(
        "SELECT wallet FROM wt_capital_distributor_candidates "
        "WHERE observation_state='OBSERVING'").fetchall()]


def get_subscribed_cdcs(conn) -> list:
    """CDC wallets currently SUBSCRIBED — needed to rehydrate WS subscriptions after restart."""
    return [r[0] for r in conn.execute(
        "SELECT wallet FROM wt_capital_distributor_candidates "
        "WHERE observation_state='SUBSCRIBED'").fetchall()]


def is_cdc_wallet(conn, wallet: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM wt_capital_distributor_candidates WHERE wallet=?",
        (wallet,)).fetchone() is not None


def expire_inactive_cdcs(conn, cutoff_ts: int) -> list:
    """Return CDC wallets subscribed but quiet since cutoff_ts and mark them INACTIVE."""
    rows = conn.execute(
        "SELECT wallet FROM wt_capital_distributor_candidates "
        "WHERE observation_state='SUBSCRIBED' "
        "AND (last_activity IS NULL OR last_activity < ?)",
        (cutoff_ts,)).fetchall()
    wallets = [r[0] for r in rows]
    if wallets:
        now = int(time.time())
        for w in wallets:
            conn.execute(
                "UPDATE wt_capital_distributor_candidates "
                "SET observation_state='INACTIVE', subscription_ended=? WHERE wallet=?",
                (now, w))
        conn.commit()
    return wallets


def promote_to_subprov(conn, *, subprov: str, treasury: str,
                       wrap_close_sig: str, creator: str,
                       amount_sol: Optional[float],
                       funding_mechanism: str = "WSOL_WRAP_CLOSE") -> None:
    """Record provisioning evidence and promote a PROVISION_CANDIDATE to PROVISIONAL_SUBPROV.

    Supports both Mechanism A (WSOL_WRAP_CLOSE) and Mechanism B (SEEDED_ACCOUNT_CLOSE).
    Idempotent on wrap_close_sig (UNIQUE constraint on wt_subprov_evidence).

    X26.3: a wrap-close-shaped funding transaction can originate from known
    infrastructure (confirmed live example: several CEX hot wallets — KuCoin,
    OKX, MEXC, WhiteBIT, Bidget, FixedFloat — produced a wrap_close_count=1
    row purely because a creator's very first transaction happened to be a
    CEX withdrawal, not a genuine WATCHTOWER treasury->subprov wrap-close).
    Raw funding evidence is still recorded in wt_subprov_evidence (operation-
    agnostic, never suppressed) but the wallet is never promoted to
    PROVISIONAL_SUBPROV state — it is marked REJECTED_INFRASTRUCTURE instead,
    so it can never be returned as a confirmed_subprov.
    """
    now = int(time.time())
    # 1. Record the evidence row (idempotent on wrap_close_sig UNIQUE) — ALWAYS
    # preserved, regardless of infrastructure status; this is raw on-chain fact,
    # not an operational-identity claim.
    try:
        conn.execute(
            "INSERT OR IGNORE INTO wt_subprov_evidence "
            "(subprov, wrap_close_sig, creator_wallet, amount_sol, funding_mechanism, observed_at) "
            "VALUES (?,?,?,?,?,?)",
            (subprov, wrap_close_sig, creator, amount_sol, funding_mechanism, now))
    except Exception:
        pass

    from src.utils.infra_mapping import is_known_account
    is_infra = is_known_account(subprov)

    # 2. Upsert wt_discovered_subprovs — recount from evidence table (idempotent).
    # seeded_account_count tracks Mechanism B independently; wrap_close_count = Mechanism A only.
    is_mech_b = funding_mechanism == "SEEDED_ACCOUNT_CLOSE"
    # INSERT initialises counts to 0; the UPDATE below does all incrementing so there's
    # no double-count when the row already exists (INSERT OR IGNORE is a no-op then).
    init_state = "REJECTED_INFRASTRUCTURE" if is_infra else "PROVISIONAL_SUBPROV"
    conn.execute(
        """INSERT OR IGNORE INTO wt_discovered_subprovs
             (subprov, first_creator, creator_count, treasury, treasury_known,
              first_seen, last_seen, wrap_close_count, seeded_account_count, state, confidence,
              rejected_reason)
           VALUES (?,?,1,?,0,?,?,0,0,?,0.45,?)""",
        (subprov, creator, treasury, now, now, init_state,
         "known infrastructure wallet" if is_infra else None))
    # wrap_close_count is recounted from wt_subprov_evidence (idempotent via UNIQUE sig).
    # seeded_account_count is incremented only when the evidence row was newly inserted
    # (rowcount > 0 on the evidence INSERT OR IGNORE) — prevents double-count on replay.
    _ev_rows = conn.execute(
        "SELECT changes()").fetchone()[0]  # 1 if new, 0 if duplicate sig
    if is_infra:
        # Known infrastructure: keep raw counts up to date for transparency, but
        # never let state advance past REJECTED_INFRASTRUCTURE, never raise
        # confidence, and never clear rejected_reason.
        conn.execute(
            """UPDATE wt_discovered_subprovs SET
                 wrap_close_count     = (SELECT COUNT(*) FROM wt_subprov_evidence WHERE subprov=? AND COALESCE(funding_mechanism,'WSOL_WRAP_CLOSE')='WSOL_WRAP_CLOSE'),
                 seeded_account_count = seeded_account_count + ?,
                 creator_count    = (SELECT COUNT(DISTINCT creator_wallet) FROM wt_subprov_evidence WHERE subprov=?),
                 last_seen        = ?,
                 state            = 'REJECTED_INFRASTRUCTURE',
                 rejected_reason  = COALESCE(rejected_reason, 'known infrastructure wallet')
               WHERE subprov = ?""",
            (subprov, (1 if is_mech_b else 0) * _ev_rows, subprov, now, subprov))
    else:
        conn.execute(
            """UPDATE wt_discovered_subprovs SET
                 wrap_close_count     = (SELECT COUNT(*) FROM wt_subprov_evidence WHERE subprov=? AND COALESCE(funding_mechanism,'WSOL_WRAP_CLOSE')='WSOL_WRAP_CLOSE'),
                 seeded_account_count = seeded_account_count + ?,
                 creator_count    = (SELECT COUNT(DISTINCT creator_wallet) FROM wt_subprov_evidence WHERE subprov=?),
                 last_seen        = ?,
                 state            = CASE
                   WHEN state = 'PROVISION_CANDIDATE' THEN 'PROVISIONAL_SUBPROV'
                   ELSE state END,
                 confidence       = MIN(0.74, 0.20 +
                   (SELECT COUNT(*) FROM wt_subprov_evidence WHERE subprov=?) * 0.08)
               WHERE subprov = ?""",
            (subprov, (1 if is_mech_b else 0) * _ev_rows, subprov, now, subprov, subprov))
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
        _bump_stat("dropped_queue_full")  # queue-full is a genuine loss; counted, not silent


# ──────────────────────────── session helpers ───────────────────────────────
def start_session(conn, *, subprov: str, treasury: Optional[str], funding_sig: Optional[str],
                  funding_amount: Optional[float], funding_time: Optional[int],
                  ttl_seconds: int, subprov_known: int = 0,
                  open_reason: str = "PROVISION_CANDIDATE",
                  monitoring_state: str = "LIVE_ARMED",
                  funding_sequence_number: Optional[int] = None,
                  treasury_rotated: bool = False,
                  last_activity_at: Optional[int] = None,
                  funding_mechanism: Optional[str] = None) -> bool:
    """Record a confirmed treasury→SUB_PROV funding as an ACTIVE session. Idempotent on
    (subprov, funding_sig). Returns True if a NEW session row was created.

    The active SUB_PROV is DISCOVERED from this funding — it need NOT already be in
    wt_discovered_subprovs. `subprov_known` records whether it happened to be known (a
    confidence signal), but membership is never a gate for session creation.

    `open_reason` is the Phase-A classification label:
      PROVISION_CANDIDATE  — unknown recipient, unproven
      SUBPROV_TOP_UP       — known subprov, extending/re-opening
      SUBPROV_REACTIVATED  — historical subprov (wrap-close seen), re-opened

    X65.68: `subprov` is never opened as a candidate session if it is a known
    CEX/exchange/infrastructure wallet (src.utils.infra_mapping.is_known_account
    — the same registry already used by promote_to_subprov, run_subprov_discovery_job,
    and walkback_worker._is_known_infrastructure). The funding transaction itself is
    real and is not suppressed — record_treasury_hit()/wt_webhook_hits already logs
    the treasury→exchange transfer as legitimate boundary activity — inference simply
    does not continue past the exchange wallet into a fabricated Subprovider session.
    """
    now = int(time.time())
    from src.utils.infra_mapping import is_known_account
    if is_known_account(subprov):
        return False

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
                "    last_topup_at=?, "
                "    funding_sequence_number=COALESCE(funding_sequence_number,?), "
                "    treasury_rotated=COALESCE(treasury_rotated,?), "
                "    last_activity_at=COALESCE(last_activity_at,?), "
                "    monitoring_state=CASE WHEN ?='INTEL_ONLY' THEN 'INTEL_ONLY' ELSE monitoring_state END "
                "WHERE subprov_wallet=? AND state='ACTIVE'",
                (now + ttl_seconds, _amt, now,
                 funding_sequence_number, int(treasury_rotated), last_activity_at,
                 monitoring_state, subprov))
        else:
            conn.execute(
                "UPDATE wt_active_subprov_sessions "
                "SET expires_at=MAX(expires_at,?), "
                "    monitoring_state=CASE WHEN ?='INTEL_ONLY' THEN 'INTEL_ONLY' ELSE monitoring_state END "
                "WHERE subprov_wallet=? AND state='ACTIVE'",
                (now + ttl_seconds, monitoring_state, subprov))
        if open_reason == "SUBPROV_TOP_UP":
            try:
                _topup_op_uuid = None
                try:
                    _r = conn.execute(
                        "SELECT operation_uuid FROM wt_ops_v2 "
                        "WHERE treasury_root=? ORDER BY last_seen DESC LIMIT 1",
                        (treasury or "",)).fetchone()
                    _topup_op_uuid = _r["operation_uuid"] if _r else None
                except Exception:
                    pass
                conn.execute(
                    "INSERT OR IGNORE INTO wt_subprov_topups "
                    "(subprov, treasury, sig, amount_sol, operation_uuid, recorded_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (subprov, treasury or "", funding_sig or "", funding_amount,
                     _topup_op_uuid, now))
            except Exception:
                pass
        conn.commit()
        return False   # no new session row — caller should not re-subscribe

    _fmech = funding_mechanism or "WSOL_WRAP_CLOSE"
    # operation_state: ARMED if LIVE_ARMED (subscribed, candidate pipeline active),
    # FUNDED otherwise (INTEL_ONLY — capital received but not actively monitored).
    # Trailing annotation only — written after detection decisions are already made.
    _op_state = "ARMED" if (monitoring_state == "LIVE_ARMED" and open_reason == "PROVISION_CANDIDATE") else "FUNDED"
    cur = conn.execute(
        """INSERT OR IGNORE INTO wt_active_subprov_sessions
             (subprov_wallet, treasury_wallet, funding_signature, funding_amount,
              initial_funding_amount, funding_time, subprov_known, open_reason,
              monitoring_state, funding_mechanism, operation_state, state, detected_at, expires_at,
              funding_sequence_number, treasury_rotated, last_activity_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?, 'ACTIVE', ?, ?,?,?,?)""",
        (subprov, treasury, funding_sig, funding_amount, funding_amount,
         funding_time or now, int(subprov_known), open_reason, monitoring_state, _fmech, _op_state,
         now, now + ttl_seconds, funding_sequence_number, int(treasury_rotated), last_activity_at))
    conn.commit()
    if cur.rowcount == 0:
        # duplicate funding_sig (e.g. webhook + WS both fire, or pre-restart row) →
        # extend TTL and backfill sequence/reason if the existing row lacks them.
        conn.execute(
            "UPDATE wt_active_subprov_sessions "
            "SET expires_at=MAX(expires_at,?), "
            "    open_reason=COALESCE(NULLIF(open_reason,''),?), "
            "    funding_sequence_number=COALESCE(funding_sequence_number,?), "
            "    treasury_rotated=COALESCE(treasury_rotated,?), "
            "    last_activity_at=COALESCE(last_activity_at,?) "
            "WHERE subprov_wallet=? AND state='ACTIVE'",
            (now + ttl_seconds, open_reason, funding_sequence_number,
             int(treasury_rotated), last_activity_at, subprov))
        conn.commit()
    return cur.rowcount > 0


def active_sessions(conn) -> list:
    return conn.execute(
        "SELECT id, subprov_wallet, treasury_wallet, funding_signature, funding_amount, "
        "funding_time, expires_at, open_reason, subprov_known, "
        "COALESCE(monitoring_state,'LIVE_ARMED') as monitoring_state, "
        "COALESCE(funding_mechanism,'WSOL_WRAP_CLOSE') as funding_mechanism "
        "FROM wt_active_subprov_sessions WHERE state='ACTIVE'").fetchall()


# ───────────────── X24.2 Phase 2: fair, bounded sweep scheduler ──────────────
# Deterministic priority (never restarts to an unfair state — the ordering is
# entirely derived from durable columns, not in-memory state):
#   1. never swept (last_swept_at IS NULL), soonest expiry first
#   2. swept before, least-recently-swept first, soonest expiry as secondary key
#   3. id as a final deterministic tie-breaker (stable across ties, monotonic)
# All eligible ACTIVE sessions are candidates; the caller bounds how many rows
# it actually processes per cycle (the RPC/WS budget), not this query.
def fair_sweep_candidates(conn, limit: int) -> list:
    return conn.execute(
        "SELECT id, subprov_wallet, treasury_wallet, funding_signature, funding_amount, "
        "funding_time, expires_at, open_reason, subprov_known, "
        "COALESCE(monitoring_state,'LIVE_ARMED') as monitoring_state, "
        "COALESCE(funding_mechanism,'WSOL_WRAP_CLOSE') as funding_mechanism, "
        "last_swept_at, sweep_count, first_swept_at "
        "FROM wt_active_subprov_sessions "
        "WHERE state='ACTIVE' "
        "ORDER BY (last_swept_at IS NOT NULL), "        # never-swept (NULL) sorts first
        "         CASE WHEN last_swept_at IS NULL THEN expires_at END, "  # never-swept: soonest expiry
        "         last_swept_at ASC, "                   # swept-before: least-recently-swept first
        "         expires_at ASC, "                       # secondary: soonest expiry
        "         id ASC "                                # deterministic tie-breaker
        "LIMIT ?", (limit,)).fetchall()


def mark_swept(conn, session_id: int, swept_at: Optional[int] = None) -> None:
    """Durable sweep-fairness bookkeeping. Idempotent — safe to call once per
    inspection, even if the inspection found nothing new. Takes a plain _ops()
    connection with its own commit, matching every other per-row
    wt_active_subprov_sessions write in this module (record_launch,
    start_session, etc.) — operations_write's async queue is reserved for
    fire-and-forget metering (treasury/subprov WS usage counters), not the
    inline detection-write path this sits alongside."""
    now = int(swept_at if swept_at is not None else time.time())
    conn.execute(
        "UPDATE wt_active_subprov_sessions "
        "SET last_swept_at=?, sweep_count=COALESCE(sweep_count,0)+1, "
        "    first_swept_at=COALESCE(first_swept_at,?) "
        "WHERE id=?",
        (now, now, session_id))
    conn.commit()


def sweep_coverage_snapshot(conn, *, cap: int) -> dict:
    """Phase 1 instrumentation: point-in-time measurement of sweep coverage,
    read-only, safe to call every cycle without affecting scheduling. Returns
    the metrics the sprint's Phase 1 requires (eligible/selected/never-swept/
    expiring-soon), computed directly from the durable columns so historical
    coverage can be reconstructed even across restarts."""
    now = int(time.time())
    rows = conn.execute(
        "SELECT id, expires_at, last_swept_at, sweep_count "
        "FROM wt_active_subprov_sessions WHERE state='ACTIVE'").fetchall()
    eligible = len(rows)
    never_swept = sum(1 for r in rows if r[2] is None)
    swept_within_30s = sum(1 for r in rows if r[2] is not None and (now - r[2]) <= 30)
    expiring_within_60s_never_swept = sum(
        1 for r in rows if r[2] is None and r[1] is not None and (r[1] - now) <= 60)
    duplicate_sweeps = sum(1 for r in rows if (r[3] or 0) > 1)
    return {
        "eligible_sessions": eligible,
        "cap_per_cycle": cap,
        "never_swept": never_swept,
        "swept_within_30s": swept_within_30s,
        "expiring_within_60s_never_swept": expiring_within_60s_never_swept,
        "sessions_swept_more_than_once": duplicate_sweeps,
        "measured_at": now,
    }


def sweep_arrival_rate(conn, *, window_seconds: int = 300) -> dict:
    """X24.2.1 — measures new-session arrival rate from durable detected_at
    timestamps (survives restart, unlike an in-memory counter). Used to
    compute whether current sweep throughput is keeping up with arrivals,
    independent of whether RPC calls are individually succeeding."""
    now = int(time.time())
    cutoff = now - window_seconds
    n = conn.execute(
        "SELECT COUNT(*) FROM wt_active_subprov_sessions "
        "WHERE state='ACTIVE' AND detected_at >= ?", (cutoff,)).fetchone()[0]
    per_minute = round(n / (window_seconds / 60.0), 2) if window_seconds else 0.0
    return {"window_seconds": window_seconds, "arrivals_in_window": n, "arrivals_per_minute": per_minute}


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
    import sqlite3 as _sq3
    _prev_rf = conn.row_factory
    conn.row_factory = _sq3.Row
    rows = conn.execute(
        "SELECT id, treasury, subprov, funding_sig, funding_amount, funding_time, "
        "open_reason, subprov_known, ttl_seconds, priority "
        "FROM wt_pending_session_writes WHERE state='PENDING' "
        "ORDER BY priority DESC, enqueued_at ASC LIMIT 20"
    ).fetchall()
    conn.row_factory = _prev_rf
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
        def write(c):
            c.execute(
                "INSERT OR IGNORE INTO wt_treasury_ws_usage (treasury_wallet, subscribed_at) VALUES (?, ?)",
                (treasury, now))
        operations_write("ws-cascade-treasury-register", write)
    except Exception:
        pass  # best-effort metering — never crash the WS loop


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


def subprov_account_ws_register(conn, subprov: str) -> None:
    """Mirror of treasury_ws_register for accountSubscribe-watched (PLAIN_TRANSFER-funded)
    sub-provisioners. Must not raise a lock error — best-effort metering only."""
    now = int(time.time())
    try:
        def write(c):
            c.execute(
                "INSERT OR IGNORE INTO wt_subprov_account_ws_usage (subprov_wallet, subscribed_at) "
                "VALUES (?, ?)", (subprov, now))
        operations_write("ws-cascade-subprov-account-register", write)
    except Exception:
        pass


def subprov_account_ws_record_notif(conn, subprov: str, sig: Optional[str]) -> None:
    """Mirror of treasury_ws_record_notif for accountSubscribe-watched sub-provisioners."""
    now = int(time.time())
    hb = now // 3600
    row = conn.execute(
        "SELECT hour_bucket, notif_count_1h FROM wt_subprov_account_ws_usage WHERE subprov_wallet=?",
        (subprov,)).fetchone()
    if row is None:
        conn.execute("INSERT OR IGNORE INTO wt_subprov_account_ws_usage (subprov_wallet, subscribed_at) "
                     "VALUES (?, ?)", (subprov, now))
        cur_bucket, cur_1h = hb, 0
    else:
        cur_bucket, cur_1h = row[0], row[1]
    new_1h = (cur_1h + 1) if cur_bucket == hb else 1
    conn.execute(
        """UPDATE wt_subprov_account_ws_usage
              SET notif_count = notif_count + 1,
                  last_notif_at = ?, last_notif_sig = ?,
                  notif_count_1h = ?, hour_bucket = ?
            WHERE subprov_wallet = ?""",
        (now, sig, new_1h, hb, subprov))
    conn.commit()


def session_for_subprov(conn, subprov: str):
    return conn.execute(
        "SELECT id, treasury_wallet, funding_time, funding_signature FROM wt_active_subprov_sessions "
        "WHERE subprov_wallet=? AND state='ACTIVE' ORDER BY detected_at DESC LIMIT 1",
        (subprov,)).fetchone()


def close_session(conn, session_id: int, state: str) -> None:
    # operation_state: transition FUNDED or ARMED → ABORTED on terminal close without CREATE.
    # Only these two source states are eligible — any state that reached POST_CREATE or beyond
    # is immutable here. Using an allowlist on the source state (not a denylist on targets)
    # so future states added downstream are never silently overwritten.
    _op_update = (
        ", operation_state=CASE WHEN operation_state IN ('FUNDED','ARMED') "
        "THEN 'ABORTED' ELSE operation_state END"
        if state in ("EXPIRED", "BUY_SWARM_REJECTED") else ""
    )
    conn.execute(
        f"UPDATE wt_active_subprov_sessions SET state=?, closed_at=?{_op_update} WHERE id=?",
        (state, int(time.time()), session_id))
    conn.commit()


def set_session_post_create(conn, subprov: str) -> Optional[int]:
    """Transition the active session for *subprov* to POST_CREATE_ACTIVE monitoring state.
    Session stays ACTIVE (not closed) — subprov WS subscription stays live for the 120s
    continuation window. Returns the session id, or None if no active session found."""
    now = int(time.time())
    row = conn.execute(
        "SELECT id FROM wt_active_subprov_sessions "
        "WHERE subprov_wallet=? AND state='ACTIVE' ORDER BY detected_at DESC LIMIT 1",
        (subprov,)).fetchone()
    if not row:
        return None
    conn.execute(
        "UPDATE wt_active_subprov_sessions "
        "SET monitoring_state='POST_CREATE_ACTIVE', "
        "    operation_state='POST_CREATE' "
        "WHERE id=?",
        (row[0],))
    conn.commit()
    return row[0]


def set_session_intel_only(conn, subprov: str) -> None:
    """Downgrade a POST_CREATE_ACTIVE session to INTEL_ONLY (passive intelligence window).
    Session stays ACTIVE so the 4h operation-grouping window is preserved — but the
    subprov WS subscription is dropped by the caller."""
    conn.execute(
        "UPDATE wt_active_subprov_sessions "
        "SET monitoring_state='INTEL_ONLY' "
        "WHERE subprov_wallet=? AND state='ACTIVE'",
        (subprov,))
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
_CANDIDATE_REJECT_WINDOW_S      = int(os.environ.get("WS_CANDIDATE_REJECT_WINDOW_S",      str(2 * 3600)))
_CANDIDATE_REJECT_WINDOW_HV_S   = int(os.environ.get("WS_CANDIDATE_REJECT_WINDOW_HV_S",   str(6 * 3600)))
_CANDIDATE_REJECT_HV_FLOOR      = float(os.environ.get("WS_CANDIDATE_REJECT_HV_FLOOR",    "100"))


def reject_unproven_sessions(conn) -> list:
    """Phase D: expire PROVISION_CANDIDATE sessions that have been ACTIVE for longer than
    WS_CANDIDATE_REJECT_WINDOW_S (default 2h) without producing any wrap-close evidence.

    A wallet that receives treasury SOL but never wrap-closes within 2h is almost certainly
    not a sub-provisioner (AMM pool, CEX deposit, operational wallet, refund recipient, etc).
    Expiring it stops the daemon resubscribing it on reconnect and clears UI noise.

    Returns list of (id, subprov_wallet) expired so caller can unsubscribe.
    """
    now = int(time.time())
    cutoff    = now - _CANDIDATE_REJECT_WINDOW_S
    cutoff_hv = now - _CANDIDATE_REJECT_WINDOW_HV_S
    # Find PROVISION_CANDIDATE sessions opened before the cutoff with no wrap-close evidence.
    # High-value sessions (≥ SESSION_HIGH_SOL_FLOOR) use the longer 6h reject window.
    rows = conn.execute(
        """SELECT s.id, s.subprov_wallet
           FROM wt_active_subprov_sessions s
           WHERE s.state = 'ACTIVE'
             AND s.open_reason = 'PROVISION_CANDIDATE'
             AND (
               CASE
                 WHEN COALESCE(s.initial_funding_amount, s.funding_amount, 0) >= ?
                   THEN s.detected_at < ?
                 ELSE s.detected_at < ?
               END
             )
             AND NOT EXISTS (
               SELECT 1 FROM wt_subprov_evidence e WHERE e.subprov = s.subprov_wallet
             )
             AND NOT EXISTS (
               SELECT 1 FROM wt_candidate_websocket_watches w
               WHERE w.subprov_wallet = s.subprov_wallet
                 AND w.state IN ('WATCHING','FIRED_CREATE','BUY_SWARM')
             )""",
        (_CANDIDATE_REJECT_HV_FLOOR, cutoff_hv, cutoff)).fetchall()
    for r in rows:
        conn.execute(
            "UPDATE wt_active_subprov_sessions "
            "SET state='EXPIRED', closed_at=?, open_reason='PROVISION_CANDIDATE_REJECTED' "
            "WHERE id=?",
            (now, r[0]))
    if rows:
        conn.commit()
    return rows


def tag_operational_spend_proxies(conn) -> int:
    """Retrospectively tag expired, zero-fanout sessions as OPERATIONAL_SPEND_PROXY.

    Requires dict-style row access; ensures row_factory is set if caller omitted it.

    Two detection paths (either qualifies):

    PATH A - Direct on-chain signal (strongest):
      The session wallet appears in wt_webhook_hits as a SENDER to a known Hello
      fee wallet (FudPMePe…, JBGUGPmK…) or the shared service recipient (21wG4F3Z…).
      Requires the proxy to be webhooked, which is rare — this path catches future
      cases if we ever subscribe the proxy wallet.

    PATH B — Structural inference (covers current unwebhooked proxies):
      The session is EXPIRED, zero fan-out, AND the treasury sends the same small
      amount (within ±0.5 SOL) to at least 2 other wallets that also expired with
      zero fan-out in the same DB. This fingerprints the treasury's repeating
      spend-proxy pattern without needing the proxy's own outbound data.
      Only fires for amounts in the operational-spend band (2–20 SOL), never for
      amounts ≥ 50 SOL (which could be a real small-cap subprov).

    Soft tag only — does not change session state or affect the live pipeline.
    Excludes tagged sessions from ARMED strip and Mission 3 provisioning views.
    Idempotent — skips already-tagged rows.

    Returns count of newly tagged sessions.
    """
    import sqlite3 as _sqlite3
    if conn.row_factory is None:
        conn.row_factory = _sqlite3.Row
    now = int(time.time())
    count = 0

    # PATH A — two sub-paths:
    #
    # A1: proxy wallet's own outbounds to Hello signals (requires proxy to be webhooked).
    #     Covers future cases where we subscribe the proxy wallet directly.
    _hello_recipients = [
        "FudPMePe",          # Hello fee wallet 1 (prefix)
        "JBGUGPmK",          # Hello fee wallet 2 (prefix)
        "21wG4F3ZR8gwGC47CkpD6ySBUgH9AABtYMBWFiYdTTgv",  # shared service recipient A
        "FjzGoWfjuTBEpVA4CpGFSEgmkLPCMFCfJcpLvPCwYNk",   # shared service recipient B (seen via 77WErjic + DNS3T5)
    ]
    recipient_clauses = " OR ".join(
        f"h.counterparty LIKE '{r}%'" if len(r) < 44 else f"h.counterparty = '{r}'"
        for r in _hello_recipients
    )
    path_a1 = conn.execute(
        f"""SELECT s.id
            FROM wt_active_subprov_sessions s
            WHERE s.state = 'EXPIRED'
              AND (s.session_tag IS NULL OR s.session_tag = 'POSSIBLE_OPERATIONAL_SPEND_PROXY')
              AND NOT EXISTS (
                SELECT 1 FROM wt_subprov_evidence e WHERE e.subprov = s.subprov_wallet
              )
              AND EXISTS (
                SELECT 1 FROM wt_webhook_hits h
                WHERE h.wallet_address = s.subprov_wallet
                  AND h.direction = 'OUT'
                  AND ({recipient_clauses})
              )"""
    ).fetchall()
    for row in path_a1:
        conn.execute(
            "UPDATE wt_active_subprov_sessions "
            "SET session_tag = 'OPERATIONAL_SPEND_PROXY' WHERE id = ?", (row[0],))
        count += 1

    # A2: confirmed Hello proxy wallets identified via Solscan forensic investigation.
    #     These wallets were directly observed making singleSolPayment calls to 21wG4F3Z.
    #     Enumerated explicitly because they are not webhooked (outbounds not in DB).
    _CONFIRMED_HELLO_PROXIES = {
        "DNS3T5cHmJxjD3TnU6t3vvmRw1FstxTZjRErxJSU5Xf1",  # DchJqu proxy, confirmed 2026-07-07
        "J1dLKj4TJC6S9HCHCGEbwGmXjPByGBvPuNBCJVePMdtE",   # Dtwi proxy, confirmed 2026-07-07
        "77WErjicCa9Popxi8J5pMvQ93oF1ZBRuF7KUUwPvUjc9",   # DchJqu proxy, confirmed 2026-07-07
        "G6kpDV5DeePqXZR7FERqxAARyFJdDLc5LJtBfCS4WuFd",   # DchJqu proxy, confirmed 2026-07-07
        "zeczXRxxdEUpG8YYd2kGPsyFGCvB8JDQxbgTNGN5suP",    # DchJqu proxy, confirmed 2026-07-07
        "7ftn3aHCQzGeJdL2MsfyPMKaeqHwkpYL5QRDYiehdrU3",   # DchJqu proxy, confirmed 2026-07-07
    }
    if _CONFIRMED_HELLO_PROXIES:
        placeholders = ",".join("?" * len(_CONFIRMED_HELLO_PROXIES))
        path_a2 = conn.execute(
            f"""SELECT s.id FROM wt_active_subprov_sessions s
                WHERE s.subprov_wallet IN ({placeholders})
                  AND s.session_tag IS NULL""",
            list(_CONFIRMED_HELLO_PROXIES)
        ).fetchall()
        for row in path_a2:
            conn.execute(
                "UPDATE wt_active_subprov_sessions "
                "SET session_tag = 'OPERATIONAL_SPEND_PROXY' WHERE id = ?", (row[0],))
            count += 1

    # PATH B: structural — same treasury, same round PLAIN_TRANSFER amount, ≥3 peers
    # Targets the repeating spend-proxy pattern: treasury sends an identical round SOL
    # amount via plain transfer to multiple wallets that all expire with zero fan-out.
    # Guards:
    #   - funding_mechanism = PLAIN_TRANSFER only (eliminates all WSOL_WRAP_CLOSE seeds)
    #   - amount in 2–20 SOL band
    #   - amount must be "round" (within ±0.1 SOL of a whole number) — operational
    #     budgets are round; creator seeds from wrap-close are fractional
    #   - ≥3 peer sessions from same treasury at same amount (±0.1 SOL), all zero-fanout
    #     (≥3 is much harder to hit by coincidence than ≥2)
    path_b_candidates = conn.execute(
        """SELECT s.id, s.subprov_wallet, s.treasury_wallet, s.funding_amount
           FROM wt_active_subprov_sessions s
           WHERE s.state = 'EXPIRED'
             AND s.session_tag IS NULL
             AND s.funding_mechanism = 'PLAIN_TRANSFER'
             AND s.funding_amount BETWEEN 2.0 AND 20.0
             AND ABS(s.funding_amount - ROUND(s.funding_amount)) < 0.1
             AND s.treasury_wallet IS NOT NULL
             AND NOT EXISTS (
               SELECT 1 FROM wt_subprov_evidence e WHERE e.subprov = s.subprov_wallet
             )"""
    ).fetchall()
    for row in path_b_candidates:
        peers = conn.execute(
            """SELECT COUNT(*) FROM wt_active_subprov_sessions peer
               WHERE peer.treasury_wallet = ?
                 AND peer.subprov_wallet != ?
                 AND peer.state = 'EXPIRED'
                 AND peer.funding_mechanism = 'PLAIN_TRANSFER'
                 AND ABS(peer.funding_amount - ?) < 0.1
                 AND NOT EXISTS (
                   SELECT 1 FROM wt_subprov_evidence e WHERE e.subprov = peer.subprov_wallet
                 )""",
            (row["treasury_wallet"], row["subprov_wallet"], row["funding_amount"])
        ).fetchone()[0]
        if peers >= 3:
            conn.execute(
                "UPDATE wt_active_subprov_sessions "
                "SET session_tag = 'OPERATIONAL_SPEND_PROXY' WHERE id = ?", (row["id"],))
            count += 1

    # PATH C: fast post-expiry single-wallet classifier.
    # Fires on any expired zero-fanout session that looks like a one-shot operational
    # budget transfer — does NOT require peers (fires on the first instance) but uses
    # a tighter signal set to compensate:
    #   - exactly ONE treasury transfer into the wallet (no seed+capital pair)
    #   - round amount in the known operational-spend band (5–20 SOL ± 0.1)
    #   - confirmed treasury funder
    #   - zero wrap-close / seeded-close evidence
    #   - session already EXPIRED (never promoted beyond PROVISION_CANDIDATE)
    # Tags as POSSIBLE_OPERATIONAL_SPEND_PROXY (softer than A/B) — removed from ARMED
    # views but auditable; promoted to OPERATIONAL_SPEND_PROXY if Hello evidence appears.
    _OPEX_BAND_MIN = 5.0
    _OPEX_BAND_MAX = 20.0
    path_c_candidates = conn.execute(
        """SELECT s.id, s.subprov_wallet, s.treasury_wallet, s.funding_amount
           FROM wt_active_subprov_sessions s
           WHERE s.state = 'EXPIRED'
             AND s.session_tag IS NULL
             AND s.treasury_wallet IS NOT NULL
             AND s.funding_amount BETWEEN ? AND ?
             AND ABS(s.funding_amount - ROUND(s.funding_amount)) < 0.1
             AND NOT EXISTS (
               SELECT 1 FROM wt_subprov_evidence e WHERE e.subprov = s.subprov_wallet
             )""",
        (_OPEX_BAND_MIN, _OPEX_BAND_MAX)
    ).fetchall()
    for row in path_c_candidates:
        # Count ALL treasury transfers into this wallet (from any source, not just webhook hits)
        # using wt_webhook_hits inbounds — a seed+capital pair would show as 2 transfers.
        # If the wallet isn't in webhook_hits at all, fall back to checking capital_reloads.
        inbound_count = conn.execute(
            """SELECT COUNT(DISTINCT h.tx_signature) FROM wt_webhook_hits h
               WHERE h.counterparty = ?
                 AND h.direction = 'OUT'
                 AND h.wallet_address IN (SELECT treasury FROM wt_confirmed_treasuries)""",
            (row["subprov_wallet"],)
        ).fetchone()[0]

        # If not in webhook_hits, check capital_reloads for transfer count
        if inbound_count == 0:
            inbound_count = conn.execute(
                """SELECT COUNT(*) FROM wt_capital_reloads cr
                   WHERE cr.subprov = ? AND cr.treasury IN (SELECT treasury FROM wt_confirmed_treasuries)""",
                (row["subprov_wallet"],)
            ).fetchone()[0]

        # Exactly 1 inbound treasury transfer = one-shot operational budget (not seed+capital)
        if inbound_count == 1:
            conn.execute(
                "UPDATE wt_active_subprov_sessions "
                "SET session_tag = 'POSSIBLE_OPERATIONAL_SPEND_PROXY' WHERE id = ?",
                (row["id"],))
            count += 1

    if count:
        conn.commit()
    return count


# ─────────────────────────── candidate helpers ──────────────────────────────
def open_candidate_watch(conn, *, candidate: str, subprov: str, treasury: Optional[str],
                         wrap_close_sig: Optional[str], wrap_wallet: Optional[str],
                         temp_wsol: Optional[str], funding_amount: Optional[float],
                         ttl_seconds: int, wrap_close_time: Optional[int] = None,
                         funding_mechanism: str = "WSOL_WRAP_CLOSE") -> bool:
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
    # X28.0 Phase 3 — snapshot the parent subprov's own funding provenance onto the candidate
    # row at capture time, so it survives independent of the session row (never deleted today,
    # but no longer required for the candidate to retain its lineage). Best-effort: a missing
    # session row (e.g. legacy data, or a candidate opened via a path with no session yet)
    # leaves these NULL rather than failing the insert.
    _sess = conn.execute(
        "SELECT funding_signature, initial_funding_amount, funding_amount, funding_time "
        "FROM wt_active_subprov_sessions WHERE subprov_wallet=? "
        "ORDER BY id DESC LIMIT 1", (subprov,)).fetchone()
    _init_sig = _sess[0] if _sess else None
    _init_sol = (_sess[1] if _sess and _sess[1] is not None else (_sess[2] if _sess else None))
    _init_time = _sess[3] if _sess else None
    _fanout_row = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(funding_amount), 0.0) "
        "FROM wt_candidate_websocket_watches WHERE subprov_wallet=?", (subprov,)).fetchone()
    _fanout_count = (_fanout_row[0] or 0) + 1  # +1 for the row being inserted now
    _fanout_value = (_fanout_row[1] or 0.0) + (funding_amount or 0.0)
    cur = conn.execute(
        """INSERT OR IGNORE INTO wt_candidate_websocket_watches
             (candidate_wallet, subprov_wallet, treasury_wallet, wrap_close_signature,
              wrap_close_time, wrap_wallet, temp_wsol_account, close_destination, funding_amount,
              funding_mechanism, state, detected_at, expires_at,
              initial_subprov_funding_sol, initial_subprov_funding_signature,
              initial_subprov_funding_time, subprov_fanout_count_at_capture,
              subprov_fanout_value_at_capture)
           VALUES (?,?,?,?,?,?,?,?,?,?, 'WATCHING', ?, ?, ?,?,?,?,?)""",
        (candidate, subprov, treasury, wrap_close_sig, wrap_close_time, wrap_wallet, temp_wsol,
         candidate, funding_amount, funding_mechanism, now, now + ttl_seconds,
         _init_sol, _init_sig, _init_time, _fanout_count, _fanout_value))
    conn.commit()
    return cur.rowcount > 0


def subprov_sig_enqueue_running(conn, *, subprov: str, signature: str,
                                slot: Optional[int] = None) -> tuple[bool, float]:
    """X24.2.2 — combines subprov_sig_enqueue()+subprov_sig_mark_running() into a
    single write (one _acquire_write_lane() acquisition instead of two).

    The original two-step (PENDING then RUNNING) existed so a crash between the
    two writes would leave the row PENDING for due_subprov_sig_retries() to pick
    up later. Skipping straight to RUNNING preserves that same safety property:
    a crash before this single write leaves no row at all, which is recovered
    identically to a genuinely-never-seen signature (the live WS/catch-up path
    re-notifies it, exactly as it does today for any signature this process
    never got as far as writing anything for). No crash window is introduced
    that didn't already exist; one write-lock acquisition per signature is
    removed, mattering most under X24.2.1's concurrent sweep sessions where
    every write across the process contends for the single DB_WRITE_SERIALIZE lock.

    Returns (is_new, first_seen_at) — is_new True the first time this
    (subprov, signature) is recorded; first_seen_at is the original detection
    time if this signature was already known (e.g. previously FAILED and now
    retried), else the current time.
    """
    now = int(time.time())
    existing = conn.execute(
        "SELECT first_seen_at, status FROM wt_subprov_sig_retry "
        "WHERE subprov_wallet=? AND signature=?", (subprov, signature)).fetchone()
    is_new = existing is None
    first_seen_at = float(existing[0]) if existing and existing[0] else float(now)
    conn.execute(
        """INSERT INTO wt_subprov_sig_retry
             (subprov_wallet, signature, slot, first_seen_at, last_attempt_at,
              attempts, status)
           VALUES (?,?,?,?,?, 1, 'RUNNING')
           ON CONFLICT(subprov_wallet, signature) DO UPDATE SET
             slot = COALESCE(wt_subprov_sig_retry.slot, excluded.slot),
             last_attempt_at = excluded.last_attempt_at,
             attempts = wt_subprov_sig_retry.attempts + 1,
             status = 'RUNNING',
             last_error = NULL""",
        (subprov, signature, slot, now, now))
    conn.commit()
    return is_new, first_seen_at


def subprov_sig_mark_done(conn, *, subprov: str, signature: str,
                          slot: Optional[int] = None, block_time: Optional[int] = None) -> None:
    """Marks one signature DONE in the retry table AND advances the durable
    cursor to it. Correct ONLY when called in strict oldest-to-newest
    processing order (the cursor is unconditionally overwritten on every
    call, so the last call determines the final cursor position) — this is
    the WS live-path contract (one signature at a time, arrival order) and
    is preserved exactly for that caller. X24.7's batched/reordered catch-up
    path uses subprov_sig_mark_retry_done() + subprov_sig_advance_cursor()
    instead, precisely because it does NOT process oldest-to-newest."""
    now = int(time.time())
    subprov_sig_mark_retry_done(conn, subprov=subprov, signature=signature)
    subprov_sig_advance_cursor(conn, subprov=subprov, signature=signature,
                               slot=slot, block_time=block_time)


def subprov_sig_mark_retry_done(conn, *, subprov: str, signature: str) -> None:
    """Marks one signature DONE in the durable retry table only — no cursor
    write. Safe to call in any order (including out-of-chronological-order,
    e.g. under an alternating/reordered processing policy), since each row
    is keyed by its own (subprov, signature) and never depends on any other
    row's state."""
    now = int(time.time())
    conn.execute(
        """UPDATE wt_subprov_sig_retry
           SET status='DONE', last_error=NULL, last_attempt_at=?
           WHERE subprov_wallet=? AND signature=?""",
        (now, subprov, signature))
    conn.commit()


def subprov_sig_advance_cursor(conn, *, subprov: str, signature: str,
                               slot: Optional[int] = None, block_time: Optional[int] = None) -> None:
    """Advances the durable per-subprov cursor to `signature`. Callers are
    responsible for ensuring `signature` is the correct one to advance to —
    for a batch processed out of chronological order (X24.7), this must be
    the NEWEST signature that was successfully processed in that batch, not
    merely whichever signature happened to be handled last. Called at most
    once per catch_up_subprov() batch (not per-signature) by that caller."""
    now = int(time.time())
    conn.execute(
        """INSERT INTO wt_subprov_sig_cursor
             (subprov_wallet, last_seen_sig, last_seen_slot, last_seen_at, updated_at)
           VALUES (?,?,?,?,?)
           ON CONFLICT(subprov_wallet) DO UPDATE SET
             last_seen_sig=excluded.last_seen_sig,
             last_seen_slot=COALESCE(excluded.last_seen_slot, wt_subprov_sig_cursor.last_seen_slot),
             last_seen_at=COALESCE(excluded.last_seen_at, wt_subprov_sig_cursor.last_seen_at),
             updated_at=excluded.updated_at""",
        (subprov, signature, slot, block_time, now))
    conn.commit()


# X64.9B1 — age buckets required by the measurement design (see
# docs/design/x64_9/x64_9b1_observability_design.md). Order matters: first
# matching upper bound wins, so buckets must stay ascending.
_DEDUPE_AGE_BUCKETS = [
    ("<5m",     300),
    ("5m-30m",  1800),
    ("30m-2h",  7200),
    ("2h-12h",  43200),
    ("12h-24h", 86400),
    ("1d-3d",   259200),
    ("3d-7d",   604800),
    ("7d-14d",  1209600),
    ("14d-30d", 2592000),
    (">30d",    None),
]

_DEDUPE_SOURCE_COLUMNS = {
    "WS": "source_ws",
    "CATCHUP": "source_catchup",
    "RETRY": "source_retry",
    "HOT_BURST": "source_hot_burst",
}


def dedupe_age_bucket(age_s: int) -> str:
    """Maps a duplicate age in seconds to its bucket label. Pure function,
    no I/O — safe to call from any context, including error handlers."""
    age_s = max(0, int(age_s or 0))
    for label, upper in _DEDUPE_AGE_BUCKETS:
        if upper is None or age_s < upper:
            return label
    return ">30d"  # unreachable given the None sentinel above, kept for clarity


def record_subprov_sig_duplicate(conn, *, subprov: str, age_s: int,
                                 source: str, observed_at: Optional[int] = None) -> None:
    """Durably records one duplicate-signature observation (X64.9B1).

    Best-effort by design: this function assumes the caller has already
    wrapped it in a try/except that swallows any failure (see
    src/core/ws_cascade.py's dedupe branch) — observability must never be
    able to affect the existing dedup/skip behaviour it is measuring.
    Does NOT touch wt_subprov_sig_retry in any way."""
    now = int(observed_at if observed_at is not None else time.time())
    age_s = max(0, int(age_s or 0))
    bucket = dedupe_age_bucket(age_s)
    source_col = _DEDUPE_SOURCE_COLUMNS.get(source)

    set_source_clause = f", {source_col} = {source_col} + 1" if source_col else ""
    conn.execute(
        f"""INSERT INTO wt_subprov_sig_dedupe_stats
             (subprov_wallet, age_bucket, duplicate_count, max_duplicate_age_s,
              first_observed_at, last_observed_at{"," + source_col if source_col else ""})
           VALUES (?, ?, 1, ?, ?, ?{", 1" if source_col else ""})
           ON CONFLICT(subprov_wallet, age_bucket) DO UPDATE SET
             duplicate_count = duplicate_count + 1,
             max_duplicate_age_s = MAX(
                 COALESCE(wt_subprov_sig_dedupe_stats.max_duplicate_age_s, 0),
                 excluded.max_duplicate_age_s),
             last_observed_at = excluded.last_observed_at{set_source_clause}""",
        (subprov, bucket, age_s, now, now))

    conn.execute(
        """INSERT INTO wt_subprov_sig_dedupe_summary
             (id, total_checked, total_duplicates, max_duplicate_age_s,
              first_duplicate_at, last_duplicate_at, updated_at)
           VALUES (1, 0, 1, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             total_duplicates = total_duplicates + 1,
             max_duplicate_age_s = MAX(COALESCE(wt_subprov_sig_dedupe_summary.max_duplicate_age_s, 0), excluded.max_duplicate_age_s),
             last_duplicate_at = excluded.last_duplicate_at,
             updated_at = excluded.updated_at""",
        (age_s, now, now, now))
    conn.commit()


def record_subprov_sig_checked(conn, *, observed_at: Optional[int] = None) -> None:
    """Increments the durable total_checked denominator (X64.9B1). Called for
    EVERY signature reaching the dedupe check, whether skipped or not — this
    is what makes a later '0 duplicates' result meaningful (see
    x64_9b1_measurement_contract.md). Best-effort, same discipline as
    record_subprov_sig_duplicate()."""
    now = int(observed_at if observed_at is not None else time.time())
    conn.execute(
        """INSERT INTO wt_subprov_sig_dedupe_summary
             (id, total_checked, total_duplicates, updated_at)
           VALUES (1, 1, 0, ?)
           ON CONFLICT(id) DO UPDATE SET
             total_checked = total_checked + 1,
             updated_at = excluded.updated_at""",
        (now,))
    conn.commit()


def subprov_sig_mark_failed(conn, *, subprov: str, signature: str, error: str,
                            max_attempts: int = 8) -> None:
    now = int(time.time())
    err = (error or "")[:500]
    row = conn.execute(
        "SELECT attempts FROM wt_subprov_sig_retry WHERE subprov_wallet=? AND signature=?",
        (subprov, signature)).fetchone()
    attempts = int(row[0] or 0) if row else 0
    status = "FAILED" if attempts >= max_attempts else "PENDING"
    conn.execute(
        """INSERT INTO wt_subprov_sig_retry
             (subprov_wallet, signature, first_seen_at, last_attempt_at,
              attempts, last_error, status)
           VALUES (?,?,?,?, 1, ?, ?)
           ON CONFLICT(subprov_wallet, signature) DO UPDATE SET
             last_attempt_at=excluded.last_attempt_at,
             last_error=excluded.last_error,
             status=excluded.status""",
        (subprov, signature, now, now, err, status))
    conn.commit()


def subprov_cursor(conn, subprov: str) -> Optional[str]:
    row = conn.execute(
        "SELECT last_seen_sig FROM wt_subprov_sig_cursor WHERE subprov_wallet=?",
        (subprov,)).fetchone()
    return row[0] if row and row[0] else None


def due_subprov_sig_retries(conn, *, limit: int = 25, now: Optional[int] = None) -> list:
    """Return retryable active-subprov signatures with exponential-ish backoff."""
    now = int(now or time.time())
    rows = conn.execute(
        """SELECT r.subprov_wallet, r.signature, r.slot, r.attempts,
                  r.last_attempt_at
           FROM wt_subprov_sig_retry r
           JOIN wt_active_subprov_sessions s
             ON s.subprov_wallet=r.subprov_wallet AND s.state='ACTIVE'
           WHERE r.status='PENDING'
              OR (r.status='RUNNING' AND COALESCE(r.last_attempt_at, 0) < ?)
           ORDER BY COALESCE(r.last_attempt_at, 0), r.first_seen_at
           LIMIT ?""",
        (now - 120, limit * 4,)).fetchall()
    due = []
    for row in rows:
        attempts = int(row[3] or 0)
        last_attempt = int(row[4] or 0)
        delay = min(300, 2 ** min(attempts, 8))
        if not last_attempt or now - last_attempt >= delay:
            due.append(row)
        if len(due) >= limit:
            break
    return due


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
                  wrap_close_sol: Optional[float] = None,
                  detection_source: Optional[str] = None,
                  detection_delay_seconds: Optional[int] = None,
                  funding_mechanism: str = FUNDING_MECHANISM) -> bool:
    """Authoritative launch record. Idempotent on (creator, create_sig). Marks the
    candidate FIRED_CREATE. Returns True if newly recorded.

    subprov_funding_sol = treasury→subprov load (the big provisioning capital).
    wrap_close_sol       = subprov→creator wrap-close seed (the creator's birth amount).
    Together: the full provisioning-cost chain that produced this launch."""
    cur = conn.execute(
        """INSERT OR IGNORE INTO wt_watchtower_launches
             (mint, creator_wallet, create_signature, create_time, create_slot, treasury_wallet,
              subprov_wallet, subprov_funding_sol, wrap_close_sol, wrap_close_signature,
              birth_to_launch_seconds, detection_source, detection_delay_seconds,
              funding_mechanism, creator_extraction_method, confidence, state)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'FIRED_CREATE')""",
        (mint, creator, create_sig, create_time, create_slot, treasury, subprov,
         subprov_funding_sol, wrap_close_sol, wrap_close_sig,
         birth_to_launch_s, detection_source, detection_delay_seconds,
         funding_mechanism, EXTRACTION_METHOD, confidence))
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
        threading.Thread(target=_write_detected_create,
                         args=(mint, creator, create_sig, create_slot),
                         daemon=True, name="wt-detected-create").start()
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


def _write_detected_create(mint: str, creator: str, create_sig: Optional[str],
                           create_slot: Optional[int]) -> None:
    """Write wt_detected_creates + update token_analysis.create_tx_signature in the live DB.
    Idempotent (INSERT OR IGNORE on mint). Runs in a daemon thread off the detection path."""
    for _attempt in range(3):
        try:
            lc = db_connect(LIVE_DB_PATH, timeout=20)
            try:
                lc.execute("PRAGMA busy_timeout=15000")
                now_ = time.time()
                lc.execute(
                    "INSERT OR IGNORE INTO wt_detected_creates "
                    "(mint, creator, slot, signature, detected_at, created_at) "
                    "VALUES (?, ?, ?, ?, ?, strftime('%s','now'))",
                    (mint, creator, create_slot, create_sig, now_))
                if create_sig:
                    lc.execute(
                        "UPDATE token_analysis SET "
                        "create_tx_signature = COALESCE(create_tx_signature, ?) "
                        "WHERE mint = ?",
                        (create_sig, mint))
                lc.commit()
            finally:
                lc.close()
            return
        except Exception as e:
            if "locked" in str(e).lower() and _attempt < 2:
                time.sleep(1.0); continue
            print(f"[WS_CASCADE] wt_detected_creates write failed for {mint[:10]}…: {e}", flush=True)
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


def record_fanout_event(conn, *, subprov: str, treasury: Optional[str],
                        fanout_time: int, dests: list, sig: str) -> None:
    """Write one row to wt_fanout_events summarising this wrap-close fan-out burst.
    dests: list of {"candidate": str, "base_amount_sol": float|None, ...}"""
    if not dests:
        return
    amounts = [d.get("base_amount_sol") or 0.0 for d in dests]
    total_sol = sum(amounts)
    largest = max(amounts) if amounts else 0.0
    smallest = min(amounts) if amounts else 0.0
    avg_sol = total_sol / len(amounts) if amounts else 0.0
    # identical if all non-zero amounts are within 0.1% of each other
    nonzero = [a for a in amounts if a > 0]
    has_identical = bool(nonzero and (max(nonzero) - min(nonzero)) / max(nonzero) < 0.001)
    now = int(time.time())
    conn.execute(
        """INSERT INTO wt_fanout_events
             (subprov_wallet, treasury_wallet, fanout_time, fanout_count, total_sol,
              largest_sol, smallest_sol, avg_sol, has_identical_amounts, sig_sample,
              creates_fired, buy_swarms, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?, 0, 0, ?)
           ON CONFLICT(subprov_wallet, fanout_time) DO UPDATE SET
             fanout_count = wt_fanout_events.fanout_count + excluded.fanout_count,
             total_sol = COALESCE(wt_fanout_events.total_sol, 0) + COALESCE(excluded.total_sol, 0),
             largest_sol = MAX(COALESCE(wt_fanout_events.largest_sol, 0), COALESCE(excluded.largest_sol, 0)),
             smallest_sol = CASE
               WHEN COALESCE(wt_fanout_events.smallest_sol, 0)=0 THEN excluded.smallest_sol
               WHEN COALESCE(excluded.smallest_sol, 0)=0 THEN wt_fanout_events.smallest_sol
               ELSE MIN(wt_fanout_events.smallest_sol, excluded.smallest_sol)
             END,
             avg_sol = (COALESCE(wt_fanout_events.total_sol, 0) + COALESCE(excluded.total_sol, 0))
                       / (wt_fanout_events.fanout_count + excluded.fanout_count),
             has_identical_amounts = CASE
               WHEN wt_fanout_events.has_identical_amounts=1 AND excluded.has_identical_amounts=1 THEN 1
               ELSE 0
             END,
             sig_sample = COALESCE(wt_fanout_events.sig_sample, excluded.sig_sample)""",
        (subprov, treasury, fanout_time, len(dests), round(total_sol, 9),
         round(largest, 9), round(smallest, 9), round(avg_sol, 9),
         int(has_identical), sig[:88], now))
    conn.commit()


def latest_launch(conn):
    return conn.execute(
        "SELECT mint, creator_wallet, create_signature, create_time, treasury_wallet, "
        "subprov_wallet, birth_to_launch_seconds, confidence, recorded_at "
        "FROM wt_watchtower_launches ORDER BY id DESC LIMIT 1").fetchone()


# ── Token Lifecycle (derived, read-only aggregation) ─────────────────────────

def upsert_lifecycle_launched(conn, *, mint: str, treasury: str, subprov: str,
                               creator: str, create_sig: str, launched_at: int,
                               operation_uuid: Optional[str] = None) -> None:
    """Insert or update lifecycle row to LAUNCHED state. Idempotent on mint.

    Pulls funded_at from the matching LIVE_ARMED session so the full
    ARMED→LAUNCHED duration is visible in the ledger without a separate join.
    """
    now = int(time.time())
    # Any session for this subprov is valid — INTEL_ONLY sessions (e.g. PLAIN_TRANSFER
    # funded subprovs) never reach LIVE_ARMED but still carry the correct funding_time.
    session_row = conn.execute(
        """SELECT funding_time, funding_signature FROM wt_active_subprov_sessions
           WHERE subprov_wallet = ?
           ORDER BY funding_time DESC LIMIT 1""",
        (subprov,)).fetchone()
    funded_at = session_row["funding_time"] if session_row else None
    conn.execute(
        """INSERT INTO wt_token_lifecycle
               (mint, treasury, subprov, creator, create_sig, lifecycle_state,
                funded_at, launched_at, operation_uuid, updated_at)
           VALUES (?,?,?,?,?,'LAUNCHED',?,?,?,?)
           ON CONFLICT(mint) DO UPDATE SET
               lifecycle_state = CASE WHEN lifecycle_state='LAUNCHED' THEN 'LAUNCHED'
                                      ELSE lifecycle_state END,
               funded_at = COALESCE(wt_token_lifecycle.funded_at, excluded.funded_at),
               updated_at = excluded.updated_at""",
        (mint, treasury, subprov, creator, create_sig, funded_at, launched_at, operation_uuid, now))
    conn.commit()


def advance_lifecycle_migrated(conn, *, mint: str, migrated_at: int,
                                migration_sig: Optional[str] = None) -> None:
    """Advance lifecycle to MIGRATED if currently LAUNCHED. No-op otherwise."""
    now = int(time.time())
    conn.execute(
        """UPDATE wt_token_lifecycle
           SET lifecycle_state='MIGRATED', migrated_at=?, migration_sig=?,
               campaign_end_reason='MIGRATED_NO_RECYCLE_OBSERVED', updated_at=?
           WHERE mint=? AND lifecycle_state='LAUNCHED'""",
        (migrated_at, migration_sig, now, mint))
    conn.commit()


def advance_lifecycle_recycled(conn, *, mint: str, recycled_at: int,
                                recycle_sig: str, recycle_amount_sol: float,
                                recycle_direction: str,
                                campaign_end_reason: str) -> bool:
    """Advance lifecycle to RECYCLED (HIGH confidence only). Returns True if updated."""
    now = int(time.time())
    conn.execute(
        """UPDATE wt_token_lifecycle
           SET lifecycle_state='RECYCLED', recycled_at=?, recycle_sig=?,
               recycle_amount_sol=?, recycle_direction=?,
               campaign_end_reason=?, updated_at=?
           WHERE mint=? AND lifecycle_state='MIGRATED'""",
        (recycled_at, recycle_sig, recycle_amount_sol, recycle_direction,
         campaign_end_reason, now, mint))
    conn.commit()
    return conn.execute("SELECT changes()").fetchone()[0] == 1


def get_lifecycle_rows(conn, limit: int = 100) -> list:
    """Merged operations ledger: ARMED/EXPIRED pre-launch sessions + LAUNCHED/MIGRATED/RECYCLED mints.

    Session rows (no mint yet) appear with lifecycle_state = 'ARMED' or 'EXPIRED'.
    Token rows keep their existing lifecycle_state. Sorted by the earliest relevant
    timestamp so the ledger reads as a single chronological operation stream.
    """
    # Pre-launch sessions: LIVE_ARMED (active) or closed without a mint (EXPIRED)
    session_rows = conn.execute(
        """SELECT s.subprov_wallet AS subprov,
                  s.treasury_wallet AS treasury,
                  NULL AS mint,
                  NULL AS creator,
                  NULL AS create_sig,
                  CASE
                      WHEN s.state = 'ACTIVE' AND s.monitoring_state = 'LIVE_ARMED' THEN 'ARMED'
                      WHEN s.state = 'ACTIVE' AND s.monitoring_state = 'POST_CREATE_ACTIVE' THEN 'ARMED'
                      ELSE 'EXPIRED'
                  END AS lifecycle_state,
                  s.funding_time AS funded_at,
                  NULL AS launched_at,
                  NULL AS migrated_at,
                  NULL AS recycled_at,
                  NULL AS campaign_end_reason,
                  NULL AS recycle_amount_sol,
                  NULL AS recycle_direction,
                  NULL AS operation_uuid,
                  s.funding_amount,
                  s.expires_at,
                  s.topup_count,
                  s.topup_amount_total,
                  NULL AS birth_to_launch_seconds,
                  NULL AS confidence,
                  s.funding_time AS sort_ts
           FROM wt_active_subprov_sessions s
           WHERE s.monitoring_state IN ('LIVE_ARMED', 'POST_CREATE_ACTIVE')
             -- exclude operational spend proxies (confirmed and possible)
             AND s.session_tag NOT IN ('OPERATIONAL_SPEND_PROXY','POSSIBLE_OPERATIONAL_SPEND_PROXY')
             -- exclude sessions whose subprov already has a launched mint (handled below)
             AND s.subprov_wallet NOT IN (SELECT subprov FROM wt_token_lifecycle WHERE subprov IS NOT NULL)
           ORDER BY s.funding_time DESC
           LIMIT ?""",
        (limit,)).fetchall()

    # Token rows: LAUNCHED / MIGRATED / RECYCLED
    token_rows = conn.execute(
        """SELECT tl.subprov, tl.treasury, tl.mint, tl.creator, tl.create_sig,
                  tl.lifecycle_state,
                  tl.funded_at,
                  tl.launched_at,
                  tl.migrated_at,
                  tl.recycled_at,
                  tl.campaign_end_reason,
                  tl.recycle_amount_sol,
                  tl.recycle_direction,
                  tl.operation_uuid,
                  NULL AS funding_amount,
                  NULL AS expires_at,
                  NULL AS topup_count,
                  NULL AS topup_amount_total,
                  wl.birth_to_launch_seconds,
                  wl.confidence,
                  COALESCE(tl.funded_at, tl.launched_at) AS sort_ts
           FROM wt_token_lifecycle tl
           LEFT JOIN wt_watchtower_launches wl ON wl.mint = tl.mint
           ORDER BY tl.launched_at DESC
           LIMIT ?""",
        (limit,)).fetchall()

    # Merge: most-recent first; cap at limit
    merged = sorted(
        [dict(r) for r in session_rows] + [dict(r) for r in token_rows],
        key=lambda r: r.get("sort_ts") or 0,
        reverse=True,
    )
    return merged[:limit]
