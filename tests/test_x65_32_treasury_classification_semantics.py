from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates" / "discovery.html").read_text()


def _function(name: str, next_name: str) -> str:
    start = HTML.index(f"function {name}")
    return HTML[start:HTML.index(f"function {next_name}", start)]


# X65.32 — Correct Treasury Classification Semantics. Splits the old
# renderKnownWatchtowerAttribution (which mixed is_cascade_confirmed rows
# with unconfirmed WATCHTOWER-campaign candidates inside one "WATCHTOWER
# Attribution" block) into two independent, non-adjacent-in-meaning
# sections, so "Unknown Treasury" is never presented as possible WATCHTOWER
# evidence. Presentation-only: reuses x65_27ConfirmedWatchtowerRows /
# x65_27CandidateWatchtowerRows / X60_OUTCOME_GROUPS / X65_27_OUTCOME_STATUS_LABELS
# verbatim, no new classification, no new fetch.


def test_confirmed_treasury_section_scoped_to_confirmed_rows_only():
    section = _function("renderConfirmedWatchtowerTreasury", "renderUnresolvedTreasuryAttribution")
    assert "x65_27ConfirmedWatchtowerRows()" in section
    assert "x65_27CandidateWatchtowerRows" not in section
    assert "Confirmed WATCHTOWER Treasury" in section
    assert "validated reference model" in section


def test_confirmed_treasury_section_counts_distinct_treasury_families():
    section = _function("renderConfirmedWatchtowerTreasury", "renderUnresolvedTreasuryAttribution")
    assert "new Set()" in section
    assert "families.add(r.treasury_wallet)" in section
    assert "families.size" in section


def test_unresolved_section_scoped_to_candidates_only():
    section = _function("renderUnresolvedTreasuryAttribution", "renderKnownWatchtowerFunding")
    assert "x65_27CandidateWatchtowerRows()" in section
    assert "x65_27ConfirmedWatchtowerRows" not in section


def test_unresolved_section_never_labelled_as_watchtower():
    section = _function("renderUnresolvedTreasuryAttribution", "renderKnownWatchtowerFunding")
    assert "WATCHTOWER" not in section.split("dw-x58-heading")[1].split("</div>")[0], (
        "the section heading itself must never say WATCHTOWER"
    )
    assert "not WATCHTOWER evidence" in section or "investigation status" in section


def test_unresolved_section_reuses_existing_outcome_group_labels_only():
    section = _function("renderUnresolvedTreasuryAttribution", "renderKnownWatchtowerFunding")
    assert "X60_OUTCOME_GROUPS" in section
    assert "X65_27_OUTCOME_STATUS_LABELS" in section
    # No new classification/query: pure intersection of already-fetched data.
    assert "fetch(" not in section


def test_unresolved_section_omitted_when_no_candidates():
    section = _function("renderUnresolvedTreasuryAttribution", "renderKnownWatchtowerFunding")
    assert "if(!X60_UNIVERSE_LOADED||!candidates.length)return ''" in section


def test_confirmed_and_unresolved_use_distinct_visual_treatment():
    # Confirmed reuses the existing cyan "known" section styling;
    # Unresolved reuses the existing amber "candidate" styling from X65.27
    # -- never blended into one card, so a user cannot mistake an unresolved
    # candidate row's treasury state for confirmed WATCHTOWER evidence.
    confirmed = _function("renderConfirmedWatchtowerTreasury", "renderUnresolvedTreasuryAttribution")
    unresolved = _function("renderUnresolvedTreasuryAttribution", "renderKnownWatchtowerFunding")
    assert "dw-wt-known-section" in confirmed
    assert "dw-wt-candidate-section" in unresolved


def test_block_renders_confirmed_before_unresolved():
    block = _function("renderKnownWatchtowerBlock", "renderCampaignDistribution")
    confirmed_pos = block.index("renderConfirmedWatchtowerTreasury()")
    unresolved_pos = block.index("renderUnresolvedTreasuryAttribution()")
    assert confirmed_pos < unresolved_pos


def test_old_mixed_attribution_function_no_longer_used_in_dispatch():
    block = _function("renderKnownWatchtowerBlock", "renderCampaignDistribution")
    assert "renderKnownWatchtowerAttribution()" not in block
