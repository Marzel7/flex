"""
WATCHTOWER operator downstream scanner.

For each known fee payer, scans outbound transactions (2 hops) to discover:
  - Child wallets funded by operators → watchtower_operator_graph
  - Fresh wallets likely to launch tokens → watchtower_launch_candidates
  - Hop-2: wallets funded by operator children → launch candidates

Uses the same RPC pattern as second_hop_lite_worker.py.
No ownership inference — operational linkage only.
"""

import sqlite3
import json
import time
import os
import logging
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ── WATCHTOWER infrastructure (skip these as graph children) ──────────────────
from src.analysis.watchtower_detector import (
    _INFRA_SET,
    _INFRA_SKIP,
    update_wallet_state,
)

# ── Known CEX/program accounts to exclude ────────────────────────────────────
_EXCLUDED_PROGRAMS = frozenset({
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",   # pump.fun
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",   # PumpSwap
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",  # Raydium AMM v4
    "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C",  # Raydium CPMM
    "11111111111111111111111111111111",                # System program
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",   # Token program
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJe1bRS",  # Associated token
    "ComputeBudget111111111111111111111111111111",     # Compute budget
})

# Minimum outbound SOL to record as a graph edge
_MIN_OUTBOUND_SOL = 0.001
# Max to consider as "launch funding" (not a large sweep)
_MAX_LAUNCH_FUND_SOL = 5.0
# Tx lookback per operator
_MAX_SIGS = 100
# RPC timeout
_RPC_TIMEOUT = 15


