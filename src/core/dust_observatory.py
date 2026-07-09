"""WATCHTOWER Dust Observatory — longitudinal intelligence layer.

Records every transfer from known DUST_MARKER wallets, classifies each recipient using
existing WATCHTOWER tables (zero RPC in the hot path), and tracks how recipient roles
evolve over time.

This is purely observational. It does NOT:
  - open candidate sessions
  - trigger creator alerts
  - promote wallets
  - subscribe recipients
  - change any classification

Architecture:
  - WS callback enqueues (dust_wallet, sig) → write queue
  - Writer thread drains queue: getTransaction → extract recipients + amounts → INSERT
  - Enricher loop (runs every ENRICH_INTERVAL_S): classify each unclassified recipient
    against existing WATCHTOWER tables; update wt_dust_recipient_lifecycle
  - Stats queries serve the dashboard API

Storage: wt_ops_v2.db (same DB as the rest of WATCHTOWER telemetry)
"""

from __future__ import annotations

import os
import time
import json
import queue
import threading
import sqlite3
import logging
from typing import Optional, List, Dict

log = logging.getLogger(__name__)

try:
    from src.utils.db_locking import db_connect
except ImportError:
    def db_connect(path, timeout=30, row_factory=None):
        c = sqlite3.connect(path, timeout=timeout)
        if row_factory:
            c.row_factory = row_factory
        return c

OPS_DB_PATH = os.environ.get(
    "OPS_V2_DB_PATH",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "database", "wt_ops_v2.db")))

RPC_URL = os.environ.get(
    "HELIUS_RPC_URL",
    f"https://mainnet.helius-rpc.com/?api-key={os.environ.get('HELIUS_API_KEY', '')}")

# How often the enricher loop re-classifies unclassified / stale recipients (seconds)
ENRICH_INTERVAL_S = int(os.environ.get("DUST_ENRICH_INTERVAL_S", "120"))

# Only fetch dust transfers where the amount is in the known dust range (lam)
DUST_MIN_LAM = 500
DUST_MAX_LAM = 20_000

# Max sigs to enqueue per notification burst (prevents runaway on reconnect replay)
_WRITE_QUEUE_MAXSIZE = 500

# Known DUST_MARKER wallets — loaded at startup from wt_dust_markers table (or this seed list).
# The seed list captures the wallets confirmed by the June 2026 investigation. The DB table
# is the live source of truth; the seed list bootstraps a fresh install.
SEED_DUST_WALLETS: List[str] = [
    "43P1jMnMwNSJEpK9vW9c9fZuQzQXNCsurgN8XQ8vPy3D",   # confirmed, known, 43PK ecosystem
    "43PKrTh5p2B5oAPLjttM4QztGz2G7LBHhi5tr2Ej3y3D",   # 43PK prefix → 41iv treasury
    "Cgwrbh7DTtKCb4K5NjWYNWy4EdMWeSwQw71GinH1UkTe",   # Cgwr prefix → Cgwr treasury
    "2q5A33xrzi7U2TTCHEWCbmNeHE9fTAb282WjG52K7fzW",   # 2q5A prefix → 2q5A treasury
    "Dch16BUbk126BVase2a3TBPzy7x4f3N3FHmcf4VTCKhK",   # Dch1 prefix → DchJ treasury (shared)
    "Dtw1FjBno8FVXFHe2PGeJC7VT8zFHm6iBGYGXdCt7q5q",   # Dtw1 prefix → Dtwi treasury (shared)
    "9hG1bDXPGMd9YphQPqNmsU5a1gk7q38J14RdaC8JEZk4",   # 9hG1 prefix → 9hGc treasury
    "EF11p7bnxFZMCktB73bBp55MnoLHFHMzxiTv5GhEFggq3",  # EF11 prefix → EFKV treasury (shared)
    "41i1TExmAq54kg3XSgStic7DzD5jfY7JkPW5s7Ef1JaL",   # 41i1 prefix → 41iv treasury
    "5JW1HStyNushonoqZ6ceooMuWgqFpmhQpE49U5ZxifXBi",   # 5JW1 prefix → 5JWi treasury (shared)
    # "G212UVo8uwfcAKvU2eb4TtWpa1cJzE3DJQRP1fqf2ewPZ",  # INVALID (33-byte key) — needs correct G2CQ dust companion address
]

