"""
WATCHTOWER provisioning mechanism detectors.

Two confirmed creator-provisioning patterns share the same creator extraction method
(closeAccount.destination = creator) but differ in how the intermediate account is built.

Mechanism A — WSOL_WRAP_CLOSE (validated on 29/40 migrated creators, 29 distinct subprovs):
    1. system::transfer        subprov → wrap-wallet : base + 0.00203928
    2. ATA::createIdempotent   wrap-wallet creates a temp WSOL account
    3. system::transfer        wrap-wallet → temp WSOL : base
    4. spl-token::syncNative   wrap into WSOL
    5. spl-token::closeAccount destination = THE CREATOR   ← creator extracted HERE

Mechanism B — SEEDED_ACCOUNT_CLOSE (confirmed on Dtwi1e treasury, 2026-07-03):
    1. system::createAccountWithSeed   subprov → seeded account (derived address)
    2. spl-token::closeAccount         destination = THE CREATOR   ← creator extracted HERE
    No WSOL, no syncNative, no ATA. System Program accounts only.

HARD RULE (both mechanisms): the creator is closeAccount.destination — NOT any
system-transfer recipient. Naive transfer-tracing follows the intermediate account.

WATCHTOWER provisioning count = wrap_close_count + seeded_account_count
"""
from __future__ import annotations

import os
import json
import time
from typing import Optional, List

WSOL_MINT = "So11111111111111111111111111111111111111112"
ATA_RENT_LAMPORTS = 2_039_280

try:
    from src.utils.db_locking import db_connect
except Exception:                                    # pragma: no cover
    import sqlite3
    def db_connect(path, timeout=30):
        c = sqlite3.connect(path, timeout=timeout); c.row_factory = sqlite3.Row; return c

OPS_DB_PATH = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "database", "wt_ops_v2.db"))


# ── 1. structural decode ─────────────────────────────────────────────────────
def detect_wrap_close(tx: dict) -> Optional[dict]:
    """Decode a raw getTransaction. If it is the WSOL wrap-close creator-funding
    pattern, return the extracted accounts; else None. Pure parse, no RPC.

    Returns: {subprov, wrap_wallet, temp_wsol_account, creator, base_amount_sol,
              raw_amount_lamports}
    """
    if not tx:
        return None
    msg = (tx.get("transaction", {}) or {}).get("message", {}) or {}
    ixs = msg.get("instructions", []) or []

    def _is(ix, prog, typ):
        p = ix.get("parsed")
        return isinstance(p, dict) and ix.get("program") == prog and p.get("type") == typ

    has_create = any(_is(ix, "spl-associated-token-account", "createIdempotent") for ix in ixs)
    has_sync = any(_is(ix, "spl-token", "syncNative") for ix in ixs)
    if not (has_create and has_sync):
        return None

    # the closeAccount carries the creator (destination) + the temp WSOL account
    close = None
    for ix in ixs:
        p = ix.get("parsed")
        if isinstance(p, dict) and p.get("type") == "closeAccount":
            close = p.get("info", {})
            break
    if not close:
        return None
    creator = close.get("destination")
    temp_wsol = close.get("account")
    if not creator:
        return None

    # signers: [0] sub-provisioner, [1] wrap wallet
    signers = [k.get("pubkey") for k in msg.get("accountKeys", [])
               if isinstance(k, dict) and k.get("signer")]
    subprov = signers[0] if signers else None
    wrap_wallet = signers[1] if len(signers) > 1 else None

    # base amount = first system transfer FROM the subprov
    raw_lam = None
    for ix in ixs:
        p = ix.get("parsed")
        if (isinstance(p, dict) and p.get("type") == "transfer"
                and p.get("info", {}).get("source") == subprov):
            raw_lam = p["info"].get("lamports")
            break

    base = round((raw_lam - ATA_RENT_LAMPORTS) / 1e9, 4) if raw_lam else None

    # FALSE-POSITIVE GUARDS — a creator-provisioning wrap-close has a creator-seed-sized
    # base and the destination is a DISTINCT fresh wallet, not the funder itself:
    #  - base must be in the creator-seed range (0.05–7 SOL). A 690-SOL "base" is a
    #    capital sweep/refund through a wrap, not creator provisioning.
    #  - the closeAccount.destination must not be the subprov or wrap wallet (self-refund).
    if base is None or base < 0.05 or base > 7.0:
        return None
    if creator in (subprov, wrap_wallet):
        return None

    return {
        "subprov": subprov,
        "wrap_wallet": wrap_wallet,
        "temp_wsol_account": temp_wsol,
        "creator": creator,
        "raw_amount_lamports": raw_lam,
        "base_amount_sol": base,
    }


