"""Provider-free DEV-019 webhook-hint contracts; intentionally no HTTP/I/O."""
from queue import Full

import pytest

from src.ops.treasury_webhook_hints import (
    HINT_DEFERRED, HINT_DUPLICATE, HINT_EMITTED, WEBHOOK_HINT_ONLY,
    WebhookHintDeduper, emit_nonblocking_webhook_hint,
    extract_confirmed_treasury_outbound_hints,
)


TREASURY = "confirmed-treasury"


def _payload():
    return [{
        "signature": "signature-1", "slot": 42, "timestamp": 1700000000,
        "nativeTransfers": [
            {"fromUserAccount": TREASURY, "toUserAccount": "recipient-a", "amount": 10},
            {"fromUserAccount": TREASURY, "toUserAccount": "recipient-b", "amount": 20},
            {"fromUserAccount": "unconfirmed", "toUserAccount": TREASURY, "amount": 30},
        ],
    }]


def test_successful_enhanced_parsing_preserves_each_transfer_per_signature():
    hints = extract_confirmed_treasury_outbound_hints(
        enhanced_transactions=_payload(), confirmed_treasuries=[TREASURY]
    )
    assert [(hint.receiver, hint.transfer_ordinal, hint.lamports) for hint in hints] == [
        ("recipient-a", 0, 10), ("recipient-b", 1, 20),
    ]
    assert len({hint.idempotency_key for hint in hints}) == 2
    assert all(hint.coverage_status == WEBHOOK_HINT_ONLY for hint in hints)
    assert "nativeTransfers" not in hints[0].compact_record()


def test_duplicate_delivery_is_signature_and_transfer_idempotent_not_signature_collapsed():
    hints = extract_confirmed_treasury_outbound_hints(
        enhanced_transactions=_payload(), confirmed_treasuries=[TREASURY]
    )
    deduper, received = WebhookHintDeduper(), []
    assert emit_nonblocking_webhook_hint(hint=hints[0], deduper=deduper, consumer=received.append)["status"] == HINT_EMITTED
    assert emit_nonblocking_webhook_hint(hint=hints[1], deduper=deduper, consumer=received.append)["status"] == HINT_EMITTED
    assert emit_nonblocking_webhook_hint(hint=hints[0], deduper=deduper, consumer=received.append)["status"] == HINT_DUPLICATE
    assert len(received) == 2


def test_queue_full_defers_without_blocking_or_provider_calls():
    hint = extract_confirmed_treasury_outbound_hints(
        enhanced_transactions=_payload(), confirmed_treasuries=[TREASURY]
    )[0]
    result = emit_nonblocking_webhook_hint(
        hint=hint, deduper=WebhookHintDeduper(), consumer=lambda _: (_ for _ in ()).throw(Full())
    )
    assert result["status"] == HINT_DEFERRED
    assert result["provider_calls"] == 0
    assert result["cascade_blocked"] is False


def test_webhook_only_hints_reject_malformed_data_and_cannot_be_causal_facts():
    assert extract_confirmed_treasury_outbound_hints(
        enhanced_transactions=[{"signature": "bad", "nativeTransfers": [{"fromUserAccount": TREASURY, "amount": 1}]}],
        confirmed_treasuries=[TREASURY],
    ) == []
    hint = extract_confirmed_treasury_outbound_hints(
        enhanced_transactions=_payload(), confirmed_treasuries=[TREASURY]
    )[0]
    record = hint.compact_record()
    assert record["coverage_status"] == WEBHOOK_HINT_ONLY
    # A hint intentionally has no verified deltas or transaction/instruction coordinates.
    assert "sender_balance_delta" not in record
    assert "instruction_index" not in record


def test_per_payload_bound_fails_closed():
    tx = _payload()[0]
    tx["nativeTransfers"] = [
        {"fromUserAccount": TREASURY, "toUserAccount": f"recipient-{index}", "amount": 1}
        for index in range(65)
    ]
    with pytest.raises(ValueError, match="WEBHOOK_HINT_COUNT_LIMIT_EXCEEDED"):
        extract_confirmed_treasury_outbound_hints(enhanced_transactions=[tx], confirmed_treasuries=[TREASURY])
