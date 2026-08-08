import asyncio

import pytest

from src.intelligence.migrated_coverage_acquisition import (
    AcquisitionTarget, CoverageBudgetExceeded, execute_bounded, representative_sample,
)
from src.intelligence.migrated_coverage import LaunchCoverage


def test_executor_is_bounded_and_missing_only():
    seen = []
    async def fetch(item):
        seen.append(item.signature)
        return "MIRRORED"
    items = [AcquisitionTarget("a", "mint-a", "eligible_migrated_creation"),
             AcquisitionTarget("b", "mint-b", "eligible_migrated_migration")]
    result = asyncio.run(execute_bounded(items, hard_call_limit=2, fetch=fetch))
    assert result["executed_calls"] == 2
    assert result["unique_signatures"] == 2
    assert sorted(seen) == ["a", "b"]


def test_executor_refuses_to_cross_budget_before_fetch():
    called = False
    async def fetch(_item):
        nonlocal called
        called = True
        return "MIRRORED"
    with pytest.raises(CoverageBudgetExceeded):
        asyncio.run(execute_bounded(
            [AcquisitionTarget("a", "mint", "eligible_migrated_creation")],
            hard_call_limit=0, fetch=fetch,
        ))
    assert called is False


def test_representative_sample_keeps_launch_dependencies_together():
    rows = [LaunchCoverage(f"mint-{i}", "PENDING", "MISSING_CREATION_AND_MIGRATION_TRANSACTION",
            f"c-{i}", f"m-{i}", False, False, False, "BOUNDED_ACQUISITION",
            f"creator-{i%3}", i, "rpc" if i%2 else "webhook", bool(i%2)) for i in range(10)]
    sample, method = representative_sample(rows, call_limit=10)
    by_mint = {}
    for item in sample: by_mint.setdefault(item.launch, []).append(item)
    assert len(sample) == 10
    assert all(len(items) == 2 for items in by_mint.values())
    assert method["selected_launches"] == 5
    assert method["represented_strata"] > 1
