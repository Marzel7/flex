from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def text(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def test_registry_and_profile_share_attributed_launch_source():
    registry = text("templates/operators_index.html")
    profile = text("templates/operation_profile.html")
    assert "launches:Number(f.launches||0)" in registry
    assert "const attributedLaunches=Number(f.launches||0)" in profile
    assert "(f.treasuries||[]).length?f.treasuries:(infra.treasuries||[])" in profile
    assert "(f.treasuries||[]).length?f.treasuries:(f.member_treasuries||[])" in registry
    assert "Attributed launches" in profile
    assert "detailed launch records available" in profile


def test_missing_detail_records_do_not_deny_attributed_launches():
    profile = text("templates/operation_profile.html")
    assert "No launches persisted." not in profile
    assert "No launch records are currently available for this Investigation Population." in profile
    assert "launchRows.length===attributedLaunches" in profile
    assert "attributedLaunches+' attributed launches · '+launchRows.length" in profile


def test_members_use_compact_metadata_and_unavailable_marker():
    profile = text("templates/operation_profile.html")
    assert 'class="rp-metadata"' in profile
    for label in ("Attributed launches", "Creators", "Clients", "Treasuries"):
        assert label in profile
    assert "displayValue=n=>n>0?esc(n):'—'" in profile
    assert "No creators observed." not in profile
    assert "No treasuries observed." not in profile


def test_profile_uses_registry_disposition_palette_and_restrained_accents():
    profile = text("templates/operation_profile.html")
    for contract in (
        ".disp-CONFIRMED_OPERATION{--rp-color:#22c55e}",
        ".disp-REVIEW{--rp-color:#f97316}",
        ".disp-INFRASTRUCTURE{--rp-color:#60a5fa}",
        ".disp-UNRESOLVED{--rp-color:#64748b}",
        "border-left:4px solid var(--rp-color)",
        "border-bottom-color:var(--rp-color)",
    ):
        assert contract in profile
    assert ".rp-section h2" in profile and "color:var(--rp-color)" in profile


def test_reconciliation_audit_documents_named_controls_and_no_backend_change():
    audit = text("docs/audits/x73_7_metric_reconciliation_audit.md")
    for name in ("WATCHTOWER", "3SW2", "B48k / Dv34", "3hJX", "C7Ha", "Infrastructure"):
        assert name in audit
    assert "63 attributed launches" in audit
    assert "No filtering or reconciliation logic was changed." in audit
