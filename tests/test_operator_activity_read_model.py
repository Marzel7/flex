from src.ops.operator_reader import activity_read_model


def test_live_counts_are_relative_to_now_not_snapshot_creation_time():
    now = 1_000_000
    snapshot = {"launches_last_1d": 1, "launches_last_7d": 6, "launches_last_30d": 6,
                "last_observed_launch_timestamp": now - 74}
    model = activity_read_model([now - 5 * 86400], snapshot, now - 4 * 86400, now=now)
    assert (model["live_launches_24h"], model["live_launches_7d"], model["live_launches_30d"]) == (0, 1, 1)
    assert (model["snapshot_launches_24h"], model["snapshot_launches_7d"], model["snapshot_launches_30d"]) == (1, 6, 6)
    assert model["activity_state_source"] == "LIVE_RECALCULATED"
    assert model["last_launch_at"] == now - 5 * 86400


def test_missing_raw_timestamps_retains_explicit_snapshot_fallback():
    model = activity_read_model([], {"launches_last_1d": 1, "last_observed_launch_timestamp": 123}, 456, now=999)
    assert model["activity_state_source"] == "SNAPSHOT_ONLY"
    assert model["live_launches_24h"] is None
    assert model["snapshot_launches_24h"] == 1
    assert model["snapshot_as_of"] == 456
