"""Strict, provider-free extraction of a PumpSwap pool-initialisation price.

This module intentionally consumes a *retained exact transaction* only.  It
does not discover pools, resolve accounts, or query history.  The caller must
already have the canonical pool address from the migration anchor.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping


PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112"
PUMPSWAP_SELL_UNSUPPORTED = "PUMPSWAP_SELL_UNSUPPORTED"
COUNTERFACTUAL_MIGRATION_MAPPING_UNQUALIFIED = "COUNTERFACTUAL_MIGRATION_MAPPING_UNQUALIFIED"


@dataclass(frozen=True)
class InitialPoolPrice:
    pool: str
    base_raw: int
    quote_raw: int
    base_decimals: int
    quote_decimals: int
    price_sol: Decimal
    derivation_version: str = "PUMPSWAP_CREATE_POOL_POST_VAULTS_V1"


@dataclass(frozen=True)
class ProspectivePumpSwapState:
    """Required future capture boundary; this is not a quote or a wallet state."""
    pool: str
    mint: str
    base_vault: str
    quote_vault: str
    base_reserve_raw: int
    quote_reserve_lamports: int
    fee_config_identity: str
    program_identity: str
    slot: int
    signature: str
    transaction_ordinal: int
    instruction_index: int
    raw_state_sha256: str
    provenance: str

    def qualified_for_sell(self) -> bool:
        # A fee/config decoder and exact historical sell replay are deliberately
        # required before any execution arithmetic is admitted.
        return False


def prospective_sell_status(state: ProspectivePumpSwapState | None) -> str:
    """Fail closed until a version-bound fee/config and sell adapter exist."""
    if state is None:
        return "PUMPSWAP_STATE_UNAVAILABLE"
    if not all((state.pool, state.mint, state.base_vault, state.quote_vault,
                state.fee_config_identity, state.program_identity, state.signature,
                state.raw_state_sha256, state.provenance)) or min(state.base_reserve_raw, state.quote_reserve_lamports) <= 0:
        return "PUMPSWAP_STATE_INVALID"
    return PUMPSWAP_SELL_UNSUPPORTED


def counterfactual_migration_mapping_status() -> str:
    """No retained contract maps a hypothetical Pump curve into pool reserves."""
    return COUNTERFACTUAL_MIGRATION_MAPPING_UNQUALIFIED


def _account_keys(tx: Mapping[str, Any]) -> list[str]:
    message = ((tx.get("transaction") or {}).get("message") or {})
    out: list[str] = []
    for key in message.get("accountKeys") or []:
        out.append(key if isinstance(key, str) else key.get("pubkey", ""))
    loaded = (tx.get("meta") or {}).get("loadedAddresses") or {}
    out.extend(loaded.get("writable") or [])
    out.extend(loaded.get("readonly") or [])
    return out


def initial_price_from_create_pool_tx(
    tx: Mapping[str, Any], *, mint: str, pool: str
) -> InitialPoolPrice | None:
    """Return price only for an exact successful CreatePool transaction.

    A balance is accepted only when its token-account owner is the known pool.
    This binds the two reserves to the canonical migration pool and avoids
    treating unrelated user balances in the same atomic transaction as AMM
    liquidity.
    """
    meta = tx.get("meta") or {}
    logs = meta.get("logMessages") or []
    if meta.get("err") is not None or not any("Instruction: CreatePool" in line for line in logs):
        return None
    if PUMPSWAP_PROGRAM not in _account_keys(tx) or pool not in _account_keys(tx):
        return None

    balances: dict[str, tuple[int, int]] = {}
    for balance in meta.get("postTokenBalances") or []:
        if balance.get("owner") != pool or balance.get("mint") not in (mint, WRAPPED_SOL_MINT):
            continue
        amount = ((balance.get("uiTokenAmount") or {}).get("amount"))
        decimals = ((balance.get("uiTokenAmount") or {}).get("decimals"))
        if amount is None or not isinstance(decimals, int):
            return None
        try:
            parsed = int(amount)
        except (TypeError, ValueError):
            return None
        if parsed <= 0:
            return None
        balances[balance["mint"]] = (parsed, decimals)

    if mint not in balances or WRAPPED_SOL_MINT not in balances:
        return None
    base_raw, base_decimals = balances[mint]
    quote_raw, quote_decimals = balances[WRAPPED_SOL_MINT]
    price = (Decimal(quote_raw) / (Decimal(10) ** quote_decimals)) / (
        Decimal(base_raw) / (Decimal(10) ** base_decimals)
    )
    return InitialPoolPrice(pool, base_raw, quote_raw, base_decimals, quote_decimals, price)
