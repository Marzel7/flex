"""WATCHTOWER Launch Audit / Outcome Monitor.

For every detected WATCHTOWER launch (a row in wt_watchtower_launches), maintain a
wt_launch_audit record answering the ONLY question that matters operationally: when the
cascade fires at CREATE time, is there upside left — i.e. is detection ACTIONABLE?

Headline KPI:  ACTIONABLE_MULTIPLE = peak_mc / mc_at_detection

Two phases (per the approved design):
  Phase 1 (immediate, in the cascade): detection latency + tx position + entry-point market caps,
    reconstructed from the RAW bonding curve (no explorer summaries). audit_state=TIMING_CAPTURED.
  Phase 2 (deferred +5m/+30m/+2h/+24h): peak / current / migration MC + outcome + actionability,
    read from the existing snapshot tables. audit_state OUTCOME_PENDING→PEAK_CAPTURED→FINALIZED.

Per-metric PROVENANCE is stored (mc_*_source = CURVE_REPLAY | SNAPSHOT_TABLE) so we always know
which numbers came from raw reconstruction vs derived services.

Reuses (does not rebuild):
  watchtower.curve_impact_analyzer  — pump.fun curve constants + _curve_buy + _fetch_buy_details
  watchtower.buyer_position_analyzer — first-external-buy + our-position estimate
  pool_price_engine / sol_price_cache — SOL→USD
  token_analysis / token_market_cap_peaks / token_lifecycle_snapshots — peak/current/migration
"""

from __future__ import annotations

import os
import sys
import time
import json
import sqlite3
from typing import Optional, Dict, Any, List

try:
    from src.utils.db_locking import db_connect
except Exception:                                    # pragma: no cover
    def db_connect(path, timeout=30, row_factory=None):
        c = sqlite3.connect(path, timeout=timeout)
        if row_factory:
            c.row_factory = row_factory
        return c

# reuse the curve math + buyer position (single source of truth for pump.fun mechanics)
from src.core.watchtower.curve_impact_analyzer import (
    VIRTUAL_SOL_INIT, VIRTUAL_TOKEN_INIT, PUMPFUN_FEE_RATE,
    _curve_buy, _fetch_buy_details,
)

OPS_DB_PATH = os.environ.get(
    "OPS_V2_DB_PATH",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "database", "wt_ops_v2.db")))
LIVE_DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "database", "flex_complete_database.db")))

PUMP_TOTAL_SUPPLY = 1_000_000_000          # pump.fun: 1B tokens (6 decimals)
INSTANT_THRESHOLD_S = 60

# deferred checkpoint schedule (seconds after create) → run_phase2 re-visits
CHECKPOINTS = [300, 1800, 7200, 86400]     # +5m, +30m, +2h, +24h


# ─────────────────────────── SOL→USD (cached) ──────────────────────────────
_SOL_CACHE = {"px": None, "at": 0.0}


def _sol_usd() -> float:
    """Canonical SOL→USD, matching the rest of the pipeline (CoinGecko→Binance via
    PoolPriceCalculator). Cached 60s so we don't refetch per launch."""
    now = time.time()
    if _SOL_CACHE["px"] and now - _SOL_CACHE["at"] < 60:
        return _SOL_CACHE["px"]
    px = None
    # 1) the cache the pipeline already keeps warm
    try:
        from src.core.sol_price_cache import get_sol_price_cache
        px = get_sol_price_cache().get_price_sync()
    except Exception:
        px = None
    # 2) the canonical fetcher (CoinGecko→Binance→fallback) used everywhere else
    if not px:
        try:
            import asyncio
            from src.core.pool_price_engine import PoolPriceCalculator
            px = asyncio.new_event_loop().run_until_complete(
                PoolPriceCalculator.fetch_sol_price_usd())
        except Exception:
            px = None
    px = px or 150.0                                  # last-resort fallback
    _SOL_CACHE["px"] = px; _SOL_CACHE["at"] = now
    return px


