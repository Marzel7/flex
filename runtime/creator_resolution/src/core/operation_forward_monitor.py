#!/usr/bin/env python3
"""
Phase 2 — Forward Expansion Monitor.

Phase 1 reconstructs operations BACKWARD from a migrated creator (post-mortem).
Phase 2 watches known operation infrastructure FORWARD and detects expansion the
moment new wallets appear — BEFORE any token migrates.

    Treasury -> Collector -> Pass-through -> NEW fresh child wallet
                                              ^ detected & persisted here, pre-launch.

Design principles:
  * The OPERATION is the monitored entity — not creators, tokens, or WATCH scores.
  * This is NOT an attribution engine. It answers "is this operation expanding right
    now?", not "is this WATCHTOWER?". Identity comes later.
  * Incremental only: track last_signature_seen per wallet; never retrace known infra.
  * Isolated: reads live DB read-only for nothing here (pure RPC), writes ONLY to
    wt_ops_v2.db. Touches no live WATCH/attribution/dashboard tables.

Run:
    python -m src.core.operation_forward_monitor               # monitor all operations
    python -m src.core.operation_forward_monitor --max-rpc 200 --limit-wallets 60
    python -m src.core.operation_forward_monitor --op <treasury_prefix>
"""

from __future__ import annotations

import os
import sys
import json
import time
import uuid
import random as _r
import urllib.request
import urllib.error
from collections import defaultdict
from typing import Optional

from src.utils.db_locking import db_connect
from src.core.operation_discovery_poc import ATA_RENT_LAMPORTS

try:
    from src.core.rpc_cache import RPCCache
except Exception:
    RPCCache = None

OPS_DB_PATH = os.environ.get(
    "OPS_V2_DB_PATH",
    os.path.join(os.path.dirname(__file__), "..", "..", "database", "wt_ops_v2.db"),
)
HELIUS_API_KEY = os.environ.get("HELIUS_API_KEY", "")
HELIUS_RPC_URL = os.environ.get(
    "HELIUS_RPC_URL", f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}")

MIN_SOL = 0.01
TEMPLATE_BASES = {1.10, 1.11, 2.10, 0.605, 5.10}     # known WT-like library (leads only)
DORMANT_AFTER_S = 3 * 86400                            # no activity for 3d -> DORMANT


