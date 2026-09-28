import json

from src.core.pumpfun_delivery_latency import PumpfunDeliveryLatencyCapture


def test_matching_feeds_use_monotonic_delta_and_file_only_output(tmp_path):
    capture = PumpfunDeliveryLatencyCapture(enabled=True, path=str(tmp_path / "capture.jsonl"))
    capture.observe_chain(signature="sig", slot=42, receive_monotonic_ns=1_000_000, receive_utc_ns=10)
    capture.observe_pumpportal(signature="sig", mint="mint", receive_monotonic_ns=1_750_000, receive_utc_ns=11)
    capture._pending.join()

    row = json.loads((tmp_path / "capture.jsonl").read_text())
    assert row["slot"] == 42
    assert row["mint"] == "mint"
    assert row["delta_ms"] == 0.75
    assert row["pumpportal_receive_monotonic_ns"] == 1_750_000
    assert row["chain_feed_receive_monotonic_ns"] == 1_000_000


def test_disabled_capture_retains_nothing(tmp_path):
    path = tmp_path / "capture.jsonl"
    capture = PumpfunDeliveryLatencyCapture(enabled=False, path=str(path))
    capture.observe_pumpportal(signature="sig", mint="mint", receive_monotonic_ns=1, receive_utc_ns=1)
    capture.observe_chain(signature="sig", slot=1, receive_monotonic_ns=2, receive_utc_ns=2)
    assert not path.exists()
