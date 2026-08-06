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

RPC cost per row is bounded by SIG_PAGE_COUNT signature pages and
TX_FETCH_LIMIT transaction reads per hop. FULL_WALKBACK may also re-read the
selected creator-funding transaction once to retain close-destination proof.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
import sqlite3
import argparse
import contextvars
from typing import Any, Optional
from src.utils.db_locking import db_connect
from src.core import deep_walkback

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
SIG_PAGE_LIMIT  = int(os.environ.get("WALKBACK_SIG_PAGE_LIMIT",  "100"))
SIG_PAGE_COUNT  = int(os.environ.get("WALKBACK_SIG_PAGE_COUNT",  "3"))
RPC_TIMEOUT     = int(os.environ.get("WALKBACK_RPC_TIMEOUT_S",   "8"))
# X76.5 -- self-kill guard for a stuck same-thread write lease. Observed live:
# this worker's thread-local write lease (see database_write_service.py's
# _thread_write_lease) can be left held across cycles by a mechanism not yet
# fully isolated (every write path audited has a guaranteed-release finally,
# per the incident already documented in db_locking.py's own
# _release_write_lane_inner comment -- yet the exact symptom recurred: the
# SAME transaction_id held, held_seconds growing every cycle, 0% CPU, only a
# process restart clears it). Rather than leave candidate generation silently
# stalled for hours again (the actual root cause this milestone exists to
# fix), the worker now recognises its OWN stuck lease and exits for
# Supervisor to restart it -- the same self-kill pattern already proven in
# creator_funding_worker.py for the equivalent connection-leak class of bug.
MAX_LEASE_STUCK_SECONDS = int(os.environ.get("WALKBACK_MAX_LEASE_STUCK_SECONDS", "600"))


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


def _get_sigs(wallet: str, limit: int = SIG_LIMIT,
              before: Optional[str] = None) -> list:
    """getSignaturesForAddress — 1cr."""
    options = {"limit": limit, "commitment": "confirmed"}
    if before:
        options["before"] = before
    result = _rpc("getSignaturesForAddress", [wallet, options])
    return result or []


def _collect_signature_window(wallet: str, rpc_counter: list, *,
                              before_signature: Optional[str] = None) -> list:
    """Return a bounded, transaction-anchored wallet history window.

    High-throughput provisioning wallets can execute hundreds of transactions
    between their capital funding and a creator launch. A single newest-page
    query therefore misses the wallet's upstream funding edge. Pagination is
    bounded and starts before the downstream funding/create transaction, so
    later activity can never replace the historical lineage being audited.
    """
    entries: list[dict] = []
    cursor = before_signature
    for _ in range(SIG_PAGE_COUNT):
        page = _get_sigs(wallet, SIG_PAGE_LIMIT, before=cursor)
        rpc_counter[0] += 1
        if not page:
            break
        entries.extend(page)
        if len(page) < SIG_PAGE_LIMIT:
            break
        cursor = page[-1].get("signature")
        if not cursor:
            break
    return entries


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


def _close_account_destination(tx: dict) -> Optional[str]:
    """Return the parsed SPL close-account destination, when explicitly present."""
    groups = [(tx.get("transaction", {}).get("message", {}).get("instructions") or [])]
    groups.extend(
        group.get("instructions") or []
        for group in ((tx.get("meta") or {}).get("innerInstructions") or [])
    )
    for instructions in groups:
        for ix in instructions:
            parsed = ix.get("parsed") if isinstance(ix, dict) else None
            if not isinstance(parsed, dict) or parsed.get("type") != "closeAccount":
                continue
            destination = (parsed.get("info") or {}).get("destination")
            if destination:
                return destination
    return None


def _detect_mechanism(tx: dict, sender: str, receiver: str) -> str:
    """
    Determine funding mechanism from tx. Returns WSOL_WRAP_CLOSE, PLAIN_XFER, or UNKNOWN.
    WSOL_WRAP_CLOSE: tx contains a parsed SPL closeAccount instruction.
    PLAIN_XFER: pure system transfer, no token program instructions.
    """
    try:
        if _close_account_destination(tx):
            return "WSOL_WRAP_CLOSE"
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
_EVIDENCE_CONTEXT = contextvars.ContextVar("walkback_evidence_context", default=(None, 0))

# X64 — mechanisms _find_funder_via_rpc/_detect_mechanism already return that
# constitute an observed EPHEMERAL_WSOL_CREATOR_HANDOFF primitive (X62). Only
# the two variants _detect_mechanism can actually produce are listed — no new
# mechanism values are invented here.
_DISPOSABLE_HANDOFF_MECHANISMS: frozenset[str] = frozenset({
    "WSOL_WRAP_CLOSE", "SEEDED_ACCOUNT_CLOSE",
})


def _is_disposable_subprov_handoff(mechanism: Optional[str]) -> bool:
    """True when hop1's own funding mechanism is itself the WATCHTOWER
    handoff primitive (X62), independent of whether hop1's identity was
    already known. Ordinary PLAIN_XFER/UNKNOWN funding never qualifies."""
    return mechanism in _DISPOSABLE_HANDOFF_MECHANISMS


def _find_with_evidence(wallet: str, rpc_counter: list,
                        ops: Optional[sqlite3.Connection], *, source_mint: str,
                        hop_depth: int, **search_options) -> FunderInfo:
    token = _EVIDENCE_CONTEXT.set((source_mint, hop_depth))
    try:
        return _find_funder_via_rpc(wallet, rpc_counter, ops, **search_options)
    finally:
        _EVIDENCE_CONTEXT.reset(token)


