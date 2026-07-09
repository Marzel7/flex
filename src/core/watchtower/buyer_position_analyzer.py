"""
Buyer Position Analyzer
=======================
For each benchmark CREATE event, waits 45s then fetches the first N buyers
and estimates where the WATCHTOWER interceptor would have ranked.

This answers the central question:
  "If we had submitted at theoretical_submit_ts, what buyer rank would we have achieved?"

Buyer detection:
  - getSignaturesForAddress on the mint, filtered to slot >= create_slot
  - Filters to txs containing "Instruction: Buy" or "Instruction: BuyExactQuoteInV2"
  - Sorted by slot ascending → chronological buyer rank

Position estimation:
  - Our theoretical submit slot = create_slot + estimated_slot_delta
  - Count buyers whose slot <= our submit slot → that is our rank
  - Accounts for the fact that multiple buys can land in the same slot

Schedule: called from create_interceptor after each benchmark CREATE, delayed 45s.
"""

import json
import logging
import os
import sqlite3
import threading
import time
from typing import Optional, List, Dict

import requests

# Module reload detection flag (incremented on each code change for debugging)
_MODULE_RELOAD_ID = 2

_API_KEY  = os.getenv("HELIUS_API_KEY", "16f1a5fc-2592-466c-a5d4-b5799ae8da96")
_RPC_HTTP = f"https://mainnet.helius-rpc.com/?api-key={_API_KEY}"

_DB_PATH = os.getenv("DB_PATH", "")
if not _DB_PATH:
    _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    _DB_PATH = os.path.join(_root, "database", "flex_complete_database.db")

log = logging.getLogger("wt.buyer_position")
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(_h)
    log.setLevel(logging.WARNING)

# Semaphore: limit concurrent RPC-heavy analysis jobs
# 12 slots: BPV is IO-bound (RPC fetch). Each job holds semaphore ~50s (45s delay + 5s RPC + write).
# At 1 CREATE/sec, 12 slots allows ~12s of queue before dropping (acceptable for benchmark)
_analysis_sem = threading.Semaphore(12)

DELAY_SECONDS    = 45    # wait after CREATE before fetching buyers
MAX_BUYERS_FETCH = 100   # fetch up to 100 post-create txs
BUY_INSTRUCTIONS = {"Buy", "BuyExactQuoteInV2", "BuyExactIn", "BuyExactOut"}


# ── Schema ─────────────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS wt_buyer_position_validation (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    mint                  TEXT    NOT NULL UNIQUE,
    create_slot           INTEGER NOT NULL,
    create_ts             REAL    NOT NULL,
    detected_ts           REAL    NOT NULL,
    theoretical_submit_ts REAL,
    estimated_slot_delta  INTEGER,
    buyer_count           INTEGER,
    estimated_position    INTEGER,
    top_5                 INTEGER DEFAULT 0,
    top_10                INTEGER DEFAULT 0,
    top_25                INTEGER DEFAULT 0,
    top_50                INTEGER DEFAULT 0,
    first_buy_slot        INTEGER,
    first_buy_ts          REAL,
    last_sampled_ts       REAL,
    sample_window_s       REAL,
    buyers_json           TEXT,
    created_at            INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_wt_bpv_mint ON wt_buyer_position_validation(mint);
