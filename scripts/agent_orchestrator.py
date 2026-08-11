#!/usr/bin/env python3
"""Fail-closed local executor for approved Codex handoff actions.

V1 deliberately has no reviewer callback and never advances an engineering
programme beyond READY_FOR_REVIEW.  It accepts only explicit local approvals.
"""
from __future__ import annotations

import argparse, fcntl, hashlib, json, os, shutil, subprocess, sys, tempfile, time, uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HANDOFF = ROOT / "docs/agent_handoff/current.json"
TRANSPORT = Path(os.environ.get("AGENT_HANDOFF_WORKTREE", ROOT.parent / "flex-agent-handoff"))
LOCK = ROOT / ".agent_orchestrator.lock"
SAFE = {"LOCAL_READ_ONLY", "LOCAL_IMPLEMENTATION", "LOCAL_TEST", "LOCAL_COMMIT"}
ALL = SAFE | {"PRODUCTION_OBSERVATION", "PRODUCTION_MUTATION", "PROVIDER_WORK_INCREASE", "ARCHITECTURAL_DECISION"}
STATES = {"IDLE", "READY_FOR_CODEX", "CODEX_RUNNING", "READY_FOR_REVIEW", "HUMAN_APPROVAL_REQUIRED", "BLOCKED", "PROGRAMME_COMPLETE"}
TRANSITIONS = {"IDLE": {"READY_FOR_CODEX", "BLOCKED"}, "READY_FOR_CODEX": {"CODEX_RUNNING", "HUMAN_APPROVAL_REQUIRED", "BLOCKED"}, "CODEX_RUNNING": {"READY_FOR_REVIEW", "BLOCKED"}, "READY_FOR_REVIEW": {"IDLE", "PROGRAMME_COMPLETE"}, "HUMAN_APPROVAL_REQUIRED": {"READY_FOR_CODEX", "IDLE"}, "BLOCKED": {"IDLE"}, "PROGRAMME_COMPLETE": set()}

class Rejected(ValueError): pass

def load(path=HANDOFF): return json.loads(Path(path).read_text())
def digest(path=HANDOFF): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(data, path=HANDOFF):
    path = Path(path); fd, name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", text=True)
    with os.fdopen(fd, "w") as f: json.dump(data, f, indent=2, sort_keys=False); f.write("\n")
    os.replace(name, path)

def orchestration(data):
    value = data.setdefault("orchestration", {"schema_version": 1, "state": "IDLE", "run_id": None, "approved_action": None, "reviewer_callback": {"available": False, "reason": "No supported local ChatGPT/planner daemon callback discovered"}})
    if value.get("state") not in STATES: raise Rejected("unknown orchestration state")
    return value

def approved(data):
    o = orchestration(data); a = o.get("approved_action")
    if o["state"] != "READY_FOR_CODEX": raise Rejected("handoff is not READY_FOR_CODEX")
    if not isinstance(a, dict) or a.get("approved") is not True: raise Rejected("missing explicit approved_action")
    kind = a.get("authorization_class")
    if kind not in ALL: raise Rejected("unknown authorization_class")
    if kind not in SAFE: raise Rejected(f"{kind} requires human approval and cannot be autonomous")
    for key in ("milestone", "instruction", "success_criteria", "stop_conditions", "source_handoff_revision"):
        if not a.get(key): raise Rejected(f"approved_action missing {key}")
    if o.get("run_id"): raise Rejected("conflicting active run_id")
    return o, a

def transition(data, target):
    o = orchestration(data); current = o["state"]
    if target not in TRANSITIONS[current]: raise Rejected(f"invalid transition {current}->{target}")
    o["state"] = target; return o

def git(args, cwd=ROOT, check=True): return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=check)
def transport_revision(): return git(["rev-parse", "origin/agent-handoff"], TRANSPORT).stdout.strip()

