import sqlite3
from flask import Flask
from src.ops.operator_reader import OperatorReader
from src.ops import operator_routes

DEEP='bb255638-a493-551f-938c-8be7c9ea4f1e'

def _db(path):
 c=sqlite3.connect(path); c.executescript('''
 CREATE TABLE operation_admission_candidates(candidate_id TEXT PRIMARY KEY,operation_id TEXT,mint TEXT,created_at INTEGER);
 CREATE TABLE operation_admission_outcomes(candidate_id TEXT,assessment_result TEXT,assessment_semantic_version TEXT,admission_result TEXT,reason TEXT,causal_witness_id TEXT,created_at INTEGER);
 CREATE TABLE wt_deep_route_review_leads(mint TEXT PRIMARY KEY,operator_id TEXT,last_observed_at INTEGER);
 CREATE TABLE operator_launch_membership(mint TEXT PRIMARY KEY,operator_id TEXT);
 ''')
 return c
def _row(c,mint,state='REVIEW',at=100):
 c.execute('INSERT INTO operation_admission_candidates VALUES(?,?,?,?)',(mint,DEEP,mint,at))
 c.execute('INSERT INTO operation_admission_outcomes VALUES(?,?,?,?,?,?,?)',(mint,'QUALIFIED_PROSPECTIVE_MEMBER','v1',state,'reason','witness',at))
 c.execute('INSERT INTO wt_deep_route_review_leads VALUES(?,?,?)',(mint,DEEP,at))
def test_review_model_excludes_canonical_reject_and_insufficient(tmp_path,monkeypatch):
 path=tmp_path/'ops.db'; c=_db(path); _row(c,'review',at=200); _row(c,'reject','REJECT',300); _row(c,'insufficient','INSUFFICIENT_EVIDENCE',400); _row(c,'canonical',at=500); c.execute('INSERT INTO operator_launch_membership VALUES(?,?)',('canonical',DEEP)); c.commit(); c.close()
 monkeypatch.setattr('src.ops.operator_reader.time.time',lambda:600)
 model=OperatorReader(str(path)).fetch_deep_review_read_model(limit=25)
 assert model['count']==1 and model['candidates'][0]['mint']=='review'
 assert model['last_review']['admission_result']=='REVIEW' and model['candidates'][0]['canonical_membership'] is False
def test_empty_and_bounded_results(tmp_path):
 path=tmp_path/'ops.db'; c=_db(path)
 for n in range(110): _row(c,f'm{n}',at=n+1)
 c.commit(); c.close()
 model=OperatorReader(str(path)).fetch_deep_review_read_model(limit=999)
 assert model['count']==110 and model['limit']==100 and len(model['candidates'])==100 and model['last_review']['mint']=='m109'

def test_deep_review_api_is_deep_only_and_uses_read_model(tmp_path, monkeypatch):
 path=tmp_path/'ops.db'; c=_db(path); _row(c,'review',at=200); c.commit(); c.close()
 app=Flask(__name__); app.register_blueprint(operator_routes.operator_bp)
 monkeypatch.setattr(operator_routes, '_store', OperatorReader(str(path)))
 response=app.test_client().get(f'/api/ops/operators/{DEEP}/deep-review?limit=bad')
 assert response.status_code==200
 payload=response.get_json()
 assert payload['ok'] is True and payload['count']==1 and payload['limit']==25
 assert app.test_client().get('/api/ops/operators/not-deep/deep-review').status_code==404
