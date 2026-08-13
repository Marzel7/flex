from tests.test_eb1_0g_cross_stage_eligibility_extractor import _db
from src.evidence.contracts.cross_stage_eligibility_extractor import extract_cross_stage_eligibility
from src.evidence.contracts.cross_stage_eligibility_bundle import *
import pytest
R="7dbb3a46f0000000000000000000000000000000"
def test_write_replay_and_deterministic(tmp_path):
 p=tmp_path/'x.db';_db(p);r=extract_cross_stage_eligibility(p);a=write_cross_stage_eligibility_bundle(r,tmp_path/'a',run_id='r',engineering_revision=R);assert verify_cross_stage_eligibility_bundle(tmp_path/'a')==a
 write_cross_stage_eligibility_bundle(r,tmp_path/'b',run_id='r',engineering_revision=R);assert {x.name:x.read_bytes() for x in (tmp_path/'a').iterdir()}=={x.name:x.read_bytes() for x in (tmp_path/'b').iterdir()}
def test_overwrite_alter_extra_fail(tmp_path):
 p=tmp_path/'x.db';_db(p);r=extract_cross_stage_eligibility(p);o=tmp_path/'o';write_cross_stage_eligibility_bundle(r,o,run_id='r',engineering_revision=R)
 with pytest.raises(CrossStageEligibilityBundleError,match='OUTPUT_NOT_EMPTY'):write_cross_stage_eligibility_bundle(r,o,run_id='r',engineering_revision=R)
 (o/'run.json').write_text('{}\n')
 with pytest.raises(CrossStageEligibilityBundleError,match='DIGEST_MISMATCH'):verify_cross_stage_eligibility_bundle(o)
 (o/'extra').write_text('x')
 with pytest.raises(CrossStageEligibilityBundleError,match='FILE_SET_MISMATCH'):verify_cross_stage_eligibility_bundle(o)
