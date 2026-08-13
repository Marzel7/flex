import json,sqlite3
import pytest
from src.evidence.contracts.cross_stage_eligibility_extractor import *
from tests.test_eb1_0c_cross_stage_eligibility_adapters import _inputs
def _db(p,extra=False):
 c=sqlite3.connect(p);c.executescript("CREATE TABLE bundle_summary_documents(stage TEXT,document_kind TEXT,canonical_json TEXT);CREATE TABLE eb0_1_revision(engineering_revision TEXT);")
 eb1,eb2,eb3,eb4=_inputs(); data={"EB0.1":{"run":eb1[0],"aggregate":eb1[1],"hashes":eb1[2]},"EB0.2":{"run":eb2[0],"accounting":eb2[1],"hashes":eb2[2]},"EB0.3":{"run":eb3[0],"manifest":eb3[1],"hashes":eb3[2]},"EB0.4":{"run":eb4[0],"accounting":eb4[1],"hashes":eb4[2]}}
 for s,ds in data.items():
  for k,v in ds.items():c.execute("INSERT INTO bundle_summary_documents VALUES(?,?,?)",(s,k,json.dumps(v,sort_keys=True,separators=(",",":"))))
 c.execute("INSERT INTO eb0_1_revision VALUES(?)",(eb1[3],));
 if extra:c.execute("CREATE TABLE extra(x)")
 c.commit();c.close()
def test_extract_deterministic(tmp_path):
 p=tmp_path/'x.db';_db(p);a=extract_cross_stage_eligibility(p);b=extract_cross_stage_eligibility(p);assert a==b;assert a.document_count==12;assert len(a.corpus.stages)==4
def test_schema_missing_and_bound_fail(tmp_path):
 p=tmp_path/'x.db';_db(p,True)
 with pytest.raises(CrossStageEligibilityExtractorError,match="SCHEMA_MISMATCH"):extract_cross_stage_eligibility(p)
 with pytest.raises(CrossStageEligibilityExtractorError,match="INVALID_QUERY_BOUND"):extract_cross_stage_eligibility(p,max_query_seconds=31)
