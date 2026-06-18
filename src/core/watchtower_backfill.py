"""
Post-migration WATCHTOWER launch backfill.

THE GAP this closes: wt_watchtower_launches is LIVE-cascade-only — a token only lands there
if the cascade caught it in real time. When we miss a launch live (TTL/commitment/lock bugs, or
a treasury we hadn't confirmed yet), the token NEVER surfaces as WATCHTOWER, even though we can
prove it post-migration by walking the funding lineage backward. (Confirmed misses this session:
AeFSni25, Hepc74 — both never appeared on /ops/tokens.)

THE WALK (per recently-migrated token, bounded RPC):
  mint → creator (token_analysis)
       → creator's FUNDING tx (getSignaturesForAddress, oldest provisioning inflow)
       → is it a WSOL wrap-close?  → the closeAccount lineage source = the SUBPROV
       → who funded the subprov?   → walk up (≤3 hops) to a wallet in wt_confirmed_treasuries
  if the lineage reaches a KNOWN WATCHTOWER treasury → RECORD a backfilled launch.

This is ATTRIBUTION (works backward from the proven migration), distinct from the live cascade's
real-time DETECTION. Backfilled launches are marked creator_extraction_method='POST_MIG_BACKFILL'
and confidence='BACKFILL' so the UI can distinguish them from live ✓ catches; birth_to_launch and
latency are unknown (NULL) — the point is the token surfaces as WATCHTOWER at all.

RPC discipline: getSignaturesForAddress + getTransaction only (1cr), NEVER enhanced-tx. Bounded by
a per-run migration cap and the funder-walk hop cap. Idempotent (record_launch is INSERT OR IGNORE
on (creator, create_sig)), and a local 'checked' set avoids re-walking the same migration.
"""
import os
import time
import sqlite3
from typing import Callable, Optional

from src.core import ws_cascade_store as store
from src.core.wrap_close_detector import detect_wrap_close

OPS_DB = store.OPS_DB_PATH
LIVE_DB = store.LIVE_DB_PATH

BACKFILL_DAYS = int(os.environ.get("WT_BACKFILL_DAYS", "2"))
BACKFILL_LIMIT = int(os.environ.get("WT_BACKFILL_LIMIT", "40"))   # migrations per run
FUNDER_MAX_HOPS = int(os.environ.get("WT_BACKFILL_HOPS", "3"))


