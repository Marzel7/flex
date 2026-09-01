import json
import time

from src.core.pumpportal_birth_audit import PumpPortalBirthAudit


def test_disabled_collector_does_not_create_output(tmp_path):
    path = tmp_path / "births.jsonl"
    audit = PumpPortalBirthAudit(enabled=False, path=str(path))
    audit.record(receive_utc_ns=1, receive_monotonic_ns=2, parser_utc_ns=3,
                 signature="sig", mint="mint", creator="creator")
    assert not path.exists()
    assert audit.health() == {"enabled": False, "pending": 0, "dropped": 0}


def test_enabled_collector_preserves_raw_receive_timing(tmp_path):
    path = tmp_path / "births.jsonl"
    audit = PumpPortalBirthAudit(enabled=True, path=str(path))
    audit.record(receive_utc_ns=100, receive_monotonic_ns=200, parser_utc_ns=300,
                 signature="sig", mint="mint", creator="creator",
                 market_cap_sol=12.5, virtual_sol_reserves=30.25, bonding_curve="curve")
    deadline = time.monotonic() + 1
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    row = json.loads(path.read_text().strip())
    assert row["event_schema_version"] == 2
    assert row["parser_version"] == "pumpportal_create.v1"
    assert row["source"] == "pumpportal_create"
    assert row["receive_utc_ns"] == 100
    assert row["raw_payload"] == {}
    assert len(row["raw_payload_sha256"]) == 64
    assert row["signature"] == "sig"
    assert row["mint"] == "mint"
    assert row["creator"] == "creator"
    assert row["market_cap_sol"] == 12.5
    assert row["virtual_sol_reserves"] == 30.25
    assert row["bonding_curve"] == "curve"


def test_content_addressed_store_retains_raw_create_once(tmp_path):
    path = tmp_path / "birth-evidence"
    audit = PumpPortalBirthAudit(enabled=True, path=str(path))
    args = dict(receive_utc_ns=100, receive_monotonic_ns=200, parser_utc_ns=300,
                signature="sig", mint="mint", creator="creator", raw_payload={"txType": "create", "mint": "mint"})
    audit.record(**args)
    audit.record(**args)
    deadline = time.monotonic() + 1
    while len(list(path.glob("*.json"))) != 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    files = list(path.glob("*.json"))
    assert len(files) == 1
    row = json.loads(files[0].read_text())
    assert row["raw_payload"]["txType"] == "create"
    assert row["received_at"] == 0
