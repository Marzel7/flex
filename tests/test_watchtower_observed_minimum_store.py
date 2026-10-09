import copy
import sqlite3

import pytest

from src.ops.watchtower_observed_minimum import observed_minima
from src.ops.watchtower_observed_minimum_evidence import observed_minimum_records, request_identity
from src.ops.watchtower_observed_minimum_store import (
    EvidenceIdentityConflictError,
    EvidenceStoreLimitError,
    MAX_MANIFEST_BYTES,
    MAX_MINT_BYTES,
    ObservedMinimumEvidenceStore,
)


def _records(version="WATCHTOWER_OBSERVED_MINIMUM_EVIDENCE_V1"):
    contract = observed_minima(
        entry_timestamp=121, entry_mc_usd=100_000,
        candles=[{"timestamp": timestamp, "low_mc_usd": 100_000 - timestamp}
                 for timestamp in range(180, 3720, 60)],
        provider_provenance="BIRDEYE_V3_OHLCV_MCAP_USD_1M",
    )
    request = request_identity(mint="mint", entry_timestamp=121, window_end=3721,
                               provider_provenance="BIRDEYE_V3_OHLCV_MCAP_USD_1M")
    return observed_minimum_records(
        mint="mint", entry_identity={"timestamp": 121, "mc_usd": 100_000, "method": "QUALIFIED"},
        request_id=request, contract_result=contract, record_version=version,
    )


def test_all_four_windows_append_and_restart_read_only(tmp_path):
    store = ObservedMinimumEvidenceStore(tmp_path / "evidence.db")
    rows = _records()
    assert store.append_all(rows) == (True, True, True, True)
    restarted = ObservedMinimumEvidenceStore(tmp_path / "evidence.db")
    retained = restarted.read(mint="mint")
    assert {row["window_seconds"] for row in retained} == {300, 900, 1800, 3600}
    assert all("candles" not in row and "raw" not in row for row in retained)


def test_exact_duplicate_is_noop_and_conflict_fails_closed(tmp_path):
    store = ObservedMinimumEvidenceStore(tmp_path / "evidence.db")
    row = _records()[0]
    assert store.append(row) is True
    assert store.append(copy.deepcopy(row)) is False
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "UPDATE watchtower_observed_minimum_evidence SET payload_zlib=? WHERE evidence_identity=?",
            (b"not-the-same-record", row["evidence_identity"]),
        )
    with pytest.raises(EvidenceIdentityConflictError, match="EVIDENCE_IDENTITY_COLLISION"):
        store.append(row)


def test_versions_coexist_without_rewriting_prior_evidence(tmp_path):
    store = ObservedMinimumEvidenceStore(tmp_path / "evidence.db")
    old = _records("V1")[0]
    new = _records("V2")[0]
    assert store.append(old) is True
    assert store.append(new) is True
    assert {row["record_version"] for row in store.read()} == {"V1", "V2"}


def test_raw_and_record_byte_limit_fail_before_persistence(monkeypatch, tmp_path):
    store = ObservedMinimumEvidenceStore(tmp_path / "evidence.db")
    row = _records()[0]
    with pytest.raises(ValueError, match="RAW_EVIDENCE_FORBIDDEN"):
        store.append({**row, "raw": "forbidden"})
    monkeypatch.setattr("src.ops.watchtower_observed_minimum_store.MAX_RECORD_BYTES", 1)
    with pytest.raises(EvidenceStoreLimitError, match="RECORD_BYTES_EXCEEDED"):
        store.append(row)
    assert store.read() == ()


def test_per_mint_and_manifest_limits_fail_closed(monkeypatch, tmp_path):
    store = ObservedMinimumEvidenceStore(tmp_path / "evidence.db")
    row = _records()[0]
    compressed_bytes = compressed_len(row)
    monkeypatch.setattr("src.ops.watchtower_observed_minimum_store.MAX_MINT_BYTES", compressed_bytes - 1)
    with pytest.raises(EvidenceStoreLimitError, match="MINT_BYTES_EXCEEDED"):
        store.append(row)
    monkeypatch.setattr("src.ops.watchtower_observed_minimum_store.MAX_MINT_BYTES", MAX_MINT_BYTES)
    monkeypatch.setattr("src.ops.watchtower_observed_minimum_store.MAX_MANIFEST_BYTES", compressed_bytes - 1)
    with pytest.raises(EvidenceStoreLimitError, match="MANIFEST_BYTES_EXCEEDED"):
        store.append(row)
    assert store.read() == ()


def json_bytes(row):
    import json
    return json.dumps(row, sort_keys=True, separators=(",", ":")).encode()


def compressed_len(row):
    import zlib
    return len(zlib.compress(json_bytes(row), level=9))


def test_adapter_does_not_touch_unrelated_monitor_database(tmp_path):
    monitor = tmp_path / "monitor.db"
    with sqlite3.connect(monitor) as conn:
        conn.execute("CREATE TABLE monitor_facts (mint TEXT)")
        conn.execute("INSERT INTO monitor_facts VALUES ('immutable')")
    before = monitor.read_bytes()
    store = ObservedMinimumEvidenceStore(tmp_path / "isolated" / "evidence.db")
    store.append_all(_records())
    assert monitor.read_bytes() == before


def test_uncommitted_external_sqlite_write_is_not_visible_after_restart(tmp_path):
    store = ObservedMinimumEvidenceStore(tmp_path / "evidence.db")
    store.initialize()
    conn = sqlite3.connect(store.path)
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "INSERT INTO watchtower_observed_minimum_evidence VALUES (?,?,?,?)",
        ("uncommitted", "mint", "V1", b"{}"),
    )
    conn.rollback()
    conn.close()
    assert ObservedMinimumEvidenceStore(store.path).read() == ()
