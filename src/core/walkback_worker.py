"""
walkback_worker — drains wt_walkback_queue PARTIAL_* and FULL_WALKBACK rows.

Runs as a standalone daemon (python -m src.core.walkback_worker --loop) or
can be called inline for a single drain pass (drain_batch).

Safety rules:
  - Claim rows to status='running' before processing (crash-safe)
  - Strict per-batch and per-row RPC caps
  - max_attempts before permanently marking failed
  - All DB writes via busy_timeout; never blocks the live pipeline
  - LINK_ONLY / SKIP rows are already complete at enqueue time — worker ignores them
  - Never calls the 100cr enhanced-tx endpoint

RPC cost per row:
  PARTIAL_TREASURY:  1 getSignaturesForAddress + up to 5 getTransaction = 6cr max
  PARTIAL_SUBPROV:   1 getSignaturesForAddress + up to 5 getTransaction = 6cr max
  FULL_WALKBACK:     2 getSignaturesForAddress + up to 10 getTransaction = 12cr max
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
import sqlite3
import argparse
from typing import Any, Optional
from src.utils.db_locking import db_connect

_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "../.."))

# Addresses that are program accounts, AMM pools, or system accounts — never valid as a
# creator funder. Expand as new false-positives are identified.
_FUNDER_BLOCKLIST: frozenset[str] = frozenset({
    # Confirmed PumpSwap AMM pool accounts
    "BSjC7wR1kRQhjBsBiqgB6p2H5nm4shKmkDw3vzmrE8k8",  # PumpSwap AMM pool (WSOL market)
    # Known system/program addresses
    "11111111111111111111111111111111",                 # System program
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",   # Token program
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJe1bzvs", # Associated Token Account program
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",   # PumpSwap AMM program
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",   # pump.fun program
    "ComputeBudget111111111111111111111111111111",      # Compute budget program
    "SysvarRent111111111111111111111111111111111",      # Rent sysvar
})

OPS_DB_PATH = os.environ.get(
    "WT_OPS_DB_PATH",
    os.path.join(_REPO_ROOT, "database", "wt_ops_v2.db"),
)
LIVE_DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.join(_REPO_ROOT, "database", "flex_complete_database.db"),
)
RPC_URL = os.environ.get(
    "HELIUS_RPC_URL",
    "https://mainnet.helius-rpc.com/?api-key=16f1a5fc-2592-466c-a5d4-b5799ae8da96",
)

# Tuning knobs
BATCH_SIZE      = int(os.environ.get("WALKBACK_BATCH_SIZE",      "8"))
INTERVAL_SEC    = int(os.environ.get("WALKBACK_INTERVAL_SEC",    "45"))
MAX_ATTEMPTS    = int(os.environ.get("WALKBACK_MAX_ATTEMPTS",    "3"))
RPC_BUDGET_BATCH= int(os.environ.get("WALKBACK_RPC_BUDGET_BATCH","80"))  # credits per batch
SIG_LIMIT       = int(os.environ.get("WALKBACK_SIG_LIMIT",       "20"))  # getSignatures limit
TX_FETCH_LIMIT  = int(os.environ.get("WALKBACK_TX_FETCH_LIMIT",  "5"))   # max getTransaction per hop
RPC_TIMEOUT     = int(os.environ.get("WALKBACK_RPC_TIMEOUT_S",   "8"))


# ── RPC helpers (blocking, for use in worker thread — never on asyncio loop) ──

def _rpc(method: str, params: list) -> Optional[object]:
    try:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                           "params": params}).encode()
        req = urllib.request.Request(
            RPC_URL, data=body,
            headers={"Content-Type": "application/json", "User-Agent": "walkback-worker/1.0"})
        return json.loads(urllib.request.urlopen(req, timeout=RPC_TIMEOUT).read()).get("result")
    except Exception as e:
        print(f"[WALKBACK] rpc {method} failed: {e}", flush=True)
        return None


def _get_sigs(wallet: str, limit: int = SIG_LIMIT) -> list:
    """getSignaturesForAddress — 1cr."""
    result = _rpc("getSignaturesForAddress", [wallet, {"limit": limit, "commitment": "confirmed"}])
    return result or []


def _get_tx(sig: str) -> Optional[dict]:
    """getTransaction — 1cr. Never uses the enhanced endpoint."""
    return _rpc("getTransaction", [sig, {
        "encoding": "jsonParsed",
        "maxSupportedTransactionVersion": 0,
        "commitment": "confirmed",
    }])


_SYSTEM_PROGRAM = "11111111111111111111111111111111"
_TOKEN_PROGRAM  = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"

def _get_account_owner(wallet: str, rpc_counter: list) -> Optional[str]:
    """
    getAccountInfo — 1cr. Returns the owner program of wallet, or None on error.
    System Program-owned = regular wallet. Token Program-owned = token/WSOL ATA.
    """
    result = _rpc("getAccountInfo", [wallet, {"encoding": "base64", "commitment": "confirmed"}])
    rpc_counter[0] += 1
    if not result:
        return None
    value = result.get("value")
    if not value:
        return None  # account closed / doesn't exist
    return value.get("owner")


def _is_program_owned(wallet: str, rpc_counter: list) -> bool:
    """
    Returns True if wallet is owned by any program other than the System Program.
    Returns False on any RPC error so we don't silently drop valid funders.
    """
    owner = _get_account_owner(wallet, rpc_counter)
    if owner is None:
        return False  # error or closed account — pass through
    return owner != _SYSTEM_PROGRAM


def _resolve_ata_owner(ata: str, rpc_counter: list) -> Optional[str]:
    """
    For a Token Program-owned ATA, return the wallet that holds it.
    getAccountInfo with jsonParsed encoding — 1cr.

    The SPL token account's parsed.info.owner is the controlling wallet.
    We then verify that wallet is System Program-owned (a real wallet, not a PDA).
    """
    result = _rpc("getAccountInfo", [ata, {"encoding": "jsonParsed", "commitment": "confirmed"}])
    rpc_counter[0] += 1
    if not result:
        return None
    value = result.get("value") or {}
    data  = value.get("data") or {}
    if not isinstance(data, dict):
        return None
    info  = data.get("parsed", {}).get("info", {})
    owner = info.get("owner")  # the wallet that holds/controls this token account
    if not owner:
        return None
    # Verify the owner wallet is itself system-program-owned (real wallet, not a PDA)
    owner_result = _rpc("getAccountInfo", [owner, {"encoding": "base64", "commitment": "confirmed"}])
    rpc_counter[0] += 1
    if not owner_result:
        return owner  # RPC error — return owner rather than silently drop
    owner_value = owner_result.get("value") or {}
    owner_of_owner = owner_value.get("owner", _SYSTEM_PROGRAM)
    if owner_of_owner != _SYSTEM_PROGRAM:
        return None  # owner is itself a program account — not a treasury/subprov wallet
    return owner


# ── on-chain lineage resolution ────────────────────────────────────────────────

def _extract_sol_sender(tx: dict, funded_wallet: Optional[str] = None) -> Optional[str]:
    """
    Return the wallet that paid SOL into funded_wallet (or accts[0] if not specified).
    Finds the account with the largest balance decrease that isn't the funded wallet.
    """
    if not tx:
        return None
    meta = tx.get("meta") or {}
    pre  = meta.get("preBalances")  or []
    post = meta.get("postBalances") or []
    accts = (tx.get("transaction", {})
               .get("message", {})
               .get("accountKeys") or [])
    if not accts or not pre or not post:
        return None

    def _key(a: Any) -> str:
        return a["pubkey"] if isinstance(a, dict) else a

    # Find the index of funded_wallet (or default to the account with the largest gain)
    if funded_wallet:
        target_idx = next((i for i, a in enumerate(accts) if _key(a) == funded_wallet), None)
        if target_idx is None:
            return None
        target_gain = post[target_idx] - pre[target_idx] if target_idx < len(pre) else 0
    else:
        # Fall back: account[0] gain
        target_gain = (post[0] if post else 0) - (pre[0] if pre else 0)
        target_idx  = 0

    if target_gain <= 0:
        return None

    # The sender is the account with the largest balance decrease (excluding the funded wallet)
    best_sender = None
    best_loss = 0
    for i, acct in enumerate(accts):
        if i == target_idx or i >= len(pre) or i >= len(post):
            continue
        loss = pre[i] - post[i]
        if loss > best_loss:
            best_loss = loss
            best_sender = _key(acct)
    return best_sender


def _detect_mechanism(tx: dict, sender: str, receiver: str) -> str:
    """
    Determine funding mechanism from tx. Returns WSOL_WRAP_CLOSE, PLAIN_XFER, or UNKNOWN.
    WSOL_WRAP_CLOSE: tx involves the token program (wrap/close cycle).
    PLAIN_XFER: pure system transfer, no token program instructions.
    """
    try:
        log_messages = (tx.get("meta") or {}).get("logMessages") or []
        for msg in log_messages:
            if "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA" in msg or "token" in msg.lower():
                return "WSOL_WRAP_CLOSE"
        instructions = (tx.get("transaction", {})
                          .get("message", {})
                          .get("instructions") or [])
        for ix in instructions:
            prog = ix.get("programId", "")
            if "Token" in prog or prog == "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA":
                return "WSOL_WRAP_CLOSE"
        return "PLAIN_XFER"
    except Exception:
        return "UNKNOWN"


def _extract_amount_sol(tx: dict, receiver: str) -> Optional[float]:
    """Return the SOL amount received by receiver in this tx, in SOL."""
    try:
        meta = tx.get("meta") or {}
        pre  = meta.get("preBalances")  or []
        post = meta.get("postBalances") or []
        accts = (tx.get("transaction", {})
                   .get("message", {})
                   .get("accountKeys") or [])
        for i, acct in enumerate(accts):
            addr = acct["pubkey"] if isinstance(acct, dict) else acct
            if addr == receiver and i < len(pre) and i < len(post):
                gain = post[i] - pre[i]
                return round(gain / 1e9, 6) if gain > 0 else None
    except Exception:
        pass
    return None


# FunderInfo: (wallet, sig, slot, block_time, amount_sol, mechanism)
FunderInfo = tuple[Optional[str], Optional[str], Optional[int], Optional[int],
                   Optional[float], Optional[str]]


_PRIORITY_REASON = {1: "CONFIRMED_TREASURY", 2: "KNOWN_SUBPROV",
                    4: "WSOL_WRAP_CLOSE",   5: "SEEDED_ACCOUNT_CLOSE", 6: "PLAIN_XFER"}


def _find_funder_via_rpc(wallet: str, rpc_counter: list,
                         ops: Optional[sqlite3.Connection] = None) -> FunderInfo:
    """
    Collect all valid funders within the bounded tx window then select the strongest.

    Priority (lower = better):
      1  confirmed treasury   — beats all mechanism evidence
      2  known subprov        — structural WATCHTOWER evidence
      4  WSOL_WRAP_CLOSE      — mechanism evidence
      5  SEEDED_ACCOUNT_CLOSE — mechanism evidence
      6  PLAIN_XFER / UNKNOWN — weakest

    Tie-break: oldest slot (lowest slot number = earliest funding edge).

    Logs: selected_funder, reason, candidates_seen — for false-positive diagnosis.
    RPC budget: unchanged — same getSignaturesForAddress + TX_FETCH_LIMIT getTransaction calls.
    getAccountInfo calls increase only when multiple candidates exist in the window.
    """
    sigs = _get_sigs(wallet)
    rpc_counter[0] += 1
    _empty: FunderInfo = (None, None, None, None, None, None)
    if not sigs:
        return _empty

    candidates: list[tuple[int, FunderInfo]] = []  # (priority, FunderInfo)

    for entry in sigs[:TX_FETCH_LIMIT]:
        if entry.get("err"):
            continue
        sig = entry.get("signature")
        if not sig:
            continue
        tx = _get_tx(sig)
        rpc_counter[0] += 1
        if not tx:
            continue
        sender = _extract_sol_sender(tx, wallet)
        if not sender or sender == wallet or sender in _FUNDER_BLOCKLIST:
            continue
        owner = _get_account_owner(sender, rpc_counter)
        if owner is None or owner == _SYSTEM_PROGRAM:
            pass  # regular wallet
        elif owner == _TOKEN_PROGRAM:
            real_sender = _resolve_ata_owner(sender, rpc_counter)
            if not real_sender or real_sender in _FUNDER_BLOCKLIST:
                continue
            sender = real_sender
        else:
            continue  # AMM pool, PDA, or other program account — skip

        mechanism  = _detect_mechanism(tx, sender, wallet)
        amount     = _extract_amount_sol(tx, wallet)
        slot       = tx.get("slot")
        block_time = tx.get("blockTime")

        if ops and _is_known_treasury(ops, sender):
            priority = 1
        elif ops and _is_known_subprov(ops, sender):
            priority = 2
        elif mechanism == "WSOL_WRAP_CLOSE":
            priority = 4
        elif mechanism == "SEEDED_ACCOUNT_CLOSE":
            priority = 5
        else:
            priority = 6

        candidates.append((priority, (sender, sig, slot, block_time, amount, mechanism)))

    if not candidates:
        return _empty

    # Select best: lowest priority wins; ties broken by oldest slot (earliest funding edge)
    candidates.sort(key=lambda x: (x[0], x[1][2] or 0))
    best_priority, best = candidates[0]
    reason = _PRIORITY_REASON.get(best_priority, "PLAIN_XFER")
    print(f"[WALKBACK] selected_funder={best[0][:14]}… reason={reason} "
          f"candidates_seen={len(candidates)} wallet={wallet[:14]}…", flush=True)
    return best


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _ops_conn() -> sqlite3.Connection:
    c = db_connect(OPS_DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def _is_known_treasury(ops: sqlite3.Connection, wallet: str) -> bool:
    row = ops.execute(
        "SELECT 1 FROM wt_confirmed_treasuries WHERE treasury=? LIMIT 1", (wallet,)).fetchone()
    return bool(row)


def _is_known_subprov(ops: sqlite3.Connection, wallet: str) -> bool:
    row = ops.execute(
        "SELECT 1 FROM wt_discovered_subprovs WHERE subprov=? LIMIT 1", (wallet,)).fetchone()
    return bool(row)


def _mark_running(ops: sqlite3.Connection, mint: str) -> bool:
    """Claim the row atomically. Returns False if another worker already claimed it."""
    now = int(time.time())
    ops.execute(
        "UPDATE wt_walkback_queue SET status='running', started_at=?, updated_at=?, "
        "attempts=attempts+1 WHERE mint=? AND status='pending'",
        (now, now, mint))
    claimed = ops.total_changes > 0
    ops.commit()
    return claimed


def _ensure_subprov_lead(ops: sqlite3.Connection, subprov: str, creator: Optional[str],
                         first_seen: Optional[int]) -> None:
    """
    If a LINEAGE_GAP surfaces a subprov not yet in wt_discovered_subprovs, insert it
    so it appears in the Unknown-Treasury Sub-Provisioners discovery panel.
    INSERT OR IGNORE — never overwrites an existing row.
    """
    now = int(time.time())
    ops.execute(
        """
        INSERT OR IGNORE INTO wt_discovered_subprovs
            (subprov, first_creator, creator_count, treasury, treasury_known,
             first_seen, last_seen, wrap_close_count, confidence, state)
        VALUES (?,?,1,NULL,0,?,?,1,0.5,'PROVISION_CANDIDATE')
        """,
        (subprov, creator, first_seen or now, now))


def _store_funder(ops: sqlite3.Connection, mint: str,
                  funder_wallet: Optional[str], sig: Optional[str],
                  slot: Optional[int], block_time: Optional[int],
                  amount_sol: Optional[float], mechanism: Optional[str]) -> None:
    """Persist hop-1 funder fields on the queue row — always called, even on NO_ATTRIBUTION_FOUND."""
    if not funder_wallet:
        return
    ops.execute(
        "UPDATE wt_walkback_queue SET funder_wallet=?, funder_sig=?, funder_slot=?, "
        "funder_block_time=?, funder_amount_sol=?, funding_mechanism=?, updated_at=? "
        "WHERE mint=?",
        (funder_wallet, sig, slot, block_time, amount_sol, mechanism, int(time.time()), mint))


def _mark_complete(ops: sqlite3.Connection, mint: str, outcome: str,
                   subprov: Optional[str], treasury: Optional[str], rpc_used: int,
                   confirmed_subprov: bool = False) -> None:
    """
    confirmed_subprov=True means subprov was verified against wt_discovered_subprovs before
    this call — safe to write into the attribution table. False (default) means the subprov
    is an unverified hop-1 candidate that should NOT be written as confirmed attribution.
    """
    now = int(time.time())
    ops.execute(
        "UPDATE wt_walkback_queue "
        "SET status='complete', intelligence_outcome=?, "
        "    subprov=COALESCE(subprov,?), treasury=COALESCE(treasury,?), "
        "    rpc_used=rpc_used+?, completed_at=?, updated_at=? "
        "WHERE mint=?",
        (outcome, subprov, treasury, rpc_used, now, now, mint))
    # Only write confirmed attribution — unverified hop candidates must never reach this table
    if confirmed_subprov or treasury:
        row = ops.execute("SELECT creator FROM wt_walkback_queue WHERE mint=?", (mint,)).fetchone()
        creator = row["creator"] if row else None
        if creator:
            ops.execute(
                """
                INSERT INTO watchtower_token_attribution
                    (mint, creator, matched_subprov, matched_treasury, score, tier, scored_at)
                VALUES (?,?,?,?,80,'WALKBACK',?)
                ON CONFLICT(mint) DO UPDATE SET
                    matched_subprov  = COALESCE(matched_subprov,  excluded.matched_subprov),
                    matched_treasury = COALESCE(matched_treasury, excluded.matched_treasury),
                    scored_at        = excluded.scored_at
                """,
                (mint, creator, subprov if confirmed_subprov else None, treasury, now))
    # LINEAGE_GAP: unconfirmed hop-1 → surface as subprov discovery lead, NOT as attribution.
    # Also surface the funder_wallet (the wallet that funded this unknown hop-1) as a treasury
    # review lead — a wallet that funded multiple subprovs across tokens is a treasury candidate.
    if outcome == "LINEAGE_GAP" and subprov and not treasury:
        row = ops.execute(
            "SELECT creator, enqueued_at, funder_wallet, funder_sig, funder_amount_sol "
            "FROM wt_walkback_queue WHERE mint=?", (mint,)).fetchone()
        creator    = row["creator"]         if row else None
        first_seen = row["enqueued_at"]     if row else None
        funder_w   = row["funder_wallet"]   if row else None
        funder_sig = row["funder_sig"]      if row else None
        funder_amt = row["funder_amount_sol"] if row else None
        if not _is_known_subprov(ops, subprov):
            _ensure_subprov_lead(ops, subprov, creator, first_seen)
            print(f"[WALKBACK] LINEAGE_GAP → promoted {subprov[:14]}… to subprov discovery lead",
                  flush=True)
        # Surface the wallet that funded this unconfirmed hop-1 as a treasury review lead
        if funder_w and funder_w != subprov and not _is_known_treasury(ops, funder_w) and not _is_known_subprov(ops, funder_w):
            disp = _surface_treasury_review_lead(ops, funder_w, subprov, creator, mint,
                                                 funder_sig, funder_amt, None)
            print(f"[WALKBACK] LINEAGE_GAP funder lead {disp}: {funder_w[:14]}…", flush=True)
    from src.ops.attribution_outcome import materialize_outcome
    materialize_outcome(ops, mint)
    ops.commit()


def _mark_failed(ops: sqlite3.Connection, mint: str, error: str, rpc_used: int) -> None:
    now = int(time.time())
    ops.execute(
        "UPDATE wt_walkback_queue "
        "SET status='failed', last_error=?, rpc_used=rpc_used+?, completed_at=?, updated_at=? "
        "WHERE mint=?",
        (error[:500], rpc_used, now, now, mint))
    from src.ops.attribution_outcome import materialize_outcome
    materialize_outcome(ops, mint)
    ops.commit()


def _mark_exhausted(ops: sqlite3.Connection, mint: str, rpc_used: int) -> None:
    """Max attempts reached — mark failed with NO_ATTRIBUTION_FOUND."""
    now = int(time.time())
    ops.execute(
        "UPDATE wt_walkback_queue "
        "SET status='failed', intelligence_outcome='NO_ATTRIBUTION_FOUND', "
        "    rpc_used=rpc_used+?, completed_at=?, updated_at=? "
        "WHERE mint=?",
        (rpc_used, now, now, mint))
    from src.ops.attribution_outcome import materialize_outcome
    materialize_outcome(ops, mint)
    ops.commit()


def finalize_exhausted_pending(ops: sqlite3.Connection, max_attempts: int = MAX_ATTEMPTS) -> int:
    """Close legacy/recovered rows that exhausted attempts but remained pending."""
    rows = ops.execute(
        "SELECT mint FROM wt_walkback_queue WHERE status='pending' AND attempts>=?",
        (max_attempts,),
    ).fetchall()
    for row in rows:
        _mark_exhausted(ops, row["mint"], 0)
    return len(rows)


# ── X21B provisioning-relationship capture (append-only facts, no attribution) ──

def _capture_provisioning_facts(
    ops: sqlite3.Connection, *, mint: str,
    treasury: Optional[str] = None, subprov: Optional[str] = None, creator: Optional[str] = None,
    treasury_to_subprov_sig: Optional[str] = None, treasury_to_subprov_block_time: Optional[int] = None,
    treasury_to_subprov_amount_sol: Optional[float] = None, treasury_to_subprov_mechanism: Optional[str] = None,
    subprov_to_creator_sig: Optional[str] = None, subprov_to_creator_block_time: Optional[int] = None,
    subprov_to_creator_amount_sol: Optional[float] = None, subprov_to_creator_mechanism: Optional[str] = None,
) -> None:
    """Best-effort capture of observed provisioning edges/session facts for this walk.
    Never raises — a capture failure must never break walkback completion. Writes no
    attribution, no operator identity, and calls no RPC of its own (all evidence here
    was already fetched by the walkback hop that called this)."""
    try:
        from src.ops.provisioning_edges import capture_provisioning_relationship
        capture_provisioning_relationship(
            ops, source_mint=mint, treasury=treasury, subprov=subprov, creator=creator,
            treasury_to_subprov_sig=treasury_to_subprov_sig,
            treasury_to_subprov_block_time=treasury_to_subprov_block_time,
            treasury_to_subprov_amount_sol=treasury_to_subprov_amount_sol,
            treasury_to_subprov_mechanism=treasury_to_subprov_mechanism,
            subprov_to_creator_sig=subprov_to_creator_sig,
            subprov_to_creator_block_time=subprov_to_creator_block_time,
            subprov_to_creator_amount_sol=subprov_to_creator_amount_sol,
            subprov_to_creator_mechanism=subprov_to_creator_mechanism,
        )
    except Exception as e:
        print(f"[WALKBACK] provisioning-edge capture failed for {mint[:16]}…: {e}", flush=True)


# ── treasury review lead surfacing ────────────────────────────────────────────

def _surface_treasury_review_lead(ops: sqlite3.Connection,
                                   upstream: str, subprov: str, creator: str, mint: str,
                                   funding_sig: Optional[str], funding_amount_sol: Optional[float],
                                   funding_mechanism: Optional[str]) -> str:
    """
    Surface unknown hop-2 as a treasury review lead in wt_treasury_review.
    Returns the disposition string from add_walkback_hop2_lead.
    """
    try:
        from src.core import treasury_bank as tb
        disp = tb.add_walkback_hop2_lead(
            ops,
            upstream,
            subprov_wallet=subprov,
            creator_wallet=creator,
            token_mint=mint,
            funding_sig=funding_sig,
            funding_amount_sol=funding_amount_sol,
            funding_mechanism=funding_mechanism,
        )
        return disp
    except Exception as e:
        print(f"[WALKBACK] failed to surface treasury review lead {upstream[:14]}…: {e}", flush=True)
        return "error"


# ── creator recovery (DB-only, zero RPC) ─────────────────────────────────────

def _recover_creator_from_db(ops: sqlite3.Connection, mint: str) -> Optional[str]:
    """Attempt to resolve a missing creator wallet from local DB tables.
    Called when wt_walkback_queue.creator is NULL — typically because the
    enqueue fired before the async creator-resolution task completed.

    Lookup order (most reliable → least reliable):
      1. wt_watchtower_launches   — immutable WATCHTOWER detection record
      2. token_analysis           — earliest_tx_creator / pf_ws_creator (live DB)
      3. migrated_tokens          — creator stored at migration time (live DB)

    Returns the first non-empty creator found, or None. Zero RPC.
    """
    # 1. wt_watchtower_launches (ops DB — already open, no extra connection)
    row = ops.execute(
        "SELECT creator_wallet FROM wt_watchtower_launches WHERE mint=? LIMIT 1",
        (mint,)).fetchone()
    if row and row["creator_wallet"]:
        return row["creator_wallet"]

    # 2 + 3. Live DB (token_analysis, migrated_tokens)
    try:
        live = sqlite3.connect(f"file:{LIVE_DB_PATH}?mode=ro", uri=True, timeout=5)
        live.row_factory = sqlite3.Row
        try:
            row = live.execute(
                "SELECT earliest_tx_creator, pf_ws_creator "
                "FROM token_analysis WHERE mint=? LIMIT 1",
                (mint,)).fetchone()
            if row:
                creator = row["earliest_tx_creator"] or row["pf_ws_creator"]
                if creator:
                    return creator

            row = live.execute(
                "SELECT creator FROM migrated_tokens WHERE mint=? LIMIT 1",
                (mint,)).fetchone()
            if row and row["creator"]:
                return row["creator"]
        finally:
            live.close()
    except Exception as e:
        print(f"[WALKBACK] creator recovery DB error for {mint[:20]}…: {e}", flush=True)

    return None


# ── per-row processing ─────────────────────────────────────────────────────────

def _process_row(ops: sqlite3.Connection, row: sqlite3.Row) -> int:
    """
    Process one walkback row. Returns RPC credits consumed.
    Writes result back to DB. Never raises — errors are caught and stored.
    """
    mint      = row["mint"]
    creator   = row["creator"]
    subprov   = row["subprov"]
    wclass    = row["walkback_class"]
    attempts  = row["attempts"]  # already incremented by _mark_running

    rpc = [0]  # mutable counter

    try:
        if wclass == "PARTIAL_TREASURY":
            # subprov known, treasury missing — 1-hop from subprov
            if not subprov:
                _mark_failed(ops, mint, "PARTIAL_TREASURY but subprov is NULL", 0)
                return 0
            hop1, sig, slot, bt, amt, mech = _find_funder_via_rpc(subprov, rpc, ops)
            _store_funder(ops, mint, hop1, sig, slot, bt, amt, mech)  # preserve evidence
            if hop1 and _is_known_treasury(ops, hop1):
                _mark_complete(ops, mint, "WATCHTOWER_CONFIRMED", subprov, hop1, rpc[0],
                               confirmed_subprov=True)
            elif hop1:
                _mark_complete(ops, mint, "LINEAGE_GAP", subprov, hop1, rpc[0],
                               confirmed_subprov=True)
            else:
                _mark_complete(ops, mint, "LINEAGE_GAP", subprov, None, rpc[0],
                               confirmed_subprov=True)

        elif wclass == "PARTIAL_SUBPROV":
            # creator known, subprov missing — 1-hop from creator
            if not creator:
                _mark_failed(ops, mint, "PARTIAL_SUBPROV but creator is NULL", 0)
                return 0
            hop1, sig, slot, bt, amt, mech = _find_funder_via_rpc(creator, rpc, ops)
            _store_funder(ops, mint, hop1, sig, slot, bt, amt, mech)
            if hop1 and _is_known_subprov(ops, hop1):
                t_row = ops.execute(
                    "SELECT treasury FROM wt_discovered_subprovs WHERE subprov=? LIMIT 1",
                    (hop1,)).fetchone()
                treasury = t_row["treasury"] if t_row else None
                outcome = "WATCHTOWER_CONFIRMED" if treasury else "LINEAGE_GAP"
                _mark_complete(ops, mint, outcome, hop1, treasury, rpc[0],
                               confirmed_subprov=True)
            elif hop1:
                # hop1 unknown — surface as candidate, NOT confirmed attribution
                _mark_complete(ops, mint, "LINEAGE_GAP", hop1, None, rpc[0],
                               confirmed_subprov=False)
            else:
                _mark_complete(ops, mint, "NO_ATTRIBUTION_FOUND", None, None, rpc[0])

        elif wclass == "FULL_WALKBACK":
            # creator unknown or lineage completely missing — 2-hop walk
            if not creator:
                creator = _recover_creator_from_db(ops, mint)
                if creator:
                    # Persist so future runs and attribution reads see the resolved creator.
                    ops.execute(
                        "UPDATE wt_walkback_queue SET creator=? WHERE mint=?",
                        (creator, mint))
                    ops.commit()
                else:
                    _mark_complete(ops, mint, "NO_ATTRIBUTION_FOUND", None, None, 0)
                    return 0

            # Hop 1: who funded creator?
            hop1, sig1, slot1, bt1, amt1, mech1 = _find_funder_via_rpc(creator, rpc, ops)
            if not hop1:
                _mark_complete(ops, mint, "NO_ATTRIBUTION_FOUND", None, None, rpc[0])
                return rpc[0]

            # Always persist hop-1 funder — even if attribution fails
            _store_funder(ops, mint, hop1, sig1, slot1, bt1, amt1, mech1)

            if _is_known_subprov(ops, hop1):
                t_row = ops.execute(
                    "SELECT treasury FROM wt_discovered_subprovs WHERE subprov=? LIMIT 1",
                    (hop1,)).fetchone()
                treasury = t_row["treasury"] if t_row else None
                outcome = "WATCHTOWER_CONFIRMED" if treasury else "LINEAGE_GAP"
                # X21B: the subprov(hop1)->creator edge was freshly observed via RPC
                # (sig1/bt1/amt1/mech1); the treasury(hop2)->subprov edge, if any, comes
                # from an existing DB lookup rather than a fresh funding observation in
                # THIS walk, so only the subprov->creator side is captured here.
                _capture_provisioning_facts(
                    ops, mint=mint, treasury=None, subprov=hop1, creator=creator,
                    subprov_to_creator_sig=sig1, subprov_to_creator_block_time=bt1,
                    subprov_to_creator_amount_sol=amt1, subprov_to_creator_mechanism=mech1,
                )
                _mark_complete(ops, mint, outcome, hop1, treasury, rpc[0],
                               confirmed_subprov=True)
                return rpc[0]

            if _is_known_treasury(ops, hop1):
                _mark_complete(ops, mint, "WATCHTOWER_CONFIRMED", None, hop1, rpc[0])
                return rpc[0]

            # Hop 2: who funded hop1?
            hop2, sig2, slot2, bt2, amt2, mech2 = _find_funder_via_rpc(hop1, rpc, ops)

            # X21B: capture the observed treasury(hop2)->subprov(hop1)->creator relationship
            # as an operation-agnostic fact, REGARDLESS of whether hop2 is a confirmed
            # treasury. This never writes attribution — it is a separate, append-only
            # provisioning-edge record. hop1 is always the subprov role here since it was
            # already established as the wallet that funded the creator; hop2 (if found)
            # is whatever funded hop1, known or not.
            if hop2:
                _capture_provisioning_facts(
                    ops, mint=mint, treasury=hop2, subprov=hop1, creator=creator,
                    treasury_to_subprov_sig=sig2, treasury_to_subprov_block_time=bt2,
                    treasury_to_subprov_amount_sol=amt2, treasury_to_subprov_mechanism=mech2,
                    subprov_to_creator_sig=sig1, subprov_to_creator_block_time=bt1,
                    subprov_to_creator_amount_sol=amt1, subprov_to_creator_mechanism=mech1,
                )

            if hop2 and _is_known_treasury(ops, hop2):
                # hop1 is now confirmed as subprov (its funder is a known treasury)
                _mark_complete(ops, mint, "WATCHTOWER_CONFIRMED", hop1, hop2, rpc[0],
                               confirmed_subprov=True)
            elif hop2 and _is_known_subprov(ops, hop2):
                t_row = ops.execute(
                    "SELECT treasury FROM wt_discovered_subprovs WHERE subprov=? LIMIT 1",
                    (hop2,)).fetchone()
                treasury = t_row["treasury"] if t_row else None
                outcome = "WATCHTOWER_CONFIRMED" if treasury else "LINEAGE_GAP"
                # hop2 is confirmed subprov; hop1 is its downstream (unconfirmed at this level)
                _mark_complete(ops, mint, outcome, hop1, treasury, rpc[0],
                               confirmed_subprov=False)
            elif hop2:
                # Unknown hop-2 — surface as treasury review lead, mark LINEAGE_GAP
                # hop1 is unconfirmed — do NOT write it as confirmed attribution
                disp = _surface_treasury_review_lead(ops, hop2, hop1, creator, mint, sig2, amt2, mech2)
                _mark_complete(ops, mint, "LINEAGE_GAP", hop1, None, rpc[0],
                               confirmed_subprov=False)
                print(f"[WALKBACK] hop2 lead {disp}: {hop2[:14]}…", flush=True)
            else:
                # hop1 unknown, hop2 not found — no confirmation possible
                _mark_complete(ops, mint, "NO_ATTRIBUTION_FOUND", None, None, rpc[0])

        else:
            _mark_failed(ops, mint, f"unexpected class {wclass} in worker", 0)

    except Exception as e:
        err = str(e)
        print(f"[WALKBACK] error processing {mint[:16]}…: {err}", flush=True)
        if attempts >= MAX_ATTEMPTS:
            _mark_exhausted(ops, mint, rpc[0])
        else:
            # Reset to pending so it will be retried next batch
            ops.execute(
                "UPDATE wt_walkback_queue SET status='pending', last_error=?, "
                "rpc_used=rpc_used+?, updated_at=? WHERE mint=?",
                (err[:500], rpc[0], int(time.time()), mint))
            ops.commit()

    return rpc[0]


# ── post-batch breadth check ──────────────────────────────────────────────────

def promote_recurring_funders(ops: sqlite3.Connection) -> int:
    """
    After a batch completes, scan all NO_ATTRIBUTION_FOUND rows that have a funder_wallet.
    Any funder_wallet that funded >=2 distinct creators is a recurring infrastructure wallet —
    promote it as a discovery lead into wt_discovered_subprovs (treasury=NULL).

    Safety:
    - Skip if funder is already a confirmed treasury (never demote)
    - Merge if already in wt_discovered_subprovs (update counts, never overwrite treasury)
    - Idempotent: ON CONFLICT updates creator_count + last_seen only
    Returns count of wallets promoted or updated.
    """
    # Group completed NO_ATTRIBUTION_FOUND rows by funder_wallet
    candidates = ops.execute("""
        SELECT funder_wallet,
               COUNT(DISTINCT creator)  AS creator_count,
               MIN(funder_block_time)   AS first_seen,
               MAX(funder_block_time)   AS last_seen,
               GROUP_CONCAT(DISTINCT funding_mechanism) AS mechanisms
        FROM wt_walkback_queue
        WHERE intelligence_outcome = 'NO_ATTRIBUTION_FOUND'
          AND funder_wallet IS NOT NULL
        GROUP BY funder_wallet
        HAVING COUNT(DISTINCT creator) >= 2
    """).fetchall()

    promoted = 0
    now = int(time.time())
    for row in candidates:
        fw        = row["funder_wallet"]
        n         = row["creator_count"]
        first     = row["first_seen"] or now
        last      = row["last_seen"]  or now
        mechs     = (row["mechanisms"] or "").split(",")
        mechanism = "MIXED" if len(set(m for m in mechs if m)) > 1 else (mechs[0] or "UNKNOWN")

        # Skip static blocklist first (free)
        if fw in _FUNDER_BLOCKLIST:
            continue
        # Skip if already a confirmed treasury
        if _is_known_treasury(ops, fw):
            continue
        # Skip program-owned accounts (ATAs, PDAs, pools) — 1cr per NEW candidate only
        if not _is_known_subprov(ops, fw):
            rpc_dummy = [0]
            if _is_program_owned(fw, rpc_dummy):
                print(f"[WALKBACK] recurring funder {fw[:14]}… is program-owned — skipped",
                      flush=True)
                continue

        if _is_known_subprov(ops, fw):
            # Already tracked — update creator_count and last_seen, don't touch treasury
            ops.execute("""
                UPDATE wt_discovered_subprovs
                SET creator_count    = MAX(creator_count, ?),
                    last_seen        = MAX(last_seen, ?),
                    funding_mechanism= COALESCE(funding_mechanism, ?),
                    discovery_source = COALESCE(discovery_source, 'WALKBACK_RECURRING_FUNDER')
                WHERE subprov = ?
            """, (n, last, mechanism, fw))
        else:
            # New lead — insert with treasury=NULL
            ops.execute("""
                INSERT OR IGNORE INTO wt_discovered_subprovs
                    (subprov, creator_count, treasury, treasury_known,
                     first_seen, last_seen, confidence, state,
                     discovery_source, funding_mechanism)
                VALUES (?, ?, NULL, 0, ?, ?, 0.4,
                        'PROVISION_CANDIDATE',
                        'WALKBACK_RECURRING_FUNDER', ?)
            """, (fw, n, first, last, mechanism))
            print(f"[WALKBACK] promoted recurring funder {fw[:14]}… "
                  f"(creators={n}, mechanism={mechanism})", flush=True)

        promoted += 1

    if promoted:
        ops.commit()
    return promoted


# ── batch drain ────────────────────────────────────────────────────────────────

def drain_batch(ops: sqlite3.Connection, batch_size: int = BATCH_SIZE,
                rpc_budget: int = RPC_BUDGET_BATCH) -> dict:
    """
    Claim up to batch_size pending rows (attempts < MAX_ATTEMPTS) and process them.
    Returns summary: {processed, rpc_used, outcomes}.
    """
    rows = ops.execute(
        "SELECT mint, creator, subprov, treasury, walkback_class, attempts "
        "FROM wt_walkback_queue "
        "WHERE status='pending' AND attempts < ? "
        "ORDER BY enqueued_at ASC "
        "LIMIT ?",
        (MAX_ATTEMPTS, batch_size)).fetchall()

    if not rows:
        return {"processed": 0, "rpc_used": 0, "outcomes": {}}

    rpc_used_total  = 0
    outcomes: dict[str, int] = {}
    processed_count = 0
    skipped_claimed = 0  # concurrency: row already claimed by another worker

    for row in rows:
        if rpc_used_total >= rpc_budget:
            print(f"[WALKBACK] RPC budget {rpc_budget} exhausted after {rpc_used_total} credits, "
                  f"deferring {len(rows) - processed_count} remaining rows", flush=True)
            break

        mint = row["mint"]
        if not _mark_running(ops, mint):
            skipped_claimed += 1
            continue  # another worker already claimed this row

        rpc = _process_row(ops, row)
        rpc_used_total += rpc

        # Read back outcome for summary
        result = ops.execute(
            "SELECT intelligence_outcome, status FROM wt_walkback_queue WHERE mint=?",
            (mint,)).fetchone()
        outcome = (result["intelligence_outcome"] or result["status"] or "UNKNOWN") if result else "UNKNOWN"
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        processed_count += 1

    # Instrumentation: treasury review leads inserted this batch
    try:
        hop2_leads = ops.execute(
            "SELECT COUNT(*) FROM wt_treasury_review "
            "WHERE detected_via='walkback_hop2' AND status='PENDING_REVIEW'").fetchone()[0]
    except Exception:
        hop2_leads = -1

    print(f"[WALKBACK] batch done: processed={processed_count} skipped_claimed={skipped_claimed} "
          f"rpc={rpc_used_total} outcomes={outcomes} hop2_leads_total={hop2_leads}", flush=True)

    # Post-batch: promote any recurring unknown funders as discovery leads
    promoted = promote_recurring_funders(ops)
    if promoted:
        print(f"[WALKBACK] post-batch: promoted {promoted} recurring funder(s) to subprov leads",
              flush=True)

    return {"processed": processed_count, "rpc_used": rpc_used_total,
            "outcomes": outcomes, "leads_promoted": promoted,
            "hop2_leads_total": hop2_leads}


# ── heartbeat ──────────────────────────────────────────────────────────────────

def _write_heartbeat(ops: sqlite3.Connection) -> None:
    now = int(time.time())
    from src.ops.walkback_health import build_walkback_health
    health = build_walkback_health(ops, now=now, heartbeat_override=now)
    ops.execute(
        "INSERT INTO wt_worker_heartbeat (worker_name,last_seen,status,meta_json) VALUES (?,?,?,?) "
        "ON CONFLICT(worker_name) DO UPDATE SET last_seen=excluded.last_seen,"
        "status=excluded.status,meta_json=excluded.meta_json",
        ("walkback_worker", now, health["status"], json.dumps(health, sort_keys=True)))
    ops.commit()


# ── main loop ──────────────────────────────────────────────────────────────────

def _is_lock_error(exc: Exception) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower()


def run_loop() -> None:
    from src.core.walkback_queue import ensure_schema as _ensure_walkback_schema
    from src.core import treasury_bank
    from src.ops.walkback_health import recover_stalled_running_jobs
    from src.ops.walkback_cycle_trace import trace_boundary, trace_failure
    startup = _ops_conn()
    try:
        # Schema initialization must never be silently skipped — a schema failure
        # here means every downstream operation is unsafe, so these re-raise as before.
        _ensure_walkback_schema(startup)
        treasury_bank.initialize_schema(startup)
        from src.ops.attribution_outcome import ensure_schema as _ensure_outcome_schema
        _ensure_outcome_schema(startup)

        # Startup MAINTENANCE (recover-stalled / finalize-exhausted) is non-essential:
        # skipping it for one boot only delays cleanup of crash-stranded rows, which
        # the next successful boot (or a later scheduled pass) will still catch. A
        # transient lock here must not crash the whole process before it ever reaches
        # the main loop. Only a lock-contention OperationalError is swallowed; any
        # other exception still fails loudly, since that would indicate a real defect
        # rather than transient contention.
        recovered = {"requeued": 0, "failed": 0}
        trace_boundary("maintenance_write_attempted", extra={"task": "recover_stalled_running_jobs"})
        _t0 = time.monotonic()
        try:
            recovered = recover_stalled_running_jobs(
                startup, max_attempts=MAX_ATTEMPTS,
                stalled_after_seconds=max(INTERVAL_SEC * 3, 180),
            )
        except Exception as e:
            trace_failure("maintenance_write_attempted:recover_stalled_running_jobs", e, elapsed_s=time.monotonic() - _t0)
            if not _is_lock_error(e):
                raise
            print(f"[WALKBACK] startup maintenance skipped (recover_stalled_running_jobs): {e}", flush=True)

        exhausted = 0
        trace_boundary("maintenance_write_attempted", extra={"task": "finalize_exhausted_pending"})
        _t0 = time.monotonic()
        try:
            exhausted = finalize_exhausted_pending(startup)
        except Exception as e:
            trace_failure("maintenance_write_attempted:finalize_exhausted_pending", e, elapsed_s=time.monotonic() - _t0)
            if not _is_lock_error(e):
                raise
            print(f"[WALKBACK] startup maintenance skipped (finalize_exhausted_pending): {e}", flush=True)
    finally:
        startup.close()
    print(f"[WALKBACK] worker starting: batch={BATCH_SIZE} interval={INTERVAL_SEC}s "
          f"max_attempts={MAX_ATTEMPTS} rpc_budget={RPC_BUDGET_BATCH} "
          f"recovered={recovered} exhausted={exhausted}", flush=True)
    while True:
        trace_boundary("cycle_started")
        try:
            ops = _ops_conn()
            try:
                trace_boundary("heartbeat_write_attempted")
                _t0 = time.monotonic()
                try:
                    _write_heartbeat(ops)
                    trace_boundary("heartbeat_write_completed", extra={"elapsed_s": round(time.monotonic() - _t0, 3)})
                except Exception as e:
                    trace_failure("heartbeat_write_attempted", e, elapsed_s=time.monotonic() - _t0)
                    raise

                trace_boundary("queue_inspection_started")
                pending = ops.execute(
                    "SELECT COUNT(*) FROM wt_walkback_queue "
                    "WHERE status='pending' AND attempts < ?",
                    (MAX_ATTEMPTS,)).fetchone()[0]
                if pending > 0:
                    trace_boundary("queue_claim_attempted", extra={"pending": pending})
                    _t0 = time.monotonic()
                    try:
                        drain_batch(ops)
                        trace_boundary("queue_claim_completed", extra={"elapsed_s": round(time.monotonic() - _t0, 3)})
                    except Exception as e:
                        trace_failure("queue_claim_attempted", e, elapsed_s=time.monotonic() - _t0)
                        raise
                    _write_heartbeat(ops)
                else:
                    print(f"[WALKBACK] queue empty (pending=0), sleeping {INTERVAL_SEC}s", flush=True)
            finally:
                ops.close()
            trace_boundary("cycle_completed")
        except Exception as e:
            trace_failure("cycle_completed", e)
            print(f"[WALKBACK] outer loop error: {e}", flush=True)
        time.sleep(INTERVAL_SEC)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true", help="run continuously")
    parser.add_argument("--once", action="store_true", help="drain one batch and exit")
    args = parser.parse_args()

    if args.loop:
        run_loop()
    elif args.once:
        from src.core.walkback_queue import ensure_schema as _ensure_walkback_schema
        from src.core import treasury_bank
        from src.ops.walkback_health import recover_stalled_running_jobs
        ops = _ops_conn()
        _ensure_walkback_schema(ops)
        treasury_bank.initialize_schema(ops)
        from src.ops.attribution_outcome import ensure_schema as _ensure_outcome_schema
        _ensure_outcome_schema(ops)
        recover_stalled_running_jobs(ops, max_attempts=MAX_ATTEMPTS)
        finalize_exhausted_pending(ops)
        result = drain_batch(ops)
        ops.close()
        print(f"done: {result}")
    else:
        parser.print_help()
