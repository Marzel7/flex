from pathlib import Path

from src.ops.emerging_operator_service import EmergingOperatorService

from tests.test_x73_8a_infrastructure_launch_membership import _databases


ROOT = Path(__file__).resolve().parents[1]


def test_participants_derive_only_from_selected_launch_membership(tmp_path):
    ops, live = _databases(tmp_path)
    service = EmergingOperatorService(str(ops), str(live))
    with service._connect(str(ops)) as conn:
        profiles = service._discovery_profiles(conn, service._tables(conn))
    profile = profiles["INFRA"]
    assert profile["launches"] == {"MINT_A", "MINT_B"}
    assert profile["creators"] == {"CREATOR_A", "CREATOR_B"}
    assert profile["provisioning_clients"] == {"CLIENT_A", "CLIENT_B"}
    assert profile["treasuries"] == {"INFRA"}
    assert "CREATOR_C" not in profile["creators"]
    assert "CLIENT_C" not in profile["provisioning_clients"]
    assert "OTHER_TREASURY" not in profile["treasuries"]


def test_population_projects_distinct_participants_with_launches(tmp_path):
    ops, live = _databases(tmp_path)
    service = EmergingOperatorService(str(ops), str(live))
    with service._connect(str(ops)) as conn:
        profiles = service._discovery_profiles(conn, service._tables(conn))
    population = service._population_builder().build_group([profiles["INFRA"]])
    family = service._legacy_adapter(None, []).project(population)
    assert family["launches"] == len(family["launch_list"]) == 2
    assert family["unique_creators"] == ["CREATOR_A", "CREATOR_B"]
    assert family["provisioning_clients"] == ["CLIENT_A", "CLIENT_B"]
    assert family["client_wallets"] == family["provisioning_clients"]
    assert family["treasuries"] == ["INFRA"]


def test_participant_terminology_is_explicit_and_consistent():
    profile = (ROOT / "templates/operation_profile.html").read_text(encoding="utf-8")
    registry = (ROOT / "templates/operators_index.html").read_text(encoding="utf-8")
    assert "Provisioning clients" in profile
    assert "provisioning clients" in registry
    assert '<span class="rp-label">Clients</span>' not in profile


def test_no_participant_scalar_hint_or_decision_layer_change():
    population = (ROOT / "src/ops/investigation_population.py").read_text(encoding="utf-8")
    service = (ROOT / "src/ops/emerging_operator_service.py").read_text(encoding="utf-8")
    assert '"provisioning_clients": provisioning_clients' in population
    assert 'p["provisioning_clients"].add' in service
    assert "distinct_creators" not in population
    assert "distinct_subproviders" not in population
    for relative in (
        "src/ops/operation_attribution.py", "src/ops/disposition_resolver.py",
        "src/ops/evidence_reconciliation.py", "src/ops/promotion_service.py",
        "src/ops/operator_resolver.py",
    ):
        assert "X73.8B" not in (ROOT / relative).read_text(encoding="utf-8")
