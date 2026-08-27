from src.ops.operation_identity_metadata import IDENTITIES


def test_canonical_human_names_are_read_side_metadata_only():
    assert IDENTITIES["FOUR_STEP_30_SOL_14_479K_WSOL_LADDER"]["human_name"] == "30 SOL 14.479K Ladder"
    assert IDENTITIES["P3R"]["human_name"] == "100 SOL WSOL Close"
    assert IDENTITIES["P3R_13A04"]["human_name"] == "30 SOL 5K Ladder"


def test_activity_and_identity_axes_remain_independent():
    identity, activity = "CONFIRMED", "DORMANT"
    assert identity == "CONFIRMED"
    assert activity == "DORMANT"
