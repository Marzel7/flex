"""Bounded, callback-injected Pump.fun creation-anchor recovery.

This module has no provider, database, membership, or replay dependency.  The
caller owns retention and supplies one fixed signature-history page plus the
bounded candidate transaction fetches.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence


PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_LAYOUTS = {
    hashlib.sha256(b"global:create").digest()[:8]: ("CREATE", 0, 2, 7),
    hashlib.sha256(b"global:create_v2").digest()[:8]: ("CREATE_V2", 0, 2, 5),
}


@dataclass(frozen=True)
class CurveBirthRecovery:
    mint: str
    bonding_curve: str
    qualification: str
    signature_history_count: int
    history_complete: bool
    candidates_fetched: tuple[str, ...]
    creation_signature: str | None = None
    creation_slot: int | None = None
    creator: str | None = None
    create_type: str | None = None


def _b58_decode(value: str) -> bytes:
    number = 0
    for char in value:
        number = number * 58 + _B58.index(char)
    encoded = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return b"\0" * (len(value) - len(value.lstrip("1"))) + encoded


def _pubkey(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping) and isinstance(value.get("pubkey"), str):
        return value["pubkey"]
    return None


def _message_accounts(message: Mapping[str, Any]) -> list[str | None]:
    return [_pubkey(item) for item in (message.get("accountKeys") or ())]


def _resolve(value: Any, accounts: Sequence[str | None]) -> str | None:
    if isinstance(value, int):
        return accounts[value] if 0 <= value < len(accounts) else None
    return _pubkey(value)


def _all_instructions(tx: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    message = ((tx.get("transaction") or {}).get("message") or {})
    result = list(message.get("instructions") or ())
    for group in ((tx.get("meta") or {}).get("innerInstructions") or ()):
        if isinstance(group, Mapping):
            result.extend(group.get("instructions") or ())
    return tuple(item for item in result if isinstance(item, Mapping))


def decode_pumpfun_create(tx: Mapping[str, Any]) -> tuple[str, str, str, str] | None:
    """Return ``(type, mint, creator, bonding_curve)`` for an exact Create/CreateV2."""
    message = ((tx.get("transaction") or {}).get("message") or {})
    accounts = _message_accounts(message)
    for instruction in _all_instructions(tx):
        program = _resolve(instruction.get("programId", instruction.get("programIdIndex")), accounts)
        if program != PUMP_PROGRAM or not isinstance(instruction.get("data"), str):
            continue
        try:
            layout = _LAYOUTS.get(_b58_decode(instruction["data"])[:8])
        except (ValueError, IndexError):
            continue
        if layout is None:
            continue
        create_type, mint_index, curve_index, creator_index = layout
        values = [_resolve(value, accounts) for value in (instruction.get("accounts") or ())]
        if max(mint_index, curve_index, creator_index) >= len(values):
            continue
        mint, curve, creator = values[mint_index], values[curve_index], values[creator_index]
        if all(isinstance(value, str) and value for value in (mint, curve, creator)):
            return create_type, mint, creator, curve
    return None


def _rows(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping) and "result" in value:
        value = value["result"]
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("signature history response is not a list")
    return [item for item in value if isinstance(item, Mapping)]


def _transaction(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping) and "result" in value:
        value = value["result"]
    return value if isinstance(value, Mapping) else None


def recover_creation_from_complete_curve_history(
    *, mint: str, bonding_curve: str,
    signature_history_source: Callable[[str, int], Any],
    transaction_source: Callable[[str], Any],
    fixed_limit: int, candidate_limit: int,
) -> CurveBirthRecovery:
    """Recover only from one complete, fixed-size curve history page.

    A full page is deliberately not inspected: it fails closed as an incomplete
    address history.  For a shorter page, only the fixed oldest candidate set
    is fetched, oldest first.
    """
    if fixed_limit < 1 or candidate_limit < 1:
        raise ValueError("fixed and candidate limits must be positive")
    try:
        rows = _rows(signature_history_source(bonding_curve, fixed_limit))
    except Exception:
        return CurveBirthRecovery(mint, bonding_curve, "PROVIDER_ERROR_BOUNDED", 0, False, ())
    count = len(rows)
    if count == fixed_limit:
        return CurveBirthRecovery(mint, bonding_curve, "HISTORY_EXCEEDS_BOUND", count, False, ())
    if not rows:
        return CurveBirthRecovery(mint, bonding_curve, "NO_SIGNATURES", 0, True, ())

    signatures = [row.get("signature") for row in rows if isinstance(row.get("signature"), str)]
    candidates = list(reversed(signatures[-candidate_limit:]))
    fetched: list[str] = []
    seen: set[str] = set()
    observed_mint_mismatch = False
    observed_curve_mismatch = False
    for signature in candidates:
        if signature in seen:
            continue
        seen.add(signature)
        fetched.append(signature)
        try:
            tx = _transaction(transaction_source(signature))
        except Exception:
            return CurveBirthRecovery(mint, bonding_curve, "PROVIDER_ERROR_BOUNDED", count, True, tuple(fetched))
        if tx is None or (tx.get("meta") or {}).get("err") is not None:
            continue
        decoded = decode_pumpfun_create(tx)
        if decoded is None:
            continue
        create_type, decoded_mint, creator, decoded_curve = decoded
        if decoded_mint != mint:
            observed_mint_mismatch = True
            continue
        if decoded_curve != bonding_curve:
            observed_curve_mismatch = True
            continue
        slot = tx.get("slot")
        if not isinstance(slot, int) or not creator:
            continue
        return CurveBirthRecovery(mint, bonding_curve, "RECOVERED", count, True, tuple(fetched),
                                  signature, slot, creator, create_type)
    status = "CURVE_MISMATCH" if observed_curve_mismatch else (
        "CREATE_MINT_MISMATCH" if observed_mint_mismatch else "NO_VALID_CREATE_IN_FIXED_CANDIDATES"
    )
    return CurveBirthRecovery(mint, bonding_curve, status, count, True, tuple(fetched))