def detect_seeded_account_close(tx: dict) -> Optional[dict]:
    """Decode a raw getTransaction for the SEEDED_ACCOUNT_CLOSE provisioning pattern.
    Matches: SystemProgram.createAccountWithSeed + spl-token.closeAccount (no WSOL/syncNative).
    Returns the same shape as detect_wrap_close, or None.

    Returns: {subprov, wrap_wallet, temp_wsol_account, creator, base_amount_sol,
              raw_amount_lamports, funding_mechanism}
    """
    if not tx:
        return None
    msg = (tx.get("transaction", {}) or {}).get("message", {}) or {}
    ixs = msg.get("instructions", []) or []

    def _is(ix, prog, typ):
        p = ix.get("parsed")
        return isinstance(p, dict) and ix.get("program") == prog and p.get("type") == typ

    # Must have createAccountWithSeed but NOT syncNative (that's Mechanism A)
    has_seeded = any(_is(ix, "system", "createAccountWithSeed") for ix in ixs)
    has_sync = any(_is(ix, "spl-token", "syncNative") for ix in ixs)
    if not has_seeded or has_sync:
        return None

    close = None
    for ix in ixs:
        p = ix.get("parsed")
        if isinstance(p, dict) and p.get("type") == "closeAccount":
            close = p.get("info", {})
            break
    if not close:
        return None
    creator = close.get("destination")
    seeded_account = close.get("account")
    if not creator:
        return None

    signers = [k.get("pubkey") for k in msg.get("accountKeys", [])
               if isinstance(k, dict) and k.get("signer")]
    subprov = signers[0] if signers else None

    # base amount: SOL received by the creator = postBalance - preBalance at creator index
    keys = [k.get("pubkey") if isinstance(k, dict) else k
            for k in msg.get("accountKeys", [])]
    meta = tx.get("meta") or {}
    pre = meta.get("preBalances") or []
    post = meta.get("postBalances") or []
    raw_lam = None
    try:
        ci = keys.index(creator)
        if ci < len(pre) and ci < len(post) and post[ci] > pre[ci]:
            raw_lam = post[ci] - pre[ci]
    except (ValueError, IndexError):
        pass

    base = round(raw_lam / 1e9, 4) if raw_lam else None

    # same false-positive guards as Mechanism A
    if base is None or base < 0.05 or base > 7.0:
        return None
    if creator == subprov:
        return None

    return {
        "subprov": subprov,
        "wrap_wallet": None,          # no wrap wallet in this mechanism
        "temp_wsol_account": seeded_account,
        "creator": creator,
        "raw_amount_lamports": raw_lam,
        "base_amount_sol": base,
        "funding_mechanism": "SEEDED_ACCOUNT_CLOSE",
    }


