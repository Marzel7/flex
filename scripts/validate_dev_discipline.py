#!/usr/bin/env python3
"""Small, offline checks for Development Execution Discipline V2.

This command never starts Supervisor or contacts a socket.  It only reads Git
metadata and supplied JSON contracts so DEV admission/promotion fails closed
before any live action can be considered.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


REQUIRED_DEV_FIELDS = {
    "dev_id", "objective", "branch", "worktree", "starting_sha",
    "source_authority", "configuration_authority", "definition_of_done",
    "acceptance_criteria", "dependencies", "current_sha", "status",
    "next_action", "qualification_evidence", "integration_promotion_status",
}
ACTIVE_IMPLEMENTATION = {"IMPLEMENTING", "QUALIFYING"}
LIVE_TRANSITIONS = {"LIVE_TRANSITION"}


class GuardError(ValueError):
    pass


def load_json(path: str | Path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], text=True, capture_output=True, check=False
    )
    if result.returncode:
        raise GuardError(result.stderr.strip() or "git command failed")
    return result.stdout.strip()


def require_clean_worktree(root: Path) -> None:
    if git(root, "status", "--porcelain=v1"):
        raise GuardError("DIRTY_STARTING_WORKTREE")


def reject_protected_path(path: Path, protected_roots: list[str]) -> None:
    resolved = path.resolve()
    for root in protected_roots:
        protected = Path(root).resolve()
        if resolved == protected or protected in resolved.parents:
            raise GuardError("PROTECTED_RUNTIME_PATH")


def validate_dev_entry(entry: dict, repo: Path, protected_roots: list[str]) -> None:
    missing = sorted(REQUIRED_DEV_FIELDS - set(entry))
    if missing:
        raise GuardError("MISSING_DEV_FIELDS:" + ",".join(missing))
    if not entry["dev_id"] or not entry["objective"]:
        raise GuardError("EMPTY_DEV_ID_OR_OBJECTIVE")
    if not entry["branch"].startswith("codex/"):
        raise GuardError("BRANCH_MUST_USE_CODEX_PREFIX")
    worktree = Path(entry["worktree"])
    if not worktree.is_absolute() or not worktree.is_dir():
        raise GuardError("MISSING_OR_RELATIVE_WORKTREE")
    reject_protected_path(worktree, protected_roots)
    if Path(git(worktree, "rev-parse", "--show-toplevel")).resolve() != worktree.resolve():
        raise GuardError("WORKTREE_ROOT_MISMATCH")
    if git(worktree, "branch", "--show-current") != entry["branch"]:
        raise GuardError("BRANCH_AUTHORITY_MISMATCH")
    try:
        resolved_sha = git(worktree, "rev-parse", entry["starting_sha"] + "^{commit}")
    except GuardError as exc:
        raise GuardError("MISSING_STARTING_SHA") from exc
    if len(resolved_sha) != 40:
        raise GuardError("AMBIGUOUS_STARTING_SHA")
    if len(git(worktree, "rev-parse", entry["current_sha"] + "^{commit}")) != 40:
        raise GuardError("MISSING_CURRENT_SHA")
    require_clean_worktree(worktree)
    if not isinstance(entry["definition_of_done"], list) or not entry["definition_of_done"]:
        raise GuardError("MISSING_DEFINITION_OF_DONE")
    if not isinstance(entry["acceptance_criteria"], list) or not entry["acceptance_criteria"]:
        raise GuardError("MISSING_ACCEPTANCE_CRITERIA")
    if not isinstance(entry["dependencies"], list):
        raise GuardError("INVALID_DEPENDENCIES")
    if not entry["source_authority"] or not entry["configuration_authority"]:
        raise GuardError("MISSING_AUTHORITY")


def validate_register(register: dict, repo: Path, protected_roots: list[str]) -> None:
    policy = register.get("policy", {})
    if policy.get("max_active_implementation_devs") != 2:
        raise GuardError("INVALID_IMPLEMENTATION_CONCURRENCY_LIMIT")
    entries = register.get("devs")
    if not isinstance(entries, list):
        raise GuardError("INVALID_DEV_REGISTER")
    ids = [entry.get("dev_id") for entry in entries]
    if len(ids) != len(set(ids)):
        raise GuardError("DUPLICATE_DEV_ID")
    active = [entry for entry in entries if entry.get("status") in ACTIVE_IMPLEMENTATION]
    if len(active) > policy["max_active_implementation_devs"]:
        raise GuardError("IMPLEMENTATION_CONCURRENCY_EXCEEDED")
    transitions = [entry for entry in entries if entry.get("status") in LIVE_TRANSITIONS]
    if len(transitions) > 1:
        raise GuardError("CONCURRENT_LIVE_TRANSITIONS")
    for index, left in enumerate(active):
        left_services = set(left.get("touched_services", []))
        left_state = set(left.get("shared_mutable_state", []))
        for right in active[index + 1:]:
            if left_services & set(right.get("touched_services", [])):
                raise GuardError("CONCURRENT_SERVICE_OWNERSHIP")
            if left_state & set(right.get("shared_mutable_state", [])):
                raise GuardError("CONCURRENT_MUTABLE_STATE")
    for entry in entries:
        if entry.get("status") in ACTIVE_IMPLEMENTATION | LIVE_TRANSITIONS:
            validate_dev_entry(entry, repo, protected_roots)


def validate_launcher(root: Path, launcher: str, required: list[str]) -> None:
    target = (root / launcher).resolve()
    if not target.is_file() or root.resolve() not in target.parents:
        raise GuardError("MISSING_LAUNCHER")
    for item in required:
        candidate = (root / item).resolve()
        if not candidate.is_file() or root.resolve() not in candidate.parents:
            raise GuardError("MISSING_LAUNCHER_DEPENDENCY:" + item)
        if not git(root, "ls-files", "--error-unmatch", item):
            raise GuardError("UNTRACKED_LAUNCHER_DEPENDENCY:" + item)


def validate_capabilities(requirements: dict, actual: dict) -> None:
    for capability, expectation in requirements.items():
        state = actual.get(capability, {})
        if expectation == "operational" and state.get("state") != "PRESENT_AND_REACHABLE":
            raise GuardError("MISSING_OPERATIONAL_CAPABILITY:" + capability)
        if expectation == "disabled" and state.get("state") != "PRESENT_BUT_DISABLED":
            raise GuardError("INVALID_DISABLED_CAPABILITY:" + capability)
        if state.get("state") == "REPLACED_WITH_PROVEN_EQUIVALENT" and not state.get("evidence"):
            raise GuardError("UNPROVEN_REPLACEMENT:" + capability)


def validate_handoff(contract: dict, worktree: Path) -> None:
    for field in ("committed_sha", "regression_results", "limitations", "next_action"):
        if not contract.get(field):
            raise GuardError("INCOMPLETE_HANDOFF:" + field)
    if len(git(worktree, "rev-parse", contract["committed_sha"] + "^{commit}")) != 40:
        raise GuardError("HANDOFF_SHA_UNRESOLVABLE")
    if contract["remote_verified"] is not True:
        raise GuardError("HANDOFF_REMOTE_UNVERIFIED")
    require_clean_worktree(worktree)


def validate_promotion(contract: dict) -> None:
    required = (
        "source_authority", "dependency_authority", "launcher_contract_verified",
        "capability_contract_verified", "active_supervisor_authority",
        "offline_supervisor_validation", "rollback_sha", "live_action",
    )
    for field in required:
        if field not in contract or contract[field] in (None, ""):
            raise GuardError("INCOMPLETE_PROMOTION_CONTRACT:" + field)
    if contract["launcher_contract_verified"] is not True:
        raise GuardError("LAUNCHER_CONTRACT_UNVERIFIED")
    if contract["capability_contract_verified"] is not True:
        raise GuardError("CAPABILITY_CONTRACT_UNVERIFIED")
    if contract["offline_supervisor_validation"] != "parser-only":
        raise GuardError("SUPERVISOR_VALIDATION_NOT_PARSER_ONLY")
    if contract["live_action"] and contract.get("explicit_live_approval") is not True:
        raise GuardError("LIVE_MUTATION_NOT_EXPLICITLY_APPROVED")


def validate_test_paths(paths: list[str], protected_roots: list[str]) -> None:
    if len(paths) != 2 or len({str(Path(path).resolve()) for path in paths}) != 2:
        raise GuardError("TEST_PATHS_NOT_ISOLATED")
    for path in paths:
        candidate = Path(path)
        if not candidate.is_absolute() or not str(candidate.resolve()).startswith("/private/tmp/"):
            raise GuardError("TEST_PATH_OUTSIDE_TEMPORARY_ROOT")
        reject_protected_path(candidate, protected_roots)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    admit = sub.add_parser("admit")
    admit.add_argument("--entry", required=True)
    admit.add_argument("--repo", required=True)
    admit.add_argument("--protected-root", action="append", default=[])
    reg = sub.add_parser("register")
    reg.add_argument("--register", required=True)
    reg.add_argument("--repo", required=True)
    reg.add_argument("--protected-root", action="append", default=[])
    launch = sub.add_parser("launcher")
    launch.add_argument("--repo", required=True)
    launch.add_argument("--launcher", required=True)
    launch.add_argument("--required", action="append", default=[])
    cap = sub.add_parser("capabilities")
    cap.add_argument("--requirements", required=True)
    cap.add_argument("--actual", required=True)
    hand = sub.add_parser("handoff")
    hand.add_argument("--contract", required=True)
    hand.add_argument("--worktree", required=True)
    promote = sub.add_parser("promotion")
    promote.add_argument("--contract", required=True)
    paths = sub.add_parser("test-paths")
    paths.add_argument("--path", action="append", required=True)
    paths.add_argument("--protected-root", action="append", default=[])
    args = parser.parse_args()
    try:
        if args.command == "admit":
            validate_dev_entry(load_json(args.entry), Path(args.repo), args.protected_root)
        elif args.command == "register":
            validate_register(load_json(args.register), Path(args.repo), args.protected_root)
        elif args.command == "launcher":
            validate_launcher(Path(args.repo), args.launcher, args.required)
        elif args.command == "capabilities":
            validate_capabilities(load_json(args.requirements), load_json(args.actual))
        elif args.command == "handoff":
            validate_handoff(load_json(args.contract), Path(args.worktree))
        elif args.command == "promotion":
            validate_promotion(load_json(args.contract))
        else:
            validate_test_paths(args.path, args.protected_root)
    except GuardError as exc:
        print(f"REJECTED:{exc}", file=sys.stderr)
        return 2
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
