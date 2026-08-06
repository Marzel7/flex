"""
wt_walkback_queue — lineage-completeness-driven attribution gap resolver.

Migration is the TRIGGER, not the job. On every migration we classify:

  LINK_ONLY        creator+subprov+treasury already known → zero RPC, just link
  PARTIAL_TREASURY subprov known, treasury missing       → 1-hop RPC on subprov
  PARTIAL_SUBPROV  creator known, subprov missing        → 1-hop RPC on creator
  FULL_WALKBACK    creator unknown or not in any table   → full 3-hop RPC
  SKIP             CEX/relay funded, not WATCHTOWER       → no RPC, mark done

Classification is done entirely from the ops DB (zero RPC). Only PARTIAL_* and
FULL_WALKBACK rows consume the RPC budget. LINK_ONLY rows update linkage and
close immediately. As WATCHTOWER coverage grows, the queue should trend toward
LINK_ONLY and the RPC budget should shrink.
"""
from __future__ import annotations

import os
import sqlite3
import time
from typing import Optional

OPS_DB_PATH = os.environ.get(
    "WT_OPS_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "database", "wt_ops_v2.db"),
)

LIVE_DB_PATH = os.environ.get(
    "FLEX_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "database", "flex_complete_database.db"),
)

WALKBACK_CLASSES = (
    "LINK_ONLY",             # full lineage known — zero RPC
    "LINK_ONLY_GRAPH",       # treasury resolved via op graph treasury_root — zero RPC
    "PARTIAL_TREASURY",      # subprov known, genuine treasury gap — 1-hop RPC
    "PARTIAL_SUBPROV",       # creator known, subprov missing — 1-hop RPC
    "FULL_WALKBACK",         # creator unknown — full 3-hop RPC
    "SKIP",                  # CEX/relay funded, not WATCHTOWER
    "OP_GRAPH_ROLE_MISMATCH",  # treasury_root set but role≠TREASURY and wallet≠treasury_root
    "SELF_ROOTED_OPERATION",   # treasury_root == subprov — no upstream treasury
)
STATUSES = ("pending", "running", "complete", "skipped", "failed")


