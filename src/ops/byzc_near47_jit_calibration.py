"""Strict, research-only evaluator for a Near47 Df8C→ByZc JIT pair."""
from __future__ import annotations

CONTRACT_ID = "NEAR47_DF8C_BYZC_WSOL_CALIBRATION_V1"
CALIBRATION_PASS = "CALIBRATION_PASS"
NOT_NEAR47 = "NOT_NEAR47"
INCOMPLETE = "NEAR47_INCOMPLETE_EVIDENCE"
NEGATIVE = "NEAR47_NEGATIVE_COMPLETE_WINDOW"


def evaluate(candidate: dict, *, max_time_delta: int | None, max_slot_delta: int | None) -> dict:
    """Evaluate a pre-collected mint-local candidate; never acquires evidence."""
    if not candidate.get("near47_member"):
        return {"state": NOT_NEAR47, "failed_gates": ["DISQUALIFIER_NOT_NEAR47"]}
    if not candidate.get("target_qualified") or not candidate.get("target_window_complete"):
        return {"state": INCOMPLETE, "failed_gates": ["TARGET_OR_WINDOW_INCOMPLETE"]}
    if not candidate.get("df8c_transfer"):
        return {"state": NEGATIVE, "failed_gates": ["DF8C_TRANSFER_ABSENT_COMPLETE_WINDOW"]}
    required = ("target_slot", "target_time", "df8c_slot", "df8c_time", "amounts_complete", "wsol_complete", "mint_link")
    if not all(candidate.get(key) for key in required) or max_time_delta is None or max_slot_delta is None:
        return {"state": INCOMPLETE, "failed_gates": ["PROVENANCE_OR_FROZEN_BOUND_MISSING"]}
    slot_delta = candidate["target_slot"] - candidate["df8c_slot"]
    time_delta = candidate["target_time"] - candidate["df8c_time"]
    if slot_delta <= 0 or time_delta < 0:
        return {"state": INCOMPLETE, "failed_gates": ["ORDERING_UNPROVEN"]}
    if slot_delta > max_slot_delta or time_delta > max_time_delta:
        return {"state": INCOMPLETE, "failed_gates": ["DISQUALIFIER_OUTSIDE_FROZEN_WINDOW"]}
    if candidate.get("competing_funder_ambiguous"):
        return {"state": INCOMPLETE, "failed_gates": ["DISQUALIFIER_AMBIGUOUS_COMPETING_FUNDER"]}
    return {"state": CALIBRATION_PASS, "failed_gates": [], "slot_delta": slot_delta, "time_delta": time_delta}