def _resolve_rpc_url() -> Optional[str]:
    for key in ("RPC_HTTP", "RPC_URL", "HELIUS_RPC_URL"):
        val = os.environ.get(key)
        if val:
            return val
    env_file = Path(__file__).resolve().parent.parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            for key in ("RPC_HTTP", "RPC_URL", "HELIUS_RPC_URL"):
                if line.startswith(f"export {key}=") or line.startswith(f"{key}="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val
    return None


def _rpc_post(rpc_url: str, payload: dict) -> Optional[dict]:
    try:
        resp = requests.post(
            rpc_url,
            json=payload,
            timeout=_RPC_TIMEOUT,
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code != 200:
            return None
        return resp.json()
    except requests.RequestException:
        return None


def _get_signatures(rpc_url: str, address: str, limit: int = _MAX_SIGS) -> list[str]:
    resp = _rpc_post(rpc_url, {
        "jsonrpc": "2.0", "id": 1,
        "method": "getSignaturesForAddress",
        "params": [address, {"limit": limit}],
    })
    if not resp:
        return []
    result = resp.get("result") or []
    return [s["signature"] for s in result if s.get("err") is None and s.get("signature")]


def _get_transaction(rpc_url: str, sig: str) -> Optional[dict]:
    resp = _rpc_post(rpc_url, {
        "jsonrpc": "2.0", "id": 1,
        "method": "getTransaction",
        "params": [sig, {"encoding": "json", "maxSupportedTransactionVersion": 0}],
    })
    if not resp:
        return None
    return resp.get("result")


def _extract_outbound(tx_data: dict, sender: str) -> list[tuple[str, float, int]]:
    """
    Extract outbound SOL transfers from sender in this transaction.
    Returns list of (recipient_address, amount_sol, block_time).
    """
    results = []
    meta = tx_data.get("meta") or {}
    tx   = tx_data.get("transaction") or {}
    block_time = tx_data.get("blockTime") or 0

    account_keys = tx.get("message", {}).get("accountKeys") or []
    pre  = meta.get("preBalances")  or []
    post = meta.get("postBalances") or []

    if not account_keys or len(pre) != len(account_keys):
        return results

    # Normalise account keys (may be dicts in some encoding)
    def _addr(k):
        return k if isinstance(k, str) else (k.get("pubkey") if isinstance(k, dict) else None)

    keys = [_addr(k) for k in account_keys]

    try:
        sender_idx = keys.index(sender)
    except ValueError:
        return results

    sender_delta = post[sender_idx] - pre[sender_idx]
    if sender_delta >= 0:
        return results  # sender didn't spend SOL here

    for i, addr in enumerate(keys):
        if i == sender_idx or not addr:
            continue
        if addr in _EXCLUDED_PROGRAMS or addr in _INFRA_SKIP:
            continue
        delta_sol = (post[i] - pre[i]) / 1e9
        if delta_sol >= _MIN_OUTBOUND_SOL:
            results.append((addr, round(delta_sol, 9), block_time))

    return results


def _is_fresh_wallet(address: str, conn: sqlite3.Connection) -> bool:
    """Heuristic: wallet has no creator_risk_scores entry and no known activity."""
    row = conn.execute(
        "SELECT 1 FROM creator_risk_scores WHERE creator_address = ? LIMIT 1",
        (address,),
    ).fetchone()
    return row is None


def _write_graph_edge(
    conn: sqlite3.Connection,
    operator: str,
    child: str,
    relationship: str,
    amount_sol: float,
    block_time: int,
    sig: str,
    hop: int,
) -> None:
    now = int(time.time())
    conn.execute(
        """
        INSERT INTO watchtower_operator_graph
            (operator_address, child_address, relationship, amount_sol,
             first_seen_at, last_seen_at, tx_signature, hop)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(operator_address, child_address, relationship) DO UPDATE SET
            last_seen_at = excluded.last_seen_at,
            amount_sol   = amount_sol + excluded.amount_sol
        """,
        (operator, child, relationship, amount_sol, block_time or now, block_time or now, sig, hop),
    )


def _write_launch_candidate(
    conn: sqlite3.Connection,
    address: str,
    source_operator: str,
    reason: str,
    confidence: str,
    evidence: dict,
) -> None:
    now = int(time.time())
    conn.execute(
        """
        INSERT INTO watchtower_launch_candidates
            (address, source_operator, candidate_reason, confidence,
             first_signal_at, last_signal_at, evidence_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(address) DO UPDATE SET
            last_signal_at   = excluded.last_signal_at,
            confidence       = CASE
                WHEN excluded.confidence = 'high' THEN 'high'
                WHEN confidence = 'high' THEN 'high'
                ELSE excluded.confidence
            END
        """,
        (address, source_operator, reason, confidence, now, now, json.dumps(evidence)),
    )


def scan_operator_downstream(
    address: str,
    db_path: str,
    rpc_url: Optional[str] = None,
    hop: int = 1,
    _visited: Optional[set] = None,
) -> dict:
    """
    Scan outbound transactions from `address` (a known WATCHTOWER operator or child).
    Writes results to watchtower_operator_graph and watchtower_launch_candidates.
    hop=1 for direct operator children, hop=2 for grandchildren.
    """
    if rpc_url is None:
        rpc_url = _resolve_rpc_url()
    if not rpc_url:
        return {"error": "no_rpc_url"}

    if _visited is None:
        _visited = set()
    if address in _visited:
        return {"skipped": "already_visited"}
    _visited.add(address)

    children_found = 0
    candidates_found = 0
    hop2_queued = []

    # --- Phase 1: all RPC work, no DB connection held ---
    sigs = _get_signatures(rpc_url, address, limit=_MAX_SIGS)
    raw_outbound = []  # list of (child_addr, amount_sol, block_time, sig)
    for sig in sigs:
        tx_data = _get_transaction(rpc_url, sig)
        if not tx_data:
            continue
        for child_addr, amount_sol, block_time in _extract_outbound(tx_data, address):
            if child_addr not in _visited:
                raw_outbound.append((child_addr, amount_sol, block_time, sig))

    # --- Phase 2: brief DB window for fresh-wallet checks and writes ---
    conn = sqlite3.connect(db_path, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")

    try:
        for child_addr, amount_sol, block_time, sig in raw_outbound:
            # Determine relationship
            if amount_sol < _MAX_LAUNCH_FUND_SOL and _is_fresh_wallet(child_addr, conn):
                relationship = "launch_wallet"
                confidence   = "high"
                reason       = "post_fee_fanout" if hop == 1 else "funded_child"
            else:
                relationship = "funded"
                confidence   = "medium"
                reason       = "funded_child"

            root_op = address if hop == 1 else None
            if hop == 2:
                row = conn.execute(
                    "SELECT operator_address FROM watchtower_operator_graph "
                    "WHERE child_address = ? LIMIT 1",
                    (address,),
                ).fetchone()
                root_op = row["operator_address"] if row else address

            _write_graph_edge(conn, root_op, child_addr, relationship,
                              amount_sol, block_time, sig, hop)
            children_found += 1

            if relationship == "launch_wallet" or (hop == 2 and amount_sol < _MAX_LAUNCH_FUND_SOL):
                _write_launch_candidate(conn, child_addr, root_op, reason, confidence, {
                    "funded_by": address,
                    "amount_sol": amount_sol,
                    "tx": sig,
                    "hop": hop,
                })
                candidates_found += 1

            if hop == 1 and relationship in ("launch_wallet", "funded"):
                hop2_queued.append(child_addr)

        conn.commit()

        if hop == 1:
            try:
                update_wallet_state(address, conn)
                conn.commit()
            except Exception:
                pass

    finally:
        conn.close()

    result = {
        "address": address,
        "hop": hop,
        "sigs_scanned": len(sigs),
        "children_found": children_found,
        "candidates_found": candidates_found,
    }

    # Recurse to hop 2
    if hop == 1 and hop2_queued:
        hop2_results = []
        for child in hop2_queued[:20]:  # cap at 20 hop-2 scans per operator
            r = scan_operator_downstream(child, db_path, rpc_url, hop=2, _visited=_visited)
            hop2_results.append(r)
        result["hop2_scanned"] = len(hop2_results)
        result["hop2_candidates"] = sum(r.get("candidates_found", 0) for r in hop2_results)

    return result


def batch_scan_all_operators(
    db_path: str,
    rpc_url: Optional[str] = None,
    limit: int = 20,
) -> dict:
    """
    Run downstream scan for up to `limit` operators not yet scanned.
    Prioritises operators with recent fee activity.
    """
    if rpc_url is None:
        rpc_url = _resolve_rpc_url()
    if not rpc_url:
        return {"error": "no_rpc_url"}

    conn = sqlite3.connect(db_path, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        # Operators not yet in graph (never scanned) or oldest scan
        rows = conn.execute(
            """
            SELECT wfp.address
            FROM watchtower_fee_payers wfp
            LEFT JOIN (
                SELECT operator_address, MAX(last_seen_at) as last_scan
                FROM watchtower_operator_graph
                GROUP BY operator_address
            ) g ON g.operator_address = wfp.address
            ORDER BY g.last_scan ASC NULLS FIRST, wfp.last_seen_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        addresses = [r["address"] for r in rows]
    finally:
        conn.close()

    results = []
    for addr in addresses:
        try:
            r = scan_operator_downstream(addr, db_path, rpc_url)
            results.append(r)
            logger.info(f"[WT-SCAN] {addr[:20]}… hop1={r.get('children_found',0)} "
                        f"candidates={r.get('candidates_found',0)}")
        except Exception as exc:
            logger.warning(f"[WT-SCAN] {addr[:20]}… error: {exc}")

    return {
        "scanned": len(results),
        "total_children": sum(r.get("children_found", 0) for r in results),
        "total_candidates": sum(r.get("candidates_found", 0) for r in results),
    }


def scan_operator_downstream_stateless(
    address: str,
    rpc_url: Optional[str] = None,
    hop: int = 1,
    _visited: Optional[set] = None,
) -> dict:
    """
    Pure RPC probe — no DB writes. Returns structured result for POST to internal API.

    This is the new architecture entrypoint: workers call this, then POST the result
    to POST /api/internal/operator-graph. No sqlite3 connection is opened.
    """
    if rpc_url is None:
        rpc_url = _resolve_rpc_url()
    if _visited is None:
        _visited = set()
    if address in _visited:
        return {"skipped": True, "operator_address": address}
    _visited.add(address)

    sigs = _get_signatures(rpc_url, address, limit=_MAX_SIGS)
    raw_outbound = []
    for sig in sigs:
        tx_data = _get_transaction(rpc_url, sig)
        if not tx_data:
            continue
        for child_addr, amount_sol, block_time in _extract_outbound(tx_data, address):
            if child_addr not in _visited:
                raw_outbound.append((child_addr, amount_sol, block_time, sig))

    edges = []
    candidates = []
    hop2_targets = []

    for child_addr, amount_sol, block_time, sig in raw_outbound:
        is_small   = amount_sol < _MAX_LAUNCH_FUND_SOL
        relationship = "launch_wallet" if is_small else "funded"
        confidence   = "high" if is_small else "medium"
        reason       = "post_fee_fanout" if hop == 1 else "funded_child"

        edges.append({
            "child_address":  child_addr,
            "relationship":   relationship,
            "amount_sol":     amount_sol,
            "first_seen_at":  block_time,
            "tx_signature":   sig,
        })
        if is_small or (hop == 2 and amount_sol < _MAX_LAUNCH_FUND_SOL):
            candidates.append({
                "address":          child_addr,
                "source_operator":  address,
                "candidate_reason": reason,
                "confidence":       confidence,
                "evidence": {
                    "funded_by":  address,
                    "amount_sol": amount_sol,
                    "tx":         sig,
                    "hop":        hop,
                },
            })
        if hop == 1:
            hop2_targets.append(child_addr)

    result = {
        "operator_address": address,
        "scan_timestamp":   int(time.time()),
        "hop":              hop,
        "edges":            edges,
        "launch_candidates": candidates,
        "hop2_edges":       [],
        "hop2_candidates":  [],
    }

    # Hop 2 — still stateless, still no DB
    if hop == 1 and hop2_targets:
        for child in hop2_targets[:20]:
            r = scan_operator_downstream_stateless(child, rpc_url, hop=2, _visited=_visited)
            result["hop2_edges"].extend(r.get("edges", []))
            result["hop2_candidates"].extend(r.get("launch_candidates", []))

    return result
