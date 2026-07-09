"""
Treasury bank — the authoritative live-watch set + the review pipeline.

Clean architectural split:
  • wt_confirmed_treasuries   = the AUTHORITATIVE live detection anchors (source of truth).
                                 The forward-walk fires ONLY from this set. Pages read it.
  • wt_treasury_review        = candidate pipeline (discovery, review-only).
  • ops-graph roles           = supporting context, NOT authority.

Flow:  micro-ping candidate → RPC confirm (3-signal) → wt_treasury_review (PENDING)
       → human confirm → promote to wt_confirmed_treasuries → webhook.

NEW treasuries are NEVER auto-promoted from webhook hits — promotion is a deliberate
human action via promote_to_confirmed().
"""
from __future__ import annotations

import os
import json
import time

try:
    from src.utils.db_locking import db_connect
except Exception:                                    # pragma: no cover
    import sqlite3
    def db_connect(path, timeout=30):
        c = sqlite3.connect(path, timeout=timeout); c.row_factory = sqlite3.Row; return c

OPS_DB_PATH = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "database", "wt_ops_v2.db"))

_schema_ensured = False


def _add_cols_if_missing(conn, table: str, cols: list) -> None:
    existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for col, defn in cols:
        if col not in existing:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}")
            except Exception:
                pass


def _ensure_schema_once() -> None:
    global _schema_ensured
    if _schema_ensured:
        return
    try:
        c = db_connect(OPS_DB_PATH, timeout=30)
        ensure_schema(c)
        c.commit()
        c.close()
        _schema_ensured = True
    except Exception:
        pass



