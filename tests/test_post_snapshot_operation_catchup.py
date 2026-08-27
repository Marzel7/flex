from scripts.reconcile_post_snapshot_operation_catchup import OPS, START_EDGES, START_QUEUE


def test_catchup_excludes_watchtower_and_uses_frozen_highwaters():
    assert all(item["name"] != "WATCHTOWER" for item in OPS)
    assert START_QUEUE == 32353
    assert START_EDGES == 60299


def test_catchup_uses_only_versioned_post_snapshot_detectors():
    assert {item["name"] for item in OPS} == {
        "Byzantine", "FOUR_STEP_30_SOL_14_479K_WSOL_LADDER", "WSOL_PROVISION_CLOSE_1_SOL_MINUS_15K"
    }
    assert all(item["detector"] for item in OPS)