def extract_close_destinations(tx: dict) -> List[dict]:
    """WATCH-step extractor for the websocket cascade. Returns EVERY provisioning
    closeAccount.destination in this tx across ALL recognised mechanisms.

    Mechanism A (WSOL_WRAP_CLOSE): requires createIdempotent + syncNative.
    Mechanism B (SEEDED_ACCOUNT_CLOSE): requires createAccountWithSeed, no syncNative.

    No amount guard — we WATCH all destinations and let the Pump.fun CREATE confirm
    the real creator. Each dict includes `funding_mechanism` so callers can record it.

    Each dict: {candidate, wrap_wallet, temp_wsol_account, subprov, base_amount_sol,
                funding_mechanism}.
    Pure parse, no RPC."""
    out: List[dict] = []
    if not tx:
        return out
    msg = (tx.get("transaction", {}) or {}).get("message", {}) or {}
    ixs = msg.get("instructions", []) or []

    def _is(ix, prog, typ):
        p = ix.get("parsed")
        return isinstance(p, dict) and ix.get("program") == prog and p.get("type") == typ

    has_create_idempotent = any(_is(ix, "spl-associated-token-account", "createIdempotent") for ix in ixs)
    has_sync = any(_is(ix, "spl-token", "syncNative") for ix in ixs)
    has_seeded = any(_is(ix, "system", "createAccountWithSeed") for ix in ixs)

    # Mechanism A: WSOL wrap-close
    is_mechanism_a = has_create_idempotent and has_sync
    # Mechanism B: seeded account close (no syncNative — that would be Mechanism A)
    is_mechanism_b = has_seeded and not has_sync

    if not (is_mechanism_a or is_mechanism_b):
        return out

    mechanism = "WSOL_WRAP_CLOSE" if is_mechanism_a else "SEEDED_ACCOUNT_CLOSE"

    signers = [k.get("pubkey") for k in msg.get("accountKeys", [])
               if isinstance(k, dict) and k.get("signer")]
    subprov = signers[0] if signers else None
    wrap_wallet = signers[1] if (is_mechanism_a and len(signers) > 1) else None

    for ix in ixs:
        p = ix.get("parsed")
        if not (isinstance(p, dict) and p.get("type") == "closeAccount"):
            continue
        info = p.get("info", {}) or {}
        dest = info.get("destination")
        temp_account = info.get("account")
        if not dest or dest in (subprov, wrap_wallet):
            continue                                  # skip self-refunds
        # best-effort base amount (informational only — not a gate)
        raw_lam = None
        for j in ixs:
            q = j.get("parsed")
            if (isinstance(q, dict) and q.get("type") == "transfer"
                    and q.get("info", {}).get("source") == subprov):
                raw_lam = q["info"].get("lamports")
                break
        base = round((raw_lam - ATA_RENT_LAMPORTS) / 1e9, 4) if (raw_lam and is_mechanism_a) else (
               round(raw_lam / 1e9, 4) if raw_lam else None)
        out.append({
            "candidate": dest,
            "wrap_wallet": wrap_wallet,
            "temp_wsol_account": temp_account,
            "subprov": subprov,
            "base_amount_sol": base,
            "funding_mechanism": mechanism,
        })
    return out


# ── 2. lineage + freshness verification ──────────────────────────────────────
def confirmed_treasury_set(conn) -> set:
    try:
        return {r[0] for r in conn.execute("SELECT treasury FROM wt_confirmed_treasuries").fetchall()}
    except Exception:
        return set()


def verify_lineage(conn, subprov: str, funder_walk_fn, confirmed: set, max_hops: int = 3):
    """Does the subprov trace up to a CONFIRMED treasury within max_hops?
    funder_walk_fn(addr) -> list[(funder, amount_sol)] (RPC-backed, caller-supplied).
    Returns the confirmed treasury ancestor, or None."""
    if subprov in confirmed:
        return subprov
    cur, seen = subprov, {subprov}
    for _ in range(max_hops):
        funders = funder_walk_fn(cur) or []
        if not funders:
            return None
        top = max(funders, key=lambda x: x[1])[0]
        if top in confirmed:
            return top
        if top in seen:
            return None
        seen.add(top); cur = top
    return None


def is_fresh_creator(creator: str, sig_count_fn, max_tx: int = 4) -> bool:
    """A real WATCHTOWER creator is fresh/single-use. sig_count_fn(addr,cap)->int (1 credit)."""
    n = sig_count_fn(creator, max_tx + 2)
    return 0 <= n <= max_tx


