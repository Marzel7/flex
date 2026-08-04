from src.ops.operation_attribution import OperationAttributionService, REGISTRY_VERSION


def _family(family_id, name, stage, mints, wallets=()):
    return {
        "family_id": family_id, "family_name": name, "stage": stage,
        "launch_list": list(mints), "member_wallets": list(wallets),
        "evidence_completeness": {"score": 80},
    }


def test_shared_resolver_returns_uniform_registry_contract(monkeypatch):
    service = OperationAttributionService("ops", "live")
    monkeypatch.setattr(service.registry, "_compose", lambda: [
        _family("family:b48", "B48k/Dv34", "EMERGING", ["MINT"], ["B48"])
    ])
    result = service.resolve_operation_for_token("MINT")
    assert result == service.resolve_entity("B48")
    assert result["operation_id"] == "family:b48"
    assert result["family_id"] == "family:b48"
    assert result["operation_name"] == "B48k/Dv34"
    assert result["lifecycle"] == "EMERGING"
    assert result["state"] == "EMERGING_OPERATION"
    assert result["evidence_source"] == "operation_registry"
    assert result["confidence"] == "HIGH"
    assert result["registry_version"] == REGISTRY_VERSION


def test_shared_resolver_enforces_exclusive_priority(monkeypatch):
    service = OperationAttributionService("ops", "live")
    monkeypatch.setattr(service.registry, "_compose", lambda: [
        _family("family:candidate", "Candidate", "CANDIDATE", ["MINT"]),
        _family("family:confirmed", "WATCHTOWER", "CONFIRMED", ["MINT"]),
    ])
    result = service.resolve_operation_for_token("MINT")
    assert result["family_id"] == "family:confirmed"
    assert result["state"] == "CONFIRMED_OPERATION"


def test_unknown_is_explicit_not_unassigned(monkeypatch):
    service = OperationAttributionService("ops", "live")
    monkeypatch.setattr(service.registry, "_compose", lambda: [])
    result = service.resolve_operation_for_token("MISSING")
    assert result["operation_name"] == "Unknown"
    assert result["state"] == "UNKNOWN"
    assert result["operation_id"] is None


def test_registry_search_is_generic(monkeypatch):
    service = OperationAttributionService("ops", "live")
    monkeypatch.setattr(service.registry, "_compose", lambda: [
        _family("family:c7", "C7Ha", "EMERGING", ["MINT"], ["TREASURY"])
    ])
    assert service.search("c7")[0]["href"] == "/intelligence/operations/family:c7"
    assert service.search("MINT")[0]["operation_attribution"]["operation_name"] == "C7Ha"
