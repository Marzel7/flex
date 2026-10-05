"""Operation-selected entry-reference policies over generic opening evidence.

The registry is keyed by capability policy name, never by operation ID.  It
contains no transport, queue, raw-provider retention, or Monitor mutation.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any, Callable, Mapping

from .byzantine_live_cluster_recurrence import classify
from .operation_strategy_trigger_producer import evaluate_byzantine_scenario_d
from .pre_action_state_comparison import pre_action_state_digest

ENTRY_REFERENCE_POLICY_ADAPTER_VERSION = "ENTRY_REFERENCE_POLICY_ADAPTER_V1"
SCENARIO_D_FX_DEPENDENCY_POLICY = "REQUIRED_FOR_USD_ATTACHMENT_AFTER_NATIVE_QUALIFICATION"
SCENARIO_D_FX_ATTACHMENT_CONTRACT_VERSION = "SCENARIO_D_NATIVE_ENTRY_FX_ATTACHMENT_V1"
PRE_ACTION_STATE_RESOLVER_VERSION = "pumpfun-trade-event-inverse-transition.v1"
_PUMP_SUPPLY_RAW = Decimal("1000000000000000")
_LAMPORTS_PER_SOL = Decimal("1000000000")


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def inverse_transition_boundary(action: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return an exact pre-action FDV-in-SOL boundary from compact evidence.

    This is the historical Scenario-D rule promoted into the generic contract:
    event post-reserves plus integer transition deltas are inverted before the
    fixed hypothetical 0.25 SOL quote.  There is no post-state substitution.
    """
    post = action.get("event_post_state") or {}
    delta = action.get("event_transition_delta") or {}
    required = {"post_virtual_sol_reserves", "post_virtual_token_reserves", "post_real_sol_reserves", "post_real_token_reserves"}
    if set(post) != required or set(delta) != {"sol_amount", "token_amount"} or action.get("action_type") not in {"BUY", "SELL"}:
        return None
    try:
        sol, token = int(delta["sol_amount"]), int(delta["token_amount"])
        if sol < 0 or token < 0:
            return None
        direction = -1 if action["action_type"] == "BUY" else 1
        pre = {"virtual_sol_reserves": int(post["post_virtual_sol_reserves"]) + direction * sol,
               "virtual_token_reserves": int(post["post_virtual_token_reserves"]) - direction * token,
               "real_sol_reserves": int(post["post_real_sol_reserves"]) + direction * sol,
               "real_token_reserves": int(post["post_real_token_reserves"]) - direction * token}
        pre_sol, pre_token = pre["virtual_sol_reserves"], pre["virtual_token_reserves"]
        if any(value < 0 for value in pre.values()) or pre_token <= 0:
            return None
        from .pumpfun_execution_model_target_era import CurveState, buy_exact_sol_in
        quote = buy_exact_sol_in(CurveState(pre_sol, pre_token), 250_000_000)
        if quote.token_delta <= 0:
            return None
        entry_sol = (Decimal(quote.user_sol_total) * _PUMP_SUPPLY_RAW /
                     Decimal(quote.token_delta) / _LAMPORTS_PER_SOL)
    except (TypeError, ValueError, ArithmeticError):
        return None
    compact = {"post": dict(post), "delta": dict(delta), "signature": action.get("signature"),
               "slot": action.get("slot"), "transaction_index": action.get("transaction_index"),
               "action_index": action.get("action_index")}
    post_digest = _digest(dict(post)); pre_digest = pre_action_state_digest(pre)
    return {"semantic": "PRE_ACTION_STATE", "ref": _digest(compact), "entry_mc_sol": str(entry_sol),
            "timestamp": action.get("timestamp"), "decoded": pre, "parser_version": PRE_ACTION_STATE_RESOLVER_VERSION,
            "post_state_digest": post_digest, "transition_delta": dict(delta), "pre_state_digest": pre_digest,
            "provenance": "INVERSE_OF_RETAINED_TRADE_EVENT_POST_RESERVES_AND_INTEGER_DELTA"}


