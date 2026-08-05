from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def source():
    return (ROOT / "templates/operation_profile.html").read_text(encoding="utf-8")


def test_evidence_is_grouped_once_and_raw_semantics_are_advanced():
    page = source()
    assert "const groupedEvidence=" in page
    assert "groups[x.evidence_type||x.label||'EVIDENCE']" in page
    assert "observations" in page
    assert "Advanced Evidence" in page
    assert "provenance,x.dependency_group" in page
    assert "disclosure('Advanced Evidence'" in page


def test_missing_evidence_is_short_and_consistent():
    page = source()
    assert "replace(/\\bUnavailable\\b/gi,'')" in page
    assert 'class="rp-missing"' in page
    assert "<b>Missing</b>" in page
    missing = page.split("const missingSummary=", 1)[1].split(";", 1)[0]
    assert "description" not in missing
    assert "applicability_reason" not in missing


def test_wallets_are_hidden_behind_collapsed_disclosures():
    page = source()
    assert "disclosure('Wallet Membership'" in page
    assert "disclosure('Infrastructure Wallets'" in page
    assert "rp-wallets" in page
    assert "Infrastructure Wallets',infrastructureWallets.length" in page
    assert "treasury.slice(0,8)" in page


def test_all_profile_chronology_is_newest_first():
    page = source()
    assert "const newest=" in page
    for contract in (
        "newest(perf.launches||[],['created_at','timestamp'])",
        "newest(confirmation.history||[],['action_at','timestamp','created_at'])",
        "timeline=newest(intel.timeline||[],['timestamp','observed_at','created_at'])",
        "newest(audit.events||[],['timestamp','created_at'])",
        "newest(xs,['observed_at','timestamp','created_at'])",
    ):
        assert contract in page
    assert page.count("Newest first") >= 2


def test_linked_operator_governance_and_evidence_are_newest_first():
    page = (ROOT / "templates/operator_intelligence.html").read_text(encoding="utf-8")
    assert "(b.created_at || 0) - (a.created_at || 0)" in page
    assert "(b.ts || 0) - (a.ts || 0)" in page
    assert "(b.timestamp || 0) - (a.timestamp || 0)" in page
    assert "operator.promotion_history|reverse" in page


def test_analysis_is_a_stack_of_independent_summaries():
    page = source()
    for label in ("Topology", "Provisioning", "Walkback", "Behaviour", "Potential Clusters"):
        assert f"disclosure('{label}'" in page
    assert 'class="rp-analysis-stack"' in page
    assert "No walkback yet." in page
    assert "No treasury identified." in page


def test_default_summary_contains_only_five_analyst_answers():
    page = source()
    summary = page.split("const summary=", 1)[1].split(";", 1)[0]
    for answer in ("identityCard", "State", "Reason", "Promotion", "Next Evidence"):
        assert answer in summary
    for detail in ("support", "contradictions", "infrastructureWallets", "launchRows", "historyRows"):
        assert detail not in summary


def test_x74_2_remains_presentation_only():
    page = source()
    assert "fetch('/api/ops/emerging-operators/'" in page
    assert "fetch('/api/operators/promotions/'" in page
    assert "method:'POST'" in page
