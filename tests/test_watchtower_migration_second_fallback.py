from src.ops.strict_migration_window import MIGRATION_SECOND_FALLBACK_METHOD, PLUS1_ENTRY_METHOD, reduce_policy


def test_plus_one_is_preferred_and_has_existing_exactness():
    got = reduce_policy(migration_timestamp=100, entry={"timestamp": 101, "mc": 12})
    assert got == {"state": "QUALIFIED", "entry_method": PLUS1_ENTRY_METHOD, "entry_timestamp": 101, "entry_mc_usd": 12.0, "entry_exactness": PLUS1_ENTRY_METHOD}


def test_migration_second_is_only_accepted_when_plus_one_absent():
    got = reduce_policy(migration_timestamp=100, entry={"timestamp": 100, "mc": 11, "plus_one_absent": True})
    assert got["entry_method"] == got["entry_exactness"] == MIGRATION_SECOND_FALLBACK_METHOD
    assert reduce_policy(migration_timestamp=100, entry={"timestamp": 100, "mc": 11})["state"] == "INSUFFICIENT_EVIDENCE"


def test_plus_two_is_never_a_substitute():
    assert reduce_policy(migration_timestamp=100, entry={"timestamp": 102, "mc": 11})["state"] == "INSUFFICIENT_EVIDENCE"
