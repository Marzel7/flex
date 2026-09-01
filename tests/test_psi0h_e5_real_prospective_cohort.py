from hashlib import sha256
import json
from pathlib import Path

import pytest

from src.acquisition.transaction import AcquisitionMetadata, AcquisitionResponse
from src.evidence.contracts.psi0h_e5_real_prospective_cohort import (
    MAX_MIGRATIONS,
    MAX_REQUESTS,
    REQUESTS_PER_MIGRATION,
    Psi0hE5PreflightError,
    build_e5_real_prospective_preflight,
    verify_e5_preflight,
)


def _e4_preflight_payload(census_path: Path) -> dict:
    stats = census_path.stat()
    artifact = {
        "schema_version": "psi0h-e4.live-census-high-water-preflight.v1",
        "run_id": "psi0h-e4-live-census",
        "source_id": "pumpportal-migration-census",
        "source_kind": "migration-census-live-file",
        "census_path": str(census_path.resolve()),
        "census_size_bytes": stats.st_size,
        "census_start_offset": stats.st_size,
        "census_device": stats.st_dev,
        "census_inode": stats.st_ino,
        "census_mtime_ns": stats.st_mtime_ns,
        "interval_start": 1,
        "interval_end": 20,
        "cutoff": 0,
        "source_read": False,
        "source_read_authorized": False,
        "provider_access_authorized": False,
        "service_changes_authorized": False,
        "comparison_authorized": False,
        "monitoring_authorized": False,
        "activation_authorized": False,
        "maximum_census_bytes": 65536,
        "staging_directory": "/tmp/e4-stage",
        "output_directory": "/tmp/e4-output",
        "consumption_directory": "/tmp/e4-consumption",
    }
    digest = sha256(json.dumps(artifact, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"status": "PASS", "preflight": artifact, "preflight_digest": digest}


def _build_valid_inputs(tmp_path: Path):
    census_path = tmp_path / "oip_migration_census.jsonl"
    # establish an initial frozen byte offset by writing one old row
    pre_existing_row = json.dumps({
        "event_id": "old", "event_type": "MIGRATION", "signature": "old", "mint": "oldmint",
        "receive_utc_ns": 1_000_000_000,
    })
    census_path.write_text(pre_existing_row + "\n")
    e4_artifact = tmp_path / "psi0h_e4.json"
    e4_artifact.write_text(json.dumps(_e4_preflight_payload(census_path=census_path), sort_keys=True, separators=(",", ":")))
    e3_artifact = tmp_path / "psi0h_e3.json"
    e3_artifact.write_text(json.dumps({"status": "PASS"}))
    return census_path, e4_artifact, e3_artifact


def test_preflight_build_and_verify(tmp_path):
    census_path, e4_artifact, e3_artifact = _build_valid_inputs(tmp_path)
    e4_payload = json.loads(e4_artifact.read_text())
    record = build_e5_real_prospective_preflight(
        run_id="psi0h-e5-real-prospective",
        e4_artifact=e4_artifact,
        e4_preflight=e4_payload,
        e3_artifact=e3_artifact,
        e3_artifact_digest="a" * 64,
        staging_directory=tmp_path / "staging",
        output_directory=tmp_path / "output",
        consumption_directory=tmp_path / "consumption",
    )
    assert verify_e5_preflight(record)
    assert record.maximum_migrations == MAX_MIGRATIONS
    assert record.maximum_provider_requests == MAX_REQUESTS
    assert record.requests_per_migration == REQUESTS_PER_MIGRATION
    assert record.provider_access_authorized is False


def test_preflight_fails_if_e4_highwater_drifted(tmp_path):
    census_path, e4_artifact, e3_artifact = _build_valid_inputs(tmp_path)
    # truncate file after preflight freeze to trigger identity/high-water drift.
    census_path.write_text("{}\n")
    e4_payload = json.loads(e4_artifact.read_text())
    with pytest.raises(Psi0hE5PreflightError, match="E4_HIGHWATER_DRIFT"):
        build_e5_real_prospective_preflight(
            run_id="psi0h-e5-real-prospective",
            e4_artifact=e4_artifact,
            e4_preflight=e4_payload,
            e3_artifact=e3_artifact,
            e3_artifact_digest="a" * 64,
            staging_directory=tmp_path / "staging",
            output_directory=tmp_path / "output",
            consumption_directory=tmp_path / "consumption",
        )


def test_script_preflight_only_does_not_execute(tmp_path, monkeypatch):
    import scripts.run_psi0h_e5_real_prospective_cohort as runner

    _, e4_artifact, e3_artifact = _build_valid_inputs(tmp_path)
    out = tmp_path / "psi0h_e5_real_preflight.json"
    result = runner.run(output=out, e4_artifact=e4_artifact, e3_artifact=e3_artifact, execute=False)
    assert result["status"] == "PASS"
    assert result["execution_status"] == "READY_FOR_AUTHORIZATION"
    assert result["fixture_only"] is False
    assert not result["real_run_authorized"]


def test_script_execute_requires_authorization(tmp_path, monkeypatch):
    import scripts.run_psi0h_e5_real_prospective_cohort as runner

    _, e4_artifact, e3_artifact = _build_valid_inputs(tmp_path)
    out = tmp_path / "psi0h_e5_real_preflight.json"

    with pytest.raises(runner.Psi0hE5ExecutionError, match="NOT_AUTHORIZED"):
        runner.run(output=out, e4_artifact=e4_artifact, e3_artifact=e3_artifact, execute=True,
                   provider_url="https://example.com")


def test_script_execute_runs_with_authorized_transport_and_hard_stops_on_request_ceiling(tmp_path, monkeypatch):
    import scripts.run_psi0h_e5_real_prospective_cohort as runner

    census_path, e4_artifact, e3_artifact = _build_valid_inputs(tmp_path)

    # append one fresh migration candidate after high-water
    fresh_event = json.dumps({
        "event_id": "e1",
        "event_type": "MIGRATION",
        "signature": "sig1",
        "mint": "mint1",
        "receive_utc_ns": 2_000_000_000,
    }) + "\n"
    with census_path.open("a", encoding="utf-8") as handle:
        handle.write(fresh_event)

    def transport_ok(signature: str, mint: str) -> AcquisitionResponse:
        body = {
            "jsonrpc": "2.0",
            "result": {"blockTime": 2, "transaction": {"signatures": [signature]}},
        }
        raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        metadata = AcquisitionMetadata("acq", "corr", "psi0h-e5-real", None, mint, "json_rpc", "solana-rpc",
                                     "getTransaction", None, None, 1.0, "miss", 0)
        return AcquisitionResponse(200, body, None, {}, metadata, 1.0, raw, "EXACT_PROVIDER_ARTIFACT")

    monkeypatch.setattr(runner, "_make_transport", lambda _url: transport_ok)
    monkeypatch.setenv("PSI0H_E5_REAL_RUN_AUTHORIZED", "1")

    out = tmp_path / "psi0h_e5_real_preflight.json"
    result = runner.run(output=out, e4_artifact=e4_artifact, e3_artifact=e3_artifact, execute=True,
                        provider_url="https://dummy.local")
    assert result["execution_status"] == "COMPLETED"
    assert result["fixture_only"] is False
    assert result["real_run_authorized"] is True
    assert result["provider_request_count"] == 1
    assert result["scope"]["provider_access"] is True
    assert not result["execution"]["monitoring_activated"]
    assert not result["execution"]["activation_authority"]


def test_script_execute_accepts_live_rows_with_extra_fields(tmp_path, monkeypatch):
    import scripts.run_psi0h_e5_real_prospective_cohort as runner

    census_path, e4_artifact, e3_artifact = _build_valid_inputs(tmp_path)

    with census_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "event_id": "live1",
            "event_type": "MIGRATION",
            "signature": "sig-live",
            "mint": "mint-live",
            "receive_utc_ns": 2_000_000_000,
            "schema_version": 1,
            "source": "pumpportal",
        }) + "\n")

    def transport_ok(signature: str, mint: str) -> AcquisitionResponse:
        body = {
            "jsonrpc": "2.0",
            "result": {"blockTime": 2, "transaction": {"signatures": [signature]}},
        }
        raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        metadata = AcquisitionMetadata("acq", "corr", "psi0h-e5-real", None, mint, "json_rpc", "solana-rpc",
                                     "getTransaction", None, None, 1.0, "miss", 0)
        return AcquisitionResponse(200, body, None, {}, metadata, 1.0, raw, "EXACT_PROVIDER_ARTIFACT")

    monkeypatch.setattr(runner, "_make_transport", lambda _url: transport_ok)
    monkeypatch.setenv("PSI0H_E5_REAL_RUN_AUTHORIZED", "1")

    # rewrite preflight interval to include the appended event timestamp without altering boundary
    e4_payload = json.loads(e4_artifact.read_text())
    e4_payload["preflight"]["interval_start"] = 1
    e4_payload["preflight"]["interval_end"] = 3
    e4_payload["preflight"]["cutoff"] = 0
    from hashlib import sha256
    e4_payload["preflight_digest"] = sha256(
        json.dumps(e4_payload["preflight"], sort_keys=True, separators=(",", ":")).encode(),
    ).hexdigest()
    e4_payload["artifact_digest"] = sha256(
        json.dumps({k: v for k, v in e4_payload.items() if k != "artifact_digest"}, sort_keys=True,
                   separators=(",", ":")).encode(),
    ).hexdigest()
    e4_artifact.write_text(json.dumps(e4_payload, sort_keys=True, separators=(",", ":")))

    out = tmp_path / "psi0h_e5_real_extras.json"
    result = runner.run(output=out, e4_artifact=e4_artifact, e3_artifact=e3_artifact, execute=True,
                        provider_url="https://dummy.local")
    assert result["provider_request_count"] == 1
