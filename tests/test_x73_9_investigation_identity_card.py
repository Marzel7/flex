from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def source():
    return (ROOT / "templates/operation_profile.html").read_text(encoding="utf-8")


def test_summary_has_one_compact_population_identity_card():
    page = source()
    assert 'aria-label="Investigation summary"' in page
    assert 'class="rp-identity identity-' in page
    assert "rp-identity-badge" in page
    assert "rp-identity-icon" in page
    assert "Current Relationships" not in page
    assert "reasonFor" not in page


def test_identity_card_contains_only_identity_and_compact_counts():
    page = source()
    identity = page.split("identityCard=", 1)[1].split(";", 1)[0]
    metrics = page.split("identityMetricRows=", 1)[1].split(",identityMetrics=", 1)[0]
    for label in ("Launches", "Creators", "Provisioning Clients", "Treasury"):
        assert label in metrics
    assert "<p>" not in identity
    assert "explanation" not in identity.lower()


def test_infrastructure_separates_owned_identity_from_connected_treasuries():
    page = source()
    assert "Connected Treasury Context" in page
    assert "Connected Treasuries" in page
    assert "not treasuries owned by" in page
    assert "Connected upstream treasury" in page
    assert "Infrastructure Wallets" in page
    assert "launches_by_treasury" in page
    assert "launches reconciled" in page
    assert "Unattributed" in page


def test_connected_treasury_addresses_link_to_full_solscan_accounts():
    page = source()
    assert "const accountLink=value=>" in page
    assert 'https://solscan.io/account/' in page
    assert 'target="_blank" rel="noopener noreferrer"' in page
    treasury_context = page.split("const treasurySplit=", 1)[1].split(";\n", 1)[0]
    assert "accountLink(x.treasury)" in treasury_context
    assert "accountLink(w)" in treasury_context


def test_supported_identity_types_are_derived_from_existing_profile_data():
    page = source()
    for identity_type in (
        "Confirmed Operator", "Operational Treasury", "Provisioning Controller",
        "Shared Infrastructure", "Infrastructure", "Session Cluster", "Unknown",
    ):
        assert identity_type in page
    assert "walkback_descendant_count" in page
    assert "membersList.length>1&&treasuries.length>1" in page
    assert "clients.length&&creators.length" in page


def test_identity_palette_is_restrained_and_registry_aligned():
    page = source()
    for contract in (
        ".identity-confirmed{--identity-color:#22c55e}",
        ".identity-treasury{--identity-color:#818cf8}",
        ".identity-controller{--identity-color:#a78bfa}",
        ".identity-shared{--identity-color:#f97316}",
        ".identity-session{--identity-color:#eab308}",
        ".identity-unknown{--identity-color:#94a3b8}",
        "border-left:4px solid var(--identity-color)",
    ):
        assert contract in page


def test_summary_is_limited_to_identity_state_reason_and_next_evidence():
    page = source()
    summary = page.split("const overview=", 1)[1].split(";", 1)[0]
    assert "identityCard" in summary
    assert "State" in summary
    assert "Reason" in summary
    assert "Promotion" in summary
    assert "Missing evidence" in summary
    for duplicate in ("Current operator", "Parent investigation", "Child identities", "Actions"):
        assert duplicate not in summary
    assert "directRelationships+structuralMembership" in page


def test_promotion_workflow_remains_gated_and_outside_summary():
    page = source()
    assert "pres.confirmation_permitted&&disp==='OPERATOR_CANDIDATE'" in page
    assert "/api/operators/promotions/" in page
    assert "evidence='" in page and "+blockers+advanced+confirmForm" in page
