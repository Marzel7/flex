from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from src.evidence.primitives.contracts import PrimitiveQuality, PrimitiveType
from src.evidence.primitives.engine import PrimitiveEngine
from src.evidence.primitives.registry import PrimitiveRegistry
from src.evidence.service import EvidencePlatform
from tests.test_ep1_3_immutable_evidence_normalization import (
    _config, _enqueue, _transaction_body,
)


def _platform_with_primitives(
    tmp_path: Path, *, history_signatures: list[str] | None = None,
    creator_pre_balance: int | None = None,
) -> EvidencePlatform:
    platform = EvidencePlatform(replace(_config(tmp_path), primitive_engine_enabled=True))
    body = _transaction_body()
    if creator_pre_balance is not None:
        body["result"]["meta"]["preBalances"][0] = creator_pre_balance
    instructions = body["result"]["transaction"]["message"]["instructions"]
    instructions[0]["parsed"]["info"].update({"source": "recipient", "destination": "creator"})
    instructions[0]["accounts"] = [1, 0]
    instructions[1]["accounts"] = [1, 0, 2]
    instructions[2]["accounts"] = [1, 0]
    instructions[3]["accounts"] = [0, 2]
    instructions.insert(1, {
        "programId": "11111111111111111111111111111111", "accounts": [1, 0],
        "parsed": {"type": "transfer", "info": {
            "source": "recipient", "destination": "creator", "lamports": 2000,
        }},
    })
    _enqueue(platform, body, envelope_id="env-primitive-tx")
    second = _transaction_body()
    if creator_pre_balance is not None:
        second["result"]["meta"]["preBalances"][0] = creator_pre_balance
    second["result"]["slot"] = 124
    second["result"]["blockTime"] = 1700000010
    second["result"]["transaction"]["signatures"] = ["sig-2"]
    second["result"]["transaction"]["message"]["instructions"] = [{
        "programId": "11111111111111111111111111111111", "accounts": [1, 0],
        "parsed": {"type": "transfer", "info": {
            "source": "recipient", "destination": "creator", "lamports": 3000,
        }},
    }]
    _enqueue(
        platform, second, envelope_id="env-primitive-tx-2",
        acquisition_changes={"transaction_signatures": ["sig-2"]},
    )
    _enqueue(
        platform,
        {"jsonrpc": "2.0", "result": [
            {"signature": signature}
            for signature in (history_signatures or ["sig-1"])
        ]},
        envelope_id="env-primitive-history", method="getSignaturesForAddress",
        acquisition_changes={"creator": "creator", "transaction_signatures": [],
                             "page_size": 100, "page_complete": True},
    )
    platform.writer.start()
    platform.writer.run_once()
    return platform


def _freshness_rows(path: Path, wallet: str = "creator") -> list[dict]:
    rows = _query(
        path,
        "SELECT quality_state,missing_inputs_json,failure_state,output_payload_json "
        "FROM primitive_observations WHERE primitive_type='WALLET_FRESH_AT_EVENT'",
    )
    return [
        {**dict(row), "output": json.loads(row["output_payload_json"]),
         "missing": json.loads(row["missing_inputs_json"])}
        for row in rows
        if json.loads(row["output_payload_json"]).get("wallet") == wallet
    ]


def _query(path: Path, sql: str) -> list[sqlite3.Row]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(sql).fetchall()
    connection.close()
    return rows


def test_registry_contains_only_eleven_frozen_v1_primitives():
    expected = {
        "SYSTEM_TRANSFER", "LAUNCH_SIGNER", "WSOL_CLOSE", "DIRECT_COUNTERPARTY",
        "PROGRAM_INTERACTION", "WALLET_FRESH_AT_EVENT", "LAUNCH_ACTIVATION",
        "ECONOMIC_FUNDING", "REPEATED_COUNTERPARTY", "SHARED_TRANSACTION",
        "BEHAVIOURAL_TIMING",
    }
    assert {item.value for item in PrimitiveRegistry.types()} == expected
    assert not expected.intersection(PrimitiveRegistry.deferred_candidates())
    assert set(PrimitiveRegistry.deferred_candidates()) == {
        "TOKEN_TRANSFER", "ACCOUNT_CREATION", "TRANSACTION_SIGNER", "FEE_PAYER",
        "LAUNCH_CREATOR", "ACCOUNT_CLOSE", "PROGRAM_REUSE",
    }


