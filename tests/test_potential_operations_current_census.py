import json
from pathlib import Path

from src.ops import potential_operations as po


ARTIFACT = Path("docs/audits/potential_operations_current_census_reconciliation.v1.json")


def test_frozen_census_reconciliation_is_complete_and_accounted():
    payload = json.loads(ARTIFACT.read_text())
    assert payload["frozen_highwaters"] == {
        "wt_walkback_queue": 35620,
        "wt_walkback_edge_candidates": 67599,
        "wt_walkback_atomic_flows": 7747,
    }
    assert payload["family_summary"] == {"coherent_families": 230, "existing_potential_matches": 61}
    assert sum(cluster["count"] for cluster in payload["sentinel_clusters"]) == 75
    assert sum(cluster["count"] for cluster in payload["harbinger_clusters"]) == 97
    assert sum(cluster["qualification_result"] == "PASSES_EXISTING_COHERENT_FAMILY_MINIMUMS" for cluster in payload["sentinel_clusters"]) == 2
    assert not any(cluster["qualification_result"] == "PASSES_EXISTING_COHERENT_FAMILY_MINIMUMS" for cluster in payload["harbinger_clusters"])
    assert payload["safety"] == {"membership_writes": 0, "provider_calls": 0, "source_writes": 0, "workflow_writes": 0}


def test_current_evidence_is_read_projection_not_discovery_rewrite():
    row = {"candidate_id": "candidate", "priority_rank": 7, "canonical_tier": "T2"}
    evidence = {"candidate": {"matches": 9, "current_evidence_state": "ACTIVE", "metrics": {"last_1d": 1, "last_7d": 4, "last_30d": 9}}}
    projected = po._attach_current_evidence(row, evidence)
    assert projected["priority_rank"] == 7
    assert projected["canonical_tier"] == "T2"
    assert projected["current_evidence"] == {
        "state": "ACTIVE", "matches": 9, "metrics": {"last_1d": 1, "last_7d": 4, "last_30d": 9},
        "attention": "HIGH", "attention_rank": 3,
        "reason": "1 / 4 / 9 current census activity; active fingerprint.",
    }
