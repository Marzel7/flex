"""EB1.0G fixture-only query extractor."""
from dataclasses import dataclass
from hashlib import sha256
import json,sqlite3,time
from pathlib import Path
from urllib.parse import quote
from .cross_stage_eligibility_adapters import adapt_verified_bundle_summaries
from .cross_stage_eligibility_manifest import build_cross_stage_eligibility_manifest,CrossStageEligibilityManifest
from .cross_stage_eligibility_corpus import assemble_cross_stage_eligibility_corpus,CrossStageEligibilityCorpus
SCHEMA_VERSION="eb1.0g.v1"; MAX_SECONDS=30.; MAX_BYTES=1024*1024
class CrossStageEligibilityExtractorError(RuntimeError): pass
@dataclass(frozen=True)
class CrossStageEligibilityExtraction:
 schema_version:str; document_count:int; manifests:tuple; corpus:CrossStageEligibilityCorpus; input_fingerprint:str; result_digest:str
def _d(v): return sha256(json.dumps(v,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
def extract_cross_stage_eligibility(path:Path,*,max_query_seconds=MAX_SECONDS,clock=time.monotonic):
 if max_query_seconds<=0 or max_query_seconds>MAX_SECONDS: raise CrossStageEligibilityExtractorError("EB1_0G_INVALID_QUERY_BOUND")
 if not Path(path).is_file(): raise CrossStageEligibilityExtractorError("EB1_0G_SOURCE_NOT_FOUND")
 c=sqlite3.connect(f"file:{quote(str(Path(path).resolve()),safe='/')}?mode=ro",uri=True);c.row_factory=sqlite3.Row;c.execute("PRAGMA query_only=ON")
 try:
  if c.execute("PRAGMA query_only").fetchone()[0]!=1: raise CrossStageEligibilityExtractorError("EB1_0G_QUERY_ONLY_NOT_ENFORCED")
  tables={r[0] for r in c.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
  if tables!={"bundle_summary_documents","eb0_1_revision"}: raise CrossStageEligibilityExtractorError("EB1_0G_SCHEMA_MISMATCH")
  if {r[1] for r in c.execute("PRAGMA table_info(bundle_summary_documents)")}!={"stage","document_kind","canonical_json"}: raise CrossStageEligibilityExtractorError("EB1_0G_SCHEMA_MISMATCH")
  deadline=clock()+max_query_seconds;c.set_progress_handler(lambda:int(clock()>=deadline),1000)
  rows=c.execute("SELECT stage,document_kind,canonical_json FROM bundle_summary_documents ORDER BY stage,document_kind").fetchall(); revisions=c.execute("SELECT engineering_revision FROM eb0_1_revision").fetchall()
  if clock()>=deadline: raise CrossStageEligibilityExtractorError("EB1_0G_QUERY_TIMEOUT")
  if len(rows)!=12 or len(revisions)!=1 or sum(len(r[2].encode()) for r in rows)>MAX_BYTES: raise CrossStageEligibilityExtractorError("EB1_0G_DOCUMENT_BOUNDARY_REJECTED")
  docs={};
  for r in rows:
   key=(r[0],r[1])
   if key in docs: raise CrossStageEligibilityExtractorError("EB1_0G_DUPLICATE_DOCUMENT")
   try: value=json.loads(r[2])
   except Exception as e: raise CrossStageEligibilityExtractorError("EB1_0G_INVALID_JSON") from e
   if json.dumps(value,sort_keys=True,separators=(",",":"))!=r[2]: raise CrossStageEligibilityExtractorError("EB1_0G_NONCANONICAL_JSON")
   docs[key]=value
  required={(s,k) for s,kinds in {"EB0.1":("run","aggregate","hashes"),"EB0.2":("run","accounting","hashes"),"EB0.3":("run","manifest","hashes"),"EB0.4":("run","accounting","hashes")}.items() for k in kinds}
  if set(docs)!=required: raise CrossStageEligibilityExtractorError("EB1_0G_DOCUMENT_SET_MISMATCH")
  stages=adapt_verified_bundle_summaries(eb0_1=(docs["EB0.1","run"],docs["EB0.1","aggregate"],docs["EB0.1","hashes"],revisions[0][0]),eb0_2=(docs["EB0.2","run"],docs["EB0.2","accounting"],docs["EB0.2","hashes"]),eb0_3=(docs["EB0.3","run"],docs["EB0.3","manifest"],docs["EB0.3","hashes"]),eb0_4=(docs["EB0.4","run"],docs["EB0.4","accounting"],docs["EB0.4","hashes"]))
  m=build_cross_stage_eligibility_manifest(stages); corpus=assemble_cross_stage_eligibility_corpus([m]); fingerprint=_d({"docs":[{"stage":k[0],"kind":k[1],"value":docs[k]} for k in sorted(docs)],"revision":revisions[0][0]}); body={"schema":SCHEMA_VERSION,"manifest":m.manifest_digest,"corpus":corpus.corpus_digest,"input":fingerprint}
  return CrossStageEligibilityExtraction(SCHEMA_VERSION,12,(m,),corpus,fingerprint,_d(body))
 finally: c.close()