# buy-swarm: a subprov that wrap-close-funds MANY wallets at the SAME instant is spraying a
# coordinated BUY-SWARM (fan-out), NOT provisioning creators. Real creator provisioning funds
# wallets individually (one creator → one token → it launches). Same-instant multi-wallet
# funding from one subprov = fan-out buy-wallets that go on to SWAP an already-launched token,
# never CREATE. Confirmed: 5g2tRNF4 (3 @ same instant → all traded HGii9wjc), 6keL8c1A (14 @
# same instant → none launched). Detected from LOCAL candidate data (zero RPC).
BUYSWARM_MIN_SIBLINGS = 1     # subprov funding >this many at one instant = buy-swarm.
                              # Lowered from 2: buy-swarms evade by funding EXACTLY 2 at a
                              # time (pairs). A genuine WATCHTOWER creator is provisioned
                              # SOLO (1 per subprov+instant). >1 same-instant = swarm. Real
                              # creators are confirmed post-launch by the CREATE (vs SWAP).


def is_buy_swarm(conn, subprov: str, funded_at: int) -> bool:
    """True if this subprov funded multiple wallets at the same instant (fan-out buy-swarm).
    Checked against already-stored candidates — local only, no RPC."""
    if not subprov or not funded_at:
        return False
    n = conn.execute(
        "SELECT COUNT(*) FROM wt_wrap_close_candidates WHERE subprov_wallet=? AND funded_at=?",
        (subprov, funded_at)).fetchone()
    return bool(n and n[0] > BUYSWARM_MIN_SIBLINGS)


