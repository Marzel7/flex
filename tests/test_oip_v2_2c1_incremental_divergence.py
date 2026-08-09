from src.evidence.primitives.contracts import (
    ObservationWindow, PrimitiveObservation, PrimitiveQuality, PrimitiveType,
)
from src.evidence.primitives.engine import PrimitiveEngine


def observation(kind, evidence, subjects, payload, parameters=None, timestamp=1):
    return PrimitiveObservation.create(
        primitive_type=kind, primitive_version="1", evidence_ids=evidence,
        subjects=subjects, parameters=parameters or {},
        observation_window=ObservationWindow(timestamp, timestamp),
        output_payload=payload, quality_state=PrimitiveQuality.PROVEN,
        generated_at=0,
    )


def test_unversioned_policy_change_creates_historical_freshness_identity():
    old = observation(
        PrimitiveType.WALLET_FRESH_AT_EVENT, ["balance", "history"], ["wallet"],
        {"wallet": "wallet", "reference_event": "tx", "freshness_state": "VERIFIED_FRESH"},
        {"permitted_prior_transaction_count": 0, "required_zero_balance": True,
         "require_complete_history": True},
    )
    corrected = observation(
        PrimitiveType.WALLET_FRESH_AT_EVENT, ["balance", "history"], ["wallet"],
        {"wallet": "wallet", "reference_event": "tx", "freshness_state": "VERIFIED_FRESH"},
        {"permitted_prior_transaction_count": 0, "required_zero_balance": True,
         "require_complete_history": True, "history_order": "NEWEST_FIRST",
         "reference_boundary": "STRICTLY_PRECEDING"},
    )
    assert old.primitive_version == corrected.primitive_version == "1"
    assert old.primitive_id != corrected.primitive_id


def test_incremental_recurrence_aggregate_is_not_final_clean_aggregate():
    engine = PrimitiveEngine(None)  # private pure generator does not access the database
    first = observation(PrimitiveType.DIRECT_COUNTERPARTY, ["e1"], ["a", "b"],
                        {"source": "a", "destination": "b", "signature": "tx1"}, timestamp=1)
    second = observation(PrimitiveType.DIRECT_COUNTERPARTY, ["e2"], ["a", "b"],
                         {"source": "a", "destination": "b", "signature": "tx2"}, timestamp=2)
    third = observation(PrimitiveType.DIRECT_COUNTERPARTY, ["e3"], ["a", "b"],
                        {"source": "a", "destination": "b", "signature": "tx3"}, timestamp=3)
    incremental = engine._repeated_counterparties([first, second])[0]
    final = engine._repeated_counterparties([first, second, third])[0]
    assert incremental.output_payload["transaction_count"] == 2
    assert final.output_payload["transaction_count"] == 3
    assert incremental.primitive_id != final.primitive_id


def test_incremental_timing_cohort_is_not_final_clean_cohort():
    engine = PrimitiveEngine(None)
    first = observation(PrimitiveType.SYSTEM_TRANSFER, ["e1"], ["wallet", "x"],
                        {"source": "wallet", "destination": "x"}, timestamp=1)
    second = observation(PrimitiveType.SYSTEM_TRANSFER, ["e2"], ["wallet", "y"],
                         {"source": "wallet", "destination": "y"}, timestamp=2)
    third = observation(PrimitiveType.SYSTEM_TRANSFER, ["e3"], ["wallet", "z"],
                        {"source": "wallet", "destination": "z"}, timestamp=3)
    incremental = next(x for x in engine._behavioural_timing([first, second])
                       if x.subjects == ("wallet",))
    final = next(x for x in engine._behavioural_timing([first, second, third])
                 if x.subjects == ("wallet",))
    assert incremental.output_payload["sample_count"] == 2
    assert final.output_payload["sample_count"] == 3
    assert incremental.primitive_id != final.primitive_id
