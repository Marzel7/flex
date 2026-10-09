import json
from pathlib import Path


ARTIFACT = Path("docs/audits/dev014_watchtower_historical_research_anchor_qualification_20261009.v1.json")


def test_skipped_mints_are_observed_price_not_fabricated_entries():
    data = json.loads(ARTIFACT.read_text())
    rows = data["skipped_batch_3"]
    assert [row["rank"] for row in rows] == [22, 23, 24, 26]
    assert all(row["anchor_class"] == "OBSERVED_PRICE_ANCHOR" for row in rows)
    assert all("entry_mc_usd" not in row for row in rows)
    assert all(row["migration_boundary"].startswith("UNQUALIFIED_") for row in rows)


def test_batch_four_is_chronological_and_metric_scope_is_separate():
    data = json.loads(ARTIFACT.read_text())
    plan = data["batch_4_provider_free_plan"]
    assert [row["rank"] for row in plan] == list(range(31, 41))
    assert sum(row["anchor_class"] == "QUALIFIED_ENTRY_ANCHOR" for row in plan) == 8
    assert sum(row["anchor_class"] == "OBSERVED_PRICE_ANCHOR" for row in plan) == 2
    contract = data["batch_4_contract"]
    assert contract["entry_anchor_metrics"] == "ENTRY_RELATIVE_RESEARCH_ONLY"
    assert contract["observed_price_metrics"] == "OBSERVED_PRICE_RELATIVE_ONLY"
    assert contract["raw_provider_payload_retention"] is False
    assert contract["aggregate_max_bytes"] < 500_000_000
