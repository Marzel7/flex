"""EB1.1A pure non-executable evidence-gap requirement contract."""
from dataclasses import asdict,dataclass
from hashlib import sha256
import json
from typing import Iterable,Tuple
from .cross_stage_eligibility import CrossStageEligibilityProjection,StageEligibility,CONTRACT_VERSION as INPUT_VERSION
CONTRACT_VERSION="eb1.1a.v1";AUTHORITY="NON_EXECUTABLE_EVIDENCE_REQUIREMENT"
KINDS={"MISSING_EVIDENCE","COMPLETENESS_EVIDENCE","CONFLICT_RESOLUTION_EVIDENCE"}
class EvidenceGapRequirementError(ValueError):pass
@dataclass(frozen=True)
class EvidenceGapRequirement:
 requirement_kind:str;authority_class:str;upstream_stage:str;authority_lane:str;bundle_digest:str;cohort_or_window_identity:str;eligibility_id:str;eligibility_state:str;total_count:int;observed_count:int;missing_count:int;conflicting_count:int;reason_codes:Tuple[str,...];provenance_digest:str;requirement_id:str
@dataclass(frozen=True)
class EvidenceGapRequirementProjection:
 contract_version:str;input_contract_version:str;input_projection_digest:str;requirement_count:int;requirements:Tuple[EvidenceGapRequirement,...];projection_digest:str
def _d(v):return sha256(json.dumps(v,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
def project_evidence_gap_requirements(p:CrossStageEligibilityProjection):
 if p.contract_version!=INPUT_VERSION:raise EvidenceGapRequirementError("EB1_1A_INPUT_VERSION_MISMATCH")
 req=[]
 for s in p.stages:
  kinds=[]
  if s.eligibility_state in {"ELIGIBLE","NOT_APPLICABLE"}:continue
  if s.eligibility_state=="INELIGIBLE_CONFLICTING":kinds=["CONFLICT_RESOLUTION_EVIDENCE"]
  elif s.eligibility_state=="INELIGIBLE_MISSING":
   if s.missing_count or s.observed_count==0:kinds.append("MISSING_EVIDENCE")
   if s.completeness_state!="COMPLETE":kinds.append("COMPLETENESS_EVIDENCE")
  else:raise EvidenceGapRequirementError("EB1_1A_UNKNOWN_ELIGIBILITY_STATE")
  for k in sorted(set(kinds)):
   body={"contract_version":CONTRACT_VERSION,"requirement_kind":k,"authority_class":AUTHORITY,"upstream_stage":s.upstream_stage,"authority_lane":s.authority_lane,"bundle_digest":s.bundle_digest,"cohort_or_window_identity":s.cohort_or_window_identity,"eligibility_id":s.eligibility_id,"eligibility_state":s.eligibility_state,"total_count":s.total_count,"observed_count":s.observed_count,"missing_count":s.missing_count,"conflicting_count":s.conflicting_count,"reason_codes":s.reason_codes,"provenance_digest":s.provenance_digest}
   req.append(EvidenceGapRequirement(**{x:body[x] for x in body if x!="contract_version"},requirement_id=_d(body)))
 ordered=tuple(sorted(req,key=lambda x:x.requirement_id));body={"contract_version":CONTRACT_VERSION,"input_contract_version":INPUT_VERSION,"input_projection_digest":p.projection_digest,"requirement_count":len(ordered),"requirements":[asdict(x) for x in ordered]}
 return EvidenceGapRequirementProjection(CONTRACT_VERSION,INPUT_VERSION,p.projection_digest,len(ordered),ordered,_d(body))
def verify_evidence_gap_requirements(result,p):
 if project_evidence_gap_requirements(p)!=result:raise EvidenceGapRequirementError("EB1_1A_REPLAY_MISMATCH")
 return True