def ensure_schema(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS wt_confirmed_treasuries (
        treasury TEXT PRIMARY KEY, transfer_pct INTEGER, out_sol REAL, recipients INTEGER,
        micro_pings INTEGER, method TEXT, confidence TEXT, confirmed_at INTEGER)""")
    # provenance: CONFIRMED_SEED (manual/hand-verified) vs CONFIRMED_AUTO (fingerprint).
    # Lets us separate seed truth from auto truth if the fingerprint ever leaks.
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(wt_confirmed_treasuries)").fetchall()]
        if "provenance" not in cols:
            conn.execute("ALTER TABLE wt_confirmed_treasuries ADD COLUMN provenance TEXT DEFAULT 'CONFIRMED_SEED'")
    except Exception:
        pass
    # AUDIT LEDGER — every fingerprint decision, reversible. Auto-promotion must never
    # silently mutate core truth; this is the trail.
    conn.execute("""CREATE TABLE IF NOT EXISTS wt_treasury_fingerprint_decisions (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        wallet          TEXT NOT NULL,
        decision        TEXT NOT NULL,          -- CONFIRMED | NEAR_MISS | REJECT
        signals_json    TEXT,
        evidence_txs_json TEXT,
        source_migration TEXT,                  -- the migrated creator that surfaced it
        promoted_at     INTEGER,                -- set if it became CONFIRMED_AUTO
        webhook_status  TEXT,                   -- PENDING | WEBHOOKED | FAILED | N/A
        decided_at      INTEGER NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_tfd_wallet ON wt_treasury_fingerprint_decisions(wallet)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_tfd_decision ON wt_treasury_fingerprint_decisions(decision)")
    conn.execute("""CREATE TABLE IF NOT EXISTS wt_confirmed_treasury_webhooks (
        treasury TEXT PRIMARY KEY, source TEXT DEFAULT 'CONFIRMED_TREASURY',
        enrolled_at INTEGER, webhook_active INTEGER DEFAULT 1, last_hit INTEGER,
        last_fanout INTEGER, last_strict_candidate INTEGER, last_fired_token TEXT,
        last_fired_at INTEGER)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS wt_treasury_review (
        treasury TEXT PRIMARY KEY, transfer_pct INTEGER, out_sol REAL, recipients INTEGER,
        micro_pings INTEGER, detected_via TEXT, status TEXT DEFAULT 'PENDING_REVIEW',
        reviewed_by TEXT, detected_at INTEGER, reviewed_at INTEGER)""")
    # Walkback-sourced evidence columns (migration-safe)
    _add_cols_if_missing(conn, "wt_treasury_review", [
        ("subprov_wallet",      "TEXT"),
        ("creator_wallet",      "TEXT"),
        ("token_mint",          "TEXT"),
        ("distinct_subprovs",   "INTEGER DEFAULT 1"),
        ("distinct_creators",   "INTEGER DEFAULT 1"),
        ("evidence_sigs",       "TEXT"),   # JSON array of unique funding sigs
        ("evidence_subprovs",   "TEXT"),   # JSON array of unique subprov wallets seen
        ("evidence_creators",   "TEXT"),   # JSON array of unique creator wallets seen
        ("evidence_mints",      "TEXT"),   # JSON array of unique token mints seen
        ("has_walkback_evidence","INTEGER DEFAULT 0"),  # 1 if ever surfaced by walkback
        ("first_walkback_at",   "INTEGER"),
        ("last_walkback_at",    "INTEGER"),
    ])
    conn.commit()


# ── review pipeline ──────────────────────────────────────────────────────────
def add_review_candidate(conn, treasury, *, transfer_pct=None, out_sol=None,
                         recipients=None, micro_pings=None, detected_via="micro_ping") -> bool:
    """Add an RPC-confirmed candidate to the review queue. Skips ones already confirmed, and
    REJECTS known sub-provisioners — a wallet with recorded wrap-close fan-out is a SUBPROV, not a
    treasury. The treasury fingerprint (100% transfer-purity + capital-scale) CANNOT tell them
    apart on flow alone: a subprov's wrap-close fan-out is also 100% pure transfers to many
    recipients, so subprovs like Efm1jBsiGv8k (15 wrap-closes) falsely fingerprinted as treasuries.
    The discriminator is what it PRODUCES: wrap-close children = subprov."""
    _ensure_schema_once()
    if conn.execute("SELECT 1 FROM wt_confirmed_treasuries WHERE treasury=?", (treasury,)).fetchone():
        return False                                  # already a confirmed treasury
    try:
        if conn.execute(
            "SELECT 1 FROM wt_wrap_close_candidates WHERE subprov_wallet=? LIMIT 1",
            (treasury,)).fetchone():
            return False                              # has wrap-close fan-out → SUBPROV, not treasury
    except Exception:
        pass
    conn.execute(
        """INSERT INTO wt_treasury_review
             (treasury, transfer_pct, out_sol, recipients, micro_pings, detected_via,
              status, detected_at)
           VALUES (?,?,?,?,?,?, 'PENDING_REVIEW', ?)
           ON CONFLICT(treasury) DO UPDATE SET
             transfer_pct=excluded.transfer_pct, out_sol=excluded.out_sol,
             recipients=excluded.recipients, micro_pings=excluded.micro_pings""",
        (treasury, transfer_pct, out_sol, recipients, micro_pings, detected_via, int(time.time())))
    conn.commit()
    return True


# Addresses that must never be surfaced as treasury review leads.
# This is a boundary defence — the worker also filters these, but this function
# must be safe to call from any path.
_TREASURY_LEAD_BLOCKLIST: frozenset = frozenset({
    # System / native programs
    "11111111111111111111111111111111",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJe1bzvs",
    "ComputeBudget111111111111111111111111111111",
    "SysvarRent111111111111111111111111111111111",
    "SysvarC1ock11111111111111111111111111111111",
    "Vote111111111111111111111111111111111111111h",
    # pump.fun + PumpSwap programs / authorities
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",   # pump.fun program
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",   # PumpSwap AMM program
    "39azUYFWPez6bovNoRRfmmJMeGouAr6j7K43BuVFiaTD",   # pump.fun migration authority
    "BSjC7wR1kRQhjBsBiqgB6p2H5nm4shKmkDw3vzmrE8k8",  # PumpSwap WSOL market pool
    # Raydium
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",  # Raydium fee account
    "7YttLkHDoNj9wyDur5pM1ejNaAvT9X4eqaYcHQqtj2G5",  # Raydium authority
    # Burn / null
    "1nc1nerator11111111111111111111111111111111",
})


def add_walkback_hop2_lead(conn, upstream_wallet: str, *,
                           subprov_wallet: str | None = None,
                           creator_wallet: str | None = None,
                           token_mint: str | None = None,
                           funding_sig: str | None = None,
                           funding_amount_sol: float | None = None,
                           funding_mechanism: str | None = None) -> str:
    """
    Surface an unknown hop-2 wallet as a treasury review lead.

    Called by walkback_worker when FULL_WALKBACK finds:
        creator ← hop1 (subprov candidate) ← hop2 (upstream_wallet, unknown)

    Never auto-confirms. Never touches wt_confirmed_treasuries.
    Aggregates evidence correctly using deduped JSON sets — out_sol only added
    when the sig is new, distinct counts derived from set lengths.

    Returns a string disposition: 'inserted' | 'updated' | 'skipped:<reason>'
    """
    _ensure_schema_once()

    # ── boundary blocklist (self-defending) ──────────────────────────────────
    if upstream_wallet in _TREASURY_LEAD_BLOCKLIST:
        return "skipped:blocklist"

    # Already a confirmed treasury — no action needed
    if conn.execute("SELECT 1 FROM wt_confirmed_treasuries WHERE treasury=?",
                    (upstream_wallet,)).fetchone():
        return "skipped:confirmed_treasury"

    # Known subprov — distribution mesh, not treasury tier
    try:
        if conn.execute("SELECT 1 FROM wt_discovered_subprovs WHERE subprov=? LIMIT 1",
                        (upstream_wallet,)).fetchone():
            return "skipped:known_subprov"
    except Exception:
        pass

    # Reject if upstream IS the creator (self-rooted noise)
    if upstream_wallet == creator_wallet:
        return "skipped:self_rooted"

    now = int(time.time())

    existing = conn.execute(
        "SELECT distinct_subprovs, distinct_creators, out_sol, "
        "       evidence_sigs, evidence_subprovs, evidence_creators, evidence_mints "
        "FROM wt_treasury_review WHERE treasury=?",
        (upstream_wallet,)).fetchone()

    def _load(field) -> list:
        try:
            v = existing[field] if existing else None
            return json.loads(v) if v else []
        except Exception:
            return []

    sigs      = _load("evidence_sigs")
    subprovs  = _load("evidence_subprovs")
    creators  = _load("evidence_creators")
    mints     = _load("evidence_mints")

    # Only add capital and evidence if this is a genuinely new sig (prevents retry double-counting)
    sig_is_new = bool(funding_sig and funding_sig not in sigs)

    if sig_is_new and funding_sig:
        sigs.append(funding_sig)
    if subprov_wallet and subprov_wallet not in subprovs:
        subprovs.append(subprov_wallet)
    if creator_wallet and creator_wallet not in creators:
        creators.append(creator_wallet)
    if token_mint and token_mint not in mints:
        mints.append(token_mint)

    # out_sol only increments when a new sig is seen (dedup-gated)
    added_sol = (funding_amount_sol or 0) if sig_is_new else 0

    n_subprovs = max(1, len(subprovs))
    n_creators = max(1, len(creators))

    if existing:
        existing_sol = existing["out_sol"] or 0
        conn.execute("""
            UPDATE wt_treasury_review
            SET distinct_subprovs    = ?,
                distinct_creators    = ?,
                out_sol              = ?,
                evidence_sigs        = ?,
                evidence_subprovs    = ?,
                evidence_creators    = ?,
                evidence_mints       = ?,
                has_walkback_evidence= 1,
                last_walkback_at     = ?,
                subprov_wallet       = COALESCE(subprov_wallet, ?),
                creator_wallet       = COALESCE(creator_wallet, ?),
                token_mint           = COALESCE(token_mint, ?)
            WHERE treasury = ?
        """, (n_subprovs, n_creators, existing_sol + added_sol,
              json.dumps(sigs), json.dumps(subprovs), json.dumps(creators), json.dumps(mints),
              now, subprov_wallet, creator_wallet, token_mint,
              upstream_wallet))
        conn.commit()
        return "updated"
    else:
        conn.execute("""
            INSERT INTO wt_treasury_review
                (treasury, out_sol, detected_via, status, detected_at,
                 subprov_wallet, creator_wallet, token_mint,
                 distinct_subprovs, distinct_creators,
                 evidence_sigs, evidence_subprovs, evidence_creators, evidence_mints,
                 has_walkback_evidence, first_walkback_at, last_walkback_at)
            VALUES (?,?,?,?,?, ?,?,?, ?,?, ?,?,?,?, 1,?,?)
        """, (upstream_wallet, funding_amount_sol or 0, "walkback_hop2", "PENDING_REVIEW", now,
              subprov_wallet, creator_wallet, token_mint,
              n_subprovs, n_creators,
              json.dumps(sigs), json.dumps(subprovs), json.dumps(creators), json.dumps(mints),
              now, now))
        conn.commit()
        return "inserted"


def promote_to_confirmed(conn, treasury, reviewed_by="human") -> dict:
    """Human action: promote a reviewed candidate into the authoritative confirmed set.
    Does NOT webhook here — the caller webhooks (needs the live-DB Helius key)."""
    _ensure_schema_once()
    r = conn.execute(
        "SELECT transfer_pct, out_sol, recipients, micro_pings FROM wt_treasury_review WHERE treasury=?",
        (treasury,)).fetchone()
    if not r:
        return {"ok": False, "error": "not in review queue"}
    now = int(time.time())
    conn.execute(
        """INSERT INTO wt_confirmed_treasuries
             (treasury, transfer_pct, out_sol, recipients, micro_pings, method, confidence, provenance, confirmed_at)
           VALUES (?,?,?,?,?, 'REVIEW_PROMOTED', 'CONFIRMED', 'CONFIRMED_REVIEW', ?)
           ON CONFLICT(treasury) DO NOTHING""",
        (treasury, r[0], r[1], r[2], r[3], now))
    conn.execute(
        "UPDATE wt_treasury_review SET status='CONFIRMED', reviewed_by=?, reviewed_at=? WHERE treasury=?",
        (reviewed_by, now, treasury))
    conn.commit()
    return {"ok": True, "treasury": treasury, "needs_webhook": True}


def reject_candidate(conn, treasury, reviewed_by="human") -> dict:
    _ensure_schema_once()
    conn.execute(
        "UPDATE wt_treasury_review SET status='REJECTED', reviewed_by=?, reviewed_at=? WHERE treasury=?",
        (reviewed_by, int(time.time()), treasury))
    conn.commit()
    return {"ok": True, "treasury": treasury}


# ── reads for the dashboard ──────────────────────────────────────────────────
def confirmed_treasuries(conn) -> list:
    _ensure_schema_once()
    return [dict(r) for r in conn.execute(
        """SELECT c.treasury, c.transfer_pct, c.out_sol, c.recipients, c.micro_pings,
                  c.confidence, COALESCE(w.webhook_active,0) webhooked,
                  w.last_hit, w.last_fanout, w.last_strict_candidate, w.last_fired_token, w.last_fired_at
           FROM wt_confirmed_treasuries c
           LEFT JOIN wt_confirmed_treasury_webhooks w ON w.treasury=c.treasury
           ORDER BY c.out_sol DESC""").fetchall()]


def review_queue(conn) -> list:
    _ensure_schema_once()
    return [dict(r) for r in conn.execute(
        "SELECT * FROM wt_treasury_review WHERE status='PENDING_REVIEW' ORDER BY out_sol DESC").fetchall()]


# ── the treasury FINGERPRINT — well-defined, deterministic, no human needed ──
# 1. TRANSFER-purity : ≥95% of txs are plain TRANSFER (no SWAP/trade) — separates treasury
#                      from trading wallets (the dominant false positive)
# 2. capital-scale   : distributes ≥50 SOL to ≥3 recipients
# 3. micro-ping      : carries the ~0.00001 SOL coordination-ping signature
# 4. RECENCY (gate)  : active within the last STALE_DAYS — a dormant wallet is not a live
#                      provisioning treasury, however treasury-shaped it looks.
STRICT_TRANSFER_PCT = 95
STRICT_MIN_OUT_SOL = 50.0
STRICT_MIN_RECIPIENTS = 3
STALE_DAYS = 7                  # no activity in this long → STALE, not an active treasury


def fingerprint_treasury(addr: str, raw_txs_fn, micro_ping_count: int = 0) -> dict:
    """Run the treasury fingerprint on `addr`. raw_txs_fn(addr) -> list of Helius-shaped
    txs with `type`, `nativeTransfers`, and `timestamp` (RAW RPC, cheap). Returns the
    measured signals + a verdict: CONFIRMED | REVIEW | REJECT | STALE."""
    txs = raw_txs_fn(addr) or []
    n = len(txs)
    if n == 0:
        return {"verdict": "REJECT", "reason": "no txs"}
    transfer = sum(1 for t in txs if (t.get("type") or "").upper() == "TRANSFER")
    transfer_pct = round(100 * transfer / n)
    out_sol = 0.0
    recipients = set()
    last_ts = 0
    raw_ping_count = 0          # micro-pings counted FROM THE RAW TXS (same source as the other
                                # signals) — works for candidates that aren't webhooked. The
                                # wt_webhook_hits-based micro_ping_count only sees WEBHOOKED
                                # wallets, so post-migration lineage-discovered candidates always
                                # scored 0 pings and could never reach 3/3 (stuck at REVIEW).
    for t in txs:
        last_ts = max(last_ts, t.get("timestamp") or t.get("blockTime") or 0)
        for nt in t.get("nativeTransfers", []) or []:
            amt = (nt.get("amount", 0) or 0) / 1e9
            if nt.get("fromUserAccount") == addr:
                out_sol += amt
                recipients.add(nt.get("toUserAccount"))
                if 0.000005 <= amt <= 0.00002:      # ~0.00001 SOL coordination ping
                    raw_ping_count += 1

    age_days = (time.time() - last_ts) / 86400 if last_ts else 9999
    # prefer the webhook-based count (more complete history) but FALL BACK to the raw-tx count
    # so non-webhooked candidates still get a real ping signal instead of a forced 0.
    micro_ping_count = max(micro_ping_count or 0, raw_ping_count)
    sig = {"transfer_pct": transfer_pct, "out_sol": round(out_sol, 1),
           "recipients": len(recipients), "micro_pings": micro_ping_count, "tx_count": n,
           "last_active_days": round(age_days, 1)}

    # RECENCY GATE first — a stale wallet is not an active treasury, period.
    if age_days > STALE_DAYS:
        sig["verdict"] = "STALE"
        sig["reason"] = f"dormant {age_days:.0f}d (>{STALE_DAYS}d) — not an active treasury"
        return sig

    pure = transfer_pct >= STRICT_TRANSFER_PCT
    capital = out_sol >= STRICT_MIN_OUT_SOL and len(recipients) >= STRICT_MIN_RECIPIENTS
    ping = micro_ping_count > 0

    if pure and capital and ping:
        sig["verdict"] = "CONFIRMED"           # all 3 + recent → auto-promote
    elif pure and (capital or ping):
        sig["verdict"] = "REVIEW"              # treasury-shaped but missing one signal
    else:
        sig["verdict"] = "REJECT"              # not a treasury (trades, or no scale)
    sig["reason"] = f"transfer={transfer_pct}% out={out_sol:.0f}SOL/{len(recipients)} pings={micro_ping_count} active={age_days:.0f}d"
    return sig


def _log_decision(conn, wallet, decision, signals, *, source_migration=None,
                  evidence_txs=None, promoted_at=None, webhook_status="N/A") -> None:
    """Append to the audit ledger — every fingerprint decision is recorded."""
    conn.execute(
        """INSERT INTO wt_treasury_fingerprint_decisions
             (wallet, decision, signals_json, evidence_txs_json, source_migration,
              promoted_at, webhook_status, decided_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (wallet, decision, json.dumps(signals), json.dumps(evidence_txs or []),
         source_migration, promoted_at, webhook_status, int(time.time())))


def auto_evaluate(conn, addr: str, raw_txs_fn, micro_ping_count: int = 0, *,
                  source_migration: Optional[str] = None,
                  evidence_txs: Optional[list] = None) -> dict:
    """Fingerprint a candidate and ACT automatically (no human):
       CONFIRMED → promote into wt_confirmed_treasuries as CONFIRMED_AUTO (caller webhooks).
       NEAR_MISS → add to wt_treasury_review for an optional look.
       REJECT    → nothing.
    EVERY decision is written to the audit ledger (reversible). Idempotent."""
    _ensure_schema_once()
    if conn.execute("SELECT 1 FROM wt_confirmed_treasuries WHERE treasury=?", (addr,)).fetchone():
        return {"verdict": "ALREADY_CONFIRMED", "treasury": addr, "needs_webhook": False}
    fp = fingerprint_treasury(addr, raw_txs_fn, micro_ping_count)
    v = fp["verdict"]
    now = int(time.time())
    # NO AUTO-PROMOTION — every candidate, even a full 3/3 CONFIRMED, goes to the human review
    # queue and requires an explicit ✓ promote. A 3/3 lands as a high-confidence (READY) candidate;
    # a 2/3 lands as a near-miss. Promotion into wt_confirmed_treasuries only happens via
    # promote_to_confirmed() (the human "✓ promote" button). This is deliberate: auto-re-rooting /
    # auto-confirming overshoots (a treasury-shaped wallet may be a peer mesh node, a piggybacking
    # op, etc.) — judgment stays with the human.
    if v in ("CONFIRMED", "REVIEW"):
        add_review_candidate(conn, addr, transfer_pct=fp["transfer_pct"], out_sol=fp["out_sol"],
                             recipients=fp["recipients"], micro_pings=fp.get("micro_pings", 0),
                             detected_via=("auto_fingerprint_3of3" if v == "CONFIRMED"
                                           else "auto_fingerprint_nearmiss"))
        _log_decision(conn, addr, ("READY_3OF3" if v == "CONFIRMED" else "NEAR_MISS"), fp,
                      source_migration=source_migration, evidence_txs=evidence_txs)
        conn.commit()
        # signals carry the verdict so the UI can show "3/3 ready" vs "2/3 review"; needs_webhook
        # stays False — webhooking happens only on human promote.
        return {"verdict": v, "treasury": addr, "needs_webhook": False, "signals": fp,
                "queued_for_review": True}
    _log_decision(conn, addr, "REJECT", fp, source_migration=source_migration,
                  evidence_txs=evidence_txs)
    conn.commit()
    return {"verdict": v, "treasury": addr, "needs_webhook": False, "signals": fp}


def auto_confirm_from_launch_chain(conn, treasury: str, *, subprov: str, creator: str,
                                   mint: str, create_sig: Optional[str] = None) -> dict:
    """BEHAVIORAL auto-confirm: a wallet that funds a SUB_PROV which demonstrably
    wrap-closed → a CREATOR that actually CREATEd a pump.fun token IS a WATCHTOWER
    treasury — by the definition of the mechanism, not a statistical fingerprint. This
    is the STRONGEST evidence (the chain completed), and unlike the transfer-purity
    fingerprint it cannot be fooled by a recycling/trading desk (those never produce a
    CREATE — see the 5WG7sw case). Auto-confirms the treasury so its FUTURE provisioning
    arms in real time, instead of waiting on a manual funder-trace (the gap that missed
    DchJqu's 14:07 wrap-close: discovered 12:55, manually confirmed 16:56).

    Idempotent. Returns {verdict, treasury, needs_webhook}. The caller webhooks +
    WS-subscribes on needs_webhook=True."""
    _ensure_schema_once()
    if not treasury or not creator or not mint:
        return {"verdict": "INSUFFICIENT", "treasury": treasury, "needs_webhook": False}
    if conn.execute("SELECT 1 FROM wt_confirmed_treasuries WHERE treasury=?", (treasury,)).fetchone():
        return {"verdict": "ALREADY_CONFIRMED", "treasury": treasury, "needs_webhook": False}
    now = int(time.time())
    conn.execute(
        """INSERT INTO wt_confirmed_treasuries
             (treasury, method, confidence, provenance, confirmed_at)
           VALUES (?, 'LAUNCH_CHAIN', 'STRICT', 'CONFIRMED_LAUNCH_CHAIN', ?)
           ON CONFLICT(treasury) DO NOTHING""",
        (treasury, now))
    _log_decision(conn, treasury, "CONFIRMED",
                  {"verdict": "CONFIRMED", "reason": "treasury→subprov→wrap-close→creator→CREATE chain",
                   "subprov": subprov, "creator": creator, "mint": mint},
                  evidence_txs=[create_sig] if create_sig else [],
                  promoted_at=now, webhook_status="PENDING")
    conn.commit()
    return {"verdict": "CONFIRMED", "treasury": treasury, "needs_webhook": True,
            "provenance": "CONFIRMED_LAUNCH_CHAIN",
            "evidence": {"subprov": subprov, "creator": creator, "mint": mint}}


def mark_webhooked(conn, treasury: str, ok: bool = True) -> None:
    """Update the ledger's webhook_status for the most recent decision of this treasury."""
    conn.execute(
        "UPDATE wt_treasury_fingerprint_decisions SET webhook_status=? "
        "WHERE wallet=? AND id=(SELECT MAX(id) FROM wt_treasury_fingerprint_decisions WHERE wallet=?)",
        ("WEBHOOKED" if ok else "FAILED", treasury, treasury))
    conn.commit()


def revert_auto_promotion(conn, treasury: str, reason: str = "manual_revert") -> dict:
    """Reverse an AUTO promotion (seed treasuries are protected — cannot be reverted here).
    Removes from confirmed set + logs the reversal. Caller un-webhooks separately."""
    _ensure_schema_once()
    r = conn.execute("SELECT provenance FROM wt_confirmed_treasuries WHERE treasury=?", (treasury,)).fetchone()
    if not r:
        return {"ok": False, "error": "not confirmed"}
    if r[0] == "CONFIRMED_SEED":
        return {"ok": False, "error": "seed treasury — protected from auto-revert"}
    conn.execute("DELETE FROM wt_confirmed_treasuries WHERE treasury=?", (treasury,))
    _log_decision(conn, treasury, "REVERTED", {"reason": reason}, webhook_status="REMOVE")
    conn.commit()
    return {"ok": True, "treasury": treasury, "reverted": True}


def micro_ping_count(live_conn, addr: str) -> int:
    """Local count of the ~0.00001 SOL coordination-ping signature for `addr`
    (from wt_webhook_hits — no RPC). Part of the treasury fingerprint."""
    try:
        r = live_conn.execute(
            "SELECT COUNT(*) FROM wt_webhook_hits WHERE wallet_address=? "
            "AND amount_sol BETWEEN 0.000005 AND 0.00002", (addr,)).fetchone()
        return r[0] if r else 0
    except Exception:
        return 0


def _discover_treasury_funder(conn, live_conn, treasury: str, funder_walk_fn, raw_txs_fn) -> None:
    """AUTO-DISCOVERY of the layer ABOVE a known treasury: who funds it? A treasury's top
    funder that is itself treasury-shaped (pure-transfer + capital-scale) is a NEW treasury
    (the network is a mesh — treasuries fund treasuries, e.g. G2CQewGx funds Cgwr+43PKjr).
    Queues treasury-shaped funders for review; marks evaluated funders so they aren't redone.
    One extra hop per already-confirmed lineage — cheap, raw RPC only."""
    funders = funder_walk_fn(treasury) or []
    if not funders:
        return
    # Evaluate the TOP FEW funders (not just #1) — a recycling subprov can outrank the genuine
    # treasury-funder (e.g. 43PKjr's #1 is a sweep subprov, but #3 G2CQewGx is the real treasury
    # above it). Check the top 3 capital funders, skipping known/decided/recycling ones.
    for top, _amt in sorted(funders, key=lambda x: -x[1])[:3]:
        if not top:
            continue
        # skip if already confirmed, reviewed, or fingerprint-decided
        if conn.execute("SELECT 1 FROM wt_confirmed_treasuries WHERE treasury=?", (top,)).fetchone():
            continue
        if conn.execute("SELECT 1 FROM wt_treasury_review WHERE treasury=?", (top,)).fetchone():
            continue
        if conn.execute("SELECT 1 FROM wt_treasury_fingerprint_decisions WHERE wallet=?", (top,)).fetchone():
            continue
        # RECYCLING guard: if the treasury also SENT to this funder, it's recycling, not apex.
        try:
            paid = live_conn.execute(
                "SELECT 1 FROM wt_webhook_hits WHERE direction='outbound' AND wallet_address=? AND counterparty=? LIMIT 1",
                (treasury, top)).fetchone()
            if paid:
                _log_decision(conn, top, "REJECT", {"reason": "recycling — treasury funded it"},
                              source_migration=treasury)
                continue
        except Exception:
            pass
        _evaluate_funder_candidate(conn, live_conn, treasury, top, raw_txs_fn)


def rescore_decided_candidates(conn, raw_txs_fn, *, max_candidates: int = 40,
                               include_rejected: bool = True) -> dict:
    """ONE-SHOT re-evaluation of fingerprint candidates already in wt_treasury_review, using the
    CURRENT fingerprint logic (raw-tx micro-pings). Built for the webhook-blind ping fix: every
    prior near-miss scored ping=0 because micro_ping_count only saw webhooked wallets, so strong
    treasuries were stuck at 2/3 (and many human-rejected on a broken signal). Re-runs the
    fingerprint; a candidate that now hits 3/3 is bumped to a high-confidence READY review row
    (status reset to PENDING_REVIEW). NEVER auto-promotes — still needs the human ✓. Bounded RPC
    (raw_txs_fn per candidate, capped). Returns a summary."""
    _ensure_schema_once()
    try:
        conn.execute("PRAGMA busy_timeout=15000")
    except Exception:
        pass
    statuses = "('PENDING_REVIEW','REJECTED')" if include_rejected else "('PENDING_REVIEW')"
    cands = [r[0] for r in conn.execute(
        f"SELECT treasury FROM wt_treasury_review "
        f"WHERE status IN {statuses} AND (detected_via LIKE 'auto_fingerprint%' "
        f"OR detected_via LIKE 'funder_discovery%') ORDER BY out_sol DESC LIMIT ?",
        (max_candidates,)).fetchall()]
    promoted_ready, still_near, now = [], 0, int(time.time())
    for addr in cands:
        try:
            fp = fingerprint_treasury(addr, raw_txs_fn, micro_ping_count=0)  # raw-tx pings only
        except Exception:
            continue
        if fp.get("verdict") == "CONFIRMED":     # now 3/3 with the fixed ping → bump to READY
            conn.execute(
                "UPDATE wt_treasury_review SET status='PENDING_REVIEW', micro_pings=?, "
                "detected_via='rescore_3of3', detected_at=? WHERE treasury=?",
                (fp.get("micro_pings", 0), now, addr))
            promoted_ready.append((addr, fp.get("out_sol"), fp.get("micro_pings")))
        elif fp.get("verdict") == "REVIEW":
            still_near += 1
    conn.commit()
    return {"checked": len(cands), "now_ready_3of3": len(promoted_ready),
            "still_near_miss": still_near, "ready": promoted_ready}


def _evaluate_funder_candidate(conn, live_conn, treasury, top, raw_txs_fn) -> None:
    """Fingerprint one treasury-funder candidate; promote/review/reject."""
    fp = fingerprint_treasury(top, raw_txs_fn, micro_ping_count=micro_ping_count(live_conn, top))
    v = fp.get("verdict")
    # NO AUTO-PROMOTION from the fingerprint — a 3/3 lands as a high-confidence (READY) review
    # candidate, a near-miss as a 2/3. Promotion only via the human ✓ promote. (Only the
    # behavioral LAUNCH-CHAIN path — a completed CREATE — auto-confirms.)
    if v == "CONFIRMED" or v == "REVIEW" or (fp.get("transfer_pct", 0) >= 95
            and fp.get("out_sol", 0) >= 50 and fp.get("recipients", 0) >= 3):
        add_review_candidate(conn, top, transfer_pct=fp.get("transfer_pct"), out_sol=fp.get("out_sol"),
                             recipients=fp.get("recipients"), micro_pings=fp.get("micro_pings", 0),
                             detected_via=("funder_discovery_3of3" if v == "CONFIRMED"
                                           else "funder_discovery"))
        _log_decision(conn, top, ("READY_3OF3" if v == "CONFIRMED" else "NEAR_MISS"), fp,
                      source_migration=treasury)
    else:
        _log_decision(conn, top, "REJECT", fp, source_migration=treasury)


def evaluate_lineage_root(conn, live_conn, creator: str, funder_walk_fn, raw_txs_fn,
                          max_hops: int = 4) -> Optional[dict]:
    """POST-MIGRATION TRIGGER. Walk up from a migrated creator to the funding-chain root,
    then auto-fingerprint that root as a treasury. No human gate: strict pass auto-promotes.

    funder_walk_fn(addr)->[(funder, amount_sol)]; raw_txs_fn(addr)->[txs]. Both raw RPC.
    Returns the auto_evaluate result for the root, or None if no root reached."""
    cur, seen = creator, {creator}
    root = None
    hops = 0
    for _ in range(max_hops):
        funders = funder_walk_fn(cur) or []
        if not funders:
            root = cur if cur != creator else None
            break
        top = max(funders, key=lambda x: x[1])[0]
        if top in seen:
            root = cur; break
        seen.add(top); cur = top; root = top; hops += 1
        if conn.execute("SELECT 1 FROM wt_confirmed_treasuries WHERE treasury=?", (root,)).fetchone():
            # creator's lineage already rooted at a known treasury — log it so we don't redo.
            # FUNDER-LAYER DISCOVERY: don't stop here — walk ONE hop ABOVE the known treasury
            # to find what FUNDS it (the G2CQewGx layer). A treasury's top funder that is
            # ITSELF treasury-shaped (pure-transfer + capital-scale) is a NEW treasury (network
            # mesh: treasuries fund treasuries). This is the auto-discovery of the layer above.
            try:
                _discover_treasury_funder(conn, live_conn, root, funder_walk_fn, raw_txs_fn)
            except Exception:
                pass
            _log_decision(conn, creator, "ALREADY_CONFIRMED",
                          {"root": root, "hops": hops}, source_migration=creator)
            conn.commit()
            return {"verdict": "ALREADY_CONFIRMED", "treasury": root, "needs_webhook": False}
    if not root:
        # no funding root reached — still LOG it against the creator so it's marked done
        # (won't be re-checked next cycle) and the audit trail is complete.
        _log_decision(conn, creator, "NO_ROOT", {"hops": hops, "reason": "no funding root within max_hops"},
                      source_migration=creator)
        conn.commit()
        return {"verdict": "NO_ROOT", "treasury": None, "needs_webhook": False}
    return auto_evaluate(conn, root, raw_txs_fn, micro_ping_count(live_conn, root),
                         source_migration=creator)
