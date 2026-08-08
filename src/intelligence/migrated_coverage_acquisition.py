"""Bounded missing-only acquisition for OIP v2.1 shadow Evidence coverage."""
from __future__ import annotations

import asyncio
import hashlib
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Awaitable, Callable, Iterable

from src.intelligence.migrated_coverage import LaunchCoverage


class CoverageBudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class AcquisitionTarget:
    signature: str
    launch: str
    purpose: str


def targets(rows: Iterable[LaunchCoverage]) -> list[AcquisitionTarget]:
    by_signature: dict[str, AcquisitionTarget] = {}
    for row in rows:
        if row.recovery != "BOUNDED_ACQUISITION":
            continue
        if row.creation_signature and not row.creation_transaction_present:
            by_signature.setdefault(row.creation_signature, AcquisitionTarget(
                row.creation_signature, row.mint, "eligible_migrated_creation"
            ))
        if row.migration_signature and not row.migration_transaction_present:
            by_signature.setdefault(row.migration_signature, AcquisitionTarget(
                row.migration_signature, row.mint, "eligible_migrated_migration"
            ))
    return [by_signature[key] for key in sorted(by_signature)]


def representative_sample(rows: Iterable[LaunchCoverage], *, call_limit: int) -> tuple[list[AcquisitionTarget], dict]:
    """Select complete launch dependency groups across objective strata.

    Ordering uses a stable digest, never insertion order. A first pass maximises
    creator diversity; a second fills remaining capacity. Dependency groups are
    indivisible so selected launches can become complete rather than receiving
    one of two required transactions.
    """
    candidates = [row for row in rows if row.recovery == "BOUNDED_ACQUISITION"]
    dated = sorted(row.launch_timestamp for row in candidates if row.launch_timestamp is not None)
    cuts = [dated[min(len(dated)-1, len(dated)*q//4)] if dated else None for q in (1,2,3)]
    def date_band(row):
        if row.launch_timestamp is None: return "UNKNOWN"
        return str(sum(row.launch_timestamp > cut for cut in cuts if cut is not None))
    def key(row):
        return (date_band(row), row.reason, row.provider_source,
                "WATCHTOWER" if row.watchtower_population else "NON_WATCHTOWER",
                "RUNTIME_READY" if row.launch_fact_present else "NOT_RUNTIME_READY",
                row.discovery_participation)
    buckets: dict[tuple, deque[LaunchCoverage]] = defaultdict(deque)
    for row in sorted(candidates, key=lambda r: hashlib.sha256(r.mint.encode()).hexdigest()):
        buckets[key(row)].append(row)
    selected: list[AcquisitionTarget] = []
    selected_mints: set[str] = set(); creators: set[str] = set(); represented: set[tuple] = set()
    def dependencies(row):
        return [item for item in targets([row])]
    for creator_pass in (True, False):
        progress = True
        while progress:
            progress = False
            for stratum in sorted(buckets, key=str):
                bucket = buckets[stratum]
                if not bucket: continue
                chosen = None
                for _ in range(len(bucket)):
                    row = bucket.popleft()
                    if creator_pass and row.creator and row.creator in creators:
                        bucket.append(row); continue
                    chosen = row; break
                if chosen is None: continue
                deps = dependencies(chosen)
                if len(selected) + len(deps) > call_limit:
                    bucket.append(chosen); continue
                selected.extend(deps); selected_mints.add(chosen.mint); represented.add(stratum)
                if chosen.creator: creators.add(chosen.creator)
                progress = True
                if len(selected) == call_limit: break
            if len(selected) == call_limit: break
        if len(selected) == call_limit: break
    return selected, {"method": "DETERMINISTIC_STRATIFIED_LAUNCH_DEPENDENCY_GROUPS_V1",
                      "call_limit": call_limit, "selected_calls": len(selected),
                      "selected_launches": len(selected_mints), "distinct_creators": len(creators),
                      "represented_strata": len(represented),
                      "dimensions": ["launch_date_quartile", "missing_dependency", "provider_source",
                                     "watchtower_population", "runtime_ready_state", "discovery_participation"],
                      "discovery_participation_limitation": "Occurrence-level membership is absent from the immutable summary snapshot."}


async def execute_bounded(
    acquisition_targets: Iterable[AcquisitionTarget],
    *,
    hard_call_limit: int,
    fetch: Callable[[AcquisitionTarget], Awaitable[str]],
    concurrency: int = 4,
) -> dict:
    """Execute an explicit budget using a supplied shared-acquisition callback."""
    planned = list(acquisition_targets)
    if hard_call_limit < 0 or len(planned) > hard_call_limit:
        raise CoverageBudgetExceeded(
            f"planned calls {len(planned)} exceed hard limit {hard_call_limit}"
        )
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def one(item: AcquisitionTarget) -> tuple[str, str]:
        async with semaphore:
            return item.signature, await fetch(item)

    outcomes = await asyncio.gather(*(one(item) for item in planned))
    states: dict[str, int] = {}
    for _, state in outcomes:
        states[state] = states.get(state, 0) + 1
    return {"planned_calls": len(planned), "executed_calls": len(outcomes),
            "unique_signatures": len({item.signature for item in planned}),
            "outcomes": states, "hard_call_limit": hard_call_limit}