# ─────────────────────────── schema (additive, isolated) ───────────────────
def ensure_schema(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS wt_operation_activity (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            operation_uuid TEXT NOT NULL,
            wallet         TEXT NOT NULL,        -- the known op wallet that acted
            counterparty   TEXT,                 -- the other side (often a new child)
            event_type     TEXT NOT NULL,        -- NEW_CHILD | FUNDING | NEW_PASS_THROUGH | NEW_CREATOR_CANDIDATE
            amount         REAL,
            signature      TEXT,
            block_time     INTEGER,
            created_at     INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            UNIQUE(signature, wallet, counterparty, event_type)
        );

        CREATE TABLE IF NOT EXISTS wt_operation_candidates (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            operation_uuid TEXT NOT NULL,
            wallet         TEXT NOT NULL,        -- the new child wallet (future creator?)
            source_wallet  TEXT,                 -- which op wallet funded it
            source_role    TEXT,                 -- TREASURY|COLLECTOR|PASS_THROUGH
            amount         REAL,
            template_base  REAL,                 -- stripped ATA-rent base, if it matches
            confidence     REAL NOT NULL DEFAULT 0.0,
            first_seen     INTEGER NOT NULL,
            last_seen      INTEGER NOT NULL,
            status         TEXT NOT NULL DEFAULT 'PENDING',
            -- PENDING | FUNDED | CREATOR | MIGRATED | DISCARDED
            UNIQUE(operation_uuid, wallet)
        );

        CREATE TABLE IF NOT EXISTS wt_operation_lifecycle (
            operation_uuid TEXT PRIMARY KEY,
            state          TEXT NOT NULL DEFAULT 'DISCOVERED',
            -- DISCOVERED | ACTIVE | PROVISIONING | CREATORS_SEEN | MIGRATED | DORMANT | REACTIVATED
            last_changed   INTEGER NOT NULL,
            last_activity  INTEGER
        );

        -- per-wallet incremental cursor so we never re-scan known history
        CREATE TABLE IF NOT EXISTS wt_operation_wallet_cursor (
            wallet              TEXT PRIMARY KEY,
            last_signature_seen TEXT,
            last_checked_at     INTEGER
        );

        CREATE INDEX IF NOT EXISTS ix_woa_op ON wt_operation_activity(operation_uuid);
        CREATE INDEX IF NOT EXISTS ix_woc_op ON wt_operation_candidates(operation_uuid);
        """
    )
    conn.commit()


def _rpc_get_with_backoff(url: str, max_attempts: int = 4):
    """GET JSON with jittered exponential backoff on 429/5xx/timeout/network errors.
    Returns the decoded list/dict, or None if all attempts fail. Never raises."""
    import random as _r
    for attempt in range(max_attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "flex-fwd-monitor/0.1"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < max_attempts - 1:
                time.sleep(min(8.0, (2 ** attempt)) + _r.random())   # backoff + jitter
                continue
            return None
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError):
            if attempt < max_attempts - 1:
                time.sleep(min(8.0, (2 ** attempt)) + _r.random())
                continue
            return None
    return None


# ─────────────────────────── RPC: cheap change-check ────────────────────────
def _newest_signature(addr: str) -> Optional[str]:
    """1-credit getSignaturesForAddress(limit=1) — the newest signature for `addr`.
    Used to gate the 100-credit enhanced-tx fetch: if the newest sig matches our
    cursor, nothing changed and we skip the expensive call entirely. Returns the
    signature, or None on error/empty (caller treats None as 'fetch to be safe')."""
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "getSignaturesForAddress",
        "params": [addr, {"limit": 1}],
    }).encode()
    try:
        req = urllib.request.Request(
            HELIUS_RPC_URL, data=body,
            headers={"Content-Type": "application/json", "User-Agent": "flex-fwd/0.1"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    res = json.loads(r.read().decode()).get("result") or []
                    return res[0]["signature"] if res else None
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(min(8.0, 2 ** attempt) + _r.random()); continue
                return None
            except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError):
                if attempt < 2:
                    time.sleep(min(8.0, 2 ** attempt) + _r.random()); continue
                return None
    except Exception:
        return None
    return None


# ─────────────────────────── RPC: incremental signatures ────────────────────
def _rpc_post(method: str, params: list):
    """One raw JSON-RPC call (1 credit). Returns result or None."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                HELIUS_RPC_URL, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode()).get("result")
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep(min(8.0, 2 ** attempt) + _r.random()); continue
            return None
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError):
            if attempt < 2:
                time.sleep(1.0 + _r.random()); continue
            return None
    return None


def _native_transfers_from_raw(tx: dict) -> list:
    """Derive nativeTransfers (the only field forward-expansion needs) from a raw
    getTransaction. Reconstructs per-account SOL deltas from pre/postBalances —
    EQUIVALENT to Helius's nativeTransfers for our purpose, at 1 credit instead of 100.

    Emits Helius-compatible dicts: {fromUserAccount, toUserAccount, amount(lamports)}.
    For a simple transfer the signer (index 0, minus fee) is the source; positive-delta
    accounts are destinations. This covers the provisioning transfers we track."""
    meta = (tx or {}).get("meta") or {}
    msg = (tx or {}).get("transaction", {}).get("message", {})
    keys = msg.get("accountKeys", [])
    keys = [k.get("pubkey") if isinstance(k, dict) else k for k in keys]
    pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
    if not keys or len(pre) != len(keys) or len(post) != len(keys):
        return []
    fee = meta.get("fee", 0) or 0
    deltas = []
    for i, k in enumerate(keys):
        d = post[i] - pre[i]
        if i == 0:                      # fee payer: add the fee back to see the true transfer
            d += fee
        deltas.append((k, d))
    senders = [(k, -d) for k, d in deltas if d < 0]
    receivers = [(k, d) for k, d in deltas if d > 0]
    if not senders or not receivers:
        return []
    src = max(senders, key=lambda x: x[1])[0]   # the largest debit = the funder
    out = []
    for k, amt in receivers:
        if amt > 0:
            out.append({"fromUserAccount": src, "toUserAccount": k, "amount": amt})
    return out


