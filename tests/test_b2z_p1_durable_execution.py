"""B2Z-P1: focused tests for the durable, resumable B2Z execution boundary.

All tests use tmp_path-scoped ledgers and a FakeTransport -- zero real
network calls, zero credentials. Structural guards (test_no_test_resolves_to_live_paths)
apply the B2N test-incident lesson: no test may default-resolve to the real
docs/audits/ live B2Z paths.
"""
from __future__ import annotations

import json

import pytest

from src.acquisition.b2n_qualification import B2NManifest, B2NMember
from src.acquisition.b2w_projection import B2WInputProjection, B2WRequestInput
from src.acquisition.b2z_durable_execution import (
    FAN_OUT_REVIEW_ORDINALS,
    MAX_TOTAL_REQUESTS,
    STAGE_CREATOR_HISTORY,
    STAGE_FUNDING_TX,
    STAGE_MIGRATION_TX,
    B2ZEventLedger,
    B2ZP1Error,
    B2ZStageOutputLedger,
    build_authorization,
    derive_b2z_run_id,
    resume_next,
)

MANIFEST_DIGEST_SEED = "seed"


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
    """Deterministic fake keyed on method+first-param so multi-stage
    sequences resolve correctly regardless of call order across resume
    invocations."""

    def __init__(self, *, fail_stage=None, fail_mode="dispatch_error", no_candidate_mints=None,
                 bad_funding_mints=None):
        self.physical_request_count = 0
        self.requests = []
        self.fail_stage = fail_stage  # (mint, method) tuple to fail on
        self.fail_mode = fail_mode  # "before_dispatch" or "dispatch_error"
        self.no_candidate_mints = no_candidate_mints or set()
        self.bad_funding_mints = bad_funding_mints or set()

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
        if method == "getTransaction":  # funding tx lookup
            mint = target.replace("funding-", "")
            creator = f"creator-{mint}"
            if mint in self.bad_funding_mints:
                return funding_result(creator=creator, destination="someone-else")
            return funding_result(creator=creator)
        raise AssertionError(f"unexpected request: {request}")


def auth(tmp_path):
    return build_authorization(
        manifest=manifest(), projection=projection(),
        b2n_closure_digest="fake-b2n-digest", p0_preflight_digest="fake-p0-digest",
    )


def ledgers(tmp_path):
    return (
        B2ZEventLedger(tmp_path / "events.jsonl"),
        B2ZStageOutputLedger(tmp_path / "stage_outputs.json"),
    )


# --- authorization binding ---------------------------------------------

def test_authorization_binds_exact_cohort_and_budget(tmp_path):
    a = auth(tmp_path)
    assert a.max_total_requests == 60
    assert a.max_requests_per_member == 3
    assert a.max_requests_per_stage == 1
    assert a.retries == 0
    assert a.pagination_budget == 0
    assert a.fallback_budget == 0
    assert a.production_db_read is False
    assert a.production_db_write is False
    assert a.candidate_evidence_only is True


def test_run_id_deterministic_and_distinct_from_b2n(tmp_path):
    a1 = auth(tmp_path)
    a2 = auth(tmp_path)
    assert a1.run_id == a2.run_id  # deterministic
    assert a1.run_id != "b2n-p3e-98695fbdf44146c93ff08c8c"
    assert a1.run_id.startswith("b2z-p1-")


def test_authorization_digest_is_stable(tmp_path):
    a1 = auth(tmp_path)
    a2 = auth(tmp_path)
    assert a1.digest() == a2.digest()


# --- stage identity / dependency order ----------------------------------

def test_first_resume_call_processes_stage_1_of_member_1(tmp_path):
    a = auth(tmp_path)
    event_ledger, stage_ledger = ledgers(tmp_path)
    transport = FakeTransport()
    result = resume_next(
        manifest=manifest(), projection=projection(), authorization=a, transport=transport,
        event_ledger=event_ledger, stage_output_ledger=stage_ledger,
    )
    assert result["status"] == "STAGE_COMPLETE"
    assert result["sample_ordinal"] == 1
    assert result["stage"] == STAGE_MIGRATION_TX
    assert event_ledger.physical_requests_attempted() == 1


