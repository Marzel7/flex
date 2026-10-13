#!/usr/bin/env python3
"""Local DEV work registry and fail-closed production-candidate preflight.

This module is bookkeeping only.  It does not push, deploy, restart services,
or write outside the repository-local docs/dev_work directory.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_ROOT = PROJECT_ROOT / "docs" / "dev_work"
REGISTRY_PATH = Path(os.environ.get("FLEX_DEV_WORK_REGISTRY", REGISTRY_ROOT / "current.json")).resolve()
HISTORY_PATH = Path(os.environ.get("FLEX_DEV_WORK_HISTORY", REGISTRY_ROOT / "history")).resolve()

DEV_STATUSES = {"PLANNED", "IN_PROGRESS", "READY_FOR_DEV_REVIEW", "DEV_COMPLETE", "ABANDONED"}
PROD_INTENTS = {"UNDECIDED", "DEV_ONLY", "EVENTUAL_PROD", "DO_NOT_DEPLOY"}
PROD_APPROVALS = {"NOT_REQUESTED", "PENDING", "APPROVED", "REJECTED"}
TEST_STATUSES = {"NOT_RUN", "PASS", "FAIL", "PARTIAL"}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=PROJECT_ROOT, text=True, capture_output=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def default_registry() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "updated_at": now_iso(),
        "rules": {
            "prod_approval_required": True,
            "dev_complete_implies_prod": False,
            "git_push_implies_prod": False,
            "selective_promotion_supported": True,
        },
        "work_items": [],
        "unmapped_existing_dev_work": {
            "recorded": True,
            "description": "Pre-registry working-tree changes are intentionally unmapped; no production intent is inferred.",
        },
    }


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    if not path.exists():
        return default_registry()
    data = json.loads(path.read_text())
    if data.get("schema_version") != 1 or not isinstance(data.get("work_items"), list):
        raise ValueError(f"unsupported DEV work registry: {path}")
    return data


def save_registry(data: dict[str, Any], path: Path = REGISTRY_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = now_iso()
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def next_work_id(data: dict[str, Any]) -> str:
    numbers = []
    for item in data["work_items"]:
        try:
            numbers.append(int(item["work_id"].split("-", 1)[1]))
        except (KeyError, ValueError, IndexError):
            continue
    return f"DEV-{max(numbers, default=0) + 1:03d}"


def find_item(data: dict[str, Any], work_id: str) -> dict[str, Any]:
    wanted = work_id.upper()
    for item in data["work_items"]:
        if item["work_id"].upper() == wanted:
            return item
    raise KeyError(f"unknown work item: {work_id}")


def create_item(data: dict[str, Any], title: str, description: str = "") -> dict[str, Any]:
    timestamp = now_iso()
    item = {
        "work_id": next_work_id(data),
        "title": title,
        "description": description,
        "status": "PLANNED",
        "created_at": timestamp,
        "updated_at": timestamp,
        "branch": _git("branch", "--show-current") or "DETACHED_OR_UNKNOWN",
        "base_commit": _git("rev-parse", "HEAD") or None,
        "current_commit": None,
        "files_changed": [],
        "dependencies": [],
        "test_status": "NOT_RUN",
        "test_summary": "",
        "dev_ui_verified": False,
        "prod_intent": "UNDECIDED",
        "prod_approval": "NOT_REQUESTED",
        "prod_candidate_commit": None,
        "prod_deployment_status": "NOT_CANDIDATE",
        "prod_persistent_path_impact_reviewed": False,
        "affected_production_services": [],
        "prod_promotion_blocked": True,
        "notes": "",
    }
    data["work_items"].append(item)
    return item


def changed_files() -> list[str]:
    # Keep existing untracked directories summarized; expanding large retained
    # runtime trees would make this safety report unusable without adding any
    # promotion assurance.
    output = _git("status", "--porcelain=v1", "--untracked-files=normal")
    paths: list[str] = []
    for line in output.splitlines():
        raw = line[3:]
        if " -> " in raw:
            raw = raw.split(" -> ", 1)[1]
        paths.append(raw.strip('"'))
    return sorted(set(paths))


def mapping_report(data: dict[str, Any]) -> dict[str, Any]:
    changed = changed_files()
    owners: dict[str, list[str]] = {}
    for item in data["work_items"]:
        for path in item.get("files_changed", []):
            owners.setdefault(path, []).append(item["work_id"])
    mapped = {path: owners[path] for path in changed if path in owners}
    return {
        "dev_working_tree_changes": changed,
        "mapped_work_items": mapped,
        "unmapped_changes": [path for path in changed if path not in owners],
    }


def prod_check(data: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    mapped = mapping_report(data)
    dependencies = []
    blocked_dependencies = []
    for dependency_id in item.get("dependencies", []):
        try:
            dependency = find_item(data, dependency_id)
            detail = {
                "work_id": dependency_id,
                "prod_approval": dependency["prod_approval"],
                "prod_candidate_commit": dependency.get("prod_candidate_commit"),
            }
            if dependency["prod_approval"] != "APPROVED":
                blocked_dependencies.append(dependency_id)
        except KeyError:
            detail = {"work_id": dependency_id, "error": "UNKNOWN_DEPENDENCY"}
            blocked_dependencies.append(dependency_id)
        dependencies.append(detail)

    files = item.get("files_changed", [])
    source_diff = {
        "base_commit": item.get("base_commit"),
        "current_commit": item.get("current_commit"),
        "files_changed": files,
        "identified": bool(files),
    }
    reasons = []
    if item["status"] != "DEV_COMPLETE":
        reasons.append("WORK_STATUS_NOT_DEV_COMPLETE")
    if item["prod_intent"] != "EVENTUAL_PROD":
        reasons.append("PROD_INTENT_NOT_EVENTUAL_PROD")
    if item["prod_approval"] != "APPROVED":
        reasons.append("PROD_APPROVAL_NOT_APPROVED")
    if not source_diff["identified"]:
        reasons.append("SOURCE_DIFF_NOT_IDENTIFIED")
    if blocked_dependencies:
        reasons.append("UNAPPROVED_DEPENDENCY")
    if item["test_status"] != "PASS":
        reasons.append("FOCUSED_TESTS_NOT_PASSING")
    if not item.get("prod_persistent_path_impact_reviewed"):
        reasons.append("PERSISTENT_PATH_IMPACT_NOT_REVIEWED")
    if not item.get("affected_production_services"):
        reasons.append("AFFECTED_SERVICES_NOT_IDENTIFIED")

    # Unmapped edits are reported, not silently swept into the candidate. They
    # do not block selective promotion unless the proposed source path itself
    # is missing from this item's explicit mapping.
    result = {
        "work_id": item["work_id"],
        "work_status": item["status"],
        "prod_intent": item["prod_intent"],
        "prod_approval": item["prod_approval"],
        "source_diff": source_diff,
        "dependencies": dependencies,
        "prod_promotion_blocked": bool(blocked_dependencies),
        "unrelated_dev_changes": mapped["unmapped_changes"],
        "test_status": item["test_status"],
        "test_summary": item["test_summary"],
        "persistent_path_impact_reviewed": item.get("prod_persistent_path_impact_reviewed", False),
        "affected_services": item.get("affected_production_services", []),
        "prod_candidate_commit": item.get("prod_candidate_commit"),
        "prod_promotion_ready": not reasons,
        "blocking_reasons": reasons,
    }
    item["prod_promotion_blocked"] = bool(reasons)
    return result


def _csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local DEV work registry (never deploys)")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("title")
    create.add_argument("--description", default="")
    sub.add_parser("list")
    show = sub.add_parser("show"); show.add_argument("work_id")
    complete = sub.add_parser("complete"); complete.add_argument("work_id")
    status = sub.add_parser("status"); status.add_argument("work_id"); status.add_argument("status")
    intent = sub.add_parser("intent"); intent.add_argument("work_id"); intent.add_argument("intent")
    approve = sub.add_parser("approve-prod"); approve.add_argument("work_id")
    reject = sub.add_parser("reject-prod"); reject.add_argument("work_id")
    archive = sub.add_parser("archive"); archive.add_argument("work_id")
    files = sub.add_parser("files"); files.add_argument("work_id"); files.add_argument("paths")
    deps = sub.add_parser("dependencies"); deps.add_argument("work_id"); deps.add_argument("work_ids")
    tests = sub.add_parser("tests"); tests.add_argument("work_id"); tests.add_argument("status"); tests.add_argument("summary")
    verified = sub.add_parser("dev-verified"); verified.add_argument("work_id"); verified.add_argument("value", choices=["true", "false"])
    services = sub.add_parser("services"); services.add_argument("work_id"); services.add_argument("names")
    paths = sub.add_parser("path-review"); paths.add_argument("work_id"); paths.add_argument("value", choices=["true", "false"])
    commit = sub.add_parser("current-commit"); commit.add_argument("work_id"); commit.add_argument("commit")
    candidate = sub.add_parser("candidate"); candidate.add_argument("work_id"); candidate.add_argument("commit")
    sub.add_parser("changes")
    check = sub.add_parser("prod-check"); check.add_argument("work_id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data = load_registry()
    try:
        item = find_item(data, getattr(args, "work_id", "")) if hasattr(args, "work_id") else None
    except KeyError as exc:
        print(str(exc), file=sys.stderr); return 2

    changed = False
    if args.command == "create":
        item = create_item(data, args.title, args.description); changed = True
        print(item["work_id"])
    elif args.command == "list":
        for row in data["work_items"]:
            print(f'{row["work_id"]}\t{row["status"]}\t{row["prod_intent"]}\t{row["prod_approval"]}\t{row["title"]}')
    elif args.command == "show": print(json.dumps(item, indent=2, sort_keys=True))
    elif args.command == "complete": item["status"] = "DEV_COMPLETE"; changed = True
    elif args.command == "status":
        value = args.status.upper().replace("-", "_")
        if value not in DEV_STATUSES: raise SystemExit(f"invalid status: {value}")
        item["status"] = value; changed = True
    elif args.command == "intent":
        value = args.intent.upper().replace("-", "_")
        if value not in PROD_INTENTS: raise SystemExit(f"invalid intent: {value}")
        item["prod_intent"] = value; changed = True
    elif args.command == "approve-prod":
        if item["prod_intent"] != "EVENTUAL_PROD":
            raise SystemExit("set PROD_INTENT=EVENTUAL_PROD before approval")
        item["prod_approval"] = "APPROVED"; changed = True
    elif args.command == "reject-prod": item["prod_approval"] = "REJECTED"; changed = True
    elif args.command == "files": item["files_changed"] = _csv(args.paths); changed = True
    elif args.command == "dependencies": item["dependencies"] = _csv(args.work_ids); changed = True
    elif args.command == "tests":
        value = args.status.upper()
        if value not in TEST_STATUSES: raise SystemExit(f"invalid test status: {value}")
        item["test_status"], item["test_summary"] = value, args.summary; changed = True
    elif args.command == "dev-verified": item["dev_ui_verified"] = args.value == "true"; changed = True
    elif args.command == "services": item["affected_production_services"] = _csv(args.names); changed = True
    elif args.command == "path-review": item["prod_persistent_path_impact_reviewed"] = args.value == "true"; changed = True
    elif args.command == "current-commit": item["current_commit"] = args.commit; changed = True
    elif args.command == "candidate":
        if item["prod_approval"] != "APPROVED": raise SystemExit("production approval is required before recording a candidate")
        item["prod_candidate_commit"] = args.commit
        item["prod_deployment_status"] = "CANDIDATE_FROZEN_NOT_DEPLOYED"; changed = True
    elif args.command == "changes": print(json.dumps(mapping_report(data), indent=2, sort_keys=True))
    elif args.command == "prod-check":
        result = prod_check(data, item); print(json.dumps(result, indent=2, sort_keys=True)); return 0 if result["prod_promotion_ready"] else 1
    elif args.command == "archive":
        if item["status"] not in {"ABANDONED", "DEV_COMPLETE"}:
            raise SystemExit("only ABANDONED or DEV_COMPLETE work can be archived")
        HISTORY_PATH.mkdir(parents=True, exist_ok=True)
        archive_path = HISTORY_PATH / f'{item["work_id"]}.json'
        if archive_path.exists(): raise SystemExit(f"archive already exists: {archive_path}")
        archive_path.write_text(json.dumps(item, indent=2, sort_keys=True) + "\n")
        data["work_items"].remove(item); changed = True

    if changed:
        item["updated_at"] = now_iso()
        save_registry(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
