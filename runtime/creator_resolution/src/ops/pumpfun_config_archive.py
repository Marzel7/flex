"""Bounded true-historical Pump.fun configuration acquisition.

This module deliberately rejects ``minContextSlot``.  Alchemy Account Archive's
``slot`` and ``firstUpdateAfterSlot`` extensions are the only supported sources
because they identify account *data at a historical point*, not node context.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

PUMP_GLOBAL = "4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf"
PUMP_FEE_CONFIG = "8Wf5TiAheLUqBrKXeYg2JtAFFMWtKdG2BSFgqUcPVwTt"
EARLIEST_REQUIRED_SLOT = 438_962_813
LATEST_REQUIRED_SLOT = 444_335_877
MAX_HISTORY_RESPONSES_PER_ACCOUNT = 16
MAX_UPDATE_RESPONSES_PER_ACCOUNT = MAX_HISTORY_RESPONSES_PER_ACCOUNT - 3
MAX_SIGNATURE_PAGES_PER_ACCOUNT = 0
MAX_SIGNATURES_PER_ACCOUNT = 0
MAX_TRANSACTION_LOOKUPS = 0
MAX_TOTAL_PROVIDER_CALLS = 2 * MAX_HISTORY_RESPONSES_PER_ACCOUNT


class HistoricalConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Request:
    account: str
    config: dict[str, Any]
    kind: str


def snapshot_request(account: str, slot: int = EARLIEST_REQUIRED_SLOT) -> Request:
    if slot < EARLIEST_REQUIRED_SLOT or slot > LATEST_REQUIRED_SLOT:
        raise HistoricalConfigError("SNAPSHOT_SLOT_OUT_OF_CONTRACT")
    return Request(account, {"encoding": "base64", "slot": slot}, "SNAPSHOT")


def snapshot_slots() -> tuple[int, int, int]:
    """Inclusive start/end and deterministic floor midpoint of the frozen range."""
    return (EARLIEST_REQUIRED_SLOT, (EARLIEST_REQUIRED_SLOT + LATEST_REQUIRED_SLOT) // 2,
            LATEST_REQUIRED_SLOT)


def next_update_request(account: str, cursor: int) -> Request:
    if cursor < EARLIEST_REQUIRED_SLOT:
        raise HistoricalConfigError("HISTORY_CURSOR_OUT_OF_CONTRACT")
    return Request(account, {"encoding": "base64", "firstUpdateAfterSlot": cursor}, "NEXT_UPDATE")


def _context_slot(response: Mapping[str, Any]) -> int:
    result = response.get("result")
    if not isinstance(result, Mapping):
        raise HistoricalConfigError("MALFORMED_ARCHIVE_RESPONSE")
    context = result.get("context")
    if not isinstance(context, Mapping) or not isinstance(context.get("slot"), int):
        raise HistoricalConfigError("MISSING_ARCHIVE_CONTEXT_SLOT")
    return int(context["slot"])


def acquire_snapshots(
    *, account: str, rpc: Callable[[str, list[Any]], Mapping[str, Any]],
    before_dispatch: Callable[[Request], None], retain_raw: Callable[[Request, Mapping[str, Any]], None],
    checkpoint: Callable[[Request, str], None],
) -> list[tuple[Request, Mapping[str, Any]]]:
    """Acquire the exact start, midpoint, and end archive snapshots."""
    responses: list[tuple[Request, Mapping[str, Any]]] = []
    for slot in snapshot_slots():
        request = snapshot_request(account, slot)
        before_dispatch(request)
        response = rpc("getAccountInfo", [request.account, request.config])
        retain_raw(request, response)
        returned_slot = _context_slot(response)
        if returned_slot != slot:
            checkpoint(request, "INTEGRITY_RETURNED_SLOT_MISMATCH")
            raise HistoricalConfigError("SNAPSHOT_RETURNED_SLOT_MISMATCH")
        checkpoint(request, "RAW_RETAINED")
        responses.append((request, response))
    return responses


def discover_updates(
    *, account: str, rpc: Callable[[str, list[Any]], Mapping[str, Any]],
    before_dispatch: Callable[[Request], None], retain_raw: Callable[[Request, Mapping[str, Any]], None],
    checkpoint: Callable[[Request, str], None],
) -> list[tuple[Request, Mapping[str, Any]]]:
    """Discover only updates in the frozen range after a snapshot byte change.

    ``before_dispatch`` must durably account the physical request.  Raw response
    retention precedes all response interpretation and the subsequent checkpoint.
    """
    responses: list[tuple[Request, Mapping[str, Any]]] = []
    cursor = EARLIEST_REQUIRED_SLOT
    for _ in range(MAX_UPDATE_RESPONSES_PER_ACCOUNT):
        request = next_update_request(account, cursor)
        before_dispatch(request)
        response = rpc("getAccountInfo", [request.account, request.config])
        retain_raw(request, response)
        # Alchemy documents -32020 as the clean terminal for a cursor walk.
        if isinstance(response.get("error"), Mapping) and response["error"].get("code") == -32020:
            checkpoint(request, "HISTORY_COMPLETE")
            return responses
        returned_slot = _context_slot(response)
        if returned_slot <= cursor:
            checkpoint(request, "INTEGRITY_NON_MONOTONIC_HISTORY")
            raise HistoricalConfigError("NON_MONOTONIC_HISTORY_CURSOR")
        checkpoint(request, "RAW_RETAINED")
        if returned_slot > LATEST_REQUIRED_SLOT:
            return responses
        responses.append((request, response))
        cursor = returned_slot
    raise HistoricalConfigError("HISTORY_RESPONSE_BUDGET_EXHAUSTED")


def acquire_target_range(**kwargs: Any) -> dict[str, list[tuple[Request, Mapping[str, Any]]]]:
    """Compatibility helper: snapshots first, then cursors for both accounts."""
    return {"global": acquire_snapshots(account=PUMP_GLOBAL, **kwargs) + discover_updates(account=PUMP_GLOBAL, **kwargs),
            "fee_config": acquire_snapshots(account=PUMP_FEE_CONFIG, **kwargs) + discover_updates(account=PUMP_FEE_CONFIG, **kwargs)}
