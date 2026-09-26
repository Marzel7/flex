"""Compact, operation-agnostic signature-to-slot evidence primitives."""
from __future__ import annotations

import hashlib
import json
from typing import Mapping


SLOT_VERSION = "KNOWN_SIGNATURE_SLOT_V1"


def slot_evidence_identity(record: Mapping) -> str:
    """Stable identity over compact authoritative slot evidence only."""
    fields = {key: record.get(key) for key in (
        "signature", "slot", "source_identity", "acquisition_identity", "semantic_version",
    )}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def compact_slot_record(signature: str, response: Mapping | None, *, source_identity: str,
                        acquisition_identity: str, acquired_at: int) -> dict:
    """Validate a known-signature result without retaining its raw response."""
    result = (response or {}).get("result")
    if not isinstance(result, Mapping) or not isinstance(result.get("slot"), int):
        return {"state": "INSUFFICIENT_EVIDENCE", "reason": "missing_authoritative_slot"}
    observed = ((result.get("transaction") or {}).get("signatures") or [])
    if signature not in observed:
        return {"state": "INSUFFICIENT_EVIDENCE", "reason": "signature_not_in_response"}
    record = {"signature": signature, "slot": result["slot"], "source_identity": source_identity,
              "acquisition_identity": acquisition_identity, "semantic_version": SLOT_VERSION,
              "acquired_at": acquired_at}
    record["evidence_id"] = slot_evidence_identity(record)
    return {"state": "QUALIFIED", "record": record}


def compare_slots(parent: Mapping, child: Mapping) -> dict:
    """Stage one only: no ordinal inference and no operation-specific behavior."""
    if parent.get("slot") is None or child.get("slot") is None:
        return {"state": "UNKNOWN", "getblock_allowed": False}
    if int(parent["slot"]) < int(child["slot"]):
        return {"state": "PARENT_BEFORE_CHILD", "getblock_allowed": False}
    if int(parent["slot"]) > int(child["slot"]):
        return {"state": "PARENT_AFTER_CHILD", "getblock_allowed": False}
    return {"state": "SAME_SLOT", "getblock_allowed": True}
