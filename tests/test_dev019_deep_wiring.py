import sqlite3
from src.ops.operation_admission_adapter import ensure_schema, record_review_only_candidate

def test_deep_review_wiring_is_durable_idempotent_and_noncanonical():
 c=sqlite3.connect(':memory:'); c.execute('CREATE TABLE operator_launch_membership(mint TEXT PRIMARY KEY,operator_id TEXT,source_population_id TEXT,assigned_at INTEGER,event_id TEXT)'); ensure_schema(c)
 n={'operation_id':'bb255638-a493-551f-938c-8be7c9ea4f1e','mint':'positive','nomination_type':'RETAINED_DEEP_REVIEW','nomination_semantic_version':'WATCHTOWER_DEEP_NOMINATION_V1','nomination_evidence_id':'route'}
 a={'state':'QUALIFIED_PROSPECTIVE_MEMBER','assessment_id':'route','semantic_version':'WATCHTOWER_DEEP_ASSESSMENT_V1','causal_witness_id':'route','capital_continuity_id':'route','transaction_order_id':'route','evidence_complete':True}
 p={'policy_id':'WATCHTOWER_DEEP_ADMISSION_POLICY_V1','policy_version':'v1','required_assessment_outcome':'QUALIFIED_PROSPECTIVE_MEMBER','positive_action':'REVIEW'}
 assert record_review_only_candidate(c,n,a,p,1)==record_review_only_candidate(c,n,a,p,2)
 assert c.execute("SELECT admission_result FROM operation_admission_outcomes").fetchone()[0]=='REVIEW'
 assert c.execute('SELECT count(*) FROM operator_launch_membership').fetchone()[0]==0
 assert c.execute('SELECT count(*) FROM operation_event_outbox').fetchone()[0]==0
