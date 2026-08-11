import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
import pytest

SPEC = importlib.util.spec_from_file_location("orchestrator", Path(__file__).parents[1] / "scripts/agent_orchestrator.py")
mod = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(mod)

def action(kind="LOCAL_TEST"):
    return {"approved": True, "milestone": "SCOPE", "authorization_class": kind, "instruction": "test", "success_criteria": "pass", "stop_conditions": "stop", "source_handoff_revision": "rev"}
def state(kind="LOCAL_TEST"):
    return {"orchestration": {"schema_version": 2, "state": "READY_FOR_CODEX", "run_id": None, "attempt": 0, "approved_action": action(kind)}}
def child():
    return {"status": "COMPLETE", "verdict": "PASS", "files_changed": ["x"], "tests": ["ok"], "blockers": [], "production_impact": False, "provider_impact": False, "commits": [], "recommended_next_action": "SCOPE continue"}
def decision(kind="LOCAL_TEST", **kw):
    value = {"decision": "APPROVE_NEXT_LOCAL_ACTION", "authorization_class": kind, "next_action": "SCOPE local test", "reason": "safe", "human_question": "", "scope_change": False, "production_change": False, "provider_work_increase": False}; value.update(kw); return value
class Reviewer:
    def review(self, context): return decision(), {"store": False}

def test_reviewer_and_git_transport_are_host_owned():
    source = Path(mod.__file__).read_text()
    assert "class ResponsesReviewer" in source and "def publish_handoff" in source
    assert "Responses API" in mod.child_prompt(action()) and "fetch/push Git" in mod.child_prompt(action())
def test_child_prompt_forbids_network_review_and_handoff():
    prompt = mod.child_prompt(action())
    for phrase in ("MUST NOT use network", "Responses API", "fetch/push Git", "update/publish the agent-handoff"): assert phrase in prompt
def test_structured_child_result_captured_and_ready_for_review():
    data = state(); result = mod.execute_one(data, 3, 2, lambda a, t: child())
    assert result["verdict"] == "PASS" and data["orchestration"]["last_child_result"] == result
    assert data["orchestration"]["state"] == "READY_FOR_REVIEW"
def test_mock_reviewer_approves_local_test_and_continues():
    data = state(); mod.execute_one(data, 1, 2, lambda a, t: child())
    assert mod.review_once(data, Reviewer(), "rev") == "READY_FOR_CODEX"
def test_host_loop_owns_ready_review_and_transport():
    class StopReviewer:
        def review(self, context): return decision(decision="HUMAN_APPROVAL_REQUIRED"), {"store": False}
    revisions = iter(["ready-rev", "reviewed-rev"]); published = []
    def publisher(data, expected, message): published.append((data["orchestration"]["state"], expected)); return next(revisions)
    data = state()
    assert mod.run_host_loop(data, 1, 2, StopReviewer(), lambda a, t: child(), publisher) == "HUMAN_APPROVAL_REQUIRED"
    assert published == [("READY_FOR_REVIEW", "rev"), ("HUMAN_APPROVAL_REQUIRED", "ready-rev")]
@pytest.mark.parametrize("kind", ["PRODUCTION_MUTATION", "PROVIDER_WORK_INCREASE"])
def test_hard_policy_stops_risky_classes(kind):
    assert mod.policy(decision(kind), "SCOPE") == "HUMAN_APPROVAL_REQUIRED"
    with pytest.raises(mod.Rejected): mod.validate_action(state(kind))
def test_handoff_optimistic_concurrency(monkeypatch):
    monkeypatch.setattr(mod, "git", lambda *a, **k: SimpleNamespace(stdout="different\n"))
    with pytest.raises(mod.Rejected, match="HANDOFF_CONFLICT"): mod.publish_handoff({}, "expected", "msg")
def test_child_timeout_and_failure_are_bounded(tmp_path):
    def timeout(*a, **k): raise subprocess.TimeoutExpired("codex", 1)
    with pytest.raises(mod.Rejected, match="CHILD_TIMEOUT"): mod.run_child(action(), 1, runner=timeout, executable="codex")
    def failed(*a, **k): return SimpleNamespace(returncode=9)
    with pytest.raises(mod.Rejected, match="CHILD_FAILED:9"): mod.run_child(action(), 1, runner=failed, executable="codex")
def test_iteration_cap_is_bounded():
    data = state(); data["orchestration"]["attempt"] = 2
    with pytest.raises(mod.Rejected, match="ITERATION_CAP_REACHED"): mod.execute_one(data, 1, 2, lambda a, t: child())
def test_duplicate_or_stale_run_rejected():
    data = state(); data["orchestration"]["run_id"] = "existing"
    with pytest.raises(mod.Rejected, match="DUPLICATE_OR_STALE_RUN"): mod.validate_action(data)
def test_api_key_and_secrets_not_passed_to_child(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "secret"); monkeypatch.setenv("OTHER_TOKEN", "token")
    captured = {}
    def runner(args, **kw):
        captured.update(kw); Path(args[args.index("-o") + 1]).write_text(json.dumps(child())); return SimpleNamespace(returncode=0)
    mod.run_child(action(), 1, runner=runner, executable="codex")
    assert "OPENAI_API_KEY" not in captured["env"] and "OTHER_TOKEN" not in captured["env"]
    assert "secret" not in mod.child_prompt(action()) and "token" not in mod.child_prompt(action())
def test_default_is_disabled(capsys):
    assert mod.main(["host-smoke-live"]) == 2
    assert "ORCHESTRATOR_DISABLED" in capsys.readouterr().err
def test_host_lock_rejects_concurrent_run(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(mod, "LOCK", tmp_path / "lock")
    monkeypatch.setattr(mod.fcntl, "flock", lambda *a: (_ for _ in ()).throw(BlockingIOError()))
    assert mod.main(["--enable", "host-smoke-live"]) == 2
    assert "CONCURRENT_RUN" in capsys.readouterr().err
def test_synthetic_host_smoke_never_invokes_child(monkeypatch):
    monkeypatch.setattr(mod, "run_child", lambda *a: (_ for _ in ()).throw(AssertionError("child invoked")))
    class SmokeReviewer:
        def review(self, context): return decision(next_action="HOST_SMOKE local test"), {"store": False}
    assert mod.synthetic_host_smoke(SmokeReviewer()) == "READY_FOR_CODEX"

def test_no_api_loop_never_constructs_reviewer_and_completes_manifest():
    a = action(); a["instruction"] = "one"
    data = {"orchestration": {"schema_version": 3, "state": "IDLE", "run_id": None, "approved_action": a, "approved_programme": {"approved": True, "actions": [a]}}}
    assert mod.run_no_api_loop(data, 1, 2, lambda a,t: child(), lambda *x: "rev") == "PROGRAMME_COMPLETE"
    assert data["orchestration"]["deterministic_policy"]["api_reviewer_used"] is False
def test_no_api_child_policy_hard_stops_impacts_and_scope_change():
    bad = child(); bad["provider_impact"] = True
    assert mod.child_policy(bad, "SCOPE") == "HUMAN_APPROVAL_REQUIRED"
    bad = child(); bad["recommended_next_action"] = "other"
    assert mod.child_policy(bad, "SCOPE") == "HUMAN_APPROVAL_REQUIRED"
