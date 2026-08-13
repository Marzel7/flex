from dataclasses import replace
import json
from pathlib import Path

import pytest

from src.evidence.contracts.operational_family_adapters import adapt_normalized_operation_runtime
from src.evidence.contracts.operational_family_corpus import (
    OperationalFamilyCorpusError, assemble_operational_family_corpora,
    verify_operational_family_corpora,
)
from src.evidence.contracts.operational_family_manifest import build_operational_family_manifest
from src.evidence.contracts.operational_family_nomination import nominate_operational_family


def _manifest():
    base = json.loads((Path(__file__).parent / "fixtures/eb0_4c_normalized_operation_runtime.json").read_text())
    second = dict(base); second.update(operation_id="operation-beta", module_id="secondary", topology_revision_id="t2", behaviour_observation_id="b2", input_digest="i2")
    facts = tuple(f for row in (base, second) for f in adapt_normalized_operation_runtime(row))
    nominations = (nominate_operational_family(facts, nomination_state="SUPPORTED"),)
    return build_operational_family_manifest(facts, nominations)


def test_per_role_corpus_preserves_evidence_and_replays():
    manifest = _manifest()
    corpora = assemble_operational_family_corpora([manifest])
    assert len(corpora) == 1
    assert corpora[0].operation_count == 2
    assert corpora[0].nomination_state_counts == {"SUPPORTED": 1}
    assert verify_operational_family_corpora(corpora, [manifest])


def test_order_independent_deduplication_and_tamper_detection():
    manifest = _manifest()
    assert assemble_operational_family_corpora([manifest, manifest]) == assemble_operational_family_corpora([manifest])
    corpus = assemble_operational_family_corpora([manifest])[0]
    with pytest.raises(OperationalFamilyCorpusError, match="REPLAY_MISMATCH"):
        verify_operational_family_corpora([replace(corpus, corpus_digest="bad")], [manifest])


def test_empty_and_unverified_manifests_fail_closed():
    with pytest.raises(OperationalFamilyCorpusError, match="EMPTY_INPUT"):
        assemble_operational_family_corpora([])
    manifest = _manifest()
    with pytest.raises(OperationalFamilyCorpusError, match="UNVERIFIED_MANIFEST"):
        assemble_operational_family_corpora([replace(manifest, manifest_digest="bad")])
