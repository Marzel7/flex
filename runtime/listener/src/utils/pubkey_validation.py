"""X24.9 — canonical Solana pubkey validator.

Single source of truth for "is this a subscribable Solana wallet address."
Every websocket-subscription source (treasury, session subprov, promoted
subprov, dust marker, CDC, any future standing watchlist) must call
is_valid_pubkey() before a wallet ever reaches SubscriptionManager.subscribe().

Root cause this exists to prevent (X24.8): two wt_dust_markers entries were
33-byte (not 32-byte) base58 strings — one character too long. Helius accepted
the logsSubscribe request without complaint but could never emit a matching
notification for an invalid address, so the subscription retried and
exhausted forever without anyone noticing it was bad data, not a websocket
or provider defect.
"""
from __future__ import annotations

import base58

PUBKEY_BYTE_LENGTH = 32


def is_valid_pubkey(wallet: str | None) -> bool:
    """True iff wallet decodes as base58 to exactly 32 bytes.

    Deterministic, no I/O, no network. Does not check whether the address
    exists on-chain or holds any account — only that it is structurally a
    valid Solana pubkey shape, which is all a subscribe-time gate can and
    should check.
    """
    if not wallet or not isinstance(wallet, str):
        return False
    try:
        decoded = base58.b58decode(wallet)
    except Exception:
        return False
    return len(decoded) == PUBKEY_BYTE_LENGTH


def invalid_reason(wallet: str | None) -> str | None:
    """None if valid; otherwise a short machine-readable reason code, for
    audit/health-metric reporting (not for control flow — use is_valid_pubkey
    for that)."""
    if not wallet or not isinstance(wallet, str):
        return "EMPTY_OR_NOT_STRING"
    try:
        decoded = base58.b58decode(wallet)
    except Exception:
        return "BASE58_DECODE_ERROR"
    if len(decoded) != PUBKEY_BYTE_LENGTH:
        return f"WRONG_LENGTH_{len(decoded)}_BYTES"
    return None
