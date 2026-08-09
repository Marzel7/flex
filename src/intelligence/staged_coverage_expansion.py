"""Deterministic OIP v2.1E migration-first target selection."""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CoverageTarget:
    manifest_position: int
    signature: str
    launch: str
    dependency_type: str
    current_completeness: str
    expected_completion_effect: str
    already_known_dependency_state: str = "MISSING"


def _order_key(launch) -> tuple[str, str]:
    return hashlib.sha256(launch.mint.encode()).hexdigest(), launch.mint


def construct_manifest(coverage, limit: int = 1_000) -> tuple[list[CoverageTarget], dict]:
    migration_only = sorted((row for row in coverage if row.reason == "MISSING_MIGRATION_TRANSACTION"), key=_order_key)
    creation_only = sorted((row for row in coverage if row.reason == "MISSING_CREATION_TRANSACTION"), key=_order_key)
    both = sorted((row for row in coverage if row.reason == "MISSING_CREATION_AND_MIGRATION_TRANSACTION"), key=_order_key)
    raw: list[tuple[str, str, str, str, str]] = []
    for row in migration_only:
        raw.append((row.migration_signature, row.mint, "MIGRATION", row.reason, "COMPLETE_LAUNCH"))
    for row in creation_only:
        raw.append((row.creation_signature, row.mint, "CREATION", row.reason, "COMPLETE_LAUNCH"))
    for row in both:
        if len(raw) >= limit:
            break
        raw.append((row.migration_signature, row.mint, "MIGRATION", row.reason, "REDUCE_DEPENDENCY_DEFICIT"))
        if len(raw) >= limit:
            break
        raw.append((row.creation_signature, row.mint, "CREATION", row.reason, "COMPLETE_LAUNCH"))
    targets = [CoverageTarget(position, signature, launch, dependency, completeness, effect)
               for position, (signature, launch, dependency, completeness, effect)
               in enumerate(raw[:limit], 1)]
    manifest = {"milestone": "OIP v2.1E", "selection_method": "MIGRATION_FIRST_STABLE_SHA256_PAIRED_V1",
        "physical_attempt_limit": limit, "target_count": len(targets),
        "direct_completion_targets": len(migration_only) + len(creation_only),
        "paired_missing_both_launches": sum(target.dependency_type == "CREATION" and
            target.current_completeness == "MISSING_CREATION_AND_MIGRATION_TRANSACTION" for target in targets),
        "completion_opportunities": sum(target.expected_completion_effect == "COMPLETE_LAUNCH" for target in targets),
        "dependency_counts": {kind: sum(target.dependency_type == kind for target in targets)
                              for kind in ("MIGRATION", "CREATION")},
        "targets": [asdict(target) for target in targets]}
    return targets, manifest
