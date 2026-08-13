from tests.test_eb1_1g_requirement_extractor import _db
from src.evidence.contracts.evidence_gap_requirement_extractor import extract_evidence_gap_requirements
from src.evidence.contracts.evidence_gap_requirement_bundle import *
import pytest
R='dec6447e00000000000000000000000000000000'
def test_bundle_write_replay_deterministic(tmp_path):
 p=tmp_path/'x.db';_db(p);r=extract_evidence_gap_requirements(p);a=write_evidence_gap_requirement_bundle(r,tmp_path/'a',run_id='r',engineering_revision=R);assert verify_evidence_gap_requirement_bundle(tmp_path/'a')==a
 write_evidence_gap_requirement_bundle(r,tmp_path/'b',run_id='r',engineering_revision=R);assert {x.name:x.read_bytes() for x in (tmp_path/'a').iterdir()}=={x.name:x.read_bytes() for x in (tmp_path/'b').iterdir()}
def test_overwrite_alter_extra_fail(tmp_path):
 p=tmp_path/'x.db';_db(p);r=extract_evidence_gap_requirements(p);o=tmp_path/'o';write_evidence_gap_requirement_bundle(r,o,run_id='r',engineering_revision=R)
 with pytest.raises(EvidenceGapRequirementBundleError,match='OUTPUT_NOT_EMPTY'):write_evidence_gap_requirement_bundle(r,o,run_id='r',engineering_revision=R)
 (o/'run.json').write_text('{}\n')
 with pytest.raises(EvidenceGapRequirementBundleError,match='DIGEST_MISMATCH'):verify_evidence_gap_requirement_bundle(o)
 (o/'extra').write_text('x')
 with pytest.raises(EvidenceGapRequirementBundleError,match='FILE_SET_MISMATCH'):verify_evidence_gap_requirement_bundle(o)
