import pytest
from src.ops.manual_review_admission_contract import authorize_request, review_state_token


def row(**overrides):
    value={"operation_id":"deep","mint":"mint","candidate_id":"candidate","review_outcome_id":"review","assessment_id":"assessment","assessment_semantic_version":"v1","policy_id":"policy","policy_version":"v1","admission_result":"REVIEW"}
    return {**value,**overrides}


def admin(name="approver"):
    return {"id":name,"authenticated":True,"permissions":{"operations.deep.review.approve"}}


def test_approve_is_state_bound_and_durable_admit_only():
    result=authorize_request(principal=admin(),row=row(),supplied_token=review_state_token(row()),action="APPROVE",membership_exists=False)
    assert result["result"]=="APPROVED" and result["admission"]["admission_result"]=="ADMIT"


def test_reject_insufficient_pending_and_stale_fail_closed():
    for state in ("REJECT","INSUFFICIENT_EVIDENCE","PENDING"):
        with pytest.raises(ValueError): review_state_token(row(admission_result=state))
    with pytest.raises(ValueError,match="STALE"):
        authorize_request(principal=admin(),row=row(),supplied_token="old",action="APPROVE",membership_exists=False)


def test_unauthorized_and_canonical_race_never_create_admit():
    with pytest.raises(PermissionError): authorize_request(principal={"id":"viewer","authenticated":True,"permissions":set()},row=row(),supplied_token=review_state_token(row()),action="APPROVE",membership_exists=False)
    assert authorize_request(principal=admin(),row=row(),supplied_token=review_state_token(row()),action="APPROVE",membership_exists=True)["result"]=="ALREADY_CANONICAL"


def test_replay_and_approver_identity_are_deterministic_and_decline_is_distinct():
    one=authorize_request(principal=admin(),row=row(),supplied_token=review_state_token(row()),action="APPROVE",membership_exists=False)
    two=authorize_request(principal=admin(),row=row(),supplied_token=review_state_token(row()),action="APPROVE",membership_exists=False)
    assert one["approval_id"]==two["approval_id"]
    decline=authorize_request(principal=admin("other"),row=row(),supplied_token=review_state_token(row()),action="DECLINE",membership_exists=False)
    assert decline["result"]=="DECLINED" and decline["admission"] is None