# ─────────────────────────── curve → market cap ────────────────────────────
def mc_from_curve(virtual_sol: int, virtual_tokens: int, sol_usd: float) -> Optional[float]:
    """USD market cap from raw bonding-curve reserves.
    price_sol_per_token = virtual_sol / virtual_tokens ; mc = price * total_supply * sol_usd."""
    if not virtual_sol or not virtual_tokens:
        return None
    price_sol_per_token = (virtual_sol / 1e9) / (virtual_tokens / 1e6)   # SOL per whole token
    return price_sol_per_token * PUMP_TOTAL_SUPPLY * sol_usd


def replay_curve_to(buys: List[Dict], *, until_ts: float = None, until_slot: int = None,
                    n_buys: int = None) -> Dict[str, Any]:
    """Advance the pump.fun curve through `buys` (ordered, from _fetch_buy_details) up to the
    given cutoff (timestamp via slot proxy, slot, or count). Returns curve state + cumulative SOL.

    The curve is reconstructed purely from on-chain buy amounts — the 'raw bonding curve state'
    the design requires (no explorer summaries)."""
    vsol, vtok = VIRTUAL_SOL_INIT, VIRTUAL_TOKEN_INIT
    cum_sol = 0
    applied = 0
    for b in buys:
        if until_slot is not None and b.get("slot", 0) > until_slot:
            break
        if n_buys is not None and applied >= n_buys:
            break
        sol_in = b.get("sol_lamports", 0)
        if sol_in <= 0:
            continue
        _, vsol, vtok = _curve_buy(sol_in, vsol, vtok)
        cum_sol += sol_in
        applied += 1
    return {"virtual_sol": vsol, "virtual_tokens": vtok,
            "cumulative_sol": cum_sol, "n_buys": applied}


