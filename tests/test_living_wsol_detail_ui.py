from pathlib import Path
from src.ops import living_potential_operations as living

def test_living_projection_is_read_only_and_has_history(tmp_path):
    p=str(tmp_path/'db.sqlite')
    import sqlite3
    c=sqlite3.connect(p); living.ensure_schema(c)
    c.executescript('''CREATE TABLE p3r_v2_candidate_membership(run_id TEXT,candidate_id TEXT,mint TEXT); CREATE TABLE wt_walkback_edge_candidates(evidence_key TEXT,mint TEXT,wallet TEXT,candidate_parent TEXT,signature TEXT,amount_lamports INTEGER,mechanism TEXT,selection_status TEXT,last_observed_at INTEGER); CREATE TABLE wt_walkback_atomic_flows(evidence_key TEXT,mint TEXT,signature TEXT,transfer_lamports INTEGER,last_observed_at INTEGER,has_create INTEGER,has_sync_native INTEGER,has_close INTEGER); CREATE TABLE wt_walkback_queue(mint TEXT,funder_wallet TEXT,updated_at INTEGER);''')
    c.execute('INSERT INTO p3r_v2_candidate_membership VALUES(?,?,?)',(living.WSOL_RUN,living.WSOL_CANDIDATE_ID,'m'))
    c.execute("INSERT INTO wt_walkback_edge_candidates VALUES('e','m','c','f','s',99999985000,'WSOL_WRAP_CLOSE','SELECTED',1)");c.execute("INSERT INTO wt_walkback_atomic_flows VALUES('a','m','s',99999985000,1,1,1,1)");c.execute("INSERT INTO wt_walkback_queue VALUES('m','f',1)");c.commit();c.close();living.bootstrap_wsol_potential_operation(p)
    import sqlite3
    c=sqlite3.connect(p); before=[c.execute(f'SELECT count(*) FROM {n}').fetchone()[0] for n in ('potential_operation_identity','potential_operation_evidence_association','potential_operation_assessment_version','potential_operation_current','wt_walkback_edge_candidates')];c.close()
    projection=living.living_detail_projection(p)
    c=sqlite3.connect(p); after=[c.execute(f'SELECT count(*) FROM {n}').fetchone()[0] for n in ('potential_operation_identity','potential_operation_evidence_association','potential_operation_assessment_version','potential_operation_current','wt_walkback_edge_candidates')];c.close()
    assert before==after and projection['current']['status']=='PAUSED' and projection['history_count']==1
    assert projection['association_counts']['UNRESOLVED'] and projection['global_automatic_walkback_updates']=='NOT ENABLED'

def test_detail_template_labels_living_evidence_as_associations_not_members():
    page=Path('templates/potential_operation_detail.html').read_text()
    assert 'Living Assessment' in page and 'These are associations, not proven members' in page
    assert 'mapped population, not membership' in page and 'Global automatic walkback updates' in page
