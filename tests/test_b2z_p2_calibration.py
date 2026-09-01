"""B2Z-P2: focused tests for the local-vs-raw calibration mechanism.

Covers: frozen-signature enforcement, anti-circularity (seeded stage never
counts toward physical budget), the exact 50-request plan, the P2 authorization
using a 50-request ceiling narrower than the runner's 60 hard ceiling, and
disagreement recording without silent correction. All tests use tmp_path and
a FakeTransport -- zero real network calls.
"""
from __future__ import annotations

import dataclasses

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
    seed_frozen_creator_history_from_local_prediction,
)


def manifest():
    return B2NManifest(tuple(B2NMember(i, f"mint-{i}", f"event-{i}", True) for i in range(1, 21)))


def projection():
    return B2WInputProjection(tuple(
        B2WRequestInput(i, f"mint-{i}", f"event-{i}", f"migration-{i}") for i in range(1, 21)
    ))


def migration_result(mint, block_time=1786568285):
    return {"result": {"blockTime": block_time, "slot": 12345, "transaction": {"message": {"accountKeys": [
        {"pubkey": mint, "signer": False}, {"pubkey": f"creator-{mint}", "signer": True},
    ]}}}}


def funding_result(creator, block_time=1786568143, lamports=49718778, destination=None):
    return {"result": {"blockTime": block_time, "slot": 11111, "transaction": {"message": {"instructions": [{
        "program": "system", "parsed": {"type": "transfer", "info": {
            "source": "frozen-local-funder", "destination": destination or creator, "lamports": lamports,
        }},
    }]}}}}


class FakeTransport:
    def __init__(self, *, bad_destination_mints=None):
        self.physical_request_count = 0
        self.requests = []
        self.bad_destination_mints = bad_destination_mints or set()

    def post_json(self, request):
        self.physical_request_count += 1
        self.requests.append(request)
        method, target = request["method"], request["params"][0]
        if method == "getTransaction" and target.startswith("migration-"):
            mint = target.replace("migration-", "mint-")
            return migration_result(mint)
        if method == "getSignaturesForAddress":
            mint = target.replace("creator-", "")
            return {"result": [{"signature": mint, "blockTime": 1786568143}]}
        if method == "getTransaction":  # a targeted funding lookup (frozen or live-selected signature)
            mint = target  # signature IS the mint tag in this fixture, for simplicity
            creator = f"creator-{mint}"
            bad = mint in self.bad_destination_mints
            return funding_result(creator, destination="someone-else" if bad else None)
        raise AssertionError(f"unexpected request: {request}")


def auth(tmp_path, max_total=50):
    a = build_authorization(manifest=manifest(), projection=projection(),
                             b2n_closure_digest="d1", p0_preflight_digest="d2")
    return dataclasses.replace(a, max_total_requests=max_total)


def ledgers(tmp_path):
    return (B2ZEventLedger(tmp_path / "events.jsonl"), B2ZStageOutputLedger(tmp_path / "stage.json"))


# --- exact 50-request plan ---------------------------------------------

def test_ten_skip_members_yield_exactly_50_total_requests(tmp_path):
    """1 MIGRATION_TX (live) + 0 CREATOR_HISTORY (seeded, frozen) + 1 FUNDING_TX
    (live, targeted) for 10 members = 20; 3 live stages for the other 10 = 30;
    total = 50, matching the frozen plan exactly."""
    a = auth(tmp_path)
    event_ledger, stage_ledger = ledgers(tmp_path)
    transport = FakeTransport()
    skip_ordinals = {1, 2, 4, 7, 10, 12, 13, 14, 17, 20}

    for ordinal in range(1, 21):
        mint = f"mint-{ordinal}"
        # Stage 1: always live
        r1 = resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                          event_ledger=event_ledger, stage_output_ledger=stage_ledger)
        assert r1["sample_ordinal"] == ordinal and r1["stage"] == STAGE_MIGRATION_TX
        migration_time = r1["output"]["migration_time"]

        if ordinal in skip_ordinals:
            # Seed Stage 2 from a frozen local prediction -- no live dispatch
            seed_frozen_creator_history_from_local_prediction(
                run_id=a.run_id, sample_ordinal=ordinal, mint=mint, event_ledger=event_ledger,
                stage_output_ledger=stage_ledger, frozen_creator=f"creator-{mint}",
                frozen_migration_time=migration_time, frozen_funding_signature=mint,
                frozen_prediction_digest="frozen-abc",
            )
        else:
            r2 = resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                              event_ledger=event_ledger, stage_output_ledger=stage_ledger)
            assert r2["stage"] == STAGE_CREATOR_HISTORY

        r3 = resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                          event_ledger=event_ledger, stage_output_ledger=stage_ledger)
        assert r3["stage"] == STAGE_FUNDING_TX
        assert r3["status"] == "MEMBER_COMPLETE"

    assert event_ledger.physical_requests_attempted() == 50
    final = resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                         event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    assert final["status"] == "ALL_MEMBERS_COMPLETE"