def test_stage_2_requires_stage_1_output_present(tmp_path):
    a = auth(tmp_path)
    event_ledger, stage_ledger = ledgers(tmp_path)
    # manually seed only stage-1 completion for member 1, then resume should hit stage 2
    transport = FakeTransport()
    resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    result = resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                          event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    assert result["stage"] == STAGE_CREATOR_HISTORY
    assert result["output"]["creator"] == "creator-mint-1"


def test_full_member_completes_in_exactly_3_resume_calls(tmp_path):
    a = auth(tmp_path)
    event_ledger, stage_ledger = ledgers(tmp_path)
    transport = FakeTransport()
    r1 = resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                      event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    r2 = resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                      event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    r3 = resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                      event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    assert [r1["stage"], r2["stage"], r3["stage"]] == [STAGE_MIGRATION_TX, STAGE_CREATOR_HISTORY, STAGE_FUNDING_TX]
    assert r3["status"] == "MEMBER_COMPLETE"
    assert r3["output"]["evidence_observed"] is True
    assert r3["output"]["candidate_only"] is True
    assert event_ledger.physical_requests_attempted() == 3


# --- pre-dispatch durability ---------------------------------------------

def test_reservation_written_before_dispatch_for_every_stage(tmp_path):
    a = auth(tmp_path)
    event_ledger, stage_ledger = ledgers(tmp_path)
    transport = FakeTransport()
    resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    events = event_ledger.events()
    assert events[0]["event"] == "ATTEMPT_RESERVED"
    assert events[1]["event"] == "ATTEMPT_SUCCEEDED"
    assert events[0]["sample_ordinal"] == events[1]["sample_ordinal"] == 1
    assert events[0]["stage"] == events[1]["stage"] == STAGE_MIGRATION_TX


def test_failure_before_dispatch_does_not_consume_budget(tmp_path):
    a = auth(tmp_path)
    event_ledger, stage_ledger = ledgers(tmp_path)
    transport = FakeTransport(fail_stage=("migration-1", "getTransaction"), fail_mode="before_dispatch")
    with pytest.raises(ValueError):
        resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                    event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    assert event_ledger.physical_requests_attempted() == 0
    events = event_ledger.events()
    assert events[-1]["event"] == "ATTEMPT_NOT_DISPATCHED"


def test_failure_after_dispatch_consumes_budget_and_is_durably_recorded(tmp_path):
    a = auth(tmp_path)
    event_ledger, stage_ledger = ledgers(tmp_path)
    transport = FakeTransport(fail_stage=("migration-1", "getTransaction"), fail_mode="dispatch_error")
    with pytest.raises(TimeoutError):
        resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                    event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    assert event_ledger.physical_requests_attempted() == 1
    events = event_ledger.events()
    assert events[-1]["event"] == "ATTEMPT_FAILED_AFTER_DISPATCH"
    assert events[-1]["error_class"] == "TimeoutError"


def test_blocked_member_raises_and_does_not_skip_ahead(tmp_path):
    """After a failed-after-dispatch stage, the member is blocked -- resume
    must not silently jump to a later member's stage 1."""
    a = auth(tmp_path)
    event_ledger, stage_ledger = ledgers(tmp_path)
    transport = FakeTransport(fail_stage=("migration-1", "getTransaction"), fail_mode="dispatch_error")
    with pytest.raises(TimeoutError):
        resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                    event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    with pytest.raises(B2ZP1Error, match="MEMBER_BLOCKED_BY_FAILED_STAGE"):
        resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                    event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    # no member-2 attempt was made
    assert all(e["sample_ordinal"] == 1 for e in event_ledger.events())


# --- duplicate prevention -------------------------------------------------

def test_duplicate_stage_attempt_rejected(tmp_path):
    a = auth(tmp_path)
    event_ledger, stage_ledger = ledgers(tmp_path)
    transport = FakeTransport()
    resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    from src.acquisition.b2z_durable_execution import DurableB2ZClient
    client = DurableB2ZClient(transport=transport, event_ledger=event_ledger, authorization=a)
    with pytest.raises(B2ZP1Error, match="DUPLICATE_STAGE_ATTEMPT"):
        client.dispatch(sample_ordinal=1, mint="mint-1", stage=STAGE_MIGRATION_TX, method="getTransaction",
                         params=["migration-1", {}], dependency_digest=None)


