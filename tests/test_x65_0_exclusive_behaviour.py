"""X65.0 — Exclusive Behaviour Classification.

Adds canonical_behaviour_for(), an exclusive (single-value) behaviour
assignment computed from the most specific matching rule, alongside the
existing additive `behaviours` list -- which remains fully intact and
unchanged for filtering/cross-dimensional-query use (see
docs/design/x65_0/ for the full audit/overlap/precedence derivation).

Must never: change the additive behaviours list, change topology/
mechanism/creator-identity/operation-attribution logic, or let a launch
receive more than one canonical behaviour.
"""
from __future__ import annotations

from src.ops.operational_behaviour_tags import (
    BURST_LAUNCH,
    CANONICAL_BEHAVIOUR_ORDER,
    CREATOR_RECYCLING,
    DELAYED_MIGRATION,
    MIGRATION_5_TO_15M,
    QUICK_BIRTH_MIGRATION,
    RAPID_BIRTH_LAUNCH,
    RAPID_MIGRATION,
    UNKNOWN_BEHAVIOUR,
    canonical_behaviour_for,
)


def test_every_candidate_in_canonical_order_has_a_label():
    from src.ops.operational_behaviour_tags import BEHAVIOUR_LABELS
    for c in CANONICAL_BEHAVIOUR_ORDER:
        assert c in BEHAVIOUR_LABELS, f"{c} missing from BEHAVIOUR_LABELS"


def test_empty_behaviours_and_no_quick_birth_yields_unknown():
    assert canonical_behaviour_for([]) == UNKNOWN_BEHAVIOUR
    assert canonical_behaviour_for([], is_quick_birth_migration=False) == UNKNOWN_BEHAVIOUR


def test_single_tag_returns_that_tag():
    assert canonical_behaviour_for([BURST_LAUNCH]) == BURST_LAUNCH
    assert canonical_behaviour_for([CREATOR_RECYCLING]) == CREATOR_RECYCLING
    assert canonical_behaviour_for([RAPID_MIGRATION]) == RAPID_MIGRATION


def test_quick_birth_migration_outranks_rapid_migration():
    """The exact overlap the task names: a launch matching both
    QUICK_BIRTH_MIGRATION and RAPID_MIGRATION (measured live: 100% of
    QUICK_BIRTH_MIGRATION launches are also RAPID_MIGRATION) must be
    canonically QUICK_BIRTH_MIGRATION, the more specific rule."""
    result = canonical_behaviour_for([RAPID_MIGRATION], is_quick_birth_migration=True)
    assert result == QUICK_BIRTH_MIGRATION


def test_rapid_birth_launch_outranks_everything():
    result = canonical_behaviour_for(
        [BURST_LAUNCH, CREATOR_RECYCLING, RAPID_MIGRATION, RAPID_BIRTH_LAUNCH],
        is_quick_birth_migration=True,
    )
    assert result == RAPID_BIRTH_LAUNCH


def test_burst_launch_outranks_creator_recycling_and_migration_timing():
    result = canonical_behaviour_for([CREATOR_RECYCLING, RAPID_MIGRATION, BURST_LAUNCH])
    assert result == BURST_LAUNCH


def test_creator_recycling_outranks_migration_timing_trio():
    for migration_tag in (RAPID_MIGRATION, MIGRATION_5_TO_15M, DELAYED_MIGRATION):
        assert canonical_behaviour_for([migration_tag, CREATOR_RECYCLING]) == CREATOR_RECYCLING


def test_migration_timing_trio_is_the_fallback_tier():
    """When nothing more specific matches, the migration-timing tags (which
    are already mutually exclusive among themselves by construction) are
    used as-is -- no change to their own mutual exclusivity."""
    assert canonical_behaviour_for([RAPID_MIGRATION]) == RAPID_MIGRATION
    assert canonical_behaviour_for([MIGRATION_5_TO_15M]) == MIGRATION_5_TO_15M
    assert canonical_behaviour_for([DELAYED_MIGRATION]) == DELAYED_MIGRATION


def test_every_combination_yields_exactly_one_behaviour():
    """Property test over every subset of the known tags: the function
    must always return exactly one string, never a list, never None."""
    from itertools import combinations
    all_tags = [RAPID_BIRTH_LAUNCH, BURST_LAUNCH, CREATOR_RECYCLING,
                RAPID_MIGRATION, MIGRATION_5_TO_15M, DELAYED_MIGRATION]
    for r in range(len(all_tags) + 1):
        for combo in combinations(all_tags, r):
            for quick in (True, False):
                result = canonical_behaviour_for(list(combo), is_quick_birth_migration=quick)
                assert isinstance(result, str)
                assert result in CANONICAL_BEHAVIOUR_ORDER


def test_canonical_order_matches_measured_specificity():
    """Regression test for the precedence itself (docs/design/x65_0/
    x65_0_precedence.md): RAPID_MIGRATION -- the single MOST POPULAR tag
    measured live (93.5% coverage) -- must rank LAST among the matchable
    rules, not first. Popularity and specificity are inversely related
    here; this test guards against someone re-ordering by popularity."""
    order = list(CANONICAL_BEHAVIOUR_ORDER)
    assert order.index(RAPID_BIRTH_LAUNCH) < order.index(QUICK_BIRTH_MIGRATION)
    assert order.index(QUICK_BIRTH_MIGRATION) < order.index(BURST_LAUNCH)
    assert order.index(BURST_LAUNCH) < order.index(CREATOR_RECYCLING)
    assert order.index(CREATOR_RECYCLING) < order.index(RAPID_MIGRATION)
    assert order.index(RAPID_MIGRATION) < order.index(UNKNOWN_BEHAVIOUR)


def test_additive_behaviours_list_is_never_mutated():
    """canonical_behaviour_for must be a pure read -- the caller's
    behaviours list must be unchanged after the call, since it is still
    used elsewhere (filtering, cross-dimensional query, Observed
    Patterns UI) exactly as before X65.0."""
    original = [BURST_LAUNCH, CREATOR_RECYCLING]
    snapshot = list(original)
    canonical_behaviour_for(original, is_quick_birth_migration=True)
    assert original == snapshot