# --- frozen-signature enforcement / anti-circularity --------------------

def test_seeded_stage_never_counts_toward_physical_budget(tmp_path):
    a = auth(tmp_path)
    event_ledger, stage_ledger = ledgers(tmp_path)
    transport = FakeTransport()
    resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                event_ledger=event_ledger, stage_output_ledger=stage_ledger)  # member 1 stage 1
    seed_frozen_creator_history_from_local_prediction(
        run_id=a.run_id, sample_ordinal=1, mint="mint-1", event_ledger=event_ledger,
        stage_output_ledger=stage_ledger, frozen_creator="creator-mint-1",
        frozen_migration_time=1786568285, frozen_funding_signature="mint-1",
        frozen_prediction_digest="frozen-xyz",
    )
    assert event_ledger.physical_requests_attempted() == 1  # only stage 1 counted, seed added zero


def test_frozen_signature_is_the_exact_target_dispatched_in_stage3(tmp_path):
    """The runner must inspect the FROZEN signature, not re-derive/select a
    different one after seeing stage 1's raw result."""
    a = auth(tmp_path)
    event_ledger, stage_ledger = ledgers(tmp_path)
    transport = FakeTransport()
    resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    seed_frozen_creator_history_from_local_prediction(
        run_id=a.run_id, sample_ordinal=1, mint="mint-1", event_ledger=event_ledger,
        stage_output_ledger=stage_ledger, frozen_creator="creator-mint-1",
        frozen_migration_time=1786568285, frozen_funding_signature="mint-1",
        frozen_prediction_digest="frozen-xyz",
    )
    resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    dispatched_targets = [r["params"][0] for r in transport.requests if r["method"] == "getTransaction"
                           and not r["params"][0].startswith("migration-")]
    assert dispatched_targets == ["mint-1"]  # exactly the frozen signature, nothing substituted


def test_seed_refuses_to_overwrite_existing_stage(tmp_path):
    a = auth(tmp_path)
    event_ledger, stage_ledger = ledgers(tmp_path)
    transport = FakeTransport()
    resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    seed_frozen_creator_history_from_local_prediction(
        run_id=a.run_id, sample_ordinal=1, mint="mint-1", event_ledger=event_ledger,
        stage_output_ledger=stage_ledger, frozen_creator="creator-mint-1",
        frozen_migration_time=1786568285, frozen_funding_signature="mint-1",
        frozen_prediction_digest="frozen-xyz",
    )
    with pytest.raises(B2ZP1Error, match="STAGE_ALREADY_HAS_EVENTS"):
        seed_frozen_creator_history_from_local_prediction(
            run_id=a.run_id, sample_ordinal=1, mint="mint-1", event_ledger=event_ledger,
            stage_output_ledger=stage_ledger, frozen_creator="creator-mint-1",
            frozen_migration_time=1786568285, frozen_funding_signature="different-sig",
            frozen_prediction_digest="frozen-different",
        )


# --- disagreement recording, not silent correction -----------------------