# Role labels used in wt_dust_recipient_lifecycle.current_role
# Ordered from most to least specific WATCHTOWER infrastructure
KNOWN_ROLES = (
    "TREASURY",
    "TREASURY_MESH",
    "CAPITAL_RELAY",    # CDC
    "PROVISION_CANDIDATE",
    "SUBPROV",
    "BUY_SWARM",
    "PROFIT_RELAY",
    "CREATOR",
    "UNKNOWN",
)

# ── Schema ─────────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS wt_dust_markers (
    wallet          TEXT PRIMARY KEY,
    label           TEXT,                    -- e.g. "43P1_confirmed"
    associated_treasury TEXT,
    first_seen      INTEGER,
    added_at        INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    active          INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS wt_dust_observations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    dust_wallet     TEXT    NOT NULL,
    recipient_wallet TEXT   NOT NULL,
    signature       TEXT    NOT NULL UNIQUE,
    slot            INTEGER,
    block_time      INTEGER,
    amount_lamports INTEGER NOT NULL,
    amount_sol      REAL    NOT NULL,
    observed_at     REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wt_dust_obs_recipient
    ON wt_dust_observations(recipient_wallet);
CREATE INDEX IF NOT EXISTS idx_wt_dust_obs_dust_wallet
    ON wt_dust_observations(dust_wallet);
CREATE INDEX IF NOT EXISTS idx_wt_dust_obs_observed_at
    ON wt_dust_observations(observed_at DESC);

CREATE TABLE IF NOT EXISTS wt_dust_recipient_lifecycle (
    recipient_wallet        TEXT PRIMARY KEY,
    first_dust_at           INTEGER,
    first_dust_wallet       TEXT,
    dust_count              INTEGER NOT NULL DEFAULT 0,

    -- lifecycle timestamps (NULL until the event is observed)
    first_treasury_funded_at    INTEGER,
    first_cdc_detected_at       INTEGER,
    first_subprov_detected_at   INTEGER,
    first_creator_funded_at     INTEGER,
    first_treasury_mesh_at      INTEGER,

    -- derived lag metrics (hours, NULL until both endpoints exist)
    hours_dust_to_treasury      REAL,
    hours_dust_to_cdc           REAL,
    hours_dust_to_subprov       REAL,
    hours_dust_to_creator       REAL,

    -- current classification
    current_role        TEXT NOT NULL DEFAULT 'UNKNOWN',
    role_confidence     TEXT NOT NULL DEFAULT 'NONE',
    role_evidence_json  TEXT,

    last_enriched_at    INTEGER,
    created_at          INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    updated_at          INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_wt_dust_lifecycle_role
    ON wt_dust_recipient_lifecycle(current_role);
CREATE INDEX IF NOT EXISTS idx_wt_dust_lifecycle_first_dust
    ON wt_dust_recipient_lifecycle(first_dust_at);

-- Pending sig queue (survives process restart)
CREATE TABLE IF NOT EXISTS wt_dust_pending_sigs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    dust_wallet TEXT    NOT NULL,
    signature   TEXT    NOT NULL UNIQUE,
    queued_at   REAL    NOT NULL DEFAULT (unixepoch('now','subsec'))
);
"""


def ensure_schema(conn) -> None:
    for stmt in _SCHEMA_SQL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            try:
                conn.execute(stmt)
            except Exception:
                pass
    conn.commit()


# ── Seed / load dust marker wallets ───────────────────────────────────────

def load_dust_markers(conn) -> List[str]:
    """Return the active DUST_MARKER wallet list from DB. Seed from SEED_DUST_WALLETS if empty."""
    existing = {r[0] for r in conn.execute(
        "SELECT wallet FROM wt_dust_markers WHERE active=1").fetchall()}
    if not existing:
        now = int(time.time())
        for w in SEED_DUST_WALLETS:
            conn.execute(
                "INSERT OR IGNORE INTO wt_dust_markers (wallet, label, first_seen, added_at) "
                "VALUES (?, ?, ?, ?)",
                (w, "seed", now, now))
        conn.commit()
        existing = set(SEED_DUST_WALLETS)
    return sorted(existing)


def add_dust_marker(conn, wallet: str, label: str = "", associated_treasury: str | None = None) -> bool:
    """Add a new dust marker wallet. Returns True if newly added."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO wt_dust_markers (wallet, label, associated_treasury, first_seen, added_at) "
        "VALUES (?, ?, ?, strftime('%s','now'), strftime('%s','now'))",
        (wallet, label, associated_treasury))
    conn.commit()
    return cur.rowcount > 0


