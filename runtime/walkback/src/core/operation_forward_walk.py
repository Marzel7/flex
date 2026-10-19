"""
Treasury-triggered forward chain-walker — the live early-warning layer.

EMPIRICAL JUSTIFICATION (measured 2026-06-09):
  - 83% of known creators are reachable at HOP 1 from known operational infra.
  - The gap is not discovery or graph distance.
  - Hop-1 creator funders are SINGLE-USE (10/10 sampled funded exactly one creator),
    so they cannot be statically watched.
  - Backward reconstruction (intake) works post-migration; live detection requires
    walking FORWARD from the persistent treasury/collector anchor as the chain forms.

WHAT THIS DOES:
  A tracked treasury/collector emits an outbound transfer → start a temporary,
  TTL-bounded, depth-bounded forward-walk. Follow outbound hops while they look like
  provisioning. When a 1.0–1.3 SOL transfer (weighted to 1.112039280) lands in a FRESH
  wallet, that wallet is the pre-launch creator → create a candidate + ARM it for the
  pump.fun CREATE. Persist every hop to wt_ops_v2_edges; emit lifecycle events to
  watchtower_events.

CONSTRAINTS (per spec):
  - No static pass-through lists. No assumption hop-1 funders are reusable.
  - Treasury/collector infra are the ONLY stable anchors.
  - Narrow real-time follower, NOT a deep graph crawler. max_depth=4, TTL 60-90m/hop.
  - Cheap: 1-credit signature checks gate the heavier enhanced fetches.

Writes only to wt_ops_v2.db (+ watchtower_events in the live DB) + the webhook via
arm_creator. Never touches the live WATCH pipeline.
"""
from __future__ import annotations

import os
import time
import json
import threading
import urllib.request
import urllib.error
from typing import Optional

OPS_DB_PATH = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "database", "wt_ops_v2.db"))
LIVE_DB_PATH = os.environ.get(
    "DB_PATH", os.path.join(os.path.dirname(OPS_DB_PATH), "flex_complete_database.db"))
HELIUS_API_KEY = os.environ.get("HELIUS_API_KEY", "")

# ── tuning ──────────────────────────────────────────────────────────────────
MAX_DEPTH          = int(os.environ.get("FWALK_MAX_DEPTH", "4"))
TTL_SECS           = int(os.environ.get("FWALK_TTL_SECS", str(90 * 60)))   # 90 min/walk
POLL_INTERVAL_SECS = int(os.environ.get("FWALK_POLL_SECS", "30"))

# STRICT ARM GATE = the ATA-RENT-TAIL TEMPLATE FAMILY, not a single amount.
# Empirical (32 known launchers): 27/32 (84%) carry the EXACT ATA-rent tail
# (…2,039,280 lamports) across a few discrete small bases {0.201, 0.605, 1.11, 2.12}.
# The other 5 are random one-offs (no tail). ALL 36 false arms had random tails. So the
# real signature is "small base + ATA-rent tail", which is still strict (the tail is the
# discriminator that killed the false positives) but covers the whole template family,
# not just 1.11. We do NOT hard-code the bases — any base ≤ MAX is accepted if the tail
# matches, so a new template base in the family is caught automatically.
ATA_RENT_LAMPORTS  = 2_039_280
TEMPLATE_TAIL_MOD  = 1_000_000           # match the low 6 lamport digits (…039280)
# The ATA-rent TAIL is the real discriminator — debris all has random tails (verified:
# the 36 false arms had tail=0). So the base ceiling only needs to cover the observed
# template family. Bases seen across operations: {0.201, 0.605, 1.11, 2.12, 6.16} — note
# treasuries that fan out DIRECTLY (no sub-prov layer, e.g. EtyBB1) use larger bases like
# 6.16. Cap at 7 to cover the family + headroom; the tail keeps it strict.
TEMPLATE_BASE_MAX  = 7.0
TEMPLATE_BASE_MIN  = 0.05
# legacy single-amount (kept for the resolver's exact fast-path in main.py)
TEMPLATE_EXACT     = 1.112039280
# the relay chain travels carrying the SAME template amounts — used ONLY to keep FOLLOWING
RELAY_BAND_LO      = 0.05
RELAY_BAND_HI      = 2.5
# provisioning hops we follow (wide, to traverse sub-prov loads); never an arm trigger
PROVISIONING_MIN   = 0.05
PROVISIONING_MAX   = 1000.0

SETTLE_WINDOW_SECS = int(os.environ.get("FWALK_SETTLE_SECS", "60"))   # confirm no-forward
# cadence guard: a real campaign launches ~1 creator / hours, not dozens / minutes.
ARM_CADENCE_MAX    = int(os.environ.get("FWALK_ARM_CADENCE_MAX", "3"))     # arms allowed
ARM_CADENCE_WINDOW = int(os.environ.get("FWALK_ARM_CADENCE_WINDOW", "3600"))  # per hour


