"""Forward wrapper for the frozen Byzantine Actual-Entry Opening contract.

Selection, selected-event identity evidence, and containing-candle valuation are
injected capabilities.  This module deliberately neither owns transport nor
selects a replacement event after an identity failure.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping

ADAPTER = "BYZANTINE_ACTUAL_ENTRY_V2"
MAX_HELIUS_CALLS_PER_TOKEN = 10
MAX_ADDITIONAL_IDENTITY_CALLS_PER_TOKEN = 1
MAX_BIRDEYE_ENTRY_CALLS_PER_TOKEN = 1


def execute(assignment: Mapping[str, object], *, select: Callable, identity: Callable,
            valuation: Callable) -> dict[str, object]:
    """Return an existing QualifiedOpening-shaped result or visible fail-closed state."""
    mint = str(assignment.get("mint") or "")
    if not mint or not assignment.get("event_id"):
        return {"qualified": False, "state": "FAIL_CLOSED_ASSIGNMENT_INCOMPLETE"}
    selected = select(dict(assignment))
    if not isinstance(selected, Mapping):
        return {"qualified": False, "state": "FAIL_CLOSED_NO_QUALIFYING_TRADE"}
    frozen = {key: selected.get(key) for key in ("signature", "slot", "timestamp")}
    if not frozen["signature"] or frozen["slot"] is None or frozen["timestamp"] is None:
        return {"qualified": False, "state": "FAIL_CLOSED_SELECTED_EVENT_INCOMPLETE"}
    proof = identity(dict(frozen), mint)
    if not isinstance(proof, Mapping) or proof.get("state") != "MATCH":
        return {"qualified": False, "state": "FAIL_CLOSED_EVENT_MINT_" + str((proof or {}).get("state") or "UNPROVEN"), "selected": frozen}
    candle = valuation(dict(frozen), mint)
    if not isinstance(candle, Mapping) or candle.get("c") is None:
        return {"qualified": False, "state": "FAIL_CLOSED_NO_CONTAINING_CANDLE", "selected": frozen}
    return {
        "qualified": True, "operation_id": "byzantine", "mint": mint,
        "timestamp": int(frozen["timestamp"]), "mc_usd": float(candle["c"]),
        "entry_reference_state": "QUALIFIED", "adapter": ADAPTER,
        "selected": frozen, "event_mint_proof": dict(proof),
        "provenance": {"adapter": ADAPTER, "selected_signature": frozen["signature"], "selected_slot": frozen["slot"]},
    }


EXECUTABLE_ADAPTERS = {ADAPTER: execute}
