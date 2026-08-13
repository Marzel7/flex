from dataclasses import replace
import json
from pathlib import Path
import pytest
from src.evidence.contracts.cross_stage_eligibility import project_cross_stage_eligibility
from src.evidence.contracts.evidence_gap_requirement import project_evidence_gap_requirements
from src.evidence.contracts.requirement_review_disposition import *
F=Path(__file__).parent/'fixtures/eb1_0a_cross_stage_eligibility.json';D='a'*64
def _req():return project_evidence_gap_requirements(project_cross_stage_eligibility(json.loads(F.read_text()))).requirements
def _record(r,seq=0,disp='ACKNOWLEDGED',sup=None):return {"requirement_id":r.requirement_id,"requirement_projection_digest":D,"requirement_manifest_digest":D,"requirement_corpus_digest":D,"disposition":disp,"reviewer_identity_token":"reviewer-token","review_sequence":seq,"reason_code":"EVIDENCE_GAP_REVIEWED","rationale_digest":D,"supersedes_disposition_id":sup}
def test_all_dispositions_non_executable_and_replayable():
 req=_req();records=[_record(req[i],disp=d) for i,d in enumerate(sorted(DISPOSITIONS))];h=project_requirement_review_history(records,req);assert h.disposition_count==4;assert all(not x.grants_execution_authority for x in h.dispositions);assert verify_requirement_review_history(h,records,req)
def test_valid_append_only_supersession_chain():
 r=_req()[0];first=project_requirement_review_history([_record(r)],_req()).dispositions[0];records=[_record(r),_record(r,1,'READY_FOR_SEPARATE_PLANNING',first.disposition_id)];h=project_requirement_review_history(records,_req());assert h.disposition_count==2;assert h.dispositions[-1].supersedes_disposition_id==first.disposition_id
def test_missing_supersession_unknown_requirement_and_bad_sequence_fail():
 r=_req()[0]
 with pytest.raises(RequirementReviewDispositionError,match='INVALID_SUPERSESSION'):project_requirement_review_history([_record(r),_record(r,1)],_req())
 bad=_record(r);bad['requirement_id']='unknown'
 with pytest.raises(RequirementReviewDispositionError,match='UNKNOWN_REQUIREMENT'):project_requirement_review_history([bad],_req())
def test_executable_provider_budget_identity_and_action_content_rejected():
 r=_req()[0]
 for field,value in [('reason_code','call Helius RPC'),('reviewer_identity_token','operator wallet'),('reason_code','budget 20 requests'),('reason_code','deploy production')]:
  bad=_record(r);bad[field]=value
  with pytest.raises(RequirementReviewDispositionError,match='FORBIDDEN'):project_requirement_review_history([bad],_req())
def test_tamper_fails_replay():
 req=_req();records=[_record(req[0])];h=project_requirement_review_history(records,req)
 with pytest.raises(RequirementReviewDispositionError,match='REPLAY_MISMATCH'):verify_requirement_review_history(replace(h,history_digest='bad'),records,req)
