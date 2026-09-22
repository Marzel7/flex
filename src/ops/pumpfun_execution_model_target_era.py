"""Offline, variant-specific Pump.fun target-era execution calculators.

This module is deliberately research-only.  It has no RPC, wallet, or live
execution dependency.  Unknown variants fail closed rather than falling back
to a generic curve formula.
"""
from __future__ import annotations

from dataclasses import dataclass

PROTOCOL_FEE_BP = 95
CREATOR_FEE_BP = 30
BPS_DENOMINATOR = 10_000


class UnqualifiedVariant(ValueError):
    """Raised when a caller asks the target-era model to guess a variant."""


@dataclass(frozen=True)
class CurveState:
    virtual_sol: int
    virtual_token: int


@dataclass(frozen=True)
class ExecutionQuote:
    variant: str
    curve_sol_delta: int
    token_delta: int
    protocol_fee: int
    creator_fee: int
    user_sol_total: int


def ceil_div(numerator: int, denominator: int) -> int:
    return (numerator + denominator - 1) // denominator


def _fees(net_sol: int) -> tuple[int, int]:
    return (
        ceil_div(net_sol * PROTOCOL_FEE_BP, BPS_DENOMINATOR),
        ceil_div(net_sol * CREATOR_FEE_BP, BPS_DENOMINATOR),
    )


def _buy_exact_quote(variant: str, state: CurveState, spendable_sol_in: int) -> ExecutionQuote:
    """Quote the documented exact-SOL/quote-in path in raw integer units."""
    net = (spendable_sol_in * BPS_DENOMINATOR) // (BPS_DENOMINATOR + PROTOCOL_FEE_BP + CREATOR_FEE_BP)
    protocol_fee, creator_fee = _fees(net)
    # The contract adjusts the net amount only when independent ceil fees would
    # otherwise push the user charge above the supplied spendable amount.
    net -= max(0, net + protocol_fee + creator_fee - spendable_sol_in)
    tokens = ((net - 1) * state.virtual_token) // (state.virtual_sol + net - 1)
    return ExecutionQuote(variant, net, tokens, protocol_fee, creator_fee, spendable_sol_in)


def buy_exact_sol_in(state: CurveState, spendable_sol_in: int) -> ExecutionQuote:
    return _buy_exact_quote("BuyExactSolIn", state, spendable_sol_in)


def buy_exact_quote_in_v2(state: CurveState, spendable_quote_in: int) -> ExecutionQuote:
    # The retained target-era V2 case proves the same user-price/curve quote
    # path.  Its 5,000 bp buyback value partitions protocol fee downstream;
    # it is not an additional user fee or a curve-reserve adjustment.
    return _buy_exact_quote("BuyExactQuoteInV2", state, spendable_quote_in)


def buy(state: CurveState, token_amount: int) -> ExecutionQuote:
    """Quote legacy Buy: exact token amount, bounded user SOL cost."""
    if token_amount >= state.virtual_token:
        raise ValueError("token amount exhausts virtual reserves")
    curve_sol = ceil_div(token_amount * state.virtual_sol, state.virtual_token - token_amount)
    protocol_fee, creator_fee = _fees(curve_sol)
    return ExecutionQuote("Buy", curve_sol, token_amount, protocol_fee, creator_fee, curve_sol + protocol_fee + creator_fee)


def sell(state: CurveState, token_amount: int) -> ExecutionQuote:
    """Quote standard Sell, preserving the integer constant-product floor."""
    gross_sol = (token_amount * state.virtual_sol) // (state.virtual_token + token_amount)
    protocol_fee, creator_fee = _fees(gross_sol)
    return ExecutionQuote("Sell", -gross_sol, -token_amount, protocol_fee, creator_fee, gross_sol - protocol_fee - creator_fee)


def dispatch(variant: str, state: CurveState, amount: int) -> ExecutionQuote:
    handlers = {
        "Buy": buy,
        "BuyExactSolIn": buy_exact_sol_in,
        "BuyExactQuoteInV2": buy_exact_quote_in_v2,
        "Sell": sell,
    }
    try:
        return handlers[variant](state, amount)
    except KeyError as exc:
        raise UnqualifiedVariant(f"target-era variant is not qualified: {variant}") from exc
