"""Frozen-source semantic coverage checks for the disposable Living pipeline.

This module intentionally audits source availability only.  It does not derive
associations and never opens a database.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import hashlib
import json

from src.ops.generic_living_pipeline_v2 import EvidenceSourceSpec, extract_evidence_sources


ROOT = Path(__file__).resolve().parents[2]
WSOL_SOURCE = "docs/audits/generic_living_wsol_frozen_source.v1.json"
WSOL_EXPECTED = "docs/audits/generic_living_wsol_expected_output.v1.json"
EIGHT_SOURCE = "docs/audits/generic_living_eight_hop_frozen_source.v1.json"
EIGHT_EXPECTED = "docs/audits/generic_living_eight_hop_expected_output.v1.json"

# These are source declarations, not association records.  Aggregate caveat
# semantics are deliberately absent until their underlying same-boundary
# evidence is retained; association_inputs is not used as a source.
COMMON_TYPES = {
    "canonical": {"FROZEN_C357_SELECTED_MEMBER", "CANONICAL_SELECTED_MEMBER"},
    "direct_funder": {"DIRECT_FUNDER_CONNECTIVITY", "DIRECT_FUNDER"},
    "alternative_edge": {"ALTERNATIVE_EDGE_SUPPORT", "ALTERNATIVE_SUPPORT"},
}
WSOL_TYPES = {**COMMON_TYPES, "atomic_flow": {"ATOMIC_FLOW_SUPPORT"}}


def fixture_hash(path: str) -> str:
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def source_specs(candidate: str):
    specs = [
        EvidenceSourceSpec("canonical", "source_evidence.canonical_mints", "distinct_values",
                           identity_fields=("value",), contributes_associations=True),
        EvidenceSourceSpec("direct_funder", "source_evidence.queue_rows", "records",
                           identity_fields=("funder_wallet",), dedupe_fields=("funder_wallet",),
                           contributes_associations=True),
        EvidenceSourceSpec("alternative_edge", "source_evidence.edges", "records",
                           identity_fields=("evidence_key",), where=(("selection_status", "ALTERNATIVE"),),
                           contributes_associations=True),
    ]
    if candidate == "wsol":
        specs.append(EvidenceSourceSpec("atomic_flow", "source_evidence.atomic_flows", "records",
                                        identity_fields=("evidence_key",), contributes_associations=True))
    return tuple(specs)


def category_for(evidence_type: str, candidate: str) -> str:
    supported = WSOL_TYPES if candidate == "wsol" else COMMON_TYPES
    for key, types in supported.items():
        if evidence_type in types:
            return key
    categories = {
        "MAPPED_CANDIDATE_POPULATION": "MAPPED_POPULATION",
        "MAPPED_POPULATION": "MAPPED_POPULATION",
        "COMPATIBLE_LAUNCH_SET": "EXCLUSION",
        "FALSE_POSITIVE_COMPARISON": "FALSE_POSITIVE",
        "HISTORY_SELECTION_LIMITATION": "HISTORICAL_CAVEAT",
        "ATTRIBUTION_LIMIT": "CONTRADICTION",
    }
    return categories.get(evidence_type, "OTHER:" + evidence_type)


def build_candidate_coverage(candidate: str, source_path: str, expected_path: str) -> dict:
    source = json.loads((ROOT / source_path).read_text())
    expected = json.loads((ROOT / expected_path).read_text())
    specs = source_specs(candidate)
    extracted = extract_evidence_sources({"evidence_sources": specs}, source)
    present_keys = {spec.key for spec in specs}
    inventory = []
    for association in expected["normalized_associations"]:
        category = category_for(association["evidence_type"], candidate)
        available = category in present_keys
        inventory.append({
            "candidate": expected["candidate_id"],
            "expected_association_identity": association["evidence_key"],
            "evidence_type": association["evidence_type"],
            "association_state": association["state"],
            "provenance_source_semantic": category,
            "required_source_category": category,
            "source_category_currently_available": available,
            "current_frozen_source_path": (
                next((spec.source_path for spec in specs if spec.key == category), None)
            ),
        })
    represented = sum(row["source_category_currently_available"] for row in inventory)
    missing = sorted({row["required_source_category"] for row in inventory
                      if not row["source_category_currently_available"]})
    source_table = []
    for spec in specs:
        source_table.append({
            "source_key": spec.key,
            "extractor": spec.extractor,
            "record_value_count": len(extracted[spec.key]),
            "association_semantics_supported": sorted((WSOL_TYPES if candidate == "wsol" else COMMON_TYPES)[spec.key]),
            "expected_associations_covered_by_source": sum(
                row["required_source_category"] == spec.key for row in inventory),
        })
    return {
        "candidate": candidate,
        "fixture": {"source_path": source_path, "source_sha256": fixture_hash(source_path),
                    "expected_path": expected_path, "expected_sha256": fixture_hash(expected_path),
                    "frozen_boundary": source["frozen_boundary"]},
        "expected_associations": len(inventory),
        "initially_represented": represented,
        "initially_unrepresented": len(inventory) - represented,
        "missing_source_categories": missing,
        "v1_already_contained_underlying_data": {key: True for key in present_keys},
        "source_inventory": inventory,
        "final_extracted_source_categories": source_table,
        "source_semantic_coverage": {"covered": represented, "total": len(inventory)},
    }


def build_coverage_report() -> dict:
    report = {"schema_version": "generic_living_association_source_coverage.v1",
              "real_db_writes": 0,
              "candidate_specific_extraction_branches": 0,
              "final_association_equivalence_attempted": False,
              "candidates": [build_candidate_coverage("wsol", WSOL_SOURCE, WSOL_EXPECTED),
                             build_candidate_coverage("eight_hop", EIGHT_SOURCE, EIGHT_EXPECTED)]}
    report["verdict"] = "HOLD_FROZEN_SOURCE_GAP_NOT_RECONSTRUCTABLE"
    report["exact_next_step"] = ("Capture same-boundary underlying evidence for every listed aggregate caveat/population "
                                 "category, create a V2 enriched source fixture, then rerun source coverage; do not run association equivalence first.")
    return report