def ensure_backfill_schema(conn) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS wt_backfill_checked (
            mint        TEXT PRIMARY KEY,
            result      TEXT,              -- RECORDED | NO_LINEAGE | NOT_WATCHTOWER | ERROR
            checked_at  INTEGER
        )"""
    )
    conn.commit()


def _confirmed_treasuries(conn) -> set:
    try:
        return {r[0] for r in conn.execute("SELECT treasury FROM wt_confirmed_treasuries").fetchall()}
    except Exception:
        return set()


def _walk_to_known_treasury(subprov: str, funder_walk_fn: Callable, confirmed: set):
    """Walk subprov → funder → … up to FUNDER_MAX_HOPS; return the first known treasury reached,
    or None. The subprov itself might be funded directly by a treasury (1 hop) or via a mid-tier
    rotator (2-3 hops) — see the 5JWii73→Dtwi→Efm mesh."""
    seen = set()
    cur = subprov
    for _hop in range(FUNDER_MAX_HOPS):
        if not cur or cur in seen:
            break
        seen.add(cur)
        if cur in confirmed:
            return cur
        funders = funder_walk_fn(cur) or []
        if not funders:
            break
        # the biggest funder is the provisioning edge (wrap-close-aware walk already prefers it)
        nxt = funders[0][0] if funders else None
        if nxt in confirmed:
            return nxt
        cur = nxt
    return None


def backfill_recent_migrations(*, raw_txs_fn: Callable, funder_walk_fn: Callable,
                               get_tx_fn: Callable, get_sigs_fn: Callable,
                               limit: int = BACKFILL_LIMIT) -> dict:
    """Walk recent migrations backward; record any whose lineage reaches a known WATCHTOWER
    treasury. raw_txs_fn/funder_walk_fn/get_tx_fn/get_sigs_fn are RPC closures supplied by the
    caller (the scheduler, which owns the key + budget)."""
    ops = sqlite3.connect(OPS_DB, timeout=30, isolation_level=None)
    ops.execute("PRAGMA busy_timeout=20000")
    ensure_backfill_schema(ops)
    live = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True, timeout=15)

    confirmed = _confirmed_treasuries(ops)
    if not confirmed:
        ops.close(); live.close()
        return {"checked": 0, "recorded": 0, "reason": "no confirmed treasuries"}

    # already-launched mints + already-checked mints → skip
    launched = {r[0] for r in ops.execute("SELECT mint FROM wt_watchtower_launches").fetchall()}
    checked = {r[0] for r in ops.execute("SELECT mint FROM wt_backfill_checked").fetchall()}

    rows = live.execute(
        "SELECT mint, COALESCE(earliest_tx_creator, pf_ws_creator) cw, migrated_at "
        "FROM token_analysis "
        "WHERE migrated_at > strftime('%s','now', ?) AND migrated_at IS NOT NULL "
        "AND COALESCE(earliest_tx_creator, pf_ws_creator) IS NOT NULL "
        "ORDER BY migrated_at DESC LIMIT ?",
        (f"-{BACKFILL_DAYS} days", limit * 3)).fetchall()

    recorded, n_checked = [], 0
    for mint, creator, migrated_at in rows:
        if n_checked >= limit:
            break
        if mint in launched or mint in checked:
            continue
        n_checked += 1
        result = "NO_LINEAGE"
        try:
            # 1) find the creator's provisioning inflow — its OLDEST few sigs (the wrap-close that
            #    birthed it is early in its history). Look for a WSOL wrap-close among them.
            sigs = get_sigs_fn(creator, limit=20) or []
            subprov = wrap_sig = wrap_sol = None
            for sg in reversed([s for s in sigs if not s.get("err")]):   # oldest first
                tx = get_tx_fn(sg["signature"])
                wc = detect_wrap_close(tx) if tx else None
                if wc and wc.get("subprov"):
                    subprov = wc["subprov"]; wrap_sig = sg["signature"]
                    wrap_sol = wc.get("base_amount_sol")
                    break
            if not subprov:
                result = "NO_LINEAGE"
            else:
                # 2) walk subprov → known treasury
                treasury = _walk_to_known_treasury(subprov, funder_walk_fn, confirmed)
                if not treasury:
                    result = "NOT_WATCHTOWER"
                else:
                    # 3) reached a known WATCHTOWER treasury → RECORD the backfilled launch
                    newly = store.record_launch(
                        ops, mint=mint, creator=creator, create_sig=None,
                        create_time=migrated_at, treasury=treasury, subprov=subprov,
                        wrap_close_sig=wrap_sig, birth_to_launch_s=None,
                        confidence="BACKFILL", wrap_close_sol=wrap_sol)
                    # mark the extraction method so the UI can distinguish backfill from live
                    try:
                        ops.execute(
                            "UPDATE wt_watchtower_launches SET creator_extraction_method='POST_MIG_BACKFILL' "
                            "WHERE mint=? AND creator_wallet=?", (mint, creator))
                        ops.commit()
                    except Exception:
                        pass
                    if newly:
                        recorded.append((mint, treasury))
                    result = "RECORDED"
        except Exception as e:
            result = f"ERROR:{str(e)[:40]}"
        try:
            ops.execute(
                "INSERT OR REPLACE INTO wt_backfill_checked (mint, result, checked_at) VALUES (?,?,?)",
                (mint, result, int(time.time())))
            ops.commit()
        except Exception:
            pass

    ops.close(); live.close()
    return {"checked": n_checked, "recorded": len(recorded), "details": recorded}
