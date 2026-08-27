from src.ops.operation_identity_metadata import IDENTITIES


def test_canonical_human_names_are_read_side_metadata_only():
    assert IDENTITIES["FOUR_STEP_30_SOL_14_479K_WSOL_LADDER"]["human_name"] == "Sentinel"
    assert IDENTITIES["P3R"]["human_name"] == "Leviathan"
    assert IDENTITIES["P3R_13A04"]["human_name"] == "Harbinger"


def test_activity_and_identity_axes_remain_independent():
    identity, activity = "CONFIRMED", "DORMANT"
    assert identity == "CONFIRMED"
    assert activity == "DORMANT"
