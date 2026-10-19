"""EB1.0H immutable eligibility bundles."""
from dataclasses import asdict,dataclass
from hashlib import sha256
import json,re
from pathlib import Path
from .cross_stage_eligibility_extractor import CrossStageEligibilityExtraction
SCHEMA_VERSION="eb1.0h.v1";FILES=("run.json","manifest.json","corpus.json");REV=re.compile(r"^[0-9a-f]{7,64}$")
class CrossStageEligibilityBundleError(RuntimeError):pass
@dataclass(frozen=True)
class CrossStageEligibilityBundle: output_directory:Path;bundle_digest:str;file_digests:dict
def _j(v):return json.dumps(v,sort_keys=True,separators=(",",":"),default=str).encode()+b"\n"
def _d(v):return sha256(v).hexdigest()
def write_cross_stage_eligibility_bundle(result:CrossStageEligibilityExtraction,out:Path,*,run_id:str,engineering_revision:str):
 if not run_id or not REV.fullmatch(engineering_revision):raise CrossStageEligibilityBundleError("EB1_0H_INVALID_METADATA")
 out=Path(out)
 if out.exists() and (not out.is_dir() or any(out.iterdir())):raise CrossStageEligibilityBundleError("EB1_0H_OUTPUT_NOT_EMPTY")
 out.mkdir(exist_ok=True)
 payload={"run.json":_j({"schema_version":SCHEMA_VERSION,"run_id":run_id,"engineering_revision":engineering_revision,"extraction_schema_version":result.schema_version,"input_fingerprint":result.input_fingerprint,"result_digest":result.result_digest}),"manifest.json":_j(asdict(result.manifests[0])),"corpus.json":_j(asdict(result.corpus))}
 digs={k:_d(v) for k,v in payload.items()};bd=_d(_j(digs))
 for k,v in payload.items():
  with (out/k).open("xb") as f:f.write(v)
 with (out/"hashes.json").open("xb") as f:f.write(_j({"schema_version":SCHEMA_VERSION,"files":digs,"bundle_digest":bd}))
 return CrossStageEligibilityBundle(out,bd,digs)
def verify_cross_stage_eligibility_bundle(out:Path):
 out=Path(out);expected=set(FILES)|{"hashes.json"}
 if not out.is_dir() or {x.name for x in out.iterdir()}!=expected:raise CrossStageEligibilityBundleError("EB1_0H_FILE_SET_MISMATCH")
 try: docs={k:json.loads((out/k).read_text()) for k in FILES};h=json.loads((out/'hashes.json').read_text())
 except Exception as e:raise CrossStageEligibilityBundleError("EB1_0H_INVALID_JSON") from e
 if any((out/k).read_bytes()!=_j(docs[k]) for k in FILES) or (out/'hashes.json').read_bytes()!=_j(h):raise CrossStageEligibilityBundleError("EB1_0H_NONCANONICAL_JSON")
 actual={k:_d((out/k).read_bytes()) for k in FILES};bd=_d(_j(actual))
 if h!={"schema_version":SCHEMA_VERSION,"files":actual,"bundle_digest":bd}:raise CrossStageEligibilityBundleError("EB1_0H_DIGEST_MISMATCH")
 if docs["run.json"].get("schema_version")!=SCHEMA_VERSION or not REV.fullmatch(docs["run.json"].get("engineering_revision","")):raise CrossStageEligibilityBundleError("EB1_0H_METADATA_MISMATCH")
 if docs["manifest.json"].get("manifest_digest") not in docs["corpus.json"].get("source_manifest_digests",[]):raise CrossStageEligibilityBundleError("EB1_0H_LINEAGE_MISMATCH")
 return CrossStageEligibilityBundle(out,bd,actual)