def _find_funder_via_rpc(wallet: str, rpc_counter: list,
                         ops: Optional[sqlite3.Connection] = None, *,
                         before_signature: Optional[str] = None,
                         prefer_oldest: bool = False,
                         source_mint: Optional[str] = None,
                         hop_depth: int = 0) -> FunderInfo:
    """
    Collect all valid funders within the bounded tx window then select the strongest.

    Priority (lower = better):
      1  confirmed treasury   — beats all mechanism evidence
      2  known subprov        — structural WATCHTOWER evidence
      4  WSOL_WRAP_CLOSE      — mechanism evidence
      5  SEEDED_ACCOUNT_CLOSE — mechanism evidence
      6  PLAIN_XFER / UNKNOWN — weakest

    Tie-break: closest pre-CREATE transaction for creator funding; oldest
    transaction in the bounded window for upstream capital funding.

    Logs: selected_funder, reason, candidates_seen — for false-positive diagnosis.
    RPC budget remains bounded by SIG_PAGE_COUNT signature pages and
    TX_FETCH_LIMIT transaction reads. getAccountInfo calls increase only when
    multiple candidates exist in the window.
    """
    context_mint, context_depth = _EVIDENCE_CONTEXT.get()
    source_mint = source_mint or context_mint
    hop_depth = hop_depth or context_depth
    sigs = _collect_signature_window(
        wallet, rpc_counter, before_signature=before_signature)
    _empty: FunderInfo = (None, None, None, None, None, None)
    if not sigs:
        return _empty

    # Creator funding is normally the closest qualifying transaction before
    # CREATE. For an active provisioning wallet, its capital source is usually
    # at the oldest edge of the bounded pre-funding window.
    sigs.sort(key=lambda entry: (entry.get("slot") or 0), reverse=not prefer_oldest)

    candidates: list[tuple[int, FunderInfo]] = []  # (priority, FunderInfo)
    candidate_txs: dict[str, dict] = {}

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
        # X29.4 — Infrastructure Spam exclusion: a confirmed spam sender is
        # excluded as funding evidence entirely (never extends lineage,
        # never becomes a candidate funder). The recipient is annotated as
        # spam_recipient only -- receiving unsolicited SOL is environmental
        # noise, not evidence, and must never influence attribution.
        if ops and _is_known_spam_sender(ops, sender):
            from src.ops.wallet_quality import record_spam_transfer
            record_spam_transfer(ops, sender, wallet)
            print(f"[WALKBACK] IGNORED_SPAM_SENDER sender={sender[:14]}… wallet={wallet[:14]}…", flush=True)
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
        candidate_txs[sig] = tx

    if not candidates:
        return _empty

    # Select best: lowest priority wins; ties broken by oldest slot (earliest funding edge)
    candidates.sort(key=lambda x: (
        x[0],
        (x[1][2] or 0) if prefer_oldest else -(x[1][2] or 0),
    ))
    best_priority, best = candidates[0]
    if ops is not None and source_mint:
        deep_walkback.ensure_schema(ops)
        for _priority, candidate in candidates:
            parent, sig, _slot, block_time, amount, mechanism = candidate
            deep_walkback.persist_edge_candidate(
                ops, mint=source_mint, wallet=wallet, parent=parent or "",
                signature=sig or "", block_time=block_time,
                amount_lamports=round(amount * 1e9) if amount is not None else None,
                mechanism=mechanism or "UNKNOWN", anchor_signature=before_signature,
                anchor_block_time=None, hop_depth=hop_depth,
                selection_status="SELECTED" if candidate == best else "ALTERNATIVE",
                rejection_reason="" if candidate == best else "LOWER_RANKED_BUT_RETAINED")
            tx = candidate_txs.get(sig or "")
            if tx:
                flows = deep_walkback.materialize_atomic_wsol(tx, sig or "")
                deep_walkback.persist_atomic_flows(ops, source_mint, flows)
        ops.commit()
    reason = _PRIORITY_REASON.get(best_priority, "PLAIN_XFER")
    print(f"[WALKBACK] selected_funder={best[0][:14]}… reason={reason} "
          f"candidates_seen={len(candidates)} wallet={wallet[:14]}…", flush=True)
    return best


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _ops_conn() -> sqlite3.Connection:
    c = db_connect(OPS_DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def _is_known_spam_sender(ops: sqlite3.Connection, wallet: str) -> bool:
    """X29.4 — true only for wallets manually confirmed in
    wt_known_spam_wallets. Never infers spam from behaviour observed
    during the walk itself."""
    from src.ops.known_spam_wallets import is_known_spam_wallet
    return is_known_spam_wallet(ops, wallet)


def _is_known_treasury(ops: sqlite3.Connection, wallet: str) -> bool:
    row = ops.execute(
        "SELECT 1 FROM wt_confirmed_treasuries WHERE treasury=? LIMIT 1", (wallet,)).fetchone()
    return bool(row)


def _is_known_subprov(ops: sqlite3.Connection, wallet: str) -> bool:
    # X26.3: a REJECTED_INFRASTRUCTURE (or REJECTED_NON_PROVISIONING) row still
    # exists in wt_discovered_subprovs so its raw evidence is preserved, but it
    # must never be treated as a genuine sub-provisioner by the walk — the
    # walk gates WATCHTOWER_CONFIRMED on this function returning True, so a
    # rejected row must count as "not known" here, not "known and confirmed."
    row = ops.execute(
        "SELECT 1 FROM wt_discovered_subprovs WHERE subprov=? "
        "AND COALESCE(state,'') NOT LIKE 'REJECTED%' LIMIT 1", (wallet,)).fetchone()
    return bool(row)


def _is_known_infrastructure(wallet: str) -> bool:
    """X26.3 canonical infrastructure exclusion — never treat a wallet already
    catalogued as known automation/relay/bridge/CEX infrastructure as a
    sub-provisioner, regardless of how many creators it happens to have
    funded. Zero RPC; reuses the same static registry
    src/ops/attribution_outcome.py's _boundary() already trusts for
    infrastructure-boundary attribution, so a wallet is never simultaneously
    "known infrastructure" for attribution purposes and "a sub-provisioner"
    for Discovery purposes."""
    from src.utils.infra_mapping import is_known_account
    return bool(wallet) and is_known_account(wallet)


def _mark_running(ops: sqlite3.Connection, mint: str) -> bool:
    """Claim with a renewable lease; expired work is safely reclaimable."""
    worker_id = os.environ.get("WALKBACK_WORKER_ID", f"walkback-{os.getpid()}")
    lease_seconds = int(os.environ.get("WALKBACK_LEASE_SECONDS", "300"))
    claimed = deep_walkback.claim_with_lease(ops, mint, worker_id, lease_seconds)
    if claimed:
        from src.ops.watchtower_candidates import sync_walkback_result
        sync_walkback_result(ops, mint)
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


def _store_close_destination_evidence(
    ops: sqlite3.Connection, *, creator: str, subprov: str, tx: Optional[dict],
    signature: Optional[str], block_time: Optional[int], amount_sol: Optional[float],
) -> bool:
    """Persist transaction-derived close-recipient evidence without attribution.

    The WALKBACK_EVIDENCE state is intentionally review-only. Existing live
    detector rows are never overwritten, and this function does not touch any
    treasury, Operator, registry, or attribution table.
    """
    if not tx or _close_account_destination(tx) != creator:
        return False
    try:
        ops.execute(
            """
            INSERT OR IGNORE INTO wt_wrap_close_candidates
                (creator, funding_mechanism, creator_extraction_method,
                 subprov_wallet, close_destination, base_amount_sol,
                 tx_signature, funded_at, confidence, state, detected_at)
            VALUES (?, 'WSOL_WRAP_CLOSE', 'CLOSE_ACCOUNT_DESTINATION',
                    ?, ?, ?, ?, ?, 'STRICT', 'WALKBACK_EVIDENCE', ?)
            """,
            (creator, subprov, creator, amount_sol, signature, block_time,
             int(time.time())),
        )
        return True
    except sqlite3.Error as exc:
        print(f"[WALKBACK] close-destination evidence capture failed: {exc}", flush=True)
        return False


def _promote_if_canonical_watchtower(ops: sqlite3.Connection, mint: str,
                                      materialized: Optional[dict]) -> None:
    """X65.44 -- called after EVERY materialize_outcome() commit (from
    _mark_complete/_mark_failed/_mark_exhausted alike) so no walkback
    terminal path can silently skip registry promotion. The predicate
    check inside promote_walkback_confirmed_watchtower() naturally no-ops
    for the vast majority of calls (only CANONICAL_OPERATOR_REACHED/
    WATCHTOWER outcomes are eligible) -- this is a single, safe, reusable
    hook point rather than three separately-maintained call sites.
    A promotion failure is logged but never raised/re-raised -- it must
    never affect the walkback commit that already succeeded. This wraps
    the call itself in try/except as defense-in-depth: promote_walkback_
    confirmed_watchtower() already catches its own internal errors, but a
    failure importing/calling it at all (rather than inside it) must be
    equally unable to break the caller's terminal-state transition."""
    if not materialized:
        return

    # X67.21 -- shared canonical predicate integration (mode-aware; see
    # src/ops/watchtower_canonical_integration.py). The walkback commit
    # that triggered this call has ALREADY succeeded (see _mark_complete/
    # _mark_failed/_mark_exhausted -- ops.commit() runs before this
    # function is ever called) -- nothing below can roll it back. This
    # entire block is best-effort: any failure here is caught and logged,
    # never re-raised, exactly matching this function's pre-existing
    # contract for the legacy promotion call itself.
    from src.core.watchtower_registry_promotion import is_canonical_watchtower_outcome

    _legacy_eligible = is_canonical_watchtower_outcome(
        materialized.get("outcome_type"), materialized.get("operator_id"),
    )
    _legacy_decision = "ACCEPTED" if _legacy_eligible else "REJECTED"

    _integration_result = None
    try:
        from src.ops.watchtower_canonical_integration import (
            evaluate_canonical_decision, get_canonical_predicate_mode,
            record_comparison_telemetry,
        )
        from src.ops.watchtower_canonical_adapters import build_evidence_for_walkback_queue

        _mode = get_canonical_predicate_mode()
        _integration_result = evaluate_canonical_decision(
            path="path_b_walkback", mint=mint,
            build_evidence=lambda: build_evidence_for_walkback_queue(ops, mint=mint),
            legacy_decision=_legacy_decision,
            legacy_reason=materialized.get("outcome_type"),
            mode=_mode,
        )
        record_comparison_telemetry(ops, _integration_result, mint=mint)
    except Exception as exc:  # noqa: BLE001 -- must never break the caller's terminal transition
        print(f"[X67.21] canonical predicate integration failed mint={mint}: {exc}", flush=True)

    # Determine whether THIS call may invoke the existing promotion helper.
    # LEGACY/SHADOW: unconditionally attempt promotion exactly as before
    # X67.21 -- the helper's own is_canonical_watchtower_outcome() gate is
    # what actually decides eligibility in these two modes, unchanged.
    # ENFORCE: only call the helper when the integration layer's
    # authoritative decision is ACCEPTED -- REVIEW_REQUIRED/REJECTED must
    # not promote, and there is no silent fallback to legacy acceptance.
    _should_attempt_promotion = True
    if _integration_result is not None and _integration_result.mode == "enforce":
        _should_attempt_promotion = (_integration_result.authoritative_decision == "ACCEPTED")
        if not _should_attempt_promotion:
            print(
                f"[X67.21] ENFORCE mode: mint={mint} authoritative_decision="
                f"{_integration_result.authoritative_decision} reason="
                f"{_integration_result.authoritative_reason} -- promotion withheld",
                flush=True,
            )

    if not _should_attempt_promotion:
        return

    try:
        from src.core.watchtower_registry_promotion import promote_walkback_confirmed_watchtower
        result = promote_walkback_confirmed_watchtower(
            ops, mint,
            outcome_type=materialized.get("outcome_type"),
            operator_id=materialized.get("operator_id"),
            evidence=materialized.get("evidence"),
            completed_at=materialized.get("completed_at"),
        )
        if result["action"] == "failed":
            print(f"[WALKBACK] registry promotion FAILED mint={mint}: {result['error']}", flush=True)
        elif result["action"] == "promoted":
            print(f"[WALKBACK] registry promotion → {mint[:14]}… promoted (WALKBACK_RECOVERED)", flush=True)
    except Exception as exc:  # noqa: BLE001 -- must never break the caller's terminal transition
        print(f"[WALKBACK] registry promotion call failed mint={mint}: {exc}", flush=True)


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
        if not _is_known_subprov(ops, subprov) and not _is_known_infrastructure(subprov):
            _ensure_subprov_lead(ops, subprov, creator, first_seen)
            print(f"[WALKBACK] LINEAGE_GAP → promoted {subprov[:14]}… to subprov discovery lead",
                  flush=True)
        elif _is_known_infrastructure(subprov):
            print(f"[WALKBACK] LINEAGE_GAP at {subprov[:14]}… — known infrastructure wallet, "
                  f"terminating candidate inference here (not promoted to subprov lead)",
                  flush=True)
        # Surface the wallet that funded this unconfirmed hop-1 as a treasury review lead
        if funder_w and funder_w != subprov and not _is_known_treasury(ops, funder_w) and not _is_known_subprov(ops, funder_w):
            disp = _surface_treasury_review_lead(ops, funder_w, subprov, creator, mint,
                                                 funder_sig, funder_amt, None)
            print(f"[WALKBACK] LINEAGE_GAP funder lead {disp}: {funder_w[:14]}…", flush=True)
    from src.ops.attribution_outcome import materialize_outcome
    materialized = materialize_outcome(ops, mint)
    from src.ops.watchtower_candidates import sync_walkback_result
    sync_walkback_result(ops, mint)
    ops.commit()
    _promote_if_canonical_watchtower(ops, mint, materialized)


def _mark_failed(ops: sqlite3.Connection, mint: str, error: str, rpc_used: int) -> None:
    now = int(time.time())
    ops.execute(
        "UPDATE wt_walkback_queue "
        "SET status='failed', last_error=?, rpc_used=rpc_used+?, completed_at=?, updated_at=? "
        "WHERE mint=?",
        (error[:500], rpc_used, now, now, mint))
    from src.ops.attribution_outcome import materialize_outcome
    materialized = materialize_outcome(ops, mint)
    from src.ops.watchtower_candidates import sync_walkback_result
    sync_walkback_result(ops, mint)
    ops.commit()
    _promote_if_canonical_watchtower(ops, mint, materialized)


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
    materialized = materialize_outcome(ops, mint)
    from src.ops.watchtower_candidates import sync_walkback_result
    sync_walkback_result(ops, mint)
    ops.commit()
    _promote_if_canonical_watchtower(ops, mint, materialized)


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


# X65.21 — additive Provisioning Wallet capture (X65.19 proof: SubProv funds a
# distinct Wallet P, never the creator directly). Uses ONLY the transaction the
# walkback hop already fetched to detect mechanism/find the funder -- no new
# RPC call, no redesign of the detector. Best-effort: a failure here never
# breaks walkback completion, matching _capture_provisioning_facts' own
# discipline.
def _capture_provisioning_wallet(ops: sqlite3.Connection, *, mint: str, subprov: str,
                                  creator: str, mechanism: Optional[str], tx: Optional[dict]) -> None:
    if not tx or mechanism not in ("WSOL_WRAP_CLOSE", "SEEDED_ACCOUNT_CLOSE"):
        return
    try:
        from src.ops.provisioning_wallet import record_provisioning_wallet
        instrs = tx.get("transaction", {}).get("message", {}).get("instructions", [])
        wallet_p = None
        close_owner = None
        close_destination = None
        for ix in instrs:
            info = ix.get("parsed", {}).get("info", {}) if isinstance(ix.get("parsed"), dict) else {}
            ptype = ix.get("parsed", {}).get("type") if isinstance(ix.get("parsed"), dict) else None
            if ptype == "transfer" and info.get("source") == subprov:
                wallet_p = info.get("destination")
            if ptype == "createAccountWithSeed" and info.get("source") == subprov:
                pass  # resolved via close_owner below (the seeded account's `base`)
            if ptype == "closeAccount":
                close_owner = info.get("owner")
                close_destination = info.get("destination")
        if mechanism == "SEEDED_ACCOUNT_CLOSE" and close_owner:
            wallet_p = close_owner
        if not wallet_p or close_destination != creator:
            return
        record_provisioning_wallet(
            ops, mint=mint, subprov_wallet=subprov, creator_wallet=creator,
            provisioning_wallet=wallet_p, mechanism=mechanism,
            recovery_method="LIVE_CAPTURE", reconstructed=False,
        )
    except Exception as e:
        print(f"[WALKBACK] provisioning-wallet capture failed for {mint[:16]}…: {e}", flush=True)


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


def _recover_create_signature_from_db(mint: str,
                                      ops: Optional[sqlite3.Connection] = None) -> Optional[str]:
    """Recover the CREATE signature used as the temporal walkback anchor."""
    if ops is not None:
        try:
            row = ops.execute(
                "SELECT create_anchor_signature FROM wt_walkback_queue WHERE mint=?",
                (mint,)).fetchone()
            if row and row[0] and deep_walkback.valid_signature(row[0]):
                return row[0]
        except sqlite3.OperationalError:
            pass
    try:
        live = sqlite3.connect(f"file:{LIVE_DB_PATH}?mode=ro", uri=True, timeout=5)
        try:
            row = live.execute(
                "SELECT create_tx_signature FROM creator_funding_queue WHERE mint=? "
                "ORDER BY updated_at DESC LIMIT 1", (mint,)
            ).fetchone()
            if row and row[0]:
                return row[0]
            row = live.execute(
                "SELECT create_tx_signature FROM token_analysis WHERE mint=? LIMIT 1",
                (mint,),
            ).fetchone()
            return row[0] if row and row[0] else None
        finally:
            live.close()
    except Exception as exc:
        print(f"[WALKBACK] create-signature recovery failed for {mint[:16]}…: {exc}", flush=True)
        return None


DEEP_MAX_HOPS = int(os.environ.get("WALKBACK_DEEP_MAX_HOPS", "8"))
DEEP_ROW_RPC_BUDGET = int(os.environ.get("WALKBACK_DEEP_ROW_RPC_BUDGET", "80"))


def _expand_unknown_upstream(ops: sqlite3.Connection, *, mint: str,
                             start_wallet: str, anchor_signature: Optional[str],
                             rpc_counter: list, start_depth: int = 2) -> dict:
    """Continue a causally anchored path without promoting unknown roots."""
    wallet, anchor = start_wallet, anchor_signature
    visited = {wallet}; deepest = wallet
    deep_walkback.set_path_state(ops, mint, "UPSTREAM_EXPANDING",
                                 {"start_wallet": wallet, "start_depth": start_depth})
    for depth in range(start_depth + 1, DEEP_MAX_HOPS + 1):
        if rpc_counter[0] >= DEEP_ROW_RPC_BUDGET:
            return {"state": "RPC_BUDGET_EXHAUSTED", "deepest": deepest,
                    "treasury": None, "hop_depth": depth - 1}
        parent, sig, slot, block_time, amount, mechanism = _find_with_evidence(
            wallet, rpc_counter, ops, before_signature=anchor, prefer_oldest=True,
            source_mint=mint, hop_depth=depth)
        if not parent:
            return {"state": "ARCHIVAL_GAP", "deepest": deepest,
                    "treasury": None, "hop_depth": depth - 1}
        if parent in visited:
            return {"state": "FAILED_TERMINAL", "deepest": deepest,
                    "treasury": None, "hop_depth": depth,
                    "reason": "CYCLE_DETECTED"}
        visited.add(parent); deepest = parent
        if _is_known_treasury(ops, parent):
            return {"state": "KNOWN_TREASURY_REACHED", "deepest": parent,
                    "treasury": parent, "hop_depth": depth}
        if _is_known_infrastructure(parent):
            deep_walkback.materialize_candidate(ops, parent, service_or_exchange=True)
            return {"state": "FAILED_TERMINAL", "deepest": parent,
                    "treasury": None, "hop_depth": depth,
                    "reason": "SERVICE_OR_EXCHANGE"}
        deep_walkback.materialize_candidate(ops, parent)
        wallet, anchor = parent, sig
    candidate = deep_walkback.materialize_candidate(ops, deepest)
    return {"state": "TREASURY_CANDIDATE_SURFACED", "deepest": deepest,
            "treasury": None, "hop_depth": DEEP_MAX_HOPS,
            "candidate_confidence": candidate["confidence"]}


# ── X77.1 collect-then-persist: hop1 evidence split into an RPC-only
# collection step and a pure-write persistence step, so callers can run the
# collection BEFORE any write (and before hop2's own RPC work), then persist
# everything once the RPC-heavy hop resolution is entirely finished. Splits
# the previously-fused fetch+write shape (X65.21) without changing what is
# fetched, when it is fetched relative to sig1/mech1 being known, or what is
# ultimately written -- only the ORDER relative to hop2's RPC call changes.

def _collect_hop1_evidence(mech1: Optional[str], sig1: Optional[str]) -> tuple[bool, Optional[dict]]:
    """RPC-only: fetch hop1's funding transaction if its mechanism warrants
    one. Returns (fetched, tx) -- fetched is True whenever a _get_tx call was
    actually attempted (matching the pre-X77.1 rpc[0] increment, which fired
    on ATTEMPT, not on a successful non-None result), so callers must
    increment their rpc counter on `fetched`, never on `tx is not None`. tx
    itself is None both when no fetch was needed (PLAIN_XFER/UNKNOWN, or
    WSOL_WRAP_CLOSE/SEEDED_ACCOUNT_CLOSE without a signature) and when the
    fetch was attempted but failed -- callers must inspect `fetched` to tell
    the two apart. Never writes."""
    if mech1 == "WSOL_WRAP_CLOSE" and sig1:
        return True, _get_tx(sig1)
    if mech1 == "SEEDED_ACCOUNT_CLOSE" and sig1:
        return True, _get_tx(sig1)
    return False, None


def _persist_hop1_evidence(ops: sqlite3.Connection, *, mint: str, creator: str, subprov: str,
                           mechanism: Optional[str], sig: Optional[str], block_time: Optional[int],
                           amount_sol: Optional[float], tx: Optional[dict]) -> Optional[str]:
    """Pure writes: persist whatever _collect_hop1_evidence already fetched.
    No RPC of its own. Returns hop1_evidence_level (STRICT/MECHANISM_ONLY/
    None), matching exactly what the pre-X77.1 inline code computed at the
    same point in _process_row. A no-op (returns None) when tx is None,
    identical to the pre-X77.1 branches that never fetched anything."""
    if tx is None:
        return None
    if mechanism == "WSOL_WRAP_CLOSE":
        stored_strict = _store_close_destination_evidence(
            ops, creator=creator, subprov=subprov, tx=tx,
            signature=sig, block_time=block_time, amount_sol=amount_sol)
        # X65.21 Phase 5 — reuse this already-fetched transaction to
        # additively persist the proven Provisioning Wallet (X65.19).
        _capture_provisioning_wallet(ops, mint=mint, subprov=subprov,
                                      creator=creator, mechanism=mechanism, tx=tx)
        return "STRICT" if stored_strict else "MECHANISM_ONLY"
    if mechanism == "SEEDED_ACCOUNT_CLOSE":
        _capture_provisioning_wallet(ops, mint=mint, subprov=subprov,
                                      creator=creator, mechanism=mechanism, tx=tx)
        return "MECHANISM_ONLY"
    return None


# ── per-row processing ─────────────────────────────────────────────────────────

def _process_row(ops: sqlite3.Connection, row: sqlite3.Row) -> int:
    """
    Process one walkback row. Returns RPC credits consumed.
    Writes result back to DB. Never raises — errors are caught and stored.
    """
    deep_walkback.ensure_schema(ops)
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
            hop1, sig, slot, bt, amt, mech = _find_with_evidence(
                subprov, rpc, ops, source_mint=mint, hop_depth=1)
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
            hop1, sig, slot, bt, amt, mech = _find_with_evidence(
                creator, rpc, ops, source_mint=mint, hop_depth=1)
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

            # Hop 1: who funded creator? Anchor before CREATE when that evidence
            # survived in either live table, so post-launch activity is excluded.
            try:
                create_sig = _recover_create_signature_from_db(mint, ops)
            except TypeError:  # compatibility with deterministic one-argument test doubles
                create_sig = _recover_create_signature_from_db(mint)
            hop1, sig1, slot1, bt1, amt1, mech1 = _find_with_evidence(
                creator, rpc, ops, before_signature=create_sig,
                source_mint=mint, hop_depth=1)
            if not hop1:
                _mark_complete(ops, mint, "NO_ATTRIBUTION_FOUND", None, None, rpc[0])
                return rpc[0]

            # X77.1 — control-flow gates only (pure reads, no writes, no RPC):
            # both decide whether hop2 is even attempted, so they must stay
            # here, immediately after hop1 resolves, exactly as before. What
            # moved is the WRITE work below them (_store_funder, mech1
            # evidence capture) -- deferred past hop2's own RPC resolution
            # (see the collect-then-persist block after hop2, below). Neither
            # gate reads anything _store_funder/mech1-evidence-capture would
            # have written (they query wt_discovered_subprovs/
            # wt_confirmed_treasuries, never wt_walkback_queue's funder_*
            # columns or the provisioning-edge tables), so this reordering
            # changes no decision, only when the bookkeeping writes land.
            if _is_known_subprov(ops, hop1):
                t_row = ops.execute(
                    "SELECT treasury FROM wt_discovered_subprovs WHERE subprov=? LIMIT 1",
                    (hop1,)).fetchone()
                treasury = t_row["treasury"] if t_row else None
                outcome = "WATCHTOWER_CONFIRMED" if treasury else "LINEAGE_GAP"
                # X77.1 — collect mech1 evidence (RPC) BEFORE any write, same
                # shape as the FULL_WALKBACK persistence stage below; this
                # early-return branch never reaches hop2, so its own
                # persistence stage is inlined here rather than shared.
                hop1_fetched, hop1_tx = _collect_hop1_evidence(mech1, sig1)
                if hop1_fetched:
                    rpc[0] += 1
                _store_funder(ops, mint, hop1, sig1, slot1, bt1, amt1, mech1)
                _persist_hop1_evidence(ops, mint=mint, creator=creator, subprov=hop1,
                                       mechanism=mech1, sig=sig1, block_time=bt1,
                                       amount_sol=amt1, tx=hop1_tx)
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
                # No hop2 attempted; hop1 evidence is still owed (X77.1: same
                # inline persistence stage as the branch above, deferred past
                # this decision but there IS no further RPC after it here).
                hop1_fetched, hop1_tx = _collect_hop1_evidence(mech1, sig1)
                if hop1_fetched:
                    rpc[0] += 1
                _store_funder(ops, mint, hop1, sig1, slot1, bt1, amt1, mech1)
                _persist_hop1_evidence(ops, mint=mint, creator=creator, subprov=hop1,
                                       mechanism=mech1, sig=sig1, block_time=bt1,
                                       amount_sol=amt1, tx=hop1_tx)
                _mark_complete(ops, mint, "WATCHTOWER_CONFIRMED", None, hop1, rpc[0])
                return rpc[0]

            # Hop 2: who funded hop1? Search strictly before the creator-funding
            # transaction and inspect the oldest edge of the bounded history.
            # This recovers capital funding hidden behind high-frequency fan-out.
            # X77.1 — this call, and hop1's own call above, are the ONLY RPC
            # work remaining in this branch: _store_funder/mech1-evidence-
            # capture/_capture_provisioning_facts (all pure DB writes, zero
            # RPC of their own -- see their own docstrings) are deferred to
            # fire together, once, after hop2 resolves -- collect fully, then
            # persist once, per this milestone's core principle. hop2 needs
            # only hop1/sig1 (already local variables); it does not depend on
            # anything _store_funder or the mech1 evidence capture write.
            hop2, sig2, slot2, bt2, amt2, mech2 = _find_with_evidence(
                hop1, rpc, ops, before_signature=sig1, prefer_oldest=True,
                source_mint=mint, hop_depth=2)

            # X77.1 persistence stage — everything RPC-free from here on.
            # Collect hop1's own evidence (mech1 tx fetch, if any) now, RIGHT
            # BEFORE writing, so no write precedes any RPC call in this
            # branch. hop1_tx is None when mech1 required no extra fetch
            # (PLAIN_XFER/UNKNOWN) OR when a required fetch failed --
            # hop1_fetched distinguishes "no fetch needed" (rpc[0] unchanged)
            # from "fetch attempted" (rpc[0] +1 regardless of tx outcome),
            # matching the pre-X77.1 code's unconditional increment on ATTEMPT
            # rather than on a successful result.
            hop1_fetched, hop1_tx = _collect_hop1_evidence(mech1, sig1)
            if hop1_fetched:
                rpc[0] += 1
            _store_funder(ops, mint, hop1, sig1, slot1, bt1, amt1, mech1)
            hop1_evidence_level = _persist_hop1_evidence(
                ops, mint=mint, creator=creator, subprov=hop1,
                mechanism=mech1, sig=sig1, block_time=bt1,
                amount_sol=amt1, tx=hop1_tx)

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
                # Unknown hop-2 is an intermediate until a deeper anchored walk
                # proves otherwise. Preserve the existing review lead, then
                # expand without using unknown candidates as attribution roots.
                disp = _surface_treasury_review_lead(
                    ops, hop2, hop1, creator, mint, sig2, amt2, mech2)
                deep = _expand_unknown_upstream(
                    ops, mint=mint, start_wallet=hop2, anchor_signature=sig2,
                    rpc_counter=rpc, start_depth=2)
                if deep.get("treasury"):
                    _mark_complete(ops, mint, "WATCHTOWER_CONFIRMED", hop1,
                                   deep["treasury"], rpc[0], confirmed_subprov=True)
                else:
                    state = deep["state"]
                    _mark_complete(ops, mint, "LINEAGE_GAP", hop1, None, rpc[0],
                                   confirmed_subprov=False)
                    deep_walkback.set_path_state(
                        ops, mint, state, {"deepest_wallet": deep["deepest"],
                                           "hop_depth": deep["hop_depth"],
                                           "review_lead": disp})
                print(f"[WALKBACK] deep hop2 lead {disp}: {hop2[:14]}… "
                      f"state={deep['state']} depth={deep['hop_depth']}", flush=True)
            else:
                # hop1 unknown, hop2 not found — no lineage confirmation is
                # possible either way. X64: if hop1's OWN funding mechanism
                # was itself the EPHEMERAL_WSOL_CREATOR_HANDOFF primitive
                # (X62), that is directly observed WATCHTOWER infrastructure
                # evidence and must not be discarded just because no known
                # upstream identity was reachable — disposable sub-provisioners
                # are, by design, expected to be single-use and unknown.
                # Preserving it (LINEAGE_GAP, subprov=hop1, confirmed_subprov=
                # False) activates the existing _ensure_subprov_lead discovery
                # path with zero additional RPC and zero attribution risk — an
                # ordinary PLAIN_XFER/UNKNOWN hop1 still terminates exactly as
                # before.
                if _is_disposable_subprov_handoff(mech1):
                    _mark_complete(ops, mint, "LINEAGE_GAP", hop1, None, rpc[0],
                                   confirmed_subprov=False)
                    print(
                        "[WALKBACK_DISPOSABLE_SUBPROV_LEAD] "
                        f"mint={mint} creator={creator} subprov={hop1} "
                        f"mechanism={mech1} outcome=LINEAGE_GAP "
                        f"reason=UPSTREAM_UNRESOLVED "
                        f"evidence_level={hop1_evidence_level or 'MECHANISM_ONLY'} "
                        f"rpc_used={rpc[0]}", flush=True)
                else:
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
        # X26.3: skip known infrastructure (automation/relay/bridge/CEX wallets, e.g.
        # Axiom, Raydium/Meteora authorities, exchange hot wallets). Recurrence alone
        # (funding >=2 distinct creators) is NOT proof of sub-provisioner status — a
        # confirmed live example (Axiom, 23 funded creators, 0 wrap-close, 0 CREATE
        # evidence) showed this promotion path had no infrastructure check at all
        # (X26.2/X26.3 audit). Checked before the program-owned RPC check so it's
        # free and skips the RPC call entirely for known infrastructure.
        if _is_known_infrastructure(fw):
            print(f"[WALKBACK] recurring funder {fw[:14]}… is known infrastructure — skipped",
                  flush=True)
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
    deep_walkback.ensure_schema(ops)
    rows = ops.execute(
        "SELECT mint, creator, subprov, treasury, walkback_class, attempts "
        "FROM wt_walkback_queue "
        "WHERE (status='pending' OR (status='running' AND COALESCE(lease_expires_at,0) < ?)) "
        "AND attempts < ? AND COALESCE(next_retry_at,0) <= ? "
        "ORDER BY COALESCE(priority,0) DESC, enqueued_at ASC "
        "LIMIT ?",
        (int(time.time()), MAX_ATTEMPTS, int(time.time()), batch_size)).fetchall()

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


def _check_stuck_lease() -> None:
    """X76.5 -- if THIS worker's own thread-local write lease has been held
    for longer than MAX_LEASE_STUCK_SECONDS, self-kill for Supervisor to
    restart. A restart has been confirmed (live, during this milestone's own
    investigation) to fully clear the stuck state -- the lease is per-process
    thread-local, so a fresh process starts clean. Checked once per cycle,
    cheap (pure in-memory introspection, no DB I/O).

    X76.5A -- the event is now persisted (wt_walkback_recovery_events)
    BEFORE os._exit(), so Mission Control has an auditable record of every
    self-kill, distinct from a manual/external termination (which cannot
    write this row, since the process never gets a chance to detect its
    own death -- see src/ops/walkback_recovery_log.py's module docstring
    and reconcile_unexplained_restarts.py)."""
    from src.ops.walkback_cycle_trace import _lease_snapshot
    lease = _lease_snapshot()
    if lease and lease.get("held_seconds", 0) > MAX_LEASE_STUCK_SECONDS:
        held = lease["held_seconds"]
        command = lease.get("command")
        txid = lease.get("transaction_id")
        print(f"[WALKBACK] CRITICAL_STUCK_LEASE: held {held:.0f}s "
              f"(max={MAX_LEASE_STUCK_SECONDS}) command={command} "
              f"transaction_id={txid} — exiting for supervisord restart",
              flush=True)
        try:
            # X76.5A bug fix (found live, during this milestone's own
            # validation): the FIRST four real firings all failed to log,
            # every time, with NestedDatabaseWriteError -- db_connect()
            # returns a TrackedConnection, and _check_stuck_lease is by
            # definition running on a thread whose _thread_write_lease is
            # ALREADY poisoned by the very stuck lease being reported, so
            # attempting a normally-tracked write here immediately
            # self-nests against itself. The fix: bypass TrackedConnection
            # entirely for this one write, using the ORIGINAL unpatched
            # sqlite3.connect (db_locking.py's own _sqlite3_connect_orig).
            # Safe specifically here because the process calls os._exit(1)
            # immediately after -- there is no risk of this write itself
            # leaving a dangling lease behind for a future cycle to trip
            # over, since there is no future cycle in this process.
            from src.utils.db_locking import _sqlite3_connect_orig
            from src.ops.walkback_recovery_log import record_self_kill
            _log_conn = _sqlite3_connect_orig(OPS_DB_PATH, timeout=5)
            try:
                record_self_kill(
                    _log_conn, worker="walkback_worker",
                    reason=f"stale write lease held {held:.0f}s (threshold {MAX_LEASE_STUCK_SECONDS}s)",
                    lease_age_seconds=held, lease_command=command, lease_transaction_id=txid,
                )
            finally:
                _log_conn.close()
        except Exception as e:
            # Never let recovery-log persistence block the self-kill itself --
            # a failure to log must not become a failure to recover.
            print(f"[WALKBACK] failed to persist self-kill event (non-fatal): {e}", flush=True)
        os._exit(1)


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

        # X76.5A -- this is a fresh process boot (run_loop() only executes
        # once per process). If the most recent recovery event for this
        # worker has no restarted_at/healthy_at yet, this boot IS that
        # restart -- close the loop on the recovery-history row so Mission
        # Control can show "time to healthy heartbeat" instead of a
        # permanently open-ended event. Non-essential: failure here must
        # never block startup.
        try:
            from src.ops.walkback_recovery_log import recent_events, mark_restarted, mark_healthy
            _recent = recent_events(startup, worker="walkback_worker", limit=1)
            if _recent and _recent[0].get("restarted_at") is None:
                _now = int(time.time())
                mark_restarted(startup, _recent[0]["event_id"], restarted_at=_now, outcome="restarted successfully")
                mark_healthy(startup, _recent[0]["event_id"], healthy_at=_now)
        except Exception as e:
            print(f"[WALKBACK] recovery-log startup reconciliation skipped (non-fatal): {e}", flush=True)

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
        _check_stuck_lease()
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

                # X64.5 — self-healing pass: rows stuck WAITING_FOR_CREATE_ANCHOR
                # never reach drain_batch's own SELECT (it only matches
                # status='pending'/expired-'running'), so a valid CREATE
                # signature that becomes visible in creator_funding_queue AFTER
                # enqueue would otherwise leave the row permanently stuck. This
                # is a zero-RPC, local-DB-only re-check — never increments the
                # normal walkback `attempts` counter (uses its own
                # anchor_lookup_attempts column). Non-essential maintenance:
                # a transient failure here is logged and skipped, matching the
                # existing recover_stalled_running_jobs/finalize_exhausted_pending
                # pattern above, never crashes the loop.
                trace_boundary("anchor_reconciliation_attempted")
                _t0 = time.monotonic()
                try:
                    from src.ops.anchor_reconciliation import reconcile_waiting_create_anchors
                    _live = sqlite3.connect(f"file:{LIVE_DB_PATH}?mode=ro", uri=True, timeout=5)
                    try:
                        recon = reconcile_waiting_create_anchors(ops, _live)
                        if recon["recovered"]:
                            print(f"[WALKBACK] anchor reconciliation: recovered "
                                  f"{len(recon['recovered'])} of {recon['examined']} "
                                  f"stuck rows this cycle", flush=True)
                    finally:
                        _live.close()
                    trace_boundary("anchor_reconciliation_completed",
                                   extra={"elapsed_s": round(time.monotonic() - _t0, 3)})
                except Exception as e:
                    trace_failure("anchor_reconciliation_attempted", e, elapsed_s=time.monotonic() - _t0)
                    if not _is_lock_error(e):
                        raise
                    print(f"[WALKBACK] anchor reconciliation skipped (lock contention): {e}", flush=True)

                # X64.7A Phase 2 — durable ledger-write retry pass. A CREATE
                # observation that failed to commit into wt_create_event_ledger
                # (e.g. transient lock) is durably queued in
                # wt_create_ledger_pending; this pass retries it here, zero-RPC,
                # bounded backoff, same non-essential-maintenance pattern as
                # the anchor reconciliation pass immediately above.
                trace_boundary("create_ledger_retry_attempted")
                _t0 = time.monotonic()
                try:
                    from src.ops.create_event_ledger import retry_pending_writes
                    retry_result = retry_pending_writes(ops)
                    if retry_result["recovered"]:
                        print(f"[WALKBACK] create-ledger retry: recovered "
                              f"{len(retry_result['recovered'])} of {retry_result['examined']} "
                              f"pending writes this cycle", flush=True)
                    if retry_result["exhausted"]:
                        print(f"[WALKBACK] create-ledger retry: {retry_result['exhausted']} "
                              f"pending writes have exhausted retry attempts", flush=True)
                    trace_boundary("create_ledger_retry_completed",
                                   extra={"elapsed_s": round(time.monotonic() - _t0, 3)})
                except Exception as e:
                    trace_failure("create_ledger_retry_attempted", e, elapsed_s=time.monotonic() - _t0)
                    if not _is_lock_error(e):
                        raise
                    print(f"[WALKBACK] create-ledger retry skipped (lock contention): {e}", flush=True)

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
