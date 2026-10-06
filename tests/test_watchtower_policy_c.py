from src.ops.watchtower_policy_c import POLICY_C_CHECKPOINTS, policy_c_checkpoint_offsets


def test_policy_c_has_exact_24_hour_checkpoint_sequence():
    offsets = policy_c_checkpoint_offsets()
    assert POLICY_C_CHECKPOINTS == ((0, 7200, 900), (7200, 21600, 1800), (21600, 43200, 3600), (43200, 86400, 7200))
    assert len(offsets) == 28
    assert [sum(start < item <= end for item in offsets) for start, end, _ in POLICY_C_CHECKPOINTS] == [8, 8, 6, 6]
    assert offsets[-1] == 86400
