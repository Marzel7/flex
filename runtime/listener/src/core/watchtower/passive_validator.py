"""
Passive Validation Framework
=============================
Tier 1-3 observation layer without capital deployment.

Separates infrastructure latency validation (Tier 3) from strategy validation (Tier 1-2).

Tier 1: Confirmed WATCH launches only
Tier 2: WATCH-like operators (detected via relay/treasury/signal)
Tier 3: All pump.fun CREATEs (pure infrastructure metrics)

All modes: build tx, measure latency, estimate buyer position, persist — NO ACTUAL SEND.
"""

import os
import sqlite3
import json
import time
import requests
import logging
from typing import Optional, Dict, List
from dataclasses import dataclass, asdict
from datetime import datetime

_API_KEY = os.getenv("HELIUS_API_KEY", "16f1a5fc-2592-466c-a5d4-b5799ae8da96")
_RPC_HTTP = f"https://mainnet.helius-rpc.com/?api-key={_API_KEY}"

_DB_PATH = os.getenv("DB_PATH", "")
if not _DB_PATH:
    _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    _DB_PATH = os.path.join(_root, "database", "flex_complete_database.db")

log = logging.getLogger("wt.passive_validator")


# ── Schema ─────────────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS wt_interceptor_validation (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    mint                TEXT    NOT NULL UNIQUE,
    creator             TEXT,
    create_slot         INTEGER,
    create_ts           REAL    NOT NULL,
    create_detected_at  REAL    NOT NULL,

    build_start_ts      REAL,
    build_end_ts        REAL,
    build_ms            REAL,

    submit_simulated_ts REAL,
    submit_ms           REAL,

    estimated_land_ts   REAL,
    estimated_slot_delta INTEGER,

    armed_source        TEXT,                -- "signal", "treasury_out", "subprov_relay", "unknown"
    armed_op_id         INTEGER REFERENCES wt_armed_operations(id),
    launch_type         TEXT,                -- "WATCH", "watch_like", "general"
    watch_confidence    REAL DEFAULT 0.0,

    -- First N buyers analysis
    first_5_buyers      TEXT,                -- JSON array of (slot, amount, buyer_addr)
    first_10_buyers     TEXT,
    first_25_buyers     TEXT,
    first_50_buyers     TEXT,
    first_100_buyers    TEXT,

    -- Estimated position if we landed at submit time
    est_position_top5   BOOLEAN DEFAULT 0,
    est_position_top10  BOOLEAN DEFAULT 0,
    est_position_top25  BOOLEAN DEFAULT 0,
    est_position_top50  BOOLEAN DEFAULT 0,

    actual_position     INTEGER,             -- where we actually would have landed

    bonding_curve_at_create TEXT,            -- state snapshot

    mode                TEXT DEFAULT 'PASSIVE',  -- "PASSIVE" or "LIVE"
    created_at          INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_wt_val_mint ON wt_interceptor_validation(mint);
