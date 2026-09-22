"""Retained-only, fail-closed Pump.fun curve-state reconstruction helpers."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any, Iterable

from src.ops.pumpfun_execution_model_target_era import CurveState, dispatch


@dataclass(frozen=True)
class ReplayState:
    mint: str
    bonding_curve: str | None
    virtual_sol_reserves: int
    virtual_token_reserves: int
    real_sol_reserves: int
    real_token_reserves: int
    complete: bool | None
    provenance: str

    def integer_fields(self) -> dict[str, int]:
        return {key: value for key, value in asdict(self).items() if isinstance(value, int) and not isinstance(value, bool)}


@dataclass(frozen=True)
class StateCheckpoint:
    state: ReplayState
    provenance: str
    signature: str
    slot: int
    order_index: int
    source: str


@dataclass(frozen=True)
class TransitionResult:
    status: str
    state: ReplayState | None
    barrier: bool = False


def compare(reconstructed: ReplayState, authoritative: ReplayState, checkpoint: StateCheckpoint, last_exact: str | None, barrier: bool) -> dict[str, Any]:
    for field, expected in authoritative.integer_fields().items():
        actual = reconstructed.integer_fields()[field]
        if actual != expected:
            return {"status": "STATE_DIVERGENCE", "mint": checkpoint.state.mint, "signature": checkpoint.signature,
                    "slot": checkpoint.slot, "transaction_instruction_ordinal": checkpoint.order_index, "field": field,
                    "expected_integer": expected, "reconstructed_integer": actual, "signed_delta": actual - expected,
                    "last_exact_transition": last_exact, "unresolved_barrier": barrier}
    return {"status": "EXACT_MATCH", "mint": checkpoint.state.mint, "signature": checkpoint.signature,
            "slot": checkpoint.slot, "transaction_instruction_ordinal": checkpoint.order_index,
            "last_exact_transition": last_exact, "unresolved_barrier": barrier}


def apply_known_transition(state: ReplayState, variant: str, args: list[int]) -> ReplayState:
    if variant not in {"Buy", "BuyExactSolIn", "BuyExactQuoteInV2", "Sell"}:
        raise ValueError("UNQUALIFIED_KNOWN_TRANSITION")
    amount = int(args[0]) if variant != "Buy" else int(args[0])
    quote = dispatch(variant, CurveState(state.virtual_sol_reserves, state.virtual_token_reserves), amount)
    if variant.startswith("Buy"):
        return replace(state, virtual_sol_reserves=state.virtual_sol_reserves + quote.curve_sol_delta,
                       virtual_token_reserves=state.virtual_token_reserves - quote.token_delta,
                       real_sol_reserves=state.real_sol_reserves + quote.curve_sol_delta,
                       real_token_reserves=state.real_token_reserves - quote.token_delta,
                       provenance="KNOWN_TRANSITION_DERIVED")
    return replace(state, virtual_sol_reserves=state.virtual_sol_reserves + quote.curve_sol_delta,
                   virtual_token_reserves=state.virtual_token_reserves + quote.token_delta,
                   real_sol_reserves=state.real_sol_reserves + quote.curve_sol_delta,
                   real_token_reserves=state.real_token_reserves + quote.token_delta,
                   provenance="KNOWN_TRANSITION_DERIVED")


def run_chain(rows: Iterable[dict[str, Any]], initial: dict[str, StateCheckpoint], authoritative: dict[str, StateCheckpoint] | None = None) -> tuple[list[dict[str, Any]], dict[str, int]]:
    current = dict(initial)
    authoritative = authoritative or {}
    blocked: set[str] = set()
    last_exact: dict[str, str | None] = {mint: None for mint in initial}
    events: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: (item["mint"], item["order_index"])):
        mint = row["mint"]
        if mint not in current:
            continue
        classification = row["classification"]
        base = {"mint": mint, "signature": row["signature"], "slot": row["slot"], "order_index": row["order_index"]}
        checkpoint = authoritative.get(row["signature"])
        if checkpoint is not None:
            if mint not in blocked:
                events.append(compare(current[mint].state, checkpoint.state, checkpoint, last_exact[mint], False))
                if events[-1]["status"] == "STATE_DIVERGENCE":
                    break
            current[mint] = checkpoint
            blocked.discard(mint)
            last_exact[mint] = row["signature"]
            events.append({**base, "status": "AUTHORITATIVE_RESYNCHRONIZATION", "barrier": False})
            continue
        if classification == "PARSE_INSUFFICIENT":
            blocked.add(mint)
            events.append({**base, "status": "UNRESOLVED_STATE_TRANSITION", "barrier": True})
        elif mint in blocked:
            events.append({**base, "status": "BLOCKED_BY_UNRESOLVED_STATE_TRANSITION", "barrier": True})
        elif classification == "STATE_NEUTRAL":
            last_exact[mint] = row["signature"]
            events.append({**base, "status": "EXACT_STATE_NEUTRAL", "barrier": False})
        elif classification == "PUMPFUN_STATE_MUTATING":
            try:
                next_state = apply_known_transition(current[mint].state, row["instruction_variant"], row["instruction_args"])
            except (ValueError, ZeroDivisionError) as exc:
                # A qualified instruction is not permission to invent an
                # absent predecessor; preserve the same fail-closed boundary.
                blocked.add(mint)
                events.append({**base, "status": "UNRESOLVED_STATE_TRANSITION", "barrier": True,
                               "reason": f"KNOWN_TRANSITION_PRESTATE_INVALID:{exc}"})
            else:
                current[mint] = StateCheckpoint(next_state, "KNOWN_TRANSITION_DERIVED", row["signature"], row["slot"], row["order_index"], row.get("source_artifact", "IN_MEMORY_REPLAY_ROW"))
                last_exact[mint] = row["signature"]
                events.append({**base, "status": "KNOWN_TRANSITION_APPLIED", "barrier": False})
    metrics = {"known_transitions_chained": sum(e["status"] == "KNOWN_TRANSITION_APPLIED" for e in events),
               "state_neutral_preserved": sum(e["status"] == "EXACT_STATE_NEUTRAL" for e in events),
               "unresolved_barriers": sum(e["status"] == "UNRESOLVED_STATE_TRANSITION" for e in events),
               "blocked_after_barrier": sum(e["status"] == "BLOCKED_BY_UNRESOLVED_STATE_TRANSITION" for e in events),
               "authoritative_resynchronizations": sum(e["status"] == "AUTHORITATIVE_RESYNCHRONIZATION" for e in events),
               "genuine_checkpoint_divergences": sum(e["status"] == "STATE_DIVERGENCE" for e in events)}
    return events, metrics


def materialize_instruction(row: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    tx = payload["body"]["result"]
    message, meta = tx["transaction"]["message"], tx["meta"]
    keys = message["accountKeys"] + meta.get("loadedAddresses", {}).get("writable", []) + meta.get("loadedAddresses", {}).get("readonly", [])
    header = message["header"]
    writable_end = len(keys) - header["numReadonlyUnsignedAccounts"]
    signer_end = header["numRequiredSignatures"]
    ix = next((item for item in message["instructions"] if keys[item["programIdIndex"]] == "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"), None)
    accounts = [] if ix is None else [{"pubkey": keys[index], "is_signer": index < signer_end, "is_writable": index < writable_end} for index in ix["accounts"]]
    return {"token": row["mint"], "signature": row["signature"], "slot": row["slot"], "transaction_ordinal": tx.get("transactionIndex"), "instruction_index": None,
            "discriminator": row.get("instruction_variant"), "ordered_account_metas": accounts,
            "pre_balances": meta.get("preBalances"), "post_balances": meta.get("postBalances"), "inner_instructions": meta.get("innerInstructions"),
            "cpi_programs": sorted({line.split()[1] for line in (meta.get("logMessages") or []) if line.startswith("Program ")}),
            "logs": meta.get("logMessages"), "provenance": row["source_artifact"], "unresolved_boundary_status": row["classification"] == "PARSE_INSUFFICIENT"}