# ── Write queue (WS callback → writer thread) ─────────────────────────────

_write_queue: queue.Queue = queue.Queue(maxsize=_WRITE_QUEUE_MAXSIZE)


def enqueue_sig(dust_wallet: str, sig: str) -> None:
    """Called from the WS callback (asyncio thread). Non-blocking. Drops on full queue."""
    try:
        _write_queue.put_nowait((dust_wallet, sig))
    except queue.Full:
        pass


# ── RPC helper (used only in the writer thread, off the WS hot path) ──────

def _rpc_get_transaction(sig: str) -> Optional[dict]:
    import urllib.request, urllib.error
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1,
        "method": "getTransaction",
        "params": [sig, {"encoding": "jsonParsed",
                         "maxSupportedTransactionVersion": 0,
                         "commitment": "confirmed"}]
    }).encode()
    try:
        req = urllib.request.Request(
            RPC_URL, data=payload,
            headers={"Content-Type": "application/json"},
            method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
            return body.get("result")
    except Exception as e:
        log.debug(f"[DustObs] getTransaction {sig[:16]}… failed: {e}")
        return None


def _extract_dust_recipients(tx: dict, dust_wallet: str) -> List[dict]:
    """Parse a getTransaction result and return a list of dust-range recipients."""
    if not tx:
        return []
    meta = tx.get("meta") or {}
    msg  = (tx.get("transaction") or {}).get("message") or {}
    keys = [
        k.get("pubkey") if isinstance(k, dict) else k
        for k in (msg.get("accountKeys") or [])
    ]
    pre  = meta.get("preBalances") or []
    post = meta.get("postBalances") or []
    slot = tx.get("slot")
    btime = meta.get("blockTime") or tx.get("blockTime")

    try:
        sender_idx = keys.index(dust_wallet)
    except ValueError:
        return []

    # Confirm the dust wallet actually sent something
    if sender_idx >= len(pre) or sender_idx >= len(post):
        return []
    if post[sender_idx] >= pre[sender_idx]:
        return []  # net receiver in this tx — skip

    results = []
    for i, w in enumerate(keys):
        if i == sender_idx or not w:
            continue
        if i >= len(pre) or i >= len(post):
            continue
        gain = post[i] - pre[i]
        if DUST_MIN_LAM <= gain <= DUST_MAX_LAM:
            results.append({
                "recipient_wallet": w,
                "slot": slot,
                "block_time": btime,
                "amount_lamports": gain,
                "amount_sol": round(gain / 1e9, 9),
            })
    return results


def _persist_observation(conn, dust_wallet: str, sig: str, recipient: dict,
                         observed_at: float) -> bool:
    """INSERT OR IGNORE one observation row. Returns True if newly written."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO wt_dust_observations "
        "(dust_wallet, recipient_wallet, signature, slot, block_time, "
        " amount_lamports, amount_sol, observed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (dust_wallet, recipient["recipient_wallet"], sig,
         recipient.get("slot"), recipient.get("block_time"),
         recipient["amount_lamports"], recipient["amount_sol"],
         observed_at))
    return cur.rowcount > 0


def _upsert_lifecycle_on_observation(conn, recipient: str, dust_wallet: str,
                                     observed_at: float) -> None:
    """Maintain the lifecycle row for a recipient on every new observation."""
    obs_ts = int(observed_at)
    conn.execute(
        """INSERT INTO wt_dust_recipient_lifecycle
               (recipient_wallet, first_dust_at, first_dust_wallet, dust_count, updated_at)
           VALUES (?, ?, ?, 1, ?)
           ON CONFLICT(recipient_wallet) DO UPDATE SET
               first_dust_at    = MIN(first_dust_at, excluded.first_dust_at),
               first_dust_wallet = CASE WHEN first_dust_at <= excluded.first_dust_at
                                        THEN first_dust_wallet ELSE excluded.first_dust_wallet END,
               dust_count       = dust_count + 1,
               updated_at       = excluded.updated_at
        """,
        (recipient, obs_ts, dust_wallet, obs_ts))


# ── Writer thread ──────────────────────────────────────────────────────────

def _writer_loop() -> None:
    """Drain the write queue: fetch tx via RPC, persist observations. Runs in a daemon thread."""
    log.info("[DustObs] writer thread started")
    while True:
        try:
            dust_wallet, sig = _write_queue.get(timeout=5)
        except queue.Empty:
            continue
        try:
            observed_at = time.time()
            tx = _rpc_get_transaction(sig)
            if not tx:
                continue
            recipients = _extract_dust_recipients(tx, dust_wallet)
            if not recipients:
                continue
            conn = db_connect(OPS_DB_PATH, timeout=30)
            try:
                ensure_schema(conn)
                for r in recipients:
                    newly = _persist_observation(conn, dust_wallet, sig, r, observed_at)
                    if newly:
                        _upsert_lifecycle_on_observation(
                            conn, r["recipient_wallet"], dust_wallet, observed_at)
                # Remove from pending-sigs table if it was there (restart recovery)
                conn.execute("DELETE FROM wt_dust_pending_sigs WHERE signature=?", (sig,))
                conn.commit()
                if recipients:
                    log.info(f"[DustObs] {dust_wallet[:12]}… → {len(recipients)} dust recipient(s) sig={sig[:16]}…")
            finally:
                conn.close()
        except Exception as e:
            log.warning(f"[DustObs] writer error sig={sig[:16]}…: {e}")


def start_writer_thread() -> threading.Thread:
    t = threading.Thread(target=_writer_loop, daemon=True, name="dust-obs-writer")
    t.start()
    return t


# ── Enricher loop ──────────────────────────────────────────────────────────
# Classifies recipients using existing WATCHTOWER tables. No RPC.

def _classify_recipient_no_rpc(conn, recipient: str) -> tuple[str, str, dict]:
    """Return (role, confidence, evidence_dict) using only existing WATCHTOWER tables."""
    evidence: dict = {}

    # 1. Confirmed treasury?
    row = conn.execute(
        "SELECT treasury, confidence FROM wt_confirmed_treasuries WHERE treasury=?",
        (recipient,)).fetchone()
    if row:
        return "TREASURY", row[1] or "STRICT", {"treasury": row[0]}

    # 2. Known subprov?
    row = conn.execute(
        "SELECT subprov, subprov_type, wrap_close_count, buy_swarm_count, create_count, "
        "       buy_swarm_ratio, state "
        "FROM wt_discovered_subprovs WHERE subprov=?", (recipient,)).fetchone()
    if row:
        evidence = {"subprov_type": row[1], "wrap_close_count": row[2],
                    "buy_swarm_count": row[3], "create_count": row[4], "state": row[6]}
        bsr = row[5] or 0.0
        n_obs = (row[3] or 0) + (row[4] or 0)
        if row[1] == "BUY_SWARM_PROVISIONER" or (bsr > 0.7 and n_obs >= 10):
            return "BUY_SWARM", "HIGH", evidence
        if (row[2] or 0) >= 1:
            return "SUBPROV", "HIGH", evidence
        return "PROVISION_CANDIDATE", "MEDIUM", evidence

    # 3. CDC candidate?
    row = conn.execute(
        "SELECT wallet, derived_role, role_confidence FROM wt_capital_distributor_candidates "
        "WHERE wallet=?", (recipient,)).fetchone()
    if row:
        evidence = {"derived_role": row[1]}
        return "CAPITAL_RELAY", row[2] or "MEDIUM", evidence

    # 4. Known creator? (wt_watchtower_launches.creator_wallet)
    row = conn.execute(
        "SELECT creator_wallet, subprov_wallet FROM wt_watchtower_launches "
        "WHERE creator_wallet=? LIMIT 1", (recipient,)).fetchone()
    if row:
        evidence = {"subprov": row[1]}
        return "CREATOR", "HIGH", evidence

    # 5. Known launch subprov? (appears as subprov in launches)
    row = conn.execute(
        "SELECT subprov_wallet, COUNT(*) as c FROM wt_watchtower_launches "
        "WHERE subprov_wallet=? GROUP BY subprov_wallet", (recipient,)).fetchone()
    if row and row[1] >= 1:
        evidence = {"launch_count": row[1]}
        return "SUBPROV", "HIGH", evidence

    return "UNKNOWN", "NONE", {}


def _enrich_lifecycle_timestamps(conn, recipient: str, role: str, now: int) -> dict:
    """Update the lifecycle milestone timestamps based on newly confirmed role."""
    updates: dict = {}
    if role == "TREASURY":
        updates["first_treasury_funded_at"] = now
    elif role == "CAPITAL_RELAY":
        updates["first_cdc_detected_at"] = now
    elif role in ("SUBPROV", "BUY_SWARM", "PROVISION_CANDIDATE"):
        updates["first_subprov_detected_at"] = now
    elif role == "CREATOR":
        updates["first_creator_funded_at"] = now
    elif role == "TREASURY_MESH":
        updates["first_treasury_mesh_at"] = now
    return updates


def _compute_lags(first_dust_at: Optional[int], milestone_ts: Optional[int]) -> Optional[float]:
    if first_dust_at and milestone_ts and milestone_ts > first_dust_at:
        return round((milestone_ts - first_dust_at) / 3600, 2)
    return None


def _enrich_batch(conn, batch_size: int = 200) -> int:
    """Classify unclassified / stale recipients. Returns count enriched."""
    cutoff = int(time.time()) - ENRICH_INTERVAL_S * 3
    rows = conn.execute(
        """SELECT recipient_wallet, current_role, first_dust_at,
                  first_treasury_funded_at, first_cdc_detected_at,
                  first_subprov_detected_at, first_creator_funded_at
           FROM wt_dust_recipient_lifecycle
           WHERE current_role = 'UNKNOWN'
              OR last_enriched_at IS NULL
              OR last_enriched_at < ?
           ORDER BY first_dust_at ASC
           LIMIT ?""",
        (cutoff, batch_size)).fetchall()

    count = 0
    now = int(time.time())
    for row in rows:
        recipient = row[0]
        old_role = row[1]
        first_dust_at = row[2]

        role, confidence, evidence = _classify_recipient_no_rpc(conn, recipient)

        # Compute lag hours for newly-discovered milestones
        milestone_updates = {}
        lag_updates = {}

        if role != old_role and role != "UNKNOWN":
            milestone_updates = _enrich_lifecycle_timestamps(conn, recipient, role, now)

        # Recalculate all lags from current DB state (merge with any new milestones)
        treas_ts = row[3] or milestone_updates.get("first_treasury_funded_at")
        cdc_ts   = row[4] or milestone_updates.get("first_cdc_detected_at")
        sub_ts   = row[5] or milestone_updates.get("first_subprov_detected_at")
        cre_ts   = row[6] or milestone_updates.get("first_creator_funded_at")

        lag_updates = {
            "hours_dust_to_treasury": _compute_lags(first_dust_at, treas_ts),
            "hours_dust_to_cdc":      _compute_lags(first_dust_at, cdc_ts),
            "hours_dust_to_subprov":  _compute_lags(first_dust_at, sub_ts),
            "hours_dust_to_creator":  _compute_lags(first_dust_at, cre_ts),
        }

        # Build SET clause dynamically (only update non-None values)
        set_parts = [
            "current_role = ?",
            "role_confidence = ?",
            "role_evidence_json = ?",
            "last_enriched_at = ?",
            "updated_at = ?",
        ]
        params = [role, confidence, json.dumps(evidence), now, now]

        for col, val in {**milestone_updates, **lag_updates}.items():
            if val is not None:
                set_parts.append(f"{col} = COALESCE({col}, ?)")
                params.append(val)

        params.append(recipient)
        conn.execute(
            f"UPDATE wt_dust_recipient_lifecycle SET {', '.join(set_parts)} WHERE recipient_wallet=?",
            params)
        count += 1

    if count:
        conn.commit()
    return count


def run_enricher_once() -> int:
    """Run one enrichment pass. Returns count enriched. Called by the enricher loop."""
    conn = db_connect(OPS_DB_PATH, timeout=30)
    try:
        ensure_schema(conn)
        return _enrich_batch(conn)
    finally:
        conn.close()


def enricher_loop() -> None:
    """Standalone enricher loop. Runs as a separate process or thread."""
    log.info(f"[DustObs] enricher started (interval={ENRICH_INTERVAL_S}s)")
    while True:
        try:
            n = run_enricher_once()
            if n:
                log.info(f"[DustObs] enriched {n} recipient(s)")
        except Exception as e:
            log.warning(f"[DustObs] enricher error: {e}")
        time.sleep(ENRICH_INTERVAL_S)


# ── Stats / dashboard queries ──────────────────────────────────────────────

def _ops(timeout: int = 10) -> sqlite3.Connection:
    c = db_connect(OPS_DB_PATH, timeout=timeout)
    c.row_factory = sqlite3.Row
    return c


def get_dust_marker_summary() -> List[dict]:
    """Return one row per dust marker wallet with activity stats."""
    conn = _ops()
    try:
        ensure_schema(conn)
        markers = conn.execute(
            "SELECT wallet, label, associated_treasury, first_seen, active "
            "FROM wt_dust_markers ORDER BY first_seen ASC"
        ).fetchall()
        results = []
        now = int(time.time())
        day_ago = now - 86400
        for m in markers:
            wallet = m["wallet"]
            stats = conn.execute(
                """SELECT
                     COUNT(*)                          AS total_obs,
                     COUNT(DISTINCT recipient_wallet)  AS unique_recipients,
                     MIN(observed_at)                  AS first_seen_obs,
                     MAX(observed_at)                  AS last_seen_obs,
                     SUM(CASE WHEN observed_at >= ? THEN 1 ELSE 0 END) AS today_count
                   FROM wt_dust_observations
                   WHERE dust_wallet = ?""",
                (day_ago, wallet)).fetchone()

            role_dist = conn.execute(
                """SELECT rl.current_role, COUNT(*) AS cnt
                   FROM wt_dust_observations o
                   JOIN wt_dust_recipient_lifecycle rl
                     ON rl.recipient_wallet = o.recipient_wallet
                   WHERE o.dust_wallet = ?
                   GROUP BY rl.current_role
                   ORDER BY cnt DESC""",
                (wallet,)).fetchall()

            active_recipients = conn.execute(
                """SELECT COUNT(DISTINCT recipient_wallet) FROM wt_dust_observations
                   WHERE dust_wallet = ? AND observed_at >= ?""",
                (wallet, day_ago * 1.0)).fetchone()[0]

            results.append({
                "wallet":               wallet,
                "wallet_short":         wallet[:8] + "…",
                "label":                m["label"] or "",
                "associated_treasury":  m["associated_treasury"],
                "active":               bool(m["active"]),
                "total_observations":   stats["total_obs"] or 0,
                "unique_recipients":    stats["unique_recipients"] or 0,
                "active_recipients_24h": active_recipients or 0,
                "first_seen":           stats["first_seen_obs"],
                "last_seen":            stats["last_seen_obs"],
                "today_count":          stats["today_count"] or 0,
                "role_distribution":    [
                    {"role": r["current_role"], "count": r["cnt"]}
                    for r in role_dist
                ],
            })
        return results
    finally:
        conn.close()


def get_role_transition_stats() -> dict:
    """Aggregate transition counts and lag statistics across all recipients."""
    conn = _ops()
    try:
        ensure_schema(conn)
        total = conn.execute(
            "SELECT COUNT(*) FROM wt_dust_recipient_lifecycle").fetchone()[0]

        role_counts = conn.execute(
            "SELECT current_role, COUNT(*) AS cnt FROM wt_dust_recipient_lifecycle "
            "GROUP BY current_role ORDER BY cnt DESC").fetchall()

        # Lag stats (only where both endpoints present)
        lag_stats = conn.execute(
            """SELECT
                 AVG(hours_dust_to_treasury) AS avg_h_treasury,
                 AVG(hours_dust_to_cdc)      AS avg_h_cdc,
                 AVG(hours_dust_to_subprov)  AS avg_h_subprov,
                 AVG(hours_dust_to_creator)  AS avg_h_creator,
                 COUNT(hours_dust_to_treasury) AS n_treasury,
                 COUNT(hours_dust_to_cdc)      AS n_cdc,
                 COUNT(hours_dust_to_subprov)  AS n_subprov,
                 COUNT(hours_dust_to_creator)  AS n_creator
               FROM wt_dust_recipient_lifecycle
               WHERE current_role != 'UNKNOWN'""").fetchone()

        # Per-dust-wallet specialisation
        specialisation = conn.execute(
            """SELECT o.dust_wallet,
                      rl.current_role,
                      COUNT(*) AS cnt,
                      CAST(COUNT(*) AS REAL) /
                        (SELECT COUNT(*) FROM wt_dust_observations o2
                         WHERE o2.dust_wallet = o.dust_wallet) AS pct
               FROM wt_dust_observations o
               JOIN wt_dust_recipient_lifecycle rl ON rl.recipient_wallet = o.recipient_wallet
               GROUP BY o.dust_wallet, rl.current_role
               ORDER BY o.dust_wallet, cnt DESC""").fetchall()

        spec_by_wallet: dict = {}
        for r in specialisation:
            w = r["dust_wallet"][:8] + "…"
            spec_by_wallet.setdefault(w, []).append({
                "role": r["current_role"],
                "count": r["cnt"],
                "pct": round((r["pct"] or 0) * 100, 1),
            })

        return {
            "total_recipients": total,
            "role_counts": {r["current_role"]: r["cnt"] for r in role_counts},
            "lag_hours": {
                "dust_to_treasury": {
                    "avg": round(lag_stats["avg_h_treasury"] or 0, 1),
                    "n":   lag_stats["n_treasury"]},
                "dust_to_cdc": {
                    "avg": round(lag_stats["avg_h_cdc"] or 0, 1),
                    "n":   lag_stats["n_cdc"]},
                "dust_to_subprov": {
                    "avg": round(lag_stats["avg_h_subprov"] or 0, 1),
                    "n":   lag_stats["n_subprov"]},
                "dust_to_creator": {
                    "avg": round(lag_stats["avg_h_creator"] or 0, 1),
                    "n":   lag_stats["n_creator"]},
            },
            "specialisation_by_wallet": spec_by_wallet,
        }
    finally:
        conn.close()


def get_recipient_lifecycle(wallet: str) -> Optional[dict]:
    """Return the full lifecycle record for one recipient wallet."""
    conn = _ops()
    try:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT * FROM wt_dust_recipient_lifecycle WHERE recipient_wallet=?",
            (wallet,)).fetchone()
        if not row:
            return None
        result = dict(row)
        # Attach observation history (last 50)
        obs = conn.execute(
            """SELECT dust_wallet, signature, slot, block_time, amount_lamports, amount_sol, observed_at
               FROM wt_dust_observations WHERE recipient_wallet=?
               ORDER BY observed_at DESC LIMIT 50""",
            (wallet,)).fetchall()
        result["observations"] = [dict(o) for o in obs]
        if result.get("role_evidence_json"):
            try:
                result["role_evidence"] = json.loads(result["role_evidence_json"])
            except Exception:
                result["role_evidence"] = {}
        return result
    finally:
        conn.close()


def get_all_recipients() -> List[dict]:
    """Return all recipient lifecycle rows ordered by first_dust_at desc."""
    conn = _ops()
    try:
        ensure_schema(conn)
        rows = conn.execute(
            """SELECT recipient_wallet, first_dust_wallet, dust_count, current_role,
                      role_confidence, first_dust_at, first_treasury_funded_at,
                      first_subprov_detected_at, hours_dust_to_subprov, hours_dust_to_treasury
               FROM wt_dust_recipient_lifecycle
               ORDER BY first_dust_at DESC"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_intelligence_summary() -> dict:
    """Answer the long-term intelligence questions."""
    conn = _ops()
    try:
        ensure_schema(conn)
        total_obs = conn.execute("SELECT COUNT(*) FROM wt_dust_observations").fetchone()[0]
        total_recipients = conn.execute(
            "SELECT COUNT(*) FROM wt_dust_recipient_lifecycle").fetchone()[0]
        classified = conn.execute(
            "SELECT COUNT(*) FROM wt_dust_recipient_lifecycle "
            "WHERE current_role != 'UNKNOWN'").fetchone()[0]

        # What % of dusted wallets become each role?
        pct_by_role = conn.execute(
            """SELECT current_role,
                      COUNT(*) AS cnt,
                      ROUND(100.0 * COUNT(*) / MAX(1, (SELECT COUNT(*) FROM wt_dust_recipient_lifecycle)), 1) AS pct
               FROM wt_dust_recipient_lifecycle
               GROUP BY current_role ORDER BY cnt DESC""").fetchall()

        # New roles in the last 7 days
        week_ago = int(time.time()) - 7 * 86400
        new_roles_7d = conn.execute(
            """SELECT current_role, COUNT(*) AS cnt
               FROM wt_dust_recipient_lifecycle
               WHERE updated_at >= ? AND current_role != 'UNKNOWN'
               GROUP BY current_role ORDER BY cnt DESC""",
            (week_ago,)).fetchall()

        return {
            "total_observations": total_obs,
            "total_recipients":   total_recipients,
            "classified_pct":     round(100.0 * classified / max(1, total_recipients), 1),
            "role_breakdown": [
                {"role": r["current_role"], "count": r["cnt"], "pct": r["pct"]}
                for r in pct_by_role
            ],
            "new_roles_7d": [
                {"role": r["current_role"], "count": r["cnt"]}
                for r in new_roles_7d
            ],
        }
    finally:
        conn.close()


# ── Startup / init ─────────────────────────────────────────────────────────

_writer_thread: Optional[threading.Thread] = None
_enricher_thread: Optional[threading.Thread] = None


def init(start_enricher: bool = True) -> List[str]:
    """Initialise schema, seed dust markers, start writer thread.
    Returns the list of active dust marker wallets to subscribe."""
    conn = db_connect(OPS_DB_PATH, timeout=30)
    try:
        ensure_schema(conn)
        markers = load_dust_markers(conn)
    finally:
        conn.close()

    global _writer_thread, _enricher_thread
    if _writer_thread is None or not _writer_thread.is_alive():
        _writer_thread = start_writer_thread()

    if start_enricher and (_enricher_thread is None or not _enricher_thread.is_alive()):
        _enricher_thread = threading.Thread(
            target=enricher_loop, daemon=True, name="dust-obs-enricher")
        _enricher_thread.start()

    log.info(f"[DustObs] initialised — {len(markers)} dust marker wallet(s)")
    return markers


# ── CLI entry point (standalone enricher process) ─────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s",
                        stream=sys.stdout)
    log.info("[DustObs] standalone enricher process starting")
    conn0 = db_connect(OPS_DB_PATH, timeout=30)
    ensure_schema(conn0)
    conn0.close()
    enricher_loop()
