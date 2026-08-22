"""Non-authoritative operational fingerprints and higher-order eligibility.

This module deliberately does not cluster or promote launches.  It makes the
input and lineage requirements for a later, local candidate generator explicit:
shared direct funding is a FUNDING_STRUCTURE signal only, never operation
identity evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping


class EvidenceLineage(str, Enum):
    INDEPENDENT = "INDEPENDENT"
    SHARED_SOURCE_LINEAGE = "SHARED_SOURCE_LINEAGE"
    DERIVED_FROM_SAME_PRIMITIVE = "DERIVED_FROM_SAME_PRIMITIVE"
    UNKNOWN_LINEAGE = "UNKNOWN_LINEAGE"


@dataclass(frozen=True)
class OperationalFingerprint:
    mint: str
    create_creator: str | None = None
    migration_signer: str | None = None
    direct_funder: str | None = None
    upstream_funders: tuple[str, ...] = ()
    topology_refs: tuple[str, ...] = ()
    behaviour_refs: tuple[str, ...] = ()
    timing_refs: tuple[str, ...] = ()
    source_lineage: Mapping[str, EvidenceLineage] | None = None

    def available_families(self) -> frozenset[str]:
        values = set()
        if self.direct_funder or self.upstream_funders:
            values.add("FUNDING")
        if self.topology_refs:
            values.add("TOPOLOGY")
        if self.behaviour_refs:
            values.add("BEHAVIOUR")
        if self.timing_refs:
            values.add("TIMING")
        if self.migration_signer:
            values.add("MIGRATION_ACTOR")
        return frozenset(values)


def higher_order_eligibility(fingerprints: Iterable[OperationalFingerprint]) -> dict:
    """Explain whether a set can be considered by a later candidate generator.

    The gate is intentionally qualitative rather than a numeric score. At least
    one topology and one behaviour signal must be present, and both must have
    explicitly independent source lineage. Funding can be supporting context
    only. This prevents direct-funder, CEX, provisioner, or signer-only merges.
    """
    values = tuple(fingerprints)
    family_counts = {name: sum(name in item.available_families() for item in values)
                     for name in ("FUNDING", "TOPOLOGY", "BEHAVIOUR", "TIMING", "MIGRATION_ACTOR")}
    lineages = [
        lineage for item in values for lineage in (item.source_lineage or {}).values()
    ]
    independent = sum(lineage is EvidenceLineage.INDEPENDENT for lineage in lineages)
    missing = []
    if not family_counts["TOPOLOGY"]:
        missing.append("EP3_POPULATION_GAP")
    if not family_counts["BEHAVIOUR"]:
        missing.append("EP4_LAUNCH_MAPPING_GAP")
    if not family_counts["MIGRATION_ACTOR"]:
        missing.append("MIGRATION_SIGNER_COVERAGE_GAP")
    if independent < 2:
        missing.append("INDEPENDENT_EVIDENCE_LINEAGE_GAP")
    eligible = not missing
    return {
        "eligible": eligible,
        "classification": "NON_CANONICAL_OPERATION_CANDIDATE" if eligible else "NOT_ELIGIBLE_DATA_GAP",
        "family_coverage": family_counts,
        "independent_lineage_observations": independent,
        "missing_gates": tuple(missing),
        "authority": "NON_AUTHORITATIVE_NO_PROMOTION",
    }
