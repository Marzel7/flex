import json,sqlite3
from pathlib import Path
import pytest
from src.evidence.contracts.evidence_gap_requirement_extractor import *
F=Path(__file__).parent/'fixtures/eb1_0a_cross_stage_eligibility.json'
def _db(p,extra=False):
 c=sqlite3.connect(p);c.execute("CREATE TABLE eligibility_records(position INTEGER,canonical_json TEXT)")
 for i,v in enumerate(json.loads(F.read_text())):c.execute("INSERT INTO eligibility_records VALUES(?,?)",(i,json.dumps(v,sort_keys=True,separators=(",",":"))))
 if extra:c.execute("CREATE TABLE extra(x)")
 c.commit();c.close()
def test_deterministic_fixture_extraction(tmp_path):
 p=tmp_path/'x.db';_db(p);a=extract_evidence_gap_requirements(p);assert a==extract_evidence_gap_requirements(p);assert a.input_record_count==4;assert len(a.corpus.lanes)==2
def test_schema_and_bound_fail(tmp_path):
 p=tmp_path/'x.db';_db(p,True)
 with pytest.raises(EvidenceGapRequirementExtractorError,match='SCHEMA_MISMATCH'):extract_evidence_gap_requirements(p)
 with pytest.raises(EvidenceGapRequirementExtractorError,match='INVALID_QUERY_BOUND'):extract_evidence_gap_requirements(p,max_query_seconds=31)
