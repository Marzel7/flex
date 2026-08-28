from pathlib import Path

from src.ops.potential_operations import _decorate, _presentation_name, evolution_watch


ROOT = Path(__file__).resolve().parents[1]


def test_potential_page_uses_shared_cards_and_structured_sections():
    page = (ROOT / "templates/potential_operations.html").read_text()
    for text in ("ip-strip", "ip-metric", "Focus Next", "po-focus-grid", "Evolution Watch", "Qualified clusters", "Evolution Candidates", "Actionable Unresolved Candidates", "Attention", "Evidence", "Relationship", "Action"):
        assert text in page
    assert "Mechanism</th>" not in page
    assert "Discovery</th>" not in page


def test_candidate_names_are_mechanism_derived_and_relationship_free():
    assert _presentation_name({"key_mechanism": "hop-1 PLAIN_XFER | hop-2 PLAIN_XFER"}) == "2-hop Transfer Sequence"
    assert _presentation_name({"key_mechanism": "hop-1 PLAIN_XFER 10000 lamports"}) == "10K-lamport Direct Transfer"
    sentinel = _decorate({"candidate_id": "s", "workflow_status": "QUEUED", "latest_verdict": "POTENTIAL_VARIANT_OF_SENTINEL", "proposed_name": "Potential variant of Sentinel · 10 SOL Ladder"})
    assert sentinel["display_descriptor"] == "10 SOL Ladder Variant"
    assert not sentinel["display_descriptor"].startswith("Unresolved")


def test_evolution_projection_retains_two_rows_and_read_only_actions():
    variants = [
        {"candidate_id": "a", "latest_verdict": "POTENTIAL_VARIANT_OF_SENTINEL"},
        {"candidate_id": "b", "latest_verdict": "POTENTIAL_VARIANT_OF_SENTINEL"},
    ]
    watch = evolution_watch(variants)
    assert len(watch["sentinel_variants"]) == 2
    assert watch["harbinger"] == {"related_observations": 97, "qualifying_clusters": 0, "admitted_candidates": 0, "operator_id": watch["harbinger"]["operator_id"]}
    assert _decorate({"candidate_id": "p", "workflow_status": "ACTIVE_PROVISIONAL"})["action_label"] == "Review evidence →"