def _is_exact_template(amount: float) -> bool:
    """Creator-template = exact ATA-rent tail (…2039280 lamports) + a small base.
    Covers the whole template family ({0.201, 0.605, 1.11, 2.12, …}) — NOT just 1.11 —
    while the ATA-rent tail keeps it strict (random-tailed debris is rejected)."""
    if amount is None or amount <= 0:
        return False
    lam = round(amount * 1e9)
    if lam % TEMPLATE_TAIL_MOD != ATA_RENT_LAMPORTS % TEMPLATE_TAIL_MOD:
        return False                      # tail doesn't match → not a template
    base = round((lam - ATA_RENT_LAMPORTS) / 1e9, 4)
    return TEMPLATE_BASE_MIN <= base <= TEMPLATE_BASE_MAX
FRESH_MAX_TX       = 8     # a fresh creator wallet has very few prior txs

try:
    from src.utils.db_locking import db_connect
except Exception:                                    # pragma: no cover
    import sqlite3
    def db_connect(path, timeout=30):
        c = sqlite3.connect(path, timeout=timeout); c.row_factory = sqlite3.Row; return c

_ACTIVE_WALKS: dict = {}     # session_id -> dict (in-memory view for the UI)
_WALK_LOCK = threading.Lock()
# NOTE: the 100-credit enhanced endpoint (/v0/addresses) has been REMOVED — the walker
# now uses raw getSignaturesForAddress + getTransaction (1cr each) via _raw_txs.
_HELIUS_RPC = os.environ.get("HELIUS_RPC_URL", f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}")


