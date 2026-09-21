"""
Curve Position Impact Analyzer
================================
Answers the economic question: does slot +3 matter?

For each analyzed launch, fetches the SOL amount of the first N buys,
reconstructs the bonding curve state at each buy, and calculates the
price penalty (% above initial price) for every slot offset bucket.

pump.fun bonding curve (constant product):
  k = virtual_sol_reserves × virtual_token_reserves
  Initial: 30 SOL virtual, 1,073,000,191 tokens virtual
  Fee: ~1% deducted from SOL in (fee goes to protocol)
  tokens_out = virtual_tokens_before - (k / (virtual_sol_before + sol_in_after_fee))
  price = virtual_sol / virtual_tokens  (SOL per raw token unit)

Key output: for each mint, at each slot offset (+1,+2,+3,+5,+10,+20),
  - cumulative_sol_before_us    (SOL that entered before we would land)
  - price_penalty_pct           (% worse price vs fresh curve)
  - tokens_penalty_pct          (% fewer tokens we'd receive vs landing first)

The slot offsets are theoretical — we use the actual buyer slot distribution
to determine how many buyers had already transacted by each offset.
"""

import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Tuple

import requests

_API_KEY  = os.getenv("HELIUS_API_KEY", "16f1a5fc-2592-466c-a5d4-b5799ae8da96")
_RPC_HTTP = f"https://mainnet.helius-rpc.com/?api-key={_API_KEY}"

_DB_PATH = os.getenv("DB_PATH", "")
if not _DB_PATH:
    _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    _DB_PATH = os.path.join(_root, "database", "flex_complete_database.db")

log = logging.getLogger("wt.curve_impact")
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(_h)
    log.setLevel(logging.WARNING)

# Semaphore: CIA is IO-bound (RPC fetches of 100 buyer txs). 8 slots = reasonable concurrency.
_analysis_sem = threading.Semaphore(8)

# ── pump.fun curve constants ───────────────────────────────────────────────────

VIRTUAL_SOL_INIT    = 30_000_000_000       # 30 SOL in lamports
VIRTUAL_TOKEN_INIT  = 1_073_000_191_000_000  # tokens with 6 decimals
PUMPFUN_FEE_RATE    = 0.01                  # 1% fee
OUR_BUY_SOL         = 100_000_000           # 0.1 SOL in lamports (benchmark buy size)

SLOT_BUCKETS = [0, 1, 2, 3, 5, 10, 20]  # slot offsets to simulate
BUY_INSTRUCTIONS = {"Buy", "BuyExactQuoteInV2", "BuyExactIn", "BuyExactOut"}

MIN_BUYERS_FOR_ANALYSIS = 3  # skip mints with fewer buyers (insufficient data)


# ── Schema ─────────────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS wt_curve_impact_analysis (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    mint                  TEXT    NOT NULL UNIQUE,
    create_slot           INTEGER NOT NULL,
    create_ts             REAL,
    launch_type           TEXT    DEFAULT 'GENERAL_PUMPFUN',
    buyer_count_sampled   INTEGER,
    total_sol_first_100   REAL,

    -- Per-slot-offset simulation results (JSON: {slot_offset: {penalty_pct, tokens_penalty_pct, sol_before, buyer_rank}})
    slot_bucket_results   TEXT,

    -- Pre-computed summary for fast dashboard queries
    penalty_slot0         REAL,   -- % price penalty if landing at slot+0 (i.e. 0 — baseline)
    penalty_slot1         REAL,   -- % vs fresh curve if we land at slot+1
    penalty_slot2         REAL,
    penalty_slot3         REAL,
    penalty_slot5         REAL,
    penalty_slot10        REAL,
    penalty_slot20        REAL,

    tokens_penalty_slot1  REAL,   -- % fewer tokens vs landing first at slot+1
    tokens_penalty_slot3  REAL,
    tokens_penalty_slot5  REAL,

    sol_before_slot1      REAL,   -- cumulative SOL in curve before slot+1
    sol_before_slot3      REAL,
    sol_before_slot5      REAL,

    buyer_rank_at_slot1   INTEGER,
    buyer_rank_at_slot3   INTEGER,
    buyer_rank_at_slot5   INTEGER,

    -- First buyer slot offset (how many slots after CREATE did first buy land?)
    first_buy_slot_offset INTEGER,
    first_buy_sol         REAL,

    analyzed_at           REAL,
    created_at            INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_wt_cia_mint    ON wt_curve_impact_analysis(mint);
