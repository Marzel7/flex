from pathlib import Path

from src.ops.potential_operations import _attach_current_evidence, _decorate, evolution_watch


ROOT = Path(__file__).resolve().parents[1]


def test_templates_surface_explicit_evolution_and_focus_contracts():
    page = (ROOT / "templates/potential_operations.html").read_text()
    detail = (ROOT / "templates/potential_operation_detail.html").read_text()
    for text in ("Evolution Watch", "75 near → 2 qualifying clusters → 2 admitted variants", "97 related → 0 qualifying clusters → 0 admitted candidates", "No Potential candidate currently attributable to Harbinger.", "FOCUS NEXT", "Why now:", "Evolution Candidates", "Attention #", "Variant of Sentinel", "Provisional operation"):
        assert text in page or text in (ROOT / "src/ops/potential_operations.py").read_text()
    assert "Related Confirmed Operation: Sentinel" in detail
    assert "Why this is currently prioritized" in detail


def test_relationships_and_current_attention_are_read_only_projection():
    provisional = _decorate(_attach_current_evidence({"candidate_id":"900","workflow_status":"ACTIVE_PROVISIONAL"}, {}))
    unresolved = _decorate(_attach_current_evidence({"candidate_id":"x","workflow_status":"QUEUED","key_mechanism":"hop-1 PLAIN_XFER | hop-2 PLAIN_XFER"}, {}))
    sentinel = _decorate(_attach_current_evidence({"candidate_id":"s","workflow_status":"QUEUED","latest_verdict":"POTENTIAL_VARIANT_OF_SENTINEL","proposed_name":"Potential variant of Sentinel · 10 SOL Ladder"}, {}))
    assert provisional["relationship_label"] == "Provisional operation"
    assert unresolved["relationship_label"] == "Unresolved" and unresolved["compact_mechanism"] == "2-hop transfer sequence"
    assert evolution_watch([sentinel])["sentinel_variants"] == [sentinel]