# --- no-candidate / multiple-candidate semantics --------------------------

def test_no_candidate_ends_member_with_only_2_requests(tmp_path):
    a = auth(tmp_path)
    event_ledger, stage_ledger = ledgers(tmp_path)
    transport = FakeTransport(no_candidate_mints={"mint-1"})
    resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                event_ledger=event_ledger, stage_output_ledger=stage_ledger)  # stage 1
    r2 = resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                      event_ledger=event_ledger, stage_output_ledger=stage_ledger)  # stage 2
    assert r2["status"] == "NO_CANDIDATE"
    r3 = resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                      event_ledger=event_ledger, stage_output_ledger=stage_ledger)  # stage 3 short-circuits
    assert r3["status"] == "NO_EVIDENCE_NO_CANDIDATE"
    assert event_ledger.physical_requests_attempted() == 2  # NOT 3 -- no provider call for stage 3
    # no request for the funding tx was ever sent
    assert not any(r["method"] == "getTransaction" and r["params"][0].startswith("funding-") for r in transport.requests)
    # NO_CANDIDATE_TERMINAL was durably recorded so resume can advance
    assert 1 in event_ledger.no_candidate_terminal_ordinals()
    assert (1, STAGE_FUNDING_TX) in event_ledger.succeeded_stage_keys()
    # a repeated call for the SAME member is idempotent (no duplicate event,
    # no new request) and, critically, resume now ADVANCES to ordinal 2
    # instead of re-deriving the same NO_EVIDENCE_NO_CANDIDATE result forever
    r4 = resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                      event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    assert r4["sample_ordinal"] == 2
    assert r4["stage"] == STAGE_MIGRATION_TX
    no_candidate_events = [e for e in event_ledger.events() if e["event"] == "NO_CANDIDATE_TERMINAL"]
    assert len(no_candidate_events) == 1  # not duplicated by the repeated call


def test_no_candidate_terminal_liveness_regression(tmp_path):
    """Regression test for the observed live incident: 17 consecutive
    --resume invocations against a NO_EVIDENCE_NO_CANDIDATE member made zero
    progress (0 new physical requests, 0 new ledger events, permanently
    stuck on the same ordinal) because the member was never durably marked
    complete. Prove that calling resume_next() many times past a
    NO_EVIDENCE_NO_CANDIDATE member eventually reaches a LATER member,
    rather than looping on the same one forever."""
    a = auth(tmp_path)
    event_ledger, stage_ledger = ledgers(tmp_path)
    transport = FakeTransport(no_candidate_mints={"mint-1"})
    results = []
    for _ in range(6):
        results.append(resume_next(manifest=manifest(), projection=projection(), authorization=a,
                                    transport=transport, event_ledger=event_ledger,
                                    stage_output_ledger=stage_ledger))
    # stage1, stage2(NO_CANDIDATE), stage3(NO_EVIDENCE_NO_CANDIDATE), then
    # ordinal 2's three stages -- by the 6th call we must be well past ordinal 1
    assert results[-1]["sample_ordinal"] > 1
    # physical requests reflect real work only: ordinal 1 consumed 2 (not 3),
    # ordinal 2 consumed up to 3 more depending on how far the loop got
    assert event_ledger.physical_requests_attempted() >= 2
    # simulate the exact observed incident: call resume 17 MORE times after
    # ordinal 1 is already terminally resolved, and confirm the ordinal
    # never regresses back to 1 and no duplicate NO_CANDIDATE_TERMINAL appears
    for _ in range(17):
        try:
            resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                        event_ledger=event_ledger, stage_output_ledger=stage_ledger)
        except B2ZP1Error:
            break  # acceptable -- e.g. all members complete or a later block; must NOT be stuck on ordinal 1
    no_candidate_events = [e for e in event_ledger.events() if e["event"] == "NO_CANDIDATE_TERMINAL"
                            and e["sample_ordinal"] == 1]
    assert len(no_candidate_events) == 1
    assert not any(e["sample_ordinal"] == 1 and e["event"] not in
                    ("ATTEMPT_RESERVED", "ATTEMPT_SUCCEEDED", "NO_CANDIDATE_TERMINAL")
                    for e in event_ledger.events())


