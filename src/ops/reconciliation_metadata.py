"""Additive, versioned API projection of immutable reconciliation results.

This module never changes attribution.  It builds an optional presentation-safe
summary from the X69 shadow pipeline; callers omit the object on any failure.
"""
from __future__ import annotations

import time
from typing import Any, Mapping, Sequence

from src.ops.disposition_resolver import (
    INFRASTRUCTURE,
    REJECTED,
    REVIEW,
    UNRESOLVED,
    DispositionResolver,
)
from src.ops.evidence_reconciliation import EvidenceReconciliationService


RECONCILIATION_SCHEMA_VERSION = "operation-reconciliation-v1"
_METADATA_CACHE: dict[tuple[str, str], tuple[float, dict[str, dict[str, Any]]]] = {}


def clear_reconciliation_metadata_cache(ops_db_path: str | None = None) -> None:
    for key in list(_METADATA_CACHE):
        if ops_db_path is None or key[0] == ops_db_path:
            _METADATA_CACHE.pop(key, None)


def _expected_difference(comparison, result) -> bool:
    """Classify only evidence-explained legacy/shadow transitions."""
    if comparison.agreement:
        return False
    if result.disposition in {INFRASTRUCTURE, REJECTED, REVIEW}:
        return bool(result.contradictory_evidence)
    if result.disposition == UNRESOLVED:
        return bool(result.missing_evidence)
    return False


def _reasoning_summary(result) -> str:
    # The opening resolver line contains immutable internal identifiers already
    # represented by dedicated fields.  The final factual step is the useful API
    # summary and avoids exposing the developer workspace's full reasoning trace.
    return result.reasoning_chain[-1]


def project_reconciliation_metadata(package, result, comparison) -> dict[str, Any]:
    """Return the stable public metadata schema; no internal models leak out."""
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
    }


def build_reconciliation_metadata(
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
