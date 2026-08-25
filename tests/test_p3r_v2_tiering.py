from src.ops.p3r_v2_tiering import activity_metrics, alternative_fingerprint, assign_tier, atomic_fingerprint, base_fingerprint, digest, stable_candidate_id


def test_fingerprints_strip_addresses_and_normalize_zero_amounts():
    one = base_fingerprint([(1, "WSOL_WRAP_CLOSE", 0), (2, "PLAIN_XFER", 5000)])
    two = base_fingerprint([(2, "PLAIN_XFER", 5000), (1, "WSOL_WRAP_CLOSE", None)])
    assert one == two
    assert stable_candidate_id(one) == stable_candidate_id(two)


def test_alternative_and_atomic_fingerprints_are_deterministic():
    assert digest(alternative_fingerprint([(2, "PLAIN_XFER", 5000)])) == digest(alternative_fingerprint([(2, "PLAIN_XFER", 5000)]))
    assert atomic_fingerprint(["create", "sync", "close"], 1, 1, 1, 0)["transfer_lamports"] is None


def test_activity_and_tier_assignment_are_deterministic():
    cutoff = 1_000_000
    metrics = activity_metrics([cutoff - 3600 * item for item in range(8)], cutoff)
    assert metrics["activity_state"] == "VERY_HIGH_ACTIVITY"
    assert assign_tier("VERY_HIGH_ACTIVITY", True, "STRONGLY_RECURRENT", "STRONGLY_RECURRENT", True) == "V2_TIER_1_ACTIVE_MULTI_LAYER"
    assert assign_tier("HIGH_ACTIVITY", True, "STRONGLY_RECURRENT", "NOT_OBSERVED", False) == "V2_TIER_2_ACTIVE_STRUCTURAL"
