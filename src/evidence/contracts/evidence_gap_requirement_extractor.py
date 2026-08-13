"""EB1.1G fixture-only requirement extractor."""
from dataclasses import dataclass
from hashlib import sha256
import json,sqlite3,time
from pathlib import Path
from urllib.parse import quote
from .cross_stage_eligibility import project_cross_stage_eligibility
from .evidence_gap_requirement import project_evidence_gap_requirements
from .evidence_gap_requirement_manifest import build_evidence_gap_requirement_manifest
from .evidence_gap_requirement_corpus import assemble_evidence_gap_requirement_corpus
SCHEMA_VERSION="eb1.1g.v1";MAX_SECONDS=30.;MAX_BYTES=262144
class EvidenceGapRequirementExtractorError(RuntimeError):pass
@dataclass(frozen=True)
class EvidenceGapRequirementExtraction:
 schema_version:str;input_record_count:int;manifest:object;corpus:object;input_fingerprint:str;result_digest:str
def _d(v):return sha256(json.dumps(v,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
def extract_evidence_gap_requirements(path:Path,*,max_query_seconds=MAX_SECONDS,clock=time.monotonic):
 if max_query_seconds<=0 or max_query_seconds>MAX_SECONDS:raise EvidenceGapRequirementExtractorError("EB1_1G_INVALID_QUERY_BOUND")
 if not Path(path).is_file():raise EvidenceGapRequirementExtractorError("EB1_1G_SOURCE_NOT_FOUND")
 c=sqlite3.connect(f"file:{quote(str(Path(path).resolve()),safe='/')}?mode=ro",uri=True);c.row_factory=sqlite3.Row;c.execute("PRAGMA query_only=ON")
 try:
  if c.execute("PRAGMA query_only").fetchone()[0]!=1:raise EvidenceGapRequirementExtractorError("EB1_1G_QUERY_ONLY_NOT_ENFORCED")
  tables={x[0] for x in c.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
  if tables!={"eligibility_records"} or {x[1] for x in c.execute("PRAGMA table_info(eligibility_records)")}!={"position","canonical_json"}:raise EvidenceGapRequirementExtractorError("EB1_1G_SCHEMA_MISMATCH")
  deadline=clock()+max_query_seconds;c.set_progress_handler(lambda:int(clock()>=deadline),1000);rows=c.execute("SELECT position,canonical_json FROM eligibility_records ORDER BY position").fetchall()
  if clock()>=deadline:raise EvidenceGapRequirementExtractorError("EB1_1G_QUERY_TIMEOUT")
  if len(rows)!=4 or [x[0] for x in rows]!=list(range(4)) or sum(len(x[1].encode()) for x in rows)>MAX_BYTES:raise EvidenceGapRequirementExtractorError("EB1_1G_INPUT_BOUNDARY_REJECTED")
  records=[]
  for x in rows:
   try:v=json.loads(x[1])
   except Exception as e:raise EvidenceGapRequirementExtractorError("EB1_1G_INVALID_JSON") from e
   if json.dumps(v,sort_keys=True,separators=(",",":"))!=x[1]:raise EvidenceGapRequirementExtractorError("EB1_1G_NONCANONICAL_JSON")
   records.append(v)
  eligibility=project_cross_stage_eligibility(records);requirements=project_evidence_gap_requirements(eligibility);m=build_evidence_gap_requirement_manifest(requirements);corpus=assemble_evidence_gap_requirement_corpus([m]);fp=_d(records);body={"schema":SCHEMA_VERSION,"manifest":m.manifest_digest,"corpus":corpus.corpus_digest,"input":fp}
  return EvidenceGapRequirementExtraction(SCHEMA_VERSION,4,m,corpus,fp,_d(body))
 finally:c.close()