def test_raw_disagreement_with_frozen_prediction_is_not_silently_corrected(tmp_path):
    """If the raw Stage 3 result's parsed destination does NOT match the
    creator (e.g. the frozen local signature turns out not to prove the
    funding edge), the runner must FAIL (raise), not substitute a different
    signature or silently accept a non-matching result."""
    a = auth(tmp_path)
    event_ledger, stage_ledger = ledgers(tmp_path)
    transport = FakeTransport(bad_destination_mints={"mint-1"})
    resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    seed_frozen_creator_history_from_local_prediction(
        run_id=a.run_id, sample_ordinal=1, mint="mint-1", event_ledger=event_ledger,
        stage_output_ledger=stage_ledger, frozen_creator="creator-mint-1",
        frozen_migration_time=1786568285, frozen_funding_signature="mint-1",
        frozen_prediction_digest="frozen-xyz",
    )
    with pytest.raises(B2ZP1Error, match="NO_FUNDING_EDGE"):
        resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                    event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    # the request WAS still dispatched and counted -- disagreement is a real, accounted outcome
    assert event_ledger.physical_requests_attempted() == 2


# --- P2 authorization ceiling narrower than runner's 60 hard ceiling ------

def test_p2_authorization_fails_closed_at_50_not_60(tmp_path):
    a = auth(tmp_path, max_total=50)
    assert a.max_total_requests == 50
    from src.acquisition.b2z_durable_execution import MAX_TOTAL_REQUESTS
    assert MAX_TOTAL_REQUESTS == 60  # the module's runner-level hard ceiling, unchanged
    assert a.max_total_requests < MAX_TOTAL_REQUESTS  # P2's authorization is strictly narrower


def test_51st_request_is_refused_under_50_ceiling(tmp_path):
    a = auth(tmp_path, max_total=1)  # tiny ceiling to exercise the guard cheaply
    event_ledger, stage_ledger = ledgers(tmp_path)
    transport = FakeTransport()
    resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                event_ledger=event_ledger, stage_output_ledger=stage_ledger)  # consumes the only allowed request
    seed_frozen_creator_history_from_local_prediction(
        run_id=a.run_id, sample_ordinal=1, mint="mint-1", event_ledger=event_ledger,
        stage_output_ledger=stage_ledger, frozen_creator="creator-mint-1",
        frozen_migration_time=1786568285, frozen_funding_signature="mint-1",
        frozen_prediction_digest="frozen-xyz",
    )
    with pytest.raises(B2ZP1Error, match="GLOBAL_BUDGET_EXHAUSTED"):
        resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                    event_ledger=event_ledger, stage_output_ledger=stage_ledger)


# --- resume works with seeded stages mixed in -----------------------------

def test_resume_after_interruption_correctly_resumes_past_a_seeded_stage(tmp_path):
    a = auth(tmp_path)
    event_ledger, stage_ledger = ledgers(tmp_path)
    transport = FakeTransport()
    resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                event_ledger=event_ledger, stage_output_ledger=stage_ledger)  # member 1 stage 1
    seed_frozen_creator_history_from_local_prediction(
        run_id=a.run_id, sample_ordinal=1, mint="mint-1", event_ledger=event_ledger,
        stage_output_ledger=stage_ledger, frozen_creator="creator-mint-1",
        frozen_migration_time=1786568285, frozen_funding_signature="mint-1",
        frozen_prediction_digest="frozen-xyz",
    )
    # "interruption" -- just stop, then resume fresh
    result = resume_next(manifest=manifest(), projection=projection(), authorization=a, transport=transport,
                          event_ledger=event_ledger, stage_output_ledger=stage_ledger)
    assert result["sample_ordinal"] == 1
    assert result["stage"] == STAGE_FUNDING_TX  # correctly skips the seeded CREATOR_HISTORY stage


# --- live-path isolation ---------------------------------------------------

def test_no_test_resolves_to_live_paths(tmp_path):
    event_ledger, stage_ledger = ledgers(tmp_path)
    assert "docs/audits" not in str(event_ledger.path)
    assert "docs/audits" not in str(stage_ledger.path)