# ── schema ──────────────────────────────────────────────────────────────────
def ensure_walk_schema(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wt_ops_v2_forward_walks (
            session_id     TEXT PRIMARY KEY,
            trigger_wallet TEXT NOT NULL,
            trigger_role   TEXT,
            operation_uuid TEXT,
            trigger_sig    TEXT,
            trigger_amount REAL,
            seed_wallet    TEXT NOT NULL,    -- first destination (the trigger's recipient)
            started_at     INTEGER NOT NULL,
            expires_at     INTEGER NOT NULL,
            state          TEXT DEFAULT 'WALKING',  -- WALKING | ARMED | EXPIRED | COLD | AMM_STOP | DEPTH_STOP
            depth          INTEGER DEFAULT 0,
            current_wallet TEXT,
            creator_wallet TEXT,             -- set on success
            last_checked   INTEGER,
            stop_reason    TEXT
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_fwalk_state ON wt_ops_v2_forward_walks(state)")
    conn.commit()


# ── RPC helpers ─────────────────────────────────────────────────────────────
def _newest_sig(addr: str):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getSignaturesForAddress",
                       "params": [addr, {"limit": 1}]}).encode()
    try:
        req = urllib.request.Request(_HELIUS_RPC, data=body,
                                     headers={"Content-Type": "application/json", "User-Agent": "fwalk/0.1"})
        r = json.loads(urllib.request.urlopen(req, timeout=12).read()).get("result") or []
        return r[0]["signature"] if r else None
    except Exception:
        return None


# Program IDs that identify CREATE / AMM activity from RAW instructions (no enhanced API).
_PUMP_FUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
_AMM_PROGRAMS = {
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",   # PumpSwap AMM
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",   # Raydium AMM v4
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK",   # Raydium CLMM
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",    # Orca Whirlpool
}


def _rpc(method: str, params: list):
    """One raw JSON-RPC call (1 credit)."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    try:
        req = urllib.request.Request(_HELIUS_RPC, data=body,
                                     headers={"Content-Type": "application/json", "User-Agent": "fwalk/0.1"})
        return json.loads(urllib.request.urlopen(req, timeout=15).read()).get("result")
    except Exception:
        return None


def _raw_txs(addr: str, limit: int = 25):
    """Recent txs for `addr` via RAW RPC (getSignaturesForAddress + getTransaction, ~1cr
    each) instead of the 100-credit enhanced endpoint. Returns Helius-shaped dicts with
    the fields the walker uses: `type` ('CREATE'/'SWAP'/'' — reconstructed from program
    IDs), `timestamp`, `signature`, `nativeTransfers` (from balance deltas)."""
    sigs = _rpc("getSignaturesForAddress", [addr, {"limit": limit}])
    if not sigs:
        return None if sigs is None else []
    out = []
    for s in sigs:
        sig = s.get("signature")
        if s.get("err"):
            continue
        tx = _rpc("getTransaction",
                  [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
        if not tx:
            continue
        out.append({
            "signature": sig,
            "timestamp": s.get("blockTime") or tx.get("blockTime", 0),
            "type": _tx_type_from_raw(tx),
            "nativeTransfers": _native_transfers_from_raw(tx, addr),
        })
    return out


def _tx_type_from_raw(tx: dict) -> str:
    """Classify a raw tx as CREATE / SWAP / '' for the walker's has_create/has_amm gate.

    Precision matters DIRECTIONALLY: has_create=True makes the walker REJECT a wallet as
    a fresh creator (line ~306). So over-flagging CREATE causes MISSED arms (rejecting a
    real creator that also traded). We therefore detect CREATE narrowly — the actual
    pump.fun 'create'/'InitializeMint' instruction via log messages — not merely touching
    the pump.fun program (which trades do too)."""
    meta = (tx or {}).get("meta") or {}
    msg = (tx or {}).get("transaction", {}).get("message", {})
    progs = set()
    for ix in msg.get("instructions", []):
        if ix.get("programId"):
            progs.add(ix["programId"])
    for grp in meta.get("innerInstructions", []) or []:
        for ix in grp.get("instructions", []):
            if ix.get("programId"):
                progs.add(ix["programId"])
    if progs & _AMM_PROGRAMS:
        return "SWAP"
    # CREATE = pump.fun program AND a create/mint instruction (from logs) — not a trade.
    if _PUMP_FUN_PROGRAM in progs:
        logs = " ".join(meta.get("logMessages", []) or [])
        if ("Instruction: Create" in logs or "InitializeMint" in logs
                or "Instruction: CreateMetadataAccount" in logs):
            return "CREATE"
        if "Instruction: Buy" in logs or "Instruction: Sell" in logs:
            return "SWAP"          # pump.fun bonding-curve trade = AMM-like, not create
    return ""


def _native_transfers_from_raw(tx: dict, addr: str) -> list:
    """Outbound native SOL transfers FROM `addr`, reconstructed from balance deltas.
    The walker only reads fromUserAccount==addr, so we emit those. 1 credit vs 100."""
    meta = (tx or {}).get("meta") or {}
    msg = (tx or {}).get("transaction", {}).get("message", {})
    keys = [k.get("pubkey") if isinstance(k, dict) else k for k in msg.get("accountKeys", [])]
    pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
    if not keys or len(pre) != len(keys) or len(post) != len(keys) or addr not in keys:
        return []
    i = keys.index(addr)
    fee = meta.get("fee", 0) or 0
    debit = pre[i] - post[i] - (fee if i == 0 else 0)   # what addr actually sent
    if debit <= 0:
        return []
    receivers = [(keys[j], post[j] - pre[j]) for j in range(len(keys))
                 if j != i and (post[j] - pre[j]) > 0]
    return [{"fromUserAccount": addr, "toUserAccount": k, "amount": amt}
            for k, amt in receivers]


def _enhanced(addr: str):
    """RAW-RPC replacement (kept name for callers). ~1 credit/tx instead of 100."""
    return _raw_txs(addr, limit=25)


# ── RPC cost control ─────────────────────────────────────────────────────────
# _wallet_profile uses the 100-CREDIT enhanced endpoint. Without these guards the
# walker re-profiles the same wallets every cycle AND profiles every fan-out recipient
# (a treasury spraying to 60 wallets = 60×100cr/cycle). Two fixes:
#   1. cache profiles (30s TTL) — the same wallet recurs constantly within/across walks
#   2. 1-credit sig-count pre-screen — a wallet with >FRESH_MAX_TX signatures is NOT a
#      fresh creator and NOT worth a 100-credit enhanced fetch; reject it for 1 credit.
_PROFILE_CACHE: dict = {}            # addr -> (profile, ts)
_PROFILE_TTL = 30
_PROFILE_LOCK = threading.Lock()


def _sig_count_capped(addr: str, cap: int = 6) -> int:
    """1-credit getSignaturesForAddress(limit=cap). Returns sig count (≤cap). A fresh
    creator has very few; a busy wallet hits the cap → reject without the 100cr fetch."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getSignaturesForAddress",
                       "params": [addr, {"limit": cap}]}).encode()
    try:
        req = urllib.request.Request(_HELIUS_RPC, data=body,
                                     headers={"Content-Type": "application/json", "User-Agent": "fwalk/0.1"})
        return len(json.loads(urllib.request.urlopen(req, timeout=10).read()).get("result") or [])
    except Exception:
        return -1


