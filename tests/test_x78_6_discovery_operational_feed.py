from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DISCOVERY = (ROOT / "templates/discovery.html").read_text()
CONVERGENCE = (ROOT / "src/discovery/operation_convergence.py").read_text()
REGISTRY = (ROOT / "templates/operators_index.html").read_text()


def test_discovery_is_explicitly_a_recent_change_feed():
    assert "Operational Feed · Last 24 hours" in DISCOVERY
    assert "What changed?" in DISCOVERY
    assert "Persistent operational state remains in the Operations Registry" in DISCOVERY
    assert 'activity_cutoff = now - 24 * 60 * 60' in CONVERGENCE
    assert '"question": "What changed?"' in CONVERGENCE


def test_recovered_operation_card_is_lightweight():
    renderer = DISCOVERY.split("function convergenceOperationCard", 1)[1].split(
        "function convergenceExpansionCard", 1
    )[0]
    assert "Recovered today" in renderer
    assert "Last activity" in renderer
    assert "Open Operator →" in renderer
    assert "Exact topology matches" not in renderer
    assert "Recently expanded" not in renderer
    assert "Mission Control" not in renderer


def test_potential_new_operations_are_bounded_and_progressive():
    assert "Potential New Operations" in DISCOVERY
    assert "newOperations.slice(0,5)" in DISCOVERY
    assert "newOperations.slice(5)" in DISCOVERY
    assert "View remaining · " in DISCOVERY
    renderer = DISCOVERY.split("function convergenceInvestigationCard", 1)[1].split(
        "// X75.2", 1
    )[0]
    assert "supporting launches" in renderer
    assert "Open Investigation →" in renderer
    assert "investigation_trigger" not in renderer
    assert "Members" not in renderer


def test_registry_keeps_persistent_state_sections():
    for label in ("Confirmed Operations", "Investigation Populations", "Analyst Queue",
                  "Infrastructure", "Dismissed", "Dormant", "Merged", "Split",
                  "Recent Investigation Activity"):
        assert label in REGISTRY
