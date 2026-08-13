from dataclasses import replace
import json
from pathlib import Path

import pytest

from src.evidence.contracts.operational_family_adapters import adapt_normalized_operation_runtime
from src.evidence.contracts.operational_family_manifest import (
    OperationalFamilyManifestError,
    build_operational_family_manifest,
    verify_operational_family_manifest,
)
from src.evidence.contracts.operational_family_nomination import nominate_operational_family


def _material():
    first = json.loads((Path(__file__).parent / "fixtures/eb0_4c_normalized_operation_runtime.json").read_text())
    second = dict(first)
    second.update(
        operation_id="operation-beta",
        module_id="secondary_behaviour",
        topology_revision_id="topology-beta",
        behaviour_observation_id="behaviour-beta",
        input_digest="input-beta",
    )
    rows = (first, second)
    facts = tuple(f for row in rows for f in adapt_normalized_operation_runtime(row))
    nomination = nominate_operational_family(facts, nomination_state="SUPPORTED")
    return facts, (nomination,)


def test_manifest_is_order_independent_and_exactly_replayable():
    facts, nominations = _material()
    forward = build_operational_family_manifest(facts, nominations)
    reverse = build_operational_family_manifest(reversed(facts), nominations)
    assert forward == reverse
    assert verify_operational_family_manifest(forward, facts, nominations)
    assert forward.operation_count == 2
    assert forward.nomination_state_counts == {"SUPPORTED": 1}


def test_empty_duplicate_and_missing_support_fail_closed():
    facts, nominations = _material()
    with pytest.raises(OperationalFamilyManifestError, match="EMPTY_FACTS"):
        build_operational_family_manifest([], nominations)
    with pytest.raises(OperationalFamilyManifestError, match="DUPLICATE_FACT"):
        build_operational_family_manifest((*facts, facts[0]), nominations)
    with pytest.raises(OperationalFamilyManifestError, match="MISSING_SUPPORTING_FACT"):
        build_operational_family_manifest(facts[:1], nominations)


def test_tampering_and_version_promotion_fail_replay():
    facts, nominations = _material()
    manifest = build_operational_family_manifest(facts, nominations)
    with pytest.raises(OperationalFamilyManifestError, match="REPLAY_MISMATCH"):
        verify_operational_family_manifest(replace(manifest, manifest_digest="bad"), facts, nominations)
    with pytest.raises(OperationalFamilyManifestError, match="VERSION_MISMATCH"):
        verify_operational_family_manifest(replace(manifest, adapter_version="bad"), facts, nominations)
    with pytest.raises(OperationalFamilyManifestError, match="NONCANONICAL_NOMINATION"):
        build_operational_family_manifest(facts, [replace(nominations[0], operator_identity_asserted=True)])
