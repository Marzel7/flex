import pytest

from src.ops.strict_migration_window import (
    normalize_opening_ohlcv,
    normalize_partial_birdeye_mcap_ohlcv,
)


def _row(timestamp, *, alias=False, high=12):
    if alias:
        return {"unix_time": timestamp, "open": 10, "high": high, "low": 8, "close": 11}
    return {"unixTime": timestamp, "o": 10, "h": high, "l": 8, "c": 11}


def test_shared_15m_contract_reuses_full_ohlc_aliases_and_orders_rows():
    rows, rejected = normalize_partial_birdeye_mcap_ohlcv(
        {"success": True, "data": {"items": [_row(1800, alias=True), _row(900)]}}
    )
    assert rejected == {}
    assert [row["timestamp"] for row in rows] == [900, 1800]
    assert rows[0]["open"] == 10.0 and rows[1]["high"] == 12.0


def test_shared_15m_contract_retains_valid_rows_and_accounts_for_invalid_and_duplicate():
    rows, rejected = normalize_partial_birdeye_mcap_ohlcv(
        {"success": True, "data": {"items": [_row(900), _row(900), _row(1800, high="nan")]}}
    )
    assert [row["timestamp"] for row in rows] == [900]
    assert rejected == {"DUPLICATE_TIMESTAMP": 1, "INVALID_VALUE_OR_FIELDS": 1}


def test_shared_15m_contract_requires_success_and_preserves_opening_normalizer_contract():
    with pytest.raises(ValueError, match="INVALID_PROVIDER_SUCCESS"):
        normalize_partial_birdeye_mcap_ohlcv({"success": False, "data": {"items": []}})
    opening = normalize_opening_ohlcv({"data": {"items": [_row(900)]}})
    assert opening == [{"timestamp": 900, "open": 10.0, "high": 12.0, "low": 8.0, "close": 11.0}]