CREATE INDEX IF NOT EXISTS idx_wt_cia_ts      ON wt_curve_impact_analysis(create_ts DESC);
CREATE INDEX IF NOT EXISTS idx_wt_cia_type    ON wt_curve_impact_analysis(launch_type);
"""


def ensure_schema():
    try:
        conn = sqlite3.connect(_DB_PATH, timeout=15)
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning(f"[CIA] schema error: {e}")


# ── Curve math ─────────────────────────────────────────────────────────────────

def _curve_buy(sol_in_lamports: int,
               virtual_sol: int = VIRTUAL_SOL_INIT,
               virtual_tokens: int = VIRTUAL_TOKEN_INIT) -> Tuple[int, int, int]:
    """
    Simulate a pump.fun buy. Returns (tokens_out, new_virtual_sol, new_virtual_tokens).
    """
    k = virtual_sol * virtual_tokens
    sol_after_fee = int(sol_in_lamports * (1 - PUMPFUN_FEE_RATE))
    new_virtual_sol = virtual_sol + sol_after_fee
    new_virtual_tokens = k // new_virtual_sol
    tokens_out = virtual_tokens - new_virtual_tokens
    return tokens_out, new_virtual_sol, new_virtual_tokens


def _price_sol_per_million(virtual_sol: int, virtual_tokens: int) -> float:
    """Price in SOL per 1 million tokens."""
    return (virtual_sol / virtual_tokens) * 1_000_000 / 1e9


def _simulate_our_entry(cumulative_sol_before_us_lamports: int) -> Dict:
    """
    Given how much SOL entered the curve before us, compute our entry metrics.
    Returns price_penalty_pct, tokens_penalty_pct, tokens_we_receive.
    """
    # Fresh curve (what we'd get if first)
    fresh_tokens, _, _ = _curve_buy(OUR_BUY_SOL)
    fresh_price = _price_sol_per_million(VIRTUAL_SOL_INIT, VIRTUAL_TOKEN_INIT)

    if cumulative_sol_before_us_lamports <= 0:
        return {
            "price_penalty_pct":  0.0,
            "tokens_penalty_pct": 0.0,
            "tokens_received":    fresh_tokens,
            "entry_price":        fresh_price,
        }

    # Advance the curve by all buys that happened before us
    # We don't know the fee-adjusted breakdown per-buy, so treat it as one lump sum
    # (conservative — slightly over-estimates penalty vs sequential small buys)
    sol_after_fee = int(cumulative_sol_before_us_lamports * (1 - PUMPFUN_FEE_RATE))
    k = VIRTUAL_SOL_INIT * VIRTUAL_TOKEN_INIT
    advanced_virtual_sol = VIRTUAL_SOL_INIT + sol_after_fee
    advanced_virtual_tokens = k // advanced_virtual_sol

    our_tokens, _, _ = _curve_buy(OUR_BUY_SOL, advanced_virtual_sol, advanced_virtual_tokens)
    our_price = _price_sol_per_million(advanced_virtual_sol, advanced_virtual_tokens)

    price_penalty  = (our_price / fresh_price - 1) * 100
    tokens_penalty = (1 - our_tokens / fresh_tokens) * 100

    return {
        "price_penalty_pct":  round(price_penalty, 2),
        "tokens_penalty_pct": round(tokens_penalty, 2),
        "tokens_received":    our_tokens,
        "entry_price":        our_price,
    }


# ── RPC fetch ──────────────────────────────────────────────────────────────────

def _fetch_buy_details(mint: str, create_slot: int, limit: int = 100) -> List[Dict]:
    """
    Fetch pump.fun buy transactions by looking for ProgramData signatures
    on the pump.fun program (searches for BUY instruction invocations).

    Uses getSignaturesForAddress on mint, filters to pump.fun program,
    then fetches full tx to extract buyer + SOL amount.
    """
    try:
        # Fetch all sigs involving this mint
        r = requests.post(_RPC_HTTP, json={
            "jsonrpc": "2.0", "id": 1,
            "method": "getSignaturesForAddress",
            "params": [mint, {"limit": limit * 5, "commitment": "confirmed"}]  # 5× oversampling to account for filters
        }, timeout=8)
        sigs = r.json().get("result", [])

        post = sorted(
            [s for s in sigs if not s.get("err") and s.get("slot", 0) >= create_slot],
            key=lambda x: x["slot"]
        )

        buys = []
        for sig_info in post[:limit * 3]:  # Try more sigs since filtering will drop many
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

                # Filter: must have pump.fun program invocation with BUY instruction
                has_pumpfun = any("6EF8rr" in l for l in logs)  # pump.fun program prefix
                is_buy = any(
                    any(instr in l for instr in BUY_INSTRUCTIONS)
                    for l in logs if "Instruction:" in l
                )

                if not (has_pumpfun and is_buy):
                    continue

                slot = tx.get("slot")
                pre   = tx.get("meta", {}).get("preBalances", [])
                post_b = tx.get("meta", {}).get("postBalances", [])
                sol_spent = (pre[0] - post_b[0]) if pre and post_b else 0

                # For pump.fun: buyer's SOL balance decreases by amount + fee
                # Accept any positive decrease (transaction cost + purchase)
                if sol_spent <= 0:
                    continue

                buys.append({
                    "rank":        len(buys) + 1,
                    "slot":        slot,
                    "slot_offset": slot - create_slot,
                    "sol_lamports": sol_spent,
                })

                if len(buys) >= limit:
                    break

            except Exception:
                continue

        return buys

    except Exception as e:
        log.warning(f"[CIA] fetch_buy_details error mint={mint[:20]}: {e}")
        return []


# ── Main analysis ──────────────────────────────────────────────────────────────

def analyze_curve_impact(
    mint: str,
    create_slot: int,
    create_ts: float,
    launch_type: str = "GENERAL_PUMPFUN",
):
    """
    Fetch first 100 buy transactions, reconstruct curve state at each slot offset,
    compute price penalty at each SLOT_BUCKET. Persist results.
    """
    if not _analysis_sem.acquire(blocking=False):
        log.warning(f"[CIA] semaphore full, skipping mint={mint[:20]}")
        return

    try:
        buys = _fetch_buy_details(mint, create_slot, limit=100)

        if len(buys) < MIN_BUYERS_FOR_ANALYSIS:
            log.warning(f"[CIA] insufficient buyers ({len(buys)}) mint={mint[:20]}")
            return

        total_sol = sum(b["sol_lamports"] for b in buys) / 1e9

        # For each slot bucket: cumulative SOL that entered before that slot offset
        bucket_results = {}
        for slot_offset in SLOT_BUCKETS:
            buyers_before = [b for b in buys if b["slot_offset"] < slot_offset]
            sol_before_lamports = sum(b["sol_lamports"] for b in buyers_before)
            buyer_rank = len(buyers_before) + 1

            sim = _simulate_our_entry(sol_before_lamports)
            bucket_results[slot_offset] = {
                "price_penalty_pct":  sim["price_penalty_pct"],
                "tokens_penalty_pct": sim["tokens_penalty_pct"],
                "sol_before_sol":     round(sol_before_lamports / 1e9, 4),
                "buyer_rank":         buyer_rank,
            }

        # Find earliest actual buyer (by slot, not array order — buys are sorted by slot)
        first_buy = min(buys, key=lambda b: b["slot"]) if buys else {"slot_offset": 0, "sol_lamports": 0}
        r = bucket_results

        log.warning(
            f"[CIA] mint={mint[:20]}  buyers={len(buys)}  first@+{first_buy.get('slot_offset')}  "
            f"sol_total={total_sol:.2f}  "
            f"penalty@slot+1={r.get(1,{}).get('price_penalty_pct')}%  "
            f"penalty@slot+3={r.get(3,{}).get('price_penalty_pct')}%  "
            f"penalty@slot+5={r.get(5,{}).get('price_penalty_pct')}%"
        )

        _persist(mint, create_slot, create_ts, launch_type, buys, bucket_results,
                 total_sol, first_buy)

    finally:
        _analysis_sem.release()


def _persist(mint, create_slot, create_ts, launch_type, buys, bucket_results,
             total_sol, first_buy):
    try:
        def bp(slot, key, default=None):
            return bucket_results.get(slot, {}).get(key, default)

        sql = """INSERT OR IGNORE INTO wt_curve_impact_analysis
               (mint, create_slot, create_ts, launch_type, buyer_count_sampled, total_sol_first_100,
                slot_bucket_results,
                penalty_slot0, penalty_slot1, penalty_slot2, penalty_slot3,
                penalty_slot5, penalty_slot10, penalty_slot20,
                tokens_penalty_slot1, tokens_penalty_slot3, tokens_penalty_slot5,
                sol_before_slot1, sol_before_slot3, sol_before_slot5,
                buyer_rank_at_slot1, buyer_rank_at_slot3, buyer_rank_at_slot5,
                first_buy_slot_offset, first_buy_sol, analyzed_at)
               VALUES (?,?,?,?,?,?, ?,?,?,?,?,?,?,?, ?,?,?, ?,?,?, ?,?,?, ?,?,?)"""
        params = (mint, create_slot, create_ts, launch_type, len(buys), total_sol,
                  json.dumps(bucket_results),
                  bp(0, "price_penalty_pct", 0),
                  bp(1, "price_penalty_pct"), bp(2, "price_penalty_pct"),
                  bp(3, "price_penalty_pct"), bp(5, "price_penalty_pct"),
                  bp(10, "price_penalty_pct"), bp(20, "price_penalty_pct"),
                  bp(1, "tokens_penalty_pct"), bp(3, "tokens_penalty_pct"), bp(5, "tokens_penalty_pct"),
                  bp(1, "sol_before_sol"), bp(3, "sol_before_sol"), bp(5, "sol_before_sol"),
                  bp(1, "buyer_rank"), bp(3, "buyer_rank"), bp(5, "buyer_rank"),
                  first_buy["slot_offset"], round(first_buy["sol_lamports"] / 1e9, 4),
                  time.time())

        # Direct write with 50ms timeout — drop if locked
        try:
            conn = sqlite3.connect(_DB_PATH, timeout=0.05)
            conn.execute(sql, params)
            conn.commit()
            conn.close()
        except sqlite3.OperationalError:
            pass  # Loss-tolerant, acceptable
    except Exception as e:
        log.warning(f"[CIA] persist error mint={mint[:20]}: {e}")


# ── Schedule helper ────────────────────────────────────────────────────────────

def schedule_analysis(
    mint: str,
    create_slot: int,
    create_ts: float,
    launch_type: str = "GENERAL_PUMPFUN",
    delay_s: float = 90,  # wait 90s — need buyers to settle more than position analysis
):
    """Schedule curve impact analysis in a daemon thread."""
    def _run():
        time.sleep(delay_s)
        analyze_curve_impact(mint, create_slot, create_ts, launch_type)

    threading.Thread(
        target=_run,
        daemon=True,
        name=f"wt-cia-{mint[:8]}"
    ).start()


# ── Report ─────────────────────────────────────────────────────────────────────

def get_impact_report(days: int = 7, launch_type: str = None) -> Dict:
    """
    Aggregate curve impact metrics across all analyzed launches.
    This is the report that answers 'is slot+3 economically equivalent to slot+1?'
    """
    try:
        conn = sqlite3.connect(_DB_PATH, timeout=15)
        conn.row_factory = sqlite3.Row
        cutoff = time.time() - days * 86400

        where = "WHERE create_ts >= ?"
        args  = [cutoff]
        if launch_type:
            where += " AND launch_type = ?"
            args.append(launch_type)

        row = conn.execute(f"""
            SELECT
                COUNT(*)                              n,
                ROUND(AVG(penalty_slot1),  2)         avg_penalty_slot1,
                ROUND(AVG(penalty_slot2),  2)         avg_penalty_slot2,
                ROUND(AVG(penalty_slot3),  2)         avg_penalty_slot3,
                ROUND(AVG(penalty_slot5),  2)         avg_penalty_slot5,
                ROUND(AVG(penalty_slot10), 2)         avg_penalty_slot10,
                ROUND(AVG(tokens_penalty_slot1), 2)   avg_tok_penalty_slot1,
                ROUND(AVG(tokens_penalty_slot3), 2)   avg_tok_penalty_slot3,
                ROUND(AVG(tokens_penalty_slot5), 2)   avg_tok_penalty_slot5,
                ROUND(AVG(sol_before_slot1), 4)       avg_sol_before_slot1,
                ROUND(AVG(sol_before_slot3), 4)       avg_sol_before_slot3,
                ROUND(AVG(sol_before_slot5), 4)       avg_sol_before_slot5,
                ROUND(AVG(buyer_rank_at_slot1), 1)    avg_rank_slot1,
                ROUND(AVG(buyer_rank_at_slot3), 1)    avg_rank_slot3,
                ROUND(AVG(first_buy_slot_offset), 1)  avg_first_buy_offset,
                ROUND(AVG(total_sol_first_100), 3)    avg_total_sol,
                ROUND(AVG(buyer_count_sampled), 1)    avg_buyers
            FROM wt_curve_impact_analysis {where}
        """, args).fetchone()

        # Percentile distributions for penalty_slot3 (the key number)
        penalties_3 = sorted([r[0] for r in conn.execute(
            f"SELECT penalty_slot3 FROM wt_curve_impact_analysis {where} "
            "AND penalty_slot3 IS NOT NULL ORDER BY penalty_slot3",
            args
        ).fetchall()])

        def pct(arr, p):
            if not arr: return None
            return arr[min(int(len(arr) * p / 100), len(arr) - 1)]

        conn.close()

        n = row["n"] or 0
        return {
            "n":                    n,
            "avg_buyers":           row["avg_buyers"],
            "avg_total_sol":        row["avg_total_sol"],
            "avg_first_buy_offset": row["avg_first_buy_offset"],
            "slot1": {
                "avg_price_penalty_pct":  row["avg_penalty_slot1"],
                "avg_tokens_penalty_pct": row["avg_tok_penalty_slot1"],
                "avg_sol_before":         row["avg_sol_before_slot1"],
                "avg_buyer_rank":         row["avg_rank_slot1"],
            },
            "slot2": {
                "avg_price_penalty_pct":  row["avg_penalty_slot2"],
            },
            "slot3": {
                "avg_price_penalty_pct":  row["avg_penalty_slot3"],
                "p50_price_penalty_pct":  pct(penalties_3, 50),
                "p75_price_penalty_pct":  pct(penalties_3, 75),
                "p95_price_penalty_pct":  pct(penalties_3, 95),
                "avg_tokens_penalty_pct": row["avg_tok_penalty_slot3"],
                "avg_sol_before":         row["avg_sol_before_slot3"],
                "avg_buyer_rank":         row["avg_rank_slot3"],
            },
            "slot5": {
                "avg_price_penalty_pct":  row["avg_penalty_slot5"],
                "avg_tokens_penalty_pct": row["avg_tok_penalty_slot5"],
                "avg_sol_before":         row["avg_sol_before_slot5"],
            },
            "slot10": {
                "avg_price_penalty_pct":  row["avg_penalty_slot10"],
            },
        }

    except Exception as e:
        log.warning(f"[CIA] report error: {e}")
        return {"error": str(e), "n": 0}