def test_multiple_candidates_selects_most_recent_before_migration(tmp_path):
    """Mirrors the existing B2ZRunner contract exactly: candidates[0] after
    the blockTime < migration_time filter, no additional provider calls to
    disambiguate."""
    a = auth(tmp_path)
    event_ledger, stage_ledger = ledgers(tmp_path)

    class MultiCandidateTransport(FakeTransport):
        def post_json(self, request):
            method, target = request["method"], request["params"][0]
            if method == "getSignaturesForAddress":
                self.physical_request_count += 1
                self.requests.append(request)
                return {"result": [
                    {"signature": "funding-mint-1-recent", "blockTime": 95},
                    {"signature": "funding-mint-1-older", "blockTime": 80},
                    {"signature": "funding-mint-1-after-migration", "blockTime": 150},
                ]}
            return super().post_json(request)

    transport = MultiCandidateTransport()
    resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                event_ledger=event_ledger, stage_output_ledger=stage_ledger)  # stage 1
    r2 = resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                      event_ledger=event_ledger, stage_output_ledger=stage_ledger)  # stage 2
    assert r2["output"]["candidate_funding_signature"] == "funding-mint-1-recent"
    assert r2["output"]["candidate_pool_size_after_filter"] == 2  # the post-migration one is filtered out
    assert event_ledger.physical_requests_attempted() == 2  # exactly 1 getSignaturesForAddress call, no extra disambiguation calls


# --- fan-out review flag ---------------------------------------------------

def test_fan_out_ordinals_get_review_flag_without_changing_acquisition():
    assert FAN_OUT_REVIEW_ORDINALS == frozenset({8, 11, 15, 19})


