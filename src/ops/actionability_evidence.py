"""Prospective, operation-neutral actionability evidence and compact facts.

All acquisition, decoding, and role analysis happens before this module is
called.  ``build_actionability_facts`` is pure; callers pass its compact
output to the single-token ``write_facts`` lane.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from src.ops.operation_fact_framework import EvidenceCompleteness, OperationContract, OperationFact

ACTIONABILITY_EVIDENCE_CONTRACT_VERSION = "ACTIONABILITY_EVIDENCE_CONTRACT_V1"
CONSERVATIVE_ACTIONABILITY_LINKAGE_GATE_VERSION = "CONSERVATIVE_ACTIONABILITY_LINKAGE_GATE_V1"
PRIMARY_RESEARCH_ENTRY_POLICY_VERSION = "PRIMARY_RESEARCH_ENTRY_POLICY_V1"
LIVE_ACTIONABILITY_CAPTURE_ENABLED = False


class ActionableEntryExactness(StrEnum):
    THEORETICAL = "THEORETICAL"
    CONSERVATIVE_LINKED = "CONSERVATIVE_LINKED"
    EXACT_EXCLUSIVITY_PROVEN = "EXACT_EXCLUSIVITY_PROVEN"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class InterleavingState(StrEnum):
    PROVEN_NON_INTERLEAVABLE = "PROVEN_NON_INTERLEAVABLE"
    PROVEN_INTERLEAVABLE = "PROVEN_INTERLEAVABLE"
    NOT_PROVEN_PROHIBITED = "NOT_PROVEN_PROHIBITED"
    UNKNOWN = "UNKNOWN"


class ActionableVenueValuationAdapter(Protocol):
    """Venue-specific valuation outside framework core; no strategy buy size."""
    venue: str
    def value(self, *, actionable_event: dict[str, Any], executable_state: dict[str, Any], supply: dict[str, Any], quote_asset: str, timestamp: int | None) -> dict[str, Any]: ...


def provenance_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def conservative_linkage_gate(linkage: dict[str, Any]) -> bool:
    """Frozen all-signals gate. Timing, buy size, and impact alone never pass."""
    return bool(
        linkage.get("direct_creator_or_operation_funding")
        and linkage.get("signer_continuity")
        and float(linkage.get("funding_buy_match_percent", 101)) <= 5.0
        and linkage.get("immediately_next_ordered_transaction")
    )


def choose_primary_entry(values: dict[str, Any]) -> tuple[Any, ActionableEntryExactness]:
    exact = values.get("exact_actionable_entry_mc")
    conservative = values.get("conservative_actionable_entry_mc")
    theoretical = values.get("theoretical_first_executable_mc")
    if exact is not None:
        return exact, ActionableEntryExactness.EXACT_EXCLUSIVITY_PROVEN
    if conservative is not None:
        return conservative, ActionableEntryExactness.CONSERVATIVE_LINKED
    if theoretical is not None:
        return theoretical, ActionableEntryExactness.THEORETICAL
    return None, ActionableEntryExactness.INSUFFICIENT_EVIDENCE


def _fact(contract: OperationContract, evidence: dict[str, Any], fact_type: str, payload: dict[str, Any], completeness: EvidenceCompleteness) -> OperationFact:
    boundary = payload.get("boundary_identity") or evidence.get("theoretical_first_executable_event", {}).get("signature") or "NO_BOUNDARY"
    provenance = {"contract": ACTIONABILITY_EVIDENCE_CONTRACT_VERSION, "digest": provenance_digest(evidence.get("provenance", evidence)), "raw_references": evidence.get("raw_references", [])}
    return OperationFact(contract.operation_id, evidence["mint"], fact_type, "v1", contract.version, payload, provenance, completeness, payload.get("timestamp"), boundary)


def build_actionability_facts(evidence: dict[str, Any], contract: OperationContract) -> list[OperationFact]:
    """Build six owned compact facts; never acquires providers or touches SQLite."""
    if evidence.get("actionability_not_applicable"):
        exactness = ActionableEntryExactness.NOT_APPLICABLE
        completeness = EvidenceCompleteness.NOT_APPLICABLE
        values = {"theoretical_first_executable_mc": None, "conservative_actionable_entry_mc": None, "exact_actionable_entry_mc": None}
    else:
        values = dict(evidence.get("valuations", {}))
        linkage_pass = conservative_linkage_gate(evidence.get("operation_linkage_evidence", {}))
        if linkage_pass and values.get("conservative_actionable_entry_mc") is None:
            values["conservative_actionable_entry_mc"] = evidence.get("conservative_boundary", {}).get("market_cap")
        exclusivity = evidence.get("execution_exclusivity_evidence", {})
        exact_ok = exclusivity.get("interleaving_state") == InterleavingState.PROVEN_NON_INTERLEAVABLE and values.get("exact_actionable_entry_mc") is not None
        if not exact_ok:
            values["exact_actionable_entry_mc"] = None
        _, exactness = choose_primary_entry(values)
        completeness = EvidenceCompleteness.COMPLETE if exactness != ActionableEntryExactness.INSUFFICIENT_EVIDENCE else EvidenceCompleteness.INSUFFICIENT
    primary, selected_exactness = choose_primary_entry(values)
    if evidence.get("actionability_not_applicable"):
        primary, exactness = None, ActionableEntryExactness.NOT_APPLICABLE
    else:
        exactness = selected_exactness
    theoretical = {"event": evidence.get("theoretical_first_executable_event"), "state": evidence.get("theoretical_first_executable_state"), "market_cap": values.get("theoretical_first_executable_mc"), "boundary_identity": evidence.get("theoretical_first_executable_event", {}).get("signature")}
    linkage = {"signals": evidence.get("operation_linkage_evidence", {}), "gate_version": CONSERVATIVE_ACTIONABILITY_LINKAGE_GATE_VERSION, "gate_pass": conservative_linkage_gate(evidence.get("operation_linkage_evidence", {})), "boundary_identity": evidence.get("conservative_boundary", {}).get("identity")}
    exclusivity = {"evidence": evidence.get("execution_exclusivity_evidence", {}), "first_external_candidate": evidence.get("first_external_candidate"), "boundary_identity": evidence.get("exact_boundary", {}).get("identity")}
    conservative = {"boundary": evidence.get("conservative_boundary"), "market_cap": values.get("conservative_actionable_entry_mc"), "boundary_identity": evidence.get("conservative_boundary", {}).get("identity")}
    exact = {"boundary": evidence.get("exact_boundary"), "market_cap": values.get("exact_actionable_entry_mc"), "boundary_identity": evidence.get("exact_boundary", {}).get("identity")}
    summary = {"primary_actionable_entry_mc": primary, "actionable_entry_exactness": exactness.value, "theoretical_first_executable_mc": values.get("theoretical_first_executable_mc"), "conservative_actionable_entry_mc": values.get("conservative_actionable_entry_mc"), "exact_actionable_entry_mc": values.get("exact_actionable_entry_mc"), "evidence_completeness": completeness.value, "boundary_identity": theoretical["boundary_identity"]}
    return [_fact(contract,evidence,"THEORETICAL_FIRST_EXECUTABLE",theoretical,completeness), _fact(contract,evidence,"OPERATION_LINKAGE_EVIDENCE",linkage,completeness), _fact(contract,evidence,"EXECUTION_EXCLUSIVITY_EVIDENCE",exclusivity,completeness), _fact(contract,evidence,"CONSERVATIVE_ACTIONABLE_ENTRY",conservative,completeness), _fact(contract,evidence,"EXACT_ACTIONABLE_ENTRY",exact,completeness), _fact(contract,evidence,"ACTIONABILITY_COMPLETENESS",summary,completeness)]


class PumpFunActiveCurveValuationAdapter:
    venue = "PUMPFUN_ACTIVE_CURVE"
    def value(self, *, actionable_event, executable_state, supply, quote_asset, timestamp):
        return {"price_quote": executable_state["price_quote"], "mc_quote": executable_state["market_cap_quote"], "usd_conversion": executable_state.get("usd_conversion"), "actionable_entry_mc": executable_state.get("market_cap_usd"), "provenance": executable_state.get("provenance")}


class PumpSwapInitialPoolValuationAdapter:
    venue = "PUMPSWAP_INITIAL_POOL"
    def value(self, *, actionable_event, executable_state, supply, quote_asset, timestamp):
        return {"price_quote": executable_state["price_quote"], "mc_quote": executable_state["market_cap_quote"], "usd_conversion": executable_state.get("usd_conversion"), "actionable_entry_mc": executable_state.get("market_cap_usd"), "provenance": executable_state.get("provenance")}
