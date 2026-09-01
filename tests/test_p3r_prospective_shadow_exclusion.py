from scripts.run_p3r_corrected_prospective_shadow import eligible_mints


def test_unseen_requires_absence_from_historical_and_valid_seen_sets():
    historical = {"historical_only", "both"}
    valid_seen = {"seen_only", "both"}
    raw = ["historical_only", "seen_only", "both", "eligible"]
    assert eligible_mints(raw, historical, valid_seen) == ["eligible"]


def test_quarantined_successor_is_not_a_valid_seen_input():
    valid_predecessor_seen = {"valid_mint"}
    quarantined_invalid_successor_mints = {"historical_only", "leaked_mint"}
    assert "leaked_mint" not in valid_predecessor_seen
    assert eligible_mints(["leaked_mint"], {"leaked_mint"}, valid_predecessor_seen) == []
