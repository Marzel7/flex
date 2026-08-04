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
    "UNRESOLVED": "Investigation Population",
    "REJECTED": "Rejected",
    "RETIRED": "Retired",
}


def _potential_operator_clusters(family: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Project only explicitly persisted child-cluster candidates.

    Treasury families and topology variants are deliberately not promoted into
    clusters here: shared infrastructure is not evidence of common control.
    The aliases keep the presentation contract forward-compatible with a later
    population builder without requiring a B48k-specific branch.
    """
    raw = family.get("potential_operator_clusters") or family.get("candidate_clusters") or ()
    clusters: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            continue
        cluster_id = item.get("cluster_id") or item.get("family_id") or item.get("id")
        if not cluster_id:
            continue
        clusters.append({
            "cluster_id": str(cluster_id),
            "launch_count": int(item.get("launch_count") or item.get("launches") or 0),
            "creator_count": int(item.get("creator_count") or item.get("creators") or 0),
            "current_evidence": item.get("current_evidence") or item.get("reasoning_summary"),
            "current_disposition": item.get("current_disposition") or item.get("disposition") or "UNRESOLVED",
            "supporting_observations": list(item.get("supporting_observations") or ()),
            "missing_evidence": list(item.get("missing_evidence") or ()),
            "profile_href": item.get("profile_href"),
            "display_order": index,
        })
    return clusters


def _investigation_summary(
    family: Mapping[str, Any], reconciliation: Mapping[str, Any]
) -> list[str]:
    launches = int(family.get("launches") or family.get("observed_launches") or 0)
    supporting = int(reconciliation.get("supporting_evidence_count") or 0)
    missing = int(reconciliation.get("missing_evidence_count") or 0)
    lines = [
        f"This bounded population contains {launches} persisted launches and remains active for investigation."
        if launches else "This bounded evidence population remains active for investigation."
    ]
    if supporting:
        lines.append(
            f"Reconciliation retained {supporting} supporting observations without treating shared evidence as proof of common control."
        )
    if missing:
        lines.append(
            f"The current package records {missing} evidence gaps, so no child cluster is eligible for confirmation."
        )
    else:
        lines.append("No independently supported child cluster is currently eligible for confirmation.")
    return lines

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
    clusters = _potential_operator_clusters(family) if kind == "investigation_population" else []
    title_prefix = {
        "REVIEW": "Review Population",
        "INFRASTRUCTURE": "Infrastructure Record",
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
        "parent_population_id": family_id if kind == "investigation_population" else family.get("parent_population_id"),
        "potential_operator_clusters": clusters,
        "child_operations": list(family.get("child_operations") or ()),
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
        "disposition_label": disposition.replace("_", " ").title(),
        "historical_context": (
            f"Previously presented through the legacy registry as {legacy.replace('_', ' ').title()}. "
            "The reconciled disposition now governs analyst presentation."
            if reconciliation.get("expected_difference") else None
        ),
        "investigation_summary": (
            _investigation_summary(family, reconciliation)
            if kind == "investigation_population" else []
        ),
    }


def attribution_presentation(assignment: Mapping[str, Any] | None) -> dict[str, Any]:
    assignment = assignment or {}
    family = {
        "family_id": assignment.get("family_id"),
        "family_name": assignment.get("operation_name"),
        "lifecycle_state": assignment.get("lifecycle"),
    }
    return reconciliation_presentation(family, assignment.get("reconciliation"))
