import importlib.util, json
from pathlib import Path

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
