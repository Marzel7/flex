"""X69.4 additive reconciliation API metadata acceptance tests."""
from __future__ import annotations

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import time

import pytest

from src.ops.emerging_operator_service import EmergingOperatorService
from src.ops.operation_attribution import (
    OperationAttributionService,
    _assignment,
    clear_operation_attribution_cache,
    unknown_assignment,
)
from src.ops.reconciliation_metadata import (
    RECONCILIATION_SCHEMA_VERSION,
    build_reconciliation_metadata,
)


ROOT = Path(__file__).resolve().parents[1]
OPS_DB = str(ROOT / "database" / "wt_ops_v2.db")
LIVE_DB = str(ROOT / "database" / "flex_complete_database.db")
PUBLIC_KEYS = {
    "schema_version", "population_revision_id", "reconciliation_package_id",
    "disposition", "reasoning_summary", "supporting_evidence_count",
    "contradictory_evidence_count", "missing_evidence_count",
    "dependency_groups", "deterministic_result_id",
    "legacy_shadow_agreement", "expected_difference",
    "supporting_evidence", "contradictory_evidence", "missing_evidence",
    "why_population_exists", "analyst_explanation", "promotion_readiness",
}


@pytest.fixture(scope="module")
def live_projection():
    registry = EmergingOperatorService(OPS_DB, LIVE_DB)
    families = registry._compose()
    metadata = build_reconciliation_metadata(registry, families)
    return families, metadata


def _family(live_projection, fragment):
    return next(
        family for family in live_projection[0]
        if fragment in family.get("family_name", "")
    )


def test_schema_is_versioned_minimal_and_immutable(live_projection):
    _, metadata = live_projection
    assert metadata
    for value in metadata.values():
        assert set(value) == PUBLIC_KEYS
        assert value["schema_version"] == RECONCILIATION_SCHEMA_VERSION
        assert value["population_revision_id"].startswith("ipr:")
        assert value["reconciliation_package_id"].startswith("erp:")
        assert value["deterministic_result_id"].startswith("dr:")
        assert isinstance(value["dependency_groups"], list)
        assert "reasoning_chain" not in value
        assert "evidence" not in value


def test_required_live_reconciliation_results(live_projection):
    _, metadata = live_projection
    expected = {
        "WATCHTOWER": ("CONFIRMED_OPERATION", True, False),
        "B48k": ("UNRESOLVED", False, True),
        "C7Ha": ("REVIEW", False, True),
    }
    for name, verdict in expected.items():
        family = _family(live_projection, name)
        value = metadata[family["family_id"]]
        assert (
            value["disposition"], value["legacy_shadow_agreement"],
            value["expected_difference"],
        ) == verdict


def test_every_legacy_assignment_field_is_byte_compatible(live_projection):
    families, metadata = live_projection
    for family in families:
        before = _assignment(family)
        after = _assignment(family, metadata.get(family["family_id"]))
        reconciliation = after.pop("reconciliation", None)
        assert after == before
        if family["family_id"] in metadata:
            assert reconciliation == metadata[family["family_id"]]
        else:
            assert reconciliation is None
    assert "reconciliation" not in unknown_assignment()


def test_shared_attribution_service_adds_metadata_without_changing_identity(
    monkeypatch, live_projection
):
    families, metadata = live_projection
    selected = [
        _family(live_projection, "WATCHTOWER"),
        _family(live_projection, "B48k"),
        _family(live_projection, "C7Ha"),
    ]
    service = OperationAttributionService("missing-ops", "missing-live")
    monkeypatch.setattr(service.registry, "_compose", lambda: selected)
    monkeypatch.setattr(
        "src.ops.reconciliation_metadata.build_reconciliation_metadata",
        lambda registry, families: {
            family["family_id"]: metadata[family["family_id"]] for family in selected
        },
    )
    clear_operation_attribution_cache()
    for family in selected:
        entity = (family.get("member_wallets") or [family["family_id"]])[0]
        result = service.resolve_entity(entity)
        legacy = _assignment(family)
        reconciliation = result.pop("reconciliation")
        assert result == legacy
        assert reconciliation == metadata[family["family_id"]]


def test_metadata_failure_is_omitted_and_never_changes_attribution(monkeypatch):
    family = {
        "family_id": "family:test", "family_name": "Test", "stage": "EMERGING",
        "launch_list": ["MINT"], "member_wallets": ["WALLET"],
        "evidence_completeness": {"score": 80},
    }
    service = OperationAttributionService("missing-ops", "missing-live")
    monkeypatch.setattr(service.registry, "_compose", lambda: [family])
    monkeypatch.setattr(
        "src.ops.reconciliation_metadata.build_reconciliation_metadata",
        lambda registry, families: (_ for _ in ()).throw(RuntimeError("unavailable")),
    )
    clear_operation_attribution_cache()
    result = service.resolve_operation_for_token("MINT")
    assert result == _assignment(family)
    assert "reconciliation" not in result


def test_search_exposes_same_optional_child(monkeypatch, live_projection):
    family = _family(live_projection, "C7Ha")
    metadata = live_projection[1][family["family_id"]]
    service = OperationAttributionService("missing-ops", "missing-live")
    monkeypatch.setattr(service.registry, "_compose", lambda: [family])
    monkeypatch.setattr(
        "src.ops.reconciliation_metadata.build_reconciliation_metadata",
        lambda registry, families: {family["family_id"]: metadata},
    )
    result = service.search("C7Ha")[0]
    assert result["operation_attribution"]["reconciliation"] == metadata
    legacy = _assignment(family)
    enriched = dict(result["operation_attribution"])
    enriched.pop("reconciliation")
    assert enriched == legacy


def test_response_metadata_does_not_expose_cached_mutable_state(
    monkeypatch, live_projection
):
    family = _family(live_projection, "C7Ha")
    metadata = live_projection[1][family["family_id"]]
    service = OperationAttributionService("missing-ops", "missing-live")
    monkeypatch.setattr(service.registry, "_compose", lambda: [family])
    monkeypatch.setattr(
        "src.ops.reconciliation_metadata.build_reconciliation_metadata",
        lambda registry, families: {family["family_id"]: metadata},
    )
    clear_operation_attribution_cache()
    first = service.resolve_entity(family["family_id"])
    first["reconciliation"]["disposition"] = "MUTATED_BY_CALLER"
    first["reconciliation"]["dependency_groups"].append("MUTATED")
    second = service.resolve_entity(family["family_id"])
    assert second["reconciliation"]["disposition"] == "REVIEW"
    assert "MUTATED" not in second["reconciliation"]["dependency_groups"]


def test_cold_metadata_build_is_single_flight(monkeypatch):
    from src.ops import reconciliation_metadata as module

    class Registry:
        ops_db_path = "single-flight-ops"
        live_db_path = "single-flight-live"
        refresh_seconds = 60

    registry = Registry()
    calls = 0
    module.clear_reconciliation_metadata_cache(registry.ops_db_path)

    def cold_build(current, families):
        nonlocal calls
        key = (current.ops_db_path, current.live_db_path)
        cached = module._METADATA_CACHE.get(key)
        if cached:
            return cached[1]
        calls += 1
        time.sleep(0.05)
        value = {"family:test": {"disposition": "UNRESOLVED"}}
        module._METADATA_CACHE[key] = (time.monotonic(), value)
        return value

    monkeypatch.setattr(module, "_build_reconciliation_metadata_uncached", cold_build)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(
            lambda _: module.build_reconciliation_metadata(registry, []), range(8)
        ))
    assert calls == 1
    assert all(result == results[0] for result in results)
    module.clear_reconciliation_metadata_cache(registry.ops_db_path)
