"""
Farm detector — operator clustering for SINGLE-USE-CREATOR migrated launches.

WATCHTOWER is only ONE farm style (the WSOL wrap-close provisioning mechanism). On-chain
there is a much larger ecosystem of PLAIN-TRANSFER farms running the identical playbook —
fresh single-use creator → migrate — funded by a plain SOL transfer instead of a wrap-close.
WATCHTOWER detection is 100% blind to them (it keys on the wrap-close signature).

This detector is mechanism-AGNOSTIC: it clusters recent single-use-creator MIGRATED tokens by
their creator's FUNDER. A funder that provisioned ≥2 such creators is a farm/operator,
regardless of whether it used wrap-close or plain transfer. (Measured 2026-06-16: 15 active
plain-transfer farms, 67 launches in 3 days — far outnumbering the wrap-close WATCHTOWER set.)

  funder → [single-use migrated creators]  ·  ≥2 = FARM
  mechanism = WRAP_CLOSE (wt-style) | PLAIN_XFER (the larger ecosystem)

Stored in wt_farms / wt_farm_launches (ops db). Read-only over token_analysis + RPC for the
funder trace (1 getSignatures + 1 getTransaction per new creator, cached by checked-set).
"""
from __future__ import annotations
import os
import json
import time
import sqlite3
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

try:
    from src.utils.db_locking import db_connect
except Exception:
    def db_connect(path, timeout=30, row_factory=None):
        c = sqlite3.connect(path, timeout=timeout)
        if row_factory:
            c.row_factory = row_factory
        return c

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OPS_DB = os.environ.get("OPS_V2_DB_PATH", os.path.join(_REPO, "database", "wt_ops_v2.db"))
LIVE_DB = os.environ.get("FLEX_DB_PATH", os.path.join(_REPO, "database", "flex_complete_database.db"))
_RPC = os.environ.get("HELIUS_RPC_URL", "")


