"""X67.39 -- Clarify "WATCHTOWER Provisioning" Campaign Semantics.

Ecosystem Intelligence already excludes every authoritative Canonical
WATCHTOWER launch (x65_25RemainingUniverseRows(): !r.is_watchtower, see
X67.35). Presenting a campaign bucket inside that same view labelled bare
"WATCHTOWER Provisioning" read as self-contradictory -- users reasonably
asked "if this excludes WATCHTOWER, why am I looking at WATCHTOWER?".

This is a label/description-only change: renames the display text to
"WATCHTOWER Provisioning Fingerprint", adds an explicit clarifying
sentence under the Campaign heading, and an optional "Fingerprint" badge
on the pick card -- x58CampaignLabel()'s VALUE mapping (the 'WATCHTOWER'
string itself, compared against campaign_classification.py's actual
output) is unchanged, so no classification, attribution, or count logic
is touched.

No JS execution harness exists in this codebase (matches the established
static-source-assertion pattern from tests/test_x67_33_ecosystem_window_
filter.py and tests/test_x67_35_ecosystem_canonical_exclusion.py).
"""
import os


def _discovery_html_source() -> str:
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "templates", "discovery.html")
    with open(path, encoding="utf-8") as f:
        return f.read()


def _function_block(src: str, fn_signature: str, next_fn_hint: str = "function ") -> str:
    start = src.index(fn_signature)
    end = src.index(next_fn_hint, start + len(fn_signature))
    return src[start:end]


class TestCampaignLabelRenamed:
    def test_watchtower_campaign_label_is_fingerprint_variant(self):
        block = _function_block(_discovery_html_source(), "function x58CampaignLabel(value){")
        assert "WATCHTOWER Provisioning Fingerprint" in block

    def test_bare_watchtower_provisioning_label_no_longer_present(self):
        """The old, ambiguous label string must not remain as a live value
        anywhere x58CampaignLabel's map is defined -- only the new,
        explicit variant."""
        block = _function_block(_discovery_html_source(), "function x58CampaignLabel(value){")
        assert "'WATCHTOWER Provisioning'," not in block
        assert "'WATCHTOWER Provisioning Fingerprint'" in block

    def test_campaign_value_mapping_unchanged(self):
        """Only the display TEXT changed -- the dict key compared against
        campaign_classification.py's actual output ('WATCHTOWER',
        'OTHER_CAMPAIGN', 'UNCLASSIFIED') must be exactly the same three
        keys as before, so no classification logic is affected."""
        block = _function_block(_discovery_html_source(), "function x58CampaignLabel(value){")
        assert "WATCHTOWER:" in block
        assert "OTHER_CAMPAIGN:" in block
        assert "UNCLASSIFIED:" in block


class TestCampaignSectionClarification:
    def test_explanatory_description_present(self):
        block = _function_block(_discovery_html_source(), "function renderCampaignDistribution(){")
        assert ("Launches exhibiting the validated WATCHTOWER provisioning "
                "fingerprint while remaining outside authoritative Canonical "
                "WATCHTOWER attribution") in block

    def test_description_states_not_operator_attribution(self):
        block = _function_block(_discovery_html_source(), "function renderCampaignDistribution(){")
        assert "not operator attribution" in block

    def test_description_states_membership_does_not_imply_confirmation(self):
        block = _function_block(_discovery_html_source(), "function renderCampaignDistribution(){")
        assert "does not imply WATCHTOWER confirmation" in block

    def test_description_points_to_canonical_watchtower_location(self):
        block = _function_block(_discovery_html_source(), "function renderCampaignDistribution(){")
        assert "Canonical WATCHTOWER (Operation Intelligence)" in block

    def test_fingerprint_badge_present_on_watchtower_card_only(self):
        block = _function_block(_discovery_html_source(), "function renderCampaignDistribution(){")
        assert "dw-campaign-pick-badge" in block
        assert "Fingerprint</span>" in block
        # gated specifically on the WATCHTOWER key, not applied to every card
        assert "key==='WATCHTOWER'?'<span class=\"dw-campaign-pick-badge\">Fingerprint</span>'" in block


class TestNoClassificationOrCountLogicChanged:
    def test_campaign_counting_logic_untouched(self):
        """The counts={WATCHTOWER:0,...} accumulator and its forEach must
        still key off r.campaign's raw value exactly as before -- only
        rendering/labels changed, not what gets counted or how."""
        block = _function_block(_discovery_html_source(), "function renderCampaignDistribution(){")
        assert "counts={WATCHTOWER:0,OTHER_CAMPAIGN:0,UNCLASSIFIED:0}" in block
        assert "r.campaign||'UNCLASSIFIED'" in block

    def test_treasury_breakdown_gate_unchanged(self):
        """renderCampaignTreasuryBreakdown still gates on the raw
        TOPO_SELECTION.campaign==='WATCHTOWER' value, not a label string --
        confirms the rename didn't touch this comparison."""
        block = _function_block(_discovery_html_source(), "function renderCampaignTreasuryBreakdown(rows,total){")
        assert "TOPO_SELECTION.campaign!=='WATCHTOWER'" in block

    def test_ecosystem_exclusion_filter_still_uses_is_watchtower(self):
        """Regression guard: X67.35's authoritative exclusion
        (!r.is_watchtower) must remain untouched by this label-only task."""
        block = _function_block(_discovery_html_source(), "function x65_25RemainingUniverseRows(){")
        assert "!r.is_watchtower" in block


class TestRegressionNeighboringSystems:
    def test_x58label_watchtower_sentinel_still_separate(self):
        """x58Label's OWN 'WATCHTOWER' key (Operation Attribution's
        confirmed-operation id, an unrelated dimension) must still read
        plain 'WATCHTOWER', never accidentally picking up the campaign
        rename -- the two label maps must remain fully independent."""
        src = _discovery_html_source()
        label_block = _function_block(src, "function x58Label(value){", next_fn_hint="function x58CampaignLabel")
        assert "WATCHTOWER:'WATCHTOWER'," in label_block
        assert "WATCHTOWER Provisioning" not in label_block

    def test_canonical_watchtower_panel_function_untouched(self):
        assert "function renderKnownWatchtowerBlock(){" in _discovery_html_source()
