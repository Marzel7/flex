"""
Creator SOL movement parsing.

parse_creator_sol_movement(tx, creator) is the single entry point for both
webhook and reconcile paths — both produce identical MovementEvent output.

Net lamports calculation:
  net = sum(inbound nativeTransfers to creator)
      - sum(outbound nativeTransfers from creator)
      - fee (if creator is fee payer)

Fallback (no nativeTransfers):
  net = postBalances[creator_idx] - preBalances[creator_idx]
  This is already net of fee — do NOT subtract fee again.

Fee payer:
  Helius enhanced tx: feePayer field (top-level or under transaction.feePayer).
  Raw RPC tx: accountKeys[0] when it is a signer+writable (fee payer is always
  the first account in a Solana transaction).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

_LAMPORTS_PER_SOL  = 1_000_000_000
_MIN_ABS_LAMPORTS  = 1_000   # 0.000001 SOL — skip pure dust / rounding


@dataclass(slots=True)
class MovementEvent:
    creator_address: str
    signature:       str
    slot:            int
    block_time:      int
    net_lamports:    int                      # signed: positive=in, negative=out
    abs_lamports:    int                      # abs(net_lamports)
    direction:       Literal["in", "out"]
    counterparty:    Optional[str]            # best-effort, nullable
    source:          Literal["webhook", "reconcile"]


def parse_creator_sol_movement(
    tx:      dict,
    creator: str,
    *,
    source:  Literal["webhook", "reconcile"] = "webhook",
) -> Optional[MovementEvent]:
    """
    Parse a Helius enhanced transaction (or a raw RPC getTransaction result
    adapted to the same shape) and return a MovementEvent for `creator`.

    Returns None when:
      - signature is missing
      - creator is not involved in any SOL movement
      - net movement is below dust threshold
    """
    sig = _extract_sig(tx)
    if not sig:
        return None

    slot       = tx.get("slot") or 0
    block_time = tx.get("blockTime") or tx.get("timestamp") or 0
    fee        = _extract_fee(tx)
    fee_payer  = _extract_fee_payer(tx)

    native = tx.get("nativeTransfers")
    if isinstance(native, list) and native:
        net, counterparty = _net_from_native_transfers(creator, native, fee, fee_payer)
    else:
        # Fallback: preBalances / postBalances delta (already includes fee)
        net, counterparty = _net_from_balance_diff(creator, tx)

    if net is None or abs(net) < _MIN_ABS_LAMPORTS:
        return None

    return MovementEvent(
        creator_address = creator,
        signature       = sig,
        slot            = slot,
        block_time      = block_time,
        net_lamports    = net,
        abs_lamports    = abs(net),
        direction       = "in" if net > 0 else "out",
        counterparty    = counterparty,
        source          = source,
    )


# ---------------------------------------------------------------------------
# Net calculation — nativeTransfers path (Helius enhanced)
# ---------------------------------------------------------------------------

def _net_from_native_transfers(
    creator:   str,
    transfers: list,
    fee:       int,
    fee_payer: Optional[str],
) -> tuple[Optional[int], Optional[str]]:
    """
    Aggregate all nativeTransfer entries involving creator.

    Each entry: {"fromUserAccount": str, "toUserAccount": str, "amount": int}

    Returns (net_lamports, counterparty).
    net_lamports is None if creator has no transfer involvement at all.
    """
    inbound  = 0
    outbound = 0
    counterparties: list[tuple[int, str]] = []  # (lamports, address)

    for entry in transfers:
        if not isinstance(entry, dict):
            continue
        frm     = entry.get("fromUserAccount")
        to      = entry.get("toUserAccount")
        amount  = entry.get("amount", 0)
        if not isinstance(amount, int):
            try:
                amount = int(amount)
            except Exception:
                continue

        if to == creator:
            inbound += amount
            if isinstance(frm, str) and frm != creator:
                counterparties.append((amount, frm))
        elif frm == creator:
            outbound += amount
            if isinstance(to, str) and to != creator:
                counterparties.append((amount, to))

    if inbound == 0 and outbound == 0:
        return None, None

    # Subtract fee if creator is the fee payer
    fee_adjustment = fee if (fee_payer == creator) else 0
    net = inbound - outbound - fee_adjustment

    counterparty = _pick_counterparty(counterparties, net)
    return net, counterparty


# ---------------------------------------------------------------------------
# Net calculation — preBalances/postBalances path (RPC fallback)
# ---------------------------------------------------------------------------

def _net_from_balance_diff(
    creator: str,
    tx:      dict,
) -> tuple[Optional[int], Optional[str]]:
    """
    Derive net SOL change for creator from pre/post balance arrays.

    Works for both Helius raw format and RPC getTransaction (jsonParsed).
    The delta is already net of fee — do NOT apply fee correction here.
    """
    meta = tx.get("meta") or {}
    pre  = meta.get("preBalances")  or []
    post = meta.get("postBalances") or []

    if not pre or not post:
        return None, None

    keys = _account_keys(tx)
    if not keys:
        return None, None

    creator_idx = None
    for i, k in enumerate(keys):
        if k == creator:
            creator_idx = i
            break

    if creator_idx is None or creator_idx >= len(pre) or creator_idx >= len(post):
        return None, None

    net = int(post[creator_idx]) - int(pre[creator_idx])

    # Best-effort counterparty: account with largest opposite balance change
    counterparty = _counterparty_from_balance_diff(creator_idx, keys, pre, post, net)
    return net, counterparty


def _counterparty_from_balance_diff(
    creator_idx: int,
    keys:        list[str],
    pre:         list,
    post:        list,
    net:         int,
) -> Optional[str]:
    best_addr   = None
    best_delta  = 0
    want_sign   = -1 if net > 0 else 1   # counterparty moves opposite direction

    for i, addr in enumerate(keys):
        if i == creator_idx:
            continue
        if i >= len(pre) or i >= len(post):
            continue
        delta = int(post[i]) - int(pre[i])
        if (delta * want_sign) > 0 and abs(delta) > abs(best_delta):
            best_delta = delta
            best_addr  = addr

    return best_addr


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_sig(tx: dict) -> Optional[str]:
    sig = tx.get("signature")
    if isinstance(sig, str) and sig:
        return sig
    # RPC getTransaction wraps sigs in transaction.signatures list
    try:
        sigs = tx["transaction"]["signatures"]
        if sigs:
            return sigs[0]
    except (KeyError, TypeError, IndexError):
        pass
    return None


def _extract_fee(tx: dict) -> int:
    """Transaction fee in lamports. Falls back to meta.fee."""
    fee = tx.get("fee")
    if isinstance(fee, int):
        return fee
    meta = tx.get("meta") or {}
    f = meta.get("fee")
    return int(f) if isinstance(f, int) else 0


def _extract_fee_payer(tx: dict) -> Optional[str]:
    """
    Fee payer is always accountKeys[0] (first signer) in Solana.
    Helius enhanced tx may also expose a top-level feePayer field.
    """
    fp = tx.get("feePayer")
    if isinstance(fp, str) and fp:
        return fp
    keys = _account_keys(tx)
    return keys[0] if keys else None


def _account_keys(tx: dict) -> list[str]:
    """
    Normalise account keys from both Helius enhanced and RPC jsonParsed formats.

    Enhanced:  tx["accountData"][*]["account"]  OR  tx["transaction"]["message"]["accountKeys"]
    jsonParsed: accountKeys is list of {"pubkey": str, ...}
    Raw:        accountKeys is list of str
    """
    # Helius enhanced: accountData list
    account_data = tx.get("accountData")
    if isinstance(account_data, list) and account_data:
        keys = []
        for entry in account_data:
            if isinstance(entry, dict):
                a = entry.get("account")
                if isinstance(a, str):
                    keys.append(a)
        if keys:
            return keys

    # RPC / raw webhook: transaction.message.accountKeys
    try:
        raw_keys = tx["transaction"]["message"]["accountKeys"]
        keys = []
        for k in raw_keys:
            if isinstance(k, str):
                keys.append(k)
            elif isinstance(k, dict):
                pk = k.get("pubkey")
                if isinstance(pk, str):
                    keys.append(pk)
        if keys:
            return keys
    except (KeyError, TypeError):
        pass

    return []


def _pick_counterparty(
    counterparties: list[tuple[int, str]],
    net:            int,
) -> Optional[str]:
    """
    Pick the single most significant counterparty.

    For outflow (net < 0): largest receiver.
    For inflow  (net > 0): largest sender.
    If multiple equal candidates, return None (ambiguous).
    """
    if not counterparties:
        return None
    if len(counterparties) == 1:
        return counterparties[0][1]

    # Sort by lamports descending; pick the largest
    counterparties.sort(key=lambda x: x[0], reverse=True)
    top_amount, top_addr = counterparties[0]
    second_amount = counterparties[1][0]

    # If top two are equal, counterparty is ambiguous
    if top_amount == second_amount:
        return None

    return top_addr