def _wallet_profile(addr: str, cheap_screen: bool = False) -> dict:
    """Classify a wallet. Cached 30s. If cheap_screen, first do a 1-credit sig-count and
    skip the 100-credit enhanced fetch for wallets that are obviously not fresh creators
    (too many txs) — used when checking fan-out RECIPIENTS (the cost multiplier)."""
    now = time.time()
    with _PROFILE_LOCK:
        hit = _PROFILE_CACHE.get(addr)
        if hit and (now - hit[1]) < _PROFILE_TTL:
            return hit[0]
    if cheap_screen:
        n = _sig_count_capped(addr, cap=FRESH_MAX_TX + 2)
        if n > FRESH_MAX_TX:             # busy wallet → not a fresh creator, reject for 1cr
            prof = {"ok": True, "tx_count": n, "has_create": True, "has_amm": True,
                    "outbounds": [], "screened_out": True}
            with _PROFILE_LOCK:
                _PROFILE_CACHE[addr] = (prof, now)
            return prof
    txs = _enhanced(addr)
    if txs is None:
        return {"ok": False}
    has_create = any("CREATE" in (t.get("type") or "") for t in txs)
    has_amm = any(t.get("type") in ("SWAP", "CREATE_POOL", "ADD_LIQUIDITY", "REMOVE_LIQUIDITY")
                  for t in txs)
    outs = []
    for t in txs:
        bt = t.get("timestamp")
        for nt in t.get("nativeTransfers", []):
            if nt.get("fromUserAccount") == addr:
                amt = nt.get("amount", 0) / 1e9
                if amt >= PROVISIONING_MIN:
                    outs.append({"to": nt.get("toUserAccount"), "amount": amt,
                                 "sig": t.get("signature"), "block_time": bt})
    prof = {"ok": True, "tx_count": len(txs), "has_create": has_create,
            "has_amm": has_amm, "outbounds": outs}
    with _PROFILE_LOCK:
        _PROFILE_CACHE[addr] = (prof, time.time())
    return prof


def _is_fresh_creator(addr: str, profile: dict) -> bool:
    """STRICT fresh rule: ~no prior history except the funding, no AMM, no prior CREATE."""
    return (profile.get("ok") and profile.get("tx_count", 99) <= 2
            and not profile.get("has_amm") and not profile.get("has_create"))


def _forwards_relay(profile: dict) -> bool:
    """Does this wallet forward a TEMPLATE-tailed amount onward? (→ relay, not terminal).
    A creator HOLDS the template (terminal); a relay forwards it. Using the template
    signature (not a loose band) keeps the relay-vs-creator call precise."""
    return any(_is_exact_template(o["amount"])
               for o in profile.get("outbounds", [])) if profile.get("ok") else False


def _in_watchtower_lineage(conn, trigger_wallet: str, operation_uuid: str) -> bool:
    """The funding source must be inside confirmed WATCHTOWER lineage — the trigger is a
    known treasury/collector of a real operation, not a random wallet."""
    try:
        r = conn.execute(
            "SELECT 1 FROM wt_ops_v2_wallets WHERE wallet=? AND role IN ('TREASURY','COLLECTOR') LIMIT 1",
            (trigger_wallet,)).fetchone()
        if r:
            return True
        if operation_uuid:
            r = conn.execute("SELECT 1 FROM wt_ops_v2 WHERE operation_uuid=? LIMIT 1",
                             (operation_uuid,)).fetchone()
            return r is not None
    except Exception:
        pass
    return False


def _arm_cadence_ok(conn) -> bool:
    """Cadence guard: a real campaign launches ~1 creator/hours. Reject if we'd exceed
    ARM_CADENCE_MAX strict arms in the window — that's debris, not creators."""
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM wt_ops_v2_armed WHERE armed_at > strftime('%s','now')-? "
            "AND arm_grade='STRICT'", (ARM_CADENCE_WINDOW,)).fetchone()[0]
        return n < ARM_CADENCE_MAX
    except Exception:
        return True


