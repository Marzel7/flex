"""Generic fact-isolated token-data research orchestration.

This module contains no provider, database, or UI code.  It decides which
already-authorized request identities may be materialized and whether a run is
complete, partial, or blocked without changing valuation/actionability rules.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

CONTRACT_VERSION = "OPERATION_TOKEN_DATA_ORCHESTRATION_CONTRACT_V1"
DAG_VERSION = "TOKEN_DATA_FACT_DEPENDENCY_DAG_V1"
BIRDEYE_COVERAGE_POLICY_VERSION = "BIRDEYE_RECENT_TOKEN_SAMPLE_POLICY_V1"
BIRDEYE_INITIAL_LIFECYCLE_SAMPLE_MAX_TOKEN_AGE = 604800
BIRDEYE_V3_TIMESTAMP_PARSER_VERSION = "BIRDEYE_V3_TIMESTAMP_ALIASES_V1"
LIFECYCLE_WINDOW_POLICY_VERSION = "BIRDEYE_CAPPED_24H_LIFECYCLE_WINDOW_V1"
RECENT_ELIGIBILITY_MANIFEST_REQUIRED = True
SAMPLE_EXPANSION_REQUIRES_RETAINED_ELIGIBILITY_MANIFEST = True
BIRDEYE_V3_TIMESTAMP_FIELDS = ("unix_time", "unixTime", "time", "timestamp")
LIFECYCLE_COVERAGE_FACTS = frozenset({"PRICE_HISTORY", "PEAK", "TERMINAL_VALUE", "MAX_PROVEN_DRAWDOWN", "SOL_USD_FX"})


def capped_lifecycle_window(create_timestamp: int, provider_request_timestamp: int) -> tuple[int, int]:
    """Return the no-future, at-most-24-hour lifecycle window."""
    if provider_request_timestamp < create_timestamp:
        raise ValueError("provider request precedes create timestamp")
    return create_timestamp, min(create_timestamp + 86400, provider_request_timestamp)

FACT_QUALIFIED = "FACT_QUALIFIED"
FACT_INSUFFICIENT_EVIDENCE = "FACT_INSUFFICIENT_EVIDENCE"
FACT_UNRECOVERABLE_HISTORICAL_STATE = "FACT_UNRECOVERABLE_HISTORICAL_STATE"
FACT_PROVIDER_BLOCKED = "FACT_PROVIDER_BLOCKED"
FACT_RATE_LIMITED_PENDING_RETRY = "FACT_RATE_LIMITED_PENDING_RETRY"
FACT_RATE_LIMITED_TERMINAL = "FACT_RATE_LIMITED_TERMINAL"
# Compatibility alias for pre-correction compact artifacts.  New results must
# distinguish a retryable 429 from an exhausted retry budget.
FACT_RATE_LIMITED = FACT_RATE_LIMITED_TERMINAL
FACT_NOT_APPLICABLE = "FACT_NOT_APPLICABLE"
FACT_NOT_EVALUATED_DEPENDENCY_BLOCKED = "FACT_NOT_EVALUATED_DEPENDENCY_BLOCKED"

TERMINAL_FACT_STATES = frozenset({
    FACT_QUALIFIED, FACT_INSUFFICIENT_EVIDENCE, FACT_UNRECOVERABLE_HISTORICAL_STATE,
    FACT_PROVIDER_BLOCKED, FACT_RATE_LIMITED_TERMINAL, FACT_NOT_APPLICABLE,
    FACT_NOT_EVALUATED_DEPENDENCY_BLOCKED,
})
UNRECOVERABLE_FACT_STATES = frozenset({
    FACT_UNRECOVERABLE_HISTORICAL_STATE, FACT_NOT_APPLICABLE,
})

RUN_QUALIFIED_COMPLETE = "RUN_QUALIFIED_COMPLETE"
RUN_QUALIFIED_PARTIAL = "RUN_QUALIFIED_PARTIAL"
RUN_BLOCKED_AUTHORIZATION_REQUIRED = "RUN_BLOCKED_AUTHORIZATION_REQUIRED"
RUN_BLOCKED_METHOD_CHANGE_REQUIRED = "RUN_BLOCKED_METHOD_CHANGE_REQUIRED"
RUN_BLOCKED_SAFETY = "RUN_BLOCKED_SAFETY"


def fact_dependency_dag() -> dict[str, dict[str, object]]:
    """Return the immutable operation-agnostic family dependency contract."""
    return {
        "TOKEN_IDENTITY": {"required_inputs": [], "optional_inputs": [], "dependent_facts": ["CREATE_FACT", "MIGRATION", "PRICE_HISTORY"], "independent_downstream_facts": [], "failure_propagation_scope": "identity-bound facts only"},
        "CREATE_FACT": {"required_inputs": ["TOKEN_IDENTITY"], "optional_inputs": ["retained create signature"], "dependent_facts": ["OPENING_STATE", "PRICE_HISTORY", "OPENING_EXECUTION_FINGERPRINT"], "independent_downstream_facts": ["MIGRATION"], "failure_propagation_scope": "create-timestamp-derived facts only"},
        "OPENING_STATE": {"required_inputs": ["CREATE_FACT"], "optional_inputs": ["historical account-state checkpoint", "bounded ordered early-transaction reconstruction"], "recovery_hierarchy": ["retained qualified opening/account state", "bounded qualified early-transaction reconstruction", "FACT_UNRECOVERABLE_HISTORICAL_STATE"], "dependent_facts": ["THEORETICAL_ENTRY", "CONSERVATIVE_ACTIONABILITY", "EXACT_ACTIONABILITY"], "independent_downstream_facts": ["MIGRATION", "PRICE_HISTORY", "PEAK", "TERMINAL_VALUE", "MAX_PROVEN_DRAWDOWN"], "failure_propagation_scope": "opening valuation/actionability only"},
        "THEORETICAL_ENTRY": {"required_inputs": ["OPENING_STATE"], "optional_inputs": ["SOL_USD_FX"], "dependent_facts": ["CONSERVATIVE_ACTIONABILITY", "EXACT_ACTIONABILITY"], "independent_downstream_facts": ["MIGRATION", "PRICE_HISTORY"], "failure_propagation_scope": "entry-derived facts only"},
        "CONSERVATIVE_ACTIONABILITY": {"required_inputs": ["THEORETICAL_ENTRY"], "optional_inputs": ["actionability linkage evidence"], "dependent_facts": [], "independent_downstream_facts": ["MIGRATION", "PRICE_HISTORY"], "failure_propagation_scope": "conservative actionability only"},
        "EXACT_ACTIONABILITY": {"required_inputs": ["THEORETICAL_ENTRY"], "optional_inputs": ["exclusive executable action evidence"], "dependent_facts": [], "independent_downstream_facts": ["MIGRATION", "PRICE_HISTORY"], "failure_propagation_scope": "exact actionability only"},
        "MIGRATION": {"required_inputs": ["TOKEN_IDENTITY"], "optional_inputs": ["retained migration signature"], "dependent_facts": ["MIGRATION_POOL_VALUATION", "SOL_USD_FX", "OPENING_EXECUTION_FINGERPRINT"], "independent_downstream_facts": ["PRICE_HISTORY"], "failure_propagation_scope": "migration-derived facts only"},
        "MIGRATION_POOL_VALUATION": {"required_inputs": ["MIGRATION"], "optional_inputs": ["SOL_USD_FX"], "dependent_facts": [], "independent_downstream_facts": ["PRICE_HISTORY"], "failure_propagation_scope": "migration-pool valuation only"},
        "SOL_USD_FX": {"required_inputs": ["TOKEN_IDENTITY"], "optional_inputs": ["CREATE_FACT", "MIGRATION"], "dependent_facts": ["THEORETICAL_ENTRY", "MIGRATION_POOL_VALUATION"], "independent_downstream_facts": ["PRICE_HISTORY"], "failure_propagation_scope": "USD conversions only"},
        "PRICE_HISTORY": {"required_inputs": ["CREATE_FACT"], "optional_inputs": ["MIGRATION"], "dependent_facts": ["PEAK", "TERMINAL_VALUE", "MAX_PROVEN_DRAWDOWN"], "independent_downstream_facts": ["OPENING_STATE", "MIGRATION"], "failure_propagation_scope": "lifecycle-price facts only"},
        "PEAK": {"required_inputs": ["PRICE_HISTORY"], "optional_inputs": [], "dependent_facts": ["MAX_PROVEN_DRAWDOWN"], "independent_downstream_facts": ["OPENING_STATE", "MIGRATION"], "failure_propagation_scope": "peak/drawdown only"},
        "TERMINAL_VALUE": {"required_inputs": ["PRICE_HISTORY"], "optional_inputs": [], "dependent_facts": ["MAX_PROVEN_DRAWDOWN"], "independent_downstream_facts": ["OPENING_STATE", "MIGRATION"], "failure_propagation_scope": "terminal/drawdown only"},
        "MAX_PROVEN_DRAWDOWN": {"required_inputs": ["PEAK", "TERMINAL_VALUE"], "optional_inputs": [], "dependent_facts": [], "independent_downstream_facts": ["OPENING_STATE", "MIGRATION"], "failure_propagation_scope": "drawdown only"},
        "OPENING_EXECUTION_FINGERPRINT": {"required_inputs": ["CREATE_FACT"], "optional_inputs": ["MIGRATION", "OPENING_STATE"], "dependent_facts": [], "independent_downstream_facts": ["PRICE_HISTORY", "PEAK", "TERMINAL_VALUE"], "failure_propagation_scope": "fingerprint fields with direct dependencies only"},
    }


@dataclass(frozen=True)
class AuthorizationEnvelope:
    allowed_providers: frozenset[str]
    allowed_methods_or_endpoints: frozenset[str]
    maximum_provider_calls: int
    maximum_per_token_calls: int
    retry_allowed: bool
    raw_retention_allowed: bool
    maximum_physical_attempts: int | None = None
    allowed_fact_families: frozenset[str] | None = None


def lifecycle_coverage_selection_required(requested_families: Sequence[str]) -> bool:
    """Whether requested facts require provider-coverage sample preflight."""
    return bool(LIFECYCLE_COVERAGE_FACTS.intersection(requested_families))


def select_recent_lifecycle_sample(*, members: Sequence[Mapping[str, object]], run_timestamp: int,
                                   sample_size: int) -> dict[str, object]:
    """Select a non-performance-based recent sample from frozen canonical order.

    ``members`` must already be in its frozen canonical order and each row must
    provide ``create_timestamp``.  This is a research-selection policy that
    maximizes likely retrievability; it makes no claim about provider retention.
    """
    if sample_size < 1:
        raise ValueError("sample_size must be positive")
    eligible = []
    for member in members:
        timestamp = member.get("create_timestamp")
        if not isinstance(timestamp, int):
            continue
        age = run_timestamp - timestamp
        if 0 <= age <= BIRDEYE_INITIAL_LIFECYCLE_SAMPLE_MAX_TOKEN_AGE:
            eligible.append({**member, "token_age_seconds": age})
    if len(eligible) < sample_size:
        return {"state": "INSUFFICIENT_RECENT_MEMBERS", "recent_eligible_member_count": len(eligible), "sample": []}
    indices = tuple(((len(eligible) - 1) * percentile) // 100 for percentile in (0, 25, 50, 75, 100))
    # For a non-five request, evenly spaced deterministic quantiles retain the
    # same frozen-order, non-performance-based property.
    if sample_size != 5:
        indices = tuple(((len(eligible) - 1) * ordinal) // (sample_size - 1) if sample_size > 1 else 0 for ordinal in range(sample_size))
    return {"state": "SAMPLE_SELECTION_READY", "recent_eligible_member_count": len(eligible),
            "selection_method": "frozen canonical order; deterministic quantiles first/p25/median/p75/last" if sample_size == 5 else "frozen canonical order; evenly spaced deterministic quantiles",
            "sample": [eligible[index] for index in indices]}


def execute_birdeye_same_identity_retry(*, dispatch, sleep):
    """Execute an already-authorized Birdeye identity under the shared policy.

    ``dispatch`` returns ``(status, retry_after)`` where status is either
    ``HTTP_429`` or a terminal transport/provider outcome.  The function is
    deliberately transport- and operation-agnostic; callers account every
    physical dispatch and retain only compact results.  A retry is authorized
    only because it is the same deterministic identity under the envelope's
    qualified policy, never a new request identity.
    """
    from src.ops.birdeye_rate_limit import run_retries

    def policy_dispatch():
        status, retry_after = dispatch()
        return ("RATE_LIMITED" if status == "HTTP_429" else status), retry_after

    result = run_retries(policy_dispatch, sleep)
    return {
        **result,
        "fact_state": (
            FACT_RATE_LIMITED_TERMINAL
            if result["status"] == "RATE_LIMITED" and result["exhausted"]
            else FACT_RATE_LIMITED_PENDING_RETRY
            if result["status"] == "RATE_LIMITED"
            else None
        ),
    }


def dependencies_satisfied(family: str, facts: Mapping[str, str]) -> bool:
    node = fact_dependency_dag()[family]
    return all(facts.get(dependency) == FACT_QUALIFIED for dependency in node["required_inputs"])


def dependency_blocked_state(family: str, facts: Mapping[str, str]) -> str | None:
    node = fact_dependency_dag()[family]
    values = [facts.get(dependency) for dependency in node["required_inputs"]]
    if any(value in UNRECOVERABLE_FACT_STATES for value in values):
        return FACT_NOT_EVALUATED_DEPENDENCY_BLOCKED
    return None


def materialize_dependent_requests(*, facts: Mapping[str, str], candidates: Sequence[Mapping[str, object]],
                                   envelope: AuthorizationEnvelope, calls_used: int, token_calls_used: Mapping[str, int]) -> tuple[list[dict[str, object]], str | None]:
    """Approve only pre-authorized, prerequisite-satisfied request templates.

    A candidate must contain ``family``, ``provider``, ``method_or_endpoint``,
    ``token_mint``, and a fully resolved ``identity``.  This makes timestamps
    produced by an upstream fact legitimate inputs to a downstream identity.
    """
    materialized: list[dict[str, object]] = []
    for candidate in candidates:
        family = str(candidate["family"])
        if not dependencies_satisfied(family, facts):
            continue
        if candidate.get("provider") not in envelope.allowed_providers or candidate.get("method_or_endpoint") not in envelope.allowed_methods_or_endpoints:
            return materialized, RUN_BLOCKED_AUTHORIZATION_REQUIRED
        if not candidate.get("identity"):
            return materialized, RUN_BLOCKED_METHOD_CHANGE_REQUIRED
        mint = str(candidate["token_mint"])
        if calls_used + len(materialized) >= envelope.maximum_provider_calls or token_calls_used.get(mint, 0) + sum(item["token_mint"] == mint for item in materialized) >= envelope.maximum_per_token_calls:
            return materialized, RUN_BLOCKED_AUTHORIZATION_REQUIRED
        materialized.append(dict(candidate))
    return materialized, None


def run_outcome(*, requested_families: Sequence[str], facts: Mapping[str, str], run_block: str | None = None) -> str:
    if run_block:
        return run_block
    states = [facts.get(family, FACT_NOT_EVALUATED_DEPENDENCY_BLOCKED) for family in requested_families]
    if any(state not in TERMINAL_FACT_STATES for state in states):
        return RUN_BLOCKED_SAFETY
    return RUN_QUALIFIED_COMPLETE if all(state in {FACT_QUALIFIED, FACT_NOT_APPLICABLE} for state in states) else RUN_QUALIFIED_PARTIAL
