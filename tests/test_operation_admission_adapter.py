import sqlite3
from src.ops.operation_admission_adapter import ensure_schema,persist_candidate,persist_outcome,resume_admission

def db():
 c=sqlite3.connect(':memory:'); c.execute('CREATE TABLE operator_launch_membership(mint TEXT PRIMARY KEY,operator_id TEXT NOT NULL,source_population_id TEXT,assigned_at INTEGER,event_id TEXT)'); ensure_schema(c); return c
def nomination(op='WATCHTOWER_DEEP', mint='m'):
 return {'operation_id':op,'mint':mint,'nomination_type':'RETAINED','nomination_semantic_version':'n1','nomination_evidence_id':'e1'}
def assessment(state='QUALIFIED_PROSPECTIVE_MEMBER', aid='a1', **extra):
 return {'state':state,'assessment_id':aid,'semantic_version':'assessment-v1','causal_witness_id':'w1','capital_continuity_id':'c1',**extra}
def policy(action='REVIEW'):
 return {'policy_id':'frozen-policy','policy_version':'v1','required_assessment_outcome':'QUALIFIED_PROSPECTIVE_MEMBER','positive_action':action}
def prepare(c, n=None, a=None, p=None):
 n=n or nomination(); cid=persist_candidate(c,n,1); oid=persist_outcome(c,cid,n,a or assessment(),p or policy(),2); c.commit(); return cid,oid
def counts(c): return tuple(c.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in ('operation_admission_candidates','operation_admission_outcomes','operator_launch_membership','operation_event_outbox'))
def test_deep_review_is_durable_and_never_commits():
 c=db(); _,oid=prepare(c); assert resume_admission(c,oid,3)=={'membership_id':None,'event_id':None}; assert counts(c)==(1,1,0,0)
def test_conflict_missing_and_unknown_fail_closed():
 for a in (assessment('CONFLICT','a1'),assessment('INSUFFICIENT_EVIDENCE','a2'),assessment(aid='a3',evidence_complete=False)):
  c=db(); _,oid=prepare(c,a=a); assert resume_admission(c,oid,3)=={'membership_id':None,'event_id':None}; assert counts(c)[2:]==(0,0)
def test_synthetic_admit_is_idempotent_and_restart_safe():
 c=db(); n=nomination('SYNTHETIC','synthetic'); cid,oid=prepare(c,n,assessment(),policy('ADMIT')); first=resume_admission(c,oid,3); c.commit(); assert resume_admission(c,oid,4)==first; assert counts(c)==(1,1,1,1)
def test_candidate_and_outcome_history_are_idempotent_but_policy_version_is_preserved():
 c=db(); n=nomination(); cid,one=prepare(c,n); assert persist_candidate(c,n,3)==cid; two=persist_outcome(c,cid,n,assessment(),{**policy(),'policy_version':'v2'},4); assert one!=two and counts(c)[:2]==(1,2)
def test_membership_requires_existing_admit_and_canonical_conflict_fails_closed():
 c=db();
 try: resume_admission(c,'missing',1)
 except ValueError: pass
 else: assert False
 n=nomination('SYNTHETIC','x'); _,oid=prepare(c,n,assessment(),policy('ADMIT')); c.execute("INSERT INTO operator_launch_membership VALUES('x','other','z',1,'z')")
 try: resume_admission(c,oid,3)
 except ValueError: pass
 else: assert False
