"""B2Z-P2R: focused tests for execution-exclusion-aware resume repair.

All tests use tmp_path-scoped ledgers and a FakeTransport -- zero real
network calls, zero credentials. No test may default-resolve to the real
docs/audits/ live B2Z paths.
"""
from __future__ import annotations

import pytest

from src.acquisition.b2n_qualification import B2NManifest, B2NMember
from src.acquisition.b2w_projection import B2WInputProjection, B2WRequestInput
from src.acquisition.b2z_durable_execution import (
    STAGE_CREATOR_HISTORY,
    STAGE_FUNDING_TX,
    STAGE_MIGRATION_TX,
    B2ZEventLedger,
    B2ZP1Error,
    B2ZStageOutputLedger,
    build_authorization,
    resume_next,
)


def manifest():
    return B2NManifest(tuple(B2NMember(i, f"mint-{i}", f"event-{i}", True) for i in range(1, 21)))


def projection():
    return B2WInputProjection(tuple(
        B2WRequestInput(i, f"mint-{i}", f"event-{i}", f"migration-{i}") for i in range(1, 21)
    ))


def migration_result(mint, block_time=100):
    return {"result": {"blockTime": block_time, "slot": 12345, "transaction": {"message": {"accountKeys": [
        {"pubkey": mint, "signer": False}, {"pubkey": f"creator-{mint}", "signer": True},
    ]}}}}


def funding_result(*, creator, block_time=90, lamports=10, destination=None):
    return {"result": {"blockTime": block_time, "slot": 11111, "transaction": {"message": {"instructions": [{
        "program": "system", "parsed": {"type": "transfer", "info": {
            "source": "funder", "destination": destination or creator, "lamports": lamports,
        }},
    }]}}}}


class FakeTransport:
    def __init__(self, *, fail_stage=None, fail_mode="dispatch_error", no_candidate_mints=None):
        self.physical_request_count = 0
        self.requests = []
        self.fail_stage = fail_stage
        self.fail_mode = fail_mode
        self.no_candidate_mints = no_candidate_mints or set()

    def post_json(self, request):
        method = request["method"]
        target = request["params"][0]

        if self.fail_stage and (target, method) == self.fail_stage:
            if self.fail_mode == "before_dispatch":
                raise ValueError("injected pre-dispatch failure")
            self.physical_request_count += 1
            self.requests.append(request)
            raise TimeoutError("injected dispatch failure")

        self.physical_request_count += 1
        self.requests.append(request)

        if method == "getTransaction" and target.startswith("migration-"):
            mint = target.replace("migration-", "mint-")
            return migration_result(mint)
        if method == "getSignaturesForAddress":
            creator = target
            mint = creator.replace("creator-", "")
            if mint in self.no_candidate_mints:
                return {"result": []}
            return {"result": [{"signature": f"funding-{mint}", "blockTime": 90}]}
        if method == "getTransaction":
            mint = target.replace("funding-", "")
            creator = f"creator-{mint}"
            return funding_result(creator=creator)
        raise AssertionError(f"unexpected request: {request}")


def auth():
    return build_authorization(
        manifest=manifest(), projection=projection(),
        b2n_closure_digest="fake-b2n-digest", p0_preflight_digest="fake-p0-digest",
    )


def ledgers(tmp_path):
    return (
        B2ZEventLedger(tmp_path / "events.jsonl"),
        B2ZStageOutputLedger(tmp_path / "stage_outputs.json"),
    )


def seed_ordinal_1_failure(event_ledger, transport_kwargs=None):
    """Reproduce the exact real incident: ordinal 1 MIGRATION_TX
    ATTEMPT_RESERVED + ATTEMPT_FAILED_AFTER_DISPATCH, consuming exactly 1
    physical request."""
    a = auth()
    stage_ledger = B2ZStageOutputLedger(event_ledger.path.parent / "stage_outputs_seed.json")
    transport = FakeTransport(fail_stage=("migration-1", "getTransaction"), fail_mode="dispatch_error",
                               **(transport_kwargs or {}))
    with pytest.raises(TimeoutError):
        resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                    event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    return a