def _scenario_d(projection: Mapping[str, Any], *, classifier: Callable[[Mapping[str, Any]], Mapping[str, Any]],
                boundary_states: Mapping[tuple[str, int], Mapping[str, Any]],
                fx: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Evaluate the retained 13th distinct non-creator BUY boundary.

    The frozen 12-wallet opening-cluster classifier is a separate historical
    recurrence projection.  It is not the predicate that selected Scenario-D:
    the retained counterfactual implementation selected the thirteenth
    *distinct non-creator BUY* in slot/transaction/action order.  Keep the
    classifier dependency in this generic adapter signature for capability
    compatibility, but deliberately do not use it to redefine that boundary.
    """
    distinct_buys = []
    seen_actors = set()
    for action in projection.get("ordered_actions", []):
        actor = action.get("actor")
        if (action.get("action_type") != "BUY" or
                action.get("creator_relationship") == "CREATOR" or
                not isinstance(actor, str) or not actor or actor in seen_actors):
            continue
        seen_actors.add(actor)
        distinct_buys.append(action)
    if len(distinct_buys) < 13:
        return {"state": "INSUFFICIENT_EVIDENCE", "reason": "SCENARIO_D_DISTINCT_NON_CREATOR_POSITION_NOT_REACHED"}
    action = distinct_buys[12]
    if action.get("creator_relationship") == "CREATOR":
        return {"state": "INSUFFICIENT_EVIDENCE", "reason": "SCENARIO_D_POSITION_13_CREATOR_ACTION"}
    key = (str(action.get("signature")), int(action.get("action_index") or 0))
    boundary = boundary_states.get(key) or inverse_transition_boundary(action)
    if not boundary or boundary.get("semantic") != "PRE_ACTION_STATE" or boundary.get("entry_mc_sol") is None:
        return {"state": "DEPENDENCY_BLOCKED", "reason": "SCENARIO_D_PRE_ACTION_STATE_REQUIRED"}
    evidence = {"operation_id": "byzantine", "mint": projection["mint"],
                "cluster_id": "HISTORICAL_DISTINCT_NON_CREATOR_BUY_ORDER",
                "opening_positions": list(range(1, 13)), "recurrent_cluster_completed": True,
                "signature": action["signature"], "slot": int(action["slot"]),
                "event_index": int(action["action_index"]), "entry_state_reference": boundary.get("ref"),
                "position": 13}
    trigger = evaluate_byzantine_scenario_d(evidence)
    if trigger["result"] != "TRIGGER_QUALIFIED":
        return {"state": "INSUFFICIENT_EVIDENCE", "reason": trigger["result"]}
    result = {"state": "QUALIFIED_NATIVE", "entry_method": "SCENARIO_D_ACTIONABLE_COUNTERFACTUAL_ENTRY_FLOOR",
              "entry_timestamp": int(boundary.get("timestamp") or projection["create_timestamp"]),
              "entry_native_mc_sol": str(boundary["entry_mc_sol"]),
              "entry_exactness": "SCENARIO_D_12_13_COMMITTED", "source_signature": action["signature"],
              "slot": int(action["slot"]), "transaction_index": action.get("transaction_index"),
              "action_index": int(action["action_index"]), "boundary_state_reference": boundary["ref"],
              "policy_version": "SCENARIO_D_POLICY_ADAPTER_V1", "trigger_boundary": trigger["boundary"]}
    result["provenance_digest"] = _digest(result)
    return attach_fx(result, fx) if fx is not None else result


def attach_fx(native_entry: Mapping[str, Any], fx: Mapping[str, Any] | None) -> dict[str, Any]:
    """Attach a qualified historical SOL/USD fact without reopening policy.

    A native entry remains durable and valid while the shared FX dependency is
    blocked, rate-limited, or insufficient.  USD is never invented.
    """
    result = dict(native_entry)
    if result.get("state") != "QUALIFIED_NATIVE":
        return result
    if not fx or fx.get("state") != "QUALIFIED" or fx.get("sol_usd_price") is None:
        result.update({"fx_state": (fx or {}).get("state", "PENDING"),
                       "fx_attachment_state": "PENDING_OR_UNAVAILABLE",
                       "fx_attachment_contract": SCENARIO_D_FX_ATTACHMENT_CONTRACT_VERSION})
        result["provenance_digest"] = _digest(result)
        return result
    result.update({"state": "QUALIFIED", "entry_mc_usd": float(Decimal(str(result["entry_native_mc_sol"])) * Decimal(str(fx["sol_usd_price"]))),
                   "fx_state": "QUALIFIED", "fx_reference": fx.get("ref"),
                   "fx_attachment_contract": SCENARIO_D_FX_ATTACHMENT_CONTRACT_VERSION})
    result["provenance_digest"] = _digest(result)
    return result


_ADAPTERS = {"SCENARIO_D": _scenario_d}


def evaluate(policy: str, projection: Mapping[str, Any], **dependencies: Any) -> dict[str, Any]:
    adapter = _ADAPTERS.get(policy)
    if not adapter:
        return {"state": "INSUFFICIENT_EVIDENCE", "reason": "ENTRY_REFERENCE_POLICY_UNSUPPORTED"}
    return adapter(projection, **dependencies)


def policy_names() -> tuple[str, ...]:
    return tuple(sorted(_ADAPTERS))
