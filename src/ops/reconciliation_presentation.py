"""Presentation-only projection of optional reconciliation metadata.

This module never resolves attribution or changes Registry authority.  It maps
the additive shadow metadata into explicit analyst-facing nouns and bounded UI
categories, falling back to labelled legacy context when metadata is absent.
"""
from __future__ import annotations

from typing import Any, Mapping


LABELS = {
    "CONFIRMED_OPERATION": "Confirmed Operation",
    "OPERATOR_CANDIDATE": "Operator Candidate",
    "REVIEW": "Review Required",
    "INFRASTRUCTURE": "Shared Infrastructure",
    "UNRESOLVED": "Unresolved",
    "REJECTED": "Rejected",
    "RETIRED": "Retired",
}

KINDS = {
    "CONFIRMED_OPERATION": "operation",
    "OPERATOR_CANDIDATE": "operation_candidate",
    "REVIEW": "investigation_population",
    "INFRASTRUCTURE": "infrastructure",
    "UNRESOLVED": "investigation_population",
    "REJECTED": "investigation_population",
    "RETIRED": "investigation_population",
}


def reconciliation_presentation(
    family: Mapping[str, Any] | None,
    reconciliation: Mapping[str, Any] | None,
) -> dict[str, Any]:
    family = family or {}
    legacy = str(family.get("lifecycle_state") or family.get("stage") or "UNKNOWN")
    name = str(family.get("family_name") or family.get("operation_name") or "Unknown")
    family_id = family.get("family_id")
    if not reconciliation:
        return {
            "reconciled": False,
            "disposition": None,
            "label": f"Legacy attribution · {legacy.replace('_', ' ').title()}",
            "kind": "legacy_attribution",
            "title": name,
            "profile_kicker": "Legacy attribution",
            "confirmation_permitted": False,
            "legacy_lifecycle": legacy,
            "profile_href": f"/intelligence/operations/{family_id}" if family_id else None,
        }
    disposition = str(reconciliation.get("disposition") or "UNRESOLVED")
    kind = KINDS.get(disposition, "investigation_population")
    title_prefix = {
        "REVIEW": "Review Population",
        "INFRASTRUCTURE": "Infrastructure Record",
        "UNRESOLVED": "Investigation Population",
        "REJECTED": "Rejected Population",
        "RETIRED": "Retired Population",
    }.get(disposition)
    return {
        "reconciled": True,
        "disposition": disposition,
        "label": LABELS.get(disposition, disposition.replace("_", " ").title()),
        "kind": kind,
        "title": f"{title_prefix}: {name}" if title_prefix else name,
        "profile_kicker": (
            "Operation profile" if kind in {"operation", "operation_candidate"}
            else "Infrastructure record" if kind == "infrastructure"
            else "Investigation population"
        ),
        "confirmation_permitted": disposition == "OPERATOR_CANDIDATE",
        "legacy_lifecycle": legacy,
        "profile_href": f"/intelligence/operations/{family_id}" if family_id else None,
        "reasoning_summary": reconciliation.get("reasoning_summary"),
        "population_revision_id": reconciliation.get("population_revision_id"),
        "reconciliation_package_id": reconciliation.get("reconciliation_package_id"),
        "supporting_evidence_count": reconciliation.get("supporting_evidence_count", 0),
        "contradictory_evidence_count": reconciliation.get("contradictory_evidence_count", 0),
        "missing_evidence_count": reconciliation.get("missing_evidence_count", 0),
        "legacy_shadow_agreement": reconciliation.get("legacy_shadow_agreement"),
        "expected_difference": reconciliation.get("expected_difference"),
    }


def attribution_presentation(assignment: Mapping[str, Any] | None) -> dict[str, Any]:
    assignment = assignment or {}
    family = {
        "family_id": assignment.get("family_id"),
        "family_name": assignment.get("operation_name"),
        "lifecycle_state": assignment.get("lifecycle"),
    }
    return reconciliation_presentation(family, assignment.get("reconciliation"))
