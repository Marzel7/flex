import sqlite3
import pytest
from src.ops.operation_admission_adapter import ensure_schema,persist_candidate,persist_outcome,resume_admission
from src.ops.manual_review_admission import ensure_manual_approval_schema,write_manual_review_decision
from src.ops.manual_review_admission_contract import review_state_token

def setup():
 c=sqlite3.connect(':memory:'); ensure_schema(c); ensure_manual_approval_schema(c); c.executescript('CREATE TABLE operator_launch_membership(mint TEXT PRIMARY KEY,operator_id TEXT,source_population_id TEXT,assigned_at INTEGER,event_id TEXT);')
 n={'operation_id':'op','mint':'mint','nomination_type':'n','nomination_semantic_version':'v','nomination_evidence_id':'e'}; cid=persist_candidate(c,n,1)
 a={'assessment_id':'a','semantic_version':'a1','state':'QUALIFIED_PROSPECTIVE_MEMBER','evidence_complete':True}; p={'policy_id':'p','policy_version':'v1','required_assessment_outcome':'QUALIFIED_PROSPECTIVE_MEMBER','positive_action':'REVIEW'}; oid=persist_outcome(c,cid,n,a,p,2); c.commit()
 row={'operation_id':'op','mint':'mint','candidate_id':cid,'review_outcome_id':oid,'assessment_id':'a','assessment_semantic_version':'a1','policy_id':'p','policy_version':'v1','admission_result':'REVIEW'}
 return c,cid,oid,review_state_token(row)
def principal(): return {'id':'admin','authenticated':True,'permissions':{'operations.deep.review.approve'}}
def test_approval_writes_only_provenance_and_admit_then_existing_resumer():
 c,cid,oid,t=setup(); r=write_manual_review_decision(c,principal=principal(),candidate_id=cid,review_outcome_id=oid,state_token=t,action='APPROVE',now=3)
 assert c.execute('select count(*) from operation_manual_approvals').fetchone()[0]==1 and c.execute('select count(*) from operator_launch_membership').fetchone()[0]==0
 first=resume_admission(c,r['admission_outcome_id'],4); assert first['membership_id'] and first['event_id']; assert resume_admission(c,r['admission_outcome_id'],5)==first
def test_stale_unauthorized_decline_and_comment_bound_fail_closed():
 c,cid,oid,t=setup()
 with pytest.raises(PermissionError): write_manual_review_decision(c,principal={'id':'v','authenticated':True,'permissions':set()},candidate_id=cid,review_outcome_id=oid,state_token=t,action='APPROVE')
 with pytest.raises(ValueError): write_manual_review_decision(c,principal=principal(),candidate_id=cid,review_outcome_id=oid,state_token='bad',action='APPROVE')
 with pytest.raises(ValueError): write_manual_review_decision(c,principal=principal(),candidate_id=cid,review_outcome_id=oid,state_token=t,action='APPROVE',comment='x'*513)
 d=write_manual_review_decision(c,principal=principal(),candidate_id=cid,review_outcome_id=oid,state_token=t,action='DECLINE'); assert d['admission_outcome_id'] is None
