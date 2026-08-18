import json
from pathlib import Path
from unittest import mock

import pytest

import scripts.run_b2n_p3c_migration_lineage_run as p3c
from src.acquisition.b2n_qualification import B2NAttemptLedger, CONTRACT_VERSION

ROOT = Path(__file__).parents[1]


class FakeSuccessTransport:
    def __init__(self):
        self.physical_request_count = 0

    def __call__(self, endpoint):
        return self

    def post_json(self, request):
        self.physical_request_count += 1
        sig = request["params"][0]
        manifest = p3c._load_frozen_manifest()
        reviewed = p3c._load_reviewed_binding()
        proj = p3c.build_projection(manifest, reviewed)
        member = next(m for m in proj.members if m.migration_signature == sig)
        return {"result": {"slot": 1, "transaction": {"message": {"accountKeys": [{"pubkey": member.mint}]}}}}


def _seed_ordinal_1_complete(ledger_path: Path, event_ledger_path: Path):
    manifest = p3c._load_frozen_manifest()
    member1 = manifest.members[0]
    durable_ledger = B2NAttemptLedger(ledger_path)
    durable_ledger.append({
        "contract_version": CONTRACT_VERSION, "run_id": p3c.EXPECTED_RUN_ID,
        "manifest_digest": manifest.digest(), "sample_ordinal": 1, "mint": member1.mint,
        "observation_required": True, "provider": "helius", "request_count": 1,
        "request_outcome": "SUCCESS", "request_started_utc_ns": 1, "response_received_utc_ns": 2,
        "elapsed_monotonic_ns": 1, "evidence_observed": False, "provenance_complete": True,
        "error_class": "B2W_MIGRATION_LINEAGE_ONLY",
    })
    event_ledger = p3c.PreDispatchEventLedger(event_ledger_path)
    event_ledger.append_event(event="ATTEMPT_RESERVED", run_id=p3c.EXPECTED_RUN_ID, sample_ordinal=1,
                               mint=member1.mint, provider="helius", method="getTransaction", request_number=1)
    event_ledger.append_event(event="ATTEMPT_SUCCEEDED", run_id=p3c.EXPECTED_RUN_ID, sample_ordinal=1,
                               mint=member1.mint, provider="helius", method="getTransaction", request_number=1,
                               request_outcome="SUCCESS")


@pytest.fixture(autouse=True)
def _credential(monkeypatch):
    monkeypatch.setenv("B2N_P3C_HELIUS_ENDPOINT", "https://mainnet.helius-rpc.com/?api-key=FAKE")


# --- Part 2/reconciliation artifact ------------------------------------------

def test_reconciliation_artifact_records_exact_partial_state():
    d = json.loads((ROOT / "docs/audits/b2n_p3g_partial_live_run_reconciliation.json").read_text())
    assert d["run_id"] == "b2n-p3e-98695fbdf44146c93ff08c8c"
    assert d["completed_ordinals"] == [1]
    assert d["remaining_count"] == 19
    assert d["physical_requests_consumed"] == 1
    assert d["part2_ledger_reconciliation"]["exactly_one_reservation"] is True
    assert d["part2_ledger_reconciliation"]["exactly_one_terminal_event"] is True
    assert d["part2_ledger_reconciliation"]["exactly_one_attempt_ledger_entry"] is True
    assert d["part2_ledger_reconciliation"]["no_events_or_attempts_for_ordinals_2_through_20"] is True


def test_diagnosis_confirms_intentional_not_a_bug():
    d = json.loads((ROOT / "docs/audits/b2n_p3g_partial_live_run_reconciliation.json").read_text())
    assert d["part3_diagnosis"]["verdict"] == "INTENTIONAL_ONE_MEMBER_PER_INVOCATION_BEHAVIOR_NOT_A_BUG"
    assert d["part4_fix_decision"]["chosen_approach"] == "SAFE_CONTINUATION_NOT_EXECUTOR_MODIFICATION"


# --- Part 3: exact cause, reproduced with mocks -------------------------------

