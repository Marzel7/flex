"""
internal_api.py — Flask Blueprint for internal ingestion APIs.

All external workers POST results here instead of writing SQLite directly.
Routes enqueue writes and return immediately — never block on DB writes.

Auth: X-Internal-Token header must match INTERNAL_API_SECRET env var.
"""

import json
import os
import time
import logging

from flask import Blueprint, request, jsonify

from src.core.db_write_queue import enqueue_write, WriteItem, queue_depths
from src.core.db_writer import get_writer_stats, emit_event

logger = logging.getLogger("internal_api")

internal_bp = Blueprint("internal", __name__, url_prefix="/api/internal")

_SECRET = os.getenv("INTERNAL_API_SECRET", "flex-internal-dev")


def _auth() -> bool:
    return request.headers.get("X-Internal-Token") == _SECRET


def _unauthorized():
    return jsonify({"error": "unauthorized"}), 401


# ---------------------------------------------------------------------------
# Queue health
# ---------------------------------------------------------------------------

@internal_bp.route("/queue-health", methods=["GET"])
def queue_health():
    return jsonify({
        "queue_depths": queue_depths(),
        "writer": get_writer_stats(),
    })


@internal_bp.route("/funding-queue-stats", methods=["GET"])
def funding_queue_stats():
    """SQLite-backed creator_funding_queue counts + last worker run info."""
    import sqlite3
    import re as _re

    db_path = os.getenv("DB_PATH", os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "../../database/flex_complete_database.db"
    ))

    stats = {"pending": 0, "running": 0, "complete": 0, "failed": 0, "retry": 0,
             "complete_24h": 0, "last_worker_run": None, "last_worker_completed": 0,
             "worker_mode": "external_cron_5m"}
    try:
        conn = sqlite3.connect(db_path, timeout=3)
        conn.execute("PRAGMA query_only = ON")
        rows = conn.execute(
            "SELECT status, COUNT(*) FROM creator_funding_queue GROUP BY status"
        ).fetchall()
        for status, count in rows:
            stats[status] = count

        cutoff_24h = int(time.time()) - 86400
        stats["complete_24h"] = conn.execute(
            "SELECT COUNT(*) FROM creator_funding_queue WHERE status='complete' AND funding_extracted_at >= ?",
            (cutoff_24h,)
        ).fetchone()[0]
        conn.close()
    except Exception as e:
        stats["db_error"] = str(e)

    # Parse last run from worker log
    log_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "../../logs/creator_funding_worker.log"
    )
    try:
        with open(log_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 8192))
            tail = f.read().decode("utf-8", errors="ignore")
        lines = tail.splitlines()
        for line in reversed(lines):
            if "[FUNDING_WORKER] summary" in line:
                stats["last_worker_run"] = line.strip()
                m = _re.search(r"completed=(\d+)", line)
                if m:
                    stats["last_worker_completed"] = int(m.group(1))
                break
    except Exception:
        pass

    return jsonify(stats)


# ---------------------------------------------------------------------------
# Operators to scan (read-only — workers fetch this to know what to scan)
# ---------------------------------------------------------------------------

