from dataclasses import replace
import json
from pathlib import Path
import pytest
from src.evidence.contracts.cross_stage_eligibility import project_cross_stage_eligibility
from src.evidence.contracts.cross_stage_eligibility_manifest import build_cross_stage_eligibility_manifest
from src.evidence.contracts.evidence_gap_requirement_adapters import *
from src.evidence.contracts.evidence_gap_requirement_manifest import *
from src.evidence.contracts.evidence_gap_requirement_corpus import *
F=Path(__file__).parent/'fixtures/eb1_0a_cross_stage_eligibility.json'
def _m():
 p=project_cross_stage_eligibility(json.loads(F.read_text()));return build_cross_stage_eligibility_manifest(p.stages)
def test_verified_adapter_manifest_corpus_replay():
 p=adapt_verified_eligibility_manifest(_m());m=build_evidence_gap_requirement_manifest(p);assert verify_evidence_gap_requirement_manifest(m,p)
 c=assemble_evidence_gap_requirement_corpus([m]);assert verify_evidence_gap_requirement_corpus(c,[m]);assert {x.upstream_stage for x in c.lanes}=={"EB0.1","EB0.2"}
def test_unverified_empty_and_tamper_fail():
 with pytest.raises(EvidenceGapRequirementAdapterError):adapt_verified_eligibility_manifest(replace(_m(),manifest_digest='bad'))
 with pytest.raises(EvidenceGapRequirementCorpusError):assemble_evidence_gap_requirement_corpus([])
 p=adapt_verified_eligibility_manifest(_m());m=build_evidence_gap_requirement_manifest(p)
 with pytest.raises(EvidenceGapRequirementManifestError):verify_evidence_gap_requirement_manifest(replace(m,manifest_digest='bad'),p)
