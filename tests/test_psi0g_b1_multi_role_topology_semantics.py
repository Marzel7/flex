from types import SimpleNamespace

import pytest

from src.evidence.operation_contracts.formalization import TopologyEdge, TopologyNode, TopologyRevision
from src.evidence.operation_contracts.runtime import OperationRuntime


DIGEST = "a" * 64
CONTRACT = {
    "contract_version": "1.0.0",
    "topology_contract": {
        "topology_version": "1.0.0",
        "local_roles": ["SOURCE", "DESTINATION"],
        "edge_rules": [{
            "source_role": "SOURCE", "destination_role": "DESTINATION",
            "primitive_type": "SYSTEM_TRANSFER",
        }],
    },
}
SNAPSHOT = SimpleNamespace(
    contract_id="fixture.operation", contract_version="1.0.0", subjects=("subject",)
)


def node(entity, role):
    return TopologyNode(entity, role, "fixture.operation", "1.0.0", (), (DIGEST,))


def edge(source, destination):
    return TopologyEdge(source, destination, "SYSTEM_TRANSFER", "ONE_TO_MANY",
        None, True, (), (DIGEST,))


def topology(nodes, edges):
    return TopologyRevision.create(contract_id="fixture.operation", contract_version="1.0.0",
        topology_version="1.0.0", subjects=("subject",), nodes=nodes, edges=edges,
        behaviour_observation_refs=(), input_digest=DIGEST, generated_at=1)


def validate(value):
    OperationRuntime._validate_topology(value, CONTRACT, SNAPSHOT, (), (DIGEST,), ())


def test_local_roles_are_node_scoped_and_multi_role_entity_is_valid():
    value = topology(
        (node("a", "SOURCE"), node("b", "DESTINATION"),
         node("b", "SOURCE"), node("c", "DESTINATION")),
        (edge("a", "b"), edge("b", "c")),
    )
    validate(value)
    assert [(item.entity_ref, item.local_role) for item in value.nodes] == [
        ("a", "SOURCE"), ("b", "DESTINATION"), ("b", "SOURCE"),
        ("c", "DESTINATION"),
    ]


def test_edge_still_requires_one_declared_source_destination_role_pair():
    value = topology((node("a", "SOURCE"), node("b", "DESTINATION")), (edge("b", "a"),))
    with pytest.raises(ValueError, match="edge is not permitted"):
        validate(value)


def test_edge_endpoint_without_a_node_still_fails_closed():
    value = topology((node("a", "SOURCE"),), (edge("a", "missing"),))
    with pytest.raises(ValueError, match="edge is not permitted"):
        validate(value)


def test_undeclared_role_still_fails_closed():
    value = topology((node("a", "SOURCE"), node("b", "OTHER")), (edge("a", "b"),))
    with pytest.raises(ValueError, match="undeclared local role"):
        validate(value)