def ensure_farm_schema(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS wt_farms (
        funder            TEXT PRIMARY KEY,
        creator_count     INTEGER DEFAULT 0,      -- distinct single-use migrated creators funded
        wrap_close_count  INTEGER DEFAULT 0,      -- how many via the wrap-close mechanism
        mechanism         TEXT,                   -- WRAP_CLOSE | PLAIN_XFER | MIXED
        peak_mc_sum       REAL DEFAULT 0,
        last_launch_at    INTEGER,
        is_known_treasury INTEGER DEFAULT 0,
        funder_type       TEXT,                   -- OPERATOR | CEX  (a CEX-funded cluster is still
        cex_label         TEXT,                   --   valuable: an operator funding via CEX
        first_detected    INTEGER NOT NULL DEFAULT (strftime('%s','now')),  -- withdrawals = an
        updated_at        INTEGER NOT NULL DEFAULT (strftime('%s','now'))   -- upstream lead, not noise)
    )""")
    # migrate: add funder_type/cex_label to a pre-existing table
    try:
        _cols = {r[1] for r in conn.execute("PRAGMA table_info(wt_farms)").fetchall()}
        if "funder_type" not in _cols:
            conn.execute("ALTER TABLE wt_farms ADD COLUMN funder_type TEXT")
        if "cex_label" not in _cols:
            conn.execute("ALTER TABLE wt_farms ADD COLUMN cex_label TEXT")
    except Exception:
        pass
    conn.execute("""CREATE TABLE IF NOT EXISTS wt_farm_launches (
        mint            TEXT PRIMARY KEY,
        funder          TEXT NOT NULL,
        creator         TEXT,
        peak_mc         REAL,
        migrated_at     INTEGER,
        wrap_close      INTEGER DEFAULT 0,
        seed_sol        REAL,
        detected_at     INTEGER NOT NULL DEFAULT (strftime('%s','now'))
    )""")
    # which creators we've already traced (avoid re-RPC)
    conn.execute("""CREATE TABLE IF NOT EXISTS wt_farm_checked (
        creator TEXT PRIMARY KEY, checked_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_farm_launch_funder ON wt_farm_launches(funder)")
    # The farm row remains the compact recurrence projection.  Rich transaction
    # facts live canonically in wt_walkback_transaction_roles; this table is only
    # a stable, idempotent link to that evidence for future zero-RPC reuse.
    conn.execute("""CREATE TABLE IF NOT EXISTS wt_farm_launch_evidence (
        mint TEXT PRIMARY KEY, funder TEXT NOT NULL, creator TEXT,
        funding_signature TEXT NOT NULL, role_evidence_key TEXT NOT NULL,
        transfer_block_time INTEGER, launch_time INTEGER,
        provenance TEXT NOT NULL, retained_at INTEGER NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_farm_launch_evidence_role ON wt_farm_launch_evidence(role_evidence_key)")
    conn.commit()


def ensure_farm_evidence_schema(conn) -> None:
    """Additive migration for existing farm databases."""
    conn.execute("""CREATE TABLE IF NOT EXISTS wt_farm_launch_evidence (
        mint TEXT PRIMARY KEY, funder TEXT NOT NULL, creator TEXT,
        funding_signature TEXT NOT NULL, role_evidence_key TEXT NOT NULL,
        transfer_block_time INTEGER, launch_time INTEGER,
        provenance TEXT NOT NULL, retained_at INTEGER NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_farm_launch_evidence_role ON wt_farm_launch_evidence(role_evidence_key)")
    conn.commit()


def _rpc(method, params, retry=2):
    if not _RPC:
        key = os.environ.get("HELIUS_API_KEY", "")
        url = f"https://mainnet.helius-rpc.com/?api-key={key}"
    else:
        url = _RPC
    for a in range(retry):
        try:
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            return json.loads(urllib.request.urlopen(req, timeout=15).read()).get("result")
        except Exception:
            time.sleep(0.5 * (a + 1))
    return None


@dataclass(frozen=True)
class FunderTrace:
    funder: str | None
    wrap_close: bool
    seed_sol: float
    signature: str | None = None
    block_time: int | None = None
    transaction: dict | None = None


def _trace_funder_evidence(creator: str) -> FunderTrace:
    """The creator's funder = the SOL sender in its OLDEST tx. Also detect whether that
    funding tx is a wrap-close (syncNative+closeAccount). Returns (funder, wrap_close, seed)."""
    sigs = _rpc("getSignaturesForAddress", [creator, {"limit": 1000}])
    if not sigs:
        return FunderTrace(None, False, 0.0)
    # page to the true oldest (busy creators rare, but be safe)
    before = sigs[-1]["signature"]
    while len(sigs) == 1000:
        nx = _rpc("getSignaturesForAddress", [creator, {"limit": 1000, "before": before}])
        if not nx:
            break
        sigs = nx; before = sigs[-1]["signature"]
    tx = _rpc("getTransaction", [sigs[-1]["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
    if not tx:
        return FunderTrace(None, False, 0.0, sigs[-1]["signature"])
    meta = tx.get("meta") or {}
    keys = [k.get("pubkey") if isinstance(k, dict) else k
            for k in (tx.get("transaction", {}).get("message", {}).get("accountKeys") or [])]
    pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
    types = {ix.get("parsed", {}).get("type")
             for ix in (tx.get("transaction", {}).get("message", {}).get("instructions") or [])
             if isinstance(ix.get("parsed"), dict)}
    wc = "closeAccount" in types and "syncNative" in types
    if creator in keys:
        ci = keys.index(creator)
        seed = (post[ci] - pre[ci]) / 1e9 if ci < min(len(pre), len(post)) else 0.0
        cand = sorted([(keys[i], post[i] - pre[i]) for i in range(min(len(pre), len(post), len(keys)))
                       if keys[i] != creator], key=lambda x: x[1])
        return FunderTrace(cand[0][0] if cand else None, wc, seed, sigs[-1]["signature"], tx.get("blockTime"), tx)
    return FunderTrace(None, wc, 0.0, sigs[-1]["signature"], tx.get("blockTime"), tx)


def _trace_funder(creator: str):
    """Compatibility projection of the richer trace used by older callers."""
    trace = _trace_funder_evidence(creator)
    return trace.funder, trace.wrap_close, trace.seed_sol


def _persist_farm_launch_evidence(conn, *, mint: str, creator: str, trace: FunderTrace,
                                  launch_time: int | None) -> str | None:
    """Persist generic roles once, then link the compact farm row to them.

    This is called only after the transaction was already acquired by the farm
    scanner.  It never performs RPC and is intentionally fail-closed: an absent
    or undecodable trace results in no link, not inferred quality evidence.
    """
    if not trace.funder or not trace.signature or not trace.transaction:
        return None
    from src.core.deep_walkback import persist_decoded_transaction_roles
    role_key = persist_decoded_transaction_roles(
        conn, mint=mint, signature=trace.signature, anchor_signature=None,
        tx=trace.transaction,
    )
    conn.execute("""INSERT INTO wt_farm_launch_evidence
        (mint,funder,creator,funding_signature,role_evidence_key,transfer_block_time,launch_time,provenance,retained_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(mint) DO UPDATE SET
          role_evidence_key=excluded.role_evidence_key,
          transfer_block_time=excluded.transfer_block_time,
          launch_time=excluded.launch_time,
          retained_at=excluded.retained_at
    """, (mint, trace.funder, creator, trace.signature, role_key, trace.block_time,
          launch_time, "farm_detector_oldest_creator_transaction.v1", int(time.time())))
    return role_key


def run_farm_scan(lookback_days: int = 3, max_rpc: int = 250, quiet: bool = False) -> dict:
    """Scan recent single-use-creator migrated tokens, trace each NEW creator's funder, cluster
    into farms. Idempotent: only traces creators not already in wt_farm_checked (bounded RPC).
    Lock-resilient: busy_timeout + retry on the DDL, so the lock storm doesn't fail the scan."""
    # Plain sqlite3 connects (not the db_connect wrapper, which runs startup checks that log
    # DB_LOCK_ERROR and fail under the lock storm). Opening a connection takes no lock; we add
    # busy_timeout so the actual reads/writes wait-and-retry instead of erroring.
    oc = sqlite3.connect(OPS_DB, timeout=30)
    oc.execute("PRAGMA busy_timeout=20000")
    # LIVE db is read-only here (we only SELECT token_analysis; all writes go to the ops db).
    # A read-only URI connection bypasses write locks entirely in WAL mode — essential because
    # the live db is heavily WAL-pinned by the listener/API.
    lc = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True, timeout=20)
    # schema DDL takes a write lock — only run it if the tables don't exist yet (after first
    # run they always do). Re-running CREATE TABLE every scan needlessly competed for the write
    # lock under the storm and was the failing op.
    try:
        have = oc.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='wt_farms'").fetchone()
        if not have:
            for _a in range(3):
                try:
                    ensure_farm_schema(oc); break
                except Exception:
                    time.sleep(1.5 * (_a + 1))
    except Exception:
        pass
    # Existing installations already have wt_farms, so this separate additive
    # migration must not be hidden behind the first-install schema branch.
    try:
        ensure_farm_evidence_schema(oc)
        from src.core.deep_walkback import ensure_schema as ensure_walkback_schema
        ensure_walkback_schema(oc)
    except Exception:
        pass
    try:
        known = {r[0] for r in oc.execute("SELECT treasury FROM wt_confirmed_treasuries").fetchall()}
        # self-heal: any creator we ALREADY have a launch for is already checked — seed it so we
        # never re-RPC-trace creators we've resolved (a partial earlier write left checked far
        # behind launches, causing ~200 redundant traces/scan).
        try:
            oc.execute("INSERT OR IGNORE INTO wt_farm_checked (creator) "
                       "SELECT DISTINCT creator FROM wt_farm_launches WHERE creator IS NOT NULL")
            oc.commit()
        except Exception:
            pass
        checked = {r[0] for r in oc.execute("SELECT creator FROM wt_farm_checked").fetchall()}
        rows = lc.execute(
            """SELECT ta.mint, ta.earliest_tx_creator, ta.market_cap_highest, ta.migrated_at
               FROM token_analysis ta
               JOIN (SELECT earliest_tx_creator cr, COUNT(*) cnt FROM token_analysis
                     WHERE earliest_tx_creator IS NOT NULL GROUP BY earliest_tx_creator) cc
                 ON cc.cr = ta.earliest_tx_creator
               WHERE ta.migrated_at > strftime('%s','now', ?)
                 AND cc.cnt = 1 AND ta.earliest_tx_creator IS NOT NULL
               ORDER BY ta.migrated_at DESC""", (f"-{lookback_days} days",)).fetchall()
        # BUFFER all writes during the RPC loop (no DB touch) — a transaction held open across
        # the slow getSignatures/getTransaction calls straddled the ops-db WAL and lost the lock
        # race to the scheduler. Collect rows, write them ALL at the end in one short txn.
        rpc_used = 0
        checked_rows, launch_rows = [], []
        for mint, creator, peak, mig in rows:
            if creator in checked or rpc_used >= max_rpc:
                continue
            trace = _trace_funder_evidence(creator)
            rpc_used += 2
            checked_rows.append((creator,))
            if not trace.funder:
                continue
            launch_rows.append((mint, trace.funder, creator, peak, mig, 1 if trace.wrap_close else 0, round(trace.seed_sol, 4)))
            # Keep raw transaction objects out of the long RPC loop and persist
            # them only in the short write transaction below.
            launch_rows[-1] = launch_rows[-1] + (trace,)

        # single short write transaction, retried on lock (ops db is contended by the scheduler)
        for _attempt in range(4):
            try:
                if checked_rows:
                    oc.executemany("INSERT OR IGNORE INTO wt_farm_checked (creator) VALUES (?)", checked_rows)
                if launch_rows:
                    oc.executemany(
                        "INSERT OR REPLACE INTO wt_farm_launches "
                        "(mint, funder, creator, peak_mc, migrated_at, wrap_close, seed_sol) "
                        "VALUES (?,?,?,?,?,?,?)", [row[:7] for row in launch_rows])
                    for mint, _funder, creator, _peak, mig, _wc, _seed, trace in launch_rows:
                        _persist_farm_launch_evidence(oc, mint=mint, creator=creator, trace=trace, launch_time=mig)
                oc.commit()
                break
            except Exception as e:
                if "locked" in str(e).lower() and _attempt < 3:
                    time.sleep(1.5 * (_attempt + 1)); continue
                raise
        # rebuild the farm aggregate from launches (funders with ≥2 launches = a farm)
        oc.execute("DELETE FROM wt_farms")
        agg = defaultdict(lambda: {"n": 0, "wc": 0, "peak": 0.0, "last": 0})
        for r in oc.execute("SELECT funder, wrap_close, peak_mc, migrated_at FROM wt_farm_launches").fetchall():
            a = agg[r[0]]; a["n"] += 1; a["wc"] += (r[1] or 0)
            a["peak"] += (r[2] or 0); a["last"] = max(a["last"], r[3] or 0)
        farms = 0
        # CEX detection — a funder that is a known exchange hot wallet (Coinbase, Binance, …) is
        # NOT an operator, but the cluster is STILL valuable: creators all withdrawing from the
        # same CEX may be one operator obfuscating via withdrawals, and the CEX is an upstream
        # investigation point. So we TAG it (funder_type=CEX + exchange), not exclude it.
        try:
            from src.utils.infra_mapping import get_funder_label
        except Exception:
            get_funder_label = lambda a: None
        now = int(time.time())
        for funder, a in agg.items():
            if a["n"] < 2:
                continue
            mech = "WRAP_CLOSE" if a["wc"] >= a["n"] else ("PLAIN_XFER" if a["wc"] == 0 else "MIXED")
            lbl = get_funder_label(funder)
            # CEX / PLATFORM / INFRA = a shared service (Coinbase, Axiom Trade, …), not an operator.
            # The cluster is still valuable (an operator may fund via a shared service to obfuscate),
            # so TAG it; only an UNLABELED funder is a candidate OPERATOR.
            funder_type = lbl["kind"] if lbl else "OPERATOR"
            cex_label = lbl["name"] if lbl else None
            oc.execute(
                """INSERT OR REPLACE INTO wt_farms
                   (funder, creator_count, wrap_close_count, mechanism, peak_mc_sum,
                    last_launch_at, is_known_treasury, funder_type, cex_label, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (funder, a["n"], a["wc"], mech, a["peak"], a["last"],
                 1 if funder in known else 0, funder_type, cex_label, now))
            farms += 1
        oc.commit()
        s = {"scanned": len(rows), "rpc_used": rpc_used, "farms": farms,
             "launches_total": sum(1 for _ in oc.execute("SELECT 1 FROM wt_farm_launches"))}
        if not quiet:
            print(f"[FARM_SCAN] scanned={len(rows)} rpc={rpc_used} farms={farms}", flush=True)
        return {"status": "OK", "summary": s}
    except Exception as exc:
        if not quiet:
            print(f"[FARM_SCAN] error: {exc}", flush=True)
        return {"status": "ERROR", "error": str(exc)}
    finally:
        oc.close(); lc.close()


def farm_for_mints(conn, mints: list) -> dict:
    """mint → {funder, mechanism, farm_creator_count, is_known} for any mint that's a farm launch.
    Zero RPC — pure DB read of the persisted clustering. For the token-performance page tag."""
    if not mints:
        return {}
    out = {}
    try:
        ph = ",".join("?" * len(mints))
        for r in conn.execute(
            f"""SELECT l.mint, l.funder, f.mechanism, f.creator_count, f.is_known_treasury, l.wrap_close
                FROM wt_farm_launches l JOIN wt_farms f ON f.funder = l.funder
                WHERE l.mint IN ({ph})""", list(mints)).fetchall():
            out[r[0]] = {"funder": r[1], "mechanism": r[2], "farm_creator_count": r[3],
                         "is_known_treasury": bool(r[4]), "wrap_close": bool(r[5])}
    except Exception:
        pass
    return out


if __name__ == "__main__":
    import sys
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    print(json.dumps(run_farm_scan(lookback_days=days), indent=2))