CREATE INDEX IF NOT EXISTS idx_wt_bpv_ts   ON wt_buyer_position_validation(create_ts DESC);
"""


def ensure_schema():
    try:
        conn = sqlite3.connect(_DB_PATH, timeout=15)
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning(f"[BPV] schema error: {e}")


# ── Buyer fetching ─────────────────────────────────────────────────────────────

def _fetch_buyers(mint: str, create_slot: int, limit: int = MAX_BUYERS_FETCH) -> List[Dict]:
    """
    Fetch post-CREATE buyers for a mint, sorted chronologically.
    Returns list of {slot, ts, buyer, rank} dicts.
    """
    try:
        # Get signatures for mint address post-create
        r = requests.post(_RPC_HTTP, json={
            "jsonrpc": "2.0", "id": 1,
            "method": "getSignaturesForAddress",
            "params": [mint, {"limit": limit * 2, "commitment": "confirmed"}]
        }, timeout=8)
        sigs = r.json().get("result", [])

        # Filter to post-create, no errors, sort oldest-first
        post = sorted(
            [s for s in sigs if not s.get("err") and s.get("slot", 0) >= create_slot],
            key=lambda x: x["slot"]
        )

        buyers = []
        for sig_info in post[:limit]:
            try:
                r2 = requests.post(_RPC_HTTP, json={
                    "jsonrpc": "2.0", "id": 1,
                    "method": "getTransaction",
                    "params": [sig_info["signature"], {
                        "encoding": "jsonParsed",
                        "maxSupportedTransactionVersion": 0
                    }]
                }, timeout=5)
                tx = r2.json().get("result")
                if not tx:
                    continue

                logs = tx.get("meta", {}).get("logMessages", []) or []
                is_buy = any(
                    any(instr in l for instr in BUY_INSTRUCTIONS)
                    for l in logs
                    if "Instruction:" in l
                )
                if not is_buy:
                    continue

                accs  = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])
                addrs = [a if isinstance(a, str) else a.get("pubkey", "") for a in accs]
                buyer = addrs[0] if addrs else None
                if not buyer:
                    continue

                # Extract SOL spent (feepayer balance decrease)
                pre   = tx.get("meta", {}).get("preBalances", [])
                post_b = tx.get("meta", {}).get("postBalances", [])
                sol_spent = (pre[0] - post_b[0]) if pre and post_b else 0

                buyers.append({
                    "rank":        len(buyers) + 1,
                    "slot":        tx.get("slot"),
                    "ts":          tx.get("blockTime"),
                    "buyer":       buyer,
                    "sol_lamports": sol_spent,  # New: SOL spent (lamports)
                })
            except Exception:
                continue

        return buyers

    except Exception as e:
        log.warning(f"[BPV] fetch_buyers error mint={mint[:20]}: {e}")
        return []


# ── Position estimation ────────────────────────────────────────────────────────

def _estimate_position(buyers: List[Dict], our_slot: int) -> Optional[int]:
    """
    Count buyers who landed at or before our_slot.
    Our rank = that count + 1 (we would be next).
    """
    if not buyers:
        return None
    ahead = sum(1 for b in buyers if b["slot"] <= our_slot)
    return ahead + 1


# ── Main analysis function ─────────────────────────────────────────────────────

def analyze_buyer_position(
    mint: str,
    create_slot: int,
    create_ts: float,
    detected_ts: float,
    estimated_slot_delta: int,
):
    """
    Called 45s after CREATE detection. Fetches early buyers and estimates position.
    Runs in a background thread — acquires semaphore to cap concurrency.
    """
    if not _analysis_sem.acquire(blocking=False):
        log.warning(f"[BPV] semaphore full, skipping mint={mint[:20]}")
        return

    try:
        sample_start = time.time()
        buyers = _fetch_buyers(mint, create_slot)
        last_sampled_ts = time.time()
        sample_window_s = last_sampled_ts - sample_start

        if not buyers:
            log.warning(f"[BPV] no buyers found mint={mint[:20]}")
            _persist(mint, create_slot, create_ts, detected_ts, estimated_slot_delta,
                     buyer_count=0, position=None, buyers=[], last_sampled_ts=last_sampled_ts,
                     sample_window_s=sample_window_s)
            return

        # Theoretical submit slot — where we would have landed
        our_slot      = create_slot + estimated_slot_delta
        position      = _estimate_position(buyers, our_slot)
        buyer_count   = len(buyers)
        first_buy     = buyers[0]
        theoretical_submit_ts = create_ts + (estimated_slot_delta / 2.5)

        top_5  = position is not None and position <= 5
        top_10 = position is not None and position <= 10
        top_25 = position is not None and position <= 25
        top_50 = position is not None and position <= 50

        log.warning(
            f"[BPV] mint={mint[:20]}  buyers={buyer_count}  "
            f"our_slot=+{estimated_slot_delta}  position=#{position}  "
            f"top5={top_5}  top10={top_10}  top25={top_25}"
        )

        _persist(mint, create_slot, create_ts, detected_ts, estimated_slot_delta,
                 buyer_count=buyer_count, position=position, buyers=buyers,
                 theoretical_submit_ts=theoretical_submit_ts,
                 first_buy_slot=first_buy["slot"], first_buy_ts=first_buy["ts"],
                 top_5=top_5, top_10=top_10, top_25=top_25, top_50=top_50,
                 last_sampled_ts=last_sampled_ts, sample_window_s=sample_window_s)

        # Schedule curve impact analysis 45s later (needs more buyers to settle)
        if buyer_count >= 3:
            try:
                from src.core.watchtower.curve_impact_analyzer import schedule_analysis as cia_schedule
                cia_schedule(mint=mint, create_slot=create_slot, create_ts=create_ts,
                             delay_s=45)
            except Exception as e:
                log.warning(f"[BPV] cia_schedule error: {e}")

    finally:
        _analysis_sem.release()


def _persist(
    mint: str, create_slot: int, create_ts: float, detected_ts: float,
    estimated_slot_delta: int, buyer_count: int, position: Optional[int],
    buyers: List[Dict], theoretical_submit_ts: float = None,
    first_buy_slot: int = None, first_buy_ts: float = None,
    top_5: bool = False, top_10: bool = False, top_25: bool = False, top_50: bool = False,
    last_sampled_ts: float = None, sample_window_s: float = None,
):
    try:
        # Direct write with minimal retry (loss-tolerant benchmark data)
        buyers_snapshot = buyers[:50]  # store first 50 buyers only
        sql = f"""INSERT OR IGNORE INTO wt_buyer_position_validation
               (mint, create_slot, create_ts, detected_ts, theoretical_submit_ts,
                estimated_slot_delta, buyer_count, estimated_position,
                top_5, top_10, top_25, top_50,
                first_buy_slot, first_buy_ts, last_sampled_ts, sample_window_s, buyers_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
        params = (mint, create_slot, create_ts, detected_ts, theoretical_submit_ts,
                  estimated_slot_delta, buyer_count, position,
                  int(top_5), int(top_10), int(top_25), int(top_50),
                  first_buy_slot, first_buy_ts, last_sampled_ts, sample_window_s,
                  json.dumps(buyers_snapshot))

        # Try once with 50ms timeout — drop if locked (loss-tolerant)
        try:
            conn = sqlite3.connect(_DB_PATH, timeout=0.05)
            conn.execute(sql, params)
            conn.commit()
            conn.close()
        except sqlite3.OperationalError:
            # Database locked — acceptable for benchmark data, just drop
            pass
    except Exception as e:
        log.warning(f"[BPV] persist error mint={mint[:20]}: {e}")


# ── Schedule helper ────────────────────────────────────────────────────────────

def schedule_analysis(
    mint: str,
    create_slot: int,
    create_ts: float,
    detected_ts: float,
    estimated_slot_delta: int,
    delay_s: float = DELAY_SECONDS,
):
    """
    Schedule analyze_buyer_position to run after delay_s seconds.
    Fire-and-forget — runs in a daemon thread.
    """
    def _run():
        time.sleep(delay_s)
        analyze_buyer_position(mint, create_slot, create_ts, detected_ts, estimated_slot_delta)

    threading.Thread(
        target=_run,
        daemon=True,
        name=f"wt-bpv-{mint[:8]}"
    ).start()


# ── Reporting ──────────────────────────────────────────────────────────────────

def get_position_report(days: int = 7) -> Dict:
    """Summary statistics for the dashboard and status endpoint."""
    try:
        conn = sqlite3.connect(_DB_PATH, timeout=15)
        conn.row_factory = sqlite3.Row
        cutoff = time.time() - days * 86400

        row = conn.execute("""
            SELECT
                COUNT(*)                                            total,
                SUM(CASE WHEN estimated_position IS NOT NULL
                         THEN 1 ELSE 0 END)                        with_position,
                ROUND(AVG(estimated_position),1)                   avg_position,
                ROUND(AVG(CASE WHEN estimated_position IS NOT NULL
                               AND buyer_count >= 5
                               THEN estimated_position END), 1)    avg_pos_filtered,
                SUM(top_5)                                         cnt_top5,
                SUM(top_10)                                        cnt_top10,
                SUM(top_25)                                        cnt_top25,
                SUM(top_50)                                        cnt_top50,
                SUM(CASE WHEN buyer_count = 0 THEN 1 ELSE 0 END)  no_buyers,
                ROUND(AVG(buyer_count),1)                          avg_buyers
            FROM wt_buyer_position_validation
            WHERE create_ts >= ?
        """, (cutoff,)).fetchone()

        positions = [r[0] for r in conn.execute("""
            SELECT estimated_position FROM wt_buyer_position_validation
            WHERE create_ts >= ? AND estimated_position IS NOT NULL AND buyer_count >= 5
            ORDER BY estimated_position
        """, (cutoff,)).fetchall()]

        conn.close()

        n     = row["with_position"] or 0
        total = row["total"] or 0

        def pct(v): return round((v or 0) / n * 100, 1) if n > 0 else None
        def pctile(arr, p):
            if not arr: return None
            return arr[min(int(len(arr) * p / 100), len(arr) - 1)]

        # Decision band
        top25_rate = pct(row["cnt_top25"])
        if top25_rate is None:   verdict = "INSUFFICIENT_DATA"
        elif top25_rate >= 75:   verdict = "EXCELLENT"
        elif top25_rate >= 50:   verdict = "GOOD"
        elif top25_rate >= 25:   verdict = "MARGINAL"
        else:                    verdict = "POOR"

        return {
            "total_analyzed":    total,
            "with_position":     n,
            "avg_position":      row["avg_pos_filtered"],
            "p50_position":      pctile(positions, 50),
            "p95_position":      pctile(positions, 95),
            "top_5_rate":        pct(row["cnt_top5"]),
            "top_10_rate":       pct(row["cnt_top10"]),
            "top_25_rate":       top25_rate,
            "top_50_rate":       pct(row["cnt_top50"]),
            "cnt_top5":          row["cnt_top5"] or 0,
            "cnt_top10":         row["cnt_top10"] or 0,
            "cnt_top25":         row["cnt_top25"] or 0,
            "cnt_top50":         row["cnt_top50"] or 0,
            "no_buyers_pct":     round((row["no_buyers"] or 0) / total * 100, 1) if total else None,
            "avg_buyers_sampled": row["avg_buyers"],
            "verdict":           verdict,
        }

    except Exception as e:
        log.warning(f"[BPV] report error: {e}")
        return {"error": str(e)}