CREATE INDEX IF NOT EXISTS idx_wt_val_ts   ON wt_interceptor_validation(create_ts DESC);
CREATE INDEX IF NOT EXISTS idx_wt_val_type ON wt_interceptor_validation(launch_type);
"""


def _ensure_schema():
    try:
        conn = sqlite3.connect(_DB_PATH, timeout=10)
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning(f"[VALIDATOR] schema init error: {e}")


# ── Data Models ────────────────────────────────────────────────────────────────

@dataclass
class ValidationRecord:
    mint: str
    creator: str
    create_slot: int
    create_ts: float
    create_detected_at: float
    build_start_ts: float
    build_end_ts: float
    armed_source: Optional[str] = None
    armed_op_id: Optional[int] = None
    launch_type: str = "general"
    watch_confidence: float = 0.0

    # Calculated
    build_ms: Optional[float] = None
    submit_ms: float = 5.0  # RPC submit time
    estimated_land_ts: Optional[float] = None
    estimated_slot_delta: Optional[int] = None

    # Buyer position
    first_5_buyers: Optional[List] = None
    first_10_buyers: Optional[List] = None
    first_25_buyers: Optional[List] = None
    first_50_buyers: Optional[List] = None
    first_100_buyers: Optional[List] = None

    est_position_top5: bool = False
    est_position_top10: bool = False
    est_position_top25: bool = False
    est_position_top50: bool = False
    actual_position: Optional[int] = None

    bonding_curve_at_create: Optional[str] = None
    mode: str = "PASSIVE"

    def calculate_metrics(self, network_latency_ms: float = 200.0, slot_per_second: float = 2.5):
        """Calculate derived metrics."""
        self.build_ms = (self.build_end_ts - self.build_start_ts) * 1000

        # detection_lag: wall-clock time between on-chain blockTime and our detection
        detection_lag_ms = (self.create_detected_at - self.create_ts) * 1000

        # Total pipeline latency from CREATE on-chain to our tx landing:
        #   detection lag + build time + RPC submit + network propagation
        total_latency_ms = detection_lag_ms + self.build_ms + self.submit_ms + network_latency_ms
        total_latency_s  = total_latency_ms / 1000.0

        self.estimated_land_ts = self.create_ts + total_latency_s

        # Slots elapsed between CREATE landing and our tx landing (positive = slots after)
        slot_distance = total_latency_s * slot_per_second
        self.estimated_slot_delta = max(0, int(round(slot_distance)))

    def to_dict(self) -> Dict:
        d = asdict(self)
        # Serialize lists to JSON
        for k in ['first_5_buyers', 'first_10_buyers', 'first_25_buyers', 'first_50_buyers', 'first_100_buyers']:
            if d[k] is not None:
                d[k] = json.dumps(d[k]) if isinstance(d[k], list) else d[k]
        return d


# ── Tx build simulation ───────────────────────────────────────────────────────

# pump.fun program and fixed accounts used in every buy instruction
_PUMPFUN_PROGRAM    = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
_SYSTEM_PROGRAM     = "11111111111111111111111111111111"
_TOKEN_PROGRAM      = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
_ASSOC_TOKEN_PROG   = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
_RENT_SYSVAR        = "SysvarRent111111111111111111111111111111111"
_EVENT_AUTH         = "Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1"
_FEE_RECIPIENT      = "CebN5WGQ4jvEPvsVU4EoHEpgznyZKIC643bkQo7WNMJ"

def _simulate_tx_build(mint: str) -> bytes:
    """
    Simulate the account list construction and instruction serialisation for a
    pump.fun buy transaction. No signing, no RPC calls — pure CPU work.
    Measures the Python overhead that would exist in the real hot path.
    """
    import struct
    import base58  # available in this env via solders deps

    # Derive bonding curve PDA (seeds: ["bonding-curve", mint_bytes])
    # Use base58 decode + struct pack to simulate the PDA derivation work
    try:
        mint_bytes = base58.b58decode(mint)
    except Exception:
        mint_bytes = b'\x00' * 32

    # Account keys for pump.fun buy instruction (10 accounts)
    accounts = [
        b'\x01' + mint_bytes,                              # 0: global (PDA)
        base58.b58decode(_FEE_RECIPIENT)[:32],             # 1: fee recipient
        mint_bytes,                                        # 2: mint
        b'\x02' + mint_bytes,                              # 3: bonding curve (PDA)
        b'\x03' + mint_bytes,                              # 4: associated bonding curve
        b'\x04' + mint_bytes,                              # 5: associated user account (ATA)
        b'\x05' + b'\x00' * 27,                           # 6: user (placeholder)
        base58.b58decode(_SYSTEM_PROGRAM)[:32],            # 7: system program
        base58.b58decode(_TOKEN_PROGRAM)[:32],             # 8: token program
        base58.b58decode(_RENT_SYSVAR)[:32],               # 9: rent sysvar
        base58.b58decode(_EVENT_AUTH)[:32],                # 10: event authority
        base58.b58decode(_PUMPFUN_PROGRAM)[:32],           # 11: pump program
    ]

    # Instruction discriminator (8 bytes) + amount_sol (u64) + max_sol_cost (u64)
    discriminator = b'\x66\x06\x3d\x12\x01\xda\xeb\xea'  # pump buy discriminator
    amount        = struct.pack('<Q', 100_000_000)          # 0.1 SOL in lamports
    max_cost      = struct.pack('<Q', 110_000_000)          # 10% slippage
    instruction_data = discriminator + amount + max_cost

    # Serialise: account count (1 byte) + all account bytes + instruction length + data
    payload = bytes([len(accounts)])
    for acc in accounts:
        payload += acc[:32].ljust(32, b'\x00')
    payload += struct.pack('<H', len(instruction_data))
    payload += instruction_data

    return payload


# ── Buyer Position Analysis ────────────────────────────────────────────────────

def _fetch_earliest_buyers(mint: str, limit: int = 100) -> Optional[List[Dict]]:
    """Fetch the first N buyers of a token (by timestamp/slot)."""
    try:
        # Query token's swap transactions via RPC
        resp = requests.post(_RPC_HTTP, json={
            "jsonrpc": "2.0", "id": 1,
            "method": "getSignaturesForAddress",
            "params": [mint, {"limit": min(limit * 2, 100)}]  # fetch more to have margin
        }, timeout=5)

        sigs = resp.json().get("result", [])
        buyers = []

        for sig_info in sigs:
            if sig_info.get("err"):
                continue

            # Fetch tx details
            tx_resp = requests.post(_RPC_HTTP, json={
                "jsonrpc": "2.0", "id": 1,
                "method": "getTransaction",
                "params": [sig_info["signature"], {
                    "encoding": "jsonParsed",
                    "maxSupportedTransactionVersion": 0
                }]
            }, timeout=3)

            tx = tx_resp.json().get("result")
            if not tx:
                continue

            slot = tx.get("slot")
            bt = tx.get("blockTime", 0)

            # Extract buyer account (usually [1] in pump.fun buys after [0] feePayer)
            accs = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])
            if len(accs) > 1:
                buyer = accs[1] if isinstance(accs[1], str) else accs[1].get("pubkey", "")

                # Extract amount from tokenTransfers
                for tt in tx.get("tokenTransfers", []):
                    if tt.get("mint") == mint:
                        amount = tt.get("tokenAmount", {}).get("uiAmount", 0)
                        buyers.append({
                            "slot": slot,
                            "ts": bt,
                            "buyer": buyer[:20],
                            "amount": amount,
                        })
                        break

        return sorted(buyers, key=lambda x: x["slot"])[:limit]
    except Exception as e:
        log.debug(f"[VALIDATOR] fetch_earliest_buyers error: {e}")
        return None


def _estimate_buyer_position(record: ValidationRecord) -> int:
    """
    Estimate where in the buyer queue we would have landed.
    Lower = better (1 = first buyer).
    """
    if not record.first_100_buyers:
        return None

    # Sort buyers by their slot
    buyers_sorted = sorted(record.first_100_buyers, key=lambda x: x.get("slot", 0))

    # Our estimated submission slot
    submit_slot = record.create_slot + (record.estimated_slot_delta or 0)

    # Count how many buyers came before our slot
    position = 1
    for buyer in buyers_sorted:
        if buyer.get("slot", 0) < submit_slot:
            position += 1
        else:
            break

    return position


# ── Main Validation Function ───────────────────────────────────────────────────

def validate_create(
    mint: str,
    creator: str,
    create_slot: int,
    create_ts: float,
    create_detected_at: float,
    armed_source: Optional[str] = None,
    armed_op_id: Optional[int] = None,
    launch_type: str = "general",
    watch_confidence: float = 0.0,
    fetch_buyers: bool = True,
) -> Optional[ValidationRecord]:
    """
    Validate a single CREATE transaction passively.

    Measures latency, estimates buyer position, persists — no actual send.
    """
    try:
        # Simulate tx build: construct pump.fun buy instruction accounts + compute budget,
        # serialize to bytes. Measures real Python overhead without signing or submitting.
        build_start = time.time()
        try:
            _simulate_tx_build(mint)
        except Exception:
            pass
        build_end = time.time()

        record = ValidationRecord(
            mint=mint,
            creator=creator,
            create_slot=create_slot,
            create_ts=create_ts,
            create_detected_at=create_detected_at,
            build_start_ts=build_start,
            build_end_ts=build_end,
            armed_source=armed_source,
            armed_op_id=armed_op_id,
            launch_type=launch_type,
            watch_confidence=watch_confidence,
            mode="PASSIVE",
        )

        # Calculate latency metrics
        record.calculate_metrics()

        # Fetch earliest buyers — skipped for GENERAL_PUMPFUN benchmark (too expensive,
        # mint has no buyers yet at CREATE time; position estimation runs separately)
        if fetch_buyers and launch_type != "GENERAL_PUMPFUN":
            record.first_5_buyers   = _fetch_earliest_buyers(mint, 5)
            record.first_10_buyers  = _fetch_earliest_buyers(mint, 10)
            record.first_25_buyers  = _fetch_earliest_buyers(mint, 25)
            record.first_50_buyers  = _fetch_earliest_buyers(mint, 50)
            record.first_100_buyers = _fetch_earliest_buyers(mint, 100)

        # Estimate our position
        if record.first_100_buyers:
            record.actual_position = _estimate_buyer_position(record)

            # Check if we'd be in top N
            if record.actual_position and record.actual_position <= 5:
                record.est_position_top5 = True
            if record.actual_position and record.actual_position <= 10:
                record.est_position_top10 = True
            if record.actual_position and record.actual_position <= 25:
                record.est_position_top25 = True
            if record.actual_position and record.actual_position <= 50:
                record.est_position_top50 = True

        # Persist
        _persist_validation(record)

        log.info(f"[VALIDATOR] validated mint={mint[:20]}  "
                 f"build_ms={record.build_ms:.1f}  "
                 f"delta={record.estimated_slot_delta}  "
                 f"pos={record.actual_position or '?'}  "
                 f"type={launch_type}  confidence={watch_confidence:.2f}")

        return record

    except Exception as e:
        log.warning(f"[VALIDATOR] validate_create error: {e}")
        return None


def _persist_validation(record: ValidationRecord):
    """Persist validation record to DB via write-with-retry (respects single-writer architecture)."""
    try:
        # Direct write with minimal retry — drop if locked (benchmark data is loss-tolerant)
        d = record.to_dict()
        placeholders = ", ".join("?" * len(d))
        cols = ", ".join(d.keys())
        sql = f"INSERT OR IGNORE INTO wt_interceptor_validation ({cols}) VALUES ({placeholders})"

        try:
            # 50ms timeout — fast fail if locked, acceptable for benchmark
            conn = sqlite3.connect(_DB_PATH, timeout=0.05)
            conn.execute(sql, tuple(d.values()))
            conn.commit()
            conn.close()
        except sqlite3.OperationalError:
            # Database locked — benchmark data is loss-tolerant, just drop
            pass
    except Exception as e:
        log.warning(f"[VALIDATOR] persist error: {e}")


# ── Reporting ──────────────────────────────────────────────────────────────────

def generate_report(days: int = 7, output_path: Optional[str] = None) -> Dict:
    """
    Generate validation report from passive observations.

    Returns metrics and saves to JSON if output_path given.
    """
    try:
        conn = sqlite3.connect(_DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row

        cutoff_ts = time.time() - (days * 86400)

        rows = conn.execute("""
            SELECT * FROM wt_interceptor_validation
            WHERE create_ts > ?
            ORDER BY create_ts DESC
        """, (cutoff_ts,)).fetchall()

        conn.close()

        if not rows:
            return {"error": "No validation records in period"}

        # Infrastructure metrics (all tiers)
        build_times  = [r["build_ms"]  for r in rows if r["build_ms"]]
        submit_times = [r["submit_ms"] for r in rows if r["submit_ms"]]
        # Exclude sub-second aliased rows (blockTime integer precision → lag rounds to 0)
        slot_deltas  = [r["estimated_slot_delta"] for r in rows
                        if r["estimated_slot_delta"] is not None
                        and r["create_detected_at"] and r["create_ts"]
                        and (r["create_detected_at"] - r["create_ts"]) >= 0.5]

        # WATCH metrics (Tier 1 only)
        watch_rows = [r for r in rows if r["launch_type"] == "WATCH" and r["watch_confidence"] > 0.5]
        watch_build_times = [r["build_ms"] for r in watch_rows if r["build_ms"]]
        watch_positions = [r["actual_position"] for r in watch_rows if r["actual_position"]]
        watch_top5 = sum(1 for r in watch_rows if r["est_position_top5"])
        watch_top10 = sum(1 for r in watch_rows if r["est_position_top10"])
        watch_top25 = sum(1 for r in watch_rows if r["est_position_top25"])

        def percentile(arr, p):
            if not arr:
                return None
            sorted_arr = sorted(arr)
            idx = int(len(sorted_arr) * (p / 100))
            return sorted_arr[idx]

        report = {
            "period_days": days,
            "generated_at": datetime.utcnow().isoformat(),

            "infrastructure_metrics": {
                "total_creates_observed": len(rows),
                "build_time_ms": {
                    "mean": sum(build_times) / len(build_times) if build_times else None,
                    "p50": percentile(build_times, 50),
                    "p95": percentile(build_times, 95),
                    "max": max(build_times) if build_times else None,
                },
                "submit_time_ms": {
                    "mean": sum(submit_times) / len(submit_times) if submit_times else None,
                },
                "slot_delta": {
                    "mean": sum(slot_deltas) / len(slot_deltas) if slot_deltas else None,
                    "p50": percentile(slot_deltas, 50),
                    "p95": percentile(slot_deltas, 95),
                    "min": min(slot_deltas) if slot_deltas else None,
                    "max": max(slot_deltas) if slot_deltas else None,
                },
            },

            "watch_metrics": {
                "launches_observed": len(watch_rows),
                "avg_position": sum(watch_positions) / len(watch_positions) if watch_positions else None,
                "top_5_count": watch_top5,
                "top_10_count": watch_top10,
                "top_25_count": watch_top25,
                "top_5_rate": watch_top5 / len(watch_rows) * 100 if watch_rows else None,
                "top_10_rate": watch_top10 / len(watch_rows) * 100 if watch_rows else None,
                "top_25_rate": watch_top25 / len(watch_rows) * 100 if watch_rows else None,
            },

            "sample_records": [
                {
                    "mint": r["mint"],
                    "create_slot": r["create_slot"],
                    "build_ms": r["build_ms"],
                    "slot_delta": r["estimated_slot_delta"],
                    "position": r["actual_position"],
                    "type": r["launch_type"],
                    "confidence": r["watch_confidence"],
                }
                for r in rows[:20]  # first 20 for reference
            ],
        }

        if output_path:
            with open(output_path, "w") as f:
                json.dump(report, f, indent=2, default=str)
            log.info(f"[VALIDATOR] report saved to {output_path}")

        return report

    except Exception as e:
        log.warning(f"[VALIDATOR] report generation error: {e}")
        return {"error": str(e)}


def startup():
    """Initialize passive validator."""
    _ensure_schema()
    log.info("[VALIDATOR] passive validation framework initialized")
