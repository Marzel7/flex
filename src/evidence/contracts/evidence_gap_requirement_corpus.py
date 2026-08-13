"""EB1.1E immutable per-authority-lane requirement corpus."""
from dataclasses import dataclass
from hashlib import sha256
import json
from .evidence_gap_requirement import EvidenceGapRequirement
from .evidence_gap_requirement_manifest import verify_evidence_gap_requirement_manifest
SCHEMA_VERSION="eb1.1e.v1"
class EvidenceGapRequirementCorpusError(ValueError):pass
@dataclass(frozen=True)
class EvidenceGapRequirementLane:
 upstream_stage:str;authority_lane:str;requirements:tuple;lane_digest:str
@dataclass(frozen=True)
class EvidenceGapRequirementCorpus:
 schema_version:str;source_manifest_digests:tuple;lanes:tuple;corpus_digest:str
def _d(v):return sha256(json.dumps(v,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
def assemble_evidence_gap_requirement_corpus(manifests):
 ms=tuple(manifests)
 if not ms:raise EvidenceGapRequirementCorpusError("EB1_1E_EMPTY_INPUT")
 grouped={}
 for m in ms:
  try:verify_evidence_gap_requirement_manifest(m,m.projection)
  except Exception as e:raise EvidenceGapRequirementCorpusError("EB1_1E_UNVERIFIED_MANIFEST") from e
  for r in m.projection.requirements:
   key=(r.upstream_stage,r.authority_lane);prior=grouped.setdefault(key,{}).get(r.requirement_id)
   if prior and prior!=r:raise EvidenceGapRequirementCorpusError("EB1_1E_REQUIREMENT_COLLISION")
   grouped[key][r.requirement_id]=r
 lanes=[]
 for key in sorted(grouped):
  req=tuple(grouped[key][x] for x in sorted(grouped[key]));lanes.append(EvidenceGapRequirementLane(*key,req,_d(req)))
 lineage=tuple(sorted(m.manifest_digest for m in ms));lanes=tuple(lanes)
 return EvidenceGapRequirementCorpus(SCHEMA_VERSION,lineage,lanes,_d({"schema":SCHEMA_VERSION,"lineage":lineage,"lanes":lanes}))
def verify_evidence_gap_requirement_corpus(c,manifests):
 if assemble_evidence_gap_requirement_corpus(manifests)!=c:raise EvidenceGapRequirementCorpusError("EB1_1E_REPLAY_MISMATCH")
 return True
