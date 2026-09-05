import base64
import json
from pathlib import Path

from src.ops.birth_anchored_opening_acquisition import decode_trade_event


ROOT = Path(__file__).resolve().parents[1]


def test_trade_event_prefix_matches_retained_watchtower_reference():
    raw = json.loads((ROOT / "docs/audits/watchtower_buy3_opening_mc/raw_evidence.v1.json").read_text())
    event = decode_trade_event(base64.b64decode(raw["creation"]["trade_event_base64"]))
    assert event is not None
    assert event["mint"] == "BuY3MHzaDEd2ipwub6uAhddH8Ah95oHoZfNDVkVVpump"
    assert event["action_type"] == "BUY"
    assert event["post_virtual_sol_reserves"] == 31_975_308_640
    assert event["post_virtual_token_reserves"] == 1_006_714_285_776_477
