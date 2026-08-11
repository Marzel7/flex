import importlib.util, json
from pathlib import Path
import pytest

SPEC=importlib.util.spec_from_file_location("orchestrator", Path(__file__).parents[1]/"scripts/agent_orchestrator.py")
mod=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(mod)

def action(kind="LOCAL_TEST"):
    return {"milestone":"T", "authorization_class":kind, "instruction":"test", "success_criteria":"pass", "stop_conditions":"stop", "source_handoff_revision":"abc", "approved":True}
def state(kind="LOCAL_TEST"):
    return {"orchestration":{"schema_version":1,"state":"READY_FOR_CODEX","run_id":None,"approved_action":action(kind)}}
def test_only_explicit_safe_approval_is_accepted():
    _, approved=mod.approved(state()); assert approved["authorization_class"] == "LOCAL_TEST"
def test_provider_and_production_are_fail_closed():
    for kind in ("PRODUCTION_MUTATION","PROVIDER_WORK_INCREASE","PRODUCTION_OBSERVATION"):
        try: mod.approved(state(kind))
        except mod.Rejected: pass
        else: raise AssertionError(kind)
def test_invalid_transition_rejected():
    try: mod.transition(state(), "READY_FOR_REVIEW")
    except mod.Rejected: pass
    else: raise AssertionError("transition accepted")

class Fake:
    def __init__(self,v): self.v=v; self.calls=0
    def review(self,c): self.calls+=1; return self.v,{"store":False,"response_status":200,"model":"fake"}
def decision(kind="LOCAL_TEST", **changes):
    v={"decision":"APPROVE_NEXT_LOCAL_ACTION","authorization_class":kind,"next_action":"PAUSED_OIP_E_SERIES harmless local test","reason":"safe","human_question":"","scope_change":False,"production_change":False,"provider_work_increase":False}; v.update(changes); return v
def ready():
    d=state(); d["orchestration"]["state"]="READY_FOR_REVIEW"; d["next_action"]={"milestone":"PAUSED_OIP_E_SERIES"}; return d
def test_reviewer_safe_transitions_ready_and_store_false():
    f=Fake(decision()); assert mod.review_once(ready(),f,"r") == "READY_FOR_CODEX"; assert f.calls==1
def test_risky_reviewer_decisions_are_human_gated():
    for v in (decision("PRODUCTION_MUTATION"),decision("PROVIDER_WORK_INCREASE"),decision("PRODUCTION_OBSERVATION"),decision("LOCAL_TEST",scope_change=True),decision("LOCAL_TEST",production_change=True)):
        assert mod.policy(v,"PAUSED_OIP_E_SERIES") == "HUMAN_APPROVAL_REQUIRED"
def test_malformed_and_duplicate_review_fail_closed():
    try: mod.validate_review({})
    except mod.Rejected: pass
    else: raise AssertionError("malformed accepted")
    d=ready(); d["orchestration"]["review_id"]="used"
    try: mod.review_once(d,Fake(decision()),"r")
    except mod.Rejected: pass
    else: raise AssertionError("duplicate accepted")

@pytest.mark.parametrize("kind,changes,expected", [
    ("LOCAL_READ_ONLY",{},"READY_FOR_CODEX"), ("LOCAL_IMPLEMENTATION",{},"READY_FOR_CODEX"),
    ("LOCAL_COMMIT",{},"READY_FOR_CODEX"), ("PRODUCTION_MUTATION",{},"HUMAN_APPROVAL_REQUIRED"),
    ("PROVIDER_WORK_INCREASE",{},"HUMAN_APPROVAL_REQUIRED"), ("PRODUCTION_OBSERVATION",{},"HUMAN_APPROVAL_REQUIRED"),
    ("ARCHITECTURAL_DECISION",{},"HUMAN_APPROVAL_REQUIRED"), ("LOCAL_TEST",{"provider_work_increase":True},"HUMAN_APPROVAL_REQUIRED"),
    ("LOCAL_TEST",{"production_change":True},"HUMAN_APPROVAL_REQUIRED"), ("LOCAL_TEST",{"scope_change":True},"HUMAN_APPROVAL_REQUIRED"),
])
def test_policy_matrix(kind,changes,expected):
    assert mod.policy(decision(kind,**changes),"PAUSED_OIP_E_SERIES") == expected

def test_unknown_class_and_stale_revision_fail_closed():
    v=decision(); v["authorization_class"]="UNKNOWN"
    with pytest.raises(mod.Rejected): mod.validate_review(v)
    d=ready(); d["orchestration"]["source_handoff_revision"]="expected"
    with pytest.raises(mod.Rejected): mod.review_once(d,Fake(decision()),"different")

def test_real_client_requires_env_without_leaking_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY",raising=False); monkeypatch.setenv("ORCHESTRATOR_REVIEW_MODEL","test")
    with pytest.raises(mod.Rejected,match="OPENAI_API_KEY_NOT_VISIBLE_TO_ORCHESTRATOR"): mod.ResponsesReviewer().review({})
