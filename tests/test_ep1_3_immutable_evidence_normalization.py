from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from src.evidence.config import EvidenceConfig
from src.evidence.normalization import NormalizationEngine
from src.evidence.normalizers import AcquisitionNormalizer
from src.evidence.service import EvidencePlatform


def _config(tmp_path: Path) -> EvidenceConfig:
    return EvidenceConfig(
        platform_enabled=True, writer_enabled=True, queue_enabled=True,
        artifact_store_enabled=True, health_enabled=True,
        normalization_enabled=True,
        database_path=tmp_path / "evidence" / "evidence.db",
        queue_path=tmp_path / "intake", artifact_path=tmp_path / "artifacts",
        mirror_spool_path=tmp_path / "spool", writer_batch_size=10,
        writer_poll_seconds=0.01,
    )


def _transaction_body(*, fee: int = 5000) -> dict:
    return {
        "jsonrpc": "2.0", "id": 1,
        "result": {
            "slot": 123, "blockTime": 1700000000, "version": "legacy",
            "transaction": {
                "signatures": ["sig-1"],
                "message": {
                    "header": {"numRequiredSignatures": 1,
                               "numReadonlySignedAccounts": 0,
                               "numReadonlyUnsignedAccounts": 1},
                    "accountKeys": ["creator", "recipient", "mint", "program"],
                    "recentBlockhash": "blockhash",
                    "instructions": [
                        {"programId": "11111111111111111111111111111111",
                         "parsed": {"type": "transfer", "info": {
                             "source": "creator", "destination": "recipient",
                             "lamports": 1000}}},
                        {"programId": "token-program", "parsed": {
                            "type": "transferChecked", "info": {
                                "source": "token-a", "destination": "token-b",
                                "mint": "mint", "authority": "creator",
                                "tokenAmount": {"amount": "25", "decimals": 6}}}},
                        {"programId": "token-program", "parsed": {
                            "type": "closeAccount", "info": {
                                "account": "token-a", "owner": "creator",
                                "authority": "creator", "destination": "creator",
                                "mint": "mint"}}},
                        {"programId": "launch-program", "parsed": {
                            "type": "create", "info": {
                                "mint": "mint", "creator": "creator", "name": "token"}}},
                    ],
                },
            },
            "meta": {
                "err": None, "fee": fee, "preBalances": [2000, 0, 0, 0],
                "postBalances": [500, 1000, 0, 0], "innerInstructions": [],
                "logMessages": ["ok"],
                "preTokenBalances": [{"accountIndex": 1, "mint": "mint",
                                       "owner": "recipient",
                                       "uiTokenAmount": {"amount": "0", "decimals": 6}}],
                "postTokenBalances": [{"accountIndex": 1, "mint": "mint",
                                        "owner": "recipient",
                                        "uiTokenAmount": {"amount": "25", "decimals": 6}}],
            },
        },
    }


def _enqueue(platform: EvidencePlatform, body: object, *, envelope_id: str,
             method: str = "getTransaction", provider: str = "provider-a",
             representation: str = "EXACT_PROVIDER_ARTIFACT",
             payload_type: str = "acquisition/response",
             acquisition_changes: dict | None = None) -> dict:
    raw = json.dumps(body, separators=(",", ":")).encode()
    artifact = platform.artifacts.put(raw, content_type="application/json")
    acquisition = {
        "acquisition_id": envelope_id, "correlation_id": "corr",
        "provider": provider, "method": method, "purpose": "test",
        "creator": "creator", "launch": "mint",
        "transaction_signatures": ["sig-1"], "cursor": None,
        "request_digest": "a" * 64, "response_digest": artifact.digest,
        "timestamp": 1700000001, "parser_version": "raw-acquisition-v1",
        "retry_count": 0, "cache_state": "miss",
        "artifact_reference": artifact.digest,
        "artifact_representation": representation,
    }
    acquisition.update(acquisition_changes or {})
    envelope = {
        "envelope_id": envelope_id, "observed_at": 1700000000,
        "acquired_at": 1700000001, "source": "shared_transaction_acquisition",
        "source_version": "ep1.2-mirror-v1", "provider": provider,
        "evidence_digest": artifact.digest, "replay_version": "1",
        "parser_version": "raw-acquisition-v1", "payload_type": payload_type,
        "artifact": {"digest": artifact.digest, "size_bytes": artifact.size_bytes,
                     "compressed_bytes": artifact.compressed_bytes,
                     "content_type": artifact.content_type, "compression": artifact.compression,
                     "representation": representation},
        "provenance": {"provider_request_id": envelope_id,
                       "rpc_verification_state": "ACQUIRED_RESPONSE",
                       "acquisition_method": method,
                       "source_metadata": {"request_digest": "a" * 64,
                                           "dependency_group": provider}},
        "acquisition": acquisition,
    }
    platform.queue.enqueue(envelope, message_id=envelope_id)
    return envelope