def test_engine_generates_all_v1_primitive_types_from_evidence_only(tmp_path):
    platform = _platform_with_primitives(tmp_path)
    try:
        rows = _query(
            platform.config.database_path,
            "SELECT DISTINCT primitive_type FROM primitive_observations",
        )
        assert {row["primitive_type"] for row in rows} == {
            item.value for item in PrimitiveType
        }
        assert platform.primitive_engine.health()["status"] == "HEALTHY"
    finally:
        platform.writer.stop()


def test_replay_is_deterministic_and_does_not_duplicate_primitives(tmp_path):
    platform = _platform_with_primitives(tmp_path)
    try:
        before = _query(
            platform.config.database_path,
            "SELECT primitive_id,output_payload_json FROM primitive_observations ORDER BY primitive_id",
        )
        replay = platform.primitive_engine.run_once()
        after = _query(
            platform.config.database_path,
            "SELECT primitive_id,output_payload_json FROM primitive_observations ORDER BY primitive_id",
        )
        assert replay["inserted"] == 0
        assert replay["duplicates"] == len(before)
        assert [tuple(row) for row in before] == [tuple(row) for row in after]
    finally:
        platform.writer.stop()


def test_wallet_freshness_ignores_activity_after_reference_event(tmp_path):
    platform = _platform_with_primitives(
        tmp_path, history_signatures=["sig-2", "sig-1"], creator_pre_balance=0
    )
    try:
        reference = [
            row for row in _freshness_rows(platform.config.database_path)
            if row["output"]["reference_event"] == "sig-1"
        ]
        assert len(reference) == 1
        assert reference[0]["output"]["freshness_state"] == "VERIFIED_FRESH"
        assert reference[0]["quality_state"] == "PROVEN"
        assert reference[0]["missing"] == []
    finally:
        platform.writer.stop()


def test_wallet_freshness_uses_only_strictly_preceding_history(tmp_path):
    platform = _platform_with_primitives(
        tmp_path, history_signatures=["sig-2", "sig-1", "older-sig"],
        creator_pre_balance=0,
    )
    try:
        reference = [
            row for row in _freshness_rows(platform.config.database_path)
            if row["output"]["reference_event"] == "sig-1"
        ]
        assert len(reference) == 1
        assert reference[0]["output"]["freshness_state"] == "NOT_FRESH"
        assert reference[0]["quality_state"] == "PROVEN"
    finally:
        platform.writer.stop()


def test_wallet_freshness_is_unverifiable_when_reference_is_not_retained(tmp_path):
    platform = _platform_with_primitives(
        tmp_path, history_signatures=["sig-2", "newer-sig"], creator_pre_balance=0
    )
    try:
        reference = [
            row for row in _freshness_rows(platform.config.database_path)
            if row["output"]["reference_event"] == "sig-1"
        ]
        assert len(reference) == 1
        assert reference[0]["output"]["freshness_state"] == "UNKNOWN"
        assert reference[0]["quality_state"] == "UNVERIFIABLE"
        assert reference[0]["failure_state"] == "MISSING_REFERENCE_EVENT"
        assert reference[0]["missing"] == [
            "AddressHistoryObservation.reference_event"
        ]
    finally:
        platform.writer.stop()


def test_primitive_versions_coexist_without_overwrite(tmp_path):
    platform = _platform_with_primitives(tmp_path)
    try:
        v1_count = _query(platform.config.database_path,
                          "SELECT COUNT(*) AS count FROM primitive_observations")[0]["count"]
        v2 = PrimitiveEngine(platform.writer.database, version="2", metrics=platform.metrics)
        result = v2.run_once()
        assert result["inserted"] == v1_count
        versions = _query(
            platform.config.database_path,
            "SELECT primitive_version,COUNT(*) AS count FROM primitive_observations "
            "GROUP BY primitive_version ORDER BY primitive_version",
        )
        assert [row["primitive_version"] for row in versions] == ["1", "2"]
        assert all(row["count"] == v1_count for row in versions)
    finally:
        platform.writer.stop()