def test_b2n_executor_stops_after_ordinal_1_even_with_always_successful_transport(tmp_path):
    """Reproduces the exact diagnosis: even if EVERY member would succeed,
    B2NExecutor.run() still only processes ordinal 1, because
    evidence_observed is permanently False for MigrationGetTransactionAdapter."""
    from src.acquisition.b2n_qualification import (
        AppendOnlyLedger, B2NExecutor, B2NQualificationRunAuthorization,
    )
    from src.acquisition.b2w_projection import MigrationGetTransactionAdapter

    manifest = p3c._load_frozen_manifest()
    reviewed = p3c._load_reviewed_binding()
    projection = p3c.build_projection(manifest, reviewed)
    transport = FakeSuccessTransport()
    adapter = MigrationGetTransactionAdapter(transport, projection)
    ledger_path = tmp_path / "l.jsonl"
    event_ledger = p3c.PreDispatchEventLedger(tmp_path / "e.jsonl")
    client = p3c.DurableAccountingClient(
        adapter=adapter, transport=transport, event_ledger=event_ledger,
        run_id="test", provider="helius", method="getTransaction",
    )
    auth = B2NQualificationRunAuthorization(
        provider="helius", endpoint_family="helius-mainnet-json-rpc", run_id="test",
        manifest_digest=manifest.digest(), ledger_path=str(ledger_path),
    )
    executor = B2NExecutor(manifest=manifest, ledger=AppendOnlyLedger(ledger_path), client=client,
                            provider="helius", run_id="test", authorization=auth)
    results = executor.run()
    assert len(results) == 1
    assert transport.physical_request_count == 1


# --- Part 5/6: safe continuation contract -------------------------------------

def test_ordinal_1_cannot_be_redispatched(tmp_path):
    ledger_path = tmp_path / "ledger.jsonl"
    event_ledger_path = tmp_path / "events.jsonl"
    _seed_ordinal_1_complete(ledger_path, event_ledger_path)

    with mock.patch.object(p3c, "RedactingJsonRpcTransport", FakeSuccessTransport()):
        result = p3c.resume_run(ledger_path=ledger_path, event_ledger_path=event_ledger_path)

    assert result["processed_ordinal"] == 2
    assert 1 not in [result["processed_ordinal"]]
    entries = B2NAttemptLedger(ledger_path).entries()
    assert len(entries) == 2
    assert {e["sample_ordinal"] for e in entries} == {1, 2}


def test_remaining_set_is_19_after_seeding_ordinal_1(tmp_path):
    ledger_path = tmp_path / "ledger.jsonl"
    event_ledger_path = tmp_path / "events.jsonl"
    _seed_ordinal_1_complete(ledger_path, event_ledger_path)
    durable_ledger = B2NAttemptLedger(ledger_path)
    event_ledger = p3c.PreDispatchEventLedger(event_ledger_path)
    completed = p3c._completed_ordinals_from_ledgers(durable_ledger, event_ledger)
    assert completed == {1}
    manifest = p3c._load_frozen_manifest()
    remaining = [m.sample_ordinal for m in manifest.members if m.sample_ordinal not in completed]
    assert len(remaining) == 19


def test_cumulative_budget_reflects_prior_consumption(tmp_path):
    ledger_path = tmp_path / "ledger.jsonl"
    event_ledger_path = tmp_path / "events.jsonl"
    _seed_ordinal_1_complete(ledger_path, event_ledger_path)

    with mock.patch.object(p3c, "RedactingJsonRpcTransport", FakeSuccessTransport()):
        result = p3c.resume_run(ledger_path=ledger_path, event_ledger_path=event_ledger_path)

    assert result["physical_requests_consumed"] == 2
    assert result["physical_requests_remaining_budget"] == 18