def publish(data, message):
    save(data); subprocess.run(["git", "fetch", "origin", "agent-handoff"], cwd=TRANSPORT, check=True)
    subprocess.run(["git", "merge", "--ff-only", "origin/agent-handoff"], cwd=TRANSPORT, check=True)
    target = TRANSPORT / "docs/agent_handoff/current.json"; target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(HANDOFF, target)
    subprocess.run(["git", "add", "--", "docs/agent_handoff/current.json"], cwd=TRANSPORT, check=True)
    subprocess.run(["git", "commit", "-m", message], cwd=TRANSPORT, check=True)
    subprocess.run(["git", "push", "origin", "agent-handoff"], cwd=TRANSPORT, check=True)
    subprocess.run(["git", "fetch", "origin", "agent-handoff"], cwd=TRANSPORT, check=True)
    if transport_revision() != git(["rev-parse", "HEAD"], TRANSPORT).stdout.strip() or digest() != hashlib.sha256(target.read_bytes()).hexdigest(): raise RuntimeError("handoff transport verification failed")

def capability():
    exe = shutil.which("codex") or "/Applications/ChatGPT.app/Contents/Resources/codex"
    result = subprocess.run([exe, "--version"], text=True, capture_output=True)
    return {"executable": exe, "version": (result.stdout + result.stderr).strip(), "headless": "codex exec [PROMPT] with -C, stdin, --json, -o, --sandbox", "desktop_required": False, "auth": "Codex CLI stored ChatGPT auth; provider reachability must be checked at execution"}

def execute(timeout):
    with LOCK.open("a+") as lock:
        try: fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise Rejected("another orchestrator run holds the lock")
        data = load(); o, a = approved(data)
        subprocess.run(["git", "fetch", "origin", "agent-handoff"], cwd=TRANSPORT, check=True)
        if transport_revision() != a["source_handoff_revision"]: raise Rejected("stale handoff revision")
        o["run_id"] = str(uuid.uuid4()); o["attempt"] = int(o.get("attempt", 0)) + 1; transition(data, "CODEX_RUNNING")
        data["updated_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()); publish(data, f"agent handoff: {a['milestone']} codex running")
        prompt = f"Read AGENTS.md and docs/agent_handoff/current.json. Execute only approved action {a['milestone']} ({a['authorization_class']}): {a['instruction']} Constraints: {a.get('constraints', [])}. Success: {a['success_criteria']}. Stop: {a['stop_conditions']}. Update and publish the handoff before completion. Do not broaden authority."
        exe = capability()["executable"]; result = subprocess.run([exe, "exec", "-C", str(ROOT), "-s", "workspace-write", "--json", "-o", str(ROOT / ".agent_orchestrator_last_message.txt"), prompt], cwd=ROOT, text=True, capture_output=True, timeout=timeout)
        data = load(); o = orchestration(data); o["run_id"] = o.get("run_id") or "unknown"; o["last_result"] = {"status": "COMPLETE" if result.returncode == 0 else "FAILED", "exit_code": result.returncode, "files_changed": git(["diff", "--name-only"], ROOT).stdout.splitlines(), "production_changes": False, "provider_rpc_changes": False, "human_approval_required": True}
        o["state"] = "READY_FOR_REVIEW" if result.returncode == 0 else "BLOCKED"; data["updated_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()); publish(data, f"agent handoff: {a['milestone']} ready for review")
        return result.returncode

def main():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest="cmd", required=True); sub.add_parser("capability"); run=sub.add_parser("run"); run.add_argument("--timeout", type=int, default=1800)
    args=p.parse_args()
    if args.cmd == "capability": print(json.dumps(capability(), indent=2)); return 0
    try: return execute(args.timeout)
    except (Rejected, subprocess.SubprocessError, OSError) as e: print(f"ORCHESTRATOR_BLOCKED: {e}", file=sys.stderr); return 2
if __name__ == "__main__": raise SystemExit(main())
