import copy

import pytest

from src.ops.token_data_provider_bindings import ProviderTransportOutcome
from src.ops.watchtower_sharp_exit_evidence import normalize_30s_exit_evidence, request_identity


MINT = "TestMint111111111111111111111111111111111111"
ENTRY = 1_800_000_000
CANDIDATE = 1_800_003_600


def candle(timestamp, opening, high, low, close):
    return {"unixTime": timestamp, "o": opening, "h": high, "l": low, "c": close}


def outcome(items, *, success=True, headers=None, status=200, payload=True):
    return ProviderTransportOutcome(status, {"success": success, "data": {"items": items}} if payload else None,
                                    {"X-RateLimit-Limit": "300"} if headers is None else headers)


def valid_items():
    start = CANDIDATE - 300
    rows = []
    for index, timestamp in enumerate(range(start, CANDIDATE + 4500, 30)):
        opening = 100_000.0 - index
        rows.append(candle(timestamp, opening, opening + 20, opening - 20, opening - 5))
    return rows


def normalize(items=None, **kwargs):
    return normalize_30s_exit_evidence(
        outcome=kwargs.pop("transport", outcome(valid_items() if items is None else items)), mint=MINT,
        entry_timestamp=ENTRY, entry_mc_usd=120_000, candidate_start=CANDIDATE,
        request_id=request_identity(mint=MINT, entry_timestamp=ENTRY, candidate_start=CANDIDATE),
        provider_provenance="BIRDEYE_V3_30S_MCAP", observed_peak_timestamp=ENTRY + 900, **kwargs)


def test_actual_transport_shape_normalizes_catastrophic_candle_without_raw_history():
    rows = valid_items()
    rows[10] = candle(CANDIDATE, 100_000, 100_100, 2_900, 3_000)
    record = normalize(rows)
    assert record["exit_classification"] == "HIGH_RESOLUTION_EXIT_CONFIRMED"
    assert record["largest_observed_red_candle"]["collapse_percent"] == pytest.approx(97.0)
    assert record["missing_bucket_timestamps"] == []
    assert "items" not in record and "payload" not in record


@pytest.mark.parametrize("transport,error", [
    (ProviderTransportOutcome(200, None, {"X": "1"}), "MISSING_PROVIDER_PAYLOAD"),
    (ProviderTransportOutcome(200, {"success": True, "data": {"items": []}}, {}), "MISSING_RESPONSE_HEADERS"),
    (ProviderTransportOutcome(200, {"success": False, "data": {"items": []}}, {"X": "1"}), "INVALID_PROVIDER_SUCCESS_STATE"),
    (ProviderTransportOutcome(200, {"success": True, "data": {"items": [None]}}, {"X": "1"}), "NON_MAPPING_PROVIDER_CANDLE_ITEM"),
    (outcome([]), "EMPTY_PROVIDER_CANDLE_LIST"),
])
def test_transport_shape_failures_fail_closed(transport, error):
    with pytest.raises(ValueError, match=error):
        normalize(transport=transport)


def test_sparse_internal_gap_and_tail_keep_exact_gap_coordinates():
    rows = valid_items()
    rows = [row for row in rows if row["unixTime"] not in {CANDIDATE + 30, CANDIDATE + 60} and row["unixTime"] < CANDIDATE + 4410]
    record = normalize(rows)
    assert record["coverage_classification"] == "PARTIAL_30S_COVERAGE"
    assert record["missing_bucket_timestamps"] == [CANDIDATE + 30, CANDIDATE + 60, *range(CANDIDATE + 4410, CANDIDATE + 4500, 30)]
    assert record["missing_bucket_ranges"] == [[CANDIDATE + 30, CANDIDATE + 60], [CANDIDATE + 4410, CANDIDATE + 4470]]


@pytest.mark.parametrize("mutate,error", [
    (lambda rows: rows + [copy.deepcopy(rows[-1])], "DUPLICATE_OR_OUT_OF_ORDER_30S_CANDLES"),
    (lambda rows: [dict(row, l=0) if row["unixTime"] == CANDIDATE else row for row in rows], "INVALID_30S_MCAP_CANDLE"),
])
def test_duplicate_and_invalid_ohlc_fail_closed(mutate, error):
    with pytest.raises(ValueError, match=error):
        normalize(mutate(valid_items()))


def test_multiple_candle_decline_and_no_collapse_classifications():
    rows = valid_items()
    rows[10] = candle(CANDIDATE, 100_000, 100_100, 90_000, 95_000)
    rows[11] = candle(CANDIDATE + 30, 95_000, 95_100, 85_000, 90_000)
    assert normalize(rows)["exit_classification"] == "MULTI_CANDLE_DECLINE"
    green = [candle(row["unixTime"], row["o"], row["o"] + 20, row["o"] - 20, row["o"] + 5) for row in valid_items()]
    assert normalize(green)["exit_classification"] == "NO_QUALIFYING_COLLAPSE"


def test_response_fields_prevent_original_attribute_error_and_identity_is_deterministic():
    first = normalize()
    second = normalize()
    assert first["evidence_identity"] == second["evidence_identity"]
    assert first["provider_response_metadata"] == {"x-ratelimit-limit": "300"}
    assert first["returned_candle_count"] == 160
