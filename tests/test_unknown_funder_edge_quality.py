from src.ops.unknown_funder_edge_quality import (
    EdgeQuality, FundingObservation, classify_unknown_funder_edge,
)


def result(**facts):
    return classify_unknown_funder_edge(FundingObservation(**facts))[0]


def test_proven_role_remains_qualifying_even_when_low_value_and_new():
    assert result(
        proven_funding_role=True, amount_lamports=1_000,
        funder_account_age_seconds=1, broad_unrelated_fanout=True,
    ) is EdgeQuality.QUALIFYING_FUNDING_EDGE


def test_complete_launch_coupling_remains_qualifying():
    assert result(launch_coupling=True, transaction_role_consistent=True) is EdgeQuality.QUALIFYING_FUNDING_EDGE


def test_amount_novelty_and_fanout_cannot_independently_disqualify():
    assert result(amount_lamports=1_000, funder_account_age_seconds=1, broad_unrelated_fanout=True) is EdgeQuality.INSUFFICIENT_TO_CLASSIFY


def test_dust_requires_weak_semantics_and_two_independent_non_amount_signals():
    assert result(
        creator_specific_coupling_absent=True, broad_unrelated_fanout=True,
        repeated_unsolicited_tiny_transfers=True,
    ) is EdgeQuality.DUST_SPAM_EDGE


def test_one_spam_signal_fails_open():
    assert result(creator_specific_coupling_absent=True, repeated_unsolicited_tiny_transfers=True) is EdgeQuality.INSUFFICIENT_TO_CLASSIFY


def test_environmental_is_not_dust():
    assert result(environmental_or_post_launch=True, role_inconsistent=True) is EdgeQuality.ENVIRONMENTAL_EDGE
