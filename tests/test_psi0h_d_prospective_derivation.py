import copy

import pytest

from src.evidence.contracts.psi0h_prospective_derivation import (
    Psi0hProspectiveDerivationError,
    qualify_prospective_derivation,
)


DIGEST = "a" * 64


def inputs():
    return {
        "cutoff": 100, "interval_start": 101, "interval_end": 110,
        "envelopes": [{"envelope_id": "env-1", "event_time": 102,
                       "acquired_at": 105, "artifact_digest": DIGEST}],
        "evidence_rows": [{"evidence_id": "e-1", "envelope_id": "env-1",
                           "fact_family": "LaunchFact", "event_time": 102,
                           "payload_digest": DIGEST}],
        "primitive_rows": [{"primitive_id": "p-1", "primitive_type": "LAUNCH_SIGNER",
                            "window_start": 102, "window_end": 102, "generated_at": 106,
                            "evidence_ids": ["e-1"], "missing_inputs": ["AccountParticipationFact"]}],
    }


def test_complete_event_time_lineage_passes_without_authority():
    result = qualify_prospective_derivation(**inputs())
    assert result["status"] == "PASS" and result["primitive_count"] == 1
    assert not result["comparison_performed"] and not any(result["authority"].values())


def test_replay_is_deterministic_and_input_order_independent():
    values = inputs()
    values["envelopes"].append({"envelope_id": "env-2", "event_time": 103,
                                "acquired_at": 106, "artifact_digest": "b" * 64})
    values["evidence_rows"].append({"evidence_id": "e-2", "envelope_id": "env-2",
                                    "fact_family": "TransactionFact", "event_time": 103,
                                    "payload_digest": "b" * 64})
    values["primitive_rows"].append({"primitive_id": "p-2", "primitive_type": "SYSTEM_TRANSFER",
                                     "window_start": 103, "window_end": 103, "generated_at": 107,
                                     "evidence_ids": ["e-2"], "missing_inputs": []})
    first = qualify_prospective_derivation(**values)
    second = qualify_prospective_derivation(**{**values,
        "envelopes": list(reversed(values["envelopes"])),
        "evidence_rows": list(reversed(values["evidence_rows"])),
        "primitive_rows": list(reversed(values["primitive_rows"]))})
    assert first["lineage_digest"] == second["lineage_digest"]
    assert first["replay_digest"] == second["replay_digest"]


@pytest.mark.parametrize("field,value,error", [
    ("acquired_at", 99, "ENVELOPE_INVALID"),
    ("event_time", 100, "ENVELOPE_INVALID"),
])
def test_historical_or_impossible_envelope_time_fails(field, value, error):
    values = inputs()
    values["envelopes"][0][field] = value
    with pytest.raises(Psi0hProspectiveDerivationError, match=error):
        qualify_prospective_derivation(**values)


def test_acquisition_time_cannot_make_historical_event_fresh():
    values = inputs()
    values["envelopes"][0].update(event_time=90, acquired_at=105)
    values["evidence_rows"][0]["event_time"] = 90
    values["primitive_rows"][0].update(window_start=105, window_end=105)
    with pytest.raises(Psi0hProspectiveDerivationError, match="ENVELOPE_INVALID"):
        qualify_prospective_derivation(**values)


def test_missing_or_unbound_lineage_fails_closed():
    missing = inputs()
    missing["primitive_rows"][0]["evidence_ids"] = ["absent"]
    with pytest.raises(Psi0hProspectiveDerivationError, match="LINEAGE_INCOMPLETE"):
        qualify_prospective_derivation(**missing)
    unbound = inputs()
    unbound["evidence_rows"].append({"evidence_id": "e-extra", "envelope_id": "env-1",
                                     "fact_family": "InstructionFact", "event_time": 102,
                                     "payload_digest": "c" * 64})
    with pytest.raises(Psi0hProspectiveDerivationError, match="UNBOUND_EVIDENCE"):
        qualify_prospective_derivation(**unbound)


def test_window_must_equal_underlying_evidence_event_range():
    values = inputs()
    values["primitive_rows"][0]["window_start"] = 103
    values["primitive_rows"][0]["window_end"] = 103
    with pytest.raises(Psi0hProspectiveDerivationError, match="EVENT_TIME_DRIFT"):
        qualify_prospective_derivation(**values)


def test_baseline_identity_reuse_and_bounds_fail_closed():
    values = inputs()
    values["baseline_evidence_ids"] = ["e-1"]
    with pytest.raises(Psi0hProspectiveDerivationError, match="EVIDENCE_INVALID"):
        qualify_prospective_derivation(**values)
    values = inputs()
    values["maximum_primitives"] = 0
    with pytest.raises(Psi0hProspectiveDerivationError, match="BOUND_INVALID"):
        qualify_prospective_derivation(**values)


def test_malformed_digest_and_missing_input_fail_closed():
    values = inputs()
    values["envelopes"][0]["artifact_digest"] = "z" * 64
    with pytest.raises(Psi0hProspectiveDerivationError, match="ENVELOPE_INVALID"):
        qualify_prospective_derivation(**values)
    values = inputs()
    values["primitive_rows"][0]["missing_inputs"] = [""]
    with pytest.raises(Psi0hProspectiveDerivationError, match="PRIMITIVE_INVALID"):
        qualify_prospective_derivation(**values)
