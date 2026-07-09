"""
Auto-arm loop for pre-launch creators.

When the forward monitor detects a template-funded creator (the ~1.11 ATA-rent
template — a wallet that launches ~58 min later), we ARM it:
  1. record an armed row (creator, operation, treasury, funded_at, expected_launch)
  2. auto-enrol the creator wallet into the candidate webhook (real-time CREATE)

When that creator MIGRATES (appears in wt_ops_v2_creators with a migration_time),
we DISARM it:
  1. remove the webhook enrolment (single-use creators must not accumulate)
  2. mark the armed row FIRED with the lead time

This module is standalone and idempotent. reconcile_armed() is called by the
operation scheduler each cycle; arm_creator() is called by the forward monitor the
moment a NEW_CREATOR_CANDIDATE is recorded. Writes only to wt_ops_v2.db (+ the
webhook via webhook_manager). Never touches the live WATCH pipeline.
"""
from __future__ import annotations

import os
import time
import asyncio
from typing import Optional

OPS_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "..", "database", "wt_ops_v2.db")
OPS_DB_PATH = os.path.abspath(OPS_DB_PATH)
LIVE_DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.join(os.path.dirname(OPS_DB_PATH), "flex_complete_database.db"))

LEAD_TIME_MIN = 58            # observed template-funding → migration average
ARM_STALE_MIN = 180          # auto-disarm an armed creator that never migrated after 3h

try:
    from src.utils.db_locking import db_connect
except Exception:                                    # pragma: no cover
    import sqlite3
    def db_connect(path, timeout=30):
        c = sqlite3.connect(path, timeout=timeout)
        c.row_factory = sqlite3.Row
        return c


