"""Compact, inert Helius webhook hints for DEV-019 treasury discovery.

This module is deliberately not wired into the API, ``ws_cascade``, a queue,
or a database.  A webhook delivery is an acceleration hint only: it lacks
complete selected-page coverage and independently verified balance deltas, so
it must never be promoted to a causal funding fact or an operation assignment.
``ws_cascade`` remains the sole reconciliation and cursor authority.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from queue import Full
from typing import Callable, Iterable, Mapping


WEBHOOK_HINT_ONLY = "WEBHOOK_HINT_ONLY"
HINT_EMITTED = "HINT_EMITTED"
HINT_DUPLICATE = "HINT_DUPLICATE"
HINT_DEFERRED = "HINT_DEFERRED"
MAX_HINTS_PER_PAYLOAD = 64
MAX_HINT_RECORD_BYTES = 2_048
MAX_HINT_BATCH_BYTES = 32_768


@dataclass(frozen=True)
class WebhookFundingHint:
    """One direct native-SOL transfer, indexed separately within a signature."""

    sender: str
    receiver: str
    signature: str
    transfer_ordinal: int
    lamports: int
    slot: int | None
    block_time: int | None
    provenance: str = "HELIUS_ENHANCED_WEBHOOK"
    coverage_status: str = WEBHOOK_HINT_ONLY

    def validate(self) -> None:
        if (not self.sender or not self.receiver or not self.signature
                or self.transfer_ordinal < 0 or self.lamports <= 0
                or self.coverage_status != WEBHOOK_HINT_ONLY):
            raise ValueError("INVALID_WEBHOOK_FUNDING_HINT")

    @property
    def idempotency_key(self) -> str:
        # The ordinal prevents two material transfers in one transaction from
        # collapsing into one signature-level record.
        return ":".join((self.signature, str(self.transfer_ordinal), self.sender,
                         self.receiver, str(self.lamports)))

    def compact_record(self) -> dict:
        self.validate()
        record = asdict(self)
        record["idempotency_key"] = self.idempotency_key
        if len(json.dumps(record, sort_keys=True, separators=(",", ":")).encode()) > MAX_HINT_RECORD_BYTES:
            raise ValueError("COMPACT_WEBHOOK_HINT_TOO_LARGE")
        return record


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        converted = int(value)  # Helius may encode some fields as strings.
    except (TypeError, ValueError):
        return None
    return converted if converted >= 0 else None


def extract_confirmed_treasury_outbound_hints(
    *, enhanced_transactions: Iterable[Mapping[str, object]], confirmed_treasuries: Iterable[str]
) -> list[WebhookFundingHint]:
    """Extract bounded outbound hints without retaining a raw webhook body.

    Malformed/unknown transfers are omitted, not interpreted as evidence of
    absence.  ``transfer_ordinal`` refers only to the enhanced payload's native
    transfer list; it is *not* an on-chain instruction coordinate.
    """
    confirmed = {str(address) for address in confirmed_treasuries if address}
    result: list[WebhookFundingHint] = []
    for transaction in enhanced_transactions:
        signature = transaction.get("signature")
        if not isinstance(signature, str) or not signature:
            continue
        slot = _as_int(transaction.get("slot"))
        block_time = _as_int(transaction.get("timestamp", transaction.get("blockTime")))
        transfers = transaction.get("nativeTransfers")
        if not isinstance(transfers, list):
            continue
        for ordinal, transfer in enumerate(transfers):
            if not isinstance(transfer, Mapping):
                continue
            sender = transfer.get("fromUserAccount", transfer.get("source"))
            receiver = transfer.get("toUserAccount", transfer.get("destination"))
            lamports = _as_int(transfer.get("amount"))
            if (not isinstance(sender, str) or not isinstance(receiver, str)
                    or sender not in confirmed or lamports is None or lamports == 0):
                continue
            hint = WebhookFundingHint(
                sender=sender, receiver=receiver, signature=signature,
                transfer_ordinal=ordinal, lamports=lamports, slot=slot, block_time=block_time,
            )
            hint.compact_record()
            result.append(hint)
            if len(result) > MAX_HINTS_PER_PAYLOAD:
                raise ValueError("WEBHOOK_HINT_COUNT_LIMIT_EXCEEDED")
    encoded = json.dumps([hint.compact_record() for hint in result], sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) > MAX_HINT_BATCH_BYTES:
        raise ValueError("WEBHOOK_HINT_BATCH_TOO_LARGE")
    return result


class WebhookHintDeduper:
    """Caller-owned in-memory idempotency guard; no persistence or I/O."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def accept(self, hint: WebhookFundingHint) -> bool:
        key = hint.idempotency_key
        if key in self._seen:
            return False
        self._seen.add(key)
        return True


def emit_nonblocking_webhook_hint(
    *, hint: WebhookFundingHint, deduper: WebhookHintDeduper,
    consumer: Callable[[dict], None],
) -> dict:
    """Offer a hint without blocking cascade or initiating provider work."""
    record = hint.compact_record()
    if not deduper.accept(hint):
        return {"status": HINT_DUPLICATE, "idempotency_key": hint.idempotency_key,
                "provider_calls": 0, "cascade_blocked": False}
    try:
        consumer(record)
    except (Full, Exception):
        return {"status": HINT_DEFERRED, "idempotency_key": hint.idempotency_key,
                "provider_calls": 0, "cascade_blocked": False}
    return {"status": HINT_EMITTED, "idempotency_key": hint.idempotency_key,
            "provider_calls": 0, "cascade_blocked": False}
