from src.ops.known_signature_slot import SLOT_VERSION, compact_slot_record, compare_slots, slot_evidence_identity


def _response(signature="child", slot=11):
    return {"result": {"slot": slot, "transaction": {"signatures": [signature]}, "unneeded": "discarded"}}


def test_known_signature_compacts_authoritative_slot_without_raw_response():
    result = compact_slot_record("child", _response(), source_identity="solana-rpc-getTransaction-v1", acquisition_identity="r1", acquired_at=1)
    assert result["state"] == "QUALIFIED"
    assert result["record"]["semantic_version"] == SLOT_VERSION
    assert "unneeded" not in result["record"]


def test_missing_signature_and_null_provider_result_fail_closed():
    assert compact_slot_record("other", _response(), source_identity="rpc", acquisition_identity="r1", acquired_at=1)["state"] == "INSUFFICIENT_EVIDENCE"
    assert compact_slot_record("child", {"result": None}, source_identity="rpc", acquisition_identity="r1", acquired_at=1)["state"] == "INSUFFICIENT_EVIDENCE"


def test_slot_identity_is_idempotent_restart_safe_and_operation_agnostic():
    first = compact_slot_record("child", _response(), source_identity="rpc", acquisition_identity="r1", acquired_at=1)["record"]
    restarted = dict(first, acquired_at=99, operation_id="another-operation")
    assert slot_evidence_identity(first) == slot_evidence_identity(restarted)


def test_two_stage_slot_rule_only_allows_getblock_for_proven_same_slot():
    assert compare_slots({"slot": 1}, {"slot": 2}) == {"state": "PARENT_BEFORE_CHILD", "getblock_allowed": False}
    assert compare_slots({"slot": 2}, {"slot": 1}) == {"state": "PARENT_AFTER_CHILD", "getblock_allowed": False}
    assert compare_slots({"slot": 1}, {"slot": 1}) == {"state": "SAME_SLOT", "getblock_allowed": True}
    assert compare_slots({"slot": 1}, {}) == {"state": "UNKNOWN", "getblock_allowed": False}

