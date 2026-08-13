"""EB1.0E immutable eligibility corpus."""
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Iterable,Tuple
from .cross_stage_eligibility import StageEligibility
from .cross_stage_eligibility_manifest import CrossStageEligibilityManifest,verify_cross_stage_eligibility_manifest
SCHEMA_VERSION="eb1.0e.v1"
class CrossStageEligibilityCorpusError(ValueError): pass
@dataclass(frozen=True)
class CrossStageEligibilityCorpus:
 schema_version:str; source_manifest_digests:Tuple[str,...]; stages:Tuple[StageEligibility,...]; corpus_digest:str
def _d(v): return sha256(json.dumps(v,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
def assemble_cross_stage_eligibility_corpus(manifests:Iterable[CrossStageEligibilityManifest]):
 material=tuple(manifests)
 if not material: raise CrossStageEligibilityCorpusError("EB1_0E_EMPTY_INPUT")
 stages={}
 for m in material:
  try: verify_cross_stage_eligibility_manifest(m,m.projection.stages)
  except Exception as e: raise CrossStageEligibilityCorpusError("EB1_0E_UNVERIFIED_MANIFEST") from e
  for x in m.projection.stages:
   prior=stages.get(x.upstream_stage)
   if prior and prior!=x: raise CrossStageEligibilityCorpusError("EB1_0E_STAGE_CONFLICT")
   stages[x.upstream_stage]=x
 ordered=tuple(stages[k] for k in sorted(stages)); lineage=tuple(sorted(m.manifest_digest for m in material))
 return CrossStageEligibilityCorpus(SCHEMA_VERSION,lineage,ordered,_d({"schema":SCHEMA_VERSION,"lineage":lineage,"stages":ordered}))
def verify_cross_stage_eligibility_corpus(corpus,manifests):
 if assemble_cross_stage_eligibility_corpus(manifests)!=corpus: raise CrossStageEligibilityCorpusError("EB1_0E_REPLAY_MISMATCH")
 return True