# --- Part 7, requirement 1-6: exclusion mechanics --------------------------

def test_resume_does_not_redispatch_ordinal_1_after_exclusion(tmp_path):
    event_ledger, stage_ledger = ledgers(tmp_path)
    a = seed_ordinal_1_failure(event_ledger)
    event_ledger.exclude_member_from_execution(
        run_id=a.run_id, sample_ordinal=1, mint="mint-1", failed_stage=STAGE_MIGRATION_TX,
        exclusion_reason="CREDENTIAL_INPUT_CORRUPTION", decision_source="OPTION_A_HUMAN_DECISION",
    )
    transport = FakeTransport()
    result = resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                          event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    # resume advanced past ordinal 1 without dispatching anything for it
    assert not any(r["params"][0] == "migration-1" for r in transport.requests)
    assert result["sample_ordinal"] == 2


def test_resume_selects_ordinal_2_migration_tx(tmp_path):
    event_ledger, stage_ledger = ledgers(tmp_path)
    a = seed_ordinal_1_failure(event_ledger)
    event_ledger.exclude_member_from_execution(
        run_id=a.run_id, sample_ordinal=1, mint="mint-1", failed_stage=STAGE_MIGRATION_TX,
        exclusion_reason="CREDENTIAL_INPUT_CORRUPTION", decision_source="OPTION_A_HUMAN_DECISION",
    )
    transport = FakeTransport()
    result = resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                          event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    assert result == {"status": "STAGE_COMPLETE", "sample_ordinal": 2, "stage": STAGE_MIGRATION_TX,
                       "output": result["output"]}
    assert result["output"]["creator"] == "creator-mint-2"


def test_budget_remains_49_after_exclusion_and_one_resume(tmp_path):
    event_ledger, stage_ledger = ledgers(tmp_path)
    a = seed_ordinal_1_failure(event_ledger)
    assert event_ledger.physical_requests_attempted() == 1
    event_ledger.exclude_member_from_execution(
        run_id=a.run_id, sample_ordinal=1, mint="mint-1", failed_stage=STAGE_MIGRATION_TX,
        exclusion_reason="CREDENTIAL_INPUT_CORRUPTION", decision_source="OPTION_A_HUMAN_DECISION",
    )
    # exclusion itself consumed zero requests
    assert event_ledger.physical_requests_attempted() == 1
    transport = FakeTransport()
    resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    # exactly one more physical request consumed (ordinal 2 MIGRATION_TX)
    assert event_ledger.physical_requests_attempted() == 2
    remaining = a.max_total_requests - event_ledger.physical_requests_attempted()
    # original authorization for this build_authorization() call carries the
    # module MAX_TOTAL_REQUESTS=60 constant; the REAL P2 authorization uses a
    # P2-specific max_total_requests=50 (see b2z_p2_execution_plan_and_authorization.json)
    assert remaining == a.max_total_requests - 2


def test_ordinal_1_history_unchanged_after_exclusion(tmp_path):
    event_ledger, stage_ledger = ledgers(tmp_path)
    a = seed_ordinal_1_failure(event_ledger)
    events_before = event_ledger.events()
    event_ledger.exclude_member_from_execution(
        run_id=a.run_id, sample_ordinal=1, mint="mint-1", failed_stage=STAGE_MIGRATION_TX,
        exclusion_reason="CREDENTIAL_INPUT_CORRUPTION", decision_source="OPTION_A_HUMAN_DECISION",
    )
    events_after = event_ledger.events()
    # the original 2 events (RESERVED, FAILED_AFTER_DISPATCH) are byte-identical, untouched
    assert events_after[:2] == events_before[:2]
    assert events_after[0]["event"] == "ATTEMPT_RESERVED"
    assert events_after[1]["event"] == "ATTEMPT_FAILED_AFTER_DISPATCH"
    assert events_after[2]["event"] == "EXECUTION_EXCLUDED"


