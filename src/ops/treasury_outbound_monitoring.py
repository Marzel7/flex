"""Provider-free, disabled-by-default monitoring contract for DEV-019.

This module plans compact incremental discovery of *known* treasury outbound
funding.  It performs no RPC, opens no production database, and does not
register a scheduler.  A future runtime adapter must supply a cursor store and
the existing explicit-authority Helius transport at a separately approved
deployment boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Iterable, Mapping

from src.ops.treasury_activity import ACTIVE, DORMANT, HOT, RETIRED_CANDIDATE, ACTIVITY_UNKNOWN


FEATURE_FLAG_DEFAULT = False
MAX_COMPACT_RECORD_BYTES = 1_024
MAX_COMPACT_STORE_BYTES = 10_000_000
MAX_SINGLE_FILE_BYTES = 10_000_000
VALID_COVERAGE = {
    "LIVE_COVERED", "RECONCILIATION_DUE", "COVERAGE_INCOMPLETE",
    "PROVIDER_LIMIT_STOP", "ACTIVITY_UNKNOWN",
}


@dataclass(frozen=True)
class TreasuryMonitorState:
    treasury: str
    activity: str
    cursor_signature: str | None
    coverage_status: str

    def validate(self) -> None:
        if not self.treasury or self.coverage_status not in VALID_COVERAGE:
            raise ValueError("INVALID_TREASURY_MONITOR_STATE")


@dataclass(frozen=True)
class MonitoringPlan:
    enabled: bool = FEATURE_FLAG_DEFAULT
    hot_interval_seconds: int = 15 * 60
    active_interval_seconds: int = 60 * 60
    inactive_interval_seconds: int = 24 * 60 * 60
    max_signatures_per_reconciliation: int = 20
    max_decodes_per_reconciliation: int = 20
    material_screening_lamports: int = 10_000_000_000

    def validate(self) -> None:
        if self.enabled:
            raise ValueError("MONITORING_FEATURE_MUST_REMAIN_DISABLED_IN_DEV")
        if min(self.hot_interval_seconds, self.active_interval_seconds, self.inactive_interval_seconds) <= 0:
            raise ValueError("INVALID_MONITOR_INTERVAL")
        if not 1 <= self.max_signatures_per_reconciliation <= 20:
            raise ValueError("INVALID_SIGNATURE_PAGE_BOUND")
        if not 1 <= self.max_decodes_per_reconciliation <= self.max_signatures_per_reconciliation:
            raise ValueError("INVALID_DECODE_BOUND")
        if self.material_screening_lamports <= 0:
            raise ValueError("INVALID_MATERIALITY_THRESHOLD")


def reconciliation_interval(activity: str, plan: MonitoringPlan = MonitoringPlan()) -> int:
    """Every confirmed treasury remains eligible; inactivity only reduces cost."""
    plan.validate()
    if activity == HOT:
        return plan.hot_interval_seconds
    if activity == ACTIVE:
        return plan.active_interval_seconds
    return plan.inactive_interval_seconds


def next_reconciliation(state: TreasuryMonitorState, *, now: int, last_checked_at: int | None,
                        plan: MonitoringPlan = MonitoringPlan()) -> dict:
    state.validate()
    interval = reconciliation_interval(state.activity, plan)
    due = last_checked_at is None or now >= last_checked_at + interval
    return {
        "treasury": state.treasury,
        "due": due,
        "cursor_signature": state.cursor_signature,
        "coverage_status": state.coverage_status,
        "request": "getSignaturesForAddress" if due else None,
        "limit": plan.max_signatures_per_reconciliation if due else 0,
        "recovery": "DECODE_ALL_SELECTED_SIGNATURES_BEFORE_CURSOR_ADVANCE",
    }


def classify_outbound_fact(fact: Mapping[str, object], *, confirmed_treasuries: Iterable[str]) -> dict:
    """Accept only compact direct, balance-verified native SOL funding facts."""
    treasury = str(fact.get("sender") or "")
    direct = fact.get("route_semantics") == "DIRECT"
    verified = bool(fact.get("balance_delta_verified"))
    recipient = str(fact.get("receiver") or "")
    accepted = treasury in set(confirmed_treasuries) and direct and verified and bool(recipient)
    return {
        "status": "VERIFIED_OUTBOUND" if accepted else "NOT_INDEXABLE_OUTBOUND",
        "treasury": treasury if accepted else None,
        "recipient": recipient if accepted else None,
        "promotion": None,
        "reason": None if accepted else "REQUIRES_CONFIRMED_SENDER_DIRECT_BALANCE_VERIFIED_FACT",
    }


def update_coverage(*, selected_signatures: Iterable[str], decoded_signatures: Iterable[str],
                    provider_stopped: bool) -> str:
    selected, decoded = set(selected_signatures), set(decoded_signatures)
    if provider_stopped:
        return "PROVIDER_LIMIT_STOP"
    if selected != decoded:
        return "COVERAGE_INCOMPLETE"
    return "LIVE_COVERED"


def daily_request_estimate(activity_counts: Mapping[str, int], *,
                           plan: MonitoringPlan = MonitoringPlan(),
                           decoded_transactions_per_poll: float = 0.0) -> dict:
    """Estimate bounded polling calls; event/subscription delivery is excluded.

    The estimate assumes one non-paginated signature request per scheduled
    reconciliation.  Transaction detail calls are an explicit variable rather
    than a claim about future wallet activity.
    """
    plan.validate()
    if not 0 <= decoded_transactions_per_poll <= plan.max_decodes_per_reconciliation:
        raise ValueError("INVALID_DECODE_RATE")
    count = lambda activity: max(0, int(activity_counts.get(activity, 0)))
    polls = (
        count(HOT) * 86_400 / plan.hot_interval_seconds
        + count(ACTIVE) * 86_400 / plan.active_interval_seconds
        + (count(DORMANT) + count(RETIRED_CANDIDATE) + count(ACTIVITY_UNKNOWN))
        * 86_400 / plan.inactive_interval_seconds
    )
    signature_requests = ceil(polls)
    transaction_requests = ceil(polls * decoded_transactions_per_poll)
    return {
        "treasury_count": sum(count(a) for a in (HOT, ACTIVE, DORMANT, RETIRED_CANDIDATE, ACTIVITY_UNKNOWN)),
        "signature_requests_per_day": signature_requests,
        "transaction_requests_per_day": transaction_requests,
        "total_rpc_requests_per_day": signature_requests + transaction_requests,
        "helius_credit_estimate_at_10_per_request": (signature_requests + transaction_requests) * 10,
        "assumptions": "one bounded signature page/poll; decode rate is explicit; websocket/webhook delivery excluded",
    }


def scaled_activity_counts(*, population: int, measured_counts: Mapping[str, int]) -> dict[str, int]:
    """Scale the measured 83-treasury mix deterministically for planning only."""
    if population < 0:
        raise ValueError("INVALID_POPULATION")
    categories = (HOT, ACTIVE, DORMANT, RETIRED_CANDIDATE, ACTIVITY_UNKNOWN)
    total = sum(max(0, int(measured_counts.get(category, 0))) for category in categories)
    if not total:
        raise ValueError("MISSING_ACTIVITY_DISTRIBUTION")
    base = {category: int(population * max(0, int(measured_counts.get(category, 0))) / total) for category in categories}
    remainder = population - sum(base.values())
    fractions = sorted(categories, key=lambda c: (-(population * max(0, int(measured_counts.get(c, 0))) / total - base[c]), c))
    for category in fractions[:remainder]:
        base[category] += 1
    return base


def storage_estimate(*, compact_records_per_day: int) -> dict:
    if compact_records_per_day < 0:
        raise ValueError("INVALID_RECORD_RATE")
    daily = compact_records_per_day * MAX_COMPACT_RECORD_BYTES
    return {
        "estimated_logical_bytes_per_day": daily,
        "record_bound_bytes": MAX_COMPACT_RECORD_BYTES,
        "store_fail_closed_bytes": MAX_COMPACT_STORE_BYTES,
        "max_single_file_bytes": MAX_SINGLE_FILE_BYTES,
        "raw_transaction_retention": False,
        "unbounded_growth_paths": 0,
    }
