"""EB1.0D immutable eligibility projection manifests."""
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from typing import Iterable, Tuple
from .cross_stage_eligibility import CONTRACT_VERSION, CrossStageEligibilityProjection, StageEligibility, project_cross_stage_eligibility
from .cross_stage_eligibility_adapters import ADAPTER_VERSION

SCHEMA_VERSION="eb1.0d.v1"
class CrossStageEligibilityManifestError(ValueError): pass
@dataclass(frozen=True)
class CrossStageEligibilityManifest:
 schema_version:str; contract_version:str; adapter_version:str; input_digest:str
 projection:CrossStageEligibilityProjection; manifest_digest:str
def _d(v): return sha256(json.dumps(v,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
def _records(stages:Iterable[StageEligibility]):
 result=[]
 for x in stages:
  result.append({k:v for k,v in asdict(x).items() if k not in {"eligibility_state","reason_codes","eligibility_id"}})
 return result
def build_cross_stage_eligibility_manifest(stages:Iterable[StageEligibility]):
 records=sorted(_records(stages),key=lambda x:x["upstream_stage"]); projection=project_cross_stage_eligibility(records)
 body={"schema_version":SCHEMA_VERSION,"contract_version":CONTRACT_VERSION,"adapter_version":ADAPTER_VERSION,"input_digest":_d(records),"projection":asdict(projection)}
 return CrossStageEligibilityManifest(SCHEMA_VERSION,CONTRACT_VERSION,ADAPTER_VERSION,body["input_digest"],projection,_d(body))
def verify_cross_stage_eligibility_manifest(manifest,stages):
 if manifest.schema_version!=SCHEMA_VERSION or manifest.contract_version!=CONTRACT_VERSION or manifest.adapter_version!=ADAPTER_VERSION: raise CrossStageEligibilityManifestError("EB1_0D_VERSION_MISMATCH")
 if build_cross_stage_eligibility_manifest(stages)!=manifest: raise CrossStageEligibilityManifestError("EB1_0D_REPLAY_MISMATCH")
 return True
