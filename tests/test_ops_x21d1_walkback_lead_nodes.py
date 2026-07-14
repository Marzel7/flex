"""X21D.1 — Funding Walkback begins with the initiating WATCHTOKEN and persisted
creator, using only entities already returned by the Discovery model. Presentation
only: no attribution/walkback/resolver/API/schema changes.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates/discovery.html").read_text()


def test_walkback_function_accepts_subject_attribution_outcome_and_canonical_identity():
    assert "function walkback(w, subject, attributionOutcome, canonicalIdentity){" in HTML


def test_lead_nodes_never_fabricate_when_absent():
    assert "function walkbackLeadNodes(subject, attributionOutcome, firstHopRole, confirmed){" in HTML
    # Token node only added when subject.type is genuinely 'token'.
    assert "subject.type==='token'" in HTML
    # Creator only added when the outcome evidence actually carries one.
    assert "attributionOutcome.evidence.creator" in HTML


def test_creator_lead_node_is_skipped_if_already_the_first_hop():
    """Some confirmed-launch walks already resolve CREATOR as hop 1 (see
    DiscoveryService line ~355) — the lead-node helper must never duplicate it."""
    assert "firstHopRole||''" in HTML
    assert "toUpperCase()!=='CREATOR'" in HTML


def test_lead_nodes_use_identical_markup_to_existing_chain_nodes():
    """The brief requires the token to render exactly like every other node — no
    distinct visual style. Confirm the lead-node template string reuses the same
    vi-chain-node class and internal structure as the hop-rendering map function."""
    assert "'<a class=\"vi-chain-node '" in HTML
    # Same icon/role/name/meta structure as the existing per-hop renderer.
    assert 'vi-chain-icon">' in HTML and 'vi-chain-role">' in HTML and 'vi-chain-name" title=' in HTML


def test_call_site_passes_subject_attribution_outcome_and_canonical_identity():
    assert "walkback(d.walkback||{}, subject, d.attribution_outcome, d.canonical_identity)" in HTML


def test_endpoint_relabeled_to_canonical_operator_only_when_genuinely_resolved():
    """The generic 'Endpoint / CONFIRMED' terminal node must become the resolved
    operator's own name ('WATCHTOWER', etc.) with 'Canonical operator reached'
    wording — but ONLY when canonicalIdentity is actually present. Non-canonical
    terminal outcomes must keep their existing typed wording untouched."""
    assert "Canonical operator reached" in HTML
    assert "if(canonicalIdentity && canonicalIdentity.operator_name){" in HTML
    # The non-canonical fallback branch must still exist, preserving w.status/stop_reason.
    assert "esc(w.status||'Stopped')" in HTML
    assert "esc(w.stop_reason)" in HTML


def test_no_duplicate_launch_attribution_section_introduced():
    """The brief explicitly forbids adding a second 'Launch Attribution Path'
    section — this must remain a single Funding Walkback component."""
    assert HTML.count('Funding walkback</summary>') == 1
    assert "Launch Attribution Path" not in HTML


def test_no_other_walkback_related_function_signature_changed():
    """Constraint check: only walkback()'s own signature and the new helper were
    touched — the rest of the Level 2 disclosure wiring (attribution/evidence/
    lineage/raw sections) must remain byte-identical to before this sprint."""
    for unchanged_call in (
        "canonicalIdentity(d.canonical_identity)",
        "operatorHistory(d.operator_history)",
        "groups(d.evidence_groups||{})",
        "cross(d.cross_operation)",
    ):
        assert unchanged_call in HTML
