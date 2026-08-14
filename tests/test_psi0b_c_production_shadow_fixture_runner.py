import json
from pathlib import Path
import sqlite3

import pytest

from src.evidence.contracts.production_shadow_fixture_runner import (
    ProductionShadowFixtureRunnerError,
    build_fixture_runner_contract,
    execute_fixture_shadow,
    verify_fixture_runner_contract,
    verify_fixture_shadow_bundle,
)
from src.evidence.contracts.production_shadow_health_gate import (
    build_health_checkpoint,
    build_production_shadow_health_gate_contract,
    evaluate_active_health_gate,
    evaluate_prestart_health_gate,
)
from src.evidence.contracts.production_shadow_run_preflight import (
    build_immutable_cohort_artifact,
    build_production_shadow_run_preflight,
)


def _checkpoint(at, **changes):
    values = dict(
        observed_at_epoch=at, listener_pid=1, listener_service_state="RUNNING",
        primary_fd_count=0, critical_listener_db_handle_count=0,
        serializer_p99_wait_ms=1.0, serializer_lock_errors=0,
        serializer_queue_depth=0, database_wal_state="HEALTHY",
        write_lease_state="HEALTHY", pumpportal_state="HEALTHY",
        pumpswap_state="HEALTHY", ingestion_state="HEALTHY", worker_state="HEALTHY",
        queue_state="HEALTHY", service_state="HEALTHY", telemetry_complete=True,
    )
    values.update(changes)
    return build_health_checkpoint(**values)


def _health():
    contract = build_production_shadow_health_gate_contract()
    prestart = evaluate_prestart_health_gate(
        contract, (_checkpoint(40), _checkpoint(70), _checkpoint(100)),
        now_epoch=101, baseline_lock_errors=0,
    )
    active = evaluate_active_health_gate(
        contract, _checkpoint(70), _checkpoint(100), now_epoch=101,
        expected_listener_pid=1, baseline_lock_errors=0,
    )
    return prestart, active


def _databases(root: Path):
    paths = {name: root / f"{name}.sqlite" for name in ("creator", "evidence", "main", "ops")}
    db = sqlite3.connect(paths["creator"])
    db.execute("CREATE TABLE creator_tokens(creator_address TEXT,mint TEXT,created_at INTEGER)")
    db.execute("CREATE INDEX ix_creator_mint ON creator_tokens(mint)")
    db.execute("INSERT INTO creator_tokens VALUES('creator','mint-a',1)"); db.commit(); db.close()
    db = sqlite3.connect(paths["evidence"])
    db.execute("CREATE TABLE normalized_evidence_records(fact_family TEXT,payload_json TEXT,raw_artifact_digest TEXT,acquired_at INTEGER,source_id TEXT,source_version TEXT,verification_state TEXT)")
    db.execute("CREATE INDEX ix_evidence_family ON normalized_evidence_records(fact_family)")
    db.execute("INSERT INTO normalized_evidence_records VALUES('LaunchFact','{}','d',1,'s','v','VERIFIED')"); db.commit(); db.close()
    db = sqlite3.connect(paths["main"])
    db.executescript("""
      CREATE TABLE token_analysis(mint TEXT,migrated_at INTEGER,first_observed_mc REAL,first_observed_price REAL,first_observed_at INTEGER,first_observed_source TEXT,first_observed_confidence REAL,pf_ws_creator TEXT,creator_mismatch INTEGER);
      CREATE INDEX ix_token_mint ON token_analysis(mint);
      CREATE TABLE token_price_snapshots(snapshot_id INTEGER,mint TEXT,price_usd REAL,market_cap REAL,source TEXT,captured_at INTEGER,created_at INTEGER);
      CREATE INDEX ix_snap_mint_time ON token_price_snapshots(mint,captured_at);
      INSERT INTO token_analysis VALUES('mint-a',2,10,1,2,'s',1,'creator',0);
      INSERT INTO token_price_snapshots VALUES(1,'mint-a',1,10,'s',2,2);
    """); db.commit(); db.close()
    db = sqlite3.connect(paths["ops"])
    db.execute("CREATE TABLE wt_watchtower_launches(mint TEXT,creator_wallet TEXT,create_signature TEXT,create_time INTEGER,create_slot INTEGER,creator_extraction_method TEXT,confidence TEXT,recorded_at INTEGER)")
    db.execute("CREATE INDEX ix_ops_mint ON wt_watchtower_launches(mint)")
    db.execute("INSERT INTO wt_watchtower_launches VALUES('mint-a','creator','sig',1,1,'fixture','HIGH',1)"); db.commit(); db.close()
    return paths


def _inputs(tmp_path):
    root = tmp_path / "fixtures"; root.mkdir()
    paths = _databases(root)
    output = tmp_path / "output"
    cohort = build_immutable_cohort_artifact(
        cohort_id="fixture", mints=("mint-a", "mint-b"), source_artifact_digest="a" * 64,
    )
    preflight = build_production_shadow_run_preflight(
        run_id="fixture-run", cohort=cohort, fact_family="LaunchFact", output_directory=output,
    )
    prestart, active = _health()
    return root, paths, output, preflight, prestart, active


def test_contract_and_successful_fixture_bundle_replay(tmp_path):
    root, paths, output, preflight, prestart, active = _inputs(tmp_path)
    contract = build_fixture_runner_contract(); assert verify_fixture_runner_contract(contract)
    bundle = execute_fixture_shadow(
        contract, preflight, paths, output, prestart_health=prestart,
        active_health_check=lambda _: active, fixture_root=root,
    )
    assert bundle.total_rows == 4
    assert verify_fixture_shadow_bundle(output) == bundle
    run = json.loads((output / "run.json").read_text())
    assert run["fixture_only"] and not run["grants_production_execution_authority"]


