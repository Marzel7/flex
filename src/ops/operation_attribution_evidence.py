"""Non-live, operation-agnostic attribution proof gate (V1)."""
from __future__ import annotations

FAMILIES = ("ADDRESS_CONTINUITY", "FUNDING_CONTINUITY", "CONTROLLER_CONTINUITY", "TRANSACTION_FINGERPRINT", "EARLY_EXECUTION_FINGERPRINT", "TEMPORAL_FINGERPRINT", "LIFECYCLE_BEHAVIOUR", "OTHER_INDEPENDENT_ATTRIBUTION_EVIDENCE")
IDENTITY_FAMILIES = {"ADDRESS_CONTINUITY", "FUNDING_CONTINUITY", "CONTROLLER_CONTINUITY", "OTHER_INDEPENDENT_ATTRIBUTION_EVIDENCE"}
CONTRACT_VERSION = "OPERATION_ATTRIBUTION_EVIDENCE_CONTRACT_V1"
DECISION_SCHEMA_VERSION = "OPERATION_ATTRIBUTION_DECISION_SCHEMA_V1"
COMMON_INFRASTRUCTURE_TYPES = {"CEX", "RELAY", "SOLVER", "RELAY_SOLVER", "ROUTER", "BRIDGE", "GENERIC_SERVICE"}

def decide(families, contradictions=(), common_infrastructure_exclusions=()):
    """Return ATTRIBUTION_PROVEN only for independent strong+moderate proof."""
    blocked = {x.get("dependency_group") for x in common_infrastructure_exclusions if str(x.get("type", "")).upper() in COMMON_INFRASTRUCTURE_TYPES}
    counted = [f for f in families if f["state"] in {"PROVEN_STRONG", "PROVEN_MODERATE", "PROVEN_WEAK"} and f["independence_from_detector"] == "PASS" and f["independence_from_other_families"] == "PASS" and f["dependency_group"] not in blocked]
    groups = [f["dependency_group"] for f in counted]
    strong = [f for f in counted if f["state"] == "PROVEN_STRONG"]
    additional_moderate = [f for f in counted if f["state"] in {"PROVEN_STRONG", "PROVEN_MODERATE"} and f not in strong]
    valid = (not contradictions and len(counted) >= 2 and len(set(groups)) == len(groups)
             and strong and additional_moderate
             and any(f["family"] in IDENTITY_FAMILIES for f in counted))
    return "ATTRIBUTION_PROVEN" if valid else "ATTRIBUTION_NOT_PROVEN"


def build_decision(candidate_id, proposed_operation_id, detector_contract, families, *, detector_evidence_refs=(), common_infrastructure_exclusions=(), contradictions=(), member_attribution=(), pairwise_attribution=(), completeness_state="COMPLETE", builder_version="v1", decision_timestamp="1970-01-01T00:00:00Z"):
    """Deterministic non-live decision schema; it cannot commit membership."""
    state = decide(families, contradictions, common_infrastructure_exclusions)
    normalised = [{**f, "evidence_refs": list(f.get("evidence_refs", [])), "discriminative_strength": f.get("discriminative_strength", f["state"]), "counted_for_attribution": f["state"] in {"PROVEN_STRONG", "PROVEN_MODERATE", "PROVEN_WEAK"} and f["independence_from_detector"] == "PASS" and f["independence_from_other_families"] == "PASS", "rationale": f.get("rationale", "")} for f in families]
    return {"contract_version": CONTRACT_VERSION, "decision_schema_version": DECISION_SCHEMA_VERSION, "candidate_id": candidate_id, "proposed_operation_id": proposed_operation_id, "detector_contract": detector_contract, "detector_evidence_refs": list(detector_evidence_refs), "evidence_families": normalised, "common_infrastructure_exclusions": [{**x, "attribution_eligible": False, "attribution_weight": 0} for x in common_infrastructure_exclusions if str(x.get("type", "")).upper() in COMMON_INFRASTRUCTURE_TYPES], "contradictions": list(contradictions), "member_attribution": list(member_attribution), "pairwise_attribution": list(pairwise_attribution), "minimum_proof_set": [x["family"] for x in normalised if x["counted_for_attribution"]] if state == "ATTRIBUTION_PROVEN" else [], "attribution_state": state, "cohort_state": "MIXED_COHORT" if any(x.get("state") == "UNATTRIBUTED" for x in member_attribution) else "UNIFORM_COHORT", "promotion_eligible": state == "ATTRIBUTION_PROVEN", "completeness_state": completeness_state, "builder_version": builder_version, "decision_timestamp": decision_timestamp}
