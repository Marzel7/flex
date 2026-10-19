"""EB1.2A pure non-executable requirement review dispositions."""
from dataclasses import asdict,dataclass
from hashlib import sha256
import json,re
from typing import Iterable,Optional,Tuple
from .evidence_gap_requirement import EvidenceGapRequirement
CONTRACT_VERSION="eb1.2a.v1";AUTHORITY="NON_EXECUTABLE_REVIEW_DISPOSITION"
DISPOSITIONS={"ACKNOWLEDGED","DEFERRED","REJECTED_AS_INVALID","READY_FOR_SEPARATE_PLANNING"}
FORBIDDEN=("http://","https://","rpc","gmgn","helius","api_key","credential","curl ","python ","restart","deploy","production","wallet","creator","operator","rank","score","profit","cashflow","activate","budget","request_count","endpoint")
DIGEST=re.compile(r"^[0-9a-f]{64}$")
class RequirementReviewDispositionError(ValueError):pass
@dataclass(frozen=True)
class RequirementReviewDisposition:
 requirement_id:str;requirement_projection_digest:str;requirement_manifest_digest:str;requirement_corpus_digest:str;upstream_stage:str;authority_lane:str;cohort_or_window_identity:str;disposition:str;reviewer_identity_token:str;review_sequence:int;reason_code:str;rationale_digest:str;supersedes_disposition_id:Optional[str];authority_class:str;grants_execution_authority:bool;disposition_id:str
@dataclass(frozen=True)
class RequirementReviewHistory:
 contract_version:str;disposition_count:int;disposition_counts:dict;dispositions:Tuple[RequirementReviewDisposition,...];history_digest:str
def _d(v):return sha256(json.dumps(v,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
def _text(v,name):
 if not isinstance(v,str) or not v.strip():raise RequirementReviewDispositionError(f"EB1_2A_INVALID_{name.upper()}")
 if any(x in v.lower() for x in FORBIDDEN):raise RequirementReviewDispositionError("EB1_2A_EXECUTABLE_OR_FORBIDDEN_CONTENT")
 return v.strip()
def project_requirement_review_history(records:Iterable[dict],requirements:Iterable[EvidenceGapRequirement]):
 req={x.requirement_id:x for x in requirements};out=[];seen={};last_seq={}
 for r in sorted(records,key=lambda x:x.get("review_sequence",-1)):
  expected={"requirement_id","requirement_projection_digest","requirement_manifest_digest","requirement_corpus_digest","disposition","reviewer_identity_token","review_sequence","reason_code","rationale_digest","supersedes_disposition_id"}
  if not isinstance(r,dict) or set(r)!=expected:raise RequirementReviewDispositionError("EB1_2A_SCHEMA_DRIFT")
  rid=_text(r["requirement_id"],"requirement_id")
  if rid not in req:raise RequirementReviewDispositionError("EB1_2A_UNKNOWN_REQUIREMENT")
  q=req[rid];disp=_text(r["disposition"],"disposition")
  if disp not in DISPOSITIONS:raise RequirementReviewDispositionError("EB1_2A_UNKNOWN_DISPOSITION")
  reviewer=_text(r["reviewer_identity_token"],"reviewer_identity_token");reason=_text(r["reason_code"],"reason_code")
  seq=r["review_sequence"]
  if isinstance(seq,bool) or not isinstance(seq,int) or seq<0 or seq<=last_seq.get(rid,-1):raise RequirementReviewDispositionError("EB1_2A_INVALID_REVIEW_SEQUENCE")
  digests=[r[x] for x in ("requirement_projection_digest","requirement_manifest_digest","requirement_corpus_digest","rationale_digest")]
  if any(not isinstance(x,str) or not DIGEST.fullmatch(x) for x in digests):raise RequirementReviewDispositionError("EB1_2A_INVALID_DIGEST")
  supersedes=r["supersedes_disposition_id"]
  if rid in last_seq:
   if not isinstance(supersedes,str) or supersedes not in seen or seen[supersedes].requirement_id!=rid:raise RequirementReviewDispositionError("EB1_2A_INVALID_SUPERSESSION")
  elif supersedes is not None:raise RequirementReviewDispositionError("EB1_2A_UNUSED_SUPERSESSION")
  body={"contract_version":CONTRACT_VERSION,"requirement_id":rid,"requirement_projection_digest":digests[0],"requirement_manifest_digest":digests[1],"requirement_corpus_digest":digests[2],"upstream_stage":q.upstream_stage,"authority_lane":q.authority_lane,"cohort_or_window_identity":q.cohort_or_window_identity,"disposition":disp,"reviewer_identity_token":reviewer,"review_sequence":seq,"reason_code":reason,"rationale_digest":digests[3],"supersedes_disposition_id":supersedes,"authority_class":AUTHORITY,"grants_execution_authority":False}
  x=RequirementReviewDisposition(**{k:v for k,v in body.items() if k!="contract_version"},disposition_id=_d(body));out.append(x);seen[x.disposition_id]=x;last_seq[rid]=seq
 ordered=tuple(sorted(out,key=lambda x:(x.requirement_id,x.review_sequence,x.disposition_id)));counts={k:sum(x.disposition==k for x in ordered) for k in sorted(DISPOSITIONS) if any(x.disposition==k for x in ordered)};body={"contract_version":CONTRACT_VERSION,"disposition_count":len(ordered),"disposition_counts":counts,"dispositions":[asdict(x) for x in ordered]}
 return RequirementReviewHistory(CONTRACT_VERSION,len(ordered),counts,ordered,_d(body))
def verify_requirement_review_history(h,records,requirements):
 if project_requirement_review_history(records,requirements)!=h:raise RequirementReviewDispositionError("EB1_2A_REPLAY_MISMATCH")
 return True
