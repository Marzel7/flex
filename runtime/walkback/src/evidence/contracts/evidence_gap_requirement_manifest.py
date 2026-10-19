"""EB1.1D immutable requirement manifests."""
from dataclasses import asdict,dataclass
from hashlib import sha256
import json
from .evidence_gap_requirement import CONTRACT_VERSION,EvidenceGapRequirementProjection
from .evidence_gap_requirement_adapters import ADAPTER_VERSION
SCHEMA_VERSION="eb1.1d.v1"
class EvidenceGapRequirementManifestError(ValueError):pass
@dataclass(frozen=True)
class EvidenceGapRequirementManifest:
 schema_version:str;contract_version:str;adapter_version:str;input_projection_digest:str;projection:EvidenceGapRequirementProjection;manifest_digest:str
def _d(v):return sha256(json.dumps(v,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
def build_evidence_gap_requirement_manifest(p):
 body={"schema_version":SCHEMA_VERSION,"contract_version":CONTRACT_VERSION,"adapter_version":ADAPTER_VERSION,"input_projection_digest":p.input_projection_digest,"projection":asdict(p)}
 return EvidenceGapRequirementManifest(SCHEMA_VERSION,CONTRACT_VERSION,ADAPTER_VERSION,p.input_projection_digest,p,_d(body))
def verify_evidence_gap_requirement_manifest(m,p):
 if m.schema_version!=SCHEMA_VERSION or m.contract_version!=CONTRACT_VERSION or m.adapter_version!=ADAPTER_VERSION:raise EvidenceGapRequirementManifestError("EB1_1D_VERSION_MISMATCH")
 if build_evidence_gap_requirement_manifest(p)!=m:raise EvidenceGapRequirementManifestError("EB1_1D_REPLAY_MISMATCH")
 return True
