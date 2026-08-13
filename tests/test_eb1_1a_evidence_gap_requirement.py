from dataclasses import replace
import json
from pathlib import Path
import pytest
from src.evidence.contracts.cross_stage_eligibility import project_cross_stage_eligibility
from src.evidence.contracts.evidence_gap_requirement import *
F=Path(__file__).parent/'fixtures/eb1_0a_cross_stage_eligibility.json'
def _p():return project_cross_stage_eligibility(json.loads(F.read_text()))
def test_only_ineligible_lanes_emit_non_executable_requirements():
 r=project_evidence_gap_requirements(_p());assert r.requirement_count==4;assert {x.upstream_stage for x in r.requirements}=={"EB0.1","EB0.2"};assert {x.authority_class for x in r.requirements}=={AUTHORITY};assert {x.requirement_kind for x in r.requirements}=={"MISSING_EVIDENCE","COMPLETENESS_EVIDENCE"};assert verify_evidence_gap_requirements(r,_p())
def test_conflict_emits_resolution_evidence_without_choice():
 p=_p();st=list(p.stages);i=next(i for i,x in enumerate(st) if x.upstream_stage=="EB0.4");st[i]=replace(st[i],eligibility_state="INELIGIBLE_CONFLICTING",conflicting_count=1,reason_codes=("CONFLICTING_EVIDENCE",));p=replace(p,stages=tuple(st));r=project_evidence_gap_requirements(p);x=next(x for x in r.requirements if x.upstream_stage=="EB0.4");assert x.requirement_kind=="CONFLICT_RESOLUTION_EVIDENCE"
def test_order_and_replay_tamper():
 p=_p();assert project_evidence_gap_requirements(replace(p,stages=tuple(reversed(p.stages))))==project_evidence_gap_requirements(p)
 with pytest.raises(EvidenceGapRequirementError):verify_evidence_gap_requirements(replace(project_evidence_gap_requirements(p),projection_digest="bad"),p)