def ensure_armed_schema(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wt_ops_v2_armed (
            creator_wallet   TEXT PRIMARY KEY,
            operation_uuid   TEXT NOT NULL,
            treasury         TEXT,
            template_base    REAL,
            funded_at        INTEGER NOT NULL,        -- on-chain block_time of the template funding
            expected_launch  INTEGER NOT NULL,        -- funded_at + 58m
            armed_at         INTEGER NOT NULL,        -- when we armed (detection time)
            webhooked        INTEGER DEFAULT 0,       -- 1 once enrolled on the webhook
            state            TEXT DEFAULT 'ARMED',    -- ARMED | FIRED | EXPIRED
            migration_time   INTEGER,                 -- set on disarm-by-launch
            lead_time_min    INTEGER,                 -- actual funded→migrated minutes
            disarmed_at      INTEGER,
            disarm_reason    TEXT,
            arm_grade        TEXT DEFAULT 'STRICT'    -- STRICT (passed full gate) | LOOSE
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_armed_state ON wt_ops_v2_armed(state)")
    # migrate: add arm_grade if the table predates it
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(wt_ops_v2_armed)").fetchall()]
        if "arm_grade" not in cols:
            conn.execute("ALTER TABLE wt_ops_v2_armed ADD COLUMN arm_grade TEXT DEFAULT 'STRICT'")
    except Exception:
        pass
    conn.commit()


# ─────────────────────────── ARM ────────────────────────────────────────────
def arm_creator(conn, creator_wallet: str, operation_uuid: str, treasury: Optional[str],
                template_base: Optional[float], funded_at: int,
                auto_enroll: bool = True) -> bool:
    """Arm a freshly-detected template-funded creator. Idempotent (PK on wallet).
    Returns True if newly armed. Auto-enrols into the webhook unless disabled."""
    ensure_armed_schema(conn)
    now = int(time.time())
    expected = funded_at + LEAD_TIME_MIN * 60
    cur = conn.execute(
        """INSERT OR IGNORE INTO wt_ops_v2_armed
             (creator_wallet, operation_uuid, treasury, template_base,
              funded_at, expected_launch, armed_at, state)
           VALUES (?,?,?,?,?,?,?, 'ARMED')""",
        (creator_wallet, operation_uuid, treasury, template_base,
         funded_at, expected, now))
    conn.commit()
    if cur.rowcount == 0:
        return False                                 # already armed
    if auto_enroll:
        _enroll(creator_wallet, conn)
    return True


def _enroll(creator_wallet: str, conn) -> None:
    """Best-effort webhook enrol of an armed creator. Failure is non-fatal — the
    armed row stands; reconcile retries enrolment for ARMED rows with webhooked=0."""
    try:
        from src.analysis.webhook_manager import WebhookManager, CANDIDATE_ROLE
        loop = asyncio.new_event_loop()
        try:
            mgr = WebhookManager(LIVE_DB_PATH)
            n = loop.run_until_complete(
                mgr.enroll_batch([creator_wallet], role=CANDIDATE_ROLE,
                                 notes="auto-arm pre-launch creator"))
        finally:
            loop.close()
        if n is not None:
            conn.execute("UPDATE wt_ops_v2_armed SET webhooked=1 WHERE creator_wallet=?",
                         (creator_wallet,))
            conn.commit()
    except Exception as e:
        print(f"[ARMED] enrol failed for {creator_wallet[:10]}…: {e}", flush=True)


def _disarm_webhook(creator_wallet: str) -> None:
    """Remove an armed creator from the webhook (single-use → don't accumulate)."""
    try:
        from src.analysis.webhook_manager import WebhookManager, CANDIDATE_ROLE
        loop = asyncio.new_event_loop()
        try:
            mgr = WebhookManager(LIVE_DB_PATH)
            loop.run_until_complete(
                mgr.remove(creator_wallet, role=CANDIDATE_ROLE, reason="launched"))
        finally:
            loop.close()
    except Exception as e:
        print(f"[ARMED] disarm-webhook failed for {creator_wallet[:10]}…: {e}", flush=True)


PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"


def _detect_create(creator_wallet):
    """AT-CREATE detection: did this armed wallet do a pump.fun CREATE (real creator) or only
    SWAP (buy-swarm false positive)? Returns (launch_time, mint, acted_as) where acted_as is
    'CREATE' | 'SWAP' | None. RAW RPC only. The CREATE-vs-SWAP next-action is the ONLY reliable
    creator-vs-buyswarm discriminator (pre-launch they're identical) — so reconcile uses this
    to FIRE real creators and DISARM buy-swarms."""
    import os, json as _json, urllib.request
    key = os.environ.get("HELIUS_API_KEY", "")
    if not key:
        return None, None, None
    url = f"https://mainnet.helius-rpc.com/?api-key={key}"

    def _rpc(m, p):
        try:
            body = _json.dumps({"jsonrpc": "2.0", "id": 1, "method": m, "params": p}).encode()
            return _json.loads(urllib.request.urlopen(urllib.request.Request(
                url, data=body, headers={"Content-Type": "application/json"}), timeout=12).read()).get("result")
        except Exception:
            return None

    sigs = _rpc("getSignaturesForAddress", [creator_wallet, {"limit": 15}]) or []
    swapped = False
    for s in sorted([x for x in sigs if not x.get("err")], key=lambda x: x.get("blockTime") or 0):
        tx = _rpc("getTransaction", [s["signature"], {"encoding": "jsonParsed",
                  "maxSupportedTransactionVersion": 0}])
        if not tx:
            continue
        meta = tx.get("meta") or {}
        keys = [k.get("pubkey") if isinstance(k, dict) else k
                for k in tx.get("transaction", {}).get("message", {}).get("accountKeys", [])]
        logs = " ".join(meta.get("logMessages", []) or [])
        if "Instruction: Create" in logs and PUMP_PROGRAM in keys:
            mint = None
            for tb in (meta.get("postTokenBalances") or []):
                if tb.get("mint") and "So111" not in tb["mint"]:
                    mint = tb["mint"]; break
            return s.get("blockTime"), mint, "CREATE"
        if "Instruction: Buy" in logs or "Instruction: Sell" in logs:
            swapped = True
    return None, None, ("SWAP" if swapped else None)


# ─────────────────────────── RECONCILE (disarm) ─────────────────────────────
INSTANT_THRESHOLD_S = 60     # <60s birth→launch = INSTANT (uncatchable); >= = STAGED


def _classify_creator_mode(conn, creator, funded_at, launched_at) -> None:
    """Record a fired creator's birth→launch gap + mode into wt_creator_birth_launch.
    The live feeder for the STAGED/INSTANT distribution (no RPC — uses times already known).

    HARDENED against the contamination that the first measurement bug produced:
      - require BOTH times present and a STRICTLY POSITIVE gap (launch after funding) —
        rejects the negative/zero false rows where the wrap-close funding tx was mistaken
        for the launch.
      - require a REAL launched token (a non-placeholder token_mint in wt_ops_v2_creators);
        a creator with only a 'pending:' mint never actually launched, so don't classify it.
    All checks are LOCAL (zero RPC)."""
    if not funded_at or not launched_at:
        return
    gap = launched_at - funded_at
    if gap <= 0:                          # launch cannot precede/equal funding → bad data
        return
    # require a genuine launched token (real mint, not the pre-launch 'pending:' placeholder)
    real_launch = conn.execute(
        "SELECT 1 FROM wt_ops_v2_creators WHERE creator_wallet=? "
        "AND token_mint IS NOT NULL AND token_mint NOT LIKE 'pending:%' "
        "AND migration_time IS NOT NULL LIMIT 1", (creator,)).fetchone()
    if not real_launch:
        return
    mode = "INSTANT" if gap < INSTANT_THRESHOLD_S else "STAGED"
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS wt_creator_birth_launch (
            creator TEXT PRIMARY KEY, treasury TEXT, subprov TEXT, funded_at INTEGER,
            launched_at INTEGER, birth_to_launch_s INTEGER, creator_mode TEXT,
            token_mint TEXT, funding_sig TEXT, launch_sig TEXT, base_amount_sol REAL,
            measured_at INTEGER)""")
        treasury = (conn.execute(
            "SELECT treasury FROM wt_ops_v2_armed WHERE creator_wallet=?", (creator,)).fetchone() or [None])[0]
        mint = (conn.execute(
            "SELECT token_mint FROM wt_ops_v2_creators WHERE creator_wallet=? "
            "AND token_mint NOT LIKE 'pending:%' AND migration_time IS NOT NULL LIMIT 1",
            (creator,)).fetchone() or [None])[0]
        conn.execute(
            "INSERT OR REPLACE INTO wt_creator_birth_launch "
            "(creator, treasury, funded_at, launched_at, birth_to_launch_s, creator_mode, token_mint, measured_at) "
            "VALUES (?,?,?,?,?,?,?,?)", (creator, treasury, funded_at, launched_at, gap, mode, mint, int(time.time())))
    except Exception:
        pass


def reconcile_armed(conn=None) -> dict:
    """Each scheduler cycle:
      - DISARM-by-launch: armed creators now present in wt_ops_v2_creators with a
        migration_time → remove webhook, mark FIRED + record lead time.
      - DISARM-stale: armed creators past expected_launch + grace that never
        migrated → remove webhook, mark EXPIRED.
      - retry enrolment for ARMED rows that failed to webhook (webhooked=0).
    """
    own = conn is None
    if own:
        conn = db_connect(OPS_DB_PATH, timeout=30)
    ensure_armed_schema(conn)
    now = int(time.time())
    stats = {"fired": 0, "expired": 0, "enroll_retried": 0, "still_armed": 0}
    try:
        # positional access — robust regardless of the caller's row_factory
        armed = conn.execute(
            "SELECT creator_wallet, operation_uuid, funded_at, expected_launch, webhooked "
            "FROM wt_ops_v2_armed WHERE state='ARMED'").fetchall()
        for a in armed:
            w, _op, funded_at, expected_launch, webhooked = a[0], a[1], a[2], a[3], a[4]
            # BUY-SWARM RECLASSIFY (zero RPC): the pre-arm buy-swarm gate is a point-in-time
            # check that loses a RACE against same-instant fan-out — the arm can win by a few
            # seconds before all sibling fundings land, then a later detection flips the
            # wrap-close candidate to BUY_SWARM. That verdict never disarmed the live ARMED row
            # (12 stale buy-swarms observed: tiny 0.05–0.13 SOL siblings of a real creator).
            # If the candidate is now BUY_SWARM, this is a confirmed false positive → disarm.
            try:
                _wc = conn.execute(
                    "SELECT state FROM wt_wrap_close_candidates WHERE creator=?", (w,)).fetchone()
            except Exception:
                _wc = None
            if _wc and _wc[0] == "BUY_SWARM":
                _disarm_webhook(w)
                conn.execute(
                    "UPDATE wt_ops_v2_armed SET state='EXPIRED', disarmed_at=?, "
                    "disarm_reason='buy_swarm_reclassified' WHERE creator_wallet=?", (now, w))
                conn.commit()
                stats["expired"] += 1
                continue
            # FIRED = the creator LAUNCHED. The launch event is the CREATE (a real token_mint),
            # NOT migration. Most WATCHTOWER tokens dump before bonding (INSTANT mode), so they
            # CREATE but never migrate — waiting for migration would never mark them fired. Use
            # the token CREATE (real, non-placeholder mint) as the launch signal; migration_time
            # if present is the precise launch time, else fall back to the creator row's create.
            row = conn.execute(
                "SELECT migration_time, token_mint FROM wt_ops_v2_creators "
                "WHERE creator_wallet=? AND token_mint IS NOT NULL AND token_mint NOT LIKE 'pending:%' "
                "ORDER BY migration_time IS NULL, migration_time LIMIT 1", (w,)).fetchone()
            launch_time = mint = None
            acted_as = None
            if row:
                launch_time, mint = row[0], row[1]
            else:
                # AT-CREATE detection (1 RPC): did the armed wallet CREATE (real creator) or
                # only SWAP (buy-swarm false positive)? CREATE-vs-SWAP is the ONLY reliable
                # creator-vs-swarm discriminator (pre-launch they look identical).
                lt, m, acted_as = _detect_create(w)
                if lt and acted_as == "CREATE":
                    launch_time, mint = lt, m
                    conn.execute(
                        "UPDATE wt_ops_v2_creators SET token_mint=?, migration_time=COALESCE(migration_time,?) "
                        "WHERE creator_wallet=? AND token_mint LIKE 'pending:%'",
                        (m or f"launched:{w}", lt, w))
            # BUY-SWARM FALSE POSITIVE: armed wallet SWAPped instead of CREATEing → it's a
            # buy-swarm that slipped the pre-arm gate. Disarm, mark, and flag the candidate so
            # the gate learns (same-instant siblings of a swarm are also swarm).
            if acted_as == "SWAP" and not launch_time:
                _disarm_webhook(w)
                conn.execute(
                    "UPDATE wt_ops_v2_armed SET state='EXPIRED', disarmed_at=?, "
                    "disarm_reason='buy_swarm_swapped' WHERE creator_wallet=?", (now, w))
                try:
                    conn.execute("UPDATE wt_wrap_close_candidates SET state='BUY_SWARM' WHERE creator=?", (w,))
                except Exception:
                    pass
                stats["expired"] += 1
                continue
            if launch_time or mint:
                mt = launch_time
                lead = round(((mt or now) - funded_at) / 60) if (mt or now) > funded_at else None
                _disarm_webhook(w)
                conn.execute(
                    "UPDATE wt_ops_v2_armed SET state='FIRED', migration_time=?, "
                    "lead_time_min=?, disarmed_at=?, disarm_reason='launched' "
                    "WHERE creator_wallet=?", (mt, lead, now, w))
                if mt:
                    _classify_creator_mode(conn, w, funded_at, mt)
                stats["fired"] += 1
                continue
            # stale: never migrated within the window + grace
            if now > expected_launch + ARM_STALE_MIN * 60:
                _disarm_webhook(w)
                conn.execute(
                    "UPDATE wt_ops_v2_armed SET state='EXPIRED', disarmed_at=?, "
                    "disarm_reason='stale_no_launch' WHERE creator_wallet=?", (now, w))
                stats["expired"] += 1
                continue
            # still armed — retry enrolment if it never landed
            if not webhooked:
                _enroll(w, conn)
                stats["enroll_retried"] += 1
            stats["still_armed"] += 1
        conn.commit()
        return stats
    finally:
        if own:
            conn.close()


if __name__ == "__main__":
    import json
    print(json.dumps(reconcile_armed(), indent=2))