def test_full_resume_sequence_processes_all_19_remaining_exactly_once(tmp_path):
    ledger_path = tmp_path / "ledger.jsonl"
    event_ledger_path = tmp_path / "events.jsonl"
    _seed_ordinal_1_complete(ledger_path, event_ledger_path)

    with mock.patch.object(p3c, "RedactingJsonRpcTransport", FakeSuccessTransport()):
        results = []
        for _ in range(19):
            r = p3c.resume_run(ledger_path=ledger_path, event_ledger_path=event_ledger_path)
            results.append(r)

    assert results[-1]["status"] == "ALL_MEMBERS_COMPLETE"
    entries = B2NAttemptLedger(ledger_path).entries()
    assert len(entries) == 20
    assert {e["sample_ordinal"] for e in entries} == set(range(1, 21))
    event_ledger = p3c.PreDispatchEventLedger(event_ledger_path)
    assert event_ledger.physical_requests_attempted() == 20

    # calling resume_run again after completion must not attempt anything further
    final = p3c.resume_run(ledger_path=ledger_path, event_ledger_path=event_ledger_path)
    assert final["status"] == "ALL_MEMBERS_COMPLETE"
    assert final["physical_requests_consumed"] == 20


def test_interruption_after_partial_subset_then_deterministic_continuation(tmp_path):
    """Process ordinals 2-6 (5 steps), simulate an interruption (just stop
    calling resume_run), then verify a fresh resume_run call picks up exactly
    at ordinal 7 with no gaps or repeats."""
    ledger_path = tmp_path / "ledger.jsonl"
    event_ledger_path = tmp_path / "events.jsonl"
    _seed_ordinal_1_complete(ledger_path, event_ledger_path)

    with mock.patch.object(p3c, "RedactingJsonRpcTransport", FakeSuccessTransport()):
        for _ in range(5):  # ordinals 2,3,4,5,6
            p3c.resume_run(ledger_path=ledger_path, event_ledger_path=event_ledger_path)

        entries_mid = B2NAttemptLedger(ledger_path).entries()
        assert {e["sample_ordinal"] for e in entries_mid} == {1, 2, 3, 4, 5, 6}

        # "interruption" -- simply a new call, as if the process had been killed and restarted
        result = p3c.resume_run(ledger_path=ledger_path, event_ledger_path=event_ledger_path)
        assert result["processed_ordinal"] == 7

    entries_final = B2NAttemptLedger(ledger_path).entries()
    assert {e["sample_ordinal"] for e in entries_final} == {1, 2, 3, 4, 5, 6, 7}
    assert len(entries_final) == 7  # no duplicates, no gaps


def test_duplicate_ordinal_rejected_even_via_direct_client_call(tmp_path):
    ledger_path = tmp_path / "ledger.jsonl"
    event_ledger_path = tmp_path / "events.jsonl"
    _seed_ordinal_1_complete(ledger_path, event_ledger_path)

    manifest = p3c._load_frozen_manifest()
    reviewed = p3c._load_reviewed_binding()
    projection = p3c.build_projection(manifest, reviewed)
    transport = FakeSuccessTransport()
    from src.acquisition.b2w_projection import MigrationGetTransactionAdapter
    adapter = MigrationGetTransactionAdapter(transport, projection)
    event_ledger = p3c.PreDispatchEventLedger(event_ledger_path)
    client = p3c.DurableAccountingClient(
        adapter=adapter, transport=transport, event_ledger=event_ledger,
        run_id=p3c.EXPECTED_RUN_ID, provider="helius", method="getTransaction",
    )
    member1 = manifest.members[0]
    with pytest.raises(RuntimeError, match="DUPLICATE_ORDINAL_ATTEMPT"):
        client.acquire_once(mint=member1.mint)


