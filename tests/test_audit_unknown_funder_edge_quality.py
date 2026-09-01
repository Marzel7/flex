import json

from scripts.audit_unknown_funder_edge_quality import main


def test_audit_is_read_only_and_fails_closed_without_joinable_dust(tmp_path, monkeypatch):
    db=tmp_path/'ops.db'
    import sqlite3
    c=sqlite3.connect(db)
    c.executescript('''
      CREATE TABLE wt_farm_launches(mint TEXT PRIMARY KEY,funder TEXT,creator TEXT,peak_mc REAL,migrated_at INTEGER,wrap_close INTEGER,seed_sol REAL,detected_at INTEGER);
      CREATE TABLE wt_confirmed_treasuries(treasury TEXT PRIMARY KEY);
      CREATE TABLE wt_discovered_subprovs(subprov TEXT PRIMARY KEY);
      CREATE TABLE wt_walkback_edge_candidates(mint TEXT,candidate_parent TEXT,selection_status TEXT,amount_lamports INTEGER,block_time INTEGER);
      CREATE TABLE operator_launch_membership(mint TEXT PRIMARY KEY);
      CREATE TABLE confirmed_operation_matches(mint TEXT,state TEXT);
      CREATE TABLE provisional_operation_matches(mint TEXT);
      CREATE TABLE wt_dust_observations(dust_wallet TEXT,recipient_wallet TEXT,amount_lamports INTEGER);
      CREATE TABLE wt_known_spam_wallets(wallet TEXT PRIMARY KEY);
      CREATE TABLE wt_dust_markers(wallet TEXT PRIMARY KEY);
      INSERT INTO wt_farm_launches VALUES('m','f','c',NULL,1,0,0.000001,1);
      INSERT INTO wt_walkback_edge_candidates VALUES('m','f','SELECTED',1000,1);
      INSERT INTO wt_dust_observations VALUES('d','a',1000),('d','b',1000),('d','c',1000);
    ''');c.commit();c.close()
    out=tmp_path/'audit.json'
    monkeypatch.setattr('sys.argv',['audit','--db',str(db),'--output',str(out)])
    assert main()==0
    data=json.loads(out.read_text())
    assert data['genuine_control_population']['KNOWN_GENUINE_FALSE_DUST_COUNT']==0
    assert data['dust_fixture_replay']['fixtures'][0]['classifier_result']=='DUST_SPAM_EDGE'
    assert data['verdict'].startswith('HOLD_')
