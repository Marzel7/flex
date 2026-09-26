from src.ops.prospective_causal_retention import assess_prospective_candidate, member_committed_event, witness_identity
from src.ops.known_signature_slot import compare_slots
from src.ops.transaction_order import compare


def candidate(**extra):
    value={"nominated":True,"operation_id":"other-operation","mint":"candidate","root":"root","coordinator":"coordinator","lower_signature":"lower","upper_signature":"upper","slot":1,"transaction_order":2,"instruction_order":3,"source_evidence_id":"evidence","qualification_version":"v1"}; value.update(extra); return value


def test_positive_retained_witness_qualifies_and_is_idempotent():
    c=candidate(); anchors={("root","coordinator")}
    assert assess_prospective_candidate(c,anchors)["state"] == "QUALIFIED_PROSPECTIVE_MEMBER"
    assert witness_identity(c) == witness_identity(c)


def test_conflict_and_missing_witness_fail_closed():
    assert assess_prospective_candidate(candidate(selected_upstream_conflict=True),set())["state"] == "CONFLICT"
    assert assess_prospective_candidate(candidate(transaction_order=None),{("root","coordinator")})["state"] == "INSUFFICIENT_EVIDENCE"


def test_unknown_root_is_not_conflict_and_event_is_generic_post_commit_shape():
    assert assess_prospective_candidate(candidate(root="unknown"),set())["state"] == "INSUFFICIENT_EVIDENCE"
    event=member_committed_event("another-operation","m","membership","witness",1,"v1")
    assert event["event_version"] == "OPERATION_MEMBER_COMMITTED_V1" and event["operation_id"] == "another-operation"


def test_later_selected_parent_is_a_terminal_prospective_conflict():
    """Different-slot ordering enters the generic assessor as contradiction."""
    parent, child = {"slot": 450415455}, {"slot": 450414177}
    assert compare_slots(parent, child) == {"state": "PARENT_AFTER_CHILD", "getblock_allowed": False}
    assert compare(parent, child) == {"state": "CONFLICT", "reason": "parent_later_slot"}
    result = assess_prospective_candidate(candidate(selected_upstream_conflict=True), set())
    assert result == {"state": "CONFLICT", "reason": "selected_upstream_disagrees"}
