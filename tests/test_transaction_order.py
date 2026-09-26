from src.ops.prospective_causal_retention import assess_prospective_candidate, witness_identity
from src.ops.transaction_order import ORDERING_VERSION, compare


def _candidate(**changes):
    record = {
        "nominated": True, "operation_id": "op", "mint": "future-mint",
        "root": "root", "coordinator": "coordinator", "lower_signature": "lower",
        "upper_signature": "upper", "slot": 55, "transaction_order": 7,
        "instruction_order": 2, "source_evidence_id": "evidence",
        "qualification_version": "v1",
    }
    record.update(changes)
    return record


def test_same_slot_uses_ordinal_not_signature_or_time():
    assert compare({'slot':1,'transaction_order':2,'signature':'z'},{'slot':1,'transaction_order':3,'signature':'a'})['state']=='QUALIFIED'
    assert compare({'slot':1,'transaction_order':4},{'slot':1,'transaction_order':3})['state']=='CONFLICT'


def test_same_slot_missing_ordinal_fails_closed_and_different_slot_needs_none():
    assert compare({'slot':1},{'slot':1})['state']=='INSUFFICIENT_EVIDENCE'
    assert compare({'slot':1},{'slot':2})['state']=='QUALIFIED'


def test_ordered_future_witness_enables_existing_generic_admission_only_after_bridge_qualifies():
    bridge = compare({"slot": 55, "transaction_order": 4}, {"slot": 55, "transaction_order": 7})
    assert bridge == {"state": "QUALIFIED", "reason": "earlier_transaction_ordinal"}
    assert assess_prospective_candidate(_candidate(), {("root", "coordinator")})["state"] == "QUALIFIED_PROSPECTIVE_MEMBER"
    assert compare({"slot": 55, "transaction_order": 8}, {"slot": 55, "transaction_order": 7})["state"] == "CONFLICT"


def test_compact_order_identity_is_restart_safe_idempotent_and_does_not_retain_raw_payload():
    evidence = _candidate(source_semantic_version=ORDERING_VERSION, raw_provider_response={"large": "ignored"})
    restarted = dict(evidence, raw_provider_response={"changed": "still ignored"})
    assert witness_identity(evidence) == witness_identity(restarted)
    assert assess_prospective_candidate(restarted, {("root", "coordinator")})["state"] == "QUALIFIED_PROSPECTIVE_MEMBER"