def _addr_txs(addr: str, cache, until_sig: Optional[str]) -> tuple[list, Optional[str]]:
    """Fetch recent txs for `addr` as {signature, timestamp, nativeTransfers}, stopping
    at `until_sig`. RAW RPC path (getSignaturesForAddress + getTransaction, ~1 credit each)
    instead of the 100-credit enhanced endpoint — forward expansion only needs native
    SOL transfers, which we reconstruct from balance deltas. Returns (new_txs, newest_sig).
    """
    sigs = _rpc_post("getSignaturesForAddress", [addr, {"limit": 100}]) or []
    if not sigs:
        return [], until_sig
    newest_sig = sigs[0].get("signature")
    new = []
    for s in sigs:
        sig = s.get("signature")
        if until_sig and sig == until_sig:
            break                       # reached previously-seen frontier
        if s.get("err"):
            continue                    # failed tx — no transfer
        tx = _rpc_post("getTransaction",
                       [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
        if not tx:
            continue
        new.append({
            "signature": sig,
            "timestamp": s.get("blockTime") or tx.get("blockTime", 0),
            "nativeTransfers": _native_transfers_from_raw(tx),
        })
    return new, newest_sig


# ─────────────────────────── child detection ───────────────────────────────
MIN_TEMPLATE_BASE = 0.05    # below this the "base" is just rent/dust, not a real seed
MAX_TEMPLATE_BASE = 7.0     # above this it's a treasury/infra capital load, NOT a creator
                            # seed (creators get ~0.2–6.16 SOL; matches the arm-gate cap).
                            # Large transfers that merely carry the …039280 tail (e.g. a
                            # 242-SOL treasury→sub-prov load) must not flag a pre-launch creator.


def _template_base(amount_sol: float) -> Optional[float]:
    """Stripped base of an ATA-rent-tailed funding transfer, IF it's a meaningful
    seed (>= MIN_TEMPLATE_BASE). Tiny dust transfers that merely carry the rent tail
    are NOT provisioning templates and must not flag a creator candidate."""
    lamports = round(amount_sol * 1e9)
    if lamports % 1_000_000 == ATA_RENT_LAMPORTS % 1_000_000:
        base = round((lamports - ATA_RENT_LAMPORTS) / 1e9, 4)
        if MIN_TEMPLATE_BASE <= base <= MAX_TEMPLATE_BASE:
            return base
    return None


def _known_wallets(conn) -> dict:
    """All wallets already attached to any operation -> (operation_uuid, role)."""
    rows = conn.execute(
        "SELECT wallet, operation_uuid, role FROM wt_ops_v2_wallets").fetchall()
    out = {}
    for w, op, role in rows:
        out[w] = (op, role)
    return out


def _candidate_confidence(amount_sol: float, template_base: Optional[float],
                          source_role: str) -> float:
    """Lead confidence (NOT attribution). Fresh + template + from collector = strong."""
    c = 0.2                                    # any new funded child is a weak lead
    if template_base is not None:
        c += 0.3                               # carries the ATA-rent funding template
    if template_base in TEMPLATE_BASES:
        c += 0.2                               # matches the known WT-like library
    if source_role in ("COLLECTOR", "TREASURY"):
        c += 0.2                               # funded by a hub, not a spent forwarder
    return round(min(c, 1.0), 2)


# ─────────────────────────── state machine ─────────────────────────────────
def _get_state(conn, op_uuid: str) -> Optional[str]:
    row = conn.execute("SELECT state FROM wt_operation_lifecycle WHERE operation_uuid=?",
                       (op_uuid,)).fetchone()
    return row[0] if row else None


def _set_state(conn, op_uuid: str, state: str, now: int, last_activity: Optional[int] = None):
    conn.execute(
        """INSERT INTO wt_operation_lifecycle (operation_uuid, state, last_changed, last_activity)
           VALUES (?,?,?,?)
           ON CONFLICT(operation_uuid) DO UPDATE SET
             state=excluded.state, last_changed=excluded.last_changed,
             last_activity=COALESCE(excluded.last_activity, wt_operation_lifecycle.last_activity)""",
        (op_uuid, state, now, last_activity))


def _advance_state(conn, op_uuid: str, now: int, saw_children: bool,
                   saw_candidates: bool, last_activity: Optional[int]) -> Optional[str]:
    """Drive the lifecycle forward automatically based on what this poll observed."""
    cur = _get_state(conn, op_uuid)
    # initialise
    if cur is None:
        _set_state(conn, op_uuid, "DISCOVERED", now, last_activity)
        cur = "DISCOVERED"

    # migrations observed (creators that have token migrations) -> MIGRATED
    migrated = conn.execute(
        "SELECT 1 FROM wt_ops_v2_creators WHERE operation_uuid=? AND migration_time IS NOT NULL LIMIT 1",
        (op_uuid,)).fetchone()

    new_state = cur
    if saw_candidates:
        new_state = "CREATORS_SEEN"
    elif saw_children:
        new_state = "PROVISIONING"
    elif cur == "DISCOVERED":
        new_state = "ACTIVE"

    # dormancy / reactivation by activity recency
    la = conn.execute("SELECT last_activity FROM wt_operation_lifecycle WHERE operation_uuid=?",
                      (op_uuid,)).fetchone()
    la = (la[0] if la else None) or last_activity
    if not (saw_children or saw_candidates) and la and (now - la) > DORMANT_AFTER_S:
        new_state = "DORMANT"
    if (saw_children or saw_candidates) and cur == "DORMANT":
        new_state = "REACTIVATED"

    if migrated and new_state in ("PROVISIONING", "CREATORS_SEEN", "ACTIVE") and not (saw_children or saw_candidates):
        new_state = "MIGRATED"

    if new_state != cur:
        _set_state(conn, op_uuid, new_state, now, last_activity)
    elif last_activity:
        conn.execute("UPDATE wt_operation_lifecycle SET last_activity=? WHERE operation_uuid=?",
                     (max(la or 0, last_activity), op_uuid))
    return new_state


# ─────────────────────────── the forward monitor ───────────────────────────
def _follow_child_for_creators(conn, cache, op_uuid, child, parent_role, known,
                               s: dict, op_saw_candidates: dict):
    """Peek one hop below a freshly-funded forwarder child to catch the actual
    template-funded CREATOR before it launches. Bounded: one RPC, outbound only."""
    txs, _ = _addr_txs(child, cache, None)
    s["rpc_calls"] += 1
    for t in txs:
        ts = t.get("timestamp", 0) or 0
        sig = t.get("signature")
        for nt in t.get("nativeTransfers", []):
            amt = nt.get("amount", 0) / 1e9
            if amt < MIN_SOL or nt.get("fromUserAccount") != child:
                continue
            grandchild = nt.get("toUserAccount")
            tbase = _template_base(amt)
            if tbase is None or grandchild in known:
                continue                         # only template-funded fresh creators
            conn.execute(
                """INSERT OR IGNORE INTO wt_operation_activity
                   (operation_uuid, wallet, counterparty, event_type, amount, signature, block_time)
                   VALUES (?,?,?, 'NEW_CREATOR_CANDIDATE', ?,?,?)""",
                (op_uuid, child, grandchild, round(amt, 9), sig, ts))
            conf = _candidate_confidence(amt, tbase, "PASS_THROUGH")
            c = conn.execute(
                """INSERT OR IGNORE INTO wt_operation_candidates
                   (operation_uuid, wallet, source_wallet, source_role, amount,
                    template_base, confidence, first_seen, last_seen, status)
                   VALUES (?,?,?, 'PASS_THROUGH', ?,?,?,?,?, 'PENDING')""",
                (op_uuid, grandchild, child, round(amt, 9), tbase, conf, ts, ts))
            if c.rowcount > 0:
                s["new_candidates"] += 1
                s["new_creators"] += 1           # a real template-funded creator lead
                op_saw_candidates[op_uuid] = True


def operation_forward_monitor(limit_wallets: Optional[int] = None,
                              max_rpc: Optional[int] = None,
                              only_treasury_prefix: Optional[str] = None,
                              follow_children: bool = True,
                              verbose=True) -> dict:
    """Poll known operation infrastructure for NEW outbound children. Persist
    expansion events + candidate creators, advance lifecycle states. Incremental."""
    t0 = time.time()
    conn = db_connect(OPS_DB_PATH, timeout=30)
    ensure_schema(conn)
    now = int(time.time())
    known = _known_wallets(conn)
    cache = RPCCache(OPS_DB_PATH) if RPCCache is not None else None

    # Which wallets to poll: ONLY persistent producers — TREASURY + COLLECTOR.
    # PASS_THROUGH is single-use (each funds exactly one creator, then dies), so
    # polling them is wasted budget on guaranteed-dead wallets. Prioritise wallets
    # on ACTIVE operations (PROVISIONING/CREATORS_SEEN/REACTIVATED) over MIGRATED,
    # over DORMANT — that's where a new creator can actually appear.
    where = "w.role IN ('TREASURY','COLLECTOR')"
    params: list = []
    if only_treasury_prefix:
        # DIAGNOSTIC-ONLY prefix match — `only_treasury_prefix` is set exclusively by the CLI
        # `--op` debug flag (see __main__), never in the production scheduler path. Do NOT use
        # this prefix LIKE for real attribution: distinct treasuries can share a prefix
        # (43PKjr22AFXt…3y3D vs …n7vh). Pass a full treasury_root to scope to exactly one.
        where += """ AND w.operation_uuid IN
                     (SELECT operation_uuid FROM wt_ops_v2 WHERE treasury_root LIKE ?)"""
        params.append(only_treasury_prefix + "%")
    wallets = conn.execute(
        f"""SELECT DISTINCT w.wallet, w.operation_uuid, w.role
            FROM wt_ops_v2_wallets w
            LEFT JOIN wt_operation_lifecycle l ON l.operation_uuid = w.operation_uuid
            WHERE {where}
            ORDER BY
              CASE COALESCE(l.state,'')
                WHEN 'PROVISIONING'  THEN 0 WHEN 'CREATORS_SEEN' THEN 0
                WHEN 'REACTIVATED'   THEN 0 WHEN 'ACTIVE'        THEN 1
                WHEN 'MIGRATED'      THEN 2 WHEN 'DORMANT'       THEN 4
                ELSE 3 END,
              CASE w.role WHEN 'TREASURY' THEN 0 WHEN 'COLLECTOR' THEN 1 ELSE 2 END,
              COALESCE(l.last_activity, 0) DESC""",
        params).fetchall()
    if limit_wallets:
        wallets = wallets[:limit_wallets]

    s = {"wallets_polled": 0, "rpc_calls": 0, "new_children": 0, "new_candidates": 0,
         "new_creators": 0, "new_migrations": 0, "wallets_skipped": 0, "cheap_checks": 0,
         "state_changes": [], "per_op": defaultdict(lambda: defaultdict(int))}
    op_activity_ts: dict = defaultdict(int)
    op_saw_children: dict = defaultdict(bool)
    op_saw_candidates: dict = defaultdict(bool)

    for wallet, op_uuid, role in wallets:
        if max_rpc is not None and s["rpc_calls"] >= max_rpc:
            if verbose:
                print(f"[FWD] max-rpc {max_rpc} reached — stopping early")
            break
        cur = conn.execute(
            "SELECT last_signature_seen FROM wt_operation_wallet_cursor WHERE wallet=?",
            (wallet,)).fetchone()
        until_sig = cur[0] if cur else None

        # ── CHEAP GATE: 1-credit signature check before the 100-credit enhanced fetch.
        # If the newest signature matches our cursor, nothing changed → skip the
        # expensive fetch. Dormant wallets (the majority) cost 1 credit, not 100.
        # Only gate wallets we've seen before (until_sig set); first-time wallets fetch.
        if until_sig is not None:
            newest = _newest_signature(wallet)
            s["rpc_calls"] += 1            # the cheap check (1 credit)
            s["cheap_checks"] += 1
            if newest is not None and newest == until_sig:
                conn.execute(
                    "UPDATE wt_operation_wallet_cursor SET last_checked_at=? WHERE wallet=?",
                    (now, wallet))
                s["wallets_skipped"] += 1
                continue                   # no change — skip the 100-credit fetch

        new_txs, newest_sig = _addr_txs(wallet, cache, until_sig)
        s["rpc_calls"] += 1
        s["wallets_polled"] += 1
        # advance cursor regardless (so we never re-scan this frontier)
        conn.execute(
            """INSERT INTO wt_operation_wallet_cursor (wallet, last_signature_seen, last_checked_at)
               VALUES (?,?,?)
               ON CONFLICT(wallet) DO UPDATE SET last_signature_seen=excluded.last_signature_seen,
                 last_checked_at=excluded.last_checked_at""",
            (wallet, newest_sig or until_sig, now))

        for t in new_txs:
            ts = t.get("timestamp", 0) or 0
            sig = t.get("signature")
            for nt in t.get("nativeTransfers", []):
                amt = nt.get("amount", 0) / 1e9
                if amt < MIN_SOL:
                    continue
                if nt.get("fromUserAccount") != wallet:
                    continue                     # only OUTBOUND from the known op wallet
                child = nt.get("toUserAccount")
                if not child:
                    continue
                op_activity_ts[op_uuid] = max(op_activity_ts[op_uuid], ts)

                child_known = child in known
                tbase = _template_base(amt)
                # classify the event
                if not child_known:
                    event = "NEW_CHILD"
                    if tbase is not None:
                        event = "NEW_CREATOR_CANDIDATE"
                else:
                    event = "FUNDING"           # movement between known op wallets

                # record activity (idempotent on signature+pair+type)
                cur2 = conn.execute(
                    """INSERT OR IGNORE INTO wt_operation_activity
                       (operation_uuid, wallet, counterparty, event_type, amount, signature, block_time)
                       VALUES (?,?,?,?,?,?,?)""",
                    (op_uuid, wallet, child, event, round(amt, 9), sig, ts))
                if cur2.rowcount == 0:
                    continue                     # already seen this exact event

                if event in ("NEW_CHILD", "NEW_CREATOR_CANDIDATE"):
                    s["new_children"] += 1
                    op_saw_children[op_uuid] = True
                    s["per_op"][op_uuid]["children"] += 1
                    # attach a candidate immediately — DO NOT wait for migration
                    conf = _candidate_confidence(amt, tbase, role)
                    c3 = conn.execute(
                        """INSERT OR IGNORE INTO wt_operation_candidates
                           (operation_uuid, wallet, source_wallet, source_role, amount,
                            template_base, confidence, first_seen, last_seen, status)
                           VALUES (?,?,?,?,?,?,?,?,?, 'PENDING')""",
                        (op_uuid, child, wallet, role, round(amt, 9), tbase, conf, ts, ts))
                    if c3.rowcount > 0:
                        s["new_candidates"] += 1
                        s["per_op"][op_uuid]["candidates"] += 1
                    # AUTO-ARM: a template-funded creator → arm + auto-enrol webhook.
                    # Only the real template path (tbase set), never dust/infra.
                    if event == "NEW_CREATOR_CANDIDATE" and tbase is not None:
                        try:
                            from src.core.operation_armed import arm_creator
                            _tr = conn.execute(
                                "SELECT treasury_root FROM wt_ops_v2 WHERE operation_uuid=?",
                                (op_uuid,)).fetchone()
                            treasury = _tr[0] if _tr else None
                            if arm_creator(conn, child, op_uuid, treasury, tbase, ts):
                                s["armed"] = s.get("armed", 0) + 1
                        except Exception as _ae:
                            print(f"[FWD] auto-arm failed: {_ae}", flush=True)
                        if tbase is not None:
                            op_saw_candidates[op_uuid] = True
                        # ONE-HOP FOLLOW: the creator-funding template appears one hop
                        # below the monitored hubs (hub -> forwarder -> CREATOR). When a
                        # hub funds a small forwarder-shaped child, peek at that child's
                        # outbound to catch the template-funded creator BEFORE migration.
                        if (follow_children and tbase is None and 0.5 <= amt <= 10.0
                                and (max_rpc is None or s["rpc_calls"] < max_rpc)):
                            _follow_child_for_creators(
                                conn, cache, op_uuid, child, role, known, s, op_saw_candidates)

    # advance lifecycle states for every touched operation
    # (wallets rows are (wallet, op_uuid, role) — key on op_uuid, the 2nd field)
    touched = set(op_activity_ts) | {op_uuid for _, op_uuid, _ in wallets}
    for op_uuid in touched:
        before = _get_state(conn, op_uuid)
        after = _advance_state(conn, op_uuid, now,
                               op_saw_children[op_uuid], op_saw_candidates[op_uuid],
                               op_activity_ts.get(op_uuid))
        if after != before:
            s["state_changes"].append({"operation": op_uuid, "from": before, "to": after})

    conn.commit()
    conn.close()
    s["per_op"] = {k: dict(v) for k, v in s["per_op"].items()}
    s["runtime_s"] = round(time.time() - t0, 1)
    return s


# ─────────────────────────── alerting / summary ────────────────────────────
def _print_alerts(s: dict):
    # credit estimate: cheap checks = 1 credit, enhanced fetches = 100 credits
    _est_credits = s.get("cheap_checks", 0) * 1 + s["wallets_polled"] * 100
    _naive_credits = (s.get("cheap_checks", 0) + s["wallets_polled"]) * 100
    print("\n================= FORWARD MONITOR SUMMARY =================")
    print(f"  wallets polled   : {s['wallets_polled']}  (100-credit enhanced fetch)")
    print(f"  wallets skipped  : {s.get('wallets_skipped', 0)}  (1-credit sig-check, no change)")
    print(f"  cheap checks     : {s.get('cheap_checks', 0)}")
    print(f"  RPC calls        : {s['rpc_calls']}")
    print(f"  est credits      : ~{_est_credits}  (vs ~{_naive_credits} un-gated — saved ~{_naive_credits - _est_credits})")
    print(f"  NEW children     : {s['new_children']}")
    print(f"  NEW candidates   : {s['new_candidates']}")
    print(f"  state changes    : {len(s['state_changes'])}")
    print(f"  runtime          : {s['runtime_s']}s")
    for sc in s["state_changes"]:
        print(f"    [STATE] {str(sc['operation'])[:8]}…  {sc['from']} -> {sc['to']}")

    conn = db_connect(OPS_DB_PATH, timeout=30)
    print("\n  --- OPERATIONS (live expansion view) ---")
    rows = conn.execute(
        """SELECT substr(o.treasury_root,1,16), substr(o.operation_uuid,1,8),
                  COALESCE(l.state,'-'),
                  (SELECT COUNT(*) FROM wt_operation_candidates c WHERE c.operation_uuid=o.operation_uuid),
                  (SELECT COUNT(*) FROM wt_operation_activity a WHERE a.operation_uuid=o.operation_uuid),
                  datetime(COALESCE(l.last_activity,0),'unixepoch')
           FROM wt_ops_v2 o LEFT JOIN wt_operation_lifecycle l ON l.operation_uuid=o.operation_uuid
           ORDER BY 4 DESC, 5 DESC""").fetchall()
    print(f"  {'treasury':18s} {'state':13s} {'cands':5s} {'events':6s} last_activity")
    for r in rows:
        print(f"  {r[0]:18s} {r[2]:13s} {r[3]:<5} {r[4]:<6} {r[5]}")
    conn.close()


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Phase 2 — forward expansion monitor")
    ap.add_argument("--max-rpc", type=int, default=None)
    ap.add_argument("--limit-wallets", type=int, default=None)
    ap.add_argument("--op", type=str, default=None, help="only monitor ops whose treasury_root starts with this")
    ap.add_argument("--no-follow", action="store_true", help="disable one-hop child follow")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    s = operation_forward_monitor(limit_wallets=args.limit_wallets, max_rpc=args.max_rpc,
                                  only_treasury_prefix=args.op,
                                  follow_children=not args.no_follow, verbose=not args.quiet)
    _print_alerts(s)


if __name__ == "__main__":
    main()
