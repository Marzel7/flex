"""Provider-free contract primitives for explicit REVIEW -> ADMIT approval.

This is deliberately not a web endpoint or live writer.  A future adapter must
provide an authenticated server-side principal and persist the returned
approval/state identities before invoking the existing ADMIT-only resumer.
"""
from __future__ import annotations

import hashlib
import json
from typing import Mapping

VERSION = "MANUAL_REVIEW_APPROVAL_V1"
REQUIRED_PERMISSION = "operations.deep.review.approve"


def _digest(value: Mapping) -> str:
    return hashlib.sha256(json.dumps(dict(value), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def review_state_token(row: Mapping) -> str:
    """Bind approval to immutable current REVIEW state, never membership."""
    required = ("operation_id", "mint", "candidate_id", "review_outcome_id", "assessment_id",
                "assessment_semantic_version", "policy_id", "policy_version", "admission_result")
    body = {key: row.get(key) for key in required}
    if any(body[key] in (None, "") for key in required) or body["admission_result"] != "REVIEW":
        raise ValueError("MANUAL_APPROVAL_REVIEW_STATE_INVALID")
    return _digest(body)


def approval_identity(row: Mapping, *, approver_id: str, action: str) -> str:
    if action not in {"APPROVE", "DECLINE"} or not approver_id:
        raise ValueError("MANUAL_APPROVAL_ACTION_OR_APPROVER_INVALID")
    return _digest({"version": VERSION, "state_token": review_state_token(row),
                    "approver_id": approver_id, "action": action})


def authorize_request(*, principal: Mapping, row: Mapping, supplied_token: str,
                      action: str, membership_exists: bool) -> dict:
    """Pure precommit gate. Callers must re-read state in their write transaction."""
    if not principal.get("authenticated") or REQUIRED_PERMISSION not in set(principal.get("permissions", ())):
        raise PermissionError("MANUAL_APPROVAL_UNAUTHORIZED")
    current = review_state_token(row)
    if supplied_token != current:
        raise ValueError("MANUAL_APPROVAL_STALE_REVIEW")
    if membership_exists:
        return {"result": "ALREADY_CANONICAL", "approval_id": None, "admission": None}
    if action == "DECLINE":
        return {"result": "DECLINED", "approval_id": approval_identity(row, approver_id=principal["id"], action=action), "admission": None}
    if action != "APPROVE":
        raise ValueError("MANUAL_APPROVAL_ACTION_INVALID")
    return {"result": "APPROVED", "approval_id": approval_identity(row, approver_id=principal["id"], action=action),
            "admission": {"policy_id": "MANUAL_REVIEW_ADMISSION_POLICY", "policy_version": "v1", "admission_result": "ADMIT", "state_token": current}}
