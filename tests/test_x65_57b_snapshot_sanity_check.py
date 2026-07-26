"""X65.57 follow-up — snapshot sanity check.

Root cause of a real production incident: a technically-successful build
(no exception) can still return a suspiciously small/incomplete result
(e.g. a transient partial DB read during startup). Before this fix,
write_snapshot() persisted such a result unconditionally, and after a
restart, hydrate() trusted it as FRESH -- serving stale-looking-fresh
garbage data for a full TTL cycle until the next natural refresh happened
to overwrite it.

This adds an OPTIONAL, purely structural sanity check: if the caller
supplies `completeness_key`, a new build whose value for that key has
dropped more than `max_relative_drop` (default 50%) relative to the
EXISTING on-disk snapshot is rejected -- the previous snapshot is left
untouched, and write_snapshot() returns False. No completeness_key means
no check at all (exactly the original behaviour), and the check knows
nothing about WATCHTOWER/detection semantics -- only relative magnitude
of one named numeric field.
"""
import pytest

import src.ops.intelligence_snapshots as snap


@pytest.fixture(autouse=True)
def _isolated_snapshot_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(snap, "SNAPSHOT_DIR", str(tmp_path / "snapshots"))
    yield


def test_first_ever_write_is_always_accepted_even_with_completeness_key():
    # No previous snapshot exists -- nothing to compare against, so any
    # first build is accepted regardless of its total_launches value.
    ok = snap.write_snapshot(
        "operational_intelligence", 86400, {"total_launches": 1}, build_duration_ms=1.0,
        completeness_key="total_launches",
    )
    assert ok is True
    assert snap.read_snapshot("operational_intelligence", 86400).payload == {"total_launches": 1}


def test_a_large_relative_drop_is_rejected_and_previous_snapshot_kept():
    snap.write_snapshot(
        "operational_intelligence", 86400, {"total_launches": 813}, build_duration_ms=1.0,
        completeness_key="total_launches",
    )
    ok = snap.write_snapshot(
        "operational_intelligence", 86400, {"total_launches": 5}, build_duration_ms=1.0,
        completeness_key="total_launches",
    )
    assert ok is False
    # Previous, good snapshot is untouched.
    assert snap.read_snapshot("operational_intelligence", 86400).payload == {"total_launches": 813}


def test_a_small_reasonable_decrease_is_accepted():
    snap.write_snapshot(
        "operational_intelligence", 86400, {"total_launches": 812}, build_duration_ms=1.0,
        completeness_key="total_launches",
    )
    ok = snap.write_snapshot(
        "operational_intelligence", 86400, {"total_launches": 809}, build_duration_ms=1.0,
        completeness_key="total_launches",
    )
    assert ok is True
    assert snap.read_snapshot("operational_intelligence", 86400).payload == {"total_launches": 809}


def test_an_increase_is_always_accepted():
    snap.write_snapshot(
        "operational_intelligence", 86400, {"total_launches": 100}, build_duration_ms=1.0,
        completeness_key="total_launches",
    )
    ok = snap.write_snapshot(
        "operational_intelligence", 86400, {"total_launches": 500}, build_duration_ms=1.0,
        completeness_key="total_launches",
    )
    assert ok is True


def test_no_completeness_key_disables_the_check_entirely_original_behaviour():
    snap.write_snapshot("operational_intelligence", 86400, {"total_launches": 813}, build_duration_ms=1.0)
    ok = snap.write_snapshot("operational_intelligence", 86400, {"total_launches": 5}, build_duration_ms=1.0)
    assert ok is True
    assert snap.read_snapshot("operational_intelligence", 86400).payload == {"total_launches": 5}


def test_boundary_at_exactly_max_relative_drop_is_accepted():
    snap.write_snapshot(
        "operational_intelligence", 86400, {"total_launches": 100}, build_duration_ms=1.0,
        completeness_key="total_launches",
    )
    # Exactly 50% drop -- the boundary itself must not be rejected (the
    # check rejects STRICTLY LESS than the threshold).
    ok = snap.write_snapshot(
        "operational_intelligence", 86400, {"total_launches": 50}, build_duration_ms=1.0,
        completeness_key="total_launches",
    )
    assert ok is True


def test_custom_max_relative_drop_threshold():
    snap.write_snapshot(
        "operational_intelligence", 86400, {"total_launches": 100}, build_duration_ms=1.0,
        completeness_key="total_launches", max_relative_drop=0.1,
    )
    # 20% drop rejected under a stricter 10% tolerance.
    ok = snap.write_snapshot(
        "operational_intelligence", 86400, {"total_launches": 80}, build_duration_ms=1.0,
        completeness_key="total_launches", max_relative_drop=0.1,
    )
    assert ok is False


def test_missing_key_in_new_payload_does_not_block_the_write():
    snap.write_snapshot(
        "operational_intelligence", 86400, {"total_launches": 100}, build_duration_ms=1.0,
        completeness_key="total_launches",
    )
    ok = snap.write_snapshot(
        "operational_intelligence", 86400, {"something_else": 1}, build_duration_ms=1.0,
        completeness_key="total_launches",
    )
    assert ok is True  # can't compare -- don't block on something unmeasurable


def test_zero_previous_value_does_not_block_the_write():
    snap.write_snapshot(
        "operational_intelligence", 86400, {"total_launches": 0}, build_duration_ms=1.0,
        completeness_key="total_launches",
    )
    ok = snap.write_snapshot(
        "operational_intelligence", 86400, {"total_launches": 0}, build_duration_ms=1.0,
        completeness_key="total_launches",
    )
    assert ok is True


def test_different_windows_are_independent_for_sanity_checking():
    snap.write_snapshot(
        "operational_intelligence", 86400, {"total_launches": 800}, build_duration_ms=1.0,
        completeness_key="total_launches",
    )
    # A totally different window key (604800) has no prior snapshot yet --
    # its own small value must be accepted, unaffected by 86400's value.
    ok = snap.write_snapshot(
        "operational_intelligence", 604800, {"total_launches": 5}, build_duration_ms=1.0,
        completeness_key="total_launches",
    )
    assert ok is True