def test_review_flag_set_only_for_flagged_ordinal_full_member(tmp_path):
    # Build an 8-member-capable manifest/projection so ordinal 8 exists
    m = B2NManifest(tuple(B2NMember(i, f"mint-{i}", f"event-{i}", True) for i in range(1, 21)))
    p = B2WInputProjection(tuple(B2WRequestInput(i, f"mint-{i}", f"event-{i}", f"migration-{i}") for i in range(1, 21)))
    a = build_authorization(manifest=m, projection=p, b2n_closure_digest="d1", p0_preflight_digest="d2")
    event_ledger, stage_ledger = ledgers(tmp_path)
    transport = FakeTransport()
    # drive through members 1..8 (3 stages each = 24 calls) using resume_next repeatedly
    result = None
    for _ in range(24):
        result = resume_next(manifest=m, projection=p, authorization=a, transport=transport,
                              event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    assert result["sample_ordinal"] == 8
    assert result["status"] == "MEMBER_COMPLETE"
    assert result["output"]["review_flag"] == "FUNDING_SOURCE_REQUIRES_DOWNSTREAM_REVIEW"

    # ordinal 1 must NOT carry the flag
    outputs_1 = stage_ledger.member_outputs(1)
    assert outputs_1[STAGE_FUNDING_TX]["review_flag"] is None


def test_review_flag_does_not_alter_request_count_or_reject_evidence(tmp_path):
    m = B2NManifest(tuple(B2NMember(i, f"mint-{i}", f"event-{i}", True) for i in range(1, 21)))
    p = B2WInputProjection(tuple(B2WRequestInput(i, f"mint-{i}", f"event-{i}", f"migration-{i}") for i in range(1, 21)))
    a = build_authorization(manifest=m, projection=p, b2n_closure_digest="d1", p0_preflight_digest="d2")
    event_ledger, stage_ledger = ledgers(tmp_path)
    transport = FakeTransport()
    for _ in range(24):
        resume_next(manifest=m, projection=p, authorization=a, transport=transport,
                    event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    assert event_ledger.physical_requests_attempted() == 24  # exactly 3/member, flag adds zero requests


# --- budget exhaustion -----------------------------------------------------

def test_global_budget_exhaustion_stops_before_the_61st_request(tmp_path):
    from src.acquisition.b2z_durable_execution import B2ZAuthorization
    a = auth(tmp_path)
    # authorization with an artificially tiny ceiling to exercise the guard cheaply
    tiny = B2ZAuthorization(**{**a.__dict__, "max_total_requests": 2})
    event_ledger, stage_ledger = ledgers(tmp_path)
    transport = FakeTransport()
    resume_next(manifest=manifest(), projection=projection(), authorization=tiny, transport=transport,
                event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    resume_next(manifest=manifest(), projection=projection(), authorization=tiny, transport=transport,
                event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    with pytest.raises(B2ZP1Error, match="GLOBAL_BUDGET_EXHAUSTED"):
        resume_next(manifest=manifest(), projection=projection(), authorization=tiny, transport=transport,
                    event_ledger=event_ledger, stage_output_ledger=stage_ledger)


# --- full mocked 20-member run + resume from injected interruptions -------

def test_full_mocked_20_member_run_exactly_60_requests(tmp_path):
    a = auth(tmp_path)
    event_ledger, stage_ledger = ledgers(tmp_path)
    transport = FakeTransport()
    results = []
    for _ in range(60):
        r = resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                         event_ledger=event_ledger, stage_output_ledger=stage_ledger)
        results.append(r)
    assert results[-1]["status"] == "MEMBER_COMPLETE"
    assert results[-1]["sample_ordinal"] == 20
    assert event_ledger.physical_requests_attempted() == 60
    member_completes = [r for r in results if r.get("status") == "MEMBER_COMPLETE"]
    assert len(member_completes) == 20
    assert sorted(r["sample_ordinal"] for r in member_completes) == list(range(1, 21))
    # no duplicates, no gaps in event ledger
    events = event_ledger.events()
    reservations = [(e["sample_ordinal"], e["stage"]) for e in events if e["event"] == "ATTEMPT_RESERVED"]
    assert len(reservations) == len(set(reservations)) == 60
    # calling again returns ALL_MEMBERS_COMPLETE, no further dispatch
    final = resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                         event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    assert final["status"] == "ALL_MEMBERS_COMPLETE"
    assert event_ledger.physical_requests_attempted() == 60


def test_resume_from_interruption_after_member_4_stage_2(tmp_path):
    a = auth(tmp_path)
    event_ledger, stage_ledger = ledgers(tmp_path)
    transport = FakeTransport()
    # drive members 1-3 fully (9 calls) + member 4 stage 1 + stage 2 (2 calls) = 11 calls
    for _ in range(11):
        resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                    event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    # "interruption" -- just stop calling, as if the process died here
    assert event_ledger.physical_requests_attempted() == 11

    # fresh resume must continue at member 4 / FUNDING_TX, not member 1 or member 5
    result = resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                          event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    assert result["sample_ordinal"] == 4
    assert result["stage"] == STAGE_FUNDING_TX
    assert result["status"] == "MEMBER_COMPLETE"


def test_resume_from_interruption_after_member_2_stage_1(tmp_path):
    a = auth(tmp_path)
    event_ledger, stage_ledger = ledgers(tmp_path)
    transport = FakeTransport()
    for _ in range(4):  # member 1 (3) + member 2 stage 1 (1)
        resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                    event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    result = resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                          event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    assert result["sample_ordinal"] == 2
    assert result["stage"] == STAGE_CREATOR_HISTORY


# --- test-path isolation ---------------------------------------------------

def test_no_test_resolves_to_live_paths(tmp_path):
    event_ledger, stage_ledger = ledgers(tmp_path)
    assert "docs/audits" not in str(event_ledger.path)
    assert "docs/audits" not in str(stage_ledger.path)
    assert str(tmp_path) in str(event_ledger.path)
    assert str(tmp_path) in str(stage_ledger.path)


# --- secret redaction (no credential ever touches this module) ------------

def test_no_credential_symbols_anywhere_in_module():
    import src.acquisition.b2z_durable_execution as mod
    import inspect
    source = inspect.getsource(mod)
    assert "HELIUS_ENDPOINT" not in source
    assert "os.environ" not in source
    assert "api-key" not in source.lower()
    # module intentionally never constructs a transport itself -- the caller
    # injects one, so no endpoint/credential string can appear here at all


def test_stage_2_timeout_after_dispatch_durably_accounted(tmp_path):
    a = auth(tmp_path)
    event_ledger, stage_ledger = ledgers(tmp_path)
    transport = FakeTransport(fail_stage=("creator-mint-1", "getSignaturesForAddress"), fail_mode="dispatch_error")
    resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                event_ledger=event_ledger, stage_output_ledger=stage_ledger)  # stage 1 succeeds
    with pytest.raises(TimeoutError):
        resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                    event_ledger=event_ledger, stage_output_ledger=stage_ledger)  # stage 2 fails after dispatch
    assert event_ledger.physical_requests_attempted() == 2
    events = event_ledger.events()
    assert events[-1]["event"] == "ATTEMPT_FAILED_AFTER_DISPATCH"
    assert events[-1]["stage"] == STAGE_CREATOR_HISTORY


def test_stage_3_malformed_response_raises_and_is_accounted(tmp_path):
    """Regression test for the observed live incident: ordinal 8's FUNDING_TX
    dispatched successfully (ATTEMPT_SUCCEEDED, physical request correctly
    counted) but failed proves_inbound_sol_funding(). Before the fix, this
    left the member looking "done" to succeeded_stage_keys() (via the raw
    ATTEMPT_SUCCEEDED) with NO stage output ever recorded -- a future resume
    would have silently skipped it as if real evidence existed. The fix
    durably records SEMANTIC_VALIDATION_FAILED_TERMINAL (distinguishable
    from ATTEMPT_SUCCEEDED-with-real-evidence) and a stage output describing
    the contradiction, so the finding cannot be silently lost."""
    a = auth(tmp_path)
    event_ledger, stage_ledger = ledgers(tmp_path)
    transport = FakeTransport(bad_funding_mints={"mint-1"})
    resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                event_ledger=event_ledger, stage_output_ledger=stage_ledger)  # stage 1
    resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                event_ledger=event_ledger, stage_output_ledger=stage_ledger)  # stage 2
    with pytest.raises(B2ZP1Error, match="NO_FUNDING_EDGE"):
        resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                    event_ledger=event_ledger, stage_output_ledger=stage_ledger)  # stage 3 -- wrong destination
    assert event_ledger.physical_requests_attempted() == 3  # the malformed-for-our-purposes response was still dispatched and counted
    events = event_ledger.events()
    # the raw transport-level ATTEMPT_SUCCEEDED remains untouched (the request
    # genuinely did succeed at the HTTP/RPC level) -- but the durable
    # SEMANTIC_VALIDATION_FAILED_TERMINAL is appended immediately after it,
    # so the failure is never silently indistinguishable from real evidence
    assert events[-2]["event"] == "ATTEMPT_SUCCEEDED"
    assert events[-1]["event"] == "SEMANTIC_VALIDATION_FAILED_TERMINAL"
    assert events[-1]["failure_reason"] == "B2Z_P1_NO_FUNDING_EDGE"
    assert 1 in event_ledger.semantic_validation_failed_ordinals()
    # a distinguishable stage output was recorded -- NOT a MEMBER_COMPLETE-shaped one
    output = stage_ledger.get_stage_output(sample_ordinal=1, stage=STAGE_FUNDING_TX)
    assert output is not None
    assert output["terminal_status"] == "SEMANTIC_VALIDATION_FAILED"
    assert output["evidence_observed"] is False
    # critically: a LATER resume_next() call must NOT silently skip ordinal 1
    # as if it had genuine evidence -- it must raise, surfacing the failure,
    # exactly like an unexcluded ATTEMPT_FAILED_AFTER_DISPATCH would
    with pytest.raises(B2ZP1Error):
        resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                    event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    # and the raise must happen WITHOUT any new physical request or duplicate event
    assert event_ledger.physical_requests_attempted() == 3
    assert len([e for e in event_ledger.events() if e["event"] == "SEMANTIC_VALIDATION_FAILED_TERMINAL"]) == 1


# --- operation isolation ---------------------------------------------------

def test_no_watchtower_or_operation_coupling():
    import inspect
    import src.acquisition.b2z_durable_execution as mod
    source = inspect.getsource(mod)
    for forbidden in ("watchtower", "three_sw2", "wt_operations", "canonical_operation"):
        assert forbidden not in source.lower()


def test_output_is_candidate_only():
    a = build_authorization(manifest=manifest(), projection=projection(),
                             b2n_closure_digest="d1", p0_preflight_digest="d2")
    assert a.candidate_evidence_only is True
    assert a.existing_operation_mutation_forbidden is True
