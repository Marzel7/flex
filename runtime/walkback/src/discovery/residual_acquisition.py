"""Bounded, non-dispatching OPS-DISCOVERY-P3R request planning."""
from __future__ import annotations


def freeze_budget(*, migration_transactions: int, upstream_histories: int, behaviour_transactions: int, cache_hits: int) -> dict:
    total = migration_transactions + upstream_histories + behaviour_transactions
    return {"maximum_physical_requests": total, "cache_hits": cache_hits,
            "categories": {"migration_actor": migration_transactions, "topology": upstream_histories,
                           "behaviour": behaviour_transactions},
            "execution_state": "MANUAL_GATE" if total <= 200 else "HUMAN_APPROVAL_REQUIRED",
            "no_retry": True, "no_pagination": True}