def test_cleanup_events_cover_every_successful_query(tmp_path):
    root, paths, output, preflight, prestart, active = _inputs(tmp_path)
    events = []
    execute_fixture_shadow(
        build_fixture_runner_contract(), preflight, paths, output,
        prestart_health=prestart, active_health_check=lambda _: active, fixture_root=root,
        lifecycle_event=lambda query, event: events.append((query, event)),
    )
    for query in {item[0] for item in events}:
        assert [event for item, event in events if item == query][-3:] == [
            "PROGRESS_HANDLER_REMOVED", "ROLLBACK_ATTEMPTED", "CONNECTION_CLOSED",
        ]


def test_health_do_not_start_and_active_stop_publish_nothing(tmp_path):
    root, paths, output, preflight, prestart, active = _inputs(tmp_path)
    stopped = type(prestart)(**{**prestart.__dict__, "status": "DO_NOT_START"})
    with pytest.raises(Exception):
        execute_fixture_shadow(build_fixture_runner_contract(), preflight, paths, output,
            prestart_health=stopped, active_health_check=lambda _: active, fixture_root=root)
    assert not output.exists()
    bad_active = type(active)(**{**active.__dict__, "status": "STOP"})
    with pytest.raises(Exception):
        execute_fixture_shadow(build_fixture_runner_contract(), preflight, paths, output,
            prestart_health=prestart, active_health_check=lambda _: bad_active, fixture_root=root)
    assert not output.exists()


def test_sqlite_exception_cleans_up_and_publishes_nothing(tmp_path):
    root, paths, output, preflight, prestart, active = _inputs(tmp_path)
    db = sqlite3.connect(paths["creator"]); db.execute("DROP TABLE creator_tokens"); db.commit(); db.close()
    events = []
    with pytest.raises(ProductionShadowFixtureRunnerError, match="SQLITE_QUERY_EXCEPTION"):
        execute_fixture_shadow(build_fixture_runner_contract(), preflight, paths, output,
            prestart_health=prestart, active_health_check=lambda _: active, fixture_root=root,
            lifecycle_event=lambda query, event: events.append((query, event)))
    assert not output.exists()
    assert [event for _, event in events][-3:] == ["PROGRESS_HANDLER_REMOVED", "ROLLBACK_ATTEMPTED", "CONNECTION_CLOSED"]


def test_deadline_interrupt_cleans_up_and_publishes_nothing(tmp_path):
    root, paths, output, preflight, prestart, active = _inputs(tmp_path)
    events = []

    class DeadlineClock:
        def __init__(self): self.calls = 0
        def __call__(self):
            self.calls += 1
            return 0.0 if self.calls <= 2 else 10.0

    with pytest.raises(ProductionShadowFixtureRunnerError, match="QUERY_DEADLINE_EXCEEDED"):
        execute_fixture_shadow(
            build_fixture_runner_contract(), preflight, paths, output,
            prestart_health=prestart, active_health_check=lambda _: active,
            fixture_root=root, clock=DeadlineClock(), progress_steps=1,
            lifecycle_event=lambda query, event: events.append((query, event)),
        )
    assert not output.exists()
    assert [event for _, event in events][-3:] == [
        "PROGRESS_HANDLER_REMOVED", "ROLLBACK_ATTEMPTED", "CONNECTION_CLOSED",
    ]


def test_resource_ceiling_and_non_fixture_path_fail_closed(tmp_path):
    root, paths, output, preflight, prestart, active = _inputs(tmp_path)
    with pytest.raises(ProductionShadowFixtureRunnerError, match="MEMORY_CEILING"):
        execute_fixture_shadow(build_fixture_runner_contract(), preflight, paths, output,
            prestart_health=prestart, active_health_check=lambda _: active, fixture_root=root,
            resource_probe=lambda: (129 * 1024 * 1024, 0))
    assert not output.exists()
    with pytest.raises(ProductionShadowFixtureRunnerError, match="NON_FIXTURE_PATH"):
        execute_fixture_shadow(build_fixture_runner_contract(), preflight,
            {**paths, "creator": Path("/tmp/not-in-root.sqlite")}, output,
            prestart_health=prestart, active_health_check=lambda _: active, fixture_root=root)


@pytest.mark.parametrize("mutation", ("missing", "extra", "altered"))
def test_missing_extra_altered_bundle_fail(tmp_path, mutation):
    root, paths, output, preflight, prestart, active = _inputs(tmp_path)
    execute_fixture_shadow(build_fixture_runner_contract(), preflight, paths, output,
        prestart_health=prestart, active_health_check=lambda _: active, fixture_root=root)
    if mutation == "missing":
        (output / "accounting.json").unlink()
        expected = "FILE_SET"
    elif mutation == "extra":
        (output / "extra").write_text("x")
        expected = "FILE_SET"
    else:
        (output / "results.json").write_text("{}\n")
        expected = "DIGEST"
    with pytest.raises(ProductionShadowFixtureRunnerError, match=expected):
        verify_fixture_shadow_bundle(output)


def test_existing_output_rejected_without_query(tmp_path):
    root, paths, output, preflight, prestart, active = _inputs(tmp_path); output.mkdir()
    with pytest.raises(ProductionShadowFixtureRunnerError, match="OUTPUT_NOT_NEW"):
        execute_fixture_shadow(build_fixture_runner_contract(), preflight, paths, output,
            prestart_health=prestart, active_health_check=lambda _: active, fixture_root=root)
