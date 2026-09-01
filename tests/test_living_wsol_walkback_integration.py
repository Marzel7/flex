import sqlite3
from src.ops.living_potential_operations import *

def _db(tmp_path):
 p=str(tmp_path/'ops.db'); c=sqlite3.connect(p); ensure_schema(c)
 c.executescript('''CREATE TABLE p3r_v2_candidate_membership(run_id TEXT,candidate_id TEXT,mint TEXT);
 CREATE TABLE wt_walkback_edge_candidates(evidence_key TEXT,mint TEXT,wallet TEXT,candidate_parent TEXT,signature TEXT,amount_lamports INTEGER,mechanism TEXT,selection_status TEXT,last_observed_at INTEGER);
 CREATE TABLE wt_walkback_atomic_flows(evidence_key TEXT,mint TEXT,signature TEXT,transfer_lamports INTEGER,last_observed_at INTEGER,has_create INTEGER,has_sync_native INTEGER,has_close INTEGER);
 CREATE TABLE wt_walkback_queue(mint TEXT,funder_wallet TEXT,updated_at INTEGER);''')
 c.execute('INSERT INTO p3r_v2_candidate_membership VALUES(?,?,?)',(WSOL_RUN,WSOL_CANDIDATE_ID,'m1'))
 c.execute("INSERT INTO wt_walkback_edge_candidates VALUES('e','m1','creator','funder','sig',?,'WSOL_WRAP_CLOSE','SELECTED',1)",(WSOL_AMOUNT,))
 c.execute("INSERT INTO wt_walkback_edge_candidates VALUES('a','m1','creator','funder','sig2',?,'WSOL_WRAP_CLOSE','ALTERNATIVE',1)",(WSOL_AMOUNT,))
 c.execute("INSERT INTO wt_walkback_atomic_flows VALUES('f','m1','sig',?,1,1,1,1)",(WSOL_AMOUNT,));c.execute("INSERT INTO wt_walkback_queue VALUES('m1','funder',1)");c.commit();c.close();return p

def test_wsol_identity_associations_and_exact_replay(tmp_path):
 p=_db(tmp_path); first=bootstrap_wsol_potential_operation(p); second=bootstrap_wsol_potential_operation(p); c=sqlite3.connect(p)
 assert first['assessment_id']==second['assessment_id'] and second['idempotent']
 assert c.execute('select count(*) from potential_operation_identity').fetchone()[0]==1
 assert c.execute('select count(*) from potential_operation_assessment_version').fetchone()[0]==1
 assert set(x[0] for x in c.execute('select distinct state from potential_operation_evidence_association'))=={'INCLUDED','EXCLUDED','UNRESOLVED','CONTRADICTORY'}
 assert first['payload']['status']=='PAUSED' and first['payload']['promotion']=='NO'

def test_wsol_bounded_bridge_only_recomputes_resolved_candidate(tmp_path):
 p=_db(tmp_path);bootstrap_wsol_potential_operation(p)
 assert handle_walkback_evidence_update(p,mint='other')['affected_potential_operation_ids']==[]
 hit=handle_walkback_evidence_update(p,mint='m1')
 assert hit['affected_potential_operation_ids']==[WSOL_POTENTIAL_OPERATION_ID] and hit['automatic_global_integration'] is False