def _connect(path: str, readonly: bool = False) -> sqlite3.Connection:
    uri = f"file:{path}?mode=ro" if readonly else path
    conn = sqlite3.connect(uri, uri=readonly, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


INTELLIGENCE_OUTCOMES = (
    "WATCHTOWER_CONFIRMED",  # full lineage known — creator→subprov→treasury
    "TREASURY_CANDIDATE",    # recurring unknown funder — manual review/promotion only
    "LINEAGE_GAP",           # subprov found, treasury still missing after RPC
    "GRAPH_QUALITY_ISSUE",   # op graph inconsistency (role mismatch or self-rooted)
    "NON_WATCHTOWER",        # CEX/relay funded, not in the operation
    "NO_ATTRIBUTION_FOUND",  # full walkback complete, nothing found
    # NULL = not yet processed (pending)
)

# walkback_class → intelligence_outcome for zero-RPC completions
_ZERO_RPC_OUTCOMES: dict[str, str] = {
    "LINK_ONLY":              "WATCHTOWER_CONFIRMED",
    "LINK_ONLY_GRAPH":        "WATCHTOWER_CONFIRMED",
    "SKIP":                   "NON_WATCHTOWER",
    "OP_GRAPH_ROLE_MISMATCH": "GRAPH_QUALITY_ISSUE",
    "SELF_ROOTED_OPERATION":  "GRAPH_QUALITY_ISSUE",
    # PARTIAL_* and FULL_WALKBACK: outcome written by RPC worker after investigation
}


def ensure_schema(conn: sqlite3.Connection) -> None:
    # Step 1: migrate existing tables before CREATE TABLE so new columns exist for the index.
    # X77.3 fix: check PRAGMA table_info first rather than attempting the ALTER TABLE and
    # swallowing a "duplicate column" failure -- on every restart after the table already has
    # these columns (i.e. every restart after the very first one ever), the old code's
    # conn.execute() acquired this connection's write lease (TrackedConnection's
    # _acquire_write_lane fires on any write statement, success or not), the ALTER TABLE then
    # failed, and the except-pass swallowed it WITHOUT reaching conn.commit() -- the only
    # place that releases the lease. The leaked lease then blocked this same connection's next
    # real write, which manifested as a permanent NestedDatabaseWriteError self-nesting loop on
    # every subsequent restart (observed live during the X77.3 soak). Checking existing columns
    # first means a fully-migrated table issues zero ALTER TABLE statements, so nothing ever
    # acquires a lease it might fail to release.
    def _table_exists(table: str) -> bool:
        try:
            return conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone() is not None
        except Exception:
            return False

    if _table_exists("wt_walkback_queue"):
        existing = {r[1] for r in conn.execute("PRAGMA table_info(wt_walkback_queue)").fetchall()}
        for col, typedef in [
            ("intelligence_outcome", "TEXT"),
            ("funder_wallet",        "TEXT"),
            ("funding_mechanism",    "TEXT"),
            ("funder_amount_sol",    "REAL"),
            ("funder_sig",           "TEXT"),
            ("funder_slot",          "INTEGER"),
            ("funder_block_time",    "INTEGER"),
        ]:
            if col in existing:
                continue
            try:
                conn.execute(f"ALTER TABLE wt_walkback_queue ADD COLUMN {col} {typedef}")
            except Exception:
                continue
            conn.commit()
    # else: table doesn't exist yet -- Step 2's CREATE TABLE IF NOT EXISTS below
    # creates it with every column already present, so there is nothing to migrate.

    if _table_exists("wt_discovered_subprovs"):
        existing_subprov = {r[1] for r in conn.execute("PRAGMA table_info(wt_discovered_subprovs)").fetchall()}
        for col, typedef in [
            ("discovery_source",  "TEXT"),
            ("funding_mechanism", "TEXT"),
        ]:
            if col in existing_subprov:
                continue
            try:
                conn.execute(f"ALTER TABLE wt_discovered_subprovs ADD COLUMN {col} {typedef}")
            except Exception:
                continue
            conn.commit()
    # else: wt_discovered_subprovs is owned by ws_cascade_store.ensure_cascade_schema,
    # not this module -- if it hasn't been created yet, there's nothing to migrate here
    # (and attempting an ALTER TABLE against a nonexistent table would itself leak the
    # write lease exactly like the bug this fix addresses, so this must stay guarded).

    # Step 2: CREATE TABLE (no-op if already exists) + indexes
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS wt_walkback_queue (
            mint                TEXT NOT NULL,
            creator             TEXT,
            subprov             TEXT,
            treasury            TEXT,
            walkback_class      TEXT NOT NULL DEFAULT 'FULL_WALKBACK',
            attribution_source  TEXT,
            intelligence_outcome TEXT,
            status              TEXT NOT NULL DEFAULT 'pending',
            rpc_used            INTEGER NOT NULL DEFAULT 0,
            attempts            INTEGER NOT NULL DEFAULT 0,
            last_error          TEXT,
            enqueued_at         INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            started_at          INTEGER,
            completed_at        INTEGER,
            updated_at          INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            funder_wallet       TEXT,
            funding_mechanism   TEXT,
            funder_amount_sol   REAL,
            funder_sig          TEXT,
            funder_slot         INTEGER,
            funder_block_time   INTEGER,
            PRIMARY KEY (mint)
        );

        CREATE INDEX IF NOT EXISTS ix_wbq_status
            ON wt_walkback_queue(status, enqueued_at);

        CREATE INDEX IF NOT EXISTS ix_wbq_class
            ON wt_walkback_queue(walkback_class, status);

        CREATE INDEX IF NOT EXISTS ix_wbq_outcome
            ON wt_walkback_queue(intelligence_outcome);

        CREATE INDEX IF NOT EXISTS ix_wbq_funder
            ON wt_walkback_queue(funder_wallet);
    """)
    from src.ops.attribution_outcome import ensure_schema as ensure_outcome_schema
    ensure_outcome_schema(conn)
    from src.core.deep_walkback import ensure_schema as ensure_deep_walkback_schema
    ensure_deep_walkback_schema(conn)
    from src.ops.watchtower_candidates import ensure_schema as ensure_candidate_schema
    ensure_candidate_schema(conn)
    conn.commit()


# ── classification logic (zero RPC) ────────────────────────────────────────

def _resolve_treasury_via_op_graph(
    subprov: str, ops_conn: sqlite3.Connection
) -> tuple[Optional[str], str]:
    """
    Given a known subprov wallet, attempt to resolve treasury via wt_ops_v2_wallets.
    Returns (treasury, outcome_class) where outcome_class is one of:
      LINK_ONLY_GRAPH        — treasury_root found and role is TREASURY
      OP_GRAPH_ROLE_MISMATCH — treasury_root found but no wallet has role=TREASURY
      SELF_ROOTED_OPERATION  — treasury_root == subprov
      GENUINE_TREASURY_MISSING — no op graph entry or treasury_root is NULL
    """
    row = ops_conn.execute(
        "SELECT wov.role, op.treasury_root, op.operation_uuid "
        "FROM wt_ops_v2_wallets wov "
        "JOIN wt_ops_v2 op ON wov.operation_uuid = op.operation_uuid "
        "WHERE wov.wallet=? AND op.treasury_root IS NOT NULL LIMIT 1",
        (subprov,)).fetchone()
    if not row:
        return None, "GENUINE_TREASURY_MISSING"
    treasury_root = row["treasury_root"]
    op_uuid = row["operation_uuid"]
    if treasury_root == subprov:
        return None, "SELF_ROOTED_OPERATION"
    # Check if any wallet in the op has role=TREASURY matching treasury_root
    t_row = ops_conn.execute(
        "SELECT wallet FROM wt_ops_v2_wallets "
        "WHERE operation_uuid=? AND wallet=? AND role='TREASURY' LIMIT 1",
        (op_uuid, treasury_root)).fetchone()
    if t_row:
        return treasury_root, "LINK_ONLY_GRAPH"
    return treasury_root, "OP_GRAPH_ROLE_MISMATCH"


def classify_creator(creator: Optional[str], ops_conn: sqlite3.Connection,
                     live_conn: Optional[sqlite3.Connection] = None
                     ) -> tuple[str, Optional[str], Optional[str], Optional[str], str]:
    """
    Returns (walkback_class, creator, subprov, treasury, attribution_source).
    All lookups are zero-RPC — ops DB tables only, with optional live DB for CEX check.
    """
    if not creator:
        return "FULL_WALKBACK", None, None, None, "no_creator"

    # ── Case 0: creator in wt_ops_v2_wallets → operation → treasury ─────────
    row = ops_conn.execute(
        "SELECT wov.wallet, wov.role, op.treasury_root "
        "FROM wt_ops_v2_wallets wov "
        "JOIN wt_ops_v2 op ON wov.operation_uuid = op.operation_uuid "
        "WHERE wov.wallet=? AND op.treasury_root IS NOT NULL LIMIT 1",
        (creator,)).fetchone()
    if row and row["treasury_root"]:
        return ("LINK_ONLY", creator, None, row["treasury_root"], "wt_ops_v2_wallets")

    # ── Case 1: confirmed WATCHTOWER launch — complete lineage ──────────────
    row = ops_conn.execute(
        "SELECT creator_wallet, subprov_wallet, treasury_wallet FROM wt_watchtower_launches "
        "WHERE creator_wallet=? LIMIT 1", (creator,)).fetchone()
    if row and row["subprov_wallet"] and row["treasury_wallet"]:
        return ("LINK_ONLY", row["creator_wallet"], row["subprov_wallet"],
                row["treasury_wallet"], "wt_watchtower_launches")

    # ── Case 2: wrap-close candidate — creator+subprov known ───────────────
    row = ops_conn.execute(
        "SELECT creator, subprov_wallet, lineage_source_treasury FROM wt_wrap_close_candidates "
        "WHERE creator=? LIMIT 1", (creator,)).fetchone()
    if row and row["subprov_wallet"]:
        if row["lineage_source_treasury"]:
            return ("LINK_ONLY", row["creator"], row["subprov_wallet"],
                    row["lineage_source_treasury"], "wt_wrap_close_candidates")
        sp = row["subprov_wallet"]
        treasury, outcome = _resolve_treasury_via_op_graph(sp, ops_conn)
        if outcome == "LINK_ONLY_GRAPH":
            return (outcome, row["creator"], sp, treasury, "wt_wrap_close_candidates")
        if outcome in ("OP_GRAPH_ROLE_MISMATCH", "SELF_ROOTED_OPERATION"):
            return (outcome, row["creator"], sp, treasury, "wt_wrap_close_candidates")
        return ("PARTIAL_TREASURY", row["creator"], sp, None, "wt_wrap_close_candidates")

    # ── Case 3: birth launch record ─────────────────────────────────────────
    row = ops_conn.execute(
        "SELECT creator, subprov, treasury FROM wt_creator_birth_launch "
        "WHERE creator=? LIMIT 1", (creator,)).fetchone()
    if row:
        if row["subprov"] and row["treasury"]:
            return ("LINK_ONLY", row["creator"], row["subprov"],
                    row["treasury"], "wt_creator_birth_launch")
        if row["subprov"]:
            sp = row["subprov"]
            treasury, outcome = _resolve_treasury_via_op_graph(sp, ops_conn)
            if outcome == "LINK_ONLY_GRAPH":
                return (outcome, row["creator"], sp, treasury, "wt_creator_birth_launch")
            if outcome in ("OP_GRAPH_ROLE_MISMATCH", "SELF_ROOTED_OPERATION"):
                return (outcome, row["creator"], sp, treasury, "wt_creator_birth_launch")
            return ("PARTIAL_TREASURY", row["creator"], sp, None, "wt_creator_birth_launch")

    # ── Case 4: creator was a websocket candidate — subprov is recorded ─────
    row = ops_conn.execute(
        "SELECT candidate_wallet, subprov_wallet FROM wt_candidate_websocket_watches "
        "WHERE candidate_wallet=? LIMIT 1", (creator,)).fetchone()
    if row and row["subprov_wallet"]:
        sp = row["subprov_wallet"]
        # Try discovered_subprovs first, then op graph
        t_row = ops_conn.execute(
            "SELECT treasury FROM wt_discovered_subprovs WHERE subprov=? LIMIT 1",
            (sp,)).fetchone()
        treasury = t_row["treasury"] if t_row else None
        if treasury:
            return ("LINK_ONLY", creator, sp, treasury, "wt_candidate_websocket_watches")
        treasury, outcome = _resolve_treasury_via_op_graph(sp, ops_conn)
        if outcome == "LINK_ONLY_GRAPH":
            return (outcome, creator, sp, treasury, "wt_candidate_websocket_watches")
        if outcome in ("OP_GRAPH_ROLE_MISMATCH", "SELF_ROOTED_OPERATION"):
            return (outcome, creator, sp, treasury, "wt_candidate_websocket_watches")
        return ("PARTIAL_TREASURY", creator, sp, None, "wt_candidate_websocket_watches")

    # ── Case 5: token_attribution score — may have matched_subprov ─────────
    row = ops_conn.execute(
        "SELECT creator, matched_subprov, matched_treasury FROM watchtower_token_attribution "
        "WHERE creator=? LIMIT 1", (creator,)).fetchone()
    if row:
        if row["matched_subprov"] and row["matched_treasury"]:
            return ("LINK_ONLY", creator, row["matched_subprov"],
                    row["matched_treasury"], "watchtower_token_attribution")
        if row["matched_subprov"]:
            sp = row["matched_subprov"]
            treasury, outcome = _resolve_treasury_via_op_graph(sp, ops_conn)
            if outcome == "LINK_ONLY_GRAPH":
                return (outcome, creator, sp, treasury, "watchtower_token_attribution")
            if outcome in ("OP_GRAPH_ROLE_MISMATCH", "SELF_ROOTED_OPERATION"):
                return (outcome, creator, sp, treasury, "watchtower_token_attribution")
            return ("PARTIAL_TREASURY", creator, sp, None, "watchtower_token_attribution")

    # ── Case 6: established launcher profile ──────────────────────────
    # Repetition alone is not exclusion evidence. Canonical lineage checks above
    # always win, and fresh provisioning/infrastructure changes disqualify this stop.
    if live_conn:
        from src.ops.attribution_outcome import evaluate_launcher_profile
        if evaluate_launcher_profile(ops_conn, live_conn, creator)["established"]:
            return ("SKIP", creator, None, None, "known_multi_token_creator")

    # ── Case 7: CEX/relay check — skip if not WATCHTOWER lineage ───────────
    if live_conn:
        cex_row = live_conn.execute(
            "SELECT is_cex FROM creator_funders WHERE creator_address=? LIMIT 1",
            (creator,)).fetchone()
        if cex_row and cex_row["is_cex"]:
            return ("SKIP", creator, None, None, "cex_funded")

    # ── Case 8: completely unknown — full walkback needed ──────────────────
    return ("FULL_WALKBACK", creator, None, None, "unknown")


# ── queue entry point ────────────────────────────────────────────────────────

def enqueue_migration(conn: sqlite3.Connection, *,
                      mint: str,
                      creator: Optional[str],
                      live_conn: Optional[sqlite3.Connection] = None,
                      force: bool = False,
                      create_signature: Optional[str] = None,
                      create_slot: Optional[int] = None,
                      create_block_time: Optional[int] = None,
                      create_source: Optional[str] = None) -> Optional[str]:
    """
    Classify the migration's lineage completeness and insert into wt_walkback_queue.
    Returns the walkback_class assigned, or None if already queued and force=False.
    Idempotent on (mint) — uses INSERT OR IGNORE unless force=True.
    """
    if not force:
        existing = conn.execute(
            "SELECT walkback_class, status FROM wt_walkback_queue WHERE mint=?",
            (mint,)).fetchone()
        if existing:
            try:
                from src.ops.watchtower_candidates import evaluate_and_enqueue_candidate
                evaluate_and_enqueue_candidate(
                    conn, mint=mint, creator=creator, live_conn=live_conn,
                )
            except Exception as exc:
                print(f"[WATCHTOWER_CANDIDATE] evaluation failed mint={mint[:16]}: {exc}", flush=True)
            return existing["walkback_class"]

    cls, r_creator, r_subprov, r_treasury, src = classify_creator(creator, conn, live_conn)

    # X64.5 — every real production call site (watchtower_attribution.py's
    # store_migration, pumpfun_curve_listener.py's creator-unknown fallback)
    # calls enqueue_migration() without live_conn, so this anchor lookup was
    # unconditionally skipped and every FULL_WALKBACK row permanently
    # collapsed to MISSING_OR_MALFORMED even when creator_funding_queue held
    # a genuinely valid signature (confirmed: 308 of 350 stuck rows were
    # zero-RPC recoverable). Rather than thread live_conn through call sites
    # whose broader connection lifecycle isn't safe to change here, open a
    # short-lived read-only connection ourselves when the caller didn't
    # supply one — same pattern watchtower_candidates.py's
    # evaluate_and_enqueue_candidate() already uses for the same reason.
    _owned_live_conn = None
    if live_conn is None:
        try:
            _owned_live_conn = sqlite3.connect(f"file:{LIVE_DB_PATH}?mode=ro", uri=True, timeout=5)
            live_conn = _owned_live_conn
        except sqlite3.Error:
            live_conn = None

    if live_conn and not create_signature:
        row = live_conn.execute(
            "SELECT create_tx_signature FROM creator_funding_queue WHERE mint=? "
            "ORDER BY updated_at DESC LIMIT 1", (mint,)).fetchone()
        if row and row[0]:
            create_signature, create_source = row[0], "creator_funding_queue"
        else:
            row = live_conn.execute(
                "SELECT create_tx_signature FROM token_analysis WHERE mint=? LIMIT 1",
                (mint,)).fetchone()
            if row and row[0]:
                create_signature, create_source = row[0], "token_analysis"

    if _owned_live_conn is not None:
        _owned_live_conn.close()

    from src.core.deep_walkback import valid_signature
    anchor_valid = valid_signature(create_signature)

    now = int(time.time())
    # Zero-RPC classes resolve their outcome immediately; RPC classes stay NULL until the worker runs
    initial_outcome = _ZERO_RPC_OUTCOMES.get(cls)  # None for PARTIAL_* / FULL_WALKBACK
    initial_status = "skipped" if cls == "SKIP" else (
        "complete" if cls in ("LINK_ONLY", "LINK_ONLY_GRAPH",
                              "OP_GRAPH_ROLE_MISMATCH", "SELF_ROOTED_OPERATION") else "pending"
    )
    path_state = "CREATE_ANCHORED" if anchor_valid else (
        "WAITING_FOR_CREATE_ANCHOR" if cls == "FULL_WALKBACK" else "QUEUED")
    if cls == "FULL_WALKBACK" and not anchor_valid:
        initial_status = "waiting"

    if force:
        conn.execute("DELETE FROM wt_walkback_queue WHERE mint=?", (mint,))

    conn.execute(
        """
        INSERT OR IGNORE INTO wt_walkback_queue
            (mint, creator, subprov, treasury, walkback_class, attribution_source,
             intelligence_outcome, status, rpc_used, enqueued_at, updated_at,
             create_anchor_signature,create_anchor_slot,create_anchor_block_time,
             create_anchor_source,create_anchor_audit_state,path_state)
        VALUES (?,?,?,?,?,?,?,?,0,?,?,?,?,?,?,?,?)
        """,
        (mint, r_creator, r_subprov, r_treasury, cls, src,
         initial_outcome, initial_status, now, now, create_signature if anchor_valid else None,
         create_slot, create_block_time, create_source,
         "VALID" if anchor_valid else "MISSING_OR_MALFORMED", path_state))
    try:
        from src.ops.watchtower_candidates import evaluate_and_enqueue_candidate
        evaluate_and_enqueue_candidate(conn, mint=mint, creator=r_creator or creator, live_conn=live_conn)
    except Exception as exc:
        print(f"[WATCHTOWER_CANDIDATE] evaluation failed mint={mint[:16]}: {exc}", flush=True)
    if initial_status in {"complete", "skipped"}:
        from src.ops.attribution_outcome import materialize_outcome
        materialize_outcome(conn, mint, core_conn=live_conn)
        from src.ops.watchtower_candidates import sync_walkback_result
        sync_walkback_result(conn, mint)
    conn.commit()
    return cls


def link_only(conn: sqlite3.Connection, *, mint: str,
              creator: str, subprov: str, treasury: str,
              source: str) -> None:
    """Update migration linkage in watchtower_token_attribution without RPC."""
    now = int(time.time())
    conn.execute(
        """
        INSERT INTO watchtower_token_attribution
            (mint, creator, matched_subprov, matched_treasury, score, tier, scored_at)
        VALUES (?,?,?,?,100,'CONFIRMED',?)
        ON CONFLICT(mint) DO UPDATE SET
            creator          = excluded.creator,
            matched_subprov  = COALESCE(matched_subprov, excluded.matched_subprov),
            matched_treasury = COALESCE(matched_treasury, excluded.matched_treasury),
            scored_at        = excluded.scored_at
        """,
        (mint, creator, subprov, treasury, now))
    conn.execute(
        """
        UPDATE wt_walkback_queue
        SET status='complete', intelligence_outcome='WATCHTOWER_CONFIRMED',
            completed_at=?, updated_at=?
        WHERE mint=?
        """,
        (now, now, mint))
    from src.ops.attribution_outcome import materialize_outcome
    materialize_outcome(conn, mint)
    from src.ops.watchtower_candidates import sync_walkback_result
    sync_walkback_result(conn, mint)
    conn.commit()


# ── dashboard stats ──────────────────────────────────────────────────────────

def set_intelligence_outcome(conn: sqlite3.Connection, *, mint: str,
                             outcome: str, rpc_used: int = 0) -> None:
    """
    Called by the RPC worker after a PARTIAL_* or FULL_WALKBACK row is investigated.
    Writes the operator-facing outcome and marks the row complete.
    outcome must be one of INTELLIGENCE_OUTCOMES.
    """
    now = int(time.time())
    conn.execute(
        """
        UPDATE wt_walkback_queue
        SET intelligence_outcome=?, status='complete',
            rpc_used=rpc_used+?, completed_at=?, updated_at=?
        WHERE mint=?
        """,
        (outcome, rpc_used, now, now, mint))
    from src.ops.attribution_outcome import materialize_outcome
    materialize_outcome(conn, mint)
    conn.commit()


def queue_stats(conn: sqlite3.Connection) -> dict:
    """Return per-outcome, per-class, and per-status counts for the dashboard panel."""
    by_outcome = {}
    for row in conn.execute(
        "SELECT intelligence_outcome, COUNT(*) as n FROM wt_walkback_queue "
        "GROUP BY intelligence_outcome"
    ).fetchall():
        key = row["intelligence_outcome"] or "PENDING"
        by_outcome[key] = row["n"]

    by_class = {}
    for row in conn.execute(
        "SELECT walkback_class, COUNT(*) as n FROM wt_walkback_queue GROUP BY walkback_class"
    ).fetchall():
        by_class[row["walkback_class"]] = row["n"]

    by_status = {}
    for row in conn.execute(
        "SELECT status, COUNT(*) as n FROM wt_walkback_queue GROUP BY status"
    ).fetchall():
        by_status[row["status"]] = row["n"]

    rpc_total = (conn.execute(
        "SELECT SUM(rpc_used) FROM wt_walkback_queue").fetchone()[0] or 0)

    # trend: last 24h outcome split
    cutoff = int(time.time()) - 86400
    trend = {}
    for row in conn.execute(
        "SELECT intelligence_outcome, COUNT(*) as n FROM wt_walkback_queue "
        "WHERE enqueued_at>=? GROUP BY intelligence_outcome", (cutoff,)).fetchall():
        key = row["intelligence_outcome"] or "PENDING"
        trend[key] = row["n"]

    return {
        "by_outcome": by_outcome,
        "by_class": by_class,
        "by_status": by_status,
        "rpc_total": rpc_total,
        "trend_24h": trend,
    }