def _rows(path: Path, query: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return rows


def test_transaction_artifact_normalizes_operation_neutral_fact_families(tmp_path):
    platform = EvidencePlatform(_config(tmp_path))
    _enqueue(platform, _transaction_body(), envelope_id="env-tx")
    platform.writer.start()
    try:
        assert platform.writer.run_once()["inserted"] == 1
    finally:
        platform.writer.stop()
    families = {row["fact_family"] for row in _rows(
        platform.config.database_path,
        "SELECT fact_family FROM normalized_evidence_records",
    )}
    assert {
        "TransactionFact", "AccountParticipationFact", "InstructionFact",
        "BalanceFact", "NativeMovementFact", "TokenMovementFact",
        "AccountCloseFact", "ProgramEventFact", "LaunchFact",
        "TransactionVerificationObservation",
    } <= families
    status = _rows(platform.config.database_path, "SELECT * FROM normalization_status")[0]
    assert status["state"] == "COMPLETE"
    assert status["artifact_representation"] == "EXACT_PROVIDER_ARTIFACT"
    assert platform.normalizer.health()["status"] == "HEALTHY"
    assert platform.normalizer.health()["artifacts_awaiting_normalization"] == 0
    launch = _rows(
        platform.config.database_path,
        "SELECT payload_json FROM normalized_evidence_records WHERE fact_family='LaunchFact'",
    )[0]
    assert json.loads(launch["payload_json"])["creator_signer_state"] is True


def test_replay_is_idempotent_and_parser_versions_coexist(tmp_path):
    platform = EvidencePlatform(_config(tmp_path))
    envelope = _enqueue(platform, _transaction_body(), envelope_id="env-replay")
    platform.writer.start()
    try:
        platform.writer.run_once()
        before = _rows(platform.config.database_path,
                       "SELECT evidence_id,logical_fact_id FROM normalized_evidence_records ORDER BY evidence_id")
        replay = platform.normalizer.normalize_envelope(envelope)
        after = _rows(platform.config.database_path,
                      "SELECT evidence_id,logical_fact_id FROM normalized_evidence_records ORDER BY evidence_id")
        assert replay["inserted"] == 0
        assert [tuple(row) for row in before] == [tuple(row) for row in after]
        v2 = NormalizationEngine(
            platform.writer.database, platform.artifacts,
            normalizer=AcquisitionNormalizer(parser_version="2"),
            metrics=platform.metrics,
        )
        result = v2.normalize_envelope(envelope)
        assert result["inserted"] == len(before)
    finally:
        platform.writer.stop()
    observations = _rows(
        platform.config.database_path,
        "SELECT logical_fact_id,COUNT(DISTINCT evidence_id) AS observations "
        "FROM normalized_evidence_records GROUP BY logical_fact_id",
    )
    assert all(row["observations"] == 2 for row in observations)


def test_provider_disagreement_preserves_shared_logical_fact_and_both_observations(tmp_path):
    platform = EvidencePlatform(_config(tmp_path))
    _enqueue(platform, _transaction_body(fee=5000), envelope_id="env-a", provider="provider-a")
    _enqueue(platform, _transaction_body(fee=6000), envelope_id="env-b", provider="provider-b")
    platform.writer.start()
    try:
        platform.writer.run_once()
    finally:
        platform.writer.stop()
    rows = _rows(
        platform.config.database_path,
        "SELECT logical_fact_id,evidence_id,payload_json FROM normalized_evidence_records "
        "WHERE fact_family='TransactionFact' ORDER BY evidence_id",
    )
    assert len(rows) == 2
    assert rows[0]["logical_fact_id"] == rows[1]["logical_fact_id"]
    assert rows[0]["evidence_id"] != rows[1]["evidence_id"]
    assert {json.loads(row["payload_json"])["fee"] for row in rows} == {5000, 6000}


def test_legacy_artifact_is_accepted_without_claiming_exact_provenance(tmp_path):
    platform = EvidencePlatform(_config(tmp_path))
    wrapper = {"status": 200, "data": _transaction_body(), "text": None,
               "headers": {"Content-Type": "application/json"}}
    _enqueue(platform, wrapper, envelope_id="env-legacy",
             representation="CANONICALIZED_RESPONSE_REPRESENTATION")
    platform.writer.start()
    try:
        platform.writer.run_once()
        health = platform.normalizer.health()
    finally:
        platform.writer.stop()
    qualities = {row["provenance_quality"] for row in _rows(
        platform.config.database_path,
        "SELECT DISTINCT provenance_quality FROM normalized_evidence_records",
    )}
    assert qualities == {"CANONICALIZED_LEGACY_REPRESENTATION"}
    assert health["legacy_artifact_ratio"] == 1.0


def test_malformed_artifact_fails_independently_and_batch_continues(tmp_path):
    platform = EvidencePlatform(_config(tmp_path))
    _enqueue(platform, _transaction_body(), envelope_id="env-good")
    bad = platform.artifacts.put(b"not-json", content_type="application/json")
    envelope = {
        "envelope_id": "env-bad", "observed_at": 1, "acquired_at": 2,
        "source": "shared_transaction_acquisition", "source_version": "1",
        "provider": "provider", "evidence_digest": bad.digest,
        "replay_version": "1", "parser_version": "raw", "payload_type": "acquisition/response",
        "artifact": {"digest": bad.digest, "size_bytes": bad.size_bytes,
                     "compressed_bytes": bad.compressed_bytes,
                     "content_type": bad.content_type, "compression": bad.compression,
                     "representation": "EXACT_PROVIDER_ARTIFACT"},
        "provenance": {"provider_request_id": "bad", "rpc_verification_state": "ACQUIRED_RESPONSE",
                       "acquisition_method": "getTransaction",
                       "source_metadata": {"request_digest": "b" * 64}},
        "acquisition": {"method": "getTransaction", "transaction_signatures": ["bad"],
                        "artifact_representation": "EXACT_PROVIDER_ARTIFACT"},
    }
    platform.queue.enqueue(envelope, message_id="env-bad")
    platform.writer.start()
    try:
        result = platform.writer.run_once()
    finally:
        platform.writer.stop()
    assert result["inserted"] == 2
    states = {row["envelope_id"]: row["state"] for row in _rows(
        platform.config.database_path, "SELECT envelope_id,state FROM normalization_status"
    )}
    assert states == {"env-bad": "FAILED", "env-good": "COMPLETE"}


def test_history_and_external_registry_complete_all_frozen_families(tmp_path):
    platform = EvidencePlatform(_config(tmp_path))
    _enqueue(
        platform, {"jsonrpc": "2.0", "result": [{"signature": "history-sig"}]},
        envelope_id="env-history", method="getSignaturesForAddress",
        acquisition_changes={"creator": "address", "transaction_signatures": [],
                             "page_size": 100, "page_complete": True},
    )
    _enqueue(
        platform,
        {"subject": "wallet", "claimed_label": "Exchange", "registry": "registry",
         "registry_version": "2026-08", "source_url": "https://example.invalid",
         "document_digest": "c" * 64, "valid_from": None, "valid_to": None,
         "observed_at": 1700000000},
        envelope_id="env-registry", method="registryLookup",
        payload_type="external/registry",
    )
    platform.writer.start()
    try:
        platform.writer.run_once()
    finally:
        platform.writer.stop()
    families = {row["fact_family"] for row in _rows(
        platform.config.database_path,
        "SELECT fact_family FROM normalized_evidence_records",
    )}
    assert families == {"AddressHistoryObservation", "ExternalRegistryObservation"}


def test_immutable_fact_and_provenance_tables_reject_mutation(tmp_path):
    platform = EvidencePlatform(_config(tmp_path))
    _enqueue(platform, _transaction_body(), envelope_id="env-immutable")
    platform.writer.start()
    try:
        platform.writer.run_once()
    finally:
        platform.writer.stop()
    conn = sqlite3.connect(platform.config.database_path)
    with pytest.raises(sqlite3.IntegrityError, match="immutable normalized evidence"):
        conn.execute("UPDATE normalized_evidence_records SET provider='changed'")
    with pytest.raises(sqlite3.IntegrityError, match="immutable normalized provenance"):
        conn.execute("DELETE FROM normalized_evidence_provenance")
    conn.close()


def test_normalization_package_contains_no_rpc_or_production_consumer_imports():
    paths = [Path("src/evidence/normalization.py"), Path("src/evidence/normalizers.py")]
    source = "\n".join(path.read_text() for path in paths)
    forbidden = ("aiohttp", "requests.", "creator_funding", "walkback", "WATCHTOWER", "3SW2")
    assert all(item not in source for item in forbidden)