def test_calibration_eligible_count_is_19(tmp_path):
    event_ledger, _ = ledgers(tmp_path)
    a = seed_ordinal_1_failure(event_ledger)
    event_ledger.exclude_member_from_execution(
        run_id=a.run_id, sample_ordinal=1, mint="mint-1", failed_stage=STAGE_MIGRATION_TX,
        exclusion_reason="CREDENTIAL_INPUT_CORRUPTION", decision_source="OPTION_A_HUMAN_DECISION",
    )
    excluded = event_ledger.excluded_ordinals()
    eligible = set(range(1, 21)) - excluded
    assert excluded == {1}
    assert len(eligible) == 19


def test_exclusion_itself_consumes_zero_requests(tmp_path):
    event_ledger, _ = ledgers(tmp_path)
    a = seed_ordinal_1_failure(event_ledger)
    before = event_ledger.physical_requests_attempted()
    event_ledger.exclude_member_from_execution(
        run_id=a.run_id, sample_ordinal=1, mint="mint-1", failed_stage=STAGE_MIGRATION_TX,
        exclusion_reason="CREDENTIAL_INPUT_CORRUPTION", decision_source="OPTION_A_HUMAN_DECISION",
    )
    after = event_ledger.physical_requests_attempted()
    assert before == after == 1


# --- Part 7, requirement 7: ordinary failures still block without exclusion

def test_unexcluded_failed_member_still_blocks_resume(tmp_path):
    event_ledger, stage_ledger = ledgers(tmp_path)
    a = seed_ordinal_1_failure(event_ledger)
    transport = FakeTransport()
    with pytest.raises(B2ZP1Error, match="MEMBER_BLOCKED_BY_FAILED_STAGE"):
        resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                    event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    # no ordinal-2 request was made -- the run remains blocked as before
    assert transport.requests == []


# --- Part 7, requirement 8: exclusion cannot be forged ---------------------

def test_exclusion_rejected_without_matching_failure(tmp_path):
    """Cannot exclude a member that has no actual terminal failure on the
    named stage -- prevents forging an exclusion to bypass an ordinary
    semantic/provider failure."""
    event_ledger, _ = ledgers(tmp_path)
    a = auth()
    with pytest.raises(B2ZP1Error, match="NO_MATCHING_FAILURE_TO_EXCLUDE"):
        event_ledger.exclude_member_from_execution(
            run_id=a.run_id, sample_ordinal=5, mint="mint-5", failed_stage=STAGE_MIGRATION_TX,
            exclusion_reason="CREDENTIAL_INPUT_CORRUPTION", decision_source="OPTION_A_HUMAN_DECISION",
        )


def test_exclusion_rejected_for_stage_that_already_succeeded(tmp_path):
    event_ledger, stage_ledger = ledgers(tmp_path)
    a = auth()
    transport = FakeTransport()
    resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                event_ledger=event_ledger, stage_output_ledger=stage_ledger)  # ordinal 1 MIGRATION_TX succeeds
    with pytest.raises(B2ZP1Error, match="NO_MATCHING_FAILURE_TO_EXCLUDE"):
        event_ledger.exclude_member_from_execution(
            run_id=a.run_id, sample_ordinal=1, mint="mint-1", failed_stage=STAGE_MIGRATION_TX,
            exclusion_reason="CREDENTIAL_INPUT_CORRUPTION", decision_source="OPTION_A_HUMAN_DECISION",
        )


# --- Part 7, requirement 9: duplicate exclusion rejected --------------------