def test_dangling_reservation_without_terminal_event_fails_closed(tmp_path):
    """An ATTEMPT_RESERVED with no corresponding terminal event (simulating a
    crash mid-dispatch) must not be silently treated as complete or safe."""
    ledger_path = tmp_path / "ledger.jsonl"
    event_ledger_path = tmp_path / "events.jsonl"
    manifest = p3c._load_frozen_manifest()
    member1 = manifest.members[0]
    event_ledger = p3c.PreDispatchEventLedger(event_ledger_path)
    event_ledger.append_event(event="ATTEMPT_RESERVED", run_id=p3c.EXPECTED_RUN_ID, sample_ordinal=1,
                               mint=member1.mint, provider="helius", method="getTransaction", request_number=1)
    # no terminal event written -- simulates a crash between reservation and dispatch completion

    durable_ledger = B2NAttemptLedger(ledger_path)
    with pytest.raises(p3c.B2NP3CError, match="DANGLING_RESERVATION"):
        p3c._completed_ordinals_from_ledgers(durable_ledger, event_ledger)


def test_ledger_event_ledger_mismatch_fails_closed(tmp_path):
    """If the attempt ledger and event ledger disagree about which ordinals
    dispatched, resume must fail closed rather than guess."""
    ledger_path = tmp_path / "ledger.jsonl"
    event_ledger_path = tmp_path / "events.jsonl"
    manifest = p3c._load_frozen_manifest()
    member1 = manifest.members[0]

    # event ledger says ordinal 1 dispatched and succeeded...
    event_ledger = p3c.PreDispatchEventLedger(event_ledger_path)
    event_ledger.append_event(event="ATTEMPT_RESERVED", run_id=p3c.EXPECTED_RUN_ID, sample_ordinal=1,
                               mint=member1.mint, provider="helius", method="getTransaction", request_number=1)
    event_ledger.append_event(event="ATTEMPT_SUCCEEDED", run_id=p3c.EXPECTED_RUN_ID, sample_ordinal=1,
                               mint=member1.mint, provider="helius", method="getTransaction", request_number=1,
                               request_outcome="SUCCESS")
    # ...but the attempt ledger has NO entry for it (simulating a crash after
    # the terminal event was written but before durable_ledger.append())
    durable_ledger = B2NAttemptLedger(ledger_path)

    with pytest.raises(p3c.B2NP3CError, match="LEDGER_EVENT_LEDGER_MISMATCH"):
        p3c._completed_ordinals_from_ledgers(durable_ledger, event_ledger)


def test_resume_reports_all_members_complete_when_no_remaining(tmp_path):
    ledger_path = tmp_path / "ledger.jsonl"
    event_ledger_path = tmp_path / "events.jsonl"
    manifest = p3c._load_frozen_manifest()
    durable_ledger = B2NAttemptLedger(ledger_path)
    event_ledger = p3c.PreDispatchEventLedger(event_ledger_path)
    for m in manifest.members:
        durable_ledger.append({
            "contract_version": CONTRACT_VERSION, "run_id": p3c.EXPECTED_RUN_ID,
            "manifest_digest": manifest.digest(), "sample_ordinal": m.sample_ordinal, "mint": m.mint,
            "observation_required": True, "provider": "helius", "request_count": 1,
            "request_outcome": "SUCCESS", "request_started_utc_ns": 1, "response_received_utc_ns": 2,
            "elapsed_monotonic_ns": 1, "evidence_observed": False, "provenance_complete": True,
            "error_class": "B2W_MIGRATION_LINEAGE_ONLY",
        })
        event_ledger.append_event(event="ATTEMPT_RESERVED", run_id=p3c.EXPECTED_RUN_ID,
                                   sample_ordinal=m.sample_ordinal, mint=m.mint, provider="helius",
                                   method="getTransaction", request_number=m.sample_ordinal)
        event_ledger.append_event(event="ATTEMPT_SUCCEEDED", run_id=p3c.EXPECTED_RUN_ID,
                                   sample_ordinal=m.sample_ordinal, mint=m.mint, provider="helius",
                                   method="getTransaction", request_number=m.sample_ordinal,
                                   request_outcome="SUCCESS")

    result = p3c.resume_run(ledger_path=ledger_path, event_ledger_path=event_ledger_path)
    assert result["status"] == "ALL_MEMBERS_COMPLETE"
    assert result["physical_requests_consumed"] == 20
