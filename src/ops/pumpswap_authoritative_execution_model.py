"""Research-only PumpSwap integer quote model derived from Pump.fun SDK 1.19.0.

This module has no RPC, wallet, signer, transaction builder, or submitter.  It
is deliberately version-bound: callers must name the reviewed ProgramData and
fee configuration rather than silently applying current economics to another
deployment.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

PROGRAM_ID = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
SDK_VERSION = "@pump-fun/pump-swap-sdk@1.19.0"
MODEL_VERSION = "pumpswap-execution-model-sdk-1.19.0-v1"
BPS_DENOMINATOR = 10_000
BUY_DISCRIMINATOR = bytes((102, 6, 61, 18, 1, 218, 235, 234))
SELL_DISCRIMINATOR = bytes((51, 230, 133, 164, 1, 127, 131, 173))


def ceil_div(numerator: int, denominator: int) -> int:
    if numerator < 0 or denominator <= 0:
        raise ValueError("UNSUPPORTED_OR_INVALID_ARITHMETIC")
    return (numerator + denominator - 1) // denominator


def fee(amount: int, basis_points: int) -> int:
    if amount < 0 or not 0 <= basis_points <= BPS_DENOMINATOR:
        raise ValueError("UNSUPPORTED_FEE_CONFIG")
    return ceil_div(amount * basis_points, BPS_DENOMINATOR)


@dataclass(frozen=True)
class PoolState:
    base_reserve: int
    quote_reserve: int
    virtual_quote_reserves: int
    program_id: str
    programdata_sha256: str
    fee_config_identity: str
    lp_fee_bps: int
    protocol_fee_bps: int
    creator_fee_bps: int = 0
    coin_creator_present: bool = False

    def validate(self) -> None:
        if self.program_id != PROGRAM_ID or not self.programdata_sha256 or not self.fee_config_identity:
            raise ValueError("UNSUPPORTED_PROGRAM_OR_CONFIG")
        if self.base_reserve <= 0 or self.quote_reserve <= 0 or self.virtual_quote_reserves < 0:
            raise ValueError("INVALID_POOL_STATE")
        if self.effective_quote_reserve <= 0:
            raise ValueError("INVALID_POOL_STATE")
        for value in (self.lp_fee_bps, self.protocol_fee_bps, self.creator_fee_bps):
            if not 0 <= value <= BPS_DENOMINATOR:
                raise ValueError("UNSUPPORTED_FEE_CONFIG")

    @property
    def effective_quote_reserve(self) -> int:
        return self.quote_reserve + self.virtual_quote_reserves

    @property
    def active_creator_fee_bps(self) -> int:
        return self.creator_fee_bps if self.coin_creator_present else 0


@dataclass(frozen=True)
class Quote:
    direction: str
    base_amount: int
    raw_quote_amount: int
    lp_fee: int
    protocol_fee: int
    creator_fee: int
    trader_quote_amount: int
    post_base_reserve: int
    post_quote_reserve: int
    min_or_max_quote_amount: int | None
    model_version: str = MODEL_VERSION

    def deterministic(self) -> dict[str, Any]:
        return asdict(self)


def sell_exact_base_in(state: PoolState, base_amount_in: int, min_quote_amount_out: int | None = None) -> Quote:
    """Mirror published SDK `sellBaseInput`: CP output floor, each fee ceiling."""
    state.validate()
    if base_amount_in <= 0:
        raise ValueError("INVALID_BASE_INPUT")
    raw = state.effective_quote_reserve * base_amount_in // (state.base_reserve + base_amount_in)
    lp, protocol, creator = (fee(raw, state.lp_fee_bps), fee(raw, state.protocol_fee_bps), fee(raw, state.active_creator_fee_bps))
    net = raw - lp - protocol - creator
    if net < 0 or state.quote_reserve < raw - lp:
        raise ValueError("INSUFFICIENT_REAL_QUOTE_RESERVES")
    if min_quote_amount_out is not None and net < min_quote_amount_out:
        raise ValueError("MIN_QUOTE_OUTPUT_NOT_MET")
    # SDK formula prices with effective reserves. Fee transfers leave LP fee in the
    # real vault; virtual reserves are accounting only.
    return Quote("SELL_BASE_TO_QUOTE", base_amount_in, raw, lp, protocol, creator, net,
                 state.base_reserve + base_amount_in, state.quote_reserve - raw + lp,
                 min_quote_amount_out)


def buy_exact_base_out(state: PoolState, base_amount_out: int, max_quote_amount_in: int | None = None) -> Quote:
    state.validate()
    if base_amount_out <= 0 or base_amount_out >= state.base_reserve:
        raise ValueError("INVALID_BASE_OUTPUT")
    raw = ceil_div(state.effective_quote_reserve * base_amount_out, state.base_reserve - base_amount_out)
    lp, protocol, creator = (fee(raw, state.lp_fee_bps), fee(raw, state.protocol_fee_bps), fee(raw, state.active_creator_fee_bps))
    total = raw + lp + protocol + creator
    if max_quote_amount_in is not None and total > max_quote_amount_in:
        raise ValueError("MAX_QUOTE_INPUT_EXCEEDED")
    return Quote("BUY_QUOTE_TO_BASE", base_amount_out, raw, lp, protocol, creator, total,
                 state.base_reserve - base_amount_out, state.quote_reserve + raw + lp,
                 max_quote_amount_in)


def minimum_sell_input(state: PoolState, position: int, target_net_quote: int) -> int | None:
    """Pure monotone binary search; not connected to the Byzantine executor."""
    if position <= 0 or target_net_quote <= 0:
        return None
    low, high, answer = 1, position, None
    while low <= high:
        candidate = (low + high) // 2
        if sell_exact_base_in(state, candidate).trader_quote_amount >= target_net_quote:
            answer, high = candidate, candidate - 1
        else:
            low = candidate + 1
    return answer


def solve_principal_recovery(state: PoolState, token_holdings: int, principal_target: int) -> dict[str, Any]:
    """Generic venue-level principal recovery proof; no operation assumptions."""
    amount = minimum_sell_input(state, token_holdings, principal_target)
    if amount is None:
        return {"status": "PRINCIPAL_NOT_RECOVERABLE", "principal_target": principal_target}
    quote = sell_exact_base_in(state, amount)
    prior = sell_exact_base_in(state, amount - 1).trader_quote_amount if amount > 1 else 0
    return {"status": "QUALIFIED", "principal_target": principal_target, "token_amount": amount,
            "net_quote": quote.trader_quote_amount, "prior_net_quote": prior,
            "minimality_proven": quote.trader_quote_amount >= principal_target and prior < principal_target,
            "runner_tokens": token_holdings - amount, "model_version": MODEL_VERSION}


def submission_capability() -> str:
    return "NONE"
