import json
import sqlite3

import pytest

from src.ops.watchtower_offset_audit import (
    MAX_FILE_BYTES, MAX_NORMALIZED_TIMESTAMPS, OffsetAuditStore, compact_record, product_projection,
)
from src.ops.strict_migration_window import reduce_policy
from scripts.run_watchtower_offset_audit import candidates


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


def test_runner_excludes_only_complete_retained_controls(tmp_path):
    operations = tmp_path / "operations.db"
    canonical = tmp_path / "canonical.db"
    with sqlite3.connect(operations) as connection:
        connection.execute("create table operation_monitor_facts(operation_id text, mint text)")
        connection.executemany("insert into operation_monitor_facts values('watchtower', ?)", [(f"mint-{i}",) for i in range(93)])
        connection.executemany("insert into operation_monitor_facts values('watchtower', ?)", [(mint,) for mint in [
            "B8S7WGQYteNSBz4rU4Xs1hkr1seGpxYgUuV2wgtbpump", "7iXZi55AyKbJv5e9XTQuVcywdT2ejHjGBf3NKuCLpump", "3yvK6WWww1moF3qx3UvHngZCHy9kRCVd9Jva8tRrpump"]])
    with sqlite3.connect(canonical) as connection:
        connection.execute("create table token_analysis(mint text primary key, migrated_at integer)")
        connection.executemany("insert into token_analysis values(?, ?)", [(f"mint-{i}", i + 1) for i in range(93)])
        connection.executemany("insert into token_analysis values(?, ?)", [(mint, 1000 + i) for i, mint in enumerate([
            "B8S7WGQYteNSBz4rU4Xs1hkr1seGpxYgUuV2wgtbpump", "7iXZi55AyKbJv5e9XTQuVcywdT2ejHjGBf3NKuCLpump", "3yvK6WWww1moF3qx3UvHngZCHy9kRCVd9Jva8tRrpump"])])
    assert len(candidates(operations_db=operations, canonical_db=canonical)) == 93


def test_plus2_policy_never_beats_plus1_or_t_and_is_distinct():
    assert reduce_policy(migration_timestamp=100, entry={"timestamp": 101, "mc": 11})["entry_method"] == "FIRST_FULL_POST_MIGRATION_SECOND_MC"
    assert reduce_policy(migration_timestamp=100, entry={"timestamp": 100, "mc": 10, "plus_one_absent": True})["entry_method"] == "MIGRATION_SECOND_MC_FALLBACK"
    plus2 = reduce_policy(migration_timestamp=100, entry={"timestamp": 102, "mc": 12, "plus_one_absent": True, "migration_absent": True})
    assert plus2["entry_method"] == "BOUNDED_POST_MIGRATION_MC_FALLBACK"
    assert plus2["entry_exactness"] == "POST_MIGRATION_OFFSET_2S_OBSERVED_MC"
    assert reduce_policy(migration_timestamp=100, entry={"timestamp": 103, "mc": 13})["state"] == "INSUFFICIENT_EVIDENCE"


def test_product_projection_is_separate_from_entry_authority(tmp_path):
    store = OffsetAuditStore(tmp_path / "audit.json")
    store.record(compact_record(mint="plus2", migration_timestamp=100, http_status=200, payload=_payload([_candle(102, 12)])))
    store.record(compact_record(mint="none", migration_timestamp=100, http_status=200, payload=_payload([])))
    projection = product_projection(tmp_path / "audit.json")
    assert projection["plus2"]["proposed_plus2_classification"] == "PLUS2"
    assert projection["plus2"]["simulated_selected_mc"] == 12.0
    assert projection["plus2"]["audit_label"] == "Historical audit candidate: +2"
    assert projection["none"]["audit_label"] == "NO_VALID_OPENING_CANDLE_THROUGH_PLUS4"
    assert "entry_mc_usd" not in projection["plus2"]
