"""Pure, feature-gated contracts for Byzantine Scenario-D shadow observation.

This module deliberately contains no RPC client, database connection, wallet,
signer, transaction serializer, or submitter.  A future post-commit worker may
inject retained evidence into these functions, but this contract cannot trade.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any, Mapping, Sequence

from src.ops.pumpfun_execution_model_target_era import CurveState, ExecutionQuote, buy_exact_sol_in, sell

STRATEGY_VERSION = "BYZANTINE_SCENARIO_D_SHADOW_V1"
PRIMARY_SOL_LAMPORTS = 250_000_000
COMPARISON_SOL_LAMPORTS = (100_000_000, 500_000_000, 1_000_000_000)
FEATURE_FLAG = "BYZANTINE_PROSPECTIVE_SHADOW_OBSERVER_ENABLED"
FEATURE_FLAG_DEFAULT = False
UNKNOWN_DISCRIMINATORS = frozenset({
    "5df6823ce7e940b2", "b817ee6167c5d33d", "5e06ca73ff60e8b7",
    "9beae792ec9ea21e", "bbcb121fceedfe29",
})
QUALIFIED_PROPAGATION_DISCRIMINATORS = frozenset({"Buy", "BuyExactSolIn", "BuyExactQuoteInV2", "Sell"})
HORIZONS_SECONDS = (0, 30, 60, 120, 300, 600, 1800, 3600)


def canonical_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class ScenarioDTrigger:
    operation_id: str
    mint: str
    creator: str
    fingerprint_version: str
    opening_positions: tuple[str, ...]
    recurrent_cluster_completed: bool
    trigger_signature: str
    slot: int
    transaction_ordinal: int
    instruction_index: int
    detector_version: str

    def qualified(self) -> bool:
        return (len(self.opening_positions) == 12 and self.recurrent_cluster_completed
                and all((self.operation_id, self.mint, self.creator, self.fingerprint_version,
                         self.trigger_signature, self.detector_version))
                and self.slot >= 0 and self.transaction_ordinal >= 0 and self.instruction_index >= 0)


@dataclass(frozen=True)
class EntryState:
    mint: str
    bonding_curve: str
    program_id: str
    slot: int
    signature: str
    transaction_ordinal: int
    instruction_index: int
    virtual_sol: int
    virtual_token: int
    real_sol: int | None
    real_token: int
    complete: bool
    global_config_identity: str
    fee_config_identity: str
    raw_state_sha256: str
    provenance: str

    def curve_state(self) -> CurveState:
        if self.virtual_sol <= 0 or self.virtual_token <= 1 or not self.raw_state_sha256 or not self.provenance:
            raise ValueError("ENTRY_STATE_UNAVAILABLE")
        return CurveState(self.virtual_sol, self.virtual_token)


@dataclass(frozen=True)
class ShadowQuote:
    gross_sol_input: int
    quote: ExecutionQuote
    pre_state: CurveState
    post_state: CurveState
    label: str = "MODELLED_WITH_QUALIFIED_EXECUTION"


@dataclass(frozen=True)
class ShadowTransition:
    signature: str
    slot: int
    transaction_ordinal: int
    instruction_index: int
    discriminator: str
    observed_pre: CurveState
    observed_post: CurveState
    raw_pre_sha256: str
    raw_post_sha256: str
    has_instruction_data: bool
    has_ordered_accounts: bool
    has_inner_instructions: bool
    has_balance_deltas: bool
    has_logs: bool

    def evidence_complete(self) -> bool:
        return all((self.signature, self.raw_pre_sha256, self.raw_post_sha256,
                    self.has_instruction_data, self.has_ordered_accounts,
                    self.has_inner_instructions, self.has_balance_deltas, self.has_logs))


@dataclass(frozen=True)
class PathResult:
    status: str
    counterfactual_state: CurveState | None
    reason: str | None = None


def shadow_identity(trigger: ScenarioDTrigger, *, position_size_version: str = "lamports-v1") -> str:
    if not trigger.qualified():
        raise ValueError("SHADOW_ENTRY_NOT_QUALIFIED")
    return canonical_digest({"operation_id": trigger.operation_id, "mint": trigger.mint,
        "trigger_signature": trigger.trigger_signature, "slot": trigger.slot,
        "transaction_ordinal": trigger.transaction_ordinal, "instruction_index": trigger.instruction_index,
        "strategy_version": STRATEGY_VERSION, "position_size_version": position_size_version})


def quote_entry(state: EntryState, amount: int = PRIMARY_SOL_LAMPORTS) -> ShadowQuote:
    pre = state.curve_state()
    quote = buy_exact_sol_in(pre, amount)
    return ShadowQuote(amount, quote, pre, CurveState(pre.virtual_sol + quote.curve_sol_delta, pre.virtual_token - quote.token_delta))


def quote_entry_sizes(state: EntryState) -> dict[int, ShadowQuote]:
    return {amount: quote_entry(state, amount) for amount in (PRIMARY_SOL_LAMPORTS, *COMPARISON_SOL_LAMPORTS)}


def propagate_transition(counterfactual_state: CurveState | None, transition: ShadowTransition) -> PathResult:
    if counterfactual_state is None:
        return PathResult("COUNTERFACTUAL_BLOCKED", None, "PRIOR_COUNTERFACTUAL_BLOCKED")
    if transition.discriminator in UNKNOWN_DISCRIMINATORS:
        return PathResult("COUNTERFACTUAL_BLOCKED", None, "HIGH_VALUE_UNKNOWN_DISCRIMINATOR")
    if transition.discriminator not in QUALIFIED_PROPAGATION_DISCRIMINATORS or not transition.evidence_complete():
        return PathResult("COUNTERFACTUAL_BLOCKED", None, "COUNTERFACTUAL_TRANSITION_UNSUPPORTED")
    # The future worker must supply a reviewed semantic adapter before this path
    # can change state.  Matching observed state is never substituted here.
    return PathResult("COUNTERFACTUAL_QUALIFIED", counterfactual_state, "SEMANTIC_ADAPTER_REQUIRED")


def minimum_principal_recovery(state: CurveState, token_position: int, principal_lamports: int = PRIMARY_SOL_LAMPORTS) -> dict[str, Any]:
    if token_position <= 0:
        return {"status": "PRINCIPAL_NOT_RECOVERABLE"}
    lo, hi, answer = 1, token_position, None
    while lo <= hi:
        candidate = (lo + hi) // 2
        quote = sell(state, candidate)
        if quote.user_sol_total >= principal_lamports:
            answer, hi = (candidate, quote), candidate - 1
        else:
            lo = candidate + 1
    if answer is None:
        return {"status": "PRINCIPAL_NOT_RECOVERABLE"}
    amount, quote = answer
    return {"status": "QUALIFIED", "token_amount": amount, "gross_sol": -quote.curve_sol_delta,
            "protocol_fee": quote.protocol_fee, "creator_fee": quote.creator_fee,
            "net_sol": quote.user_sol_total, "sell_fraction": [amount, token_position],
            "runner_fraction": [token_position - amount, token_position],
            "post_sell_state": asdict(CurveState(state.virtual_sol + quote.curve_sol_delta, state.virtual_token - quote.token_delta))}


def bounded_schedule() -> tuple[int, ...]:
    return HORIZONS_SECONDS


def submission_capability() -> str:
    return "NONE"
