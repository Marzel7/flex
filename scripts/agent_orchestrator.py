#!/usr/bin/env python3
"""Agent Orchestrator V1.3: no-API host-owned local Codex loop.

This program must be run from a normal host terminal. Codex children perform
repository-local work only; this host owns Git handoff transport and policy.
The orchestrator is disabled unless --enable is explicitly supplied.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HANDOFF = ROOT / "docs/agent_handoff/current.json"
TRANSPORT = Path(os.environ.get("AGENT_HANDOFF_WORKTREE", ROOT.parent / "flex-agent-handoff"))
LOCK = ROOT / ".agent_orchestrator.lock"
SAFE = {"LOCAL_READ_ONLY", "LOCAL_IMPLEMENTATION", "LOCAL_TEST", "LOCAL_COMMIT"}
ALL = SAFE | {"PRODUCTION_OBSERVATION", "PRODUCTION_MUTATION", "PROVIDER_WORK_INCREASE", "ARCHITECTURAL_DECISION"}
STATES = {"IDLE", "READY_FOR_CODEX", "CODEX_RUNNING", "READY_FOR_REVIEW", "REVIEW_RUNNING", "HUMAN_APPROVAL_REQUIRED", "BLOCKED", "PROGRAMME_COMPLETE"}
TRANSITIONS = {
    "IDLE": {"READY_FOR_CODEX", "BLOCKED"},
    "READY_FOR_CODEX": {"CODEX_RUNNING", "HUMAN_APPROVAL_REQUIRED", "BLOCKED"},
    "CODEX_RUNNING": {"READY_FOR_REVIEW", "BLOCKED"},
    "READY_FOR_REVIEW": {"REVIEW_RUNNING", "HUMAN_APPROVAL_REQUIRED", "BLOCKED", "PROGRAMME_COMPLETE"},
    "REVIEW_RUNNING": {"READY_FOR_CODEX", "HUMAN_APPROVAL_REQUIRED", "BLOCKED", "PROGRAMME_COMPLETE"},
    "HUMAN_APPROVAL_REQUIRED": set(), "BLOCKED": set(), "PROGRAMME_COMPLETE": set(),
}
DECISIONS = {"APPROVE_NEXT_LOCAL_ACTION", "HUMAN_APPROVAL_REQUIRED", "PROGRAMME_COMPLETE", "BLOCKED"}
REVIEW_REQUIRED = {"decision", "authorization_class", "next_action", "reason", "human_question", "scope_change", "production_change", "provider_work_increase"}
CHILD_REQUIRED = {"status", "verdict", "files_changed", "tests", "blockers", "production_impact", "provider_impact", "commits", "recommended_next_action"}
REVIEW_SCHEMA = {"type": "object", "additionalProperties": False, "required": sorted(REVIEW_REQUIRED), "properties": {
    "decision": {"type": "string", "enum": sorted(DECISIONS)}, "authorization_class": {"type": "string", "enum": sorted(ALL)},
    "next_action": {"type": "string"}, "reason": {"type": "string"}, "human_question": {"type": "string"},
    "scope_change": {"type": "boolean"}, "production_change": {"type": "boolean"}, "provider_work_increase": {"type": "boolean"},
}}

class Rejected(ValueError): pass

def load(path=HANDOFF): return json.loads(Path(path).read_text())
def save(data, path=HANDOFF):
    path = Path(path); fd, name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", text=True)
    with os.fdopen(fd, "w") as f: json.dump(data, f, indent=2); f.write("\n")
    os.replace(name, path)
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def git(args, cwd=ROOT): return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True)

def orchestration(data):
    value = data.setdefault("orchestration", {"schema_version": 2, "state": "IDLE", "run_id": None, "approved_action": None})
    if value.get("state") not in STATES: raise Rejected("UNKNOWN_STATE")
    return value

def transition(data, target):
    o = orchestration(data)
    if target not in TRANSITIONS[o["state"]]: raise Rejected(f"INVALID_TRANSITION:{o['state']}->{target}")
    o["state"] = target
    return o

def validate_action(data):
    o = orchestration(data); action = o.get("approved_action")
    if o["state"] != "READY_FOR_CODEX": raise Rejected("NOT_READY_FOR_CODEX")
    if o.get("run_id"): raise Rejected("DUPLICATE_OR_STALE_RUN")
    if not isinstance(action, dict) or action.get("approved") is not True: raise Rejected("ACTION_NOT_APPROVED")
    if action.get("authorization_class") not in ALL: raise Rejected("UNKNOWN_AUTHORIZATION_CLASS")
    if action["authorization_class"] not in SAFE: raise Rejected("HUMAN_APPROVAL_REQUIRED")
    for key in ("milestone", "instruction", "success_criteria", "stop_conditions", "source_handoff_revision"):
        if not action.get(key): raise Rejected(f"ACTION_MISSING:{key}")
    return o, action

def validate_child_result(value):
    if not isinstance(value, dict) or set(value) != CHILD_REQUIRED: raise Rejected("CHILD_RESULT_SCHEMA_INVALID")
    if not all(isinstance(value[k], str) for k in ("status", "verdict", "recommended_next_action")): raise Rejected("CHILD_RESULT_SCHEMA_INVALID")
    if not all(isinstance(value[k], list) for k in ("files_changed", "tests", "blockers", "commits")): raise Rejected("CHILD_RESULT_SCHEMA_INVALID")
    if not isinstance(value["production_impact"], bool) or not isinstance(value["provider_impact"], bool): raise Rejected("CHILD_RESULT_SCHEMA_INVALID")
    return value

def child_policy(result, approved_scope):
    """No model decides authority: only a clean, in-scope local child can continue."""
    result = validate_child_result(result)
    if result["status"] != "COMPLETE" or result["production_impact"] or result["provider_impact"]:
        return "HUMAN_APPROVAL_REQUIRED"
    if not result["recommended_next_action"].startswith(approved_scope):
        return "HUMAN_APPROVAL_REQUIRED"
    return "READY_FOR_CODEX"

def manifest_child_policy(result):
    """V1.3 progression is authorized solely by the ordered approved manifest."""
    result = validate_child_result(result)
    if result["status"] != "COMPLETE" or result["production_impact"] or result["provider_impact"]:
        return "HUMAN_APPROVAL_REQUIRED"
    return "READY_FOR_CODEX"

def child_prompt(action):
    return "\n".join([
        "You are the sandboxed Codex engineering child. Perform only this approved repository-local action:",
        json.dumps({k: action.get(k) for k in ("milestone", "authorization_class", "instruction", "constraints", "success_criteria", "stop_conditions")}),
        "You MUST NOT use network access, call any reviewer or Responses API, fetch/push Git, or update/publish the agent-handoff worktree.",
        "Do not weaken the sandbox, change permissions, touch production, or broaden authority.",
        "Your final output MUST be only one JSON object with exactly these keys: status, verdict, files_changed, tests, blockers, production_impact, provider_impact, commits, recommended_next_action.",
        "files_changed, tests, blockers, and commits are arrays; production_impact and provider_impact are booleans; all other values are strings.",
    ])

def child_environment(source=None):
    source = dict(os.environ if source is None else source)
    for key in list(source):
        upper = key.upper()
        if upper in {"OPENAI_API_KEY", "ORCHESTRATOR_REVIEW_MODEL"} or any(token in upper for token in ("SECRET", "TOKEN", "PASSWORD", "PRIVATE_KEY")):
            source.pop(key, None)
    return source

def run_child(action, timeout, runner=subprocess.run, executable=None):
    exe = executable or shutil.which("codex") or "/Applications/ChatGPT.app/Contents/Resources/codex"
    with tempfile.TemporaryDirectory(prefix="agent-orchestrator-") as td:
        output = Path(td) / "child-result.json"
        try:
            result = runner([exe, "exec", "-C", str(ROOT), "-s", "workspace-write", "--json", "-o", str(output), child_prompt(action)],
                            cwd=ROOT, text=True, capture_output=True, timeout=timeout, env=child_environment())
        except subprocess.TimeoutExpired as exc: raise Rejected("CHILD_TIMEOUT") from exc
        if result.returncode != 0: raise Rejected(f"CHILD_FAILED:{result.returncode}")
        if not output.exists(): raise Rejected("CHILD_RESULT_MISSING")
        try: value = json.loads(output.read_text())
        except json.JSONDecodeError as exc: raise Rejected("CHILD_RESULT_SCHEMA_INVALID") from exc
        return validate_child_result(value)

def validate_review(value):
    if not isinstance(value, dict) or set(value) != REVIEW_REQUIRED: raise Rejected("REVIEW_SCHEMA_INVALID")
    if value["decision"] not in DECISIONS or value["authorization_class"] not in ALL: raise Rejected("REVIEW_SCHEMA_INVALID")
    if not all(isinstance(value[k], str) for k in ("next_action", "reason", "human_question")): raise Rejected("REVIEW_SCHEMA_INVALID")
    if not all(isinstance(value[k], bool) for k in ("scope_change", "production_change", "provider_work_increase")): raise Rejected("REVIEW_SCHEMA_INVALID")
    return value

def policy(value, approved_scope):
    value = validate_review(value); kind = value["authorization_class"]
    risky = value["scope_change"] or value["production_change"] or value["provider_work_increase"]
    words = (value["next_action"] + " " + value["reason"]).lower()
    risky |= any(x in words for x in ("restart", "production", "provider", " rpc", "schema migration", "delete", "architecture expansion"))
    if value["decision"] == "PROGRAMME_COMPLETE": return "PROGRAMME_COMPLETE"
    if value["decision"] == "BLOCKED": return "BLOCKED"
    if value["decision"] != "APPROVE_NEXT_LOCAL_ACTION" or kind not in SAFE or risky or not value["next_action"].startswith(approved_scope):
        return "HUMAN_APPROVAL_REQUIRED"
    return "READY_FOR_CODEX"

class ResponsesReviewer:
    """Host-only Responses API reviewer; credentials are read at call time."""
    def __init__(self, model=None, timeout=30):
        self.model = model or os.environ.get("ORCHESTRATOR_REVIEW_MODEL"); self.timeout = timeout
        if not self.model: raise Rejected("REVIEW_MODEL_UNAVAILABLE")
    def review(self, context):
        key = os.environ.get("OPENAI_API_KEY")
        if not key: raise Rejected("OPENAI_API_KEY_NOT_VISIBLE_TO_HOST")
        body = {"model": self.model, "store": False, "max_output_tokens": 800,
                "input": [{"role": "system", "content": "Return only the strict JSON decision. You advise; host policy authorizes."}, {"role": "user", "content": json.dumps(context)}],
                "text": {"format": {"type": "json_schema", "name": "orchestrator_review", "strict": True, "schema": REVIEW_SCHEMA}}}
        req = urllib.request.Request("https://api.openai.com/v1/responses", data=json.dumps(body).encode(), headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response: payload = json.loads(response.read()); status = response.status
        except urllib.error.HTTPError as exc: raise Rejected(f"REVIEW_HTTP_{exc.code}") from exc
        except (OSError, TimeoutError) as exc: raise Rejected("REVIEW_API_UNAVAILABLE") from exc
        text = payload.get("output_text")
        if not isinstance(text, str): raise Rejected("REVIEW_SCHEMA_INVALID")
        return validate_review(json.loads(text)), {"status": status, "model": self.model, "usage": payload.get("usage"), "store": False}

def review_once(data, reviewer, source_revision):
    o = orchestration(data)
    if o["state"] != "READY_FOR_REVIEW" or source_revision != o.get("source_handoff_revision"): raise Rejected("HANDOFF_CONFLICT")
    if o.get("review_id"): raise Rejected("DUPLICATE_OR_STALE_RUN")
    transition(data, "REVIEW_RUNNING"); o["review_id"] = str(uuid.uuid4())
    decision, metadata = reviewer.review({"child_result": o.get("last_child_result"), "approved_action": o.get("approved_action")})
    scope = o["approved_action"]["milestone"]
    target = policy(decision, scope); o["review"] = {"review_id": o["review_id"], "decision": decision, "policy_decision": target, "metadata": metadata}; o["state"] = target
    if target == "READY_FOR_CODEX":
        o["run_id"] = None
        o.pop("review_id", None)
        o["approved_action"] = {**o["approved_action"], "authorization_class": decision["authorization_class"], "instruction": decision["next_action"]}
    return target

def transport_head(): return git(["rev-parse", "origin/agent-handoff"], TRANSPORT).stdout.strip()
def publish_handoff(data, expected_revision, message):
    """Host-only optimistic-concurrency transport."""
    git(["fetch", "origin", "agent-handoff"], TRANSPORT)
    if transport_head() != expected_revision: raise Rejected("HANDOFF_CONFLICT")
    git(["merge", "--ff-only", expected_revision], TRANSPORT)
    target = TRANSPORT / "docs/agent_handoff/current.json"; target.parent.mkdir(parents=True, exist_ok=True); save(data, target); written_sha = sha(target)
    git(["add", "--", "docs/agent_handoff/current.json"], TRANSPORT); git(["commit", "-m", message], TRANSPORT); git(["push", "origin", "HEAD:agent-handoff"], TRANSPORT)
    git(["fetch", "origin", "agent-handoff"], TRANSPORT)
    if transport_head() != git(["rev-parse", "HEAD"], TRANSPORT).stdout.strip() or sha(target) != written_sha: raise Rejected("HANDOFF_VERIFY_FAILED")
    return transport_head()

def execute_one(data, timeout, max_iterations, child_runner=run_child):
    o, action = validate_action(data)
    attempt = int(o.get("attempt", 0)) + 1
    if attempt > max_iterations: raise Rejected("ITERATION_CAP_REACHED")
    o.update({"run_id": str(uuid.uuid4()), "attempt": attempt}); transition(data, "CODEX_RUNNING")
    try: child = child_runner(action, timeout)
    except Exception:
        o["state"] = "BLOCKED"; raise
    o["last_child_result"] = child; o["source_handoff_revision"] = action["source_handoff_revision"]; transition(data, "READY_FOR_REVIEW")
    return child

def synthetic_host_smoke(reviewer):
    action = {"approved": True, "milestone": "HOST_SMOKE", "authorization_class": "LOCAL_TEST", "instruction": "synthetic", "success_criteria": "review", "stop_conditions": "stop", "source_handoff_revision": "synthetic"}
    data = {"orchestration": {"schema_version": 2, "state": "READY_FOR_CODEX", "run_id": None, "attempt": 0, "approved_action": action}}
    execute_one(data, 1, 1, child_runner=lambda _a, _t: {"status": "COMPLETE", "verdict": "PASS", "files_changed": [], "tests": ["synthetic"], "blockers": [], "production_impact": False, "provider_impact": False, "commits": [], "recommended_next_action": "HOST_SMOKE LOCAL_TEST continue"})
    return review_once(data, reviewer, "synthetic")

def run_host_loop(data, timeout, max_iterations, reviewer=None, child_runner=run_child, publisher=publish_handoff):
    """Execute/publish/review/publish on the host until policy stops or the cap is reached."""
    reviewer = reviewer or ResponsesReviewer()
    expected_revision = orchestration(data).get("approved_action", {}).get("source_handoff_revision")
    if not expected_revision: raise Rejected("ACTION_MISSING:source_handoff_revision")
    iterations = 0
    while orchestration(data)["state"] == "READY_FOR_CODEX":
        iterations += 1
        if iterations > max_iterations: raise Rejected("ITERATION_CAP_REACHED")
        execute_one(data, timeout, max_iterations, child_runner)
        ready_revision = publisher(data, expected_revision, "agent handoff: orchestrator ready for review")
        orchestration(data)["source_handoff_revision"] = ready_revision
        review_once(data, reviewer, ready_revision)
        reviewed_revision = publisher(data, ready_revision, f"agent handoff: orchestrator {orchestration(data)['state'].lower()}")
        expected_revision = reviewed_revision
        if orchestration(data)["state"] == "READY_FOR_CODEX":
            orchestration(data)["approved_action"]["source_handoff_revision"] = reviewed_revision
    return orchestration(data)["state"]

def run_no_api_loop(data, timeout, max_iterations, child_runner=run_child, publisher=publish_handoff):
    """V1.3 default. A preapproved manifest, not a reviewer, supplies next work."""
    o = orchestration(data); manifest = o.get("approved_programme")
    if not isinstance(manifest, dict) or manifest.get("approved") is not True or not isinstance(manifest.get("actions"), list) or not manifest.get("active_milestone"):
        raise Rejected("PROGRAMME_MANIFEST_NOT_APPROVED")
    actions = manifest["actions"]
    if not actions: raise Rejected("PROGRAMME_ACTIONS_EMPTY")
    active = o.get("approved_action")
    if active is None:
        active = actions[0]
        o["approved_action"] = active
        start_index = 0
    elif not isinstance(active, dict):
        raise Rejected("APPROVED_ACTION_MALFORMED")
    else:
        try: start_index = actions.index(active)
        except ValueError as exc: raise Rejected("APPROVED_ACTION_NOT_IN_PROGRAMME") from exc
    if not isinstance(active, dict): raise Rejected("PROGRAMME_ACTION_MALFORMED")
    expected = active.get("source_handoff_revision")
    if not expected: raise Rejected("ACTION_MISSING:source_handoff_revision")
    for index in range(start_index, len(actions)):
        action = actions[index]
        if not isinstance(action, dict): raise Rejected("PROGRAMME_ACTION_MALFORMED")
        if action.get("milestone") != manifest["active_milestone"]: raise Rejected("ACTIVE_MILESTONE_MISMATCH")
        if index - start_index >= max_iterations: raise Rejected("ITERATION_CAP_REACHED")
        if index != start_index: o["approved_action"] = action
        o["run_id"] = None; o["state"] = "READY_FOR_CODEX"
        child = execute_one(data, timeout, max_iterations, child_runner)
        target = manifest_child_policy(child)
        o["deterministic_policy"] = {"decision": target, "api_reviewer_used": False, "iteration": index + 1}
        if target != "READY_FOR_CODEX":
            o["state"] = target; return target
        expected = publisher(data, expected, "agent handoff: no-api child complete")
        if index + 1 == len(manifest["actions"]):
            o["state"] = "PROGRAMME_COMPLETE"; return "PROGRAMME_COMPLETE"
        manifest["actions"][index + 1]["source_handoff_revision"] = expected
    return "PROGRAMME_COMPLETE"

def main(argv=None):
    parser = argparse.ArgumentParser(); parser.add_argument("--enable", action="store_true", help="explicitly enable this otherwise-disabled orchestrator")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run"); run.add_argument("--timeout", type=int, default=1800); run.add_argument("--max-iterations", type=int, default=5)
    sub.add_parser("no-api-run", help="V1.3 preapproved local manifest only; no Responses API")
    smoke = sub.add_parser("host-smoke-live", help="host-only synthetic result -> live reviewer -> policy; never starts Codex")
    args = parser.parse_args(argv)
    if not args.enable: print("ORCHESTRATOR_DISABLED", file=sys.stderr); return 2
    try:
        with LOCK.open("a+") as lock:
            try: fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc: raise Rejected("CONCURRENT_RUN") from exc
            if args.command == "host-smoke-live": print(synthetic_host_smoke(ResponsesReviewer())); return 0
            data = load(); final_state = run_no_api_loop(data, args.timeout, args.max_iterations) if args.command in {"run", "no-api-run"} else run_host_loop(data, args.timeout, args.max_iterations); save(data); print(final_state); return 0
    except (Rejected, subprocess.SubprocessError, OSError) as exc: print(f"ORCHESTRATOR_BLOCKED: {exc}", file=sys.stderr); return 2

if __name__ == "__main__": raise SystemExit(main())
