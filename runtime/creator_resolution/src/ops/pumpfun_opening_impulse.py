"""Pure replay of retained Pump.fun opening-slot evidence.

This module deliberately accepts decoded, retained evidence only.  Acquisition,
SQLite, provider clients and UI concerns stay outside this boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Mapping


LAMPORTS_PER_SOL = Decimal("1000000000")


@dataclass(frozen=True)
class PumpfunOpeningAction:
    slot: int
    transaction_index: int
    action_index: int
    signature: str
    action_type: str
    buyer: str
    post_virtual_sol_reserves: int
    post_virtual_token_reserves: int


@dataclass(frozen=True)
class PumpfunOpeningImpulseResult:
    mint: str
    creation_signature: str
    creation_slot: int
    creation_time: int | None
    creator: str
    bonding_curve: str
    associated_bonding_curve: str | None
    supply_raw: int
    decimals: int
    opening_slot: int
    ordered_actions: tuple[PumpfunOpeningAction, ...]
    first_buy_signature: str | None
    first_buy_buyer: str | None
    first_buy_mc_sol: Decimal | None
    first_independent_buy_signature: str | None
    first_independent_buy_buyer: str | None
    first_independent_buy_mc_sol: Decimal | None
    opening_slot_trade_count: int
    opening_slot_start_mc_sol: Decimal | None
    opening_slot_peak_mc_sol: Decimal | None
    opening_slot_low_mc_sol: Decimal | None
    opening_slot_end_mc_sol: Decimal | None
    qualification: str


def market_cap_sol(*, virtual_sol_reserves: int, virtual_token_reserves: int,
                   supply_raw: int, decimals: int) -> Decimal:
    """Post-action Pump.fun spot MC in SOL from retained virtual reserves."""
    if virtual_sol_reserves <= 0 or virtual_token_reserves <= 0 or supply_raw <= 0:
        raise ValueError("positive reserve and supply evidence is required")
    token_units = Decimal(10) ** decimals
    supply_ui = Decimal(supply_raw) / token_units
    price_sol = (Decimal(virtual_sol_reserves) / LAMPORTS_PER_SOL) / (
        Decimal(virtual_token_reserves) / token_units
    )
    return price_sol * supply_ui


def _value(item: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        value = item.get(name)
        if value is not None:
            return value
    return default


def _action(item: Mapping[str, Any], *, default_slot: int, default_action_index: int) -> PumpfunOpeningAction:
    context = item.get("event_timestamp_or_slot_context") or {}
    return PumpfunOpeningAction(
        slot=int(_value(item, "slot", default=context.get("slot", default_slot))),
        transaction_index=int(_value(item, "transaction_index")),
        action_index=int(_value(item, "action_index", "action_number", "number", default=default_action_index)),
        signature=str(_value(item, "signature")),
        action_type=str(_value(item, "action_type", "type")).upper(),
        buyer=str(_value(item, "buyer", "user", default="")),
        post_virtual_sol_reserves=int(_value(item, "post_virtual_sol_reserves")),
        post_virtual_token_reserves=int(_value(item, "post_virtual_token_reserves")),
    )


def reconstruct_opening_impulse(*, mint: str, creation_signature: str, creation_slot: int,
                                creation_time: int | None, creator: str, bonding_curve: str,
                                associated_bonding_curve: str | None, supply_raw: int,
                                decimals: int, opening_slot: int,
                                actions: Iterable[Mapping[str, Any]],
                                archived_prebuy_state: Mapping[str, Any] | None = None) -> PumpfunOpeningImpulseResult:
    """Replay one retained opening slot in slot/transaction/action order.

    ``archived_prebuy_state`` is evidence/provenance for action-one continuity;
    it is intentionally not fetched or decoded here.
    """
    del archived_prebuy_state  # Contract marker: callers retain this separately.
    ordered = tuple(sorted(
        (_action(item, default_slot=opening_slot, default_action_index=index)
         for index, item in enumerate(actions, start=1)),
        key=lambda item: (item.slot, item.transaction_index, item.action_index),
    ))
    if not ordered:
        raise ValueError("opening action evidence is required")
    if any(item.slot != opening_slot for item in ordered):
        raise ValueError("opening replay accepts one bounded slot")
    state_actions = tuple(item for item in ordered if item.action_type in {"BUY", "SELL"})
    if not state_actions:
        raise ValueError("no state-changing Pump.fun actions")
    mcs = tuple(market_cap_sol(virtual_sol_reserves=item.post_virtual_sol_reserves,
                               virtual_token_reserves=item.post_virtual_token_reserves,
                               supply_raw=supply_raw, decimals=decimals) for item in state_actions)
    buys = tuple(item for item in state_actions if item.action_type == "BUY")
    first_buy = buys[0] if buys else None
    independent = next((item for item in buys if item.buyer != creator), None)
    def mc_for(target: PumpfunOpeningAction | None) -> Decimal | None:
        if target is None:
            return None
        return market_cap_sol(virtual_sol_reserves=target.post_virtual_sol_reserves,
                              virtual_token_reserves=target.post_virtual_token_reserves,
                              supply_raw=supply_raw, decimals=decimals)
    return PumpfunOpeningImpulseResult(
        mint=mint, creation_signature=creation_signature, creation_slot=creation_slot,
        creation_time=creation_time, creator=creator, bonding_curve=bonding_curve,
        associated_bonding_curve=associated_bonding_curve, supply_raw=supply_raw,
        decimals=decimals, opening_slot=opening_slot, ordered_actions=ordered,
        first_buy_signature=first_buy.signature if first_buy else None,
        first_buy_buyer=first_buy.buyer if first_buy else None, first_buy_mc_sol=mc_for(first_buy),
        first_independent_buy_signature=independent.signature if independent else None,
        first_independent_buy_buyer=independent.buyer if independent else None,
        first_independent_buy_mc_sol=mc_for(independent), opening_slot_trade_count=len(state_actions),
        opening_slot_start_mc_sol=mcs[0], opening_slot_peak_mc_sol=max(mcs),
        opening_slot_low_mc_sol=min(mcs), opening_slot_end_mc_sol=mcs[-1],
        qualification="RETAINED_POST_ACTION_STATE_REPLAY",
    )
