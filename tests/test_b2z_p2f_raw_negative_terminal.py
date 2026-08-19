"""B2Z-P2F: focused tests for the raw-verification-negative terminal
calibration classification.

All tests use tmp_path-scoped ledgers and a FakeTransport -- zero real
network calls, zero credentials.
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
    def __init__(self, *, bad_funding_mints=None, no_candidate_mints=None):
        self.physical_request_count = 0
        self.requests = []
        self.bad_funding_mints = bad_funding_mints or set()
        self.no_candidate_mints = no_candidate_mints or set()

    def post_json(self, request):
        method = request["method"]
        target = request["params"][0]
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
            if mint in self.bad_funding_mints:
                return funding_result(creator=creator, destination="someone-else")
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


def seed_ordinal_8_semantic_failure(event_ledger, stage_ledger):
    """Reproduce the real incident shape for ordinal 8: MIGRATION_TX and
    CREATOR_HISTORY succeed normally, FUNDING_TX dispatches successfully at
    the transport level but fails proves_inbound_sol_funding()."""
    a = auth()
    transport = FakeTransport(bad_funding_mints={"mint-8"})
    # walk ordinals 1-7 through MIGRATION_TX + CREATOR_HISTORY + FUNDING_TX (all clean)
    for _ in range(7 * 3):
        resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                    event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    # ordinal 8 MIGRATION_TX + CREATOR_HISTORY
    resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    with pytest.raises(B2ZP1Error, match="NO_FUNDING_EDGE"):
        resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                    event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    return a, transport


# 1-4: raw dispatch, semantic failure, physical request counted, terminal recorded

def test_raw_stage3_dispatch_succeeds_then_semantic_validation_fails(tmp_path):
    event_ledger, stage_ledger = ledgers(tmp_path)
    a, transport = seed_ordinal_8_semantic_failure(event_ledger, stage_ledger)
    events = [e for e in event_ledger.events() if e["sample_ordinal"] == 8 and e["stage"] == STAGE_FUNDING_TX]
    assert events[0]["event"] == "ATTEMPT_RESERVED"
    assert events[1]["event"] == "ATTEMPT_SUCCEEDED"
    assert events[2]["event"] == "SEMANTIC_VALIDATION_FAILED_TERMINAL"
    assert 8 in event_ledger.semantic_validation_failed_ordinals()


def test_physical_request_counted_exactly_once(tmp_path):
    event_ledger, stage_ledger = ledgers(tmp_path)
    a, transport = seed_ordinal_8_semantic_failure(event_ledger, stage_ledger)
    funding_tx_dispatches = [r for r in transport.requests if r["method"] == "getTransaction"
                              and r["params"][0] == "funding-mint-8"]
    assert len(funding_tx_dispatches) == 1


# 5-6: human-authorized raw-negative terminal, zero requests consumed

def test_raw_negative_terminal_classification_consumes_zero_requests(tmp_path):
    event_ledger, stage_ledger = ledgers(tmp_path)
    a, transport = seed_ordinal_8_semantic_failure(event_ledger, stage_ledger)
    before = event_ledger.physical_requests_attempted()
    event_ledger.classify_raw_verification_negative_terminal(
        run_id=a.run_id, sample_ordinal=8, mint="mint-8", prediction_digest="fake-pred-digest",
        failure_reason="B2Z_P1_NO_FUNDING_EDGE", review_sensitive=True,
        decision_source="HUMAN_REVIEW_KEEP_IN_CALIBRATION",
    )
    after = event_ledger.physical_requests_attempted()
    assert before == after
    assert 8 in event_ledger.raw_negative_terminal_ordinals()


# 7: member becomes terminal for sequencing

def test_member_terminal_for_sequencing_after_classification(tmp_path):
    event_ledger, stage_ledger = ledgers(tmp_path)
    a, transport = seed_ordinal_8_semantic_failure(event_ledger, stage_ledger)
    event_ledger.classify_raw_verification_negative_terminal(
        run_id=a.run_id, sample_ordinal=8, mint="mint-8", prediction_digest="fake-pred-digest",
        failure_reason="B2Z_P1_NO_FUNDING_EDGE", review_sensitive=True,
        decision_source="HUMAN_REVIEW_KEEP_IN_CALIBRATION",
    )
    result = resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                          event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    assert result["sample_ordinal"] == 9
    assert result["stage"] == STAGE_MIGRATION_TX


# 8-9: member remains in calibration denominator, not converted to success

def test_member_remains_negative_not_success(tmp_path):
    event_ledger, stage_ledger = ledgers(tmp_path)
    a, transport = seed_ordinal_8_semantic_failure(event_ledger, stage_ledger)
    event_ledger.classify_raw_verification_negative_terminal(
        run_id=a.run_id, sample_ordinal=8, mint="mint-8", prediction_digest="fake-pred-digest",
        failure_reason="B2Z_P1_NO_FUNDING_EDGE", review_sensitive=True,
        decision_source="HUMAN_REVIEW_KEEP_IN_CALIBRATION",
    )
    output = stage_ledger.get_stage_output(sample_ordinal=8, stage=STAGE_FUNDING_TX)
    assert output["terminal_status"] == "SEMANTIC_VALIDATION_FAILED"
    assert output["evidence_observed"] is False
    # the classification event itself explicitly marks calibration treatment as INCLUDED, not a success
    events = [e for e in event_ledger.events() if e["event"] == "RAW_VERIFICATION_NEGATIVE_TERMINAL"]
    assert events[0]["calibration_denominator_treatment"] == "INCLUDED"


# 10: cannot be retried

def test_ordinal_8_cannot_be_retried_after_classification(tmp_path):
    event_ledger, stage_ledger = ledgers(tmp_path)
    a, transport = seed_ordinal_8_semantic_failure(event_ledger, stage_ledger)
    event_ledger.classify_raw_verification_negative_terminal(
        run_id=a.run_id, sample_ordinal=8, mint="mint-8", prediction_digest="fake-pred-digest",
        failure_reason="B2Z_P1_NO_FUNDING_EDGE", review_sensitive=True,
        decision_source="HUMAN_REVIEW_KEEP_IN_CALIBRATION",
    )
    before_count = len(transport.requests)
    resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                event_ledger=event_ledger, stage_output_ledger=stage_ledger)  # advances to ordinal 9
    # no request for ordinal 8's funding tx was made again
    ord8_funding_requests = [r for r in transport.requests if r["params"][0] == "funding-mint-8"]
    assert len(ord8_funding_requests) == 1  # still only the original one


# 11: execution exclusion refuses this case (already proven in P2E, re-verify here for completeness)

def test_execution_exclusion_still_refuses_raw_negative_case(tmp_path):
    event_ledger, stage_ledger = ledgers(tmp_path)
    a, transport = seed_ordinal_8_semantic_failure(event_ledger, stage_ledger)
    with pytest.raises(B2ZP1Error, match="NO_MATCHING_FAILURE_TO_EXCLUDE"):
        event_ledger.exclude_member_from_execution(
            run_id=a.run_id, sample_ordinal=8, mint="mint-8", failed_stage=STAGE_FUNDING_TX,
            exclusion_reason="ATTEMPTED_MISUSE", decision_source="TEST",
        )


# 12: next member selected correctly

def test_next_member_selected_correctly(tmp_path):
    event_ledger, stage_ledger = ledgers(tmp_path)
    a, transport = seed_ordinal_8_semantic_failure(event_ledger, stage_ledger)
    event_ledger.classify_raw_verification_negative_terminal(
        run_id=a.run_id, sample_ordinal=8, mint="mint-8", prediction_digest="fake-pred-digest",
        failure_reason="B2Z_P1_NO_FUNDING_EDGE", review_sensitive=True,
        decision_source="HUMAN_REVIEW_KEEP_IN_CALIBRATION",
    )
    result = resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                          event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    assert result["status"] == "STAGE_COMPLETE"
    assert result["sample_ordinal"] == 9


# 13: local false-positive/contradiction metric preserved (structural check)

def test_local_false_positive_metric_derivable_from_ledger(tmp_path):
    event_ledger, stage_ledger = ledgers(tmp_path)
    a, transport = seed_ordinal_8_semantic_failure(event_ledger, stage_ledger)
    event_ledger.classify_raw_verification_negative_terminal(
        run_id=a.run_id, sample_ordinal=8, mint="mint-8", prediction_digest="fake-pred-digest",
        failure_reason="B2Z_P1_NO_FUNDING_EDGE", review_sensitive=True,
        decision_source="HUMAN_REVIEW_KEEP_IN_CALIBRATION",
    )
    # a reader can derive "local false positive" for ordinal 8 by checking:
    # semantic_validation_failed_ordinals() contains it AND raw_negative_terminal_ordinals() contains it
    assert 8 in event_ledger.semantic_validation_failed_ordinals()
    assert 8 in event_ledger.raw_negative_terminal_ordinals()


# 14: duplicate raw-negative terminal rejected

def test_duplicate_raw_negative_terminal_rejected(tmp_path):
    event_ledger, stage_ledger = ledgers(tmp_path)
    a, transport = seed_ordinal_8_semantic_failure(event_ledger, stage_ledger)
    event_ledger.classify_raw_verification_negative_terminal(
        run_id=a.run_id, sample_ordinal=8, mint="mint-8", prediction_digest="fake-pred-digest",
        failure_reason="B2Z_P1_NO_FUNDING_EDGE", review_sensitive=True,
        decision_source="HUMAN_REVIEW_KEEP_IN_CALIBRATION",
    )
    with pytest.raises(B2ZP1Error, match="DUPLICATE_RAW_NEGATIVE_TERMINAL"):
        event_ledger.classify_raw_verification_negative_terminal(
            run_id=a.run_id, sample_ordinal=8, mint="mint-8", prediction_digest="fake-pred-digest",
            failure_reason="B2Z_P1_NO_FUNDING_EDGE", review_sensitive=True,
            decision_source="HUMAN_REVIEW_KEEP_IN_CALIBRATION",
        )


def test_classification_refuses_member_with_no_semantic_failure(tmp_path):
    event_ledger, stage_ledger = ledgers(tmp_path)
    a = auth()
    with pytest.raises(B2ZP1Error, match="NO_MATCHING_SEMANTIC_FAILURE_TO_CLASSIFY"):
        event_ledger.classify_raw_verification_negative_terminal(
            run_id=a.run_id, sample_ordinal=5, mint="mint-5", prediction_digest="fake-pred-digest",
            failure_reason="B2Z_P1_NO_FUNDING_EDGE", review_sensitive=False,
            decision_source="TEST",
        )


def test_classification_refuses_execution_excluded_member(tmp_path):
    event_ledger, stage_ledger = ledgers(tmp_path)
    a = auth()

    class FailTransport(FakeTransport):
        def post_json(self, request):
            if request["params"][0] == "migration-1":
                self.physical_request_count += 1
                self.requests.append(request)
                raise TimeoutError("injected")
            return super().post_json(request)

    ft = FailTransport()
    with pytest.raises(TimeoutError):
        resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=ft,
                    event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    event_ledger.exclude_member_from_execution(
        run_id=a.run_id, sample_ordinal=1, mint="mint-1", failed_stage=STAGE_MIGRATION_TX,
        exclusion_reason="CREDENTIAL_INPUT_CORRUPTION", decision_source="OPTION_A_HUMAN_DECISION",
    )
    # even if ordinal 1 somehow also had a semantic failure recorded (it
    # doesn't in this fixture -- it failed at MIGRATION_TX, not FUNDING_TX --
    # this test proves the guard exists and fires on the exclusion check),
    # attempting to raw-negative-classify an execution-excluded ordinal must fail
    event_ledger.record_semantic_validation_failed_terminal(
        run_id=a.run_id, sample_ordinal=1, mint="mint-1",
        failure_reason="SYNTHETIC_FOR_TEST", candidate_signature="sig",
    )
    with pytest.raises(B2ZP1Error, match="ALREADY_EXECUTION_EXCLUDED"):
        event_ledger.classify_raw_verification_negative_terminal(
            run_id=a.run_id, sample_ordinal=1, mint="mint-1", prediction_digest="fake-pred-digest",
            failure_reason="SYNTHETIC_FOR_TEST", review_sensitive=False,
            decision_source="TEST",
        )


# 15-16: unreviewed semantic failures and transport failures still block

def test_unreviewed_semantic_failure_still_blocks(tmp_path):
    event_ledger, stage_ledger = ledgers(tmp_path)
    a, transport = seed_ordinal_8_semantic_failure(event_ledger, stage_ledger)
    # no classify_raw_verification_negative_terminal() call -- unreviewed
    with pytest.raises(B2ZP1Error, match="MEMBER_BLOCKED_BY_FAILED_STAGE"):
        resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                    event_ledger=event_ledger, stage_output_ledger=stage_ledger)


def test_transport_failure_still_blocks(tmp_path):
    event_ledger, stage_ledger = ledgers(tmp_path)
    a = auth()

    class FailTransport(FakeTransport):
        def post_json(self, request):
            if request["params"][0] == "migration-1":
                self.physical_request_count += 1
                self.requests.append(request)
                raise TimeoutError("injected")
            return super().post_json(request)

    ft = FailTransport()
    with pytest.raises(TimeoutError):
        resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=ft,
                    event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    with pytest.raises(B2ZP1Error, match="MEMBER_BLOCKED_BY_FAILED_STAGE"):
        resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=ft,
                    event_ledger=event_ledger, stage_output_ledger=stage_ledger)


# 17: ledger inconsistencies fail closed

def test_invalid_event_type_fails_closed(tmp_path):
    event_ledger, stage_ledger = ledgers(tmp_path)
    with pytest.raises(B2ZP1Error, match="INVALID_EVENT_TYPE"):
        event_ledger.append_event(
            event="NOT_A_REAL_EVENT", run_id="r", sample_ordinal=8, mint="mint-8",
            stage=STAGE_FUNDING_TX, physical_request_number=0, provider="x",
            endpoint_family="x", method="x", dependency_digest=None, request_digest=None,
        )


# Part 8: mock remaining walk from the exact current state, ordinal 8 through 20

def test_mock_remaining_walk_ordinal_8_through_20(tmp_path):
    import dataclasses
    event_ledger, stage_ledger = ledgers(tmp_path)
    a, transport = seed_ordinal_8_semantic_failure(event_ledger, stage_ledger)
    a = dataclasses.replace(a, max_total_requests=50)
    event_ledger.classify_raw_verification_negative_terminal(
        run_id=a.run_id, sample_ordinal=8, mint="mint-8", prediction_digest="fake-pred-digest",
        failure_reason="B2Z_P1_NO_FUNDING_EDGE", review_sensitive=True,
        decision_source="HUMAN_REVIEW_KEEP_IN_CALIBRATION",
    )
    steps = 0
    while True:
        try:
            result = resume_next(manifest=manifest(), projection=projection(), authorization=a,
                                  transport=transport, event_ledger=event_ledger,
                                  stage_output_ledger=stage_ledger)
        except B2ZP1Error:
            break
        steps += 1
        if result["status"] == "ALL_MEMBERS_COMPLETE":
            break
        if steps > 200:
            raise AssertionError("did not terminate")
    # ordinal 8's funding tx (raw-negative) was never redispatched a second time
    ord8_funding = [r for r in transport.requests if r["params"][0] == "funding-mint-8"]
    assert len(ord8_funding) == 1
    assert event_ledger.physical_requests_attempted() <= 50
    assert all(e.get("physical_request_number", 0) <= 50
               for e in event_ledger.events() if e["event"] in
               ("ATTEMPT_SUCCEEDED", "ATTEMPT_FAILED_AFTER_DISPATCH"))