def test_quality_states_are_closed_and_no_confidence_is_stored(tmp_path):
    platform = _platform_with_primitives(tmp_path)
    try:
        rows = _query(
            platform.config.database_path,
            "SELECT quality_state,parameters_json,output_payload_json FROM primitive_observations",
        )
        allowed = {item.value for item in PrimitiveQuality}
        assert {row["quality_state"] for row in rows} <= allowed
        serialized = json.dumps([dict(row) for row in rows]).lower()
        assert "confidence" not in serialized
        assert "probability" not in serialized
    finally:
        platform.writer.stop()


def test_parameterized_primitives_expose_policies_without_operation_thresholds(tmp_path):
    platform = _platform_with_primitives(tmp_path)
    try:
        economic = _query(
            platform.config.database_path,
            "SELECT parameters_json FROM primitive_observations "
            "WHERE primitive_type='ECONOMIC_FUNDING' LIMIT 1",
        )[0]
        params = json.loads(economic["parameters_json"])
        assert params == {"amount_policy": "UNFILTERED",
                          "recipient_policy": "LAUNCH_CREATOR"}
        assert "minimum_amount" not in params
    finally:
        platform.writer.stop()


def test_failure_isolation_keeps_other_generators_running(tmp_path, monkeypatch):
    platform = _platform_with_primitives(tmp_path)
    try:
        engine = PrimitiveEngine(platform.writer.database, version="failure-test",
                                 metrics=platform.metrics)
        monkeypatch.setattr(engine, "_system_transfers",
                            lambda _index: (_ for _ in ()).throw(ValueError("malformed")))
        result = engine.run_once()
        assert result["inserted"] > 0
        assert platform.metrics.snapshot()["counters"]["primitive_failures"] >= 1
    finally:
        platform.writer.stop()


def test_conflicting_provider_evidence_is_preserved_as_conflicting_quality(tmp_path):
    platform = EvidencePlatform(replace(_config(tmp_path), primitive_engine_enabled=True))
    first = _transaction_body(fee=5000)
    first["result"]["transaction"]["message"]["instructions"][0]["accounts"] = [0, 1]
    second = _transaction_body(fee=6000)
    second["result"]["transaction"]["message"]["instructions"][0]["accounts"] = [0, 1]
    _enqueue(platform, first, envelope_id="provider-a", provider="provider-a")
    _enqueue(platform, second, envelope_id="provider-b", provider="provider-b")
    platform.writer.start()
    try:
        platform.writer.run_once()
    finally:
        platform.writer.stop()
    rows = _query(
        platform.config.database_path,
        "SELECT quality_state,failure_state,output_payload_json FROM primitive_observations "
        "WHERE primitive_type='SYSTEM_TRANSFER'",
    )
    assert rows
    assert {row["quality_state"] for row in rows} == {"CONFLICTING"}
    assert {row["failure_state"] for row in rows} == {
        "CONFLICTING_EVIDENCE_OBSERVATIONS"
    }
    assert all("provider" not in json.loads(row["output_payload_json"]) for row in rows)


def test_primitive_storage_and_evidence_links_are_immutable(tmp_path):
    platform = _platform_with_primitives(tmp_path)
    platform.writer.stop()
    connection = sqlite3.connect(platform.config.database_path)
    with pytest.raises(sqlite3.IntegrityError, match="immutable primitive"):
        connection.execute("UPDATE primitive_observations SET primitive_version='changed'")
    with pytest.raises(sqlite3.IntegrityError, match="immutable primitive input"):
        connection.execute("DELETE FROM primitive_evidence_inputs")
    connection.close()


def test_engine_has_no_rpc_production_or_operation_dependencies():
    source = Path("src/evidence/primitives/engine.py").read_text()
    forbidden = (
        "aiohttp", "requests.", "src.core", "creator_funding", "walkback",
        "WATCHTOWER", "3SW2", "governance", "operator", "topology",
        "confidence", "similarity",
    )
    assert all(value not in source for value in forbidden)
