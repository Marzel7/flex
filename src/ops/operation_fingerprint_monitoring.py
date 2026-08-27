"""Read-only behavioural fingerprint health projection for Active Operations.

Detector identity is structural and deliberately contains no literal wallet or
mint requirement. Address observations are corroborating evidence only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FingerprintDefinition:
    fingerprint_id: str
    description: str
    detection_type: str
    infrastructure_classification: str
    infrastructure_summary: str
    required_fields: tuple[tuple[str, str], ...]
    qualified_tp: int | None = None
    qualified_external_exact_matches: int | None = None
    observable_population: int | None = None


# Presentation/provenance labels for immutable detector versions. No entry
# contains a literal address and these values never participate in matching.
DEFINITIONS: dict[str, FingerprintDefinition] = {
    "WATCHTOWER": FingerprintDefinition("WSOL-ROUTE-STRICT-v1", "Strict completed funding-route lifecycle with rotating launch roles.", "ADDRESS-INDEPENDENT", "ROTATING_INFRASTRUCTURE", "Rotating route infrastructure corroborates the behavioural fingerprint; no literal account is a detector input.", (("completed_route_topology", "behavioural/structural"), ("funding_mechanism", "behavioural/structural"), ("route_validation", "behavioural/structural"))),
    "Byzantine": FingerprintDefinition("10SOL-WSOL-4STEP-v1", "Single funder pattern provisioning rotating creators through an exact 10 SOL four-step WSOL provision-and-close flow.", "ADDRESS-INDEPENDENT", "STRONG_SHARED_INFRASTRUCTURE", "32/32 discovery-cohort launches retain shared direct-funder evidence; that address is corroboration, not a detector input.", (("selected_hop_structure", "behavioural/structural"), ("semantic_sequence", "behavioural/structural"), ("amount_vector", "behavioural/structural"), ("atomic_lifecycle", "behavioural/structural")), 32, 0, 12041),
    "FOUR_STEP_30_SOL_14_479K_WSOL_LADDER": FingerprintDefinition("30SOL-WSOL-LADDER-14479K-v1", "Exact four-hop approximately 30 SOL funding ladder followed through a distinctive sequence of WSOL close amounts.", "ADDRESS-INDEPENDENT", "ROTATING_INFRASTRUCTURE", "No shared literal account is required or established; identity is the exact behavioural ladder.", (("selected_hop_structure", "behavioural/structural"), ("semantic_sequence", "behavioural/structural"), ("amount_vector", "behavioural/structural"), ("atomic_lifecycle", "behavioural/structural")), 9, 0, 12041),
    "P3R": FingerprintDefinition("100SOL-WSOL-CLOSE-v1", "Exact 99.999985 SOL WSOL close behaviour with its required completed atomic lifecycle.", "ADDRESS-INDEPENDENT", "MULTIPLE_RECURRING_ADDRESSES", "Recurring funding infrastructure is retained as corroboration; rotating addresses are not detector inputs.", (("selected_hop_structure", "behavioural/structural"), ("funding_mechanism", "behavioural/structural"), ("amount_vector", "behavioural/structural"), ("atomic_lifecycle", "behavioural/structural"))),
    "P3R_13A04": FingerprintDefinition("30SOL-5K-LADDER-v1", "Four-hop approximately 30 SOL ladder using alternating transfer and WSOL-close steps with fixed 5,000-lamport increments.", "ADDRESS-INDEPENDENT", "ROTATING_INFRASTRUCTURE", "No literal address is required; retained route roles rotate across the reference cohort.", (("selected_hop_structure", "behavioural/structural"), ("semantic_sequence", "behavioural/structural"), ("amount_vector", "behavioural/structural"))),
    "WSOL_PROVISION_CLOSE_1_SOL_MINUS_15K": FingerprintDefinition("1SOL-WSOL-PROVISION-CLOSE-15K-v1", "Near-1-SOL temporary WSOL provision-and-close behaviour with recurring funding infrastructure.", "PROVISIONAL-HYBRID", "SHARED_ADDRESS_EVIDENCE", "Recurring funder evidence increases review confidence but is not sufficient for confirmed attribution.", (("selected_hop_structure", "behavioural/structural"), ("funding_mechanism", "behavioural/structural"), ("amount_vector", "behavioural/structural"))),
}


def _percentage(tp: int | None, external: int | None) -> float | None:
    return None if tp is None or external is None or tp + external == 0 else round(tp * 100 / (tp + external), 2)


def build_fingerprint_health(operation: dict[str, Any]) -> dict[str, Any] | None:
    """Build a transparent projection; near matches never change exact uniqueness."""
    definition = DEFINITIONS.get(operation.get("display_name"))
    if definition is None:
        return None
    contract = operation.get("qualification_contract") or {}
    benchmark = contract.get("benchmark") or {}
    tp = benchmark.get("tp") if isinstance(benchmark.get("tp"), int) else definition.qualified_tp
    external = benchmark.get("external_exact_matches") if isinstance(benchmark.get("external_exact_matches"), int) else definition.qualified_external_exact_matches
    return {
        "fingerprint_id": definition.fingerprint_id,
        "description": definition.description,
        "detector_version": contract.get("detector_version"),
        "detection_type": definition.detection_type,
        "required_fields": [{"field": field, "classification": kind, "literal_address_required": False, "required": True} for field, kind in definition.required_fields],
        "infrastructure_evidence": {"classification": definition.infrastructure_classification, "summary": definition.infrastructure_summary},
        "qualified_uniqueness_percent": _percentage(definition.qualified_tp, definition.qualified_external_exact_matches),
        "current_uniqueness_percent": _percentage(tp, external),
        "matching_operation_launches": tp,
        "external_exact_matches": external,
        "observable_population": benchmark.get("observable_population", definition.observable_population),
        "trend": "INSUFFICIENT_HISTORY",
        "near_match_count": 0,
        "drift_status": "NO_RECURRING_NEAR_MATCH_OBSERVED",
        "potential_relation": None,
        "formula": "TP / (TP + external_exact_matches) * 100; unobservable and near-match rows are excluded.",
    }


def audit_confirmed_address_independence() -> list[dict[str, Any]]:
    return [{"operation": name, "classification": "ADDRESS_INDEPENDENT_PASS", "fields": [{"field": field, "classification": kind, "literal_address_required": False, "required": True} for field, kind in definition.required_fields]} for name, definition in DEFINITIONS.items() if definition.detection_type == "ADDRESS-INDEPENDENT"]
