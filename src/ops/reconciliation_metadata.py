"""Additive, versioned API projection of immutable reconciliation results.

This module never changes attribution.  It builds an optional presentation-safe
summary from the X69 shadow pipeline; callers omit the object on any failure.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Mapping, Sequence

from src.ops.disposition_resolver import (
    INFRASTRUCTURE,
    OPERATOR_CANDIDATE,
    REJECTED,
    REVIEW,
    UNRESOLVED,
    DispositionResolver,
)
from src.ops.evidence_reconciliation import EvidenceReconciliationService
from src.ops.evidence_semantics import EvidenceSemanticsService


RECONCILIATION_SCHEMA_VERSION = "operation-reconciliation-v1"
_METADATA_CACHE: dict[tuple[str, str], tuple[float, dict[str, dict[str, Any]]]] = {}
_METADATA_BUILD_LOCK = threading.RLock()


def clear_reconciliation_metadata_cache(ops_db_path: str | None = None) -> None:
    with _METADATA_BUILD_LOCK:
        for key in list(_METADATA_CACHE):
            if ops_db_path is None or key[0] == ops_db_path:
                _METADATA_CACHE.pop(key, None)


def _expected_difference(comparison, result) -> bool:
    """Classify only evidence-explained legacy/shadow transitions."""
    if comparison.agreement:
        return False
    if result.disposition in {INFRASTRUCTURE, REJECTED, REVIEW}:
        return bool(result.contradictory_evidence)
    if result.disposition == OPERATOR_CANDIDATE:
        return any(
            item.evidence_type == "CREATOR_REUSE_CONTROL"
            for item in result.supporting_evidence
        )
    if result.disposition == UNRESOLVED:
        return bool(result.missing_evidence)
    return False


def _reasoning_summary(result) -> str:
    # The opening resolver line contains immutable internal identifiers already
    # represented by dedicated fields.  The final factual step is the useful API
    # summary and avoids exposing the developer workspace's full reasoning trace.
    return result.reasoning_chain[-1]


def _observation_count(details: Mapping[str, Any], launches: Sequence[str]) -> int | None:
    for key in (
        "observation_count", "session_count", "distinct_mint_count",
        "launch_count", "active_session_count",
    ):
        value = details.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return len(launches) or None


def _evidence_projection(item, semantic) -> dict[str, Any]:
    provenance = item.provenance
    origin = (
        "RPC-confirmed transaction" if provenance.rpc
        else provenance.registry or provenance.table or provenance.source
    )
    return {
        "evidence_type": item.evidence_type,
        "label": item.evidence_type.replace("_", " ").title(),
        "description": item.statement,
        "truth_status": semantic.truth,
        "applicability": semantic.applicability,
        "applicability_reason": semantic.applicability_reason,
        "provenance": str(origin).replace("_", " "),
        "provenance_independence": semantic.provenance_independence,
        "dependency_group": provenance.dependency_group,
        "observation_count": (
            None if item.role == "MISSING"
            else _observation_count(item.details, item.launches)
        ),
        "eligible": semantic.eligible,
    }


def _promotion_readiness(package, result, evidence: dict[str, tuple[dict[str, Any], ...]]) -> dict[str, Any]:
    disposition = result.disposition
    missing = evidence["missing"]
    contradictory = evidence["contradictory"]
    population_types = {"TREASURY_RELATIONSHIPS", "PROVISIONING_LINEAGE", "PROVISIONING_SESSIONS"}
    has_population = any(item.evidence_type in population_types for item in package.supporting_evidence)
    if disposition == "OPERATOR_CANDIDATE":
        state = "READY FOR CONFIRMATION"
        message = "Eligible for analyst confirmation."
    elif disposition == "REVIEW":
        state = "READY FOR REVIEW"
        message = "Contradictions must be resolved before confirmation."
    elif disposition == "CONFIRMED_OPERATION":
        state = "READY FOR CONFIRMATION"
        message = "Confirmation is supported by persisted canonical promotion history."
    elif disposition == "UNRESOLVED" and has_population:
        state = "PARTIALLY READY"
        message = "Additional independent evidence is required before confirmation."
    else:
        state = "NOT ELIGIBLE"
        message = "This disposition is not eligible for analyst confirmation."

    blockers: list[str] = []
    requirements: list[str] = []
    if disposition not in {"CONFIRMED_OPERATION", "OPERATOR_CANDIDATE"}:
        blockers.extend(item["description"] for item in contradictory)
        blockers.extend(item["description"] for item in missing)
        requirements.extend(item["description"] for item in missing)
        if disposition == "UNRESOLVED":
            blockers.insert(0, "No eligible control-bearing evidence independently establishes a single controller.")
    return {
        "state": state,
        "eligible_for_confirmation": disposition == "OPERATOR_CANDIDATE",
        "message": message,
        "blockers": list(dict.fromkeys(blockers)),
        "requirements": list(dict.fromkeys(requirements)),
    }


def project_reconciliation_metadata(package, result, comparison) -> dict[str, Any]:
    """Return the stable public metadata schema; no internal models leak out."""
    semantics = EvidenceSemanticsService.evaluate(package)
    semantic_by_id = {
        item.evidence.evidence_id: item for item in semantics.observations
    }
    evidence = {
        role: tuple(
            _evidence_projection(item, semantic_by_id[item.evidence_id])
            for item in items
        )
        for role, items in (
            ("supporting", package.supporting_evidence),
            ("contradictory", package.contradictory_evidence),
            ("missing", package.missing_evidence),
        )
    }
    why_population_exists = [
        item.statement for item in package.explainability.why_population_exists
    ]
    analyst_explanation = why_population_exists + list(result.reasoning_chain[1:])
    return {
        "schema_version": RECONCILIATION_SCHEMA_VERSION,
        "population_revision_id": package.population.revision_id,
        "reconciliation_package_id": package.package_id,
        "disposition": result.disposition,
        "reasoning_summary": _reasoning_summary(result),
        "supporting_evidence_count": len(package.supporting_evidence),
        "contradictory_evidence_count": len(package.contradictory_evidence),
        "missing_evidence_count": len(package.missing_evidence),
        "dependency_groups": list(result.dependency_groups_consulted),
        "deterministic_result_id": result.result_id,
        "legacy_shadow_agreement": comparison.agreement,
        "expected_difference": _expected_difference(comparison, result),
        "supporting_evidence": list(evidence["supporting"]),
        "contradictory_evidence": list(evidence["contradictory"]),
        "missing_evidence": list(evidence["missing"]),
        "why_population_exists": why_population_exists,
        "analyst_explanation": analyst_explanation,
        "promotion_readiness": _promotion_readiness(package, result, evidence),
    }


def _build_reconciliation_metadata_uncached(
    registry,
    families: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Best-effort metadata indexed by family ID.

    Absence, incomplete fixtures, or package-generation failures deliberately
    produce no entry.  Attribution output remains authoritative and unchanged.
    """
    cache_key = (str(registry.ops_db_path), str(registry.live_db_path))
    cached = _METADATA_CACHE.get(cache_key)
    if cached and time.monotonic() - cached[0] < registry.refresh_seconds:
        return cached[1]
    family_by_id = {
        str(family.get("family_id")): family
        for family in families if family.get("family_id")
    }
    try:
        with registry._connect(registry.ops_db_path) as conn:
            populations = registry._population_builder().build(
                registry._discovery_profiles(conn, registry._tables(conn))
            )
        reconciler = EvidenceReconciliationService(registry.ops_db_path)
    except Exception:
        return {}

    sources = list(populations)
    for family in families:
        if family.get("is_canonical_operator"):
            try:
                sources.append(reconciler.population_from_canonical_registry(family))
            except (KeyError, TypeError, ValueError):
                continue

    metadata: dict[str, dict[str, Any]] = {}
    for population in sources:
        family = family_by_id.get(population.population_id)
        if family is None:
            continue
        try:
            package = reconciler.build(population)
            result = DispositionResolver.resolve(package)
            comparison = DispositionResolver.compare_legacy(
                str(family.get("stage") or "BACKGROUND"), result
            )
            metadata[population.population_id] = project_reconciliation_metadata(
                package, result, comparison
            )
        except Exception:
            # Optional means a single incomplete population cannot affect any
            # legacy API response or suppress metadata for valid populations.
            continue
    _METADATA_CACHE[cache_key] = (time.monotonic(), metadata)
    return metadata


def build_reconciliation_metadata(
    registry,
    families: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Return cached metadata or run exactly one cold build at a time."""
    cache_key = (str(registry.ops_db_path), str(registry.live_db_path))
    cached = _METADATA_CACHE.get(cache_key)
    if cached and time.monotonic() - cached[0] < registry.refresh_seconds:
        return cached[1]
    with _METADATA_BUILD_LOCK:
        # The helper repeats the cache lookup after waiting, so followers use
        # the first request's result rather than launching duplicate builds.
        return _build_reconciliation_metadata_uncached(registry, families)
