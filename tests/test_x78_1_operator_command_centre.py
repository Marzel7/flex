from pathlib import Path


SOURCE = (Path(__file__).resolve().parents[1] / "templates/operator_intelligence.html").read_text()


def test_command_centre_sections_are_on_confirmed_summary():
    summary = SOURCE.split('data-panel="summary"', 1)[1].split('data-panel="evidence"', 1)[0]
    for text in (
        "Current Operational Snapshot",
        "Why we're confident",
        "Current Intelligence",
        "Expansion Queue",
        "Operational Evolution",
        "Identity Governance",
    ):
        assert text in summary


def test_snapshot_uses_explicit_operational_units():
    for label in (
        "Current Status",
        "Current Activity",
        "Last Confirmed Launch",
        "Last Treasury Added",
        "Campaigns",
        "Treasury Families",
        "Current Active Treasuries",
        "Current Provisioning Controllers",
        "Current Expansion Candidates",
    ):
        assert label in SOURCE


def test_confidence_is_evidence_backed_not_percentage_ranked():
    assert "Evidence-backed identity" in SOURCE
    assert "Unresolved contradictions" in SOURCE
    assert "Recorded identity observations" not in SOURCE  # label is the explicit unit
    summary = SOURCE.split('id="oi-confidence-list"', 1)[1].split('id="oi-current-intelligence"', 1)[0]
    assert "%" not in summary


def test_expansion_queue_uses_persisted_operator_link_only():
    assert "/api/ops/treasury-review?status=PENDING_REVIEW" in SOURCE
    assert "item.related_identity.operator_id === OP_ID" in SOURCE
    assert "not inferred from similarity" in SOURCE


def test_relationships_and_navigation_are_integrated():
    assert "Representative Relationships" in SOURCE
    for href in (
        'href="/ops-os"',
        'href="/intelligence/treasury-review"',
        'href="#oi-expansion-queue"',
        'href="/intelligence/operators"',
    ):
        assert href in SOURCE


def test_operational_evolution_is_newest_first():
    assert "return out.sort(function(a,b){return (b.ts||0)-(a.ts||0);});" in SOURCE
