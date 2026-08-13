from dataclasses import replace
import json
from pathlib import Path
import pytest
from src.evidence.contracts.cross_stage_eligibility import project_cross_stage_eligibility
from src.evidence.contracts.cross_stage_eligibility_manifest import *
from src.evidence.contracts.cross_stage_eligibility_corpus import *
F=Path(__file__).parent/'fixtures/eb1_0a_cross_stage_eligibility.json'
def test_manifest_and_corpus_replay_and_order():
 p=project_cross_stage_eligibility(json.loads(F.read_text())); m=build_cross_stage_eligibility_manifest(p.stages)
 assert verify_cross_stage_eligibility_manifest(m,p.stages)
 assert build_cross_stage_eligibility_manifest(reversed(p.stages))==m
 c=assemble_cross_stage_eligibility_corpus([m]); assert verify_cross_stage_eligibility_corpus(c,[m]); assert len(c.stages)==4
def test_tamper_and_empty_fail_closed():
 p=project_cross_stage_eligibility(json.loads(F.read_text()));m=build_cross_stage_eligibility_manifest(p.stages)
 with pytest.raises(CrossStageEligibilityManifestError): verify_cross_stage_eligibility_manifest(replace(m,manifest_digest='bad'),p.stages)
 with pytest.raises(CrossStageEligibilityCorpusError): assemble_cross_stage_eligibility_corpus([])
 with pytest.raises(CrossStageEligibilityCorpusError): assemble_cross_stage_eligibility_corpus([replace(m,manifest_digest='bad')])
