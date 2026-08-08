from __future__ import annotations

import json

import pytest

from src.evidence.discovery import (
    CandidateLifecycle, DiscoveryEngine, DiscoverySnapshot, DiscoveryStore,
)
from src.evidence.operation_contracts.input_windows import (
    EvidenceInputWindow, PrimitiveInputWindow,
)
from src.evidence.primitives.contracts import (
    ObservationWindow, PrimitiveObservation, PrimitiveQuality, PrimitiveType,
)


def primitive(kind, subjects, payload, *, signature, start=1, quality=PrimitiveQuality.PROVEN,
              missing=()):
    return PrimitiveObservation.create(
        primitive_type=kind, primitive_version="1", evidence_ids=[f"ev-{signature}"],
        subjects=subjects, parameters={}, observation_window=ObservationWindow(start, start),
        output_payload=payload, quality_state=quality, missing_inputs=missing,
        generated_at=7,
    )


def snapshot(primitives, *, generated_at=10):
    subjects=sorted({subject for item in primitives for subject in item.subjects})
    evidence=EvidenceInputWindow.create(subjects=subjects,start=1,end=9,
        watermark="a"*64,observations=())
    primitive_window=PrimitiveInputWindow.create(subjects=subjects,start=1,end=9,
        watermark="b"*64,observations=primitives)
    return DiscoverySnapshot.create(discovery_version="1.0.0",
        evidence_window=evidence,primitive_window=primitive_window,generated_at=generated_at)


def recurring_primitives():
    return (
        primitive(PrimitiveType.SYSTEM_TRANSFER,("wallet-a","wallet-b"),
                  {"source":"wallet-a","destination":"wallet-b","signature":"s1"},signature="s1"),
        primitive(PrimitiveType.SYSTEM_TRANSFER,("wallet-a","wallet-c"),
                  {"source":"wallet-a","destination":"wallet-c","signature":"s2"},signature="s2",start=2),
        primitive(PrimitiveType.LAUNCH_SIGNER,("wallet-b","mint-1"),
                  {"wallet":"wallet-b","mint":"mint-1","signer":True},signature="s3",start=3),
        primitive(PrimitiveType.LAUNCH_SIGNER,("wallet-c","mint-2"),
                  {"wallet":"wallet-c","mint":"mint-2","signer":True},signature="s4",start=4),
    )


def test_discovery_is_deterministic_label_blind_candidate_generation():
    engine=DiscoveryEngine(); source=recurring_primitives()
    first=engine.discover(snapshot(source)); second=engine.discover(snapshot(tuple(reversed(source))))
    assert [item.candidate_id for item in first]==[item.candidate_id for item in second]
    assert first and all(item.lifecycle=="RECURRING_PATTERN" for item in first)
    encoded=json.dumps([item.to_dict() for item in first]).lower()
    for forbidden in ("operator", "treasury", "controller", "watchtower", "governance", "confidence"):
        assert forbidden not in encoded
    assert all(item.supporting_evidence_ids and item.supporting_primitive_ids for item in first)


def test_replay_identity_does_not_depend_on_materialization_wall_clock():
    source=recurring_primitives(); engine=DiscoveryEngine()
    early=snapshot(source,generated_at=10); late=snapshot(source,generated_at=999)
    assert early.input_digest==late.input_digest
    assert [item.to_dict() for item in engine.discover(early)] == [
        item.to_dict() for item in engine.discover(late)
    ]


def test_candidate_persistence_is_append_only_and_replayable(tmp_path):
    candidates=DiscoveryEngine().discover(snapshot(recurring_primitives()))
    store=DiscoveryStore(tmp_path/"discovery.db"); store.open()
    try:
        first=store.append(candidates); second=store.append(candidates)
        assert first=={"inserted":len(candidates),"duplicates":0}
        assert second=={"inserted":0,"duplicates":len(candidates)}
        candidate=candidates[0]
        event=store.transition(candidate.candidate_id,
            from_state=CandidateLifecycle.RECURRING_PATTERN,
            to_state=CandidateLifecycle.INVESTIGATE,reason="analyst triage",occurred_at=11)
        assert event and store.current_state(candidate.candidate_id) is CandidateLifecycle.INVESTIGATE
        with pytest.raises(ValueError):
            store.transition(candidate.candidate_id,
                from_state=CandidateLifecycle.INVESTIGATE,
                to_state=CandidateLifecycle.RECURRING_PATTERN,reason="forbidden",occurred_at=12)
        assert store.health()["authoritative"] is False
    finally: store.close()


def test_missing_and_conflicting_inputs_remain_explicit():
    values=list(recurring_primitives())
    values.append(primitive(PrimitiveType.SHARED_TRANSACTION,("wallet-a","wallet-d"),
        {"wallets":["wallet-a","wallet-d"],"signature":"s5"},signature="s5",start=5,
        quality=PrimitiveQuality.CONFLICTING,missing=("TransactionFact",)))
    candidates=DiscoveryEngine().discover(snapshot(tuple(values)))
    candidate=next(item for item in candidates if "wallet-a" in item.population and
                   "wallet-d" in item.population)
    assert candidate.quality_state=="CONFLICTING"
    assert candidate.missing_evidence==("TransactionFact",)
    assert "ev-s5" in candidate.contradictory_evidence


def test_discovery_health_has_no_identity_or_governance_authority():
    health=DiscoveryEngine().health()
    assert health["authoritative"] is False
    assert health["identity_enabled"] is False
    assert health["governance_enabled"] is False