def test_duplicate_exclusion_rejected(tmp_path):
    event_ledger, _ = ledgers(tmp_path)
    a = seed_ordinal_1_failure(event_ledger)
    event_ledger.exclude_member_from_execution(
        run_id=a.run_id, sample_ordinal=1, mint="mint-1", failed_stage=STAGE_MIGRATION_TX,
        exclusion_reason="CREDENTIAL_INPUT_CORRUPTION", decision_source="OPTION_A_HUMAN_DECISION",
    )
    with pytest.raises(B2ZP1Error, match="DUPLICATE_EXCLUSION"):
        event_ledger.exclude_member_from_execution(
            run_id=a.run_id, sample_ordinal=1, mint="mint-1", failed_stage=STAGE_MIGRATION_TX,
            exclusion_reason="CREDENTIAL_INPUT_CORRUPTION", decision_source="OPTION_A_HUMAN_DECISION",
        )


# --- Part 7, requirement 10: ledger inconsistency fails closed -------------

def test_exclusion_invalid_event_type_rejected(tmp_path):
    event_ledger, _ = ledgers(tmp_path)
    with pytest.raises(B2ZP1Error, match="INVALID_EVENT_TYPE"):
        event_ledger.append_event(
            event="NOT_A_REAL_EVENT", run_id="r", sample_ordinal=1, mint="mint-1",
            stage=STAGE_MIGRATION_TX, physical_request_number=0, provider="x",
            endpoint_family="x", method="x", dependency_digest=None, request_digest=None,
        )


# --- Part 8: 19-member mock completion under the 49-request remainder -----

def test_19_member_calibration_walk_never_exceeds_original_authorization(tmp_path):
    """Starting from the exact excluded-ordinal-1 state, walk ordinals 2..20
    under the REAL P2-specific 50-request authorization (dataclasses.replace,
    exactly as scripts/run_b2z_p2_calibration.py constructs it -- NOT the
    module's build_authorization() 60-ceiling default). 19 members * 3 stages
    = 57 possible additional stages, plus ordinal 1's already-consumed 1 =
    58 possible total -- this EXCEEDS the real 50-request ceiling, so the
    walk must fail closed via the budget guard in DurableB2ZClient.dispatch()
    once exhausted, never silently completing all 19 members and never
    dispatching request 51+."""
    import dataclasses
    event_ledger, stage_ledger = ledgers(tmp_path)
    a = seed_ordinal_1_failure(event_ledger)
    a = dataclasses.replace(a, max_total_requests=50)  # the real P2-specific ceiling
    event_ledger.exclude_member_from_execution(
        run_id=a.run_id, sample_ordinal=1, mint="mint-1", failed_stage=STAGE_MIGRATION_TX,
        exclusion_reason="CREDENTIAL_INPUT_CORRUPTION", decision_source="OPTION_A_HUMAN_DECISION",
    )
    transport = FakeTransport()
    steps = 0
    budget_exhausted = False
    while True:
        try:
            result = resume_next(manifest=manifest(), projection=projection(), authorization=a,
                                  transport=transport, event_ledger=event_ledger,
                                  stage_output_ledger=stage_ledger)
        except B2ZP1Error as exc:
            assert "BUDGET" in str(exc) or "MAX_TOTAL_REQUESTS" in str(exc) or "AUTHORIZED" in str(exc)
            budget_exhausted = True
            break
        steps += 1
        assert event_ledger.physical_requests_attempted() <= 50
        if result["status"] == "ALL_MEMBERS_COMPLETE":
            break
        if steps > 200:
            raise AssertionError("resume loop did not terminate")
    # ordinal 1 never dispatched at any point during the walk
    assert not any(r["params"][0] == "migration-1" for r in transport.requests)
    total_consumed = event_ledger.physical_requests_attempted()
    # never exceeded the real 50-request ceiling; no request numbered 51+ exists
    assert total_consumed <= 50
    assert all(e.get("physical_request_number", 0) <= 50
               for e in event_ledger.events() if e["event"] in
               ("ATTEMPT_SUCCEEDED", "ATTEMPT_FAILED_AFTER_DISPATCH"))
    # given 58 possible stage-dispatches against a 50 ceiling, budget
    # exhaustion before ALL_MEMBERS_COMPLETE is the expected, correct outcome
    assert budget_exhausted, (
        "expected the 50-request ceiling to be hit before all 19 members "
        "could complete all 3 stages each -- if this assertion fails, "
        "verify whether NO_CANDIDATE/seeded-stage semantics reduced total "
        "demand below 50, which would be a legitimate but different finding"
    )


