from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.acquisition.transaction import AcquisitionMetadata
from src.evidence.artifacts import ArtifactStore
from src.evidence.queue import EvidenceIntakeQueue
from src.ops.watchtower_shadow_recovery import (
    RecoveryBudget,
    RecoveryBudgetExceeded,
    RecoveryLimits,
    WatchtowerShadowRecovery,
)


def _metadata(method: str = "getTransaction") -> AcquisitionMetadata:
    return AcquisitionMetadata(
        acquisition_id="a", correlation_id="c", purpose="shadow",
        creator=None, launch=None, request_type="json_rpc", provider="helius_rpc",
        method=method, page_number=None, cursor=None, timestamp=1.0,
        cache_state="miss", retry_count=0,
    )


def test_budget_counts_physical_attempts_and_stops_before_overrun():
    budget = RecoveryBudget(RecoveryLimits(rpc_calls=2, credits=20))
    budget.observe(_metadata())
    budget.observe(_metadata("getSignaturesForAddress"))
    assert budget.report()["rpc_calls"] == 2
    assert budget.report()["credits"] == 20
    with pytest.raises(RecoveryBudgetExceeded):
        budget.observe(_metadata())
    assert budget.report()["rpc_calls"] == 2


def test_required_signature_projection_is_population_bounded():
    population = {
        "launches": [{"mint": "mint-a", "creator_wallet": "creator-a",
                      "create_signature": None, "wrap_close_signature": "1" * 80}],
        "provisioning_edges": [
            {"source_mint": "mint-a", "funding_tx_signature": "2" * 80,
             "to_wallet": "creator-a"},
            {"source_mint": "outside", "funding_tx_signature": "3" * 80,
             "to_wallet": "creator-b"},
        ],
    }
    required = WatchtowerShadowRecovery._required_signatures(
        population, {"mint-a": "4" * 80}
    )
    assert set(required) == {"1" * 80, "2" * 80, "4" * 80}
    assert all(item["launch"] == "mint-a" for item in required.values())


def test_recovery_defaults_are_shadow_paths_and_frozen_limits():
    recovery = WatchtowerShadowRecovery(
        operations_db=Path("database/wt_ops_v2.db"),
        main_db=Path("database/flex_complete_database.db"),
        transaction_cache_db=Path("database/transaction_first_lineage.db"),
        output_root=Path("database/evidence_platform/watchtower_shadow_ep3_0d"),
    )
    assert recovery.limits == RecoveryLimits()
    config = recovery.materializer._config()
    assert "evidence_platform" in str(config.database_path)
    assert config.database_path.name == "evidence.db"


def test_ep3_0g_amends_distinct_observations_without_duplicating_artifact(tmp_path):
    recovery = WatchtowerShadowRecovery(
        operations_db=tmp_path / "ops.db", main_db=tmp_path / "main.db",
        transaction_cache_db=tmp_path / "cache.db", output_root=tmp_path / "shadow",
    )
    artifacts = ArtifactStore(tmp_path / "shadow" / "artifacts", enabled=True)
    artifact = artifacts.put(b'{"jsonrpc":"2.0","result":null}',
                             content_type="application/json")
    queue = EvidenceIntakeQueue(tmp_path / "shadow" / "intake", enabled=True)
    for number in (1, 2):
        envelope = {
            "envelope_id": f"legacy-{number}", "observed_at": number,
            "acquired_at": number, "source": "shared_transaction_acquisition",
            "source_version": "ep1.2-mirror-v1", "provider": "provider",
            "evidence_digest": artifact.digest, "replay_version": "1",
            "parser_version": "raw", "payload_type": "acquisition/response",
            "artifact": {"digest": artifact.digest},
            "provenance": {"acquisition_method": "getTransaction",
                           "source_metadata": {"request_digest": str(number) * 64,
                                               "http_status": 200}},
            "acquisition": {"acquisition_id": f"acq-{number}",
                            "correlation_id": f"corr-{number}", "purpose": "test",
                            "provider": "provider", "method": "getTransaction",
                            "request_digest": str(number) * 64, "timestamp": number,
                            "retry_count": 0, "cache_state": "miss"},
        }
        queue.enqueue(envelope, message_id=f"legacy-{number}")
    report = recovery._amend_durable_queue()
    payloads = [json.loads(path.read_text()) for path in
                (tmp_path / "shadow" / "intake" / "pending").glob("*.json")]
    assert report == {
        "messages_amended": 2, "observation_digests": 2,
        "artifact_digests": 1, "shared_artifact_references": 1,
    }
    assert len({item["envelope"]["evidence_digest"] for item in payloads}) == 2
    assert {item["envelope"]["artifact"]["digest"] for item in payloads} == {artifact.digest}