# ─────────────────────────────── schema ────────────────────────────────────
def ensure_audit_schema(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS wt_launch_audit (
        mint                        TEXT PRIMARY KEY,
        creator                     TEXT,
        treasury                    TEXT,
        subprov                     TEXT,
        create_signature            TEXT,
        create_slot                 INTEGER,
        create_time                 INTEGER,

        -- detection latency (epoch seconds, float where sub-second)
        ws_seen_at                  REAL,
        tx_fetched_at               REAL,
        mint_extracted_at           REAL,
        alert_emitted_at            REAL,
        detection_latency_ms        INTEGER,      -- alert_emitted - create_time
        fetch_latency_ms            INTEGER,      -- tx_fetched - ws_seen
        alert_latency_ms            INTEGER,      -- alert_emitted - ws_seen

        -- position / tx ordering
        create_tx_index_in_slot     INTEGER,
        first_external_buy_sig       TEXT,
        first_external_buy_slot      INTEGER,
        first_external_buy_index_in_slot INTEGER,
        our_possible_buy_slot        INTEGER,
        our_possible_buy_index_estimate INTEGER,
        txs_before_first_external_buy   INTEGER,
        buys_before_first_external_buy  INTEGER,
        buys_before_detection        INTEGER,
        buys_before_possible_entry   INTEGER,

        -- market cap (+ provenance per metric)
        mc_at_create                REAL,
        mc_at_create_source         TEXT,
        mc_at_detection             REAL,
        mc_at_detection_source      TEXT,
        mc_at_first_external_buy    REAL,
        mc_at_first_external_buy_source TEXT,
        peak_mc                     REAL,
        peak_mc_source              TEXT,
        current_mc                  REAL,
        time_to_peak_s              INTEGER,
        retrace_from_peak_pct       REAL,
        mc_unavailable_reason       TEXT,
        curve_virtual_sol           INTEGER,
        curve_virtual_tokens        INTEGER,

        -- outcome
        migrated                    INTEGER DEFAULT 0,
        migration_signature         TEXT,
        migration_time              INTEGER,
        dumped_before_migration     INTEGER DEFAULT 0,
        dump_time                   INTEGER,
        final_state                 TEXT,        -- INSTANT|STAGED|MIGRATED|DUMPED_BEFORE_MIGRATION|EXPIRED|UNKNOWN

        -- actionability
        actionable_multiple         REAL,        -- peak_mc / mc_at_detection (the headline KPI)
        peak_multiple_from_first_external REAL,
        seconds_from_detection_to_peak    INTEGER,

        -- workflow + provenance
        source                      TEXT DEFAULT 'LIVE_WS_CASCADE',  -- vs FIXTURE_BACKFILL
        audit_state                 TEXT DEFAULT 'PENDING',
        last_checkpoint             INTEGER DEFAULT 0,
        created_at                  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
        updated_at                  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
    )""")
    # migrate: add source to a pre-existing audit table
    try:
        _cols = {r[1] for r in conn.execute("PRAGMA table_info(wt_launch_audit)").fetchall()}
        if "source" not in _cols:
            conn.execute("ALTER TABLE wt_launch_audit ADD COLUMN source TEXT DEFAULT 'LIVE_WS_CASCADE'")
    except Exception:
        pass
    conn.execute("CREATE INDEX IF NOT EXISTS ix_launch_audit_state ON wt_launch_audit(audit_state)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_launch_audit_created ON wt_launch_audit(created_at DESC)")
    conn.commit()


_audit_schema_ensured = False

def _ops(read_only: bool = False):
    global _audit_schema_ensured
    if read_only:
        # Dashboard read path: a writable connection + ensure_audit_schema (a
        # WRITE) blocked on the write lane during the scheduler's write storm
        # (25s stalls). Read-only bypasses the lane; schema is ensured by the
        # audit writer pipeline, not the dashboard.
        try:
            c = db_connect(OPS_DB_PATH, timeout=20, read_only=True)
            # if the table doesn't exist yet, caller handles the empty result
            return c
        except TypeError:
            pass  # older db_connect without read_only — fall through
    c = db_connect(OPS_DB_PATH, timeout=20)
    c.execute("PRAGMA busy_timeout=20000")
    if not _audit_schema_ensured:
        ensure_audit_schema(c)
        _audit_schema_ensured = True
    return c


# ─────────────────────── Phase 1: timing + entry MC ─────────────────────────
def capture_phase1(*, mint: str, creator: str, treasury: str = None, subprov: str = None,
                   create_signature: str = None, create_slot: int = None, create_time: int = None,
                   ws_seen_at: float = None, tx_fetched_at: float = None,
                   mint_extracted_at: float = None, alert_emitted_at: float = None,
                   source: str = "LIVE_WS_CASCADE") -> Dict:
    """Immediate audit capture at detection. Latencies + tx-position + entry-point MC (raw curve).
    Idempotent on mint. Returns the captured field dict. Safe to call off-thread."""
    now = int(time.time())
    # ---- latencies (ms) ----
    def _ms(a, b):
        return int((a - b) * 1000) if (a is not None and b is not None) else None
    detection_latency_ms = _ms(alert_emitted_at, create_time)
    fetch_latency_ms     = _ms(tx_fetched_at, ws_seen_at)
    alert_latency_ms     = _ms(alert_emitted_at, ws_seen_at)

    # ---- tx position + first external buy (reuse buyer_position_analyzer) ----
    first_ext_sig = first_ext_slot = est_position = buyer_count = None
    buys_before_detection = buys_before_entry = None
    try:
        from src.core.watchtower import buyer_position_analyzer as bpa
        bpa.ensure_schema()
        # run if not already present for this mint (it persists wt_buyer_position_validation)
        if create_slot:
            lc = db_connect(LIVE_DB_PATH, timeout=15)
            try:
                have = lc.execute("SELECT first_buy_slot, estimated_position, buyer_count, buyers_json "
                                  "FROM wt_buyer_position_validation WHERE mint=?", (mint,)).fetchone()
            finally:
                lc.close()
            if not have:
                # analyze_buyer_position persists; detected_ts = alert time (our decision moment)
                bpa.analyze_buyer_position(mint, create_slot, float(create_time or now),
                                           float(alert_emitted_at or now), estimated_slot_delta=2)
                lc = db_connect(LIVE_DB_PATH, timeout=15)
                try:
                    have = lc.execute("SELECT first_buy_slot, estimated_position, buyer_count, buyers_json "
                                      "FROM wt_buyer_position_validation WHERE mint=?", (mint,)).fetchone()
                finally:
                    lc.close()
            if have:
                first_ext_slot, est_position, buyer_count, buyers_json = have
                try:
                    blist = json.loads(buyers_json or "[]")
                    if blist:
                        first_ext_sig = blist[0].get("signature") or blist[0].get("sig")
                        # buys before our alert time / before our possible entry slot
                        if alert_emitted_at:
                            buys_before_detection = sum(1 for b in blist
                                                        if (b.get("ts") or 0) <= alert_emitted_at)
                        if est_position is not None:
                            buys_before_entry = max(est_position - 1, 0)
                except Exception:
                    pass
    except Exception as e:
        print(f"[LAUNCH_AUDIT] buyer-position failed mint={mint[:10]}: {e}", flush=True)

    # ---- entry-point market caps from RAW curve replay ----
    sol_usd = _sol_usd()
    mc_create = mc_detection = mc_first_ext = None
    vsol = vtok = None
    mc_reason = None
    try:
        buys = _fetch_buy_details(mint, create_slot, limit=100) if create_slot else []
        # mc at create = fresh curve (no buys yet)
        mc_create = mc_from_curve(VIRTUAL_SOL_INIT, VIRTUAL_TOKEN_INIT, sol_usd)
        # mc at first external buy = curve right BEFORE that buy lands
        if first_ext_slot is not None:
            st = replay_curve_to(buys, until_slot=first_ext_slot - 1)
            mc_first_ext = mc_from_curve(st["virtual_sol"], st["virtual_tokens"], sol_usd)
        # mc at detection = curve advanced by all buys up to our alert moment.
        # proxy: buys at slots <= our_possible_buy_slot (create_slot + delta)
        our_slot = (create_slot + 2) if create_slot else None
        if our_slot is not None:
            st = replay_curve_to(buys, until_slot=our_slot)
            mc_detection = mc_from_curve(st["virtual_sol"], st["virtual_tokens"], sol_usd)
            vsol, vtok = st["virtual_sol"], st["virtual_tokens"]
        if not buys and create_slot:
            mc_reason = "no_buys_fetched"
    except Exception as e:
        mc_reason = f"curve_replay_error:{e}"
        print(f"[LAUNCH_AUDIT] curve replay failed mint={mint[:10]}: {e}", flush=True)

    fields = dict(
        mint=mint, creator=creator, treasury=treasury, subprov=subprov,
        create_signature=create_signature, create_slot=create_slot, create_time=create_time,
        ws_seen_at=ws_seen_at, tx_fetched_at=tx_fetched_at, mint_extracted_at=mint_extracted_at,
        alert_emitted_at=alert_emitted_at,
        detection_latency_ms=detection_latency_ms, fetch_latency_ms=fetch_latency_ms,
        alert_latency_ms=alert_latency_ms,
        first_external_buy_sig=first_ext_sig, first_external_buy_slot=first_ext_slot,
        our_possible_buy_slot=(create_slot + 2) if create_slot else None,
        our_possible_buy_index_estimate=est_position,
        buys_before_first_external_buy=0,
        buys_before_detection=buys_before_detection, buys_before_possible_entry=buys_before_entry,
        mc_at_create=mc_create, mc_at_create_source="CURVE_REPLAY" if mc_create else None,
        mc_at_detection=mc_detection, mc_at_detection_source="CURVE_REPLAY" if mc_detection else None,
        mc_at_first_external_buy=mc_first_ext,
        mc_at_first_external_buy_source="CURVE_REPLAY" if mc_first_ext else None,
        curve_virtual_sol=vsol, curve_virtual_tokens=vtok,
        mc_unavailable_reason=mc_reason,
        source=source,
        audit_state="TIMING_CAPTURED",
    )
    _upsert(fields)
    return fields


def _upsert(fields: Dict) -> None:
    cols = [k for k in fields.keys()]
    conn = _ops()
    try:
        placeholders = ",".join("?" for _ in cols)
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "mint")
        conn.execute(
            f"INSERT INTO wt_launch_audit ({','.join(cols)}, updated_at) "
            f"VALUES ({placeholders}, strftime('%s','now')) "
            f"ON CONFLICT(mint) DO UPDATE SET {updates}, updated_at=strftime('%s','now')",
            [fields[c] for c in cols])
        conn.commit()
    finally:
        conn.close()


# ─────────────────── Phase 2: peak / outcome / actionability ────────────────
def _read_peak_and_current(mint: str) -> Dict:
    """Peak/current/migration MC from the existing snapshot tables (SNAPSHOT_TABLE provenance).
    No RPC — reads token_market_cap_peaks, token_analysis, token_lifecycle_snapshots."""
    out = {"peak_mc": None, "peak_at": None, "current_mc": None,
           "migrated": 0, "migration_time": None, "migration_sig": None, "create_ts": None}
    lc = db_connect(LIVE_DB_PATH, timeout=15)
    try:
        try:
            r = lc.execute("SELECT peak_market_cap, peak_market_cap_at FROM token_market_cap_peaks "
                           "WHERE mint=?", (mint,)).fetchone()
            if r:
                out["peak_mc"], out["peak_at"] = r[0], r[1]
        except Exception:
            pass
        try:
            r = lc.execute(
                "SELECT market_cap_highest, market_cap_highest_at_ts, market_cap_current, "
                "migrated_at, migration_tx, created_at, lifecycle_stage "
                "FROM token_analysis WHERE mint=?", (mint,)).fetchone()
            if r:
                if (out["peak_mc"] or 0) < (r[0] or 0):
                    out["peak_mc"], out["peak_at"] = r[0], r[1]
                out["current_mc"] = r[2]
                if r[3]:
                    out["migrated"] = 1; out["migration_time"] = r[3]; out["migration_sig"] = r[4]
                try:
                    out["create_ts"] = int(float(r[5])) if r[5] else None
                except Exception:
                    out["create_ts"] = None
        except Exception:
            pass
    finally:
        lc.close()
    return out


def run_phase2(mint: str) -> Dict:
    """Deferred: fill peak/current/migration + outcome + actionability. Reads snapshot tables
    (no RPC). Advances audit_state by age. Idempotent."""
    conn = _ops()
    try:
        row = conn.execute(
            "SELECT create_time, mc_at_detection, mc_at_first_external_buy, audit_state "
            "FROM wt_launch_audit WHERE mint=?", (mint,)).fetchone()
    finally:
        conn.close()
    if not row:
        return {"error": "no audit row"}
    create_time, mc_detection, mc_first_ext, state = row

    snap = _read_peak_and_current(mint)
    peak_mc = snap["peak_mc"]
    now = int(time.time())
    age = now - (create_time or now)

    actionable = (peak_mc / mc_detection) if (peak_mc and mc_detection) else None
    peak_mult_fe = (peak_mc / mc_first_ext) if (peak_mc and mc_first_ext) else None
    secs_to_peak = (snap["peak_at"] - create_time) if (snap["peak_at"] and create_time) else None
    retrace = None
    if peak_mc and snap["current_mc"] is not None and peak_mc > 0:
        retrace = round((1 - snap["current_mc"] / peak_mc) * 100, 1)

    # outcome classification
    migrated = snap["migrated"]
    final_state = "UNKNOWN"
    dumped = 0; dump_time = None
    if migrated:
        final_state = "MIGRATED"
    elif peak_mc is not None:
        # dumped before migration = had a peak, never migrated, and retraced hard
        if retrace is not None and retrace >= 70 and not migrated:
            final_state = "DUMPED_BEFORE_MIGRATION"; dumped = 1; dump_time = snap["peak_at"]
        elif age >= CHECKPOINTS[-1] and not migrated:
            final_state = "EXPIRED"
        else:
            # INSTANT vs STAGED by birth→launch is recorded on the launch row; here classify by
            # whether it's still developing
            final_state = "STAGED" if age < CHECKPOINTS[-1] else "EXPIRED"
    # workflow state by age + data completeness.
    # DO NOT finalize while peak_mc is still NULL just because the token migrated — migration
    # is NOT the peak. A token can migrate early and keep climbing on the AMM for hours
    # (observed: Bn9kT5… migrated at +6min but peaked at +3h17m → $194k). Finalizing on
    # migration alone froze the row before token_market_cap_peaks was populated, so peak_mc /
    # actionable_multiple stayed blank forever (FINALIZED rows are skipped by the checkpoint
    # loop). Require peak_mc present to FINALIZE; otherwise keep re-checking, with the 24h
    # window as the hard backstop so a token that genuinely never gets a peak still closes out.
    if peak_mc is not None and (migrated or age >= CHECKPOINTS[-1]):
        new_state = "FINALIZED"
    elif age >= CHECKPOINTS[-1]:
        new_state = "FINALIZED"                        # backstop: 24h elapsed, close out even w/o peak
    elif peak_mc is not None:
        new_state = "PEAK_CAPTURED"
    else:
        new_state = "OUTCOME_PENDING"

    fields = dict(
        mint=mint,
        peak_mc=peak_mc, peak_mc_source="SNAPSHOT_TABLE" if peak_mc is not None else None,
        current_mc=snap["current_mc"],
        time_to_peak_s=secs_to_peak, retrace_from_peak_pct=retrace,
        migrated=migrated, migration_time=snap["migration_time"],
        migration_signature=snap["migration_sig"],
        dumped_before_migration=dumped, dump_time=dump_time, final_state=final_state,
        actionable_multiple=actionable, peak_multiple_from_first_external=peak_mult_fe,
        seconds_from_detection_to_peak=secs_to_peak,
        audit_state=new_state, last_checkpoint=now,
    )
    _upsert(fields)
    return fields


def due_for_checkpoint(limit: int = 20) -> List[str]:
    """Mints whose audit isn't FINALIZED and are due for the next checkpoint pass."""
    conn = _ops()
    try:
        now = int(time.time())
        rows = conn.execute(
            "SELECT mint, create_time, last_checkpoint FROM wt_launch_audit "
            "WHERE audit_state != 'FINALIZED' AND audit_state != 'PENDING' "
            "ORDER BY last_checkpoint ASC LIMIT ?", (limit * 3,)).fetchall()
    finally:
        conn.close()
    due = []
    for mint, ct, last in rows:
        ct = ct or now
        age = now - ct
        # next checkpoint boundary not yet covered by last_checkpoint
        nxt = next((c for c in CHECKPOINTS if (ct + c) > (last or 0)), None)
        if nxt is not None and age >= nxt:
            due.append(mint)
        elif age >= CHECKPOINTS[-1]:
            due.append(mint)
        if len(due) >= limit:
            break
    return due


# ─────────────────────────────── report ────────────────────────────────────
def _median(vals):
    v = sorted(x for x in vals if x is not None)
    return v[len(v) // 2] if v else None


def _stats(rows: list) -> Dict:
    """rows = [(latency, mc_detect, peak, mult, final_state, dumped), ...] → KPI block."""
    n = len(rows)
    mults = [r[3] for r in rows if r[3] is not None]
    buckets = {"<1.5x": 0, "1.5-2x": 0, "2-5x": 0, "5-10x": 0, "10x+": 0}
    for m in mults:
        if m < 1.5: buckets["<1.5x"] += 1
        elif m < 2: buckets["1.5-2x"] += 1
        elif m < 5: buckets["2-5x"] += 1
        elif m < 10: buckets["5-10x"] += 1
        else: buckets["10x+"] += 1
    gt2 = sum(1 for m in mults if m >= 2)
    gt5 = sum(1 for m in mults if m >= 5)
    dumped = sum(1 for r in rows if r[5])
    return {
        "n_launches": n, "n_with_multiple": len(mults),
        "median_detection_latency_ms": _median([r[0] for r in rows]),
        "median_mc_at_detection": _median([r[1] for r in rows]),
        "median_peak_mc": _median([r[2] for r in rows]),
        "median_actionable_multiple": _median(mults),
        "pct_gt_2x": round(100 * gt2 / len(mults), 1) if mults else None,
        "pct_gt_5x": round(100 * gt5 / len(mults), 1) if mults else None,
        "pct_dumped_before_migration": round(100 * dumped / n, 1) if n else None,
        "buckets": buckets,
        "verdict": ("ACTIONABLE" if (mults and _median(mults) and _median(mults) >= 2)
                    else "MARGINAL" if mults else "INSUFFICIENT_DATA"),
    }


def outcome_report() -> Dict:
    """The §8 actionability answer, SPLIT BY SOURCE so fixtures don't overstate live performance.
    Returns {all, live, fixtures} KPI blocks. The dashboard headline should read live; fixtures
    are the validated baseline. Top-level keys mirror `all` for backward compat."""
    conn = _ops(read_only=True)
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='wt_launch_audit'").fetchone():
            return {"all": _stats([]), "live": _stats([]), "fixtures": _stats([])}
        rows = conn.execute(
            "SELECT detection_latency_ms, mc_at_detection, peak_mc, actionable_multiple, "
            "final_state, dumped_before_migration, source FROM wt_launch_audit").fetchall()
    finally:
        conn.close()
    live = [r[:6] for r in rows if (r[6] or "LIVE_WS_CASCADE") == "LIVE_WS_CASCADE"]
    fixtures = [r[:6] for r in rows if r[6] == "FIXTURE_BACKFILL"]
    out = dict(_stats([r[:6] for r in rows]))         # `all` (back-compat top-level)
    out["all"] = _stats([r[:6] for r in rows])
    out["live"] = _stats(live)
    out["fixtures"] = _stats(fixtures)
    return out


# ─────────────────────────────── backfill ──────────────────────────────────
def backfill(limit: int = None) -> Dict:
    """Audit every existing wt_watchtower_launches row (idempotent). Latency NULL for historical
    rows (timestamps unknown) but entry/first-external/outcome MC are computed — the point."""
    conn = _ops()
    try:
        # create_slot is stored on the launch row (the cascade records it) — prefer it; only
        # re-resolve via RPC when it's missing (older rows).
        q = ("SELECT mint, creator_wallet, treasury_wallet, subprov_wallet, create_signature, "
             "create_time, create_slot FROM wt_watchtower_launches ORDER BY id DESC")
        if limit:
            q += f" LIMIT {int(limit)}"
        launches = conn.execute(q).fetchall()
    finally:
        conn.close()
    done = 0
    for mint, creator, treasury, subprov, csig, ctime, cslot in launches:
        if not mint:
            continue
        if not cslot and csig:
            cslot = _resolve_create_slot(csig)        # RPC fallback only when slot absent
        capture_phase1(mint=mint, creator=creator, treasury=treasury, subprov=subprov,
                       create_signature=csig, create_slot=cslot, create_time=ctime,
                       alert_emitted_at=None, ws_seen_at=None, source="FIXTURE_BACKFILL")
        run_phase2(mint)
        done += 1
    return {"audited": done}


def _resolve_create_slot(sig: str) -> Optional[int]:
    try:
        import urllib.request
        key = os.environ.get("HELIUS_API_KEY", "")
        url = os.environ.get("HELIUS_RPC_URL", f"https://mainnet.helius-rpc.com/?api-key={key}")
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getTransaction",
                           "params": [sig, {"encoding": "jsonParsed",
                                            "maxSupportedTransactionVersion": 0}]}).encode()
        r = json.loads(urllib.request.urlopen(urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}), timeout=15).read())
        return (r.get("result") or {}).get("slot")
    except Exception:
        return None


if __name__ == "__main__":
    if "--backfill" in sys.argv:
        n = None
        for a in sys.argv:
            if a.startswith("--limit="):
                n = int(a.split("=")[1])
        print(json.dumps(backfill(limit=n), indent=2))
    elif "--report" in sys.argv:
        print(json.dumps(outcome_report(), indent=2))
    else:
        print("usage: python -m src.core.launch_audit [--backfill [--limit=N]] | --report")