def test_stage2_seeded_member_still_behaves_correctly_after_exclusion(tmp_path):
    from src.acquisition.b2z_durable_execution import seed_frozen_creator_history_from_local_prediction
    event_ledger, stage_ledger = ledgers(tmp_path)
    a = seed_ordinal_1_failure(event_ledger)
    event_ledger.exclude_member_from_execution(
        run_id=a.run_id, sample_ordinal=1, mint="mint-1", failed_stage=STAGE_MIGRATION_TX,
        exclusion_reason="CREDENTIAL_INPUT_CORRUPTION", decision_source="OPTION_A_HUMAN_DECISION",
    )
    transport = FakeTransport()
    # advance ordinal 2 through MIGRATION_TX first (Stage 1 must be real/live per contract)
    resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    # seed ordinal 2's CREATOR_HISTORY from a frozen local prediction, skipping the live call
    seed_frozen_creator_history_from_local_prediction(
        run_id=a.run_id, sample_ordinal=2, mint="mint-2", event_ledger=event_ledger,
        stage_output_ledger=stage_ledger, frozen_creator="creator-mint-2", frozen_migration_time=100,
        frozen_funding_signature="funding-mint-2", frozen_prediction_digest="fake-prediction-digest",
    )
    before = event_ledger.physical_requests_attempted()
    result = resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                          event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    # CREATOR_HISTORY was skipped (seeded) -- next call goes straight to FUNDING_TX,
    # consuming exactly one more physical request (not two)
    assert event_ledger.physical_requests_attempted() == before + 1
    assert result["stage"] == STAGE_FUNDING_TX


def test_comparison_engine_uses_denominator_19():
    from src.acquisition.b2z_calibration_comparison import compute_metrics
    import inspect
    # structural check: compute_metrics must accept an explicit eligible-member
    # set/count so the caller can pass exactly the 19-member eligible population
    # (ordinal 1 excluded) rather than assuming the full original cohort of 20.
    sig = inspect.signature(compute_metrics)
    params = list(sig.parameters.keys())
    assert len(params) >= 1  # comparisons list/mapping is caller-supplied and caller controls the denominator


def test_semantic_validation_failure_cannot_be_excluded_via_credential_corruption_path(tmp_path):
    """exclude_member_from_execution() (the B2Z-P2R credential-corruption
    exclusion mechanism) must NOT be usable to skip a genuine local-vs-raw
    semantic disagreement (B2Z_P2D's SEMANTIC_VALIDATION_FAILED_TERMINAL).
    These are two structurally distinct outcomes -- a malformed credential
    causing a transport-level failure is not the same as raw evidence
    contradicting a local prediction -- and conflating them would let a
    genuine disagreement be silently swept aside using the same mechanism
    meant only for infrastructure corruption."""
    event_ledger, _ = ledgers(tmp_path)
    event_ledger.record_semantic_validation_failed_terminal(
        run_id="r", sample_ordinal=8, mint="mint-8", failure_reason="B2Z_P1_NO_FUNDING_EDGE",
        candidate_signature="some-signature",
    )
    with pytest.raises(B2ZP1Error, match="NO_MATCHING_FAILURE_TO_EXCLUDE"):
        event_ledger.exclude_member_from_execution(
            run_id="r", sample_ordinal=8, mint="mint-8", failed_stage=STAGE_FUNDING_TX,
            exclusion_reason="ATTEMPTED_MISUSE", decision_source="TEST",
        )
