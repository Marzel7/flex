import json

import pytest

from src.ops.watchtower_offset_audit import (
    MAX_FILE_BYTES, MAX_NORMALIZED_TIMESTAMPS, OffsetAuditStore, compact_record,
)


def _payload(items):
    return {"data": {"items": items}}


def _candle(timestamp, close):
    return {"unixTime": timestamp, "o": close, "h": close, "l": close, "c": close}


def test_compact_record_keeps_only_bounded_normalized_offset_evidence():
    record = compact_record(mint="mint", migration_timestamp=100, http_status=200, payload=_payload([
        _candle(100, 10), _candle(101, 11), _candle(102, 12), _candle(105, 15),
    ]))
    assert record["normalized_timestamps"] == [100, 101, 102, 105]
    assert record["offsets"] == {"0": 10.0, "1": 11.0, "2": 12.0}
    assert "payload" not in record


def test_duplicate_early_second_is_explicit_and_not_selected():
    record = compact_record(mint="mint", migration_timestamp=100, http_status=200, payload=_payload([
        _candle(102, 12), _candle(102, 13),
    ]))
    assert record["duplicate_offsets"] == [2]
    assert record["offsets"] == {}


def test_store_is_idempotent_bounded_and_restart_safe(tmp_path):
    path = tmp_path / "offset-audit.json"
    store = OffsetAuditStore(path)
    first = compact_record(mint="mint", migration_timestamp=100, http_status=200, payload=_payload([_candle(100, 10)]))
    assert store.record(first) < MAX_FILE_BYTES
    updated = dict(first, http_status=503, normalization_state="NOT_RUN", normalized_item_count=0,
                   normalized_timestamps=[], offsets={}, duplicate_offsets=[], failure_state="HTTP_NON_200")
    store.record(updated)
    reopened = OffsetAuditStore(path)
    assert reopened.records() == [updated]
    raw = json.loads(path.read_text())
    assert raw["raw_provider_payload_retention"] is False


def test_timestamp_cap_fails_closed():
    payload = _payload([_candle(index, float(index)) for index in range(1, MAX_NORMALIZED_TIMESTAMPS + 2)])
    record = compact_record(mint="mint", migration_timestamp=1, http_status=200, payload=payload)
    assert len(record["normalized_timestamps"]) == MAX_NORMALIZED_TIMESTAMPS
    assert set(record["offsets"]) == {"0", "1", "2", "3", "4"}


def test_raw_payload_key_is_rejected(tmp_path):
    store = OffsetAuditStore(tmp_path / "offset-audit.json")
    with pytest.raises(ValueError, match="RAW_PROVIDER_RETENTION_FORBIDDEN"):
        store.record({"mint": "mint", "migration_timestamp": 1, "http_status": 200,
                      "normalization_state": "COMPLETE", "normalized_item_count": 0,
                      "normalized_timestamps": [], "offsets": {}, "duplicate_offsets": [],
                      "failure_state": "NONE", "payload": {}})
