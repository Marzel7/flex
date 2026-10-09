import copy
import json

import pytest

from src.ops.watchtower_observed_minimum import observed_minima
from src.ops.watchtower_observed_minimum_evidence import append_once, observed_minimum_records, request_identity


def _contract():
    candles = [{"timestamp": timestamp, "low_mc_usd": 100_000 - timestamp} for timestamp in range(180, 3720, 60)]
    return observed_minima(entry_timestamp=121, entry_mc_usd=100_000, candles=candles,
                            provider_provenance="BIRDEYE_V3_OHLCV_MCAP_USD_1M")


def _records(version="WATCHTOWER_OBSERVED_MINIMUM_EVIDENCE_V1"):
    contract = _contract()
    rid = request_identity(mint="mint", entry_timestamp=121, window_end=3721,
                           provider_provenance="BIRDEYE_V3_OHLCV_MCAP_USD_1M")
    return observed_minimum_records(mint="mint", entry_identity={"timestamp": 121, "mc_usd": 100_000, "method": "QUALIFIED"},
                                    request_id=rid, contract_result=contract, record_version=version)


def test_one_append_only_record_is_built_per_all_four_windows_without_raw_candles():
    rows = _records()
    assert [row["window_seconds"] for row in rows] == [300, 900, 1800, 3600]
    assert all("candles" not in row and "raw" not in row for row in rows)
    assert all(len(json.dumps(row)) < 2048 for row in rows)


def test_duplicate_identity_is_a_noop_and_collision_fails_closed():
    row = _records()[0]
    stored, inserted = append_once((), row)
    assert inserted is True
    same, inserted = append_once(stored, copy.deepcopy(row))
    assert same == stored and inserted is False
    conflict = {**row, "observed_minimum_mc_usd": 1}
    with pytest.raises(ValueError, match="EVIDENCE_IDENTITY_COLLISION"):
        append_once(stored, conflict)


def test_version_coexistence_has_distinct_identity_without_rewriting_prior_evidence():
    old, new = _records("V1")[0], _records("V2")[0]
    stored, _ = append_once((), old)
    stored, inserted = append_once(stored, new)
    assert inserted is True
    assert len(stored) == 2
    assert stored[0] == old
    assert old["evidence_identity"] != new["evidence_identity"]


def test_missing_entry_or_result_fields_fail_closed():
    with pytest.raises(ValueError, match="INVALID_ENTRY_IDENTITY"):
        observed_minimum_records(mint="mint", entry_identity={}, request_id="request", contract_result=_contract())
    with pytest.raises(ValueError, match="INCOMPLETE_OBSERVED_MINIMUM_RESULT"):
        observed_minimum_records(mint="mint", entry_identity={"timestamp": 1, "mc_usd": 1}, request_id="request",
                                 contract_result={"results": {"300": {}}})