# ── 3. storage ───────────────────────────────────────────────────────────────
def ensure_schema(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wt_wrap_close_candidates (
            creator                  TEXT PRIMARY KEY,   -- = closeAccount.destination
            funding_mechanism        TEXT DEFAULT 'WSOL_WRAP_CLOSE',
            creator_extraction_method TEXT DEFAULT 'CLOSE_ACCOUNT_DESTINATION',
            subprov_wallet           TEXT,
            wrap_wallet              TEXT,
            temp_wsol_account        TEXT,
            close_destination        TEXT,               -- = creator (explicit, audit)
            lineage_source_treasury  TEXT,
            base_amount_sol          REAL,
            tx_signature             TEXT,
            funded_at                INTEGER,
            confidence               TEXT DEFAULT 'STRICT',
            state                    TEXT DEFAULT 'DETECTED',  -- DETECTED|ARMED|FIRED|EXPIRED
            detected_at              INTEGER NOT NULL
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_wcc_state ON wt_wrap_close_candidates(state)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_wcc_subprov ON wt_wrap_close_candidates(subprov_wallet)")
    conn.commit()


def ensure_operation_for_treasury(conn, treasury: str, *, subprov: Optional[str] = None) -> str:
    """Create (or fetch) the wt_ops_v2 operation rooted at a CONFIRMED treasury, so the
    forward wrap-close path produces operations too (not just arms). Returns the op uuid.

    Bridges the two detection paths: backward-intake ops and forward wrap-close ops now
    both live in wt_ops_v2, rooted on the treasury. Idempotent — one op per treasury."""
    import uuid as _uuid
    r = conn.execute("SELECT operation_uuid FROM wt_ops_v2 WHERE treasury_root=?", (treasury,)).fetchone()
    if r:
        op = r[0]
    else:
        op = str(_uuid.uuid4())
        now = int(time.time())
        conn.execute(
            """INSERT INTO wt_ops_v2
                 (operation_uuid, treasury_root, status, confidence, first_seen, last_seen,
                  created_at, updated_at, source, notes, op_type)
               VALUES (?,?, 'FORMING', 0.9, ?,?,?,?, 'wrap_close_forward',
                       '{"detected_via":"wrap_close"}', 'WATCHTOWER')""",
            (op, treasury, now, now, now, now))
        # record the treasury as the TREASURY wallet of the op
        conn.execute(
            "INSERT OR IGNORE INTO wt_ops_v2_wallets (operation_uuid, wallet, role, first_seen, last_seen) "
            "VALUES (?,?, 'TREASURY', ?, ?)", (op, treasury, now, now))
    # record the subprov as a SUB_PROV wallet of the op
    if subprov:
        now = int(time.time())
        conn.execute(
            "INSERT OR IGNORE INTO wt_ops_v2_wallets (operation_uuid, wallet, role, first_seen, last_seen) "
            "VALUES (?,?, 'SUB_PROV', ?, ?)", (op, subprov, now, now))
    return op


def record_wrap_close_creator(conn, op_uuid: str, creator: str, base_amount_sol,
                              funded_at: Optional[int] = None, migrated: bool = False) -> None:
    """Record a wrap-close-detected creator as a creator of the operation, so it shows on
    the operation pages alongside intake-discovered creators. The token doesn't exist yet
    (pre-launch), so token_mint uses a 'pending:' placeholder (NOT NULL PK requires a value);
    it's filled with the real mint when the creator launches."""
    token_mint = f"pending:{creator}"          # placeholder — replaced on launch
    conn.execute(
        """INSERT OR IGNORE INTO wt_ops_v2_creators
             (operation_uuid, creator_wallet, token_mint, migration_time, funding_amount_sol, source_creator)
           VALUES (?,?,?,?,?, 'wrap_close')""",
        (op_uuid, creator, token_mint, funded_at if migrated else None,
         (base_amount_sol + 0.00203928) if base_amount_sol is not None else None))


def store_candidate(conn, extracted: dict, *, lineage_treasury: str, tx_signature: str,
                    funded_at: Optional[int] = None) -> bool:
    """Store a confirmed wrap-close WATCHTOWER creator candidate. Idempotent on creator."""
    ensure_schema(conn)
    creator = extracted["creator"]
    now = int(time.time())
    cur = conn.execute(
        """INSERT OR IGNORE INTO wt_wrap_close_candidates
             (creator, funding_mechanism, creator_extraction_method, subprov_wallet,
              wrap_wallet, temp_wsol_account, close_destination, lineage_source_treasury,
              base_amount_sol, tx_signature, funded_at, confidence, state, detected_at)
           VALUES (?, 'WSOL_WRAP_CLOSE', 'CLOSE_ACCOUNT_DESTINATION', ?,?,?,?,?,?,?,?, 'STRICT', 'DETECTED', ?)""",
        (creator, extracted.get("subprov"), extracted.get("wrap_wallet"),
         extracted.get("temp_wsol_account"), creator, lineage_treasury,
         extracted.get("base_amount_sol"), tx_signature, funded_at or now, now))
    conn.commit()
    return cur.rowcount > 0


# ── 4. the full live rule ────────────────────────────────────────────────────
def process_candidate_tx(conn, tx: dict, tx_signature: str, *, funder_walk_fn,
                         sig_count_fn, confirmed: Optional[set] = None,
                         funded_at: Optional[int] = None) -> Optional[dict]:
    """The complete live rule for one candidate tx:
       structural wrap-close  → lineage to confirmed treasury  → fresh creator
       → store as STRICT WATCHTOWER creator candidate.
    Returns the stored candidate dict (ARM-ready), or None if it doesn't qualify."""
    ext = detect_wrap_close(tx)
    if not ext or not ext.get("subprov"):
        return None
    if confirmed is None:
        confirmed = confirmed_treasury_set(conn)
    treasury = verify_lineage(conn, ext["subprov"], funder_walk_fn, confirmed)
    if not treasury:
        return None                                   # no confirmed-treasury lineage → not WT
    if not is_fresh_creator(ext["creator"], sig_count_fn):
        return None                                   # not a fresh single-use creator
    store_candidate(conn, ext, lineage_treasury=treasury, tx_signature=tx_signature,
                    funded_at=funded_at)
    return {**ext, "lineage_source_treasury": treasury, "tx_signature": tx_signature,
            "confidence": "STRICT", "arm": "ARM_STRICT_WRAP_CLOSE"}