def _record_candidate(conn, op_uuid, source, wallet, amount, funded_at):
    """CANDIDATE_DETECTED — a loose/exact match that is NOT yet armed. Recorded so we
    can measure detection→arm→fire conversion. Distinct from an arm."""
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS wt_ops_v2_walk_candidates (
            wallet TEXT PRIMARY KEY, operation_uuid TEXT, source_wallet TEXT,
            amount REAL, funded_at INTEGER, detected_at INTEGER, state TEXT DEFAULT 'CANDIDATE_DETECTED')""")
        conn.execute(
            "INSERT OR IGNORE INTO wt_ops_v2_walk_candidates (wallet, operation_uuid, "
            "source_wallet, amount, funded_at, detected_at) VALUES (?,?,?,?,?,?)",
            (wallet, op_uuid or "", source, round(amount, 9), funded_at, int(time.time())))
        conn.commit()
    except Exception:
        pass


_WC_SEEN: set = set()    # creators already armed via wrap-close this process (dedup)


def _scan_wrap_close(node: str, op_uuid: str, trigger_wallet: str, session_id: str,
                     conn, depth: int) -> Optional[dict]:
    """WSOL wrap-close fast-path. Scan `node`'s recent txs for the structural creator-
    funding signature; if found, the creator = closeAccount.destination is armed STRICT.
    Lineage is GIVEN (the walk was triggered by a confirmed treasury). Returns a small
    status dict if a wrap-close was acted on, else None."""
    try:
        from src.core.wrap_close_detector import detect_wrap_close, store_candidate, is_fresh_creator
    except Exception:
        return None
    sigs = _rpc("getSignaturesForAddress", [node, {"limit": 15}]) or []
    armed_any = False
    for s in (sigs if isinstance(sigs, list) else []):
        if s.get("err"):
            continue
        sig = s.get("signature")
        tx = _rpc("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
        if not tx:
            continue
        ext = detect_wrap_close(tx)
        if not ext or ext.get("subprov") != node or not ext.get("creator"):
            continue
        creator = ext["creator"]
        if creator in _WC_SEEN:
            continue
        # fresh creator check (at detection time the creator is genuinely new)
        if not is_fresh_creator(creator, _sig_count_capped):
            _emit_event("REJECTED_INFRA", creator, trigger_wallet,
                        {"session": session_id, "reason": "wrap_close_not_fresh"})
            continue
        # lineage_source_treasury = the trigger (confirmed treasury that started this walk)
        store_candidate(conn, ext, lineage_treasury=trigger_wallet, tx_signature=sig,
                        funded_at=s.get("blockTime"))
        # build/attach the OPERATION rooted at the confirmed treasury, so the forward
        # wrap-close path produces operations too (DchJquEZ etc. get operation cards).
        wc_op = op_uuid
        try:
            from src.core.wrap_close_detector import ensure_operation_for_treasury, record_wrap_close_creator
            wc_op = ensure_operation_for_treasury(conn, trigger_wallet, subprov=node)
            record_wrap_close_creator(conn, wc_op, creator, ext.get("base_amount_sol"),
                                      funded_at=s.get("blockTime"))
            conn.commit()
        except Exception:
            pass
        _emit_event("CANDIDATE_DETECTED", creator, trigger_wallet,
                    {"session": session_id, "mechanism": "WSOL_WRAP_CLOSE",
                     "subprov": node, "base": ext.get("base_amount_sol"), "operation": wc_op})
        # BUY-SWARM GATE: if this subprov funded multiple wallets at the SAME instant, it's a
        # coordinated fan-out buy-swarm (they SWAP an already-launched token, never CREATE) —
        # NOT creator provisioning. Store the candidate for visibility but DON'T arm it.
        try:
            from src.core.wrap_close_detector import is_buy_swarm
            if is_buy_swarm(conn, node, s.get("blockTime")):
                conn.execute(
                    "UPDATE wt_wrap_close_candidates SET state='BUY_SWARM' WHERE subprov_wallet=? AND funded_at=?",
                    (node, s.get("blockTime")))
                conn.commit()
                _emit_event("BUY_SWARM_REJECTED", creator, trigger_wallet,
                            {"session": session_id, "subprov": node, "reason": "same-instant fan-out"})
                _WC_SEEN.add(creator)
                continue
        except Exception:
            pass
        # ARM STRICT — structural + confirmed lineage + fresh + not-buy-swarm
        try:
            from src.core.operation_armed import arm_creator
            arm_creator(conn, creator, wc_op or "", trigger_wallet,
                        ext.get("base_amount_sol"), s.get("blockTime") or int(time.time()))
            conn.execute("UPDATE wt_wrap_close_candidates SET state='ARMED' WHERE creator=?", (creator,))
            conn.commit()
        except Exception:
            pass
        _emit_event("CREATOR_ARMED", creator, trigger_wallet,
                    {"session": session_id, "grade": "ARM_STRICT_WRAP_CLOSE",
                     "mechanism": "WSOL_WRAP_CLOSE"})
        _WC_SEEN.add(creator); armed_any = True
    if armed_any:
        return {"armed": True, "creator": creator, "advanced": False}
    return None


def _settle_confirms_terminal(addr: str) -> bool:
    """After a short settle window, re-check: a true creator HOLDS the 1.11 (no relay
    forward). If it forwarded the 1.11 during settle, it was a relay → reject."""
    time.sleep(SETTLE_WINDOW_SECS)
    p = _wallet_profile(addr)
    if not p.get("ok"):
        return False
    return not _forwards_relay(p) and not p.get("has_amm")


# ── events ──────────────────────────────────────────────────────────────────
def _emit_event(event_type: str, wallet: str, related: str = None, payload: dict = None):
    for _attempt in range(3):     # retry on transient lock contention
        try:
            c = db_connect(LIVE_DB_PATH, timeout=30)
            try:
                c.execute("PRAGMA busy_timeout=30000")
                c.execute(
                    "INSERT INTO watchtower_events (event_type, wallet_address, related_wallet, "
                    "payload_json, source, created_at) VALUES (?,?,?,?,?,?)",
                    (event_type, wallet, related, json.dumps(payload or {}),
                     "forward_walk", int(time.time())))
                c.commit()
                return
            finally:
                c.close()
        except Exception as e:
            if "locked" in str(e).lower() and _attempt < 2:
                time.sleep(1.5); continue
            print(f"[FWALK] event emit failed {event_type}: {e}", flush=True)
            return


def _persist_edge(conn, op_uuid, frm, to, amount, sig, block_time, depth):
    try:
        conn.execute(
            "INSERT OR IGNORE INTO wt_ops_v2_edges (operation_uuid, from_wallet, to_wallet, "
            "amount_sol, block_time, signature, edge_type, hop_depth) VALUES (?,?,?,?,?,?,?,?)",
            (op_uuid or "", frm, to, amount, block_time, sig or f"fwalk:{frm[:8]}:{to[:8]}",
             "FORWARD_WALK", depth))
        conn.commit()
    except Exception:
        pass


# ── the walk session ────────────────────────────────────────────────────────
def start_walk(trigger_wallet: str, trigger_role: str, operation_uuid: str,
               trigger_sig: str, trigger_amount: float, dest_wallet: str,
               trigger_block_time: int = None):
    """Begin a forward-walk from a treasury/collector outbound. Spawns a daemon thread.
    Idempotent on the seed destination (one active walk per seed)."""
    if not HELIUS_API_KEY or not dest_wallet:
        return
    # Session ID is a DB PRIMARY KEY — must use FULL addresses, not truncated prefixes.
    # Two distinct wallets can share an 8/12-char prefix (e.g. 43PKjr22AFXt…3y3D vs …n7vh),
    # which would collide on a truncated key and silently drop the second walk (INSERT OR
    # IGNORE). Use the full addresses (uniqueness) — truncate only for the [FWALK] log line.
    session_id = f"{trigger_wallet}:{dest_wallet}:{int(time.time())}"
    with _WALK_LOCK:
        if any(w["seed_wallet"] == dest_wallet and w["state"] == "WALKING"
               for w in _ACTIVE_WALKS.values()):
            return                                   # already walking this seed
    now = int(time.time())
    conn = db_connect(OPS_DB_PATH, timeout=30)
    try:
        ensure_walk_schema(conn)
        conn.execute(
            "INSERT OR IGNORE INTO wt_ops_v2_forward_walks (session_id, trigger_wallet, "
            "trigger_role, operation_uuid, trigger_sig, trigger_amount, seed_wallet, "
            "started_at, expires_at, state, current_wallet, last_checked) "
            "VALUES (?,?,?,?,?,?,?,?,?, 'WALKING', ?, ?)",
            (session_id, trigger_wallet, trigger_role, operation_uuid, trigger_sig,
             trigger_amount, dest_wallet, now, now + TTL_SECS, dest_wallet, now))
        conn.commit()
        _persist_edge(conn, operation_uuid, trigger_wallet, dest_wallet,
                      trigger_amount, trigger_sig, trigger_block_time, 0)
    finally:
        conn.close()
    with _WALK_LOCK:
        _ACTIVE_WALKS[session_id] = {
            "session_id": session_id, "trigger_wallet": trigger_wallet,
            "operation_uuid": operation_uuid, "seed_wallet": dest_wallet,
            "current_wallet": dest_wallet, "depth": 0, "state": "WALKING",
            "started_at": now, "expires_at": now + TTL_SECS}
    _emit_event("FORWARD_WALK_STARTED", trigger_wallet, dest_wallet,
                {"session": session_id, "amount": trigger_amount, "operation": operation_uuid})
    print(f"[FWALK] ▶ walk {session_id} from {trigger_role} {trigger_wallet[:10]}… "
          f"→ seed {dest_wallet[:10]}…", flush=True)
    threading.Thread(target=_run_walk, args=(session_id,), daemon=True, name="fwalk").start()


def _run_walk(session_id: str):
    """Follow outbound hops from the seed until a creator is found or a stop fires."""
    import sqlite3 as _sq
    conn = db_connect(OPS_DB_PATH, timeout=30, row_factory=_sq.Row)
    try:
        row = conn.execute("SELECT * FROM wt_ops_v2_forward_walks WHERE session_id=?",
                           (session_id,)).fetchone()
        if not row:
            return
        op_uuid = row["operation_uuid"]
        cur = row["seed_wallet"]
        depth = 0
        last_sig = None
        while True:
            now = int(time.time())
            # TTL stop
            if now >= row["expires_at"]:
                _finish(conn, session_id, "EXPIRED", "ttl", cur, depth)
                _emit_event("FORWARD_WALK_EXPIRED", cur, None, {"session": session_id, "reason": "ttl"})
                return
            # cheap change-gate: only fetch if the current wallet moved
            ns = _newest_sig(cur)
            if ns is not None and ns == last_sig:
                time.sleep(POLL_INTERVAL_SECS); continue
            last_sig = ns

            # ── WSOL WRAP-CLOSE FAST-PATH ─────────────────────────────────────
            # The validated WATCHTOWER creator-funding signature (29/40 creators):
            # createIdempotent + syncNative + closeAccount. The creator is the
            # closeAccount.destination — NOT a transfer recipient. Lineage is GIVEN: this
            # walk was triggered from a confirmed treasury, so `cur` is in that lineage.
            # Detect it BEFORE the amount-based logic — it's structural, amount-agnostic.
            wc = _scan_wrap_close(cur, op_uuid, row["trigger_wallet"], session_id, conn, depth)
            if wc:
                advanced = bool(wc.get("advanced"))
                if wc.get("armed"):
                    _finish(conn, session_id, "ARMED", "wrap_close", wc["creator"], depth)
                    return
                # if a wrap-close creator was armed/recorded, continue the loop normally

            prof = _wallet_profile(cur)
            if not prof.get("ok"):
                time.sleep(POLL_INTERVAL_SECS); continue

            # AMM/trading → this branch is a trader, not provisioning → stop
            if prof.get("has_amm"):
                _finish(conn, session_id, "AMM_STOP", "amm_interaction", cur, depth)
                _emit_event("FORWARD_WALK_EXPIRED", cur, None, {"session": session_id, "reason": "amm"})
                return

            # examine outbound transfers for the next hop / creator funding
            advanced = False
            for o in sorted(prof["outbounds"], key=lambda x: -x["amount"]):
                to, amt, sig, bt = o["to"], o["amount"], o["sig"], o["block_time"]
                if not to:
                    continue
                _persist_edge(conn, op_uuid, cur, to, amt, sig, bt, depth + 1)

                # ── STRICT ARM GATE ───────────────────────────────────────────
                # A recipient is a CREATOR CANDIDATE only on the EXACT template. A
                # relay (1.11-band but forwards it onward) is followed, not armed.
                # Everything else is provisioning debris.
                if _is_exact_template(amt):
                    cprof = _wallet_profile(to, cheap_screen=True)
                    # REJECT: trades, or forwards the 1.11 onward (= relay, not terminal)
                    if cprof.get("has_amm") or _forwards_relay(cprof):
                        if _forwards_relay(cprof):
                            cur = to; depth += 1; advanced = True
                            _emit_event("FORWARD_HOP_DETECTED", cur, None,
                                        {"session": session_id, "depth": depth, "amount": amt, "kind": "relay"})
                            break
                        _emit_event("REJECTED_INFRA", to, None,
                                    {"session": session_id, "reason": "amm", "amount": amt})
                        continue
                    # loose match recorded but NOT armed unless it passes the full gate
                    if not _is_fresh_creator(to, cprof):
                        _emit_event("REJECTED_INFRA", to, None,
                                    {"session": session_id, "reason": "not_fresh", "amount": amt})
                        continue
                    # CANDIDATE_DETECTED — exact template + fresh + terminal-so-far.
                    _record_candidate(conn, op_uuid, row["trigger_wallet"], to, amt, bt or now)
                    _emit_event("CANDIDATE_DETECTED", to, row["trigger_wallet"],
                                {"session": session_id, "amount": amt, "operation": op_uuid})
                    # STRICT confirmations before arming: lineage + cadence + settle-confirm
                    if not _in_watchtower_lineage(conn, row["trigger_wallet"], op_uuid):
                        _emit_event("REJECTED_INFRA", to, None, {"session": session_id, "reason": "no_lineage"})
                        continue
                    if not _arm_cadence_ok(conn):
                        _emit_event("REJECTED_INFRA", to, None, {"session": session_id, "reason": "cadence_exceeded"})
                        continue
                    if not _settle_confirms_terminal(to):     # waits SETTLE_WINDOW, re-checks
                        _emit_event("REJECTED_INFRA", to, None, {"session": session_id, "reason": "forwarded_after_settle"})
                        continue
                    # PASSED ALL STRICT GATES → ARM
                    _on_creator_found(conn, session_id, op_uuid, row["trigger_wallet"],
                                      to, amt, bt or now, depth + 1)
                    return

                # 1.11-BAND relay (not exact) → follow forward, do NOT arm
                if RELAY_BAND_LO <= amt <= RELAY_BAND_HI:
                    rprof = _wallet_profile(to, cheap_screen=True)
                    if _forwards_relay(rprof) and not rprof.get("has_amm"):
                        cur = to; depth += 1; advanced = True
                        _emit_event("FORWARD_HOP_DETECTED", cur, None,
                                    {"session": session_id, "depth": depth, "amount": amt, "kind": "relay"})
                        break
                    # 1.11 into a non-fresh, non-forwarding wallet → ambiguous; skip branch
                    continue

                # provisioning-sized hop (not creator band) → follow the largest
                if PROVISIONING_MIN <= amt <= PROVISIONING_MAX:
                    cur = to; depth += 1; advanced = True
                    _emit_event("FORWARD_HOP_DETECTED", cur, None,
                                {"session": session_id, "depth": depth, "amount": amt, "kind": "provision"})
                    break

            if advanced:
                if depth >= MAX_DEPTH:
                    _finish(conn, session_id, "DEPTH_STOP", "max_depth", cur, depth)
                    _emit_event("FORWARD_WALK_EXPIRED", cur, None, {"session": session_id, "reason": "depth"})
                    return
                conn.execute("UPDATE wt_ops_v2_forward_walks SET current_wallet=?, depth=?, "
                             "last_checked=? WHERE session_id=?", (cur, depth, now, session_id))
                conn.commit()
                with _WALK_LOCK:
                    if session_id in _ACTIVE_WALKS:
                        _ACTIVE_WALKS[session_id].update({"current_wallet": cur, "depth": depth})
                last_sig = None     # force a fresh look at the new wallet
            else:
                time.sleep(POLL_INTERVAL_SECS)
    except Exception as e:
        print(f"[FWALK] walk {session_id} error: {e}", flush=True)
    finally:
        conn.close()


def _on_creator_found(conn, session_id, op_uuid, trigger_wallet, creator, amount, funded_at, depth):
    """A fresh wallet got the creator-band funding → candidate + ARM."""
    print(f"[FWALK] 🎯 CREATOR FOUND {creator[:12]}… (amt={amount:.4f}, depth={depth}, "
          f"session {session_id}) — arming", flush=True)
    _emit_event("CREATOR_FUNDING_DETECTED", creator, trigger_wallet,
                {"session": session_id, "amount": amount, "depth": depth, "operation": op_uuid})
    # candidate row
    try:
        from src.core.operation_forward_monitor import _template_base
        tb = _template_base(amount)
    except Exception:
        tb = round(amount - 0.00203928, 4)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO wt_operation_candidates (operation_uuid, wallet, source_wallet, "
            "source_role, amount, template_base, confidence, first_seen, last_seen, status) "
            "VALUES (?,?,?,?,?,?,?,?,?, 'PENDING')",
            (op_uuid or "", creator, trigger_wallet, "FORWARD_WALK", round(amount, 9),
             tb, 0.95, funded_at, funded_at))
        conn.commit()
    except Exception:
        pass
    # ARM (auto-enrol webhook + countdown)
    try:
        from src.core.operation_armed import arm_creator
        treasury = (conn.execute("SELECT treasury_root FROM wt_ops_v2 WHERE operation_uuid=?",
                                 (op_uuid,)).fetchone() or [trigger_wallet])[0] if op_uuid else trigger_wallet
        arm_creator(conn, creator, op_uuid or "", treasury, tb, funded_at)
        _emit_event("CREATOR_CANDIDATE_ARMED", creator, treasury,
                    {"session": session_id, "amount": amount, "expected_launch_min": 58})
    except Exception as e:
        print(f"[FWALK] arm failed: {e}", flush=True)
    _finish(conn, session_id, "ARMED", "creator_found", creator, depth, creator=creator)


def _finish(conn, session_id, state, reason, current, depth, creator=None):
    conn.execute(
        "UPDATE wt_ops_v2_forward_walks SET state=?, stop_reason=?, current_wallet=?, "
        "depth=?, creator_wallet=?, last_checked=? WHERE session_id=?",
        (state, reason, current, depth, creator, int(time.time()), session_id))
    conn.commit()
    with _WALK_LOCK:
        if session_id in _ACTIVE_WALKS:
            if state in ("EXPIRED", "COLD", "AMM_STOP", "DEPTH_STOP"):
                _ACTIVE_WALKS.pop(session_id, None)
            else:
                _ACTIVE_WALKS[session_id]["state"] = state


def active_walks(conn=None) -> list:
    """Current WALKING + recently-ARMED sessions, for the UI. Uses positional column
    access so it works regardless of the caller's row_factory."""
    own = conn is None
    if own:
        conn = db_connect(OPS_DB_PATH, timeout=15)
    try:
        ensure_walk_schema(conn)
        now = int(time.time())
        # CLEANUP: expire stale WALKING rows whose TTL has passed (they stalled — no forward
        # activity — and were never concluded). Without this they pile up forever (e.g. a
        # high-fanout trigger spawning thousands that never close).
        try:
            conn.execute(
                "UPDATE wt_ops_v2_forward_walks SET state='EXPIRED', stop_reason='ttl_expired' "
                "WHERE state='WALKING' AND expires_at < ?", (now,))
            conn.commit()
        except Exception:
            pass
        cols = ["session_id", "trigger_wallet", "trigger_role", "operation_uuid",
                "seed_wallet", "current_wallet", "depth", "state", "started_at",
                "expires_at", "creator_wallet", "stop_reason"]
        # Only CONFIRMED-treasury triggers (the authoritative set) — never non-confirmed
        # wallets like the 8sUKF9 sub-prov that leaked in before the treasury-only rule.
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM wt_ops_v2_forward_walks "
            "WHERE (state='WALKING' OR (state='ARMED' AND last_checked > strftime('%s','now')-3600)) "
            "  AND trigger_wallet IN (SELECT treasury FROM wt_confirmed_treasuries) "
            "ORDER BY started_at DESC LIMIT 50").fetchall()
        out = []
        for r in rows:
            d = {c: r[i] for i, c in enumerate(cols)}
            d["ttl_remaining_s"] = max(0, (d["expires_at"] or now) - now)
            d["operation"] = (d["operation_uuid"] or "")[:8]
            out.append(d)
        return out
    finally:
        if own:
            conn.close()