@internal_bp.route("/operators-to-scan", methods=["GET"])
def operators_to_scan():
    if not _auth():
        return _unauthorized()
    try:
        limit = min(int(request.args.get("limit", 20)), 100)
        from src.utils.db_locking import db_connect
        db_path = os.getenv("DB_PATH", "database/flex_complete_database.db")
        conn = db_connect(db_path, timeout=10)
        conn.execute("PRAGMA query_only = ON")
        rows = conn.execute("""
            SELECT address FROM watchtower_fee_payers
            WHERE address NOT IN (SELECT DISTINCT operator_address FROM watchtower_operator_graph)
            LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return jsonify({"operators": [r[0] for r in rows]})
    except Exception as e:
        logger.error(f"[INTERNAL_API] operators-to-scan error: {e}")
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Migration event — HIGHEST PRIORITY, 50ms flush via migration_signal
# ---------------------------------------------------------------------------

@internal_bp.route("/migration-event", methods=["POST"])
def ingest_migration_event():
    """
    Called by pumpfun_curve_listener when a token migrates.
    Uses 'migration' domain — triggers immediate writer wakeup.
    """
    if not _auth():
        return _unauthorized()
    data = request.get_json(silent=True) or {}
    mint            = data.get("mint", "")
    lifecycle_stage = data.get("lifecycle_stage", "migrated")
    migrated_at     = data.get("migrated_at") or int(time.time())
    migration_tx    = data.get("migration_tx")
    creator         = data.get("creator_address")

    if not mint:
        return jsonify({"error": "mint required"}), 400

    statements = [(
        """UPDATE token_analysis
           SET lifecycle_stage = ?,
               migrated_at     = COALESCE(migrated_at, ?),
               migration_tx    = COALESCE(migration_tx, ?)
           WHERE mint = ?""",
        (lifecycle_stage, migrated_at, migration_tx, mint)
    )]

    accepted = enqueue_write(WriteItem(
        domain="migration",   # triggers migration_signal → immediate flush
        label=f"migration:{mint[:20]}",
        statements=statements,
        idempotency_key=f"migration:{mint}:{migration_tx or migrated_at}",
    ))

    if accepted:
        emit_event(
            "token_migrated",
            wallet_address=creator,
            token_mint=mint,
            payload={"lifecycle_stage": lifecycle_stage, "migrated_at": migrated_at,
                     "migration_tx": migration_tx},
            source="curve_listener",
            domain="migration",
        )

    return jsonify({"accepted": accepted, "mint": mint})


# ---------------------------------------------------------------------------
# Operator graph — from watchtower_operator_scanner
# ---------------------------------------------------------------------------

@internal_bp.route("/operator-graph", methods=["POST"])
def ingest_operator_graph():
    if not _auth():
        return _unauthorized()
    data            = request.get_json(silent=True) or {}
    operator        = data.get("operator_address", "")
    edges           = data.get("edges", [])
    candidates      = data.get("launch_candidates", [])
    hop2_edges      = data.get("hop2_edges", [])
    hop2_candidates = data.get("hop2_candidates", [])
    scan_ts         = data.get("scan_timestamp") or int(time.time())
    hop             = data.get("hop", 1)

    if not operator:
        return jsonify({"error": "operator_address required"}), 400

    statements = []

    def _edge_stmt(op_addr, e, hop_num):
        return (
            """INSERT INTO watchtower_operator_graph
               (operator_address, child_address, relationship, amount_sol,
                first_seen_at, last_seen_at, tx_signature, hop)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(operator_address, child_address, relationship) DO UPDATE SET
                   last_seen_at = excluded.last_seen_at,
                   amount_sol   = amount_sol + excluded.amount_sol""",
            (op_addr, e["child_address"], e.get("relationship", "funded"),
             e.get("amount_sol", 0), e.get("first_seen_at", scan_ts),
             scan_ts, e.get("tx_signature"), hop_num)
        )

    def _candidate_stmt(c):
        return (
            """INSERT INTO watchtower_launch_candidates
               (address, source_operator, candidate_reason, confidence,
                first_signal_at, last_signal_at, evidence_json)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(address) DO UPDATE SET
                   last_signal_at = excluded.last_signal_at,
                   confidence = CASE WHEN excluded.confidence='high' THEN 'high'
                                     WHEN confidence='high'          THEN 'high'
                                     ELSE excluded.confidence END,
                   evidence_json = excluded.evidence_json""",
            (c["address"], c.get("source_operator", operator),
             c.get("candidate_reason"), c.get("confidence", "medium"),
             scan_ts, scan_ts, json.dumps(c.get("evidence", {})))
        )

    for e in edges:
        statements.append(_edge_stmt(operator, e, 1))
    for c in candidates:
        statements.append(_candidate_stmt(c))
    for e in hop2_edges:
        statements.append(_edge_stmt(operator, e, 2))
    for c in hop2_candidates:
        statements.append(_candidate_stmt(c))

    total_edges = len(edges) + len(hop2_edges)
    total_candidates = len(candidates) + len(hop2_candidates)

    accepted = enqueue_write(WriteItem(
        domain="scan",
        label=f"op_graph:{operator[:20]}",
        statements=statements,
        idempotency_key=f"opgraph:{operator}:{scan_ts // 60}",
    ))

    if accepted and total_edges > 0:
        emit_event(
            "graph_edge_discovered",
            wallet_address=operator,
            payload={"edges": total_edges, "candidates": total_candidates,
                     "hop": hop, "scan_ts": scan_ts},
            source="operator_scanner",
        )

    return jsonify({
        "accepted": accepted,
        "edges_queued": total_edges,
        "candidates_queued": total_candidates,
    })


# ---------------------------------------------------------------------------
# Launch candidate
# ---------------------------------------------------------------------------

@internal_bp.route("/launch-candidate", methods=["POST"])
def ingest_launch_candidate():
    if not _auth():
        return _unauthorized()
    data = request.get_json(silent=True) or {}
    address  = data.get("address", "")
    operator = data.get("source_operator", "")
    reason   = data.get("candidate_reason", "unknown")
    confidence = data.get("confidence", "medium")
    evidence = data.get("evidence", {})
    now = int(time.time())

    if not address:
        return jsonify({"error": "address required"}), 400

    accepted = enqueue_write(WriteItem(
        domain="scan",
        label=f"candidate:{address[:20]}",
        statements=[(
            """INSERT INTO watchtower_launch_candidates
               (address, source_operator, candidate_reason, confidence,
                first_signal_at, last_signal_at, evidence_json)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(address) DO UPDATE SET
                   last_signal_at = excluded.last_signal_at,
                   confidence = CASE WHEN excluded.confidence='high' THEN 'high'
                                     WHEN confidence='high'          THEN 'high'
                                     ELSE excluded.confidence END""",
            (address, operator, reason, confidence, now, now, json.dumps(evidence))
        )],
        idempotency_key=f"candidate:{address}:{now // 60}",
    ))

    if accepted:
        emit_event("launch_candidate_detected", wallet_address=address,
                   related_wallet=operator,
                   payload={"reason": reason, "confidence": confidence, "evidence": evidence},
                   source="scan")

    return jsonify({"accepted": accepted})


# ---------------------------------------------------------------------------
# Operator state transition
# ---------------------------------------------------------------------------

@internal_bp.route("/operator-state", methods=["POST"])
def ingest_operator_state():
    if not _auth():
        return _unauthorized()
    data     = request.get_json(silent=True) or {}
    address  = data.get("address", "")
    state    = data.get("state", "")
    evidence = data.get("evidence", {})
    changed_at = data.get("state_changed_at") or int(time.time())

    if not address or not state:
        return jsonify({"error": "address and state required"}), 400

    col_map = {
        "activated":   "activated_at",
        "operational": "operational_at",
        "launching":   "launching_at",
        "extracting":  "extracting_at",
        "dormant":     "dormant_at",
    }
    ts_col = col_map.get(state)

    if ts_col:
        sql = f"""
            INSERT INTO watchtower_wallet_state
               (address, state, state_changed_at, {ts_col}, evidence_json, updated_at)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(address) DO UPDATE SET
                state            = excluded.state,
                state_changed_at = excluded.state_changed_at,
                {ts_col}         = COALESCE({ts_col}, excluded.{ts_col}),
                evidence_json    = excluded.evidence_json,
                updated_at       = excluded.updated_at
        """
        params = (address, state, changed_at, changed_at,
                  json.dumps(evidence), int(time.time()))
    else:
        sql = """
            INSERT INTO watchtower_wallet_state
               (address, state, state_changed_at, evidence_json, updated_at)
            VALUES (?,?,?,?,?)
            ON CONFLICT(address) DO UPDATE SET
                state            = excluded.state,
                state_changed_at = excluded.state_changed_at,
                evidence_json    = excluded.evidence_json,
                updated_at       = excluded.updated_at
        """
        params = (address, state, changed_at, json.dumps(evidence), int(time.time()))

    accepted = enqueue_write(WriteItem(
        domain="poller",
        label=f"state:{address[:20]}:{state}",
        statements=[(sql, params)],
        idempotency_key=f"state:{address}:{state}",
    ))

    if accepted:
        emit_event("state_transition", wallet_address=address,
                   payload={"to": state, "evidence": evidence},
                   source="poller")

    return jsonify({"accepted": accepted, "state": state})


# ---------------------------------------------------------------------------
# Raydium launch
# ---------------------------------------------------------------------------

@internal_bp.route("/raydium-launch", methods=["POST"])
def ingest_raydium_launch():
    if not _auth():
        return _unauthorized()
    data = request.get_json(silent=True) or {}
    pool_address    = data.get("pool_address", "")
    mint            = data.get("mint", "")
    creator         = data.get("creator_address", "")
    pool_program    = data.get("pool_program", "")
    initial_liq     = data.get("initial_liquidity_sol", 0)
    block_time      = data.get("block_time") or int(time.time())
    tx_sig          = data.get("tx_signature", "")
    operator_link   = data.get("operator_link", "")
    link_type       = data.get("link_type", "")
    evidence        = data.get("evidence", {})

    if not pool_address or not mint:
        return jsonify({"error": "pool_address and mint required"}), 400

    accepted = enqueue_write(WriteItem(
        domain="scan",
        label=f"raydium:{pool_address[:20]}",
        statements=[(
            """INSERT INTO watchtower_raydium_launches
               (pool_address, mint, creator_address, pool_program, initial_liquidity_sol,
                detected_at, block_time, tx_signature, operator_link, link_type, evidence_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(pool_address) DO NOTHING""",
            (pool_address, mint, creator, pool_program, initial_liq,
             int(time.time()), block_time, tx_sig, operator_link, link_type,
             json.dumps(evidence))
        )],
        idempotency_key=f"raydium:{pool_address}",
    ))

    if accepted:
        emit_event("raydium_launch", wallet_address=creator, related_wallet=pool_address,
                   token_mint=mint,
                   payload={"pool_program": pool_program, "initial_liquidity_sol": initial_liq,
                            "operator_link": operator_link, "link_type": link_type},
                   source="scan")

    return jsonify({"accepted": accepted, "pool_address": pool_address})


# ---------------------------------------------------------------------------
# Fee payer event (from webhook or external forwarder)
# ---------------------------------------------------------------------------

@internal_bp.route("/fee-payer-event", methods=["POST"])
def ingest_fee_payer_event():
    if not _auth():
        return _unauthorized()
    data = request.get_json(silent=True) or {}
    transactions = data.get("transactions", [])
    queued = 0

    for tx in transactions:
        sig        = tx.get("signature", "")
        block_time = tx.get("block_time") or int(time.time())
        fee_payers = tx.get("fee_payers", [])

        statements = []
        for fp in fee_payers:
            addr   = fp.get("address", "")
            amount = fp.get("amount_sol", 0)
            if not addr:
                continue
            statements.append((
                """INSERT INTO watchtower_fee_payers
                   (address, total_sol_sent, tx_count, first_seen_at, last_seen_at)
                   VALUES (?,?,1,?,?)
                   ON CONFLICT(address) DO UPDATE SET
                       total_sol_sent = total_sol_sent + excluded.total_sol_sent,
                       tx_count       = tx_count + 1,
                       last_seen_at   = MAX(last_seen_at, excluded.last_seen_at)""",
                (addr, amount, block_time, block_time)
            ))

        if statements:
            accepted = enqueue_write(WriteItem(
                domain="webhook",
                label=f"fee_payer_event:{sig[:20]}",
                statements=statements,
                idempotency_key=f"fee_event:{sig}",
            ))
            if accepted:
                queued += len(statements)

    return jsonify({"accepted": True, "fee_payers_queued": queued})


# ---------------------------------------------------------------------------
# Second-hop links
# ---------------------------------------------------------------------------

@internal_bp.route("/second-hop-links", methods=["POST"])
def ingest_second_hop_links():
    if not _auth():
        return _unauthorized()
    data   = request.get_json(silent=True) or {}
    funder = data.get("funder_address", "")
    links  = data.get("links", [])
    now    = int(time.time())

    if not funder:
        return jsonify({"error": "funder_address required"}), 400

    statements = []
    for link in links:
        upstream = link.get("upstream_address", "")
        amount   = link.get("amount_sol", 0)
        bt       = link.get("block_time", now)
        sig      = link.get("tx_signature", "")
        if not upstream:
            continue
        statements.append((
            """INSERT OR IGNORE INTO funder_upstream_links
               (creator_address, funder_address, amount_sol, first_detected_at)
               VALUES (?,?,?,?)""",
            (funder, upstream, amount, bt)
        ))

    if not statements:
        return jsonify({"accepted": True, "links_queued": 0})

    accepted = enqueue_write(WriteItem(
        domain="scan",
        label=f"2hop:{funder[:20]}",
        statements=statements,
        idempotency_key=f"2hop:{funder}:{now // 60}",
    ))

    return jsonify({"accepted": accepted, "links_queued": len(statements)})
